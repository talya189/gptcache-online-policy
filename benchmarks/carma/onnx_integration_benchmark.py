"""Prospective real-text ONNX system benchmark for Gate 7.

This module intentionally does not modify or reinterpret the historical
precomputed-vector integration runs.  It builds a new, versioned evidence
bundle whose measured request path starts with real text and includes the
pinned GPTCache ALBERT ONNX embedding before SQLite/FAISS lookup and eviction
policy work.

The public process is an orchestrator.  Every policy/seed block runs in a fresh
child process and a fresh temporary SQLite/FAISS directory.  The orchestrator
samples child resources from outside the measured process and writes buffered
request records only after the child has finished.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import functools
import hashlib
import json
import math
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path
from typing import Any, Callable, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import psutil

from benchmarks.carma.integration_benchmark import (
    IntegrationConfig,
    _build_manager,
    _verify_storage,
)
from benchmarks.carma.qqp import (
    MODEL_REPOSITORY,
    MODEL_REVISION,
    TOKENIZER_REPOSITORY,
    TOKENIZER_REVISION,
)
from gptcache import Cache, Config
from gptcache.adapter.adapter import adapt


SCHEMA_VERSION = "carma-gate7-onnx-v1"
ATTEMPT_LEDGER_SCHEMA_VERSION = "carma-gate7-attempt-ledger-v2"
EXPERIMENT_ID = "gate7b-onnx-v1"
POLICIES = ("LRU", "LFU", "CARMA")
FULL_SEEDS = (20261001, 20261002, 20261003, 20261004, 20261005)
DEFAULT_CONTRACT = PROJECT_ROOT / "docs" / "project" / "gate7-remediation-contract.md"
DEFAULT_PREPARED = PROJECT_ROOT / "artifacts" / "qqp-full" / "prepared"
FORMAL_ATTEMPT_ROOT = PROJECT_ROOT / "artifacts" / "gate7-onnx-attempts"
POLICY_ORDER_NAMESPACE = "gate7b-policy-base-v1"
PINNED_MODEL_SHA256 = "a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8"
PINNED_ARCHIVE_SHA256 = "1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58"
PINNED_PAIRS_SHA256 = "c84d9897bd4838e8c12f45d59e04401031bbd5fa533570490db016cf40235126"
PINNED_TEXTS_SHA256 = "645ece94cebf36d1d37a66410d925d7d2252b6d39dd42b473e866289d1576dc3"
HISTORICAL_BASELINE_COMMIT = "021f421e80d76ab6cde10e21cff909bda236728d"
# Filled with the SHA-256 of DEFAULT_CONTRACT after the prospective protocol
# text is finalized. Full mode refuses any other contract byte sequence.
PINNED_CONTRACT_SHA256 = "e93b3f301373a0b1a1c9fa99378f555bd45b9c6e8f8717ac82ea817763ecdf4a"

EXCLUSIVE_STAGES = (
    "preprocess",
    "tokenize",
    "onnx_inference",
    "embedding_postprocess",
    "faiss_search",
    "faiss_mutation",
    "sqlite_read",
    "sqlite_write",
    "similarity_evaluation",
    "policy_exclusive",
    "response_materialization",
)
SUMMARY_STAGES = (
    "end_to_end",
    "post_embedding",
    "embedding",
    "faiss",
    "sqlite",
    "policy_exclusive",
    "policy_inclusive",
    "response_return",
    "residual",
)

OUTCOME_NAMES = (
    "hit",
    "admitted_without_eviction",
    "admitted_with_eviction",
    "rejected",
)
OUTCOME_REPORT_NAMES = ("all",) + OUTCOME_NAMES

OUTCOME_LATENCY_FIELDS: Dict[str, str] = {
    "text_preprocess_tokenize": "text_preprocess_tokenize_ns",
    "onnx_inference": "onnx_inference_ns",
    "embedding_postprocess": "embedding_postprocess_ns",
    "faiss_search": "faiss_search_ns",
    "sqlite_read": "sqlite_read_ns",
    "similarity_decision": "similarity_decision_ns",
    "policy_exclusive": "policy_exclusive_ns",
    "sqlite_write": "sqlite_write_ns",
    "faiss_mutation": "faiss_mutation_ns",
    "response_return": "response_return_ns",
    "residual": "residual_ns",
    "embedding": "embedding_ns",
    "cache_management": "cache_management_ns",
    "post_embedding": "post_embedding_total_ns",
    "request_total": "request_total_ns",
}


@dataclass(frozen=True)
class Gate7Config:
    """Configuration shared verbatim by every policy process."""

    mode: str
    requests: int
    capacity: int
    hit_threshold: float = 0.97
    topic_threshold: float = 0.70
    cell_threshold: float = 0.97
    demand_half_life: float = 500.0
    quota_strength: float = 1.0
    ghost_support_threshold: float = 1.5
    admission_margin: float = 1.05
    centroid_alpha: float = 0.05
    entry_hit_weight: float = 0.25
    onnx_intra_threads: int = 1
    onnx_inter_threads: int = 1
    warmup_requests: int = 20
    resource_interval_ms: int = 100
    max_length: int = 512
    fake_embedding: bool = False

    def validate(self) -> None:
        if self.mode not in ("smoke", "full"):
            raise ValueError("mode must be smoke or full")
        if self.requests < 10 or self.requests % 10:
            raise ValueError("requests must be at least 10 and divisible by 10")
        if self.capacity < 2:
            raise ValueError("capacity must be at least 2")
        if not 0.0 <= self.hit_threshold <= 1.0:
            raise ValueError("hit threshold must be in [0, 1]")
        if min(self.onnx_intra_threads, self.onnx_inter_threads) < 1:
            raise ValueError("ONNX thread counts must be positive")
        if self.warmup_requests < 1:
            raise ValueError("warmup requests must be positive")
        if self.resource_interval_ms < 10:
            raise ValueError("resource interval must be at least 10 ms")
        if self.max_length != 512:
            raise ValueError("the pinned ONNX model requires max_length=512")
        if self.mode == "full" and self.fake_embedding:
            raise ValueError("formal full mode forbids fake embeddings")
        if self.mode == "full":
            expected = {
                "requests": 3000,
                "capacity": 100,
                "hit_threshold": 0.97,
                "topic_threshold": 0.70,
                "cell_threshold": 0.97,
                "demand_half_life": 500.0,
                "quota_strength": 1.0,
                "ghost_support_threshold": 1.5,
                "admission_margin": 1.05,
                "centroid_alpha": 0.05,
                "entry_hit_weight": 0.25,
                "onnx_intra_threads": 1,
                "onnx_inter_threads": 1,
                "warmup_requests": 20,
                "resource_interval_ms": 100,
                "max_length": 512,
            }
            observed = asdict(self)
            mismatches = {
                key: (observed[key], value)
                for key, value in expected.items()
                if observed[key] != value
            }
            if mismatches:
                raise ValueError("formal full mode differs from frozen contract: %r" % mismatches)


class ChildExecutionError(RuntimeError):
    """A failed isolated child with the external samples retained so far."""

    def __init__(
        self,
        message: str,
        returncode: int,
        stdout: str,
        stderr: str,
        samples: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(message)
        self.returncode = int(returncode)
        self.stdout = stdout
        self.stderr = stderr
        self.samples = [dict(sample) for sample in samples]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact_identity(path: Path, rows: Optional[int] = None) -> Dict[str, Any]:
    identity: Dict[str, Any] = {
        "sha256": sha256_file(path),
        "bytes": int(path.stat().st_size),
    }
    if rows is not None:
        identity["rows"] = int(rows)
    return identity


def _source_identities() -> Dict[str, Any]:
    paths = (
        "benchmarks/carma/onnx_integration_benchmark.py",
        "benchmarks/carma/gate7_trace.py",
        "benchmarks/carma/gate7_audit.py",
        "benchmarks/carma/qqp.py",
        "benchmarks/carma/integration_benchmark.py",
        "scripts/run_onnx_integration_benchmark.sh",
        "scripts/verify_project.sh",
        "tests/project_tests/test_gate7_trace.py",
        "tests/project_tests/test_gate7_onnx_integration.py",
        "tests/project_tests/test_gate7_audit.py",
        "requirements-benchmark.lock",
        "requirements-project.lock",
        "Dockerfile.project",
    )
    identities: Dict[str, Any] = {}
    for relative in paths:
        path = PROJECT_ROOT / relative
        identities[relative] = (
            _artifact_identity(path) if path.is_file() else None
        )
    return identities


def _project_path(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(Path(path).resolve())


def _atomic_write(path: Path, writer: Callable[[Any], None], newline: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name,
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline=newline) as output:
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    def write(output: Any) -> None:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write("\n")

    _atomic_write(path, write)


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    def write(output: Any) -> None:
        for record in records:
            output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    _atomic_write(path, write)


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
    def write(output: Any) -> None:
        writer = csv.DictWriter(output, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fields})

    _atomic_write(path, write, newline="")


def _attempt_id_for(started_at_utc: datetime, head_commit: str) -> str:
    return "%s-%s" % (
        started_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        head_commit[:12],
    )


def _parse_attempt_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _valid_prior_record(
    value: Any, filename: str, directory: str
) -> bool:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("experiment_id") != EXPERIMENT_ID
        or not isinstance(value.get("config"), dict)
        or value["config"].get("mode") != "full"
        or not isinstance(value.get("prior_attempts"), list)
        or not isinstance(value.get("attempt_ledger"), dict)
    ):
        return False
    expected_kind = (
        "prospective_gate7_followup"
        if filename == "manifest.json"
        else "prospective_gate7_failed_attempt"
    )
    if value.get("kind") != expected_kind:
        return False
    if filename == "manifest.json" and value.get("formal_claimable_mode") is not True:
        return False
    if filename == "attempt-failure.json" and value.get("status") != "invalid":
        return False
    started_at = _parse_attempt_timestamp(value.get("started_at_utc"))
    git = value.get("git")
    head_commit = git.get("head_commit") if isinstance(git, dict) else None
    if (
        started_at is None
        or not isinstance(head_commit, str)
        or len(head_commit) != 40
        or any(character not in "0123456789abcdef" for character in head_commit)
        or value.get("attempt_id") != _attempt_id_for(started_at, head_commit)
    ):
        return False
    attempt_policy = value.get("attempt_policy")
    return (
        isinstance(attempt_policy, dict)
        and attempt_policy.get("formal_root")
        == "artifacts/gate7-onnx-attempts"
        and attempt_policy.get("directory") == directory
        and attempt_policy.get("eligibility")
        == "first structurally valid complete attempt in the retained predecessor chain"
        and attempt_policy.get("rerun_scope")
        == "complete five-seed, three-policy matrix under a new attempt ID"
    )


def _prior_attempts(output_dir: Path) -> List[Dict[str, Any]]:
    parent = Path(output_dir).resolve().parent
    if not parent.is_dir():
        return []
    attempts: List[Dict[str, Any]] = []
    for sibling in parent.iterdir():
        if sibling.resolve() == Path(output_dir).resolve() or not sibling.is_dir():
            continue
        record_names = [
            filename
            for filename in ("manifest.json", "attempt-failure.json")
            if (sibling / filename).is_file()
        ]
        found_record = False
        if len(record_names) == 1:
            filename = record_names[0]
            candidate = sibling / filename
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                value = None
            if _valid_prior_record(value, filename, sibling.name):
                found_record = True
                attempts.append(
                    {
                        "attempt_id": value.get("attempt_id"),
                        "kind": value.get("kind"),
                        "status": (
                            "producer_complete"
                            if filename == "manifest.json"
                            else "invalid_operational_failure"
                        ),
                        "directory": sibling.name,
                        "record": filename,
                        "record_sha256": sha256_file(candidate),
                        "record_bytes": int(candidate.stat().st_size),
                        "started_at_utc": value.get("started_at_utc"),
                    }
                )
        if not found_record and any(sibling.iterdir()):
            attempts.append(
                {
                    "attempt_id": None,
                    "kind": "unresolved_attempt_directory",
                    "status": "unresolved",
                    "directory": sibling.name,
                    "record": None,
                    "record_sha256": None,
                    "record_bytes": None,
                    "started_at_utc": None,
                }
            )
    attempts.sort(
        key=lambda item: (
            str(item.get("started_at_utc") or ""),
            str(item.get("attempt_id") or ""),
            str(item.get("directory") or ""),
        )
    )
    return attempts


def _attempts_sha256(attempts: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(attempt) for attempt in attempts],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _ledger_entry_sha256(entry: Mapping[str, Any]) -> str:
    unhashed = dict(entry)
    unhashed.pop("entry_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(unhashed)).hexdigest()


def _read_attempt_ledger(root: Path) -> Tuple[List[Dict[str, Any]], List[bytes]]:
    path = Path(root) / "attempt-ledger.jsonl"
    if not path.exists():
        return [], []
    raw_lines = path.read_bytes().splitlines(keepends=True)
    entries: List[Dict[str, Any]] = []
    previous_hash: Optional[str] = None
    starts: Dict[str, Dict[str, Any]] = {}
    terminal_attempts: set = set()
    seen_directories: set = set()
    open_attempt_id: Optional[str] = None
    start_count = 0
    for position, raw_line in enumerate(raw_lines, start=1):
        try:
            entry = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("formal attempt ledger is not valid JSONL") from exc
        if not isinstance(entry, dict):
            raise RuntimeError("formal attempt ledger row is not an object")
        if raw_line != (
            json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"):
            raise RuntimeError("formal attempt ledger is not canonically encoded")
        entry_hash = entry.get("entry_sha256")
        expected_hash = _ledger_entry_sha256(entry)
        event = entry.get("event")
        if (
            entry.get("schema_version") != ATTEMPT_LEDGER_SCHEMA_VERSION
            or entry.get("experiment_id") != EXPERIMENT_ID
            or entry.get("sequence") != position
            or entry.get("previous_entry_sha256") != previous_hash
            or entry_hash != expected_hash
            or event not in ("START", "TERMINAL")
        ):
            raise RuntimeError("formal attempt ledger integrity check failed")

        started_at = _parse_attempt_timestamp(entry.get("started_at_utc"))
        head_commit = entry.get("head_commit")
        attempt_id = entry.get("attempt_id")
        directory = entry.get("directory")
        common_identity_valid = (
            started_at is not None
            and isinstance(head_commit, str)
            and len(head_commit) == 40
            and not any(
                character not in "0123456789abcdef" for character in head_commit
            )
            and attempt_id == _attempt_id_for(started_at, head_commit)
            and isinstance(directory, str)
            and bool(directory)
            and Path(directory).name == directory
        )
        if not common_identity_valid:
            raise RuntimeError("formal attempt ledger identity check failed")

        if event == "START":
            if (
                open_attempt_id is not None
                or attempt_id in starts
                or directory in seen_directories
                or entry.get("prior_attempt_count") != start_count
                or not isinstance(entry.get("prior_attempts_sha256"), str)
                or len(str(entry.get("prior_attempts_sha256"))) != 64
            ):
                raise RuntimeError("formal attempt ledger START check failed")
            starts[str(attempt_id)] = entry
            seen_directories.add(str(directory))
            open_attempt_id = str(attempt_id)
            start_count += 1
        else:
            start = starts.get(str(attempt_id))
            terminal_record = entry.get("terminal_record")
            if (
                start is None
                or open_attempt_id != attempt_id
                or attempt_id in terminal_attempts
                or entry.get("started_at_utc") != start.get("started_at_utc")
                or head_commit != start.get("head_commit")
                or directory != start.get("directory")
                or terminal_record not in ("manifest.json", "attempt-failure.json")
                or not isinstance(entry.get("terminal_record_sha256"), str)
                or len(str(entry.get("terminal_record_sha256"))) != 64
                or not isinstance(entry.get("terminal_record_bytes"), int)
                or isinstance(entry.get("terminal_record_bytes"), bool)
                or int(entry.get("terminal_record_bytes")) <= 0
            ):
                raise RuntimeError("formal attempt ledger TERMINAL check failed")
            if terminal_record == "manifest.json":
                auditor_identity = entry.get("auditor_identity")
                if (
                    entry.get("preterminal_report")
                    != "gate7-preterminal-adjudication.json"
                    or not isinstance(entry.get("preterminal_report_sha256"), str)
                    or len(str(entry.get("preterminal_report_sha256"))) != 64
                    or not isinstance(entry.get("preterminal_report_bytes"), int)
                    or isinstance(entry.get("preterminal_report_bytes"), bool)
                    or int(entry.get("preterminal_report_bytes")) <= 0
                    or entry.get("preterminal_report_status")
                    not in ("pass", "fail", "pending", "invalid")
                    or not isinstance(entry.get("preterminal_report_claimable"), bool)
                    or entry.get("preterminal_report_claimable")
                    != (entry.get("preterminal_report_status") in ("pass", "fail"))
                    or not isinstance(auditor_identity, dict)
                    or auditor_identity.get("path")
                    != "benchmarks/carma/gate7_audit.py"
                    or not isinstance(auditor_identity.get("sha256"), str)
                    or len(str(auditor_identity.get("sha256"))) != 64
                    or not isinstance(auditor_identity.get("bytes"), int)
                    or isinstance(auditor_identity.get("bytes"), bool)
                    or int(auditor_identity.get("bytes")) <= 0
                ):
                    raise RuntimeError(
                        "formal attempt ledger completed TERMINAL check failed"
                    )
            elif (
                any(
                    entry.get(field) is not None
                    for field in (
                        "preterminal_report",
                        "preterminal_report_sha256",
                        "preterminal_report_bytes",
                        "auditor_identity",
                    )
                )
                or entry.get("preterminal_report_status") != "invalid"
                or entry.get("preterminal_report_claimable") is not False
            ):
                raise RuntimeError(
                    "formal attempt ledger failure TERMINAL carries adjudication"
                )
            terminal_attempts.add(str(attempt_id))
            open_attempt_id = None
        previous_hash = entry_hash
        entries.append(entry)
    return entries, raw_lines


def _append_attempt_ledger_entry(root: Path, entry: Mapping[str, Any]) -> None:
    """Append one canonical ledger event and force it to stable storage."""

    path = Path(root) / "attempt-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json_bytes(entry) + b"\n"
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("failed to append formal attempt ledger event")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ledger_prefix_identity(root: Path, entry: Mapping[str, Any]) -> Dict[str, Any]:
    ledger_path = Path(root) / "attempt-ledger.jsonl"
    entries, _ = _read_attempt_ledger(root)
    identity = _artifact_identity(ledger_path, rows=len(entries))
    return {
        "path": "attempt-ledger.jsonl",
        "prefix_rows": len(entries),
        "prefix_sha256": identity["sha256"],
        "prefix_bytes": identity["bytes"],
        "entry_sha256": entry["entry_sha256"],
        "event": entry["event"],
    }


def _register_formal_attempt(
    output_dir: Path,
    attempt_id: str,
    started_at_utc: datetime,
    head_commit: str,
    prior_attempts: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    root = Path(output_dir).resolve().parent
    entries, _ = _read_attempt_ledger(root)
    starts = [entry for entry in entries if entry.get("event") == "START"]
    terminals = {
        str(entry["attempt_id"]): entry
        for entry in entries
        if entry.get("event") == "TERMINAL"
    }
    if len(starts) != len(terminals):
        raise RuntimeError(
            "formal attempt ledger contains an unmatched START; resolve the retained attempt before registering another"
        )
    if len(starts) != len(prior_attempts):
        raise RuntimeError(
            "formal attempt ledger length differs from retained predecessor chain"
        )
    for position, (entry, predecessor) in enumerate(
        zip(starts, prior_attempts), start=1
    ):
        terminal = terminals.get(str(entry.get("attempt_id")))
        record_path = (
            root
            / str(predecessor.get("directory"))
            / str(predecessor.get("record"))
        )
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "formal attempt ledger predecessor is unreadable"
            ) from exc
        if (
            terminal is None
            or entry.get("attempt_id") != predecessor.get("attempt_id")
            or entry.get("started_at_utc") != predecessor.get("started_at_utc")
            or entry.get("directory") != predecessor.get("directory")
            or entry.get("prior_attempt_count") != position - 1
            or not isinstance(record, dict)
            or entry.get("prior_attempts_sha256")
            != _attempts_sha256(record.get("prior_attempts", []))
            or terminal.get("terminal_record") != predecessor.get("record")
            or terminal.get("terminal_record_sha256")
            != predecessor.get("record_sha256")
            or terminal.get("terminal_record_bytes")
            != predecessor.get("record_bytes")
        ):
            raise RuntimeError(
                "formal attempt ledger differs from retained predecessor records"
            )
        if terminal.get("terminal_record") == "manifest.json":
            report_path = root / str(entry["directory"]) / str(
                terminal.get("preterminal_report")
            )
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "formal attempt ledger preterminal adjudication is unreadable"
                ) from exc
            if (
                not report_path.is_file()
                or terminal.get("preterminal_report_sha256")
                != sha256_file(report_path)
                or terminal.get("preterminal_report_bytes")
                != int(report_path.stat().st_size)
                or not isinstance(report, dict)
                or report.get("status")
                != terminal.get("preterminal_report_status")
                or report.get("claimable")
                != terminal.get("preterminal_report_claimable")
                or report.get("auditor_identity")
                != terminal.get("auditor_identity")
            ):
                raise RuntimeError(
                    "formal attempt ledger preterminal adjudication was altered"
                )
            if terminal.get("preterminal_report_status") in ("pass", "fail"):
                raise RuntimeError(
                    "a TERMINAL-bound claimable Gate 7 attempt already exists in the retained formal root"
                )
    entry: Dict[str, Any] = {
        "schema_version": ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "sequence": len(entries) + 1,
        "event": "START",
        "attempt_id": attempt_id,
        "started_at_utc": started_at_utc.isoformat(),
        "head_commit": head_commit,
        "directory": Path(output_dir).name,
        "prior_attempt_count": len(prior_attempts),
        "prior_attempts_sha256": _attempts_sha256(prior_attempts),
        "previous_entry_sha256": (
            entries[-1]["entry_sha256"] if entries else None
        ),
    }
    entry["entry_sha256"] = _ledger_entry_sha256(entry)
    _append_attempt_ledger_entry(root, entry)
    return _ledger_prefix_identity(root, entry)


def _append_formal_attempt_terminal(
    output_dir: Path,
    terminal_record: str,
    preterminal_report: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Bind immutable terminal evidence to the registered formal START."""

    output_dir = Path(output_dir).resolve()
    root = output_dir.parent
    entries, _ = _read_attempt_ledger(root)
    if not entries or entries[-1].get("event") != "START":
        raise RuntimeError("formal attempt has no unmatched terminal START")
    start = entries[-1]
    if start.get("directory") != output_dir.name:
        raise RuntimeError("formal terminal directory differs from the open START")
    if terminal_record not in ("manifest.json", "attempt-failure.json"):
        raise ValueError("unknown formal terminal record")
    record_path = output_dir / terminal_record
    if not record_path.is_file():
        raise RuntimeError("formal terminal record is missing")

    report_path: Optional[Path] = None
    if terminal_record == "manifest.json":
        if not isinstance(preterminal_report, Mapping):
            raise RuntimeError("completed formal attempt has no preterminal report")
        report_name = "gate7-preterminal-adjudication.json"
        report_path = output_dir / report_name
        if not report_path.is_file():
            raise RuntimeError("formal preterminal adjudication artifact is missing")
        status = preterminal_report.get("status")
        claimable = preterminal_report.get("claimable")
        auditor_identity = preterminal_report.get("auditor_identity")
        try:
            terminal_manifest = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("formal terminal manifest is unreadable") from exc
        source_identities = (
            terminal_manifest.get("source_identities")
            if isinstance(terminal_manifest, dict)
            else None
        )
        frozen_auditor = (
            source_identities.get("benchmarks/carma/gate7_audit.py")
            if isinstance(source_identities, dict)
            else None
        )
        if (
            status not in ("pass", "fail", "pending", "invalid")
            or not isinstance(claimable, bool)
            or claimable != (status in ("pass", "fail"))
            or not isinstance(auditor_identity, dict)
            or preterminal_report.get("audit_phase") != "preterminal"
            or preterminal_report.get("attempt_id") != start["attempt_id"]
            or frozen_auditor != auditor_identity
        ):
            raise RuntimeError("formal preterminal adjudication is malformed")
    else:
        report_name = None
        status = "invalid"
        claimable = False
        auditor_identity = None

    entry: Dict[str, Any] = {
        "schema_version": ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "sequence": len(entries) + 1,
        "event": "TERMINAL",
        "attempt_id": start["attempt_id"],
        "started_at_utc": start["started_at_utc"],
        "head_commit": start["head_commit"],
        "directory": start["directory"],
        "terminal_record": terminal_record,
        "terminal_record_sha256": sha256_file(record_path),
        "terminal_record_bytes": int(record_path.stat().st_size),
        "preterminal_report": report_name,
        "preterminal_report_sha256": (
            sha256_file(report_path) if report_path is not None else None
        ),
        "preterminal_report_bytes": (
            int(report_path.stat().st_size) if report_path is not None else None
        ),
        "preterminal_report_status": status,
        "preterminal_report_claimable": claimable,
        "auditor_identity": dict(auditor_identity) if auditor_identity else None,
        "previous_entry_sha256": entries[-1]["entry_sha256"],
    }
    entry["entry_sha256"] = _ledger_entry_sha256(entry)
    _append_attempt_ledger_entry(root, entry)
    return _ledger_prefix_identity(root, entry)


