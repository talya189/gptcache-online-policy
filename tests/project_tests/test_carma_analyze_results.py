"""Network-free artifact integrity and figure tests for CARMA analysis."""

import csv
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from benchmarks.carma.analyze_results import (
    AGGREGATE_FIELDS,
    ANALYSIS_SCHEMA,
    CAPACITY_RATE_DOMAIN_PERCENT,
    CAPACITY_RATE_TICKS_PERCENT,
    FULL_RUN_FIELDS,
    HOST_VERIFICATION_SCHEMA,
    INTEGRATION_FIELDS,
    MAX_CAPACITY_FIGURE_HEIGHT_IN,
    MAX_PUBLICATION_FIGURE_WIDTH_IN,
    MIN_REPORT_FONT_PT,
    MIN_SOURCE_FONT_PT,
    MOSS_FIELDS,
    PNG_DPI,
    PROJECT_ROOT,
    PUBLICATION_FIGURE_WIDTH_IN,
    REPORT_TEXT_WIDTH_IN,
    RUN_FIELDS,
    CONTAINER_REPRODUCIBILITY_SCHEMA,
    VALIDATION_FIELDS,
    VALIDATION_RUN_FIELDS,
    AnalysisError,
    _commit_binding,
    _current_git_head,
    _packaging_descendant_changes,
    _require_clean_worktree,
    _wilson_lower,
    analyze_results,
    main,
    sha256_file,
)


CONFIG_ID = "fixture-config"
WORKLOADS = ("stationary", "phase_shift", "pollution_scan")
POLICIES = ("LRU", "LFU", "CARMA")
TEST_SEEDS = tuple(range(20260901, 20260911))
FIXTURE_LOCK = (PROJECT_ROOT / "requirements-project.lock").read_text(
    encoding="utf-8"
)
FIXTURE_LOCK_PINS = re.findall(
    r"(?m)^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+) \\$",
    FIXTURE_LOCK,
)
FIXTURE_PACKAGES = "".join(
    "%s==%s\n" % (name, version) for name, version in FIXTURE_LOCK_PINS
)


def _git_head():
    if shutil.which("git") is None:
        return "1" * 40
    try:
        return subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "1" * 40


def _bind_analyzer_to_fixture_head(monkeypatch):
    head = _git_head()
    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._current_git_head",
        lambda: {
            "status": "available",
            "head_commit": head,
            "project_root": str(PROJECT_ROOT.resolve()),
        },
    )
    return head


@pytest.fixture(autouse=True)
def _fixture_clean_worktree(monkeypatch):
    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._require_clean_worktree",
        lambda label: None,
    )


def _write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _artifact_reference(root, path):
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
    }


def _ci_benchmark_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    trace_hash = "a" * 64
    run_id = "ci-fixture-carma-stationary"
    row = {field: "" for field in RUN_FIELDS}
    row.update(
        {
            "schema_version": "carma-benchmark-v2",
            "run_id": run_id,
            "policy": "CARMA",
            "workload": "stationary",
            "seed": 7,
            "trace_hash": trace_hash,
            "deterministic_digest": "d" * 64,
            "requests": 1,
            "capacity": 50,
            "raw_hits": 0,
            "valid_hits": 0,
            "false_hits": 0,
            "misses": 1,
            "false_misses": 0,
            "reuse_opportunities": 0,
            "valid_hit_rate": 0.0,
            "false_hit_rate": 0.0,
            "hit_precision": 1.0,
            "opportunity_recall": 1.0,
            "safe_token_saving_ratio": 0.0,
            "admissions": 1,
            "rejections": 0,
            "evictions": 0,
            "final_cache_entries": 1,
            "mean_latency_us": 0.0,
            "p50_latency_us": 0.0,
            "p95_latency_us": 0.0,
            "p99_latency_us": 0.0,
            "throughput_qps": 0.0,
            "policy_topics": 1,
            "policy_cells": 1,
            "policy_ghost_cells": 0,
        }
    )
    _write_csv(root / "runs.csv", RUN_FIELDS, [row])
    (root / "requests.jsonl").write_text(
        '{"request_index":0,"workload":"stationary"}\n', encoding="utf-8"
    )
    _write_json(
        root / "manifest.json",
        {
            "schema_version": "carma-benchmark-v2",
            "config": {"measure_latency": False, "seed": 7},
            "trace_hashes": {"stationary": trace_hash},
            "run_ids": [run_id],
            "timing_is_measured": False,
            "determinism_scope": "fixture non-timing fields",
        },
    )
    return root


def _host_verification_fixture(root, source_commit=None, lock_text=FIXTURE_LOCK):
    if source_commit is None:
        source_commit = _git_head()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "requirements-project.lock"
    lock.write_text(lock_text, encoding="utf-8")
    install_log = root / "host-install.log"
    install_log.write_text(
        "No broken requirements found.\n[install] pip check PASS\n",
        encoding="utf-8",
    )
    installed_packages = root / "host-packages.txt"
    installed_packages.write_text(
        FIXTURE_PACKAGES + "gptcache==0.1.44\n", encoding="utf-8"
    )
    log = root / "host-verification.log"
    log.write_text("fixture test output\n[verify] PASS\n", encoding="utf-8")
    benchmark = _ci_benchmark_fixture(root / "benchmark")
    evidence = {
        "schema_version": HOST_VERIFICATION_SCHEMA,
        "status": "pass",
        "source_commit": source_commit,
        "baseline_ancestor_verified": True,
        "python": {"implementation": "CPython", "version": "3.12.13"},
        "platform": "fixture-host",
        "dependency_lock": {
            **_artifact_reference(root, lock),
            "require_hashes": True,
            "only_binary": True,
            "index_url": "https://pypi.org/simple",
        },
        "environment_install_log": _artifact_reference(root, install_log),
        "installed_packages": _artifact_reference(root, installed_packages),
        "verification_log": _artifact_reference(root, log),
        "benchmark_artifacts": {
            name: _artifact_reference(root, benchmark / name)
            for name in ("manifest.json", "requests.jsonl", "runs.csv")
        },
    }
    path = root / "host-verification.json"
    _write_json(path, evidence)
    return path


