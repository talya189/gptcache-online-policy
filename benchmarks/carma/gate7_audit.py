"""Independent offline adjudicator for prospective Gate 7 ONNX evidence.

The benchmark runner is deliberately not imported here.  Frozen protocol
values, integrity checks, recomputations, and pass/fail rules live in this
separate module so a defect in the producer cannot silently define what its
own output means.

Status has four possible values:

``invalid``
    Existing evidence is internally inconsistent, malformed, or has failed an
    integrity/structural check.  It cannot be used for a Gate 7 claim.
``pending``
    Valid evidence is developmental or does not contain all five frozen,
    three-policy seed blocks.  No confirmatory conclusion is available.
``fail``
    A complete, structurally valid formal experiment violates at least one
    frozen numerical bound.
``pass``
    A complete, structurally valid formal experiment satisfies every frozen
    bound for every seed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "carma-gate7-onnx-v1"
AUDIT_SCHEMA_VERSION = "carma-gate7-adjudication-v1"
ATTEMPT_LEDGER_SCHEMA_VERSION = "carma-gate7-attempt-ledger-v2"
EXPERIMENT_ID = "gate7b-onnx-v1"
PRETERMINAL_REPORT_NAME = "gate7-preterminal-adjudication.json"
TERMINAL_RECORD_NAMES = ("manifest.json", "attempt-failure.json")
ADJUDICATION_STATUSES = ("pass", "fail", "pending", "invalid")
PROTOCOL_FULL_SEEDS = (20261001, 20261002, 20261003, 20261004, 20261005)
FULL_SEEDS = PROTOCOL_FULL_SEEDS
POLICIES = ("LRU", "LFU", "CARMA")
POLICY_ORDER_NAMESPACE = "gate7b-policy-base-v1"
CANDIDATE_NAMESPACE = "gate7b-qqp-candidate-v1"
PHASE_NAMESPACE = "gate7b-qqp-phase-v1"
DEFAULT_CONTRACT_PATH = "docs/project/gate7-remediation-contract.md"
PINNED_CONTRACT_SHA256 = "e93b3f301373a0b1a1c9fa99378f555bd45b9c6e8f8717ac82ea817763ecdf4a"
EXPECTED_POLICY_ORDERS: Dict[int, Tuple[str, ...]] = {
    20261001: ("CARMA", "LFU", "LRU"),
    20261002: ("LFU", "LRU", "CARMA"),
    20261003: ("LRU", "CARMA", "LFU"),
    20261004: ("LRU", "LFU", "CARMA"),
    20261005: ("CARMA", "LRU", "LFU"),
}

FROZEN_CONFIG: Dict[str, Any] = {
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

FROZEN_MODEL: Dict[str, Any] = {
    "kind": "pinned_onnx",
    "model_repository": "GPTCache/paraphrase-albert-onnx",
    "model_revision": "5b562a100bc67e898ac89814e7a4668a18d65756",
    "model_sha256": "a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8",
    "tokenizer_repository": "GPTCache/paraphrase-albert-small-v2",
    "tokenizer_revision": "5fb246187b5489d59ce0db167e739192759defab",
    "provider": "CPUExecutionProvider",
}

FROZEN_SOURCE_HASHES = {
    "source_archive_sha256": "1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58",
    "source_pairs_sha256": "c84d9897bd4838e8c12f45d59e04401031bbd5fa533570490db016cf40235126",
    "source_texts_sha256": "645ece94cebf36d1d37a66410d925d7d2252b6d39dd42b473e866289d1576dc3",
}

REQUIRED_SOURCE_IDENTITIES = (
    "benchmarks/carma/onnx_integration_benchmark.py",
    "benchmarks/carma/gate7_audit.py",
    "benchmarks/carma/gate7_trace.py",
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

BOUNDS: Dict[str, Any] = {
    "p95_ratio_max": 1.25,
    "p95_delta_ns_max": 500_000,
    "throughput_ratio_min": 0.90,
    "rss_ratio_max": 1.20,
    "rss_delta_bytes_max": 64 * 1024 * 1024,
}

REQUIRED_ARTIFACTS = (
    "runs.csv",
    "requests.jsonl",
    "resources.jsonl",
    "outcome-latency.jsonl",
)

TIMING_FIELDS = (
    "text_preprocess_tokenize_ns",
    "onnx_inference_ns",
    "embedding_postprocess_ns",
    "faiss_search_ns",
    "sqlite_read_ns",
    "similarity_decision_ns",
    "policy_exclusive_ns",
    "sqlite_write_ns",
    "faiss_mutation_ns",
    "response_return_ns",
    "residual_ns",
    "request_total_ns",
)

SUMMARY_STAGE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "end_to_end": ("request_total_ns", "end_to_end_ns"),
    "post_embedding": ("post_embedding_total_ns", "post_embedding_ns"),
    "embedding": ("embedding_ns",),
    "faiss": ("faiss_ns",),
    "sqlite": ("sqlite_ns",),
    "policy_exclusive": ("policy_exclusive_ns",),
    "policy_inclusive": ("policy_inclusive_ns",),
    "response_return": ("response_return_ns",),
    "residual": ("residual_ns",),
}

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

TRACE_PROJECTION = (
    "request_index",
    "request_id",
    "phase",
    "occurrence",
    "reuse_opportunity",
    "text_id",
    "concept_id",
    "expected_response_id",
)

TRACE_FIELDS = {
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


class _Audit:
    def __init__(self) -> None:
        self.errors: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []

    def error(self, code: str, message: str, **context: Any) -> None:
        item: Dict[str, Any] = {"code": code, "message": message}
        if context:
            item["context"] = context
        self.errors.append(item)

    def warn(self, code: str, message: str, **context: Any) -> None:
        item: Dict[str, Any] = {"code": code, "message": message}
        if context:
            item["context"] = context
        self.warnings.append(item)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _auditor_identity() -> Dict[str, Any]:
    path = Path(__file__).resolve()
    return {
        "path": "benchmarks/carma/gate7_audit.py",
        "sha256": _sha256_file(path),
        "bytes": int(path.stat().st_size),
    }


def _json_loads(value: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError("non-finite JSON number: %s" % token)

    return json.loads(value, parse_constant=reject_constant)


def _load_json(path: Path, audit: _Audit, code: str) -> Optional[Dict[str, Any]]:
    try:
        value = _json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        audit.error(code, "cannot read valid JSON", path=str(path), detail=str(exc))
        return None
    if not isinstance(value, dict):
        audit.error(code, "JSON root must be an object", path=str(path))
        return None
    return value


def _load_jsonl(path: Path, audit: _Audit, code: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    audit.error(
                        code,
                        "blank JSONL rows are not canonical evidence",
                        path=str(path),
                        line=line_number,
                    )
                    continue
                try:
                    row = _json_loads(line)
                except (ValueError, json.JSONDecodeError) as exc:
                    audit.error(
                        code,
                        "invalid JSONL row",
                        path=str(path),
                        line=line_number,
                        detail=str(exc),
                    )
                    continue
                if not isinstance(row, dict):
                    audit.error(
                        code,
                        "JSONL row must be an object",
                        path=str(path),
                        line=line_number,
                    )
                    continue
                rows.append(row)
    except (OSError, UnicodeError) as exc:
        audit.error(code, "cannot read JSONL artifact", path=str(path), detail=str(exc))
    return rows


def _load_csv(path: Path, audit: _Audit) -> List[Dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None:
                audit.error("runs_csv_header", "runs.csv has no header")
                return []
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                audit.error("runs_csv_header", "runs.csv has duplicate column names")
            return [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        audit.error("runs_csv_read", "cannot read runs.csv", detail=str(exc))
        return []


def _row_count(path: Path) -> int:
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            next(reader, None)
            return sum(1 for _ in reader)
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as source:
            return sum(1 for line in source if line.strip())
    with path.open("rb") as source:
        return sum(1 for _ in source)


def _safe_artifact_path(bundle: Path, name: str, declaration: Mapping[str, Any]) -> Optional[Path]:
    declared_path = declaration.get("path", name)
    if not isinstance(declared_path, str) or not declared_path:
        return None
    candidate = (bundle / declared_path).resolve()
    try:
        candidate.relative_to(bundle.resolve())
    except ValueError:
        return None
    return candidate


def _verify_artifacts(
    bundle: Path, manifest: Mapping[str, Any], audit: _Audit
) -> Dict[str, Dict[str, Any]]:
    declarations = manifest.get("artifacts")
    if not isinstance(declarations, dict):
        audit.error("artifact_manifest", "manifest artifacts must be an object")
        return {}
    for required in REQUIRED_ARTIFACTS:
        if required not in declarations:
            audit.error("artifact_missing_declaration", "required artifact is undeclared", artifact=required)

    computed: Dict[str, Dict[str, Any]] = {}
    for name, raw_declaration in declarations.items():
        if not isinstance(name, str) or not isinstance(raw_declaration, dict):
            audit.error("artifact_manifest", "artifact declaration is malformed", artifact=str(name))
            continue
        path = _safe_artifact_path(bundle, name, raw_declaration)
        if path is None:
            audit.error("artifact_path", "artifact path leaves bundle or is invalid", artifact=name)
            continue
        if not path.is_file():
            audit.error("artifact_missing", "declared artifact is missing", artifact=name, path=str(path))
            continue
        try:
            values = {
                "path": str(path.relative_to(bundle)),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": _row_count(path),
            }
        except (OSError, UnicodeError, csv.Error) as exc:
            audit.error("artifact_read", "cannot inspect declared artifact", artifact=name, detail=str(exc))
            continue
        computed[name] = values
        for field in ("sha256", "rows", "bytes"):
            if field not in raw_declaration:
                audit.warn(
                    "artifact_field_missing",
                    "artifact declaration omits a reproducibility field",
                    artifact=name,
                    field=field,
                )
                continue
            observed = raw_declaration[field]
            expected = values[field]
            if observed != expected:
                audit.error(
                    "artifact_integrity",
                    "artifact declaration does not match retained bytes",
                    artifact=name,
                    field=field,
                    declared=observed,
                    computed=expected,
                )
    return computed


def _normalize_text(text: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).strip().split())


def _load_retained_traces(
    bundle: Path,
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> Dict[int, List[Dict[str, Any]]]:
    metadata_by_seed = manifest.get("trace_metadata")
    if not isinstance(metadata_by_seed, dict):
        return {}
    result: Dict[int, List[Dict[str, Any]]] = {}
    for raw_seed, metadata in metadata_by_seed.items():
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError):
            audit.error("trace_seed", "trace metadata seed key is not an integer", seed=raw_seed)
            continue
        if not isinstance(metadata, dict):
            continue
        trace_hash = metadata.get("trace_sha256")
        matches = [
            (name, values)
            for name, values in artifacts.items()
            if name.startswith("traces/")
            and values.get("sha256") == trace_hash
        ]
        if len(matches) != 1:
            audit.error(
                "trace_artifact_binding",
                "seed must bind to exactly one retained trace artifact",
                seed=seed,
                matching_artifacts=[name for name, _ in matches],
            )
            continue
        name, values = matches[0]
        path = bundle / str(values["path"])
        rows = _load_jsonl(path, audit, "trace_jsonl")
        seen_concepts: set = set()
        for position, row in enumerate(rows):
            context = {"seed": seed, "request_position": position}
            if set(row) != TRACE_FIELDS:
                audit.error(
                    "trace_schema",
                    "retained trace row has unexpected fields",
                    fields=sorted(row),
                    **context,
                )
            if row.get("index") != position:
                audit.error(
                    "trace_order",
                    "retained trace indices are not contiguous",
                    observed=row.get("index"),
                    **context,
                )
            concept_id = row.get("concept_id")
            expected_reuse = concept_id in seen_concepts
            if row.get("reuse_opportunity") is not expected_reuse:
                audit.error(
                    "trace_reuse",
                    "reuse-opportunity flag is not reproducible from prior concepts",
                    observed=row.get("reuse_opportunity"),
                    computed=expected_reuse,
                    **context,
                )
            seen_concepts.add(concept_id)
            if row.get("response_id") != concept_id or row.get(
                "response_payload"
            ) != "recorded-response:" + str(concept_id):
                audit.error(
                    "trace_response_rule",
                    "retained trace response identity violates the frozen rule",
                    **context,
                )
            if row.get("text") != _normalize_text(row.get("text")):
                audit.error(
                    "trace_text_normalization",
                    "retained trace text is not in frozen normalized form",
                    **context,
                )
        declared_rows = metadata.get("requests")
        if declared_rows != len(rows):
            audit.error(
                "trace_artifact_rows",
                "retained trace row count differs from trace metadata",
                seed=seed,
                declared=declared_rows,
                computed=len(rows),
            )
        result[seed] = rows
    return result


def _load_calibration_catalog(
    prepared_dir: Path, audit: _Audit
) -> Optional[Dict[str, Tuple[str, str]]]:
    """Rebuild canonical calibration concepts without importing the producer."""

    pairs_path = prepared_dir / "pairs.jsonl"
    texts_path = prepared_dir / "texts.jsonl"
    manifest_path = prepared_dir / "manifest.json"
    if not all(path.is_file() for path in (pairs_path, texts_path, manifest_path)):
        audit.error(
            "trace_source_missing",
            "prepared QQP sources needed for independent trace reconstruction are missing",
            prepared_dir=str(prepared_dir),
        )
        return None
    source_manifest = _load_json(manifest_path, audit, "trace_source_manifest")
    if source_manifest is None:
        return None
    observed_pairs_hash = _sha256_file(pairs_path)
    observed_texts_hash = _sha256_file(texts_path)
    if source_manifest.get("pairs_sha256") != observed_pairs_hash:
        audit.error(
            "trace_source_integrity",
            "prepared QQP pairs hash differs from its manifest",
        )
    if source_manifest.get("texts_sha256") != observed_texts_hash:
        audit.error(
            "trace_source_integrity",
            "prepared QQP texts hash differs from its manifest",
        )

    concept_text_ids: DefaultDict[str, set] = defaultdict(set)
    concept_by_text: Dict[str, str] = {}
    for row in _load_jsonl(pairs_path, audit, "trace_source_pairs"):
        split = row.get("split")
        if split == "test":
            continue
        if split != "calibration":
            audit.error(
                "trace_source_split",
                "prepared QQP pair has an unknown split",
                split=split,
            )
            continue
        for suffix in ("a", "b"):
            text_id = row.get("text_%s_id" % suffix)
            concept_id = row.get("concept_%s" % suffix)
            if not isinstance(text_id, str) or not isinstance(concept_id, str):
                audit.error(
                    "trace_source_schema",
                    "calibration endpoint lacks string text/concept identity",
                )
                continue
            previous = concept_by_text.setdefault(text_id, concept_id)
            if previous != concept_id:
                audit.error(
                    "trace_source_mapping",
                    "one calibration text ID maps to multiple concepts",
                    text_id=text_id,
                )
            concept_text_ids[concept_id].add(text_id)
    required_texts = {
        text_id for values in concept_text_ids.values() for text_id in values
    }
    text_by_id: Dict[str, str] = {}
    for row in _load_jsonl(texts_path, audit, "trace_source_texts"):
        text_id = row.get("text_id")
        if text_id not in required_texts:
            continue
        text = row.get("text")
        if not isinstance(text_id, str) or not isinstance(text, str):
            audit.error(
                "trace_source_schema",
                "required prepared text row is malformed",
            )
            continue
        if text_id in text_by_id:
            audit.error(
                "trace_source_mapping",
                "required prepared text ID is duplicated",
                text_id=text_id,
            )
            continue
        text_by_id[text_id] = text
    missing = required_texts.difference(text_by_id)
    if missing:
        audit.error(
            "trace_source_mapping",
            "prepared texts omit calibration IDs",
            missing_count=len(missing),
        )
        return None
    return {
        concept_id: min(
            ((text_id, text_by_id[text_id]) for text_id in text_ids),
            key=lambda item: item[0],
        )
        for concept_id, text_ids in concept_text_ids.items()
    }


def _rebuild_expected_trace(
    canonical: Mapping[str, Tuple[str, str]],
    seed: int,
    requests: int,
    capacity: int,
) -> List[Dict[str, Any]]:
    warm_count = 3 * requests // 10
    scan_count = 4 * requests // 10
    return_count = 3 * requests // 10
    hot_count = (4 * capacity) // 5

    def digest(namespace: str, *values: Any) -> bytes:
        payload = namespace + "|" + "|".join(str(value) for value in values)
        return hashlib.sha256(payload.encode("utf-8")).digest()

    ranked = sorted(
        canonical,
        key=lambda concept_id: (
            digest(CANDIDATE_NAMESPACE, seed, canonical[concept_id][0]),
            canonical[concept_id][0],
        ),
    )
    if len(ranked) < hot_count + scan_count:
        raise ValueError("calibration catalog is too small for the frozen trace")
    hot = ranked[:hot_count]
    scan = ranked[hot_count : hot_count + scan_count]

    def phase_rows(phase: str, concepts: Sequence[str], count: int) -> List[Tuple[bytes, str, int]]:
        occurrences: DefaultDict[str, int] = defaultdict(int)
        values: List[Tuple[bytes, str, int]] = []
        for position in range(count):
            concept_id = concepts[position % len(concepts)]
            occurrence = occurrences[concept_id]
            occurrences[concept_id] += 1
            text_id = canonical[concept_id][0]
            values.append(
                (
                    digest(PHASE_NAMESPACE, seed, phase, text_id, occurrence),
                    concept_id,
                    occurrence,
                )
            )
        values.sort(key=lambda item: (item[0], canonical[item[1]][0], item[2]))
        return values

    phases = (
        ("warm", phase_rows("warm", hot, warm_count)),
        ("scan", phase_rows("scan", scan, scan_count)),
        ("return", phase_rows("return", hot, return_count)),
    )
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for phase, values in phases:
        for _, concept_id, occurrence in values:
            text_id, text = canonical[concept_id]
            index = len(rows)
            rows.append(
                {
                    "index": index,
                    "request_id": "gate7b-%d-%04d" % (seed, index),
                    "phase": phase,
                    "occurrence": occurrence,
                    "reuse_opportunity": concept_id in seen,
                    "text_id": text_id,
                    "text": text,
                    "concept_id": concept_id,
                    "response_id": concept_id,
                    "response_payload": "recorded-response:" + concept_id,
                }
            )
            seen.add(concept_id)
    return rows


def _validate_trace_selection_from_source(
    manifest: Mapping[str, Any],
    retained: Mapping[int, Sequence[Mapping[str, Any]]],
    audit: _Audit,
) -> None:
    metadata_by_seed = manifest.get("trace_metadata")
    config = manifest.get("config")
    seeds = manifest.get("seeds")
    production_formal = (
        isinstance(config, dict)
        and config.get("mode") == "full"
        and seeds == list(PROTOCOL_FULL_SEEDS)
    )
    if not isinstance(metadata_by_seed, dict) or not metadata_by_seed:
        return
    prepared_paths = {
        metadata.get("prepared_dir")
        for metadata in metadata_by_seed.values()
        if isinstance(metadata, dict) and metadata.get("prepared_dir")
    }
    if len(prepared_paths) != 1:
        if production_formal:
            audit.error(
                "trace_source_path",
                "formal traces do not name one common prepared source directory",
            )
        return
    prepared_dir = Path(next(iter(prepared_paths))).resolve()
    if not prepared_dir.is_dir():
        repository_default = (
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "qqp-full"
            / "prepared"
        )
        if repository_default.is_dir():
            prepared_dir = repository_default.resolve()
    if not prepared_dir.is_dir():
        if production_formal:
            audit.error(
                "trace_source_missing",
                "formal trace source directory is unavailable to the auditor",
                prepared_dir=str(prepared_dir),
            )
        return
    canonical = _load_calibration_catalog(prepared_dir, audit)
    if canonical is None or not isinstance(config, dict):
        return
    try:
        requests = int(config["requests"])
        capacity = int(config["capacity"])
    except (KeyError, TypeError, ValueError):
        audit.error(
            "trace_source_rebuild",
            "trace configuration cannot be used for independent reconstruction",
        )
        return
    for seed, observed in retained.items():
        try:
            expected = _rebuild_expected_trace(
                canonical, seed, requests, capacity
            )
        except (TypeError, ValueError) as exc:
            audit.error(
                "trace_source_rebuild",
                "independent trace reconstruction failed",
                seed=seed,
                detail=str(exc),
            )
            continue
        if list(observed) != expected:
            audit.error(
                "trace_source_rebuild",
                "retained trace differs from independent frozen selection reconstruction",
                seed=seed,
            )


def _int_value(
    value: Any, audit: _Audit, code: str, field: str, **context: Any
) -> Optional[int]:
    if isinstance(value, bool):
        audit.error(code, "integer field cannot be boolean", field=field, **context)
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        audit.error(code, "field is not an integer", field=field, value=value, **context)
        return None
    if isinstance(value, float) and not value.is_integer():
        audit.error(code, "integer field has a fractional value", field=field, value=value, **context)
        return None
    return parsed


def _decimal_value(value: Any) -> Optional[Decimal]:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _bool_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return None


def _float_matches(observed: Any, expected: float, tolerance: str) -> bool:
    parsed = _decimal_value(observed)
    if parsed is None:
        return False
    return abs(parsed - Decimal(str(expected))) <= Decimal(tolerance)


def nearest_rank(values: Sequence[int], quantile: float) -> int:
    """Return the frozen one-based nearest-rank quantile."""

    if not values:
        raise ValueError("nearest_rank requires at least one value")
    if not 0.0 < quantile <= 1.0:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(int(value) for value in values)
    return ordered[int(math.ceil(quantile * len(ordered))) - 1]


def _latency_recomputation(values: Sequence[int]) -> Dict[str, Any]:
    return {
        "samples": len(values),
        "mean_ns": sum(values) / len(values),
        "p50_ns": nearest_rank(values, 0.50),
        "p95_ns": nearest_rank(values, 0.95),
        "p99_ns": nearest_rank(values, 0.99),
    }


def _field_from_aliases(row: Mapping[str, Any], aliases: Sequence[str]) -> Optional[Any]:
    for name in aliases:
        if name in row:
            return row[name]
    return None


def _validate_request_row(
    row: Mapping[str, Any], audit: _Audit, run_id: str, position: int, capacity: int
) -> bool:
    context = {"run_id": run_id, "request_position": position}
    valid = True
    for field in TIMING_FIELDS:
        value = _int_value(row.get(field), audit, "request_timing", field, **context)
        if value is None or value < 0:
            if value is not None:
                audit.error("request_timing", "timing field is negative", field=field, value=value, **context)
            valid = False
    if not valid:
        return False

    timings = {field: int(row[field]) for field in TIMING_FIELDS}
    exclusive_sum = sum(
        timings[field]
        for field in TIMING_FIELDS
        if field != "request_total_ns"
    )
    if exclusive_sum != timings["request_total_ns"]:
        audit.error(
            "request_timing_reconciliation",
            "exclusive timing stages do not sum to request_total_ns",
            expected=timings["request_total_ns"],
            computed=exclusive_sum,
            **context,
        )
    embedding = (
        timings["text_preprocess_tokenize_ns"]
        + timings["onnx_inference_ns"]
        + timings["embedding_postprocess_ns"]
    )
    if _int_value(row.get("embedding_ns"), audit, "request_derived_timing", "embedding_ns", **context) != embedding:
        audit.error("request_derived_timing", "embedding_ns does not reconcile", computed=embedding, **context)
    post_embedding = timings["request_total_ns"] - embedding
    observed_post = _field_from_aliases(row, ("post_embedding_total_ns", "post_embedding_ns"))
    if _int_value(observed_post, audit, "request_derived_timing", "post_embedding_total_ns", **context) != post_embedding:
        audit.error(
            "request_derived_timing",
            "post_embedding_total_ns does not reconcile",
            computed=post_embedding,
            **context,
        )
    cache_management = (
        timings["policy_exclusive_ns"]
        + timings["sqlite_write_ns"]
        + timings["faiss_mutation_ns"]
    )
    if _int_value(row.get("cache_management_ns"), audit, "request_derived_timing", "cache_management_ns", **context) != cache_management:
        audit.error(
            "request_derived_timing",
            "cache_management_ns does not reconcile",
            computed=cache_management,
            **context,
        )
    if row.get("exclusive_reconciles") is not True:
        audit.error("request_timing_reconciliation", "producer did not mark exclusive reconciliation true", **context)

    request_index = _int_value(row.get("request_index"), audit, "request_identity", "request_index", **context)
    if request_index != position:
        audit.error(
            "request_order",
            "request indices are not contiguous within the run",
            observed=request_index,
            expected=position,
            **context,
        )
    for field in ("cache_size_before", "cache_size_after"):
        value = _int_value(row.get(field), audit, "request_cache_size", field, **context)
        if value is not None and (value < 0 or value > capacity):
            audit.error(
                "capacity_violation",
                "request cache size is outside the frozen capacity",
                field=field,
                value=value,
                capacity=capacity,
                **context,
            )
    raw_hit = row.get("raw_hit")
    valid_hit = row.get("valid_hit")
    false_hit = row.get("false_hit")
    if not all(isinstance(value, bool) for value in (raw_hit, valid_hit, false_hit)):
        audit.error("request_hit_flags", "hit flags must be booleans", **context)
    elif valid_hit or false_hit:
        if not raw_hit or valid_hit == false_hit:
            audit.error("request_hit_flags", "raw/valid/false hit flags are inconsistent", **context)
    elif raw_hit:
        audit.error("request_hit_flags", "a raw hit must be classified valid or false", **context)
    returned_concept = row.get("returned_concept_id")
    expected_concept = row.get("concept_id")
    expected_valid_hit = bool(raw_hit is True and returned_concept == expected_concept)
    expected_false_hit = bool(raw_hit is True and returned_concept != expected_concept)
    if valid_hit is not expected_valid_hit or false_hit is not expected_false_hit:
        audit.error(
            "request_hit_semantics",
            "producer hit labels do not match the returned and expected concepts",
            returned_concept_id=returned_concept,
            expected_concept_id=expected_concept,
            declared_valid_hit=valid_hit,
            declared_false_hit=false_hit,
            computed_valid_hit=expected_valid_hit,
            computed_false_hit=expected_false_hit,
            **context,
        )
    if returned_concept != row.get("returned_response_id"):
        audit.error(
            "request_response_identity",
            "returned concept and response IDs differ",
            **context,
        )
    policy_action = row.get("policy_action")
    if raw_hit is True and policy_action != "hit":
        audit.error(
            "request_action_semantics",
            "a raw hit must have the hit policy action",
            policy_action=policy_action,
            **context,
        )
    if row.get("schema_version") != SCHEMA_VERSION:
        audit.error("request_schema", "request row has the wrong schema", **context)
    if row.get("experiment_id") != EXPERIMENT_ID:
        audit.error("request_experiment", "request row has the wrong experiment identity", **context)
    return True


def _validate_summary_latency(
    summary: Mapping[str, Any], stages: Mapping[str, Dict[str, Any]], audit: _Audit, run_id: str
) -> None:
    for stage, values in stages.items():
        expected_mean_us = round(float(values["mean_ns"]) / 1000.0, 6)
        expectations = {
            "%s_mean_us" % stage: expected_mean_us,
            "%s_p50_us" % stage: round(values["p50_ns"] / 1000.0, 6),
            "%s_p95_us" % stage: round(values["p95_ns"] / 1000.0, 6),
            "%s_p99_us" % stage: round(values["p99_ns"] / 1000.0, 6),
        }
        for field, expected in expectations.items():
            if field not in summary:
                audit.error("run_latency_field", "runs.csv lacks a latency summary field", run_id=run_id, field=field)
            elif not _float_matches(summary[field], expected, "0.000001"):
                audit.error(
                    "run_latency_mismatch",
                    "runs.csv latency is not reproducible from requests.jsonl",
                    run_id=run_id,
                    field=field,
                    declared=summary[field],
                    computed=expected,
                )


def _recompute_requests(
    rows: Sequence[Mapping[str, Any]],
    run_rows: Mapping[str, Mapping[str, str]],
    audit: _Audit,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Mapping[str, Any]]]]:
    grouped: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row_number, row in enumerate(rows, start=1):
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            audit.error("request_run_id", "request row lacks a run ID", row=row_number)
            continue
        if run_id not in run_rows:
            audit.error("request_unknown_run", "request row references an unknown run", row=row_number, run_id=run_id)
            continue
        grouped[run_id].append(row)

    recomputed: Dict[str, Dict[str, Any]] = {}
    for run_id, summary in run_rows.items():
        run_requests = grouped.get(run_id, [])
        expected_count = _int_value(summary.get("requests"), audit, "run_field", "requests", run_id=run_id)
        capacity = _int_value(summary.get("capacity"), audit, "run_field", "capacity", run_id=run_id)
        if expected_count is None or capacity is None:
            continue
        if len(run_requests) != expected_count:
            audit.error(
                "request_count",
                "request row count does not match runs.csv",
                run_id=run_id,
                declared=expected_count,
                computed=len(run_requests),
            )

        run_requests = sorted(
            run_requests,
            key=lambda row: int(row.get("request_index", -1))
            if isinstance(row.get("request_index"), (int, str)) and str(row.get("request_index")).lstrip("-").isdigit()
            else -1,
        )
        valid_timings = True
        for position, row in enumerate(run_requests):
            valid_timings = _validate_request_row(row, audit, run_id, position, capacity) and valid_timings
            for field in ("seed", "policy", "trace_hash"):
                declared = summary.get(field)
                observed = str(row.get(field))
                if observed != str(declared):
                    audit.error(
                        "request_run_identity",
                        "request identity differs from runs.csv",
                        run_id=run_id,
                        field=field,
                        declared=declared,
                        observed=row.get(field),
                    )
        if not run_requests or not valid_timings:
            continue

        summary_seed = _int_value(
            summary.get("seed"), audit, "run_field", "seed", run_id=run_id
        )
        summary_policy = summary.get("policy")
        if summary_seed is None or summary_policy not in POLICIES:
            continue

        stages: Dict[str, Dict[str, Any]] = {}
        for stage, aliases in SUMMARY_STAGE_FIELDS.items():
            stage_samples: List[int] = []
            for row in run_requests:
                value = _field_from_aliases(row, aliases)
                parsed = _int_value(value, audit, "request_stage_field", aliases[0], run_id=run_id)
                if parsed is None or parsed < 0:
                    stage_samples = []
                    break
                stage_samples.append(parsed)
            if stage_samples:
                stages[stage] = _latency_recomputation(stage_samples)
        _validate_summary_latency(summary, stages, audit, run_id)

        totals = [int(row["request_total_ns"]) for row in run_requests]
        service_seconds = sum(totals) / 1_000_000_000.0
        if not _float_matches(summary.get("service_seconds"), round(service_seconds, 9), "0.000000001"):
            audit.error(
                "service_seconds_mismatch",
                "service_seconds is not reproducible from request totals",
                run_id=run_id,
                declared=summary.get("service_seconds"),
                computed=round(service_seconds, 9),
            )
        service_qps = len(run_requests) / service_seconds if service_seconds > 0 else 0.0
        if not _float_matches(summary.get("service_throughput_qps"), round(service_qps, 6), "0.000001"):
            audit.error(
                "service_throughput_mismatch",
                "service throughput is not reproducible",
                run_id=run_id,
                declared=summary.get("service_throughput_qps"),
                computed=round(service_qps, 6),
            )

        loop_seconds_value = _decimal_value(summary.get("loop_seconds"))
        loop_seconds = float(loop_seconds_value) if loop_seconds_value is not None else 0.0
        if loop_seconds <= 0:
            audit.error("loop_seconds", "loop_seconds must be finite and positive", run_id=run_id)
        loop_start = summary.get("loop_start_monotonic_ns")
        loop_end = summary.get("loop_end_monotonic_ns")
        if loop_start not in (None, "") and loop_end not in (None, ""):
            start_ns = _int_value(loop_start, audit, "loop_timer", "loop_start_monotonic_ns", run_id=run_id)
            end_ns = _int_value(loop_end, audit, "loop_timer", "loop_end_monotonic_ns", run_id=run_id)
            if start_ns is not None and end_ns is not None:
                computed_loop_seconds = (end_ns - start_ns) / 1_000_000_000.0
                if computed_loop_seconds <= 0 or not _float_matches(
                    summary.get("loop_seconds"), round(computed_loop_seconds, 9), "0.000000001"
                ):
                    audit.error(
                        "loop_timer_mismatch",
                        "loop_seconds does not reconcile with monotonic boundaries",
                        run_id=run_id,
                        computed=round(computed_loop_seconds, 9),
                    )
        else:
            audit.warn(
                "loop_boundaries_missing",
                "throughput can be recomputed from loop_seconds but its raw timer boundaries are absent",
                run_id=run_id,
            )
        loop_qps = len(run_requests) / loop_seconds if loop_seconds > 0 else 0.0
        for field in ("throughput_qps", "loop_throughput_qps"):
            if not _float_matches(summary.get(field), round(loop_qps, 6), "0.000001"):
                audit.error(
                    "throughput_mismatch",
                    "throughput is not reproducible from request count and loop_seconds",
                    run_id=run_id,
                    field=field,
                    declared=summary.get(field),
                    computed=round(loop_qps, 6),
                )

        hits = sum(row.get("raw_hit") is True for row in run_requests)
        valid_hits = sum(
            row.get("raw_hit") is True
            and row.get("returned_concept_id") == row.get("concept_id")
            for row in run_requests
        )
        false_hits = sum(
            row.get("raw_hit") is True
            and row.get("returned_concept_id") != row.get("concept_id")
            for row in run_requests
        )
        unknown_answers = sum(
            row.get("returned_response_id") != row.get("expected_response_id")
            for row in run_requests
        )
        max_cache_size = max(
            max(int(row.get("cache_size_before", 0)), int(row.get("cache_size_after", 0)))
            for row in run_requests
        )
        count_expectations = {
            "hits": hits,
            "misses": len(run_requests) - hits,
            "valid_hits": valid_hits,
            "false_hits": false_hits,
            "unknown_answers": unknown_answers,
            "max_cache_size": max_cache_size,
        }
        for field, expected in count_expectations.items():
            observed = _int_value(summary.get(field), audit, "run_counter", field, run_id=run_id)
            if observed != expected:
                audit.error(
                    "run_counter_mismatch",
                    "runs.csv counter is not reproducible from request records",
                    run_id=run_id,
                    field=field,
                    declared=observed,
                    computed=expected,
                )

        recomputed[run_id] = {
            "run_id": run_id,
            "seed": summary_seed,
            "policy": summary_policy,
            "trace_hash": summary["trace_hash"],
            "request_count": len(run_requests),
            "latency": stages,
            "service_seconds": service_seconds,
            "loop_seconds": loop_seconds,
            "throughput_qps_unrounded": loop_qps,
            "hit_counts": count_expectations,
        }
        grouped[run_id] = run_requests
    return recomputed, dict(grouped)


def _request_outcome(row: Mapping[str, Any]) -> Optional[str]:
    if row.get("raw_hit") is True:
        return "hit"
    if row.get("rejected") is True:
        return "rejected"
    if row.get("admitted") is True and row.get("evicted") is True:
        return "admitted_with_eviction"
    if row.get("admitted") is True and row.get("evicted") is False:
        return "admitted_without_eviction"
    return None


def _recompute_outcome_latency(
    declared_rows: Sequence[Mapping[str, Any]],
    requests: Mapping[str, Sequence[Mapping[str, Any]]],
    run_rows: Mapping[str, Mapping[str, str]],
    audit: _Audit,
) -> Dict[str, Dict[str, Any]]:
    declared: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for row_number, row in enumerate(declared_rows, start=1):
        run_id = row.get("run_id")
        outcome = row.get("outcome")
        if not isinstance(run_id, str) or run_id not in run_rows:
            audit.error(
                "outcome_run_id",
                "outcome latency row references an unknown run",
                row=row_number,
                run_id=run_id,
            )
            continue
        if outcome not in OUTCOME_REPORT_NAMES:
            audit.error(
                "outcome_name",
                "outcome latency row has an unknown category",
                row=row_number,
                outcome=outcome,
            )
            continue
        key = (run_id, str(outcome))
        if key in declared:
            audit.error(
                "outcome_duplicate",
                "outcome latency rows duplicate a run/category",
                run_id=run_id,
                outcome=outcome,
            )
            continue
        declared[key] = row

    recomputed: Dict[str, Dict[str, Any]] = {}
    for run_id, summary in run_rows.items():
        by_outcome: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for request in requests.get(run_id, []):
            outcome = _request_outcome(request)
            if outcome is None:
                audit.error(
                    "outcome_partition",
                    "request does not belong to one frozen outcome category",
                    run_id=run_id,
                    request_index=request.get("request_index"),
                )
                continue
            by_outcome[outcome].append(request)
        run_result: Dict[str, Any] = {}
        all_requests = list(requests.get(run_id, []))
        for outcome in OUTCOME_REPORT_NAMES:
            row = declared.get((run_id, outcome))
            if row is None:
                audit.error(
                    "outcome_missing",
                    "run lacks a required outcome latency row",
                    run_id=run_id,
                    outcome=outcome,
                )
                continue
            for field in ("schema_version", "experiment_id", "attempt_id", "seed", "policy"):
                expected = (
                    SCHEMA_VERSION
                    if field == "schema_version"
                    else EXPERIMENT_ID
                    if field == "experiment_id"
                    else summary.get(field)
                )
                if str(row.get(field)) != str(expected):
                    audit.error(
                        "outcome_identity",
                        "outcome latency identity differs from its run",
                        run_id=run_id,
                        outcome=outcome,
                        field=field,
                        observed=row.get(field),
                        expected=expected,
                    )
            outcome_requests = (
                all_requests if outcome == "all" else by_outcome.get(outcome, [])
            )
            sample_count = len(outcome_requests)
            if _int_value(
                row.get("samples"),
                audit,
                "outcome_samples",
                "samples",
                run_id=run_id,
                outcome=outcome,
            ) != sample_count:
                audit.error(
                    "outcome_samples",
                    "outcome sample count is not reproducible",
                    run_id=run_id,
                    outcome=outcome,
                    computed=sample_count,
                )
            latency = row.get("latency")
            if not isinstance(latency, dict):
                audit.error(
                    "outcome_latency",
                    "outcome latency payload must be an object",
                    run_id=run_id,
                    outcome=outcome,
                )
                continue
            stage_result: Dict[str, Any] = {}
            for stage, field in OUTCOME_LATENCY_FIELDS.items():
                observed = latency.get(stage)
                if not isinstance(observed, dict):
                    audit.error(
                        "outcome_latency",
                        "outcome latency lacks a stage object",
                        run_id=run_id,
                        outcome=outcome,
                        stage=stage,
                    )
                    continue
                values = [int(request[field]) for request in outcome_requests]
                expected_values: Dict[str, Any]
                if values:
                    expected_values = {
                        "mean_ns": sum(values) / len(values),
                        "p50_ns": nearest_rank(values, 0.50),
                        "p95_ns": nearest_rank(values, 0.95),
                        "p99_ns": nearest_rank(values, 0.99),
                    }
                else:
                    expected_values = {
                        "mean_ns": None,
                        "p50_ns": None,
                        "p95_ns": None,
                        "p99_ns": None,
                    }
                for statistic, expected in expected_values.items():
                    value = observed.get(statistic)
                    if expected is None:
                        matches = value is None
                    elif statistic == "mean_ns":
                        matches = _float_matches(value, float(expected), "0.000000001")
                    else:
                        matches = value == expected
                    if not matches:
                        audit.error(
                            "outcome_latency_mismatch",
                            "outcome latency is not reproducible from request rows",
                            run_id=run_id,
                            outcome=outcome,
                            stage=stage,
                            statistic=statistic,
                            declared=value,
                            computed=expected,
                        )
                stage_result[stage] = expected_values
            run_result[outcome] = {
                "samples": sample_count,
                "latency": stage_result,
            }
        recomputed[run_id] = run_result
    return recomputed


def _recompute_resources(
    rows: Sequence[Mapping[str, Any]],
    run_rows: Mapping[str, Mapping[str, str]],
    audit: _Audit,
    interval_ms: int,
) -> Dict[str, Dict[str, Any]]:
    grouped: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row_number, row in enumerate(rows, start=1):
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or run_id not in run_rows:
            audit.error("resource_unknown_run", "resource row references an unknown run", row=row_number, run_id=run_id)
            continue
        grouped[run_id].append(row)

    recomputed: Dict[str, Dict[str, Any]] = {}
    for run_id, summary in run_rows.items():
        samples = grouped.get(run_id, [])
        if not samples:
            audit.error("resource_missing", "run has no external resource samples", run_id=run_id)
            continue
        def sample_sort_key(row: Mapping[str, Any]) -> int:
            value = row.get("sample_index")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lstrip("-").isdigit():
                return int(value)
            return -1

        samples = sorted(samples, key=sample_sort_key)
        sample_indices = [
            _int_value(
                row.get("sample_index"),
                audit,
                "resource_order",
                "sample_index",
                run_id=run_id,
                sample_position=position,
            )
            for position, row in enumerate(samples)
        ]
        if sample_indices != list(range(len(samples))):
            audit.error("resource_order", "resource sample indices are not contiguous", run_id=run_id)
        if samples[0].get("kind") != "loop_start" or samples[-1].get("kind") != "loop_end":
            audit.error("resource_boundaries", "resource samples do not retain mandatory loop boundaries", run_id=run_id)
        if sum(row.get("kind") == "loop_start" for row in samples) != 1 or sum(
            row.get("kind") == "loop_end" for row in samples
        ) != 1:
            audit.error("resource_boundaries", "run must contain exactly one start and one end snapshot", run_id=run_id)
        if any(
            row.get("kind") != "periodic" for row in samples[1:-1]
        ):
            audit.error(
                "resource_cadence",
                "all interior resource snapshots must be periodic samples",
                run_id=run_id,
            )

        monotonic_values = [
            _int_value(
                row.get("monotonic_ns"),
                audit,
                "resource_monotonic",
                "monotonic_ns",
                run_id=run_id,
                sample_position=position,
            )
            for position, row in enumerate(samples)
        ]
        valid_monotonic = all(value is not None for value in monotonic_values)
        gaps: List[int] = []
        if valid_monotonic:
            monotonic_ns = [int(value) for value in monotonic_values if value is not None]
            gaps = [
                current - previous
                for previous, current in zip(monotonic_ns, monotonic_ns[1:])
            ]
            if any(gap <= 0 for gap in gaps):
                audit.error(
                    "resource_monotonic",
                    "resource sample times must be strictly increasing",
                    run_id=run_id,
                )
            maximum_gap_ns = 2 * int(interval_ms) * 1_000_000
            if gaps and max(gaps) > maximum_gap_ns:
                audit.error(
                    "resource_cadence",
                    "external sampling has a gap larger than two frozen intervals",
                    run_id=run_id,
                    maximum_observed_gap_ns=max(gaps),
                    maximum_allowed_gap_ns=maximum_gap_ns,
                )
        else:
            monotonic_ns = []

        rss: List[int] = []
        uss: List[int] = []
        for position, row in enumerate(samples):
            context = {"run_id": run_id, "sample_position": position}
            if row.get("schema_version") != SCHEMA_VERSION:
                audit.error("resource_schema", "resource row has the wrong schema", **context)
            for field in ("seed", "policy"):
                if str(row.get(field)) != str(summary.get(field)):
                    audit.error("resource_identity", "resource identity differs from runs.csv", field=field, **context)
            observed_child_pid = _int_value(
                row.get("child_pid"),
                audit,
                "resource_child_pid",
                "child_pid",
                **context,
            )
            expected_child_pid = _int_value(
                summary.get("child_pid"),
                audit,
                "resource_child_pid",
                "child_pid",
                run_id=run_id,
            )
            if observed_child_pid != expected_child_pid:
                audit.error(
                    "resource_child_pid",
                    "resource sample child PID differs from runs.csv",
                    observed=observed_child_pid,
                    expected=expected_child_pid,
                    **context,
                )
            if row.get("scope") != "formal":
                audit.error("resource_scope", "resource sample is outside formal scope", **context)
            vms_value = _int_value(
                row.get("vms_bytes"), audit, "resource_value", "vms_bytes", **context
            )
            thread_count = _int_value(
                row.get("num_threads"),
                audit,
                "resource_value",
                "num_threads",
                **context,
            )
            if vms_value is not None and vms_value < 0:
                audit.error("resource_value", "VMS must be non-negative", **context)
            if thread_count is not None and thread_count <= 0:
                audit.error("resource_value", "thread count must be positive", **context)
            rss_value = _int_value(row.get("rss_bytes"), audit, "resource_value", "rss_bytes", **context)
            if rss_value is not None and rss_value >= 0:
                rss.append(rss_value)
            else:
                audit.error("resource_value", "RSS must be non-negative", **context)
            if row.get("uss_bytes") is not None:
                uss_value = _int_value(row.get("uss_bytes"), audit, "resource_value", "uss_bytes", **context)
                if uss_value is not None and uss_value >= 0:
                    uss.append(uss_value)
                else:
                    audit.error("resource_value", "USS must be null or non-negative", **context)
            if row.get("uss_available") is not (row.get("uss_bytes") is not None):
                audit.error(
                    "resource_availability",
                    "USS availability flag disagrees with the retained value",
                    **context,
                )
        if len(rss) != len(samples):
            continue
        rss_mean = sum(rss) / len(rss)
        rss_peak = max(rss)
        rss_expectations = {
            "rss_start_bytes": rss[0],
            "rss_end_bytes": rss[-1],
            "rss_end_minus_start_bytes": rss[-1] - rss[0],
            "rss_mean_bytes": round(rss_mean, 3),
            "rss_peak_sampled_bytes": rss_peak,
            "rss_peak_bytes": rss_peak,
            "rss_incremental_peak_bytes": max(0, rss_peak - rss[0]),
        }
        for field, expected in rss_expectations.items():
            tolerance = "0.001" if field == "rss_mean_bytes" else "0"
            if not _float_matches(summary.get(field), expected, tolerance):
                audit.error(
                    "resource_summary_mismatch",
                    "runs.csv resource value is not reproducible",
                    run_id=run_id,
                    field=field,
                    declared=summary.get(field),
                    computed=expected,
                )
        uss_mean: Optional[float] = None
        uss_peak: Optional[int] = None
        uss_availability = [row.get("uss_available") for row in samples]
        if not all(isinstance(value, bool) for value in uss_availability):
            audit.error(
                "resource_availability",
                "USS availability flags must be booleans",
                run_id=run_id,
            )
        elif len(set(uss_availability)) != 1:
            audit.error(
                "resource_availability",
                "USS availability changed within one run",
                run_id=run_id,
            )
        if uss:
            uss_mean = sum(uss) / len(uss)
            uss_peak = max(uss)
            if not _float_matches(summary.get("uss_mean_bytes"), round(uss_mean, 3), "0.001"):
                audit.error("resource_summary_mismatch", "USS mean is not reproducible", run_id=run_id)
            if not _float_matches(summary.get("uss_peak_bytes"), uss_peak, "0"):
                audit.error("resource_summary_mismatch", "USS peak is not reproducible", run_id=run_id)
            uss_expectations = {
                "uss_start_bytes": uss[0],
                "uss_end_bytes": uss[-1],
                "uss_incremental_peak_bytes": max(0, uss_peak - uss[0]),
                "uss_end_minus_start_bytes": uss[-1] - uss[0],
            }
            for field, expected in uss_expectations.items():
                if not _float_matches(summary.get(field), expected, "0"):
                    audit.error(
                        "resource_summary_mismatch",
                        "USS boundary/delta is not reproducible",
                        run_id=run_id,
                        field=field,
                        computed=expected,
                    )
        elif any(
            summary.get(field) not in (None, "")
            for field in (
                "uss_start_bytes",
                "uss_end_bytes",
                "uss_mean_bytes",
                "uss_peak_bytes",
                "uss_incremental_peak_bytes",
                "uss_end_minus_start_bytes",
            )
        ):
            audit.error("resource_summary_mismatch", "USS summary exists without supported USS samples", run_id=run_id)

        cpu_user = [_decimal_value(row.get("cpu_user_seconds")) for row in samples]
        cpu_system = [_decimal_value(row.get("cpu_system_seconds")) for row in samples]
        if any(value is None for value in cpu_user + cpu_system):
            audit.error(
                "resource_cpu",
                "CPU counters must be finite in every resource sample",
                run_id=run_id,
            )
            cpu_user_delta = None
            cpu_system_delta = None
        else:
            cpu_user_delta = max(Decimal(0), cpu_user[-1] - cpu_user[0])
            cpu_system_delta = max(Decimal(0), cpu_system[-1] - cpu_system[0])
            cpu_total_delta = cpu_user_delta + cpu_system_delta
            resource_window_seconds = (
                Decimal(monotonic_ns[-1] - monotonic_ns[0]) / Decimal(1_000_000_000)
                if len(monotonic_ns) >= 2
                else Decimal(0)
            )
            cpu_expectations = {
                "cpu_user_seconds": round(float(cpu_user_delta), 9),
                "cpu_system_seconds": round(float(cpu_system_delta), 9),
                "cpu_total_seconds": round(float(cpu_total_delta), 9),
                "resource_window_seconds": round(float(resource_window_seconds), 9),
                "cpu_utilization_percent": (
                    round(100.0 * float(cpu_total_delta / resource_window_seconds), 6)
                    if resource_window_seconds > 0
                    else None
                ),
            }
            for field, expected in cpu_expectations.items():
                if expected is None:
                    if summary.get(field) not in (None, ""):
                        audit.error(
                            "resource_summary_mismatch",
                            "CPU summary exists for a zero resource window",
                            run_id=run_id,
                            field=field,
                        )
                elif not _float_matches(
                    summary.get(field),
                    expected,
                    "0.000001" if field == "cpu_utilization_percent" else "0.000000001",
                ):
                    audit.error(
                        "resource_summary_mismatch",
                        "runs.csv CPU value is not reproducible",
                        run_id=run_id,
                        field=field,
                        declared=summary.get(field),
                        computed=expected,
                    )

        loop_start = _int_value(
            summary.get("loop_start_monotonic_ns"),
            audit,
            "resource_coverage",
            "loop_start_monotonic_ns",
            run_id=run_id,
        )
        loop_end = _int_value(
            summary.get("loop_end_monotonic_ns"),
            audit,
            "resource_coverage",
            "loop_end_monotonic_ns",
            run_id=run_id,
        )
        if monotonic_ns and loop_start is not None and loop_end is not None:
            if monotonic_ns[0] > loop_start or monotonic_ns[-1] < loop_end:
                audit.error(
                    "resource_coverage",
                    "mandatory resource snapshots do not cover the request loop",
                    run_id=run_id,
                    sample_start_ns=monotonic_ns[0],
                    loop_start_ns=loop_start,
                    sample_end_ns=monotonic_ns[-1],
                    loop_end_ns=loop_end,
                )

        io_availability = [row.get("io_counters_available") for row in samples]
        if not all(isinstance(value, bool) for value in io_availability):
            audit.error(
                "resource_io",
                "I/O availability flags must be booleans",
                run_id=run_id,
            )
        elif len(set(io_availability)) != 1:
            audit.error(
                "resource_io",
                "I/O counter availability changed within one run",
                run_id=run_id,
            )
        elif io_availability[0]:
            for field in (
                "io_read_count",
                "io_write_count",
                "io_read_bytes",
                "io_write_bytes",
            ):
                values = [
                    _int_value(
                        row.get(field),
                        audit,
                        "resource_io",
                        field,
                        run_id=run_id,
                        sample_position=position,
                    )
                    for position, row in enumerate(samples)
                ]
                if any(value is None for value in values):
                    continue
                delta = max(0, int(values[-1]) - int(values[0]))
                if _int_value(
                    summary.get(field + "_delta"),
                    audit,
                    "resource_summary_mismatch",
                    field + "_delta",
                    run_id=run_id,
                ) != delta:
                    audit.error(
                        "resource_summary_mismatch",
                        "runs.csv I/O delta is not reproducible",
                        run_id=run_id,
                        field=field + "_delta",
                        computed=delta,
                    )
        else:
            if any(
                row.get(field) is not None
                for row in samples
                for field in (
                    "io_read_count",
                    "io_write_count",
                    "io_read_bytes",
                    "io_write_bytes",
                )
            ):
                audit.error(
                    "resource_io",
                    "unsupported I/O counters must be retained as null",
                    run_id=run_id,
                )
            for field in (
                "io_read_count_delta",
                "io_write_count_delta",
                "io_read_bytes_delta",
                "io_write_bytes_delta",
            ):
                if summary.get(field) not in (None, ""):
                    audit.error(
                        "resource_summary_mismatch",
                        "runs.csv has an I/O delta without supported counters",
                        run_id=run_id,
                        field=field,
                    )
        recomputed[run_id] = {
            "samples": len(samples),
            "rss_start_bytes": rss[0],
            "rss_end_bytes": rss[-1],
            "rss_mean_bytes_unrounded": rss_mean,
            "rss_peak_bytes": rss_peak,
            "rss_incremental_peak_bytes": max(0, rss_peak - rss[0]),
            "rss_end_minus_start_bytes": rss[-1] - rss[0],
            "uss_supported_samples": len(uss),
            "uss_start_bytes": uss[0] if uss else None,
            "uss_end_bytes": uss[-1] if uss else None,
            "uss_mean_bytes_unrounded": uss_mean,
            "uss_peak_bytes": uss_peak,
            "uss_incremental_peak_bytes": (
                max(0, uss_peak - uss[0])
                if uss and uss_peak is not None
                else None
            ),
            "uss_end_minus_start_bytes": uss[-1] - uss[0] if uss else None,
            "cpu_user_start_seconds": float(cpu_user[0]) if cpu_user[0] is not None else None,
            "cpu_user_end_seconds": float(cpu_user[-1]) if cpu_user[-1] is not None else None,
            "cpu_system_start_seconds": float(cpu_system[0]) if cpu_system[0] is not None else None,
            "cpu_system_end_seconds": float(cpu_system[-1]) if cpu_system[-1] is not None else None,
            "maximum_sample_gap_ns": max(gaps) if valid_monotonic and gaps else 0,
            "resource_window_seconds": (
                (monotonic_ns[-1] - monotonic_ns[0]) / 1_000_000_000.0
                if len(monotonic_ns) >= 2
                else 0.0
            ),
        }
    return recomputed


def _validate_run_structure(
    run_rows: Mapping[str, Mapping[str, str]], audit: _Audit
) -> Dict[int, Dict[str, str]]:
    by_seed: DefaultDict[int, Dict[str, str]] = defaultdict(dict)
    child_pids: Dict[int, str] = {}
    storage_instances: Dict[str, str] = {}
    for run_id, row in run_rows.items():
        seed = _int_value(row.get("seed"), audit, "run_field", "seed", run_id=run_id)
        policy = row.get("policy")
        if seed is None or policy not in POLICIES:
            if policy not in POLICIES:
                audit.error("run_policy", "run has an unknown policy", run_id=run_id, policy=policy)
            continue
        if policy in by_seed[seed]:
            audit.error("duplicate_seed_policy", "seed has duplicate policy runs", seed=seed, policy=policy)
        by_seed[seed][policy] = run_id
        if row.get("schema_version") != SCHEMA_VERSION:
            audit.error("run_schema", "runs.csv row has the wrong schema", run_id=run_id)
        child_exit_status = _int_value(
            row.get("child_exit_status"),
            audit,
            "child_exit_status",
            "child_exit_status",
            run_id=run_id,
        )
        if child_exit_status != 0:
            audit.error(
                "child_exit_status",
                "retained run is not a successful child execution",
                run_id=run_id,
                value=child_exit_status,
            )
        child_pid = _int_value(
            row.get("child_pid"), audit, "child_pid", "child_pid", run_id=run_id
        )
        if child_pid is None or child_pid <= 0:
            audit.error("child_pid", "child PID must be a positive integer", run_id=run_id)
        elif child_pid in child_pids:
            audit.error(
                "child_pid_reuse",
                "fresh policy processes must have unique retained child PIDs",
                run_id=run_id,
                other_run_id=child_pids[child_pid],
                child_pid=child_pid,
            )
        else:
            child_pids[child_pid] = run_id
        storage_identity = row.get("storage_instance_sha256")
        if (
            not isinstance(storage_identity, str)
            or len(storage_identity) != 64
            or any(character not in "0123456789abcdef" for character in storage_identity)
        ):
            audit.error(
                "storage_instance",
                "storage instance identity must be a lowercase SHA-256",
                run_id=run_id,
            )
        elif storage_identity in storage_instances:
            audit.error(
                "storage_instance_reuse",
                "fresh policy runs must have unique storage identities",
                run_id=run_id,
                other_run_id=storage_instances[storage_identity],
            )
        else:
            storage_instances[storage_identity] = run_id
        embedding_dimension = _int_value(
            row.get("embedding_dimension"),
            audit,
            "embedding_dimension",
            "embedding_dimension",
            run_id=run_id,
        )
        if embedding_dimension is None or embedding_dimension <= 0:
            audit.error(
                "embedding_dimension",
                "embedding dimension must be positive",
                run_id=run_id,
                value=embedding_dimension,
            )
        elif row.get("mode") == "full" and embedding_dimension != 768:
            audit.error(
                "embedding_dimension",
                "formal Gate 7 ONNX evidence requires 768-dimensional embeddings",
                run_id=run_id,
                value=embedding_dimension,
            )
        for field in ("embedding_norm_verified", "provider_verified"):
            if _bool_value(row.get(field)) is not True:
                audit.error(
                    "embedding_verification",
                    "formal run lacks a successful embedding/provider verification flag",
                    run_id=run_id,
                    field=field,
                    observed=row.get(field),
                )
        request_buffer_rows = _int_value(
            row.get("request_buffer_rows_reserved"),
            audit,
            "buffer_reservation",
            "request_buffer_rows_reserved",
            run_id=run_id,
        )
        embedding_buffer_bytes = _int_value(
            row.get("embedding_buffer_bytes_reserved"),
            audit,
            "buffer_reservation",
            "embedding_buffer_bytes_reserved",
            run_id=run_id,
        )
        declared_requests = _int_value(
            row.get("requests"),
            audit,
            "buffer_reservation",
            "requests",
            run_id=run_id,
        )
        if declared_requests is not None and request_buffer_rows != declared_requests:
            audit.error(
                "buffer_reservation",
                "request buffer was not reserved at full run capacity",
                run_id=run_id,
                declared=request_buffer_rows,
                expected=declared_requests,
            )
        if (
            declared_requests is not None
            and embedding_dimension is not None
            and embedding_buffer_bytes != declared_requests * embedding_dimension * 4
        ):
            audit.error(
                "buffer_reservation",
                "embedding evidence buffer has the wrong reserved byte capacity",
                run_id=run_id,
                declared=embedding_buffer_bytes,
                expected=declared_requests * embedding_dimension * 4,
            )
        for field in ("false_hits", "stale_candidates", "unknown_answers", "deleted_scalar_count"):
            value = _int_value(row.get(field), audit, "structural_counter", field, run_id=run_id)
            if value != 0:
                audit.error("structural_failure", "run has a nonzero structural failure counter", run_id=run_id, field=field, value=value)
        capacity = _int_value(row.get("capacity"), audit, "run_field", "capacity", run_id=run_id)
        max_size = _int_value(row.get("max_cache_size"), audit, "run_field", "max_cache_size", run_id=run_id)
        scalar = _int_value(row.get("final_scalar_count"), audit, "run_field", "final_scalar_count", run_id=run_id)
        vector = _int_value(row.get("final_vector_count"), audit, "run_field", "final_vector_count", run_id=run_id)
        verified = _int_value(row.get("verified_entries"), audit, "run_field", "verified_entries", run_id=run_id)
        if capacity is not None and max_size is not None and max_size > capacity:
            audit.error("capacity_violation", "run exceeded configured capacity", run_id=run_id)
        if scalar is not None and vector is not None and verified is not None and not (scalar == vector == verified):
            audit.error("storage_divergence", "final SQLite/FAISS/verification counts diverge", run_id=run_id)
        if capacity is not None and scalar is not None and scalar > capacity:
            audit.error("capacity_violation", "final storage exceeds capacity", run_id=run_id)
    return dict(by_seed)


def _trace_projection(rows: Sequence[Mapping[str, Any]]) -> List[Tuple[Any, ...]]:
    return [tuple(row.get(field) for field in TRACE_PROJECTION) for row in rows]


def _validate_pairing_and_trace(
    manifest: Mapping[str, Any],
    by_seed: Mapping[int, Mapping[str, str]],
    run_rows: Mapping[str, Mapping[str, str]],
    requests: Mapping[str, Sequence[Mapping[str, Any]]],
    retained_traces: Mapping[int, Sequence[Mapping[str, Any]]],
    audit: _Audit,
) -> None:
    policy_orders = manifest.get("policy_orders")
    planned_policy_orders = manifest.get("planned_policy_orders")
    trace_metadata = manifest.get("trace_metadata")
    if not isinstance(policy_orders, dict):
        audit.error("policy_orders", "manifest policy_orders must be an object")
        policy_orders = {}
    if not isinstance(planned_policy_orders, dict):
        audit.error("planned_policy_orders", "manifest planned_policy_orders must be an object")
        planned_policy_orders = {}
    if planned_policy_orders != policy_orders:
        audit.error(
            "planned_actual_policy_order",
            "planned and actual policy-order maps differ",
            planned=planned_policy_orders,
            actual=policy_orders,
        )
    if not isinstance(trace_metadata, dict):
        audit.error("trace_metadata", "manifest trace_metadata must be an object")
        trace_metadata = {}

    for seed, policy_runs in by_seed.items():
        hashes = {run_rows[run_id].get("trace_hash") for run_id in policy_runs.values()}
        if len(hashes) != 1:
            audit.error("trace_pairing", "policies within a seed have different trace hashes", seed=seed)
        embeddings = {
            run_rows[run_id].get("embedding_digest_sha256") for run_id in policy_runs.values()
        }
        if len(embeddings) != 1:
            audit.error("embedding_pairing", "policies within a seed have different embedding digests", seed=seed)
        projections = {
            policy: _trace_projection(requests.get(run_id, []))
            for policy, run_id in policy_runs.items()
        }
        if projections:
            first = next(iter(projections.values()))
            for policy, projection in projections.items():
                if projection != first:
                    audit.error("trace_pairing", "policy request traces differ within a seed", seed=seed, policy=policy)
        retained = retained_traces.get(seed)
        if retained is None:
            audit.error(
                "trace_artifact_binding",
                "seed has no independently loaded retained trace",
                seed=seed,
            )
        else:
            retained_projection = [
                (
                    row.get("index"),
                    row.get("request_id"),
                    row.get("phase"),
                    row.get("occurrence"),
                    row.get("reuse_opportunity"),
                    row.get("text_id"),
                    row.get("concept_id"),
                    row.get("response_id"),
                )
                for row in retained
            ]
            for policy, projection in projections.items():
                if projection != retained_projection:
                    audit.error(
                        "trace_request_binding",
                        "request rows do not reproduce the retained trace",
                        seed=seed,
                        policy=policy,
                    )
            for policy, run_id in policy_runs.items():
                run_requests = requests.get(run_id, [])
                if len(run_requests) != len(retained):
                    continue
                for position, (request_row, trace_row) in enumerate(
                    zip(run_requests, retained)
                ):
                    expected_text_hash = hashlib.sha256(
                        _normalize_text(trace_row.get("text")).encode("utf-8")
                    ).hexdigest()
                    if request_row.get("text_sha256") != expected_text_hash:
                        audit.error(
                            "trace_text_binding",
                            "request text hash differs from the retained raw text",
                            seed=seed,
                            policy=policy,
                            request_position=position,
                        )
                    if request_row.get("expected_response_id") != trace_row.get(
                        "response_id"
                    ):
                        audit.error(
                            "trace_response_binding",
                            "request expected response differs from the retained trace",
                            seed=seed,
                            policy=policy,
                            request_position=position,
                        )
        declared_order = policy_orders.get(str(seed))
        if not isinstance(declared_order, list):
            audit.error("policy_order", "seed lacks a declared policy order", seed=seed)
            continue
        actual_order: List[Optional[str]] = [None] * len(declared_order)
        for policy, run_id in policy_runs.items():
            position = _int_value(run_rows[run_id].get("order_position"), audit, "policy_order", "order_position", seed=seed, policy=policy)
            if position is None or not 0 <= position < len(actual_order) or actual_order[position] is not None:
                audit.error("policy_order", "policy order positions are invalid", seed=seed, policy=policy, position=position)
            else:
                actual_order[position] = policy
        if actual_order != declared_order:
            audit.error("policy_order", "runs.csv order differs from manifest order", seed=seed, declared=declared_order, actual=actual_order)

        metadata = trace_metadata.get(str(seed))
        if not isinstance(metadata, dict):
            audit.error("trace_metadata", "seed lacks trace construction metadata", seed=seed)
            continue
        trace_hash = next(iter(hashes)) if len(hashes) == 1 else None
        if metadata.get("trace_sha256") != trace_hash:
            audit.error("trace_metadata", "trace metadata hash differs from policy runs", seed=seed)
        if metadata.get("selection_split") != "calibration":
            audit.error("trace_provenance", "trace is not calibration-only", seed=seed)

        first_run_id = next(iter(policy_runs.values()))
        projected_rows = requests.get(first_run_id, [])
        request_count = _int_value(
            run_rows[first_run_id].get("requests"),
            audit,
            "trace_structure",
            "requests",
            seed=seed,
        )
        capacity = _int_value(
            run_rows[first_run_id].get("capacity"),
            audit,
            "trace_structure",
            "capacity",
            seed=seed,
        )
        if request_count is None or capacity is None or request_count % 10:
            continue
        warm_count = 3 * request_count // 10
        scan_count = 4 * request_count // 10
        return_count = 3 * request_count // 10
        phases = [row.get("phase") for row in projected_rows]
        expected_phases = (
            ["warm"] * warm_count
            + ["scan"] * scan_count
            + ["return"] * return_count
        )
        if phases != expected_phases:
            audit.error(
                "trace_phase_sequence",
                "trace does not have the frozen contiguous 30/40/30 phases",
                seed=seed,
            )
        request_ids = [row.get("request_id") for row in projected_rows]
        if len(request_ids) != len(set(request_ids)):
            audit.error("trace_request_ids", "request IDs are not unique within a seed", seed=seed)
        expected_phase_counts = {
            "warm": warm_count,
            "scan": scan_count,
            "return": return_count,
        }
        if metadata.get("phase_counts") != expected_phase_counts:
            audit.error(
                "trace_metadata",
                "trace metadata phase counts differ from the frozen split",
                seed=seed,
                expected=expected_phase_counts,
                observed=metadata.get("phase_counts"),
            )
        if metadata.get("requests") != request_count or metadata.get("capacity") != capacity:
            audit.error(
                "trace_metadata",
                "trace metadata request count or capacity differs from the run",
                seed=seed,
            )
        hot_size = (4 * capacity) // 5
        hot_concepts = metadata.get("hot_concept_ids")
        scan_concepts = metadata.get("scan_concept_ids")
        hot_texts = metadata.get("hot_text_ids")
        scan_texts = metadata.get("scan_text_ids")
        if (
            not isinstance(hot_concepts, list)
            or not all(isinstance(value, str) for value in hot_concepts)
            or len(hot_concepts) != hot_size
            or len(set(hot_concepts)) != hot_size
            or metadata.get("hot_set_size") != hot_size
        ):
            audit.error("trace_hot_set", "trace metadata has the wrong hot-set structure", seed=seed)
        if (
            not isinstance(scan_concepts, list)
            or not all(isinstance(value, str) for value in scan_concepts)
            or len(scan_concepts) != scan_count
            or len(set(scan_concepts)) != scan_count
        ):
            audit.error("trace_scan_set", "trace metadata has the wrong distinct scan set", seed=seed)
        if (
            isinstance(hot_concepts, list)
            and all(isinstance(value, str) for value in hot_concepts)
            and isinstance(scan_concepts, list)
            and all(isinstance(value, str) for value in scan_concepts)
            and set(hot_concepts) & set(scan_concepts)
        ):
            audit.error("trace_set_overlap", "hot and scan concepts overlap", seed=seed)
        if (
            not isinstance(hot_texts, list)
            or not all(isinstance(value, str) for value in hot_texts)
            or len(hot_texts) != hot_size
            or len(set(hot_texts)) != hot_size
            or not isinstance(scan_texts, list)
            or not all(isinstance(value, str) for value in scan_texts)
            or len(scan_texts) != scan_count
            or len(set(scan_texts)) != scan_count
        ):
            audit.error("trace_text_sets", "trace metadata text sets have invalid cardinality", seed=seed)
        elif set(hot_texts) & set(scan_texts):
            audit.error("trace_set_overlap", "hot and scan text IDs overlap", seed=seed)

        warm_rows = projected_rows[:warm_count]
        scan_rows = projected_rows[warm_count : warm_count + scan_count]
        return_rows = projected_rows[warm_count + scan_count :]
        warm_concept_counts = Counter(row.get("concept_id") for row in warm_rows)
        return_concept_counts = Counter(row.get("concept_id") for row in return_rows)
        if warm_concept_counts != return_concept_counts:
            audit.error("trace_hot_multiplicity", "warm and return hot multiplicities differ", seed=seed)
        if (
            isinstance(hot_concepts, list)
            and all(isinstance(value, str) for value in hot_concepts)
            and set(warm_concept_counts) != set(hot_concepts)
        ):
            audit.error("trace_hot_coverage", "warm phase does not cover exactly the declared hot set", seed=seed)
        if (
            isinstance(scan_concepts, list)
            and all(isinstance(value, str) for value in scan_concepts)
            and [row.get("concept_id") for row in scan_rows]
            and set(row.get("concept_id") for row in scan_rows) != set(scan_concepts)
        ):
            audit.error("trace_scan_coverage", "scan phase differs from the declared scan set", seed=seed)
        for phase_name, phase_rows in (
            ("warm", warm_rows),
            ("scan", scan_rows),
            ("return", return_rows),
        ):
            occurrences: DefaultDict[Any, List[int]] = defaultdict(list)
            for row in phase_rows:
                occurrence = row.get("occurrence")
                if isinstance(occurrence, int) and not isinstance(occurrence, bool):
                    occurrences[row.get("concept_id")].append(occurrence)
                else:
                    audit.error(
                        "trace_occurrence",
                        "trace occurrence is not an integer",
                        seed=seed,
                        phase=phase_name,
                    )
            for concept_id, values in occurrences.items():
                if sorted(values) != list(range(len(values))):
                    audit.error(
                        "trace_occurrence",
                        "per-phase concept occurrences are not contiguous from zero",
                        seed=seed,
                        phase=phase_name,
                        concept_id=concept_id,
                    )


def _formal_protocol_checks(
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> List[str]:
    pending_reasons: List[str] = []
    config = manifest.get("config")
    if not isinstance(config, dict):
        audit.error("config", "manifest config must be an object")
        return pending_reasons
    if config.get("mode") != "full" or manifest.get("formal_claimable_mode") is not True:
        pending_reasons.append("bundle is not marked as a formal full-mode experiment")
        return pending_reasons

    declarations = manifest.get("artifacts")
    if isinstance(declarations, dict):
        for name, declaration in declarations.items():
            if not isinstance(declaration, dict):
                continue
            for field in ("sha256", "rows", "bytes"):
                if field not in declaration:
                    audit.error(
                        "formal_artifact_field",
                        "formal artifact declaration omits a required integrity field",
                        artifact=name,
                        field=field,
                    )

    required_flags = {
        "historical_gate7_preserved": True,
        "historical_artifacts_modified": False,
        "precomputed_embeddings": False,
        "embedding_in_request_path": True,
        "gptcache_adapter_path": True,
        "actual_onnx": True,
    }
    for field, expected in required_flags.items():
        if manifest.get(field) != expected:
            audit.error("formal_flag", "formal manifest flag violates the frozen protocol", field=field, expected=expected, observed=manifest.get(field))
    if config.get("fake_embedding") is not False:
        audit.error("formal_embedding", "formal experiment must explicitly disable fake embeddings")
    attempt_policy = manifest.get("attempt_policy")
    if (
        not isinstance(attempt_policy, dict)
        or attempt_policy.get("formal_root")
        != "artifacts/gate7-onnx-attempts"
        or attempt_policy.get("eligibility")
        != "first structurally valid complete attempt in the retained predecessor chain"
        or attempt_policy.get("rerun_scope")
        != "complete five-seed, three-policy matrix under a new attempt ID"
    ):
        audit.error(
            "attempt_policy",
            "formal manifest lacks the frozen attempt-retention/eligibility policy",
        )
    contract = manifest.get("contract")
    if not isinstance(contract, dict):
        audit.error(
            "contract_identity",
            "formal evidence must identify the frozen remediation contract",
        )
    else:
        if contract.get("path") != DEFAULT_CONTRACT_PATH:
            audit.error(
                "contract_identity",
                "formal evidence used a non-default remediation contract path",
                observed=contract.get("path"),
                expected=DEFAULT_CONTRACT_PATH,
            )
        if contract.get("sha256") != PINNED_CONTRACT_SHA256:
            audit.error(
                "contract_identity",
                "formal evidence contract hash differs from the frozen identity",
                observed=contract.get("sha256"),
                expected=PINNED_CONTRACT_SHA256,
            )
        if not isinstance(contract.get("bytes"), int) or contract.get("bytes") <= 0:
            audit.error(
                "contract_identity",
                "formal evidence contract identity lacks a positive byte count",
            )
    for field, expected in FROZEN_CONFIG.items():
        if config.get(field) != expected:
            audit.error("frozen_config", "formal configuration differs from the frozen contract", field=field, expected=expected, observed=config.get(field))
    declared_seeds = manifest.get("seeds")
    if not isinstance(declared_seeds, list) or tuple(declared_seeds) != FULL_SEEDS:
        pending_reasons.append("the five frozen seeds are not all declared in order")
    declared_policies = manifest.get("policies")
    if (
        not isinstance(declared_policies, list)
        or not all(isinstance(value, str) for value in declared_policies)
        or set(declared_policies) != set(POLICIES)
    ):
        pending_reasons.append("LRU, LFU, and CARMA are not all declared")
    if manifest.get("policy_order_namespace") != POLICY_ORDER_NAMESPACE:
        audit.error("policy_order_namespace", "formal policy-order namespace differs from the contract")
    policy_orders = manifest.get("policy_orders", {})
    if not isinstance(policy_orders, dict):
        audit.error("policy_orders", "formal policy_orders must be an object")
        policy_orders = {}
    for seed in FULL_SEEDS:
        if policy_orders.get(str(seed)) != list(EXPECTED_POLICY_ORDERS[seed]):
            audit.error("frozen_policy_order", "formal seed order differs from the frozen schedule", seed=seed)
    planned_policy_orders = manifest.get("planned_policy_orders", {})
    if not isinstance(planned_policy_orders, dict):
        audit.error("planned_policy_orders", "formal planned_policy_orders must be an object")
        planned_policy_orders = {}
    for seed in FULL_SEEDS:
        if planned_policy_orders.get(str(seed)) != list(EXPECTED_POLICY_ORDERS[seed]):
            audit.error(
                "frozen_planned_policy_order",
                "formal planned seed order differs from the frozen schedule",
                seed=seed,
            )
    git = manifest.get("git")
    if not isinstance(git, dict) or git.get("worktree_dirty") is not False or not git.get("head_commit"):
        audit.error("git_state", "formal evidence requires a recorded clean Git HEAD")
    elif (
        git.get("end_head_commit") != git.get("head_commit")
        or git.get("end_worktree_dirty") is not False
        or git.get("source_snapshot_unchanged") is not True
    ):
        audit.error(
            "git_state",
            "formal evidence did not preserve one clean source snapshot through completion",
        )
    model = manifest.get("model")
    if not isinstance(model, dict):
        audit.error("model", "manifest model must be an object")
    else:
        for field, expected in FROZEN_MODEL.items():
            if model.get(field) != expected:
                audit.error("frozen_model", "model identity differs from the frozen contract", field=field, expected=expected, observed=model.get(field))
    source_identities = manifest.get("source_identities")
    if not isinstance(source_identities, dict):
        audit.error("source_identities", "formal source_identities must be an object")
    else:
        for source_name in REQUIRED_SOURCE_IDENTITIES:
            identity = source_identities.get(source_name)
            if not isinstance(identity, dict):
                audit.error(
                    "source_identity_missing",
                    "formal source identity is absent",
                    source=source_name,
                )
                continue
            sha256 = identity.get("sha256")
            byte_count = identity.get("bytes")
            if (
                not isinstance(sha256, str)
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
            ):
                audit.error(
                    "source_identity_sha256",
                    "formal source identity lacks a valid SHA-256",
                    source=source_name,
                )
            if (
                not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count <= 0
            ):
                audit.error(
                    "source_identity_bytes",
                    "formal source identity lacks a positive byte count",
                    source=source_name,
                )
    onnx = manifest.get("onnx")
    onnx_expected = {
        "provider": "CPUExecutionProvider",
        "intra_op_threads": 1,
        "inter_op_threads": 1,
        "max_length": 512,
        "warmup_requests": 20,
        "model_load_and_warmup_in_request_timing": False,
        "provider_verified_each_child": True,
        "embedding_norm_verified_each_request": True,
    }
    if not isinstance(onnx, dict):
        audit.error("onnx", "manifest ONNX settings must be an object")
    else:
        for field, expected in onnx_expected.items():
            if onnx.get(field) != expected:
                audit.error("frozen_onnx", "ONNX setting differs from the frozen contract", field=field, expected=expected, observed=onnx.get(field))
    timing_scope = manifest.get("timing_scope")
    if (
        not isinstance(timing_scope, dict)
        or timing_scope.get("quantiles")
        != "nearest rank: one-based ceil(q*N) over raw samples"
        or timing_scope.get("outcome_categories") != list(OUTCOME_NAMES)
        or timing_scope.get("outcome_report_rows")
        != list(OUTCOME_REPORT_NAMES)
    ):
        audit.error(
            "timing_definition",
            "formal manifest lacks the frozen quantile/outcome definitions",
        )
    resource_measurement = manifest.get("resource_measurement")
    if (
        not isinstance(resource_measurement, dict)
        or resource_measurement.get("maximum_valid_sample_gap_ms") != 200
        or resource_measurement.get("fixed_buffers_reserved_before_start") is not True
    ):
        audit.error(
            "resource_definition",
            "formal manifest lacks the frozen sampling/buffer definitions",
        )
    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        audit.error("environment", "formal manifest environment must be an object")
    else:
        for field in ("filesystem", "power", "load_average_at_manifest"):
            if field not in environment:
                audit.error(
                    "environment",
                    "formal manifest omits a machine observation",
                    field=field,
                )
    trace_metadata = manifest.get("trace_metadata")
    if isinstance(trace_metadata, dict):
        for seed in FULL_SEEDS:
            metadata = trace_metadata.get(str(seed))
            if not isinstance(metadata, dict):
                continue
            for field, expected in FROZEN_SOURCE_HASHES.items():
                if metadata.get(field) != expected:
                    audit.error(
                        "frozen_trace_source",
                        "formal trace source identity differs from the frozen contract",
                        seed=seed,
                        field=field,
                        expected=expected,
                        observed=metadata.get(field),
                    )
            trace_hash = metadata.get("trace_sha256")
            matching_trace_artifacts = [
                name
                for name, values in artifacts.items()
                if "trace" in name.lower()
                and str(seed) in name
                and values.get("sha256") == trace_hash
            ]
            if not matching_trace_artifacts:
                audit.error(
                    "formal_trace_artifact",
                    "formal bundle does not retain a per-seed trace artifact matching its trace hash",
                    seed=seed,
                )
            expected_trace_name = "traces/seed-%d.jsonl" % seed
            if expected_trace_name not in artifacts:
                audit.error(
                    "formal_trace_artifact",
                    "formal bundle uses the wrong retained trace filename",
                    seed=seed,
                    expected=expected_trace_name,
                )
    if manifest.get("seeds") == list(PROTOCOL_FULL_SEEDS):
        project_root = Path(__file__).resolve().parents[2]
        local_contract = project_root / DEFAULT_CONTRACT_PATH
        if (
            not local_contract.is_file()
            or _sha256_file(local_contract) != PINNED_CONTRACT_SHA256
        ):
            audit.error(
                "contract_identity",
                "executing auditor does not have the pinned contract bytes",
            )
        if isinstance(git, dict) and isinstance(source_identities, dict):
            head_commit = git.get("head_commit")
            identities_to_verify = dict(source_identities)
            identities_to_verify[DEFAULT_CONTRACT_PATH] = contract
            for relative, identity in identities_to_verify.items():
                if not isinstance(identity, dict) or not isinstance(head_commit, str):
                    continue
                try:
                    blob = subprocess.check_output(
                        ["git", "show", "%s:%s" % (head_commit, relative)],
                        cwd=str(project_root),
                        stderr=subprocess.DEVNULL,
                    )
                except (OSError, subprocess.CalledProcessError):
                    audit.error(
                        "git_source_binding",
                        "recorded source cannot be loaded from the recorded Git HEAD",
                        source=relative,
                    )
                    continue
                if (
                    hashlib.sha256(blob).hexdigest() != identity.get("sha256")
                    or len(blob) != identity.get("bytes")
                ):
                    audit.error(
                        "git_source_binding",
                        "recorded source identity differs from the recorded Git blob",
                        source=relative,
                    )
    return pending_reasons


def adjudicate_seed_pair(
    seed: int,
    carma: Mapping[str, Any],
    lru: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply all five frozen bounds to one recomputed CARMA/LRU pair."""

    carma_p95 = int(carma["p95_request_total_ns"])
    lru_p95 = int(lru["p95_request_total_ns"])
    carma_qps = float(carma["throughput_qps_unrounded"])
    lru_qps = float(lru["throughput_qps_unrounded"])
    carma_rss = int(carma["rss_peak_bytes"])
    lru_rss = int(lru["rss_peak_bytes"])
    if lru_p95 <= 0 or lru_qps <= 0 or lru_rss <= 0:
        raise ValueError("LRU denominators must be positive")
    values = {
        "p95_ratio": carma_p95 / lru_p95,
        "p95_delta_ns": carma_p95 - lru_p95,
        "throughput_ratio": carma_qps / lru_qps,
        "rss_ratio": carma_rss / lru_rss,
        "rss_delta_bytes": carma_rss - lru_rss,
    }
    checks = {
        "p95_ratio": values["p95_ratio"] <= BOUNDS["p95_ratio_max"],
        "p95_delta": values["p95_delta_ns"] <= BOUNDS["p95_delta_ns_max"],
        "throughput_ratio": values["throughput_ratio"] >= BOUNDS["throughput_ratio_min"],
        "rss_ratio": values["rss_ratio"] <= BOUNDS["rss_ratio_max"],
        "rss_delta": values["rss_delta_bytes"] <= BOUNDS["rss_delta_bytes_max"],
    }
    return {
        "seed": seed,
        "carma": dict(carma),
        "lru": dict(lru),
        "paired_values": values,
        "checks": checks,
        "passes": all(checks.values()),
    }


