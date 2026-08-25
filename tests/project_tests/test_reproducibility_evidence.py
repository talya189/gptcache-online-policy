import json
from pathlib import Path

import pytest

from scripts import write_reproducibility_evidence as evidence


SOURCE_COMMIT = "a" * 40


def _stub_source_checks(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "_verify_exact_head",
        lambda project_root, expected: SOURCE_COMMIT,
    )
    monkeypatch.setattr(evidence, "_check_generated_locks", lambda project_root: None)
    monkeypatch.setattr(
        evidence, "_verify_checkout_credentials_absent", lambda project_root: None
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
        "\n".join(
            (
                "FROM python:3.12.13-slim-bookworm@sha256:%s" % ("3" * 64),
                "COPY requirements-project.lock ./",
                "RUN python -m pip --isolated --no-input "
                "--index-url https://pypi.org/simple --require-hashes "
                "--only-binary=:all: --requirement requirements-project.lock",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (root / ".dockerignore").write_text("*\n!Dockerfile.project\n", encoding="utf-8")
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
        "fixture\n[verify] PASS\n", encoding="utf-8"
    )
    _benchmark(root / "benchmark")

    output = evidence._host_evidence(project, root, SOURCE_COMMIT)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "carma-host-verification-v1"
    assert payload["source_commit"] == SOURCE_COMMIT
    assert payload["dependency_lock"]["require_hashes"] is True
    assert payload["dependency_lock"]["only_binary"] is True
    assert set(payload["benchmark_artifacts"]) == set(evidence.BENCHMARK_ARTIFACTS)


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
                        "Env": [
                            "PIP_CONFIG_FILE=/dev/null",
                            "PIP_INDEX_URL=https://pypi.org/simple",
                            "PIP_NO_INPUT=1",
                        ],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (root / "docker-build.log").write_text("fixture build\n", encoding="utf-8")
    logs = (
        b"completed phase in 1s\n1 passed in 1.25s\n[verify] PASS\n",
        second_phase + b"1 passed in 9s\n[verify] PASS\n",
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
                        "State": {"Status": "exited", "ExitCode": 0},
                        "HostConfig": {
                            "NetworkMode": "none",
                            "CapDrop": ["ALL"],
                            "SecurityOpt": ["no-new-privileges"],
                        },
                        "Config": {"Env": ["CARMA_ARTIFACT_DIR=/artifacts"]},
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

    output = evidence._container_evidence(project, root, SOURCE_COMMIT)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "carma-container-reproducibility-v1"
    assert payload["platform"] == "linux/amd64"
    assert payload["fresh_container_count"] == 2
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
        evidence._container_evidence(project, root, SOURCE_COMMIT)

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
        evidence._container_evidence(project, root, SOURCE_COMMIT)

    assert not (root / "container-reproducibility.json").exists()
