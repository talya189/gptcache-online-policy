#!/usr/bin/env python3
"""Fail-closed, stdlib-only launcher for the formal Gate 7 v3 protocol.

The benchmark producer imports native and third-party modules at module import
time.  Consequently, dependency verification performed by the producer alone
would be too late to prevent a Python startup hook or shadow module from
running.  This launcher is invoked with ``python -S -P`` and verifies the
locked virtual environment *before* adding site-packages or the repository to
``sys.path``.  It never calls :mod:`site`, so verified ``.pth`` files are
attested but not executed.

Invocation::

    .venv/bin/python -S -P scripts/gate7_v3_isolated_bootstrap.py \
        --role parent -- /absolute/path/to/producer.py [producer arguments]

The roles ``child`` and ``auditor`` select the only other permitted launch
paths.  A compact observation is attached to ``sys`` and its canonical digest
is exported for the target.  The target must independently require that
runtime sentinel; environment variables alone are not proof of bootstrap use.
"""

from __future__ import annotations

import base64
import csv
import fcntl
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import runpy
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, Sequence, Set, Tuple
from urllib.parse import unquote, urlparse


BOOTSTRAP_SCHEMA = "carma-gate7-isolated-bootstrap-v2"
TERMINAL_INTENT_SCHEMA = "carma-gate7-terminal-intent-v2"
BOOTSTRAP_COMPLETION_SCHEMA = "carma-gate7-bootstrap-completion-v2"
ATTEMPT_LEDGER_SCHEMA = "carma-gate7-attempt-ledger-v4"
EVIDENCE_SCHEMA = "carma-gate7-onnx-v3"
FORMAL_SOURCE_ANCHOR_SCHEMA = "carma-gate7-formal-source-anchor-v1"
FORMAL_ENTRYPOINT_SCHEMA = "carma-gate7-formal-entrypoint-attestation-v1"
EXPERIMENT_ID = "gate7d-onnx-v3"
FORMAL_ATTEMPT_ROOT = Path("artifacts/gate7-v3-onnx-attempts")
PRETERMINAL_REPORT_NAME = "gate7-preterminal-adjudication.json"
ADJUDICATION_EXIT_STATUSES = {
    "pass": 0,
    "fail": 1,
    "pending": 2,
    "invalid": 3,
}
PINNED_CPYTHON_VERSION = "3.12.13"
PINNED_LOCK_SHA256 = (
    "8723b1875ff08ff16691e7b9d7166fc6b089fb362c8d27f2f43dde34be04d8ad"
)
PINNED_LOCK_PIN_COUNT = 50
PINNED_LOCK_PIN_MAP_SHA256 = (
    "a9ad2cf1ad4db90d5c43d01eaaad84b739153616e31d5404dd817b47f195a759"
)
PINNED_LOCKED_RECORD_AGGREGATE_SHA256 = (
    "1b59e03f6a6d52a8b64adf8f11eb8ed36c40f807d53248965a00c733f8dca2c3"
)
PINNED_LOCKED_HASHED_FILE_COUNT = 10270
PINNED_LOCKED_HASHED_BYTES = 362595746
LOCAL_GPTCACHE_VERSION = "0.1.44"
PINNED_CONTRACT_SHA256 = "70bd3eacc480d7a26a8d62d7a53f757fc1c45b09f955859d70c9e92aad85ccb2"
FORMAL_SOURCE_TAG = "gate7d-onnx-v3-formal-source"
FORMAL_SUBMISSION_REMOTE = "submission"
FORMAL_SUBMISSION_URL = (
    "https://github.com/MatanGoldfarB/gptcache-online-policy.git"
)
FORMAL_CONTRACT_PATH = "docs/project/gate7-v3-remediation-contract.md"
FORMAL_SOURCE_TAG_PAYLOAD_PATH = "source/formal-source-tag.raw"
V1_PRESERVATION_MANIFEST = Path(
    "docs/project/evidence/gate7-v1-invalid-attempt.json"
)
V1_PRESERVATION_ARCHIVE = Path(
    "docs/project/evidence/gate7-v1-invalid-attempt.tar.gz"
)
PINNED_V1_PRESERVATION_MANIFEST_SHA256 = (
    "b7ee1befd512d1582c0816152b9b87c1cbed91ac3a924bbbebc2bcedc3c6dee1"
)
PINNED_V1_PRESERVATION_ARCHIVE_SHA256 = (
    "cb326101dcc8323575be376d923630cfda6191c2e7adbc3b7fd6b01acf6b9147"
)
PINNED_V1_PRESERVATION_ARCHIVE_BYTES = 484539
PINNED_V1_FAILURE_SHA256 = (
    "41a7af508e769f24a91e72b221906fd7e50c6d20176032a9d58590ba9feb9f12"
)
PINNED_V1_LEDGER_SHA256 = (
    "96539252388659e779ac09014895886156e9f405020ce278527b82bfea22aeb7"
)
V2_PRESERVATION_ROOT = Path(
    "artifacts/samples/verification/gate7-v2-invalid"
)
PINNED_V2_PRESERVATION_FILES = {
    "SHA256SUMS": {
        "sha256": "069ee7e3951ac3a4f015841fe0a020168f501c2cc38e19b3505f87f7c619cb07",
        "bytes": 269,
    },
    "attempt-ledger.jsonl": {
        "sha256": "84eee7963f4e5c27f8d962e31ddcdcec9779b1cd463a8952841946ffc8557f0c",
        "bytes": 21346,
    },
    "gate7-preterminal-adjudication.json": {
        "sha256": "e3e8df2787049d3b1ea4e622e7666502b715ae572431b4f8d4e4b213c12dd39b",
        "bytes": 293720,
    },
    "manifest.json": {
        "sha256": "7574127cce8f1c72e87a1528dc6709947860178f43667a92add155e89e1f1cf8",
        "bytes": 695528,
    },
}
PINNED_V2_ATTEMPT_ID = "20260827T131743602373Z-557c6ac0578c"
PINNED_V2_ATTEMPT_DIRECTORY = "attempt-20260827T131736Z-1784"
PINNED_V2_SOURCE_COMMIT = "557c6ac0578cb6b77c5ae51595b49abdc0407e10"
PINNED_V2_SOURCE_TAG = "gate7c-onnx-v2-formal-source"
FORMAL_RUNTIME_ROOTS = ("gptcache", "benchmarks")
FORMAL_CONTROL_PATHS = (
    "scripts/gate7_v3_isolated_bootstrap.py",
    "scripts/run_gate7_v3_onnx_integration_benchmark.sh",
    FORMAL_CONTRACT_PATH,
)
PINNED_GIT_EXECUTABLE = Path("/usr/bin/git")
PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY = {
    "record_sha256": (
        "873280782d16563eef2982efbead80814577c01aea0253cea060d7cb3cc5b140"
    ),
    "hashed_file_count": 11,
    "hashed_bytes": 29856,
    "hashed_files_sha256": (
        "29bf983930625eacdddedcd24953d1849e9ebf923f233c09bd38506d67391b91"
    ),
}

