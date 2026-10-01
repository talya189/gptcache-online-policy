"""Focused fail-closed tests for the Gate 7 v4 isolated bootstrap."""

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import gate7_v4_isolated_bootstrap as bootstrap


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
        "remote_name": bootstrap.FORMAL_SUBMISSION_REMOTE,
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
        fake_sys._gate7_v4_bootstrap_attestation["python"]["environment"][
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
        fake_sys._gate7_v4_terminal_intent = {"forged": True}
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
        fake_sys._gate7_v4_terminal_intent = {"forged": True}
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


@pytest.mark.parametrize(
    "annotation_lines",
    [
        [
            "experiment_id=%s" % bootstrap.EXPERIMENT_ID,
            "contract_sha256=%s" % bootstrap.PINNED_CONTRACT_SHA256,
            "extra=true",
        ],
        [
            "experiment_id=%s" % bootstrap.EXPERIMENT_ID,
            "experiment_id=%s" % bootstrap.EXPERIMENT_ID,
            "contract_sha256=%s" % bootstrap.PINNED_CONTRACT_SHA256,
        ],
        [
            "contract_sha256=%s" % bootstrap.PINNED_CONTRACT_SHA256,
            "experiment_id=%s" % bootstrap.EXPERIMENT_ID,
        ],
    ],
    ids=("extra", "duplicate", "reordered"),
)
def test_bootstrap_rejects_nonexact_formal_tag_annotation(
    tmp_path, monkeypatch, annotation_lines
):
    head = "a" * 40
    tree = "b" * 40
    payload = (
        "object %s\n" % head
        + "type commit\n"
        + "tag %s\n" % bootstrap.FORMAL_SOURCE_TAG
        + "tagger Gate 7 <gate7@example.invalid> 0 +0000\n\n"
        + "\n".join(annotation_lines)
        + "\n"
    ).encode("utf-8")
    tag_object = hashlib.sha1(
        ("tag %d\0" % len(payload)).encode("ascii") + payload
    ).hexdigest()
    tag_ref = "refs/tags/%s" % bootstrap.FORMAL_SOURCE_TAG
    values = {
        ("rev-parse", "--show-object-format"): "sha1",
        ("rev-parse", "HEAD"): head,
        ("rev-parse", "HEAD^{tree}"): tree,
        ("rev-parse", tag_ref): tag_object,
        ("cat-file", "-t", tag_object): "tag",
        ("rev-parse", "%s^{commit}" % tag_ref): head,
        ("rev-parse", "%s^{tree}" % tag_ref): tree,
    }
    monkeypatch.setattr(
        bootstrap,
        "_git_text",
        lambda _project_root, *arguments: values[arguments],
    )
    monkeypatch.setattr(
        bootstrap,
        "_git_bytes",
        lambda _project_root, *arguments: payload,
    )

    with pytest.raises(
        bootstrap.BootstrapError,
        match="formal source tag does not authenticate clean HEAD",
    ):
        bootstrap._formal_source_anchor(tmp_path)


def _terminal_fixture(
    tmp_path,
    monkeypatch,
    status="pass",
    prefix_mutator=None,
    manifest_mutator=None,
):
    project_root = tmp_path / "project"
    repository_root = Path(bootstrap.__file__).resolve().parents[1]
    for preserved_v1_path in (
        bootstrap.V1_PRESERVATION_MANIFEST,
        bootstrap.V1_PRESERVATION_ARCHIVE,
    ):
        destination = project_root / preserved_v1_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository_root / preserved_v1_path, destination)
    preserved_v2 = project_root / bootstrap.V2_PRESERVATION_ROOT
    preserved_v2.parent.mkdir(parents=True)
    shutil.copytree(
        repository_root / bootstrap.V2_PRESERVATION_ROOT,
        preserved_v2,
    )
    preserved_v3 = project_root / bootstrap.V3_PRESERVATION_ROOT
    shutil.copytree(
        repository_root / bootstrap.V3_PRESERVATION_ROOT,
        preserved_v3,
    )
    contract_path = project_root / bootstrap.FORMAL_CONTRACT_PATH
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        repository_root / bootstrap.FORMAL_CONTRACT_PATH,
        contract_path,
    )
    formal_root = project_root / bootstrap.FORMAL_ATTEMPT_ROOT
    output_dir = formal_root / "attempt-20260827T120000000000Z-12345"
    output_dir.mkdir(parents=True)
    (formal_root / ".formal-attempt.lock").write_bytes(b"")
    producer_path = (
        "benchmarks/carma/gate7_v4_onnx_integration_benchmark.py"
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
        "path": "benchmarks/carma/gate7_v4_audit.py",
        "sha256": "b" * 64,
        "bytes": 123,
    }
    entrypoint = {
        "schema_version": bootstrap.FORMAL_ENTRYPOINT_SCHEMA,
        "marker": "gate7e-wrapper-v1",
        "mode": "full",
        "wrapper_pid": 100,
        "producer_parent_pid": 101,
        "wrapper_path": "scripts/run_gate7_v4_onnx_integration_benchmark.sh",
        "wrapper_identity": {"sha256": "f" * 64, "bytes": 123},
        "parent_executable": ".venv/bin/python",
        "parent_cmdline": [
            ".venv/bin/python",
            "scripts/gate7_v4_isolated_bootstrap.py",
            "--role",
            "parent",
        ],
        "bootstrap_attestation": observation,
        "bootstrap_attestation_sha256": observation["attestation_sha256"],
    }
    started_at_utc = "2026-08-27T12:00:00+00:00"
    head_commit = "c" * 40
    attempt_id = bootstrap._attempt_id_for(
        bootstrap._parse_utc_timestamp(started_at_utc), head_commit
    )
    formal_source_anchor = {
        "schema_version": bootstrap.FORMAL_SOURCE_ANCHOR_SCHEMA,
        "tag_name": bootstrap.FORMAL_SOURCE_TAG,
        "remote_name": bootstrap.FORMAL_SUBMISSION_REMOTE,
        "object_format": "sha1",
        "tag_object_type": "tag",
        "tag_object_id": "1" * 40,
        "peeled_commit": head_commit,
        "head_commit": head_commit,
        "tree_id": "2" * 40,
        "tag_payload_sha256": "3" * 64,
        "tag_payload_bytes": 123,
        "annotation": {
            "experiment_id": bootstrap.EXPERIMENT_ID,
            "contract_sha256": bootstrap.PINNED_CONTRACT_SHA256,
        },
        "remote": {
            "fetch_urls": [bootstrap.FORMAL_SUBMISSION_URL],
            "push_urls": [bootstrap.FORMAL_SUBMISSION_URL],
            "tag_object_id": "1" * 40,
            "peeled_commit": head_commit,
        },
        "retained_tag_payload_path": bootstrap.FORMAL_SOURCE_TAG_PAYLOAD_PATH,
    }
    contract = {
        "path": bootstrap.FORMAL_CONTRACT_PATH,
        "sha256": bootstrap.PINNED_CONTRACT_SHA256,
        "bytes": contract_path.stat().st_size,
    }
    pre_start_power = {
        "observed_at_utc": started_at_utc,
        "available": False,
        "plugged": None,
        "percent": None,
        "seconds_left": None,
    }
    retained_inputs = {
        "trace_construction_prepared_dir": "source/prepared",
        "audit_source_policy": "retained_attempt_copies_only",
        "archive": {
            "path": "source/similiar_qqp_full.json.gz",
            "sha256": "4" * 64,
            "bytes": 1,
        },
        "prepared": {
            "pairs.jsonl": {
                "path": "source/prepared/pairs.jsonl",
                "sha256": "6" * 64,
                "bytes": 1,
            },
            "texts.jsonl": {
                "path": "source/prepared/texts.jsonl",
                "sha256": "7" * 64,
                "bytes": 1,
            },
            "manifest.json": {
                "path": "source/prepared/manifest.json",
                "sha256": "8" * 64,
                "bytes": 1,
            },
        },
        "dependency_attestation": {
            "path": "source/dependency-attestation.json",
            "sha256": "9" * 64,
            "bytes": 1,
            "attestation_sha256": "d" * 64,
        },
        "formal_source_tag_payload": {
            "path": bootstrap.FORMAL_SOURCE_TAG_PAYLOAD_PATH,
            "sha256": formal_source_anchor["tag_payload_sha256"],
            "bytes": formal_source_anchor["tag_payload_bytes"],
        },
    }
    prior_attempts = []
    prior_attempts_sha256 = hashlib.sha256(b"[]").hexdigest()

    def ledger_entry(value):
        value = dict(value)
        value["entry_sha256"] = bootstrap._canonical_mapping_sha256(value)
        return value

    genesis_value = {
        "schema_version": bootstrap.ATTEMPT_LEDGER_SCHEMA,
        "experiment_id": bootstrap.EXPERIMENT_ID,
        "sequence": 1,
        "event": "PROTOCOL_GENESIS",
        "recorded_at_utc": "2026-08-27T11:59:00+00:00",
        "v1_preservation": bootstrap._v1_preservation_identity(project_root),
        "v2_preservation": bootstrap._v2_preservation_identity(project_root),
        "v3_preservation": bootstrap._v3_preservation_identity(project_root),
        "v4_contract": {
            "path": bootstrap.FORMAL_CONTRACT_PATH,
            "sha256": bootstrap.PINNED_CONTRACT_SHA256,
            "bytes": contract_path.stat().st_size,
        },
        "formal_source_anchor": formal_source_anchor,
        "previous_entry_sha256": None,
    }
    start_value = {
        "schema_version": bootstrap.ATTEMPT_LEDGER_SCHEMA,
        "experiment_id": bootstrap.EXPERIMENT_ID,
        "sequence": 2,
        "event": "START",
        "attempt_id": attempt_id,
        "started_at_utc": started_at_utc,
        "head_commit": head_commit,
        "directory": output_dir.name,
        "prior_attempt_count": 0,
        "prior_attempts_sha256": prior_attempts_sha256,
        "contract": contract,
        "source_snapshot_sha256": "5" * 64,
        "formal_source_anchor": formal_source_anchor,
        "dependency_attestation_sha256": "d" * 64,
        "environment_sha256": "e" * 64,
        "entrypoint_attestation": entrypoint,
        "pre_start_power": pre_start_power,
        "retained_inputs": retained_inputs,
        "previous_entry_sha256": None,
    }
    if prefix_mutator is not None:
        prefix_mutator(genesis_value, start_value)
    genesis = ledger_entry(genesis_value)
    start_value["previous_entry_sha256"] = genesis["entry_sha256"]
    start = ledger_entry(start_value)
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
        "schema_version": bootstrap.EVIDENCE_SCHEMA,
        "experiment_id": bootstrap.EXPERIMENT_ID,
        "post_v3_protocol_disclosure": dict(
            bootstrap.POST_V3_PROTOCOL_DISCLOSURE
        ),
        "attempt_id": start["attempt_id"],
        "started_at_utc": start["started_at_utc"],
        "git": {"head_commit": start["head_commit"]},
        "prior_attempts": prior_attempts,
        "attempt_ledger": ledger_prefix,
        "contract": start["contract"],
        "publication_integrity": {
            field: True
            for field in bootstrap.FORMAL_PUBLICATION_INTEGRITY_FIELDS
        },
        "source_snapshot_sha256": start["source_snapshot_sha256"],
        "entrypoint_attestation": entrypoint,
        "formal_source_anchor": start["formal_source_anchor"],
        "dependency_attestation": {
            "attestation_sha256": start["dependency_attestation_sha256"]
        },
        "environment_sha256": start["environment_sha256"],
        "power_observations": {
            "pre_start": start["pre_start_power"],
            "end": start["pre_start_power"],
        },
        "retained_inputs": start["retained_inputs"],
        "attempt_policy": {
            "formal_root": bootstrap.FORMAL_ATTEMPT_ROOT.as_posix(),
            "directory": output_dir.name,
        },
        "source_identities": {
            producer_path: {
                "path": producer_path,
                "sha256": observation["target_sha256"],
                "bytes": 456,
            },
            "benchmarks/carma/gate7_v4_audit.py": {
                "path": "benchmarks/carma/gate7_v4_audit.py",
                "sha256": auditor_identity["sha256"],
                "bytes": auditor_identity["bytes"],
            },
        },
    }
    if manifest_mutator is not None:
        manifest_mutator(manifest)
    manifest_path = output_dir / "manifest.json"
    report_path = output_dir / bootstrap.PRETERMINAL_REPORT_NAME
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = {
        "audit_phase": "preterminal",
        "attempt_id": start["attempt_id"],
        "status": status,
        "claimable": status in ("pass", "fail"),
        "auditor_identity": auditor_identity,
        "manifest_identity": {
            "path": "manifest.json",
            "sha256": bootstrap._sha256_file(manifest_path),
            "bytes": manifest_path.stat().st_size,
        },
    }
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
    fake_sys = SimpleNamespace(_gate7_v4_terminal_intent=intent)
    monkeypatch.setattr(bootstrap, "sys", fake_sys)
    return {
        "project_root": project_root,
        "observation": observation,
        "intent": intent,
        "ledger_path": ledger_path,
        "ledger_payload": ledger_payload,
        "manifest_path": manifest_path,
        "report_path": report_path,
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


@pytest.mark.parametrize(
    "case",
    [
        "genesis_missing_v1_preservation",
        "genesis_missing_v3_preservation",
        "genesis_non_utc_timestamp",
        "genesis_tampered_source_anchor",
        "genesis_unexpected_key",
        "start_missing_prior_count",
        "start_tampered_attempt_id",
        "start_tampered_predecessor_digest",
        "start_contract_unexpected_key",
        "start_contract_wrong_bytes",
        "start_tampered_source_anchor",
        "start_unexpected_key",
        "manifest_tampered_predecessors",
        "manifest_tampered_contract",
        "manifest_missing_publication_integrity",
        "manifest_false_contract_unchanged",
        "manifest_moved_contract_unchanged",
        "manifest_missing_post_v3_disclosure",
        "manifest_tampered_post_v3_disclosure",
        "manifest_tampered_source_snapshot",
        "manifest_tampered_pre_start_power",
        "manifest_tampered_retained_inputs",
    ],
)
def test_parent_bootstrap_rejects_incomplete_or_tampered_protocol_prefix(
    tmp_path, monkeypatch, case
):
    def mutate_prefix(genesis, start):
        if case == "genesis_missing_v1_preservation":
            genesis.pop("v1_preservation")
        elif case == "genesis_missing_v3_preservation":
            genesis.pop("v3_preservation")
        elif case == "genesis_non_utc_timestamp":
            genesis["recorded_at_utc"] = "2026-08-27T13:59:00+02:00"
        elif case == "genesis_tampered_source_anchor":
            genesis["formal_source_anchor"] = {
                **genesis["formal_source_anchor"],
                "tag_name": "different-tag",
            }
        elif case == "genesis_unexpected_key":
            genesis["unexpected"] = True
        elif case == "start_missing_prior_count":
            start.pop("prior_attempt_count")
        elif case == "start_tampered_attempt_id":
            start["attempt_id"] = "tampered-attempt-id"
        elif case == "start_tampered_predecessor_digest":
            start["prior_attempts_sha256"] = "9" * 64
        elif case == "start_contract_unexpected_key":
            start["contract"] = {**start["contract"], "unexpected": True}
        elif case == "start_contract_wrong_bytes":
            start["contract"] = {**start["contract"], "bytes": 1}
        elif case == "start_tampered_source_anchor":
            start["formal_source_anchor"] = {
                **start["formal_source_anchor"],
                "tree_id": "9" * 40,
            }
        elif case == "start_unexpected_key":
            start["unexpected"] = True

    def mutate_manifest(manifest):
        if case == "manifest_tampered_predecessors":
            manifest["prior_attempts"] = [{"attempt_id": "unbound"}]
        elif case == "manifest_tampered_contract":
            manifest["contract"] = {**manifest["contract"], "bytes": 124}
        elif case == "manifest_missing_publication_integrity":
            manifest.pop("publication_integrity")
        elif case == "manifest_false_contract_unchanged":
            manifest["publication_integrity"]["contract_unchanged"] = False
        elif case == "manifest_moved_contract_unchanged":
            manifest["contract_unchanged"] = manifest["publication_integrity"].pop(
                "contract_unchanged"
            )
        elif case == "manifest_missing_post_v3_disclosure":
            manifest.pop("post_v3_protocol_disclosure")
        elif case == "manifest_tampered_post_v3_disclosure":
            manifest["post_v3_protocol_disclosure"]["v4_designed_after_v3"] = False
        elif case == "manifest_tampered_source_snapshot":
            manifest["source_snapshot_sha256"] = "9" * 64
        elif case == "manifest_tampered_pre_start_power":
            manifest["power_observations"]["pre_start"] = {
                **manifest["power_observations"]["pre_start"],
                "available": True,
            }
        elif case == "manifest_tampered_retained_inputs":
            manifest["retained_inputs"] = {"tampered": True}

    prefix_case = case.startswith(("genesis_", "start_"))
    manifest_case = case.startswith("manifest_")
    fixture = _terminal_fixture(
        tmp_path,
        monkeypatch,
        prefix_mutator=mutate_prefix if prefix_case else None,
        manifest_mutator=mutate_manifest if manifest_case else None,
    )

    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._append_bootstrap_completed_terminal(
            fixture["project_root"], fixture["observation"], 0
        )

    assert fixture["ledger_path"].read_bytes() == fixture["ledger_payload"]


def test_parent_bootstrap_rejects_historical_v3_contract_superset_without_append(
    tmp_path, monkeypatch
):
    def add_historical_publication_flag(manifest):
        manifest["contract"] = {
            **manifest["contract"],
            "publication_identity_unchanged": True,
        }

    fixture = _terminal_fixture(
        tmp_path,
        monkeypatch,
        manifest_mutator=add_historical_publication_flag,
    )

    with pytest.raises(
        bootstrap.BootstrapError,
        match="formal terminal evidence differs from its intent",
    ):
        bootstrap._append_bootstrap_completed_terminal(
            fixture["project_root"], fixture["observation"], 0
        )

    assert fixture["ledger_path"].read_bytes() == fixture["ledger_payload"]


def test_parent_bootstrap_rejects_changed_terminal_evidence_without_append(
    tmp_path, monkeypatch
):
    fixture = _terminal_fixture(tmp_path, monkeypatch)
    intent = bootstrap.sys._gate7_v4_terminal_intent
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


def test_parent_bootstrap_rejects_rebound_report_manifest_identity_tamper(
    tmp_path, monkeypatch
):
    fixture = _terminal_fixture(tmp_path, monkeypatch)
    report = json.loads(fixture["report_path"].read_text(encoding="utf-8"))
    report["manifest_identity"]["sha256"] = "f" * 64
    fixture["report_path"].write_text(json.dumps(report), encoding="utf-8")
    intent = bootstrap.sys._gate7_v4_terminal_intent
    intent["preterminal_report_sha256"] = bootstrap._sha256_file(
        fixture["report_path"]
    )
    intent["preterminal_report_bytes"] = fixture["report_path"].stat().st_size
    intent.pop("intent_sha256", None)
    intent["intent_sha256"] = bootstrap._canonical_mapping_sha256(intent)

    with pytest.raises(
        bootstrap.BootstrapError,
        match="formal terminal evidence differs from its intent",
    ):
        bootstrap._append_bootstrap_completed_terminal(
            fixture["project_root"], fixture["observation"], 0
        )

    assert fixture["ledger_path"].read_bytes() == fixture["ledger_payload"]


def test_parent_bootstrap_requires_terminal_intent(tmp_path, monkeypatch):
    fixture = _terminal_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(bootstrap.sys, "_gate7_v4_terminal_intent", None)

    with pytest.raises(bootstrap.BootstrapError, match="intent is not a mapping"):
        bootstrap._append_bootstrap_completed_terminal(
            fixture["project_root"], fixture["observation"], 0
        )

    assert fixture["ledger_path"].read_bytes() == fixture["ledger_payload"]
