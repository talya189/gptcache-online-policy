import ast
import csv
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from benchmarks.carma import gate7_v4_audit as gate7_audit
from benchmarks.carma import gate7_v4_onnx_integration_benchmark as gate7_runner


ORDER = ("CARMA", "LFU", "LRU")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_v4_trace_identities_match_producer_and_contract():
    expected = {
        seed: (
            gate7_runner.PINNED_FULL_TRACE_SHA256[seed],
            gate7_runner.PINNED_FULL_SEMANTIC_INDEX_SHA256[seed],
        )
        for seed in gate7_runner.FULL_SEEDS
    }
    contract = gate7_runner.DEFAULT_CONTRACT.read_text(encoding="utf-8")

    assert gate7_audit.FROZEN_TRACE_SEMANTIC_HASHES == expected
    for trace_sha256, semantic_sha256 in expected.values():
        assert trace_sha256 in contract
        assert semantic_sha256 in contract


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _nearest(values, quantile):
    return gate7_audit.nearest_rank(values, quantile)


def _add_latency_summary(summary, request_rows):
    aliases = gate7_audit.SUMMARY_STAGE_FIELDS
    for stage, names in aliases.items():
        values = []
        for row in request_rows:
            for name in names:
                if name in row:
                    values.append(row[name])
                    break
        summary[stage + "_mean_us"] = round(sum(values) / len(values) / 1000.0, 6)
        summary[stage + "_p50_us"] = round(_nearest(values, 0.50) / 1000.0, 6)
        summary[stage + "_p95_us"] = round(_nearest(values, 0.95) / 1000.0, 6)
        summary[stage + "_p99_us"] = round(_nearest(values, 0.99) / 1000.0, 6)


def _outcome_latency_rows(request_rows, run_rows):
    requests_by_run = {}
    for row in request_rows:
        requests_by_run.setdefault(row["run_id"], []).append(row)

    records = []
    for run in run_rows:
        by_outcome = {name: [] for name in gate7_audit.OUTCOME_NAMES}
        for row in requests_by_run[run["run_id"]]:
            outcome = gate7_audit._request_outcome(row)
            assert outcome is not None
            by_outcome[outcome].append(row)
        all_rows = requests_by_run[run["run_id"]]
        for outcome in gate7_audit.OUTCOME_REPORT_NAMES:
            rows = all_rows if outcome == "all" else by_outcome[outcome]
            latency = {}
            for stage, field in gate7_audit.OUTCOME_LATENCY_FIELDS.items():
                values = [int(row[field]) for row in rows]
                latency[stage] = (
                    {
                        "mean_ns": sum(values) / len(values),
                        "p50_ns": _nearest(values, 0.50),
                        "p95_ns": _nearest(values, 0.95),
                        "p99_ns": _nearest(values, 0.99),
                    }
                    if values
                    else {
                        "mean_ns": None,
                        "p50_ns": None,
                        "p95_ns": None,
                        "p99_ns": None,
                    }
                )
            records.append(
                {
                    "schema_version": gate7_audit.SCHEMA_VERSION,
                    "experiment_id": gate7_audit.EXPERIMENT_ID,
                    "attempt_id": run["attempt_id"],
                    "run_id": run["run_id"],
                    "seed": int(run["seed"]),
                    "policy": run["policy"],
                    "outcome": outcome,
                    "samples": len(rows),
                    "latency": latency,
                }
            )
    return records


def _rewrite_runs(path, rows, fieldnames=None):
    if fieldnames is None:
        fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _refresh_artifact(manifest, name, path, rows):
    manifest["artifacts"][name] = {
        "path": name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "rows": rows,
    }


def _attempts_sha256(attempts):
    payload = json.dumps(
        attempts, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _synthetic_formal_source_anchor():
    return {
        "schema_version": gate7_audit.FORMAL_SOURCE_ANCHOR_SCHEMA_VERSION,
        "tag_name": gate7_audit.FORMAL_SOURCE_TAG,
        "remote_name": gate7_audit.FORMAL_SOURCE_REMOTE,
        "object_format": "sha1",
        "tag_object_type": "tag",
        "tag_object_id": "b" * 40,
        "peeled_commit": "a" * 40,
        "head_commit": "a" * 40,
        "tree_id": "c" * 40,
        "tag_payload_sha256": "d" * 64,
        "tag_payload_bytes": 1,
        "annotation": {
            "experiment_id": gate7_audit.EXPERIMENT_ID,
            "contract_sha256": gate7_audit.PINNED_CONTRACT_SHA256,
        },
        "remote": {
            "fetch_urls": [gate7_audit.FORMAL_SOURCE_REMOTE_URL],
            "push_urls": [gate7_audit.FORMAL_SOURCE_REMOTE_URL],
            "tag_object_id": "b" * 40,
            "peeled_commit": "a" * 40,
        },
        "retained_tag_payload_path": gate7_audit.FORMAL_SOURCE_TAG_PAYLOAD_PATH,
    }


def _synthetic_preimport_source(anchor=None, contract_bytes=1):
    source_anchor = anchor or _synthetic_formal_source_anchor()
    return {
        "schema_version": gate7_audit.PREIMPORT_SOURCE_SCHEMA_VERSION,
        "enforced": True,
        "launch_mode": "full",
        "git_executable": {
            "path": "/usr/bin/git",
            "sha256": "6" * 64,
            "bytes": 100,
            "version": "git version 2.39.5 (Apple Git-154)",
        },
        "git_status_clean": True,
        "anchor": {
            "object_format": source_anchor["object_format"],
            "head_commit": source_anchor["head_commit"],
            "head_tree": source_anchor["tree_id"],
            "tag_name": source_anchor["tag_name"],
            "tag_object_type": source_anchor["tag_object_type"],
            "tag_object_id": source_anchor["tag_object_id"],
            "peeled_commit": source_anchor["peeled_commit"],
            "tag_tree": source_anchor["tree_id"],
            "tag_payload_sha256": source_anchor["tag_payload_sha256"],
            "tag_payload_bytes": source_anchor["tag_payload_bytes"],
            "annotation": dict(source_anchor["annotation"]),
            "remote": {
                "remote_name": gate7_audit.FORMAL_SOURCE_REMOTE,
                "fetch_url": gate7_audit.FORMAL_SOURCE_REMOTE_URL,
                "fetch_url_count": 1,
                "push_url": gate7_audit.FORMAL_SOURCE_REMOTE_URL,
                "push_url_count": 1,
                "tag_ref": "refs/tags/%s" % gate7_audit.FORMAL_SOURCE_TAG,
                "tag_object_id": source_anchor["tag_object_id"],
                "peeled_ref": "refs/tags/%s^{}"
                % gate7_audit.FORMAL_SOURCE_TAG,
                "peeled_commit": source_anchor["peeled_commit"],
            },
            "contract_path": gate7_audit.DEFAULT_CONTRACT_PATH,
            "contract_sha256": gate7_audit.PINNED_CONTRACT_SHA256,
            "contract_bytes": contract_bytes,
        },
        "inventory": {
            "runtime_roots": ["gptcache", "benchmarks"],
            "tracked_file_count": 20,
            "tracked_file_bytes": 1000,
            "tracked_file_map_sha256": "7" * 64,
            "runtime_file_count": 15,
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
                "import_file_count": 25,
                "untracked_or_ignored_import_file_count": 0,
                "symlink_count": 0,
            },
        },
    }


def _synthetic_wrapper_shell_startup(environment, project_root="/fixture/project"):
    value = {
        "schema_version": gate7_audit.WRAPPER_SHELL_STARTUP_SCHEMA_VERSION,
        "profile": "env-i-v1",
        "marker": gate7_audit.FORMAL_WRAPPER_SHELL_MARKER,
        "launch_mode": "full",
        "outer_env_i_operator_root_required": True,
        "home": environment["HOME"],
        "pwd": project_root,
        "tmpdir": "/tmp",
        "shlvl": "1",
        "optional_cf_user_text_encoding": environment[
            "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF"
        ],
        "fixed_environment": {
            key: gate7_audit.FORMAL_REQUIRED_ENVIRONMENT[key]
            for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
        },
    }
    value["attestation_sha256"] = gate7_audit._canonical_mapping_sha256(value)
    return value


def _synthetic_entrypoint_attestation():
    producer_path = "benchmarks/carma/gate7_v4_onnx_integration_benchmark.py"
    bootstrap = {
        "project_root": "/fixture",
        "target_sha256": hashlib.sha256(producer_path.encode("utf-8")).hexdigest(),
        "preimport_source": {"fixture": "source"},
        "dependency": {"fixture": "dependency"},
        "python": {"environment": {"FIXTURE": "1"}},
    }
    bootstrap["attestation_sha256"] = gate7_audit._canonical_mapping_sha256(
        bootstrap
    )
    return {
        "schema_version": gate7_audit.FORMAL_ENTRYPOINT_SCHEMA_VERSION,
        "marker": gate7_audit.FORMAL_ENTRYPOINT_MARKER,
        "mode": "full",
        "wrapper_pid": 123,
        "producer_parent_pid": 123,
        "wrapper_path": gate7_audit.FORMAL_WRAPPER_PATH,
        "wrapper_identity": {"sha256": "4" * 64, "bytes": 1},
        "parent_executable": str(Path("/bin/bash").resolve()),
        "parent_cmdline": [
            "/bin/bash",
            "/fixture/" + gate7_audit.FORMAL_WRAPPER_PATH,
            "full",
        ],
        "bootstrap_attestation": bootstrap,
        "bootstrap_attestation_sha256": bootstrap["attestation_sha256"],
    }


def _synthetic_pre_start_power():
    return {
        "observed_at_utc": "2026-08-27T09:59:59+00:00",
        "available": True,
        "plugged": True,
        "percent": 100.0,
        "seconds_left": -2,
    }


