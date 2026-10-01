"""Regression tests preserving the completed-but-operationally-INVALID v3 run."""

import json
import shutil
from pathlib import Path

import pytest

from benchmarks.carma import gate7_v4_audit as gate7_audit


EXPECTED_FILES = {
    "SHA256SUMS": {
        "sha256": "ff1cf2e5fae7775880c261b8e2a83486275f3cfdea0718eb7e6155a1b10ab2b7",
        "bytes": 269,
    },
    "attempt-ledger.jsonl": {
        "sha256": "aa232ea1a818d01ed7ae7338fb33e44f19124a571f1d3c29a221d688af07888a",
        "bytes": 22296,
    },
    "gate7-preterminal-adjudication.json": {
        "sha256": "2ffbe237323ce38bfde39202993b6d12c9abb7a4d77c03f4b0c5137987ebaf00",
        "bytes": 292380,
    },
    "manifest.json": {
        "sha256": "ed38aefec5fa02528f9948729144bf6df5afa5d5f5b95f06be443b707089272b",
        "bytes": 696765,
    },
}
EXPECTED_ATTEMPT_ID = "20260827T222339037448Z-05543e34c9a5"
EXPECTED_ATTEMPT_DIRECTORY = "attempt-20260827T222332Z-36292"
EXPECTED_SOURCE_COMMIT = "05543e34c9a51e43d67ba483559d573bc3021dd0"
EXPECTED_SOURCE_TAG = "gate7d-onnx-v3-formal-source"
EXPECTED_CONTRACT = {
    "bytes": 48904,
    "path": "docs/project/gate7-v3-remediation-contract.md",
    "sha256": "70bd3eacc480d7a26a8d62d7a53f757fc1c45b09f955859d70c9e92aad85ccb2",
}


def _snapshot_root() -> Path:
    repository_root = Path(gate7_audit.__file__).resolve().parents[2]
    return repository_root / gate7_audit.V3_PRESERVATION_ROOT


def _copy_snapshot(project_root: Path) -> Path:
    source = _snapshot_root()
    destination = project_root / gate7_audit.V3_PRESERVATION_ROOT
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination)
    return destination


def test_v3_invalid_snapshot_identity_is_exact_and_nonclaimable():
    identity = gate7_audit._v3_preservation_identity()

    assert identity == {
        "schema_version": "carma-gate7-v3-preservation-v1",
        "snapshot_root": gate7_audit.V3_PRESERVATION_ROOT,
        "formal_root": "artifacts/gate7-v3-onnx-attempts",
        "attempt_id": EXPECTED_ATTEMPT_ID,
        "attempt_directory": EXPECTED_ATTEMPT_DIRECTORY,
        "operational_status": "invalid",
        "claimable": False,
        "terminal_present": False,
        "preterminal_status": "fail",
        "preterminal_claimable": True,
        "semantic_guardrail_status": "FAIL",
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "source_tag": EXPECTED_SOURCE_TAG,
        "contract_sha256": EXPECTED_CONTRACT["sha256"],
        "terminalization_defect": "manifest_contract_superset",
        "files": EXPECTED_FILES,
    }
    assert identity["files"] == gate7_audit.PINNED_V3_PRESERVATION_FILES


def test_v3_snapshot_preserves_dangling_start_and_contract_shape_defect():
    root = _snapshot_root()
    ledger = [
        json.loads(line)
        for line in (root / "attempt-ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    preterminal = json.loads(
        (root / "gate7-preterminal-adjudication.json").read_text(
            encoding="utf-8"
        )
    )

    assert [entry["event"] for entry in ledger] == ["PROTOCOL_GENESIS", "START"]
    assert all(entry["event"] != "TERMINAL" for entry in ledger)
    assert len(ledger) == 2

    start = ledger[1]
    assert start["attempt_id"] == EXPECTED_ATTEMPT_ID
    assert start["directory"] == EXPECTED_ATTEMPT_DIRECTORY
    assert start["head_commit"] == EXPECTED_SOURCE_COMMIT
    assert start["contract"] == EXPECTED_CONTRACT
    assert set(start["contract"]) == {"path", "sha256", "bytes"}

    manifest_contract = manifest["contract"]
    assert set(manifest_contract) == {
        "path",
        "sha256",
        "bytes",
        "publication_identity_unchanged",
    }
    assert manifest_contract["publication_identity_unchanged"] is True
    assert {
        key: manifest_contract[key] for key in EXPECTED_CONTRACT
    } == start["contract"]
    assert manifest_contract != start["contract"]

    anchor = manifest["formal_source_anchor"]
    assert manifest["attempt_id"] == EXPECTED_ATTEMPT_ID
    assert anchor["head_commit"] == EXPECTED_SOURCE_COMMIT
    assert anchor["tag_name"] == EXPECTED_SOURCE_TAG

    assert preterminal["attempt_id"] == EXPECTED_ATTEMPT_ID
    assert preterminal["audit_phase"] == "preterminal"
    assert preterminal["status"] == "fail"
    assert preterminal["claimable"] is True
    assert preterminal["error_count"] == 0
    assert preterminal["semantic_guardrail_status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["changed", "missing"])
def test_v3_identity_rejects_changed_or_missing_snapshot(tmp_path, mutation):
    snapshot = _copy_snapshot(tmp_path)
    manifest = snapshot / "manifest.json"
    if mutation == "changed":
        manifest.chmod(0o644)
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    else:
        manifest.unlink()

    with pytest.raises(RuntimeError, match="tracked Gate 7 v3 preservation file"):
        gate7_audit._v3_preservation_identity(tmp_path)
