import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts import generate_hashed_locks
from scripts import write_reproducibility_evidence as evidence


SOURCE_COMMIT = "a" * 40
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_docker_context_parent_exceptions_remain_tight():
    wrapper = PROJECT_ROOT / "scripts" / "run_qqp_validation.sh"
    assert wrapper.is_file()

    dockerignore = PROJECT_ROOT / ".dockerignore"
    if dockerignore.is_file():
        active_patterns = [
            line.strip()
            for line in dockerignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert tuple(active_patterns) == evidence.EXPECTED_DOCKERIGNORE_PATTERNS
        examples_parent = active_patterns.index("!examples/")
        examples_reexclude = active_patterns.index("examples/**")
        examples_benchmark = active_patterns.index("!examples/benchmark/")
        examples_contents = active_patterns.index("!examples/benchmark/**")
        assert examples_parent < examples_reexclude < examples_benchmark
        assert examples_benchmark < examples_contents

        assets_parent = active_patterns.index("!assets/")
        assets_reexclude = active_patterns.index("assets/**")
        cache_parent = active_patterns.index("!assets/tiktoken-cache/")
        cache_reexclude = active_patterns.index("assets/tiktoken-cache/**")
        cache_object = active_patterns.index(
            "!assets/tiktoken-cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
        )
        assert assets_parent < assets_reexclude < cache_parent
        assert cache_parent < cache_reexclude < cache_object

        scripts_parent = active_patterns.index("!scripts/")
        scripts_reexclude = active_patterns.index("scripts/**")
        required_scripts = (
            "!scripts/generate_hashed_locks.py",
            "!scripts/run_ci_benchmark.sh",
            "!scripts/run_qqp_validation.sh",
            "!scripts/run_reproducibility_gate.sh",
            "!scripts/verify_project.sh",
            "!scripts/write_reproducibility_evidence.py",
        )
        assert scripts_parent < scripts_reexclude
        assert all(
            scripts_reexclude < active_patterns.index(pattern)
            for pattern in required_scripts
        )


def _packaged_tiktoken_cache_root():
    source_cache = PROJECT_ROOT / "assets" / "tiktoken-cache"
    if source_cache.exists() or source_cache.is_symlink():
        return source_cache
    return Path(os.environ["TIKTOKEN_CACHE_DIR"])


def test_source_bound_tiktoken_cache_asset_has_exact_identity():
    official_url = (
        "https://openaipublic.blob.core.windows.net/encodings/"
        "cl100k_base.tiktoken"
    )
    assert hashlib.sha1(official_url.encode("utf-8")).hexdigest() == (
        evidence.TIKTOKEN_CACHE_KEY
    )
    cache_root = _packaged_tiktoken_cache_root()
    cache_file = cache_root / evidence.TIKTOKEN_CACHE_KEY
    assert not cache_root.is_symlink()
    assert list(cache_root.iterdir()) == [cache_file]
    assert not cache_file.is_symlink()
    payload = cache_file.read_bytes()
    assert len(payload) == evidence.TIKTOKEN_CACHE_SIZE
    assert hashlib.sha256(payload).hexdigest() == evidence.TIKTOKEN_CACHE_SHA256


def test_source_bound_tiktoken_cache_closes_the_network_fetch():
    environment = dict(os.environ)
    environment["TIKTOKEN_CACHE_DIR"] = str(_packaged_tiktoken_cache_root())
    program = """
import sys
sys.path.insert(0, sys.argv[1])
import tiktoken.load

def forbidden_fetch(*args, **kwargs):
    raise AssertionError("network fetch attempted despite the checked-in cache")

tiktoken.load.read_file = forbidden_fetch
from benchmarks.carma.moss import TiktokenCounter
counter = TiktokenCounter()
assert counter.count("offline tokenizer closure") > 0
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program, str(PROJECT_ROOT)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def _stub_source_checks(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "_verify_exact_head",
        lambda project_root, expected: SOURCE_COMMIT,
    )
    monkeypatch.setattr(evidence, "_check_generated_locks", lambda project_root: None)
    monkeypatch.setattr(
        evidence, "_verify_live_pip_environment", lambda installed_packages: None
    )
    monkeypatch.setattr(
        evidence, "_verify_checkout_credentials_absent", lambda project_root: None
    )
    monkeypatch.setattr(
        evidence,
        "_source_archive_attestation",
        lambda project_root, evidence_root, execution_root, source_commit: {
            "path": "source-archive.sha256",
            "sha256": "1" * 64,
            "archive_sha256": "2" * 64,
            "format": "git-archive-tar",
            "source_commit": source_commit,
            "tracked_file_count": 1,
        },
    )


def _project_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    pins = (
        ("pip", "26.2.1"),
        ("setuptools", "84.0.0"),
        ("wheel", "0.48.0"),
        ("fixture", "1"),
    )
    (root / "requirements-project.lock").write_text(
        "".join(
            "%s==%s " % (name, version)
            + "\\\n"
            + "    --hash=sha256:%s\n" % (str(index) * 64)
            for index, (name, version) in enumerate(pins, 1)
        ),
        encoding="utf-8",
    )
    (root / "Dockerfile.project").write_text(
        "\n".join(evidence.EXPECTED_DOCKERFILE_STATEMENTS) + "\n",
        encoding="utf-8",
    )
    (root / ".dockerignore").write_text(
        "\n".join(evidence.EXPECTED_DOCKERIGNORE_PATTERNS) + "\n",
        encoding="utf-8",
    )
    return root


def _benchmark(root, payload=b"same deterministic bytes\n"):
    root.mkdir(parents=True)
    for name in evidence.BENCHMARK_ARTIFACTS:
        (root / name).write_bytes(name.encode("ascii") + b":" + payload)


def test_host_writer_emits_analyzer_schema_only_after_pass(monkeypatch, tmp_path):
    _stub_source_checks(monkeypatch)
    monkeypatch.setattr(evidence.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(evidence.platform, "python_version", lambda: "3.12.13")
    project = _project_root(tmp_path)
    root = tmp_path / "host"
    root.mkdir()
    (root / "host-install.log").write_text(
        "No broken requirements found.\n[install] pip check PASS\n",
        encoding="utf-8",
    )
    (root / "host-packages.txt").write_text(
        "fixture==1\n"
        "gptcache==0.0.0\n"
        "pip==26.2.1\n"
        "setuptools==84.0.0\n"
        "wheel==0.48.0\n",
        encoding="utf-8",
    )
    (root / "host-verification.log").write_text(
        "fixture\n"
        "1 passed in 0.1s\n"
        "2 passed in 0.2s\n"
        "3 passed in 0.3s\n"
        "[verify] PASS\n",
        encoding="utf-8",
    )
    _benchmark(root / "benchmark")

    output = evidence._host_evidence(
        project, root, tmp_path / "execution", SOURCE_COMMIT
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "carma-host-verification-v1"
    assert payload["source_commit"] == SOURCE_COMMIT
    assert payload["source_archive"]["source_commit"] == SOURCE_COMMIT
    assert payload["dependency_lock"]["require_hashes"] is True
    assert payload["dependency_lock"]["only_binary"] is True
    assert set(payload["benchmark_artifacts"]) == set(evidence.BENCHMARK_ARTIFACTS)


def test_host_inventory_rejects_unpinned_packages(tmp_path):
    project = _project_root(tmp_path)
    install_log = tmp_path / "install.log"
    install_log.write_text("[install] pip check PASS\n", encoding="utf-8")
    packages = tmp_path / "packages.txt"
    packages.write_text(
        "fixture==1\n"
        "gptcache==0.0.0\n"
        "pip==26.2.1\n"
        "setuptools==84.0.0\n"
        "unexpected==1\n"
        "wheel==0.48.0\n",
        encoding="utf-8",
    )

    with pytest.raises(evidence.EvidenceError, match=r"extra=\['unexpected'\]"):
        evidence._validate_host_environment(
            project / "requirements-project.lock", install_log, packages
        )


def _container_inputs(root, second_phase=b"completed phase in 1s\n"):
    root.mkdir()
    (root / "container-source-commit.txt").write_text(
        SOURCE_COMMIT + "\n", encoding="utf-8"
    )
    (root / "container-image-id.txt").write_text(
        "sha256:" + "2" * 64 + "\n", encoding="utf-8"
    )
    (root / "container-image-inspect.json").write_text(
        json.dumps(
            [
                {
                    "Id": "sha256:" + "2" * 64,
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {
                        "User": "project",
                        "WorkingDir": "/workspace",
                        "Entrypoint": evidence.EXPECTED_ENTRYPOINT,
                        "Cmd": None,
                        "Env": [
                            "PYTHONDONTWRITEBYTECODE=1",
                            "PYTHONUNBUFFERED=1",
                            "PIP_CONFIG_FILE=/dev/null",
                            "PIP_DISABLE_PIP_VERSION_CHECK=1",
                            "PIP_INDEX_URL=https://pypi.org/simple",
                            "PIP_NO_INPUT=1",
                            "PIP_ROOT_USER_ACTION=ignore",
                            "TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache",
                        ],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (root / "docker-build.log").write_text("fixture build\n", encoding="utf-8")
    logs = (
        b"completed phase in 1s\n"
        b"1 passed in 1.25s\n2 passed in 2s\n3 passed in 3.5s\n"
        b"[verify] PASS\n",
        second_phase
        + b"1 passed in 9s\n2 passed in 8.0s\n3 passed in 7s\n"
        + b"[verify] PASS\n",
    )
    for index, raw in enumerate(logs, 1):
        (root / ("docker-run-%d.log" % index)).write_bytes(raw)
        run_root = root / ("docker-run-%d" % index)
        _benchmark(run_root / "benchmark")
        (root / ("docker-run-%d.inspect.json" % index)).write_text(
            json.dumps(
                [
                    {
                        "Id": str(index + 3) * 64,
                        "Image": "sha256:" + "2" * 64,
                        "Path": "bash",
                        "Args": ["scripts/verify_project.sh"],
                        "State": {
                            "Status": "exited",
                            "ExitCode": 0,
                            "OOMKilled": False,
                            "Error": "",
                        },
                        "HostConfig": {
                            "NetworkMode": "none",
                            "Privileged": False,
                            "CapAdd": None,
                            "CapDrop": ["ALL"],
                            "SecurityOpt": ["no-new-privileges:true"],
                        },
                        "Config": {
                            "User": "project",
                            "WorkingDir": "/workspace",
                            "Entrypoint": evidence.EXPECTED_ENTRYPOINT,
                            "Cmd": None,
                            "Env": [
                                "PYTHONDONTWRITEBYTECODE=1",
                                "PYTHONUNBUFFERED=1",
                                "PIP_CONFIG_FILE=/dev/null",
                                "PIP_DISABLE_PIP_VERSION_CHECK=1",
                                "PIP_INDEX_URL=https://pypi.org/simple",
                                "PIP_NO_INPUT=1",
                                "PIP_ROOT_USER_ACTION=ignore",
                                "TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache",
                                "CARMA_ARTIFACT_DIR=/artifacts",
                            ],
                        },
                        "Mounts": [
                            {
                                "Type": "bind",
                                "Source": str(run_root.resolve()),
                                "Destination": "/artifacts",
                                "RW": True,
                            }
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )


def test_container_writer_derives_normalization_and_compares_bytes(
    monkeypatch, tmp_path
):
    _stub_source_checks(monkeypatch)
    project = _project_root(tmp_path)
    root = tmp_path / "container"
    _container_inputs(root)

    output = evidence._container_evidence(
        project, root, tmp_path / "execution", SOURCE_COMMIT
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "carma-container-reproducibility-v1"
    assert payload["platform"] == "linux/amd64"
    assert payload["fresh_container_count"] == 2
    assert payload["runs"][0]["artifact_mount_source"].endswith("docker-run-1")
    assert payload["runs"][1]["artifact_mount_source"].endswith("docker-run-2")
    assert (root / "docker-run-1.nontiming.log").read_bytes() == (
        root / "docker-run-2.nontiming.log"
    ).read_bytes()
    assert b"in <elapsed>" in (root / "docker-run-1.nontiming.log").read_bytes()
    assert b"completed phase in 1s" in (
        root / "docker-run-1.nontiming.log"
    ).read_bytes()
    assert (root / "docker-run-1.sha256").read_bytes() == (
        root / "docker-run-2.sha256"
    ).read_bytes()
    assert all(payload["comparisons"].values())


def test_container_writer_does_not_emit_pass_for_non_timing_difference(
    monkeypatch, tmp_path
):
    _stub_source_checks(monkeypatch)
    project = _project_root(tmp_path)
    root = tmp_path / "container"
    _container_inputs(root, second_phase=b"completed phase in 9s\n")

    with pytest.raises(evidence.EvidenceError, match="non-timing container logs differ"):
        evidence._container_evidence(
            project, root, tmp_path / "execution", SOURCE_COMMIT
        )

    assert not (root / "container-reproducibility.json").exists()
    assert not (root / "requirements-project.lock").exists()
    for label in (1, 2):
        assert not (root / ("docker-run-%d.nontiming.log" % label)).exists()
        assert not (root / ("docker-run-%d.sha256" % label)).exists()


def test_container_writer_rejects_unattested_runtime_control(monkeypatch, tmp_path):
    _stub_source_checks(monkeypatch)
    project = _project_root(tmp_path)
    root = tmp_path / "container"
    _container_inputs(root)
    inspect_path = root / "docker-run-2.inspect.json"
    payload = json.loads(inspect_path.read_text(encoding="utf-8"))
    payload[0]["HostConfig"]["NetworkMode"] = "default"
    inspect_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="network mode was not none"):
        evidence._container_evidence(
            project, root, tmp_path / "execution", SOURCE_COMMIT
        )

    assert not (root / "container-reproducibility.json").exists()


def test_docker_contract_rejects_wrong_base_digest(tmp_path):
    project = _project_root(tmp_path)
    dockerfile = project / "Dockerfile.project"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8").replace(
            evidence.PYTHON_IMAGE_DIGEST, "sha256:" + "f" * 64
        ),
        encoding="utf-8",
    )

    with pytest.raises(evidence.EvidenceError, match="exact pinned build/runtime"):
        evidence._validate_docker_dependency_contract(project)


def test_image_contract_rejects_changed_entrypoint(tmp_path):
    root = tmp_path / "container"
    _container_inputs(root)
    inspect_path = root / "container-image-inspect.json"
    payload = json.loads(inspect_path.read_text(encoding="utf-8"))
    payload[0]["Config"]["Entrypoint"] = ["bash", "scripts/run_ci_benchmark.sh"]
    inspect_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="entrypoint does not run"):
        evidence._validate_image_inspect(inspect_path, "sha256:" + "2" * 64)


def test_image_contract_rejects_wrong_tiktoken_cache(tmp_path):
    root = tmp_path / "container"
    _container_inputs(root)
    inspect_path = root / "container-image-inspect.json"
    payload = json.loads(inspect_path.read_text(encoding="utf-8"))
    payload[0]["Config"]["Env"] = [
        value
        if not value.startswith("TIKTOKEN_CACHE_DIR=")
        else "TIKTOKEN_CACHE_DIR=/tmp/ambient-cache"
        for value in payload[0]["Config"]["Env"]
    ]
    inspect_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="pinned dependency environment"):
        evidence._validate_image_inspect(inspect_path, "sha256:" + "2" * 64)


def test_runtime_contract_rejects_changed_args(tmp_path):
    root = tmp_path / "container"
    _container_inputs(root)
    inspect_path = root / "docker-run-1.inspect.json"
    payload = json.loads(inspect_path.read_text(encoding="utf-8"))
    payload[0]["Args"] = ["scripts/run_ci_benchmark.sh"]
    inspect_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="exact verifier"):
        evidence._validate_container_inspect(
            inspect_path,
            "sha256:" + "2" * 64,
            root / "docker-run-1",
        )


def test_runtime_contract_rejects_false_no_new_privileges(tmp_path):
    root = tmp_path / "container"
    _container_inputs(root)
    inspect_path = root / "docker-run-1.inspect.json"
    payload = json.loads(inspect_path.read_text(encoding="utf-8"))
    payload[0]["HostConfig"]["SecurityOpt"] = ["no-new-privileges:false"]
    inspect_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="exact no-new-privileges"):
        evidence._validate_container_inspect(
            inspect_path,
            "sha256:" + "2" * 64,
            root / "docker-run-1",
        )


def test_runtime_contract_rejects_missing_tiktoken_cache(tmp_path):
    root = tmp_path / "container"
    _container_inputs(root)
    inspect_path = root / "docker-run-1.inspect.json"
    payload = json.loads(inspect_path.read_text(encoding="utf-8"))
    payload[0]["Config"]["Env"] = [
        value
        for value in payload[0]["Config"]["Env"]
        if not value.startswith("TIKTOKEN_CACHE_DIR=")
    ]
    inspect_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="required environment"):
        evidence._validate_container_inspect(
            inspect_path,
            "sha256:" + "2" * 64,
            root / "docker-run-1",
        )


def test_runtime_contract_rejects_boolean_exit_code(tmp_path):
    root = tmp_path / "container"
    _container_inputs(root)
    inspect_path = root / "docker-run-1.inspect.json"
    payload = json.loads(inspect_path.read_text(encoding="utf-8"))
    payload[0]["State"]["ExitCode"] = False
    inspect_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(evidence.EvidenceError, match="exit successfully"):
        evidence._validate_container_inspect(
            inspect_path,
            "sha256:" + "2" * 64,
            root / "docker-run-1",
        )


def _single_file_archive(name, payload):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.mode = 0o644
        member.mtime = 1
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def test_execution_tree_excludes_ignored_live_shadow_and_rejects_copied_shadow(
    tmp_path,
):
    project = tmp_path / "live-project"
    execution = tmp_path / "archived-execution"
    project.mkdir()
    execution.mkdir()
    tracked = b"VALUE = 'tracked'\n"
    archive_bytes = _single_file_archive("tracked.py", tracked)
    (project / "tracked.py").write_bytes(tracked)
    live_cache = project / "__pycache__"
    live_cache.mkdir()
    (live_cache / "tracked.cpython-312.pyc").write_bytes(b"ignored poison")
    (execution / "tracked.py").write_bytes(tracked)

    assert evidence._validate_execution_tree(project, execution, archive_bytes) == 1

    execution_cache = execution / "__pycache__"
    execution_cache.mkdir()
    (execution_cache / "tracked.cpython-312.pyc").write_bytes(b"copied poison")
    with pytest.raises(evidence.EvidenceError, match="unexpected file"):
        evidence._validate_execution_tree(project, execution, archive_bytes)


def test_source_archive_record_must_match_reproduced_git_archive(
    monkeypatch, tmp_path
):
    project = tmp_path / "live-project"
    execution = tmp_path / "archived-execution"
    root = tmp_path / "evidence"
    project.mkdir()
    execution.mkdir()
    root.mkdir()
    archive_bytes = _single_file_archive("tracked.py", b"tracked\n")
    (execution / "tracked.py").write_bytes(b"tracked\n")
    monkeypatch.setattr(
        evidence, "_git_archive_bytes", lambda project_root, source_commit: archive_bytes
    )
    (root / "source-archive.sha256").write_text(
        "%s  source-%s.tar\n" % ("0" * 64, SOURCE_COMMIT),
        encoding="utf-8",
    )

    with pytest.raises(evidence.EvidenceError, match="does not match exact Git HEAD"):
        evidence._source_archive_attestation(
            project, root, execution, SOURCE_COMMIT
        )


def test_verification_log_requires_project_pytest_summary():
    with pytest.raises(evidence.EvidenceError, match="expected at least 3"):
        evidence._require_pytest_summaries(
            b"1 passed in 1s\n2 passed in 2s\n[verify] PASS\n",
            "fixture verification log",
        )


def test_lock_requirements_include_cannot_escape_project_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "mutable-outside.txt"
    outside.write_text("fixture==1\n", encoding="utf-8")
    source = project / "requirements-project.txt"
    source.write_text("-r ../mutable-outside.txt\n", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes project root"):
        generate_hashed_locks._pins(source, {}, project)


def test_local_gate_is_bash32_safe_before_any_container():
    gate = (PROJECT_ROOT / "scripts" / "run_reproducibility_gate.sh").read_text(
        encoding="utf-8"
    )
    cleanup_start = gate.index("cleanup() {")
    cleanup_end = gate.index("\n}\ntrap cleanup EXIT", cleanup_start) + 3
    cleanup_function = gate[cleanup_start:cleanup_end]
    program = (
        "set -Eeuo pipefail\n"
        "BUILD_CONTEXT=''\nHOST_ENV=''\nSOURCE_ARCHIVE=''\n"
        "CONTAINER_IDS=()\n"
        + cleanup_function
        + "\ncleanup\n"
    )
    result = subprocess.run(
        ["/bin/bash", "-c", program],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    host_start = gate.index('echo "[repro] clean host verification"')
    host_end = gate.index(
        'echo "[repro] exact-HEAD linux/amd64 image build"', host_start
    )
    host_block = gate[host_start:host_end]
    assert 'export PATH="${HOST_ENV}/bin:${PATH}"' in host_block
    assert 'PYTHON_BIN="${HOST_PYTHON}"' not in host_block


def test_local_gate_source_archive_temp_is_bsd_mktemp_compatible(tmp_path):
    gate = (PROJECT_ROOT / "scripts" / "run_reproducibility_gate.sh").read_text(
        encoding="utf-8"
    )
    helper_start = gate.index("create_source_archive_temp() {")
    helper_end = gate.index("\n}", helper_start) + 2
    helper_function = gate[helper_start:helper_end]
    cleanup_start = gate.index("cleanup() {")
    cleanup_end = gate.index("\n}\ntrap cleanup EXIT", cleanup_start) + 3
    cleanup_function = gate[cleanup_start:cleanup_end]
    program = (
        "set -Eeuo pipefail\n"
        "BUILD_CONTEXT=''\nHOST_ENV=''\nCONTAINER_IDS=()\n"
        + helper_function
        + "\n"
        + cleanup_function
        + "\nSOURCE_ARCHIVE=\"$(create_source_archive_temp)\"\n"
        + "test -f \"${SOURCE_ARCHIVE}\"\n"
        + "printf '%s\\n' \"${SOURCE_ARCHIVE}\"\n"
        + "cleanup\n"
        + "test ! -e \"${SOURCE_ARCHIVE}\"\n"
    )
    environment = dict(os.environ)
    environment["TMPDIR"] = str(tmp_path)
    result = subprocess.run(
        ["/bin/bash", "-c", program],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    source_archive = Path(result.stdout.strip())
    assert source_archive.parent.resolve() == tmp_path.resolve()
    assert source_archive.name.startswith("carma-gate-source.")
    assert len(source_archive.name) == len("carma-gate-source.") + 6
    assert not source_archive.exists()
