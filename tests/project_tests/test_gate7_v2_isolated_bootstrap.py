"""Focused fail-closed tests for the Gate 7 v2 isolated bootstrap."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import gate7_v2_isolated_bootstrap as bootstrap


TAG_OBJECT = "a" * 40
HEAD_COMMIT = "b" * 40
TAG_REF = "refs/tags/%s" % bootstrap.FORMAL_SOURCE_TAG
PEELED_REF = "%s^{}" % TAG_REF


def _remote_output(tag_object=TAG_OBJECT, peeled_commit=HEAD_COMMIT):
    return (
        "%s\t%s\n%s\t%s\n"
        % (tag_object, TAG_REF, peeled_commit, PEELED_REF)
    ).encode("utf-8")


def _install_remote_git(
    monkeypatch,
    *,
    fetch_output=None,
    push_output=None,
    remote_output=None,
):
    exact_url = (bootstrap.FORMAL_SUBMISSION_URL + "\n").encode("utf-8")
    outputs = {
        (
            "remote",
            "get-url",
            "--all",
            bootstrap.FORMAL_SUBMISSION_REMOTE,
        ): exact_url if fetch_output is None else fetch_output,
        (
            "remote",
            "get-url",
            "--push",
            "--all",
            bootstrap.FORMAL_SUBMISSION_REMOTE,
        ): exact_url if push_output is None else push_output,
        (
            "ls-remote",
            "--exit-code",
            "--tags",
            bootstrap.FORMAL_SUBMISSION_URL,
            TAG_REF,
            PEELED_REF,
        ): _remote_output() if remote_output is None else remote_output,
    }
    calls = []

    def fake_git_bytes(_project_root, *arguments):
        calls.append(arguments)
        return outputs[arguments]

    monkeypatch.setattr(bootstrap, "_git_bytes", fake_git_bytes)
    return calls


def test_submission_remote_anchor_retains_exact_urls_and_remote_tag(monkeypatch):
    calls = _install_remote_git(monkeypatch)

    remote = bootstrap._formal_submission_remote_anchor(
        Path("/project"), TAG_OBJECT, HEAD_COMMIT, HEAD_COMMIT
    )

    assert remote == {
        "remote_name": "submission",
        "fetch_url": bootstrap.FORMAL_SUBMISSION_URL,
        "fetch_url_count": 1,
        "push_url": bootstrap.FORMAL_SUBMISSION_URL,
        "push_url_count": 1,
        "tag_ref": TAG_REF,
        "tag_object_id": TAG_OBJECT,
        "peeled_ref": PEELED_REF,
        "peeled_commit": HEAD_COMMIT,
    }
    assert calls[-1] == (
        "ls-remote",
        "--exit-code",
        "--tags",
        bootstrap.FORMAL_SUBMISSION_URL,
        TAG_REF,
        PEELED_REF,
    )


@pytest.mark.parametrize("url_kind", ["fetch", "push"])
def test_submission_remote_anchor_rejects_non_singleton_exact_url(
    monkeypatch, url_kind
):
    exact_url = (bootstrap.FORMAL_SUBMISSION_URL + "\n").encode("utf-8")
    overrides = {"%s_output" % url_kind: exact_url + exact_url}
    _install_remote_git(monkeypatch, **overrides)

    with pytest.raises(
        bootstrap.BootstrapError,
        match="one exact fetch and push URL",
    ):
        bootstrap._formal_submission_remote_anchor(
            Path("/project"), TAG_OBJECT, HEAD_COMMIT, HEAD_COMMIT
        )


@pytest.mark.parametrize(
    "remote_output",
    [
        _remote_output(tag_object="c" * 40),
        _remote_output(peeled_commit="d" * 40),
        ("%s\t%s\n" % (TAG_OBJECT, TAG_REF)).encode("utf-8"),
    ],
)
def test_submission_remote_anchor_rejects_remote_tag_drift(
    monkeypatch, remote_output
):
    _install_remote_git(monkeypatch, remote_output=remote_output)

    with pytest.raises(
        bootstrap.BootstrapError,
        match="remote tag does not authenticate local HEAD",
    ):
        bootstrap._formal_submission_remote_anchor(
            Path("/project"), TAG_OBJECT, HEAD_COMMIT, HEAD_COMMIT
        )


def _observation(target, initial_path, environment):
    launch_mode = environment["CARMA_GATE7_LAUNCH_MODE"]
    preimport_source = (
        {
            "schema_version": "carma-gate7-preimport-source-v1",
            "enforced": False,
            "launch_mode": "smoke",
        }
        if launch_mode == "smoke"
        else {
            "schema_version": "carma-gate7-preimport-source-v1",
            "enforced": True,
            "launch_mode": "full",
            "sealed": True,
        }
    )
    value = {
        "schema_version": bootstrap.BOOTSTRAP_SCHEMA,
        "role": "parent",
        "target": target.name,
        "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "python": {
            "initial_sys_path": [str(initial_path)],
            "environment": dict(environment),
        },
        "dependency": {"sealed": True},
        "preimport_source": preimport_source,
    }
    value["attestation_sha256"] = bootstrap._canonical_mapping_sha256(value)
    return value


def _activation_fixture(
    tmp_path, monkeypatch, target_callback, launch_mode="smoke"
):
    project_root = tmp_path / "project"
    venv_root = project_root / ".venv"
    site_packages = venv_root / "site-packages"
    initial_path = tmp_path / "stdlib"
    pycache_prefix = tmp_path / "pycache"
    for directory in (
        project_root,
        venv_root,
        site_packages,
        initial_path,
        pycache_prefix,
    ):
        directory.mkdir(exist_ok=True)
    target = project_root / "target.py"
    target.write_text("raise SystemExit(0)\n", encoding="utf-8")
    environment = {
        "CARMA_GATE7_LAUNCH_MODE": launch_mode,
        "PYTHONPYCACHEPREFIX": str(pycache_prefix),
    }
    observation = _observation(target, initial_path.resolve(), environment)
    fake_sys = SimpleNamespace(
        prefix="/unverified",
        exec_prefix="/unverified",
        path=[str(initial_path.resolve())],
        modules={},
        pycache_prefix=str(pycache_prefix),
        argv=[],
    )
    fake_environment = dict(environment)
    monkeypatch.setattr(bootstrap, "sys", fake_sys)
    monkeypatch.setattr(bootstrap.os, "environ", fake_environment)
    monkeypatch.setattr(
        bootstrap.runpy,
        "run_path",
        lambda *_args, **_kwargs: target_callback(
            fake_sys, fake_environment, observation
        ),
    )
    monkeypatch.setattr(bootstrap, "_parse_lock", lambda _path: {})
    monkeypatch.setattr(
        bootstrap,
        "_verify_distributions",
        lambda *_args, **_kwargs: {"sealed": True},
    )
    monkeypatch.setattr(
        bootstrap,
        "_verify_formal_project_source",
        lambda _project_root: observation["preimport_source"],
    )
    return {
        "target": target,
        "project_root": project_root,
        "venv_root": venv_root,
        "site_packages": site_packages,
        "observation": observation,
    }


def _activate(fixture):
    return bootstrap._activate_and_run(
        "parent",
        fixture["target"],
        [],
        fixture["project_root"],
        fixture["venv_root"],
        fixture["site_packages"],
        fixture["observation"],
    )


def test_nested_mappingproxy_mutation_cannot_change_private_baseline(
    tmp_path, monkeypatch
):
    def mutate_visible_sentinel(fake_sys, environment, _observation):
        environment["ATTACK"] = "accepted-by-shallow-copy"
        fake_sys._gate7_v2_bootstrap_attestation["python"]["environment"][
            "ATTACK"
        ] = "accepted-by-shallow-copy"

    fixture = _activation_fixture(tmp_path, monkeypatch, mutate_visible_sentinel)

    with pytest.raises(
        bootstrap.BootstrapError,
        match="runtime sentinel bytes or digest",
    ):
        _activate(fixture)


def test_postchecks_ignore_mutated_caller_observation(tmp_path, monkeypatch):
    def mutate_caller_only(_fake_sys, environment, observation):
        environment["ATTACK"] = "accepted-by-caller-baseline"
        observation["python"]["environment"][
            "ATTACK"
        ] = "accepted-by-caller-baseline"

    fixture = _activation_fixture(tmp_path, monkeypatch, mutate_caller_only)

    with pytest.raises(
        bootstrap.BootstrapError,
        match="sealed process environment",
    ):
        _activate(fixture)


def test_full_postcheck_failure_never_calls_terminal_finalizer(
    tmp_path, monkeypatch
):
    calls = []

    def fail_after_target(fake_sys, environment, _observation):
        fake_sys._gate7_v2_terminal_intent = {"forged": True}
        environment["ATTACK"] = "post-target drift"
        raise SystemExit(0)

    fixture = _activation_fixture(
        tmp_path, monkeypatch, fail_after_target, launch_mode="full"
    )
    ledger = fixture["project_root"] / "attempt-ledger.jsonl"
    ledger.write_bytes(b'{"event":"START"}\n')
    before = ledger.read_bytes()
    monkeypatch.setattr(
        bootstrap,
        "_append_bootstrap_completed_terminal",
        lambda *_args, **_kwargs: calls.append(True),
    )

    with pytest.raises(bootstrap.BootstrapError, match="sealed process environment"):
        _activate(fixture)

    assert calls == []
    assert ledger.read_bytes() == before


def test_full_unsupported_exit_never_calls_terminal_finalizer(
    tmp_path, monkeypatch
):
    calls = []

    def unsupported_exit(fake_sys, _environment, _observation):
        fake_sys._gate7_v2_terminal_intent = {"forged": True}
        raise SystemExit(7)

    fixture = _activation_fixture(
        tmp_path, monkeypatch, unsupported_exit, launch_mode="full"
    )
    monkeypatch.setattr(
        bootstrap,
        "_append_bootstrap_completed_terminal",
        lambda *_args, **_kwargs: calls.append(True),
    )

    with pytest.raises(bootstrap.BootstrapError, match="unsupported exit status"):
        _activate(fixture)

    assert calls == []


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_entrypoint_preserves_system_exit_zero_through_three(monkeypatch, code):
    def exit_normally():
        raise SystemExit(code)

    monkeypatch.setattr(bootstrap, "main", exit_normally)

    with pytest.raises(SystemExit) as raised:
        bootstrap._entrypoint()
    assert raised.value.code == code


def test_entrypoint_maps_unexpected_exception_to_reserved_exit_four(
    monkeypatch, capsys
):
    def unexpected_postcheck_failure():
        raise ValueError("synthetic unexpected postcheck failure")

    monkeypatch.setattr(bootstrap, "main", unexpected_postcheck_failure)

    with pytest.raises(SystemExit) as raised:
        bootstrap._entrypoint()
    assert raised.value.code == 4
    assert "unexpected ValueError" in capsys.readouterr().err


def _terminal_fixture(tmp_path, monkeypatch, status="pass"):
    project_root = tmp_path / "project"
    formal_root = project_root / bootstrap.FORMAL_ATTEMPT_ROOT
    output_dir = formal_root / "attempt-20260827T120000000000Z-12345"
    output_dir.mkdir(parents=True)
    (formal_root / ".formal-attempt.lock").write_bytes(b"")
    producer_path = (
        "benchmarks/carma/gate7_v2_onnx_integration_benchmark.py"
    )
    observation = {
        "schema_version": bootstrap.BOOTSTRAP_SCHEMA,
        "role": "parent",
        "target": producer_path,
        "target_sha256": "a" * 64,
        "preimport_source": {"launch_mode": "full", "sealed": True},
        "dependency": {"sealed": True, "count": 50},
        "python": {"environment": {"A": "B"}},
    }
    observation["attestation_sha256"] = bootstrap._canonical_mapping_sha256(
        observation
    )
    auditor_identity = {
        "path": "benchmarks/carma/gate7_v2_audit.py",
        "sha256": "b" * 64,
        "bytes": 123,
    }
    entrypoint = {
        "bootstrap_attestation": observation,
        "bootstrap_attestation_sha256": observation["attestation_sha256"],
    }

    def ledger_entry(value):
        value = dict(value)
        value["entry_sha256"] = bootstrap._canonical_mapping_sha256(value)
        return value

    genesis = ledger_entry(
        {
            "schema_version": bootstrap.ATTEMPT_LEDGER_SCHEMA,
            "experiment_id": bootstrap.EXPERIMENT_ID,
            "sequence": 1,
            "event": "PROTOCOL_GENESIS",
            "previous_entry_sha256": None,
        }
    )
    start = ledger_entry(
        {
            "schema_version": bootstrap.ATTEMPT_LEDGER_SCHEMA,
            "experiment_id": bootstrap.EXPERIMENT_ID,
            "sequence": 2,
            "event": "START",
            "attempt_id": "attempt-id",
            "started_at_utc": "2026-08-27T12:00:00+00:00",
            "head_commit": "c" * 40,
            "directory": output_dir.name,
            "formal_source_anchor": {"sealed": True},
            "dependency_attestation_sha256": "d" * 64,
            "environment_sha256": "e" * 64,
            "entrypoint_attestation": entrypoint,
            "previous_entry_sha256": genesis["entry_sha256"],
        }
    )
    ledger_path = formal_root / "attempt-ledger.jsonl"
    ledger_payload = b"".join(
        bootstrap._canonical_json_bytes(entry) + b"\n"
        for entry in (genesis, start)
    )
    ledger_path.write_bytes(ledger_payload)
    ledger_prefix = bootstrap._ledger_prefix_identity(
        [genesis, start], ledger_payload
    )
    manifest = {
        "attempt_id": start["attempt_id"],
        "attempt_ledger": ledger_prefix,
        "entrypoint_attestation": entrypoint,
        "formal_source_anchor": start["formal_source_anchor"],
        "dependency_attestation": {
            "attestation_sha256": start["dependency_attestation_sha256"]
        },
        "environment_sha256": start["environment_sha256"],
        "source_identities": {
            producer_path: {
                "sha256": observation["target_sha256"],
                "bytes": 456,
            }
        },
    }
    report = {
        "audit_phase": "preterminal",
        "attempt_id": start["attempt_id"],
        "status": status,
        "claimable": status in ("pass", "fail"),
        "auditor_identity": auditor_identity,
    }
    manifest_path = output_dir / "manifest.json"
    report_path = output_dir / bootstrap.PRETERMINAL_REPORT_NAME
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    intent = {
        "schema_version": bootstrap.TERMINAL_INTENT_SCHEMA,
        "attempt_id": start["attempt_id"],
        "directory": output_dir.name,
        "terminal_record": "manifest.json",
        "terminal_record_sha256": bootstrap._sha256_file(manifest_path),
        "terminal_record_bytes": manifest_path.stat().st_size,
        "preterminal_report": bootstrap.PRETERMINAL_REPORT_NAME,
        "preterminal_report_sha256": bootstrap._sha256_file(report_path),
        "preterminal_report_bytes": report_path.stat().st_size,
        "preterminal_report_status": status,
        "preterminal_report_claimable": status in ("pass", "fail"),
        "auditor_identity": auditor_identity,
        "target_exit_status": bootstrap.ADJUDICATION_EXIT_STATUSES[status],
        "ledger_prefix": ledger_prefix,
    }
    intent["intent_sha256"] = bootstrap._canonical_mapping_sha256(intent)
    fake_sys = SimpleNamespace(_gate7_v2_terminal_intent=intent)
    monkeypatch.setattr(bootstrap, "sys", fake_sys)
    return {
        "project_root": project_root,
        "observation": observation,
        "intent": intent,
        "ledger_path": ledger_path,
        "ledger_payload": ledger_payload,
    }


@pytest.mark.parametrize("status", ["pass", "fail", "pending", "invalid"])
def test_parent_bootstrap_appends_completed_terminal_after_seal(
    tmp_path, monkeypatch, status
):
    fixture = _terminal_fixture(tmp_path, monkeypatch, status=status)
    exit_status = bootstrap.ADJUDICATION_EXIT_STATUSES[status]

    terminal = bootstrap._append_bootstrap_completed_terminal(
        fixture["project_root"], fixture["observation"], exit_status
    )

    completion = terminal["bootstrap_completion"]
    assert terminal["preterminal_report_status"] == status
    assert completion["checks_passed"] is True
    assert completion["target_exit_status"] == exit_status
    assert (
        completion["terminal_intent_sha256"]
        == fixture["intent"]["intent_sha256"]
    )
    assert completion["attestation_sha256"] == bootstrap._mapping_self_hash(
        completion, "attestation_sha256"
    )
    entries, payload = bootstrap._read_canonical_attempt_ledger(
        fixture["ledger_path"]
    )
    assert entries[-1] == terminal
    assert payload.startswith(fixture["ledger_payload"])


def test_parent_bootstrap_rejects_changed_terminal_evidence_without_append(
    tmp_path, monkeypatch
):
    fixture = _terminal_fixture(tmp_path, monkeypatch)
    intent = bootstrap.sys._gate7_v2_terminal_intent
    intent["terminal_record_sha256"] = "f" * 64
    unsigned = dict(intent)
    unsigned.pop("intent_sha256")
    intent["intent_sha256"] = bootstrap._canonical_mapping_sha256(unsigned)

    with pytest.raises(
        bootstrap.BootstrapError, match="evidence changed after intent"
    ):
        bootstrap._append_bootstrap_completed_terminal(
            fixture["project_root"], fixture["observation"], 0
        )

    assert fixture["ledger_path"].read_bytes() == fixture["ledger_payload"]


def test_parent_bootstrap_requires_terminal_intent(tmp_path, monkeypatch):
    fixture = _terminal_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(bootstrap.sys, "_gate7_v2_terminal_intent", None)

    with pytest.raises(bootstrap.BootstrapError, match="intent is not a mapping"):
        bootstrap._append_bootstrap_completed_terminal(
            fixture["project_root"], fixture["observation"], 0
        )

    assert fixture["ledger_path"].read_bytes() == fixture["ledger_payload"]
