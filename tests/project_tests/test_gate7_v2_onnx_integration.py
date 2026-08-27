"""Focused contract tests for the prospective Gate 7 ONNX runner."""

import base64
import csv
import hashlib
import inspect
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from benchmarks.carma import gate7_v2_onnx_integration_benchmark as gate7_runner
from benchmarks.carma.gate7_v2_onnx_integration_benchmark import (
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
                "schema_version": "carma-qqp-v1",
                "source_index": concept_index,
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


def _local_asset_fixture(tmp_path, monkeypatch):
    model_path = tmp_path / "model.onnx"
    model_path.write_bytes(b"test-model-bytes")
    tokenizer_path = tmp_path / "tokenizer"
    tokenizer_path.mkdir()
    for name, content in {
        "config.json": b"config",
        "special_tokens_map.json": b"special",
        "spiece.model": b"sentencepiece",
        "tokenizer.json": b"tokenizer",
        "tokenizer_config.json": b"tokenizer-config",
    }.items():
        (tokenizer_path / name).write_bytes(content)
    model_files = {"model.onnx": _sha256(model_path)}
    tokenizer_files = {
        path.name: _sha256(path)
        for path in sorted(tokenizer_path.iterdir())
        if path.is_file()
    }
    model_digest = gate7_runner._asset_map_digest(model_files)
    tokenizer_digest = gate7_runner._asset_map_digest(tokenizer_files)
    monkeypatch.setattr(gate7_runner, "PINNED_MODEL_FILES", model_files)
    monkeypatch.setattr(
        gate7_runner, "PINNED_MODEL_DIGEST_SHA256", model_digest
    )
    monkeypatch.setattr(gate7_runner, "PINNED_TOKENIZER_FILES", tokenizer_files)
    monkeypatch.setattr(
        gate7_runner, "PINNED_TOKENIZER_DIGEST_SHA256", tokenizer_digest
    )
    monkeypatch.setattr(
        gate7_runner, "PINNED_MODEL_SHA256", model_files["model.onnx"]
    )
    import huggingface_hub

    monkeypatch.setattr(
        huggingface_hub,
        "hf_hub_download",
        lambda **_kwargs: str(model_path),
    )
    monkeypatch.setattr(
        huggingface_hub,
        "snapshot_download",
        lambda **_kwargs: str(tokenizer_path),
    )
    return {
        "model_path": model_path,
        "tokenizer_path": tokenizer_path,
        "model_files": model_files,
        "model_digest_sha256": model_digest,
        "tokenizer_files": tokenizer_files,
        "tokenizer_digest_sha256": tokenizer_digest,
    }


def _direct_child_inputs(tmp_path, monkeypatch):
    from benchmarks.carma.gate7_v2_trace import (
        build_gate7_semantic_index,
        build_gate7_trace,
        semantic_index_artifact,
    )

    prepared_root = tmp_path / "prepared-root"
    prepared_root.mkdir()
    prepared = _prepared(prepared_root)
    trace, trace_hash, metadata = build_gate7_trace(
        prepared, seed=17, requests=20, capacity=5
    )
    trace_path = tmp_path / "trace.jsonl"
    gate7_runner._serialize_trace(trace_path, trace)
    semantic_index = build_gate7_semantic_index(
        prepared, sorted({row.concept_id for row in trace})
    )
    semantic_path = tmp_path / "semantic-index.json"
    gate7_runner._write_json(
        semantic_path, semantic_index_artifact(semantic_index, 17, trace_hash)
    )
    warmup_path = tmp_path / "warmup.json"
    gate7_runner._write_json(
        warmup_path,
        gate7_runner._build_warmup_artifact(
            trace, metadata["hot_text_ids"], 17, trace_hash, 2
        ),
    )
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    (control_dir / "start.ack").write_text("ok\n", encoding="utf-8")
    (control_dir / "end.ack").write_text("ok\n", encoding="utf-8")
    assets = _local_asset_fixture(tmp_path, monkeypatch)
    assets.update(
        {
            "kind": "pinned_onnx",
            "model_path": str(assets["model_path"]),
            "tokenizer_path": str(assets["tokenizer_path"]),
            "model_sha256": assets["model_files"]["model.onnx"],
        }
    )
    config = Gate7Config(
        mode="smoke",
        requests=20,
        capacity=5,
        warmup_requests=2,
        resource_interval_ms=10,
        fake_embedding=False,
    )
    return {
        "trace_path": trace_path,
        "trace_hash": trace_hash,
        "semantic_path": semantic_path,
        "semantic_hash": _sha256(semantic_path),
        "warmup_path": warmup_path,
        "warmup_hash": _sha256(warmup_path),
        "control_dir": control_dir,
        "assets": assets,
        "config": config,
    }


def _formal_context(head):
    anchor = {
        "schema_version": gate7_runner.FORMAL_SOURCE_ANCHOR_SCHEMA_VERSION,
        "tag_name": gate7_runner.FORMAL_SOURCE_TAG,
        "remote_name": gate7_runner.FORMAL_SOURCE_REMOTE,
        "object_format": "sha1",
        "tag_object_type": "tag",
        "tag_object_id": "b" * 40,
        "peeled_commit": head,
        "head_commit": head,
        "tree_id": "c" * 40,
        "tag_payload_sha256": "d" * 64,
        "tag_payload_bytes": 123,
        "annotation": {
            "experiment_id": gate7_runner.EXPERIMENT_ID,
            "contract_sha256": gate7_runner.PINNED_CONTRACT_SHA256,
        },
        "remote": {
            "fetch_urls": [gate7_runner.FORMAL_SOURCE_REMOTE_URL],
            "push_urls": [gate7_runner.FORMAL_SOURCE_REMOTE_URL],
            "tag_object_id": "b" * 40,
            "peeled_commit": head,
        },
        "retained_tag_payload_path": gate7_runner.FORMAL_SOURCE_TAG_PAYLOAD_PATH,
    }
    dependency = {
        "attestation_sha256": "e" * 64,
        "environment": {"environment_sha256": "f" * 64},
    }
    bootstrap = _bootstrap_observation("parent")
    entrypoint = {
        "schema_version": gate7_runner.FORMAL_ENTRYPOINT_SCHEMA_VERSION,
        "marker": gate7_runner.FORMAL_ENTRYPOINT_MARKER,
        "mode": "full",
        "bootstrap_attestation": bootstrap,
        "bootstrap_attestation_sha256": bootstrap["attestation_sha256"],
    }
    power = {
        "observed_at_utc": "2026-08-27T10:00:00+00:00",
        "available": False,
        "plugged": None,
        "percent": None,
        "seconds_left": None,
    }
    retained = {
        "trace_construction_prepared_dir": "source/prepared",
        "audit_source_policy": "retained_attempt_copies_only",
        "archive": {
            "path": gate7_runner.RETAINED_QQP_ARCHIVE_PATH,
            "sha256": "1" * 64,
            "bytes": 10,
        },
        "prepared": {},
        "dependency_attestation": {
            "path": gate7_runner.DEPENDENCY_ATTESTATION_PATH,
            "sha256": "2" * 64,
            "bytes": 10,
            "attestation_sha256": dependency["attestation_sha256"],
        },
        "formal_source_tag_payload": {
            "path": gate7_runner.FORMAL_SOURCE_TAG_PAYLOAD_PATH,
            "sha256": anchor["tag_payload_sha256"],
            "bytes": anchor["tag_payload_bytes"],
        },
    }
    return {
        "contract_identity": {
            "sha256": gate7_runner.PINNED_CONTRACT_SHA256,
            "bytes": 123,
        },
        "source_snapshot_sha256": "3" * 64,
        "formal_source_anchor": anchor,
        "dependency_attestation": dependency,
        "environment_sha256": dependency["environment"]["environment_sha256"],
        "entrypoint_attestation": entrypoint,
        "pre_start_power": power,
        "power_observations": {
            "pre_start": power,
            "end": power,
            "availability_limitation": "synthetic test observation",
        },
        "retained_inputs": retained,
    }


def _register_attempt(output, attempt_id, started, head, prior_attempts, context=None):
    context = context or _formal_context(head)
    declaration = gate7_runner._register_formal_attempt(
        output,
        attempt_id,
        started,
        head,
        prior_attempts,
        context["contract_identity"],
        context["source_snapshot_sha256"],
        context["formal_source_anchor"],
        context["dependency_attestation"]["attestation_sha256"],
        context["environment_sha256"],
        context["entrypoint_attestation"],
        context["pre_start_power"],
        context["retained_inputs"],
    )
    return declaration, context


def _bootstrap_observation(role):
    target = (
        "benchmarks/carma/gate7_v2_audit.py"
        if role == "auditor"
        else "benchmarks/carma/gate7_v2_onnx_integration_benchmark.py"
    )
    wrapper_environment = {
        "CARMA_GATE7_WRAPPER_SHELL": "gate7c-shell-v1",
        "CARMA_GATE7_LAUNCH_MODE": "full",
        "CARMA_GATE7_WRAPPER_SHELL_PROFILE": "env-i-v1",
        "CARMA_GATE7_WRAPPER_SHELL_HOME": "/tmp/gate7-home",
        "CARMA_GATE7_WRAPPER_SHELL_PWD": str(gate7_runner.PROJECT_ROOT),
        "CARMA_GATE7_WRAPPER_SHELL_TMPDIR": "/tmp",
        "CARMA_GATE7_WRAPPER_SHELL_SHLVL": "1",
        "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF": "",
    }
    wrapper_shell_startup = {
        "schema_version": (
            gate7_runner.WRAPPER_SHELL_STARTUP_SCHEMA_VERSION
        ),
        "profile": "env-i-v1",
        "marker": "gate7c-shell-v1",
        "launch_mode": "full",
        "outer_env_i_operator_root_required": True,
        "home": "/tmp/gate7-home",
        "pwd": str(gate7_runner.PROJECT_ROOT),
        "tmpdir": "/tmp",
        "shlvl": "1",
        "optional_cf_user_text_encoding": "",
        "fixed_environment": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LC_CTYPE": "C.UTF-8",
            "TZ": "UTC",
        },
    }
    wrapper_shell_startup["attestation_sha256"] = (
        gate7_runner._canonical_mapping_sha256(wrapper_shell_startup)
    )
    value = {
        "schema_version": gate7_runner.ISOLATED_BOOTSTRAP_SCHEMA_VERSION,
        "role": role,
        "target": target,
        "target_sha256": "a" * 64,
        "project_root": str(gate7_runner.PROJECT_ROOT),
        "venv_root": str(gate7_runner.PROJECT_ROOT / ".venv"),
        "site_packages": str(gate7_runner.PROJECT_ROOT / ".venv" / "site-packages"),
        "python": {
            "initial_sys_path": [],
            "environment": wrapper_environment,
            "wrapper_shell_startup": wrapper_shell_startup,
        },
        "pyvenv": {},
        "lock": {},
        "dependency": {},
        "preimport_source": {
            "schema_version": gate7_runner.PREIMPORT_SOURCE_SCHEMA_VERSION,
            "enforced": True,
            "launch_mode": "full",
            "git_executable": {
                "path": "/usr/bin/git",
                "sha256": "b" * 64,
                "bytes": 123,
                "version": "git version 2.50.1",
            },
            "git_status_clean": True,
            "anchor": {
                "object_format": "sha1",
                "head_commit": "c" * 40,
                "head_tree": "d" * 40,
                "tag_name": gate7_runner.FORMAL_SOURCE_TAG,
                "tag_object_type": "tag",
                "tag_object_id": "e" * 40,
                "peeled_commit": "c" * 40,
                "tag_tree": "d" * 40,
                "tag_payload_sha256": "f" * 64,
                "tag_payload_bytes": 456,
                "annotation": {
                    "experiment_id": gate7_runner.EXPERIMENT_ID,
                    "contract_sha256": gate7_runner.PINNED_CONTRACT_SHA256,
                },
                "contract_path": gate7_runner.DEFAULT_CONTRACT.relative_to(
                    gate7_runner.PROJECT_ROOT
                ).as_posix(),
                "contract_sha256": gate7_runner.PINNED_CONTRACT_SHA256,
                "contract_bytes": 789,
                "remote": {
                    "remote_name": gate7_runner.FORMAL_SOURCE_REMOTE,
                    "fetch_url": gate7_runner.FORMAL_SOURCE_REMOTE_URL,
                    "fetch_url_count": 1,
                    "push_url": gate7_runner.FORMAL_SOURCE_REMOTE_URL,
                    "push_url_count": 1,
                    "tag_ref": "refs/tags/%s"
                    % gate7_runner.FORMAL_SOURCE_TAG,
                    "tag_object_id": "e" * 40,
                    "peeled_ref": "refs/tags/%s^{}"
                    % gate7_runner.FORMAL_SOURCE_TAG,
                    "peeled_commit": "c" * 40,
                },
            },
            "inventory": {
                "runtime_roots": ["gptcache", "benchmarks"],
                "tracked_file_count": 10,
                "tracked_file_bytes": 1000,
                "tracked_file_map_sha256": "1" * 64,
                "runtime_file_count": 8,
                "runtime_untracked_or_missing_file_count": 0,
                "runtime_symlink_count": 0,
                "project_import_namespace": {
                    "excluded_sealed_roots": [".git", ".venv"],
                    "import_suffixes": [
                        ".py",
                        ".pyc",
                        ".pyo",
                        ".so",
                        ".pyd",
                        ".dylib",
                        ".dll",
                    ],
                    "import_file_count": 8,
                    "untracked_or_ignored_import_file_count": 0,
                    "symlink_count": 0,
                },
            },
        },
        "preloaded_module_origins": {},
        "site_module_absent": True,
        "pth_executed": False,
    }
    value["attestation_sha256"] = gate7_runner._canonical_mapping_sha256(value)
    return value