def _container_reproducibility_fixture(
    root, source_commit=None, lock_text=FIXTURE_LOCK
):
    if source_commit is None:
        source_commit = _git_head()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "requirements-project.lock"
    lock.write_text(lock_text, encoding="utf-8")
    build_log = root / "docker-build.log"
    build_log.write_text("fixture image build\n", encoding="utf-8")
    image_id = "sha256:" + "2" * 64
    image_inspect = root / "container-image-inspect.json"
    _write_json(
        image_inspect,
        [
            {
                "Id": image_id,
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
        ],
    )
    runs = []
    for index in (1, 2):
        label = "docker-run-%d" % index
        benchmark = _ci_benchmark_fixture(root / label / "benchmark")
        raw_log = root / (label + ".log")
        raw_log.write_text(
            "fixture container output\n1 passed in %ss\n[verify] PASS\n"
            % ("1.25" if index == 1 else "9"),
            encoding="utf-8",
        )
        non_timing_log = root / (label + ".nontiming.log")
        non_timing_log.write_text(
            "fixture container output\n1 passed in <elapsed>\n[verify] PASS\n",
            encoding="utf-8",
        )
        hash_list = root / (label + ".sha256")
        hash_list.write_text(
            "".join(
                "%s  %s\n" % (sha256_file(benchmark / name), name)
                for name in ("manifest.json", "requests.jsonl", "runs.csv")
            ),
            encoding="utf-8",
        )
        container_inspect = root / (label + ".inspect.json")
        _write_json(
            container_inspect,
            [
                {
                    "Id": str(index + 3) * 64,
                    "Image": image_id,
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
                            "Source": str((root / label).resolve()),
                            "Destination": "/artifacts",
                            "RW": True,
                        }
                    ],
                }
            ],
        )
        runs.append(
            {
                "label": label,
                "artifact_mount_source": str((root / label).resolve()),
                "raw_log": _artifact_reference(root, raw_log),
                "non_timing_log": _artifact_reference(root, non_timing_log),
                "hash_list": _artifact_reference(root, hash_list),
                "container_inspect": _artifact_reference(root, container_inspect),
                "benchmark_artifacts": {
                    name: _artifact_reference(root, benchmark / name)
                    for name in ("manifest.json", "requests.jsonl", "runs.csv")
                },
            }
        )
    evidence = {
        "schema_version": CONTAINER_REPRODUCIBILITY_SCHEMA,
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
        "dependency_lock": _artifact_reference(root, lock),
        "build_log": _artifact_reference(root, build_log),
        "image_inspect": _artifact_reference(root, image_inspect),
        "comparisons": {
            "non_timing_logs_identical": True,
            "hash_lists_identical": True,
            "manifest_json_identical": True,
            "requests_jsonl_identical": True,
            "runs_csv_identical": True,
        },
        "runs": runs,
    }
    path = root / "container-reproducibility.json"
    _write_json(path, evidence)
    return path


def _trace(stage, capacity, workload, seed):
    import hashlib

    identity = "%s|%s|%s|%s" % (stage, capacity, workload, seed)
    return hashlib.sha256(identity.encode()).hexdigest()


def _metrics(workload, policy, capacity=100):
    base = {
        "stationary": {"LRU": 0.50, "LFU": 0.52, "CARMA": 0.53},
        "phase_shift": {"LRU": 0.50, "LFU": 0.51, "CARMA": 0.54},
        "pollution_scan": {"LRU": 0.50, "LFU": 0.51, "CARMA": 0.53},
    }[workload][policy]
    if capacity != 100:
        base = max(0.0, base + (capacity - 100) / 2000)
    scan = {
        "LRU": 0.3000,
        "LFU": 0.3100,
        "CARMA": 0.3101,
    }[policy]
    return {
        "valid_hit_rate": base,
        "false_hit_rate": 0.0,
        "opportunity_recall": min(1.0, base + 0.20),
        "safe_token_saving_ratio": {
            "LRU": 0.40,
            "LFU": 0.41,
            "CARMA": 0.412,
        }[policy],
        "scan_return_valid_hit_rate": scan if workload == "pollution_scan" else None,
        "shift_recovery_lag_mean_requests": 10.0 if workload == "phase_shift" else None,
    }


def _full_run(stage, workload, policy, seed, capacity=100):
    metrics = _metrics(workload, policy, capacity)
    requests = 10_000
    valid_hits = round(metrics["valid_hit_rate"] * requests)
    false_hits = 0
    raw_hits = valid_hits + false_hits
    row = {field: "" for field in FULL_RUN_FIELDS}
    row.update(
        {
            "stage": stage,
            "config_id": CONFIG_ID,
            "topic_threshold": 0.7,
            "cell_threshold": 0.97,
            "demand_half_life": 500,
            "quota_strength": 1.0,
            "schema_version": "carma-benchmark-v2",
            "run_id": "%s-%s-%s-%s-%s" % (
                stage,
                workload,
                policy,
                capacity,
                seed,
            ),
            "policy": policy,
            "workload": workload,
            "seed": seed,
            "trace_hash": _trace(stage, capacity, workload, seed),
            "deterministic_digest": "d" * 64,
            "requests": requests,
            "capacity": capacity,
            "raw_hits": raw_hits,
            "valid_hits": valid_hits,
            "false_hits": false_hits,
            "misses": requests - raw_hits,
            "false_misses": 0,
            "reuse_opportunities": 8000,
            "valid_hit_rate": metrics["valid_hit_rate"],
            "false_hit_rate": 0.0,
            "hit_precision": 1.0,
            "opportunity_recall": metrics["opportunity_recall"],
            "safe_token_saving_ratio": metrics["safe_token_saving_ratio"],
            "admissions": requests - raw_hits,
            "rejections": 0,
            "evictions": max(0, requests - raw_hits - capacity),
            "final_cache_entries": capacity,
            "mean_latency_us": 0.0,
            "p50_latency_us": 0.0,
            "p95_latency_us": 0.0,
            "p99_latency_us": 0.0,
            "throughput_qps": 0.0,
            "policy_topics": 3 if policy == "CARMA" else 0,
            "policy_cells": 6 if policy == "CARMA" else 0,
            "policy_ghost_cells": 0,
            "scan_return_requests": 10_000 if workload == "pollution_scan" else "",
            "scan_return_valid_hits": (
                round(metrics["scan_return_valid_hit_rate"] * 10_000)
                if workload == "pollution_scan"
                else ""
            ),
            "scan_return_false_hits": 0 if workload == "pollution_scan" else "",
            "scan_return_reuse_opportunities": (
                8000 if workload == "pollution_scan" else ""
            ),
            "scan_return_valid_hit_rate": (
                metrics["scan_return_valid_hit_rate"]
                if workload == "pollution_scan"
                else ""
            ),
            "scan_return_false_hit_rate": 0.0 if workload == "pollution_scan" else "",
            "scan_return_opportunity_recall": (
                metrics["scan_return_valid_hit_rate"]
                if workload == "pollution_scan"
                else ""
            ),
            "shift_recovery_lag_mean_requests": (
                metrics["shift_recovery_lag_mean_requests"]
                if workload == "phase_shift"
                else ""
            ),
            "shift_recovery_lag_max_requests": 12 if workload == "phase_shift" else "",
            "shift_recovery_failures": 0 if workload == "phase_shift" else "",
            "shift_recovery_transitions_json": "[]" if workload == "phase_shift" else "",
        }
    )
    return row


