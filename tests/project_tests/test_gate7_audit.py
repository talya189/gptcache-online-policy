import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from benchmarks.carma import gate7_audit


ORDER = ("CARMA", "LFU", "LRU")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "rows": rows,
    }


def _attempts_sha256(attempts):
    payload = json.dumps(
        attempts, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _append_attempt_ledger(
    root, attempt_id, started_at_utc, directory, prior_attempts
):
    path = root / "attempt-ledger.jsonl"
    entries = (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if path.is_file()
        else []
    )
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
        "terminal_record": "manifest.json",
        "terminal_record_sha256": _sha256(manifest_path),
        "terminal_record_bytes": manifest_path.stat().st_size,
        "preterminal_report": gate7_audit.PRETERMINAL_REPORT_NAME,
        "preterminal_report_sha256": _sha256(report_path),
        "preterminal_report_bytes": report_path.stat().st_size,
        "preterminal_report_status": preterminal_report["status"],
        "preterminal_report_claimable": preterminal_report["claimable"],
        "auditor_identity": preterminal_report["auditor_identity"],
        "previous_entry_sha256": start["entry_sha256"],
    }
    entry["entry_sha256"] = hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    entries.append(entry)
    _write_jsonl(path, entries)
    return entry


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
                    "response_payload": "recorded-response:" + concept_id,
                }
            )
            seen.add(concept_id)
    return rows


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
                "raw_hit": False,
                "valid_hit": False,
                "false_hit": False,
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
    carma_total_ns=1_200_000,
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
            "request_buffer_rows_reserved": 20,
            "embedding_buffer_bytes_reserved": 20 * 768 * 4,
            "hits": 0,
            "misses": 20,
            "valid_hits": 0,
            "false_hits": 0,
            "stale_candidates": 0,
            "unknown_answers": 0,
            "max_cache_size": 5,
            "admissions": 20,
            "rejections": 0,
            "evictions": 15,
            "service_seconds": round(service_seconds, 9),
            "loop_seconds": loop_seconds,
            "loop_start_monotonic_ns": 1_000_000_000,
            "loop_end_monotonic_ns": 1_100_000_000,
            "throughput_qps": round(20 / loop_seconds, 6),
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
    ):
        artifacts[name] = {
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "rows": rows,
        }

    config = dict(frozen_config)
    config.update({"mode": "full", "fake_embedding": False})
    manifest = {
        "schema_version": gate7_audit.SCHEMA_VERSION,
        "experiment_id": gate7_audit.EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "kind": "prospective_gate7_followup",
        "started_at_utc": started_at_utc,
        "completed_at_utc": "2026-08-27T10:01:00+00:00",
        "historical_gate7_preserved": True,
        "historical_artifacts_modified": False,
        "precomputed_embeddings": False,
        "embedding_in_request_path": True,
        "gptcache_adapter_path": True,
        "actual_onnx": True,
        "formal_claimable_mode": True,
        "prior_attempt_exists": bool(prior_attempts),
        "prior_attempts": prior_attempts,
        "attempt_policy": {
            "formal_root": "artifacts/gate7-onnx-attempts",
            "directory": bundle.name,
            "eligibility": "first structurally valid complete attempt in the retained predecessor chain",
            "rerun_scope": "complete five-seed, three-policy matrix under a new attempt ID",
        },
        "contract": {
            "path": gate7_audit.DEFAULT_CONTRACT_PATH,
            "sha256": gate7_audit.PINNED_CONTRACT_SHA256,
            "bytes": 1,
        },
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
            name: {"sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(), "bytes": 1}
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
                **gate7_audit.FROZEN_SOURCE_HASHES,
            }
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
    manifest["source_identities"]["benchmarks/carma/gate7_audit.py"] = (
        gate7_audit._auditor_identity()
    )
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


def test_seed_pair_applies_simultaneous_frozen_bounds():
    lru = {
        "p95_request_total_ns": 2_000_000,
        "throughput_qps_unrounded": 100.0,
        "rss_peak_bytes": 100 * 1024 * 1024,
    }
    carma = {
        "p95_request_total_ns": 2_500_000,
        "throughput_qps_unrounded": 90.0,
        "rss_peak_bytes": 120 * 1024 * 1024,
    }
    result = gate7_audit.adjudicate_seed_pair(1, carma, lru)
    assert result["passes"] is True
    assert all(result["checks"].values())

    carma["p95_request_total_ns"] += 1
    result = gate7_audit.adjudicate_seed_pair(1, carma, lru)
    assert result["passes"] is False
    assert result["checks"]["p95_ratio"] is False
    assert result["checks"]["p95_delta"] is False


def test_complete_recomputed_fixture_passes(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path, monkeypatch)
    result = gate7_audit.analyze_bundle(bundle)
    assert result["status"] == "pass", result["errors"]
    assert result["claimable"] is True


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
            "valid_hit": True,
            "false_hit": False,
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
            "valid_hits": "1",
            "false_hits": "0",
            "unknown_answers": "1",
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
        error["code"] == "request_hit_semantics"
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
        if "maximum_observed_gap_ns" in error["context"]
    )


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
        "path": "benchmarks/carma/gate7_audit.py",
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
    manifest["source_identities"]["benchmarks/carma/gate7_audit.py"] = None
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
    manifest["source_identities"]["benchmarks/carma/gate7_audit.py"][
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
