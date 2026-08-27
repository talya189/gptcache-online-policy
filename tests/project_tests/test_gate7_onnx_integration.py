"""Focused contract tests for the prospective Gate 7 ONNX runner."""

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from benchmarks.carma import onnx_integration_benchmark as gate7_runner
from benchmarks.carma.onnx_integration_benchmark import (
    DEFAULT_CONTRACT,
    FULL_SEEDS,
    PINNED_CONTRACT_SHA256,
    POLICIES,
    ExclusiveStageRecorder,
    Gate7Config,
    policy_schedule,
    run_bundle,
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path, rows):
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _prepared(tmp_path, calibration_concepts=24):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    text_rows = []
    pair_rows = []
    for concept_index in range(calibration_concepts):
        concept_id = "calibration-concept-%02d" % concept_index
        text_id = "calibration-text-%02d" % concept_index
        text_rows.append(
            {"text_id": text_id, "text": "Real QQP question %02d?" % concept_index}
        )
        pair_rows.append(
            {
                "split": "calibration",
                "text_a_id": text_id,
                "text_b_id": text_id,
                "concept_a": concept_id,
                "concept_b": concept_id,
                "label": 1,
            }
        )
    _write_jsonl(prepared / "pairs.jsonl", pair_rows)
    _write_jsonl(prepared / "texts.jsonl", text_rows)
    manifest = {
        "schema_version": "carma-qqp-v1",
        "archive_sha256": "a" * 64,
        "pairs_sha256": _sha256(prepared / "pairs.jsonl"),
        "texts_sha256": _sha256(prepared / "texts.jsonl"),
    }
    (prepared / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return prepared


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _completed_formal_attempt(root, directory="attempt-01"):
    output = root / directory
    output.mkdir(parents=True)
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    attempt_id = gate7_runner._attempt_id_for(started, head)
    declaration = gate7_runner._register_formal_attempt(
        output, attempt_id, started, head, []
    )
    auditor_identity = {
        "path": "benchmarks/carma/gate7_audit.py",
        "sha256": "b" * 64,
        "bytes": 123,
    }
    manifest = {
        "schema_version": gate7_runner.SCHEMA_VERSION,
        "experiment_id": gate7_runner.EXPERIMENT_ID,
        "kind": "prospective_gate7_followup",
        "attempt_id": attempt_id,
        "started_at_utc": started.isoformat(),
        "git": {"head_commit": head},
        "config": {"mode": "full"},
        "prior_attempts": [],
        "formal_claimable_mode": True,
        "attempt_ledger": declaration,
        "attempt_policy": {
            "formal_root": "artifacts/gate7-onnx-attempts",
            "directory": directory,
            "eligibility": (
                "first structurally valid complete attempt in the retained "
                "predecessor chain"
            ),
            "rerun_scope": "complete five-seed, three-policy matrix under a new attempt ID",
        },
        "source_identities": {
            "benchmarks/carma/gate7_audit.py": auditor_identity,
        },
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = {
        "audit_phase": "preterminal",
        "attempt_id": attempt_id,
        "status": "pass",
        "claimable": True,
        "auditor_identity": auditor_identity,
    }
    report_path = output / "gate7-preterminal-adjudication.json"
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    terminal_declaration = gate7_runner._append_formal_attempt_terminal(
        output, "manifest.json", report
    )
    return {
        "output": output,
        "started": started,
        "head": head,
        "attempt_id": attempt_id,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "report": report,
        "report_path": report_path,
        "terminal_declaration": terminal_declaration,
    }


def test_policy_schedule_is_the_exact_frozen_near_balanced_order():
    assert policy_schedule(FULL_SEEDS) == {
        20261001: ("CARMA", "LFU", "LRU"),
        20261002: ("LFU", "LRU", "CARMA"),
        20261003: ("LRU", "CARMA", "LFU"),
        20261004: ("LRU", "LFU", "CARMA"),
        20261005: ("CARMA", "LRU", "LFU"),
    }
    assert policy_schedule(tuple(reversed(FULL_SEEDS))) == policy_schedule(FULL_SEEDS)


def test_default_contract_bytes_match_both_frozen_verifiers():
    from benchmarks.carma import gate7_audit

    observed = _sha256(DEFAULT_CONTRACT)
    assert observed == PINNED_CONTRACT_SHA256
    assert observed == gate7_audit.PINNED_CONTRACT_SHA256


def test_policy_schedule_rejects_duplicate_seeds():
    with pytest.raises(ValueError, match="seeds must be unique"):
        policy_schedule((20261001, 20261001))


def test_formal_attempt_lock_rejects_concurrent_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(gate7_runner, "FORMAL_ATTEMPT_ROOT", tmp_path / "attempts")
    with gate7_runner._formal_attempt_lock(True):
        with pytest.raises(RuntimeError, match="already holds"):
            with gate7_runner._formal_attempt_lock(True):
                raise AssertionError("second formal holder must not enter")


def test_formal_attempt_registration_is_hash_chained(tmp_path):
    root = tmp_path / "attempts"
    output = root / "attempt-01"
    output.mkdir(parents=True)
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    attempt_id = gate7_runner._attempt_id_for(started, head)

    identity = gate7_runner._register_formal_attempt(
        output, attempt_id, started, head, []
    )
    entries, raw_lines = gate7_runner._read_attempt_ledger(root)

    assert identity["prefix_rows"] == 1
    assert identity["prefix_sha256"] == _sha256(root / "attempt-ledger.jsonl")
    assert len(entries) == len(raw_lines) == 1
    assert entries[0]["attempt_id"] == attempt_id
    assert entries[0]["previous_entry_sha256"] is None


def test_formal_terminal_binds_exact_manifest_and_preterminal_bytes(tmp_path):
    root = tmp_path / "attempts"
    fixture = _completed_formal_attempt(root)
    entries, _ = gate7_runner._read_attempt_ledger(root)
    terminal = entries[-1]

    assert [entry["event"] for entry in entries] == ["START", "TERMINAL"]
    assert fixture["terminal_declaration"]["prefix_rows"] == 2
    assert terminal["terminal_record"] == "manifest.json"
    assert terminal["terminal_record_sha256"] == _sha256(
        fixture["manifest_path"]
    )
    assert terminal["terminal_record_bytes"] == fixture["manifest_path"].stat().st_size
    assert terminal["preterminal_report_sha256"] == _sha256(
        fixture["report_path"]
    )
    assert terminal["preterminal_report_bytes"] == fixture["report_path"].stat().st_size
    assert terminal["preterminal_report_status"] == "pass"
    assert terminal["preterminal_report_claimable"] is True
    assert terminal["auditor_identity"] == fixture["report"]["auditor_identity"]

    next_output = root / "attempt-02"
    next_started = fixture["started"] + timedelta(seconds=1)
    next_id = gate7_runner._attempt_id_for(next_started, fixture["head"])
    prior_attempts = gate7_runner._prior_attempts(next_output)
    original_report = fixture["report_path"].read_text(encoding="utf-8")
    tampered_report = dict(fixture["report"])
    tampered_report["unbound_change"] = True
    fixture["report_path"].write_text(
        json.dumps(tampered_report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="preterminal adjudication was altered"):
        gate7_runner._register_formal_attempt(
            next_output,
            next_id,
            next_started,
            fixture["head"],
            prior_attempts,
        )

    fixture["report_path"].write_text(original_report, encoding="utf-8")
    tampered_manifest = dict(fixture["manifest"])
    tampered_manifest["unbound_change"] = True
    fixture["manifest_path"].write_text(
        json.dumps(tampered_manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    mutated_prior_attempts = gate7_runner._prior_attempts(next_output)
    with pytest.raises(RuntimeError, match="differs from retained predecessor records"):
        gate7_runner._register_formal_attempt(
            next_output,
            next_id,
            next_started,
            fixture["head"],
            mutated_prior_attempts,
        )


def test_unmatched_formal_start_blocks_later_registration(tmp_path):
    root = tmp_path / "attempts"
    first_output = root / "attempt-01"
    first_output.mkdir(parents=True)
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    gate7_runner._register_formal_attempt(
        first_output,
        gate7_runner._attempt_id_for(started, head),
        started,
        head,
        [],
    )

    later_started = started + timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="unmatched START"):
        gate7_runner._register_formal_attempt(
            root / "attempt-02",
            gate7_runner._attempt_id_for(later_started, head),
            later_started,
            head,
            [],
        )


def test_claimable_terminal_blocks_later_formal_registration(tmp_path):
    root = tmp_path / "attempts"
    fixture = _completed_formal_attempt(root)
    later_started = fixture["started"] + timedelta(seconds=1)
    later_output = root / "attempt-02"

    with pytest.raises(RuntimeError, match="claimable Gate 7 attempt already exists"):
        gate7_runner._register_formal_attempt(
            later_output,
            gate7_runner._attempt_id_for(later_started, fixture["head"]),
            later_started,
            fixture["head"],
            gate7_runner._prior_attempts(later_output),
        )


def test_failure_terminal_is_invalid_and_has_no_preterminal_identity(tmp_path):
    root = tmp_path / "attempts"
    output = root / "attempt-01"
    output.mkdir(parents=True)
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    gate7_runner._register_formal_attempt(
        output,
        gate7_runner._attempt_id_for(started, head),
        started,
        head,
        [],
    )
    failure_path = output / "attempt-failure.json"
    failure_path.write_text('{"status":"invalid"}\n', encoding="utf-8")
    gate7_runner._append_formal_attempt_terminal(
        output, "attempt-failure.json"
    )
    entries, _ = gate7_runner._read_attempt_ledger(root)
    terminal = entries[-1]

    assert terminal["event"] == "TERMINAL"
    assert terminal["terminal_record"] == "attempt-failure.json"
    assert terminal["terminal_record_sha256"] == _sha256(failure_path)
    assert terminal["terminal_record_bytes"] == failure_path.stat().st_size
    assert terminal["preterminal_report"] is None
    assert terminal["preterminal_report_sha256"] is None
    assert terminal["preterminal_report_bytes"] is None
    assert terminal["preterminal_report_status"] == "invalid"
    assert terminal["preterminal_report_claimable"] is False
    assert terminal["auditor_identity"] is None


def test_malformed_full_predecessor_is_unresolved_at_preflight(tmp_path):
    root = tmp_path / "attempts"
    predecessor = root / "attempt-01"
    predecessor.mkdir(parents=True)
    (predecessor / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": gate7_runner.SCHEMA_VERSION,
                "experiment_id": gate7_runner.EXPERIMENT_ID,
                "kind": "prospective_gate7_followup",
                "attempt_id": "forged",
                "started_at_utc": "2026-08-27T10:00:00+00:00",
                "git": {"head_commit": "a" * 40},
                "config": {"mode": "full"},
                "prior_attempts": [],
                "formal_claimable_mode": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    attempts = gate7_runner._prior_attempts(root / "attempt-02")
    assert len(attempts) == 1
    assert attempts[0]["status"] == "unresolved"


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"fake_embedding": True}, "forbids fake embeddings"),
        ({"requests": 2990}, "differs from frozen contract"),
        ({"capacity": 99}, "differs from frozen contract"),
        ({"resource_interval_ms": 50}, "differs from frozen contract"),
        ({"onnx_intra_threads": 2}, "differs from frozen contract"),
    ],
)
def test_full_config_rejects_fake_or_non_frozen_settings(changes, message):
    values = {"mode": "full", "requests": 3000, "capacity": 100}
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        Gate7Config(**values).validate()


def test_exclusive_policy_recorder_covers_put_and_put_with_metadata():
    class Policy:
        def put(self, key):
            sum(range(50))
            return [{"method": "put", "key": key}]

        def put_with_metadata(self, key):
            sum(range(50))
            return [{"method": "put_with_metadata", "key": key}]

    policy = Policy()
    recorder = ExclusiveStageRecorder()
    recorder.wrap(
        policy, "put", "policy_exclusive", capture_policy_result=True
    )
    recorder.wrap(
        policy,
        "put_with_metadata",
        "policy_exclusive",
        capture_policy_result=True,
    )

    recorder.begin()
    put_result = policy.put("first")
    put_measurement = recorder.finish()
    assert recorder.last_policy_result == put_result
    assert put_measurement["policy_exclusive"] > 0
    assert put_measurement["policy_inclusive"] >= put_measurement["policy_exclusive"]

    recorder.begin()
    metadata_result = policy.put_with_metadata("second")
    metadata_measurement = recorder.finish()
    assert recorder.last_policy_result == metadata_result
    assert metadata_measurement["policy_exclusive"] > 0
    assert (
        metadata_measurement["policy_inclusive"]
        >= metadata_measurement["policy_exclusive"]
    )


def test_fake_embedding_smoke_bundle_runs_all_policies_in_fresh_children(tmp_path):
    prepared = _prepared(tmp_path)
    output = tmp_path / "bundle"
    config = Gate7Config(
        mode="smoke",
        requests=20,
        capacity=5,
        warmup_requests=2,
        resource_interval_ms=10,
        fake_embedding=True,
    )

    manifest = run_bundle(
        prepared,
        output,
        DEFAULT_CONTRACT,
        seeds=(17,),
        policies=POLICIES,
        config=config,
    )

    assert manifest["actual_onnx"] is False
    assert manifest["formal_claimable_mode"] is False
    assert manifest["precomputed_embeddings"] is False
    assert manifest["embedding_in_request_path"] is True
    assert manifest["policy_orders"] == {
        "17": list(policy_schedule((17,))[17])
    }
    assert manifest["artifacts"]["runs.csv"]["rows"] == 3
    assert manifest["artifacts"]["requests.jsonl"]["rows"] == 60
    assert manifest["artifacts"]["resources.jsonl"]["rows"] >= 6
    assert manifest["artifacts"]["outcome-latency.jsonl"]["rows"] == 15

    with (output / "runs.csv").open(newline="", encoding="utf-8") as source:
        runs = list(csv.DictReader(source))
    requests = _read_jsonl(output / "requests.jsonl")
    resources = _read_jsonl(output / "resources.jsonl")
    outcome_latency = _read_jsonl(output / "outcome-latency.jsonl")

    assert {row["policy"] for row in runs} == set(POLICIES)
    assert len({row["child_pid"] for row in runs}) == 3
    assert all(int(row["requests"]) == 20 for row in runs)
    assert all(int(row["false_hits"]) == 0 for row in runs)
    assert all(int(row["max_cache_size"]) <= 5 for row in runs)
    assert all(float(row["throughput_qps"]) > 0 for row in runs)

    assert len(requests) == 60
    assert all("text" not in row for row in requests)
    assert all(row["exclusive_reconciles"] for row in requests)
    assert all(row["request_total_ns"] > 0 for row in requests)
    assert all(row["embedding_ns"] > 0 for row in requests)
    assert all(row["cache_size_after"] <= 5 for row in requests)
    assert {row["policy"] for row in requests} == set(POLICIES)

    assert {row["kind"] for row in resources}.issuperset(
        {"loop_start", "loop_end"}
    )
    assert all(row["scope"] == "formal" for row in resources)
    assert len({row["run_id"] for row in resources}) == 3
    assert {row["outcome"] for row in outcome_latency} == set(
        gate7_runner.OUTCOME_REPORT_NAMES
    )


def test_child_failure_is_retained_with_attempt_identity(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    output = tmp_path / "failed-bundle"
    config = Gate7Config(
        mode="smoke",
        requests=20,
        capacity=5,
        warmup_requests=2,
        resource_interval_ms=10,
        fake_embedding=True,
    )

    def fail_child(*_args, **_kwargs):
        raise gate7_runner.ChildExecutionError(
            "intentional child failure", 7, "captured stdout", "captured stderr", []
        )

    monkeypatch.setattr(gate7_runner, "_run_child_with_sampling", fail_child)
    with pytest.raises(gate7_runner.ChildExecutionError):
        run_bundle(
            prepared,
            output,
            DEFAULT_CONTRACT,
            seeds=(17,),
            policies=("LRU",),
            config=config,
        )

    failure = json.loads(
        (output / "attempt-failure.json").read_text(encoding="utf-8")
    )
    assert failure["kind"] == "prospective_gate7_failed_attempt"
    assert failure["status"] == "invalid"
    assert failure["child_exit_status"] == 7
    assert failure["attempt_id"]
    assert (output / "child.stdout.log").read_text(encoding="utf-8") == "captured stdout"
    assert (output / "child.stderr.log").read_text(encoding="utf-8") == "captured stderr"
    assert any(name.startswith("traces/") for name in failure["artifacts"])


def test_finalization_failure_retains_samples_from_all_completed_children(
    tmp_path, monkeypatch
):
    prepared = _prepared(tmp_path)
    output = tmp_path / "failed-after-children"
    config = Gate7Config(
        mode="smoke",
        requests=20,
        capacity=5,
        warmup_requests=2,
        resource_interval_ms=10,
        fake_embedding=True,
    )

    def fail_finalization(*_args, **_kwargs):
        raise RuntimeError("intentional finalization failure")

    monkeypatch.setattr(
        gate7_runner, "_outcome_latency_records", fail_finalization
    )
    with pytest.raises(RuntimeError, match="intentional finalization failure"):
        run_bundle(
            prepared,
            output,
            DEFAULT_CONTRACT,
            seeds=(17,),
            policies=POLICIES,
            config=config,
        )

    failure = json.loads(
        (output / "attempt-failure.json").read_text(encoding="utf-8")
    )
    retained = _read_jsonl(output / "failed-resources.jsonl")
    assert failure["kind"] == "prospective_gate7_failed_attempt"
    assert failure["error_type"] == "RuntimeError"
    assert len({row["run_id"] for row in retained}) == 3
    assert {row["policy"] for row in retained} == set(POLICIES)