def _aggregate_row(
    row_type,
    workload,
    metric,
    policy="CARMA",
    comparator="",
    capacity=100,
    n=10,
    mean="",
    low="",
    high="",
    delta="",
    delta_low="",
    delta_high="",
    p_holm="",
    family="exploratory_unadjusted",
    stage="primary_test",
):
    return {
        "stage": stage,
        "row_type": row_type,
        "workload": workload,
        "capacity": capacity,
        "metric": metric,
        "policy": policy,
        "comparator": comparator,
        "n": n,
        "mean": mean,
        "ci_low": low,
        "ci_high": high,
        "delta_mean": delta,
        "delta_ci_low": delta_low,
        "delta_ci_high": delta_high,
        "wilcoxon_statistic": 0 if row_type == "comparison" else "",
        "p_value": p_holm if row_type == "comparison" else "",
        "p_holm": p_holm,
        "nonzero_pairs": n if row_type == "comparison" else "",
        "rank_biserial": 1.0 if row_type == "comparison" else "",
        "test_family": family if row_type == "comparison" else "",
    }


def _comparison_values(workload, metric, comparator):
    left = _metrics(workload, "CARMA")[metric]
    if comparator == "BEST_BASELINE":
        right = max(
            _metrics(workload, "LRU")[metric],
            _metrics(workload, "LFU")[metric],
        )
    else:
        right = _metrics(workload, comparator)[metric]
    return left - right


def _full_fixture(root):
    root.mkdir()
    validation = {
        field: "" for field in VALIDATION_FIELDS
    }
    validation.update(
        {
            "config_id": CONFIG_ID,
            "topic_threshold": 0.7,
            "cell_threshold": 0.97,
            "demand_half_life": 500,
            "quota_strength": 1.0,
            "run_count": 3,
            "mean_valid_hit_rate": 0.5,
            "normalized_mean_valid_hit_rate": 1.0,
            "mean_false_hit_rate": 0,
            "total_false_hits": 0,
            "mean_opportunity_recall": 0.7,
            "mean_safe_token_saving_ratio": 0.4,
            "passes_zero_false_hits": True,
            "selected": True,
        }
    )
    _write_csv(root / "validation.csv", VALIDATION_FIELDS, [validation])

    validation_runs = []
    for workload in WORKLOADS:
        source = _full_run("validation", workload, "CARMA", 20260825)
        row = {field: "" for field in VALIDATION_RUN_FIELDS}
        row.update({field: source[field] for field in RUN_FIELDS})
        row.update(
            {
                "config_id": CONFIG_ID,
                "topic_threshold": 0.7,
                "cell_threshold": 0.97,
                "demand_half_life": 500,
                "quota_strength": 1.0,
                "normalized_valid_hit_rate": 1.0,
                "config_total_false_hits": 0,
                "config_passes_zero_false_hits": True,
                "config_selected": True,
            }
        )
        validation_runs.append(row)
    _write_csv(
        root / "validation_runs.csv", VALIDATION_RUN_FIELDS, validation_runs
    )

    runs = []
    for seed in TEST_SEEDS:
        for workload in WORKLOADS:
            for policy in POLICIES:
                runs.append(_full_run("primary_test", workload, policy, seed))
    for capacity in (20, 50, 200):
        for seed in TEST_SEEDS[:5]:
            for workload in WORKLOADS:
                for policy in POLICIES:
                    runs.append(
                        _full_run(
                            "capacity_sweep", workload, policy, seed, capacity
                        )
                    )
    _write_csv(root / "runs.csv", FULL_RUN_FIELDS, runs)

    aggregate = []
    for workload in WORKLOADS:
        for policy in POLICIES:
            mean = _metrics(workload, policy)["valid_hit_rate"]
            aggregate.append(
                _aggregate_row(
                    "summary",
                    workload,
                    "valid_hit_rate",
                    policy=policy,
                    mean=mean,
                    low=mean - 0.005,
                    high=mean + 0.005,
                )
            )
        for comparator in ("LRU", "LFU", "BEST_BASELINE"):
            delta = _comparison_values(workload, "valid_hit_rate", comparator)
            if workload == "phase_shift" and comparator == "BEST_BASELINE":
                low, high, p = 0.02, 0.04, 0.03
            elif workload == "stationary" and comparator == "BEST_BASELINE":
                low, high, p = 0.005, 0.015, 0.03
            else:
                low, high, p = delta - 0.005, delta + 0.005, 0.03
            aggregate.append(
                _aggregate_row(
                    "comparison",
                    workload,
                    "valid_hit_rate",
                    comparator=comparator,
                    delta=delta,
                    delta_low=low,
                    delta_high=high,
                    p_holm=p,
                    family="primary_vhr",
                )
            )
        for metric in ("safe_token_saving_ratio", "false_hit_rate"):
            delta = _comparison_values(workload, metric, "BEST_BASELINE")
            aggregate.append(
                _aggregate_row(
                    "comparison",
                    workload,
                    metric,
                    comparator="BEST_BASELINE",
                    delta=delta,
                    delta_low=-0.001 if metric == "safe_token_saving_ratio" else 0,
                    delta_high=0.005 if metric == "safe_token_saving_ratio" else 0,
                )
            )
    for policy in POLICIES:
        mean = _metrics("pollution_scan", policy)["scan_return_valid_hit_rate"]
        aggregate.append(
            _aggregate_row(
                "summary",
                "pollution_scan",
                "scan_return_valid_hit_rate",
                policy=policy,
                mean=mean,
                low=mean - 0.0001,
                high=mean + 0.0001,
            )
        )
    for comparator in ("LRU", "LFU", "BEST_BASELINE"):
        delta = _comparison_values(
            "pollution_scan", "scan_return_valid_hit_rate", comparator
        )
        aggregate.append(
            _aggregate_row(
                "comparison",
                "pollution_scan",
                "scan_return_valid_hit_rate",
                comparator=comparator,
                delta=delta,
                delta_low=0 if comparator == "BEST_BASELINE" else delta - 0.0001,
                delta_high=0.0002 if comparator == "BEST_BASELINE" else delta + 0.0001,
                p_holm=1.0 if comparator == "BEST_BASELINE" else 0.03,
                family="primary_vhr",
            )
        )
    for capacity in (20, 50, 200):
        for workload in WORKLOADS:
            for policy in POLICIES:
                mean = _metrics(workload, policy, capacity)["valid_hit_rate"]
                aggregate.append(
                    _aggregate_row(
                        "summary",
                        workload,
                        "valid_hit_rate",
                        policy=policy,
                        capacity=capacity,
                        n=5,
                        mean=mean,
                        low=mean - 0.005,
                        high=mean + 0.005,
                        stage="capacity_sweep",
                    )
                )
    _write_csv(root / "aggregate.csv", AGGREGATE_FIELDS, aggregate)

    trace_hashes = {}
    for row in runs:
        key = "%s/c%s/%s/%s" % (
            row["stage"],
            row["capacity"],
            row["workload"],
            row["seed"],
        )
        trace_hashes[key] = row["trace_hash"]
    gates = {
        "status": "inferential",
        "claimable": True,
        "gate_3_phase_shift_vhr": {
            "observed_delta": 0.03,
            "observed_ci_low": 0.02,
            "observed_ci_high": 0.04,
            "observed_holm_p": 0.03,
            "passes": True,
        },
        "gate_4_scan_return_vhr": {
            "observed_delta": 0.0001,
            "observed_ci_low": 0,
            "observed_ci_high": 0.0002,
            "observed_holm_p": 1.0,
            "passes": False,
        },
        "gate_5_stationary_nonregression": {
            "observed_delta": 0.01,
            "observed_ci_low": 0.005,
            "observed_ci_high": 0.015,
            "passes": True,
        },
        "gate_6_token_saving_nonregression": {
            "workloads": {
                workload: {
                    "observed_delta": 0.002,
                    "observed_ci_low": -0.001,
                    "observed_ci_high": 0.005,
                }
                for workload in WORKLOADS
            },
            "passes": True,
        },
    }
    metadata = {
        "schema_version": "carma-full-experiment-v2",
        "mode": "full",
        "inferential_test_run": True,
        "validation_grid": {"configuration_count": 1},
        "validation_seeds": [20260825],
        "test_seeds": list(TEST_SEEDS),
        "workloads": list(WORKLOADS),
        "test_capacity": 100,
        "test_requests": 10_000,
        "selected_config_id": CONFIG_ID,
        "selection_uses_test_results": False,
        "seeds_are_disjoint": True,
        "capacity_sweep_included": True,
        "capacity_sweep_capacities": [20, 50, 200],
        "trace_hashes": trace_hashes,
        "synthetic_success_gates": gates,
        "artifacts": {
            name: sha256_file(root / name)
            for name in (
                "validation.csv",
                "validation_runs.csv",
                "runs.csv",
                "aggregate.csv",
            )
        },
    }
    _write_json(root / "metadata.json", metadata)
    return root


