"""Identity and status checks for the immutable Gate 7 v4 record."""

import hashlib
import json
from pathlib import Path


ROOT = (
    Path(__file__).resolve().parents[2]
    / "artifacts"
    / "samples"
    / "verification"
    / "gate7-v4-invalid"
)
FILES = {
    "SHA256SUMS": ("19594e2b2063dce2a5f6233b44083d9fd924a727d33fe8dcfd4780dad3366f60", 466),
    "attempt-1-failure.json": ("f812d9c1e75af045bcb4317a9f17eafddaaea64e1b779721e908981e940627e0", 68298),
    "attempt-2-adjudication.json": ("5ee74b8750454ec15f0e21c06b7a5ae71b9348161b4f9b127bb0c164a2180274", 277831),
    "attempt-2-manifest.json": ("f1d264807ef2d98180e70458d385aea452e485e1f82469ea72177daa8a427519", 707385),
    "attempt-2-preterminal-adjudication.json": ("223964fee9e0ced8945b4c7d8d2c09a8f5602adbd7f82b23e84eeac5541802a1", 297955),
    "attempt-ledger.jsonl": ("5d62271565bdc4ace40c2b083f2b1ee4b605ebfc2f608a4d19866268693eb5c1", 50838),
}


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v4_preservation_files_are_byte_identical():
    for name, (expected_hash, expected_bytes) in FILES.items():
        path = ROOT / name
        assert path.is_file()
        assert not path.is_symlink()
        assert path.stat().st_size == expected_bytes
        assert _sha256(path) == expected_hash


def test_v4_attempts_remain_terminal_invalid_and_numerically_failed():
    ledger = [
        json.loads(line)
        for line in (ROOT / "attempt-ledger.jsonl").read_text().splitlines()
    ]
    assert [row["event"] for row in ledger] == [
        "PROTOCOL_GENESIS",
        "START",
        "TERMINAL",
        "START",
        "TERMINAL",
    ]

    first = json.loads((ROOT / "attempt-1-failure.json").read_text())
    second = json.loads((ROOT / "attempt-2-adjudication.json").read_text())
    assert first["status"] == "invalid"
    assert second["status"] == "invalid"
    assert {error["code"] for error in second["errors"]} == {
        "entrypoint_attestation",
        "warmup_artifacts",
        "resource_cadence",
    }
    assert all(
        not row["checks"]["paired_policy_exclusive_p95_delta"]
        for row in second["seed_adjudication"].values()
    )
    assert (
        second["seed_adjudication"]["20261105"]["checks"][
            "post_embedding_p95_delta"
        ]
        is False
    )