REQUIRED_ENVIRONMENT = {
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "PYTHONSAFEPATH": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "CARMA_GATE7_WRAPPER_SHELL": "gate7d-shell-v1",
    "CARMA_GATE7_FORMAL_ENTRYPOINT": "gate7d-wrapper-v1",
    "CARMA_ONNX_WORKERS": "1",
    "CARMA_ONNX_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "LC_CTYPE": "C.UTF-8",
    "TZ": "UTC",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
}
REQUIRED_DYNAMIC_ENVIRONMENT = (
    "HOME",
    "TMPDIR",
    "PYTHONPYCACHEPREFIX",
    "HF_HOME",
    "HF_HUB_CACHE",
    "CARMA_GATE7_LAUNCH_MODE",
    "CARMA_GATE7_WRAPPER_SHELL_PROFILE",
    "CARMA_GATE7_WRAPPER_SHELL_HOME",
    "CARMA_GATE7_WRAPPER_SHELL_PWD",
    "CARMA_GATE7_WRAPPER_SHELL_TMPDIR",
    "CARMA_GATE7_WRAPPER_SHELL_SHLVL",
    "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF",
    "CARMA_GATE7_WRAPPER_PID",
    "CARMA_GATE7_WRAPPER_PATH",
)
PERMITTED_PREBOOT_ENVIRONMENT = frozenset(
    set(REQUIRED_ENVIRONMENT)
    | set(REQUIRED_DYNAMIC_ENVIRONMENT)
    | {"__CF_USER_TEXT_ENCODING"}
)
FORBIDDEN_LOADER_PREFIXES = ("DYLD_", "LD_")
FORBIDDEN_BOOTSTRAP_PREFIX = "CARMA_GATE7_BOOTSTRAP_"
class BootstrapError(RuntimeError):
    """Raised when a formal process cannot establish its import boundary."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_digest_bytes(path: Path) -> bytes:
    return bytes.fromhex(_sha256_file(path))


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_mapping_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _canonical_observation_baseline(
    observation: Mapping[str, Any],
) -> Tuple[bytes, Dict[str, Any]]:
    """Return an immutable canonical seal and a private deep observation copy."""

    try:
        canonical_bytes = _canonical_json_bytes(observation)
        baseline = json.loads(canonical_bytes)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BootstrapError("bootstrap observation is not canonical JSON") from exc
    if not isinstance(baseline, dict):
        raise BootstrapError("bootstrap observation is not a JSON object")
    attestation_sha256 = baseline.get("attestation_sha256")
    unsigned = dict(baseline)
    unsigned.pop("attestation_sha256", None)
    if (
        not isinstance(attestation_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", attestation_sha256) is None
        or _canonical_mapping_sha256(unsigned) != attestation_sha256
    ):
        raise BootstrapError("bootstrap observation digest is inconsistent")
    return canonical_bytes, baseline


def _valid_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _auditor_identity_matches_source(
    auditor_identity: Any, source_identity: Any
) -> bool:
    """Compare a pathful intent identity with a path-keyed source entry."""

    return bool(
        isinstance(auditor_identity, Mapping)
        and set(auditor_identity) == {"path", "sha256", "bytes"}
        and auditor_identity.get("path") == "benchmarks/carma/gate7_v3_audit.py"
        and _valid_sha256(auditor_identity.get("sha256"))
        and isinstance(auditor_identity.get("bytes"), int)
        and not isinstance(auditor_identity.get("bytes"), bool)
        and auditor_identity.get("bytes", 0) > 0
        and isinstance(source_identity, Mapping)
        and set(source_identity) == {"sha256", "bytes"}
        and source_identity
        == {
            "sha256": auditor_identity.get("sha256"),
            "bytes": auditor_identity.get("bytes"),
        }
    )


def _deep_canonical_mapping(value: Any, label: str) -> Dict[str, Any]:
    """Return an independent JSON mapping without trusting caller mutability."""

    if not isinstance(value, Mapping):
        raise BootstrapError("%s is not a mapping" % label)
    try:
        result = json.loads(_canonical_json_bytes(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BootstrapError("%s is not canonical JSON" % label) from exc
    if not isinstance(result, dict):
        raise BootstrapError("%s is not a JSON object" % label)
    return result


def _mapping_self_hash(value: Mapping[str, Any], field: str) -> str:
    unsigned = dict(value)
    unsigned.pop(field, None)
    return _canonical_mapping_sha256(unsigned)


def _regular_direct_file(parent: Path, name: str) -> Path:
    if (
        not isinstance(name, str)
        or not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise BootstrapError("terminal evidence names an unsafe file")
    path = parent / name
    if (
        path.is_symlink()
        or not path.is_file()
        or path.resolve().parent != parent.resolve()
    ):
        raise BootstrapError("terminal evidence is not a direct regular file")
    return path


def _artifact_identity(path: Path) -> Dict[str, Any]:
    return {
        "sha256": _sha256_file(path),
        "bytes": int(path.stat().st_size),
    }


def _v1_preservation_identity(project_root: Path) -> Dict[str, Any]:
    """Verify and identify the immutable v1 failure preservation."""

    manifest_path = Path(project_root) / V1_PRESERVATION_MANIFEST
    archive_path = Path(project_root) / V1_PRESERVATION_ARCHIVE
    for path, label in (
        (manifest_path, "manifest"),
        (archive_path, "archive"),
    ):
        if path.is_symlink() or not path.is_file():
            raise BootstrapError("the preserved Gate 7 v1 %s is missing or unsafe" % label)
    manifest_identity = _artifact_identity(manifest_path)
    archive_identity = _artifact_identity(archive_path)
    if manifest_identity["sha256"] != PINNED_V1_PRESERVATION_MANIFEST_SHA256:
        raise BootstrapError("the preserved Gate 7 v1 identity manifest changed")
    if archive_identity != {
        "sha256": PINNED_V1_PRESERVATION_ARCHIVE_SHA256,
        "bytes": PINNED_V1_PRESERVATION_ARCHIVE_BYTES,
    }:
        raise BootstrapError("the preserved Gate 7 v1 evidence archive changed")
    declaration = _read_json_mapping(
        manifest_path, "preserved Gate 7 v1 identity manifest"
    )
    archive = declaration.get("archive")
    attempt = declaration.get("attempt")
    files = declaration.get("files")
    ledger = (
        files.get("gate7-onnx-attempts/attempt-ledger.jsonl")
        if isinstance(files, dict)
        else None
    )
    if (
        declaration.get("schema_version") != "carma-gate7-v1-preservation-v1"
        or not isinstance(archive, dict)
        or archive.get("sha256") != PINNED_V1_PRESERVATION_ARCHIVE_SHA256
        or archive.get("bytes") != PINNED_V1_PRESERVATION_ARCHIVE_BYTES
        or not isinstance(attempt, dict)
        or attempt.get("failure_sha256") != PINNED_V1_FAILURE_SHA256
        or not isinstance(ledger, dict)
        or ledger.get("sha256") != PINNED_V1_LEDGER_SHA256
    ):
        raise BootstrapError("the preserved Gate 7 v1 identity is inconsistent")
    return {
        "manifest_path": V1_PRESERVATION_MANIFEST.as_posix(),
        "manifest_sha256": manifest_identity["sha256"],
        "archive_path": V1_PRESERVATION_ARCHIVE.as_posix(),
        "archive_sha256": archive_identity["sha256"],
        "formal_root": "artifacts/gate7-onnx-attempts",
        "attempt_ledger_sha256": PINNED_V1_LEDGER_SHA256,
        "terminal_attempt_id": attempt.get("attempt_id"),
        "terminal_entry_sha256": attempt.get("terminal_entry_sha256"),
        "failure_sha256": PINNED_V1_FAILURE_SHA256,
    }


def _v2_preservation_identity(project_root: Path) -> Dict[str, Any]:
    """Verify and identify the tracked snapshot of the invalid v2 attempt."""

    root = Path(project_root) / V2_PRESERVATION_ROOT
    if root.is_symlink() or not root.is_dir():
        raise BootstrapError("the tracked Gate 7 v2 preservation root is missing")
    resolved_root = root.resolve()
    observed: Dict[str, Any] = {}
    for name, expected in PINNED_V2_PRESERVATION_FILES.items():
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve().parent != resolved_root
        ):
            raise BootstrapError("a tracked Gate 7 v2 preservation file is unsafe")
        identity = _artifact_identity(path)
        if identity != expected:
            raise BootstrapError("a tracked Gate 7 v2 preservation file changed")
        observed[name] = identity

    checksum_payload = "".join(
        "%s  %s\n" % (PINNED_V2_PRESERVATION_FILES[name]["sha256"], name)
        for name in (
            "attempt-ledger.jsonl",
            "gate7-preterminal-adjudication.json",
            "manifest.json",
        )
    )
    if (root / "SHA256SUMS").read_text(encoding="utf-8") != checksum_payload:
        raise BootstrapError("the tracked Gate 7 v2 checksum declaration changed")
    try:
        ledger = [
            json.loads(line)
            for line in (root / "attempt-ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads(
            (root / "gate7-preterminal-adjudication.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("the tracked Gate 7 v2 preservation is unreadable") from exc
    anchor = manifest.get("formal_source_anchor") if isinstance(manifest, dict) else None
    if (
        len(ledger) != 2
        or not all(isinstance(entry, dict) for entry in ledger)
        or [entry.get("event") for entry in ledger] != ["PROTOCOL_GENESIS", "START"]
        or ledger[-1].get("attempt_id") != PINNED_V2_ATTEMPT_ID
        or ledger[-1].get("directory") != PINNED_V2_ATTEMPT_DIRECTORY
        or ledger[-1].get("head_commit") != PINNED_V2_SOURCE_COMMIT
        or not isinstance(manifest, dict)
        or manifest.get("attempt_id") != PINNED_V2_ATTEMPT_ID
        or not isinstance(anchor, dict)
        or anchor.get("head_commit") != PINNED_V2_SOURCE_COMMIT
        or anchor.get("tag_name") != PINNED_V2_SOURCE_TAG
        or not isinstance(report, dict)
        or report.get("attempt_id") != PINNED_V2_ATTEMPT_ID
        or report.get("status") != "invalid"
        or report.get("claimable") is not False
        or report.get("error_count") != 7
        or not isinstance(report.get("errors"), list)
        or len(report["errors"]) != 7
    ):
        raise BootstrapError("the tracked Gate 7 v2 preservation is inconsistent")
    return {
        "schema_version": "carma-gate7-v2-preservation-v1",
        "snapshot_root": V2_PRESERVATION_ROOT.as_posix(),
        "formal_root": "artifacts/gate7-v2-onnx-attempts",
        "attempt_id": PINNED_V2_ATTEMPT_ID,
        "attempt_directory": PINNED_V2_ATTEMPT_DIRECTORY,
        "status": "invalid",
        "claimable": False,
        "terminal_present": False,
        "invalid_error_count": 7,
        "source_commit": PINNED_V2_SOURCE_COMMIT,
        "source_tag": PINNED_V2_SOURCE_TAG,
        "files": observed,
    }


def _read_json_mapping(path: Path, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("%s is unreadable" % label) from exc
    if not isinstance(value, dict):
        raise BootstrapError("%s is not a JSON object" % label)
    return value


def _read_canonical_attempt_ledger(
    ledger_path: Path,
) -> Tuple[list[Dict[str, Any]], bytes]:
    """Validate the canonical hash chain needed for the bootstrap append."""

    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise BootstrapError("formal attempt ledger is missing or unsafe")
    try:
        payload = ledger_path.read_bytes()
    except OSError as exc:
        raise BootstrapError("formal attempt ledger is unreadable") from exc
    if not payload or not payload.endswith(b"\n"):
        raise BootstrapError("formal attempt ledger is incomplete")
    entries: list[Dict[str, Any]] = []
    previous: Any = None
    for sequence, raw_line in enumerate(payload.splitlines(keepends=True), start=1):
        try:
            entry = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BootstrapError("formal attempt ledger is not valid JSONL") from exc
        if not isinstance(entry, dict):
            raise BootstrapError("formal attempt ledger row is not an object")
        if raw_line != _canonical_json_bytes(entry) + b"\n":
            raise BootstrapError("formal attempt ledger is not canonical JSONL")
        entry_sha256 = entry.get("entry_sha256")
        if (
            entry.get("schema_version") != ATTEMPT_LEDGER_SCHEMA
            or entry.get("experiment_id") != EXPERIMENT_ID
            or not isinstance(entry.get("sequence"), int)
            or isinstance(entry.get("sequence"), bool)
            or entry.get("sequence") != sequence
            or entry.get("previous_entry_sha256") != previous
            or not _valid_sha256(entry_sha256)
            or _mapping_self_hash(entry, "entry_sha256") != entry_sha256
            or entry.get("event") not in (
                "PROTOCOL_GENESIS",
                "START",
                "TERMINAL",
            )
        ):
            raise BootstrapError("formal attempt ledger hash chain is invalid")
        entries.append(entry)
        previous = entry_sha256
    return entries, payload


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


def _attempt_id_for(started_at_utc: datetime, head_commit: str) -> str:
    return "%s-%s" % (
        started_at_utc.strftime("%Y%m%dT%H%M%S%fZ"),
        head_commit[:12],
    )


def _formal_source_anchor_is_structurally_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    remote = value.get("remote")
    return bool(
        set(value)
        == {
            "schema_version",
            "tag_name",
            "remote_name",
            "object_format",
            "tag_object_type",
            "tag_object_id",
            "peeled_commit",
            "head_commit",
            "tree_id",
            "tag_payload_sha256",
            "tag_payload_bytes",
            "annotation",
            "remote",
            "retained_tag_payload_path",
        }
        and value.get("schema_version") == FORMAL_SOURCE_ANCHOR_SCHEMA
        and value.get("tag_name") == FORMAL_SOURCE_TAG
        and value.get("remote_name") == FORMAL_SUBMISSION_REMOTE
        and value.get("object_format") == "sha1"
        and value.get("tag_object_type") == "tag"
        and _valid_sha1(value.get("tag_object_id"))
        and _valid_sha1(value.get("peeled_commit"))
        and value.get("head_commit") == value.get("peeled_commit")
        and _valid_sha1(value.get("tree_id"))
        and _valid_sha256(value.get("tag_payload_sha256"))
        and isinstance(value.get("tag_payload_bytes"), int)
        and not isinstance(value.get("tag_payload_bytes"), bool)
        and value["tag_payload_bytes"] > 0
        and value.get("annotation")
        == {
            "experiment_id": EXPERIMENT_ID,
            "contract_sha256": PINNED_CONTRACT_SHA256,
        }
        and remote
        == {
            "fetch_urls": [FORMAL_SUBMISSION_URL],
            "push_urls": [FORMAL_SUBMISSION_URL],
            "tag_object_id": value.get("tag_object_id"),
            "peeled_commit": value.get("peeled_commit"),
        }
        and value.get("retained_tag_payload_path")
        == FORMAL_SOURCE_TAG_PAYLOAD_PATH
    )


def _valid_attempt_directory(formal_root: Path, value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        return False
    candidate = formal_root / value
    return bool(
        candidate.is_dir()
        and not candidate.is_symlink()
        and candidate.resolve().parent == formal_root.resolve()
    )


def _power_observation_is_structurally_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "observed_at_utc",
        "available",
        "plugged",
        "percent",
        "seconds_left",
    }:
        return False
    available = value.get("available")
    plugged = value.get("plugged")
    percent = value.get("percent")
    seconds_left = value.get("seconds_left")
    if (
        _parse_utc_timestamp(value.get("observed_at_utc")) is None
        or not isinstance(available, bool)
        or (plugged is not None and not isinstance(plugged, bool))
        or (
            percent is not None
            and (
                not isinstance(percent, (int, float))
                or isinstance(percent, bool)
            )
        )
        or (
            seconds_left is not None
            and (
                not isinstance(seconds_left, int)
                or isinstance(seconds_left, bool)
            )
        )
    ):
        return False
    return bool(
        (
            available
            and isinstance(plugged, bool)
            and percent is not None
        )
        or (
            not available
            and plugged is None
            and percent is None
            and seconds_left is None
        )
    )


def _validated_terminalizable_start(
    entries: Sequence[Mapping[str, Any]],
    project_root: Path,
    formal_root: Path,
) -> Dict[str, Any]:
    """Validate the exact producer ledger shape and return its open START."""

    genesis_fields = {
        "schema_version",
        "experiment_id",
        "event",
        "sequence",
        "recorded_at_utc",
        "v1_preservation",
        "v2_preservation",
        "v3_contract",
        "formal_source_anchor",
        "previous_entry_sha256",
        "entry_sha256",
    }
    genesis = entries[0] if entries else None
    if (
        not isinstance(genesis, Mapping)
        or set(genesis) != genesis_fields
        or genesis.get("event") != "PROTOCOL_GENESIS"
        or _parse_utc_timestamp(genesis.get("recorded_at_utc")) is None
        or genesis.get("v1_preservation")
        != _v1_preservation_identity(project_root)
        or genesis.get("v2_preservation")
        != _v2_preservation_identity(project_root)
        or genesis.get("v3_contract")
        != {
            "path": FORMAL_CONTRACT_PATH,
            "sha256": PINNED_CONTRACT_SHA256,
        }
        or not _formal_source_anchor_is_structurally_valid(
            genesis.get("formal_source_anchor")
        )
    ):
        raise BootstrapError("formal protocol genesis is malformed")

    start_fields = {
        "schema_version",
        "experiment_id",
        "sequence",
        "event",
        "attempt_id",
        "started_at_utc",
        "head_commit",
        "directory",
        "prior_attempt_count",
        "prior_attempts_sha256",
        "contract",
        "source_snapshot_sha256",
        "formal_source_anchor",
        "dependency_attestation_sha256",
        "environment_sha256",
        "entrypoint_attestation",
        "pre_start_power",
        "retained_inputs",
        "previous_entry_sha256",
        "entry_sha256",
    }
    terminal_fields = {
        "schema_version",
        "experiment_id",
        "sequence",
        "event",
        "attempt_id",
        "started_at_utc",
        "head_commit",
        "directory",
        "formal_source_anchor",
        "dependency_attestation_sha256",
        "environment_sha256",
        "terminal_record",
        "terminal_record_sha256",
        "terminal_record_bytes",
        "preterminal_report",
        "preterminal_report_sha256",
        "preterminal_report_bytes",
        "preterminal_report_status",
        "preterminal_report_claimable",
        "auditor_identity",
        "bootstrap_completion",
        "previous_entry_sha256",
        "entry_sha256",
    }
    starts: Dict[str, Mapping[str, Any]] = {}
    directories: Set[str] = set()
    open_start: Mapping[str, Any] | None = None
    start_count = 0
    genesis_anchor = genesis["formal_source_anchor"]
    for entry in entries[1:]:
        started_at = _parse_utc_timestamp(entry.get("started_at_utc"))
        head_commit = entry.get("head_commit")
        attempt_id = entry.get("attempt_id")
        directory = entry.get("directory")
        if (
            started_at is None
            or not _valid_sha1(head_commit)
            or attempt_id != _attempt_id_for(started_at, head_commit)
            or not _valid_attempt_directory(formal_root, directory)
        ):
            raise BootstrapError("formal attempt ledger identity is malformed")
        if entry.get("event") == "START":
            contract = entry.get("contract")
            entrypoint = entry.get("entrypoint_attestation")
            pre_start_power = entry.get("pre_start_power")
            if (
                set(entry) != start_fields
                or open_start is not None
                or attempt_id in starts
                or directory in directories
                or not isinstance(entry.get("prior_attempt_count"), int)
                or isinstance(entry.get("prior_attempt_count"), bool)
                or entry.get("prior_attempt_count") != start_count
                or not _valid_sha256(entry.get("prior_attempts_sha256"))
                or not isinstance(contract, dict)
                or set(contract) != {"path", "sha256", "bytes"}
                or contract.get("path") != FORMAL_CONTRACT_PATH
                or contract.get("sha256") != PINNED_CONTRACT_SHA256
                or not isinstance(contract.get("bytes"), int)
                or isinstance(contract.get("bytes"), bool)
                or contract["bytes"] <= 0
                or not _valid_sha256(entry.get("source_snapshot_sha256"))
                or entry.get("formal_source_anchor") != genesis_anchor
                or not _valid_sha256(entry.get("dependency_attestation_sha256"))
                or not _valid_sha256(entry.get("environment_sha256"))
                or not isinstance(entrypoint, dict)
                or entrypoint.get("schema_version") != FORMAL_ENTRYPOINT_SCHEMA
                or not _power_observation_is_structurally_valid(pre_start_power)
                or not isinstance(entry.get("retained_inputs"), dict)
            ):
                raise BootstrapError("formal attempt ledger START is malformed")
            starts[str(attempt_id)] = entry
            directories.add(str(directory))
            open_start = entry
            start_count += 1
            continue

        if set(entry) != terminal_fields or open_start is None:
            raise BootstrapError("formal attempt ledger TERMINAL is malformed")
        start = open_start
        if (
            entry.get("attempt_id") != start.get("attempt_id")
            or entry.get("started_at_utc") != start.get("started_at_utc")
            or entry.get("head_commit") != start.get("head_commit")
            or entry.get("directory") != start.get("directory")
            or entry.get("formal_source_anchor")
            != start.get("formal_source_anchor")
            or entry.get("dependency_attestation_sha256")
            != start.get("dependency_attestation_sha256")
            or entry.get("environment_sha256")
            != start.get("environment_sha256")
            or entry.get("terminal_record")
            not in ("manifest.json", "attempt-failure.json")
            or not _valid_sha256(entry.get("terminal_record_sha256"))
            or not isinstance(entry.get("terminal_record_bytes"), int)
            or isinstance(entry.get("terminal_record_bytes"), bool)
            or entry["terminal_record_bytes"] <= 0
        ):
            raise BootstrapError("formal attempt ledger TERMINAL binding is malformed")
        if entry.get("terminal_record") == "manifest.json":
            status = entry.get("preterminal_report_status")
            if (
                entry.get("preterminal_report") != PRETERMINAL_REPORT_NAME
                or not _valid_sha256(entry.get("preterminal_report_sha256"))
                or not isinstance(entry.get("preterminal_report_bytes"), int)
                or isinstance(entry.get("preterminal_report_bytes"), bool)
                or entry["preterminal_report_bytes"] <= 0
                or status not in ADJUDICATION_EXIT_STATUSES
                or entry.get("preterminal_report_claimable")
                != (status in ("pass", "fail"))
                or not isinstance(entry.get("auditor_identity"), dict)
                or not isinstance(entry.get("bootstrap_completion"), dict)
            ):
                raise BootstrapError("formal completed TERMINAL is malformed")
        elif (
            any(
                entry.get(field) is not None
                for field in (
                    "preterminal_report",
                    "preterminal_report_sha256",
                    "preterminal_report_bytes",
                    "auditor_identity",
                    "bootstrap_completion",
                )
            )
            or entry.get("preterminal_report_status") != "invalid"
            or entry.get("preterminal_report_claimable") is not False
        ):
            raise BootstrapError("formal failure TERMINAL is malformed")
        open_start = None

    if open_start is None or entries[-1].get("event") != "START":
        raise BootstrapError("formal terminal has no unmatched START")
    return dict(open_start)


def _ledger_prefix_identity(
    entries: Sequence[Mapping[str, Any]], payload: bytes
) -> Dict[str, Any]:
    return {
        "path": "attempt-ledger.jsonl",
        "prefix_rows": len(entries),
        "prefix_sha256": hashlib.sha256(payload).hexdigest(),
        "prefix_bytes": len(payload),
        "entry_sha256": entries[-1]["entry_sha256"],
        "event": entries[-1]["event"],
    }


def _validated_terminal_intent(
    raw_intent: Any, target_exit_status: int
) -> Dict[str, Any]:
    intent = _deep_canonical_mapping(raw_intent, "formal terminal intent")
    required_fields = {
        "schema_version",
        "attempt_id",
        "directory",
        "terminal_record",
        "terminal_record_sha256",
        "terminal_record_bytes",
        "preterminal_report",
        "preterminal_report_sha256",
        "preterminal_report_bytes",
        "preterminal_report_status",
        "preterminal_report_claimable",
        "auditor_identity",
        "target_exit_status",
        "ledger_prefix",
        "intent_sha256",
    }
    status = intent.get("preterminal_report_status")
    auditor_identity = intent.get("auditor_identity")
    ledger_prefix = intent.get("ledger_prefix")
    if (
        set(intent) != required_fields
        or intent.get("schema_version") != TERMINAL_INTENT_SCHEMA
        or not isinstance(intent.get("attempt_id"), str)
        or not intent["attempt_id"]
        or not isinstance(intent.get("directory"), str)
        or not intent["directory"]
        or intent.get("terminal_record") != "manifest.json"
        or not _valid_sha256(intent.get("terminal_record_sha256"))
        or not isinstance(intent.get("terminal_record_bytes"), int)
        or isinstance(intent.get("terminal_record_bytes"), bool)
        or intent["terminal_record_bytes"] <= 0
        or intent.get("preterminal_report") != PRETERMINAL_REPORT_NAME
        or not _valid_sha256(intent.get("preterminal_report_sha256"))
        or not isinstance(intent.get("preterminal_report_bytes"), int)
        or isinstance(intent.get("preterminal_report_bytes"), bool)
        or intent["preterminal_report_bytes"] <= 0
        or status not in ADJUDICATION_EXIT_STATUSES
        or not isinstance(intent.get("preterminal_report_claimable"), bool)
        or intent["preterminal_report_claimable"] != (status in ("pass", "fail"))
        or not isinstance(intent.get("target_exit_status"), int)
        or isinstance(intent.get("target_exit_status"), bool)
        or intent["target_exit_status"] != target_exit_status
        or ADJUDICATION_EXIT_STATUSES[status] != target_exit_status
        or not isinstance(auditor_identity, dict)
        or set(auditor_identity) != {"path", "sha256", "bytes"}
        or auditor_identity.get("path")
        != "benchmarks/carma/gate7_v3_audit.py"
        or not _valid_sha256(auditor_identity.get("sha256"))
        or not isinstance(auditor_identity.get("bytes"), int)
        or isinstance(auditor_identity.get("bytes"), bool)
        or auditor_identity["bytes"] <= 0
        or not isinstance(ledger_prefix, dict)
        or set(ledger_prefix)
        != {
            "path",
            "prefix_rows",
            "prefix_sha256",
            "prefix_bytes",
            "entry_sha256",
            "event",
        }
        or ledger_prefix.get("path") != "attempt-ledger.jsonl"
        or ledger_prefix.get("event") != "START"
        or not isinstance(ledger_prefix.get("prefix_rows"), int)
        or isinstance(ledger_prefix.get("prefix_rows"), bool)
        or ledger_prefix["prefix_rows"] <= 0
        or not isinstance(ledger_prefix.get("prefix_bytes"), int)
        or isinstance(ledger_prefix.get("prefix_bytes"), bool)
        or ledger_prefix["prefix_bytes"] <= 0
        or not _valid_sha256(ledger_prefix.get("prefix_sha256"))
        or not _valid_sha256(ledger_prefix.get("entry_sha256"))
        or not _valid_sha256(intent.get("intent_sha256"))
        or _mapping_self_hash(intent, "intent_sha256")
        != intent.get("intent_sha256")
    ):
        raise BootstrapError("formal terminal intent is malformed")
    return intent


def _bootstrap_completion(
    observation: Mapping[str, Any], intent: Mapping[str, Any]
) -> Dict[str, Any]:
    preimport_source = observation.get("preimport_source")
    dependency = observation.get("dependency")
    python = observation.get("python")
    environment = python.get("environment") if isinstance(python, dict) else None
    if not all(
        isinstance(item, dict)
        for item in (preimport_source, dependency, environment)
    ):
        raise BootstrapError("bootstrap completion inputs are malformed")
    completion: Dict[str, Any] = {
        "schema_version": BOOTSTRAP_COMPLETION_SCHEMA,
        "phase": "post_target",
        "role": "parent",
        "launch_mode": "full",
        "bootstrap_attestation_sha256": observation["attestation_sha256"],
        "target_sha256": observation["target_sha256"],
        "preimport_source_sha256": _canonical_mapping_sha256(preimport_source),
        "dependency_sha256": _canonical_mapping_sha256(dependency),
        "python_environment_sha256": _canonical_mapping_sha256(environment),
        "target_exit_status": intent["target_exit_status"],
        "terminal_intent_sha256": intent["intent_sha256"],
        "checks_passed": True,
    }
    completion["attestation_sha256"] = _canonical_mapping_sha256(completion)
    return completion


def _append_bootstrap_completed_terminal(
    project_root: Path,
    observation: Mapping[str, Any],
    target_exit_status: int,
) -> Dict[str, Any]:
    """Append TERMINAL only after the authoritative parent postcheck passed."""

    raw_intent = getattr(sys, "_gate7_v3_terminal_intent", None)
    intent = _validated_terminal_intent(raw_intent, target_exit_status)
    formal_root_path = project_root / FORMAL_ATTEMPT_ROOT
    if formal_root_path.is_symlink() or not formal_root_path.is_dir():
        raise BootstrapError("formal attempt root is missing or unsafe")
    formal_root = formal_root_path.resolve()
    directory = intent["directory"]
    if (
        directory in (".", "..")
        or "/" in directory
        or "\\" in directory
        or "\x00" in directory
    ):
        raise BootstrapError("formal terminal intent directory is unsafe")
    output_dir = formal_root / directory
    if (
        output_dir.is_symlink()
        or not output_dir.is_dir()
        or output_dir.resolve().parent != formal_root
    ):
        raise BootstrapError("formal terminal intent directory is not retained")

    lock_path = formal_root / ".formal-attempt.lock"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise BootstrapError("formal attempt lock is missing or unsafe")
    lock_flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_descriptor = os.open(str(lock_path), lock_flags)
    except OSError as exc:
        raise BootstrapError("formal attempt lock cannot be opened") from exc
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        ledger_path = _regular_direct_file(formal_root, "attempt-ledger.jsonl")
        entries, ledger_payload = _read_canonical_attempt_ledger(ledger_path)
        start = _validated_terminalizable_start(
            entries, project_root, formal_root
        )
        computed_prefix = _ledger_prefix_identity(entries, ledger_payload)
        entrypoint = start.get("entrypoint_attestation")
        if (
            intent["ledger_prefix"] != computed_prefix
            or intent["attempt_id"] != start.get("attempt_id")
            or directory != start.get("directory")
            or not isinstance(entrypoint, dict)
            or entrypoint.get("bootstrap_attestation") != dict(observation)
            or entrypoint.get("bootstrap_attestation_sha256")
            != observation.get("attestation_sha256")
        ):
            raise BootstrapError("formal terminal intent differs from its START")

        manifest_path = _regular_direct_file(output_dir, "manifest.json")
        report_path = _regular_direct_file(output_dir, PRETERMINAL_REPORT_NAME)
        if (
            _artifact_identity(manifest_path)
            != {
                "sha256": intent["terminal_record_sha256"],
                "bytes": intent["terminal_record_bytes"],
            }
            or _artifact_identity(report_path)
            != {
                "sha256": intent["preterminal_report_sha256"],
                "bytes": intent["preterminal_report_bytes"],
            }
        ):
            raise BootstrapError("formal terminal evidence changed after intent")
        manifest = _read_json_mapping(manifest_path, "formal manifest")
        report = _read_json_mapping(report_path, "formal preterminal report")
        manifest_sources = manifest.get("source_identities")
        producer_identity = (
            manifest_sources.get(
                "benchmarks/carma/gate7_v3_onnx_integration_benchmark.py"
            )
            if isinstance(manifest_sources, dict)
            else None
        )
        auditor_source_identity = (
            manifest_sources.get("benchmarks/carma/gate7_v3_audit.py")
            if isinstance(manifest_sources, dict)
            else None
        )
        manifest_dependency = manifest.get("dependency_attestation")
        manifest_git = manifest.get("git")
        manifest_power = manifest.get("power_observations")
        manifest_attempt_policy = manifest.get("attempt_policy")
        prior_attempts = manifest.get("prior_attempts")
        prior_attempts_sha256 = (
            hashlib.sha256(
                json.dumps(
                    prior_attempts,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if isinstance(prior_attempts, list)
            and all(isinstance(item, dict) for item in prior_attempts)
            else None
        )
        if (
            manifest.get("schema_version") != EVIDENCE_SCHEMA
            or manifest.get("experiment_id") != EXPERIMENT_ID
            or manifest.get("attempt_id") != start.get("attempt_id")
            or manifest.get("started_at_utc") != start.get("started_at_utc")
            or not isinstance(manifest_git, dict)
            or manifest_git.get("head_commit") != start.get("head_commit")
            or not isinstance(prior_attempts, list)
            or len(prior_attempts) != start.get("prior_attempt_count")
            or prior_attempts_sha256 != start.get("prior_attempts_sha256")
            or manifest.get("attempt_ledger") != computed_prefix
            or manifest.get("contract") != start.get("contract")
            or manifest.get("source_snapshot_sha256")
            != start.get("source_snapshot_sha256")
            or manifest.get("entrypoint_attestation") != entrypoint
            or manifest.get("formal_source_anchor")
            != start.get("formal_source_anchor")
            or not isinstance(manifest_dependency, dict)
            or manifest_dependency.get("attestation_sha256")
            != start.get("dependency_attestation_sha256")
            or manifest.get("environment_sha256")
            != start.get("environment_sha256")
            or not isinstance(manifest_power, dict)
            or manifest_power.get("pre_start")
            != start.get("pre_start_power")
            or manifest.get("retained_inputs") != start.get("retained_inputs")
            or not isinstance(manifest_attempt_policy, dict)
            or manifest_attempt_policy.get("formal_root")
            != FORMAL_ATTEMPT_ROOT.as_posix()
            or manifest_attempt_policy.get("directory")
            != start.get("directory")
            or not isinstance(producer_identity, dict)
            or producer_identity.get("sha256")
            != observation.get("target_sha256")
            or not _auditor_identity_matches_source(
                intent.get("auditor_identity"), auditor_source_identity
            )
            or report.get("audit_phase") != "preterminal"
            or report.get("attempt_id") != start.get("attempt_id")
            or report.get("status") != intent["preterminal_report_status"]
            or report.get("claimable")
            != intent["preterminal_report_claimable"]
            or report.get("auditor_identity") != intent["auditor_identity"]
        ):
            raise BootstrapError("formal terminal evidence differs from its intent")

        completion = _bootstrap_completion(observation, intent)
        terminal: Dict[str, Any] = {
            "schema_version": ATTEMPT_LEDGER_SCHEMA,
            "experiment_id": EXPERIMENT_ID,
            "sequence": len(entries) + 1,
            "event": "TERMINAL",
            "attempt_id": start["attempt_id"],
            "started_at_utc": start["started_at_utc"],
            "head_commit": start["head_commit"],
            "directory": start["directory"],
            "formal_source_anchor": start["formal_source_anchor"],
            "dependency_attestation_sha256": start[
                "dependency_attestation_sha256"
            ],
            "environment_sha256": start["environment_sha256"],
            "terminal_record": "manifest.json",
            "terminal_record_sha256": intent["terminal_record_sha256"],
            "terminal_record_bytes": intent["terminal_record_bytes"],
            "preterminal_report": PRETERMINAL_REPORT_NAME,
            "preterminal_report_sha256": intent[
                "preterminal_report_sha256"
            ],
            "preterminal_report_bytes": intent["preterminal_report_bytes"],
            "preterminal_report_status": intent[
                "preterminal_report_status"
            ],
            "preterminal_report_claimable": intent[
                "preterminal_report_claimable"
            ],
            "auditor_identity": intent["auditor_identity"],
            "bootstrap_completion": completion,
            "previous_entry_sha256": start["entry_sha256"],
        }
        terminal["entry_sha256"] = _canonical_mapping_sha256(terminal)
        terminal_payload = _canonical_json_bytes(terminal) + b"\n"

        # Recheck the immutable inputs immediately before the one append.
        entries_now, payload_now = _read_canonical_attempt_ledger(ledger_path)
        _validated_terminalizable_start(
            entries_now, project_root, formal_root
        )
        if entries_now != entries or payload_now != ledger_payload:
            raise BootstrapError("formal attempt ledger changed before terminal append")
        if (
            _artifact_identity(manifest_path)["sha256"]
            != intent["terminal_record_sha256"]
            or _artifact_identity(report_path)["sha256"]
            != intent["preterminal_report_sha256"]
        ):
            raise BootstrapError("formal terminal evidence changed before append")
        append_flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        try:
            ledger_descriptor = os.open(str(ledger_path), append_flags)
            try:
                offset = 0
                while offset < len(terminal_payload):
                    written = os.write(ledger_descriptor, terminal_payload[offset:])
                    if written <= 0:
                        raise OSError("short formal ledger append")
                    offset += written
                os.fsync(ledger_descriptor)
            finally:
                os.close(ledger_descriptor)
        except OSError as exc:
            raise BootstrapError("formal TERMINAL append failed") from exc
        final_entries, final_payload = _read_canonical_attempt_ledger(ledger_path)
        if (
            len(final_entries) != len(entries) + 1
            or final_entries[-1] != terminal
            or final_payload != ledger_payload + terminal_payload
        ):
            raise BootstrapError("formal TERMINAL append did not verify")
        return terminal
    finally:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(lock_descriptor)


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _lexical_absolute(path: Path) -> Path:
    """Return an absolute normalized path without following symlinks."""

    return Path(os.path.abspath(os.fspath(path)))


def _is_under(path: Path, parent: Path) -> bool:
    try:
        _lexical_absolute(path).relative_to(_lexical_absolute(parent))
    except ValueError:
        return False
    return True


def _require_regular_tree_path(path: Path, parent: Path) -> None:
    """Reject a member or any of its in-boundary parents if it is a symlink."""

    normalized = _lexical_absolute(path)
    root = _lexical_absolute(parent)
    if not _is_under(normalized, root):
        raise BootstrapError("dependency path escapes the virtual environment")
    cursor = normalized
    while True:
        if cursor.is_symlink():
            raise BootstrapError("dependency path contains a symlink: %s" % cursor)
        if cursor == root:
            break
        cursor = cursor.parent


def _parse_lock(path: Path) -> Dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise BootstrapError("benchmark lock is missing or is a symlink")
    if _sha256_file(path) != PINNED_LOCK_SHA256:
        raise BootstrapError("benchmark lock identity changed")
    logical_lines = []
    accumulator = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not accumulator and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            accumulator += line[:-1].strip() + " "
            continue
        accumulator += line.strip()
        logical_lines.append(accumulator.strip())
        accumulator = ""
    if accumulator:
        raise BootstrapError("benchmark lock ends mid-requirement")
    pins: Dict[str, str] = {}
    for line in logical_lines:
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:\s|$)", line)
        if match is None:
            raise BootstrapError("benchmark lock contains an unpinned row")
        name = _normalized_distribution_name(match.group(1))
        if name in pins:
            raise BootstrapError("benchmark lock repeats a package")
        pins[name] = match.group(2)
    pins = dict(sorted(pins.items()))
    if len(pins) != PINNED_LOCK_PIN_COUNT:
        raise BootstrapError("benchmark lock pin count changed")
    if _canonical_mapping_sha256(pins) != PINNED_LOCK_PIN_MAP_SHA256:
        raise BootstrapError("benchmark lock pin map changed")
    return pins


def _parse_pyvenv_config(path: Path) -> Dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise BootstrapError("pyvenv.cfg is missing or is a symlink")
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if "=" not in line:
            raise BootstrapError("pyvenv.cfg has a malformed row")
        key, value = (part.strip() for part in line.split("=", 1))
        if not key or key in values:
            raise BootstrapError("pyvenv.cfg has an empty or duplicate key")
        values[key] = value
    required = {"home", "include-system-site-packages", "version", "executable"}
    if not required.issubset(values):
        raise BootstrapError("pyvenv.cfg is incomplete")
    if values["include-system-site-packages"].lower() != "false":
        raise BootstrapError("system site-packages must be disabled")
    if values["version"] != PINNED_CPYTHON_VERSION:
        raise BootstrapError("pyvenv.cfg Python version changed")
    configured_executable = Path(values["executable"])
    if configured_executable.resolve() != Path(sys.executable).resolve():
        raise BootstrapError("pyvenv.cfg interpreter differs from the running binary")
    configured_home = Path(values["home"])
    if not configured_home.is_dir():
        raise BootstrapError("pyvenv.cfg home is missing")
    return values


def _valid_sha1(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{40}", value) is not None
    )


def _git_bytes(project_root: Path, *arguments: str) -> bytes:
    if (
        not PINNED_GIT_EXECUTABLE.is_file()
        or PINNED_GIT_EXECUTABLE.is_symlink()
    ):
        raise BootstrapError("formal source verification requires /usr/bin/git")
    environment = dict(os.environ)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    command = [
        str(PINNED_GIT_EXECUTABLE),
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        *arguments,
    ]
    try:
        return subprocess.check_output(
            command,
            cwd=str(project_root),
            env=environment,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BootstrapError(
            "formal source Git verification failed: %s" % arguments[0]
        ) from exc


def _git_text(project_root: Path, *arguments: str) -> str:
    try:
        return _git_bytes(project_root, *arguments).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise BootstrapError("formal source Git output is not UTF-8") from exc


def _formal_submission_remote_anchor(
    project_root: Path,
    local_tag_object_id: str,
    local_peeled_commit: str,
    local_head_commit: str,
) -> Dict[str, Any]:
    """Bind the local source anchor to the one authorized submission remote."""

    expected_url_output = (FORMAL_SUBMISSION_URL + "\n").encode("utf-8")
    fetch_url_output = _git_bytes(
        project_root,
        "remote",
        "get-url",
        "--all",
        FORMAL_SUBMISSION_REMOTE,
    )
    push_url_output = _git_bytes(
        project_root,
        "remote",
        "get-url",
        "--push",
        "--all",
        FORMAL_SUBMISSION_REMOTE,
    )
    if (
        fetch_url_output != expected_url_output
        or push_url_output != expected_url_output
    ):
        raise BootstrapError(
            "formal submission remote must have one exact fetch and push URL"
        )

    tag_ref = "refs/tags/%s" % FORMAL_SOURCE_TAG
    peeled_ref = "%s^{}" % tag_ref
    remote_output = _git_bytes(
        project_root,
        "ls-remote",
        "--exit-code",
        "--tags",
        FORMAL_SUBMISSION_URL,
        tag_ref,
        peeled_ref,
    )
    try:
        remote_lines = remote_output.decode("utf-8").splitlines()
        remote_refs: Dict[str, str] = {}
        for line in remote_lines:
            object_id, ref_name = line.split("\t", 1)
            if ref_name in remote_refs or not _valid_sha1(object_id):
                raise ValueError("duplicate or malformed remote ref")
            remote_refs[ref_name] = object_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise BootstrapError(
            "formal submission remote tag output is malformed"
        ) from exc

    if (
        set(remote_refs) != {tag_ref, peeled_ref}
        or remote_refs[tag_ref] != local_tag_object_id
        or remote_refs[peeled_ref] != local_peeled_commit
        or remote_refs[peeled_ref] != local_head_commit
    ):
        raise BootstrapError(
            "formal submission remote tag does not authenticate local HEAD"
        )
    return {
        "remote_name": FORMAL_SUBMISSION_REMOTE,
        "fetch_url": FORMAL_SUBMISSION_URL,
        "fetch_url_count": 1,
        "push_url": FORMAL_SUBMISSION_URL,
        "push_url_count": 1,
        "tag_ref": tag_ref,
        "tag_object_id": remote_refs[tag_ref],
        "peeled_ref": peeled_ref,
        "peeled_commit": remote_refs[peeled_ref],
    }


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(
        ("blob %d\0" % len(data)).encode("ascii") + data
    ).hexdigest()


def _formal_source_anchor(project_root: Path) -> Dict[str, Any]:
    tag_ref = "refs/tags/%s" % FORMAL_SOURCE_TAG
    object_format = _git_text(project_root, "rev-parse", "--show-object-format")
    head_commit = _git_text(project_root, "rev-parse", "HEAD")
    head_tree = _git_text(project_root, "rev-parse", "HEAD^{tree}")
    tag_object_id = _git_text(project_root, "rev-parse", tag_ref)
    tag_object_type = _git_text(project_root, "cat-file", "-t", tag_object_id)
    peeled_commit = _git_text(project_root, "rev-parse", "%s^{commit}" % tag_ref)
    tag_tree = _git_text(project_root, "rev-parse", "%s^{tree}" % tag_ref)
    tag_payload = _git_bytes(project_root, "cat-file", "tag", tag_object_id)
    try:
        header_bytes, annotation_bytes = tag_payload.split(b"\n\n", 1)
        headers: Dict[str, str] = {}
        for raw_header in header_bytes.decode("utf-8").splitlines():
            key, value = raw_header.split(" ", 1)
            if key in headers:
                raise ValueError("duplicate tag header")
            headers[key] = value
        annotation_lines = set(annotation_bytes.decode("utf-8").splitlines())
    except (ValueError, UnicodeDecodeError) as exc:
        raise BootstrapError("formal source tag payload is malformed") from exc
    computed_tag_object = hashlib.sha1(
        ("tag %d\0" % len(tag_payload)).encode("ascii") + tag_payload
    ).hexdigest()
    required_annotation = {
        "experiment_id=gate7d-onnx-v3",
        "contract_sha256=%s" % PINNED_CONTRACT_SHA256,
    }
    if (
        object_format != "sha1"
        or tag_object_type != "tag"
        or not all(
            _valid_sha1(value)
            for value in (head_commit, head_tree, tag_object_id, peeled_commit, tag_tree)
        )
        or computed_tag_object != tag_object_id
        or peeled_commit != head_commit
        or tag_tree != head_tree
        or headers.get("object") != peeled_commit
        or headers.get("type") != "commit"
        or headers.get("tag") != FORMAL_SOURCE_TAG
        or not required_annotation.issubset(annotation_lines)
    ):
        raise BootstrapError("formal source tag does not authenticate clean HEAD")
    contract_path = project_root / FORMAL_CONTRACT_PATH
    if (
        not contract_path.is_file()
        or contract_path.is_symlink()
        or _sha256_file(contract_path) != PINNED_CONTRACT_SHA256
    ):
        raise BootstrapError("formal contract identity differs from its frozen hash")
    remote = _formal_submission_remote_anchor(
        project_root,
        tag_object_id,
        peeled_commit,
        head_commit,
    )
    return {
        "object_format": object_format,
        "head_commit": head_commit,
        "head_tree": head_tree,
        "tag_name": FORMAL_SOURCE_TAG,
        "tag_object_type": tag_object_type,
        "tag_object_id": tag_object_id,
        "peeled_commit": peeled_commit,
        "tag_tree": tag_tree,
        "tag_payload_sha256": hashlib.sha256(tag_payload).hexdigest(),
        "tag_payload_bytes": len(tag_payload),
        "annotation": {
            "experiment_id": "gate7d-onnx-v3",
            "contract_sha256": PINNED_CONTRACT_SHA256,
        },
        "remote": remote,
        "contract_path": FORMAL_CONTRACT_PATH,
        "contract_sha256": PINNED_CONTRACT_SHA256,
        "contract_bytes": contract_path.stat().st_size,
    }


def _project_import_namespace_scan(project_root: Path) -> Dict[str, Any]:
    try:
        tracked_paths = {
            raw.decode("utf-8")
            for raw in _git_bytes(project_root, "ls-files", "-z").split(b"\0")
            if raw
        }
    except UnicodeDecodeError as exc:
        raise BootstrapError("formal tracked path is not UTF-8") from exc
    import_suffixes = (".py", ".pyc", ".pyo", ".so", ".pyd", ".dylib", ".dll")
    import_file_count = 0
    def reject_walk_error(error: OSError) -> None:
        raise BootstrapError("formal project import namespace is unreadable") from error

    for directory, directory_names, file_names in os.walk(
        project_root,
        topdown=True,
        onerror=reject_walk_error,
        followlinks=False,
    ):
        directory_path = Path(directory)
        if directory_path == project_root:
            directory_names[:] = [
                name for name in directory_names if name not in {".git", ".venv"}
            ]
        for name in list(directory_names):
            candidate = directory_path / name
            if candidate.is_symlink():
                raise BootstrapError(
                    "formal project import namespace contains a directory symlink"
                )
        for name in file_names:
            candidate = directory_path / name
            if candidate.is_symlink():
                raise BootstrapError(
                    "formal project import namespace contains a file symlink"
                )
            if not candidate.is_file():
                raise BootstrapError(
                    "formal project import namespace contains a special entry"
                )
            if not name.lower().endswith(import_suffixes):
                continue
            import_file_count += 1
            relative = candidate.relative_to(project_root).as_posix()
            if relative not in tracked_paths:
                raise BootstrapError(
                    "formal project import namespace contains untracked or ignored code"
                )
    return {
        "excluded_sealed_roots": [".git", ".venv"],
        "import_suffixes": list(import_suffixes),
        "import_file_count": import_file_count,
        "untracked_or_ignored_import_file_count": 0,
        "symlink_count": 0,
    }


def _tracked_source_inventory(project_root: Path) -> Dict[str, Any]:
    pathspecs = [*FORMAL_RUNTIME_ROOTS, *FORMAL_CONTROL_PATHS]
    raw_inventory = _git_bytes(
        project_root, "ls-files", "-s", "-z", "--", *pathspecs
    )
    tracked: Dict[str, Dict[str, Any]] = {}
    for raw_entry in raw_inventory.split(b"\0"):
        if not raw_entry:
            continue
        try:
            raw_header, raw_path = raw_entry.split(b"\t", 1)
            mode, object_id, stage = raw_header.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise BootstrapError("formal tracked-source inventory is malformed") from exc
        if (
            relative in tracked
            or mode not in {"100644", "100755"}
            or stage != "0"
            or not _valid_sha1(object_id)
        ):
            raise BootstrapError("formal tracked-source entry is unsafe")
        path = _lexical_absolute(project_root / relative)
        if not _is_under(path, project_root):
            raise BootstrapError("formal tracked-source path escapes the project")
        cursor = path
        while True:
            if cursor.is_symlink():
                raise BootstrapError("formal tracked-source path contains a symlink")
            if cursor == project_root:
                break
            cursor = cursor.parent
        if not path.is_file():
            raise BootstrapError("formal tracked-source file is missing")
        executable = bool(path.stat().st_mode & 0o111)
        if executable != (mode == "100755"):
            raise BootstrapError("formal tracked-source executable mode changed")
        data = path.read_bytes()
        if _git_blob_sha1(data) != object_id:
            raise BootstrapError("formal tracked-source bytes differ from Git")
        tracked[relative] = {
            "mode": mode,
            "git_blob_sha1": object_id,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    missing_controls = sorted(set(FORMAL_CONTROL_PATHS).difference(tracked))
    if missing_controls:
        raise BootstrapError("formal source inventory omits required controls")

    tracked_runtime = {
        relative
        for relative in tracked
        if any(
            relative == root or relative.startswith(root + "/")
            for root in FORMAL_RUNTIME_ROOTS
        )
    }
    actual_runtime: Set[str] = set()
    for root_name in FORMAL_RUNTIME_ROOTS:
        root = project_root / root_name
        if not root.is_dir() or root.is_symlink():
            raise BootstrapError("formal runtime source root is missing or unsafe")
        for path in root.rglob("*"):
            if path.is_symlink():
                raise BootstrapError("formal runtime source contains a symlink")
            if path.is_dir():
                continue
            if not path.is_file():
                raise BootstrapError("formal runtime source contains a special entry")
            actual_runtime.add(path.relative_to(project_root).as_posix())
    if actual_runtime != tracked_runtime:
        raise BootstrapError(
            "formal runtime roots contain untracked, ignored, or missing files"
        )

    return {
        "runtime_roots": list(FORMAL_RUNTIME_ROOTS),
        "tracked_file_count": len(tracked),
        "tracked_file_bytes": sum(
            int(identity["bytes"]) for identity in tracked.values()
        ),
        "tracked_file_map_sha256": _canonical_mapping_sha256(tracked),
        "runtime_file_count": len(actual_runtime),
        "runtime_untracked_or_missing_file_count": 0,
        "runtime_symlink_count": 0,
        "project_import_namespace": _project_import_namespace_scan(project_root),
    }


def _verify_formal_project_source(project_root: Path) -> Dict[str, Any]:
    status_before = _git_bytes(
        project_root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if status_before:
        raise BootstrapError("formal source worktree is not clean")
    anchor_before = _formal_source_anchor(project_root)
    _v2_preservation_identity(project_root)
    inventory = _tracked_source_inventory(project_root)
    anchor_after = _formal_source_anchor(project_root)
    status_after = _git_bytes(
        project_root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if status_after or anchor_after != anchor_before:
        raise BootstrapError("formal source changed during pre-import verification")
    git_identity = {
        "path": str(PINNED_GIT_EXECUTABLE),
        "sha256": _sha256_file(PINNED_GIT_EXECUTABLE),
        "bytes": PINNED_GIT_EXECUTABLE.stat().st_size,
        "version": _git_text(project_root, "--version"),
    }
    return {
        "schema_version": "carma-gate7-preimport-source-v1",
        "enforced": True,
        "launch_mode": "full",
        "git_executable": git_identity,
        "git_status_clean": True,
        "anchor": anchor_before,
        "inventory": inventory,
    }


def _verify_startup_and_environment(
    project_root: Path, venv_root: Path, pycache_prefix: Path
) -> Dict[str, Any]:
    flags = sys.flags
    if (
        flags.no_site != 1
        or not flags.safe_path
        or flags.ignore_environment != 0
        or flags.isolated != 0
        or flags.dont_write_bytecode != 1
        or flags.hash_randomization != 0
    ):
        raise BootstrapError("formal launcher requires python -S -P and frozen Python env")
    if "site" in sys.modules:
        raise BootstrapError("site was imported before the isolated bootstrap")
    if platform.python_implementation() != "CPython":
        raise BootstrapError("formal launcher requires CPython")
    if platform.python_version() != PINNED_CPYTHON_VERSION:
        raise BootstrapError("formal launcher requires exact CPython 3.12.13")

    expected_executable = project_root / ".venv" / "bin" / "python"
    observed_lexical = _lexical_absolute(Path(sys.executable))
    if observed_lexical != expected_executable:
        raise BootstrapError("formal launcher requires the exact project .venv/bin/python")
    if venv_root != project_root / ".venv" or venv_root.is_symlink():
        raise BootstrapError("formal virtual-environment root changed")
    if Path.cwd().resolve() != project_root:
        raise BootstrapError("formal launcher must start in the tagged project root")

    observed_required = {key: os.environ.get(key) for key in REQUIRED_ENVIRONMENT}
    if observed_required != REQUIRED_ENVIRONMENT:
        raise BootstrapError("formal environment differs from frozen values")
    missing_dynamic = [key for key in REQUIRED_DYNAMIC_ENVIRONMENT if key not in os.environ]
    if missing_dynamic:
        raise BootstrapError("formal environment is missing required dynamic values")
    launch_mode = os.environ.get("CARMA_GATE7_LAUNCH_MODE")
    if launch_mode not in {"full", "smoke"}:
        raise BootstrapError("Gate 7 launch mode must be exactly full or smoke")
    wrapper_profile = os.environ.get("CARMA_GATE7_WRAPPER_SHELL_PROFILE")
    expected_wrapper_profile = (
        "env-i-v1" if launch_mode == "full" else "development-smoke"
    )
    wrapper_home = os.environ.get("CARMA_GATE7_WRAPPER_SHELL_HOME", "")
    wrapper_pwd = os.environ.get("CARMA_GATE7_WRAPPER_SHELL_PWD", "")
    wrapper_tmpdir = os.environ.get("CARMA_GATE7_WRAPPER_SHELL_TMPDIR", "")
    wrapper_shlvl = os.environ.get("CARMA_GATE7_WRAPPER_SHELL_SHLVL", "")
    wrapper_optional_cf = os.environ.get(
        "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF", ""
    )
    if (
        wrapper_profile != expected_wrapper_profile
        or not wrapper_home
        or Path(wrapper_home).resolve() != Path(os.environ["HOME"]).resolve()
        or not wrapper_pwd
        or Path(wrapper_pwd).resolve() != project_root
        or not wrapper_tmpdir
        or not wrapper_shlvl
        or (launch_mode == "full" and wrapper_tmpdir != "/tmp")
        or (launch_mode == "full" and wrapper_shlvl != "1")
    ):
        raise BootstrapError("wrapper-shell startup observation is inconsistent")
    wrapper_shell_startup: Dict[str, Any] = {
        "schema_version": "carma-gate7-wrapper-shell-startup-v1",
        "profile": wrapper_profile,
        "marker": REQUIRED_ENVIRONMENT["CARMA_GATE7_WRAPPER_SHELL"],
        "launch_mode": launch_mode,
        "outer_env_i_operator_root_required": launch_mode == "full",
        "home": wrapper_home,
        "pwd": wrapper_pwd,
        "tmpdir": wrapper_tmpdir,
        "shlvl": wrapper_shlvl,
        "optional_cf_user_text_encoding": wrapper_optional_cf,
        "fixed_environment": {
            "PATH": REQUIRED_ENVIRONMENT["PATH"],
            "LANG": REQUIRED_ENVIRONMENT["LANG"],
            "LC_ALL": REQUIRED_ENVIRONMENT["LC_ALL"],
            "LC_CTYPE": REQUIRED_ENVIRONMENT["LC_CTYPE"],
            "TZ": REQUIRED_ENVIRONMENT["TZ"],
        },
    }
    wrapper_shell_startup["attestation_sha256"] = _canonical_mapping_sha256(
        wrapper_shell_startup
    )
    unexpected = sorted(set(os.environ) - PERMITTED_PREBOOT_ENVIRONMENT)
    if unexpected:
        raise BootstrapError("formal environment contains unexpected keys: %r" % unexpected)
    if any(
        key.startswith(FORBIDDEN_LOADER_PREFIXES)
        for key in os.environ
    ):
        raise BootstrapError("dynamic-loader override variables are forbidden")
    if any(key.startswith(FORBIDDEN_BOOTSTRAP_PREFIX) for key in os.environ):
        raise BootstrapError("bootstrap evidence variables were forged or inherited")

    wrapper = project_root / "scripts" / "run_gate7_v3_onnx_integration_benchmark.sh"
    if Path(os.environ["CARMA_GATE7_WRAPPER_PATH"]).resolve() != wrapper:
        raise BootstrapError("formal wrapper path changed")
    try:
        wrapper_pid = int(os.environ["CARMA_GATE7_WRAPPER_PID"])
    except ValueError as exc:
        raise BootstrapError("formal wrapper PID is malformed") from exc
    if wrapper_pid <= 1:
        raise BootstrapError("formal wrapper PID is invalid")

    home = Path(os.environ["HOME"]).resolve()
    expected_hf_home = home / ".cache" / "huggingface"
    if Path(os.environ["HF_HOME"]).resolve() != expected_hf_home:
        raise BootstrapError("HF_HOME differs from the frozen local cache")
    if Path(os.environ["HF_HUB_CACHE"]).resolve() != expected_hf_home / "hub":
        raise BootstrapError("HF_HUB_CACHE differs from the frozen local cache")

    runtime_tmp = Path(os.environ["TMPDIR"])
    if (
        runtime_tmp.is_symlink()
        or not runtime_tmp.is_dir()
        or runtime_tmp.name != "tmp"
        or not runtime_tmp.parent.name.startswith("gate7-runtime.")
    ):
        raise BootstrapError("formal TMPDIR is not the wrapper-owned runtime directory")
    if (
        pycache_prefix.is_symlink()
        or not pycache_prefix.is_dir()
        or pycache_prefix.parent != runtime_tmp.parent
        or pycache_prefix.name != "pycache"
        or any(pycache_prefix.iterdir())
    ):
        raise BootstrapError("formal pycache prefix is not an empty private directory")
    if Path(sys.pycache_prefix or "") != pycache_prefix:
        raise BootstrapError("running interpreter ignored PYTHONPYCACHEPREFIX")

    initial_paths = []
    base_prefix = Path(sys.base_prefix).resolve()
    for raw_path in sys.path:
        if not raw_path:
            raise BootstrapError("unsafe empty entry exists in initial sys.path")
        path = Path(raw_path).resolve()
        if "site-packages" in path.parts or "dist-packages" in path.parts:
            raise BootstrapError("third-party path exists in initial sys.path")
        try:
            path.relative_to(base_prefix)
        except ValueError as exc:
            raise BootstrapError("initial sys.path escapes the base stdlib") from exc
        initial_paths.append(str(path))
    return {
        "flags": {
            "no_site": flags.no_site,
            "safe_path": bool(flags.safe_path),
            "ignore_environment": flags.ignore_environment,
            "isolated": flags.isolated,
            "dont_write_bytecode": flags.dont_write_bytecode,
            "hash_randomization": flags.hash_randomization,
        },
        "initial_sys_path": initial_paths,
        "base_prefix": str(base_prefix),
        "executable": str(observed_lexical),
        "executable_resolved": str(observed_lexical.resolve()),
        "executable_sha256": _sha256_file(observed_lexical.resolve()),
        "wrapper_pid": wrapper_pid,
        "environment": dict(sorted(os.environ.items())),
        "wrapper_shell_startup": wrapper_shell_startup,
    }


def _verify_preloaded_module_origins(project_root: Path) -> Dict[str, str]:
    """Prove that bootstrap imports came only from stdlib plus this script."""

    base_prefix = Path(sys.base_prefix).resolve()
    this_file = Path(__file__).resolve()
    origins: Dict[str, str] = {}
    for name, module in sorted(sys.modules.items()):
        raw_origin = getattr(module, "__file__", None)
        if not raw_origin:
            continue
        origin = Path(raw_origin).resolve()
        if origin == this_file:
            origins[name] = str(origin.relative_to(project_root))
            continue
        try:
            relative = origin.relative_to(base_prefix)
        except ValueError as exc:
            raise BootstrapError(
                "non-stdlib module loaded before dependency verification: %s" % name
            ) from exc
        origins[name] = "<stdlib>/%s" % relative.as_posix()
    return origins


def _record_member_path(distribution: importlib.metadata.Distribution, row: str) -> Path:
    return _lexical_absolute(Path(distribution.locate_file(row)))


def _verify_distributions(
    site_packages: Path,
    venv_root: Path,
    pins: Mapping[str, str],
    project_root: Path,
    *,
    require_inactive: bool = True,
) -> Dict[str, Any]:
    distributions: Dict[str, importlib.metadata.Distribution] = {}
    installed: Dict[str, str] = {}
    for distribution in importlib.metadata.distributions(path=[str(site_packages)]):
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise BootstrapError("installed distribution lacks a canonical name")
        name = _normalized_distribution_name(raw_name)
        if name in distributions:
            raise BootstrapError("installed distribution name is duplicated: %s" % name)
        distributions[name] = distribution
        installed[name] = distribution.version
    expected_installed = dict(pins)
    expected_installed["gptcache"] = LOCAL_GPTCACHE_VERSION
    expected_installed = dict(sorted(expected_installed.items()))
    installed = dict(sorted(installed.items()))
    if installed != expected_installed:
        raise BootstrapError("installed distribution map differs from the lock")

    locked_summaries: Dict[str, Any] = {}
    local_summary: Dict[str, Any] | None = None
    owner_by_member: Dict[Path, str] = {}
    covered_directories: Set[Path] = {site_packages}
    pth_files: Dict[str, Any] = {}
    unhashed_existing: Dict[str, str] = {}
    locked_hashed_file_count = 0
    locked_hashed_bytes = 0

    for name in sorted(distributions):
        distribution = distributions[name]
        distribution_path = _lexical_absolute(Path(distribution._path))  # type: ignore[attr-defined]
        if not _is_under(distribution_path, site_packages):
            raise BootstrapError("distribution metadata escapes site-packages: %s" % name)
        _require_regular_tree_path(distribution_path, venv_root)
        record_path = distribution_path / "RECORD"
        if not record_path.is_file() or record_path.is_symlink():
            raise BootstrapError("distribution RECORD is missing or unsafe: %s" % name)
        try:
            record_text = record_path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BootstrapError("distribution RECORD is not UTF-8: %s" % name) from exc
        hashed_files: Dict[str, str] = {}
        hashed_bytes = 0
        seen_rows: Set[Path] = set()
        for row in csv.reader(io.StringIO(record_text)):
            if len(row) != 3 or not row[0]:
                raise BootstrapError("distribution RECORD is malformed: %s" % name)
            member = _record_member_path(distribution, row[0])
            if not _is_under(member, venv_root):
                raise BootstrapError("RECORD member escapes the virtual environment")
            _require_regular_tree_path(member, venv_root)
            if member in seen_rows:
                raise BootstrapError("distribution RECORD repeats a member: %s" % name)
            seen_rows.add(member)
            previous_owner = owner_by_member.setdefault(member, name)
            if previous_owner != name:
                raise BootstrapError("RECORD member has duplicate distribution owners")
            if _is_under(member, site_packages):
                cursor = member.parent
                while _is_under(cursor, site_packages):
                    covered_directories.add(cursor)
                    if cursor == site_packages:
                        break
                    cursor = cursor.parent

            encoded_hash = row[1]
            if not encoded_hash:
                relative_record = member.relative_to(venv_root).as_posix()
                is_record = member == record_path
                is_cache = member.suffix == ".pyc" and "__pycache__" in member.parts
                if not (is_record or is_cache):
                    raise BootstrapError("unexpected unhashed RECORD member: %s" % row[0])
                if member.exists():
                    if not member.is_file():
                        raise BootstrapError("unhashed RECORD member is not a file")
                    if is_cache:
                        raise BootstrapError(
                            "installed bytecode has no frozen RECORD hash: %s" % row[0]
                        )
                    unhashed_existing[relative_record] = _sha256_file(member)
                continue
            if not encoded_hash.startswith("sha256="):
                raise BootstrapError("RECORD uses a non-SHA256 member digest")
            if not member.is_file() or member.is_symlink():
                raise BootstrapError("hashed RECORD member is missing or unsafe")
            encoded_digest = encoded_hash.split("=", 1)[1]
            try:
                expected_digest = base64.urlsafe_b64decode(
                    encoded_digest + "=" * (-len(encoded_digest) % 4)
                )
                expected_size = int(row[2])
            except (TypeError, ValueError) as exc:
                raise BootstrapError("RECORD member hash or size is malformed") from exc
            observed_size = int(member.stat().st_size)
            observed_digest = _sha256_digest_bytes(member)
            if observed_size != expected_size or observed_digest != expected_digest:
                raise BootstrapError("hashed RECORD member identity changed: %s" % row[0])
            relative_member = member.relative_to(venv_root).as_posix()
            hashed_files[relative_member] = observed_digest.hex()
            hashed_bytes += observed_size
            if member.suffix.lower() == ".pth":
                pth_files[relative_member] = {
                    "owner": name,
                    "sha256": observed_digest.hex(),
                    "bytes": observed_size,
                }
        summary = {
            "record_sha256": _sha256_file(record_path),
            "hashed_file_count": len(hashed_files),
            "hashed_bytes": hashed_bytes,
            "hashed_files_sha256": _canonical_mapping_sha256(hashed_files),
        }
        if name == "gptcache":
            local_summary = summary
        else:
            locked_summaries[name] = summary
            locked_hashed_file_count += len(hashed_files)
            locked_hashed_bytes += hashed_bytes

    locked_record_aggregate = _canonical_mapping_sha256(locked_summaries)
    if (
        len(locked_summaries) != PINNED_LOCK_PIN_COUNT
        or locked_hashed_file_count != PINNED_LOCKED_HASHED_FILE_COUNT
        or locked_hashed_bytes != PINNED_LOCKED_HASHED_BYTES
        or locked_record_aggregate != PINNED_LOCKED_RECORD_AGGREGATE_SHA256
    ):
        raise BootstrapError("locked distribution RECORD aggregate changed")
    if local_summary is None:
        raise BootstrapError("local editable GPTCache RECORD is missing")
    if local_summary != PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY:
        raise BootstrapError("local editable GPTCache RECORD identity changed")

    # Seal the whole import root: every actual path must be declared by some
    # installed RECORD (directories are covered by a declared descendant).
    site_file_count = 0
    site_directory_count = 0
    for path in site_packages.rglob("*"):
        normalized = _lexical_absolute(path)
        _require_regular_tree_path(normalized, venv_root)
        if path.is_symlink():
            raise BootstrapError("site-packages contains a symlink")
        if path.is_file():
            site_file_count += 1
            if normalized not in owner_by_member:
                raise BootstrapError(
                    "site-packages contains an unowned file: %s"
                    % normalized.relative_to(site_packages)
                )
        elif path.is_dir():
            site_directory_count += 1
            if normalized not in covered_directories:
                raise BootstrapError(
                    "site-packages contains an unowned directory: %s"
                    % normalized.relative_to(site_packages)
                )
        else:
            raise BootstrapError("site-packages contains a special filesystem entry")

    for startup_name in ("sitecustomize.py", "usercustomize.py"):
        if (site_packages / startup_name).exists():
            raise BootstrapError("Python startup customization is forbidden")

    gptcache_distribution = distributions["gptcache"]
    direct_url_text = gptcache_distribution.read_text("direct_url.json")
    try:
        direct_url = json.loads(direct_url_text or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise BootstrapError("editable GPTCache direct_url.json is unreadable") from exc
    parsed_url = urlparse(str(direct_url.get("url", "")))
    source_path = Path(unquote(parsed_url.path)).resolve()
    editable = (
        isinstance(direct_url.get("dir_info"), dict)
        and direct_url["dir_info"].get("editable") is True
    )
    if parsed_url.scheme != "file" or source_path != project_root or not editable:
        raise BootstrapError("GPTCache editable provenance is not source-bound")

    # No .pth file has been executed: the site module is still absent and the
    # verified site root has not yet been added to sys.path.
    if require_inactive and (
        "site" in sys.modules or str(site_packages) in sys.path
    ):
        raise BootstrapError("site-packages became active before verification finished")
    return {
        "installed_packages": installed,
        "installed_package_map_sha256": _canonical_mapping_sha256(installed),
        "locked_distribution_count": len(locked_summaries),
        "locked_hashed_file_count": locked_hashed_file_count,
        "locked_hashed_bytes": locked_hashed_bytes,
        "locked_record_aggregate_sha256": locked_record_aggregate,
        "local_editable": local_summary,
        "pth_files": dict(sorted(pth_files.items())),
        "unhashed_existing_file_count": len(unhashed_existing),
        "unhashed_existing_sha256": _canonical_mapping_sha256(unhashed_existing),
        "site_file_count": site_file_count,
        "site_directory_count": site_directory_count,
        "sealed_site_packages": True,
    }


def _parse_invocation(argv: Sequence[str], project_root: Path) -> Tuple[str, Path, list[str]]:
    if len(argv) < 5 or argv[1] != "--role" or argv[3] != "--":
        raise BootstrapError(
            "usage: gate7_v3_isolated_bootstrap.py --role "
            "<parent|child|auditor> -- <absolute-target> [arguments...]"
        )
    role = argv[2]
    if role not in {"parent", "child", "auditor"}:
        raise BootstrapError("unsupported bootstrap role")
    target_raw = Path(argv[4])
    if not target_raw.is_absolute() or target_raw.is_symlink():
        raise BootstrapError("bootstrap target must be an absolute regular path")
    target = target_raw.resolve()
    expected = {
        "parent": project_root / "benchmarks" / "carma" / "gate7_v3_onnx_integration_benchmark.py",
        "child": project_root / "benchmarks" / "carma" / "gate7_v3_onnx_integration_benchmark.py",
        "auditor": project_root / "benchmarks" / "carma" / "gate7_v3_audit.py",
    }[role]
    if target != expected or not target.is_file():
        raise BootstrapError("bootstrap target is not permitted for this role")
    return role, target, list(argv[5:])


def _observation(
    role: str,
    target: Path,
    project_root: Path,
    venv_root: Path,
    site_packages: Path,
) -> Dict[str, Any]:
    pycache_prefix = Path(os.environ.get("PYTHONPYCACHEPREFIX", ""))
    startup = _verify_startup_and_environment(
        project_root, venv_root, pycache_prefix
    )
    preloaded_origins = _verify_preloaded_module_origins(project_root)
    pyvenv_path = venv_root / "pyvenv.cfg"
    pyvenv_values = _parse_pyvenv_config(pyvenv_path)
    lock_path = project_root / "requirements-benchmark.lock"
    pins = _parse_lock(lock_path)
    dependency = _verify_distributions(
        site_packages, venv_root, pins, project_root
    )
    launch_mode = os.environ["CARMA_GATE7_LAUNCH_MODE"]
    preimport_source = (
        _verify_formal_project_source(project_root)
        if launch_mode == "full"
        else {
            "schema_version": "carma-gate7-preimport-source-v1",
            "enforced": False,
            "launch_mode": "smoke",
        }
    )
    observation: Dict[str, Any] = {
        "schema_version": BOOTSTRAP_SCHEMA,
        "role": role,
        "target": target.relative_to(project_root).as_posix(),
        "target_sha256": _sha256_file(target),
        "project_root": str(project_root),
        "venv_root": str(venv_root),
        "site_packages": str(site_packages),
        "python": startup,
        "pyvenv": {
            "path": str(pyvenv_path.relative_to(project_root)),
            "sha256": _sha256_file(pyvenv_path),
            "values": pyvenv_values,
        },
        "lock": {
            "path": str(lock_path.relative_to(project_root)),
            "sha256": PINNED_LOCK_SHA256,
            "pin_count": PINNED_LOCK_PIN_COUNT,
            "pin_map_sha256": PINNED_LOCK_PIN_MAP_SHA256,
        },
        "dependency": dependency,
        "preimport_source": preimport_source,
        "preloaded_module_origins": preloaded_origins,
        "site_module_absent": True,
        "pth_executed": False,
    }
    observation["attestation_sha256"] = _canonical_mapping_sha256(observation)
    return observation


def _activate_and_run(
    role: str,
    target: Path,
    target_args: Sequence[str],
    project_root: Path,
    venv_root: Path,
    site_packages: Path,
    observation: Mapping[str, Any],
) -> None:
    # Seal three independent representations before exposing anything to the
    # target: immutable canonical bytes, a private deep postcheck baseline, and
    # a separate target-visible mapping.  A shallow MappingProxyType alone
    # would still permit mutation through nested dictionaries and lists.
    private_baseline_bytes, private_baseline = _canonical_observation_baseline(
        observation
    )

    # Under -S, CPython does not apply pyvenv.cfg.  Set only the virtual-env
    # prefixes needed by sysconfig/importlib after all pre-import checks pass.
    sys.prefix = str(venv_root)
    sys.exec_prefix = str(venv_root)

    # Keep stdlib first, followed by the sealed dependency root and then the
    # tagged project.  Raw insertion does not execute any verified .pth file.
    sys.path.append(str(site_packages))
    sys.path.append(str(project_root))
    if "site" in sys.modules:
        raise BootstrapError("site module appeared while activating verified paths")

    digest = str(private_baseline["attestation_sha256"])
    bootstrap_environment = {
        "CARMA_GATE7_BOOTSTRAP_SCHEMA": BOOTSTRAP_SCHEMA,
        "CARMA_GATE7_BOOTSTRAP_ROLE": role,
        "CARMA_GATE7_BOOTSTRAP_ATTESTATION_SHA256": digest,
    }
    os.environ.update(bootstrap_environment)
    # MappingProxyType protects top-level assignment.  The separate JSON deep
    # copy plus the canonical-byte postcheck below protects nested state.
    target_visible_observation = json.loads(private_baseline_bytes)
    sentinel = MappingProxyType(target_visible_observation)
    sys._gate7_v3_bootstrap_attestation = sentinel  # type: ignore[attr-defined]

    sys.argv = [str(target), *target_args]
    target_error = None
    target_traceback = None
    try:
        runpy.run_path(str(target), run_name="__main__")
    except BaseException as error:
        # Retain the target outcome while the authoritative parent performs
        # every post-target check.  A successful formal TERMINAL is appended
        # only after those checks, immediately before this outcome is reraised.
        target_error = error
        target_traceback = error.__traceback__
    finally:
        # SystemExit from the target still passes through this block.  Recheck
        # the cheap startup invariants and then re-hash the sealed dependency
        # environment so post-run mutation cannot be silently accepted.
        pycache_prefix = Path(
            private_baseline["python"]["environment"][
                "PYTHONPYCACHEPREFIX"
            ]
        )
        if (
            Path(sys.pycache_prefix or "") != pycache_prefix
            or pycache_prefix.is_symlink()
            or not pycache_prefix.is_dir()
            or any(pycache_prefix.iterdir())
        ):
            raise BootstrapError("formal pycache boundary changed during the target")
        if "site" in sys.modules:
            raise BootstrapError("target imported the site module")
        if sys.prefix != str(venv_root) or sys.exec_prefix != str(venv_root):
            raise BootstrapError("target changed the virtual-environment prefix")
        expected_path = [
            *private_baseline["python"]["initial_sys_path"],
            str(site_packages),
            str(project_root),
        ]
        if [str(Path(item).resolve()) for item in sys.path] != expected_path:
            raise BootstrapError("target changed the verified import path")
        current_sentinel = getattr(
            sys, "_gate7_v3_bootstrap_attestation", None
        )
        if current_sentinel is not sentinel:
            raise BootstrapError("target replaced the bootstrap runtime sentinel")
        try:
            sentinel_after_bytes, sentinel_after = (
                _canonical_observation_baseline(current_sentinel)
            )
        except BootstrapError as exc:
            raise BootstrapError(
                "target changed the bootstrap runtime sentinel bytes or digest"
            ) from exc
        if (
            sentinel_after_bytes != private_baseline_bytes
            or sentinel_after.get("attestation_sha256") != digest
        ):
            raise BootstrapError(
                "target changed the bootstrap runtime sentinel bytes or digest"
            )
        expected_environment = dict(private_baseline["python"]["environment"])
        expected_environment.update(bootstrap_environment)
        if dict(sorted(os.environ.items())) != dict(sorted(expected_environment.items())):
            raise BootstrapError("target changed the sealed process environment")
        if _sha256_file(target) != private_baseline["target_sha256"]:
            raise BootstrapError("bootstrap target bytes changed during execution")
        if private_baseline["preimport_source"].get("launch_mode") == "full":
            source_after = _verify_formal_project_source(project_root)
            if source_after != private_baseline["preimport_source"]:
                raise BootstrapError("formal project source changed during execution")
        pins = _parse_lock(project_root / "requirements-benchmark.lock")
        dependency_after = _verify_distributions(
            site_packages,
            venv_root,
            pins,
            project_root,
            require_inactive=False,
        )
        if dependency_after != private_baseline["dependency"]:
            raise BootstrapError("dependency environment changed during execution")

    if role == "parent" and os.environ.get("CARMA_GATE7_LAUNCH_MODE") == "full":
        if not isinstance(target_error, SystemExit):
            if target_error is None:
                raise BootstrapError(
                    "formal producer returned without a terminal exit status"
                )
            # Operational producer failures retain their nonclaimable failure
            # TERMINAL inside the producer lock.  They never receive a
            # bootstrap completion receipt.
        else:
            exit_status = target_error.code
            if (
                not isinstance(exit_status, int)
                or isinstance(exit_status, bool)
                or exit_status not in ADJUDICATION_EXIT_STATUSES.values()
            ):
                raise BootstrapError(
                    "formal producer returned an unsupported exit status"
                )
            _append_bootstrap_completed_terminal(
                project_root,
                private_baseline,
                exit_status,
            )

    if target_error is not None:
        raise target_error.with_traceback(target_traceback)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    if Path(__file__).is_symlink():
        raise BootstrapError("isolated bootstrap source may not be a symlink")
    project_root = Path(__file__).resolve().parents[1]
    role, target, target_args = _parse_invocation(arguments, project_root)

    # Do not resolve sys.executable here: the project interpreter is a symlink
    # to the pinned base runtime, while its lexical path identifies the venv.
    executable_lexical = _lexical_absolute(Path(sys.executable))
    venv_root = executable_lexical.parent.parent
    site_packages = (
        venv_root
        / "lib"
        / ("python%d.%d" % (sys.version_info.major, sys.version_info.minor))
        / "site-packages"
    )
    if not site_packages.is_dir() or site_packages.is_symlink():
        raise BootstrapError("formal site-packages root is missing or unsafe")

    observation = _observation(
        role, target, project_root, venv_root, site_packages
    )
    _activate_and_run(
        role,
        target,
        target_args,
        project_root,
        venv_root,
        site_packages,
        observation,
    )
    return 0


def _entrypoint() -> None:
    try:
        raise SystemExit(main())
    except BootstrapError as error:
        print("Gate 7 v3 isolated bootstrap rejected the run: %s" % error, file=sys.stderr)
        raise SystemExit(4)
    except Exception as error:
        print(
            "Gate 7 v3 isolated bootstrap failed closed after unexpected %s: %s"
            % (type(error).__name__, error),
            file=sys.stderr,
        )
        raise SystemExit(4)


if __name__ == "__main__":
    _entrypoint()