def _integration_fixture(root, seed):
    root.mkdir(parents=True)
    rows = []
    values = {
        "LRU": (1000, 10, 100, 100 * 1024 * 1024),
        "LFU": (950, 11, 102, 102 * 1024 * 1024),
        "CARMA": (1100, 50, 95, 110 * 1024 * 1024),
    }
    for policy, (p95, policy_p95, qps, rss) in values.items():
        row = {field: 0 for field in INTEGRATION_FIELDS}
        row.update(
            {
                "schema_version": "carma-sqlite-faiss-v1",
                "run_id": "%s-%s" % (seed, policy),
                "policy": policy,
                "mode": "full",
                "workload": "pollution_scan",
                "seed": seed,
                "trace_hash": "a" * 64,
                "requests": 3000,
                "capacity": 100,
                "embedding_dimension": 834,
                "top_k": 1,
                "hit_threshold": 0.97,
                "hits": 1500,
                "misses": 1500,
                "valid_hits": 1500,
                "false_hits": 0,
                "stale_candidates": 0,
                "reuse_opportunities": 1800,
                "hit_rate": 0.5,
                "opportunity_recall": 0.8,
                "safe_token_saving_ratio": 0.4,
                "admissions": 1500,
                "rejections": 0,
                "evictions": 1400,
                "final_scalar_count": 100,
                "final_vector_count": 100,
                "deleted_scalar_count": 1400,
                "verified_entries": 100,
                "wall_seconds": 30,
                "throughput_qps": qps,
                "cpu_user_seconds": 20,
                "cpu_system_seconds": 5,
                "cpu_percent_one_core": 83,
                "rss_start_bytes": 90 * 1024 * 1024,
                "rss_end_bytes": rss,
                "rss_peak_sampled_bytes": rss,
                "rss_delta_bytes": rss - 90 * 1024 * 1024,
                "io_counters_available": False,
                "total_p95_us": p95,
                "policy_p95_us": policy_p95,
            }
        )
        rows.append(row)
    _write_csv(root / "runs.csv", INTEGRATION_FIELDS, rows)
    manifest = {
        "schema_version": "carma-sqlite-faiss-v1",
        "git": {"head_commit": "integration-fixture", "worktree_dirty": False},
        "config": {
            "mode": "full",
            "workload": "pollution_scan",
            "seed": seed,
            "requests": 3000,
            "capacity": 100,
            "hit_threshold": 0.97,
        },
        "policies": list(POLICIES),
        "trace_hash": "a" * 64,
        "identical_trace_verified": True,
        "artifacts": {
            "runs.csv": {
                "sha256": sha256_file(root / "runs.csv"),
                "rows": 3,
            }
        },
    }
    _write_json(root / "manifest.json", manifest)
    return root


def _qqp_fixture(path):
    tp, fp, tn, fn = 1000, 0, 1000, 100
    metrics = {
        "threshold": 0.97,
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "precision": 1.0,
        "recall": tp / (tp + fn),
        "false_positive_rate": 0.0,
        "wilson_precision_lower_one_sided_95": _wilson_lower(tp, tp + fp),
    }
    result = {
        "schema_version": "carma-qqp-v1",
        "status": "ok",
        "selected_threshold": 0.97,
        "selection_rule": "fixture",
        "calibration": metrics,
        "test": metrics,
        "calibration_pairs": tp + fp + tn + fn,
        "test_pairs": tp + fp + tn + fn,
    }
    _write_json(path, result)
    return path


def _moss_fixture(root):
    root.mkdir()
    row = {field: 0 for field in MOSS_FIELDS}
    row.update(
        {
            "schema_version": "carma-moss-recorded-response-v2",
            "run_id": "moss-fixture",
            "policy": "RECORDED_RESPONSE_EXACT_LRU",
            "workload": "moss-recorded-response",
            "trace_hash": "b" * 64,
            "requests": 6,
            "capacity": 3,
            "misses": 6,
            "recorded_response_replays": 6,
        }
    )
    _write_csv(root / "runs.csv", MOSS_FIELDS, [row])
    (root / "pool.jsonl").write_text("{\"fixture\":true}\n", encoding="utf-8")
    (root / "requests.jsonl").write_text(
        "".join("{\"request_index\":%d}\n" % index for index in range(6)),
        encoding="utf-8",
    )
    irregularities = {
        "declared_turn_count_mismatches": 0,
        "negative_sentinel_conversation_id_rows": 0,
    }
    prepare = {
        "schema_version": "carma-moss-recorded-response-v2",
        "stage": "prepare",
        "source_irregularities": irregularities,
    }
    _write_json(root / "prepare-manifest.json", prepare)
    manifest = {
        "schema_version": "carma-moss-recorded-response-v2",
        "stage": "replay",
        "no_live_llm_or_api": True,
        "source": {
            "source_kind": "test-fixture",
            "revision": "fixture-v1",
        },
        "prepared_manifest_sha256": sha256_file(root / "prepare-manifest.json"),
        "pool_sha256": sha256_file(root / "pool.jsonl"),
        "preparation": {"source_irregularities": irregularities},
        "artifacts": {
            name: sha256_file(root / name)
            for name in (
                "pool.jsonl",
                "prepare-manifest.json",
                "requests.jsonl",
                "runs.csv",
            )
        },
    }
    _write_json(root / "manifest.json", manifest)
    return root


