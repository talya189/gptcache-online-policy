#!/usr/bin/env python3
"""Validate retained verification outputs and emit analyzer-ready evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "c59fb3a6152a4458b2a070ca183b61c4b614095f"
BENCHMARK_ARTIFACTS = ("manifest.json", "requests.jsonl", "runs.csv")
COMMIT = re.compile(r"[0-9a-f]{40}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
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
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise EvidenceError("%s is outside its evidence root: %s" % (label, path)) from exc
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
    statements = _dockerfile_statements(project_root / "Dockerfile.project")
    from_lines = [line for line in statements if line.startswith("FROM ")]
    if len(from_lines) != 1 or not re.fullmatch(
        r"FROM python:3\.12\.13-slim-bookworm@sha256:[0-9a-f]{64}",
        from_lines[0],
    ):
        raise EvidenceError("Dockerfile base is not an exact Python 3.12.13 digest")
    if "COPY requirements-project.lock ./" not in statements:
        raise EvidenceError("Dockerfile does not copy the generated primary lock")
    installs = [
        line
        for line in statements
        if line.startswith("RUN python -m pip ")
        and "--requirement requirements-project.lock" in line
    ]
    if len(installs) != 1:
        raise EvidenceError("Dockerfile does not contain one primary lock install")
    install = installs[0]
    for required in (
        "--isolated",
        "--no-input",
        "--index-url https://pypi.org/simple",
        "--require-hashes",
        "--only-binary=:all:",
        "--requirement requirements-project.lock",
    ):
        if required not in install:
            raise EvidenceError("Dockerfile lock install lacks %s" % required)

    dockerignore = [
        line.strip()
        for line in _read_text(
            project_root / ".dockerignore", "Docker context allowlist"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not dockerignore or dockerignore[0] != "*":
        raise EvidenceError(".dockerignore is not a deny-by-default allowlist")
    if any(line in ("!.git", "!.git/", "!.git/**") for line in dockerignore):
        raise EvidenceError(".dockerignore allows Git metadata into the image")


def _single_inspect(path: Path, label: str) -> Dict[str, Any]:
    payload = _load_json(path, label)
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise EvidenceError("%s must contain one Docker inspect object" % label)
    return payload[0]


def _validate_image_inspect(path: Path, expected_image_id: str) -> None:
    item = _single_inspect(path, "container image inspection")
    if str(item.get("Id", "")).lower() != expected_image_id:
        raise EvidenceError("inspected image ID does not match the recorded image ID")
    if item.get("Os") != "linux" or item.get("Architecture") != "amd64":
        raise EvidenceError("inspected image platform is not linux/amd64")
    config = item.get("Config")
    if not isinstance(config, dict) or config.get("User") != "project":
        raise EvidenceError("inspected image does not use the unprivileged project user")
    environment = config.get("Env")
    if not isinstance(environment, list):
        raise EvidenceError("inspected image environment is missing")
    required = {
        "PIP_CONFIG_FILE=/dev/null",
        "PIP_INDEX_URL=https://pypi.org/simple",
        "PIP_NO_INPUT=1",
    }
    if not required.issubset(set(environment)):
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
    if (
        not isinstance(state, dict)
        or state.get("Status") != "exited"
        or state.get("ExitCode") != 0
    ):
        raise EvidenceError("container did not exit successfully before inspection")
    host = item.get("HostConfig")
    if not isinstance(host, dict) or host.get("NetworkMode") != "none":
        raise EvidenceError("container runtime network mode was not none")
    dropped = host.get("CapDrop")
    if not isinstance(dropped, list) or {str(value).upper() for value in dropped} != {
        "ALL"
    }:
        raise EvidenceError("container did not drop all capabilities")
    security = host.get("SecurityOpt")
    if not isinstance(security, list) or not any(
        str(value).lower().startswith("no-new-privileges") for value in security
    ):
        raise EvidenceError("container did not enable no-new-privileges")
    config = item.get("Config")
    environment = config.get("Env") if isinstance(config, dict) else None
    if not isinstance(environment, list) or "CARMA_ARTIFACT_DIR=/artifacts" not in environment:
        raise EvidenceError("container artifact environment is missing")
    mounts = item.get("Mounts")
    matching = [
        mount
        for mount in mounts
        if isinstance(mount, dict) and mount.get("Destination") == "/artifacts"
    ] if isinstance(mounts, list) else []
    if len(matching) != 1:
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
    project_root: Path, evidence_root: Path, expected_commit: Optional[str]
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
    log = evidence_root / "host-verification.log"
    if _last_nonempty_line(log, "host verification log") != "[verify] PASS":
        raise EvidenceError("host verification log does not end in [verify] PASS")
    benchmark_root = evidence_root / "benchmark"
    benchmark = {
        name: _ref(
            evidence_root,
            benchmark_root / name,
            "host benchmark %s" % name,
        )
        for name in BENCHMARK_ARTIFACTS
    }

    evidence = {
        "schema_version": "carma-host-verification-v1",
        "status": "pass",
        "source_commit": source_commit,
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
    project_root: Path, evidence_root: Path, expected_commit: Optional[str]
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
    benchmark_pairs: Dict[str, Sequence[Tuple[Path, bytes]]] = {
        name: [] for name in BENCHMARK_ARTIFACTS
    }
    container_ids = []
    for index in (1, 2):
        label = "docker-run-%d" % index
        raw_log = evidence_root / (label + ".log")
        if _last_nonempty_line(raw_log, "%s raw log" % label) != "[verify] PASS":
            raise EvidenceError("%s raw log does not end in [verify] PASS" % label)
        normalized = evidence_root / (label + ".nontiming.log")
        normalized_bytes = _normalize_pytest_elapsed(_read_bytes(raw_log, label))
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
        container_ids.append(
            _validate_container_inspect(
                runtime_inspect, image_id, evidence_root / label
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
            "--expected-source-commit",
            help="optional full commit ID that must equal the clean checkout HEAD",
        )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    evidence_root = _resolve(project_root, args.evidence_root)
    try:
        if args.command == "host":
            output = _host_evidence(
                project_root, evidence_root, args.expected_source_commit
            )
        else:
            output = _container_evidence(
                project_root, evidence_root, args.expected_source_commit
            )
    except EvidenceError as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