def _bootstrap_completion(context, terminal_intent):
    bootstrap = context["entrypoint_attestation"]["bootstrap_attestation"]
    completion = {
        "schema_version": gate7_runner.BOOTSTRAP_COMPLETION_SCHEMA_VERSION,
        "phase": "post_target",
        "role": "parent",
        "launch_mode": "full",
        "bootstrap_attestation_sha256": bootstrap["attestation_sha256"],
        "target_sha256": bootstrap["target_sha256"],
        "preimport_source_sha256": gate7_runner._canonical_mapping_sha256(
            bootstrap["preimport_source"]
        ),
        "dependency_sha256": gate7_runner._canonical_mapping_sha256(
            bootstrap["dependency"]
        ),
        "python_environment_sha256": gate7_runner._canonical_mapping_sha256(
            bootstrap["python"]["environment"]
        ),
        "target_exit_status": terminal_intent["target_exit_status"],
        "terminal_intent_sha256": terminal_intent["intent_sha256"],
        "checks_passed": True,
    }
    completion["attestation_sha256"] = (
        gate7_runner._canonical_mapping_sha256(completion)
    )
    return completion


def _append_completed_terminal_for_test(
    output, report, terminal_intent, bootstrap_completion
):
    """Emulate the bootstrap append without granting it to producer code."""

    entries, _ = gate7_runner._read_attempt_ledger(output.parent)
    start = entries[-1]
    manifest_path = output / "manifest.json"
    report_path = output / "gate7-preterminal-adjudication.json"
    terminal = {
        "schema_version": gate7_runner.ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": gate7_runner.EXPERIMENT_ID,
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
        "terminal_record_sha256": _sha256(manifest_path),
        "terminal_record_bytes": manifest_path.stat().st_size,
        "preterminal_report": report_path.name,
        "preterminal_report_sha256": _sha256(report_path),
        "preterminal_report_bytes": report_path.stat().st_size,
        "preterminal_report_status": report["status"],
        "preterminal_report_claimable": report["claimable"],
        "auditor_identity": report["auditor_identity"],
        "bootstrap_completion": bootstrap_completion,
        "previous_entry_sha256": start["entry_sha256"],
    }
    assert terminal_intent["intent_sha256"] == bootstrap_completion[
        "terminal_intent_sha256"
    ]
    terminal["entry_sha256"] = gate7_runner._ledger_entry_sha256(terminal)
    gate7_runner._append_attempt_ledger_entry(output.parent, terminal)
    return gate7_runner._ledger_prefix_identity(output.parent, terminal)


