"""Contract tests for Gate 7 v2 traces and semantic evidence."""

import copy
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.carma.gate7_trace import build_gate7_trace as build_v1_trace
from benchmarks.carma.gate7_v2_trace import (
    CANDIDATE_NAMESPACE,
    PHASE_NAMESPACE,
    RELATION_NEGATIVE_COMPONENT_DERIVED,
    RELATION_NEGATIVE_DIRECT,
    RELATION_NOT_APPLICABLE,
    RELATION_POSITIVE_SAME_COMPONENT,
    RELATION_UNLABELED_CROSS_COMPONENT,
    SCHEMA_VERSION,
    STATUS_INDETERMINATE,
    STATUS_INVALID,
    STATUS_NOT_APPLICABLE,
    STATUS_VALID,
    Gate7TraceRequest,
    build_gate7_semantic_index,
    build_gate7_trace,
    classify_semantic_relation,
    load_semantic_index_artifact,
    semantic_index_artifact,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PREPARED = REPO_ROOT / "artifacts" / "qqp-full" / "prepared"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path, rows):
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_prepared(prepared, pair_rows, text_rows):
    prepared.mkdir()
    _write_jsonl(prepared / "pairs.jsonl", pair_rows)
    _write_jsonl(prepared / "texts.jsonl", text_rows)
    manifest = {
        "schema_version": "carma-qqp-v1",
        "archive_sha256": "a" * 64,
        "pairs_sha256": _sha256(prepared / "pairs.jsonl"),
        "texts_sha256": _sha256(prepared / "texts.jsonl"),
    }
    (prepared / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return prepared


def _refresh_manifest(prepared):
    manifest_path = prepared / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pairs_sha256"] = _sha256(prepared / "pairs.jsonl")
    manifest["texts_sha256"] = _sha256(prepared / "texts.jsonl")
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _prepared(tmp_path, calibration_concepts=24, test_rows=None):
    text_rows = []
    pair_rows = []
    for index in range(calibration_concepts):
        concept_id = "calibration-concept-%02d" % index
        text_id = "calibration-text-%02d" % index
        text_rows.append(
            {"text_id": text_id, "text": "Real QQP question %02d?" % index}
        )
        pair_rows.append(
            {
                "split": "calibration",
                "source_index": index,
                "text_a_id": text_id,
                "text_b_id": text_id,
                "concept_a": concept_id,
                "concept_b": concept_id,
                "label": 1,
            }
        )
    pair_rows.extend(test_rows or [])
    return _write_prepared(tmp_path / "prepared", pair_rows, text_rows)


def _semantic_prepared(tmp_path):
    texts = [
        {"text_id": "a0", "text": "A canonical"},
        {"text_id": "a1", "text": "A paraphrase"},
        {"text_id": "b0", "text": "B canonical"},
        {"text_id": "b1", "text": "B paraphrase"},
        {"text_id": "c0", "text": "C canonical"},
        {"text_id": "d0", "text": "D canonical"},
    ]
    pairs = [
        {
            "split": "calibration",
            "source_index": 0,
            "label": 1,
            "text_a_id": "a0",
            "text_b_id": "a1",
            "concept_a": "A",
            "concept_b": "A",
        },
        {
            "split": "calibration",
            "source_index": 1,
            "label": 1,
            "text_a_id": "b0",
            "text_b_id": "b1",
            "concept_a": "B",
            "concept_b": "B",
        },
        {
            "split": "calibration",
            "source_index": 2,
            "label": 1,
            "text_a_id": "c0",
            "text_b_id": "c0",
            "concept_a": "C",
            "concept_b": "C",
        },
        {
            "split": "calibration",
            "source_index": 3,
            "label": 1,
            "text_a_id": "d0",
            "text_b_id": "d0",
            "concept_a": "D",
            "concept_b": "D",
        },
        {
            "split": "calibration",
            "source_index": 10,
            "label": 0,
            "text_a_id": "a0",
            "text_b_id": "b0",
            "concept_a": "A",
            "concept_b": "B",
        },
        {
            "split": "calibration",
            "source_index": 11,
            "label": 0,
            "text_a_id": "a1",
            "text_b_id": "c0",
            "concept_a": "A",
            "concept_b": "C",
        },
        {"split": "test", "poison": "must not be dereferenced"},
    ]
    return _write_prepared(tmp_path / "semantic", pairs, texts)


def _pair_rows(prepared):
    return [
        json.loads(line)
        for line in (prepared / "pairs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def test_v2_trace_is_deterministic_and_preserves_v1_text_order(tmp_path):
    prepared = _prepared(tmp_path)
    first, first_hash, metadata = build_gate7_trace(
        prepared, seed=17, requests=20, capacity=5
    )
    second, second_hash, second_metadata = build_gate7_trace(
        prepared, seed=17, requests=20, capacity=5
    )
    v1, _, v1_metadata = build_v1_trace(
        prepared, seed=17, requests=20, capacity=5
    )

    assert first == second
    assert first_hash == second_hash == metadata["trace_sha256"]
    assert metadata == second_metadata
    assert metadata["schema_version"] == SCHEMA_VERSION
    assert CANDIDATE_NAMESPACE == "gate7b-qqp-candidate-v1"
    assert PHASE_NAMESPACE == "gate7b-qqp-phase-v1"
    assert [
        (
            row.index,
            row.request_id,
            row.phase,
            row.occurrence,
            row.reuse_opportunity,
            row.text_id,
            row.text,
            row.concept_id,
            row.response_id,
        )
        for row in first
    ] == [
        (
            row.index,
            row.request_id,
            row.phase,
            row.occurrence,
            row.reuse_opportunity,
            row.text_id,
            row.text,
            row.concept_id,
            row.response_id,
        )
        for row in v1
    ]
    assert metadata["hot_text_ids"] == v1_metadata["hot_text_ids"]
    assert metadata["scan_text_ids"] == v1_metadata["scan_text_ids"]
    assert all(isinstance(row, Gate7TraceRequest) for row in first)
    assert all(
        row.response_payload
        == "recorded-response-v2:%s:%s" % (row.concept_id, row.text_id)
        for row in first
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        first[0].text = "mutated"

    assert metadata["phase_counts"] == {"warm": 6, "scan": 8, "return": 6}
    assert metadata["hot_set_size"] == 4
    assert metadata["semantic_index_counts"]["selected_concepts"] == 12
    assert len(metadata["semantic_index_sha256"]) == 64


def test_semantic_index_classifies_all_five_outcomes(tmp_path):
    prepared = _semantic_prepared(tmp_path)
    index = build_gate7_semantic_index(prepared, ["D", "C", "B", "A"])

    assert index.selected_concept_ids == ("A", "B", "C", "D")
    assert dict(index.counts) == {
        "component_negative_pairs": 2,
        "direct_negative_pairs": 1,
        "direct_negative_text_pairs": 2,
        "distinct_candidate_pairs": 6,
        "negative_component_derived_pairs": 1,
        "selected_concepts": 4,
        "selected_texts": 6,
        "unlabeled_cross_component_pairs": 4,
    }

    same = classify_semantic_relation(index, True, "a0", "A", "a1", "A")
    direct = classify_semantic_relation(index, True, "b0", "B", "a0", "A")
    derived = classify_semantic_relation(index, True, "a0", "A", "c0", "C")
    unlabeled = classify_semantic_relation(index, True, "b0", "B", "c0", "C")
    miss = classify_semantic_relation(index, False, "d0", "D")

    assert (same.relation, same.status, same.label, same.source_indices) == (
        RELATION_POSITIVE_SAME_COMPONENT,
        STATUS_VALID,
        1,
        (),
    )
    assert (direct.relation, direct.status, direct.label, direct.source_indices) == (
        RELATION_NEGATIVE_DIRECT,
        STATUS_INVALID,
        0,
        (10,),
    )
    assert (
        derived.relation,
        derived.status,
        derived.label,
        derived.source_indices,
    ) == (RELATION_NEGATIVE_COMPONENT_DERIVED, STATUS_INVALID, 0, (11,))
    assert (unlabeled.relation, unlabeled.status, unlabeled.label) == (
        RELATION_UNLABELED_CROSS_COMPONENT,
        STATUS_INDETERMINATE,
        None,
    )
    assert (miss.relation, miss.status, miss.label) == (
        RELATION_NOT_APPLICABLE,
        STATUS_NOT_APPLICABLE,
        None,
    )


def test_classifier_rejects_missing_or_inconsistent_hit_identity(tmp_path):
    index = build_gate7_semantic_index(
        _semantic_prepared(tmp_path), ["A", "B", "C", "D"]
    )
    with pytest.raises(ValueError, match="requires candidate"):
        classify_semantic_relation(index, True, "a0", "A")
    with pytest.raises(ValueError, match="query text/concept identity mismatch"):
        classify_semantic_relation(index, False, "a0", "B")
    with pytest.raises(ValueError, match="candidate text ID is absent"):
        classify_semantic_relation(index, True, "a0", "A", "missing", "B")


def test_semantic_artifact_round_trip_is_json_safe_and_validated(tmp_path):
    index = build_gate7_semantic_index(
        _semantic_prepared(tmp_path), ["A", "B", "C", "D"]
    )
    artifact = semantic_index_artifact(index, 17, "f" * 64)
    serialized = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
    restored_value = json.loads(serialized)
    restored = load_semantic_index_artifact(
        restored_value, expected_seed=17, expected_trace_sha256="f" * 64
    )

    assert restored.sha256 == index.sha256
    assert restored.canonical_row_count == index.canonical_row_count
    assert dict(restored.counts) == dict(index.counts)
    assert dict(restored.direct_negative_source_indices) == dict(
        index.direct_negative_source_indices
    )
    assert all(isinstance(row, dict) for row in artifact["direct_negative_pairs"])

    damaged = copy.deepcopy(artifact)
    damaged["semantic_index_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="canonical hash mismatch"):
        load_semantic_index_artifact(damaged)

    damaged = copy.deepcopy(artifact)
    damaged["counts"]["direct_negative_pairs"] += 1
    with pytest.raises(ValueError, match="counts mismatch"):
        load_semantic_index_artifact(damaged)

    with pytest.raises(ValueError, match="trace hash mismatch"):
        load_semantic_index_artifact(
            artifact, expected_seed=17, expected_trace_sha256="e" * 64
        )


def test_index_hash_is_calibration_only_and_order_independent(tmp_path):
    prepared = _semantic_prepared(tmp_path)
    first = build_gate7_semantic_index(prepared, ["A", "B", "C", "D"])
    rows = _pair_rows(prepared)
    calibration = list(reversed(rows[:-1]))
    # Removing the held-out poison entirely and reordering calibration rows
    # must not change the selected calibration-only semantic identity.
    _write_jsonl(prepared / "pairs.jsonl", calibration)
    _refresh_manifest(prepared)
    second = build_gate7_semantic_index(prepared, ["D", "C", "B", "A"])

    assert second.sha256 == first.sha256
    assert second.canonical_row_count == first.canonical_row_count
    assert dict(second.counts) == dict(first.counts)
    assert second.source_pairs_sha256 != first.source_pairs_sha256


@pytest.mark.parametrize(
    "bad_row,message",
    [
        (
            {
                "split": "calibration",
                "source_index": 20,
                "label": 0,
                "text_a_id": "a0",
                "text_b_id": "a0",
                "concept_a": "A",
                "concept_b": "A",
            },
            "inside one positive component",
        ),
        (
            {
                "split": "calibration",
                "source_index": 21,
                "label": 1,
                "text_a_id": "b0",
                "text_b_id": "c0",
                "concept_a": "B",
                "concept_b": "C",
            },
            "positive calibration pair crosses",
        ),
        (
            {
                "split": "calibration",
                "source_index": 22,
                "label": 1,
                "text_a_id": "a0",
                "text_b_id": "b0",
                "concept_a": "A",
                "concept_b": "B",
            },
            "conflicting labels",
        ),
    ],
)
def test_semantic_index_rejects_contradictions(tmp_path, bad_row, message):
    prepared = _semantic_prepared(tmp_path)
    rows = _pair_rows(prepared)
    rows.insert(-1, bad_row)
    _write_jsonl(prepared / "pairs.jsonl", rows)
    _refresh_manifest(prepared)
    with pytest.raises(ValueError, match=message):
        build_gate7_semantic_index(prepared, ["A", "B", "C", "D"])


def test_heldout_poison_is_not_dereferenced_by_trace_or_index(tmp_path):
    poison = {"split": "test", "poison": "missing every semantic field"}
    prepared = _prepared(tmp_path, test_rows=[poison])
    trace, trace_hash, metadata = build_gate7_trace(prepared, 23, 20, 5)
    selected = metadata["hot_concept_ids"] + metadata["scan_concept_ids"]
    index = build_gate7_semantic_index(prepared, selected)

    assert len(trace) == 20
    assert trace_hash == metadata["trace_sha256"]
    assert index.counts["selected_concepts"] == 12
    assert all("poison" not in concept for concept in index.selected_concept_ids)


@pytest.mark.parametrize(
    "requests,capacity,message",
    [
        (21, 5, "multiple of 10"),
        (10, 5, "warm phase"),
        (20, 1, "capacity must be at least 2"),
    ],
)
def test_invalid_trace_dimensions_are_rejected(
    tmp_path, requests, capacity, message
):
    prepared = _prepared(tmp_path)
    with pytest.raises(ValueError, match=message):
        build_gate7_trace(prepared, seed=1, requests=requests, capacity=capacity)


def test_manifest_hash_mismatch_is_rejected(tmp_path):
    prepared = _prepared(tmp_path)
    manifest_path = prepared / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pairs_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="pairs_sha256 mismatch"):
        build_gate7_trace(prepared, seed=1, requests=20, capacity=5)


def test_producer_auditor_and_contract_share_frozen_full_hashes():
    from benchmarks.carma import gate7_v2_audit as auditor
    from benchmarks.carma import gate7_v2_onnx_integration_benchmark as producer

    expected = {
        20261001: (
            "25f773f174d14648c254aa355d9f7a983eafa6acad9c852f1af934ae42635c1b",
            "bf7496e49b0a2adda8414aff26ef63b4771f3791dc411084a310787dd5af6832",
        ),
        20261002: (
            "fc20d64d878d27bfcb0298bff35419a6c9aeae9ecbc686e314f61122593ce50f",
            "f96ef44b7a5d8cc5fc55a6024d516c6ad585c8b7519628e7f38e9dc085c05546",
        ),
        20261003: (
            "199178c7739cdf5d5682cf60963b7228c7e6f6bc1394dbf297f8fc8e4a3b386c",
            "c5d0a1b58453cfedb89ddd5bb890a4442c00c69d00a7647d52770eaf26240abd",
        ),
        20261004: (
            "806a58004bfd3fecf3b97603639ec279b338967c64e99842df36f17e704f9cc4",
            "07897d6572e7925c0b540f6c20ce76f692ccb29f979844cb41b5a1945ce9fd6a",
        ),
        20261005: (
            "a680defccd6e9973a65c9b34c37c44d62edfdf67a69ffcf260d7d59dbd9d270f",
            "7ccac25843a51d3c2417eaf69a5411510cfc0fe7f5d6876efb7acf6d30496a41",
        ),
    }
    assert auditor.FROZEN_TRACE_SEMANTIC_HASHES == expected
    assert producer.PINNED_FULL_TRACE_SHA256 == {
        seed: values[0] for seed, values in expected.items()
    }
    assert producer.PINNED_FULL_SEMANTIC_INDEX_SHA256 == {
        seed: values[1] for seed, values in expected.items()
    }
    contract = (
        REPO_ROOT / "docs" / "project" / "gate7-v2-remediation-contract.md"
    ).read_text(encoding="utf-8")
    assert all(
        digest in contract for values in expected.values() for digest in values
    )


@pytest.mark.skipif(
    not (REAL_PREPARED / "pairs.jsonl").is_file(),
    reason="full prepared QQP data is a local, non-repository artifact",
)
def test_real_data_source_1804_and_frozen_seed_regressions():
    expected = {
        20261001: (
            "25f773f174d14648c254aa355d9f7a983eafa6acad9c852f1af934ae42635c1b",
            "bf7496e49b0a2adda8414aff26ef63b4771f3791dc411084a310787dd5af6832",
            57,
            1,
            818502,
        ),
        20261002: (
            "fc20d64d878d27bfcb0298bff35419a6c9aeae9ecbc686e314f61122593ce50f",
            "f96ef44b7a5d8cc5fc55a6024d516c6ad585c8b7519628e7f38e9dc085c05546",
            61,
            5,
            818494,
        ),
        20261003: (
            "199178c7739cdf5d5682cf60963b7228c7e6f6bc1394dbf297f8fc8e4a3b386c",
            "c5d0a1b58453cfedb89ddd5bb890a4442c00c69d00a7647d52770eaf26240abd",
            60,
            3,
            818497,
        ),
        20261004: (
            "806a58004bfd3fecf3b97603639ec279b338967c64e99842df36f17e704f9cc4",
            "07897d6572e7925c0b540f6c20ce76f692ccb29f979844cb41b5a1945ce9fd6a",
            56,
            4,
            818500,
        ),
        20261005: (
            "a680defccd6e9973a65c9b34c37c44d62edfdf67a69ffcf260d7d59dbd9d270f",
            "7ccac25843a51d3c2417eaf69a5411510cfc0fe7f5d6876efb7acf6d30496a41",
            56,
            3,
            818501,
        ),
    }
    seed_one = None
    seed_one_metadata = None
    for seed, values in expected.items():
        trace, trace_hash, metadata = build_gate7_trace(
            REAL_PREPARED, seed, 3000, 100
        )
        counts = metadata["semantic_index_counts"]
        assert trace_hash == values[0]
        assert metadata["semantic_index_sha256"] == values[1]
        assert counts["direct_negative_pairs"] == values[2]
        assert counts["negative_component_derived_pairs"] == values[3]
        assert counts["unlabeled_cross_component_pairs"] == values[4]
        assert sum(row.reuse_opportunity for row in trace) == 1720
        if seed == 20261001:
            seed_one = trace
            seed_one_metadata = metadata

    assert seed_one is not None and seed_one_metadata is not None
    assert seed_one[1000].text_id == "dd259611437d8c81f32b2820"
    assert seed_one[1000].concept_id == "qqp-dd259611437d8c81f32b"
    selected = (
        seed_one_metadata["hot_concept_ids"]
        + seed_one_metadata["scan_concept_ids"]
    )
    index = build_gate7_semantic_index(REAL_PREPARED, selected)
    classification = classify_semantic_relation(
        index,
        True,
        "91ea52fe15ad205368fbaf12",
        "qqp-91ea52fe15ad205368fb",
        "dd259611437d8c81f32b2820",
        "qqp-dd259611437d8c81f32b",
    )
    assert classification.relation == RELATION_NEGATIVE_DIRECT
    assert classification.source_indices == (1804,)