@contextmanager
def _formal_attempt_lock(enabled: bool) -> Iterable[None]:
    """Serialize formal attempt discovery and execution on this checkout."""

    if not enabled:
        yield
        return
    FORMAL_ATTEMPT_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = FORMAL_ATTEMPT_ROOT / ".formal-attempt.lock"
    descriptor = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise RuntimeError(
                "another formal Gate 7 attempt already holds the checkout lock"
            ) from exc
        metadata = json.dumps(
            {
                "pid": os.getpid(),
                "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        os.ftruncate(descriptor, 0)
        os.write(descriptor, metadata)
        os.fsync(descriptor)
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _retain_attempt_failure(
    output_dir: Path,
    attempt_id: str,
    started_at_utc: datetime,
    seed: int,
    policy: str,
    order_position: int,
    error: BaseException,
    samples: Sequence[Mapping[str, Any]],
    config: Gate7Config,
    contract_identity: Mapping[str, Any],
    git_state: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    prior_attempts: Sequence[Mapping[str, Any]],
    attempt_ledger: Optional[Mapping[str, Any]],
    child_stdout: Optional[str] = None,
    child_stderr: Optional[str] = None,
) -> None:
    """Persist an operationally invalid attempt before propagating its error."""

    artifacts: Dict[str, Any] = {}
    traces_dir = Path(output_dir) / "traces"
    if traces_dir.is_dir():
        for trace_path in sorted(traces_dir.glob("*.jsonl")):
            relative = str(trace_path.relative_to(output_dir))
            artifacts[relative] = _artifact_identity(
                trace_path,
                rows=sum(
                    1
                    for line in trace_path.read_text(encoding="utf-8").splitlines()
                    if line
                ),
            )
    if samples:
        resources_path = Path(output_dir) / "failed-resources.jsonl"
        _write_jsonl(resources_path, (dict(sample) for sample in samples))
        artifacts[resources_path.name] = _artifact_identity(
            resources_path, rows=len(samples)
        )
    captured_stdout = (
        error.stdout if isinstance(error, ChildExecutionError) else child_stdout
    )
    captured_stderr = (
        error.stderr if isinstance(error, ChildExecutionError) else child_stderr
    )
    if captured_stdout is not None or captured_stderr is not None:
        for filename, content in (
            ("child.stdout.log", captured_stdout or ""),
            ("child.stderr.log", captured_stderr or ""),
        ):
            path = Path(output_dir) / filename

            def write_log(output: Any, value: str = content) -> None:
                output.write(value)

            _atomic_write(path, write_log)
            artifacts[filename] = _artifact_identity(path)
    failure = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "kind": "prospective_gate7_failed_attempt",
        "status": "invalid",
        "started_at_utc": started_at_utc.isoformat(),
        "failed_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "policy": policy,
        "order_position": order_position,
        "error_type": type(error).__name__,
        "error": str(error),
        "child_exit_status": (
            error.returncode if isinstance(error, ChildExecutionError) else None
        ),
        "config": asdict(config),
        "prior_attempt_exists": bool(prior_attempts),
        "prior_attempts": [dict(attempt) for attempt in prior_attempts],
        "attempt_policy": {
            "formal_root": _project_path(FORMAL_ATTEMPT_ROOT),
            "directory": Path(output_dir).name,
            "eligibility": "first structurally valid complete attempt in the retained predecessor chain",
            "rerun_scope": "complete five-seed, three-policy matrix under a new attempt ID",
        },
        "attempt_ledger": dict(attempt_ledger) if attempt_ledger else None,
        "contract": {
            "path": _project_path(DEFAULT_CONTRACT),
            **dict(contract_identity),
        },
        "git": dict(git_state),
        "source_identities": dict(source_snapshot),
        "artifacts": artifacts,
        "rerun_rule": "retain this attempt and rerun the complete five-seed, three-policy matrix under a new attempt ID",
    }
    _write_json(Path(output_dir) / "attempt-failure.json", failure)


def _git_state() -> Dict[str, Any]:
    def command(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=str(PROJECT_ROOT), text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        commit = command("rev-parse", "HEAD")
        status = command("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError):
        return {"head_commit": None, "worktree_dirty": None, "status": None}
    return {
        "head_commit": commit,
        "worktree_dirty": bool(status),
        "status": status,
    }


def _assert_source_snapshot(
    expected: Mapping[str, Any], expected_head: Optional[str]
) -> None:
    observed = _source_identities()
    if observed != dict(expected):
        raise RuntimeError("Gate 7 source files changed during the benchmark attempt")
    if expected_head is not None:
        git_state = _git_state()
        if (
            git_state.get("head_commit") != expected_head
            or git_state.get("worktree_dirty") is not False
        ):
            raise RuntimeError(
                "formal Gate 7 Git state changed during the benchmark attempt"
            )


def _validate_sources_are_tracked_at_head(
    head_commit: str,
    source_identities: Mapping[str, Any],
    contract_path: Path,
) -> None:
    identities = dict(source_identities)
    identities[_project_path(contract_path)] = _artifact_identity(contract_path)
    for relative, identity in identities.items():
        if not isinstance(identity, dict):
            raise RuntimeError("formal source identity is missing: %s" % relative)
        try:
            blob = subprocess.check_output(
                ["git", "show", "%s:%s" % (head_commit, relative)],
                cwd=str(PROJECT_ROOT),
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                "formal source is not tracked at the recorded HEAD: %s" % relative
            ) from exc
        observed = {
            "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob),
        }
        if observed != {
            "sha256": identity.get("sha256"),
            "bytes": identity.get("bytes"),
        }:
            raise RuntimeError(
                "formal source bytes differ from recorded HEAD: %s" % relative
            )


def _environment() -> Dict[str, Any]:
    import cachetools
    import faiss
    import onnxruntime
    import sqlalchemy
    import transformers

    try:
        load_average: Optional[Tuple[float, float, float]] = tuple(os.getloadavg())
    except (AttributeError, OSError):
        load_average = None
    filesystem: Dict[str, Any] = {
        "available": False,
        "device_sha256": None,
        "mountpoint": None,
        "fstype": None,
        "options": None,
    }
    try:
        root = str(PROJECT_ROOT.resolve())
        matches = [
            partition
            for partition in psutil.disk_partitions(all=True)
            if root == partition.mountpoint
            or root.startswith(partition.mountpoint.rstrip(os.sep) + os.sep)
        ]
        if matches:
            partition = max(matches, key=lambda item: len(item.mountpoint))
            filesystem = {
                "available": True,
                "device_sha256": hashlib.sha256(
                    partition.device.encode("utf-8")
                ).hexdigest(),
                "mountpoint": partition.mountpoint,
                "fstype": partition.fstype,
                "options": partition.opts,
            }
    except (OSError, psutil.Error):
        pass
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, OSError, psutil.Error):
        battery = None
    power = {
        "available": battery is not None,
        "percent": float(battery.percent) if battery is not None else None,
        "plugged": bool(battery.power_plugged) if battery is not None else None,
        "seconds_left": (
            int(battery.secsleft)
            if battery is not None and battery.secsleft is not None
            else None
        ),
    }
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "hostname_sha256": hashlib.sha256(
            platform.node().encode("utf-8")
        ).hexdigest(),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "load_average_at_manifest": load_average,
        "filesystem": filesystem,
        "power": power,
        "total_memory_bytes": int(psutil.virtual_memory().total),
        "numpy": np.__version__,
        "faiss": getattr(faiss, "__version__", "unknown"),
        "sqlalchemy": sqlalchemy.__version__,
        "cachetools": cachetools.__version__,
        "psutil": psutil.__version__,
        "onnxruntime": onnxruntime.__version__,
        "transformers": transformers.__version__,
    }


def policy_schedule(seeds: Sequence[int]) -> Dict[int, Tuple[str, ...]]:
    """Return a deterministic near-balanced schedule independent of input order."""

    unique = sorted(set(int(seed) for seed in seeds))
    if len(unique) != len(seeds):
        raise ValueError("seeds must be unique")
    base = tuple(
        sorted(
            POLICIES,
            key=lambda policy: hashlib.sha256(
                (POLICY_ORDER_NAMESPACE + "|" + policy).encode("utf-8")
            ).digest(),
        )
    )
    patterns = (
        base,
        base[1:] + base[:1],
        base[2:] + base[:2],
        tuple(reversed(base)),
        tuple(reversed(base[1:] + base[:1])),
        tuple(reversed(base[2:] + base[:2])),
    )
    return {seed: patterns[index % len(patterns)] for index, seed in enumerate(unique)}


def _percentile_ns(values: Sequence[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(int(value) for value in values)
    index = max(0, int(math.ceil(quantile * len(ordered))) - 1)
    return ordered[index]


def _latency_summary(values: Sequence[int], name: str) -> Dict[str, float]:
    if not values:
        raise ValueError("cannot summarize an empty latency sequence")
    return {
        "%s_mean_us" % name: round(sum(values) / len(values) / 1000.0, 6),
        "%s_p50_us" % name: round(_percentile_ns(values, 0.50) / 1000.0, 6),
        "%s_p95_us" % name: round(_percentile_ns(values, 0.95) / 1000.0, 6),
        "%s_p99_us" % name: round(_percentile_ns(values, 0.99) / 1000.0, 6),
    }


def _outcome_name(row: Mapping[str, Any]) -> str:
    if row.get("raw_hit") is True:
        return "hit"
    if row.get("rejected") is True:
        return "rejected"
    if row.get("admitted") is True and row.get("evicted") is True:
        return "admitted_with_eviction"
    if row.get("admitted") is True and row.get("evicted") is False:
        return "admitted_without_eviction"
    raise RuntimeError("request has no frozen outcome classification")


def _outcome_latency_records(
    request_rows: Sequence[Mapping[str, Any]],
    run_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Summarize every named stage for the whole run and four outcomes."""

    grouped: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in request_rows:
        grouped[str(row["run_id"])].append(row)
    records: List[Dict[str, Any]] = []
    for run in run_rows:
        run_id = str(run["run_id"])
        by_outcome: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for row in grouped.get(run_id, []):
            by_outcome[_outcome_name(row)].append(row)
        if sum(len(values) for values in by_outcome.values()) != int(run["requests"]):
            raise RuntimeError("outcome summaries do not partition the request run")
        all_rows = grouped.get(run_id, [])
        for outcome in OUTCOME_REPORT_NAMES:
            rows = all_rows if outcome == "all" else by_outcome.get(outcome, [])
            latency: Dict[str, Any] = {}
            for stage, field in OUTCOME_LATENCY_FIELDS.items():
                values = [int(row[field]) for row in rows]
                latency[stage] = (
                    {
                        "mean_ns": sum(values) / len(values),
                        "p50_ns": _percentile_ns(values, 0.50),
                        "p95_ns": _percentile_ns(values, 0.95),
                        "p99_ns": _percentile_ns(values, 0.99),
                    }
                    if values
                    else {
                        "mean_ns": None,
                        "p50_ns": None,
                        "p95_ns": None,
                        "p99_ns": None,
                    }
                )
            records.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "experiment_id": EXPERIMENT_ID,
                    "attempt_id": run["attempt_id"],
                    "run_id": run_id,
                    "seed": int(run["seed"]),
                    "policy": run["policy"],
                    "outcome": outcome,
                    "samples": len(rows),
                    "latency": latency,
                }
            )
    return records


def _timer_overhead_ns(samples: int = 10000) -> Dict[str, int]:
    values = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        values.append(time.perf_counter_ns() - started)
    return {
        "samples": samples,
        "median_ns": _percentile_ns(values, 0.50),
        "p95_ns": _percentile_ns(values, 0.95),
    }


class ExclusiveStageRecorder:
    """Record exclusive nested storage/policy time without double counting."""

    def __init__(self) -> None:
        self.active = False
        self.current: DefaultDict[str, int] = defaultdict(int)
        self.stack: List[Dict[str, Any]] = []
        self.last_policy_result: Any = None

    def begin(self) -> None:
        if self.active:
            raise RuntimeError("stage recorder is already active")
        self.active = True
        self.current = defaultdict(int)
        self.stack = []
        self.last_policy_result = None

    def finish(self) -> Dict[str, int]:
        if self.stack:
            raise RuntimeError("stage recorder finished with active nested spans")
        self.active = False
        result = {stage: int(self.current.get(stage, 0)) for stage in EXCLUSIVE_STAGES}
        result["policy_inclusive"] = int(self.current.get("policy_inclusive", 0))
        return result

    def record(self, category: str, elapsed_ns: int) -> None:
        """Charge an already measured leaf operation to the active request."""

        if not self.active:
            return
        elapsed = int(elapsed_ns)
        if elapsed < 0:
            raise ValueError("stage duration cannot be negative")
        self.current[category] += elapsed

    def wrap(
        self,
        obj: Any,
        method_name: str,
        category: str,
        capture_policy_result: bool = False,
    ) -> None:
        original = getattr(obj, method_name, None)
        if not callable(original):
            return

        @functools.wraps(original)
        def measured(*args: Any, **kwargs: Any) -> Any:
            if not self.active:
                return original(*args, **kwargs)
            has_policy_ancestor = any(
                frame["category"] == "policy_exclusive" for frame in self.stack
            )
            frame = {
                "category": category,
                "started_ns": time.perf_counter_ns(),
                "child_ns": 0,
            }
            self.stack.append(frame)
            result: Any = None
            try:
                result = original(*args, **kwargs)
                return result
            finally:
                elapsed = time.perf_counter_ns() - int(frame["started_ns"])
                popped = self.stack.pop()
                if popped is not frame:
                    raise RuntimeError("stage recorder stack corruption")
                exclusive = max(0, elapsed - int(frame["child_ns"]))
                self.current[category] += exclusive
                if category == "policy_exclusive" and not has_policy_ancestor:
                    self.current["policy_inclusive"] += elapsed
                    if capture_policy_result:
                        self.last_policy_result = result
                if self.stack:
                    self.stack[-1]["child_ns"] += elapsed

        setattr(obj, method_name, measured)


class _TimedSimilarity(float):
    """Float whose adapter threshold comparison is charged to the decision stage."""

    def __new__(cls, value: float, recorder: ExclusiveStageRecorder) -> "_TimedSimilarity":
        instance = float.__new__(cls, value)
        instance.recorder = recorder
        return instance

    def __ge__(self, other: Any) -> bool:
        started = time.perf_counter_ns()
        result = bool(float(self) >= float(other))
        self.recorder.record(
            "similarity_evaluation", time.perf_counter_ns() - started
        )
        return result


class CosineDistanceEvaluation:
    """Convert normalized FAISS squared L2 distance back to cosine similarity."""

    def __init__(self, recorder: ExclusiveStageRecorder) -> None:
        self.recorder = recorder
        self.last_similarity: Optional[float] = None

    def evaluation(self, _query: Dict[str, Any], cached: Dict[str, Any], **_: Any) -> float:
        started = time.perf_counter_ns()
        distance = float(cached["search_result"][0])
        similarity = max(0.0, min(1.0, 1.0 - 0.5 * distance))
        self.last_similarity = similarity
        self.recorder.record(
            "similarity_evaluation", time.perf_counter_ns() - started
        )
        return _TimedSimilarity(similarity, self.recorder)

    @staticmethod
    def range() -> Tuple[float, float]:
        return 0.0, 1.0


def _instrument_manager(manager: Any, recorder: ExclusiveStageRecorder) -> None:
    for method_name in ("get_data_by_id", "peek_data_by_id", "get_ids", "count"):
        recorder.wrap(manager.s, method_name, "sqlite_read")
    for method_name in ("batch_insert", "mark_deleted", "clear_deleted_data", "flush"):
        recorder.wrap(manager.s, method_name, "sqlite_write")
    recorder.wrap(manager.v, "search", "faiss_search")
    for method_name in ("mul_add", "delete", "rebuild", "flush"):
        recorder.wrap(manager.v, method_name, "faiss_mutation")
    recorder.wrap(
        manager.eviction_base,
        "put_with_metadata",
        "policy_exclusive",
        capture_policy_result=True,
    )
    recorder.wrap(
        manager.eviction_base,
        "put",
        "policy_exclusive",
        capture_policy_result=True,
    )
    recorder.wrap(manager.eviction_base, "get", "policy_exclusive")


def _normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).strip().split())


class PinnedOnnxEmbedder:
    """Offline loader for the exact tokenizer/model revisions frozen by QQP."""

    def __init__(
        self,
        tokenizer_path: Path,
        model_path: Path,
        intra_threads: int,
        inter_threads: int,
        max_length: int,
    ) -> None:
        import onnxruntime
        from transformers import AutoConfig, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            str(tokenizer_path), local_files_only=True
        )
        config = AutoConfig.from_pretrained(str(tokenizer_path), local_files_only=True)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = intra_threads
        options.inter_op_num_threads = inter_threads
        options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        self.session = onnxruntime.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.dimension = int(config.hidden_size)
        self.max_length = max_length
        self.providers = list(self.session.get_providers())
        input_shapes = {item.name: tuple(item.shape) for item in self.session.get_inputs()}
        sequence_lengths = {
            int(shape[1])
            for shape in input_shapes.values()
            if len(shape) > 1 and isinstance(shape[1], int)
        }
        if len(sequence_lengths) > 1:
            raise RuntimeError("pinned ONNX inputs disagree on sequence length")
        if sequence_lengths and next(iter(sequence_lengths)) != max_length:
            raise ValueError("pinned ONNX model requires a different max length")

    def embed(self, text: str) -> Tuple[np.ndarray, Dict[str, int]]:
        tokenize_start = time.perf_counter_ns()
        encoded = self.tokenizer(
            text,
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
            return_tensors="np",
        )
        input_ids = np.asarray(encoded["input_ids"], dtype=np.int64)
        attention_mask = np.asarray(encoded["attention_mask"], dtype=np.int64)
        inputs = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": np.asarray(
                encoded.get("token_type_ids", np.zeros_like(input_ids)), dtype=np.int64
            ),
        }
        tokenize_ns = time.perf_counter_ns() - tokenize_start
        inference_start = time.perf_counter_ns()
        token_embeddings = self.session.run(None, inputs)[0]
        inference_ns = time.perf_counter_ns() - inference_start
        post_start = time.perf_counter_ns()
        mask = np.expand_dims(attention_mask, -1).astype(np.float32)
        pooled = np.sum(token_embeddings * mask, axis=1) / np.maximum(
            np.sum(mask, axis=1), 1e-9
        )
        vector = pooled[0]
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 0:
            raise RuntimeError("ONNX model produced an invalid embedding")
        normalized = (vector / norm).astype(np.float32)
        if normalized.shape != (self.dimension,):
            raise RuntimeError("ONNX model produced an unexpected embedding shape")
        if not np.all(np.isfinite(normalized)):
            raise RuntimeError("ONNX model produced a non-finite embedding")
        normalized_norm = float(np.linalg.norm(normalized))
        if not math.isclose(normalized_norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise RuntimeError("ONNX model produced a non-unit embedding")
        post_ns = time.perf_counter_ns() - post_start
        return normalized, {
            "tokenize": tokenize_ns,
            "onnx_inference": inference_ns,
            "embedding_postprocess": post_ns,
        }


class DeterministicFakeEmbedder:
    """Bounded smoke-only embedder; formal mode rejects this implementation."""

    dimension = 32
    providers = ["deterministic-fake"]

    def embed(self, text: str) -> Tuple[np.ndarray, Dict[str, int]]:
        started = time.perf_counter_ns()
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        raw = np.frombuffer(seed, dtype=np.uint8).astype(np.float32) - 127.5
        vector = raw / np.linalg.norm(raw)
        post_ns = time.perf_counter_ns() - started
        return vector, {
            "tokenize": 0,
            "onnx_inference": 0,
            "embedding_postprocess": post_ns,
        }


def _resolve_onnx_assets() -> Dict[str, Any]:
    from huggingface_hub import hf_hub_download, snapshot_download

    model_path = Path(
        hf_hub_download(
            repo_id=MODEL_REPOSITORY,
            filename="model.onnx",
            revision=MODEL_REVISION,
        )
    ).resolve()
    tokenizer_path = Path(
        snapshot_download(
            repo_id=TOKENIZER_REPOSITORY,
            revision=TOKENIZER_REVISION,
            allow_patterns=("*.json", "*.txt", "tokenizer.*", "*.model"),
        )
    ).resolve()
    tokenizer_files = {}
    for item in sorted(tokenizer_path.rglob("*")):
        if item.is_file():
            tokenizer_files[str(item.relative_to(tokenizer_path))] = sha256_file(item)
    tokenizer_digest = hashlib.sha256(
        json.dumps(tokenizer_files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    model_sha256 = sha256_file(model_path)
    if model_sha256 != PINNED_MODEL_SHA256:
        raise RuntimeError(
            "pinned ONNX model hash mismatch: %s" % model_sha256
        )
    return {
        "kind": "pinned_onnx",
        "model_repository": MODEL_REPOSITORY,
        "model_revision": MODEL_REVISION,
        "model_path": str(model_path),
        "model_sha256": model_sha256,
        "tokenizer_repository": TOKENIZER_REPOSITORY,
        "tokenizer_revision": TOKENIZER_REVISION,
        "tokenizer_path": str(tokenizer_path),
        "tokenizer_files": tokenizer_files,
        "tokenizer_digest_sha256": tokenizer_digest,
        "provider": "CPUExecutionProvider",
    }


def _fake_assets() -> Dict[str, Any]:
    return {
        "kind": "deterministic_fake_smoke_only",
        "model_repository": None,
        "model_revision": None,
        "model_path": None,
        "model_sha256": hashlib.sha256(b"gate7-deterministic-fake-v1").hexdigest(),
        "tokenizer_repository": None,
        "tokenizer_revision": None,
        "tokenizer_path": None,
        "tokenizer_files": {},
        "tokenizer_digest_sha256": None,
        "provider": "deterministic-fake",
    }


def _integration_config(config: Gate7Config, seed: int) -> IntegrationConfig:
    return IntegrationConfig(
        mode="full" if config.mode == "full" else "smoke",
        workload="pollution_scan",
        seed=seed,
        requests=config.requests,
        capacity=config.capacity,
        hit_threshold=config.hit_threshold,
        sample_every=max(1, config.requests),
        topic_threshold=config.topic_threshold,
        cell_threshold=config.cell_threshold,
        demand_half_life=config.demand_half_life,
        quota_strength=config.quota_strength,
        ghost_support_threshold=config.ghost_support_threshold,
        admission_margin=config.admission_margin,
        centroid_alpha=config.centroid_alpha,
        entry_hit_weight=config.entry_hit_weight,
    )


def _run_id(
    policy: str,
    seed: int,
    trace_hash: str,
    config: Gate7Config,
    model_sha256: str,
    attempt_id: str,
) -> str:
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy": policy,
        "seed": seed,
        "trace_hash": trace_hash,
        "config": asdict(config),
        "model_sha256": model_sha256,
        "attempt_id": attempt_id,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _load_trace(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            required = {
                "index",
                "request_id",
                "phase",
                "occurrence",
                "reuse_opportunity",
                "text_id",
                "text",
                "concept_id",
                "response_id",
                "response_payload",
            }
            if set(row) != required:
                raise ValueError("trace row %d has unexpected fields" % line_number)
            if int(row["index"]) != len(rows):
                raise ValueError("trace indices must be contiguous")
            rows.append(row)
    if not rows:
        raise ValueError("trace is empty")
    return rows


def _trace_digest(rows: Sequence[Dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _answer_payload(cache_data: Any) -> Optional[str]:
    if cache_data is None or not cache_data.answers:
        return None
    return str(cache_data.answers[0].answer)


def _materialize_response(payload: str) -> Dict[str, Any]:
    prefix = "recorded-response:"
    if not isinstance(payload, str) or not payload.startswith(prefix):
        raise ValueError("recorded response payload has an unexpected schema")
    concept_id = payload[len(prefix) :]
    if not concept_id:
        raise ValueError("recorded response payload has an empty concept")
    return {
        "concept_id": concept_id,
        "response_id": concept_id,
        "text": payload,
    }


def _high_water_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform.startswith("linux"):
        return value * 1024
    return value


def _write_control_state(control_dir: Path, state: str, request_index: int) -> None:
    _write_json(
        Path(control_dir) / "state.json",
        {
            "state": state,
            "pid": os.getpid(),
            "request_index": request_index,
            "monotonic_ns": time.perf_counter_ns(),
        },
    )


def _wait_for_ack(path: Path, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not Path(path).is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError("resource supervisor acknowledgement timed out")
        time.sleep(0.005)


def _policy_action(policy: str, raw_hit: bool, recorder: ExclusiveStageRecorder) -> Dict[str, Any]:
    if raw_hit:
        return {"action": "hit", "admitted": False, "rejected": False, "evicted": False}
    if policy != "CARMA":
        return {
            "action": "admit_or_replace",
            "admitted": True,
            "rejected": False,
            "evicted": None,
        }
    result = recorder.last_policy_result
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        return {"action": "unknown", "admitted": None, "rejected": None, "evicted": None}
    outcome = result[0]
    action = str(outcome.get("action", "unknown"))
    admitted = bool(outcome.get("admitted", False))
    victims = outcome.get("evicted_ids") or []
    return {
        "action": action,
        "admitted": admitted,
        "rejected": action.startswith("reject"),
        "evicted": bool(victims and victims != [outcome.get("key")]),
    }


def _cache_size(manager: Any) -> int:
    entries = getattr(manager.eviction_base, "_entries", None)
    if isinstance(entries, dict):
        return len(entries)
    cache = getattr(manager.eviction_base, "_cache", None)
    if cache is not None:
        return len(cache)
    raise RuntimeError("eviction policy does not expose an in-memory size")


def _request_record_template(
    request: Mapping[str, Any],
    attempt_id: str,
    run_id: str,
    seed: int,
    policy: str,
    order_position: int,
    trace_hash: str,
) -> Dict[str, Any]:
    """Allocate the complete fixed-schema request row before RSS sampling."""

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "seed": seed,
        "policy": policy,
        "order_position": order_position,
        "trace_hash": trace_hash,
        "request_index": int(request["index"]),
        "request_id": request["request_id"],
        "phase": request["phase"],
        "occurrence": int(request["occurrence"]),
        "reuse_opportunity": bool(request["reuse_opportunity"]),
        "text_id": request["text_id"],
        "text_sha256": "0" * 64,
        "concept_id": request["concept_id"],
        "expected_response_id": request["response_id"],
        "returned_response_id": "",
        "returned_concept_id": "",
        "top_candidate_id": None,
        "raw_hit": False,
        "valid_hit": False,
        "false_hit": False,
        "similarity": -1.0,
        "policy_action": "unclassified",
        "admitted": None,
        "rejected": None,
        "evicted": None,
        "cache_size_before": 0,
        "cache_size_after": 0,
        "preprocess_ns": 0,
        "tokenize_ns": 0,
        "text_preprocess_tokenize_ns": 0,
        "onnx_inference_ns": 0,
        "embedding_postprocess_ns": 0,
        "embedding_elapsed_ns": 0,
        "embedding_ns": 0,
        "faiss_search_ns": 0,
        "faiss_mutation_ns": 0,
        "faiss_ns": 0,
        "sqlite_read_ns": 0,
        "sqlite_write_ns": 0,
        "sqlite_ns": 0,
        "similarity_evaluation_ns": 0,
        "similarity_decision_ns": 0,
        "policy_exclusive_ns": 0,
        "policy_inclusive_ns": 0,
        "response_materialization_ns": 0,
        "response_propagation_ns": 0,
        "response_return_ns": 0,
        "cache_management_ns": 0,
        "post_embedding_elapsed_ns": 0,
        "post_embedding_ns": 0,
        "post_embedding_total_ns": 0,
        "residual_ns": 0,
        "end_to_end_ns": 0,
        "request_total_ns": 0,
        "exclusive_reconciles": False,
    }


def _execute_child(
    policy: str,
    seed: int,
    order_position: int,
    attempt_id: str,
    trace_path: Path,
    warmup_path: Path,
    control_dir: Path,
    expected_trace_hash: str,
    config: Gate7Config,
    assets: Dict[str, Any],
) -> Dict[str, Any]:
    trace = _load_trace(trace_path)
    observed_trace_hash = _trace_digest(trace)
    if observed_trace_hash != expected_trace_hash:
        raise RuntimeError("child trace hash differs from orchestrator")
    if len(trace) != config.requests:
        raise RuntimeError("child trace request count differs from configuration")
    run_id = _run_id(
        policy,
        seed,
        expected_trace_hash,
        config,
        assets["model_sha256"],
        attempt_id,
    )
    bridge = _integration_config(config, seed)
    if config.fake_embedding:
        embedder: Any = DeterministicFakeEmbedder()
    else:
        observed_model_hash = sha256_file(Path(assets["model_path"]))
        if observed_model_hash != assets["model_sha256"]:
            raise RuntimeError("child ONNX model hash differs from the orchestrator")
        if observed_model_hash != PINNED_MODEL_SHA256:
            raise RuntimeError("child ONNX model hash differs from the frozen contract")
        embedder = PinnedOnnxEmbedder(
            Path(assets["tokenizer_path"]),
            Path(assets["model_path"]),
            config.onnx_intra_threads,
            config.onnx_inter_threads,
            config.max_length,
        )
        if list(embedder.providers) != ["CPUExecutionProvider"]:
            raise RuntimeError(
                "formal ONNX provider set differs from the pinned CPU-only provider"
            )
    warmup_texts = json.loads(Path(warmup_path).read_text(encoding="utf-8"))
    if not isinstance(warmup_texts, list) or len(warmup_texts) != config.warmup_requests:
        raise ValueError("warm-up text artifact has the wrong row count")
    if any(not isinstance(value, str) or not value for value in warmup_texts):
        raise ValueError("warm-up text artifact contains an invalid value")
    for warmup_text in warmup_texts:
        warm_vector, _ = embedder.embed(warmup_text)
        if warm_vector.size != embedder.dimension:
            raise RuntimeError("warm-up embedding dimension mismatch")

    # Force the full embedding-evidence allocation resident and allocate every
    # request dictionary/key before the supervisor's mandatory start sample.
    # Only scalar values and existing string references change in the loop.
    embedding_buffer = np.full(
        (len(trace), int(embedder.dimension)), np.nan, dtype="<f4"
    )
    request_buffer: List[Dict[str, Any]] = [
        _request_record_template(
            request,
            attempt_id,
            run_id,
            seed,
            policy,
            order_position,
            expected_trace_hash,
        )
        for request in trace
    ]

    process = psutil.Process(os.getpid())
    post_warmup_memory = process.memory_info()
    post_warmup_rss = int(post_warmup_memory.rss)
    post_warmup_uss: Optional[int] = None
    try:
        post_warmup_uss = int(process.memory_full_info().uss)
    except (AttributeError, psutil.Error):
        pass

    control_dir = Path(control_dir)
    control_dir.mkdir(parents=True, exist_ok=True)
    _write_control_state(control_dir, "ready", -1)
    _wait_for_ack(control_dir / "start.ack")
    _write_control_state(control_dir, "running", -1)

    stage_values: Dict[str, List[int]] = {name: [] for name in SUMMARY_STAGES}
    embedding_digest = hashlib.sha256()
    counters = defaultdict(int)
    timer_overhead = _timer_overhead_ns()
    cpu_start = process.cpu_times()
    loop_start_monotonic_ns: Optional[int] = None
    loop_end_monotonic_ns: Optional[int] = None

    with tempfile.TemporaryDirectory(prefix="gate7-%s-" % policy.lower()) as root:
        storage_instance_sha256 = hashlib.sha256(
            str(Path(root).resolve()).encode("utf-8")
        ).hexdigest()
        manager = _build_manager(policy, root, int(embedder.dimension), bridge)
        recorder = ExclusiveStageRecorder()
        _instrument_manager(manager, recorder)

        adapter_state: Dict[str, Any] = {}
        original_search = manager.search
        original_get_scalar_data = manager.get_scalar_data

        @functools.wraps(original_search)
        def captured_search(*args: Any, **kwargs: Any) -> Any:
            result = original_search(*args, **kwargs)
            adapter_state["search_results"] = list(result or [])
            return result

        @functools.wraps(original_get_scalar_data)
        def captured_get_scalar_data(*args: Any, **kwargs: Any) -> Any:
            result = original_get_scalar_data(*args, **kwargs)
            adapter_state["candidate_cache_data_seen"] = True
            adapter_state["candidate_cache_data_present"] = result is not None
            return result

        manager.search = captured_search
        manager.get_scalar_data = captured_get_scalar_data
        similarity_evaluation = CosineDistanceEvaluation(recorder)

        def pre_embedding_func(data: Dict[str, Any], **_: Any) -> str:
            started = time.perf_counter_ns()
            normalized = _normalize_text(str(data["prompt"]))
            recorder.record("preprocess", time.perf_counter_ns() - started)
            adapter_state["normalized_text"] = normalized
            return normalized

        def embedding_func(text: str, **_: Any) -> np.ndarray:
            started = time.perf_counter_ns()
            vector, parts = embedder.embed(text)
            adapter_state["embedding_elapsed_ns"] = time.perf_counter_ns() - started
            adapter_state["embedding_parts"] = dict(parts)
            adapter_state["embedding_complete_ns"] = time.perf_counter_ns()
            recorder.record("tokenize", int(parts["tokenize"]))
            recorder.record("onnx_inference", int(parts["onnx_inference"]))
            recorder.record(
                "embedding_postprocess", int(parts["embedding_postprocess"])
            )
            request_index = int(adapter_state["request_index"])
            embedding_buffer[request_index, :] = vector
            return vector

        def local_response_handler(*_: Any, **__: Any) -> str:
            started = time.perf_counter_ns()
            adapter_state["raw_hit"] = False
            payload = str(adapter_state["response_payload"])
            recorder.record(
                "response_materialization", time.perf_counter_ns() - started
            )
            adapter_state["response_materialization_complete_ns"] = (
                time.perf_counter_ns()
            )
            return payload

        def materialize_hit(payload: Any) -> Dict[str, Any]:
            started = time.perf_counter_ns()
            adapter_state["raw_hit"] = True
            response = _materialize_response(str(payload))
            recorder.record(
                "response_materialization", time.perf_counter_ns() - started
            )
            adapter_state["response_materialization_complete_ns"] = (
                time.perf_counter_ns()
            )
            return response

        def select_first(messages: Sequence[Any]) -> Any:
            started = time.perf_counter_ns()
            if not messages:
                raise RuntimeError("GPTCache returned an empty hit answer set")
            response = messages[0]
            recorder.record(
                "response_materialization", time.perf_counter_ns() - started
            )
            adapter_state["response_materialization_complete_ns"] = (
                time.perf_counter_ns()
            )
            return response

        def save_and_materialize(
            payload: Any,
            update_cache_func: Callable[..., Any],
            *_: Any,
            **__: Any,
        ) -> Dict[str, Any]:
            try:
                update_cache_func(str(payload))
                adapter_state["save_completed"] = True
            except Exception as error:
                adapter_state["update_error"] = repr(error)
                raise
            started = time.perf_counter_ns()
            response = _materialize_response(str(payload))
            recorder.record(
                "response_materialization", time.perf_counter_ns() - started
            )
            adapter_state["response_materialization_complete_ns"] = (
                time.perf_counter_ns()
            )
            return response

        chat_cache = Cache()
        chat_cache.init(
            pre_embedding_func=pre_embedding_func,
            embedding_func=embedding_func,
            data_manager=manager,
            similarity_evaluation=similarity_evaluation,
            post_process_messages_func=select_first,
            config=Config(
                similarity_threshold=config.hit_threshold,
                auto_flush=20,
                enable_token_counter=False,
                data_check=False,
                disable_report=True,
            ),
        )

        for request in trace:
            cache_size_before = _cache_size(manager)
            adapter_state.clear()
            adapter_state.update(
                {
                    "request_index": int(request["index"]),
                    "response_payload": request["response_payload"],
                    "search_results": [],
                    "candidate_cache_data_seen": False,
                    "candidate_cache_data_present": False,
                }
            )
            similarity_evaluation.last_similarity = None
            recorder.begin()
            total_start = time.perf_counter_ns()
            if loop_start_monotonic_ns is None:
                loop_start_monotonic_ns = total_start
            response = adapt(
                local_response_handler,
                materialize_hit,
                save_and_materialize,
                prompt=request["text"],
                cache_obj=chat_cache,
                top_k=1,
                temperature=0.0,
            )
            total_stop = time.perf_counter_ns()
            total_ns = total_stop - total_start
            loop_end_monotonic_ns = total_stop
            measured = recorder.finish()

            if adapter_state.get("update_error"):
                raise RuntimeError(
                    "GPTCache adapter suppressed a cache update failure: %s"
                    % adapter_state["update_error"]
                )
            if not isinstance(response, dict):
                raise RuntimeError("GPTCache adapter returned an unmaterialized response")
            if "raw_hit" not in adapter_state:
                raise RuntimeError("GPTCache adapter did not classify the request")
            if np.isnan(embedding_buffer[int(request["index"])]).any():
                raise RuntimeError("GPTCache adapter did not compute an embedding")
            response_complete_ns = adapter_state.get(
                "response_materialization_complete_ns"
            )
            if not isinstance(response_complete_ns, int):
                raise RuntimeError(
                    "GPTCache adapter did not cross a measured response boundary"
                )
            response_propagation_ns = total_stop - response_complete_ns
            if response_propagation_ns < 0:
                raise RuntimeError("response propagation time is negative")
            response_return_ns = (
                measured["response_materialization"] + response_propagation_ns
            )

            search_results = adapter_state["search_results"]
            top_candidate_id = None
            if search_results and int(search_results[0][1]) >= 0:
                top_candidate_id = int(search_results[0][1])
                if (
                    adapter_state["candidate_cache_data_seen"]
                    and not adapter_state["candidate_cache_data_present"]
                ):
                    counters["stale_candidates"] += 1
            similarity = (
                -1.0
                if similarity_evaluation.last_similarity is None
                else float(similarity_evaluation.last_similarity)
            )
            raw_hit = bool(adapter_state["raw_hit"])

            valid_hit = raw_hit and response["concept_id"] == request["concept_id"]
            false_hit = raw_hit and not valid_hit
            unknown_answer = response["response_id"] != request["response_id"]
            action = _policy_action(policy, raw_hit, recorder)
            cache_size_after = _cache_size(manager)
            if policy != "CARMA" and not raw_hit:
                action["evicted"] = cache_size_before >= config.capacity
            counters["max_cache_size"] = max(
                counters["max_cache_size"], cache_size_before, cache_size_after
            )
            counters["hits"] += int(raw_hit)
            counters["misses"] += int(not raw_hit)
            counters["valid_hits"] += int(valid_hit)
            counters["false_hits"] += int(false_hit)
            counters["unknown_answers"] += int(unknown_answer)
            counters["admissions"] += int(action["admitted"] is True)
            counters["rejections"] += int(action["rejected"] is True)
            counters["evictions"] += int(action["evicted"] is True)

            embedding_parts = adapter_state["embedding_parts"]
            preprocess_ns = measured["preprocess"]
            text_preprocess_tokenize_ns = preprocess_ns + int(
                embedding_parts["tokenize"]
            )
            embedding_contract_ns = (
                text_preprocess_tokenize_ns
                + int(embedding_parts["onnx_inference"])
                + int(embedding_parts["embedding_postprocess"])
            )
            post_embedding_contract_ns = total_ns - embedding_contract_ns
            if post_embedding_contract_ns < 0:
                raise RuntimeError("derived post-embedding time is negative")
            post_embedding_elapsed_ns = max(
                0,
                loop_end_monotonic_ns
                - int(adapter_state.get("embedding_complete_ns", loop_end_monotonic_ns)),
            )
            cache_management_ns = (
                measured["policy_exclusive"]
                + measured["sqlite_write"]
                + measured["faiss_mutation"]
            )
            exclusive = {
                "preprocess": preprocess_ns,
                "tokenize": int(embedding_parts["tokenize"]),
                "onnx_inference": int(embedding_parts["onnx_inference"]),
                "embedding_postprocess": int(embedding_parts["embedding_postprocess"]),
                "faiss_search": measured["faiss_search"],
                "faiss_mutation": measured["faiss_mutation"],
                "sqlite_read": measured["sqlite_read"],
                "sqlite_write": measured["sqlite_write"],
                "similarity_evaluation": measured["similarity_evaluation"],
                "policy_exclusive": measured["policy_exclusive"],
                "response_return": response_return_ns,
            }
            exclusive_sum = sum(exclusive.values())
            if exclusive_sum > total_ns:
                raise RuntimeError("exclusive stage time exceeds end-to-end time")
            residual_ns = total_ns - exclusive_sum
            faiss_ns = measured["faiss_search"] + measured["faiss_mutation"]
            sqlite_ns = measured["sqlite_read"] + measured["sqlite_write"]

            stage_values["end_to_end"].append(total_ns)
            stage_values["post_embedding"].append(post_embedding_contract_ns)
            stage_values["embedding"].append(embedding_contract_ns)
            stage_values["faiss"].append(faiss_ns)
            stage_values["sqlite"].append(sqlite_ns)
            stage_values["policy_exclusive"].append(measured["policy_exclusive"])
            stage_values["policy_inclusive"].append(measured["policy_inclusive"])
            stage_values["response_return"].append(response_return_ns)
            stage_values["residual"].append(residual_ns)

            request_buffer[int(request["index"])].update(
                {
                    "text_sha256": hashlib.sha256(
                        str(adapter_state["normalized_text"]).encode("utf-8")
                    ).hexdigest(),
                    "returned_response_id": response["response_id"],
                    "returned_concept_id": response["concept_id"],
                    "top_candidate_id": top_candidate_id,
                    "raw_hit": raw_hit,
                    "valid_hit": valid_hit,
                    "false_hit": false_hit,
                    "similarity": round(similarity, 10),
                    "policy_action": action["action"],
                    "admitted": action["admitted"],
                    "rejected": action["rejected"],
                    "evicted": action["evicted"],
                    "cache_size_before": cache_size_before,
                    "cache_size_after": cache_size_after,
                    "preprocess_ns": preprocess_ns,
                    "tokenize_ns": int(embedding_parts["tokenize"]),
                    "text_preprocess_tokenize_ns": text_preprocess_tokenize_ns,
                    "onnx_inference_ns": int(embedding_parts["onnx_inference"]),
                    "embedding_postprocess_ns": int(
                        embedding_parts["embedding_postprocess"]
                    ),
                    "embedding_elapsed_ns": int(adapter_state["embedding_elapsed_ns"]),
                    "embedding_ns": embedding_contract_ns,
                    "faiss_search_ns": measured["faiss_search"],
                    "faiss_mutation_ns": measured["faiss_mutation"],
                    "faiss_ns": faiss_ns,
                    "sqlite_read_ns": measured["sqlite_read"],
                    "sqlite_write_ns": measured["sqlite_write"],
                    "sqlite_ns": sqlite_ns,
                    "similarity_evaluation_ns": measured["similarity_evaluation"],
                    "similarity_decision_ns": measured["similarity_evaluation"],
                    "policy_exclusive_ns": measured["policy_exclusive"],
                    "policy_inclusive_ns": measured["policy_inclusive"],
                    "response_materialization_ns": measured[
                        "response_materialization"
                    ],
                    "response_propagation_ns": response_propagation_ns,
                    "response_return_ns": response_return_ns,
                    "cache_management_ns": cache_management_ns,
                    "post_embedding_elapsed_ns": post_embedding_elapsed_ns,
                    "post_embedding_ns": post_embedding_contract_ns,
                    "post_embedding_total_ns": post_embedding_contract_ns,
                    "residual_ns": residual_ns,
                    "end_to_end_ns": total_ns,
                    "request_total_ns": total_ns,
                    "exclusive_reconciles": exclusive_sum + residual_ns == total_ns,
                }
            )

        if loop_start_monotonic_ns is None or loop_end_monotonic_ns is None:
            raise RuntimeError("measured request loop did not execute")
        _write_control_state(control_dir, "loop_complete", len(trace) - 1)
        _wait_for_ack(control_dir / "end.ack")
        if not np.all(np.isfinite(embedding_buffer)):
            raise RuntimeError("embedding evidence buffer is incomplete")
        for request, vector in zip(trace, embedding_buffer):
            embedding_digest.update(str(request["text_id"]).encode("utf-8"))
            embedding_digest.update(np.asarray(vector, dtype="<f4").tobytes())
        verification = _verify_storage(manager, config.capacity)
        policy_stats = (
            dict(manager.eviction_base.stats())
            if callable(getattr(manager.eviction_base, "stats", None))
            else {}
        )
        manager.close()

    structural_failures = {
        "false_hits": int(counters["false_hits"]),
        "stale_candidates": int(counters["stale_candidates"]),
        "unknown_answers": int(counters["unknown_answers"]),
        "capacity_excess": max(0, int(counters["max_cache_size"]) - config.capacity),
    }
    if config.mode == "full" and any(structural_failures.values()):
        raise RuntimeError("formal Gate 7 child has structural failures: %r" % structural_failures)

    cpu_end = process.cpu_times()
    if not all(record.get("exclusive_reconciles") is True for record in request_buffer):
        raise RuntimeError("request record buffer is incomplete")
    requests_out = list(request_buffer)
    service_seconds = sum(stage_values["end_to_end"]) / 1e9
    loop_seconds = (loop_end_monotonic_ns - loop_start_monotonic_ns) / 1e9
    summary: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "child_pid": os.getpid(),
        "child_exit_status": 0,
        "storage_instance_sha256": storage_instance_sha256,
        "mode": config.mode,
        "seed": seed,
        "policy": policy,
        "order_position": order_position,
        "trace_hash": expected_trace_hash,
        "requests": len(trace),
        "capacity": config.capacity,
        "embedding_dimension": int(embedder.dimension),
        "embedding_norm_verified": True,
        "provider_verified": (
            config.fake_embedding
            or list(embedder.providers) == ["CPUExecutionProvider"]
        ),
        "request_buffer_rows_reserved": len(request_buffer),
        "embedding_buffer_bytes_reserved": int(embedding_buffer.nbytes),
        "hits": int(counters["hits"]),
        "misses": int(counters["misses"]),
        "valid_hits": int(counters["valid_hits"]),
        "false_hits": int(counters["false_hits"]),
        "stale_candidates": int(counters["stale_candidates"]),
        "unknown_answers": int(counters["unknown_answers"]),
        "max_cache_size": int(counters["max_cache_size"]),
        "admissions": int(policy_stats.get("admissions", counters["admissions"])),
        "rejections": int(policy_stats.get("rejections", counters["rejections"])),
        "evictions": int(policy_stats.get("evictions", counters["evictions"])),
        "service_seconds": round(service_seconds, 9),
        "loop_seconds": round(loop_seconds, 9),
        "throughput_qps": round(len(trace) / loop_seconds, 6),
        "service_throughput_qps": round(len(trace) / service_seconds, 6),
        "loop_throughput_qps": round(len(trace) / loop_seconds, 6),
        "cpu_user_seconds": round(float(cpu_end.user - cpu_start.user), 9),
        "cpu_system_seconds": round(float(cpu_end.system - cpu_start.system), 9),
        "rss_post_warmup_bytes": post_warmup_rss,
        "uss_post_warmup_bytes": post_warmup_uss,
        "rss_high_water_bytes": _high_water_rss_bytes(),
        "embedding_digest_sha256": embedding_digest.hexdigest(),
        "timer_overhead_median_ns": timer_overhead["median_ns"],
        "timer_overhead_p95_ns": timer_overhead["p95_ns"],
        "loop_start_monotonic_ns": loop_start_monotonic_ns,
        "loop_end_monotonic_ns": loop_end_monotonic_ns,
        **verification,
    }
    for stage in SUMMARY_STAGES:
        summary.update(_latency_summary(stage_values[stage], stage))
    return {
        "summary": summary,
        "requests": requests_out,
        "timer_overhead": timer_overhead,
        "providers": list(embedder.providers),
    }


def _resource_sample(
    process: psutil.Process,
    attempt_id: str,
    run_id: str,
    seed: int,
    policy: str,
    sample_index: int,
    kind: str,
    scope: str,
    request_index: Optional[int],
) -> Optional[Dict[str, Any]]:
    try:
        memory = process.memory_info()
        cpu = process.cpu_times()
        try:
            uss: Optional[int] = int(process.memory_full_info().uss)
        except (AttributeError, psutil.Error):
            uss = None
        try:
            io = process.io_counters()
        except (AttributeError, NotImplementedError, psutil.Error):
            io = None
        try:
            system_load_1m: Optional[float] = float(os.getloadavg()[0])
        except (AttributeError, OSError):
            system_load_1m = None
        return {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "child_pid": int(process.pid),
            "seed": seed,
            "policy": policy,
            "sample_index": sample_index,
            "kind": kind,
            "scope": scope,
            "request_index": request_index,
            "monotonic_ns": time.perf_counter_ns(),
            "rss_bytes": int(memory.rss),
            "vms_bytes": int(memory.vms),
            "uss_bytes": uss,
            "uss_available": uss is not None,
            "cpu_user_seconds": float(cpu.user),
            "cpu_system_seconds": float(cpu.system),
            "num_threads": int(process.num_threads()),
            "system_load_1m": system_load_1m,
            "io_counters_available": io is not None,
            "io_read_count": int(getattr(io, "read_count", 0)) if io else None,
            "io_write_count": int(getattr(io, "write_count", 0)) if io else None,
            "io_read_bytes": int(getattr(io, "read_bytes", 0)) if io else None,
            "io_write_bytes": int(getattr(io, "write_bytes", 0)) if io else None,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
        return None


def _child_environment() -> Dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "TOKENIZERS_PARALLELISM": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CARMA_ONNX_WORKERS": "1",
            "CARMA_ONNX_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    return environment


def _run_child_with_sampling(
    command: Sequence[str],
    result_path: Path,
    attempt_id: str,
    run_id: str,
    seed: int,
    policy: str,
    interval_ms: int,
    control_dir: Path,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    process_handle = subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_environment(),
    )
    observed = psutil.Process(process_handle.pid)
    samples: List[Dict[str, Any]] = []
    sample_index = 0
    state = "startup"
    request_index: Optional[int] = None
    start_acknowledged = False
    end_acknowledged = False
    next_periodic = time.monotonic()
    while process_handle.poll() is None:
        state_path = Path(control_dir) / "state.json"
        if state_path.is_file():
            try:
                state_row = json.loads(state_path.read_text(encoding="utf-8"))
                state = str(state_row["state"])
                request_index = int(state_row.get("request_index", -1))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        if state == "ready" and not start_acknowledged:
            sample = _resource_sample(
                observed,
                attempt_id,
                run_id,
                seed,
                policy,
                sample_index,
                "loop_start",
                "formal",
                -1,
            )
            if sample is not None:
                samples.append(sample)
                sample_index += 1
            (Path(control_dir) / "start.ack").write_text("ok\n", encoding="utf-8")
            start_acknowledged = True
            next_periodic = time.monotonic() + interval_ms / 1000.0
        elif state == "loop_complete" and not end_acknowledged:
            sample = _resource_sample(
                observed,
                attempt_id,
                run_id,
                seed,
                policy,
                sample_index,
                "loop_end",
                "formal",
                request_index,
            )
            if sample is not None:
                samples.append(sample)
                sample_index += 1
            (Path(control_dir) / "end.ack").write_text("ok\n", encoding="utf-8")
            end_acknowledged = True
        elif state == "running" and time.monotonic() >= next_periodic:
            sample = _resource_sample(
                observed,
                attempt_id,
                run_id,
                seed,
                policy,
                sample_index,
                "periodic",
                "formal",
                request_index,
            )
            if sample is not None:
                samples.append(sample)
                sample_index += 1
            next_periodic = time.monotonic() + interval_ms / 1000.0
        time.sleep(0.005)
    stdout, stderr = process_handle.communicate()
    if process_handle.returncode != 0:
        raise ChildExecutionError(
            "%s seed %d child failed (exit %d)"
            % (policy, seed, process_handle.returncode),
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        )
    if not result_path.is_file():
        raise ChildExecutionError(
            "child completed without a result artifact",
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        )
    if not start_acknowledged or not end_acknowledged:
        raise ChildExecutionError(
            "resource supervisor did not observe both loop boundaries",
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChildExecutionError(
            "child result artifact is unreadable",
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        ) from exc
    if not isinstance(result, dict):
        raise ChildExecutionError(
            "child result artifact is not an object",
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        )
    result["_supervisor_capture"] = {"stdout": stdout, "stderr": stderr}
    return result, samples


def _config_args(config: Gate7Config) -> List[str]:
    args = [
        "--mode", config.mode,
        "--requests", str(config.requests),
        "--capacity", str(config.capacity),
        "--hit-threshold", str(config.hit_threshold),
        "--topic-threshold", str(config.topic_threshold),
        "--cell-threshold", str(config.cell_threshold),
        "--demand-half-life", str(config.demand_half_life),
        "--quota-strength", str(config.quota_strength),
        "--ghost-support-threshold", str(config.ghost_support_threshold),
        "--admission-margin", str(config.admission_margin),
        "--centroid-alpha", str(config.centroid_alpha),
        "--entry-hit-weight", str(config.entry_hit_weight),
        "--onnx-intra-threads", str(config.onnx_intra_threads),
        "--onnx-inter-threads", str(config.onnx_inter_threads),
        "--warmup-requests", str(config.warmup_requests),
        "--resource-interval-ms", str(config.resource_interval_ms),
        "--max-length", str(config.max_length),
    ]
    if config.fake_embedding:
        args.append("--fake-embedding")
    return args


def _runs_fields() -> Tuple[str, ...]:
    base = (
        "schema_version", "attempt_id", "run_id", "child_pid", "child_exit_status",
        "storage_instance_sha256", "mode", "seed", "policy", "order_position",
        "trace_hash", "requests", "capacity", "embedding_dimension",
        "embedding_norm_verified", "provider_verified",
        "request_buffer_rows_reserved", "embedding_buffer_bytes_reserved",
        "hits", "misses",
        "valid_hits", "false_hits", "stale_candidates", "unknown_answers", "max_cache_size",
        "admissions", "rejections", "evictions", "service_seconds", "loop_seconds",
        "throughput_qps", "service_throughput_qps", "loop_throughput_qps",
        "loop_start_monotonic_ns", "loop_end_monotonic_ns",
        "cpu_user_seconds", "cpu_system_seconds", "cpu_total_seconds",
        "cpu_user_lifecycle_seconds", "cpu_system_lifecycle_seconds",
        "resource_window_seconds", "cpu_utilization_percent",
        "io_read_count_delta", "io_write_count_delta", "io_read_bytes_delta",
        "io_write_bytes_delta", "rss_post_warmup_bytes",
        "uss_post_warmup_bytes", "rss_high_water_bytes", "rss_start_bytes", "rss_end_bytes",
        "rss_mean_bytes", "rss_peak_sampled_bytes", "rss_peak_bytes",
        "rss_incremental_peak_bytes", "rss_end_minus_start_bytes",
        "uss_start_bytes", "uss_end_bytes", "uss_mean_bytes", "uss_peak_bytes",
        "uss_incremental_peak_bytes", "uss_end_minus_start_bytes",
        "embedding_digest_sha256",
        "timer_overhead_median_ns", "timer_overhead_p95_ns", "final_scalar_count",
        "final_vector_count", "deleted_scalar_count", "verified_entries",
    )
    latency = tuple(
        "%s_%s_us" % (stage, statistic)
        for stage in SUMMARY_STAGES
        for statistic in ("mean", "p50", "p95", "p99")
    )
    return base + latency


def _serialize_trace(path: Path, rows: Sequence[Any]) -> None:
    _write_jsonl(path, (asdict(row) for row in rows))


def _run_bundle_impl(
    prepared_dir: Path,
    output_dir: Path,
    contract_path: Path,
    seeds: Sequence[int],
    policies: Sequence[str],
    config: Gate7Config,
    failure_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute an isolated multi-seed Gate 7 evidence bundle."""

    from benchmarks.carma.gate7_trace import build_gate7_trace

    started_at_utc = datetime.now(timezone.utc)
    config.validate()
    prepared_dir = Path(prepared_dir).resolve()
    output_dir = Path(output_dir).resolve()
    contract_path = Path(contract_path).resolve()
    if not contract_path.is_file():
        raise FileNotFoundError("Gate 7 remediation contract does not exist")
    git_state = _git_state()
    source_snapshot = _source_identities()
    contract_identity = _artifact_identity(contract_path)
    if (
        config.mode == "full"
        and output_dir.parent != FORMAL_ATTEMPT_ROOT.resolve()
    ):
        raise RuntimeError(
            "formal output must be one direct child of %s"
            % FORMAL_ATTEMPT_ROOT.resolve()
        )
    if config.mode == "full" and (
        git_state.get("head_commit") is None
        or git_state.get("worktree_dirty") is not False
    ):
        raise RuntimeError(
            "formal full mode requires a clean, committed Git worktree"
        )
    if config.mode == "full":
        if contract_path != DEFAULT_CONTRACT.resolve():
            raise RuntimeError(
                "formal full mode requires the tracked default Gate 7 contract"
            )
        if contract_identity["sha256"] != PINNED_CONTRACT_SHA256:
            raise RuntimeError(
                "formal Gate 7 contract hash differs from the frozen identity"
            )
        _validate_sources_are_tracked_at_head(
            str(git_state["head_commit"]), source_snapshot, contract_path
        )
    attempt_id = _attempt_id_for(
        started_at_utc,
        str(git_state.get("head_commit") or "uncommitted"),
    )
    prior_attempts = _prior_attempts(output_dir) if config.mode == "full" else []
    unresolved_attempts = [
        attempt for attempt in prior_attempts if attempt["status"] == "unresolved"
    ]
    if unresolved_attempts:
        raise RuntimeError(
            "formal attempt root contains unresolved predecessor directories: %r"
            % [attempt["directory"] for attempt in unresolved_attempts]
        )
    policies = tuple(policies)
    if not policies or len(set(policies)) != len(policies):
        raise ValueError("policies must be non-empty and unique")
    if any(policy not in POLICIES for policy in policies):
        raise ValueError("unknown policy")
    seeds = tuple(int(seed) for seed in seeds)
    if not seeds:
        raise ValueError("at least one seed is required")
    if config.mode == "full":
        if seeds != FULL_SEEDS:
            raise ValueError("formal full mode requires the five frozen seeds")
        if set(policies) != set(POLICIES):
            raise ValueError("formal full mode requires LRU, LFU, and CARMA")
    assets = _fake_assets() if config.fake_embedding else _resolve_onnx_assets()
    schedule = policy_schedule(seeds)

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("output directory already exists and is not empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    failure_context.update(
        {
            "output_dir": output_dir,
            "attempt_id": attempt_id,
            "started_at_utc": started_at_utc,
            "seed": -1,
            "policy": "not_started",
            "order_position": -1,
            "config": config,
            "contract_identity": contract_identity,
            "git_state": git_state,
            "source_snapshot": source_snapshot,
            "prior_attempts": prior_attempts,
            "attempt_ledger": None,
            "resource_records": [],
            "current_samples": [],
            "child_stdout": None,
            "child_stderr": None,
        }
    )
    if config.mode == "full":
        attempt_ledger = _register_formal_attempt(
            output_dir,
            attempt_id,
            started_at_utc,
            str(git_state["head_commit"]),
            prior_attempts,
        )
        failure_context["attempt_ledger"] = attempt_ledger
    rows: List[Dict[str, Any]] = []
    request_records: List[Dict[str, Any]] = []
    resource_records: List[Dict[str, Any]] = []
    failure_context["resource_records"] = resource_records
    trace_metadata: Dict[str, Any] = {}
    actual_orders: Dict[str, List[str]] = {}
    trace_artifacts: Dict[str, Dict[str, Any]] = {}
    traces_dir = output_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gate7-orchestrator-") as temporary:
        temporary_root = Path(temporary)
        source_snapshot_path = temporary_root / "source-snapshot.json"
        source_snapshot_path.write_text(
            json.dumps(source_snapshot, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        for seed in sorted(seeds):
            failure_context.update(
                {"seed": seed, "policy": "trace_build", "order_position": -1}
            )
            trace, trace_hash, metadata = build_gate7_trace(
                prepared_dir,
                seed=seed,
                requests=config.requests,
                capacity=config.capacity,
            )
            trace_path = traces_dir / ("seed-%d.jsonl" % seed)
            _serialize_trace(trace_path, trace)
            if _trace_digest(_load_trace(trace_path)) != trace_hash:
                raise RuntimeError("serialized trace hash differs from trace builder")
            trace_artifacts[str(trace_path.relative_to(output_dir))] = _artifact_identity(
                trace_path, rows=len(trace)
            )
            trace_metadata[str(seed)] = metadata
            if config.mode == "full":
                pinned_source = {
                    "source_archive_sha256": PINNED_ARCHIVE_SHA256,
                    "source_pairs_sha256": PINNED_PAIRS_SHA256,
                    "source_texts_sha256": PINNED_TEXTS_SHA256,
                }
                mismatches = {
                    key: (metadata.get(key), expected)
                    for key, expected in pinned_source.items()
                    if metadata.get(key) != expected
                }
                if mismatches:
                    raise RuntimeError(
                        "formal QQP provenance differs from the frozen contract: %r"
                        % mismatches
                    )
            text_by_id = {row.text_id: row.text for row in trace}
            canonical_hot_texts = [text_by_id[text_id] for text_id in metadata["hot_text_ids"]]
            warmup_texts = [
                canonical_hot_texts[index % len(canonical_hot_texts)]
                for index in range(config.warmup_requests)
            ]
            warmup_path = temporary_root / ("warmup-%d.json" % seed)
            warmup_path.write_text(
                json.dumps(warmup_texts, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            order = tuple(policy for policy in schedule[seed] if policy in policies)
            actual_orders[str(seed)] = list(order)
            seed_embedding_digests = set()

            for order_position, policy in enumerate(order):
                failure_context.update(
                    {
                        "seed": seed,
                        "policy": policy,
                        "order_position": order_position,
                        "current_samples": [],
                    }
                )
                run_id = _run_id(
                    policy,
                    seed,
                    trace_hash,
                    config,
                    assets["model_sha256"],
                    attempt_id,
                )
                result_path = temporary_root / ("result-%d-%s.json" % (seed, policy.lower()))
                control_dir = temporary_root / ("control-%d-%s" % (seed, policy.lower()))
                control_dir.mkdir()
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    *_config_args(config),
                    "--child-policy", policy,
                    "--child-seed", str(seed),
                    "--child-order-position", str(order_position),
                    "--child-attempt-id", attempt_id,
                    "--child-trace", str(trace_path),
                    "--child-warmup-texts", str(warmup_path),
                    "--child-control-dir", str(control_dir),
                    "--child-trace-hash", trace_hash,
                    "--child-result", str(result_path),
                    "--child-model-sha256", assets["model_sha256"],
                    "--child-source-snapshot", str(source_snapshot_path),
                ]
                if config.mode == "full":
                    command.extend(
                        ["--child-head-commit", str(git_state["head_commit"])]
                    )
                if assets["model_path"]:
                    command.extend(["--child-model-path", assets["model_path"]])
                if assets["tokenizer_path"]:
                    command.extend(["--child-tokenizer-path", assets["tokenizer_path"]])
                samples: List[Dict[str, Any]] = []
                try:
                    _assert_source_snapshot(
                        source_snapshot,
                        str(git_state["head_commit"])
                        if config.mode == "full"
                        else None,
                    )
                    result, samples = _run_child_with_sampling(
                        command,
                        result_path,
                        attempt_id,
                        run_id,
                        seed,
                        policy,
                        config.resource_interval_ms,
                        control_dir,
                    )
                    supervisor_capture = result.pop("_supervisor_capture", {})
                    failure_context["child_stdout"] = supervisor_capture.get(
                        "stdout"
                    )
                    failure_context["child_stderr"] = supervisor_capture.get(
                        "stderr"
                    )
                    failure_context["current_samples"] = samples
                    _assert_source_snapshot(
                        source_snapshot,
                        str(git_state["head_commit"])
                        if config.mode == "full"
                        else None,
                    )
                except BaseException as error:
                    failure_context["current_samples"] = (
                        error.samples
                        if isinstance(error, ChildExecutionError)
                        else samples
                    )
                    raise
                summary = result["summary"]
                if summary["run_id"] != run_id:
                    raise RuntimeError("child run identity differs from orchestrator")
                if summary["trace_hash"] != trace_hash:
                    raise RuntimeError("child trace identity differs from orchestrator")
                if int(summary["requests"]) != config.requests:
                    raise RuntimeError("child request count differs from orchestrator")
                if int(summary["order_position"]) != order_position:
                    raise RuntimeError("child policy order differs from the orchestrator")
                if int(summary["child_exit_status"]) != 0:
                    raise RuntimeError("successful child recorded a nonzero exit status")
                if any(
                    int(sample["child_pid"]) != int(summary["child_pid"])
                    for sample in samples
                ):
                    raise RuntimeError("resource samples identify the wrong child process")
                if not config.fake_embedding and result.get("providers") != [
                    "CPUExecutionProvider"
                ]:
                    raise RuntimeError("child used an unexpected ONNX provider")
                if config.mode == "full" and int(summary["embedding_dimension"]) != 768:
                    raise RuntimeError("formal embedding dimension is not 768")
                formal_samples = [sample for sample in samples if sample["scope"] == "formal"]
                if len(formal_samples) < 2:
                    raise RuntimeError("resource supervisor retained fewer than two formal samples")
                rss_values = [int(sample["rss_bytes"]) for sample in formal_samples]
                uss_values = [
                    int(sample["uss_bytes"])
                    for sample in formal_samples
                    if sample["uss_bytes"] is not None
                ]
                sampled_peak = max(rss_values)
                summary["rss_peak_sampled_bytes"] = sampled_peak
                summary["rss_peak_bytes"] = sampled_peak
                summary["rss_start_bytes"] = rss_values[0]
                summary["rss_end_bytes"] = rss_values[-1]
                summary["rss_end_minus_start_bytes"] = (
                    rss_values[-1] - rss_values[0]
                )
                summary["rss_mean_bytes"] = round(sum(rss_values) / len(rss_values), 3)
                summary["uss_peak_bytes"] = max(uss_values) if uss_values else None
                summary["uss_mean_bytes"] = (
                    round(sum(uss_values) / len(uss_values), 3) if uss_values else None
                )
                summary["uss_start_bytes"] = uss_values[0] if uss_values else None
                summary["uss_end_bytes"] = uss_values[-1] if uss_values else None
                summary["uss_incremental_peak_bytes"] = (
                    max(0, max(uss_values) - uss_values[0]) if uss_values else None
                )
                summary["uss_end_minus_start_bytes"] = (
                    uss_values[-1] - uss_values[0] if uss_values else None
                )
                summary["rss_incremental_peak_bytes"] = max(
                    0,
                    int(summary["rss_peak_bytes"]) - int(summary["rss_start_bytes"]),
                )
                summary["cpu_user_lifecycle_seconds"] = summary["cpu_user_seconds"]
                summary["cpu_system_lifecycle_seconds"] = summary[
                    "cpu_system_seconds"
                ]
                cpu_user_delta = max(
                    0.0,
                    float(formal_samples[-1]["cpu_user_seconds"])
                    - float(formal_samples[0]["cpu_user_seconds"]),
                )
                cpu_system_delta = max(
                    0.0,
                    float(formal_samples[-1]["cpu_system_seconds"])
                    - float(formal_samples[0]["cpu_system_seconds"]),
                )
                resource_window_seconds = max(
                    0.0,
                    (
                        int(formal_samples[-1]["monotonic_ns"])
                        - int(formal_samples[0]["monotonic_ns"])
                    )
                    / 1e9,
                )
                summary["cpu_user_seconds"] = round(cpu_user_delta, 9)
                summary["cpu_system_seconds"] = round(cpu_system_delta, 9)
                summary["cpu_total_seconds"] = round(
                    cpu_user_delta + cpu_system_delta, 9
                )
                summary["resource_window_seconds"] = round(
                    resource_window_seconds, 9
                )
                summary["cpu_utilization_percent"] = (
                    round(
                        100.0
                        * (cpu_user_delta + cpu_system_delta)
                        / resource_window_seconds,
                        6,
                    )
                    if resource_window_seconds > 0
                    else None
                )
                io_supported = all(
                    sample["io_counters_available"] for sample in formal_samples
                )
                for field in (
                    "io_read_count",
                    "io_write_count",
                    "io_read_bytes",
                    "io_write_bytes",
                ):
                    summary["%s_delta" % field] = (
                        max(
                            0,
                            int(formal_samples[-1][field])
                            - int(formal_samples[0][field]),
                        )
                        if io_supported
                        else None
                    )
                rows.append(summary)
                request_records.extend(result["requests"])
                resource_records.extend(samples)
                failure_context["current_samples"] = []
                failure_context["child_stdout"] = None
                failure_context["child_stderr"] = None
                seed_embedding_digests.add(summary["embedding_digest_sha256"])
            if len(seed_embedding_digests) != 1:
                raise RuntimeError("policies produced different embedding digests for one trace")

    _assert_source_snapshot(
        source_snapshot,
        str(git_state["head_commit"]) if config.mode == "full" else None,
    )
    end_git_state = _git_state()
    runs_path = output_dir / "runs.csv"
    requests_path = output_dir / "requests.jsonl"
    resources_path = output_dir / "resources.jsonl"
    outcome_latency_path = output_dir / "outcome-latency.jsonl"
    manifest_path = output_dir / "manifest.json"
    _write_csv(runs_path, rows, _runs_fields())
    _write_jsonl(requests_path, request_records)
    _write_jsonl(resources_path, resource_records)
    outcome_latency_records = _outcome_latency_records(request_records, rows)
    _write_jsonl(outcome_latency_path, outcome_latency_records)

    _assert_source_snapshot(
        source_snapshot,
        str(git_state["head_commit"]) if config.mode == "full" else None,
    )
    end_git_state = _git_state()

    completed_at_utc = datetime.now(timezone.utc)
    artifacts = {
        "runs.csv": _artifact_identity(runs_path, rows=len(rows)),
        "requests.jsonl": _artifact_identity(
            requests_path, rows=len(request_records)
        ),
        "resources.jsonl": _artifact_identity(
            resources_path, rows=len(resource_records)
        ),
        "outcome-latency.jsonl": _artifact_identity(
            outcome_latency_path, rows=len(outcome_latency_records)
        ),
        **trace_artifacts,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "kind": "prospective_gate7_followup",
        "started_at_utc": started_at_utc.isoformat(),
        "completed_at_utc": completed_at_utc.isoformat(),
        "elapsed_seconds": round(
            (completed_at_utc - started_at_utc).total_seconds(), 6
        ),
        "prior_attempt_exists": bool(prior_attempts),
        "prior_attempts": prior_attempts,
        "attempt_policy": {
            "formal_root": _project_path(FORMAL_ATTEMPT_ROOT),
            "directory": output_dir.name,
            "eligibility": "first structurally valid complete attempt in the retained predecessor chain",
            "rerun_scope": "complete five-seed, three-policy matrix under a new attempt ID",
        },
        "attempt_ledger": failure_context.get("attempt_ledger"),
        "historical_gate7_preserved": True,
        "historical_artifacts_modified": False,
        "precomputed_embeddings": False,
        "embedding_in_request_path": True,
        "gptcache_adapter_path": True,
        "actual_onnx": not config.fake_embedding,
        "formal_claimable_mode": False,
        "contract": {
            "path": _project_path(contract_path),
            **contract_identity,
        },
        "git": {
            **git_state,
            "end_head_commit": end_git_state.get("head_commit"),
            "end_worktree_dirty": end_git_state.get("worktree_dirty"),
            "source_snapshot_unchanged": _source_identities() == source_snapshot,
            "baseline_commit": HISTORICAL_BASELINE_COMMIT,
        },
        "source_identities": source_snapshot,
        "environment": _environment(),
        "config": asdict(config),
        "seeds": list(seeds),
        "policies": list(POLICIES if config.mode == "full" else policies),
        "policy_order_namespace": POLICY_ORDER_NAMESPACE,
        "planned_policy_orders": {
            str(seed): [policy for policy in schedule[seed] if policy in policies]
            for seed in sorted(seeds)
        },
        "policy_orders": actual_orders,
        "process_isolation": "one fresh child process and TemporaryDirectory per seed/policy",
        "storage_isolation": "fresh SQLite database and FAISS index per seed/policy",
        "trace_source": "QQP calibration split only; held-out rows excluded",
        "trace_metadata": trace_metadata,
        "trace_artifacts": trace_artifacts,
        "model": {
            key: value
            for key, value in assets.items()
            if key not in ("model_path", "tokenizer_path")
        },
        "onnx": {
            "provider": assets["provider"],
            "intra_op_threads": config.onnx_intra_threads,
            "inter_op_threads": config.onnx_inter_threads,
            "max_length": config.max_length,
            "warmup_requests": config.warmup_requests,
            "warmup_rule": "first hot text IDs in canonical order; cycle only in development smoke mode",
            "model_load_and_warmup_in_request_timing": False,
            "provider_verified_each_child": all(
                bool(row["provider_verified"]) for row in rows
            ),
            "embedding_norm_verified_each_request": all(
                bool(row["embedding_norm_verified"]) for row in rows
            ),
        },
        "gptcache": {
            "entrypoint": "gptcache.adapter.adapter.adapt",
            "top_k": 1,
            "similarity": "cosine reconstructed from normalized FAISS squared L2",
            "similarity_threshold": config.hit_threshold,
            "auto_flush": 20,
            "token_counter": False,
            "data_check": False,
            "report_persistence": False,
            "miss_handler": "deterministic local recorded response; no network or live LLM",
        },
        "timing_scope": {
            "end_to_end": "raw text entering GPTCache adapt through fully materialized returned response",
            "embedding": "tokenization, ONNX inference, mean pooling, and normalization",
            "post_embedding": "GPTCache adapter, SQLite/FAISS lookup or save, similarity, policy, and response materialization after embedding",
            "policy_exclusive": "put/put_with_metadata/get excluding nested SQLite/FAISS work",
            "policy_inclusive": "outer policy calls including synchronous nested cleanup",
            "response_return": "local recorded JSON response conversion plus propagation from the final response callback through adapt return",
            "residual": "orchestration, normalization, conversions, and timer overhead not assigned elsewhere",
            "request_logging": "buffered and written after each child exits; excluded from request timing",
            "outcome_categories": list(OUTCOME_NAMES),
            "outcome_report_rows": list(OUTCOME_REPORT_NAMES),
            "quantiles": "nearest rank: one-based ceil(q*N) over raw samples",
        },
        "resource_measurement": {
            "method": "external parent psutil sampling plus child OS high-water RSS",
            "interval_ms": config.resource_interval_ms,
            "formal_peak": "maximum mandatory or periodic external sampled RSS",
            "os_high_water_role": "diagnostic only; includes process startup before the formal window",
            "incremental_baseline": "child RSS after model warmup and before manager construction",
            "maximum_valid_sample_gap_ms": 2 * config.resource_interval_ms,
            "fixed_buffers_reserved_before_start": True,
        },
        "adjudication": {
            "experimental_unit": "paired seed",
            "aggregation": "every one of five seeds must satisfy every bound",
            "formal_comparator": "LRU",
            "secondary_comparator": "LFU",
            "p95_ratio_max": 1.25,
            "p95_delta_us_max": 500.0,
            "throughput_ratio_min": 0.90,
            "rss_ratio_max": 1.20,
            "rss_delta_bytes_max": 64 * 1024 * 1024,
        },
        "run_summaries": rows,
        "artifacts": artifacts,
    }
    # This is the final pre-publication integrity gate.  It deliberately runs
    # after environment collection and manifest construction so the atomic
    # manifest publication is based on the immediately observed source,
    # contract, and Git state rather than an earlier snapshot.
    publication_sources = _source_identities()
    publication_contract = _artifact_identity(contract_path)
    publication_git = _git_state()
    sources_unchanged = publication_sources == source_snapshot
    contract_unchanged = publication_contract == contract_identity
    git_unchanged = (
        publication_git.get("head_commit") == git_state.get("head_commit")
        and publication_git.get("worktree_dirty") is False
    )
    formal_claimable = (
        config.mode == "full"
        and not config.fake_embedding
        and sources_unchanged
        and contract_unchanged
        and git_state.get("worktree_dirty") is False
        and git_unchanged
    )
    if config.mode == "full" and not formal_claimable:
        raise RuntimeError(
            "formal Gate 7 source, contract, or Git state changed before manifest publication"
        )
    manifest["formal_claimable_mode"] = formal_claimable
    manifest["git"]["end_head_commit"] = publication_git.get("head_commit")
    manifest["git"]["end_worktree_dirty"] = publication_git.get(
        "worktree_dirty"
    )
    manifest["git"]["source_snapshot_unchanged"] = sources_unchanged
    manifest["contract"]["publication_identity_unchanged"] = contract_unchanged
    _write_json(manifest_path, manifest)
    failure_context["completed"] = True
    return manifest


def _run_preterminal_audit(output_dir: Path) -> Tuple[Dict[str, Any], int]:
    """Run the independent auditor before the formal TERMINAL ledger event."""

    output_dir = Path(output_dir).resolve()
    report_path = output_dir / "gate7-preterminal-adjudication.json"
    if report_path.exists():
        raise RuntimeError("formal preterminal adjudication path already exists")
    command = [
        sys.executable,
        str(PROJECT_ROOT / "benchmarks" / "carma" / "gate7_audit.py"),
        str(output_dir),
        "--output",
        str(report_path),
        "--preterminal",
    ]
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_environment(),
        check=False,
    )
    expected_status = {0: "pass", 1: "fail", 2: "pending", 3: "invalid"}
    if completed.returncode not in expected_status:
        raise RuntimeError(
            "independent preterminal auditor failed with exit %d: %s"
            % (completed.returncode, completed.stderr.strip())
        )
    if not report_path.is_file():
        raise RuntimeError("independent preterminal auditor wrote no report")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "independent preterminal auditor report is unreadable"
        ) from exc
    status = expected_status[completed.returncode]
    if (
        not isinstance(report, dict)
        or report.get("status") != status
        or report.get("claimable") != (status in ("pass", "fail"))
        or not isinstance(report.get("auditor_identity"), dict)
    ):
        raise RuntimeError(
            "independent preterminal auditor report contradicts its exit status"
        )
    expected_auditor_identity = {
        "path": "benchmarks/carma/gate7_audit.py",
        **_artifact_identity(PROJECT_ROOT / "benchmarks" / "carma" / "gate7_audit.py"),
    }
    if report.get("auditor_identity") != expected_auditor_identity:
        raise RuntimeError(
            "independent preterminal auditor did not identify its exact source bytes"
        )
    return report, int(completed.returncode)


def run_bundle(
    prepared_dir: Path,
    output_dir: Path,
    contract_path: Path,
    seeds: Sequence[int],
    policies: Sequence[str],
    config: Gate7Config,
) -> Dict[str, Any]:
    """Run one attempt and retain any post-start failure at attempt scope."""

    failure_context: Dict[str, Any] = {}
    with _formal_attempt_lock(config.mode == "full"):
        try:
            manifest = _run_bundle_impl(
                prepared_dir,
                output_dir,
                contract_path,
                seeds,
                policies,
                config,
                failure_context,
            )
            if config.mode == "full":
                preterminal_report, audit_exit_status = _run_preterminal_audit(
                    Path(output_dir)
                )
                _assert_source_snapshot(
                    failure_context["source_snapshot"],
                    str(failure_context["git_state"]["head_commit"]),
                )
                if _artifact_identity(Path(contract_path).resolve()) != dict(
                    failure_context["contract_identity"]
                ):
                    raise RuntimeError(
                        "formal Gate 7 contract changed during preterminal audit"
                    )
                terminal_identity = _append_formal_attempt_terminal(
                    Path(output_dir),
                    "manifest.json",
                    preterminal_report,
                )
                # Transient return-only metadata: manifest.json was already
                # atomically published and is what the TERMINAL event binds.
                manifest["_preterminal_audit_exit_status"] = audit_exit_status
                manifest["_terminal_attempt_ledger"] = terminal_identity
            return manifest
        except BaseException as error:
            output = failure_context.get("output_dir")
            if (
                isinstance(output, Path)
                and output.is_dir()
                and not (output / "manifest.json").exists()
                and not (output / "attempt-failure.json").exists()
                and (
                    config.mode != "full"
                    or isinstance(failure_context.get("attempt_ledger"), dict)
                )
            ):
                retained_samples = list(failure_context.get("resource_records", []))
                retained_samples.extend(failure_context.get("current_samples", []))
                _retain_attempt_failure(
                    output,
                    str(failure_context["attempt_id"]),
                    failure_context["started_at_utc"],
                    int(failure_context.get("seed", -1)),
                    str(failure_context.get("policy", "unknown")),
                    int(failure_context.get("order_position", -1)),
                    error,
                    retained_samples,
                    failure_context["config"],
                    failure_context["contract_identity"],
                    failure_context["git_state"],
                    failure_context["source_snapshot"],
                    failure_context.get("prior_attempts", []),
                    failure_context.get("attempt_ledger"),
                    failure_context.get("child_stdout"),
                    failure_context.get("child_stderr"),
                )
                if config.mode == "full":
                    _append_formal_attempt_terminal(
                        output, "attempt-failure.json"
                    )
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the prospective real-text ONNX Gate 7 benchmark."
    )
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--prepared", type=Path, default=DEFAULT_PREPARED)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--seeds", nargs="+", type=int, default=None)
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--requests", type=int, default=None)
    parser.add_argument("--capacity", type=int, default=None)
    parser.add_argument("--hit-threshold", type=float, default=0.97)
    parser.add_argument("--topic-threshold", type=float, default=0.70)
    parser.add_argument("--cell-threshold", type=float, default=0.97)
    parser.add_argument("--demand-half-life", type=float, default=500.0)
    parser.add_argument("--quota-strength", type=float, default=1.0)
    parser.add_argument("--ghost-support-threshold", type=float, default=1.5)
    parser.add_argument("--admission-margin", type=float, default=1.05)
    parser.add_argument("--centroid-alpha", type=float, default=0.05)
    parser.add_argument("--entry-hit-weight", type=float, default=0.25)
    parser.add_argument("--onnx-intra-threads", type=int, default=1)
    parser.add_argument("--onnx-inter-threads", type=int, default=1)
    parser.add_argument("--warmup-requests", type=int, default=20)
    parser.add_argument("--resource-interval-ms", type=int, default=100)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--fake-embedding", action="store_true")
    parser.add_argument("--child-policy", choices=POLICIES, help=argparse.SUPPRESS)
    parser.add_argument("--child-seed", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--child-order-position", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--child-attempt-id", help=argparse.SUPPRESS)
    parser.add_argument("--child-trace", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-warmup-texts", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-control-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-trace-hash", help=argparse.SUPPRESS)
    parser.add_argument("--child-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-model-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-tokenizer-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-model-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--child-source-snapshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-head-commit", help=argparse.SUPPRESS)
    return parser


def _config_from_args(args: argparse.Namespace) -> Gate7Config:
    requests = args.requests
    if requests is None:
        requests = 3000 if args.mode == "full" else 60
    capacity = args.capacity
    if capacity is None:
        capacity = 100 if args.mode == "full" else 10
    return Gate7Config(
        mode=args.mode,
        requests=requests,
        capacity=capacity,
        hit_threshold=args.hit_threshold,
        topic_threshold=args.topic_threshold,
        cell_threshold=args.cell_threshold,
        demand_half_life=args.demand_half_life,
        quota_strength=args.quota_strength,
        ghost_support_threshold=args.ghost_support_threshold,
        admission_margin=args.admission_margin,
        centroid_alpha=args.centroid_alpha,
        entry_hit_weight=args.entry_hit_weight,
        onnx_intra_threads=args.onnx_intra_threads,
        onnx_inter_threads=args.onnx_inter_threads,
        warmup_requests=args.warmup_requests,
        resource_interval_ms=args.resource_interval_ms,
        max_length=args.max_length,
        fake_embedding=bool(args.fake_embedding),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = _config_from_args(args)
    config.validate()
    if args.child_policy:
        required = (
            args.child_seed,
            args.child_order_position,
            args.child_attempt_id,
            args.child_trace,
            args.child_warmup_texts,
            args.child_control_dir,
            args.child_trace_hash,
            args.child_result,
            args.child_model_sha256,
            args.child_source_snapshot,
        )
        if any(value is None for value in required):
            raise ValueError("child mode is missing an internal argument")
        if not config.fake_embedding and (
            args.child_model_path is None or args.child_tokenizer_path is None
        ):
            raise ValueError("ONNX child mode needs local model and tokenizer paths")
        assets = _fake_assets() if config.fake_embedding else {
            "kind": "pinned_onnx",
            "model_path": str(args.child_model_path),
            "tokenizer_path": str(args.child_tokenizer_path),
            "model_sha256": str(args.child_model_sha256),
        }
        expected_sources = json.loads(
            Path(args.child_source_snapshot).read_text(encoding="utf-8")
        )
        if not isinstance(expected_sources, dict):
            raise ValueError("child source snapshot is not an object")
        _assert_source_snapshot(
            expected_sources,
            str(args.child_head_commit)
            if args.child_head_commit is not None
            else None,
        )
        result = _execute_child(
            args.child_policy,
            int(args.child_seed),
            int(args.child_order_position),
            str(args.child_attempt_id),
            Path(args.child_trace),
            Path(args.child_warmup_texts),
            Path(args.child_control_dir),
            str(args.child_trace_hash),
            config,
            assets,
        )
        _assert_source_snapshot(
            expected_sources,
            str(args.child_head_commit)
            if args.child_head_commit is not None
            else None,
        )
        _write_json(Path(args.child_result), result)
        return 0

    seeds = tuple(args.seeds or (FULL_SEEDS if args.mode == "full" else (FULL_SEEDS[0],)))
    output = args.output
    if output is None:
        if args.mode == "full":
            output = FORMAL_ATTEMPT_ROOT / (
                "attempt-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                + "-"
                + str(os.getpid())
            )
        else:
            output = PROJECT_ROOT / "artifacts" / "gate7-onnx-smoke"
    manifest = run_bundle(
        args.prepared,
        output,
        args.contract,
        seeds,
        args.policies,
        config,
    )
    print(
        "wrote %d isolated runs to %s"
        % (manifest["artifacts"]["runs.csv"]["rows"], Path(output).resolve())
    )
    return int(manifest.get("_preterminal_audit_exit_status", 0))


if __name__ == "__main__":
    raise SystemExit(main())
