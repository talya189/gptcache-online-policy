"""Focused contract tests for the real-text Gate 7 trace builder."""

import dataclasses
import hashlib
import json

import pytest

from benchmarks.carma.gate7_trace import Gate7TraceRequest, build_gate7_trace


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


def _prepared(tmp_path, calibration_concepts=24, test_rows=None):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    text_rows = []
    calibration_rows = []
    for concept_index in range(calibration_concepts):
        concept_id = "calibration-concept-%02d" % concept_index
        text_id = "calibration-text-%02d" % concept_index
        text_rows.append(
            {"text_id": text_id, "text": "Real QQP question %02d?" % concept_index}
        )
        calibration_rows.append(
            {
                "split": "calibration",
                "text_a_id": text_id,
                "text_b_id": text_id,
                "concept_a": concept_id,
                "concept_b": concept_id,
                "label": 1,
            }
        )
    pair_rows = calibration_rows + list(test_rows or [])
    _write_jsonl(prepared / "pairs.jsonl", pair_rows)
    _write_jsonl(prepared / "texts.jsonl", text_rows)
    manifest = {
        "schema_version": "carma-qqp-v1",
        "archive_sha256": "a" * 64,
        "pairs_sha256": _sha256(prepared / "pairs.jsonl"),
        "texts_sha256": _sha256(prepared / "texts.jsonl"),
    }
    (prepared / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return prepared


def _refresh_manifest(prepared):
    manifest_path = prepared / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pairs_sha256"] = _sha256(prepared / "pairs.jsonl")
    manifest["texts_sha256"] = _sha256(prepared / "texts.jsonl")
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def test_trace_is_deterministic_real_text_and_has_exact_pollution_shape(tmp_path):
    prepared = _prepared(tmp_path)

    first, first_hash, metadata = build_gate7_trace(
        prepared, seed=17, requests=20, capacity=5
    )
    second, second_hash, second_metadata = build_gate7_trace(
        prepared, seed=17, requests=20, capacity=5
    )

    assert first == second
    assert first_hash == second_hash == metadata["trace_sha256"]
    assert second_metadata == metadata
    assert isinstance(first, tuple)
    assert all(isinstance(row, Gate7TraceRequest) for row in first)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first[0].text = "mutated"

    assert [row.index for row in first] == list(range(20))
    assert all(row.text.startswith("Real QQP question") for row in first)
    assert all(row.response_id == row.concept_id for row in first)
    assert all(row.response_payload.startswith("recorded-response:") for row in first)
    assert all(row.request_id.startswith("gate7b-17-") for row in first)
    assert metadata["phase_counts"] == {
        "warm": 6,
        "scan": 8,
        "return": 6,
    }
    assert metadata["hot_set_size"] == 4

    warm = [row for row in first if row.phase == "warm"]
    scan = [row for row in first if row.phase == "scan"]
    returned = [row for row in first if row.phase == "return"]
    hot_ids = set(metadata["hot_concept_ids"])
    scan_ids = [row.concept_id for row in scan]
    assert len(warm) == len(returned) == 6
    assert len(scan_ids) == len(set(scan_ids)) == 8
    assert {row.concept_id for row in warm} == hot_ids
    assert {row.concept_id for row in returned}.issubset(hot_ids)
    assert set(scan_ids).isdisjoint(hot_ids)

    assert metadata["selection_split"] == "calibration"
    assert metadata["source_pairs_sha256"] == _sha256(
        prepared / "pairs.jsonl"
    )
    assert metadata["source_texts_sha256"] == _sha256(
        prepared / "texts.jsonl"
    )
    assert metadata["source_manifest_sha256"] == _sha256(
        prepared / "manifest.json"
    )
    assert metadata["source_files"]["pairs.jsonl"][
        "manifest_declared_sha256"
    ] == metadata["source_pairs_sha256"]


def test_different_seed_changes_trace_selection(tmp_path):
    prepared = _prepared(tmp_path)

    first, first_hash, _ = build_gate7_trace(
        prepared, seed=101, requests=20, capacity=5
    )
    second, second_hash, _ = build_gate7_trace(
        prepared, seed=202, requests=20, capacity=5
    )

    assert first_hash != second_hash
    assert [row.concept_id for row in first] != [
        row.concept_id for row in second
    ]


def test_held_out_pair_rows_are_never_dereferenced_or_used_for_selection(tmp_path):
    poison = {"split": "test", "poison": "missing all selection fields"}
    prepared = _prepared(tmp_path, test_rows=[poison])
    before, before_hash, before_metadata = build_gate7_trace(
        prepared, seed=23, requests=20, capacity=5
    )

    pair_rows = [
        json.loads(line)
        for line in (prepared / "pairs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    pair_rows[-1] = {
        "split": "test",
        "text_a_id": "does-not-exist",
        "text_b_id": "also-does-not-exist",
        "concept_a": "held-out-only-a",
        "concept_b": "held-out-only-b",
    }
    _write_jsonl(prepared / "pairs.jsonl", pair_rows)
    _refresh_manifest(prepared)

    after, after_hash, after_metadata = build_gate7_trace(
        prepared, seed=23, requests=20, capacity=5
    )

    assert after == before
    assert after_hash == before_hash
    assert after_metadata["source_pairs_sha256"] != before_metadata[
        "source_pairs_sha256"
    ]
    assert all("held-out-only" not in row.concept_id for row in after)


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
        build_gate7_trace(
            prepared, seed=1, requests=requests, capacity=capacity
        )


def test_builder_requires_enough_distinct_calibration_concepts(tmp_path):
    prepared = _prepared(tmp_path, calibration_concepts=10)

    with pytest.raises(ValueError, match="needs at least 12"):
        build_gate7_trace(prepared, seed=1, requests=20, capacity=5)


def test_manifest_hash_mismatch_is_rejected(tmp_path):
    prepared = _prepared(tmp_path)
    manifest_path = prepared / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pairs_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="pairs_sha256 mismatch"):
        build_gate7_trace(prepared, seed=1, requests=20, capacity=5)