def _complete_inputs(tmp_path):
    full = _full_fixture(tmp_path / "full")
    integrations = [
        _integration_fixture(tmp_path / ("integration-%s" % seed), seed)
        for seed in TEST_SEEDS[:5]
    ]
    qqp = _qqp_fixture(tmp_path / "qqp" / "evaluation" / "result.json")
    moss = _moss_fixture(tmp_path / "moss")
    return full, integrations, qqp, moss


def test_complete_fixture_preserves_failed_gate_and_renders(tmp_path):
    full, integrations, qqp, moss = _complete_inputs(tmp_path)
    output = tmp_path / "analysis"
    result = analyze_results(output, full, integrations, qqp, moss)

    assert result["schema_version"] == ANALYSIS_SCHEMA
    assert result["overall_status"] == "fail"
    assert result["gates"]["gate_3_phase_shift_vhr"]["status"] == "pass"
    assert result["gates"]["gate_4_scan_return_vhr"]["status"] == "fail"
    assert result["gates"]["gate_4_scan_return_vhr"]["passes"] is False
    assert result["gates"]["gate_7_system_overhead"]["status"] == "pending"
    diagnostics = result["gates"]["gate_7_system_overhead"]["diagnostic_by_seed"]
    assert len(diagnostics) == 5
    assert all(row["all_individual_checks_pass"] for row in diagnostics)
    assert result["gates"]["gate_7_system_overhead"][
        "conservative_require_every_seed_interpretation"
    ] == {"status": "pass", "claimable_as_frozen_gate": False}
    assert result["supplemental_checks"]["moss_recorded_response"]["status"] == "pending"
    for stem in ("vhr-deltas", "scan-return", "capacity-curve", "latency-resources"):
        assert result["figures"][stem]["status"] == "rendered"
        assert (output / (stem + ".svg")).stat().st_size > 1000
        assert (output / (stem + ".png")).stat().st_size > 1000
    scan_svg = (output / "scan-return.svg").read_text(encoding="utf-8")
    assert "Gate 4: +5.0 pp" in scan_svg
    assert "Gate 4: FAIL" in scan_svg


def test_analysis_outputs_are_byte_deterministic(tmp_path):
    full, integrations, qqp, moss = _complete_inputs(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    analyze_results(first, full, integrations, qqp, moss)
    analyze_results(second, full, integrations, qqp, moss)
    first_names = sorted(path.name for path in first.iterdir())
    assert first_names == sorted(path.name for path in second.iterdir())
    for name in first_names:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_publication_figures_enforce_final_size_typography_contract(tmp_path):
    assert PUBLICATION_FIGURE_WIDTH_IN <= MAX_PUBLICATION_FIGURE_WIDTH_IN
    assert (
        MIN_SOURCE_FONT_PT
        * min(1.0, REPORT_TEXT_WIDTH_IN / PUBLICATION_FIGURE_WIDTH_IN)
        >= MIN_REPORT_FONT_PT
    )

    full, integrations, qqp, moss = _complete_inputs(tmp_path)
    output = tmp_path / "analysis"
    result = analyze_results(output, full, integrations, qqp, moss)
    for stem in ("vhr-deltas", "scan-return", "capacity-curve", "latency-resources"):
        contract = result["figures"][stem]["publication_contract"]
        assert contract["source_width_in"] <= MAX_PUBLICATION_FIGURE_WIDTH_IN
        assert contract["minimum_source_font_pt"] >= MIN_SOURCE_FONT_PT
        assert (
            contract["minimum_font_at_report_width_pt"]
            >= MIN_REPORT_FONT_PT
        )
        png_header = (output / (stem + ".png")).read_bytes()[:24]
        assert int.from_bytes(png_header[16:20], "big") == round(
            PUBLICATION_FIGURE_WIDTH_IN * PNG_DPI
        )
    assert result["figures"]["capacity-curve"]["publication_contract"][
        "source_height_in"
    ] <= MAX_CAPACITY_FIGURE_HEIGHT_IN
    assert result["figures"]["capacity-curve"]["value_axis"] == {
        "metric": "valid_hit_rate",
        "unit": "percent",
        "domain": list(CAPACITY_RATE_DOMAIN_PERCENT),
        "ticks": list(CAPACITY_RATE_TICKS_PERCENT),
        "shared_across_panels": True,
    }


def test_missing_sources_are_pending_without_placeholder_figures(tmp_path):
    output = tmp_path / "analysis"
    result = analyze_results(output)
    assert result["overall_status"] == "pending"
    assert all(gate["status"] == "pending" for gate in result["gates"].values())
    assert all(item["status"] == "pending" for item in result["figures"].values())
    assert sorted(path.name for path in output.iterdir()) == [
        "gate-audit.json",
        "summary.csv",
    ]


def test_tampered_full_artifact_is_rejected_before_analysis(tmp_path):
    full = _full_fixture(tmp_path / "full")
    with (full / "aggregate.csv").open("a", encoding="utf-8") as output:
        output.write("tampered\n")
    with pytest.raises(AnalysisError, match="aggregate.csv SHA-256 mismatch"):
        analyze_results(tmp_path / "analysis", full_dir=full)


def test_stale_full_schema_is_labeled_and_not_zero_filled(tmp_path):
    full = tmp_path / "legacy"
    full.mkdir()
    _write_json(full / "metadata.json", {"schema_version": "carma-full-experiment-v1"})
    result = analyze_results(tmp_path / "analysis", full_dir=full)
    assert result["sources"]["full"]["status"] == "stale"
    assert result["gates"]["gate_4_scan_return_vhr"]["status"] == "pending"
    assert result["figures"]["scan-return"]["status"] == "pending"


def test_interrupted_staging_directory_is_not_ingested(tmp_path):
    full = tmp_path / "staging"
    full.mkdir()
    (full / "validation.csv").write_text("partial", encoding="utf-8")
    result = analyze_results(tmp_path / "analysis", full_dir=full)
    assert result["sources"]["full"]["status"] == "incomplete"
    assert "metadata.json" in result["sources"]["full"]["reason"]


def test_current_git_head_matches_repository_when_available():
    if shutil.which("git") is None or not (PROJECT_ROOT / ".git").exists():
        pytest.skip("Git metadata is intentionally absent from the container")
    observed = _current_git_head()
    assert observed["status"] == "available"
    assert observed["head_commit"] == _git_head()
    assert Path(observed["project_root"]) == PROJECT_ROOT.resolve()


def test_commit_binding_requires_clean_worktree_even_at_exact_head(monkeypatch):
    current = "a" * 40

    def reject_dirty(label):
        raise AnalysisError("%s binding requires a clean worktree" % label)

    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._require_clean_worktree",
        reject_dirty,
    )
    with pytest.raises(AnalysisError, match="requires a clean worktree"):
        _commit_binding(
            current,
            "fixture evidence",
            {"status": "available", "head_commit": current},
        )


