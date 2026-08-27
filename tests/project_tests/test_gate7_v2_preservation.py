"""Regression tests preserving the completed-but-INVALID Gate 7 v2 lineage."""

import shutil
from pathlib import Path

import pytest

from benchmarks.carma import gate7_v3_audit as gate7_audit


def _copy_snapshot(project_root: Path) -> Path:
    repository_root = Path(gate7_audit.__file__).resolve().parents[2]
    source = repository_root / gate7_audit.V2_PRESERVATION_ROOT
    destination = project_root / gate7_audit.V2_PRESERVATION_ROOT
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination)
    return destination


def _genesis(v2_identity):
    entry = {
        "schema_version": gate7_audit.ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": gate7_audit.EXPERIMENT_ID,
        "event": "PROTOCOL_GENESIS",
        "sequence": 1,
        "recorded_at_utc": "2026-08-28T00:00:00+00:00",
        "v1_preservation": {
            "manifest_path": gate7_audit.V1_PRESERVATION_MANIFEST_PATH,
            "manifest_sha256": gate7_audit.PINNED_V1_PRESERVATION_MANIFEST_SHA256,
            "archive_path": gate7_audit.V1_PRESERVATION_ARCHIVE_PATH,
            "archive_sha256": gate7_audit.PINNED_V1_PRESERVATION_ARCHIVE_SHA256,
            "formal_root": gate7_audit.PINNED_V1_ROOT,
            "attempt_ledger_sha256": gate7_audit.PINNED_V1_LEDGER_SHA256,
            "terminal_attempt_id": gate7_audit.PINNED_V1_ATTEMPT_ID,
            "terminal_entry_sha256": gate7_audit.PINNED_V1_TERMINAL_ENTRY_SHA256,
            "failure_sha256": gate7_audit.PINNED_V1_FAILURE_SHA256,
        },
        "v2_preservation": v2_identity,
        "v3_contract": {
            "path": gate7_audit.DEFAULT_CONTRACT_PATH,
            "sha256": gate7_audit.PINNED_CONTRACT_SHA256,
        },
        "formal_source_anchor": {},
        "previous_entry_sha256": None,
        "entry_sha256": "a" * 64,
    }
    return entry


def test_v2_invalid_snapshot_identity_is_exact_and_nonclaimable():
    identity = gate7_audit._v2_preservation_identity()

    assert identity["schema_version"] == "carma-gate7-v2-preservation-v1"
    assert identity["status"] == "invalid"
    assert identity["claimable"] is False
    assert identity["terminal_present"] is False
    assert identity["invalid_error_count"] == 7
    assert identity["files"] == gate7_audit.PINNED_V2_PRESERVATION_FILES


@pytest.mark.parametrize("mutation", ["changed", "missing"])
def test_v3_genesis_rejects_changed_or_missing_v2_invalid_snapshot(
    tmp_path, monkeypatch, mutation
):
    identity = gate7_audit._v2_preservation_identity()
    snapshot = _copy_snapshot(tmp_path)
    manifest = snapshot / "manifest.json"
    if mutation == "changed":
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    else:
        manifest.unlink()

    fake_module = tmp_path / "benchmarks/carma/gate7_v3_audit.py"
    fake_module.parent.mkdir(parents=True)
    monkeypatch.setattr(gate7_audit, "__file__", str(fake_module))
    audit = gate7_audit._Audit()

    gate7_audit._validate_protocol_genesis(_genesis(identity), audit)

    assert any(error["code"] == "v2_preservation" for error in audit.errors)
    assert any(
        error["code"] == "attempt_ledger_genesis"
        and "v2 invalid attempt" in error["message"]
        for error in audit.errors
    )
