"""Regression tests for strict two-pass QQP v2 evaluation."""

import json
import hashlib
from pathlib import Path

import numpy as np
import pytest

from benchmarks.carma import qqp_v2


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_PREPARED = PROJECT_ROOT / "artifacts" / "qqp-full" / "prepared"
REAL_EMBEDDINGS = PROJECT_ROOT / "artifacts" / "qqp-full" / "embeddings"


def _write_jsonl(path: Path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, rows, vectors):
    prepared = tmp_path / "prepared"
    embeddings = tmp_path / "embeddings"
    output = tmp_path / "evaluation"
    prepared.mkdir()
    embeddings.mkdir()
    _write_jsonl(prepared / "pairs.jsonl", rows)
    text_ids = list(vectors)
    (embeddings / "text_ids.json").write_text(
        json.dumps(text_ids), encoding="utf-8"
    )
    np.save(
        embeddings / "embeddings.npy",
        np.asarray([vectors[text_id] for text_id in text_ids], dtype=np.float32),
    )
    return prepared, embeddings, output


def test_no_threshold_never_dereferences_poisoned_heldout_row(tmp_path, monkeypatch):
    prepared, embeddings, output = _fixture(
        tmp_path,
        [
            {
                "split": "calibration",
                "label": 0,
                "text_a_id": "a",
                "text_b_id": "a",
            },
            # No label or endpoints: pass one may read only this split value.
            {"split": "test", "raise_if_any_other_field_is_required": True},
        ],
        {"a": [1.0, 0.0]},
    )
    reads = 0
    original = qqp_v2.qqp._read_jsonl

    def counted(path):
        nonlocal reads
        reads += 1
        return original(path)

    monkeypatch.setattr(qqp_v2.qqp, "_read_jsonl", counted)
    result = qqp_v2.calibrate(prepared, embeddings, output)

    assert reads == 1
    assert result["status"] == "no_threshold_met_precision_gate"
    assert result["selected_threshold"] is None
    assert result["heldout_rows_discovered_by_split_only_scan"] == 1
    assert result["heldout_endpoints_resolved"] is False
    assert result["heldout_similarities_computed"] is False
    transcript = json.loads(
        (output / qqp_v2.SELECTION_ARTIFACT_NAME).read_text(encoding="utf-8")
    )
    assert transcript["heldout_row_fields_read_before_selection"] == ["split"]
    assert transcript["selection_uses_heldout"] is False
    assert transcript["heldout_similarity_count"] == 0


def test_selected_threshold_is_frozen_before_second_pass(tmp_path, monkeypatch):
    calibration_rows = [
        {
            "split": "calibration",
            "label": 1,
            "text_a_id": "a",
            "text_b_id": "b",
        }
        for _ in range(400)
    ]
    heldout_rows = [
        {"split": "test", "label": 1, "text_a_id": "a", "text_b_id": "b"},
        {"split": "test", "label": 0, "text_a_id": "a", "text_b_id": "c"},
    ]
    prepared, embeddings, output = _fixture(
        tmp_path,
        calibration_rows + heldout_rows,
        {"a": [1.0, 0.0], "b": [1.0, 0.0], "c": [0.0, 1.0]},
    )
    selection_exists_at_second_open = []
    original = qqp_v2.qqp._read_jsonl
    reads = 0

    def observed(path):
        nonlocal reads
        reads += 1
        if reads == 2:
            selection_exists_at_second_open.append(
                (output / qqp_v2.SELECTION_ARTIFACT_NAME).is_file()
            )
        return original(path)

    monkeypatch.setattr(qqp_v2.qqp, "_read_jsonl", observed)
    result = qqp_v2.calibrate(prepared, embeddings, output)

    assert reads == 2
    assert selection_exists_at_second_open == [True]
    assert result["status"] == "ok"
    assert result["selected_threshold"] == pytest.approx(0.8)
    assert result["test_pairs"] == 2
    assert result["test"]["precision"] == 1.0
    assert result["test"]["false_hit_rate"] == 0.0
    assert result["heldout_endpoints_resolved"] is True
    assert result["heldout_similarity_count"] == 2


def test_selected_path_rejects_same_count_input_mutation_between_passes(
    tmp_path, monkeypatch
):
    rows = [
        {
            "split": "calibration",
            "label": 1,
            "text_a_id": "a",
            "text_b_id": "b",
        }
        for _ in range(400)
    ] + [
        {"split": "test", "label": 1, "text_a_id": "a", "text_b_id": "b"}
    ]
    prepared, embeddings, output = _fixture(
        tmp_path,
        rows,
        {"a": [1.0, 0.0], "b": [1.0, 0.0]},
    )
    original = qqp_v2.qqp._read_jsonl
    reads = 0

    def mutate_before_second_pass(path):
        nonlocal reads
        reads += 1
        if reads == 2:
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return original(path)

    monkeypatch.setattr(qqp_v2.qqp, "_read_jsonl", mutate_before_second_pass)
    with pytest.raises(RuntimeError, match="inputs changed during held-out"):
        qqp_v2.calibrate(prepared, embeddings, output)