def test_worktree_check_rejects_uncommitted_paths(monkeypatch):
    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._git_bytes",
        lambda command, label: b"?? untracked.txt\0",
    )
    with pytest.raises(AnalysisError, match="requires a clean worktree"):
        _require_clean_worktree("fixture evidence")


def test_packaging_descendant_parser_allows_only_added_or_modified_paths(
    monkeypatch,
):
    source = "a" * 40
    current = "b" * 40

    def retained_git_state(command, label):
        if "rev-list" in command:
            return (current + " " + source + "\n").encode("ascii")
        if "diff" in command:
            return (
                b"M\0README.md\0"
                b"A\0docs/project/report.pdf\0"
                b"A\0artifacts/samples/verification/evidence.json\0"
            )
        raise AssertionError("unexpected Git command: %r" % command)

    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._git_bytes", retained_git_state
    )
    assert _packaging_descendant_changes(
        source, current, "fixture evidence"
    ) == [
        {"status": "M", "path": "README.md"},
        {"status": "A", "path": "docs/project/report.pdf"},
        {
            "status": "A",
            "path": "artifacts/samples/verification/evidence.json",
        },
    ]


@pytest.mark.parametrize(
    "raw_changes",
    (
        b"D\0docs/project/removed.md\0",
        b"R100\0docs/project/old.md\0docs/project/new.md\0",
        b"T\0artifacts/samples/verification/evidence.json\0",
        b"M\0benchmarks/carma/analyze_results.py\0",
        b"",
    ),
)
def test_packaging_descendant_parser_rejects_unsafe_changes(
    tmp_path, monkeypatch, raw_changes
):
    source = "a" * 40
    current = "b" * 40

    def retained_git_state(command, label):
        if "rev-list" in command:
            return (current + " " + source + "\n").encode("ascii")
        if "diff" in command:
            return raw_changes
        raise AssertionError("unexpected Git command: %r" % command)

    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._git_bytes", retained_git_state
    )
    with pytest.raises(
        AnalysisError,
        match="unsupported descendant changes|contains no packaging changes",
    ):
        _packaging_descendant_changes(source, current, "fixture evidence")


@pytest.mark.parametrize(
    "parents",
    (
        ["b" * 40, "c" * 40],
        ["b" * 40, "a" * 40, "c" * 40],
    ),
)
def test_packaging_descendant_requires_direct_sole_parent(
    monkeypatch, parents
):
    source = "a" * 40
    current = "b" * 40
    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._git_bytes",
        lambda command, label: (" ".join(parents) + "\n").encode("ascii"),
    )
    with pytest.raises(AnalysisError, match="direct sole parent"):
        _packaging_descendant_changes(source, current, "fixture evidence")


def test_packaging_descendant_evidence_is_claimable(tmp_path, monkeypatch):
    source = "a" * 40
    current = "b" * 40
    changes = [
        {"status": "A", "path": "artifacts/samples/verification/evidence.json"},
        {"status": "M", "path": "README.md"},
    ]
    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._current_git_head",
        lambda: {
            "status": "available",
            "head_commit": current,
            "project_root": str(PROJECT_ROOT.resolve()),
        },
    )
    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._packaging_descendant_changes",
        lambda source_commit, current_commit, label: changes,
    )
    host = _host_verification_fixture(
        tmp_path / "host", source_commit=source
    )
    container = _container_reproducibility_fixture(
        tmp_path / "container", source_commit=source
    )

    result = analyze_results(
        tmp_path / "analysis",
        host_verification=host,
        container_reproducibility=container,
    )

    expected_binding = {
        "status": "verified_packaging_descendant",
        "evidence_source_commit": source,
        "current_git_head": current,
        "exact_match": False,
        "changed_paths": [item["path"] for item in changes],
        "changed_path_status": changes,
    }
    for name in ("host_verification", "container_reproducibility"):
        assert result["sources"][name]["commit_binding"] == expected_binding
    assert result["gates"]["gate_1_correctness"]["claimable"] is True
    assert result["gates"]["gate_8_reproducibility"]["claimable"] is True


def test_verified_host_and_container_evidence_pass_gates_1_and_8(
    tmp_path, monkeypatch
):
    assert ANALYSIS_SCHEMA == "carma-post-analysis-v2"
    head = _bind_analyzer_to_fixture_head(monkeypatch)
    host = _host_verification_fixture(tmp_path / "host")
    container = _container_reproducibility_fixture(tmp_path / "container")

    result = analyze_results(
        tmp_path / "analysis",
        host_verification=host,
        container_reproducibility=container,
    )

    assert result["sources"]["host_verification"]["status"] == "verified"
    assert result["sources"]["container_reproducibility"]["status"] == "verified"
    assert result["sources"]["host_verification"]["commit_binding"] == {
        "status": "verified",
        "evidence_source_commit": head,
        "current_git_head": head,
        "exact_match": True,
    }
    assert result["sources"]["container_reproducibility"][
        "commit_binding"
    ] == {
        "status": "verified",
        "evidence_source_commit": head,
        "current_git_head": head,
        "exact_match": True,
    }
    assert result["sources"]["host_verification"]["benchmark_validation"] == {
        "schema_version": "carma-benchmark-v2",
        "timing_is_measured": False,
        "request_rows": 1,
        "run_rows": 1,
        "run_ids": ["ci-fixture-carma-stationary"],
        "trace_hashes": {"stationary": "a" * 64},
    }
    host_report = result["sources"]["host_verification"]
    current_lock_sha256 = sha256_file(PROJECT_ROOT / "requirements-project.lock")
    assert host_report["current_dependency_lock_sha256"] == current_lock_sha256
    assert host_report["environment_install_log"]["path"] == "host-install.log"
    assert host_report["installed_packages"]["path"] == "host-packages.txt"
    assert host_report["environment_validation"] == {
        "install_sentinel": "[install] pip check PASS",
        "locked_package_count": len(FIXTURE_LOCK_PINS),
        "installed_project": "gptcache==0.1.44",
    }
    container_report = result["sources"]["container_reproducibility"]
    assert (
        container_report["current_dependency_lock_sha256"]
        == current_lock_sha256
    )
    assert container_report["image_inspect"]["path"] == (
        "container-image-inspect.json"
    )
    assert [run["container_id"] for run in container_report["runs"]] == [
        "4" * 64,
        "5" * 64,
    ]
    assert [run["container_inspect"]["path"] for run in container_report["runs"]] == [
        "docker-run-1.inspect.json",
        "docker-run-2.inspect.json",
    ]
    assert result["gates"]["gate_1_correctness"]["status"] == "pass"
    assert result["gates"]["gate_1_correctness"]["claimable"] is True
    assert result["gates"]["gate_8_reproducibility"]["status"] == "pass"
    assert result["gates"]["gate_8_reproducibility"]["claimable"] is True


