"""Deterministic real-text pollution traces for the Gate 7 benchmark.

The QQP prepared directory contains both calibration and held-out pairs.  Gate
7 needs real text, but it must not consume the held-out Gate 2 split.  This
module therefore derives its concept pool exclusively from pair rows whose
``split`` is ``calibration`` and resolves only those rows' text identifiers.

No external response service is involved.  Each QQP concept is assigned a
stable local response payload so every cache policy sees exactly the same
answer identity and response materialization work.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple


SCHEMA_VERSION = "carma-gate7-real-text-trace-v1"
PHASE_WARM = "warm"
PHASE_UNIQUE = "scan"
PHASE_RETURN = "return"
CANDIDATE_NAMESPACE = "gate7b-qqp-candidate-v1"
PHASE_NAMESPACE = "gate7b-qqp-phase-v1"


@dataclass(frozen=True)
class Gate7TraceRequest:
    """One immutable, policy-independent Gate 7 request."""

    index: int
    request_id: str
    phase: str
    occurrence: int
    reuse_opportunity: bool
    text_id: str
    text: str
    concept_id: str
    response_id: str
    response_payload: str


def build_gate7_trace(
    prepared_dir: Path,
    seed: int,
    requests: int,
    capacity: int,
) -> Tuple[Tuple[Gate7TraceRequest, ...], str, Dict[str, Any]]:
    """Build a 30/40/30 real-text pollution trace from QQP calibration data.

    The returned tuple contains immutable request records, their SHA-256 trace
    identity, and provenance/selection metadata.  ``requests`` must be a
    multiple of ten so the frozen 30%/40%/30% phase proportions are exact.
    The warm phase must also be long enough to visit every member of the
    required ``floor(0.8 * capacity)`` hot set at least once.
    """

    _validate_integer("seed", seed)
    _validate_integer("requests", requests)
    _validate_integer("capacity", capacity)
    if requests < 10 or requests % 10:
        raise ValueError("requests must be a positive multiple of 10")
    if capacity < 2:
        raise ValueError("capacity must be at least 2")

    warm_count = 3 * requests // 10
    scan_count = 4 * requests // 10
    return_count = 3 * requests // 10
    hot_set_size = (4 * capacity) // 5
    if warm_count < hot_set_size:
        raise ValueError(
            "warm phase has %d requests but the required hot set has %d concepts"
            % (warm_count, hot_set_size)
        )

    prepared_dir = Path(prepared_dir)
    if not prepared_dir.is_dir():
        raise ValueError("prepared_dir is not a directory: %s" % prepared_dir)
    pairs_path = prepared_dir / "pairs.jsonl"
    texts_path = prepared_dir / "texts.jsonl"
    manifest_path = prepared_dir / "manifest.json"
    for path in (pairs_path, texts_path, manifest_path):
        if not path.is_file():
            raise ValueError("missing QQP prepared artifact: %s" % path)

    observed_hashes = {
        "pairs.jsonl": _sha256_file(pairs_path),
        "texts.jsonl": _sha256_file(texts_path),
        "manifest.json": _sha256_file(manifest_path),
    }
    manifest = _read_manifest(manifest_path)
    _validate_declared_hash(
        manifest, "pairs_sha256", observed_hashes["pairs.jsonl"]
    )
    _validate_declared_hash(
        manifest, "texts_sha256", observed_hashes["texts.jsonl"]
    )

    concept_text_ids = _load_calibration_concepts(pairs_path)
    texts_by_id = _load_required_texts(
        texts_path,
        {
            text_id
            for text_ids in concept_text_ids.values()
            for text_id in text_ids
        },
    )
    canonical_by_concept = {
        concept_id: min(
            ((text_id, texts_by_id[text_id]) for text_id in text_ids),
            key=lambda item: item[0],
        )
        for concept_id, text_ids in concept_text_ids.items()
    }

    available_concepts = tuple(sorted(canonical_by_concept))
    required_concepts = hot_set_size + scan_count
    if len(available_concepts) < required_concepts:
        raise ValueError(
            "QQP calibration split has %d concepts; Gate 7 needs at least %d "
            "(%d hot plus %d distinct scan concepts)"
            % (
                len(available_concepts),
                required_concepts,
                hot_set_size,
                scan_count,
            )
        )

    selected_order = tuple(
        sorted(
            available_concepts,
            key=lambda concept_id: (
                _candidate_priority(seed, canonical_by_concept[concept_id][0]),
                canonical_by_concept[concept_id][0],
            ),
        )
    )
    hot_concepts = tuple(selected_order[:hot_set_size])
    scan_concepts = tuple(selected_order[hot_set_size : hot_set_size + scan_count])

    def phase_occurrences(
        phase: str, concepts: Sequence[str], count: int
    ) -> List[Tuple[bytes, str, int]]:
        occurrences: Dict[str, int] = {}
        values: List[Tuple[bytes, str, int]] = []
        for position in range(count):
            concept_id = concepts[position % len(concepts)]
            occurrence = occurrences.get(concept_id, 0)
            occurrences[concept_id] = occurrence + 1
            text_id = canonical_by_concept[concept_id][0]
            values.append(
                (
                    _phase_priority(seed, phase, text_id, occurrence),
                    concept_id,
                    occurrence,
                )
            )
        values.sort(key=lambda item: (item[0], canonical_by_concept[item[1]][0], item[2]))
        return values

    phase_values = (
        (PHASE_WARM, phase_occurrences(PHASE_WARM, hot_concepts, warm_count)),
        (PHASE_UNIQUE, phase_occurrences(PHASE_UNIQUE, scan_concepts, scan_count)),
        (PHASE_RETURN, phase_occurrences(PHASE_RETURN, hot_concepts, return_count)),
    )

    records: List[Gate7TraceRequest] = []
    seen: Set[str] = set()
    for phase, values in phase_values:
        for _, concept_id, occurrence in values:
            text_id, text = canonical_by_concept[concept_id]
            response_payload = _response_payload(concept_id)
            reuse_opportunity = concept_id in seen
            seen.add(concept_id)
            index = len(records)
            records.append(
                Gate7TraceRequest(
                    index=index,
                    request_id="gate7b-%d-%04d" % (seed, index),
                    phase=phase,
                    occurrence=occurrence,
                    reuse_opportunity=reuse_opportunity,
                    text_id=text_id,
                    text=text,
                    concept_id=concept_id,
                    response_id=concept_id,
                    response_payload=response_payload,
                )
            )

    frozen_records = tuple(records)
    trace_sha256 = _trace_hash(frozen_records)
    manifest_pairs_hash = manifest.get("pairs_sha256")
    manifest_texts_hash = manifest.get("texts_sha256")
    source_files = {
        "pairs.jsonl": {
            "sha256": observed_hashes["pairs.jsonl"],
            "manifest_declared_sha256": manifest_pairs_hash,
        },
        "texts.jsonl": {
            "sha256": observed_hashes["texts.jsonl"],
            "manifest_declared_sha256": manifest_texts_hash,
        },
        "manifest.json": {"sha256": observed_hashes["manifest.json"]},
    }
    metadata: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "trace_sha256": trace_sha256,
        "seed": seed,
        "requests": requests,
        "capacity": capacity,
        "phase_counts": {
            PHASE_WARM: warm_count,
            PHASE_UNIQUE: scan_count,
            PHASE_RETURN: return_count,
        },
        "hot_set_size": hot_set_size,
        "hot_concept_ids": list(hot_concepts),
        "hot_text_ids": [canonical_by_concept[value][0] for value in hot_concepts],
        "scan_concept_ids": list(scan_concepts),
        "scan_text_ids": [canonical_by_concept[value][0] for value in scan_concepts],
        "calibration_concepts_available": len(available_concepts),
        "calibration_texts_available": len(texts_by_id),
        "selection_split": "calibration",
        "selection_rule": (
            "canonical minimum text ID per concept; exact gate7b candidate and "
            "phase SHA-256 namespaces; floor(0.8*capacity) hot concepts; "
            "next distinct concepts form the scan"
        ),
        "response_rule": "recorded-response: + concept_id; response_id is concept_id",
        "prepared_dir": str(prepared_dir.resolve()),
        "source_files": source_files,
        # Convenient scalar aliases keep manifests easy to query while the
        # structured source_files value retains observed/declared distinction.
        "source_pairs_sha256": observed_hashes["pairs.jsonl"],
        "source_texts_sha256": observed_hashes["texts.jsonl"],
        "source_manifest_sha256": observed_hashes["manifest.json"],
        "source_manifest_schema_version": manifest.get("schema_version"),
        "source_archive_sha256": manifest.get("archive_sha256"),
    }
    return frozen_records, trace_sha256, metadata


def _load_calibration_concepts(path: Path) -> Dict[str, Set[str]]:
    concept_text_ids: Dict[str, Set[str]] = {}
    concept_by_text_id: Dict[str, str] = {}
    calibration_pairs = 0
    for row in _read_jsonl(path):
        split = row.get("split")
        if split == "test":
            # In particular, do not validate or dereference held-out IDs.
            continue
        if split != "calibration":
            raise ValueError("QQP pair has unknown split %r" % split)
        calibration_pairs += 1
        for suffix in ("a", "b"):
            text_id = _required_string(row, "text_%s_id" % suffix, path)
            concept_id = _required_string(row, "concept_%s" % suffix, path)
            previous = concept_by_text_id.setdefault(text_id, concept_id)
            if previous != concept_id:
                raise ValueError(
                    "calibration text_id %s maps to multiple concepts" % text_id
                )
            concept_text_ids.setdefault(concept_id, set()).add(text_id)
    if not calibration_pairs:
        raise ValueError("QQP prepared data contains no calibration pairs")
    if not concept_text_ids:
        raise ValueError("QQP calibration split contains no concepts")
    return concept_text_ids


def _load_required_texts(path: Path, required_ids: Set[str]) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for row in _read_jsonl(path):
        text_id = row.get("text_id")
        if text_id not in required_ids:
            continue
        if text_id in found:
            raise ValueError("duplicate calibration text_id %s" % text_id)
        text = _required_string(row, "text", path)
        if not text.strip():
            raise ValueError("calibration text_id %s has empty text" % text_id)
        found[text_id] = text
    missing = sorted(required_ids.difference(found))
    if missing:
        preview = ", ".join(missing[:3])
        raise ValueError(
            "%d calibration text IDs are missing from texts.jsonl: %s"
            % (len(missing), preview)
        )
    return found


def _read_manifest(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid QQP prepared manifest: %s" % path) from exc
    if not isinstance(value, dict):
        raise ValueError("QQP prepared manifest must be a JSON object")
    return value


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "invalid JSON at %s:%d" % (path, line_number)
                    ) from exc
                if not isinstance(value, dict):
                    raise ValueError(
                        "expected JSON object at %s:%d" % (path, line_number)
                    )
                yield value
    except (OSError, UnicodeError) as exc:
        raise ValueError("could not read QQP prepared artifact: %s" % path) from exc


def _required_string(row: Mapping[str, Any], key: str, path: Path) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("%s row has invalid %s" % (path.name, key))
    return value


def _validate_integer(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer" % name)


def _validate_declared_hash(
    manifest: Mapping[str, Any], key: str, observed: str
) -> None:
    declared = manifest.get(key)
    if declared is None:
        return
    if not isinstance(declared, str) or declared != observed:
        raise ValueError(
            "QQP manifest %s mismatch: declared %r, observed %s"
            % (key, declared, observed)
        )


def _candidate_priority(seed: int, text_id: str) -> bytes:
    payload = "%s|%d|%s" % (CANDIDATE_NAMESPACE, seed, text_id)
    return hashlib.sha256(payload.encode("utf-8")).digest()


def _phase_priority(seed: int, phase: str, text_id: str, occurrence: int) -> bytes:
    payload = "%s|%d|%s|%s|%d" % (
        PHASE_NAMESPACE,
        seed,
        phase,
        text_id,
        occurrence,
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()


def _response_payload(concept_id: str) -> str:
    return "recorded-response:" + concept_id


def _trace_hash(records: Sequence[Gate7TraceRequest]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(
            json.dumps(
                asdict(record), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError("could not hash QQP prepared artifact: %s" % path) from exc
    return digest.hexdigest()


__all__ = ["Gate7TraceRequest", "build_gate7_trace"]
