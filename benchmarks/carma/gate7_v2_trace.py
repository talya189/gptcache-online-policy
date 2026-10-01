"""Gate 7 v2 real-text traces and calibration-only semantic evidence.

Version 2 deliberately preserves the v1 candidate and phase namespaces. The
same seed therefore selects the same texts in the same order as v1. Only the
trace schema and response payload change. Positive QQP components establish
valid matches, retained negative labels establish direct or component-derived
negative evidence, and all other cross-component relationships are unlabeled.

Held-out rows are skipped immediately after reading ``split``. Their other
fields are never validated, dereferenced, selected, or included in the
semantic-index hash.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)


SCHEMA_VERSION = "carma-gate7-real-text-trace-v2"
SEMANTIC_INDEX_SCHEMA_VERSION = "carma-gate7-semantic-index-v2"
SEMANTIC_ARTIFACT_SCHEMA_VERSION = "carma-gate7-semantic-index-artifact-v2"

PHASE_WARM = "warm"
PHASE_UNIQUE = "scan"
PHASE_RETURN = "return"

# Intentionally unchanged from v1 so v2 cannot select a friendlier workload.
CANDIDATE_NAMESPACE = "gate7b-qqp-candidate-v1"
PHASE_NAMESPACE = "gate7b-qqp-phase-v1"

RELATION_NOT_APPLICABLE = "not_applicable"
RELATION_POSITIVE_SAME_COMPONENT = "positive_same_component"
RELATION_NEGATIVE_DIRECT = "negative_direct"
RELATION_NEGATIVE_COMPONENT_DERIVED = "negative_component_derived"
RELATION_UNLABELED_CROSS_COMPONENT = "unlabeled_cross_component"

STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_VALID = "valid"
STATUS_INVALID = "invalid"
STATUS_INDETERMINATE = "indeterminate"

EVIDENCE_NONE = "none"
EVIDENCE_POSITIVE_COMPONENT = "positive_component"
EVIDENCE_DIRECT_PAIR_LABEL = "direct_pair_label"
EVIDENCE_COMPONENT_PAIR_LABEL = "component_pair_label"
EVIDENCE_NO_LABEL = "no_label"

PairKey = Tuple[str, str]


@dataclass(frozen=True)
class Gate7TraceRequest:
    """One immutable, policy-independent Gate 7 v2 request."""

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


@dataclass(frozen=True)
class Gate7SemanticClassification:
    """Pure semantic classification of one cache outcome."""

    relation: str
    status: str
    label: Optional[int]
    evidence_kind: str
    source_indices: Tuple[int, ...]


@dataclass(frozen=True)
class Gate7SemanticIndex:
    """Immutable lookup state derived only from QQP calibration rows."""

    schema_version: str
    sha256: str
    canonical_row_count: int
    selected_concept_ids: Tuple[str, ...]
    canonical_text_by_concept: Mapping[str, str]
    concept_by_text_id: Mapping[str, str]
    direct_negative_source_indices: Mapping[PairKey, Tuple[int, ...]]
    component_negative_source_indices: Mapping[PairKey, Tuple[int, ...]]
    counts: Mapping[str, int]
    source_pairs_sha256: str
    source_texts_sha256: str
    source_manifest_sha256: str


def build_gate7_trace(
    prepared_dir: Path,
    seed: int,
    requests: int,
    capacity: int,
) -> Tuple[Tuple[Gate7TraceRequest, ...], str, Dict[str, Any]]:
    """Build the frozen 30/40/30 v2 trace from QQP calibration data."""

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

    paths, observed_hashes, manifest = _prepared_inputs(prepared_dir)
    concept_text_ids = _load_calibration_concepts(paths["pairs.jsonl"])
    texts_by_id = _load_required_texts(
        paths["texts.jsonl"],
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
    scan_concepts = tuple(
        selected_order[hot_set_size : hot_set_size + scan_count]
    )
    selected_concepts = hot_concepts + scan_concepts

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
        values.sort(
            key=lambda item: (
                item[0],
                canonical_by_concept[item[1]][0],
                item[2],
            )
        )
        return values

    phase_values = (
        (PHASE_WARM, phase_occurrences(PHASE_WARM, hot_concepts, warm_count)),
        (PHASE_UNIQUE, phase_occurrences(PHASE_UNIQUE, scan_concepts, scan_count)),
        (
            PHASE_RETURN,
            phase_occurrences(PHASE_RETURN, hot_concepts, return_count),
        ),
    )

    records: List[Gate7TraceRequest] = []
    seen: Set[str] = set()
    for phase, values in phase_values:
        for _, concept_id, occurrence in values:
            text_id, text = canonical_by_concept[concept_id]
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
                    response_payload=_response_payload(concept_id, text_id),
                )
            )

    frozen_records = tuple(records)
    trace_sha256 = _trace_hash(frozen_records)
    semantic_index = build_gate7_semantic_index(
        Path(prepared_dir), selected_concepts
    )
    source_files = {
        "pairs.jsonl": {
            "sha256": observed_hashes["pairs.jsonl"],
            "manifest_declared_sha256": manifest.get("pairs_sha256"),
        },
        "texts.jsonl": {
            "sha256": observed_hashes["texts.jsonl"],
            "manifest_declared_sha256": manifest.get("texts_sha256"),
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
        "hot_text_ids": [
            canonical_by_concept[value][0] for value in hot_concepts
        ],
        "scan_concept_ids": list(scan_concepts),
        "scan_text_ids": [
            canonical_by_concept[value][0] for value in scan_concepts
        ],
        "calibration_concepts_available": len(available_concepts),
        "calibration_texts_available": len(texts_by_id),
        "selection_split": "calibration",
        "selection_rule": (
            "canonical minimum text ID per concept; unchanged gate7b v1 "
            "candidate and phase SHA-256 namespaces; floor(0.8*capacity) hot "
            "concepts; next distinct concepts form the scan"
        ),
        "response_rule": (
            "recorded-response-v2:<concept_id>:<text_id>; response_id is "
            "concept_id"
        ),
        "semantic_index_schema_version": semantic_index.schema_version,
        "semantic_index_sha256": semantic_index.sha256,
        "semantic_index_canonical_row_count": semantic_index.canonical_row_count,
        "semantic_index_counts": dict(semantic_index.counts),
        "prepared_dir": str(Path(prepared_dir).resolve()),
        "source_files": source_files,
        "source_pairs_sha256": observed_hashes["pairs.jsonl"],
        "source_texts_sha256": observed_hashes["texts.jsonl"],
        "source_manifest_sha256": observed_hashes["manifest.json"],
        "source_manifest_schema_version": manifest.get("schema_version"),
        "source_archive_sha256": manifest.get("archive_sha256"),
    }
    return frozen_records, trace_sha256, metadata


def build_gate7_semantic_index(
    prepared_dir: Path, selected_concept_ids: Sequence[str]
) -> Gate7SemanticIndex:
    """Build deterministic evidence for selected calibration concepts."""

    if isinstance(selected_concept_ids, (str, bytes)):
        raise ValueError("selected_concept_ids must be a sequence of strings")
    selected_list = list(selected_concept_ids)
    if not selected_list:
        raise ValueError("selected_concept_ids must not be empty")
    for concept_id in selected_list:
        if not isinstance(concept_id, str) or not concept_id:
            raise ValueError("selected concept IDs must be nonempty strings")
    if len(set(selected_list)) != len(selected_list):
        raise ValueError("selected_concept_ids must be unique")
    selected = tuple(sorted(selected_list))
    selected_set = set(selected)

    paths, hashes, _ = _prepared_inputs(prepared_dir)
    concept_text_ids: Dict[str, Set[str]] = {}
    concept_by_text_id: Dict[str, str] = {}
    exact_labels: Dict[PairKey, int] = {}
    direct_negative: Dict[PairKey, Set[int]] = {}
    component_negative: Dict[PairKey, Set[int]] = {}
    seen_source_indices: Set[int] = set()

    for row in _read_jsonl(paths["pairs.jsonl"]):
        split = row.get("split")
        if split == "test":
            continue
        if split != "calibration":
            raise ValueError("QQP pair has unknown split %r" % split)

        label = row.get("label")
        if (
            isinstance(label, bool)
            or not isinstance(label, int)
            or label not in (0, 1)
        ):
            raise ValueError("calibration QQP pair has invalid label %r" % label)
        source_index = row.get("source_index")
        if (
            isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or source_index < 0
        ):
            raise ValueError("calibration QQP pair has invalid source_index")
        if source_index in seen_source_indices:
            raise ValueError("duplicate calibration source_index")
        seen_source_indices.add(source_index)

        text_a = _required_string(row, "text_a_id", paths["pairs.jsonl"])
        text_b = _required_string(row, "text_b_id", paths["pairs.jsonl"])
        concept_a = _required_string(row, "concept_a", paths["pairs.jsonl"])
        concept_b = _required_string(row, "concept_b", paths["pairs.jsonl"])

        for text_id, concept_id in (
            (text_a, concept_a),
            (text_b, concept_b),
        ):
            previous = concept_by_text_id.setdefault(text_id, concept_id)
            if previous != concept_id:
                raise ValueError(
                    "calibration text_id %s maps to multiple concepts" % text_id
                )
            concept_text_ids.setdefault(concept_id, set()).add(text_id)

        text_pair = _pair_key(text_a, text_b) if text_a != text_b else None
        if text_pair is not None:
            previous_label = exact_labels.setdefault(text_pair, label)
            if previous_label != label:
                raise ValueError(
                    "calibration pair %r has conflicting labels" % (text_pair,)
                )

        if label == 1:
            if concept_a != concept_b:
                raise ValueError("positive calibration pair crosses prepared concepts")
            continue

        if concept_a == concept_b:
            raise ValueError(
                "negative calibration pair is inside one positive component"
            )
        if text_pair is None:
            raise ValueError("negative calibration pair repeats one text ID")
        if concept_a in selected_set and concept_b in selected_set:
            direct_negative.setdefault(text_pair, set()).add(source_index)
            component_negative.setdefault(
                _pair_key(concept_a, concept_b), set()
            ).add(source_index)

    missing_concepts = sorted(selected_set.difference(concept_text_ids))
    if missing_concepts:
        raise ValueError(
            "selected concepts are absent from calibration data: %s"
            % ", ".join(missing_concepts[:3])
        )

    selected_members = {
        concept_id: set(concept_text_ids[concept_id]) for concept_id in selected
    }
    required_text_ids = {
        text_id
        for members in selected_members.values()
        for text_id in members
    }
    _load_required_texts(paths["texts.jsonl"], required_text_ids)
    selected_concept_by_text = {
        text_id: concept_id
        for concept_id, members in selected_members.items()
        for text_id in members
    }
    canonical_text_by_concept = {
        concept_id: min(selected_members[concept_id]) for concept_id in selected
    }

    return _make_semantic_index(
        selected_concept_ids=selected,
        canonical_text_by_concept=canonical_text_by_concept,
        concept_by_text_id=selected_concept_by_text,
        direct_negative_source_indices={
            key: tuple(sorted(values)) for key, values in direct_negative.items()
        },
        component_negative_source_indices={
            key: tuple(sorted(values))
            for key, values in component_negative.items()
        },
        source_pairs_sha256=hashes["pairs.jsonl"],
        source_texts_sha256=hashes["texts.jsonl"],
        source_manifest_sha256=hashes["manifest.json"],
    )


def classify_semantic_relation(
    semantic_index: Gate7SemanticIndex,
    raw_hit: bool,
    query_text_id: str,
    query_concept_id: str,
    candidate_text_id: Optional[str] = None,
    candidate_concept_id: Optional[str] = None,
) -> Gate7SemanticClassification:
    """Classify one hit or miss without I/O or mutable state."""

    if not isinstance(semantic_index, Gate7SemanticIndex):
        raise TypeError("semantic_index must be a Gate7SemanticIndex")
    if not isinstance(raw_hit, bool):
        raise ValueError("raw_hit must be a bool")
    _validate_index_identity(
        semantic_index, query_text_id, query_concept_id, "query"
    )
    if not raw_hit:
        return Gate7SemanticClassification(
            RELATION_NOT_APPLICABLE,
            STATUS_NOT_APPLICABLE,
            None,
            EVIDENCE_NONE,
            (),
        )

    if candidate_text_id is None or candidate_concept_id is None:
        raise ValueError("raw hit requires candidate text and concept IDs")
    _validate_index_identity(
        semantic_index,
        candidate_text_id,
        candidate_concept_id,
        "candidate",
    )
    if query_concept_id == candidate_concept_id:
        return Gate7SemanticClassification(
            RELATION_POSITIVE_SAME_COMPONENT,
            STATUS_VALID,
            1,
            EVIDENCE_POSITIVE_COMPONENT,
            (),
        )

    direct_sources = semantic_index.direct_negative_source_indices.get(
        _pair_key(query_text_id, candidate_text_id)
    )
    if direct_sources:
        return Gate7SemanticClassification(
            RELATION_NEGATIVE_DIRECT,
            STATUS_INVALID,
            0,
            EVIDENCE_DIRECT_PAIR_LABEL,
            tuple(direct_sources),
        )
    component_sources = semantic_index.component_negative_source_indices.get(
        _pair_key(query_concept_id, candidate_concept_id)
    )
    if component_sources:
        return Gate7SemanticClassification(
            RELATION_NEGATIVE_COMPONENT_DERIVED,
            STATUS_INVALID,
            0,
            EVIDENCE_COMPONENT_PAIR_LABEL,
            tuple(component_sources),
        )
    return Gate7SemanticClassification(
        RELATION_UNLABELED_CROSS_COMPONENT,
        STATUS_INDETERMINATE,
        None,
        EVIDENCE_NO_LABEL,
        (),
    )


def semantic_index_artifact(
    semantic_index: Gate7SemanticIndex, seed: int, trace_sha256: str
) -> Dict[str, Any]:
    """Return a deterministic JSON object without a circular self-hash."""

    if not isinstance(semantic_index, Gate7SemanticIndex):
        raise TypeError("semantic_index must be a Gate7SemanticIndex")
    _validate_integer("seed", seed)
    _validate_sha256("trace_sha256", trace_sha256)
    return {
        "schema_version": SEMANTIC_ARTIFACT_SCHEMA_VERSION,
        "semantic_index_schema_version": semantic_index.schema_version,
        "seed": seed,
        "trace_sha256": trace_sha256,
        "semantic_index_sha256": semantic_index.sha256,
        "canonical_row_count": semantic_index.canonical_row_count,
        "counts": dict(sorted(semantic_index.counts.items())),
        "source_pairs_sha256": semantic_index.source_pairs_sha256,
        "source_texts_sha256": semantic_index.source_texts_sha256,
        "source_manifest_sha256": semantic_index.source_manifest_sha256,
        "selected_concepts": [
            {
                "concept_id": concept_id,
                "canonical_text_id": semantic_index.canonical_text_by_concept[
                    concept_id
                ],
            }
            for concept_id in semantic_index.selected_concept_ids
        ],
        "component_members": [
            {"text_id": text_id, "concept_id": concept_id}
            for text_id, concept_id in sorted(
                semantic_index.concept_by_text_id.items()
            )
        ],
        "direct_negative_pairs": [
            {
                "text_id_a": pair[0],
                "text_id_b": pair[1],
                "source_indices": list(source_indices),
            }
            for pair, source_indices in sorted(
                semantic_index.direct_negative_source_indices.items()
            )
        ],
        "component_negative_pairs": [
            {
                "concept_id_a": pair[0],
                "concept_id_b": pair[1],
                "source_indices": list(source_indices),
            }
            for pair, source_indices in sorted(
                semantic_index.component_negative_source_indices.items()
            )
        ],
    }


def load_semantic_index_artifact(
    value: Mapping[str, Any],
    expected_seed: Optional[int] = None,
    expected_trace_sha256: Optional[str] = None,
) -> Gate7SemanticIndex:
    """Validate a serialized artifact and reconstruct its semantic index."""

    if not isinstance(value, Mapping):
        raise ValueError("semantic index artifact must be an object")
    required_fields = {
        "schema_version",
        "semantic_index_schema_version",
        "seed",
        "trace_sha256",
        "semantic_index_sha256",
        "canonical_row_count",
        "counts",
        "source_pairs_sha256",
        "source_texts_sha256",
        "source_manifest_sha256",
        "selected_concepts",
        "component_members",
        "direct_negative_pairs",
        "component_negative_pairs",
    }
    if set(value) != required_fields:
        raise ValueError("semantic index artifact has unexpected fields")
    if value["schema_version"] != SEMANTIC_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("semantic index artifact schema mismatch")
    if value["semantic_index_schema_version"] != SEMANTIC_INDEX_SCHEMA_VERSION:
        raise ValueError("semantic index schema mismatch")

    seed = value["seed"]
    _validate_integer("artifact seed", seed)
    if expected_seed is not None and seed != expected_seed:
        raise ValueError("semantic index artifact seed mismatch")
    trace_sha256 = value["trace_sha256"]
    _validate_sha256("artifact trace_sha256", trace_sha256)
    if expected_trace_sha256 is not None and trace_sha256 != expected_trace_sha256:
        raise ValueError("semantic index artifact trace hash mismatch")

    expected_index_sha256 = value["semantic_index_sha256"]
    _validate_sha256("semantic_index_sha256", expected_index_sha256)
    expected_row_count = _nonnegative_int(
        value["canonical_row_count"], "canonical_row_count"
    )
    if not isinstance(value["counts"], Mapping):
        raise ValueError("semantic index counts must be an object")
    artifact_counts = {
        str(key): _nonnegative_int(item, "semantic count")
        for key, item in value["counts"].items()
    }
    for name in (
        "source_pairs_sha256",
        "source_texts_sha256",
        "source_manifest_sha256",
    ):
        _validate_sha256(name, value[name])

    selected_records = _record_list(
        value["selected_concepts"],
        {"concept_id", "canonical_text_id"},
        "selected_concepts",
    )
    if selected_records != sorted(
        selected_records,
        key=lambda row: (row["concept_id"], row["canonical_text_id"]),
    ):
        raise ValueError("selected_concepts records are not sorted")
    canonical: Dict[str, str] = {}
    for row in selected_records:
        concept_id = _artifact_string(row, "concept_id")
        text_id = _artifact_string(row, "canonical_text_id")
        if concept_id in canonical:
            raise ValueError("duplicate selected concept")
        canonical[concept_id] = text_id
    if not canonical:
        raise ValueError("semantic index artifact selects no concepts")
    selected = tuple(sorted(canonical))

    member_records = _record_list(
        value["component_members"],
        {"text_id", "concept_id"},
        "component_members",
    )
    if member_records != sorted(
        member_records, key=lambda row: (row["text_id"], row["concept_id"])
    ):
        raise ValueError("component_members records are not sorted")
    members: Dict[str, str] = {}
    for row in member_records:
        text_id = _artifact_string(row, "text_id")
        concept_id = _artifact_string(row, "concept_id")
        if concept_id not in canonical:
            raise ValueError("component member references an unselected concept")
        if text_id in members:
            raise ValueError("duplicate component member text ID")
        members[text_id] = concept_id
    for concept_id, text_id in canonical.items():
        if members.get(text_id) != concept_id:
            raise ValueError("canonical text is not a member of its component")

    direct = _load_pair_records(
        value["direct_negative_pairs"],
        ("text_id_a", "text_id_b"),
        "direct_negative_pairs",
    )
    component = _load_pair_records(
        value["component_negative_pairs"],
        ("concept_id_a", "concept_id_b"),
        "component_negative_pairs",
    )
    for text_pair, source_indices in direct.items():
        try:
            concept_pair = _pair_key(members[text_pair[0]], members[text_pair[1]])
        except KeyError as exc:
            raise ValueError(
                "direct negative references an unknown component member"
            ) from exc
        component_sources = component.get(concept_pair)
        if component_sources is None or not set(source_indices).issubset(
            component_sources
        ):
            raise ValueError(
                "direct negative is not supported by component evidence"
            )
    for concept_pair in component:
        if concept_pair[0] not in canonical or concept_pair[1] not in canonical:
            raise ValueError(
                "component negative references an unselected concept"
            )

    index = _make_semantic_index(
        selected,
        canonical,
        members,
        direct,
        component,
        value["source_pairs_sha256"],
        value["source_texts_sha256"],
        value["source_manifest_sha256"],
    )
    if dict(index.counts) != artifact_counts:
        raise ValueError("semantic index artifact counts mismatch")
    if index.canonical_row_count != expected_row_count:
        raise ValueError("semantic index canonical row count mismatch")
    if index.sha256 != expected_index_sha256:
        raise ValueError("semantic index canonical hash mismatch")
    return index


def _make_semantic_index(
    selected_concept_ids: Tuple[str, ...],
    canonical_text_by_concept: Mapping[str, str],
    concept_by_text_id: Mapping[str, str],
    direct_negative_source_indices: Mapping[PairKey, Tuple[int, ...]],
    component_negative_source_indices: Mapping[PairKey, Tuple[int, ...]],
    source_pairs_sha256: str,
    source_texts_sha256: str,
    source_manifest_sha256: str,
) -> Gate7SemanticIndex:
    selected = selected_concept_ids
    canonical_dict = dict(sorted(canonical_text_by_concept.items()))
    member_dict = dict(sorted(concept_by_text_id.items()))
    direct_dict = {
        key: tuple(value)
        for key, value in sorted(direct_negative_source_indices.items())
    }
    component_dict = {
        key: tuple(value)
        for key, value in sorted(component_negative_source_indices.items())
    }
    counts = _semantic_counts(
        selected,
        canonical_dict,
        member_dict,
        direct_dict,
        component_dict,
    )
    rows = _canonical_semantic_rows(
        selected,
        canonical_dict,
        member_dict,
        direct_dict,
        component_dict,
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    return Gate7SemanticIndex(
        SEMANTIC_INDEX_SCHEMA_VERSION,
        digest.hexdigest(),
        len(rows),
        tuple(selected),
        MappingProxyType(canonical_dict),
        MappingProxyType(member_dict),
        MappingProxyType(direct_dict),
        MappingProxyType(component_dict),
        MappingProxyType(dict(sorted(counts.items()))),
        source_pairs_sha256,
        source_texts_sha256,
        source_manifest_sha256,
    )


def _semantic_counts(
    selected: Sequence[str],
    canonical: Mapping[str, str],
    members: Mapping[str, str],
    direct: Mapping[PairKey, Tuple[int, ...]],
    component: Mapping[PairKey, Tuple[int, ...]],
) -> Dict[str, int]:
    canonical_direct_components: Set[PairKey] = set()
    for text_pair in direct:
        concept_a = members[text_pair[0]]
        concept_b = members[text_pair[1]]
        if {canonical[concept_a], canonical[concept_b]} == set(text_pair):
            canonical_direct_components.add(_pair_key(concept_a, concept_b))
    component_pairs = set(component)
    if not canonical_direct_components.issubset(component_pairs):
        raise ValueError("direct negative lacks component-level evidence")
    derived_pairs = component_pairs.difference(canonical_direct_components)
    distinct_pairs = len(selected) * (len(selected) - 1) // 2
    labeled_pairs = len(canonical_direct_components) + len(derived_pairs)
    if labeled_pairs > distinct_pairs:
        raise ValueError("negative relationship count exceeds candidate pairs")
    return {
        "selected_concepts": len(selected),
        "selected_texts": len(members),
        "distinct_candidate_pairs": distinct_pairs,
        "direct_negative_pairs": len(canonical_direct_components),
        "direct_negative_text_pairs": len(direct),
        "component_negative_pairs": len(component_pairs),
        "negative_component_derived_pairs": len(derived_pairs),
        "unlabeled_cross_component_pairs": distinct_pairs - labeled_pairs,
    }


def _canonical_semantic_rows(
    selected: Sequence[str],
    canonical: Mapping[str, str],
    members: Mapping[str, str],
    direct: Mapping[PairKey, Tuple[int, ...]],
    component: Mapping[PairKey, Tuple[int, ...]],
) -> Tuple[str, ...]:
    rows: List[Dict[str, Any]] = []
    rows.extend(
        {
            "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "selected_concept",
            "concept_id": concept_id,
            "canonical_text_id": canonical[concept_id],
        }
        for concept_id in selected
    )
    rows.extend(
        {
            "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "component_member",
            "text_id": text_id,
            "concept_id": concept_id,
        }
        for text_id, concept_id in sorted(members.items())
    )
    rows.extend(
        {
            "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "direct_negative_pair",
            "text_id_a": pair[0],
            "text_id_b": pair[1],
            "source_indices": list(source_indices),
        }
        for pair, source_indices in sorted(direct.items())
    )
    rows.extend(
        {
            "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "component_negative_pair",
            "concept_id_a": pair[0],
            "concept_id_b": pair[1],
            "source_indices": list(source_indices),
        }
        for pair, source_indices in sorted(component.items())
    )
    return tuple(sorted(_canonical_json(row) for row in rows))


def _load_pair_records(
    value: Any, endpoint_fields: Tuple[str, str], name: str
) -> Dict[PairKey, Tuple[int, ...]]:
    rows = _record_list(
        value,
        {endpoint_fields[0], endpoint_fields[1], "source_indices"},
        name,
    )
    if rows != sorted(
        rows, key=lambda row: (row[endpoint_fields[0]], row[endpoint_fields[1]])
    ):
        raise ValueError("%s records are not sorted" % name)
    result: Dict[PairKey, Tuple[int, ...]] = {}
    for row in rows:
        left = _artifact_string(row, endpoint_fields[0])
        right = _artifact_string(row, endpoint_fields[1])
        pair = _pair_key(left, right)
        if pair != (left, right):
            raise ValueError("%s endpoints are not canonically ordered" % name)
        if pair in result:
            raise ValueError("duplicate pair in %s" % name)
        raw_indices = row["source_indices"]
        if not isinstance(raw_indices, list) or not raw_indices:
            raise ValueError("%s source_indices must be a nonempty list" % name)
        indices = tuple(
            _nonnegative_int(item, "%s source index" % name)
            for item in raw_indices
        )
        if indices != tuple(sorted(set(indices))):
            raise ValueError("%s source_indices are not sorted and unique" % name)
        result[pair] = indices
    return result


def _record_list(value: Any, fields: Set[str], name: str) -> List[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("%s must be a list" % name)
    rows: List[Mapping[str, Any]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != fields:
            raise ValueError("%s has an invalid record" % name)
        rows.append(row)
    return rows


def _artifact_string(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("semantic artifact has invalid %s" % key)
    return value


def _validate_index_identity(
    index: Gate7SemanticIndex, text_id: Any, concept_id: Any, role: str
) -> None:
    if not isinstance(text_id, str) or not text_id:
        raise ValueError("%s text ID must be a nonempty string" % role)
    if not isinstance(concept_id, str) or not concept_id:
        raise ValueError("%s concept ID must be a nonempty string" % role)
    indexed_concept = index.concept_by_text_id.get(text_id)
    if indexed_concept is None:
        raise ValueError("%s text ID is absent from semantic index" % role)
    if indexed_concept != concept_id:
        raise ValueError("%s text/concept identity mismatch" % role)


def _prepared_inputs(
    prepared_dir: Path,
) -> Tuple[Dict[str, Path], Dict[str, str], Dict[str, Any]]:
    prepared_dir = Path(prepared_dir)
    if not prepared_dir.is_dir():
        raise ValueError("prepared_dir is not a directory: %s" % prepared_dir)
    paths = {
        "pairs.jsonl": prepared_dir / "pairs.jsonl",
        "texts.jsonl": prepared_dir / "texts.jsonl",
        "manifest.json": prepared_dir / "manifest.json",
    }
    for path in paths.values():
        if not path.is_file():
            raise ValueError("missing QQP prepared artifact: %s" % path)
    hashes = {name: _sha256_file(path) for name, path in paths.items()}
    manifest = _read_manifest(paths["manifest.json"])
    _validate_declared_hash(manifest, "pairs_sha256", hashes["pairs.jsonl"])
    _validate_declared_hash(manifest, "texts_sha256", hashes["texts.jsonl"])
    return paths, hashes, manifest


def _load_calibration_concepts(path: Path) -> Dict[str, Set[str]]:
    concept_text_ids: Dict[str, Set[str]] = {}
    concept_by_text_id: Dict[str, str] = {}
    calibration_pairs = 0
    for row in _read_jsonl(path):
        split = row.get("split")
        if split == "test":
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
        raise ValueError(
            "%d calibration text IDs are missing from texts.jsonl: %s"
            % (len(missing), ", ".join(missing[:3]))
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
        with Path(path).open("r", encoding="utf-8") as source:
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


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("%s must be a nonnegative integer" % name)
    return value


def _validate_sha256(name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("%s must be a lowercase SHA-256" % name)


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


def _pair_key(left: str, right: str) -> PairKey:
    if left == right:
        raise ValueError("semantic pair endpoints must be distinct")
    return (left, right) if left < right else (right, left)


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


def _response_payload(concept_id: str, text_id: str) -> str:
    return "recorded-response-v2:%s:%s" % (concept_id, text_id)


def _trace_hash(records: Sequence[Gate7TraceRequest]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(_canonical_json(asdict(record)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError("could not hash QQP prepared artifact: %s" % path) from exc
    return digest.hexdigest()


__all__ = [
    "CANDIDATE_NAMESPACE",
    "EVIDENCE_COMPONENT_PAIR_LABEL",
    "EVIDENCE_DIRECT_PAIR_LABEL",
    "EVIDENCE_NONE",
    "EVIDENCE_NO_LABEL",
    "EVIDENCE_POSITIVE_COMPONENT",
    "Gate7SemanticClassification",
    "Gate7SemanticIndex",
    "Gate7TraceRequest",
    "PHASE_NAMESPACE",
    "RELATION_NEGATIVE_COMPONENT_DERIVED",
    "RELATION_NEGATIVE_DIRECT",
    "RELATION_NOT_APPLICABLE",
    "RELATION_POSITIVE_SAME_COMPONENT",
    "RELATION_UNLABELED_CROSS_COMPONENT",
    "SCHEMA_VERSION",
    "SEMANTIC_ARTIFACT_SCHEMA_VERSION",
    "SEMANTIC_INDEX_SCHEMA_VERSION",
    "STATUS_INDETERMINATE",
    "STATUS_INVALID",
    "STATUS_NOT_APPLICABLE",
    "STATUS_VALID",
    "build_gate7_semantic_index",
    "build_gate7_trace",
    "classify_semantic_relation",
    "load_semantic_index_artifact",
    "semantic_index_artifact",
]