def _completed_formal_attempt(
    root, directory="attempt-01", append_terminal=True
):
    output = root / directory
    output.mkdir(parents=True)
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    context = _formal_context(head)
    attempt_id = gate7_runner._attempt_id_for(started, head)
    declaration, context = _register_attempt(
        output, attempt_id, started, head, []
    )
    auditor_identity = {
        "path": "benchmarks/carma/gate7_v2_audit.py",
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
        "contract": {
            "path": "docs/project/gate7-v2-remediation-contract.md",
            **context["contract_identity"],
        },
        "attempt_policy": {
            "formal_root": "artifacts/gate7-v2-onnx-attempts",
            "directory": directory,
            "eligibility": (
                "first structurally valid complete attempt in the retained "
                "predecessor chain"
            ),
            "rerun_scope": "complete five-seed, three-policy matrix under a new attempt ID",
        },
        "source_identities": {
            "benchmarks/carma/gate7_v2_audit.py": auditor_identity,
        },
        "source_snapshot_sha256": context["source_snapshot_sha256"],
        "formal_source_anchor": context["formal_source_anchor"],
        "dependency_attestation": context["dependency_attestation"],
        "environment_sha256": context["environment_sha256"],
        "entrypoint_attestation": context["entrypoint_attestation"],
        "power_observations": context["power_observations"],
        "retained_inputs": context["retained_inputs"],
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
    terminal_intent = gate7_runner._prepare_formal_terminal_intent(
        output, report, 0
    )
    bootstrap_completion = _bootstrap_completion(context, terminal_intent)
    terminal_declaration = None
    if append_terminal:
        terminal_declaration = _append_completed_terminal_for_test(
            output,
            report,
            terminal_intent,
            bootstrap_completion,
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
        "terminal_intent": terminal_intent,
        "bootstrap_completion": bootstrap_completion,
        "terminal_declaration": terminal_declaration,
        "context": context,
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


def test_policy_schedule_base_is_sha256_derived_and_counterbalanced():
    base = tuple(
        sorted(
            gate7_runner.POLICIES,
            key=lambda policy: hashlib.sha256(
                (
                    gate7_runner.POLICY_ORDER_NAMESPACE + "|" + policy
                ).encode("utf-8")
            ).digest(),
        )
    )
    schedule = policy_schedule(FULL_SEEDS)
    assert schedule[FULL_SEEDS[0]] == base
    assert {order[0] for order in schedule.values()} == set(
        gate7_runner.POLICIES
    )
    assert {order[-1] for order in schedule.values()} == set(
        gate7_runner.POLICIES
    )


def test_default_contract_uses_v2_path_and_frozen_cross_module_digest():
    from benchmarks.carma import gate7_v2_audit
    from scripts import gate7_v2_isolated_bootstrap

    assert DEFAULT_CONTRACT.name == "gate7-v2-remediation-contract.md"
    assert PINNED_CONTRACT_SHA256 == _sha256(DEFAULT_CONTRACT)
    assert PINNED_CONTRACT_SHA256 != "0" * 64
    assert gate7_v2_audit.PINNED_CONTRACT_SHA256 == PINNED_CONTRACT_SHA256
    assert (
        gate7_v2_isolated_bootstrap.PINNED_CONTRACT_SHA256
        == PINNED_CONTRACT_SHA256
    )


def test_frozen_model_and_tokenizer_maps_have_exact_aggregate_digests():
    assert gate7_runner.PINNED_MODEL_FILES == {
        "model.onnx": (
            "a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8"
        )
    }
    assert gate7_runner.PINNED_MODEL_DIGEST_SHA256 == (
        "af8dd7ee021644802c30f27709e46ad77e65425d527ecdca8346995dea83d7ce"
    )
    assert gate7_runner.PINNED_TOKENIZER_FILES == {
        "config.json": (
            "69765de9af37e704755cab37bee53f251465c269ae3f0c95436ab250dd11e342"
        ),
        "special_tokens_map.json": (
            "129fed06908ddcc3e36105e41d753ff0b934e5cfb2e451ca0a48904acef41863"
        ),
        "spiece.model": (
            "fefb02b667a6c5c2fe27602d28e5fb3428f66ab89c7d6f388e7c8d44a02d0336"
        ),
        "tokenizer.json": (
            "d0a881fece9b11d4f8003a08ac7d8d65409e3aa573fc385faa8708cdd5a77087"
        ),
        "tokenizer_config.json": (
            "95f31ea415a8e447b1e2ca05b897a05c1f5857b0643579d42dbec5ac5c2555c1"
        ),
    }
    assert gate7_runner.PINNED_TOKENIZER_DIGEST_SHA256 == (
        "41f1aee1afa8c01eecd6f60e8097836cb8e97bae95bf2f7fe34d85c5764f9deb"
    )
    assert gate7_runner._asset_map_digest(
        gate7_runner.PINNED_MODEL_FILES
    ) == gate7_runner.PINNED_MODEL_DIGEST_SHA256
    assert gate7_runner._asset_map_digest(
        gate7_runner.PINNED_TOKENIZER_FILES
    ) == gate7_runner.PINNED_TOKENIZER_DIGEST_SHA256


def test_dependency_lock_and_record_aggregate_are_exactly_frozen():
    pins = gate7_runner._parse_benchmark_lock()
    assert len(pins) == gate7_runner.PINNED_BENCHMARK_LOCK_PIN_COUNT == 50
    assert gate7_runner._canonical_mapping_sha256(pins) == (
        gate7_runner.PINNED_BENCHMARK_PIN_MAP_SHA256
    )
    assert _sha256(gate7_runner.BENCHMARK_LOCK) == (
        gate7_runner.PINNED_BENCHMARK_LOCK_SHA256
    )
    assert gate7_runner.PINNED_LOCKED_RECORD_HASHED_FILE_COUNT == 10270
    assert gate7_runner.PINNED_LOCKED_RECORD_HASHED_BYTES == 362595746
    assert gate7_runner.PINNED_LOCKED_RECORD_AGGREGATE_SHA256 == (
        "1b59e03f6a6d52a8b64adf8f11eb8ed36c40f807d53248965a00c733f8dca2c3"
    )
    assert gate7_runner.PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY == {
        "record_sha256": (
            "873280782d16563eef2982efbead80814577c01aea0253cea060d7cb3cc5b140"
        ),
        "hashed_file_count": 11,
        "hashed_bytes": 29856,
        "hashed_files_sha256": (
            "29bf983930625eacdddedcd24953d1849e9ebf923f233c09bd38506d67391b91"
        ),
    }


def test_local_editable_record_rewrite_cannot_bless_mutated_bytes(
    tmp_path, monkeypatch
):
    prefix = tmp_path / "venv"
    site = prefix / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)

    class Distribution:
        def __init__(self, dist_info):
            self._path = dist_info

        def read_text(self, name):
            return (self._path / name).read_text(encoding="utf-8")

        def locate_file(self, relative):
            return site / relative

    def install(name, payload):
        package = site / name
        package.mkdir()
        member = package / "__init__.py"
        member.write_bytes(payload)
        dist_info = site / (name + "-1.0.dist-info")
        dist_info.mkdir()
        digest = hashlib.sha256(payload).digest()
        encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        record = dist_info / "RECORD"
        record.write_text(
            "%s/__init__.py,sha256=%s,%d\n%s/RECORD,,\n"
            % (name, encoded, len(payload), dist_info.name),
            encoding="utf-8",
        )
        hashed_files = {
            str(member.relative_to(prefix)): hashlib.sha256(payload).hexdigest()
        }
        summary = {
            "record_sha256": _sha256(record),
            "hashed_file_count": 1,
            "hashed_bytes": len(payload),
            "hashed_files_sha256": gate7_runner._canonical_mapping_sha256(
                hashed_files
            ),
        }
        return Distribution(dist_info), summary

    gptcache, local_summary = install("gptcache", b"alpha")
    dummy, dummy_summary = install("dummy", b"fixed")
    monkeypatch.setattr(
        gate7_runner, "PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY", local_summary
    )
    monkeypatch.setattr(gate7_runner, "PINNED_BENCHMARK_LOCK_PIN_COUNT", 1)
    monkeypatch.setattr(gate7_runner, "PINNED_LOCKED_RECORD_HASHED_FILE_COUNT", 1)
    monkeypatch.setattr(
        gate7_runner, "PINNED_LOCKED_RECORD_HASHED_BYTES", len(b"fixed")
    )
    monkeypatch.setattr(
        gate7_runner,
        "PINNED_LOCKED_RECORD_AGGREGATE_SHA256",
        gate7_runner._canonical_mapping_sha256({"dummy": dummy_summary}),
    )
    gate7_runner._verify_distribution_records(
        {"dummy": dummy, "gptcache": gptcache}, prefix, [str(site)]
    )

    # Rewrite both the local source byte and its RECORD hash while preserving
    # count and byte totals. A self-authenticating RECORD would accept this.
    member = site / "gptcache" / "__init__.py"
    member.write_bytes(b"bravo")
    digest = hashlib.sha256(b"bravo").digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    (gptcache._path / "RECORD").write_text(
        "gptcache/__init__.py,sha256=%s,5\n%s/RECORD,,\n"
        % (encoded, gptcache._path.name),
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError, match="local editable GPTCache RECORD identity changed"
    ):
        gate7_runner._verify_distribution_records(
            {"dummy": dummy, "gptcache": gptcache}, prefix, [str(site)]
        )


def test_bootstrap_observation_requires_raw_sentinel_and_exact_role(
    monkeypatch,
):
    parent = _bootstrap_observation("parent")
    monkeypatch.delattr(
        sys, "_gate7_v2_bootstrap_attestation", raising=False
    )
    monkeypatch.setenv(
        "CARMA_GATE7_BOOTSTRAP_SCHEMA",
        gate7_runner.ISOLATED_BOOTSTRAP_SCHEMA_VERSION,
    )
    monkeypatch.setenv("CARMA_GATE7_BOOTSTRAP_ROLE", "parent")
    monkeypatch.setenv(
        "CARMA_GATE7_BOOTSTRAP_ATTESTATION_SHA256",
        parent["attestation_sha256"],
    )
    monkeypatch.setenv("CARMA_GATE7_LAUNCH_MODE", "full")
    with pytest.raises(RuntimeError, match="lacks the isolated-bootstrap sentinel"):
        gate7_runner._bootstrap_attestation("parent")

    monkeypatch.setattr(
        sys, "_gate7_v2_bootstrap_attestation", parent, raising=False
    )
    assert gate7_runner._bootstrap_attestation("parent") == parent
    with pytest.raises(RuntimeError, match="observation is inconsistent"):
        gate7_runner._bootstrap_attestation("child")
    assert gate7_runner._validated_bootstrap_observation(
        _bootstrap_observation("child"), "child"
    )["role"] == "child"

    monkeypatch.setenv("CARMA_GATE7_LAUNCH_MODE", "smoke")
    with pytest.raises(RuntimeError, match="sentinel is inconsistent"):
        gate7_runner._bootstrap_attestation("parent")


def test_bootstrap_preimport_source_rejects_hygiene_drift():
    compromised = _bootstrap_observation("child")
    compromised["preimport_source"]["inventory"][
        "runtime_untracked_or_missing_file_count"
    ] = 1
    unsigned = dict(compromised)
    unsigned.pop("attestation_sha256")
    compromised["attestation_sha256"] = (
        gate7_runner._canonical_mapping_sha256(unsigned)
    )
    with pytest.raises(RuntimeError, match="observation is inconsistent"):
        gate7_runner._validated_bootstrap_observation(compromised, "child")

    compromised = _bootstrap_observation("child")
    compromised["preimport_source"]["inventory"][
        "project_import_namespace"
    ]["symlink_count"] = 1
    unsigned = dict(compromised)
    unsigned.pop("attestation_sha256")
    compromised["attestation_sha256"] = (
        gate7_runner._canonical_mapping_sha256(unsigned)
    )
    with pytest.raises(RuntimeError, match="observation is inconsistent"):
        gate7_runner._validated_bootstrap_observation(compromised, "child")

    smoke = _bootstrap_observation("child")
    smoke["preimport_source"] = {
        "schema_version": gate7_runner.PREIMPORT_SOURCE_SCHEMA_VERSION,
        "enforced": False,
        "launch_mode": "smoke",
    }
    smoke["python"]["environment"]["CARMA_GATE7_LAUNCH_MODE"] = "smoke"
    smoke["python"]["environment"][
        "CARMA_GATE7_WRAPPER_SHELL_PROFILE"
    ] = "development-smoke"
    smoke_startup = smoke["python"]["wrapper_shell_startup"]
    smoke_startup["profile"] = "development-smoke"
    smoke_startup["launch_mode"] = "smoke"
    smoke_startup["outer_env_i_operator_root_required"] = False
    unsigned_startup = dict(smoke_startup)
    unsigned_startup.pop("attestation_sha256")
    smoke_startup["attestation_sha256"] = (
        gate7_runner._canonical_mapping_sha256(unsigned_startup)
    )
    unsigned = dict(smoke)
    unsigned.pop("attestation_sha256")
    smoke["attestation_sha256"] = gate7_runner._canonical_mapping_sha256(
        unsigned
    )
    assert gate7_runner._validated_bootstrap_observation(
        smoke, "child"
    )["preimport_source"] == smoke["preimport_source"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("remote_name", "origin"),
        ("fetch_url", "https://example.invalid/repository.git"),
        ("fetch_url_count", 2),
        ("push_url", "https://example.invalid/repository.git"),
        ("push_url_count", 2),
        ("tag_ref", "refs/tags/wrong"),
        ("tag_object_id", "9" * 40),
        ("peeled_ref", "refs/tags/wrong^{}"),
        ("peeled_commit", "9" * 40),
    ],
)
def test_bootstrap_preimport_source_requires_exact_submission_remote(
    field, value
):
    valid = _bootstrap_observation("parent")
    assert gate7_runner._validated_bootstrap_observation(
        valid, "parent"
    )["preimport_source"]["anchor"]["remote"] == {
        "remote_name": gate7_runner.FORMAL_SOURCE_REMOTE,
        "fetch_url": gate7_runner.FORMAL_SOURCE_REMOTE_URL,
        "fetch_url_count": 1,
        "push_url": gate7_runner.FORMAL_SOURCE_REMOTE_URL,
        "push_url_count": 1,
        "tag_ref": "refs/tags/%s" % gate7_runner.FORMAL_SOURCE_TAG,
        "tag_object_id": "e" * 40,
        "peeled_ref": "refs/tags/%s^{}" % gate7_runner.FORMAL_SOURCE_TAG,
        "peeled_commit": "c" * 40,
    }

    compromised = _bootstrap_observation("parent")
    compromised["preimport_source"]["anchor"]["remote"][field] = value
    compromised.pop("attestation_sha256")
    compromised["attestation_sha256"] = (
        gate7_runner._canonical_mapping_sha256(compromised)
    )
    with pytest.raises(RuntimeError, match="observation is inconsistent"):
        gate7_runner._validated_bootstrap_observation(
            compromised, "parent"
        )


