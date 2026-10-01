"""Integrity checks for the immutable Gate 7 v1 invalid-attempt archive."""

import hashlib
import json
import tarfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    PROJECT_ROOT / "docs" / "project" / "evidence" / "gate7-v1-invalid-attempt.json"
)
ARCHIVE_PATH = (
    PROJECT_ROOT / "docs" / "project" / "evidence" / "gate7-v1-invalid-attempt.tar.gz"
)
PINNED_MANIFEST_SHA256 = (
    "b7ee1befd512d1582c0816152b9b87c1cbed91ac3a924bbbebc2bcedc3c6dee1"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ledger_entry_sha256(value):
    payload = dict(value)
    expected = payload.pop("entry_sha256")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return expected, _sha256(encoded)


def test_v1_invalid_attempt_archive_matches_complete_manifest():
    manifest_bytes = MANIFEST_PATH.read_bytes()
    manifest = json.loads(manifest_bytes)
    assert _sha256(manifest_bytes) == PINNED_MANIFEST_SHA256
    assert manifest["schema_version"] == "carma-gate7-v1-preservation-v1"
    assert manifest["kind"] == "gate7_v1_invalid_attempt_preservation"
    assert manifest["attempt"]["status"] == "invalid"

    archive_bytes = ARCHIVE_PATH.read_bytes()
    assert len(archive_bytes) == manifest["archive"]["bytes"]
    assert _sha256(archive_bytes) == manifest["archive"]["sha256"]

    expected_files = manifest["files"]
    with tarfile.open(ARCHIVE_PATH, "r:gz") as bundle:
        regular_members = {
            member.name: member
            for member in bundle.getmembers()
            if member.isfile()
        }
        assert set(regular_members) == set(expected_files)
        retained = {}
        for name, identity in expected_files.items():
            member = regular_members[name]
            assert not member.issym() and not member.islnk()
            stream = bundle.extractfile(member)
            assert stream is not None
            data = stream.read()
            assert len(data) == identity["bytes"]
            assert _sha256(data) == identity["sha256"]
            if "rows" in identity:
                assert len(data.splitlines()) == identity["rows"]
            retained[name] = data

    ledger_name = "gate7-onnx-attempts/attempt-ledger.jsonl"
    ledger = [json.loads(line) for line in retained[ledger_name].splitlines()]
    assert [row["event"] for row in ledger] == ["START", "TERMINAL"]
    assert [row["sequence"] for row in ledger] == [1, 2]
    assert ledger[0]["previous_entry_sha256"] is None
    assert ledger[1]["previous_entry_sha256"] == ledger[0]["entry_sha256"]
    for row in ledger:
        expected, observed = _ledger_entry_sha256(row)
        assert observed == expected
    terminal = ledger[-1]
    assert terminal["attempt_id"] == manifest["attempt"]["attempt_id"]
    assert terminal["entry_sha256"] == manifest["attempt"]["terminal_entry_sha256"]
    assert terminal["terminal_record_sha256"] == manifest["attempt"]["failure_sha256"]
    assert terminal["preterminal_report_status"] == "invalid"
    assert terminal["preterminal_report_claimable"] is False
