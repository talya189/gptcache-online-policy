"""Pinned, offline recorded-response replay for the MOSS SFT corpus.

The benchmark never calls an LLM, tool, inference endpoint, or paid API.  It
streams a checksum-pinned ZIP member, selects a bounded deterministic sample,
derives context-sensitive ground-truth concepts, and replays the corpus's
recorded MOSS response on every cache miss.
"""

import argparse
import csv
import hashlib
import heapq
import importlib.metadata
import io
import json
import math
import os
import stat
import tempfile
import urllib.request
import zipfile
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = "carma-moss-recorded-response-v1"
SOURCE_REPOSITORY = "OpenMOSS-Team/moss-003-sft-data"
SOURCE_REVISION = "42e216d3e3fb331c18d5fa6e7cb4f1c53eef24a4"
SOURCE_LICENSE = "cc-by-4.0"
SOURCE_FILENAME = "moss-003-sft-with-tools-no-text2image.zip"
SOURCE_ARCHIVE_SIZE = 392_830_680
SOURCE_ARCHIVE_SHA256 = (
    "4d4f57df0dd5ad1442b6c08ca69ec1a59705837bb9813e7aae3bd3a9e3adb085"
)
SOURCE_MEMBER = (
    "conversations_with_tools_with_inner_instruction_no_text2image_"
    "train_all_random_meta0.5_0.1_0.01_moss_0709.jsonl"
)
SOURCE_MEMBER_COMPRESSED_SIZE = 392_830_308
SOURCE_MEMBER_SIZE = 2_310_713_571
SOURCE_MEMBER_CRC32 = 0x34C7D393
SOURCE_XET_HASH = (
    "825003e86acba8ead51d7176516939b5327501ed67a9f8606c25b35ed5f1fc3b"
)
TIKTOKEN_VERSION = "0.14.0"
TIKTOKEN_ENCODING = "cl100k_base"
TURN_FIELDS = (
    "Human",
    "Inner Thoughts",
    "Commands",
    "Tool Responses",
    "MOSS",
)
MAX_JSON_LINE_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100.0
DEFAULT_SAMPLE_SIZE = 2048
DEFAULT_REQUESTS = 200
DEFAULT_CAPACITY = 256


@dataclass(frozen=True)
class SourceSpec:
    repository: str
    revision: str
    license: str
    filename: str
    archive_size: int
    archive_sha256: str
    member_name: str
    member_compressed_size: int
    member_size: int
    member_crc32: int
    source_kind: str = "official"


OFFICIAL_SOURCE = SourceSpec(
    repository=SOURCE_REPOSITORY,
    revision=SOURCE_REVISION,
    license=SOURCE_LICENSE,
    filename=SOURCE_FILENAME,
    archive_size=SOURCE_ARCHIVE_SIZE,
    archive_sha256=SOURCE_ARCHIVE_SHA256,
    member_name=SOURCE_MEMBER,
    member_compressed_size=SOURCE_MEMBER_COMPRESSED_SIZE,
    member_size=SOURCE_MEMBER_SIZE,
    member_crc32=SOURCE_MEMBER_CRC32,
)


@dataclass(frozen=True)
class RawTurn:
    conversation_id: int
    round_index: int
    category: str
    concept_id: str
    prior_context_sha256: str
    context_text: str
    current_input: str
    request_text: str
    recorded_response: str
    response_id: str
    sample_priority: str


class TiktokenCounter:
    """Strictly versioned cl100k_base token accounting."""

    def __init__(self) -> None:
        observed = importlib.metadata.version("tiktoken")
        if observed != TIKTOKEN_VERSION:
            raise RuntimeError(
                "MOSS token accounting requires tiktoken==%s; found %s"
                % (TIKTOKEN_VERSION, observed)
            )
        import tiktoken

        self._encoding = tiktoken.get_encoding(TIKTOKEN_ENCODING)
        self.package = "tiktoken"
        self.version = observed
        self.encoding = TIKTOKEN_ENCODING
        self.test_fixture = False

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixture_source_spec(
    archive: Path,
    repository: str = "local-fixture/moss",
    revision: str = "fixture-v1",
) -> SourceSpec:
    """Build an explicitly labeled source identity for an offline test ZIP."""

    archive = Path(archive)
    with zipfile.ZipFile(archive, mode="r") as bundle:
        files = [item for item in bundle.infolist() if not item.is_dir()]
        if len(files) != 1:
            raise ValueError("fixture ZIP must contain exactly one file")
        member = files[0]
    return SourceSpec(
        repository=repository,
        revision=revision,
        license="fixture-only",
        filename=archive.name,
        archive_size=archive.stat().st_size,
        archive_sha256=sha256_file(archive),
        member_name=member.filename,
        member_compressed_size=member.compress_size,
        member_size=member.file_size,
        member_crc32=member.CRC,
        source_kind="test-fixture",
    )