def _append_attempt_ledger(
    root, attempt_id, started_at_utc, directory, prior_attempts
):
    path = root / "attempt-ledger.jsonl"
    entries = (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if path.is_file()
        else []
    )
    if not entries:
        genesis = {
            "schema_version": gate7_audit.ATTEMPT_LEDGER_SCHEMA_VERSION,
            "experiment_id": gate7_audit.EXPERIMENT_ID,
            "sequence": 1,
            "event": "PROTOCOL_GENESIS",
            "recorded_at_utc": "2026-08-27T09:59:00+00:00",
            "v1_preservation": {
                "manifest_path": gate7_audit.V1_PRESERVATION_MANIFEST_PATH,
                "manifest_sha256": gate7_audit.PINNED_V1_PRESERVATION_MANIFEST_SHA256,
                "archive_path": gate7_audit.V1_PRESERVATION_ARCHIVE_PATH,
                "archive_sha256": gate7_audit.PINNED_V1_PRESERVATION_ARCHIVE_SHA256,
                "formal_root": gate7_audit.PINNED_V1_ROOT,
                "attempt_ledger_sha256": gate7_audit.PINNED_V1_LEDGER_SHA256,
                "terminal_attempt_id": gate7_audit.PINNED_V1_ATTEMPT_ID,
                "terminal_entry_sha256": gate7_audit.PINNED_V1_TERMINAL_ENTRY_SHA256,
                "failure_sha256": gate7_audit.PINNED_V1_FAILURE_SHA256,
            },
            "v2_preservation": gate7_audit._v2_preservation_identity(),
            "v3_preservation": gate7_audit._v3_preservation_identity(),
            "v4_contract": {
                "path": gate7_audit.DEFAULT_CONTRACT_PATH,
                "sha256": gate7_audit.PINNED_CONTRACT_SHA256,
                "bytes": (
                    Path(__file__).resolve().parents[2]
                    / gate7_audit.DEFAULT_CONTRACT_PATH
                ).stat().st_size,
            },
            "formal_source_anchor": _synthetic_formal_source_anchor(),
            "previous_entry_sha256": None,
        }
        genesis["entry_sha256"] = hashlib.sha256(
            json.dumps(
                genesis, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        entries.append(genesis)
    entry = {
        "schema_version": gate7_audit.ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": gate7_audit.EXPERIMENT_ID,
        "sequence": len(entries) + 1,
        "event": "START",
        "attempt_id": attempt_id,
        "started_at_utc": started_at_utc,
        "head_commit": "a" * 40,
        "directory": directory,
        "prior_attempt_count": len(prior_attempts),
        "prior_attempts_sha256": _attempts_sha256(prior_attempts),
        "contract": {
            "path": gate7_audit.DEFAULT_CONTRACT_PATH,
            "sha256": gate7_audit.PINNED_CONTRACT_SHA256,
            "bytes": (
                Path(__file__).resolve().parents[2]
                / gate7_audit.DEFAULT_CONTRACT_PATH
            ).stat().st_size,
        },
        "source_snapshot_sha256": "1" * 64,
        "formal_source_anchor": _synthetic_formal_source_anchor(),
        "dependency_attestation_sha256": "2" * 64,
        "environment_sha256": "3" * 64,
        "entrypoint_attestation": _synthetic_entrypoint_attestation(),
        "pre_start_power": _synthetic_pre_start_power(),
        "retained_inputs": {},
        "previous_entry_sha256": (
            entries[-1]["entry_sha256"] if entries else None
        ),
    }
    entry["entry_sha256"] = hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    entries.append(entry)
    _write_jsonl(path, entries)
    return {
        "path": "attempt-ledger.jsonl",
        "prefix_rows": len(entries),
        "prefix_sha256": _sha256(path),
        "prefix_bytes": path.stat().st_size,
        "entry_sha256": entry["entry_sha256"],
        "event": "START",
    }


def _append_attempt_terminal(root, bundle, preterminal_report):
    path = root / "attempt-ledger.jsonl"
    entries = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    start = entries[-1]
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report_path = bundle / gate7_audit.PRETERMINAL_REPORT_NAME
    entry = {
        "schema_version": gate7_audit.ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": gate7_audit.EXPERIMENT_ID,
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
        "preterminal_report": gate7_audit.PRETERMINAL_REPORT_NAME,
        "preterminal_report_sha256": _sha256(report_path),
        "preterminal_report_bytes": report_path.stat().st_size,
        "preterminal_report_status": preterminal_report["status"],
        "preterminal_report_claimable": preterminal_report["claimable"],
        "auditor_identity": preterminal_report["auditor_identity"],
        "bootstrap_completion": None,
        "previous_entry_sha256": start["entry_sha256"],
    }
    intent = gate7_audit._terminal_intent_from_retained_evidence(
        start, entry, manifest
    )
    parent_bootstrap = start["entrypoint_attestation"]["bootstrap_attestation"]
    completion = {
        "schema_version": gate7_audit.BOOTSTRAP_COMPLETION_SCHEMA_VERSION,
        "phase": "post_target",
        "role": "parent",
        "launch_mode": "full",
        "bootstrap_attestation_sha256": parent_bootstrap[
            "attestation_sha256"
        ],
        "target_sha256": parent_bootstrap["target_sha256"],
        "preimport_source_sha256": gate7_audit._canonical_mapping_sha256(
            parent_bootstrap["preimport_source"]
        ),
        "dependency_sha256": gate7_audit._canonical_mapping_sha256(
            parent_bootstrap["dependency"]
        ),
        "python_environment_sha256": gate7_audit._canonical_mapping_sha256(
            parent_bootstrap["python"]["environment"]
        ),
        "target_exit_status": gate7_audit.ADJUDICATION_EXIT_STATUSES[
            preterminal_report["status"]
        ],
        "terminal_intent_sha256": intent["intent_sha256"],
        "checks_passed": True,
    }
    completion["attestation_sha256"] = gate7_audit._canonical_mapping_sha256(
        completion
    )
    entry["bootstrap_completion"] = completion
    entry["entry_sha256"] = hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    entries.append(entry)
    _write_jsonl(path, entries)
    return entry


def _rewrite_last_ledger_entry(root, mutate):
    path = root / "attempt-ledger.jsonl"
    entries = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    terminal = entries[-1]
    assert terminal["event"] == "TERMINAL"
    mutate(terminal)
    terminal.pop("entry_sha256", None)
    terminal["entry_sha256"] = hashlib.sha256(
        json.dumps(terminal, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    _write_jsonl(path, entries)


def _replace_manifest_with_failure_terminal(root, bundle, completion=None):
    path = root / "attempt-ledger.jsonl"
    entries = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    start = entries[-1]
    manifest_path = bundle / "manifest.json"
    failure = json.loads(manifest_path.read_text(encoding="utf-8"))
    failure["kind"] = "prospective_gate7_failed_attempt"
    failure["status"] = "invalid"
    failure_path = bundle / "attempt-failure.json"
    failure_path.write_text(
        json.dumps(failure, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path.unlink()
    terminal = {
        "schema_version": gate7_audit.ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": gate7_audit.EXPERIMENT_ID,
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
        "terminal_record": "attempt-failure.json",
        "terminal_record_sha256": _sha256(failure_path),
        "terminal_record_bytes": failure_path.stat().st_size,
        "preterminal_report": None,
        "preterminal_report_sha256": None,
        "preterminal_report_bytes": None,
        "preterminal_report_status": "invalid",
        "preterminal_report_claimable": False,
        "auditor_identity": None,
        "bootstrap_completion": completion,
        "previous_entry_sha256": start["entry_sha256"],
    }
    terminal["entry_sha256"] = hashlib.sha256(
        json.dumps(terminal, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    entries.append(terminal)
    _write_jsonl(path, entries)
    return failure


def _trace_rows():
    hot = [("hot-%d" % value, "hot-text-%d" % value) for value in range(4)]
    scan = [("scan-%d" % value, "scan-text-%d" % value) for value in range(8)]
    phase_specs = (("warm", hot, 6), ("scan", scan, 8), ("return", hot, 6))
    rows = []
    seen = set()
    for phase, concepts, count in phase_specs:
        occurrences = {}
        for offset in range(count):
            concept_id, text_id = concepts[offset % len(concepts)]
            occurrence = occurrences.get(concept_id, 0)
            occurrences[concept_id] = occurrence + 1
            index = len(rows)
            rows.append(
                {
                    "index": index,
                    "request_id": "gate7b-1-%04d" % index,
                    "phase": phase,
                    "occurrence": occurrence,
                    "reuse_opportunity": concept_id in seen,
                    "text_id": text_id,
                    "text": "text for " + concept_id,
                    "concept_id": concept_id,
                    "response_id": concept_id,
                    "response_payload": "recorded-response-v2:%s:%s"
                    % (concept_id, text_id),
                }
            )
            seen.add(concept_id)
    return rows


def _semantic_artifact(trace_hash):
    trace = _trace_rows()
    selected = sorted({row["concept_id"] for row in trace})
    members = sorted(
        {(row["text_id"], row["concept_id"]) for row in trace}
    )
    canonical = {
        concept: min(text for text, observed in members if observed == concept)
        for concept in selected
    }
    canonical_rows = [
        {
            "schema_version": gate7_audit.SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "selected_concept",
            "concept_id": concept,
            "canonical_text_id": canonical[concept],
        }
        for concept in selected
    ] + [
        {
            "schema_version": gate7_audit.SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "component_member",
            "text_id": text_id,
            "concept_id": concept,
        }
        for text_id, concept in members
    ]
    canonical_lines = sorted(
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in canonical_rows
    )
    digest = hashlib.sha256()
    for line in canonical_lines:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    distinct = len(selected) * (len(selected) - 1) // 2
    return {
        "schema_version": gate7_audit.SEMANTIC_ARTIFACT_SCHEMA_VERSION,
        "semantic_index_schema_version": gate7_audit.SEMANTIC_INDEX_SCHEMA_VERSION,
        "seed": 1,
        "trace_sha256": trace_hash,
        "semantic_index_sha256": digest.hexdigest(),
        "canonical_row_count": len(canonical_lines),
        "counts": {
            "selected_concepts": len(selected),
            "selected_texts": len(members),
            "distinct_candidate_pairs": distinct,
            "direct_negative_pairs": 0,
            "direct_negative_text_pairs": 0,
            "component_negative_pairs": 0,
            "negative_component_derived_pairs": 0,
            "unlabeled_cross_component_pairs": distinct,
        },
        "source_pairs_sha256": gate7_audit.FROZEN_SOURCE_HASHES[
            "source_pairs_sha256"
        ],
        "source_texts_sha256": gate7_audit.FROZEN_SOURCE_HASHES[
            "source_texts_sha256"
        ],
        "source_manifest_sha256": "f" * 64,
        "selected_concepts": [
            {"concept_id": concept, "canonical_text_id": canonical[concept]}
            for concept in selected
        ],
        "component_members": [
            {"text_id": text_id, "concept_id": concept}
            for text_id, concept in members
        ],
        "direct_negative_pairs": [],
        "component_negative_pairs": [],
    }


def _request_rows(policy, run_id, trace_hash, total_ns, attempt_id):
    rows = []
    for trace in _trace_rows():
        text_preprocess = 100
        inference = 500
        embedding_postprocess = 50
        faiss_search = 10
        sqlite_read = 10
        similarity = 5
        policy_exclusive = 5
        sqlite_write = 5
        faiss_mutation = 5
        response_return = 5
        assigned = sum(
            (
                text_preprocess,
                inference,
                embedding_postprocess,
                faiss_search,
                sqlite_read,
                similarity,
                policy_exclusive,
                sqlite_write,
                faiss_mutation,
                response_return,
            )
        )
        residual = total_ns - assigned
        embedding = text_preprocess + inference + embedding_postprocess
        before = min(trace["index"], 5)
        after = min(trace["index"] + 1, 5)
        rows.append(
            {
                "schema_version": gate7_audit.SCHEMA_VERSION,
                "experiment_id": gate7_audit.EXPERIMENT_ID,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "seed": 1,
                "policy": policy,
                "order_position": ORDER.index(policy),
                "trace_hash": trace_hash,
                "request_index": trace["index"],
                "request_id": trace["request_id"],
                "phase": trace["phase"],
                "occurrence": trace["occurrence"],
                "reuse_opportunity": trace["reuse_opportunity"],
                "text_id": trace["text_id"],
                "text_sha256": hashlib.sha256(
                    trace["text"].encode("utf-8")
                ).hexdigest(),
                "concept_id": trace["concept_id"],
                "expected_response_id": trace["response_id"],
                "returned_response_id": trace["response_id"],
                "returned_concept_id": trace["concept_id"],
                "top_candidate_id": None,
                "top_candidate_text_id": None,
                "candidate_cache_data_seen": False,
                "candidate_cache_data_present": False,
                "candidate_cache_lookup_count": 0,
                "cached_question_text_sha256": None,
                "cached_source_text_id": None,
                "cached_source_concept_id": None,
                "cached_answer_sha256": None,
                "cached_answer_raw_identity": None,
                "cached_answer_source_text_id": None,
                "cached_answer_source_concept_id": None,
                "payload_source_text_id": trace["text_id"],
                "payload_source_concept_id": trace["concept_id"],
                "provenance_resolved": True,
                "provenance_consistent": True,
                "semantic_relation": "not_applicable",
                "semantic_status": "not_applicable",
                "semantic_label": None,
                "semantic_evidence_kind": "none",
                "semantic_source_indices": [],
                "hit_class": "miss",
                "response_id_mismatch": False,
                "source_response_mismatch": False,
                "unrecognized_response_id": False,
                "stale_candidate": False,
                "capacity_exceeded": False,
                "structural_failure": False,
                "structural_failure_reasons": "",
                "raw_hit": False,
                "similarity": 0.0,
                "policy_action": "admit_or_replace",
                "admitted": True,
                "rejected": False,
                "evicted": before == 5,
                "cache_size_before": before,
                "cache_size_after": after,
                "preprocess_ns": 40,
                "tokenize_ns": 60,
                "text_preprocess_tokenize_ns": text_preprocess,
                "onnx_inference_ns": inference,
                "embedding_postprocess_ns": embedding_postprocess,
                "embedding_elapsed_ns": embedding,
                "faiss_search_ns": faiss_search,
                "sqlite_read_ns": sqlite_read,
                "similarity_evaluation_ns": similarity,
                "similarity_decision_ns": similarity,
                "policy_exclusive_ns": policy_exclusive,
                "sqlite_write_ns": sqlite_write,
                "faiss_mutation_ns": faiss_mutation,
                "response_materialization_ns": 3,
                "response_propagation_ns": 2,
                "response_return_ns": response_return,
                "residual_ns": residual,
                "request_total_ns": total_ns,
                "end_to_end_ns": total_ns,
                "embedding_ns": embedding,
                "post_embedding_elapsed_ns": total_ns - embedding,
                "post_embedding_total_ns": total_ns - embedding,
                "post_embedding_ns": total_ns - embedding,
                "cache_management_ns": policy_exclusive + sqlite_write + faiss_mutation,
                "faiss_ns": faiss_search + faiss_mutation,
                "sqlite_ns": sqlite_read + sqlite_write,
                "policy_inclusive_ns": policy_exclusive,
                "exclusive_reconciles": True,
            }
        )
    return rows


def _make_bundle(
    tmp_path,
    monkeypatch,
    carma_total_ns=1_100_000,
    attempt_id="20260827T100000000000Z-aaaaaaaaaaaa",
    bundle_name="bundle",
    started_at_utc="2026-08-27T10:00:00+00:00",
    prior_attempts=None,
    finalize=True,
):
    monkeypatch.setattr(gate7_audit, "FULL_SEEDS", (1,))
    monkeypatch.setattr(gate7_audit, "EXPECTED_POLICY_ORDERS", {1: ORDER})
    frozen_config = dict(gate7_audit.FROZEN_CONFIG)
    frozen_config.update({"requests": 20, "capacity": 5})
    monkeypatch.setattr(gate7_audit, "FROZEN_CONFIG", frozen_config)

    bundle = tmp_path / bundle_name
    bundle.mkdir()
    prior_attempts = list(prior_attempts or [])
    trace_path = bundle / "traces" / "seed-1.jsonl"
    _write_jsonl(trace_path, _trace_rows())
    trace_hash = _sha256(trace_path)
    semantic_path = bundle / "semantic-indexes" / "seed-1.json"
    semantic_path.parent.mkdir(parents=True, exist_ok=True)
    semantic_value = _semantic_artifact(trace_hash)
    semantic_path.write_text(
        json.dumps(semantic_value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    semantic_artifact_hash = _sha256(semantic_path)
    totals = {"LRU": 1_000_000, "LFU": 1_100_000, "CARMA": carma_total_ns}
    all_requests = []
    all_resources = []
    summaries = []
    peak_by_policy = {
        "LRU": 100 * 1024 * 1024,
        "LFU": 105 * 1024 * 1024,
        "CARMA": 110 * 1024 * 1024,
    }
    for policy in ORDER:
        run_id = "run-" + policy.lower()
        requests = _request_rows(
            policy, run_id, trace_hash, totals[policy], attempt_id
        )
        all_requests.extend(requests)
        peak = peak_by_policy[policy]
        resource_rows = [
            {
                "schema_version": gate7_audit.SCHEMA_VERSION,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "child_pid": 100 + ORDER.index(policy),
                "seed": 1,
                "policy": policy,
                "sample_index": 0,
                "kind": "loop_start",
                "scope": "formal",
                "request_index": -1,
                "monotonic_ns": 950_000_000,
                "sample_started_monotonic_ns": 950_000_000,
                "sample_completed_monotonic_ns": 951_000_000,
                "sample_collection_ns": 1_000_000,
                "rss_bytes": peak - 1024 * 1024,
                "vms_bytes": peak * 2,
                "uss_bytes": peak - 2 * 1024 * 1024,
                "uss_available": True,
                "cpu_user_seconds": 1.0,
                "cpu_system_seconds": 0.5,
                "num_threads": 1,
                "system_load_1m": 0.5,
                "io_counters_available": True,
                "io_read_count": 10,
                "io_write_count": 20,
                "io_read_bytes": 100,
                "io_write_bytes": 200,
            },
            {
                "schema_version": gate7_audit.SCHEMA_VERSION,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "child_pid": 100 + ORDER.index(policy),
                "seed": 1,
                "policy": policy,
                "sample_index": 1,
                "kind": "periodic",
                "scope": "formal",
                "request_index": 9,
                "monotonic_ns": 1_050_000_000,
                "sample_started_monotonic_ns": 1_050_000_000,
                "sample_completed_monotonic_ns": 1_051_000_000,
                "sample_collection_ns": 1_000_000,
                "rss_bytes": peak,
                "vms_bytes": peak * 2,
                "uss_bytes": peak - 1024 * 1024,
                "uss_available": True,
                "cpu_user_seconds": 1.05,
                "cpu_system_seconds": 0.55,
                "num_threads": 1,
                "system_load_1m": 0.5,
                "io_counters_available": True,
                "io_read_count": 12,
                "io_write_count": 23,
                "io_read_bytes": 125,
                "io_write_bytes": 225,
            },
            {
                "schema_version": gate7_audit.SCHEMA_VERSION,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "child_pid": 100 + ORDER.index(policy),
                "seed": 1,
                "policy": policy,
                "sample_index": 2,
                "kind": "loop_end",
                "scope": "formal",
                "request_index": 19,
                "monotonic_ns": 1_150_000_000,
                "sample_started_monotonic_ns": 1_150_000_000,
                "sample_completed_monotonic_ns": 1_151_000_000,
                "sample_collection_ns": 1_000_000,
                "rss_bytes": peak,
                "vms_bytes": peak * 2,
                "uss_bytes": peak - 1024 * 1024,
                "uss_available": True,
                "cpu_user_seconds": 1.1,
                "cpu_system_seconds": 0.6,
                "num_threads": 1,
                "system_load_1m": 0.5,
                "io_counters_available": True,
                "io_read_count": 15,
                "io_write_count": 25,
                "io_read_bytes": 150,
                "io_write_bytes": 250,
            },
        ]
        all_resources.extend(resource_rows)
        service_seconds = sum(row["request_total_ns"] for row in requests) / 1e9
        loop_seconds = 0.1
        summary = {
            "schema_version": gate7_audit.SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "child_pid": 100 + ORDER.index(policy),
            "child_exit_status": 0,
            "storage_instance_sha256": "%064x" % (ORDER.index(policy) + 1),
            "mode": "full",
            "seed": 1,
            "policy": policy,
            "order_position": ORDER.index(policy),
            "trace_hash": trace_hash,
            "requests": 20,
            "capacity": 5,
            "embedding_dimension": 768,
            "embedding_norm_verified": True,
            "provider_verified": True,
            "asset_integrity_pre_verified": True,
            "asset_integrity_post_verified": True,
            "model_asset_digest_sha256": gate7_audit.FROZEN_MODEL[
                "model_digest_sha256"
            ],
            "tokenizer_asset_digest_sha256": gate7_audit.FROZEN_MODEL[
                "tokenizer_digest_sha256"
            ],
            "request_buffer_rows_reserved": 20,
            "embedding_buffer_bytes_reserved": 20 * 768 * 4,
            "hits": 0,
            "misses": 20,
            "same_concept_hits": 0,
            "direct_negative_hits": 0,
            "component_derived_negative_hits": 0,
            "unlabeled_cross_concept_hits": 0,
            "unresolved_provenance_hits": 0,
            "labeled_negative_hits": 0,
            "cross_concept_hits": 0,
            "response_id_mismatches": 0,
            "source_response_mismatches": 0,
            "unrecognized_response_ids": 0,
            "stale_candidates": 0,
            "capacity_excess_requests": 0,
            "structural_failure_requests": 0,
            "structural_failure_flags_total": 0,
            "structural_valid": True,
            "storage_failure": None,
            "semantic_guardrail_status": "NO_HITS",
            "semantic_index_sha256": semantic_value["semantic_index_sha256"],
            "semantic_index_artifact_sha256": semantic_artifact_hash,
            "max_cache_size": 5,
            "admissions": 20,
            "rejections": 0,
            "evictions": 15,
            "service_seconds": round(service_seconds, 9),
            "loop_seconds": loop_seconds,
            "loop_start_monotonic_ns": 1_000_000_000,
            "loop_end_monotonic_ns": 1_100_000_000,
            "throughput_qps": round(20 / service_seconds, 6),
            "loop_throughput_qps": round(20 / loop_seconds, 6),
            "service_throughput_qps": round(20 / service_seconds, 6),
            "cpu_user_seconds": 0.1,
            "cpu_system_seconds": 0.1,
            "cpu_total_seconds": 0.2,
            "cpu_user_lifecycle_seconds": 0.1,
            "cpu_system_lifecycle_seconds": 0.1,
            "resource_window_seconds": 0.2,
            "cpu_utilization_percent": 100.0,
            "io_read_count_delta": 5,
            "io_write_count_delta": 5,
            "io_read_bytes_delta": 50,
            "io_write_bytes_delta": 50,
            "rss_post_warmup_bytes": peak - 1024 * 1024,
            "uss_post_warmup_bytes": peak - 2 * 1024 * 1024,
            "rss_high_water_bytes": peak,
            "rss_start_bytes": peak - 1024 * 1024,
            "rss_end_bytes": peak,
            "rss_mean_bytes": round((3 * peak - 1024 * 1024) / 3, 3),
            "rss_peak_sampled_bytes": peak,
            "rss_peak_bytes": peak,
            "rss_incremental_peak_bytes": 1024 * 1024,
            "rss_end_minus_start_bytes": 1024 * 1024,
            "uss_start_bytes": peak - 2 * 1024 * 1024,
            "uss_end_bytes": peak - 1024 * 1024,
            "uss_mean_bytes": round((3 * peak - 4 * 1024 * 1024) / 3, 3),
            "uss_peak_bytes": peak - 1024 * 1024,
            "uss_incremental_peak_bytes": 1024 * 1024,
            "uss_end_minus_start_bytes": 1024 * 1024,
            "embedding_digest_sha256": "d" * 64,
            "timer_overhead_median_ns": 20,
            "timer_overhead_p95_ns": 30,
            "final_scalar_count": 5,
            "final_vector_count": 5,
            "deleted_scalar_count": 0,
            "verified_entries": 5,
        }
        _add_latency_summary(summary, requests)
        summaries.append(summary)

    runs_path = bundle / "runs.csv"
    _rewrite_runs(runs_path, summaries)
    requests_path = bundle / "requests.jsonl"
    resources_path = bundle / "resources.jsonl"
    outcomes_path = bundle / "outcome-latency.jsonl"
    _write_jsonl(requests_path, all_requests)
    _write_jsonl(resources_path, all_resources)
    outcome_rows = _outcome_latency_rows(all_requests, summaries)
    _write_jsonl(outcomes_path, outcome_rows)

    artifacts = {}
    for name, path, rows in (
        ("runs.csv", runs_path, len(summaries)),
        ("requests.jsonl", requests_path, len(all_requests)),
        ("resources.jsonl", resources_path, len(all_resources)),
        ("outcome-latency.jsonl", outcomes_path, len(outcome_rows)),
        ("traces/seed-1.jsonl", trace_path, 20),
        (
            "semantic-indexes/seed-1.json",
            semantic_path,
            gate7_audit._row_count(semantic_path),
        ),
    ):
        artifacts[name] = {
            "path": name,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        if rows is not None:
            artifacts[name]["rows"] = rows

    config = dict(frozen_config)
    config.update({"mode": "full", "fake_embedding": False})
    project_root = Path(gate7_audit.__file__).resolve().parents[2]
    gate2_artifacts = {}
    for relative in (
        gate7_audit.GATE2_V2_SELECTION_PATH,
        gate7_audit.GATE2_V2_RESULT_PATH,
        gate7_audit.GATE2_V2_THRESHOLDS_PATH,
    ):
        path = project_root / relative
        gate2_artifacts[relative] = {
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
    manifest = {
        "schema_version": gate7_audit.SCHEMA_VERSION,
        "experiment_id": gate7_audit.EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "kind": "prospective_gate7_followup",
        "post_v3_protocol_disclosure": dict(
            gate7_audit.POST_V3_PROTOCOL_DISCLOSURE
        ),
        "started_at_utc": started_at_utc,
        "completed_at_utc": "2026-08-27T10:01:00+00:00",
        "historical_gate7_preserved": True,
        "historical_artifacts_modified": False,
        "precomputed_embeddings": False,
        "embedding_in_request_path": True,
        "gptcache_adapter_path": True,
        "actual_onnx": True,
        "formal_claimable_mode": True,
        "publication_integrity": {
            field: True
            for field in gate7_audit.FORMAL_PUBLICATION_INTEGRITY_FIELDS
        },
        "prior_attempt_exists": bool(prior_attempts),
        "prior_attempts": prior_attempts,
        "attempt_policy": {
            "formal_root": gate7_audit.FORMAL_ATTEMPT_ROOT,
            "directory": bundle.name,
            "eligibility": "first structurally valid complete attempt in the retained predecessor chain",
            "rerun_scope": "complete five-seed, three-policy matrix under a new attempt ID",
        },
        "v1_lineage": {
            "manifest_path": gate7_audit.V1_PRESERVATION_MANIFEST_PATH,
            "manifest_sha256": gate7_audit.PINNED_V1_PRESERVATION_MANIFEST_SHA256,
            "archive_path": gate7_audit.V1_PRESERVATION_ARCHIVE_PATH,
            "archive_sha256": gate7_audit.PINNED_V1_PRESERVATION_ARCHIVE_SHA256,
            "formal_root": gate7_audit.PINNED_V1_ROOT,
            "attempt_ledger_sha256": gate7_audit.PINNED_V1_LEDGER_SHA256,
            "terminal_attempt_id": gate7_audit.PINNED_V1_ATTEMPT_ID,
            "terminal_entry_sha256": gate7_audit.PINNED_V1_TERMINAL_ENTRY_SHA256,
            "failure_sha256": gate7_audit.PINNED_V1_FAILURE_SHA256,
            "relationship": (
                "method-correcting successor; v1 remains invalid and is not reclassified"
            ),
        },
        "contract": {
            "path": gate7_audit.DEFAULT_CONTRACT_PATH,
            "sha256": gate7_audit.PINNED_CONTRACT_SHA256,
            "bytes": (
                Path(__file__).resolve().parents[2]
                / gate7_audit.DEFAULT_CONTRACT_PATH
            ).stat().st_size,
        },
        "source_snapshot_sha256": "1" * 64,
        "formal_source_anchor": _synthetic_formal_source_anchor(),
        "dependency_attestation": {"attestation_sha256": "2" * 64},
        "environment_sha256": "3" * 64,
        "entrypoint_attestation": _synthetic_entrypoint_attestation(),
        "power_observations": {
            "pre_start": _synthetic_pre_start_power(),
            "end": {
                "observed_at_utc": "2026-08-27T10:01:00+00:00",
                "available": True,
                "plugged": True,
                "percent": 100.0,
                "seconds_left": -2,
            },
            "availability_limitation": (
                "point observations cannot prove uninterrupted AC between observations"
            ),
        },
        "retained_inputs": {},
        "git": {
            "head_commit": "a" * 40,
            "worktree_dirty": False,
            "end_head_commit": "a" * 40,
            "end_worktree_dirty": False,
            "source_snapshot_unchanged": True,
        },
        "config": config,
        "seeds": [1],
        "policies": list(gate7_audit.POLICIES),
        "policy_order_namespace": gate7_audit.POLICY_ORDER_NAMESPACE,
        "planned_policy_orders": {"1": list(ORDER)},
        "policy_orders": {"1": list(ORDER)},
        "source_identities": {
            name: {
                "path": name,
                "sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
                "bytes": 1,
            }
            for name in gate7_audit.REQUIRED_SOURCE_IDENTITIES
        },
        "model": dict(gate7_audit.FROZEN_MODEL),
        "onnx": {
            "provider": "CPUExecutionProvider",
            "intra_op_threads": 1,
            "inter_op_threads": 1,
            "max_length": 512,
            "warmup_requests": 20,
            "model_load_and_warmup_in_request_timing": False,
            "provider_verified_each_child": True,
            "embedding_norm_verified_each_request": True,
        },
        "timing_scope": {
            "quantiles": "nearest rank: one-based ceil(q*N) over raw samples",
            "outcome_categories": list(gate7_audit.OUTCOME_NAMES),
            "outcome_report_rows": list(gate7_audit.OUTCOME_REPORT_NAMES),
        },
        "resource_measurement": {
            "maximum_valid_sample_gap_ms": 200,
            "cadence_timestamp": "sample_started_monotonic_ns",
            "deadline_schedule": "absolute_monotonic",
            "collection_timing": (
                "sample_started_monotonic_ns, sample_completed_monotonic_ns, "
                "and sample_collection_ns reconcile exactly"
            ),
            "fixed_buffers_reserved_before_start": True,
        },
        "environment": {
            "filesystem": {},
            "power": {},
            "load_average_at_manifest": 0.5,
        },
        "trace_metadata": {
            "1": {
                "trace_sha256": trace_hash,
                "seed": 1,
                "requests": 20,
                "capacity": 5,
                "phase_counts": {"warm": 6, "scan": 8, "return": 6},
                "hot_set_size": 4,
                "hot_concept_ids": ["hot-%d" % value for value in range(4)],
                "hot_text_ids": ["hot-text-%d" % value for value in range(4)],
                "scan_concept_ids": ["scan-%d" % value for value in range(8)],
                "scan_text_ids": ["scan-text-%d" % value for value in range(8)],
                "selection_split": "calibration",
                "semantic_index_schema_version": gate7_audit.SEMANTIC_INDEX_SCHEMA_VERSION,
                "semantic_index_sha256": semantic_value["semantic_index_sha256"],
                "semantic_index_canonical_row_count": semantic_value[
                    "canonical_row_count"
                ],
                "semantic_index_counts": semantic_value["counts"],
                "source_manifest_sha256": "f" * 64,
                **gate7_audit.FROZEN_SOURCE_HASHES,
            }
        },
        "semantic_index_artifacts": {
            "semantic-indexes/seed-1.json": artifacts[
                "semantic-indexes/seed-1.json"
            ]
        },
        "semantic_guardrail": {
            "status": "NO_HITS",
            "rule": (
                "fail on any direct or component-derived labeled-negative hit; "
                "pending on unlabeled cross-component hits; otherwise pass"
            ),
            "disjoint_hit_counts": {
                "same_concept_hits": 0,
                "direct_negative_hits": 0,
                "component_derived_negative_hits": 0,
                "unlabeled_cross_concept_hits": 0,
                "unresolved_provenance_hits": 0,
            },
            "labeled_negative_hits": 0,
            "does_not_change_gate7_system_adjudication": True,
        },
        "gate2_v2": {
            "status": "no_threshold_met_precision_gate",
            "selected_threshold": None,
            "heldout_evaluated": False,
            "artifacts": gate2_artifacts,
        },
        "gate7_operating_point": {
            "frozen_similarity_threshold": 0.97,
            "run_similarity_threshold": config["hit_threshold"],
            "qualification": (
                "unqualified frozen systems operating point; Gate 2 v2 did not "
                "select or qualify a threshold"
            ),
            "derived_from_gate2_v2": False,
        },
        "artifacts": artifacts,
    }
    manifest["attempt_ledger"] = _append_attempt_ledger(
        tmp_path,
        attempt_id,
        started_at_utc,
        bundle.name,
        prior_attempts,
    )
    auditor_identity = gate7_audit._auditor_identity()
    manifest["source_identities"]["benchmarks/carma/gate7_v4_audit.py"] = dict(
        auditor_identity
    )
    raw_archive_relative = "examples/benchmark/similiar_qqp_full.json.gz"
    raw_archive = project_root / raw_archive_relative
    manifest["source_identities"][raw_archive_relative] = {
        "path": raw_archive_relative,
        "sha256": _sha256(raw_archive),
        "bytes": raw_archive.stat().st_size,
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    if finalize:
        preterminal_report = gate7_audit.analyze_bundle(
            bundle, preterminal=True
        )
        report_path = bundle / gate7_audit.PRETERMINAL_REPORT_NAME
        report_path.write_text(
            json.dumps(preterminal_report, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        _append_attempt_terminal(tmp_path, bundle, preterminal_report)
    return bundle


def test_nearest_rank_uses_one_based_ceiling():
    values = [40, 10, 30, 20]
    assert gate7_audit.nearest_rank(values, 0.50) == 20
    assert gate7_audit.nearest_rank(values, 0.95) == 40
    assert gate7_audit.nearest_rank(values, 0.99) == 40


def test_producer_and_auditor_required_source_identities_are_exactly_equal():
    project_root = Path(gate7_audit.__file__).resolve().parents[2]
    producer_path = (
        project_root / "benchmarks/carma/gate7_v4_onnx_integration_benchmark.py"
    )
    module = ast.parse(producer_path.read_text(encoding="utf-8"))
    producer_paths = None
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_source_identities":
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "paths"
                        for target in statement.targets
                    )
                ):
                    producer_paths = ast.literal_eval(statement.value)
                    break
    assert producer_paths is not None
    assert tuple(producer_paths) == gate7_audit.REQUIRED_SOURCE_IDENTITIES
    assert "examples/benchmark/similiar_qqp_full.json.gz" in producer_paths


def _seed_pair_at_all_six_boundaries():
    lru = {
        "p95_request_total_ns": 2_000_000,
        "p95_post_embedding_ns": 1_000_000,
        "paired_policy_exclusive_delta_p95_ns": 500_000,
        "throughput_qps_unrounded": 100.0,
        "rss_peak_bytes": 100 * 1024 * 1024,
    }
    carma = {
        "p95_request_total_ns": 2_500_000,
        "p95_post_embedding_ns": 1_500_000,
        "paired_policy_exclusive_delta_p95_ns": 500_000,
        "throughput_qps_unrounded": 90.0,
        "rss_peak_bytes": 120 * 1024 * 1024,
    }
    return carma, lru


def test_seed_pair_applies_simultaneous_six_frozen_bounds():
    carma, lru = _seed_pair_at_all_six_boundaries()
    result = gate7_audit.adjudicate_seed_pair(1, carma, lru)
    assert result["passes"] is True
    assert all(result["checks"].values())
    assert set(result["checks"]) == {
        "p95_ratio",
        "post_embedding_p95_delta",
        "paired_policy_exclusive_p95_delta",
        "throughput_ratio",
        "rss_ratio",
        "rss_delta",
    }
    assert result["paired_values"] == {
        "p95_ratio": 1.25,
        "legacy_request_p95_delta_ns": 500_000,
        "post_embedding_p95_delta_ns": 500_000,
        "paired_policy_exclusive_p95_delta_ns": 500_000,
        "throughput_ratio": 0.9,
        "rss_ratio": 1.2,
        "rss_delta_bytes": 20 * 1024 * 1024,
    }

    carma["p95_request_total_ns"] += 1
    result = gate7_audit.adjudicate_seed_pair(1, carma, lru)
    assert result["passes"] is False
    assert result["checks"]["p95_ratio"] is False
    assert "legacy_request_p95_delta" not in result["checks"]
    assert result["checks"]["post_embedding_p95_delta"] is True
    assert result["checks"]["paired_policy_exclusive_p95_delta"] is True


def test_legacy_full_request_delta_is_diagnostic_only():
    carma, lru = _seed_pair_at_all_six_boundaries()
    lru["p95_request_total_ns"] = 10_000_000
    carma["p95_request_total_ns"] = 11_000_000

    result = gate7_audit.adjudicate_seed_pair(1, carma, lru)

    assert result["passes"] is True
    assert result["paired_values"]["legacy_request_p95_delta_ns"] == 1_000_000
    assert "legacy_request_p95_delta" not in result["checks"]


@pytest.mark.parametrize(
    ("field", "value", "failed_check"),
    (
        ("p95_post_embedding_ns", 1_500_001, "post_embedding_p95_delta"),
        (
            "paired_policy_exclusive_delta_p95_ns",
            500_001,
            "paired_policy_exclusive_p95_delta",
        ),
        ("throughput_qps_unrounded", 89.999_999, "throughput_ratio"),
        ("rss_peak_bytes", 120 * 1024 * 1024 + 1, "rss_ratio"),
    ),
)
def test_seed_pair_fails_when_one_frozen_boundary_is_exceeded(
    field, value, failed_check
):
    carma, lru = _seed_pair_at_all_six_boundaries()
    carma[field] = value

    result = gate7_audit.adjudicate_seed_pair(1, carma, lru)

    assert result["passes"] is False
    assert result["checks"][failed_check] is False


def test_seed_pair_rss_absolute_delta_boundary_is_inclusive():
    carma, lru = _seed_pair_at_all_six_boundaries()
    lru["rss_peak_bytes"] = 400 * 1024 * 1024
    carma["rss_peak_bytes"] = 464 * 1024 * 1024

    at_boundary = gate7_audit.adjudicate_seed_pair(1, carma, lru)
    assert at_boundary["checks"]["rss_ratio"] is True
    assert at_boundary["checks"]["rss_delta"] is True

    carma["rss_peak_bytes"] += 1
    over_boundary = gate7_audit.adjudicate_seed_pair(1, carma, lru)
    assert over_boundary["checks"]["rss_ratio"] is True
    assert over_boundary["checks"]["rss_delta"] is False


def _passing_frozen_seed_results():
    checks = {
        "p95_ratio": True,
        "post_embedding_p95_delta": True,
        "paired_policy_exclusive_p95_delta": True,
        "throughput_ratio": True,
        "rss_ratio": True,
        "rss_delta": True,
    }
    return {
        str(seed): {"seed": seed, "checks": dict(checks), "passes": True}
        for seed in gate7_audit.FULL_SEEDS
    }


def test_frozen_systems_pass_requires_all_five_seeds_and_all_six_checks():
    results = _passing_frozen_seed_results()

    assert gate7_audit.frozen_systems_pass(results) is True

    failed = _passing_frozen_seed_results()
    failed[str(gate7_audit.FULL_SEEDS[-1])]["checks"]["rss_delta"] = False
    failed[str(gate7_audit.FULL_SEEDS[-1])]["passes"] = False
    assert gate7_audit.frozen_systems_pass(failed) is False

    missing = _passing_frozen_seed_results()
    missing.pop(str(gate7_audit.FULL_SEEDS[-1]))
    assert gate7_audit.frozen_systems_pass(missing) is False


def test_frozen_systems_pass_rejects_incomplete_or_extra_check_sets():
    incomplete = _passing_frozen_seed_results()
    incomplete[str(gate7_audit.FULL_SEEDS[0])]["checks"].pop("rss_delta")
    assert gate7_audit.frozen_systems_pass(incomplete) is False

    extra = _passing_frozen_seed_results()
    extra[str(gate7_audit.FULL_SEEDS[0])]["checks"]["legacy_delta"] = True
    assert gate7_audit.frozen_systems_pass(extra) is False


def _paired_seed_inputs(carma_rows, lru_rows):
    by_seed = {1: {"CARMA": "run-carma", "LRU": "run-lru"}}
    request_results = {
        "run-carma": {
            "trace_hash": "a" * 64,
            "latency": {
                "end_to_end": {"p95_ns": 1_100_000},
                "post_embedding": {"p95_ns": 200_000},
            },
            "throughput_qps_unrounded": 90.0,
        },
        "run-lru": {
            "trace_hash": "a" * 64,
            "latency": {
                "end_to_end": {"p95_ns": 1_000_000},
                "post_embedding": {"p95_ns": 100_000},
            },
            "throughput_qps_unrounded": 100.0,
        },
    }
    grouped_requests = {
        "run-carma": carma_rows,
        "run-lru": lru_rows,
    }
    resource_results = {
        "run-carma": {"rss_peak_bytes": 110 * 1024 * 1024},
        "run-lru": {"rss_peak_bytes": 100 * 1024 * 1024},
    }
    return by_seed, request_results, grouped_requests, resource_results


def test_seed_adjudication_pairs_policy_timings_by_request_id_not_row_order():
    carma_rows = [
        {
            "request_id": "request-a",
            "request_index": 0,
            "policy_exclusive_ns": 600_000,
        },
        {
            "request_id": "request-b",
            "request_index": 1,
            "policy_exclusive_ns": 100_000,
        },
    ]
    lru_rows = [
        {"request_id": "request-b", "request_index": 1, "policy_exclusive_ns": 0},
        {
            "request_id": "request-a",
            "request_index": 0,
            "policy_exclusive_ns": 200_000,
        },
    ]
    inputs = _paired_seed_inputs(carma_rows, lru_rows)
    audit = gate7_audit._Audit()

    result = gate7_audit._seed_adjudications(*inputs, audit)

    assert audit.errors == []
    assert result["1"]["passes"] is True
    assert result["1"]["paired_values"][
        "paired_policy_exclusive_p95_delta_ns"
    ] == 400_000


@pytest.mark.parametrize("mismatch", ("request_id", "request_index"))
def test_seed_adjudication_rejects_unpaired_request_identity(mismatch):
    carma_rows = [
        {"request_id": "request-a", "request_index": 0, "policy_exclusive_ns": 10},
    ]
    lru_rows = [
        {
            "request_id": "request-b" if mismatch == "request_id" else "request-a",
            "request_index": 1 if mismatch == "request_index" else 0,
            "policy_exclusive_ns": 5,
        },
    ]
    inputs = _paired_seed_inputs(carma_rows, lru_rows)
    audit = gate7_audit._Audit()

    result = gate7_audit._seed_adjudications(*inputs, audit)

    assert result == {}
    assert len(audit.errors) == 1
    assert audit.errors[0]["code"] == "adjudication_input"


def test_seed_adjudication_rejects_missing_paired_request_row():
    carma_rows = [
        {"request_id": "request-a", "request_index": 0, "policy_exclusive_ns": 10},
        {"request_id": "request-b", "request_index": 1, "policy_exclusive_ns": 10},
    ]
    lru_rows = [
        {"request_id": "request-a", "request_index": 0, "policy_exclusive_ns": 5},
    ]
    inputs = _paired_seed_inputs(carma_rows, lru_rows)
    audit = gate7_audit._Audit()

    result = gate7_audit._seed_adjudications(*inputs, audit)

    assert result == {}
    assert len(audit.errors) == 1
    assert audit.errors[0]["code"] == "adjudication_input"
    assert "not exactly paired" in audit.errors[0]["context"]["detail"]


def test_seed_adjudication_rejects_duplicate_request_id():
    carma_rows = [
        {"request_id": "request-a", "request_index": 0, "policy_exclusive_ns": 10},
        {"request_id": "request-a", "request_index": 1, "policy_exclusive_ns": 11},
    ]
    lru_rows = [
        {"request_id": "request-a", "request_index": 0, "policy_exclusive_ns": 5},
        {"request_id": "request-b", "request_index": 1, "policy_exclusive_ns": 5},
    ]
    inputs = _paired_seed_inputs(carma_rows, lru_rows)
    audit = gate7_audit._Audit()

    result = gate7_audit._seed_adjudications(*inputs, audit)

    assert result == {}
    assert len(audit.errors) == 1
    assert audit.errors[0]["code"] == "adjudication_input"
    assert "invalid request identity" in audit.errors[0]["context"]["detail"]


def test_post_embedding_check_is_difference_of_marginal_p95s_not_paired_deltas():
    carma_rows = [
        {
            "request_id": "request-a",
            "request_index": 0,
            "policy_exclusive_ns": 10,
            "post_embedding_ns": 1_000_000,
        },
        {
            "request_id": "request-b",
            "request_index": 1,
            "policy_exclusive_ns": 10,
            "post_embedding_ns": 0,
        },
    ]
    lru_rows = [
        {
            "request_id": "request-a",
            "request_index": 0,
            "policy_exclusive_ns": 5,
            "post_embedding_ns": 0,
        },
        {
            "request_id": "request-b",
            "request_index": 1,
            "policy_exclusive_ns": 5,
            "post_embedding_ns": 1_000_000,
        },
    ]
    inputs = _paired_seed_inputs(carma_rows, lru_rows)
    request_results = inputs[1]
    request_results["run-carma"]["latency"]["post_embedding"]["p95_ns"] = 1_000_000
    request_results["run-lru"]["latency"]["post_embedding"]["p95_ns"] = 1_000_000
    paired_post_deltas = [
        carma_rows[index]["post_embedding_ns"]
        - lru_rows[index]["post_embedding_ns"]
        for index in range(2)
    ]
    assert gate7_audit.nearest_rank(paired_post_deltas, 0.95) == 1_000_000
    audit = gate7_audit._Audit()

    result = gate7_audit._seed_adjudications(*inputs, audit)

    assert audit.errors == []
    assert result["1"]["paired_values"]["post_embedding_p95_delta_ns"] == 0
    assert result["1"]["checks"]["post_embedding_p95_delta"] is True


def test_complete_recomputed_fixture_passes(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "pass", result["errors"]
    assert result["claimable"] is True
    assert gate7_audit.frozen_systems_pass(result["seed_adjudication"]) is True
    manifest_path = bundle / "manifest.json"
    assert result["manifest_identity"] == {
        "path": "manifest.json",
        "sha256": _sha256(manifest_path),
        "bytes": manifest_path.stat().st_size,
    }


def test_readable_invalid_manifest_still_has_exact_report_identity(tmp_path):
    bundle = tmp_path / "invalid-manifest-bundle"
    bundle.mkdir()
    manifest_path = bundle / "manifest.json"
    manifest_path.write_bytes(b"{not-json\n")

    result = gate7_audit.analyze_bundle(bundle)

    assert result["status"] == "invalid"
    assert result["manifest_identity"] == {
        "path": "manifest.json",
        "sha256": _sha256(manifest_path),
        "bytes": manifest_path.stat().st_size,
    }
    assert any(error["code"] == "manifest_read" for error in result["errors"])


def test_terminal_rejects_rebound_preterminal_manifest_identity_tamper(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)
    report_path = bundle / gate7_audit.PRETERMINAL_REPORT_NAME
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manifest_identity"] = {
        **report["manifest_identity"],
        "sha256": "f" * 64,
    }
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    ledger_path = tmp_path / "attempt-ledger.jsonl"
    ledger_entries = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
    ]
    start = ledger_entries[-2]
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    def rebind_tampered_report(terminal):
        terminal["preterminal_report_sha256"] = _sha256(report_path)
        terminal["preterminal_report_bytes"] = report_path.stat().st_size
        intent = gate7_audit._terminal_intent_from_retained_evidence(
            start, terminal, manifest
        )
        completion = terminal["bootstrap_completion"]
        completion["terminal_intent_sha256"] = intent["intent_sha256"]
        completion.pop("attestation_sha256", None)
        completion["attestation_sha256"] = (
            gate7_audit._canonical_mapping_sha256(completion)
        )

    _rewrite_last_ledger_entry(tmp_path, rebind_tampered_report)
    result = gate7_audit.analyze_bundle(bundle)

    assert result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger_preterminal"
        and "manifest identity" in error["message"]
        for error in result["errors"]
    )


def test_manifest_terminal_without_bootstrap_completion_is_invalid(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)

    def remove_completion(terminal):
        terminal.pop("bootstrap_completion")

    _rewrite_last_ledger_entry(tmp_path, remove_completion)
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger_bootstrap_completion"
        for error in result["errors"]
    )


def test_manifest_terminal_null_bootstrap_completion_is_invalid(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)

    def clear_completion(terminal):
        terminal["bootstrap_completion"] = None

    _rewrite_last_ledger_entry(tmp_path, clear_completion)
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger_bootstrap_completion"
        for error in result["errors"]
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", "wrong-schema"),
        ("phase", "pre_target"),
        ("role", "child"),
        ("launch_mode", "smoke"),
        ("bootstrap_attestation_sha256", "f" * 64),
        ("target_sha256", "f" * 64),
        ("preimport_source_sha256", "f" * 64),
        ("dependency_sha256", "f" * 64),
        ("python_environment_sha256", "f" * 64),
        ("target_exit_status", 1),
        ("terminal_intent_sha256", "f" * 64),
        ("checks_passed", False),
    ],
)
def test_manifest_terminal_rejects_rehashed_bootstrap_completion_mismatch(
    tmp_path, monkeypatch, field, replacement
):
    bundle = _make_bundle(tmp_path, monkeypatch)

    def mutate_completion(terminal):
        completion = terminal["bootstrap_completion"]
        completion[field] = replacement
        completion.pop("attestation_sha256", None)
        completion["attestation_sha256"] = (
            gate7_audit._canonical_mapping_sha256(completion)
        )

    _rewrite_last_ledger_entry(tmp_path, mutate_completion)
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger_bootstrap_completion"
        for error in result["errors"]
    )


def test_manifest_terminal_rejects_bootstrap_completion_self_hash_tamper(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)

    def corrupt_completion_hash(terminal):
        terminal["bootstrap_completion"]["attestation_sha256"] = "f" * 64

    _rewrite_last_ledger_entry(tmp_path, corrupt_completion_hash)
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger_bootstrap_completion"
        for error in result["errors"]
    )


def test_failure_terminal_requires_null_bootstrap_completion(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    failure = _replace_manifest_with_failure_terminal(tmp_path, bundle)
    audit = gate7_audit._Audit()
    gate7_audit._validate_attempt_ledger(bundle, failure, audit, False)
    assert not any(
        "failure TERMINAL has an invalid preterminal or bootstrap-completion binding"
        in error["message"]
        for error in audit.errors
    )

    def add_completion(terminal):
        terminal["bootstrap_completion"] = {"forged": True}

    _rewrite_last_ledger_entry(tmp_path, add_completion)
    audit = gate7_audit._Audit()
    gate7_audit._validate_attempt_ledger(bundle, failure, audit, False)
    assert any(
        "failure TERMINAL has an invalid preterminal or bootstrap-completion binding"
        in error["message"]
        for error in audit.errors
    )


def test_preterminal_mode_allows_only_current_unmatched_start(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)

    preterminal = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert preterminal["status"] == "pass", preterminal["errors"]
    assert preterminal["audit_phase"] == "preterminal"

    default = gate7_audit.analyze_bundle(bundle)
    assert default["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger_unresolved_start"
        for error in default["errors"]
    )
    assert any(
        error["code"] == "attempt_ledger_terminal"
        for error in default["errors"]
    )


def test_formal_missing_entire_seed_is_invalid_not_pending(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    monkeypatch.setattr(gate7_audit, "FULL_SEEDS", (1, 2))
    monkeypatch.setattr(
        gate7_audit,
        "EXPECTED_POLICY_ORDERS",
        {1: ORDER, 2: ("LFU", "LRU", "CARMA")},
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)

    assert result["status"] == "invalid"
    assert gate7_audit.frozen_systems_pass(result["seed_adjudication"]) is False
    assert any(
        error["code"] == "formal_matrix_incomplete"
        for error in result["errors"]
    )
    assert not any(
        "fewer than five" in reason for reason in result["pending_reasons"]
    )


def test_empty_unregistered_attempt_directory_is_fail_stop(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    (tmp_path / "attempt-host-kill-debris").mkdir()

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger"
        and error.get("context", {}).get("directory")
        == "attempt-host-kill-debris"
        and error.get("context", {}).get("registrations") == 0
        for error in result["errors"]
    )


def test_preterminal_cli_creates_report_once_and_never_replaces_it(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    report_path = bundle / gate7_audit.PRETERMINAL_REPORT_NAME

    assert gate7_audit.main([str(bundle), "--preterminal"]) == 0
    assert report_path.is_file()
    assert report_path.stat().st_mode & 0o222 == 0
    with pytest.raises(FileExistsError, match="already exists"):
        gate7_audit.main([str(bundle), "--preterminal"])


def test_terminal_binding_rejects_one_field_manifest_mutation(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["historical_gate7_preserved"] = False
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger_terminal"
        and "terminal bytes" in error["message"]
        for error in result["errors"]
    )
    assert result["seed_adjudication"]["1"]["passes"] is True
    assert result["artifact_recomputation"]["requests.jsonl"]["rows"] == 60


def test_complete_numerical_violation_is_fail_not_invalid(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch, carma_total_ns=1_600_001)
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "fail", result["errors"]
    assert result["error_count"] == 0
    assert result["seed_adjudication"]["1"]["checks"]["p95_ratio"] is False
    assert gate7_audit.frozen_systems_pass(result["seed_adjudication"]) is False


def test_developmental_bundle_is_pending(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    runs_path = bundle / "runs.csv"
    with runs_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        fieldnames = list(reader.fieldnames)
    for row in rows:
        row["mode"] = "smoke"
        row["embedding_dimension"] = "32"
        row["embedding_buffer_bytes_reserved"] = str(20 * 32 * 4)
    with runs_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["config"]["mode"] = "smoke"
    manifest["config"]["fake_embedding"] = True
    manifest["formal_claimable_mode"] = False
    manifest["actual_onnx"] = False
    manifest["artifacts"]["runs.csv"]["sha256"] = _sha256(runs_path)
    manifest["artifacts"]["runs.csv"]["bytes"] = runs_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "pending", result["errors"]
    assert result["claimable"] is False


def test_artifact_tampering_is_invalid(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    with (bundle / "requests.jsonl").open("a", encoding="utf-8") as output:
        output.write("\n")
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(error["code"] == "artifact_integrity" for error in result["errors"])


@pytest.mark.parametrize("mutation", ["rename", "two_key_alias", "symlink"])
def test_required_artifact_paths_are_exact_unique_regular_children(
    tmp_path, monkeypatch, mutation
):
    bundle = _make_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    requests_path = bundle / "requests.jsonl"

    if mutation == "rename":
        renamed = bundle / "renamed-requests.jsonl"
        requests_path.rename(renamed)
        manifest["artifacts"]["requests.jsonl"]["path"] = renamed.name
    elif mutation == "two_key_alias":
        manifest["artifacts"]["resources.jsonl"] = dict(
            manifest["artifacts"]["requests.jsonl"]
        )
    else:
        retained_target = bundle / "retained-requests.jsonl"
        retained_target.write_bytes(requests_path.read_bytes())
        requests_path.unlink()
        requests_path.symlink_to(retained_target.name)

    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    expected_code = (
        "artifact_path_alias" if mutation == "two_key_alias" else "artifact_path"
    )
    assert any(error["code"] == expected_code for error in result["errors"])


@pytest.mark.parametrize("mutation", ["non_normalized", "parent_symlink"])
def test_artifact_path_rejects_lexical_aliases_and_symlinked_components(
    tmp_path, mutation
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    if mutation == "non_normalized":
        declared = "nested/../requests.jsonl"
    else:
        real = bundle / "real"
        real.mkdir()
        (real / "requests.jsonl").write_text("{}\n", encoding="utf-8")
        (bundle / "alias-dir").symlink_to(real, target_is_directory=True)
        declared = "alias-dir/requests.jsonl"
    assert gate7_audit._safe_artifact_path(
        bundle, "optional", {"path": declared}
    ) is None


@pytest.mark.parametrize(
    "mutation", ["leading_space", "key_order", "crlf", "missing_final_lf"]
)
def test_jsonl_bytes_must_be_canonical_even_when_manifest_is_rehashed(
    tmp_path, monkeypatch, mutation
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    requests_path = bundle / "requests.jsonl"
    raw = requests_path.read_bytes()
    lines = raw.splitlines(keepends=True)
    if mutation == "leading_space":
        lines[0] = b" " + lines[0]
        mutated = b"".join(lines)
    elif mutation == "key_order":
        row = json.loads(lines[0])
        reordered = dict(reversed(list(row.items())))
        lines[0] = (
            json.dumps(reordered, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        mutated = b"".join(lines)
    elif mutation == "crlf":
        mutated = raw.replace(b"\n", b"\r\n")
    else:
        assert raw.endswith(b"\n")
        mutated = raw[:-1]
    requests_path.write_bytes(mutated)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(
        manifest,
        "requests.jsonl",
        requests_path,
        len(lines),
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "requests_jsonl"
        and "row bytes are not canonical" in error["message"]
        for error in result["errors"]
    )


def test_wrong_returned_concept_cannot_be_hidden_by_producer_flags_and_counters(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)
    requests_path = bundle / "requests.jsonl"
    requests = [
        json.loads(line)
        for line in requests_path.read_text(encoding="utf-8").splitlines()
    ]
    target = next(
        row
        for row in requests
        if row["run_id"] == "run-carma" and row["request_index"] == 16
    )
    assert target["admitted"] is True and target["evicted"] is True
    target.update(
        {
            "returned_concept_id": "wrong-concept",
                "returned_response_id": "wrong-concept",
                "raw_hit": True,
                "policy_action": "hit",
            "admitted": False,
            "rejected": False,
            "evicted": False,
        }
    )
    _write_jsonl(requests_path, requests)

    runs_path = bundle / "runs.csv"
    with runs_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        runs = list(reader)
        fieldnames = list(reader.fieldnames)
    carma = next(row for row in runs if row["run_id"] == "run-carma")
    carma.update(
        {
                "hits": "1",
                "misses": "19",
                "same_concept_hits": "1",
                "response_id_mismatches": "0",
                "admissions": "19",
            "evictions": "14",
        }
    )
    _rewrite_runs(runs_path, runs, fieldnames)

    outcomes_path = bundle / "outcome-latency.jsonl"
    outcome_rows = _outcome_latency_rows(requests, runs)
    _write_jsonl(outcomes_path, outcome_rows)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(manifest, "requests.jsonl", requests_path, len(requests))
    _refresh_artifact(manifest, "runs.csv", runs_path, len(runs))
    _refresh_artifact(
        manifest, "outcome-latency.jsonl", outcomes_path, len(outcome_rows)
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "semantic_recomputation"
        and error["context"].get("run_id") == "run-carma"
        and error["context"].get("request_position") == 16
        for error in result["errors"]
    )


def test_resource_gap_larger_than_two_intervals_is_invalid(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    resources_path = bundle / "resources.jsonl"
    resources = [
        json.loads(line)
        for line in resources_path.read_text(encoding="utf-8").splitlines()
    ]
    end_sample = next(
        row
        for row in resources
        if row["run_id"] == "run-carma" and row["kind"] == "loop_end"
    )
    end_sample["monotonic_ns"] = 1_250_000_001
    end_sample["sample_started_monotonic_ns"] = 1_250_000_001
    end_sample["sample_completed_monotonic_ns"] = 1_251_000_001
    _write_jsonl(resources_path, resources)

    runs_path = bundle / "runs.csv"
    with runs_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        runs = list(reader)
        fieldnames = list(reader.fieldnames)
    carma = next(row for row in runs if row["run_id"] == "run-carma")
    resource_window_seconds = 0.300000001
    carma["resource_window_seconds"] = str(resource_window_seconds)
    carma["cpu_utilization_percent"] = str(
        round(100.0 * 0.2 / resource_window_seconds, 6)
    )
    _rewrite_runs(runs_path, runs, fieldnames)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(manifest, "resources.jsonl", resources_path, len(resources))
    _refresh_artifact(manifest, "runs.csv", runs_path, len(runs))
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "resource_cadence"
        and error["context"].get("maximum_observed_gap_ns")
        > error["context"].get("maximum_allowed_gap_ns")
        for error in result["errors"]
        if "maximum_observed_gap_ns" in error.get("context", {})
    )


def test_resource_cadence_rejects_synchronous_sample_overlap(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    resources_path = bundle / "resources.jsonl"
    resources = [
        json.loads(line)
        for line in resources_path.read_text(encoding="utf-8").splitlines()
    ]
    periodic = next(
        row
        for row in resources
        if row["run_id"] == "run-carma" and row["kind"] == "periodic"
    )
    periodic["sample_completed_monotonic_ns"] = (
        periodic["sample_started_monotonic_ns"] + 250_000_000
    )
    periodic["sample_collection_ns"] = 250_000_000

    def publish():
        _write_jsonl(resources_path, resources)
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _refresh_artifact(
            manifest, "resources.jsonl", resources_path, len(resources)
        )
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    publish()
    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "resource_collection_timing"
        and "must not overlap" in error["message"]
        for error in result["errors"]
    )


def test_resource_collection_accepts_touching_boundaries_and_rejects_bad_duration(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    resources_path = bundle / "resources.jsonl"
    resources = [
        json.loads(line)
        for line in resources_path.read_text(encoding="utf-8").splitlines()
    ]
    for run_id in {row["run_id"] for row in resources}:
        samples = sorted(
            (row for row in resources if row["run_id"] == run_id),
            key=lambda row: row["sample_index"],
        )
        for previous, current in zip(samples, samples[1:]):
            previous["sample_completed_monotonic_ns"] = current[
                "sample_started_monotonic_ns"
            ]
            previous["sample_collection_ns"] = (
                previous["sample_completed_monotonic_ns"]
                - previous["sample_started_monotonic_ns"]
            )

    def publish():
        _write_jsonl(resources_path, resources)
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _refresh_artifact(
            manifest, "resources.jsonl", resources_path, len(resources)
        )
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    publish()
    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "pass", result["errors"]

    periodic = next(
        row
        for row in resources
        if row["run_id"] == "run-carma" and row["kind"] == "periodic"
    )
    periodic["sample_collection_ns"] += 1
    publish()
    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "resource_collection_timing"
        for error in result["errors"]
    )


def test_resource_cadence_accepts_exact_200ms_start_gap(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    resources_path = bundle / "resources.jsonl"
    resources = [
        json.loads(line)
        for line in resources_path.read_text(encoding="utf-8").splitlines()
    ]
    carma_samples = sorted(
        (row for row in resources if row["run_id"] == "run-carma"),
        key=lambda row: row["sample_index"],
    )
    for row, started in zip(carma_samples[1:], (1_150_000_000, 1_250_000_000)):
        row["monotonic_ns"] = started
        row["sample_started_monotonic_ns"] = started
        row["sample_completed_monotonic_ns"] = started + 1_000_000
        row["sample_collection_ns"] = 1_000_000
    _write_jsonl(resources_path, resources)

    runs_path = bundle / "runs.csv"
    with runs_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        runs = list(reader)
        fieldnames = list(reader.fieldnames)
    carma = next(row for row in runs if row["run_id"] == "run-carma")
    carma["resource_window_seconds"] = "0.3"
    carma["cpu_utilization_percent"] = str(round(100.0 * 0.2 / 0.3, 6))
    _rewrite_runs(runs_path, runs, fieldnames)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(manifest, "resources.jsonl", resources_path, len(resources))
    _refresh_artifact(manifest, "runs.csv", runs_path, len(runs))
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "pass", result["errors"]


def test_malformed_resource_row_is_reported_without_crashing(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    resources_path = bundle / "resources.jsonl"
    resources = [json.loads(line) for line in resources_path.read_text(encoding="utf-8").splitlines()]
    resources[0]["sample_index"] = {"not": "an integer"}
    _write_jsonl(resources_path, resources)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declaration = manifest["artifacts"]["resources.jsonl"]
    declaration["sha256"] = _sha256(resources_path)
    declaration["bytes"] = resources_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(error["code"] == "resource_order" for error in result["errors"])


def test_malformed_request_cache_size_is_invalid_without_crashing(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    requests_path = bundle / "requests.jsonl"
    requests = [
        json.loads(line)
        for line in requests_path.read_text(encoding="utf-8").splitlines()
    ]
    requests[0]["cache_size_before"] = "not-an-integer"
    _write_jsonl(requests_path, requests)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(manifest, "requests.jsonl", requests_path, len(requests))
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "request_cache_size" for error in result["errors"]
    )


def test_fixed_candidate_provenance_fields_cannot_be_deleted(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    requests_path = bundle / "requests.jsonl"
    requests = [
        json.loads(line)
        for line in requests_path.read_text(encoding="utf-8").splitlines()
    ]
    requests[0].pop("top_candidate_text_id")
    requests[0].pop("candidate_cache_lookup_count")
    _write_jsonl(requests_path, requests)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(manifest, "requests.jsonl", requests_path, len(requests))
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    missing = {
        error.get("context", {}).get("field")
        for error in result["errors"]
        if error["code"] == "request_provenance_schema"
    }
    assert missing == {
        "top_candidate_text_id",
        "candidate_cache_lookup_count",
    }


def test_formal_runs_are_bound_to_full_mode_and_frozen_assets(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    runs_path = bundle / "runs.csv"
    with runs_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        runs = list(reader)
        fieldnames = list(reader.fieldnames)
    for run in runs:
        run["mode"] = "smoke"
        run["embedding_dimension"] = "32"
        run["embedding_buffer_bytes_reserved"] = str(20 * 32 * 4)
        run["asset_integrity_pre_verified"] = "False"
        run["asset_integrity_post_verified"] = "False"
        run["model_asset_digest_sha256"] = "a" * 64
        run["tokenizer_asset_digest_sha256"] = "b" * 64
    _rewrite_runs(runs_path, runs, fieldnames)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(manifest, "runs.csv", runs_path, len(runs))
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    codes = {error["code"] for error in result["errors"]}
    assert result["status"] == "invalid"
    assert {"run_mode", "embedding_dimension", "asset_integrity"} <= codes


def test_system_throughput_uses_request_service_time_not_loop_wall_time(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    runs_path = bundle / "runs.csv"
    with runs_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        runs = list(reader)
        fieldnames = list(reader.fieldnames)
    carma = next(row for row in runs if row["policy"] == "CARMA")
    carma["loop_seconds"] = "0.15"
    carma["loop_start_monotonic_ns"] = "1000000000"
    carma["loop_end_monotonic_ns"] = "1150000000"
    carma["loop_throughput_qps"] = str(round(20 / 0.15, 6))
    _rewrite_runs(runs_path, runs, fieldnames)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(manifest, "runs.csv", runs_path, len(runs))
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "pass", result["errors"]
    seed = result["seed_adjudication"]["1"]
    assert seed["paired_values"]["throughput_ratio"] > 0.9
    assert (20.0 / 0.15) / 200.0 < 0.9


def test_attempt_identity_must_match_every_evidence_layer(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["attempt_id"] = "different-attempt"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(error["code"] == "attempt_id_mismatch" for error in result["errors"])


def test_later_complete_attempt_is_ineligible_when_valid_predecessor_exists(
    tmp_path, monkeypatch
):
    attempt_root = tmp_path / "attempts"
    attempt_root.mkdir()
    first = _make_bundle(
        attempt_root,
        monkeypatch,
        attempt_id="20260827T100000000000Z-aaaaaaaaaaaa",
        bundle_name="attempt-01",
        started_at_utc="2026-08-27T10:00:00+00:00",
    )
    first_manifest_path = first / "manifest.json"
    first_manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
    predecessor = {
        "attempt_id": first_manifest["attempt_id"],
        "kind": first_manifest["kind"],
        "status": "producer_complete",
        "directory": first.name,
        "record": "manifest.json",
        "record_sha256": _sha256(first_manifest_path),
        "record_bytes": first_manifest_path.stat().st_size,
        "started_at_utc": first_manifest["started_at_utc"],
    }
    second = _make_bundle(
        attempt_root,
        monkeypatch,
        attempt_id="20260827T110000000000Z-aaaaaaaaaaaa",
        bundle_name="attempt-02",
        started_at_utc="2026-08-27T11:00:00+00:00",
        prior_attempts=[predecessor],
    )
    second_manifest_path = second / "manifest.json"
    second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))
    assert second_manifest["prior_attempts"] == [
        {
            "attempt_id": first_manifest["attempt_id"],
            "kind": first_manifest["kind"],
            "status": "producer_complete",
            "directory": first.name,
            "record": "manifest.json",
            "record_sha256": _sha256(first_manifest_path),
            "record_bytes": first_manifest_path.stat().st_size,
            "started_at_utc": first_manifest["started_at_utc"],
        }
    ]

    first_result = gate7_audit.analyze_bundle(first)
    assert first_result["status"] == "pass", first_result["errors"]
    second_result = gate7_audit.analyze_bundle(second)
    assert second_result["status"] == "invalid"
    assert second_result["attempt_eligibility"]["eligible"] is False
    assert any(
        error["code"] == "attempt_ineligible"
        for error in second_result["errors"]
    )

    isolated_root = tmp_path / "isolated-attempts"
    isolated_root.mkdir()
    isolated = isolated_root / second.name
    shutil.copytree(second, isolated)
    isolated_result = gate7_audit.analyze_bundle(isolated)
    assert isolated_result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger"
        for error in isolated_result["errors"]
    )


def test_cross_version_auditor_uses_terminal_bound_predecessor_status(
    tmp_path, monkeypatch
):
    first = _make_bundle(
        tmp_path,
        monkeypatch,
        attempt_id="20260827T100000000000Z-aaaaaaaaaaaa",
        bundle_name="attempt-01",
        started_at_utc="2026-08-27T10:00:00+00:00",
    )
    first_manifest_path = first / "manifest.json"
    first_manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
    predecessor = {
        "attempt_id": first_manifest["attempt_id"],
        "kind": first_manifest["kind"],
        "status": "producer_complete",
        "directory": first.name,
        "record": "manifest.json",
        "record_sha256": _sha256(first_manifest_path),
        "record_bytes": first_manifest_path.stat().st_size,
        "started_at_utc": first_manifest["started_at_utc"],
    }

    replacement_identity = {
        "path": "benchmarks/carma/gate7_v4_audit.py",
        "sha256": "f" * 64,
        "bytes": 999_999,
    }
    monkeypatch.setattr(
        gate7_audit,
        "_auditor_identity",
        lambda: dict(replacement_identity),
    )
    second = _make_bundle(
        tmp_path,
        monkeypatch,
        attempt_id="20260827T110000000000Z-aaaaaaaaaaaa",
        bundle_name="attempt-02",
        started_at_utc="2026-08-27T11:00:00+00:00",
        prior_attempts=[predecessor],
    )

    result = gate7_audit.analyze_bundle(second)
    assert result["status"] == "invalid"
    assert result["attempt_eligibility"]["eligible"] is False
    assert result["attempt_eligibility"]["valid_complete_predecessors"] == [
        {
            "attempt_id": first_manifest["attempt_id"],
            "status": "pass",
            "directory": first.name,
        }
    ]
    assert any(
        error["code"] == "attempt_ineligible" for error in result["errors"]
    )


def test_attempt_start_time_must_match_attempt_identity(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["started_at_utc"] = "2026-08-27T09:00:00+00:00"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "prior_attempt_identity" for error in result["errors"]
    )


def test_formal_adapter_plan_and_source_provenance_are_required(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gptcache_adapter_path"] = False
    manifest["planned_policy_orders"]["1"] = ["LRU", "LFU", "CARMA"]
    manifest["source_identities"]["benchmarks/carma/gate7_v4_audit.py"] = None
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    result = gate7_audit.analyze_bundle(bundle)
    codes = {error["code"] for error in result["errors"]}
    assert result["status"] == "invalid"
    assert "formal_flag" in codes
    assert "planned_actual_policy_order" in codes
    assert "frozen_planned_policy_order" in codes
    assert "source_identity_missing" in codes


def test_formal_bundle_is_bound_to_the_executing_auditor(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_identities"]["benchmarks/carma/gate7_v4_audit.py"][
        "sha256"
    ] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "auditor_identity_mismatch"
        for error in result["errors"]
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (("wrong_path", "source_identity_path"), ("extra", "source_identity_keys")),
)
def test_formal_source_identity_map_is_exact_and_path_bound(
    tmp_path, monkeypatch, mutation, expected_code
):
    bundle = _make_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_identities = manifest["source_identities"]
    if mutation == "wrong_path":
        source_name = "benchmarks/carma/gate7_v4_audit.py"
        source_identities[source_name]["path"] = "benchmarks/carma/not-the-auditor.py"
    else:
        source_identities["unexpected-source.py"] = {
            "path": "unexpected-source.py",
            "sha256": "0" * 64,
            "bytes": 1,
        }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    result = gate7_audit.analyze_bundle(bundle)
    codes = {error["code"] for error in result["errors"]}
    assert result["status"] == "invalid"
    assert expected_code in codes


def test_formal_process_storage_embedding_and_resource_identity_are_checked(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)
    runs_path = bundle / "runs.csv"
    with runs_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        fieldnames = list(reader.fieldnames)
    rows[1]["storage_instance_sha256"] = rows[0]["storage_instance_sha256"]
    rows[1]["child_exit_status"] = "1"
    rows[2]["embedding_dimension"] = "32"
    with runs_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    resources_path = bundle / "resources.jsonl"
    resources = [json.loads(line) for line in resources_path.read_text(encoding="utf-8").splitlines()]
    resources[0]["child_pid"] = 999_999
    _write_jsonl(resources_path, resources)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, path in (("runs.csv", runs_path), ("resources.jsonl", resources_path)):
        manifest["artifacts"][name]["sha256"] = _sha256(path)
        manifest["artifacts"][name]["bytes"] = path.stat().st_size
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    result = gate7_audit.analyze_bundle(bundle)
    codes = {error["code"] for error in result["errors"]}
    assert result["status"] == "invalid"
    assert "storage_instance_reuse" in codes
    assert "child_exit_status" in codes
    assert "embedding_dimension" in codes
    assert "resource_child_pid" in codes


def test_cli_writes_machine_readable_adjudication(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    output = tmp_path / "adjudication.json"
    assert gate7_audit.main([str(bundle), "--output", str(output)]) == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["status"] == "pass"
    assert written["schema_version"] == gate7_audit.AUDIT_SCHEMA_VERSION


def test_formal_cli_adjudication_is_create_once_and_never_overwritten(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)
    output = bundle / "gate7-adjudication.json"

    assert gate7_audit.main([str(bundle)]) == 0
    original = output.read_bytes()
    assert output.stat().st_mode & 0o222 == 0

    with pytest.raises(FileExistsError, match="immutable adjudication already exists"):
        gate7_audit.main([str(bundle)])

    assert output.read_bytes() == original


def test_formal_cli_rejects_existing_adjudication_symlink_without_touching_target(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch)
    victim = tmp_path / "victim.json"
    victim.write_bytes(b"sentinel\n")
    output = bundle / "gate7-adjudication.json"
    output.symlink_to(victim)

    with pytest.raises(FileExistsError, match="immutable adjudication already exists"):
        gate7_audit.main([str(bundle)])

    assert output.is_symlink()
    assert victim.read_bytes() == b"sentinel\n"


def test_development_cli_adjudication_remains_replaceable(tmp_path, monkeypatch):
    bundle = tmp_path / "smoke-bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {"config": {"mode": "smoke"}, "formal_claimable_mode": False},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = {
        "schema_version": gate7_audit.AUDIT_SCHEMA_VERSION,
        "status": "pending",
        "claimable": False,
    }
    monkeypatch.setattr(
        gate7_audit,
        "analyze_bundle",
        lambda _bundle, *, preterminal=False: dict(report),
    )
    output = bundle / "gate7-adjudication.json"
    output.write_bytes(b"replaceable development output\n")

    assert gate7_audit.main([str(bundle)]) == 2
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_gate2_threshold_csv_is_independently_recomputed(tmp_path):
    project_root = Path(gate7_audit.__file__).resolve().parents[2]
    source = project_root / gate7_audit.GATE2_V2_THRESHOLDS_PATH
    valid = gate7_audit._Audit()
    gate7_audit._validate_gate2_threshold_csv(source, valid)
    assert valid.errors == []

    mutated = tmp_path / "calibration-thresholds.csv"
    rows = list(csv.DictReader(source.open("r", encoding="utf-8", newline="")))
    rows[0]["precision"] = "0.999999"
    _rewrite_runs(mutated, rows, list(gate7_audit.GATE2_THRESHOLD_FIELDS))
    invalid = gate7_audit._Audit()
    gate7_audit._validate_gate2_threshold_csv(mutated, invalid)
    assert any(
        error["code"] == "gate2_v2_thresholds"
        and error.get("context", {}).get("field") == "precision"
        for error in invalid.errors
    )


def test_gate2_threshold_counts_must_be_monotonic(tmp_path):
    project_root = Path(gate7_audit.__file__).resolve().parents[2]
    source = project_root / gate7_audit.GATE2_V2_THRESHOLDS_PATH
    rows = list(csv.DictReader(source.open("r", encoding="utf-8", newline="")))
    count_and_metric_fields = [
        field
        for field in gate7_audit.GATE2_THRESHOLD_FIELDS
        if field != "threshold"
    ]
    first = {field: rows[0][field] for field in count_and_metric_fields}
    last = {field: rows[-1][field] for field in count_and_metric_fields}
    rows[0].update(last)
    rows[-1].update(first)
    mutated = tmp_path / "nonmonotonic-thresholds.csv"
    _rewrite_runs(mutated, rows, list(gate7_audit.GATE2_THRESHOLD_FIELDS))

    audit = gate7_audit._Audit()
    gate7_audit._validate_gate2_threshold_csv(mutated, audit)
    assert any(
        error["code"] == "gate2_v2_thresholds"
        and "not realizable" in error["message"]
        for error in audit.errors
    )


def test_heldout_pair_payload_and_text_are_not_semantically_inspected(tmp_path):
    for candidate in range(1000):
        texts = ["calibration %d alpha" % candidate, "calibration %d beta" % candidate]
        identity = hashlib.sha256(
            "\n".join(sorted(texts)).encode("utf-8")
        ).hexdigest()
        concept_id = "qqp-" + identity[:20]
        bucket = int(
            hashlib.sha256(concept_id.encode("utf-8")).hexdigest()[:8], 16
        ) % 100
        if bucket < 20:
            break
    else:
        raise AssertionError("could not construct calibration component")
    text_ids = [
        hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
        for text in texts
    ]
    texts_path = tmp_path / "texts.jsonl"
    _write_jsonl(
        texts_path,
        [
            {"text_id": text_id, "text": text}
            for text_id, text in zip(text_ids, texts)
        ]
        + [{"text_id": "heldout-only", "text": {"must": "not inspect"}}],
    )
    pairs_path = tmp_path / "pairs.jsonl"
    _write_jsonl(
        pairs_path,
        [
            {
                "schema_version": "carma-qqp-v1",
                "split": "calibration",
                "label": 1,
                "source_index": 1,
                "text_a_id": text_ids[0],
                "text_b_id": text_ids[1],
                "concept_a": concept_id,
                "concept_b": concept_id,
            },
            {
                "split": "test",
                "label": {"must": "not inspect"},
                "text_a_id": {"must": "not inspect"},
            },
        ],
    )

    audit = gate7_audit._Audit()
    reconstructed = gate7_audit._reconstruct_prepared_semantics(
        pairs_path,
        texts_path,
        audit,
        "heldout_test",
    )
    assert audit.errors == []
    assert reconstructed["concept_by_text_id"] == {
        text_ids[0]: concept_id,
        text_ids[1]: concept_id,
    }


def _retained_input_fixture(tmp_path, monkeypatch):
    bundle = tmp_path / "retained-bundle"
    prepared = bundle / gate7_audit.RETAINED_PREPARED_DIRECTORY
    pairs_path = prepared / "pairs.jsonl"
    texts_path = prepared / "texts.jsonl"
    archive_path = bundle / gate7_audit.RETAINED_QQP_ARCHIVE_PATH
    _write_jsonl(pairs_path, [{"fixture": "calibration-pairs"}])
    _write_jsonl(texts_path, [{"fixture": "calibration-texts"}])
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(b"retained raw QQP fixture\n")
    prepared_manifest_path = prepared / "manifest.json"
    prepared_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "carma-qqp-v1",
                "pairs_sha256": _sha256(pairs_path),
                "texts_sha256": _sha256(texts_path),
                "archive_sha256": _sha256(archive_path),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths = {
        "manifest.json": prepared_manifest_path,
        "pairs.jsonl": pairs_path,
        "texts.jsonl": texts_path,
    }

    def identity(path):
        return {"sha256": _sha256(path), "bytes": path.stat().st_size}

    retained = {
        "trace_construction_prepared_dir": gate7_audit.RETAINED_PREPARED_DIRECTORY,
        "audit_source_policy": "retained_attempt_copies_only",
        "archive": {
            "path": gate7_audit.RETAINED_QQP_ARCHIVE_PATH,
            **identity(archive_path),
        },
        "prepared": {
            name: {
                "path": "%s/%s"
                % (gate7_audit.RETAINED_PREPARED_DIRECTORY, name),
                **identity(path),
            }
            for name, path in paths.items()
        },
        "dependency_attestation": None,
        "formal_source_tag_payload": None,
    }
    artifacts = {
        declaration["path"]: {
            "path": declaration["path"],
            "sha256": declaration["sha256"],
            "bytes": declaration["bytes"],
            "rows": gate7_audit._row_count(bundle / declaration["path"]),
        }
        for declaration in [retained["archive"], *retained["prepared"].values()]
    }
    manifest = {
        "config": {"mode": "full"},
        "formal_claimable_mode": True,
        "seeds": [1],
        "retained_inputs": retained,
        "artifacts": artifacts,
    }
    monkeypatch.setattr(gate7_audit, "PROTOCOL_FULL_SEEDS", (1,))
    monkeypatch.setattr(
        gate7_audit,
        "FROZEN_SOURCE_HASHES",
        {
            "source_archive_sha256": _sha256(archive_path),
            "source_pairs_sha256": _sha256(pairs_path),
            "source_texts_sha256": _sha256(texts_path),
        },
    )
    return bundle, manifest, pairs_path


def test_formal_qqp_reconstruction_accepts_only_retained_bundle_copies(
    tmp_path, monkeypatch
):
    bundle, manifest, _ = _retained_input_fixture(tmp_path, monkeypatch)
    audit = gate7_audit._Audit()
    artifacts = gate7_audit._verify_artifacts(bundle, manifest, audit)
    retained = gate7_audit._validate_retained_inputs(
        bundle, manifest, artifacts, audit
    )
    assert all(
        error["code"] == "artifact_missing_declaration"
        for error in audit.errors
    )
    assert retained == (bundle / "source/prepared").resolve()


@pytest.mark.parametrize("mutation", ["tampered", "missing"])
def test_formal_retained_qqp_missing_or_tampered_is_invalid(
    tmp_path, monkeypatch, mutation
):
    bundle, manifest, pairs_path = _retained_input_fixture(tmp_path, monkeypatch)
    if mutation == "tampered":
        pairs_path.write_bytes(pairs_path.read_bytes() + b"tamper\n")
    else:
        pairs_path.unlink()
    audit = gate7_audit._Audit()
    artifacts = gate7_audit._verify_artifacts(bundle, manifest, audit)
    retained = gate7_audit._validate_retained_inputs(
        bundle, manifest, artifacts, audit
    )
    assert retained is None
    assert any(
        error["code"] in {
            "artifact_integrity",
            "artifact_missing",
            "retained_prepared",
        }
        for error in audit.errors
    )


def _dependency_attestation_fixture(tmp_path, monkeypatch):
    pins = {"alpha": "1.0"}
    locked_summary = {
        "record_sha256": "1" * 64,
        "hashed_file_count": 2,
        "hashed_bytes": 30,
        "hashed_files_sha256": "2" * 64,
    }
    locked = {"alpha": locked_summary}
    locked_digest = gate7_audit._canonical_mapping_sha256(locked)
    pth_files = {
        "lib/python3.12/site-packages/__editable__.gptcache-0.1.44.pth": {
            "owner": "gptcache",
            "sha256": "3" * 64,
            "bytes": 10,
        }
    }
    local_summary = {
        "record_sha256": "4" * 64,
        "hashed_file_count": 1,
        "hashed_bytes": 10,
        "hashed_files_sha256": "5" * 64,
    }
    monkeypatch.setattr(gate7_audit, "PROTOCOL_FULL_SEEDS", (1,))
    monkeypatch.setattr(gate7_audit, "PINNED_BENCHMARK_LOCK_PIN_COUNT", 1)
    monkeypatch.setattr(gate7_audit, "PINNED_LOCKED_RECORD_HASHED_FILE_COUNT", 2)
    monkeypatch.setattr(gate7_audit, "PINNED_LOCKED_RECORD_HASHED_BYTES", 30)
    monkeypatch.setattr(
        gate7_audit, "PINNED_LOCKED_RECORD_AGGREGATE_SHA256", locked_digest
    )
    monkeypatch.setattr(gate7_audit, "FROZEN_PTH_FILES", pth_files)
    monkeypatch.setattr(
        gate7_audit,
        "PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY",
        local_summary,
    )
    monkeypatch.setattr(
        gate7_audit, "_parse_benchmark_lock", lambda audit: dict(pins)
    )
    installed_packages = {"alpha": "1.0", "gptcache": "0.1.44"}
    required_owners = {
        "numpy": "numpy",
        "faiss": "faiss-cpu",
        "onnxruntime": "onnxruntime",
        "transformers": "transformers",
        "tokenizers": "tokenizers",
        "huggingface_hub": "huggingface-hub",
        "sqlalchemy": "sqlalchemy",
        "cachetools": "cachetools",
        "psutil": "psutil",
    }
    dynamic = {key: "/fixture/%s" % key for key in gate7_audit.FORMAL_DYNAMIC_ENVIRONMENT}
    dynamic["CARMA_GATE7_LAUNCH_MODE"] = "full"
    dynamic["CARMA_GATE7_WRAPPER_SHELL_PROFILE"] = "env-i-v1"
    dynamic["CARMA_GATE7_WRAPPER_SHELL_HOME"] = dynamic["HOME"]
    dynamic["CARMA_GATE7_WRAPPER_SHELL_PWD"] = "/fixture/project"
    dynamic["CARMA_GATE7_WRAPPER_SHELL_TMPDIR"] = "/tmp"
    dynamic["CARMA_GATE7_WRAPPER_SHELL_SHLVL"] = "1"
    dynamic["CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF"] = ""
    environment = {
        "required": dict(gate7_audit.FORMAL_REQUIRED_ENVIRONMENT),
        "dynamic": dynamic,
        "optional_os": {},
        "bootstrap_evidence_present": True,
        "unexpected_absent": True,
        "forbidden_absent": list(gate7_audit.FORMAL_FORBIDDEN_ENVIRONMENT),
        "forbidden_cache_absent": list(
            gate7_audit.FORMAL_FORBIDDEN_CACHE_ENVIRONMENT
        ),
        "loader_variables_absent": True,
        "pycache_prefix": "/fixture/pycache",
        "pycache_prefix_empty": True,
    }
    environment["environment_sha256"] = gate7_audit._canonical_mapping_sha256(
        environment
    )
    record = {
        "locked_distribution_count": 1,
        "locked_hashed_file_count": 2,
        "locked_hashed_bytes": 30,
        "locked_aggregate_sha256": locked_digest,
        "startup_files": pth_files,
        "sitecustomize_absent": True,
        "usercustomize_absent": True,
        "locked_distributions": locked,
        "local_editable": dict(local_summary),
        "sealed_site_packages": {
            "owned_file_count": 3,
            "owned_paths_sha256": "6" * 64,
            "owned_directory_count": 2,
            "owned_directories_sha256": "7" * 64,
            "unowned_files": [],
            "unowned_directories": [],
        },
    }
    record["aggregate_sha256"] = gate7_audit._canonical_mapping_sha256(record)
    attestation = {
        "schema_version": gate7_audit.DEPENDENCY_ATTESTATION_SCHEMA_VERSION,
        "python": {
            "implementation": "CPython",
            "version": gate7_audit.PINNED_CPYTHON_VERSION,
            "executable": ".venv/bin/python",
            "resolved_executable": "/fixture/base/bin/python3.12",
            "sys_prefix": ".venv",
            "pyvenv_cfg": {
                "path": ".venv/pyvenv.cfg",
                "sha256": "8" * 64,
                "bytes": 100,
            },
        },
        "lock": {
            "path": "requirements-benchmark.lock",
            "sha256": gate7_audit.PINNED_BENCHMARK_LOCK_SHA256,
            "pin_count": 1,
            "pin_map_sha256": gate7_audit.PINNED_BENCHMARK_PIN_MAP_SHA256,
            "pins": pins,
        },
        "installed": {
            "packages": installed_packages,
            "package_map_sha256": gate7_audit._canonical_mapping_sha256(
                installed_packages
            ),
            "third_party_origins": {
                "alpha": "lib/python3.12/site-packages"
            },
            "imported_origins": {
                module: {
                    "owner": owner,
                    "path": "lib/python3.12/site-packages/%s.py" % module,
                }
                for module, owner in required_owners.items()
            },
            "gptcache": {
                "version": "0.1.44",
                "editable": True,
                "source_path": ".",
                "module_origin": "gptcache/__init__.py",
            },
        },
        "pip_check": {
            "status": "pass",
            "returncode": 0,
            "method": "in_process_requires_dist_consistency",
            "site_module_absent": True,
        },
        "environment": environment,
        "record_integrity": record,
        "project_shadow_scan": {
            "runtime_roots": ["gptcache", "benchmarks", "."],
            "rejected_files": [],
        },
        "numpy": {"blas": "accelerate", "lapack": "accelerate"},
        "faiss": {"max_threads": 1},
    }
    attestation["attestation_sha256"] = gate7_audit._canonical_mapping_sha256(
        attestation
    )
    bundle = tmp_path / "dependency-bundle"
    path = bundle / gate7_audit.DEPENDENCY_ATTESTATION_PATH
    path.parent.mkdir(parents=True)

    def publish(value):
        value["record_integrity"]["aggregate_sha256"] = (
            gate7_audit._canonical_mapping_sha256(
                {
                    key: nested
                    for key, nested in value["record_integrity"].items()
                    if key != "aggregate_sha256"
                }
            )
        )
        value["attestation_sha256"] = gate7_audit._canonical_mapping_sha256(
            {key: nested for key, nested in value.items() if key != "attestation_sha256"}
        )
        path.write_text(
            json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        declaration = {
            "path": gate7_audit.DEPENDENCY_ATTESTATION_PATH,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "attestation_sha256": value["attestation_sha256"],
        }
        manifest = {
            "config": {"mode": "full"},
            "formal_claimable_mode": True,
            "seeds": [1],
            "retained_inputs": {"dependency_attestation": declaration},
            "dependency_attestation": value,
            "environment_sha256": value["environment"]["environment_sha256"],
        }
        artifacts = {
            gate7_audit.DEPENDENCY_ATTESTATION_PATH: {
                "path": gate7_audit.DEPENDENCY_ATTESTATION_PATH,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
        }
        run_rows = {
            "run": {
                "dependency_attestation_pre_verified": "True",
                "dependency_attestation_post_verified": "True",
                "dependency_attestation_sha256": value["attestation_sha256"],
                "environment_sha256": value["environment"]["environment_sha256"],
            }
        }
        return manifest, artifacts, run_rows

    return attestation, publish


@pytest.mark.parametrize(
    "mutation", ["record_digest", "unowned_pyc", "local_editable_record"]
)
def test_dependency_attestation_rejects_self_consistent_environment_mutation(
    tmp_path, monkeypatch, mutation
):
    attestation, publish = _dependency_attestation_fixture(tmp_path, monkeypatch)
    manifest, artifacts, run_rows = publish(attestation)
    audit = gate7_audit._Audit()
    assert gate7_audit._validate_dependency_attestation(
        tmp_path / "dependency-bundle", manifest, artifacts, run_rows, audit
    ) is not None
    assert not audit.errors

    if mutation == "record_digest":
        attestation["record_integrity"]["locked_distributions"]["alpha"][
            "record_sha256"
        ] = "9" * 64
        attestation["record_integrity"]["locked_aggregate_sha256"] = (
            gate7_audit._canonical_mapping_sha256(
                attestation["record_integrity"]["locked_distributions"]
            )
        )
    elif mutation == "unowned_pyc":
        attestation["record_integrity"]["sealed_site_packages"][
            "unowned_files"
        ] = ["lib/python3.12/site-packages/__pycache__/rogue.pyc"]
    else:
        attestation["record_integrity"]["local_editable"][
            "record_sha256"
        ] = "a" * 64
    manifest, artifacts, run_rows = publish(attestation)
    audit = gate7_audit._Audit()
    gate7_audit._validate_dependency_attestation(
        tmp_path / "dependency-bundle", manifest, artifacts, run_rows, audit
    )
    assert any(error["code"] == "dependency_attestation" for error in audit.errors)


def test_bootstrap_dependency_distinguishes_lexical_and_resolved_python(
    tmp_path, monkeypatch
):
    dependency, _ = _dependency_attestation_fixture(tmp_path, monkeypatch)
    installed = dependency["installed"]
    record = dependency["record_integrity"]
    environment = dependency["environment"]
    bootstrap = {
        "project_root": "/fixture/project",
        "python": {
            "executable": "/fixture/project/.venv/bin/python",
            "executable_resolved": "/fixture/base/bin/python3.12",
            "environment": {
                **environment["required"],
                **environment["dynamic"],
                **environment["optional_os"],
            },
        },
        "dependency": {
            "installed_packages": installed["packages"],
            "installed_package_map_sha256": installed["package_map_sha256"],
            "locked_distribution_count": record["locked_distribution_count"],
            "locked_hashed_file_count": record["locked_hashed_file_count"],
            "locked_hashed_bytes": record["locked_hashed_bytes"],
            "locked_record_aggregate_sha256": record["locked_aggregate_sha256"],
            "local_editable": record["local_editable"],
            "pth_files": record["startup_files"],
        },
    }

    assert gate7_audit._bootstrap_matches_dependency(bootstrap, dependency)

    collapsed_resolved = json.loads(json.dumps(dependency))
    collapsed_resolved["python"]["resolved_executable"] = (
        "/fixture/project/.venv/bin/python"
    )
    assert not gate7_audit._bootstrap_matches_dependency(
        bootstrap, collapsed_resolved
    )

    collapsed_lexical = json.loads(json.dumps(dependency))
    collapsed_lexical["python"]["executable"] = "/fixture/base/bin/python3.12"
    assert not gate7_audit._bootstrap_matches_dependency(
        bootstrap, collapsed_lexical
    )


def _source_anchor_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(gate7_audit, "PROTOCOL_FULL_SEEDS", (1,))
    bundle = tmp_path / "source-anchor-bundle"
    payload_path = bundle / gate7_audit.FORMAL_SOURCE_TAG_PAYLOAD_PATH
    payload_path.parent.mkdir(parents=True)

    def publish(
        tag_header=gate7_audit.FORMAL_SOURCE_TAG,
        annotation_lines=None,
    ):
        if annotation_lines is None:
            annotation_lines = [
                "experiment_id=%s" % gate7_audit.EXPERIMENT_ID,
                "contract_sha256=%s" % gate7_audit.PINNED_CONTRACT_SHA256,
            ]
        payload = (
            "object %s\n"
            "type commit\n"
            "tag %s\n"
            "tagger Fixture <fixture@example.com> 0 +0000\n\n"
            % (
                "a" * 40,
                tag_header,
            )
            + "\n".join(annotation_lines)
            + "\n"
        ).encode("utf-8")
        payload_path.write_bytes(payload)
        object_id = hashlib.sha1(
            ("tag %d\0" % len(payload)).encode("ascii") + payload
        ).hexdigest()
        anchor = {
            "schema_version": gate7_audit.FORMAL_SOURCE_ANCHOR_SCHEMA_VERSION,
            "tag_name": gate7_audit.FORMAL_SOURCE_TAG,
            "remote_name": gate7_audit.FORMAL_SOURCE_REMOTE,
            "object_format": "sha1",
            "tag_object_type": "tag",
            "tag_object_id": object_id,
            "peeled_commit": "a" * 40,
            "head_commit": "a" * 40,
            "tree_id": "b" * 40,
            "tag_payload_sha256": _sha256(payload_path),
            "tag_payload_bytes": payload_path.stat().st_size,
            "annotation": {
                "experiment_id": gate7_audit.EXPERIMENT_ID,
                "contract_sha256": gate7_audit.PINNED_CONTRACT_SHA256,
            },
            "remote": {
                "fetch_urls": [gate7_audit.FORMAL_SOURCE_REMOTE_URL],
                "push_urls": [gate7_audit.FORMAL_SOURCE_REMOTE_URL],
                "tag_object_id": object_id,
                "peeled_commit": "a" * 40,
            },
            "retained_tag_payload_path": gate7_audit.FORMAL_SOURCE_TAG_PAYLOAD_PATH,
        }
        declaration = {
            "path": gate7_audit.FORMAL_SOURCE_TAG_PAYLOAD_PATH,
            "sha256": _sha256(payload_path),
            "bytes": payload_path.stat().st_size,
        }
        source_identities = {
            "fixture.py": {
                "path": "fixture.py",
                "sha256": "c" * 64,
                "bytes": 1,
            }
        }
        manifest = {
            "config": {"mode": "full"},
            "formal_claimable_mode": True,
            "seeds": [1],
            "formal_source_anchor": anchor,
            "source_identities": source_identities,
            "source_snapshot_sha256": gate7_audit._canonical_mapping_sha256(
                source_identities
            ),
            "git": {"head_commit": "a" * 40},
            "retained_inputs": {"formal_source_tag_payload": declaration},
        }
        artifacts = {
            gate7_audit.FORMAL_SOURCE_TAG_PAYLOAD_PATH: {
                "path": gate7_audit.FORMAL_SOURCE_TAG_PAYLOAD_PATH,
                "sha256": _sha256(payload_path),
                "bytes": payload_path.stat().st_size,
            }
        }
        return manifest, artifacts

    return bundle, publish


def test_formal_source_anchor_recomputes_raw_git_tag_object(tmp_path, monkeypatch):
    bundle, publish = _source_anchor_fixture(tmp_path, monkeypatch)
    manifest, artifacts = publish()
    audit = gate7_audit._Audit()
    gate7_audit._validate_formal_source_anchor(
        bundle, manifest, artifacts, audit, preterminal=False
    )
    assert not audit.errors

    manifest, artifacts = publish(tag_header="moved-formal-tag")
    audit = gate7_audit._Audit()
    gate7_audit._validate_formal_source_anchor(
        bundle, manifest, artifacts, audit, preterminal=False
    )
    assert any(
        error["code"] == "formal_source_tag_payload" for error in audit.errors
    )


@pytest.mark.parametrize(
    "annotation_lines",
    [
        [
            "experiment_id=%s" % gate7_audit.EXPERIMENT_ID,
            "contract_sha256=%s" % gate7_audit.PINNED_CONTRACT_SHA256,
            "extra=true",
        ],
        [
            "experiment_id=%s" % gate7_audit.EXPERIMENT_ID,
            "experiment_id=%s" % gate7_audit.EXPERIMENT_ID,
            "contract_sha256=%s" % gate7_audit.PINNED_CONTRACT_SHA256,
        ],
        [
            "contract_sha256=%s" % gate7_audit.PINNED_CONTRACT_SHA256,
            "experiment_id=%s" % gate7_audit.EXPERIMENT_ID,
        ],
    ],
    ids=("extra", "duplicate", "reordered"),
)
def test_auditor_rejects_nonexact_formal_tag_annotation(
    tmp_path, monkeypatch, annotation_lines
):
    bundle, publish = _source_anchor_fixture(tmp_path, monkeypatch)
    manifest, artifacts = publish(annotation_lines=annotation_lines)
    audit = gate7_audit._Audit()

    gate7_audit._validate_formal_source_anchor(
        bundle, manifest, artifacts, audit, preterminal=False
    )

    assert any(
        error["code"] == "formal_source_tag_payload" for error in audit.errors
    )


@pytest.mark.parametrize(
    "remote_result",
    [
        "mismatch",
        "failure",
        "repointed_fetch_url",
        "repointed_push_url",
        "extra_fetch_url",
        "extra_push_url",
    ],
)
def test_preterminal_source_anchor_requires_fresh_matching_remote_refs(
    tmp_path, monkeypatch, remote_result
):
    bundle, publish = _source_anchor_fixture(tmp_path, monkeypatch)
    manifest, artifacts = publish()
    anchor = manifest["formal_source_anchor"]
    payload = (bundle / gate7_audit.FORMAL_SOURCE_TAG_PAYLOAD_PATH).read_bytes()

    def fake_check_output(command, **kwargs):
        arguments = command[1:]
        if arguments[:2] == ["rev-parse", "refs/tags/%s" % gate7_audit.FORMAL_SOURCE_TAG]:
            return anchor["tag_object_id"] + "\n"
        if arguments[:2] == ["cat-file", "-t"]:
            return "tag\n"
        if arguments[:2] == ["cat-file", "tag"]:
            return payload
        if arguments[:2] == ["rev-parse", "HEAD"]:
            return anchor["head_commit"] + "\n"
        if arguments[:2] == ["rev-parse", "%s^{tree}" % anchor["tag_object_id"]]:
            return anchor["tree_id"] + "\n"
        if arguments == [
            "remote",
            "get-url",
            "--all",
            gate7_audit.FORMAL_SOURCE_REMOTE,
        ]:
            if remote_result == "repointed_fetch_url":
                return "https://example.invalid/repointed.git\n"
            if remote_result == "extra_fetch_url":
                return (
                    gate7_audit.FORMAL_SOURCE_REMOTE_URL
                    + "\nhttps://example.invalid/extra.git\n"
                )
            return gate7_audit.FORMAL_SOURCE_REMOTE_URL + "\n"
        if arguments == [
            "remote",
            "get-url",
            "--push",
            "--all",
            gate7_audit.FORMAL_SOURCE_REMOTE,
        ]:
            if remote_result == "repointed_push_url":
                return "https://example.invalid/repointed.git\n"
            if remote_result == "extra_push_url":
                return (
                    gate7_audit.FORMAL_SOURCE_REMOTE_URL
                    + "\nhttps://example.invalid/extra.git\n"
                )
            return gate7_audit.FORMAL_SOURCE_REMOTE_URL + "\n"
        if arguments[:2] == ["ls-remote", "--tags"]:
            if remote_result == "failure":
                raise subprocess.CalledProcessError(2, command)
            tag_ref = "refs/tags/%s" % gate7_audit.FORMAL_SOURCE_TAG
            return "%s\t%s\n%s\t%s^{}\n" % (
                (
                    "f" * 40
                    if remote_result == "mismatch"
                    else anchor["tag_object_id"]
                ),
                tag_ref,
                anchor["peeled_commit"],
                tag_ref,
            )
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    audit = gate7_audit._Audit()
    gate7_audit._validate_formal_source_anchor(
        bundle, manifest, artifacts, audit, preterminal=True
    )
    assert any(
        error["code"] == "formal_source_anchor_remote"
        for error in audit.errors
    )


def test_bootstrap_accepts_frozen_boundary_and_rejects_self_consistent_mutations(
    tmp_path, monkeypatch
):
    dependency, publish = _dependency_attestation_fixture(tmp_path, monkeypatch)
    manifest, _, _ = publish(dependency)
    target = "benchmarks/carma/gate7_v4_onnx_integration_benchmark.py"
    source_anchor = _synthetic_formal_source_anchor()
    manifest["source_identities"] = {
        target: {"path": target, "sha256": "a" * 64, "bytes": 1}
    }
    manifest["formal_source_anchor"] = source_anchor
    manifest["contract"] = {
        "path": gate7_audit.DEFAULT_CONTRACT_PATH,
        "sha256": gate7_audit.PINNED_CONTRACT_SHA256,
        "bytes": 1,
    }
    environment = {
        **dict(dependency["environment"]["required"]),
        **dict(dependency["environment"]["dynamic"]),
    }
    bootstrap_dependency = {
        "installed_packages": dependency["installed"]["packages"],
        "installed_package_map_sha256": dependency["installed"][
            "package_map_sha256"
        ],
        "locked_distribution_count": dependency["record_integrity"][
            "locked_distribution_count"
        ],
        "locked_hashed_file_count": dependency["record_integrity"][
            "locked_hashed_file_count"
        ],
        "locked_hashed_bytes": dependency["record_integrity"][
            "locked_hashed_bytes"
        ],
        "locked_record_aggregate_sha256": dependency["record_integrity"][
            "locked_aggregate_sha256"
        ],
        "local_editable": dict(
            dependency["record_integrity"]["local_editable"]
        ),
        "pth_files": json.loads(
            json.dumps(dependency["record_integrity"]["startup_files"])
        ),
        "unhashed_existing_file_count": 2,
        "unhashed_existing_sha256": "b" * 64,
        "site_file_count": 10,
        "site_directory_count": 5,
        "sealed_site_packages": True,
    }
    observation = {
        "schema_version": gate7_audit.ISOLATED_BOOTSTRAP_SCHEMA_VERSION,
        "role": "child",
        "target": target,
        "target_sha256": "a" * 64,
        "project_root": "/fixture/project",
        "venv_root": "/fixture/project/.venv",
        "site_packages": "/fixture/project/.venv/lib/python3.12/site-packages",
        "python": {
            "flags": {
                "no_site": 1,
                "safe_path": True,
                "ignore_environment": 0,
                "isolated": 0,
                "dont_write_bytecode": 1,
                "hash_randomization": 0,
            },
            "initial_sys_path": ["/fixture/stdlib"],
            "base_prefix": "/fixture/base",
            "executable": "/fixture/project/.venv/bin/python",
            "executable_resolved": "/fixture/base/bin/python3.12",
            "executable_sha256": "c" * 64,
            "wrapper_pid": 123,
            "environment": environment,
            "wrapper_shell_startup": _synthetic_wrapper_shell_startup(
                environment
            ),
        },
        "pyvenv": {
            "path": ".venv/pyvenv.cfg",
            "sha256": "d" * 64,
            "values": {
                "version": gate7_audit.PINNED_CPYTHON_VERSION,
                "include-system-site-packages": "false",
            },
        },
        "lock": {
            "path": "requirements-benchmark.lock",
            "sha256": gate7_audit.PINNED_BENCHMARK_LOCK_SHA256,
            "pin_count": gate7_audit.PINNED_BENCHMARK_LOCK_PIN_COUNT,
            "pin_map_sha256": gate7_audit.PINNED_BENCHMARK_PIN_MAP_SHA256,
        },
        "dependency": bootstrap_dependency,
        "preimport_source": _synthetic_preimport_source(source_anchor),
        "preloaded_module_origins": {
            "json": "<stdlib>/json/__init__.py",
            "__main__": gate7_audit.ISOLATED_BOOTSTRAP_PATH,
        },
        "site_module_absent": True,
        "pth_executed": False,
    }
    observation["attestation_sha256"] = gate7_audit._canonical_mapping_sha256(
        observation
    )
    audit = gate7_audit._Audit()
    assert gate7_audit._validate_bootstrap_attestation(
        observation, "child", manifest, audit, "fixture"
    ) is not None
    assert not audit.errors

    mutations = []

    pth_owner = json.loads(json.dumps(observation))
    next(iter(pth_owner["dependency"]["pth_files"].values()))[
        "owner"
    ] = "alpha"
    mutations.append(pth_owner)

    smoke_launch = json.loads(json.dumps(observation))
    smoke_launch["python"]["environment"]["CARMA_GATE7_LAUNCH_MODE"] = "smoke"
    smoke_launch["python"]["environment"][
        "CARMA_GATE7_WRAPPER_SHELL_PROFILE"
    ] = "development-smoke"
    smoke_launch["preimport_source"]["launch_mode"] = "smoke"
    smoke_launch["python"]["wrapper_shell_startup"]["launch_mode"] = "smoke"
    smoke_launch["python"]["wrapper_shell_startup"][
        "profile"
    ] = "development-smoke"
    smoke_launch["python"]["wrapper_shell_startup"][
        "outer_env_i_operator_root_required"
    ] = False
    mutations.append(smoke_launch)

    forged_shell_pwd = json.loads(json.dumps(observation))
    forged_shell_pwd["python"]["environment"][
        "CARMA_GATE7_WRAPPER_SHELL_PWD"
    ] = "/fixture/elsewhere"
    forged_shell_pwd["python"]["wrapper_shell_startup"][
        "pwd"
    ] = "/fixture/elsewhere"
    mutations.append(forged_shell_pwd)

    moved_anchor = json.loads(json.dumps(observation))
    moved_anchor["preimport_source"]["anchor"]["head_commit"] = "f" * 40
    moved_anchor["preimport_source"]["anchor"]["peeled_commit"] = "f" * 40
    mutations.append(moved_anchor)

    for field, replacement in (
        ("remote_name", "elsewhere"),
        ("fetch_url", "https://example.invalid/fetch.git"),
        ("fetch_url_count", 2),
        ("push_url", "https://example.invalid/push.git"),
        ("push_url_count", 2),
        ("tag_ref", "refs/tags/moved"),
        ("tag_object_id", "f" * 40),
        ("peeled_ref", "refs/tags/moved^{}"),
        ("peeled_commit", "f" * 40),
    ):
        moved_remote = json.loads(json.dumps(observation))
        moved_remote["preimport_source"]["anchor"]["remote"][field] = replacement
        mutations.append(moved_remote)

    untracked_import = json.loads(json.dumps(observation))
    untracked_import["preimport_source"]["inventory"][
        "project_import_namespace"
    ]["untracked_or_ignored_import_file_count"] = 1
    mutations.append(untracked_import)

    for mutated in mutations:
        wrapper_shell = mutated["python"]["wrapper_shell_startup"]
        wrapper_shell["attestation_sha256"] = (
            gate7_audit._canonical_mapping_sha256(
                {
                    key: value
                    for key, value in wrapper_shell.items()
                    if key != "attestation_sha256"
                }
            )
        )
        mutated["attestation_sha256"] = gate7_audit._canonical_mapping_sha256(
            {
                key: value
                for key, value in mutated.items()
                if key != "attestation_sha256"
            }
        )
        audit = gate7_audit._Audit()
        gate7_audit._validate_bootstrap_attestation(
            mutated, "child", manifest, audit, "fixture"
        )
        assert any(
            error["code"]
            in {
                "bootstrap_attestation",
                "bootstrap_preimport_source",
                "bootstrap_wrapper_shell",
            }
            for error in audit.errors
        )


def test_formal_power_observation_rejects_available_but_unplugged(
    monkeypatch,
):
    monkeypatch.setattr(gate7_audit, "PROTOCOL_FULL_SEEDS", (1,))
    observation = {
        "observed_at_utc": "2026-08-27T10:00:00+00:00",
        "available": True,
        "plugged": False,
        "percent": 80.0,
        "seconds_left": 1000,
    }
    manifest = {
        "config": {"mode": "full"},
        "formal_claimable_mode": True,
        "seeds": [1],
        "power_observations": {
            "pre_start": observation,
            "end": dict(observation),
            "availability_limitation": (
                "point observations cannot prove uninterrupted AC between observations"
            ),
        },
    }
    audit = gate7_audit._Audit()
    gate7_audit._validate_power_observations(manifest, audit)
    assert any(error["code"] == "power_observations" for error in audit.errors)


def test_formal_power_observation_rejects_unavailable_endpoint(monkeypatch):
    monkeypatch.setattr(gate7_audit, "PROTOCOL_FULL_SEEDS", (1,))
    available = _synthetic_pre_start_power()
    unavailable = {
        "observed_at_utc": "2026-08-27T10:01:00+00:00",
        "available": False,
        "plugged": None,
        "percent": None,
        "seconds_left": None,
    }
    manifest = {
        "config": {"mode": "full"},
        "formal_claimable_mode": True,
        "seeds": [1],
        "power_observations": {
            "pre_start": available,
            "end": unavailable,
            "availability_limitation": (
                "at least one endpoint power observation was unavailable; "
                "uninterrupted AC cannot be attested"
            ),
        },
    }
    audit = gate7_audit._Audit()

    gate7_audit._validate_power_observations(manifest, audit)

    assert any(error["code"] == "power_observations" for error in audit.errors)


def test_warmup_artifact_is_rebuilt_and_shared_across_policy_runs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(gate7_audit, "PROTOCOL_FULL_SEEDS", (1,))
    bundle = tmp_path / "warmup-bundle"
    path = bundle / "warmups/seed-1.json"
    path.parent.mkdir(parents=True)
    trace = [
        {"text_id": "hot-a", "text": "alpha"},
        {"text_id": "hot-b", "text": "beta"},
    ]
    value = {
        "schema_version": gate7_audit.WARMUP_SCHEMA_VERSION,
        "experiment_id": gate7_audit.EXPERIMENT_ID,
        "seed": 1,
        "trace_sha256": "d" * 64,
        "warmup_requests": 3,
        "ordered_hot_text_ids": ["hot-a", "hot-b"],
        "warmup_text_ids": ["hot-a", "hot-b", "hot-a"],
        "warmup_texts": ["alpha", "beta", "alpha"],
    }
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    identity = {
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "rows": sum(1 for _ in path.open("rb")),
    }
    manifest = {
        "config": {"mode": "full", "warmup_requests": 3},
        "formal_claimable_mode": True,
        "seeds": [1],
        "warmup_artifacts": {"warmups/seed-1.json": identity},
        "trace_metadata": {
            "1": {
                "trace_sha256": "d" * 64,
                "hot_text_ids": ["hot-a", "hot-b"],
            }
        },
    }
    artifacts = {
        "warmups/seed-1.json": {"path": "warmups/seed-1.json", **identity}
    }
    run_rows = {
        policy: {
            "seed": "1",
            "warmup_artifact_sha256": identity["sha256"],
        }
        for policy in gate7_audit.POLICIES
    }
    audit = gate7_audit._Audit()
    gate7_audit._validate_warmup_artifacts(
        bundle, manifest, artifacts, {1: trace}, run_rows, audit
    )
    assert not audit.errors
    assert set(identity) == {"sha256", "bytes", "rows"}

    for row_mutation in ("missing", "changed"):
        mutated_manifest = json.loads(json.dumps(manifest))
        declaration = mutated_manifest["warmup_artifacts"][
            "warmups/seed-1.json"
        ]
        if row_mutation == "missing":
            declaration.pop("rows")
        else:
            declaration["rows"] += 1
        audit = gate7_audit._Audit()
        gate7_audit._validate_warmup_artifacts(
            bundle,
            mutated_manifest,
            artifacts,
            {1: trace},
            run_rows,
            audit,
        )
        assert any(
            error["code"] == "warmup_artifacts" for error in audit.errors
        )

    run_rows["CARMA"]["warmup_artifact_sha256"] = "e" * 64
    audit = gate7_audit._Audit()
    gate7_audit._validate_warmup_artifacts(
        bundle, manifest, artifacts, {1: trace}, run_rows, audit
    )
    assert any(error["code"] == "warmup_artifacts" for error in audit.errors)


def test_full_mode_cannot_opt_out_of_formal_integrity(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["formal_claimable_mode"] = False
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(error["code"] == "formal_claimable_mode" for error in result["errors"])


@pytest.mark.parametrize(
    "mutation",
    ["missing", "false", "moved", "extra"],
)
def test_formal_publication_integrity_is_exact_and_location_bound(
    tmp_path, monkeypatch, mutation
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        manifest.pop("publication_integrity")
    elif mutation == "false":
        manifest["publication_integrity"]["contract_unchanged"] = False
    elif mutation == "moved":
        manifest["contract_unchanged"] = manifest["publication_integrity"].pop(
            "contract_unchanged"
        )
    else:
        manifest["publication_integrity"]["unexpected"] = True
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)

    assert result["status"] == "invalid"
    assert any(
        error["code"] == "publication_integrity" for error in result["errors"]
    )


@pytest.mark.parametrize("mutation", ["missing", "status", "boolean"])
def test_post_v3_protocol_disclosure_is_required_and_exact(
    tmp_path, monkeypatch, mutation
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        manifest.pop("post_v3_protocol_disclosure")
    elif mutation == "status":
        manifest["post_v3_protocol_disclosure"]["v3_operational_status"] = "PASS"
    else:
        manifest["post_v3_protocol_disclosure"]["reclassifies_v1_v2_v3"] = True
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)

    assert result["status"] == "invalid"
    assert result["post_v3_protocol_disclosure"] == (
        gate7_audit.POST_V3_PROTOCOL_DISCLOSURE
    )
    assert any(
        error["code"] == "post_v3_protocol_disclosure"
        for error in result["errors"]
    )


def test_entrypoint_rejects_bash_c_wrapper_embedding():
    entrypoint = _synthetic_entrypoint_attestation()
    assert gate7_audit._formal_entrypoint_structurally_valid(entrypoint)
    entrypoint["parent_cmdline"] = [
        "/bin/bash",
        "-c",
        "/fixture/" + gate7_audit.FORMAL_WRAPPER_PATH + " full",
    ]
    assert not gate7_audit._formal_entrypoint_structurally_valid(entrypoint)

    entrypoint = _synthetic_entrypoint_attestation()
    entrypoint["parent_cmdline"][1] = gate7_audit.FORMAL_WRAPPER_PATH
    assert not gate7_audit._formal_entrypoint_structurally_valid(entrypoint)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("preprocess_ns", 41),
        ("faiss_ns", 16),
        ("sqlite_ns", 16),
        ("similarity_evaluation_ns", 6),
        ("response_materialization_ns", 4),
        ("end_to_end_ns", 1_100_001),
        ("post_embedding_ns", 1_099_351),
        ("post_embedding_total_ns", 1_099_351),
        ("embedding_ns", 651),
        ("cache_management_ns", 16),
        ("policy_inclusive_ns", 4),
        ("embedding_elapsed_ns", 609),
        ("post_embedding_elapsed_ns", 1_099_351),
    ],
)
def test_request_timing_alias_tamper_is_structurally_invalid(
    tmp_path, monkeypatch, field, replacement
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    requests_path = bundle / "requests.jsonl"
    requests = [
        json.loads(line)
        for line in requests_path.read_text(encoding="utf-8").splitlines()
    ]
    requests[0][field] = replacement
    _write_jsonl(requests_path, requests)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_artifact(
        manifest, "requests.jsonl", requests_path, len(requests)
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "request_derived_timing" for error in result["errors"]
    )


def test_rehashed_gate2_post_result_threshold_selection_is_rejected(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    real_root = Path(gate7_audit.__file__).resolve().parents[2]
    shadow_root = tmp_path / "shadow-project"
    shadow_auditor = shadow_root / "benchmarks/carma/gate7_v4_audit.py"
    shadow_auditor.parent.mkdir(parents=True)
    for relative in (
        gate7_audit.GATE2_V2_SELECTION_PATH,
        gate7_audit.GATE2_V2_RESULT_PATH,
        gate7_audit.GATE2_V2_THRESHOLDS_PATH,
    ):
        target = shadow_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(real_root / relative, target)

    selection_path = shadow_root / gate7_audit.GATE2_V2_SELECTION_PATH
    result_path = shadow_root / gate7_audit.GATE2_V2_RESULT_PATH
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["status"] = "threshold_selected"
    selection["selected_threshold"] = 0.97
    selection["selected_calibration_metrics"] = {"threshold": 0.97}
    selection_path.write_text(
        json.dumps(selection, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["status"] = "threshold_selected"
    result["selected_threshold"] = 0.97
    result["calibration"] = {"threshold": 0.97}
    result["selection_transcript"] = {
        "path": "threshold-selection.json",
        "sha256": _sha256(selection_path),
        "bytes": selection_path.stat().st_size,
    }
    result_path.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(gate7_audit, "__file__", str(shadow_auditor))
    monkeypatch.setattr(
        gate7_audit,
        "PINNED_GATE2_V2_SELECTION_SHA256",
        _sha256(selection_path),
    )
    monkeypatch.setattr(
        gate7_audit,
        "PINNED_GATE2_V2_RESULT_SHA256",
        _sha256(result_path),
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shadow_identities = {}
    for relative in (
        gate7_audit.GATE2_V2_SELECTION_PATH,
        gate7_audit.GATE2_V2_RESULT_PATH,
        gate7_audit.GATE2_V2_THRESHOLDS_PATH,
    ):
        path = shadow_root / relative
        shadow_identities[relative] = {
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
    manifest["gate2_v2"] = {
        "status": "threshold_selected",
        "selected_threshold": 0.97,
        "heldout_evaluated": False,
        "artifacts": shadow_identities,
    }

    audit = gate7_audit._Audit()
    gate7_audit._formal_protocol_checks(manifest, manifest["artifacts"], audit)
    codes = {error["code"] for error in audit.errors}
    assert "gate2_v2_identity" not in codes
    assert "gate2_v2_protocol" in codes
    assert "gate2_v2_manifest" in codes


def test_semantic_source_rebuild_recovers_all_negative_edges_and_sources(
    tmp_path,
):
    def component(prefix):
        for candidate in range(1000):
            texts = [
                "%s %d alpha" % (prefix, candidate),
                "%s %d beta" % (prefix, candidate),
            ]
            identity = hashlib.sha256(
                "\n".join(sorted(texts)).encode("utf-8")
            ).hexdigest()
            concept_id = "qqp-" + identity[:20]
            bucket = int(
                hashlib.sha256(concept_id.encode("utf-8")).hexdigest()[:8],
                16,
            ) % 100
            if bucket < 20:
                return concept_id, texts
        raise AssertionError("could not construct a calibration component")

    concept_a, texts_a = component("component a")
    concept_b, texts_b = component("component b")
    text_ids_a = [
        hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
        for text in texts_a
    ]
    text_ids_b = [
        hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
        for text in texts_b
    ]
    texts_path = tmp_path / "texts.jsonl"
    _write_jsonl(
        texts_path,
        sorted(
            [
                {"text_id": text_id, "text": text}
                for text_id, text in zip(text_ids_a + text_ids_b, texts_a + texts_b)
            ],
            key=lambda row: row["text_id"],
        ),
    )
    pairs = tmp_path / "pairs.jsonl"
    pair_rows = [
            {
                "schema_version": "carma-qqp-v1",
                "split": "calibration",
                "label": 1,
                "source_index": 1,
                "text_a_id": text_ids_a[0],
                "text_b_id": text_ids_a[1],
                "concept_a": concept_a,
                "concept_b": concept_a,
            },
            {
                "schema_version": "carma-qqp-v1",
                "split": "calibration",
                "label": 1,
                "source_index": 2,
                "text_a_id": text_ids_b[0],
                "text_b_id": text_ids_b[1],
                "concept_a": concept_b,
                "concept_b": concept_b,
            },
            {
                "schema_version": "carma-qqp-v1",
                "split": "calibration",
                "label": 0,
                "source_index": 7,
                "text_a_id": text_ids_a[1],
                "text_b_id": text_ids_b[1],
                "concept_a": concept_a,
                "concept_b": concept_b,
            },
            {
                "schema_version": "carma-qqp-v1",
                "split": "calibration",
                "label": 0,
                "source_index": 8,
                "text_a_id": text_ids_a[0],
                "text_b_id": text_ids_b[0],
                "concept_a": concept_a,
                "concept_b": concept_b,
            },
        ]
    _write_jsonl(pairs, pair_rows)
    audit = gate7_audit._Audit()
    rebuilt = gate7_audit._rebuild_semantic_maps_from_pairs(
        pairs, (concept_a, concept_b), audit, 17
    )
    assert audit.errors == []
    assert rebuilt == {
        "text_id_to_concept_id": dict(
            sorted(
                [(text_id, concept_a) for text_id in text_ids_a]
                + [(text_id, concept_b) for text_id in text_ids_b]
            )
        ),
        "canonical_text_by_concept": {
            concept_a: min(text_ids_a),
            concept_b: min(text_ids_b),
        },
        "direct_negative_pairs": {
            gate7_audit._pair(text_ids_a[0], text_ids_b[0]): (8,),
            gate7_audit._pair(text_ids_a[1], text_ids_b[1]): (7,),
        },
        "component_negative_pairs": {
            gate7_audit._pair(concept_a, concept_b): (7, 8)
        },
    }

    fabricated = "qqp-fabricated-concept"
    for row in pair_rows:
        for field in ("concept_a", "concept_b"):
            if row[field] == concept_a:
                row[field] = fabricated
    _write_jsonl(pairs, pair_rows)
    mutated = gate7_audit._Audit()
    gate7_audit._rebuild_semantic_maps_from_pairs(
        pairs, (concept_a, concept_b), mutated, 17
    )
    assert any(
        error["code"] == "semantic_index_source"
        and "concept field" in error["message"]
        for error in mutated.errors
    )


def _semantic_unit_fixture():
    mapping = {
        "q": "A",
        "a2": "A",
        "d": "B",
        "b2": "B",
        "c": "C",
    }
    index = {
        "text_id_to_concept_id": mapping,
        "direct_negative_pairs": {("d", "q"): (11,)},
        "component_negative_pairs": {("A", "B"): (11, 12)},
        "raw": {"semantic_index_sha256": "canonical-index"},
        "sha256": "artifact-bytes",
    }
    traces = {
        text_id: {"text_id": text_id, "concept_id": concept, "text": "text " + text_id}
        for text_id, concept in mapping.items()
    }

    def hit(cached_text_id, cached_concept_id):
        payload = "recorded-response-v2:%s:%s" % (
            cached_concept_id,
            cached_text_id,
        )
        return {
            "raw_hit": True,
            "text_id": "q",
            "expected_response_id": "A",
            "returned_response_id": cached_concept_id,
            "returned_concept_id": cached_concept_id,
            "cached_source_text_id": cached_text_id,
            "cached_source_concept_id": cached_concept_id,
            "payload_source_text_id": cached_text_id,
            "payload_source_concept_id": cached_concept_id,
            "cached_answer_raw_identity": payload,
            "cached_answer_source_text_id": cached_text_id,
            "cached_answer_source_concept_id": cached_concept_id,
            "cached_answer_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "cached_question_text_sha256": hashlib.sha256(
                ("text " + cached_text_id).encode("utf-8")
            ).hexdigest(),
            "top_candidate_id": 4,
            "top_candidate_text_id": cached_text_id,
            "candidate_cache_data_seen": True,
            "candidate_cache_data_present": True,
            "candidate_cache_lookup_count": 1,
            "cache_size_before": 4,
            "cache_size_after": 4,
        }

    return index, traces, hit


def test_semantic_taxonomy_and_inconsistent_provenance_are_recomputed():
    index, traces, hit = _semantic_unit_fixture()
    cases = (
        (hit("a2", "A"), "same_concept", "positive_same_component"),
        (hit("d", "B"), "direct_negative", "negative_direct"),
        (
            hit("b2", "B"),
            "component_derived_negative",
            "negative_component_derived",
        ),
        (
            hit("c", "C"),
            "unlabeled_cross_concept",
            "unlabeled_cross_component",
        ),
    )
    for row, hit_class, relation in cases:
        expected = gate7_audit._semantic_expectation(row, index, traces, 10)
        assert expected["hit_class"] == hit_class
        assert expected["semantic_relation"] == relation
        assert expected["structural_failure"] is False

    inconsistent = hit("d", "B")
    inconsistent["payload_source_concept_id"] = "A"
    expected = gate7_audit._semantic_expectation(
        inconsistent, index, traces, 10
    )
    assert expected["provenance_resolved"] is True
    assert expected["provenance_consistent"] is False
    assert expected["hit_class"] == "unresolved"
    assert expected["semantic_relation"] == "not_applicable"
    assert expected["structural_failure"] is True

    mismatched_miss = hit("d", "B")
    mismatched_miss["raw_hit"] = False
    expected = gate7_audit._semantic_expectation(
        mismatched_miss, index, traces, 10
    )
    assert expected["hit_class"] == "miss"
    assert expected["source_response_mismatch"] is True
    assert expected["structural_failure"] is True


def test_labeled_negative_guardrail_failure_remains_structurally_valid():
    index, traces, hit = _semantic_unit_fixture()
    row = hit("d", "B")
    row.update(gate7_audit._semantic_expectation(row, index, traces, 10))
    summary = {
        "seed": 1,
        "capacity": 10,
        "semantic_index_sha256": "canonical-index",
        "semantic_index_artifact_sha256": "artifact-bytes",
        "hits": 1,
        "misses": 0,
        "same_concept_hits": 0,
        "direct_negative_hits": 1,
        "component_derived_negative_hits": 0,
        "unlabeled_cross_concept_hits": 0,
        "unresolved_provenance_hits": 0,
        "labeled_negative_hits": 1,
        "cross_concept_hits": 1,
        "response_id_mismatches": 1,
        "source_response_mismatches": 0,
        "unrecognized_response_ids": 0,
        "stale_candidates": 0,
        "capacity_excess_requests": 0,
        "structural_failure_requests": 0,
        "structural_failure_flags_total": 0,
        "max_cache_size": 4,
        "semantic_guardrail_status": "FAIL",
        "structural_valid": True,
        "storage_failure": None,
    }
    audit = gate7_audit._Audit()
    recomputed = gate7_audit._recompute_semantic_guardrail(
        {"run": [row]},
        {"run": summary},
        {1: list(traces.values())},
        {1: index},
        audit,
    )
    assert audit.errors == []
    assert recomputed["semantic_guardrail_status"] == "FAIL"
    assert recomputed["per_run"]["run"]["structural_valid"] is True


def test_system_gate_pass_can_coexist_with_semantic_guardrail_fail(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    trace = _trace_rows()
    target_trace = trace[16]
    cached_trace = trace[0]
    assert target_trace["concept_id"] != cached_trace["concept_id"]

    semantic_path = bundle / "semantic-indexes/seed-1.json"
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    text_pair = sorted([target_trace["text_id"], cached_trace["text_id"]])
    concept_pair = sorted(
        [target_trace["concept_id"], cached_trace["concept_id"]]
    )
    semantic["direct_negative_pairs"] = [
        {
            "text_id_a": text_pair[0],
            "text_id_b": text_pair[1],
            "source_indices": [1804],
        }
    ]
    semantic["component_negative_pairs"] = [
        {
            "concept_id_a": concept_pair[0],
            "concept_id_b": concept_pair[1],
            "source_indices": [1804],
        }
    ]
    semantic["counts"].update(
        {
            "direct_negative_pairs": 1,
            "direct_negative_text_pairs": 1,
            "component_negative_pairs": 1,
            "negative_component_derived_pairs": 0,
            "unlabeled_cross_component_pairs": semantic["counts"][
                "distinct_candidate_pairs"
            ]
            - 1,
        }
    )
    canonical_rows = []
    canonical_rows.extend(
        {
            "schema_version": gate7_audit.SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "selected_concept",
            **row,
        }
        for row in semantic["selected_concepts"]
    )
    canonical_rows.extend(
        {
            "schema_version": gate7_audit.SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "component_member",
            **row,
        }
        for row in semantic["component_members"]
    )
    canonical_rows.extend(
        {
            "schema_version": gate7_audit.SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "direct_negative_pair",
            **row,
        }
        for row in semantic["direct_negative_pairs"]
    )
    canonical_rows.extend(
        {
            "schema_version": gate7_audit.SEMANTIC_INDEX_SCHEMA_VERSION,
            "kind": "component_negative_pair",
            **row,
        }
        for row in semantic["component_negative_pairs"]
    )
    canonical_lines = sorted(
        json.dumps(row, sort_keys=True, separators=(",", ":"))
        for row in canonical_rows
    )
    semantic_digest = hashlib.sha256()
    for line in canonical_lines:
        semantic_digest.update(line.encode("utf-8"))
        semantic_digest.update(b"\n")
    semantic["canonical_row_count"] = len(canonical_lines)
    semantic["semantic_index_sha256"] = semantic_digest.hexdigest()
    semantic_path.write_text(
        json.dumps(semantic, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    semantic_artifact_sha256 = _sha256(semantic_path)

    requests_path = bundle / "requests.jsonl"
    requests = [
        json.loads(line)
        for line in requests_path.read_text(encoding="utf-8").splitlines()
    ]
    target = next(
        row
        for row in requests
        if row["run_id"] == "run-carma" and row["request_index"] == 16
    )
    cached_payload = cached_trace["response_payload"]
    target.update(
        {
            "raw_hit": True,
            "returned_response_id": cached_trace["response_id"],
            "returned_concept_id": cached_trace["concept_id"],
            "top_candidate_id": 3,
            "top_candidate_text_id": cached_trace["text_id"],
            "candidate_cache_data_seen": True,
            "candidate_cache_data_present": True,
            "candidate_cache_lookup_count": 1,
            "cached_question_text_sha256": hashlib.sha256(
                cached_trace["text"].encode("utf-8")
            ).hexdigest(),
            "cached_source_text_id": cached_trace["text_id"],
            "cached_source_concept_id": cached_trace["concept_id"],
            "cached_answer_sha256": hashlib.sha256(
                cached_payload.encode("utf-8")
            ).hexdigest(),
            "cached_answer_raw_identity": cached_payload,
            "cached_answer_source_text_id": cached_trace["text_id"],
            "cached_answer_source_concept_id": cached_trace["concept_id"],
            "payload_source_text_id": cached_trace["text_id"],
            "payload_source_concept_id": cached_trace["concept_id"],
            "similarity": 0.99,
            "policy_action": "hit",
            "admitted": False,
            "rejected": False,
            "evicted": False,
            "cache_size_after": target["cache_size_before"],
        }
    )
    semantic_index = {
        "text_id_to_concept_id": {
            row["text_id"]: row["concept_id"]
            for row in semantic["component_members"]
        },
        "direct_negative_pairs": {tuple(text_pair): (1804,)},
        "component_negative_pairs": {tuple(concept_pair): (1804,)},
    }
    target.update(
        gate7_audit._semantic_expectation(
            target,
            semantic_index,
            {row["text_id"]: row for row in trace},
            5,
        )
    )
    _write_jsonl(requests_path, requests)

    runs_path = bundle / "runs.csv"
    with runs_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        runs = list(reader)
        fieldnames = list(reader.fieldnames)
    for run in runs:
        run["semantic_index_sha256"] = semantic["semantic_index_sha256"]
        run["semantic_index_artifact_sha256"] = semantic_artifact_sha256
    carma = next(run for run in runs if run["run_id"] == "run-carma")
    carma.update(
        {
            "hits": "1",
            "misses": "19",
            "direct_negative_hits": "1",
            "labeled_negative_hits": "1",
            "cross_concept_hits": "1",
            "response_id_mismatches": "1",
            "semantic_guardrail_status": "FAIL",
            "admissions": "19",
            "evictions": "14",
        }
    )
    _rewrite_runs(runs_path, runs, fieldnames)
    outcomes_path = bundle / "outcome-latency.jsonl"
    outcome_rows = _outcome_latency_rows(requests, runs)
    _write_jsonl(outcomes_path, outcome_rows)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    semantic_declaration = {
        "path": "semantic-indexes/seed-1.json",
        "sha256": semantic_artifact_sha256,
        "bytes": semantic_path.stat().st_size,
        "rows": gate7_audit._row_count(semantic_path),
    }
    manifest["artifacts"]["semantic-indexes/seed-1.json"] = dict(
        semantic_declaration
    )
    manifest["semantic_index_artifacts"][
        "semantic-indexes/seed-1.json"
    ] = dict(semantic_declaration)
    manifest["trace_metadata"]["1"].update(
        {
            "semantic_index_sha256": semantic["semantic_index_sha256"],
            "semantic_index_canonical_row_count": semantic[
                "canonical_row_count"
            ],
            "semantic_index_counts": semantic["counts"],
        }
    )
    manifest["semantic_guardrail"] = {
        "status": "FAIL",
        "rule": (
            "fail on any direct or component-derived labeled-negative hit; "
            "pending on unlabeled cross-component hits; otherwise pass"
        ),
        "disjoint_hit_counts": {
            "same_concept_hits": 0,
            "direct_negative_hits": 1,
            "component_derived_negative_hits": 0,
            "unlabeled_cross_concept_hits": 0,
            "unresolved_provenance_hits": 0,
        },
        "labeled_negative_hits": 1,
        "does_not_change_gate7_system_adjudication": True,
    }
    _refresh_artifact(manifest, "requests.jsonl", requests_path, len(requests))
    _refresh_artifact(manifest, "runs.csv", runs_path, len(runs))
    _refresh_artifact(
        manifest, "outcome-latency.jsonl", outcomes_path, len(outcome_rows)
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "pass", result["errors"]
    assert result["semantic_guardrail_status"] == "FAIL"
    assert result["combined_disclosure"] == {
        "system_gate_status": "PASS",
        "semantic_guardrail_status": "FAIL",
        "system_gate_basis": "six frozen latency, throughput, and RSS bounds",
        "semantic_guardrail_basis": (
            "independently recomputed QQP same/direct/component/unlabeled hit relations"
        ),
        "interpretation": (
            "System Gate 7 and semantic quality are orthogonal; neither status "
            "overwrites the other."
        ),
    }


def test_rehashed_genesis_mutation_cannot_rewrite_v1_history(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    ledger_path = tmp_path / "attempt-ledger.jsonl"
    entries = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
    ]
    entries[0]["v1_preservation"]["archive_sha256"] = "0" * 64
    previous = None
    for sequence, entry in enumerate(entries, start=1):
        entry["sequence"] = sequence
        entry["previous_entry_sha256"] = previous
        entry.pop("entry_sha256", None)
        entry["entry_sha256"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        previous = entry["entry_sha256"]
    _write_jsonl(ledger_path, entries)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prefix = ledger_path.read_bytes()
    manifest["attempt_ledger"].update(
        {
            "prefix_rows": len(entries),
            "prefix_sha256": hashlib.sha256(prefix).hexdigest(),
            "prefix_bytes": len(prefix),
            "entry_sha256": entries[-1]["entry_sha256"],
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "attempt_ledger_genesis"
        and "preserved v1 invalid attempt" in error["message"]
        for error in result["errors"]
    )


def test_rehashed_parent_directory_cannot_escape_formal_attempt_root(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    ledger_path = tmp_path / "attempt-ledger.jsonl"
    entries = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
    ]
    entries[-1]["directory"] = ".."
    previous = None
    for sequence, entry in enumerate(entries, start=1):
        entry["sequence"] = sequence
        entry["previous_entry_sha256"] = previous
        entry.pop("entry_sha256", None)
        entry["entry_sha256"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        previous = entry["entry_sha256"]
    _write_jsonl(ledger_path, entries)

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prefix = ledger_path.read_bytes()
    manifest["attempt_ledger"].update(
        {
            "prefix_rows": len(entries),
            "prefix_sha256": hashlib.sha256(prefix).hexdigest(),
            "prefix_bytes": len(prefix),
            "entry_sha256": entries[-1]["entry_sha256"],
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert gate7_audit._valid_attempt_directory_name("..") is False
    assert any(
        error["code"] == "attempt_ledger"
        and "attempt identity is invalid" in error["message"]
        for error in result["errors"]
    )


@pytest.mark.parametrize(
    "directory", ["", ".", "..", "nested/attempt", "nested\\attempt", "bad\x00name"]
)
def test_attempt_directory_name_requires_one_safe_lexical_component(directory):
    assert gate7_audit._valid_attempt_directory_name(directory) is False


def test_cross_version_schema_and_v1_root_are_rejected(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "carma-gate7-onnx-v1"
    manifest["experiment_id"] = "gate7b-onnx-v1"
    manifest["attempt_policy"]["formal_root"] = gate7_audit.PINNED_V1_ROOT
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    codes = {error["code"] for error in result["errors"]}
    assert result["status"] == "invalid"
    assert {"manifest_schema", "manifest_experiment", "attempt_policy"} <= codes


def test_semantic_index_identity_requires_the_exact_v2_path(
    tmp_path, monkeypatch
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declaration = manifest["semantic_index_artifacts"].pop(
        "semantic-indexes/seed-1.json"
    )
    manifest["semantic_index_artifacts"]["1"] = declaration
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(
        error["code"] == "semantic_index_manifest"
        for error in result["errors"]
    )


@pytest.mark.parametrize(
    ("files_field", "digest_field", "file_name"),
    (
        ("model_files", "model_digest_sha256", "model.onnx"),
        ("tokenizer_files", "tokenizer_digest_sha256", "tokenizer.json"),
    ),
)
def test_self_consistent_model_or_tokenizer_file_map_mutation_is_rejected(
    tmp_path,
    monkeypatch,
    files_field,
    digest_field,
    file_name,
):
    bundle = _make_bundle(tmp_path, monkeypatch, finalize=False)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutated_hash = "0" * 64
    manifest["model"][files_field][file_name] = mutated_hash
    manifest["model"][digest_field] = hashlib.sha256(
        json.dumps(
            manifest["model"][files_field],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if files_field == "model_files":
        manifest["model"]["model_sha256"] = mutated_hash
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = gate7_audit.analyze_bundle(bundle, preterminal=True)
    assert result["status"] == "invalid"
    assert any(error["code"] == "frozen_model" for error in result["errors"])