def test_host_evidence_hash_tamper_is_rejected(tmp_path):
    host = _host_verification_fixture(tmp_path / "host")
    (host.parent / "host-verification.log").write_text(
        "fixture test output\n[verify] FAIL\n", encoding="utf-8"
    )

    with pytest.raises(AnalysisError, match="verification log SHA-256 mismatch"):
        analyze_results(tmp_path / "analysis", host_verification=host)


@pytest.mark.parametrize(
    ("field", "label"),
    (
        ("environment_install_log", "environment install log"),
        ("installed_packages", "installed packages"),
    ),
)
def test_host_environment_attestation_is_required(tmp_path, field, label):
    host = _host_verification_fixture(tmp_path / "host")
    evidence = json.loads(host.read_text(encoding="utf-8"))
    evidence.pop(field)
    _write_json(host, evidence)

    with pytest.raises(AnalysisError, match=label + " must be a path/SHA-256 object"):
        analyze_results(tmp_path / "analysis", host_verification=host)


def test_forged_host_install_sentinel_is_rejected(tmp_path):
    host = _host_verification_fixture(tmp_path / "host")
    evidence = json.loads(host.read_text(encoding="utf-8"))
    install_log = host.parent / evidence["environment_install_log"]["path"]
    install_log.write_text(
        "No broken requirements found.\n[install] pip check FAIL\n",
        encoding="utf-8",
    )
    evidence["environment_install_log"]["sha256"] = sha256_file(install_log)
    _write_json(host, evidence)

    with pytest.raises(AnalysisError, match="does not end in.*pip check PASS"):
        analyze_results(tmp_path / "analysis", host_verification=host)


def test_forged_host_package_inventory_is_rejected(tmp_path):
    host = _host_verification_fixture(tmp_path / "host")
    evidence = json.loads(host.read_text(encoding="utf-8"))
    packages = host.parent / evidence["installed_packages"]["path"]
    name, version = FIXTURE_LOCK_PINS[0]
    packages.write_text(
        FIXTURE_PACKAGES.replace(
            "%s==%s\n" % (name, version), "%s==forged\n" % name, 1
        )
        + "gptcache==0.1.44\n",
        encoding="utf-8",
    )
    evidence["installed_packages"]["sha256"] = sha256_file(packages)
    _write_json(host, evidence)

    with pytest.raises(AnalysisError, match="does not exactly match"):
        analyze_results(tmp_path / "analysis", host_verification=host)


@pytest.mark.parametrize(
    ("evidence_kind", "message"),
    (
        ("host", "clean-host verification source commit does not match"),
        ("container", "paired-container evidence source commit does not match"),
    ),
)
def test_stale_evidence_commit_is_rejected(
    tmp_path, monkeypatch, evidence_kind, message
):
    _bind_analyzer_to_fixture_head(monkeypatch)
    def reject_non_parent(source_commit, current_commit, label):
        raise AnalysisError(
            "%s source commit does not match current Git HEAD" % label
        )

    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._packaging_descendant_changes",
        reject_non_parent,
    )
    stale = "0" * 40
    if stale == _git_head():
        stale = "f" * 40
    if evidence_kind == "host":
        arguments = {
            "host_verification": _host_verification_fixture(
                tmp_path / "host", source_commit=stale
            )
        }
    else:
        arguments = {
            "container_reproducibility": _container_reproducibility_fixture(
                tmp_path / "container", source_commit=stale
            )
        }

    with pytest.raises(AnalysisError, match=message):
        analyze_results(tmp_path / "analysis", **arguments)


@pytest.mark.parametrize("evidence_kind", ("host", "container"))
def test_evidence_lock_must_match_current_project_lock(tmp_path, evidence_kind):
    forged_lock = FIXTURE_LOCK + "# forged retained lock\n"
    if evidence_kind == "host":
        arguments = {
            "host_verification": _host_verification_fixture(
                tmp_path / "host", lock_text=forged_lock
            )
        }
        message = "clean-host dependency lock does not match current"
    else:
        arguments = {
            "container_reproducibility": _container_reproducibility_fixture(
                tmp_path / "container", lock_text=forged_lock
            )
        }
        message = "container dependency lock does not match current"

    with pytest.raises(AnalysisError, match=message):
        analyze_results(tmp_path / "analysis", **arguments)


def test_no_git_keeps_verified_evidence_nonclaimable(tmp_path, monkeypatch):
    host = _host_verification_fixture(tmp_path / "host")
    container = _container_reproducibility_fixture(tmp_path / "container")
    monkeypatch.setattr(
        "benchmarks.carma.analyze_results._current_git_head",
        lambda: {
            "status": "unavailable",
            "head_commit": None,
            "reason": "fixture has no Git metadata",
        },
    )

    result = analyze_results(
        tmp_path / "analysis",
        host_verification=host,
        container_reproducibility=container,
    )

    for source in ("host_verification", "container_reproducibility"):
        report = result["sources"][source]
        assert report["status"] == "verified"
        assert report["commit_binding"] == {
            "status": "unavailable",
            "evidence_source_commit": _git_head(),
            "current_git_head": None,
            "reason": "fixture has no Git metadata",
        }
    for gate in ("gate_1_correctness", "gate_8_reproducibility"):
        assert result["gates"][gate]["status"] == "pending"
        assert result["gates"][gate]["claimable"] is False


@pytest.mark.parametrize(
    "relative_path",
    (
        "../outside.lock",
        "./requirements-project.lock",
        "benchmark/../requirements-project.lock",
    ),
)
def test_host_evidence_noncanonical_path_is_rejected(tmp_path, relative_path):
    outside = tmp_path / "outside.lock"
    outside.write_text("lock\n", encoding="utf-8")
    host = _host_verification_fixture(tmp_path / "host")
    evidence = json.loads(host.read_text(encoding="utf-8"))
    evidence["dependency_lock"].update(
        {"path": relative_path, "sha256": sha256_file(outside)}
    )
    _write_json(host, evidence)

    with pytest.raises(AnalysisError, match="path is not canonical"):
        analyze_results(tmp_path / "analysis", host_verification=host)