def inspect_archive(
    archive: Path, source_spec: SourceSpec = OFFICIAL_SOURCE
) -> Dict[str, Any]:
    """Verify archive identity and reject unsafe or unexpected ZIP layouts."""

    archive = Path(archive)
    if not archive.is_file():
        raise FileNotFoundError("MOSS archive does not exist: %s" % archive)
    observed_size = archive.stat().st_size
    if observed_size != source_spec.archive_size:
        raise ValueError(
            "MOSS archive size mismatch: expected %d, got %d"
            % (source_spec.archive_size, observed_size)
        )
    observed_sha256 = sha256_file(archive)
    if observed_sha256 != source_spec.archive_sha256:
        raise ValueError(
            "MOSS archive checksum mismatch: expected %s, got %s"
            % (source_spec.archive_sha256, observed_sha256)
        )

    with zipfile.ZipFile(archive, mode="r") as bundle:
        members = bundle.infolist()
        if len(members) != 1 or members[0].is_dir():
            raise ValueError("MOSS ZIP must contain exactly one regular file")
        member = members[0]
        _validate_member(member, source_spec)
    return {
        "archive_path": str(archive),
        "archive_size": observed_size,
        "archive_sha256": observed_sha256,
        "member_name": member.filename,
        "member_compressed_size": member.compress_size,
        "member_size": member.file_size,
        "member_crc32": "%08x" % member.CRC,
        "compression": (
            "deflate"
            if member.compress_type == zipfile.ZIP_DEFLATED
            else "stored"
        ),
        "compression_ratio": _round(member.file_size / max(1, member.compress_size)),
        "member_crc_verified_during_stream": False,
    }