def _seed_adjudications(
    by_seed: Mapping[int, Mapping[str, str]],
    request_results: Mapping[str, Mapping[str, Any]],
    resource_results: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for seed, runs in sorted(by_seed.items()):
        if "CARMA" not in runs or "LRU" not in runs:
            continue
        pair: Dict[str, Dict[str, Any]] = {}
        for policy in ("CARMA", "LRU"):
            run_id = runs[policy]
            request = request_results.get(run_id)
            resource = resource_results.get(run_id)
            if request is None or resource is None or "end_to_end" not in request.get("latency", {}):
                break
            pair[policy] = {
                "run_id": run_id,
                "trace_hash": request["trace_hash"],
                "p95_request_total_ns": request["latency"]["end_to_end"]["p95_ns"],
                "throughput_qps_unrounded": request["throughput_qps_unrounded"],
                "rss_peak_bytes": resource["rss_peak_bytes"],
            }
        if len(pair) != 2:
            continue
        try:
            results[str(seed)] = adjudicate_seed_pair(seed, pair["CARMA"], pair["LRU"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            audit.error("adjudication_input", "cannot adjudicate a paired seed", seed=seed, detail=str(exc))
    return results


def _validate_attempt_identity(
    manifest: Mapping[str, Any],
    run_rows: Mapping[str, Mapping[str, Any]],
    request_rows: Sequence[Mapping[str, Any]],
    resource_rows: Sequence[Mapping[str, Any]],
    outcome_rows: Sequence[Mapping[str, Any]],
    audit: _Audit,
) -> Optional[str]:
    manifest_attempt = manifest.get("attempt_id")
    if not isinstance(manifest_attempt, str) or not manifest_attempt.strip():
        audit.error("attempt_id", "manifest attempt_id must be a nonempty string")
        manifest_attempt = None
    observed: DefaultDict[str, List[str]] = defaultdict(list)
    if manifest_attempt is not None:
        observed[manifest_attempt].append("manifest")
    collections: Tuple[Tuple[str, Iterable[Mapping[str, Any]]], ...] = (
        ("runs.csv", run_rows.values()),
        ("requests.jsonl", request_rows),
        ("resources.jsonl", resource_rows),
        ("outcome-latency.jsonl", outcome_rows),
    )
    for source_name, rows in collections:
        for row_number, row in enumerate(rows, start=1):
            value = row.get("attempt_id")
            if not isinstance(value, str) or not value.strip():
                audit.error(
                    "attempt_id",
                    "evidence row has no nonempty attempt_id",
                    source=source_name,
                    row=row_number,
                )
                continue
            observed[value].append("%s:%d" % (source_name, row_number))
            if manifest_attempt is not None and value != manifest_attempt:
                audit.error(
                    "attempt_id_mismatch",
                    "evidence row belongs to a different attempt",
                    source=source_name,
                    row=row_number,
                    manifest_attempt_id=manifest_attempt,
                    observed_attempt_id=value,
                )
    if len(observed) > 1:
        audit.error(
            "attempt_id_mismatch",
            "bundle contains more than one attempt identity",
            attempt_ids=sorted(observed),
        )
    return manifest_attempt


def _validate_prior_attempt_eligibility(
    bundle: Path,
    manifest: Mapping[str, Any],
    audit: _Audit,
    enforce_eligibility: bool,
    preterminal: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    prior_attempts = manifest.get("prior_attempts")
    prior_flag = manifest.get("prior_attempt_exists")
    result: Dict[str, Any] = {
        "declared_prior_attempts": 0,
        "valid_complete_predecessors": [],
        "eligible": True,
    }
    ledger_state = _validate_attempt_ledger(
        bundle, manifest, audit, preterminal
    )
    if not isinstance(prior_attempts, list):
        audit.error(
            "prior_attempt_chain",
            "formal manifest prior_attempts must be a list",
        )
        return result, ledger_state
    result["declared_prior_attempts"] = len(prior_attempts)
    if prior_flag is not bool(prior_attempts):
        audit.error(
            "prior_attempt_chain",
            "prior_attempt_exists disagrees with the predecessor list",
        )
    manifest_attempt_id = manifest.get("attempt_id")
    manifest_started_at = _parse_attempt_timestamp(
        manifest.get("started_at_utc"), audit, "current"
    )
    _validate_attempt_time_identity(
        manifest, manifest_started_at, audit, "current"
    )
    attempt_policy = manifest.get("attempt_policy")
    if (
        not isinstance(attempt_policy, dict)
        or attempt_policy.get("directory") != bundle.name
    ):
        audit.error(
            "prior_attempt_directory",
            "manifest attempt directory differs from the audited bundle directory",
            declared=(
                attempt_policy.get("directory")
                if isinstance(attempt_policy, dict)
                else None
            ),
            observed=bundle.name,
        )

    current_start = ledger_state.get("current_start")
    current_sequence = (
        current_start.get("sequence")
        if isinstance(current_start, dict)
        else None
    )
    prior_ledger_attempts = [
        attempt
        for attempt in ledger_state.get("attempts", [])
        if isinstance(current_sequence, int)
        and int(attempt.get("terminal_row", 0)) < current_sequence
    ]
    discovered_prior_attempts: List[Dict[str, Any]] = []
    for attempt in prior_ledger_attempts:
        start = attempt.get("start")
        terminal = attempt.get("terminal")
        record = attempt.get("record")
        if not all(isinstance(value, dict) for value in (start, terminal, record)):
            continue
        record_name = terminal.get("terminal_record")
        discovered_prior_attempts.append(
            {
                "attempt_id": start.get("attempt_id"),
                "kind": record.get("kind"),
                "status": (
                    "producer_complete"
                    if record_name == "manifest.json"
                    else "invalid_operational_failure"
                ),
                "directory": start.get("directory"),
                "record": record_name,
                "record_sha256": terminal.get("terminal_record_sha256"),
                "record_bytes": terminal.get("terminal_record_bytes"),
                "started_at_utc": start.get("started_at_utc"),
            }
        )
    result["discovered_prior_attempts"] = len(discovered_prior_attempts)
    if prior_attempts != discovered_prior_attempts:
        audit.error(
            "prior_attempt_chain",
            "declared predecessor chain differs from retained earlier sibling attempts",
            declared=prior_attempts,
            discovered=discovered_prior_attempts,
        )

    # Eligibility is historical data, not a new interpretation by whatever
    # auditor happens to execute today.  Only the immutable report status bound
    # by that predecessor's TERMINAL event can block a later attempt.
    for attempt in prior_ledger_attempts:
        start = attempt.get("start")
        terminal = attempt.get("terminal")
        if not isinstance(start, dict) or not isinstance(terminal, dict):
            continue
        predecessor_status = terminal.get("preterminal_report_status")
        if (
            terminal.get("terminal_record") == "manifest.json"
            and predecessor_status in ("pass", "fail")
        ):
            result["valid_complete_predecessors"].append(
                {
                    "attempt_id": start.get("attempt_id"),
                    "status": predecessor_status,
                    "directory": start.get("directory"),
                }
            )
    if result["valid_complete_predecessors"]:
        result["eligible"] = False
        if enforce_eligibility:
            audit.error(
                "attempt_ineligible",
                "a TERMINAL-bound pass/fail predecessor is already eligible",
                predecessors=result["valid_complete_predecessors"],
            )
    return result, ledger_state


def _parse_attempt_timestamp(
    value: Any, audit: _Audit, position: Any
) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        audit.error(
            "prior_attempt_timestamp",
            "formal attempt has no parseable UTC start timestamp",
            position=position,
        )
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        audit.error(
            "prior_attempt_timestamp",
            "formal attempt has no parseable UTC start timestamp",
            position=position,
            observed=value,
        )
        return None
    return parsed.astimezone(timezone.utc)


def _validate_attempt_time_identity(
    record: Mapping[str, Any],
    started_at: Optional[datetime],
    audit: _Audit,
    position: Any,
) -> None:
    """Bind chronology to the producer's timestamp-and-Git attempt identity."""

    git = record.get("git")
    head_commit = git.get("head_commit") if isinstance(git, dict) else None
    if (
        started_at is None
        or not isinstance(head_commit, str)
        or len(head_commit) != 40
        or any(character not in "0123456789abcdef" for character in head_commit)
    ):
        audit.error(
            "prior_attempt_identity",
            "formal attempt cannot bind its start time to a full Git commit",
            position=position,
        )
        return
    expected = "%s-%s" % (
        started_at.strftime("%Y%m%dT%H%M%S%fZ"),
        head_commit[:12],
    )
    if record.get("attempt_id") != expected:
        audit.error(
            "prior_attempt_identity",
            "attempt ID does not match its UTC start time and Git commit",
            position=position,
            observed=record.get("attempt_id"),
            expected=expected,
        )


def _attempts_sha256(attempts: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(attempt) for attempt in attempts],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_attempt_ledger(
    bundle: Path,
    manifest: Mapping[str, Any],
    audit: _Audit,
    preterminal: bool,
) -> Dict[str, Any]:
    """Validate the two-phase START/TERMINAL ledger and bound bytes."""

    state: Dict[str, Any] = {
        "entries": [],
        "attempts": [],
        "current_start": None,
        "current_terminal": None,
        "current_terminal_expectation": None,
    }
    declaration = manifest.get("attempt_ledger")
    if not isinstance(declaration, dict):
        audit.error(
            "attempt_ledger",
            "formal manifest omits the root attempt-ledger START prefix",
        )
        return state
    if declaration.get("path") != "attempt-ledger.jsonl":
        audit.error("attempt_ledger", "formal manifest names an unexpected ledger")
    if declaration.get("event") != "START":
        audit.error(
            "attempt_ledger",
            "formal manifest must identify its START event",
        )
    ledger_path = bundle.parent / "attempt-ledger.jsonl"
    if not ledger_path.is_file():
        audit.error("attempt_ledger", "formal attempt ledger is missing")
        return state

    raw_lines = ledger_path.read_bytes().splitlines(keepends=True)
    entries: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []
    previous_hash: Optional[str] = None
    seen_attempts: set = set()
    seen_directories: set = set()
    open_start: Optional[Dict[str, Any]] = None
    for row, raw_line in enumerate(raw_lines, start=1):
        try:
            entry = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            audit.error("attempt_ledger", "ledger is not valid JSONL", row=row)
            entries.append({})
            continue
        if not isinstance(entry, dict):
            audit.error("attempt_ledger", "ledger row is not an object", row=row)
            entries.append({})
            continue
        canonical = (
            json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if canonical != raw_line:
            audit.error("attempt_ledger", "ledger row is not canonical", row=row)
        entry_hash = entry.get("entry_sha256")
        unhashed = dict(entry)
        unhashed.pop("entry_sha256", None)
        computed_hash = hashlib.sha256(
            json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        started_at = _parse_attempt_timestamp(
            entry.get("started_at_utc"), audit, "ledger:%d" % row
        )
        head_commit = entry.get("head_commit")
        expected_attempt_id = (
            "%s-%s"
            % (
                started_at.strftime("%Y%m%dT%H%M%S%fZ"),
                head_commit[:12],
            )
            if started_at is not None
            and isinstance(head_commit, str)
            and len(head_commit) == 40
            and all(character in "0123456789abcdef" for character in head_commit)
            else None
        )
        event = entry.get("event")
        attempt_id = entry.get("attempt_id")
        directory = entry.get("directory")
        if (
            entry.get("schema_version") != ATTEMPT_LEDGER_SCHEMA_VERSION
            or entry.get("experiment_id") != EXPERIMENT_ID
            or entry.get("sequence") != row
            or entry.get("previous_entry_sha256") != previous_hash
            or entry_hash != computed_hash
            or event not in ("START", "TERMINAL")
            or expected_attempt_id is None
            or attempt_id != expected_attempt_id
            or not isinstance(directory, str)
            or not directory
            or Path(directory).name != directory
        ):
            audit.error(
                "attempt_ledger",
                "ledger event hash chain or identity is invalid",
                row=row,
            )

        if event == "START":
            if open_start is not None:
                audit.error(
                    "attempt_ledger_unresolved_start",
                    "a START appears before the prior START has a TERMINAL",
                    row=row,
                )
            if attempt_id in seen_attempts or directory in seen_directories:
                audit.error(
                    "attempt_ledger",
                    "START reuses an attempt ID or directory",
                    row=row,
                )
            if (
                entry.get("prior_attempt_count") != len(attempts)
                or not isinstance(entry.get("prior_attempts_sha256"), str)
            ):
                audit.error(
                    "attempt_ledger",
                    "START predecessor count/hash is inconsistent",
                    row=row,
                )
            if isinstance(attempt_id, str):
                seen_attempts.add(attempt_id)
            if isinstance(directory, str):
                seen_directories.add(directory)
            open_start = entry
        elif event == "TERMINAL":
            if open_start is None:
                audit.error(
                    "attempt_ledger_terminal",
                    "TERMINAL has no immediately preceding START",
                    row=row,
                )
            else:
                if any(
                    entry.get(field) != open_start.get(field)
                    for field in (
                        "attempt_id",
                        "started_at_utc",
                        "head_commit",
                        "directory",
                    )
                ):
                    audit.error(
                        "attempt_ledger_terminal",
                        "TERMINAL identity differs from its START",
                        row=row,
                    )
                attempts.append(
                    {
                        "start": open_start,
                        "terminal": entry,
                        "start_row": row - 1,
                        "terminal_row": row,
                    }
                )
            open_start = None
        if isinstance(entry_hash, str):
            previous_hash = entry_hash
        entries.append(entry)

    current_id = manifest.get("attempt_id")
    current_open = (
        open_start is not None
        and open_start.get("attempt_id") == current_id
        and open_start.get("directory") == bundle.name
    )
    if open_start is not None and not (preterminal and current_open):
        audit.error(
            "attempt_ledger_unresolved_start",
            "ledger retains a START without a TERMINAL",
            attempt_id=open_start.get("attempt_id"),
        )

    # Every TERMINAL content-addresses the immutable terminal record.  A
    # successful producer record also binds the preterminal adjudication.
    for attempt in attempts:
        start = attempt["start"]
        terminal = attempt["terminal"]
        directory = str(start.get("directory"))
        attempt_dir = bundle.parent / directory
        record_name = terminal.get("terminal_record")
        if record_name not in TERMINAL_RECORD_NAMES:
            audit.error(
                "attempt_ledger_terminal",
                "TERMINAL names an unsupported terminal record",
                row=attempt["terminal_row"],
            )
            continue
        record_path = attempt_dir / str(record_name)
        retained_terminal_names = [
            name
            for name in TERMINAL_RECORD_NAMES
            if (attempt_dir / name).is_file()
        ]
        if retained_terminal_names != [record_name]:
            audit.error(
                "attempt_ledger_terminal",
                "attempt directory does not retain exactly its bound terminal record",
                row=attempt["terminal_row"],
                retained=retained_terminal_names,
            )
        if not record_path.is_file():
            audit.error(
                "attempt_ledger_terminal",
                "TERMINAL-bound record is missing",
                path=str(record_path),
            )
            continue
        if (
            terminal.get("terminal_record_sha256") != _sha256_file(record_path)
            or terminal.get("terminal_record_bytes")
            != int(record_path.stat().st_size)
        ):
            audit.error(
                "attempt_ledger_terminal",
                "retained terminal bytes differ from the TERMINAL binding",
                row=attempt["terminal_row"],
            )
        record = _load_json(record_path, audit, "attempt_terminal_record")
        attempt["record"] = record
        if record is None:
            continue
        record_git = record.get("git")
        record_prior = record.get("prior_attempts")
        record_declaration = record.get("attempt_ledger")
        record_config = record.get("config")
        start_row = int(attempt["start_row"])
        start_prefix = b"".join(raw_lines[:start_row])
        if (
            record.get("experiment_id") != EXPERIMENT_ID
            or not isinstance(record_config, dict)
            or record_config.get("mode") != "full"
            or record.get("attempt_id") != start.get("attempt_id")
            or record.get("started_at_utc") != start.get("started_at_utc")
            or not isinstance(record_git, dict)
            or record_git.get("head_commit") != start.get("head_commit")
            or not isinstance(record_prior, list)
            or start.get("prior_attempt_count") != len(record_prior)
            or start.get("prior_attempts_sha256")
            != _attempts_sha256(record_prior)
            or not isinstance(record_declaration, dict)
            or record_declaration.get("path") != "attempt-ledger.jsonl"
            or record_declaration.get("prefix_rows") != start_row
            or record_declaration.get("prefix_sha256")
            != hashlib.sha256(start_prefix).hexdigest()
            or record_declaration.get("prefix_bytes") != len(start_prefix)
            or record_declaration.get("entry_sha256")
            != start.get("entry_sha256")
        ):
            audit.error(
                "attempt_ledger_terminal",
                "terminal record differs from its START registration",
                row=attempt["terminal_row"],
            )

        if record_name == "manifest.json" and (
            record.get("kind") != "prospective_gate7_followup"
            or record.get("formal_claimable_mode") is not True
        ):
            audit.error(
                "attempt_ledger_terminal",
                "manifest TERMINAL is not a formal completed producer record",
                row=attempt["terminal_row"],
            )
        if record_name == "attempt-failure.json" and (
            record.get("kind") != "prospective_gate7_failed_attempt"
            or record.get("status") != "invalid"
        ):
            audit.error(
                "attempt_ledger_terminal",
                "failure TERMINAL is not a retained invalid operational record",
                row=attempt["terminal_row"],
            )

        if record_name == "manifest.json":
            report_name = terminal.get("preterminal_report")
            report_path = attempt_dir / str(report_name)
            if report_name != PRETERMINAL_REPORT_NAME or not report_path.is_file():
                audit.error(
                    "attempt_ledger_preterminal",
                    "manifest TERMINAL lacks the immutable preterminal report",
                    row=attempt["terminal_row"],
                )
                continue
            if (
                terminal.get("preterminal_report_sha256")
                != _sha256_file(report_path)
                or terminal.get("preterminal_report_bytes")
                != int(report_path.stat().st_size)
            ):
                audit.error(
                    "attempt_ledger_preterminal",
                    "preterminal bytes differ from their TERMINAL binding",
                    row=attempt["terminal_row"],
                )
            report = _load_json(
                report_path, audit, "attempt_preterminal_report"
            )
            attempt["preterminal_report"] = report
            report_status = terminal.get("preterminal_report_status")
            report_claimable = terminal.get("preterminal_report_claimable")
            auditor_identity = terminal.get("auditor_identity")
            source_identities = record.get("source_identities")
            frozen_auditor = (
                source_identities.get("benchmarks/carma/gate7_audit.py")
                if isinstance(source_identities, dict)
                else None
            )
            auditor_identity_valid = (
                isinstance(auditor_identity, dict)
                and auditor_identity.get("path")
                == "benchmarks/carma/gate7_audit.py"
                and isinstance(auditor_identity.get("sha256"), str)
                and len(auditor_identity["sha256"]) == 64
                and all(
                    character in "0123456789abcdef"
                    for character in auditor_identity["sha256"]
                )
                and isinstance(auditor_identity.get("bytes"), int)
                and not isinstance(auditor_identity.get("bytes"), bool)
                and auditor_identity["bytes"] > 0
            )
            if (
                report is None
                or report.get("schema_version") != AUDIT_SCHEMA_VERSION
                or report.get("audit_phase") != "preterminal"
                or report.get("attempt_id") != start.get("attempt_id")
                or report_status not in ADJUDICATION_STATUSES
                or report.get("status") != report_status
                or not isinstance(report_claimable, bool)
                or report_claimable != (report_status in ("pass", "fail"))
                or report.get("claimable") is not report_claimable
                or not auditor_identity_valid
                or report.get("auditor_identity") != auditor_identity
                or frozen_auditor != auditor_identity
            ):
                audit.error(
                    "attempt_ledger_preterminal",
                    "bound status, claimability, or auditor identity is inconsistent",
                    row=attempt["terminal_row"],
                )
        elif (
            terminal.get("preterminal_report") is not None
            or terminal.get("preterminal_report_sha256") is not None
            or terminal.get("preterminal_report_bytes") is not None
            or terminal.get("preterminal_report_status") != "invalid"
            or terminal.get("preterminal_report_claimable") is not False
            or terminal.get("auditor_identity") is not None
        ):
            audit.error(
                "attempt_ledger_terminal",
                "failure TERMINAL has an invalid preterminal binding",
                row=attempt["terminal_row"],
            )

    matching_starts = [
        entry
        for entry in entries
        if entry.get("event") == "START"
        and entry.get("attempt_id") == current_id
        and entry.get("directory") == bundle.name
    ]
    if len(matching_starts) != 1:
        audit.error(
            "attempt_ledger",
            "audited manifest lacks exactly one matching START",
            matches=len(matching_starts),
        )
    else:
        current_start = matching_starts[0]
        state["current_start"] = current_start
        current_start_row = entries.index(current_start) + 1
        prior_attempts = manifest.get("prior_attempts")
        git = manifest.get("git")
        expected = {
            "attempt_id": current_id,
            "started_at_utc": manifest.get("started_at_utc"),
            "head_commit": git.get("head_commit") if isinstance(git, dict) else None,
            "directory": bundle.name,
            "prior_attempt_count": (
                len(prior_attempts) if isinstance(prior_attempts, list) else None
            ),
            "prior_attempts_sha256": (
                _attempts_sha256(prior_attempts)
                if isinstance(prior_attempts, list)
                else None
            ),
        }
        if any(current_start.get(key) != value for key, value in expected.items()):
            audit.error(
                "attempt_ledger",
                "current START differs from the audited manifest",
            )
        prefix = b"".join(raw_lines[:current_start_row])
        if (
            declaration.get("prefix_rows") != current_start_row
            or declaration.get("prefix_sha256")
            != hashlib.sha256(prefix).hexdigest()
            or declaration.get("prefix_bytes") != len(prefix)
            or declaration.get("entry_sha256")
            != current_start.get("entry_sha256")
        ):
            audit.error(
                "attempt_ledger",
                "manifest names the wrong START prefix",
            )

    current_attempt = next(
        (
            attempt
            for attempt in attempts
            if attempt["start"].get("attempt_id") == current_id
            and attempt["start"].get("directory") == bundle.name
        ),
        None,
    )
    if current_attempt is not None:
        state["current_terminal"] = current_attempt.get("terminal")
        if current_attempt["terminal"].get("terminal_record") != "manifest.json":
            audit.error(
                "attempt_ledger_terminal",
                "audited completed manifest is not the current TERMINAL record",
            )
        report = current_attempt.get("preterminal_report")
        if isinstance(report, dict):
            state["current_terminal_expectation"] = {
                "status": report.get("status"),
                "claimable": report.get("claimable"),
            }
    if preterminal and current_attempt is not None:
        audit.error(
            "attempt_ledger_preterminal",
            "preterminal audit requires current START without TERMINAL",
        )
    if not preterminal and current_attempt is None:
        audit.error(
            "attempt_ledger_terminal",
            "default formal audit requires a current TERMINAL",
        )

    registered_directories = {
        str(entry.get("directory"))
        for entry in entries
        if entry.get("event") == "START"
        and isinstance(entry.get("directory"), str)
    }
    for sibling in bundle.parent.iterdir():
        if sibling.is_dir() and any(sibling.iterdir()):
            if sibling.name not in registered_directories:
                audit.error(
                    "attempt_ledger",
                    "formal root contains an unregistered nonempty directory",
                    directory=sibling.name,
                )

    state["entries"] = entries
    state["attempts"] = attempts
    return state


def analyze_bundle(
    bundle_dir: Path,
    enforce_prior_attempts: bool = True,
    preterminal: bool = False,
) -> Dict[str, Any]:
    """Audit and adjudicate one retained Gate 7 evidence bundle."""

    bundle = Path(bundle_dir).resolve()
    audit = _Audit()
    manifest_path = bundle / "manifest.json"
    if not bundle.is_dir():
        audit.error("bundle_missing", "bundle directory does not exist", path=str(bundle))
        manifest: Dict[str, Any] = {}
    elif not manifest_path.is_file():
        audit.error("manifest_missing", "bundle has no manifest.json", path=str(manifest_path))
        manifest = {}
    else:
        manifest = _load_json(manifest_path, audit, "manifest_read") or {}

    auditor_identity = _auditor_identity()
    if manifest:
        if manifest.get("schema_version") != SCHEMA_VERSION:
            audit.error("manifest_schema", "manifest has the wrong schema version", observed=manifest.get("schema_version"))
        if manifest.get("kind") != "prospective_gate7_followup":
            audit.error("manifest_kind", "manifest is not the prospective Gate 7 follow-up")
        if manifest.get("experiment_id") != EXPERIMENT_ID:
            audit.error(
                "manifest_experiment",
                "manifest has the wrong prospective experiment identity",
                observed=manifest.get("experiment_id"),
            )
        if manifest.get("formal_claimable_mode") is True:
            source_identities = manifest.get("source_identities")
            producer_auditor = (
                source_identities.get(auditor_identity["path"])
                if isinstance(source_identities, dict)
                else None
            )
            if not isinstance(producer_auditor, dict) or any(
                producer_auditor.get(field) != auditor_identity[field]
                for field in ("sha256", "bytes")
            ):
                audit.error(
                    "auditor_identity_mismatch",
                    "executing auditor differs from the verifier frozen by the producer",
                    producer=producer_auditor,
                    executing=auditor_identity,
                )
        elif preterminal:
            audit.error(
                "preterminal_mode",
                "preterminal adjudication is reserved for formal bundles",
            )
    artifacts = _verify_artifacts(bundle, manifest, audit) if manifest else {}
    retained_traces = (
        _load_retained_traces(bundle, manifest, artifacts, audit)
        if manifest
        else {}
    )
    if manifest:
        _validate_trace_selection_from_source(manifest, retained_traces, audit)

    runs_path = bundle / str(artifacts.get("runs.csv", {}).get("path", "runs.csv"))
    requests_path = bundle / str(artifacts.get("requests.jsonl", {}).get("path", "requests.jsonl"))
    resources_path = bundle / str(artifacts.get("resources.jsonl", {}).get("path", "resources.jsonl"))
    outcomes_path = bundle / str(
        artifacts.get("outcome-latency.jsonl", {}).get(
            "path", "outcome-latency.jsonl"
        )
    )
    run_list = _load_csv(runs_path, audit) if runs_path.is_file() else []
    request_list = _load_jsonl(requests_path, audit, "requests_jsonl") if requests_path.is_file() else []
    resource_list = _load_jsonl(resources_path, audit, "resources_jsonl") if resources_path.is_file() else []
    outcome_list = (
        _load_jsonl(outcomes_path, audit, "outcome_latency_jsonl")
        if outcomes_path.is_file()
        else []
    )

    run_rows: Dict[str, Dict[str, str]] = {}
    for row_number, row in enumerate(run_list, start=1):
        run_id = row.get("run_id")
        if not run_id:
            audit.error("run_id", "runs.csv row lacks a run ID", row=row_number)
        elif run_id in run_rows:
            audit.error("run_id", "runs.csv contains duplicate run IDs", run_id=run_id)
        else:
            run_rows[run_id] = row

    attempt_id = _validate_attempt_identity(
        manifest, run_rows, request_list, resource_list, outcome_list, audit
    )
    if manifest.get("formal_claimable_mode") is True:
        attempt_eligibility, ledger_state = _validate_prior_attempt_eligibility(
            bundle,
            manifest,
            audit,
            enforce_prior_attempts,
            preterminal,
        )
    else:
        attempt_eligibility = {
            "declared_prior_attempts": 0,
            "valid_complete_predecessors": [],
            "eligible": True,
        }
        ledger_state = {
            "current_terminal_expectation": None,
            "entries": [],
            "attempts": [],
        }

    by_seed = _validate_run_structure(run_rows, audit)
    request_results, grouped_requests = _recompute_requests(request_list, run_rows, audit)
    raw_config = manifest.get("config") if isinstance(manifest, dict) else None
    raw_interval = (
        raw_config.get("resource_interval_ms")
        if isinstance(raw_config, dict)
        else None
    )
    resource_interval_ms = _int_value(
        raw_interval,
        audit,
        "resource_interval",
        "resource_interval_ms",
    )
    if resource_interval_ms is None or resource_interval_ms <= 0:
        resource_interval_ms = 100
    resource_results = _recompute_resources(
        resource_list, run_rows, audit, resource_interval_ms
    )
    outcome_results = _recompute_outcome_latency(
        outcome_list, grouped_requests, run_rows, audit
    )
    _validate_pairing_and_trace(
        manifest,
        by_seed,
        run_rows,
        grouped_requests,
        retained_traces,
        audit,
    )
    pending_reasons = _formal_protocol_checks(manifest, artifacts, audit) if manifest else []
    seed_results = _seed_adjudications(by_seed, request_results, resource_results, audit)

    complete_blocks = all(
        seed in by_seed and set(by_seed[seed]) == set(POLICIES)
        for seed in FULL_SEEDS
    ) and set(by_seed) == set(FULL_SEEDS)
    if not complete_blocks:
        pending_reasons.append("fewer than five complete frozen three-policy seed blocks are present")
    if set(int(seed) for seed in seed_results) != set(FULL_SEEDS):
        pending_reasons.append("fewer than five CARMA/LRU seed pairs are reproducibly adjudicable")

    if audit.errors:
        status = "invalid"
        rationale = "retained evidence failed integrity or structural validation"
    elif pending_reasons:
        status = "pending"
        rationale = "valid evidence is developmental or incomplete for the frozen claim"
    elif all(result["passes"] for result in seed_results.values()):
        status = "pass"
        rationale = "all five structurally valid seed pairs satisfy all five frozen bounds"
    else:
        status = "fail"
        rationale = "at least one structurally valid seed pair violates a frozen numerical bound"

    terminal_expectation = ledger_state.get("current_terminal_expectation")
    if (
        manifest.get("formal_claimable_mode") is True
        and not preterminal
        and isinstance(terminal_expectation, dict)
        and (
            terminal_expectation.get("status") != status
            or terminal_expectation.get("claimable")
            is not (status in ("pass", "fail"))
        )
    ):
        audit.error(
            "attempt_ledger_preterminal",
            "fresh terminal audit disagrees with the bound preterminal decision",
            bound_status=terminal_expectation.get("status"),
            recomputed_status=status,
        )
        status = "invalid"
        rationale = "retained evidence disagrees with its TERMINAL-bound preterminal adjudication"

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_phase": "preterminal" if preterminal else "terminal",
        "bundle": str(bundle),
        "auditor_identity": auditor_identity,
        "attempt_id": attempt_id,
        "attempt_eligibility": attempt_eligibility,
        "status": status,
        "claimable": status in ("pass", "fail"),
        "rationale": rationale,
        "error_count": len(audit.errors),
        "warning_count": len(audit.warnings),
        "errors": audit.errors,
        "warnings": audit.warnings,
        "pending_reasons": list(dict.fromkeys(pending_reasons)),
        "artifact_recomputation": artifacts,
        "run_recomputation": request_results,
        "resource_recomputation": resource_results,
        "outcome_latency_recomputation": outcome_results,
        "seed_adjudication": seed_results,
        "frozen_bounds": dict(BOUNDS),
        "completeness": {
            "expected_seeds": list(FULL_SEEDS),
            "expected_policies": list(POLICIES),
            "observed_seed_policies": {
                str(seed): sorted(policies) for seed, policies in sorted(by_seed.items())
            },
            "complete_five_seed_blocks": complete_blocks,
        },
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    """Create a report once; a later invocation must never replace its bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(
            str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444
        )
    except FileExistsError as exc:
        raise FileExistsError(
            "immutable preterminal report already exists: %s" % path
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(path, 0o444)
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a Gate 7 ONNX evidence bundle.")
    parser.add_argument("bundle", type=Path, help="bundle directory containing manifest.json")
    parser.add_argument(
        "--preterminal",
        action="store_true",
        help=(
            "audit a completed formal manifest before TERMINAL registration and "
            "create its immutable gate7-preterminal-adjudication.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output path (default: BUNDLE/gate7-adjudication.json)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = analyze_bundle(args.bundle, preterminal=args.preterminal)
    if args.preterminal:
        expected = (args.bundle / PRETERMINAL_REPORT_NAME).resolve()
        output = Path(args.output).resolve() if args.output else expected
        if output != expected:
            raise SystemExit(
                "--preterminal output must be %s" % expected
            )
        _write_immutable_json(output, report)
    else:
        output = args.output or (args.bundle / "gate7-adjudication.json")
        _atomic_write_json(Path(output), report)
    print("Gate 7 ONNX follow-up: %s" % report["status"].upper())
    print("Adjudication: %s" % Path(output).resolve())
    return {"pass": 0, "fail": 1, "pending": 2, "invalid": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "BOUNDS",
    "FULL_SEEDS",
    "adjudicate_seed_pair",
    "analyze_bundle",
    "nearest_rank",
]