def test_host_evidence_symlink_reference_is_rejected(tmp_path):
    host = _host_verification_fixture(tmp_path / "host")
    link = host.parent / "linked-requirements-project.lock"
    try:
        link.symlink_to("requirements-project.lock")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    evidence = json.loads(host.read_text(encoding="utf-8"))
    evidence["dependency_lock"].update(
        {"path": link.name, "sha256": sha256_file(link)}
    )
    _write_json(host, evidence)

    with pytest.raises(AnalysisError, match="path contains a symlink"):
        analyze_results(tmp_path / "analysis", host_verification=host)


def test_false_container_control_is_rejected(tmp_path):
    container = _container_reproducibility_fixture(tmp_path / "container")
    evidence = json.loads(container.read_text(encoding="utf-8"))
    evidence["controls"]["no_new_privileges"] = False
    _write_json(container, evidence)

    with pytest.raises(
        AnalysisError, match="security or dependency controls are invalid"
    ):
        analyze_results(
            tmp_path / "analysis", container_reproducibility=container
        )


@pytest.mark.parametrize("attestation", ("image", "runtime"))
def test_container_inspect_attestation_is_required(tmp_path, attestation):
    container = _container_reproducibility_fixture(tmp_path / "container")
    evidence = json.loads(container.read_text(encoding="utf-8"))
    if attestation == "image":
        evidence.pop("image_inspect")
        label = "image inspection"
    else:
        evidence["runs"][0].pop("container_inspect")
        label = "docker-run-1 runtime inspection"
    _write_json(container, evidence)

    with pytest.raises(AnalysisError, match=label + " must be a path/SHA-256 object"):
        analyze_results(
            tmp_path / "analysis", container_reproducibility=container
        )


def test_forged_image_inspect_is_rejected(tmp_path):
    container = _container_reproducibility_fixture(tmp_path / "container")
    evidence = json.loads(container.read_text(encoding="utf-8"))
    inspect_path = container.parent / evidence["image_inspect"]["path"]
    inspection = json.loads(inspect_path.read_text(encoding="utf-8"))
    inspection[0]["Config"]["User"] = "root"
    _write_json(inspect_path, inspection)
    evidence["image_inspect"]["sha256"] = sha256_file(inspect_path)
    _write_json(container, evidence)

    with pytest.raises(AnalysisError, match="does not use the project user"):
        analyze_results(
            tmp_path / "analysis", container_reproducibility=container
        )


def test_forged_runtime_inspect_is_rejected(tmp_path):
    container = _container_reproducibility_fixture(tmp_path / "container")
    evidence = json.loads(container.read_text(encoding="utf-8"))
    runtime_ref = evidence["runs"][0]["container_inspect"]
    inspect_path = container.parent / runtime_ref["path"]
    inspection = json.loads(inspect_path.read_text(encoding="utf-8"))
    inspection[0]["HostConfig"]["NetworkMode"] = "default"
    _write_json(inspect_path, inspection)
    runtime_ref["sha256"] = sha256_file(inspect_path)
    _write_json(container, evidence)

    with pytest.raises(AnalysisError, match="runtime network mode was not none"):
        analyze_results(
            tmp_path / "analysis", container_reproducibility=container
        )


def test_forged_container_ids_are_rejected(tmp_path):
    container = _container_reproducibility_fixture(tmp_path / "container")
    evidence = json.loads(container.read_text(encoding="utf-8"))
    first_ref = evidence["runs"][0]["container_inspect"]
    second_ref = evidence["runs"][1]["container_inspect"]
    first_path = container.parent / first_ref["path"]
    second_path = container.parent / second_ref["path"]
    first = json.loads(first_path.read_text(encoding="utf-8"))
    second = json.loads(second_path.read_text(encoding="utf-8"))
    second[0]["Id"] = first[0]["Id"]
    _write_json(second_path, second)
    second_ref["sha256"] = sha256_file(second_path)
    _write_json(container, evidence)

    with pytest.raises(AnalysisError, match="reused a container ID"):
        analyze_results(
            tmp_path / "analysis", container_reproducibility=container
        )


def test_forged_normalized_logs_are_rejected(tmp_path):
    container = _container_reproducibility_fixture(tmp_path / "container")
    evidence = json.loads(container.read_text(encoding="utf-8"))
    forged = b"fixture container output\n1 passed in <forged>\n[verify] PASS\n"
    for run in evidence["runs"]:
        normalized = container.parent / run["non_timing_log"]["path"]
        normalized.write_bytes(forged)
        run["non_timing_log"]["sha256"] = sha256_file(normalized)
    _write_json(container, evidence)

    with pytest.raises(AnalysisError, match="is not the normalized raw log"):
        analyze_results(
            tmp_path / "analysis", container_reproducibility=container
        )


def test_relocated_container_evidence_preserves_mount_attestation(
    tmp_path, monkeypatch
):
    _bind_analyzer_to_fixture_head(monkeypatch)
    original = tmp_path / "original"
    container = _container_reproducibility_fixture(original)
    relocated = tmp_path / "curated" / "verification"
    shutil.copytree(container.parent, relocated)

    result = analyze_results(
        tmp_path / "analysis",
        container_reproducibility=relocated / container.name,
    )

    report = result["sources"]["container_reproducibility"]
    assert report["status"] == "verified"
    assert report["runs"][0]["artifact_mount_source"] == str(
        (original / "docker-run-1").resolve()
    )


def test_container_benchmark_artifact_tamper_is_rejected(tmp_path):
    container = _container_reproducibility_fixture(tmp_path / "container")
    runs = container.parent / "docker-run-1" / "benchmark" / "runs.csv"
    with runs.open("a", encoding="utf-8") as output:
        output.write("tampered\n")

    with pytest.raises(AnalysisError, match="benchmark runs.csv SHA-256 mismatch"):
        analyze_results(
            tmp_path / "analysis", container_reproducibility=container
        )


def test_cli_accepts_host_and_container_evidence_flags(
    tmp_path, capsys, monkeypatch
):
    _bind_analyzer_to_fixture_head(monkeypatch)
    host = _host_verification_fixture(tmp_path / "host")
    container = _container_reproducibility_fixture(tmp_path / "container")
    output = tmp_path / "analysis"

    exit_code = main(
        [
            "--host-verification",
            str(host),
            "--container-reproducibility",
            str(container),
            "--output",
            str(output),
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["gates"]["gate_1_correctness"]["status"] == "pass"
    assert printed["gates"]["gate_8_reproducibility"]["status"] == "pass"
    assert (output / "gate-audit.json").is_file()