def test_bootstrap_wrapper_shell_startup_rejects_profile_spoof():
    compromised = _bootstrap_observation("parent")
    startup = compromised["python"]["wrapper_shell_startup"]
    startup["profile"] = "development-smoke"
    unsigned_startup = dict(startup)
    unsigned_startup.pop("attestation_sha256")
    startup["attestation_sha256"] = (
        gate7_runner._canonical_mapping_sha256(unsigned_startup)
    )
    unsigned = dict(compromised)
    unsigned.pop("attestation_sha256")
    compromised["attestation_sha256"] = (
        gate7_runner._canonical_mapping_sha256(unsigned)
    )
    with pytest.raises(RuntimeError, match="observation is inconsistent"):
        gate7_runner._validated_bootstrap_observation(
            compromised, "parent"
        )


def test_formal_entrypoint_requires_direct_bash_wrapper_argv(monkeypatch):
    wrapper = gate7_runner.FORMAL_WRAPPER.resolve()
    bootstrap = _bootstrap_observation("parent")
    parent_argv = ["/bin/bash", str(wrapper), "full"]

    class ParentProcess:
        def cmdline(self):
            return list(parent_argv)

        def exe(self):
            return "/bin/bash"

    monkeypatch.setattr(gate7_runner, "_bootstrap_attestation", lambda _role: bootstrap)
    monkeypatch.setattr(gate7_runner.os, "getppid", lambda: 123)
    monkeypatch.setenv(
        "CARMA_GATE7_FORMAL_ENTRYPOINT", gate7_runner.FORMAL_ENTRYPOINT_MARKER
    )
    monkeypatch.setenv("CARMA_GATE7_WRAPPER_PID", "123")
    monkeypatch.setenv("CARMA_GATE7_WRAPPER_PATH", str(wrapper))
    monkeypatch.setattr(gate7_runner.psutil, "Process", lambda _pid: ParentProcess())
    monkeypatch.setattr(
        gate7_runner,
        "_git_text",
        lambda *arguments: "a" * 40
        if arguments == ("rev-parse", "HEAD")
        else "",
    )
    wrapper_bytes = wrapper.read_bytes()
    monkeypatch.setattr(gate7_runner, "_git_bytes", lambda *_args: wrapper_bytes)

    attestation = gate7_runner._formal_entrypoint_attestation()
    assert attestation["parent_executable"] == "/bin/bash"
    assert attestation["parent_cmdline"] == parent_argv

    parent_argv[:] = [
        "/bin/bash",
        "scripts/run_gate7_v2_onnx_integration_benchmark.sh",
        "full",
    ]
    with pytest.raises(RuntimeError, match="not a direct full wrapper execution"):
        gate7_runner._formal_entrypoint_attestation()

    parent_argv[:] = [
        "/bin/bash",
        "-c",
        "exec /bin/bash %s full" % wrapper,
        str(wrapper),
        "full",
    ]
    with pytest.raises(RuntimeError, match="not a direct full wrapper execution"):
        gate7_runner._formal_entrypoint_attestation()


def test_child_environment_is_allowlisted_and_preserves_only_optional_cf(
    monkeypatch, tmp_path
):
    dynamic = {
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        "HF_HOME": str(tmp_path / "hf"),
        "HF_HUB_CACHE": str(tmp_path / "hf" / "hub"),
        "CARMA_GATE7_LAUNCH_MODE": "full",
        "CARMA_GATE7_WRAPPER_SHELL_PROFILE": "env-i-v1",
        "CARMA_GATE7_WRAPPER_SHELL_HOME": str(tmp_path / "home"),
        "CARMA_GATE7_WRAPPER_SHELL_PWD": str(gate7_runner.PROJECT_ROOT),
        "CARMA_GATE7_WRAPPER_SHELL_TMPDIR": "/tmp",
        "CARMA_GATE7_WRAPPER_SHELL_SHLVL": "1",
        "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF": "",
        "CARMA_GATE7_WRAPPER_PID": "123",
        "CARMA_GATE7_WRAPPER_PATH": str(gate7_runner.FORMAL_WRAPPER),
    }
    for key, value in dynamic.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("__CF_USER_TEXT_ENCODING", "0x1F5:0x0:0x0")
    monkeypatch.setenv("CARMA_GATE7_BOOTSTRAP_ROLE", "forged")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/forged.dylib")
    environment = gate7_runner._child_environment(True)

    assert environment["__CF_USER_TEXT_ENCODING"] == "0x1F5:0x0:0x0"
    assert environment["CARMA_GATE7_WRAPPER_PID"] == "123"
    assert environment["CARMA_GATE7_LAUNCH_MODE"] == "full"
    assert environment["CARMA_GATE7_WRAPPER_SHELL_PROFILE"] == "env-i-v1"
    assert "CARMA_GATE7_BOOTSTRAP_ROLE" not in environment
    assert "LD_PRELOAD" not in environment
    assert set(environment) == (
        set(gate7_runner.FORMAL_REQUIRED_ENVIRONMENT)
        | set(gate7_runner.FORMAL_DYNAMIC_ENVIRONMENT)
        | {"__CF_USER_TEXT_ENCODING"}
    )


def test_bootstrapped_smoke_child_keeps_nonformal_launch_environment():
    source = inspect.getsource(gate7_runner._run_bundle_impl)
    call_start = source.index("result, samples = _run_child_with_sampling(")
    call_end = source.index("supervisor_capture =", call_start)
    child_call = source[call_start:call_end]

    assert 'config.mode == "full"' in child_call
    assert "bootstrap_child," in child_call