def validate_remote_metadata(
    repository_payload: Dict[str, Any], tree_payload: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Validate lightweight official API responses against the frozen source."""

    if repository_payload.get("id") != SOURCE_REPOSITORY:
        raise ValueError("Hugging Face metadata returned the wrong repository")
    if repository_payload.get("sha") != SOURCE_REVISION:
        raise ValueError("Hugging Face metadata returned the wrong revision")
    if repository_payload.get("private") is not False:
        raise ValueError("the frozen MOSS source is expected to be public")
    tags = repository_payload.get("tags") or []
    if "license:%s" % SOURCE_LICENSE not in tags:
        raise ValueError("MOSS license tag is missing or changed")

    matches = [row for row in tree_payload if row.get("path") == SOURCE_FILENAME]
    if len(matches) != 1:
        raise ValueError("frozen MOSS archive is absent or duplicated")
    file_row = matches[0]
    lfs = file_row.get("lfs") or {}
    if int(file_row.get("size", -1)) != SOURCE_ARCHIVE_SIZE:
        raise ValueError("remote MOSS archive size changed")
    if int(lfs.get("size", -1)) != SOURCE_ARCHIVE_SIZE:
        raise ValueError("remote MOSS LFS size changed")
    if lfs.get("oid") != SOURCE_ARCHIVE_SHA256:
        raise ValueError("remote MOSS LFS SHA-256 changed")
    if file_row.get("xetHash") != SOURCE_XET_HASH:
        raise ValueError("remote MOSS Xet hash changed")
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": SOURCE_REPOSITORY,
        "revision": SOURCE_REVISION,
        "license": SOURCE_LICENSE,
        "filename": SOURCE_FILENAME,
        "size": SOURCE_ARCHIVE_SIZE,
        "sha256": SOURCE_ARCHIVE_SHA256,
        "xet_hash": SOURCE_XET_HASH,
        "verified": True,
        "download_started": False,
    }


def fetch_source_metadata(output: Path) -> Dict[str, Any]:
    """Fetch only official JSON metadata; never resolve or download the ZIP."""

    repository_url = (
        "https://huggingface.co/api/datasets/%s/revision/%s"
        % (SOURCE_REPOSITORY, SOURCE_REVISION)
    )
    tree_url = (
        "https://huggingface.co/api/datasets/%s/tree/%s"
        "?recursive=true&expand=true"
        % (SOURCE_REPOSITORY, SOURCE_REVISION)
    )
    repository_payload = _read_remote_json(repository_url)
    tree_payload = _read_remote_json(tree_url)
    if not isinstance(repository_payload, dict) or not isinstance(tree_payload, list):
        raise ValueError("unexpected Hugging Face metadata shape")
    result = validate_remote_metadata(repository_payload, tree_payload)
    result["metadata_urls"] = [repository_url, tree_url]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, result)
    return result


def prepare_archive(
    archive: Path,
    output_dir: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = 0,
    source_spec: SourceSpec = OFFICIAL_SOURCE,
    token_counter: Optional[Any] = None,
) -> Dict[str, Any]:
    """Stream the entire member while retaining only a stable bounded sample."""

    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    if sample_size > 100_000:
        raise ValueError("sample_size exceeds the bounded-memory safety limit")
    inspection = inspect_archive(archive, source_spec)
    counter = token_counter if token_counter is not None else TiktokenCounter()
    _validate_counter(counter)

    heap: List[Tuple[int, str, RawTurn]] = []
    seen_conversations = set()
    raw_conversations = 0
    raw_turns = 0
    with zipfile.ZipFile(Path(archive), mode="r") as bundle:
        member_info = bundle.infolist()[0]
        with bundle.open(member_info, mode="r") as member:
            line_number = 0
            while True:
                raw_line = member.readline(MAX_JSON_LINE_BYTES + 1)
                if not raw_line:
                    break
                line_number += 1
                if len(raw_line) > MAX_JSON_LINE_BYTES:
                    raise ValueError("MOSS JSON line exceeds the safety limit")
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        "invalid MOSS JSON at member line %d" % line_number
                    ) from exc
                turns = _conversation_turns(value, line_number, seed)
                conversation_id = turns[0].conversation_id
                if conversation_id in seen_conversations:
                    raise ValueError(
                        "duplicate MOSS conversation_id %s" % conversation_id
                    )
                seen_conversations.add(conversation_id)
                raw_conversations += 1
                raw_turns += len(turns)
                for turn in turns:
                    priority = int(turn.sample_priority, 16)
                    item = (-priority, turn.concept_id, turn)
                    if len(heap) < sample_size:
                        heapq.heappush(heap, item)
                    elif priority < -heap[0][0]:
                        heapq.heapreplace(heap, item)

    selected = [item[2] for item in heap]
    selected.sort(key=lambda item: (item.sample_priority, item.concept_id))
    if len(selected) < sample_size:
        raise ValueError(
            "requested sample_size=%d but archive contains only %d turns"
            % (sample_size, len(selected))
        )
    pool_rows = [_tokenized_row(turn, counter) for turn in selected]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pool_path = output_dir / "pool.jsonl"
    _write_jsonl(pool_path, pool_rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "stage": "prepare",
        "source": _source_manifest(source_spec),
        "archive": {**inspection, "member_crc_verified_during_stream": True},
        "schema": {
            "root_fields": [
                "conversation_id",
                "meta_instruction",
                "num_turns",
                "chat",
                "category",
            ],
            "turn_fields": list(TURN_FIELDS),
        },
        "sampling": {
            "algorithm": "lowest SHA-256 priorities over all turns",
            "identity": "SHA-256(seed, context-sensitive concept_id)",
            "seed": seed,
            "requested_turns": sample_size,
            "selected_turns": len(pool_rows),
            "archive_fully_scanned": True,
            "memory_bound_turns": sample_size,
        },
        "ground_truth": {
            "concept": (
                "SHA-256 of canonical conversation_id, round, and prior "
                "context SHA-256"
            ),
            "response": "SHA-256 of the exact recorded MOSS field",
            "policy_clusters_define_correctness": False,
        },
        "token_accounting": _counter_manifest(counter),
        "token_scope": {
            "context": "meta_instruction plus all complete prior turns",
            "prompt": "current Human plus current Tool Responses fields",
            "answer": "current recorded MOSS field",
            "excluded": "current Inner Thoughts and Commands fields",
        },
        "raw_conversations": raw_conversations,
        "raw_turns": raw_turns,
        "pool_rows": len(pool_rows),
        "pool_sha256": sha256_file(pool_path),
        "no_live_llm_or_api": True,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def replay_prepared(
    prepared_dir: Path,
    output_dir: Path,
    requests: int = DEFAULT_REQUESTS,
    capacity: int = DEFAULT_CAPACITY,
    seed: int = 0,
    selection: str = "novel-long",
    repeat_passes: int = 1,
) -> Dict[str, Any]:
    """Replay exact concepts; recorded responses serve every miss."""

    if requests < 1 or capacity < 1 or repeat_passes < 1:
        raise ValueError("requests, capacity, and repeat_passes must be positive")
    if selection not in ("novel-long", "all"):
        raise ValueError("selection must be novel-long or all")
    prepared_dir = Path(prepared_dir)
    prepared_manifest = _read_json(prepared_dir / "manifest.json")
    if prepared_manifest.get("stage") != "prepare":
        raise ValueError("prepared MOSS manifest has the wrong stage")
    pool_rows = list(_read_jsonl(prepared_dir / "pool.jsonl"))
    _validate_pool(pool_rows)
    base_trace, selection_manifest = _select_trace(
        pool_rows, requests=requests, seed=seed, selection=selection
    )
    trace: List[Dict[str, Any]] = []
    for pass_index in range(repeat_passes):
        for row in base_trace:
            trace.append({**row, "pass_index": pass_index})
    workload_hash = _trace_hash(trace)
    run_id = hashlib.sha256(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "trace_hash": workload_hash,
                "capacity": capacity,
                "selection": selection,
                "repeat_passes": repeat_passes,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    request_rows, run_row = _replay_exact_lru(trace, capacity, workload_hash, run_id)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    requests_path = output_dir / "requests.jsonl"
    runs_path = output_dir / "runs.csv"
    _write_jsonl(requests_path, request_rows)
    _write_csv(runs_path, [run_row], RUN_FIELDS)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "stage": "replay",
        "source": prepared_manifest["source"],
        "archive": prepared_manifest["archive"],
        "prepared_manifest_sha256": sha256_file(prepared_dir / "manifest.json"),
        "pool_sha256": sha256_file(prepared_dir / "pool.jsonl"),
        "token_accounting": prepared_manifest["token_accounting"],
        "ground_truth": prepared_manifest["ground_truth"],
        "replay": {
            "policy": "RECORDED_RESPONSE_EXACT_LRU",
            "capacity": capacity,
            "seed": seed,
            "selection": selection,
            "repeat_passes": repeat_passes,
            "base_requests": len(base_trace),
            "requests": len(trace),
            "selection_details": selection_manifest,
            "trace_hash": workload_hash,
            "run_id": run_id,
            "miss_behavior": "deliver exact recorded MOSS response",
            "live_model_calls": 0,
        },
        "inferential_role": (
            "preregistered novel-long negative control"
            if selection == "novel-long" and repeat_passes == 1
            else "diagnostic replay"
        ),
        "limitations": [
            "This component validates recorded-response and token accounting; "
            "it is an exact-key LRU replay, not a semantic embedding run.",
            "The bounded sample is uniform by stable hash but requires a full "
            "streaming scan of the compressed JSONL member.",
        ],
        "artifacts": {
            "requests.jsonl": sha256_file(requests_path),
            "runs.csv": sha256_file(runs_path),
        },
        "no_live_llm_or_api": True,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def run_benchmark(
    archive: Path,
    output_dir: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    sample_seed: int = 0,
    requests: int = DEFAULT_REQUESTS,
    capacity: int = DEFAULT_CAPACITY,
    trace_seed: int = 0,
    selection: str = "novel-long",
    repeat_passes: int = 1,
    source_spec: SourceSpec = OFFICIAL_SOURCE,
    token_counter: Optional[Any] = None,
) -> Dict[str, Any]:
    """Atomically prepare and replay one local, checksum-pinned archive."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    names = ("pool.jsonl", "requests.jsonl", "runs.csv", "manifest.json")
    with tempfile.TemporaryDirectory(prefix=".moss-", dir=str(output_dir)) as temp:
        staging = Path(temp)
        prepare_archive(
            archive,
            staging,
            sample_size=sample_size,
            seed=sample_seed,
            source_spec=source_spec,
            token_counter=token_counter,
        )
        result = replay_prepared(
            staging,
            staging,
            requests=requests,
            capacity=capacity,
            seed=trace_seed,
            selection=selection,
            repeat_passes=repeat_passes,
        )
        result["artifacts"]["pool.jsonl"] = sha256_file(staging / "pool.jsonl")
        _write_json(staging / "manifest.json", result)
        for name in names:
            os.replace(str(staging / name), str(output_dir / name))
    return result


def _validate_member(member: zipfile.ZipInfo, source_spec: SourceSpec) -> None:
    name = member.filename
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith(("/", "\\"))
        or "\\" in name
        or any(part in ("", ".", "..") for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise ValueError("unsafe MOSS ZIP member path: %r" % name)
    mode = (member.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        raise ValueError("MOSS ZIP member must not be a symbolic link")
    if member.flag_bits & 0x1:
        raise ValueError("encrypted MOSS ZIP members are not supported")
    if member.compress_type not in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
        raise ValueError("unsupported MOSS ZIP compression method")
    ratio = member.file_size / max(1, member.compress_size)
    if ratio > MAX_COMPRESSION_RATIO:
        raise ValueError("MOSS ZIP compression ratio exceeds safety limit")
    expected = {
        "filename": (name, source_spec.member_name),
        "compressed size": (
            member.compress_size,
            source_spec.member_compressed_size,
        ),
        "uncompressed size": (member.file_size, source_spec.member_size),
        "CRC32": (member.CRC, source_spec.member_crc32),
    }
    for label, (observed, wanted) in expected.items():
        if observed != wanted:
            raise ValueError(
                "MOSS ZIP member %s mismatch: expected %r, got %r"
                % (label, wanted, observed)
            )


def _conversation_turns(value: Any, line_number: int, seed: int) -> List[RawTurn]:
    if not isinstance(value, dict):
        raise ValueError("MOSS row %d must be an object" % line_number)
    conversation_id = value.get("conversation_id")
    if isinstance(conversation_id, bool) or not isinstance(conversation_id, int):
        raise ValueError("MOSS row %d has invalid conversation_id" % line_number)
    meta_instruction = value.get("meta_instruction")
    category = value.get("category")
    num_turns = value.get("num_turns")
    chat = value.get("chat")
    if not isinstance(meta_instruction, str) or not isinstance(category, str):
        raise ValueError("MOSS row %d has invalid text metadata" % line_number)
    if isinstance(num_turns, bool) or not isinstance(num_turns, int) or num_turns < 1:
        raise ValueError("MOSS row %d has invalid num_turns" % line_number)
    if not isinstance(chat, dict):
        raise ValueError("MOSS row %d has invalid chat" % line_number)
    expected_turns = {"turn_%d" % index for index in range(1, num_turns + 1)}
    if set(chat) != expected_turns:
        raise ValueError("MOSS row %d turn keys do not match num_turns" % line_number)

    prior_turns: List[Dict[str, str]] = []
    result = []
    for round_index in range(1, num_turns + 1):
        turn = chat["turn_%d" % round_index]
        if not isinstance(turn, dict) or set(turn) != set(TURN_FIELDS):
            raise ValueError(
                "MOSS row %d turn_%d has invalid fields"
                % (line_number, round_index)
            )
        if any(not isinstance(turn[field], str) for field in TURN_FIELDS):
            raise ValueError(
                "MOSS row %d turn_%d has non-string fields"
                % (line_number, round_index)
            )
        context_text = json.dumps(
            {
                "meta_instruction": meta_instruction,
                "prior_turns": prior_turns,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        context_hash = hashlib.sha256(context_text.encode("utf-8")).hexdigest()
        concept_payload = {
            "conversation_id": conversation_id,
            "round": round_index,
            "prior_context_sha256": context_hash,
        }
        concept_digest = hashlib.sha256(
            json.dumps(
                concept_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        concept_id = "moss-" + concept_digest
        current_input = turn["Human"] + turn["Tool Responses"]
        recorded_response = turn["MOSS"]
        response_id = "moss-response-" + hashlib.sha256(
            recorded_response.encode("utf-8")
        ).hexdigest()
        sample_priority = hashlib.sha256(
            (str(seed) + "|" + concept_id).encode("utf-8")
        ).hexdigest()
        result.append(
            RawTurn(
                conversation_id=conversation_id,
                round_index=round_index,
                category=category,
                concept_id=concept_id,
                prior_context_sha256=context_hash,
                context_text=context_text,
                current_input=current_input,
                request_text=context_text + current_input,
                recorded_response=recorded_response,
                response_id=response_id,
                sample_priority=sample_priority,
            )
        )
        prior_turns.append({field: turn[field] for field in TURN_FIELDS})
    return result


def _tokenized_row(turn: RawTurn, counter: Any) -> Dict[str, Any]:
    context_tokens = int(counter.count(turn.context_text))
    prompt_tokens = int(counter.count(turn.current_input))
    answer_tokens = int(counter.count(turn.recorded_response))
    if min(context_tokens, prompt_tokens, answer_tokens) < 0:
        raise ValueError("token counter returned a negative value")
    total_tokens = context_tokens + prompt_tokens + answer_tokens
    return {
        "schema_version": SCHEMA_VERSION,
        "conversation_id": turn.conversation_id,
        "round": turn.round_index,
        "category": turn.category,
        "concept_id": turn.concept_id,
        "prior_context_sha256": turn.prior_context_sha256,
        "request_text": turn.request_text,
        "request_sha256": hashlib.sha256(
            turn.request_text.encode("utf-8")
        ).hexdigest(),
        "response_id": turn.response_id,
        "recorded_response": turn.recorded_response,
        "context_tokens": context_tokens,
        "prompt_tokens": prompt_tokens,
        "answer_tokens": answer_tokens,
        "total_tokens": total_tokens,
        "answer_token_bucket": _token_bucket(answer_tokens),
        "sample_priority": turn.sample_priority,
    }


def _select_trace(
    pool_rows: Sequence[Dict[str, Any]],
    requests: int,
    seed: int,
    selection: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if selection == "all":
        candidates = list(pool_rows)
        cutoff = None
    else:
        ordered_tokens = sorted(int(row["total_tokens"]) for row in pool_rows)
        cutoff_index = int(math.floor(0.75 * (len(ordered_tokens) - 1)))
        cutoff = ordered_tokens[cutoff_index]
        candidates = [
            row for row in pool_rows if int(row["total_tokens"]) >= cutoff
        ]
    if requests > len(candidates):
        raise ValueError(
            "requested %d trace rows but selection contains only %d"
            % (requests, len(candidates))
        )
    by_bucket: Dict[int, List[Dict[str, Any]]] = {}
    for row in candidates:
        by_bucket.setdefault(int(row["answer_token_bucket"]), []).append(row)
    for bucket, values in by_bucket.items():
        values.sort(
            key=lambda row: (
                hashlib.sha256(
                    (
                        str(seed)
                        + "|"
                        + str(bucket)
                        + "|"
                        + row["concept_id"]
                    ).encode("utf-8")
                ).hexdigest(),
                row["concept_id"],
            )
        )
    selected: List[Dict[str, Any]] = []
    positions = {bucket: 0 for bucket in by_bucket}
    while len(selected) < requests:
        progressed = False
        for bucket in sorted(by_bucket):
            position = positions[bucket]
            if position < len(by_bucket[bucket]):
                selected.append(by_bucket[bucket][position])
                positions[bucket] += 1
                progressed = True
                if len(selected) == requests:
                    break
        if not progressed:
            raise RuntimeError("stratified MOSS selection exhausted unexpectedly")
    return selected, {
        "longest_quartile_cutoff_total_tokens": cutoff,
        "candidate_rows": len(candidates),
        "answer_token_bucket_counts": {
            str(bucket): len(values) for bucket, values in sorted(by_bucket.items())
        },
        "selection_order": "stable hash within bucket, round-robin across buckets",
    }


def _replay_exact_lru(
    trace: Sequence[Dict[str, Any]],
    capacity: int,
    workload_hash: str,
    run_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    cache: "OrderedDict[str, str]" = OrderedDict()
    seen = set()
    records = []
    evictions = 0
    valid_tokens = 0
    total_tokens = 0
    for request_index, row in enumerate(trace):
        concept_id = row["concept_id"]
        expected_response = row["response_id"]
        reuse_opportunity = concept_id in seen
        cache_before = len(cache)
        raw_hit = concept_id in cache
        evicted_id = None
        if raw_hit:
            cache_response = cache.pop(concept_id)
            cache[concept_id] = cache_response
            replayed = False
        else:
            cache_response = None
            replayed = True
            if len(cache) >= capacity:
                evicted_id, _ = cache.popitem(last=False)
                evictions += 1
            cache[concept_id] = expected_response
        valid_hit = bool(raw_hit and cache_response == expected_response)
        false_hit = bool(raw_hit and not valid_hit)
        false_miss = bool(reuse_opportunity and not valid_hit)
        token_cost = int(row["total_tokens"])
        total_tokens += token_cost
        if valid_hit:
            valid_tokens += token_cost
        seen.add(concept_id)
        records.append(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "policy": "RECORDED_RESPONSE_EXACT_LRU",
                "workload": "moss-recorded-response",
                "trace_hash": workload_hash,
                "request_index": request_index,
                "pass_index": row["pass_index"],
                "conversation_id": row["conversation_id"],
                "round": row["round"],
                "category": row["category"],
                "concept_id": concept_id,
                "answer_token_bucket": row["answer_token_bucket"],
                "context_tokens": row["context_tokens"],
                "prompt_tokens": row["prompt_tokens"],
                "answer_tokens": row["answer_tokens"],
                "total_tokens": token_cost,
                "reuse_opportunity": reuse_opportunity,
                "expected_response_id": expected_response,
                "cache_response_id": cache_response,
                "delivered_response_id": expected_response,
                "raw_hit": raw_hit,
                "valid_hit": valid_hit,
                "false_hit": false_hit,
                "false_miss": false_miss,
                "recorded_response_replayed": replayed,
                "live_model_called": False,
                "evicted_concept_id": evicted_id,
                "cache_entries_before": cache_before,
                "cache_entries_after": len(cache),
            }
        )
    requests = len(records)
    raw_hits = sum(int(row["raw_hit"]) for row in records)
    valid_hits = sum(int(row["valid_hit"]) for row in records)
    false_hits = sum(int(row["false_hit"]) for row in records)
    opportunities = sum(int(row["reuse_opportunity"]) for row in records)
    replays = sum(int(row["recorded_response_replayed"]) for row in records)
    run_row = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "policy": "RECORDED_RESPONSE_EXACT_LRU",
        "workload": "moss-recorded-response",
        "trace_hash": workload_hash,
        "requests": requests,
        "capacity": capacity,
        "raw_hits": raw_hits,
        "valid_hits": valid_hits,
        "false_hits": false_hits,
        "misses": requests - raw_hits,
        "reuse_opportunities": opportunities,
        "valid_hit_rate": _ratio(valid_hits, requests),
        "false_hit_rate": _ratio(false_hits, requests),
        "hit_precision": _ratio(valid_hits, raw_hits),
        "opportunity_recall": _ratio(valid_hits, opportunities),
        "safe_token_saving_ratio": _ratio(valid_tokens, total_tokens),
        "total_tokens": total_tokens,
        "valid_saved_tokens": valid_tokens,
        "recorded_response_replays": replays,
        "live_model_calls": 0,
        "evictions": evictions,
        "final_cache_entries": len(cache),
    }
    return records, run_row


RUN_FIELDS = (
    "schema_version",
    "run_id",
    "policy",
    "workload",
    "trace_hash",
    "requests",
    "capacity",
    "raw_hits",
    "valid_hits",
    "false_hits",
    "misses",
    "reuse_opportunities",
    "valid_hit_rate",
    "false_hit_rate",
    "hit_precision",
    "opportunity_recall",
    "safe_token_saving_ratio",
    "total_tokens",
    "valid_saved_tokens",
    "recorded_response_replays",
    "live_model_calls",
    "evictions",
    "final_cache_entries",
)


def _validate_pool(rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("MOSS pool is empty")
    concepts = set()
    for index, row in enumerate(rows):
        required = {
            "conversation_id",
            "round",
            "category",
            "concept_id",
            "response_id",
            "recorded_response",
            "request_text",
            "request_sha256",
            "context_tokens",
            "prompt_tokens",
            "answer_tokens",
            "total_tokens",
            "answer_token_bucket",
        }
        if not required.issubset(row):
            raise ValueError("MOSS pool row %d is missing fields" % index)
        if row["concept_id"] in concepts:
            raise ValueError("MOSS pool contains duplicate concepts")
        concepts.add(row["concept_id"])
        expected = "moss-response-" + hashlib.sha256(
            row["recorded_response"].encode("utf-8")
        ).hexdigest()
        if row["response_id"] != expected:
            raise ValueError("MOSS pool response hash mismatch")
        observed_request = hashlib.sha256(
            row["request_text"].encode("utf-8")
        ).hexdigest()
        if row["request_sha256"] != observed_request:
            raise ValueError("MOSS pool request hash mismatch")


def _trace_hash(trace: Sequence[Dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for index, row in enumerate(trace):
        identity = {
            "index": index,
            "pass_index": row["pass_index"],
            "concept_id": row["concept_id"],
            "response_id": row["response_id"],
            "context_tokens": row["context_tokens"],
            "prompt_tokens": row["prompt_tokens"],
            "answer_tokens": row["answer_tokens"],
        }
        digest.update(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _token_bucket(tokens: int) -> int:
    if tokens <= 32:
        return 32
    if tokens <= 128:
        return 128
    if tokens <= 256:
        return 256
    return 512


def _validate_counter(counter: Any) -> None:
    for attribute in ("package", "version", "encoding", "test_fixture"):
        if not hasattr(counter, attribute):
            raise ValueError("token counter lacks %s metadata" % attribute)
    if not callable(getattr(counter, "count", None)):
        raise ValueError("token counter lacks count(text)")


def _counter_manifest(counter: Any) -> Dict[str, Any]:
    return {
        "package": str(counter.package),
        "version": str(counter.version),
        "encoding": str(counter.encoding),
        "test_fixture": bool(counter.test_fixture),
        "pinned_production_requirement": "tiktoken==%s" % TIKTOKEN_VERSION,
    }


def _source_manifest(source_spec: SourceSpec) -> Dict[str, Any]:
    value = asdict(source_spec)
    value["member_crc32"] = "%08x" % source_spec.member_crc32
    value["resolve_url"] = (
        "https://huggingface.co/datasets/%s/resolve/%s/%s"
        % (source_spec.repository, source_spec.revision, source_spec.filename)
    )
    value["revision_and_checksum_verified"] = True
    return value


def _read_remote_json(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": "carma-moss-metadata/%s" % SCHEMA_VERSION}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError("metadata request failed with HTTP %d" % response.status)
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError("metadata response exceeds safety limit")
    return json.loads(body.decode("utf-8"))


def _read_json(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object at %s" % path)
    return value


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(
                        "expected JSON object at %s:%d" % (path, line_number)
                    )
                yield value


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            )


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8") as output:
        json.dump(value, output, sort_keys=True, indent=2, ensure_ascii=False)
        output.write("\n")


def _write_csv(
    path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]
) -> None:
    if not rows:
        raise ValueError("cannot write empty MOSS runs CSV")
    with Path(path).open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _ratio(numerator: int, denominator: int) -> float:
    return _round(numerator / denominator) if denominator else 0.0


def _round(value: float) -> float:
    return round(float(value), 10)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    metadata_parser = subparsers.add_parser("source-metadata")
    metadata_parser.add_argument("--output", type=Path, required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--archive", type=Path, required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--archive", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    prepare_parser.add_argument("--seed", type=int, default=0)

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("--prepared", type=Path, required=True)
    replay_parser.add_argument("--output", type=Path, required=True)
    _add_replay_arguments(replay_parser)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--archive", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    run_parser.add_argument("--sample-seed", type=int, default=0)
    _add_replay_arguments(run_parser)
    return parser


def _add_replay_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS)
    parser.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY)
    parser.add_argument("--trace-seed", type=int, default=0)
    parser.add_argument(
        "--selection", choices=("novel-long", "all"), default="novel-long"
    )
    parser.add_argument("--repeat-passes", type=int, default=1)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "source-metadata":
        result = fetch_source_metadata(args.output)
    elif args.command == "inspect":
        result = inspect_archive(args.archive)
    elif args.command == "prepare":
        result = prepare_archive(
            args.archive,
            args.output,
            sample_size=args.sample_size,
            seed=args.seed,
        )
    elif args.command == "replay":
        result = replay_prepared(
            args.prepared,
            args.output,
            requests=args.requests,
            capacity=args.capacity,
            seed=args.trace_seed,
            selection=args.selection,
            repeat_passes=args.repeat_passes,
        )
    else:
        result = run_benchmark(
            args.archive,
            args.output,
            sample_size=args.sample_size,
            sample_seed=args.sample_seed,
            requests=args.requests,
            capacity=args.capacity,
            trace_seed=args.trace_seed,
            selection=args.selection,
            repeat_passes=args.repeat_passes,
        )
    print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