def test_calibration_metrics_include_false_hit_rate_and_wilson_bound(tmp_path):
    rows = [
        {"split": "calibration", "label": 1, "text_a_id": "a", "text_b_id": "b"},
        {"split": "calibration", "label": 0, "text_a_id": "a", "text_b_id": "b"},
    ]
    prepared, embeddings, output = _fixture(
        tmp_path,
        rows,
        {"a": [1.0, 0.0], "b": [1.0, 0.0]},
    )
    result = qqp_v2.calibrate(prepared, embeddings, output)
    table = (output / qqp_v2.THRESHOLD_TABLE_NAME).read_text(encoding="utf-8")

    assert result["status"] == "no_threshold_met_precision_gate"
    assert "false_hit_rate" in table.splitlines()[0]
    assert "wilson_precision_lower_one_sided_95" in table.splitlines()[0]
    assert "wilson_false_hit_rate_upper_one_sided_95" in table.splitlines()[0]
    first = table.splitlines()[1].split(",")
    header = table.splitlines()[0].split(",")
    assert float(first[header.index("false_hit_rate")]) == 0.5


def test_unknown_split_is_rejected_before_pair_scoring(tmp_path):
    prepared, embeddings, output = _fixture(
        tmp_path,
        [{"split": "validation", "label": 1}],
        {"a": [1.0, 0.0]},
    )
    with pytest.raises(ValueError, match="invalid split"):
        qqp_v2.calibrate(prepared, embeddings, output)


def test_retained_v2_no_selection_transcript_is_byte_pinned():
    evidence = PROJECT_ROOT / "docs" / "project" / "evidence"
    expected = {
        "qqp-v2-calibration-thresholds.csv": (
            "6cb322dc79b32fca9b3200ca16e1f28136589860b3e9aa558d70b64d44c7f038"
        ),
        "qqp-v2-threshold-selection.json": (
            "eb53e05e1816765fb93f2bed05bd01e2ce233c98879a20838f6853b97c1bd08c"
        ),
        "qqp-v2-result.json": (
            "3544e53f45f6fdd540a0489c4bc5a0a5750fcec96bb93204b9bc7df43016eca1"
        ),
    }
    for name, digest in expected.items():
        assert hashlib.sha256((evidence / name).read_bytes()).hexdigest() == digest

    selection = json.loads(
        (evidence / "qqp-v2-threshold-selection.json").read_text(encoding="utf-8")
    )
    result = json.loads(
        (evidence / "qqp-v2-result.json").read_text(encoding="utf-8")
    )
    assert selection["selected_threshold"] is None
    assert selection["selection_uses_heldout"] is False
    assert selection["heldout_endpoints_resolved"] is False
    assert selection["heldout_similarity_count"] == 0
    assert result["status"] == "no_threshold_met_precision_gate"
    assert result["selection_transcript"]["sha256"] == expected[
        "qqp-v2-threshold-selection.json"
    ]


def test_gate7_producer_auditor_contract_share_gate2_evidence_pins():
    from benchmarks.carma import gate7_v2_audit as auditor
    from benchmarks.carma import gate7_v2_onnx_integration_benchmark as producer

    expected = {
        "selection": "eb53e05e1816765fb93f2bed05bd01e2ce233c98879a20838f6853b97c1bd08c",
        "result": "3544e53f45f6fdd540a0489c4bc5a0a5750fcec96bb93204b9bc7df43016eca1",
        "thresholds": "6cb322dc79b32fca9b3200ca16e1f28136589860b3e9aa558d70b64d44c7f038",
    }
    assert producer.PINNED_GATE2_V2_SELECTION_SHA256 == expected["selection"]
    assert producer.PINNED_GATE2_V2_RESULT_SHA256 == expected["result"]
    assert producer.PINNED_GATE2_V2_THRESHOLDS_SHA256 == expected["thresholds"]
    assert auditor.PINNED_GATE2_V2_SELECTION_SHA256 == expected["selection"]
    assert auditor.PINNED_GATE2_V2_RESULT_SHA256 == expected["result"]
    assert auditor.PINNED_GATE2_V2_THRESHOLDS_SHA256 == expected["thresholds"]
    contract = (
        PROJECT_ROOT / "docs" / "project" / "gate7-v2-remediation-contract.md"
    ).read_text(encoding="utf-8")
    assert all(digest in contract for digest in expected.values())


@pytest.mark.skipif(
    not (REAL_PREPARED / "pairs.jsonl").is_file()
    or not (REAL_EMBEDDINGS / "embeddings.npy").is_file(),
    reason="full prepared QQP data and ONNX embeddings are local artifacts",
)
def test_retained_v2_evidence_reproduces_from_full_pinned_inputs(tmp_path):
    regenerated = tmp_path / "evaluation"
    result = qqp_v2.calibrate(REAL_PREPARED, REAL_EMBEDDINGS, regenerated)
    retained = PROJECT_ROOT / "docs" / "project" / "evidence"

    assert result["status"] == "no_threshold_met_precision_gate"
    assert result["selected_threshold"] is None
    for generated_name, retained_name in (
        (
            qqp_v2.THRESHOLD_TABLE_NAME,
            "qqp-v2-calibration-thresholds.csv",
        ),
        (qqp_v2.SELECTION_ARTIFACT_NAME, "qqp-v2-threshold-selection.json"),
        (qqp_v2.RESULT_NAME, "qqp-v2-result.json"),
    ):
        assert (regenerated / generated_name).read_bytes() == (
            retained / retained_name
        ).read_bytes()