def test_bootstrapped_smoke_child_propagates_bootstrap_runtime_values(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    runtime_tmp = tmp_path / "runtime" / "tmp"
    pycache = tmp_path / "runtime" / "pycache"
    for path in (home, runtime_tmp, pycache):
        path.mkdir(parents=True, exist_ok=True)
    dynamic = {
        "HOME": str(home),
        "TMPDIR": str(runtime_tmp),
        "PYTHONPYCACHEPREFIX": str(pycache),
        "HF_HOME": str(home / ".cache" / "huggingface"),
        "HF_HUB_CACHE": str(home / ".cache" / "huggingface" / "hub"),
        "CARMA_GATE7_LAUNCH_MODE": "smoke",
        "CARMA_GATE7_WRAPPER_SHELL_PROFILE": "development-smoke",
        "CARMA_GATE7_WRAPPER_SHELL_HOME": str(home),
        "CARMA_GATE7_WRAPPER_SHELL_PWD": str(gate7_runner.PROJECT_ROOT),
        "CARMA_GATE7_WRAPPER_SHELL_TMPDIR": "/tmp",
        "CARMA_GATE7_WRAPPER_SHELL_SHLVL": "1",
        "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF": "",
        "CARMA_GATE7_WRAPPER_PID": "123",
        "CARMA_GATE7_WRAPPER_PATH": str(gate7_runner.FORMAL_WRAPPER),
    }
    for key, value in dynamic.items():
        monkeypatch.setenv(key, value)

    environment = gate7_runner._child_environment(False, True)

    assert environment["CARMA_GATE7_LAUNCH_MODE"] == "smoke"
    assert environment["CARMA_GATE7_WRAPPER_PID"] == "123"
    assert environment["CARMA_GATE7_WRAPPER_PATH"] == str(
        gate7_runner.FORMAL_WRAPPER
    )
    assert environment["PYTHONPYCACHEPREFIX"] == str(pycache)


def test_launch_mode_binds_wrapper_mode_and_only_allows_unwrapped_smoke(
    monkeypatch,
):
    monkeypatch.delenv("CARMA_GATE7_LAUNCH_MODE", raising=False)
    gate7_runner._assert_launch_mode("smoke")
    with pytest.raises(RuntimeError, match="launch mode is missing"):
        gate7_runner._assert_launch_mode("full")

    monkeypatch.setenv("CARMA_GATE7_LAUNCH_MODE", "full")
    gate7_runner._assert_launch_mode("full")
    with pytest.raises(RuntimeError, match="differs from the wrapper"):
        gate7_runner._assert_launch_mode("smoke")

    monkeypatch.setenv("CARMA_GATE7_LAUNCH_MODE", "forged")
    with pytest.raises(RuntimeError, match="differs from the wrapper"):
        gate7_runner._assert_launch_mode("full")


def test_source_snapshot_includes_isolated_bootstrap_and_raw_archive():
    identities = gate7_runner._source_identities()
    assert identities["scripts/gate7_v2_isolated_bootstrap.py"] is not None
    assert identities["examples/benchmark/similiar_qqp_full.json.gz"] is not None


def test_parent_asset_preflight_retains_exact_maps_and_digests(
    tmp_path, monkeypatch
):
    expected = _local_asset_fixture(tmp_path, monkeypatch)
    assets = gate7_runner._resolve_onnx_assets()

    assert assets["model_files"] == expected["model_files"]
    assert assets["model_digest_sha256"] == expected["model_digest_sha256"]
    assert assets["tokenizer_files"] == expected["tokenizer_files"]
    assert (
        assets["tokenizer_digest_sha256"]
        == expected["tokenizer_digest_sha256"]
    )


def test_parent_asset_preflight_rejects_extra_or_changed_files(
    tmp_path, monkeypatch
):
    expected = _local_asset_fixture(tmp_path, monkeypatch)
    (expected["tokenizer_path"] / "unfrozen.json").write_text(
        "{}\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="tokenizer exact file map"):
        gate7_runner._resolve_onnx_assets()

    (expected["tokenizer_path"] / "unfrozen.json").unlink()
    expected["model_path"].write_bytes(b"mutated-model")
    with pytest.raises(RuntimeError, match="model exact file map"):
        gate7_runner._resolve_onnx_assets()


def test_parent_asset_preflight_rejects_aggregate_digest_drift(
    tmp_path, monkeypatch
):
    _local_asset_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        gate7_runner, "PINNED_TOKENIZER_DIGEST_SHA256", "0" * 64
    )
    with pytest.raises(RuntimeError, match="tokenizer aggregate digest"):
        gate7_runner._resolve_onnx_assets()


def test_child_rejects_parent_supplied_identity_drift(tmp_path, monkeypatch):
    expected = _local_asset_fixture(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="expected ONNX asset identities"):
        gate7_runner._verify_frozen_onnx_assets(
            expected["model_path"],
            expected["tokenizer_path"],
            expected["model_files"],
            "0" * 64,
            expected["tokenizer_files"],
            expected["tokenizer_digest_sha256"],
            "child pre-load",
        )


def test_formal_source_anchor_binds_raw_annotated_tag_and_remote(monkeypatch):
    head = "a" * 40
    tree = "c" * 40
    payload = (
        "object %s\n" % head
        + "type commit\n"
        + "tag %s\n" % gate7_runner.FORMAL_SOURCE_TAG
        + "tagger Gate 7 <gate7@example.invalid> 0 +0000\n\n"
        + "experiment_id=%s\n" % gate7_runner.EXPERIMENT_ID
        + "contract_sha256=%s\n" % gate7_runner.PINNED_CONTRACT_SHA256
    ).encode("utf-8")
    tag_object = hashlib.sha1(
        ("tag %d\0" % len(payload)).encode("ascii") + payload
    ).hexdigest()
    tag_ref = "refs/tags/%s" % gate7_runner.FORMAL_SOURCE_TAG
    remote_urls = {
        "fetch": gate7_runner.FORMAL_SOURCE_REMOTE_URL,
        "push": gate7_runner.FORMAL_SOURCE_REMOTE_URL,
    }

    def git_text(*arguments):
        values = {
            ("rev-parse", "--show-object-format"): "sha1",
            ("rev-parse", tag_ref): tag_object,
            ("cat-file", "-t", tag_object): "tag",
            ("rev-parse", "%s^{commit}" % tag_ref): head,
            ("rev-parse", "HEAD"): head,
            ("rev-parse", "%s^{tree}" % head): tree,
            (
                "remote",
                "get-url",
                "--all",
                gate7_runner.FORMAL_SOURCE_REMOTE,
            ): remote_urls["fetch"],
            (
                "remote",
                "get-url",
                "--push",
                "--all",
                gate7_runner.FORMAL_SOURCE_REMOTE,
            ): remote_urls["push"],
        }
        return values[arguments]

    monkeypatch.setattr(gate7_runner, "_git_text", git_text)
    monkeypatch.setattr(gate7_runner, "_git_bytes", lambda *_args: payload)
    monkeypatch.setattr(
        gate7_runner,
        "_remote_tag_refs",
        lambda *_args: {tag_ref: tag_object, "%s^{}" % tag_ref: head},
    )
    anchor, observed_payload = gate7_runner._capture_formal_source_anchor(
        {"sha256": gate7_runner.PINNED_CONTRACT_SHA256, "bytes": 123}, head
    )

    assert observed_payload == payload
    assert anchor["tag_object_id"] == tag_object
    assert anchor["tag_payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert anchor["remote"] == {
        "fetch_urls": [gate7_runner.FORMAL_SOURCE_REMOTE_URL],
        "push_urls": [gate7_runner.FORMAL_SOURCE_REMOTE_URL],
        "tag_object_id": tag_object,
        "peeled_commit": head,
    }

    remote_urls["push"] = "https://example.invalid/repointed.git"
    with pytest.raises(RuntimeError, match="remote URL differs"):
        gate7_runner._capture_formal_source_anchor(
            {"sha256": gate7_runner.PINNED_CONTRACT_SHA256, "bytes": 123}, head
        )
    remote_urls["push"] = gate7_runner.FORMAL_SOURCE_REMOTE_URL
    remote_urls["fetch"] = "\n".join(
        [
            gate7_runner.FORMAL_SOURCE_REMOTE_URL,
            gate7_runner.FORMAL_SOURCE_REMOTE_URL,
        ]
    )
    with pytest.raises(RuntimeError, match="remote URL differs"):
        gate7_runner._capture_formal_source_anchor(
            {"sha256": gate7_runner.PINNED_CONTRACT_SHA256, "bytes": 123}, head
        )
    remote_urls["fetch"] = gate7_runner.FORMAL_SOURCE_REMOTE_URL

    monkeypatch.setattr(
        gate7_runner,
        "_remote_tag_refs",
        lambda *_args: {tag_ref: "d" * 40, "%s^{}" % tag_ref: head},
    )
    with pytest.raises(RuntimeError, match="differs from remote submission"):
        gate7_runner._capture_formal_source_anchor(
            {"sha256": gate7_runner.PINNED_CONTRACT_SHA256, "bytes": 123}, head
        )


def test_retained_formal_inputs_copy_raw_archive_and_prepared_bytes(
    tmp_path, monkeypatch
):
    prepared = _prepared(tmp_path)
    archive = tmp_path / "similiar_qqp_full.json.gz"
    archive.write_bytes(b"frozen-qqp-archive")
    monkeypatch.setattr(gate7_runner, "DEFAULT_PREPARED", prepared)
    monkeypatch.setattr(gate7_runner, "DEFAULT_QQP_ARCHIVE", archive)
    monkeypatch.setattr(
        gate7_runner, "PINNED_ARCHIVE_SHA256", _sha256(archive)
    )
    monkeypatch.setattr(
        gate7_runner, "PINNED_PAIRS_SHA256", _sha256(prepared / "pairs.jsonl")
    )
    monkeypatch.setattr(
        gate7_runner, "PINNED_TEXTS_SHA256", _sha256(prepared / "texts.jsonl")
    )
    manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    manifest["archive_sha256"] = _sha256(archive)
    (prepared / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    output = tmp_path / "attempt"
    output.mkdir()
    head = "a" * 40
    context = _formal_context(head)
    tag_payload = b"tag-payload"
    context["formal_source_anchor"]["tag_payload_sha256"] = hashlib.sha256(
        tag_payload
    ).hexdigest()
    context["formal_source_anchor"]["tag_payload_bytes"] = len(tag_payload)
    context["retained_inputs"]["formal_source_tag_payload"].update(
        {
            "sha256": hashlib.sha256(tag_payload).hexdigest(),
            "bytes": len(tag_payload),
        }
    )
    retained_prepared, declaration = gate7_runner._retain_source_inputs(
        prepared,
        output,
        True,
        context["dependency_attestation"],
        context["formal_source_anchor"],
        tag_payload,
    )

    assert (output / gate7_runner.RETAINED_QQP_ARCHIVE_PATH).read_bytes() == (
        archive.read_bytes()
    )
    for name in ("pairs.jsonl", "texts.jsonl", "manifest.json"):
        assert (retained_prepared / name).read_bytes() == (prepared / name).read_bytes()
    assert declaration["audit_source_policy"] == "retained_attempt_copies_only"
    gate7_runner._assert_retained_inputs(
        output, declaration, context["dependency_attestation"]
    )


def test_final_start_boundary_rejects_mutated_retained_copy(tmp_path):
    root = tmp_path / "attempts"
    output = root / "attempt-01"
    retained_path = output / "source" / "prepared" / "pairs.jsonl"
    retained_path.parent.mkdir(parents=True)
    retained_path.write_bytes(b"original\n")
    retained_inputs = {
        "trace_construction_prepared_dir": (
            gate7_runner.RETAINED_PREPARED_DIRECTORY
        ),
        "audit_source_policy": "retained_attempt_copies_only",
        "archive": None,
        "prepared": {
            "pairs": {
                "path": "source/prepared/pairs.jsonl",
                "sha256": _sha256(retained_path),
                "bytes": retained_path.stat().st_size,
            }
        },
        "dependency_attestation": None,
        "formal_source_tag_payload": None,
    }
    retained_path.write_bytes(b"mutated!\n")
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    context = _formal_context(head)
    events = []

    with pytest.raises(RuntimeError, match="retained attempt input changed"):
        gate7_runner._register_formal_attempt(
            output,
            gate7_runner._attempt_id_for(started, head),
            started,
            head,
            [],
            context["contract_identity"],
            context["source_snapshot_sha256"],
            context["formal_source_anchor"],
            context["dependency_attestation"]["attestation_sha256"],
            context["environment_sha256"],
            context["entrypoint_attestation"],
            {},
            retained_inputs,
            dependency_attestation=context["dependency_attestation"],
            final_pre_start_check=lambda: events.append("fresh"),
            power_observer=lambda: events.append("power") or {
                "available": False
            },
        )

    assert events == ["fresh"]
    entries, _ = gate7_runner._read_attempt_ledger(root)
    assert [entry["event"] for entry in entries] == ["PROTOCOL_GENESIS"]


def test_final_start_freshness_order_is_inside_registration_boundary():
    source = inspect.getsource(gate7_runner._register_formal_attempt)
    assert source.index("_ensure_protocol_genesis") < source.index(
        "final_pre_start_check()"
    )
    assert source.index("final_pre_start_check()") < source.index(
        "_assert_retained_inputs"
    )
    assert source.index("_assert_retained_inputs") < source.index(
        "power_observer()"
    )
    assert source.index("power_observer()") < source.index(
        "entry: Dict[str, Any]"
    )
    assert source.index("entry: Dict[str, Any]") < source.index(
        "_append_attempt_ledger_entry"
    )


@pytest.mark.parametrize("failure_stage", ["retained copy", "power observation"])
def test_unregistered_preflight_failure_is_quarantined_outside_formal_root(
    tmp_path, monkeypatch, failure_stage
):
    formal_root = tmp_path / "formal"
    quarantine_root = tmp_path / "preflight"
    output = formal_root / "attempt-01"
    output.mkdir(parents=True)
    (output / "partial.bin").write_bytes(b"partial")
    monkeypatch.setattr(gate7_runner, "FORMAL_ATTEMPT_ROOT", formal_root)
    monkeypatch.setattr(
        gate7_runner, "FORMAL_PREFLIGHT_FAILURE_ROOT", quarantine_root
    )

    destination = gate7_runner._quarantine_unregistered_formal_attempt(
        output, "attempt-id", RuntimeError(failure_stage)
    )

    assert not output.exists()
    assert destination.parent == quarantine_root
    assert (destination / "partial.bin").read_bytes() == b"partial"
    diagnostic = json.loads(
        (destination / "preflight-failure.json").read_text(encoding="utf-8")
    )
    assert diagnostic["registered_start"] is False
    assert diagnostic["error_message"] == failure_stage


def test_registered_start_is_never_quarantined(tmp_path, monkeypatch):
    formal_root = tmp_path / "formal"
    quarantine_root = tmp_path / "preflight"
    output = formal_root / "attempt-01"
    output.mkdir(parents=True)
    monkeypatch.setattr(gate7_runner, "FORMAL_ATTEMPT_ROOT", formal_root)
    monkeypatch.setattr(
        gate7_runner, "FORMAL_PREFLIGHT_FAILURE_ROOT", quarantine_root
    )
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    _register_attempt(
        output,
        gate7_runner._attempt_id_for(started, head),
        started,
        head,
        [],
    )

    with pytest.raises(RuntimeError, match="START-registered"):
        gate7_runner._quarantine_unregistered_formal_attempt(
            output,
            gate7_runner._attempt_id_for(started, head),
            RuntimeError("power observation"),
        )
    assert output.is_dir()
    assert not quarantine_root.exists()


def test_remote_preflight_precedes_attempt_directory_creation():
    source = inspect.getsource(gate7_runner._run_bundle_impl)
    assert source.index("_capture_formal_source_anchor(") < source.index(
        "_create_attempt_directory"
    )


def test_formal_preexisting_empty_attempt_directory_is_not_reused(tmp_path):
    output = tmp_path / "attempt-existing"
    output.mkdir()

    with pytest.raises(FileExistsError, match="formal output directory"):
        gate7_runner._create_attempt_directory(output, True)

    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_warmup_artifact_hash_and_content_are_mutation_sensitive(
    tmp_path, monkeypatch
):
    child = _direct_child_inputs(tmp_path, monkeypatch)
    value, texts = gate7_runner._load_and_verify_warmup_artifact(
        child["warmup_path"],
        child["warmup_hash"],
        gate7_runner._load_trace(child["trace_path"]),
        17,
        child["trace_hash"],
        2,
    )
    assert value["schema_version"] == gate7_runner.WARMUP_SCHEMA_VERSION
    assert len(texts) == 2
    mutated = dict(value)
    mutated["warmup_texts"] = list(reversed(mutated["warmup_texts"]))
    gate7_runner._write_json(child["warmup_path"], mutated)
    with pytest.raises(RuntimeError, match="hash differs"):
        gate7_runner._load_and_verify_warmup_artifact(
            child["warmup_path"],
            child["warmup_hash"],
            gate7_runner._load_trace(child["trace_path"]),
            17,
            child["trace_hash"],
            2,
        )


def test_child_hashes_assets_before_model_load_and_after_measured_loop(
    tmp_path, monkeypatch
):
    child = _direct_child_inputs(tmp_path, monkeypatch)
    events = []
    original_verify = gate7_runner._verify_frozen_onnx_assets

    def tracked_verify(*args, **kwargs):
        events.append(kwargs.get("stage", args[-1]))
        return original_verify(*args, **kwargs)

    class LocalEmbedder(gate7_runner.DeterministicFakeEmbedder):
        providers = ["CPUExecutionProvider"]

        def __init__(self, *_args, **_kwargs):
            events.append("model_load")

    monkeypatch.setattr(
        gate7_runner, "_verify_frozen_onnx_assets", tracked_verify
    )
    monkeypatch.setattr(gate7_runner, "PinnedOnnxEmbedder", LocalEmbedder)
    result = gate7_runner._execute_child(
        "LRU",
        17,
        0,
        "attempt",
        child["trace_path"],
        child["warmup_path"],
        child["control_dir"],
        child["trace_hash"],
        child["semantic_path"],
        child["semantic_hash"],
        child["config"],
        child["assets"],
        child["warmup_hash"],
    )

    assert events == ["child pre-load", "model_load", "child post-loop"]
    assert result["asset_integrity"] == {
        "pre_verified": True,
        "post_verified": True,
        "model_files": child["assets"]["model_files"],
        "model_digest_sha256": child["assets"]["model_digest_sha256"],
        "tokenizer_files": child["assets"]["tokenizer_files"],
        "tokenizer_digest_sha256": child["assets"][
            "tokenizer_digest_sha256"
        ],
    }
    assert result["summary"]["asset_integrity_pre_verified"] is True
    assert result["summary"]["asset_integrity_post_verified"] is True
    service_qps = len(result["requests"]) / (
        sum(row["request_total_ns"] for row in result["requests"]) / 1e9
    )
    assert result["summary"]["service_throughput_qps"] == round(
        service_qps, 6
    )
    assert result["summary"]["throughput_qps"] == result["summary"][
        "service_throughput_qps"
    ]
    assert result["summary"]["loop_throughput_qps"] > 0


def test_child_post_loop_asset_mutation_prevents_success(tmp_path, monkeypatch):
    child = _direct_child_inputs(tmp_path, monkeypatch)
    original_verify = gate7_runner._verify_frozen_onnx_assets
    stages = []

    def mutate_before_post_check(*args, **kwargs):
        stage = kwargs.get("stage", args[-1])
        stages.append(stage)
        if stage == "child post-loop":
            (Path(child["assets"]["tokenizer_path"]) / "late-drift.json").write_text(
                "{}\n", encoding="utf-8"
            )
        return original_verify(*args, **kwargs)

    class LocalEmbedder(gate7_runner.DeterministicFakeEmbedder):
        providers = ["CPUExecutionProvider"]

        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(
        gate7_runner,
        "_verify_frozen_onnx_assets",
        mutate_before_post_check,
    )
    monkeypatch.setattr(gate7_runner, "PinnedOnnxEmbedder", LocalEmbedder)
    with pytest.raises(RuntimeError, match="tokenizer exact file map"):
        gate7_runner._execute_child(
            "LRU",
            17,
            0,
            "attempt",
            child["trace_path"],
            child["warmup_path"],
            child["control_dir"],
            child["trace_hash"],
            child["semantic_path"],
            child["semantic_hash"],
            child["config"],
            child["assets"],
            child["warmup_hash"],
        )
    assert stages == ["child pre-load", "child post-loop"]


def test_policy_schedule_rejects_duplicate_seeds():
    with pytest.raises(ValueError, match="seeds must be unique"):
        policy_schedule((20261001, 20261001))


def test_v2_payload_carries_independent_source_identity():
    response = gate7_runner._materialize_response(
        "recorded-response-v2:concept-7:text-11"
    )
    assert response == {
        "concept_id": "concept-7",
        "response_id": "concept-7",
        "source_text_id": "text-11",
        "text": "recorded-response-v2:concept-7:text-11",
    }
    with pytest.raises(ValueError, match="unexpected schema"):
        gate7_runner._materialize_response("recorded-response:concept-7")


def test_semantic_guardrail_is_separate_from_structural_validity():
    semantic_counts = {
        "direct_negative_hits": 1,
        "component_derived_negative_hits": 0,
        "unlabeled_cross_concept_hits": 0,
    }
    request = {
        "hit_class": "direct_negative",
        "structural_failure": False,
        "structural_failure_reasons": "",
    }
    assert gate7_runner._semantic_guardrail_status(semantic_counts) == "FAIL"
    assert gate7_runner._structural_summary([request])["structural_valid"] is True


@pytest.mark.parametrize(
    "counters,expected",
    [
        ({"hits": 0}, "NO_HITS"),
        ({"hits": 1, "same_concept_hits": 1}, "PASS_OBSERVED"),
        (
            {"hits": 1, "unlabeled_cross_concept_hits": 1},
            "PENDING_INDETERMINATE",
        ),
    ],
)
def test_semantic_guardrail_status_vocabulary(counters, expected):
    assert gate7_runner._semantic_guardrail_status(counters) == expected


def test_unresolved_is_structural_and_outside_semantic_classifier():
    request = {
        "hit_class": "unresolved",
        "semantic_relation": "not_applicable",
        "semantic_status": "not_applicable",
        "semantic_label": None,
        "semantic_evidence_kind": "none",
        "semantic_source_indices": [],
        "structural_failure": True,
        "structural_failure_reasons": "unresolved_provenance",
    }
    summary = gate7_runner._structural_summary([request])
    assert summary["structural_valid"] is False
    assert summary["structural_failure_reason_counts"] == {
        "unresolved_provenance": 1
    }


def test_resolved_but_conflicting_three_way_provenance_is_unresolved():
    cached_question = ("text-a", "concept-a")
    cached_answer = ("text-b", "concept-b")
    returned_payload = ("text-b", "concept-b")
    provenance_resolved = all(
        value
        for identity in (cached_question, cached_answer, returned_payload)
        for value in identity
    )
    provenance_consistent = (
        cached_question == cached_answer == returned_payload
    )
    classifier_called = False

    def must_not_classify(*_args, **_kwargs):
        nonlocal classifier_called
        classifier_called = True
        raise AssertionError("inconsistent provenance reached semantic classifier")

    outcome = gate7_runner._classify_request_semantics(
        must_not_classify,
        object(),
        True,
        provenance_resolved,
        provenance_consistent,
        "query-text",
        "query-concept",
        cached_question[0],
        cached_question[1],
    )

    assert classifier_called is False
    assert outcome == {
        "semantic_relation": "not_applicable",
        "semantic_status": "not_applicable",
        "semantic_label": None,
        "semantic_evidence_kind": "none",
        "semantic_source_indices": (),
        "hit_class": "unresolved",
    }
    structural = gate7_runner._structural_summary(
        [
            {
                **outcome,
                "structural_failure": True,
                "structural_failure_reasons": "response_provenance_corruption",
            }
        ]
    )
    assert structural["structural_valid"] is False


def test_child_failure_diagnostics_retain_only_completed_request_prefix():
    gate7_runner._CHILD_DIAGNOSTIC_STATE.clear()
    gate7_runner._CHILD_DIAGNOSTIC_STATE.update(
        {
            "attempt_id": "attempt",
            "run_id": "run",
            "seed": 17,
            "policy": "LRU",
            "order_position": 0,
            "trace_hash": "a" * 64,
            "request_buffer": [
                {"request_index": 0, "exclusive_reconciles": True},
                {"request_index": 1, "exclusive_reconciles": False},
            ],
            "adapter_state": {"request_index": 1},
        }
    )
    diagnostic = gate7_runner._child_failure_diagnostics(
        RuntimeError("storage write failed")
    )
    gate7_runner._CHILD_DIAGNOSTIC_STATE.clear()

    assert diagnostic["failed_request_index"] == 1
    assert diagnostic["completed_request_count"] == 1
    assert diagnostic["completed_requests"] == [
        {"request_index": 0, "exclusive_reconciles": True}
    ]


def test_fake_environment_allows_optional_benchmark_stack_to_be_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "transformers", None)
    environment = gate7_runner._environment()
    assert environment["onnxruntime"] is None
    assert environment["onnxruntime_available"] is False
    assert environment["transformers"] is None
    assert environment["transformers_available"] is False


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

    identity, context = _register_attempt(
        output, attempt_id, started, head, []
    )
    entries, raw_lines = gate7_runner._read_attempt_ledger(root)

    assert identity["prefix_rows"] == 2
    assert identity["prefix_sha256"] == _sha256(root / "attempt-ledger.jsonl")
    assert len(entries) == len(raw_lines) == 2
    assert [entry["event"] for entry in entries] == [
        "PROTOCOL_GENESIS",
        "START",
    ]
    genesis, start = entries
    assert genesis["sequence"] == 1
    assert genesis["v1_preservation"] == gate7_runner._v1_preservation_identity()
    assert genesis["v2_contract"] == {
        "path": "docs/project/gate7-v2-remediation-contract.md",
        "sha256": gate7_runner.PINNED_CONTRACT_SHA256,
    }
    assert start["attempt_id"] == attempt_id
    assert start["sequence"] == 2
    assert start["previous_entry_sha256"] == genesis["entry_sha256"]
    assert start["formal_source_anchor"] == context["formal_source_anchor"]
    assert start["dependency_attestation_sha256"] == (
        context["dependency_attestation"]["attestation_sha256"]
    )
    assert start["environment_sha256"] == context["environment_sha256"]


def test_formal_terminal_binds_exact_manifest_and_preterminal_bytes(tmp_path):
    root = tmp_path / "attempts"
    fixture = _completed_formal_attempt(root)
    entries, _ = gate7_runner._read_attempt_ledger(root)
    terminal = entries[-1]

    assert [entry["event"] for entry in entries] == [
        "PROTOCOL_GENESIS",
        "START",
        "TERMINAL",
    ]
    assert fixture["terminal_declaration"]["prefix_rows"] == 3
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
    assert terminal["formal_source_anchor"] == fixture["context"][
        "formal_source_anchor"
    ]
    assert terminal["dependency_attestation_sha256"] == fixture["context"][
        "dependency_attestation"
    ]["attestation_sha256"]
    assert terminal["environment_sha256"] == fixture["context"][
        "environment_sha256"
    ]
    assert terminal["bootstrap_completion"] == fixture[
        "bootstrap_completion"
    ]
    assert terminal["bootstrap_completion"]["terminal_intent_sha256"] == (
        fixture["terminal_intent"]["intent_sha256"]
    )

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
        _register_attempt(
            next_output,
            next_id,
            next_started,
            fixture["head"],
            prior_attempts,
            fixture["context"],
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
        _register_attempt(
            next_output,
            next_id,
            next_started,
            fixture["head"],
            mutated_prior_attempts,
            fixture["context"],
        )


def test_formal_success_prepares_intent_without_appending_terminal(tmp_path):
    root = tmp_path / "attempts"
    fixture = _completed_formal_attempt(root, append_terminal=False)

    entries, _ = gate7_runner._read_attempt_ledger(root)
    intent = fixture["terminal_intent"]
    assert [entry["event"] for entry in entries] == [
        "PROTOCOL_GENESIS",
        "START",
    ]
    assert gate7_runner._terminal_intent_is_structurally_valid(intent)
    assert intent == json.loads(
        json.dumps(intent, sort_keys=True, separators=(",", ":"))
    )
    assert intent["attempt_id"] == fixture["attempt_id"]
    assert intent["directory"] == fixture["output"].name
    assert intent["terminal_record_sha256"] == _sha256(
        fixture["manifest_path"]
    )
    assert intent["preterminal_report_sha256"] == _sha256(
        fixture["report_path"]
    )
    assert intent["target_exit_status"] == 0
    assert intent["ledger_prefix"]["event"] == "START"
    assert intent["ledger_prefix"]["prefix_rows"] == 2

    with pytest.raises(
        RuntimeError, match="producer may append only an attempt-failure"
    ):
        gate7_runner._append_formal_attempt_terminal(
            fixture["output"],
            "manifest.json",
            fixture["report"],
            terminal_intent=intent,
        )
    entries, _ = gate7_runner._read_attempt_ledger(root)
    assert entries[-1]["event"] == "START"


def test_producer_cannot_append_manifest_terminal_even_with_completion(tmp_path):
    root = tmp_path / "attempts"
    fixture = _completed_formal_attempt(root, append_terminal=False)
    malformed = dict(fixture["bootstrap_completion"])
    malformed["target_sha256"] = "9" * 64
    malformed.pop("attestation_sha256")
    malformed["attestation_sha256"] = gate7_runner._canonical_mapping_sha256(
        malformed
    )

    with pytest.raises(
        RuntimeError, match="producer may append only an attempt-failure"
    ):
        gate7_runner._append_formal_attempt_terminal(
            fixture["output"],
            "manifest.json",
            fixture["report"],
            terminal_intent=fixture["terminal_intent"],
            bootstrap_completion=malformed,
        )
    entries, _ = gate7_runner._read_attempt_ledger(root)
    assert entries[-1]["event"] == "START"


@pytest.mark.parametrize("completion_mutation", [None, "wrong_target"])
def test_ledger_reader_rejects_missing_or_malformed_bootstrap_completion(
    tmp_path, completion_mutation
):
    root = tmp_path / "attempts"
    _completed_formal_attempt(root)
    ledger = root / "attempt-ledger.jsonl"
    rows = _read_jsonl(ledger)
    terminal = rows[-1]
    if completion_mutation is None:
        terminal["bootstrap_completion"] = None
    else:
        completion = dict(terminal["bootstrap_completion"])
        completion["target_sha256"] = "9" * 64
        completion.pop("attestation_sha256")
        completion["attestation_sha256"] = (
            gate7_runner._canonical_mapping_sha256(completion)
        )
        terminal["bootstrap_completion"] = completion
    terminal["entry_sha256"] = gate7_runner._ledger_entry_sha256(terminal)
    _write_jsonl(ledger, rows)

    with pytest.raises(RuntimeError, match="bootstrap completion"):
        gate7_runner._read_attempt_ledger(root)


def test_unmatched_formal_start_blocks_later_registration(tmp_path):
    root = tmp_path / "attempts"
    first_output = root / "attempt-01"
    first_output.mkdir(parents=True)
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    _register_attempt(
        first_output,
        gate7_runner._attempt_id_for(started, head),
        started,
        head,
        [],
    )

    later_started = started + timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="unmatched START"):
        _register_attempt(
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
        _register_attempt(
            later_output,
            gate7_runner._attempt_id_for(later_started, fixture["head"]),
            later_started,
            fixture["head"],
            gate7_runner._prior_attempts(later_output),
            fixture["context"],
        )


def test_failure_terminal_is_invalid_and_has_no_preterminal_identity(tmp_path):
    root = tmp_path / "attempts"
    output = root / "attempt-01"
    output.mkdir(parents=True)
    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    head = "a" * 40
    declaration, context = _register_attempt(
        output,
        gate7_runner._attempt_id_for(started, head),
        started,
        head,
        [],
    )
    failure_path = output / "attempt-failure.json"
    failure_path.write_text(
        json.dumps(
            {
                "status": "invalid",
                "contract": {
                    "path": "docs/project/gate7-v2-remediation-contract.md",
                    **context["contract_identity"],
                },
                "formal_source_anchor": context["formal_source_anchor"],
                "source_snapshot_sha256": context["source_snapshot_sha256"],
                "dependency_attestation": context["dependency_attestation"],
                "environment_sha256": context["environment_sha256"],
                "entrypoint_attestation": context["entrypoint_attestation"],
                "power_observations": context["power_observations"],
                "retained_inputs": context["retained_inputs"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
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
    assert terminal["bootstrap_completion"] is None
    assert terminal["formal_source_anchor"] == context["formal_source_anchor"]
    assert terminal["dependency_attestation_sha256"] == context[
        "dependency_attestation"
    ]["attestation_sha256"]
    assert terminal["environment_sha256"] == context["environment_sha256"]


def test_invalid_whole_matrix_retry_continues_one_genesis_chain(
    tmp_path, monkeypatch
):
    root = tmp_path / "attempts"
    monkeypatch.setattr(gate7_runner, "FORMAL_ATTEMPT_ROOT", root)
    head = "a" * 40
    context = _formal_context(head)

    def retain_invalid(directory, started, prior_attempts):
        output = root / directory
        output.mkdir(parents=True)
        attempt_id = gate7_runner._attempt_id_for(started, head)
        declaration, _ = _register_attempt(
            output, attempt_id, started, head, prior_attempts, context
        )
        failure = {
            "schema_version": gate7_runner.SCHEMA_VERSION,
            "experiment_id": gate7_runner.EXPERIMENT_ID,
            "kind": "prospective_gate7_failed_attempt",
            "status": "invalid",
            "attempt_id": attempt_id,
            "started_at_utc": started.isoformat(),
            "config": {"mode": "full"},
            "prior_attempts": prior_attempts,
            "attempt_ledger": declaration,
            "contract": {
                "path": gate7_runner._project_path(gate7_runner.DEFAULT_CONTRACT),
                **context["contract_identity"],
            },
            "git": {"head_commit": head},
            "source_snapshot_sha256": context["source_snapshot_sha256"],
            "formal_source_anchor": context["formal_source_anchor"],
            "dependency_attestation": context["dependency_attestation"],
            "environment_sha256": context["environment_sha256"],
            "entrypoint_attestation": context["entrypoint_attestation"],
            "power_observations": context["power_observations"],
            "retained_inputs": context["retained_inputs"],
            "attempt_policy": {
                "formal_root": gate7_runner._project_path(root),
                "directory": directory,
                "eligibility": (
                    "first structurally valid complete attempt in the retained "
                    "predecessor chain"
                ),
                "rerun_scope": (
                    "complete five-seed, three-policy matrix under a new attempt ID"
                ),
            },
        }
        (output / "attempt-failure.json").write_text(
            json.dumps(failure, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        gate7_runner._append_formal_attempt_terminal(
            output, "attempt-failure.json"
        )
        return output

    started = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
    retain_invalid("attempt-01", started, [])
    second_output = root / "attempt-02"
    prior_attempts = gate7_runner._prior_attempts(second_output)
    retain_invalid(
        "attempt-02", started + timedelta(seconds=1), prior_attempts
    )

    entries, _ = gate7_runner._read_attempt_ledger(root)
    assert [entry["event"] for entry in entries] == [
        "PROTOCOL_GENESIS",
        "START",
        "TERMINAL",
        "START",
        "TERMINAL",
    ]
    assert [entry["sequence"] for entry in entries] == [1, 2, 3, 4, 5]


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


def test_empty_unregistered_formal_sibling_is_unresolved(tmp_path):
    root = tmp_path / "attempts"
    (root / "attempt-empty").mkdir(parents=True)

    attempts = gate7_runner._prior_attempts(root / "attempt-next")

    assert len(attempts) == 1
    assert attempts[0]["directory"] == "attempt-empty"
    assert attempts[0]["status"] == "unresolved"


def test_rehashed_ledger_parent_directory_escape_is_rejected(tmp_path):
    root = tmp_path / "attempts"
    _completed_formal_attempt(root)
    ledger = root / "attempt-ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    previous = None
    for sequence, row in enumerate(rows, start=1):
        if row["event"] in ("START", "TERMINAL"):
            row["directory"] = ".."
        row["sequence"] = sequence
        row["previous_entry_sha256"] = previous
        row["entry_sha256"] = gate7_runner._ledger_entry_sha256(row)
        previous = row["entry_sha256"]
    _write_jsonl(ledger, rows)

    with pytest.raises(RuntimeError, match="ledger identity"):
        gate7_runner._read_attempt_ledger(root)


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


def test_bootstrapped_smoke_child_command_uses_python_s_p_launcher(
    tmp_path, monkeypatch
):
    prepared = _prepared(tmp_path)
    output = tmp_path / "bootstrap-command"
    config = Gate7Config(
        mode="smoke",
        requests=20,
        capacity=5,
        warmup_requests=2,
        resource_interval_ms=10,
        fake_embedding=True,
    )
    monkeypatch.setattr(
        sys, "_gate7_v2_bootstrap_attestation", object(), raising=False
    )
    monkeypatch.setenv("CARMA_GATE7_LAUNCH_MODE", "smoke")
    observed = {}

    def inspect_command(command, *_args, **_kwargs):
        observed["command"] = list(command)
        raise gate7_runner.ChildExecutionError(
            "stop after command inspection", 7, "", "", []
        )

    monkeypatch.setattr(
        gate7_runner, "_run_child_with_sampling", inspect_command
    )
    with pytest.raises(gate7_runner.ChildExecutionError):
        run_bundle(
            prepared,
            output,
            DEFAULT_CONTRACT,
            seeds=(17,),
            policies=("LRU",),
            config=config,
        )

    assert observed["command"][:8] == [
        sys.executable,
        "-S",
        "-P",
        str(gate7_runner.ISOLATED_BOOTSTRAP),
        "--role",
        "child",
        "--",
        str(Path(gate7_runner.__file__).resolve()),
    ]


def test_success_intent_follows_auditor_and_live_runtime_recheck():
    source = inspect.getsource(gate7_runner.run_bundle)
    assert source.index("_run_preterminal_audit") < source.index(
        "terminal_dependency = _dependency_attestation()"
    )
    assert source.index("terminal_dependency = _dependency_attestation()") < (
        source.index('_bootstrap_runtime_unchanged("parent")')
    )
    assert source.index('_bootstrap_runtime_unchanged("parent")') < source.index(
        "_prepare_formal_terminal_intent"
    )
    assert source.index("_prepare_formal_terminal_intent") < source.index(
        "sys._gate7_v2_terminal_intent"
    )
    successful_path = source.split("except BaseException as error:", 1)[0]
    assert "_append_formal_attempt_terminal" not in successful_path


def test_non_full_output_is_rejected_under_formal_root(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    formal_root = tmp_path / "formal-attempts"
    monkeypatch.setattr(gate7_runner, "FORMAL_ATTEMPT_ROOT", formal_root)
    config = Gate7Config(
        mode="smoke", requests=20, capacity=5, fake_embedding=True
    )
    with pytest.raises(RuntimeError, match="non-full Gate 7 runs"):
        run_bundle(
            prepared,
            formal_root / "attempt-forbidden",
            DEFAULT_CONTRACT,
            seeds=(17,),
            policies=("LRU",),
            config=config,
        )


def test_wrapper_uses_env_i_and_rejects_ambiguous_or_reserved_arguments():
    wrapper = gate7_runner.FORMAL_WRAPPER
    text = wrapper.read_text(encoding="utf-8")
    assert "env -i" in text
    assert '"$python_bin" -S -P "$bootstrap_py"' in text
    assert "--child-*" in text
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    cases = (
        [],
        ["full", "unexpected-output"],
        ["smoke", "--child-policy", "LRU"],
        ["smoke", "--mode", "full"],
    )
    for arguments in cases:
        completed = subprocess.run(
            ["bash", str(wrapper), *arguments],
            cwd=str(gate7_runner.PROJECT_ROOT),
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode == 2, (arguments, completed.stderr)


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
    for required in (
        "runs.csv",
        "requests.jsonl",
        "resources.jsonl",
        "outcome-latency.jsonl",
    ):
        assert manifest["artifacts"][required]["path"] == required
    assert set(manifest["semantic_index_artifacts"]) == {
        "semantic-indexes/seed-17.json"
    }
    assert manifest["gate2_v2"] == {
        **gate7_runner._gate2_v2_evidence(),
    }
    assert manifest["gate7_operating_point"]["frozen_similarity_threshold"] == 0.97
    assert manifest["gate7_operating_point"]["derived_from_gate2_v2"] is False
    assert manifest["gate7_systems_status_inputs"]["matrix_complete"] is True

    with (output / "runs.csv").open(newline="", encoding="utf-8") as source:
        runs = list(csv.DictReader(source))
    requests = _read_jsonl(output / "requests.jsonl")
    resources = _read_jsonl(output / "resources.jsonl")
    outcome_latency = _read_jsonl(output / "outcome-latency.jsonl")

    assert {row["policy"] for row in runs} == set(POLICIES)
    assert len({row["child_pid"] for row in runs}) == 3
    assert all(int(row["requests"]) == 20 for row in runs)
    assert all(int(row["labeled_negative_hits"]) == 0 for row in runs)
    assert all(row["structural_valid"] == "True" for row in runs)
    assert all(int(row["max_cache_size"]) <= 5 for row in runs)
    assert all(float(row["throughput_qps"]) > 0 for row in runs)

    assert len(requests) == 60
    assert all("text" not in row for row in requests)
    assert all(row["exclusive_reconciles"] for row in requests)
    assert all(row["request_total_ns"] > 0 for row in requests)
    assert all(row["embedding_ns"] > 0 for row in requests)
    assert all(
        row["text_preprocess_tokenize_ns"]
        == row["preprocess_ns"] + row["tokenize_ns"]
        for row in requests
    )
    assert all(
        row["embedding_ns"]
        == row["text_preprocess_tokenize_ns"]
        + row["onnx_inference_ns"]
        + row["embedding_postprocess_ns"]
        for row in requests
    )
    assert all(
        row["faiss_ns"]
        == row["faiss_search_ns"] + row["faiss_mutation_ns"]
        for row in requests
    )
    assert all(
        row["sqlite_ns"]
        == row["sqlite_read_ns"] + row["sqlite_write_ns"]
        for row in requests
    )
    assert all(
        row["similarity_decision_ns"]
        == row["similarity_evaluation_ns"]
        for row in requests
    )
    assert all(
        row["response_return_ns"]
        == row["response_materialization_ns"]
        + row["response_propagation_ns"]
        for row in requests
    )
    assert all(
        row["cache_management_ns"]
        == row["policy_exclusive_ns"]
        + row["sqlite_write_ns"]
        + row["faiss_mutation_ns"]
        for row in requests
    )
    assert all(
        row["post_embedding_ns"] == row["post_embedding_total_ns"]
        == row["request_total_ns"] - row["embedding_ns"]
        for row in requests
    )
    assert all(
        row["end_to_end_ns"] == row["request_total_ns"]
        for row in requests
    )
    assert all(
        row["request_total_ns"]
        == sum(
            row[field]
            for field in (
                "preprocess_ns",
                "tokenize_ns",
                "onnx_inference_ns",
                "embedding_postprocess_ns",
                "faiss_search_ns",
                "faiss_mutation_ns",
                "sqlite_read_ns",
                "sqlite_write_ns",
                "similarity_evaluation_ns",
                "policy_exclusive_ns",
                "response_return_ns",
                "residual_ns",
            )
        )
        for row in requests
    )
    assert all(row["cache_size_after"] <= 5 for row in requests)
    assert {row["policy"] for row in requests} == set(POLICIES)
    assert {row["hit_class"] for row in requests}.issubset(
        {"miss", "same_concept"}
    )
    assert all(row["structural_failure"] is False for row in requests)
    assert all(
        row["cached_answer_raw_identity"] is None
        or row["cached_answer_sha256"]
        == hashlib.sha256(
            row["cached_answer_raw_identity"].encode("utf-8")
        ).hexdigest()
        for row in requests
    )

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
