#!/usr/bin/env python3
"""Validate retained verification outputs and emit analyzer-ready evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "c59fb3a6152a4458b2a070ca183b61c4b614095f"
BENCHMARK_ARTIFACTS = ("manifest.json", "requests.jsonl", "runs.csv")
VERIFICATION_INVENTORY_SCHEMA = "carma-project-verification-inventory-v1"
VERIFICATION_CONTRACT_VERSION = "gate7d-onnx-v3-canonical-v1"
EXPECTED_PROJECT_TESTS = (
    "tests/project_tests/test_carma_analyze_results.py",
    "tests/project_tests/test_carma_failure_paths.py",
    "tests/project_tests/test_carma_integration.py",
    "tests/project_tests/test_carma_integration_protocol.py",
    "tests/project_tests/test_carma_phase_metrics.py",
    "tests/project_tests/test_carma_protocol_remediation.py",
    "tests/project_tests/test_gate7_audit.py",
    "tests/project_tests/test_gate7_onnx_integration.py",
    "tests/project_tests/test_gate7_trace.py",
    "tests/project_tests/test_gate7_v1_preservation.py",
    "tests/project_tests/test_gate7_v2_audit.py",
    "tests/project_tests/test_gate7_v2_isolated_bootstrap.py",
    "tests/project_tests/test_gate7_v2_onnx_integration.py",
    "tests/project_tests/test_gate7_v2_preservation.py",
    "tests/project_tests/test_gate7_v2_trace.py",
    "tests/project_tests/test_gate7_v3_audit.py",
    "tests/project_tests/test_gate7_v3_isolated_bootstrap.py",
    "tests/project_tests/test_gate7_v3_onnx_integration.py",
    "tests/project_tests/test_moss_benchmark.py",
    "tests/project_tests/test_qqp_v2.py",
    "tests/project_tests/test_qqp_wrapper.py",
    "tests/project_tests/test_reproducibility_evidence.py",
    "tests/unit_tests/eviction/test_carma.py",
)
V3_VERIFICATION_INPUTS = (
    "docs/project/gate7-v3-remediation-contract.md",
    "benchmarks/carma/gate7_v3_onnx_integration_benchmark.py",
    "benchmarks/carma/gate7_v3_audit.py",
    "scripts/gate7_v3_isolated_bootstrap.py",
    "scripts/run_gate7_v3_onnx_integration_benchmark.sh",
    "tests/project_tests/test_gate7_v2_preservation.py",
    "tests/project_tests/test_gate7_v3_audit.py",
    "tests/project_tests/test_gate7_v3_isolated_bootstrap.py",
    "tests/project_tests/test_gate7_v3_onnx_integration.py",
    "artifacts/samples/verification/gate7-v2-invalid/SHA256SUMS",
    "artifacts/samples/verification/gate7-v2-invalid/attempt-ledger.jsonl",
    "artifacts/samples/verification/gate7-v2-invalid/gate7-preterminal-adjudication.json",
    "artifacts/samples/verification/gate7-v2-invalid/manifest.json",
)
COMMIT = re.compile(r"[0-9a-f]{40}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
PYTHON_IMAGE_DIGEST = (
    "sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
)
TIKTOKEN_CACHE_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
TIKTOKEN_CACHE_SHA256 = (
    "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
)
TIKTOKEN_CACHE_SIZE = 1681126
TIKTOKEN_IMAGE_CACHE_DIR = "/opt/tiktoken-cache"
EXPECTED_ENTRYPOINT = ["bash", "scripts/verify_project.sh"]
EXPECTED_IMAGE_ENVIRONMENT = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PIP_CONFIG_FILE": "/dev/null",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_INDEX_URL": "https://pypi.org/simple",
    "PIP_NO_INPUT": "1",
    "PIP_ROOT_USER_ACTION": "ignore",
    "TIKTOKEN_CACHE_DIR": TIKTOKEN_IMAGE_CACHE_DIR,
}
EXPECTED_DOCKERFILE_STATEMENTS = (
    "FROM python:3.12.13-slim-bookworm@%s" % PYTHON_IMAGE_DIGEST,
    "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 "
    "PIP_CONFIG_FILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1 "
    "PIP_INDEX_URL=https://pypi.org/simple PIP_NO_INPUT=1 "
    "PIP_ROOT_USER_ACTION=ignore TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache",
    "WORKDIR /workspace",
    "COPY Dockerfile.project ./Dockerfile.project",
    "COPY requirements-project.lock requirements-benchmark.lock ./",
    "RUN python -m pip --isolated --disable-pip-version-check install "
    "--no-cache-dir --no-input --index-url https://pypi.org/simple "
    "--require-hashes --only-binary=:all: "
    "--requirement requirements-project.lock",
    "COPY assets/tiktoken-cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4 "
    "/opt/tiktoken-cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4",
    "RUN python -c 'import hashlib, pathlib, sys; root = "
    'pathlib.Path("/opt/tiktoken-cache"); path = root / '
    '"9b5ad71b2ce5302211f9c61530b329a4922fc6a4"; data = '
    "path.read_bytes(); valid = (not root.is_symlink() and not "
    "path.is_symlink() and list(root.iterdir()) == [path] and "
    "len(data) == 1681126 and hashlib.sha256(data).hexdigest() == "
    '"223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"); '
    "sys.exit(0 if valid else \"invalid cl100k_base cache asset\")'",
    "COPY setup.py README.md requirements.txt ./",
    "COPY gptcache ./gptcache",
    "COPY gptcache_server ./gptcache_server",
    "COPY benchmarks ./benchmarks",
    "COPY docs/project/gate7-remediation-contract.md "
    "./docs/project/gate7-remediation-contract.md",
    "COPY docs/project/gate7-v2-remediation-contract.md "
    "./docs/project/gate7-v2-remediation-contract.md",
    "COPY docs/project/gate7-v3-remediation-contract.md "
    "./docs/project/gate7-v3-remediation-contract.md",
    "COPY docs/project/evidence ./docs/project/evidence",
    "COPY artifacts/samples/verification/gate7-v2-invalid "
    "./artifacts/samples/verification/gate7-v2-invalid",
    "COPY examples/benchmark ./examples/benchmark",
    "COPY tests ./tests",
    "COPY scripts ./scripts",
    "RUN python -m pip --isolated --disable-pip-version-check install "
    "--no-cache-dir --no-input --no-index --no-deps --no-build-isolation "
    "--editable . && addgroup --system project && adduser --system "
    "--ingroup project --home /home/project project && "
    "chown -R project:project /workspace /home/project",
    "USER project",
    'ENTRYPOINT ["bash", "scripts/verify_project.sh"]',
)
EXPECTED_DOCKERIGNORE_PATTERNS = (
    "*",
    "!Dockerfile.project",
    "!requirements-benchmark.lock",
    "!requirements-project.lock",
    "!setup.py",
    "!README.md",
    "!requirements.txt",
    "!assets/",
    "assets/**",
    "!assets/tiktoken-cache/",
    "assets/tiktoken-cache/**",
    "!assets/tiktoken-cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4",
    "!gptcache/",
    "!gptcache/**",
    "!gptcache_server/",
    "!gptcache_server/**",
    "!benchmarks/",
    "!benchmarks/**",
    "!docs/",
    "docs/**",
    "!docs/project/",
    "docs/project/**",
    "!docs/project/gate7-remediation-contract.md",
    "!docs/project/gate7-v2-remediation-contract.md",
    "!docs/project/gate7-v3-remediation-contract.md",
    "!docs/project/evidence/",
    "!docs/project/evidence/**",
    "!artifacts/",
    "artifacts/**",
    "!artifacts/samples/",
    "artifacts/samples/**",
    "!artifacts/samples/verification/",
    "artifacts/samples/verification/**",
    "!artifacts/samples/verification/gate7-v2-invalid/",
    "artifacts/samples/verification/gate7-v2-invalid/**",
    "!artifacts/samples/verification/gate7-v2-invalid/SHA256SUMS",
    "!artifacts/samples/verification/gate7-v2-invalid/attempt-ledger.jsonl",
    "!artifacts/samples/verification/gate7-v2-invalid/gate7-preterminal-adjudication.json",
    "!artifacts/samples/verification/gate7-v2-invalid/manifest.json",
    "!examples/",
    "examples/**",
    "!examples/benchmark/",
    "!examples/benchmark/**",
    "!tests/",
    "!tests/**",
    "!scripts/",
    "scripts/**",
    "!scripts/generate_hashed_locks.py",
    "!scripts/gate7_v2_isolated_bootstrap.py",
    "!scripts/gate7_v3_isolated_bootstrap.py",
    "!scripts/run_ci_benchmark.sh",
    "!scripts/run_gate7_v2_onnx_integration_benchmark.sh",
    "!scripts/run_gate7_v3_onnx_integration_benchmark.sh",
    "!scripts/run_qqp_validation.sh",
    "!scripts/run_reproducibility_gate.sh",
    "!scripts/verify_project.sh",
    "!scripts/write_reproducibility_evidence.py",
)
PYTEST_SUMMARY = re.compile(
    rb"(?m)^(?P<result>[0-9]+ passed"
    rb"(?:, [0-9]+ (?:deselected|skipped|xfailed|xpassed|warnings?))*)"
    rb" in [0-9]+(?:[.][0-9]+)?s$"
)


class EvidenceError(RuntimeError):
    """Raised when retained outputs cannot support a passing status file."""


def _run_git(project_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise EvidenceError("git %s failed: %s" % (" ".join(args), detail.strip()))
    return result.stdout.strip()


def _verify_exact_head(project_root: Path, expected: Optional[str]) -> str:
    head = _run_git(project_root, "rev-parse", "--verify", "HEAD").lower()
    if COMMIT.fullmatch(head) is None:
        raise EvidenceError("HEAD did not resolve to a full commit ID")
    if expected is not None and head != expected.lower():
        raise EvidenceError("HEAD %s does not match expected commit %s" % (head, expected))
    status = _run_git(
        project_root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if status:
        raise EvidenceError(
            "verification evidence requires a clean exact-HEAD checkout; found:\n%s"
            % status
        )
    _run_git(project_root, "cat-file", "-e", BASELINE_COMMIT + "^{commit}")
    _run_git(project_root, "merge-base", "--is-ancestor", BASELINE_COMMIT, head)
    return head


def _check_generated_locks(project_root: Path) -> None:
    command = [
        sys.executable,
        str(project_root / "scripts" / "generate_hashed_locks.py"),
        "--project-root",
        str(project_root),
        "--check",
    ]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise EvidenceError("generated lock validation failed: %s" % detail.strip())


def _git_archive_bytes(project_root: Path, source_commit: str) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "tar.umask=0002",
                "-C",
                str(project_root),
                "archive",
                "--format=tar",
                source_commit,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise EvidenceError("could not reproduce the exact source archive: %s" % detail)
    if not result.stdout:
        raise EvidenceError("exact source archive is empty")
    return result.stdout


def _validate_execution_tree(
    project_root: Path, execution_root: Path, archive_bytes: bytes
) -> int:
    project_root = project_root.resolve()
    execution_root = execution_root.resolve()
    if not execution_root.is_dir():
        raise EvidenceError("archived execution root is not a directory")
    try:
        if os.path.commonpath((str(project_root), str(execution_root))) == str(
            project_root
        ):
            raise EvidenceError("host/container execution must not use the live checkout")
    except ValueError:
        pass

    expected: Dict[str, Tuple[str, bool]] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                name = member.name.rstrip("/")
                parts = name.split("/") if name else []
                if not name or name.startswith("/") or any(
                    part in ("", ".", "..") for part in parts
                ):
                    raise EvidenceError("exact source archive contains an unsafe path")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise EvidenceError(
                        "exact source archive contains a non-regular entry: %s" % name
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise EvidenceError("could not read archived source file %s" % name)
                expected[name] = (
                    _sha256_bytes(extracted.read()),
                    bool(member.mode & 0o111),
                )
    except (tarfile.TarError, OSError) as exc:
        raise EvidenceError("exact source archive is not a valid tar stream") from exc
    if not expected:
        raise EvidenceError("exact source archive contains no files")

    for relative, (expected_hash, expected_executable) in expected.items():
        candidate = execution_root.joinpath(*relative.split("/"))
        component = execution_root
        for part in relative.split("/"):
            component = component / part
            if component.is_symlink():
                raise EvidenceError(
                    "archived execution path contains a symlink: %s" % relative
                )
        if not candidate.is_file():
            raise EvidenceError("archived execution file is missing: %s" % relative)
        if _sha256(candidate, "archived execution file %s" % relative) != expected_hash:
            raise EvidenceError("archived execution file changed: %s" % relative)
        if bool(candidate.stat().st_mode & 0o111) != expected_executable:
            raise EvidenceError("archived execution mode changed: %s" % relative)

    allowed_generated_prefixes = (".pytest_cache/", "gptcache.egg-info/")
    for directory, directory_names, file_names in os.walk(
        str(execution_root), followlinks=False
    ):
        directory_path = Path(directory)
        for name in list(directory_names):
            candidate = directory_path / name
            if candidate.is_symlink():
                raise EvidenceError(
                    "archived execution tree contains a generated symlink: %s"
                    % candidate.relative_to(execution_root).as_posix()
                )
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(execution_root).as_posix()
            if candidate.is_symlink():
                raise EvidenceError(
                    "archived execution tree contains a generated symlink: %s"
                    % relative
                )
            if relative in expected:
                continue
            if any(relative.startswith(prefix) for prefix in allowed_generated_prefixes):
                continue
            raise EvidenceError(
                "archived execution tree contains an unexpected file: %s" % relative
            )
    return len(expected)


def _source_archive_attestation(
    project_root: Path,
    evidence_root: Path,
    execution_root: Path,
    source_commit: str,
) -> Dict[str, Any]:
    archive_bytes = _git_archive_bytes(project_root, source_commit)
    archive_sha256 = _sha256_bytes(archive_bytes)
    record = evidence_root / "source-archive.sha256"
    expected_record = "%s  source-%s.tar\n" % (archive_sha256, source_commit)
    if _read_text(record, "source archive digest") != expected_record:
        raise EvidenceError("source archive digest does not match exact Git HEAD")
    tracked_files = _validate_execution_tree(
        project_root, execution_root, archive_bytes
    )
    return dict(
        _ref(evidence_root, record, "source archive digest"),
        archive_sha256=archive_sha256,
        format="git-archive-tar",
        source_commit=source_commit,
        tracked_file_count=tracked_files,
    )


def _read_bytes(path: Path, label: str, allow_empty: bool = False) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise EvidenceError("%s is not a retained regular file: %s" % (label, path))
    data = path.read_bytes()
    if not allow_empty and not data:
        raise EvidenceError("%s is empty: %s" % (label, path))
    return data


def _read_text(path: Path, label: str) -> str:
    try:
        return _read_bytes(path, label).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("%s is not valid UTF-8: %s" % (label, path)) from exc


def _last_nonempty_line(path: Path, label: str) -> str:
    lines = [line for line in _read_text(path, label).splitlines() if line.strip()]
    if not lines:
        raise EvidenceError("%s has no non-empty lines" % label)
    return lines[-1]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path, label: str) -> str:
    return _sha256_bytes(_read_bytes(path, label, allow_empty=True))


def _normalize_pytest_elapsed(raw: bytes) -> bytes:
    """Normalize elapsed text only on successful pytest summary lines."""

    return PYTEST_SUMMARY.sub(rb"\g<result> in <elapsed>", raw)


def _require_pytest_summaries(raw: bytes, label: str, minimum: int = 3) -> None:
    count = sum(1 for _match in PYTEST_SUMMARY.finditer(raw))
    if count < minimum:
        raise EvidenceError(
            "%s contains %d recognized successful pytest summaries; expected at least %d"
            % (label, count, minimum)
        )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=str(path.parent), prefix=path.name + ".", delete=False
    ) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    try:
        os.chmod(str(temporary_path), 0o644)
        os.replace(str(temporary_path), str(path))
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _atomic_write(path, encoded)


def _ref(root: Path, path: Path, label: str) -> Dict[str, str]:
    root = root.resolve()
    try:
        unresolved_relative = path.relative_to(root)
    except ValueError as exc:
        raise EvidenceError("%s is outside its evidence root: %s" % (label, path)) from exc
    component = root
    for part in unresolved_relative.parts:
        component = component / part
        if component.is_symlink():
            raise EvidenceError("%s path contains a symlink: %s" % (label, path))
    try:
        relative = path.resolve().relative_to(root)
    except ValueError as exc:
        raise EvidenceError("%s resolves outside its evidence root: %s" % (label, path)) from exc
    return {"path": relative.as_posix(), "sha256": _sha256(path, label)}


def _ref_bytes(root: Path, path: Path, data: bytes, label: str) -> Dict[str, str]:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise EvidenceError("%s is outside its evidence root: %s" % (label, path)) from exc
    return {"path": relative.as_posix(), "sha256": _sha256_bytes(data)}


def _resolve(project_root: Path, value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (project_root / value).resolve()


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(_read_text(path, label))
    except json.JSONDecodeError as exc:
        raise EvidenceError("%s is not valid JSON: %s" % (label, path)) from exc


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _validate_host_environment(
    lock_path: Path, install_log: Path, installed_packages: Path
) -> None:
    if _last_nonempty_line(install_log, "host install log") != "[install] pip check PASS":
        raise EvidenceError("host install log does not end in [install] pip check PASS")
    locked: Dict[str, str] = {}
    pin = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+) \\$")
    for line in _read_text(lock_path, "project dependency lock").splitlines():
        match = pin.fullmatch(line.strip())
        if match is not None:
            locked[_normalized_name(match.group(1))] = match.group(2)
    if not locked:
        raise EvidenceError("project dependency lock contains no exact pins")

    installed: Dict[str, str] = {}
    freeze = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)$")
    for line in _read_text(installed_packages, "host installed packages").splitlines():
        if not line.strip():
            continue
        match = freeze.fullmatch(line.strip())
        if match is None:
            raise EvidenceError("host installed package line is not exact: %s" % line)
        key = _normalized_name(match.group(1))
        if key in installed:
            raise EvidenceError("host installed package list duplicates %s" % key)
        installed[key] = match.group(2)
    project_version = installed.pop("gptcache", None)
    if project_version is None:
        raise EvidenceError("editable GPTCache checkout is absent from host package list")
    if installed != locked:
        missing = sorted(set(locked) - set(installed))
        extra = sorted(set(installed) - set(locked))
        mismatched = sorted(
            name
            for name in set(installed) & set(locked)
            if installed[name] != locked[name]
        )
        raise EvidenceError(
            "host environment does not exactly match requirements-project.lock "
            "(missing=%s extra=%s mismatched=%s)" % (missing, extra, mismatched)
        )


def _verify_live_pip_environment(installed_packages: Path) -> None:
    try:
        check_result = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        list_result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=freeze"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise EvidenceError("could not execute pip check in the host environment") from exc
    if check_result.returncode != 0:
        raise EvidenceError(
            "live host pip check failed: %s"
            % (
                (check_result.stdout + check_result.stderr).strip()
                or "no diagnostic"
            )
        )
    if list_result.returncode != 0:
        raise EvidenceError("could not inventory the live host environment")
    recorded = sorted(
        line
        for line in _read_text(
            installed_packages, "host installed packages"
        ).splitlines()
        if line.strip()
    )
    live = sorted(line for line in list_result.stdout.splitlines() if line.strip())
    if recorded != live:
        raise EvidenceError(
            "recorded host packages do not match the live host environment"
        )


def _verify_checkout_credentials_absent(project_root: Path) -> None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "config",
                "--local",
                "--get-regexp",
                r"^http\..*\.extraheader$",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise EvidenceError("could not inspect local checkout credentials") from exc
    if result.returncode not in (0, 1):
        raise EvidenceError("could not inspect local checkout credentials")
    if result.stdout.strip():
        raise EvidenceError("the checkout retains an HTTP authorization header")


def _dockerfile_statements(path: Path) -> Sequence[str]:
    statements = []
    buffer = ""
    for raw in _read_text(path, "project Dockerfile").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        buffer = (buffer + " " + line).strip()
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip()
            continue
        statements.append(" ".join(buffer.split()))
        buffer = ""
    if buffer:
        raise EvidenceError("project Dockerfile ends in an unfinished continuation")
    return statements


def _validate_docker_dependency_contract(project_root: Path) -> None:
    statements = tuple(_dockerfile_statements(project_root / "Dockerfile.project"))
    if statements != EXPECTED_DOCKERFILE_STATEMENTS:
        raise EvidenceError(
            "Dockerfile does not match the exact pinned build/runtime contract"
        )

    dockerignore = [
        line.strip()
        for line in _read_text(
            project_root / ".dockerignore", "Docker context allowlist"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if tuple(dockerignore) != EXPECTED_DOCKERIGNORE_PATTERNS:
        raise EvidenceError(
            ".dockerignore does not match the exact deny-by-default context contract"
        )


def _verifier_project_tests(project_root: Path) -> Tuple[str, ...]:
    """Read the canonical Bash allowlist without treating discovery as authority."""

    path = project_root / "scripts" / "verify_project.sh"
    lines = _read_text(path, "canonical project verifier").splitlines()
    try:
        start = lines.index("expected_project_tests=(")
    except ValueError as exc:
        raise EvidenceError(
            "canonical project verifier lacks expected_project_tests"
        ) from exc
    observed: List[str] = []
    for line in lines[start + 1 :]:
        value = line.strip()
        if value == ")":
            break
        if not value or re.fullmatch(r"[A-Za-z0-9_./-]+", value) is None:
            raise EvidenceError(
                "canonical project verifier has a malformed project-test entry"
            )
        observed.append(value)
    else:
        raise EvidenceError(
            "canonical project verifier project-test allowlist is unterminated"
        )
    if tuple(observed) != EXPECTED_PROJECT_TESTS:
        raise EvidenceError(
            "canonical project verifier test inventory differs from the frozen v3 inventory"
        )
    return tuple(observed)


def _source_identity(project_root: Path, relative: str) -> Dict[str, Any]:
    path = project_root.joinpath(*relative.split("/"))
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(
            "verification inventory input is missing or unsafe: %s" % relative
        )
    data = path.read_bytes()
    return {
        "path": relative,
        "sha256": _sha256_bytes(data),
        "bytes": len(data),
    }


def _verification_inventory(project_root: Path) -> Dict[str, Any]:
    tests = _verifier_project_tests(project_root)
    return {
        "schema_version": VERIFICATION_INVENTORY_SCHEMA,
        "contract_version": VERIFICATION_CONTRACT_VERSION,
        "gate7_experiment_id": "gate7d-onnx-v3",
        "historical_evidence_reclassified": False,
        "verifier": _source_identity(project_root, "scripts/verify_project.sh"),
        "project_test_count": len(tests),
        "project_tests": [
            _source_identity(project_root, relative) for relative in tests
        ],
        "v3_input_count": len(V3_VERIFICATION_INPUTS),
        "v3_inputs": [
            _source_identity(project_root, relative)
            for relative in V3_VERIFICATION_INPUTS
        ],
    }


def _single_inspect(path: Path, label: str) -> Dict[str, Any]:
    payload = _load_json(path, label)
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise EvidenceError("%s must contain one Docker inspect object" % label)
    return payload[0]


def _environment_map(value: Any, label: str) -> Dict[str, str]:
    if not isinstance(value, list):
        raise EvidenceError("%s environment is missing" % label)
    environment: Dict[str, str] = {}
    for item in value:
        if not isinstance(item, str) or "=" not in item:
            raise EvidenceError("%s environment entry is invalid" % label)
        key, item_value = item.split("=", 1)
        if not key or key in environment:
            raise EvidenceError("%s environment contains a duplicate key" % label)
        environment[key] = item_value
    return environment


def _validate_image_inspect(path: Path, expected_image_id: str) -> None:
    item = _single_inspect(path, "container image inspection")
    if str(item.get("Id", "")).lower() != expected_image_id:
        raise EvidenceError("inspected image ID does not match the recorded image ID")
    if item.get("Os") != "linux" or item.get("Architecture") != "amd64":
        raise EvidenceError("inspected image platform is not linux/amd64")
    config = item.get("Config")
    if not isinstance(config, dict):
        raise EvidenceError("inspected image configuration is missing")
    if config.get("User") != "project":
        raise EvidenceError("inspected image does not use the unprivileged project user")
    if config.get("WorkingDir") != "/workspace":
        raise EvidenceError("inspected image working directory is not /workspace")
    if config.get("Entrypoint") != EXPECTED_ENTRYPOINT:
        raise EvidenceError("inspected image entrypoint does not run the verifier")
    if config.get("Cmd") not in (None, []):
        raise EvidenceError("inspected image command adds unexpected verifier arguments")
    environment = _environment_map(config.get("Env"), "inspected image")
    if any(
        environment.get(key) != value
        for key, value in EXPECTED_IMAGE_ENVIRONMENT.items()
    ):
        raise EvidenceError("inspected image lacks the pinned dependency environment")


def _validate_container_inspect(
    path: Path, expected_image_id: str, expected_mount: Path
) -> str:
    item = _single_inspect(path, "container runtime inspection")
    container_id = str(item.get("Id", "")).lower()
    if CONTAINER_ID.fullmatch(container_id) is None:
        raise EvidenceError("container runtime inspection lacks a full container ID")
    if str(item.get("Image", "")).lower() != expected_image_id:
        raise EvidenceError("container did not run the recorded image ID")
    state = item.get("State")
    exit_code = state.get("ExitCode") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or state.get("Status") != "exited"
        or type(exit_code) is not int
        or exit_code != 0
        or state.get("OOMKilled") is not False
        or state.get("Error") != ""
    ):
        raise EvidenceError("container did not exit successfully before inspection")
    host = item.get("HostConfig")
    if not isinstance(host, dict) or host.get("NetworkMode") != "none":
        raise EvidenceError("container runtime network mode was not none")
    if host.get("Privileged") is not False:
        raise EvidenceError("container runtime was privileged")
    if host.get("CapAdd") not in (None, []):
        raise EvidenceError("container runtime added capabilities")
    dropped = host.get("CapDrop")
    if not isinstance(dropped, list) or {str(value).upper() for value in dropped} != {
        "ALL"
    }:
        raise EvidenceError("container did not drop all capabilities")
    security = host.get("SecurityOpt")
    if security not in (
        ["no-new-privileges:true"],
        ["no-new-privileges=true"],
    ):
        raise EvidenceError("container did not enable exact no-new-privileges=true")
    config = item.get("Config")
    if not isinstance(config, dict):
        raise EvidenceError("container runtime configuration is missing")
    if config.get("User") != "project" or config.get("WorkingDir") != "/workspace":
        raise EvidenceError("container runtime user/working directory changed")
    if config.get("Entrypoint") != EXPECTED_ENTRYPOINT or config.get("Cmd") not in (
        None,
        [],
    ):
        raise EvidenceError("container runtime command configuration changed")
    if item.get("Path") != "bash" or item.get("Args") != [
        "scripts/verify_project.sh"
    ]:
        raise EvidenceError("container runtime did not execute the exact verifier")
    environment = _environment_map(config.get("Env"), "container runtime")
    required_environment = dict(EXPECTED_IMAGE_ENVIRONMENT)
    required_environment["CARMA_ARTIFACT_DIR"] = "/artifacts"
    if any(
        environment.get(key) != value
        for key, value in required_environment.items()
    ):
        raise EvidenceError("container required environment is missing or changed")
    mounts = item.get("Mounts")
    matching = [
        mount
        for mount in mounts
        if isinstance(mount, dict) and mount.get("Destination") == "/artifacts"
    ] if isinstance(mounts, list) else []
    if not isinstance(mounts, list) or len(mounts) != 1 or len(matching) != 1:
        raise EvidenceError("container has no unique /artifacts bind mount")
    mount = matching[0]
    source = mount.get("Source")
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not True
        or not isinstance(source, str)
        or Path(source).resolve() != expected_mount.resolve()
    ):
        raise EvidenceError("container /artifacts mount does not match retained output")
    return container_id


def _refuse_existing(paths: Sequence[Path]) -> None:
    existing = [str(path) for path in paths if path.exists() or path.is_symlink()]
    if existing:
        raise EvidenceError(
            "refusing to overwrite existing generated evidence:\n%s"
            % "\n".join(existing)
        )


def _host_evidence(
    project_root: Path,
    evidence_root: Path,
    execution_root: Path,
    expected_commit: Optional[str],
) -> Path:
    output = evidence_root / "host-verification.json"
    lock_copy = evidence_root / "requirements-project.lock"
    _refuse_existing((output, lock_copy))
    source_commit = _verify_exact_head(project_root, expected_commit)
    _check_generated_locks(project_root)

    if platform.python_implementation() != "CPython":
        raise EvidenceError("host evidence requires CPython")
    if platform.python_version() != "3.12.13":
        raise EvidenceError(
            "host evidence requires Python 3.12.13; found %s"
            % platform.python_version()
        )

    source_lock = project_root / "requirements-project.lock"
    lock_bytes = _read_bytes(source_lock, "project dependency lock")
    install_log = evidence_root / "host-install.log"
    installed_packages = evidence_root / "host-packages.txt"
    _validate_host_environment(source_lock, install_log, installed_packages)
    _verify_live_pip_environment(installed_packages)
    log = evidence_root / "host-verification.log"
    if _last_nonempty_line(log, "host verification log") != "[verify] PASS":
        raise EvidenceError("host verification log does not end in [verify] PASS")
    _require_pytest_summaries(
        _read_bytes(log, "host verification log"), "host verification log"
    )
    benchmark_root = evidence_root / "benchmark"
    benchmark = {
        name: _ref(
            evidence_root,
            benchmark_root / name,
            "host benchmark %s" % name,
        )
        for name in BENCHMARK_ARTIFACTS
    }
    source_archive = _source_archive_attestation(
        project_root, evidence_root, execution_root, source_commit
    )
    verification_inventory = _verification_inventory(project_root)

    evidence = {
        "schema_version": "carma-host-verification-v1",
        "status": "pass",
        "source_commit": source_commit,
        "source_archive": source_archive,
        "verification_inventory": verification_inventory,
        "baseline_ancestor_verified": True,
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": platform.platform(),
        "dependency_lock": dict(
            _ref_bytes(
                evidence_root, lock_copy, lock_bytes, "host dependency lock"
            ),
            require_hashes=True,
            only_binary=True,
            index_url="https://pypi.org/simple",
        ),
        "environment_install_log": _ref(
            evidence_root, install_log, "host install log"
        ),
        "installed_packages": _ref(
            evidence_root, installed_packages, "host installed packages"
        ),
        "verification_log": _ref(
            evidence_root, log, "host verification log"
        ),
        "benchmark_artifacts": benchmark,
    }
    if _verify_exact_head(project_root, source_commit) != source_commit:
        raise EvidenceError("source commit changed while host evidence was produced")
    _atomic_write(lock_copy, lock_bytes)
    _atomic_json(output, evidence)
    return output


def _container_evidence(
    project_root: Path,
    evidence_root: Path,
    execution_root: Path,
    expected_commit: Optional[str],
) -> Path:
    output = evidence_root / "container-reproducibility.json"
    lock_copy = evidence_root / "requirements-project.lock"
    lock_digest = evidence_root / "requirements-project.lock.sha256"
    generated = [output, lock_copy, lock_digest]
    for label in (1, 2):
        generated.extend(
            (
                evidence_root / ("docker-run-%d.nontiming.log" % label),
                evidence_root / ("docker-run-%d.sha256" % label),
            )
        )
    _refuse_existing(generated)
    source_commit = _verify_exact_head(project_root, expected_commit)
    _check_generated_locks(project_root)
    _verify_checkout_credentials_absent(project_root)
    _validate_docker_dependency_contract(project_root)

    recorded_commit = _read_text(
        evidence_root / "container-source-commit.txt",
        "container source commit",
    ).strip().lower()
    if COMMIT.fullmatch(recorded_commit) is None or recorded_commit != source_commit:
        raise EvidenceError(
            "container source commit %r does not match exact HEAD %s"
            % (recorded_commit, source_commit)
        )
    source_archive = _source_archive_attestation(
        project_root, evidence_root, execution_root, source_commit
    )
    verification_inventory = _verification_inventory(project_root)
    image_id = _read_text(
        evidence_root / "container-image-id.txt", "container image ID"
    ).strip().lower()
    if IMAGE_ID.fullmatch(image_id) is None:
        raise EvidenceError("container image ID is not a sha256 digest")
    build_log = evidence_root / "docker-build.log"
    _read_bytes(build_log, "container build log")
    image_inspect = evidence_root / "container-image-inspect.json"
    _validate_image_inspect(image_inspect, image_id)

    runs = []
    normalized_outputs = []
    hash_list_outputs = []
    benchmark_pairs: Dict[str, List[Tuple[Path, bytes]]] = {
        name: [] for name in BENCHMARK_ARTIFACTS
    }
    container_ids = []
    for index in (1, 2):
        label = "docker-run-%d" % index
        raw_log = evidence_root / (label + ".log")
        if _last_nonempty_line(raw_log, "%s raw log" % label) != "[verify] PASS":
            raise EvidenceError("%s raw log does not end in [verify] PASS" % label)
        raw_log_bytes = _read_bytes(raw_log, "%s raw log" % label)
        _require_pytest_summaries(raw_log_bytes, "%s raw log" % label)
        normalized = evidence_root / (label + ".nontiming.log")
        normalized_bytes = _normalize_pytest_elapsed(raw_log_bytes)
        normalized_outputs.append((normalized, normalized_bytes))

        benchmark_root = evidence_root / label / "benchmark"
        benchmark = {}
        hash_lines = []
        for name in BENCHMARK_ARTIFACTS:
            artifact = benchmark_root / name
            artifact_bytes = _read_bytes(
                artifact, "%s benchmark %s" % (label, name)
            )
            artifact_ref = _ref(
                evidence_root, artifact, "%s benchmark %s" % (label, name)
            )
            benchmark[name] = artifact_ref
            benchmark_pairs[name].append((artifact, artifact_bytes))
            hash_lines.append("%s  %s\n" % (artifact_ref["sha256"], name))
        hash_list = evidence_root / (label + ".sha256")
        hash_list_bytes = "".join(hash_lines).encode("ascii")
        hash_list_outputs.append((hash_list, hash_list_bytes))
        runtime_inspect = evidence_root / (label + ".inspect.json")
        artifact_mount_source = str((evidence_root / label).resolve())
        container_ids.append(
            _validate_container_inspect(
                runtime_inspect, image_id, Path(artifact_mount_source)
            )
        )

        runs.append(
            {
                "label": label,
                "raw_log": _ref(evidence_root, raw_log, "%s raw log" % label),
                "non_timing_log": _ref_bytes(
                    evidence_root,
                    normalized,
                    normalized_bytes,
                    "%s non-timing log" % label,
                ),
                "hash_list": _ref_bytes(
                    evidence_root,
                    hash_list,
                    hash_list_bytes,
                    "%s hash list" % label,
                ),
                "container_inspect": _ref(
                    evidence_root,
                    runtime_inspect,
                    "%s runtime inspection" % label,
                ),
                "artifact_mount_source": artifact_mount_source,
                "benchmark_artifacts": benchmark,
            }
        )

    if normalized_outputs[0][1] != normalized_outputs[1][1]:
        raise EvidenceError("derived non-timing container logs differ")
    if hash_list_outputs[0][1] != hash_list_outputs[1][1]:
        raise EvidenceError("derived container benchmark hash lists differ")
    if len(set(container_ids)) != 2:
        raise EvidenceError("paired-container evidence reused a container ID")
    for name, paths in benchmark_pairs.items():
        if paths[0][1] != paths[1][1]:
            raise EvidenceError("container benchmark %s differs between runs" % name)

    source_lock = project_root / "requirements-project.lock"
    lock_bytes = _read_bytes(source_lock, "project dependency lock")
    copied_lock_hash = _sha256_bytes(lock_bytes)
    lock_digest_bytes = (
        "%s  requirements-project.lock\n" % copied_lock_hash
    ).encode("ascii")
    evidence = {
        "schema_version": "carma-container-reproducibility-v1",
        "status": "pass",
        "source_commit": source_commit,
        "source_archive": source_archive,
        "verification_inventory": verification_inventory,
        "image_id": image_id,
        "platform": "linux/amd64",
        "fresh_container_count": 2,
        "controls": {
            "runtime_network": "none",
            "capabilities_dropped": "ALL",
            "no_new_privileges": True,
            "dependency_hashes_required": True,
            "binary_only_dependencies": True,
            "dependency_index_url": "https://pypi.org/simple",
            "checkout_credentials_persisted": False,
            "generated_lock_check_passed": True,
        },
        "dependency_lock": _ref_bytes(
            evidence_root, lock_copy, lock_bytes, "container dependency lock"
        ),
        "build_log": _ref(evidence_root, build_log, "container build log"),
        "image_inspect": _ref(
            evidence_root, image_inspect, "container image inspection"
        ),
        "comparisons": {
            "non_timing_logs_identical": True,
            "hash_lists_identical": True,
            "manifest_json_identical": True,
            "requests_jsonl_identical": True,
            "runs_csv_identical": True,
        },
        "runs": runs,
    }
    if _verify_exact_head(project_root, source_commit) != source_commit:
        raise EvidenceError("source commit changed while container evidence was produced")
    for path, data in normalized_outputs + hash_list_outputs:
        _atomic_write(path, data)
    _atomic_write(lock_copy, lock_bytes)
    _atomic_write(lock_digest, lock_digest_bytes)
    _atomic_json(output, evidence)
    return output


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="clean Git checkout whose exact HEAD produced the evidence",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, default_root in (
        ("host", Path("artifacts/ci")),
        ("container", Path("artifacts")),
    ):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--evidence-root", type=Path, default=default_root)
        subparser.add_argument(
            "--execution-root",
            type=Path,
            required=True,
            help="materialized exact Git archive used for installation or image build",
        )
        subparser.add_argument(
            "--expected-source-commit",
            help="optional full commit ID that must equal the clean checkout HEAD",
        )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    evidence_root = _resolve(project_root, args.evidence_root)
    execution_root = _resolve(project_root, args.execution_root)
    try:
        if args.command == "host":
            output = _host_evidence(
                project_root,
                evidence_root,
                execution_root,
                args.expected_source_commit,
            )
        else:
            output = _container_evidence(
                project_root,
                evidence_root,
                execution_root,
                args.expected_source_commit,
            )
    except EvidenceError as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
