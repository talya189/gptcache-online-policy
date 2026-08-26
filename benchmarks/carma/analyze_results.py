"""Deterministic integrity audit and publication figures for CARMA results.

The analyzer consumes only completed, manifest-endorsed artifacts. Missing or
non-inferential sources remain pending; stale schemas are never zero-filled.
No benchmark is executed and no result is modified by this module.
"""

import argparse
import csv
import hashlib
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ANALYSIS_SCHEMA = "carma-post-analysis-v2"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FULL_SCHEMA = "carma-full-experiment-v2"
RUN_SCHEMA = "carma-benchmark-v2"
INTEGRATION_SCHEMA = "carma-sqlite-faiss-v1"
QQP_SCHEMA = "carma-qqp-v1"
MOSS_SCHEMA = "carma-moss-recorded-response-v2"
HOST_VERIFICATION_SCHEMA = "carma-host-verification-v1"
CONTAINER_REPRODUCIBILITY_SCHEMA = "carma-container-reproducibility-v1"
MATPLOTLIB_VERSION = "3.10.8"
CONTAINER_IMAGE_ENVIRONMENT = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PIP_CONFIG_FILE": "/dev/null",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_INDEX_URL": "https://pypi.org/simple",
    "PIP_NO_INPUT": "1",
    "PIP_ROOT_USER_ACTION": "ignore",
    "TIKTOKEN_CACHE_DIR": "/opt/tiktoken-cache",
}
REPORT_TEXT_WIDTH_IN = 7.05
PUBLICATION_FIGURE_WIDTH_IN = 8.0
MAX_PUBLICATION_FIGURE_WIDTH_IN = 8.2
MAX_CAPACITY_FIGURE_HEIGHT_IN = 6.6
CAPACITY_RATE_DOMAIN_PERCENT = (0, 80)
CAPACITY_RATE_TICKS_PERCENT = (0, 20, 40, 60, 80)
MIN_SOURCE_FONT_PT = 12.5
MIN_REPORT_FONT_PT = 10.0
PNG_DPI = 220
QQP_ARCHIVE_SHA256 = (
    "1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58"
)
QQP_TOKENIZER_REVISION = "5fb246187b5489d59ce0db167e739192759defab"
QQP_MODEL_REVISION = "5b562a100bc67e898ac89814e7a4668a18d65756"
QQP_CALIBRATION_FIELDS = (
    "threshold",
    "true_positive",
    "false_positive",
    "true_negative",
    "false_negative",
    "precision",
    "recall",
    "false_positive_rate",
    "wilson_precision_lower_one_sided_95",
)
MOSS_REVISION = "42e216d3e3fb331c18d5fa6e7cb4f1c53eef24a4"
MOSS_ARCHIVE_SHA256 = (
    "4d4f57df0dd5ad1442b6c08ca69ec1a59705837bb9813e7aae3bd3a9e3adb085"
)
PYTEST_SUMMARY = re.compile(
    rb"(?m)^(?P<result>[0-9]+ passed"
    rb"(?:, [0-9]+ (?:deselected|skipped|xfailed|xpassed|warnings?))*)"
    rb" in [0-9]+(?:[.][0-9]+)?s$"
)
PRIMARY_WORKLOADS = ("stationary", "phase_shift", "pollution_scan")
LOWER_IS_BETTER = {
    "false_hit_rate",
    "shift_recovery_lag_mean_requests",
}
FULL_ARTIFACTS = (
    "validation.csv",
    "validation_runs.csv",
    "runs.csv",
    "aggregate.csv",
)
FIGURE_NAMES = (
    "vhr-deltas",
    "scan-return",
    "capacity-curve",
    "latency-resources",
)
PUBLISHED_NAMES = (
    "gate-audit.json",
    "summary.csv",
) + tuple(
    "%s.%s" % (name, extension)
    for name in FIGURE_NAMES
    for extension in ("svg", "png")
)

RUN_FIELDS = (
    "schema_version",
    "run_id",
    "policy",
    "workload",
    "seed",
    "trace_hash",
    "deterministic_digest",
    "requests",
    "capacity",
    "raw_hits",
    "valid_hits",
    "false_hits",
    "misses",
    "false_misses",
    "reuse_opportunities",
    "valid_hit_rate",
    "false_hit_rate",
    "hit_precision",
    "opportunity_recall",
    "safe_token_saving_ratio",
    "admissions",
    "rejections",
    "evictions",
    "final_cache_entries",
    "mean_latency_us",
    "p50_latency_us",
    "p95_latency_us",
    "p99_latency_us",
    "throughput_qps",
    "policy_topics",
    "policy_cells",
    "policy_ghost_cells",
    "scan_return_requests",
    "scan_return_valid_hits",
    "scan_return_false_hits",
    "scan_return_reuse_opportunities",
    "scan_return_valid_hit_rate",
    "scan_return_false_hit_rate",
    "scan_return_opportunity_recall",
    "shift_recovery_lag_mean_requests",
    "shift_recovery_lag_max_requests",
    "shift_recovery_failures",
    "shift_recovery_transitions_json",
)
FULL_RUN_PREFIX = (
    "stage",
    "config_id",
    "topic_threshold",
    "cell_threshold",
    "demand_half_life",
    "quota_strength",
)
FULL_RUN_FIELDS = FULL_RUN_PREFIX + RUN_FIELDS
VALIDATION_FIELDS = (
    "config_id",
    "topic_threshold",
    "cell_threshold",
    "demand_half_life",
    "quota_strength",
    "run_count",
    "mean_valid_hit_rate",
    "normalized_mean_valid_hit_rate",
    "mean_false_hit_rate",
    "total_false_hits",
    "mean_opportunity_recall",
    "mean_safe_token_saving_ratio",
    "passes_zero_false_hits",
    "selected",
)
VALIDATION_RUN_FIELDS = (
    "config_id",
    "topic_threshold",
    "cell_threshold",
    "demand_half_life",
    "quota_strength",
    "normalized_valid_hit_rate",
    "config_total_false_hits",
    "config_passes_zero_false_hits",
    "config_selected",
) + RUN_FIELDS
AGGREGATE_FIELDS = (
    "stage",
    "row_type",
    "workload",
    "capacity",
    "metric",
    "policy",
    "comparator",
    "n",
    "mean",
    "ci_low",
    "ci_high",
    "delta_mean",
    "delta_ci_low",
    "delta_ci_high",
    "wilcoxon_statistic",
    "p_value",
    "p_holm",
    "nonzero_pairs",
    "rank_biserial",
    "test_family",
)
INTEGRATION_BASE_FIELDS = (
    "schema_version",
    "run_id",
    "policy",
    "mode",
    "workload",
    "seed",
    "trace_hash",
    "requests",
    "capacity",
    "embedding_dimension",
    "top_k",
    "hit_threshold",
    "hits",
    "misses",
    "valid_hits",
    "false_hits",
    "stale_candidates",
    "reuse_opportunities",
    "hit_rate",
    "opportunity_recall",
    "safe_token_saving_ratio",
    "admissions",
    "rejections",
    "evictions",
    "final_scalar_count",
    "final_vector_count",
    "deleted_scalar_count",
    "verified_entries",
    "wall_seconds",
    "throughput_qps",
    "cpu_user_seconds",
    "cpu_system_seconds",
    "cpu_percent_one_core",
    "rss_start_bytes",
    "rss_end_bytes",
    "rss_peak_sampled_bytes",
    "rss_delta_bytes",
    "io_read_bytes",
    "io_write_bytes",
    "io_read_count",
    "io_write_count",
    "io_counters_available",
    "policy_topics",
    "policy_cells",
    "policy_ghost_cells",
)
INTEGRATION_FIELDS = INTEGRATION_BASE_FIELDS + tuple(
    "%s_%s_us" % (stage, statistic)
    for stage in ("total", "search", "policy", "storage", "unaccounted")
    for statistic in ("mean", "p50", "p95", "p99")
)
MOSS_FIELDS = (
    "schema_version",
    "run_id",
    "policy",
    "workload",
    "trace_hash",
    "requests",
    "capacity",
    "raw_hits",
    "valid_hits",
    "false_hits",
    "misses",
    "reuse_opportunities",
    "valid_hit_rate",
    "false_hit_rate",
    "hit_precision",
    "opportunity_recall",
    "safe_token_saving_ratio",
    "total_tokens",
    "valid_saved_tokens",
    "recorded_response_replays",
    "live_model_calls",
    "evictions",
    "final_cache_entries",
)
SUMMARY_FIELDS = (
    "item",
    "category",
    "status",
    "estimate",
    "ci_low",
    "ci_high",
    "unit",
    "criterion",
    "source",
    "note",
)


class AnalysisError(ValueError):
    """Raised when a supplied artifact fails integrity or schema validation."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_results(
    output_dir: Path,
    full_dir: Optional[Path] = None,
    integration_dirs: Sequence[Path] = (),
    qqp_result: Optional[Path] = None,
    moss_dir: Optional[Path] = None,
    host_verification: Optional[Path] = None,
    container_reproducibility: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate supplied result sets and atomically publish the audit."""

    observed_matplotlib = importlib_metadata.version("matplotlib")
    if observed_matplotlib != MATPLOTLIB_VERSION:
        raise RuntimeError(
            "CARMA figures require matplotlib==%s; found %s"
            % (MATPLOTLIB_VERSION, observed_matplotlib)
        )

    git_head = _current_git_head()
    full = _load_full(full_dir)
    integration = _load_integrations(integration_dirs)
    qqp = _load_qqp(qqp_result)
    moss = _load_moss(moss_dir)
    host = _load_host_verification(host_verification, git_head)
    container = _load_container_reproducibility(
        container_reproducibility, git_head
    )
    gates, supplemental = _build_gate_audit(
        full, integration, qqp, moss, host, container
    )

    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".carma-analysis-", dir=str(output_dir.parent)
    ) as temporary:
        staging = Path(temporary)
        figure_report = _render_figures(full, integration, staging)
        summary_rows = _summary_rows(gates, supplemental)
        _write_csv(staging / "summary.csv", summary_rows, SUMMARY_FIELDS)

        statuses = [gate["status"] for gate in gates.values()]
        overall = (
            "fail"
            if "fail" in statuses
            else "pending"
            if any(status != "pass" for status in statuses)
            else "pass"
        )
        audit = {
            "schema_version": ANALYSIS_SCHEMA,
            "overall_status": overall,
            "status_precedence": "fail > pending > pass",
            "sources": {
                "full": full["report"],
                "integration": integration["report"],
                "qqp": qqp["report"],
                "moss": moss["report"],
                "host_verification": host["report"],
                "container_reproducibility": container["report"],
            },
            "gates": gates,
            "supplemental_checks": supplemental,
            "figures": figure_report,
            "methodology_limits": [
                (
                    "Gate 2 does not freeze which synthetic workload owns "
                    "the CARMA false-hit-rate upper-bound criterion."
                ),
                (
                    "Gate 7 requires five fresh-process system seeds but "
                    "does not freeze an across-seed aggregation rule."
                ),
                *(
                    [
                        "Gate 1 remains pending unless verified clean-host test "
                        "evidence is exact-commit-bound to current Git HEAD."
                    ]
                    if not _evidence_is_claimable(host["report"])
                    else []
                ),
                *(
                    [
                        "Gate 8 remains pending unless verified paired-container "
                        "evidence is exact-commit-bound to current Git HEAD."
                    ]
                    if not _evidence_is_claimable(container["report"])
                    else []
                ),
            ],
            "renderer": {
                "library": "matplotlib",
                "version": observed_matplotlib,
                "backend": "Agg",
                "svg_hashsalt": ANALYSIS_SCHEMA,
                "png_dpi": PNG_DPI,
                "publication_contract": {
                    "report_text_width_in": REPORT_TEXT_WIDTH_IN,
                    "source_width_in": PUBLICATION_FIGURE_WIDTH_IN,
                    "maximum_source_width_in": MAX_PUBLICATION_FIGURE_WIDTH_IN,
                    "minimum_source_font_pt": MIN_SOURCE_FONT_PT,
                    "minimum_report_font_pt": MIN_REPORT_FONT_PT,
                    "nominal_scale_to_report_width": _round(
                        REPORT_TEXT_WIDTH_IN / PUBLICATION_FIGURE_WIDTH_IN
                    ),
                    "nominal_minimum_font_at_report_width_pt": _round(
                        MIN_SOURCE_FONT_PT
                        * REPORT_TEXT_WIDTH_IN
                        / PUBLICATION_FIGURE_WIDTH_IN
                    ),
                },
            },
            "artifacts": {},
        }
        for path in sorted(staging.iterdir(), key=lambda item: item.name):
            if path.name != "gate-audit.json":
                audit["artifacts"][path.name] = {
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
        _write_json(staging / "gate-audit.json", audit)

        output_dir.mkdir(parents=True, exist_ok=True)
        staged_names = {path.name for path in staging.iterdir()}
        for name in PUBLISHED_NAMES:
            source = staging / name
            target = output_dir / name
            if name in staged_names:
                os.replace(source, target)
            elif target.is_file():
                target.unlink()
    return audit


def _load_full(path: Optional[Path]) -> Dict[str, Any]:
    empty = {"metadata": None, "runs": [], "aggregate": []}
    if path is None:
        return {
            **empty,
            "report": _pending_report(None, "full result directory not supplied"),
        }
    directory = Path(path)
    if not directory.is_dir():
        return {
            **empty,
            "report": _pending_report(directory, "full result directory is absent"),
        }
    metadata_path = directory / "metadata.json"
    if not metadata_path.is_file():
        return {
            **empty,
            "report": _pending_report(
                directory,
                "incomplete result: metadata.json was not atomically published",
                status="incomplete",
            ),
        }
    metadata = _read_json(metadata_path)
    schema = metadata.get("schema_version")
    if schema != FULL_SCHEMA:
        return {
            **empty,
            "report": {
                "status": "stale",
                "path": str(directory.resolve()),
                "observed_schema": schema,
                "required_schema": FULL_SCHEMA,
                "metadata_sha256": sha256_file(metadata_path),
                "reason": "stale full results are not mapped into current metrics",
            },
        }

    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, dict):
        raise AnalysisError("full metadata lacks artifact hashes")
    verified_files = {}
    for name in FULL_ARTIFACTS:
        artifact_path = directory / name
        if not artifact_path.is_file():
            raise AnalysisError("full metadata endorses missing %s" % name)
        expected = _hash_value(artifacts.get(name), "full %s" % name)
        observed = sha256_file(artifact_path)
        if observed != expected:
            raise AnalysisError("full %s SHA-256 mismatch" % name)
        verified_files[name] = observed

    validation, validation_fields = _read_csv(directory / "validation.csv")
    validation_runs, validation_run_fields = _read_csv(
        directory / "validation_runs.csv"
    )
    runs, run_fields = _read_csv(directory / "runs.csv")
    aggregate, aggregate_fields = _read_csv(directory / "aggregate.csv")
    _require_exact_fields(validation_fields, VALIDATION_FIELDS, "full validation.csv")
    _require_exact_fields(
        validation_run_fields,
        VALIDATION_RUN_FIELDS,
        "full validation_runs.csv",
    )
    _require_exact_fields(run_fields, FULL_RUN_FIELDS, "full runs.csv")
    _require_exact_fields(aggregate_fields, AGGREGATE_FIELDS, "full aggregate.csv")
    _validate_validation(validation, validation_runs, metadata)
    _validate_full_runs(runs, metadata)
    _validate_aggregate(aggregate, runs)
    synthetic = _synthetic_gates(aggregate, int(metadata["test_capacity"]))
    _verify_recorded_synthetic_gates(metadata, synthetic)

    mode = metadata.get("mode")
    if mode not in ("smoke", "full"):
        raise AnalysisError("full metadata has an invalid mode")
    inferential = metadata.get("inferential_test_run") is True
    if inferential != (mode == "full"):
        raise AnalysisError("full mode and inferential flag disagree")
    report = {
        "status": "verified",
        "path": str(directory.resolve()),
        "schema_version": schema,
        "mode": mode,
        "inferential": inferential,
        "rows": {
            "validation.csv": len(validation),
            "validation_runs.csv": len(validation_runs),
            "runs.csv": len(runs),
            "aggregate.csv": len(aggregate),
        },
        "files": verified_files,
        "metadata_sha256": sha256_file(metadata_path),
        "git_provenance": metadata.get("experiment_identity"),
        "local_source_verification": _verify_full_source_provenance(metadata),
        "selected_config_id": metadata.get("selected_config_id"),
    }
    return {
        "metadata": metadata,
        "runs": runs,
        "aggregate": aggregate,
        "synthetic_gates": synthetic,
        "report": report,
    }


def _validate_validation(
    rows: Sequence[Dict[str, str]],
    run_rows: Sequence[Dict[str, str]],
    metadata: Dict[str, Any],
) -> None:
    expected_configs = _integer(
        metadata.get("validation_grid", {}).get("configuration_count"),
        "validation configuration count",
    )
    if len(rows) != expected_configs:
        raise AnalysisError("validation.csv configuration count mismatch")
    identifiers = [row["config_id"] for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise AnalysisError("validation.csv has duplicate configurations")
    selected = [row for row in rows if row["selected"] == "True"]
    if len(selected) != 1 or selected[0]["config_id"] != metadata.get(
        "selected_config_id"
    ):
        raise AnalysisError("validation selection does not match metadata")
    expected_runs = (
        expected_configs
        * len(metadata.get("validation_seeds", []))
        * len(metadata.get("workloads", []))
    )
    if len(run_rows) != expected_runs:
        raise AnalysisError("validation_runs.csv row count mismatch")
    run_ids = set()
    for row in run_rows:
        if row["schema_version"] != RUN_SCHEMA:
            raise AnalysisError("validation run uses a stale runner schema")
        identity = (row["config_id"], row["workload"], row["seed"])
        if identity in run_ids:
            raise AnalysisError("validation_runs.csv has duplicate rows")
        run_ids.add(identity)
        if row["config_selected"] not in ("True", "False"):
            raise AnalysisError("validation run has invalid selection label")


def _verify_full_source_provenance(metadata: Dict[str, Any]) -> Dict[str, Any]:
    identity = metadata.get("experiment_identity")
    if not isinstance(identity, dict):
        return {"status": "unavailable", "mismatches": []}
    expected_sources = identity.get("source_sha256")
    mismatches = []
    verified = []
    if isinstance(expected_sources, dict):
        for relative, expected in sorted(expected_sources.items()):
            candidate = (PROJECT_ROOT / relative).resolve()
            try:
                candidate.relative_to(PROJECT_ROOT.resolve())
            except ValueError:
                mismatches.append({"path": relative, "reason": "outside project root"})
                continue
            if not candidate.is_file():
                mismatches.append({"path": relative, "reason": "missing locally"})
            elif sha256_file(candidate) != expected:
                mismatches.append({"path": relative, "reason": "SHA-256 differs"})
            else:
                verified.append(relative)
    contract_relative = identity.get("experiment_contract_path")
    contract_expected = identity.get("experiment_contract_sha256")
    if isinstance(contract_relative, str) and isinstance(contract_expected, str):
        contract = (PROJECT_ROOT / contract_relative).resolve()
        if not contract.is_file() or sha256_file(contract) != contract_expected:
            mismatches.append(
                {"path": contract_relative, "reason": "contract SHA-256 differs"}
            )
        else:
            verified.append(contract_relative)
    return {
        "status": "match" if not mismatches else "historical_or_modified",
        "verified_paths": sorted(set(verified)),
        "mismatches": mismatches,
    }


def _validate_full_runs(
    rows: Sequence[Dict[str, str]], metadata: Dict[str, Any]
) -> None:
    if not rows:
        raise AnalysisError("full runs.csv is empty")
    trace_hashes = metadata.get("trace_hashes")
    if not isinstance(trace_hashes, dict):
        raise AnalysisError("full metadata lacks trace hashes")
    identities = set()
    primary: Dict[Tuple[str, str], set] = {}
    for index, row in enumerate(rows, 2):
        if row["schema_version"] != RUN_SCHEMA:
            raise AnalysisError(
                "full runs.csv:%d uses stale schema %s"
                % (index, row["schema_version"])
            )
        stage = row["stage"]
        workload = row["workload"]
        policy = row["policy"]
        seed = _integer(row["seed"], "full seed")
        capacity = _integer(row["capacity"], "full capacity")
        requests = _integer(row["requests"], "full requests")
        if capacity < 1 or requests < 1:
            raise AnalysisError("full capacity and requests must be positive")
        identity = (stage, workload, capacity, seed, policy)
        if identity in identities:
            raise AnalysisError("duplicate full run identity %r" % (identity,))
        identities.add(identity)
        key = "%s/c%d/%s/%d" % (stage, capacity, workload, seed)
        if trace_hashes.get(key) != row["trace_hash"]:
            raise AnalysisError("full trace hash mismatch for %s" % key)
        for field in ("valid_hit_rate", "false_hit_rate", "opportunity_recall"):
            _rate(row[field], "full %s" % field)
        _rate(row["safe_token_saving_ratio"], "full safe token ratio")
        raw_hits = _integer(row["raw_hits"], "full raw hits")
        valid_hits = _integer(row["valid_hits"], "full valid hits")
        false_hits = _integer(row["false_hits"], "full false hits")
        misses = _integer(row["misses"], "full misses")
        if min(raw_hits, valid_hits, false_hits, misses) < 0:
            raise AnalysisError("full hit counts must be nonnegative")
        if raw_hits + misses != requests or valid_hits + false_hits != raw_hits:
            raise AnalysisError("full hit-count identity failed")
        _equal_rate(row["valid_hit_rate"], valid_hits, requests, "full VHR")
        _equal_rate(row["false_hit_rate"], false_hits, requests, "full FHR")
        if stage == "primary_test" and policy in ("LRU", "LFU", "CARMA"):
            primary.setdefault((workload, policy), set()).add(seed)

    expected_seeds = {_integer(seed, "metadata test seed") for seed in metadata["test_seeds"]}
    for workload in metadata.get("workloads", PRIMARY_WORKLOADS):
        for policy in ("LRU", "LFU", "CARMA"):
            if primary.get((workload, policy), set()) != expected_seeds:
                raise AnalysisError(
                    "full primary coverage mismatch for %s/%s" % (workload, policy)
                )
    if metadata.get("selection_uses_test_results") is not False:
        raise AnalysisError("full metadata does not preserve the leakage control")
    if metadata.get("seeds_are_disjoint") is not True:
        raise AnalysisError("validation and test seeds are not recorded as disjoint")


def _validate_aggregate(
    rows: Sequence[Dict[str, str]], runs: Sequence[Dict[str, str]]
) -> None:
    if not rows:
        raise AnalysisError("full aggregate.csv is empty")
    identities = set()
    by_run: Dict[Tuple[str, str, int, str], List[Dict[str, str]]] = {}
    for run in runs:
        key = (
            run["stage"],
            run["workload"],
            _integer(run["capacity"], "run capacity"),
            run["policy"],
        )
        by_run.setdefault(key, []).append(run)
    for row in rows:
        row_type = row["row_type"]
        if row_type not in ("summary", "comparison"):
            raise AnalysisError("aggregate has an unknown row_type")
        identity = (
            row["stage"],
            row_type,
            row["workload"],
            row["capacity"],
            row["metric"],
            row["policy"],
            row["comparator"],
        )
        if identity in identities:
            raise AnalysisError("duplicate aggregate identity %r" % (identity,))
        identities.add(identity)
        n = _integer(row["n"], "aggregate n")
        if n < 1:
            raise AnalysisError("aggregate n must be positive")
        capacity = _integer(row["capacity"], "aggregate capacity")
        key = (row["stage"], row["workload"], capacity, row["policy"])
        if row_type == "summary":
            estimate = _number(row["mean"], "aggregate mean")
            low = _number(row["ci_low"], "aggregate ci_low")
            high = _number(row["ci_high"], "aggregate ci_high")
            if not low <= estimate <= high:
                raise AnalysisError("aggregate summary CI does not contain its mean")
            values = _run_metric_values(by_run.get(key, []), row["metric"])
            if len(values) != n or not _close(_mean(values), estimate):
                raise AnalysisError("aggregate summary does not reconcile to runs")
        else:
            estimate = _number(row["delta_mean"], "aggregate delta mean")
            low = _number(row["delta_ci_low"], "aggregate delta ci_low")
            high = _number(row["delta_ci_high"], "aggregate delta ci_high")
            if not low <= estimate <= high:
                raise AnalysisError("aggregate delta CI does not contain its mean")
            deltas = _paired_deltas(row, by_run)
            if len(deltas) != n or not _close(_mean(deltas), estimate):
                raise AnalysisError("aggregate comparison does not reconcile to runs")
            if row["test_family"] == "primary_vhr":
                p_holm = _number(row["p_holm"], "Holm p-value")
                if not 0.0 <= p_holm <= 1.0:
                    raise AnalysisError("Holm p-value is outside [0, 1]")


def _paired_deltas(
    row: Dict[str, str],
    by_run: Dict[Tuple[str, str, int, str], List[Dict[str, str]]],
) -> List[float]:
    stage = row["stage"]
    workload = row["workload"]
    capacity = _integer(row["capacity"], "comparison capacity")
    metric = row["metric"]
    left_rows = by_run.get((stage, workload, capacity, "CARMA"), [])
    left = {
        _integer(item["seed"], "comparison seed"): _metric(item, metric)
        for item in left_rows
    }
    comparator = row["comparator"]
    if comparator == "BEST_BASELINE":
        right_maps = []
        for policy in ("LRU", "LFU"):
            right_maps.append(
                {
                    _integer(item["seed"], "comparison seed"): _metric(item, metric)
                    for item in by_run.get((stage, workload, capacity, policy), [])
                }
            )
        common = sorted(set(left) & set(right_maps[0]) & set(right_maps[1]))
        result = []
        for seed in common:
            values = [mapping[seed] for mapping in right_maps]
            baseline = min(values) if metric in LOWER_IS_BETTER else max(values)
            result.append(left[seed] - baseline)
        return result
    right = {
        _integer(item["seed"], "comparison seed"): _metric(item, metric)
        for item in by_run.get((stage, workload, capacity, comparator), [])
    }
    return [left[seed] - right[seed] for seed in sorted(set(left) & set(right))]


def _synthetic_gates(
    aggregate: Sequence[Dict[str, str]], capacity: int
) -> Dict[str, Dict[str, Any]]:
    shift = _comparison(
        aggregate, "phase_shift", "valid_hit_rate", capacity, "BEST_BASELINE"
    )
    scan = _comparison(
        aggregate,
        "pollution_scan",
        "scan_return_valid_hit_rate",
        capacity,
        "BEST_BASELINE",
    )
    stationary = _comparison(
        aggregate, "stationary", "valid_hit_rate", capacity, "BEST_BASELINE"
    )
    tokens = {
        workload: _comparison(
            aggregate,
            workload,
            "safe_token_saving_ratio",
            capacity,
            "BEST_BASELINE",
        )
        for workload in PRIMARY_WORKLOADS
    }
    return {
        "gate_3_phase_shift_vhr": {
            "observed_delta": _number(shift["delta_mean"], "gate 3 delta"),
            "observed_ci_low": _number(shift["delta_ci_low"], "gate 3 CI"),
            "observed_ci_high": _number(shift["delta_ci_high"], "gate 3 CI"),
            "observed_holm_p": _number(shift["p_holm"], "gate 3 p"),
            "passes": (
                _number(shift["delta_mean"], "gate 3 delta") >= 0.02
                and _number(shift["delta_ci_low"], "gate 3 CI") > 0.0
                and _number(shift["p_holm"], "gate 3 p") <= 0.05
            ),
        },
        "gate_4_scan_return_vhr": {
            "observed_delta": _number(scan["delta_mean"], "gate 4 delta"),
            "observed_ci_low": _number(scan["delta_ci_low"], "gate 4 CI"),
            "observed_ci_high": _number(scan["delta_ci_high"], "gate 4 CI"),
            "observed_holm_p": _number(scan["p_holm"], "gate 4 p"),
            "passes": (
                _number(scan["delta_mean"], "gate 4 delta") >= 0.05
                and _number(scan["delta_ci_low"], "gate 4 CI") > 0.0
                and _number(scan["p_holm"], "gate 4 p") <= 0.05
            ),
        },
        "gate_5_stationary_nonregression": {
            "observed_delta": _number(
                stationary["delta_mean"], "gate 5 delta"
            ),
            "observed_ci_low": _number(
                stationary["delta_ci_low"], "gate 5 CI"
            ),
            "observed_ci_high": _number(
                stationary["delta_ci_high"], "gate 5 CI"
            ),
            "passes": _number(stationary["delta_ci_low"], "gate 5 CI") >= -0.01,
        },
        "gate_6_token_saving_nonregression": {
            "workloads": {
                workload: {
                    "observed_delta": _number(row["delta_mean"], "gate 6 delta"),
                    "observed_ci_low": _number(row["delta_ci_low"], "gate 6 CI"),
                    "observed_ci_high": _number(row["delta_ci_high"], "gate 6 CI"),
                }
                for workload, row in tokens.items()
            },
            "passes": all(
                _number(row["delta_ci_low"], "gate 6 CI") >= -0.005
                for row in tokens.values()
            ),
        },
    }


def _verify_recorded_synthetic_gates(
    metadata: Dict[str, Any], computed: Dict[str, Dict[str, Any]]
) -> None:
    recorded = metadata.get("synthetic_success_gates")
    if not isinstance(recorded, dict):
        raise AnalysisError("full metadata lacks synthetic gate adjudication")
    for name, observed in computed.items():
        expected = recorded.get(name)
        if not isinstance(expected, dict):
            raise AnalysisError("full metadata lacks %s" % name)
        if expected.get("passes") is not observed["passes"]:
            raise AnalysisError("recorded %s verdict disagrees with aggregate" % name)
        for field in observed:
            if field.startswith("observed_") and not _close(
                _number(expected[field], "%s %s" % (name, field)),
                float(observed[field]),
            ):
                raise AnalysisError("recorded %s value disagrees with aggregate" % name)
        if name.endswith("token_saving_nonregression"):
            for workload, values in observed["workloads"].items():
                expected_values = expected.get("workloads", {}).get(workload, {})
                for field, value in values.items():
                    if not _close(
                        _number(expected_values.get(field), "%s %s" % (name, field)),
                        float(value),
                    ):
                        raise AnalysisError(
                            "recorded %s/%s disagrees with aggregate"
                            % (name, workload)
                        )


def _load_integrations(paths: Sequence[Path]) -> Dict[str, Any]:
    if not paths:
        return {
            "rows": [],
            "manifests": [],
            "report": _pending_report(None, "integration result directory not supplied"),
        }
    rows: List[Dict[str, str]] = []
    manifests = []
    reports = []
    for supplied in paths:
        directory = Path(supplied)
        if not directory.is_dir():
            reports.append(
                _pending_report(directory, "integration result directory is absent")
            )
            continue
        manifest_path = directory / "manifest.json"
        runs_path = directory / "runs.csv"
        if not manifest_path.is_file():
            reports.append(
                _pending_report(
                    directory,
                    "incomplete integration result: manifest.json is absent",
                    status="incomplete",
                )
            )
            continue
        manifest = _read_json(manifest_path)
        if manifest.get("schema_version") != INTEGRATION_SCHEMA:
            reports.append(
                {
                    "status": "stale",
                    "path": str(directory.resolve()),
                    "observed_schema": manifest.get("schema_version"),
                    "required_schema": INTEGRATION_SCHEMA,
                }
            )
            continue
        if not runs_path.is_file():
            raise AnalysisError("integration manifest endorses missing runs.csv")
        entry = manifest.get("artifacts", {}).get("runs.csv")
        expected = _hash_value(entry, "integration runs.csv")
        observed = sha256_file(runs_path)
        if expected != observed:
            raise AnalysisError("integration runs.csv SHA-256 mismatch")
        part, fields = _read_csv(runs_path)
        _require_exact_fields(fields, INTEGRATION_FIELDS, "integration runs.csv")
        expected_run_rows = _rows_value(entry)
        if expected_run_rows is not None and expected_run_rows != len(part):
            raise AnalysisError("integration run row count mismatch")
        _validate_integration_rows(part, manifest)
        resources_report = "not supplied"
        resource_entry = manifest.get("artifacts", {}).get("resources.jsonl")
        resources_path = directory / "resources.jsonl"
        if resources_path.is_file() and resource_entry is not None:
            resource_hash = sha256_file(resources_path)
            if resource_hash != _hash_value(resource_entry, "integration resources"):
                raise AnalysisError("integration resources.jsonl SHA-256 mismatch")
            expected_rows = _rows_value(resource_entry)
            observed_rows = sum(
                1 for line in resources_path.read_text(encoding="utf-8").splitlines() if line
            )
            if expected_rows is not None and expected_rows != observed_rows:
                raise AnalysisError("integration resource row count mismatch")
            resources_report = {
                "sha256": resource_hash,
                "rows": observed_rows,
            }
        rows.extend(part)
        manifests.append(manifest)
        reports.append(
            {
                "status": "verified",
                "path": str(directory.resolve()),
                "schema_version": INTEGRATION_SCHEMA,
                "mode": manifest.get("config", {}).get("mode"),
                "seed": manifest.get("config", {}).get("seed"),
                "runs_sha256": observed,
                "runs": len(part),
                "resources": resources_report,
                "git": manifest.get("git"),
            }
        )

    verified = [report for report in reports if report["status"] == "verified"]
    comparable = _integration_comparable(manifests)
    status = "verified" if verified else "pending"
    report = {
        "status": status,
        "inputs": reports,
        "verified_inputs": len(verified),
        "distinct_seeds": sorted(
            {_integer(row["seed"], "integration seed") for row in rows}
        ),
        "comparable_across_seeds": comparable,
    }
    return {"rows": rows, "manifests": manifests, "report": report}


def _validate_integration_rows(
    rows: Sequence[Dict[str, str]], manifest: Dict[str, Any]
) -> None:
    if not rows:
        raise AnalysisError("integration runs.csv is empty")
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise AnalysisError("integration manifest lacks config provenance")
    policies = set(manifest.get("policies", []))
    observed_policies = set()
    identities = set()
    for row in rows:
        if row["schema_version"] != INTEGRATION_SCHEMA:
            raise AnalysisError("integration run schema mismatch")
        policy = row["policy"]
        observed_policies.add(policy)
        identity = (policy, row["seed"], row["run_id"])
        if identity in identities:
            raise AnalysisError("duplicate integration run identity")
        identities.add(identity)
        if row["trace_hash"] != manifest.get("trace_hash"):
            raise AnalysisError("integration trace hash mismatch")
        for field in ("mode", "workload", "seed", "requests", "capacity"):
            if str(row[field]) != str(config.get(field)):
                raise AnalysisError("integration config mismatch for %s" % field)
        requests = _integer(row["requests"], "integration requests")
        hits = _integer(row["hits"], "integration hits")
        misses = _integer(row["misses"], "integration misses")
        valid = _integer(row["valid_hits"], "integration valid hits")
        false = _integer(row["false_hits"], "integration false hits")
        if hits + misses != requests or valid + false != hits:
            raise AnalysisError("integration hit-count identity failed")
        for field in (
            "throughput_qps",
            "total_p95_us",
            "policy_p95_us",
            "rss_peak_sampled_bytes",
        ):
            if _number(row[field], "integration %s" % field) < 0:
                raise AnalysisError("integration measurements must be nonnegative")
        scalar = _integer(row["final_scalar_count"], "integration scalar count")
        vector = _integer(row["final_vector_count"], "integration vector count")
        capacity = _integer(row["capacity"], "integration capacity")
        if scalar != vector or scalar > capacity:
            raise AnalysisError("integration storage counts violate capacity")
    if policies != observed_policies:
        raise AnalysisError("integration policy manifest does not match runs")
    if manifest.get("identical_trace_verified") is not True:
        raise AnalysisError("integration manifest did not verify identical traces")


def _integration_comparable(manifests: Sequence[Dict[str, Any]]) -> bool:
    if not manifests:
        return False
    normalized = []
    for manifest in manifests:
        config = dict(manifest.get("config", {}))
        config.pop("seed", None)
        normalized.append(json.dumps(config, sort_keys=True, separators=(",", ":")))
    return len(set(normalized)) == 1


def _load_qqp(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {
            "result": None,
            "report": _pending_report(None, "QQP result not supplied"),
        }
    result_path = Path(path)
    if result_path.is_dir():
        result_path = result_path / "result.json"
    if not result_path.is_file():
        return {
            "result": None,
            "report": _pending_report(result_path, "QQP result is absent"),
        }
    result = _read_json(result_path)
    if result.get("schema_version") != QQP_SCHEMA:
        return {
            "result": None,
            "report": {
                "status": "stale",
                "path": str(result_path.resolve()),
                "observed_schema": result.get("schema_version"),
                "required_schema": QQP_SCHEMA,
            },
        }
    _validate_qqp_result(result)
    provenance = _qqp_provenance(result_path)
    return {
        "result": result,
        "report": {
            "status": "verified",
            "path": str(result_path.resolve()),
            "schema_version": QQP_SCHEMA,
            "sha256": sha256_file(result_path),
            "result_status": result.get("status"),
            "provenance": provenance,
        },
    }


def _validate_qqp_result(result: Dict[str, Any]) -> None:
    status = result.get("status")
    if status == "no_threshold_met_precision_gate":
        expected_fields = {
            "schema_version",
            "status",
            "selected_threshold",
            "calibration_pairs",
            "test_pairs_not_evaluated",
        }
        if set(result) != expected_fields:
            raise AnalysisError("failed QQP result has unexpected fields")
        if result.get("selected_threshold") is not None:
            raise AnalysisError("failed QQP result unexpectedly selects a threshold")
        calibration_pairs = _integer(
            result.get("calibration_pairs"), "QQP calibration pairs"
        )
        test_pairs = _integer(
            result.get("test_pairs_not_evaluated"),
            "QQP test pairs not evaluated",
        )
        if calibration_pairs <= 0 or test_pairs <= 0:
            raise AnalysisError("failed QQP result pair counts must be positive")
        return
    if status != "ok":
        raise AnalysisError("QQP result has an unknown status")
    threshold = _number(result.get("selected_threshold"), "QQP threshold")
    if not 0.80 <= threshold <= 0.99:
        raise AnalysisError("QQP threshold is outside the calibrated grid")
    for split in ("calibration", "test"):
        metrics = result.get(split)
        if not isinstance(metrics, dict):
            raise AnalysisError("QQP result lacks %s metrics" % split)
        tp = _integer(metrics.get("true_positive"), "QQP true positives")
        fp = _integer(metrics.get("false_positive"), "QQP false positives")
        tn = _integer(metrics.get("true_negative"), "QQP true negatives")
        fn = _integer(metrics.get("false_negative"), "QQP false negatives")
        if min(tp, fp, tn, fn) < 0:
            raise AnalysisError("QQP confusion counts must be nonnegative")
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        if not _close(precision, _number(metrics.get("precision"), "QQP precision")):
            raise AnalysisError("QQP precision does not match confusion counts")
        if not _close(recall, _number(metrics.get("recall"), "QQP recall")):
            raise AnalysisError("QQP recall does not match confusion counts")
        wilson = _wilson_lower(tp, tp + fp)
        if not _close(
            wilson,
            _number(
                metrics.get("wilson_precision_lower_one_sided_95"),
                "QQP Wilson lower bound",
            ),
        ):
            raise AnalysisError("QQP Wilson lower bound does not reconcile")
        pair_key = "%s_pairs" % split
        if _integer(result.get(pair_key), pair_key) != tp + fp + tn + fn:
            raise AnalysisError("QQP %s pair count does not reconcile" % split)


def _validate_qqp_calibration_grid(
    path: Path, expected_pairs: int, result: Dict[str, Any]
) -> Dict[str, Any]:
    if not path.is_file():
        raise AnalysisError("QQP calibration threshold grid is missing")
    rows, fields = _read_csv(path)
    _require_exact_fields(fields, QQP_CALIBRATION_FIELDS, "QQP calibration grid")
    if len(rows) != 20:
        raise AnalysisError("QQP calibration grid must contain 20 thresholds")

    normalized = []
    for index, row in enumerate(rows):
        threshold = _number(row["threshold"], "QQP calibration threshold")
        expected_threshold = (80 + index) / 100.0
        if not _close(threshold, expected_threshold):
            raise AnalysisError("QQP calibration threshold grid is not 0.80..0.99")
        tp = _integer(row["true_positive"], "QQP calibration true positives")
        fp = _integer(row["false_positive"], "QQP calibration false positives")
        tn = _integer(row["true_negative"], "QQP calibration true negatives")
        fn = _integer(row["false_negative"], "QQP calibration false negatives")
        if min(tp, fp, tn, fn) < 0 or tp + fp + tn + fn != expected_pairs:
            raise AnalysisError("QQP calibration confusion counts do not reconcile")
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        false_positive_rate = fp / (fp + tn) if fp + tn else 0.0
        wilson = _wilson_lower(tp, tp + fp)
        expected_metrics = (
            ("precision", precision),
            ("recall", recall),
            ("false_positive_rate", false_positive_rate),
            ("wilson_precision_lower_one_sided_95", wilson),
        )
        if any(
            not _close(_number(row[name], "QQP calibration %s" % name), value)
            for name, value in expected_metrics
        ):
            raise AnalysisError("QQP calibration metric does not reconcile")
        normalized.append(
            {
                "threshold": threshold,
                "true_positive": tp,
                "false_positive": fp,
                "true_negative": tn,
                "false_negative": fn,
                "precision": precision,
                "recall": recall,
                "false_positive_rate": false_positive_rate,
                "wilson_precision_lower_one_sided_95": wilson,
            }
        )

    qualifying = [
        row
        for row in normalized
        if row["wilson_precision_lower_one_sided_95"] >= 0.99
    ]
    if result.get("status") == "no_threshold_met_precision_gate":
        if qualifying:
            raise AnalysisError(
                "failed QQP result contradicts its calibration threshold grid"
            )
    else:
        if not qualifying or not _close(
            result.get("selected_threshold"), qualifying[0]["threshold"]
        ):
            raise AnalysisError("QQP selected threshold is not the first qualifier")
    best = max(
        normalized,
        key=lambda row: (
            row["wilson_precision_lower_one_sided_95"],
            -row["threshold"],
        ),
    )
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "rows": len(normalized),
        "qualifying_thresholds": len(qualifying),
        "best_wilson_row": best,
    }


def _qqp_provenance(result_path: Path) -> Dict[str, Any]:
    root = result_path.parent.parent
    prepared_manifest = root / "prepared" / "manifest.json"
    embeddings_manifest = root / "embeddings" / "manifest.json"
    report: Dict[str, Any] = {
        "status": "partial",
        "note": "result.json contains no direct links to preparation or embedding manifests",
    }
    if not prepared_manifest.is_file() or not embeddings_manifest.is_file():
        return report
    prepared = _read_json(prepared_manifest)
    embeddings = _read_json(embeddings_manifest)
    result = _read_json(result_path)
    if prepared.get("schema_version") != QQP_SCHEMA:
        raise AnalysisError("QQP prepared manifest schema mismatch")
    if prepared.get("archive_sha256") != QQP_ARCHIVE_SHA256:
        raise AnalysisError("QQP archive provenance mismatch")
    if embeddings.get("schema_version") != QQP_SCHEMA:
        raise AnalysisError("QQP embedding manifest schema mismatch")
    if embeddings.get("tokenizer_revision") != QQP_TOKENIZER_REVISION:
        raise AnalysisError("QQP tokenizer revision mismatch")
    if embeddings.get("model_revision") != QQP_MODEL_REVISION:
        raise AnalysisError("QQP model revision mismatch")
    if embeddings.get("tokenizer_repository") != (
        "GPTCache/paraphrase-albert-small-v2"
    ):
        raise AnalysisError("QQP tokenizer repository mismatch")
    if embeddings.get("model_repository") != "GPTCache/paraphrase-albert-onnx":
        raise AnalysisError("QQP model repository mismatch")
    if (
        embeddings.get("dimension") != 768
        or embeddings.get("dtype") != "float32"
        or embeddings.get("max_length") != 512
        or embeddings.get("normalized") is not True
    ):
        raise AnalysisError("QQP embedding configuration mismatch")
    unique_questions = _integer(
        prepared.get("unique_questions_kept"), "QQP prepared question count"
    )
    if _integer(embeddings.get("rows"), "QQP embedding rows") != unique_questions:
        raise AnalysisError("QQP embedding rows do not match prepared questions")
    _hash_value(prepared.get("pairs_sha256"), "QQP prepared pairs")
    _hash_value(prepared.get("texts_sha256"), "QQP prepared texts")
    _hash_value(embeddings.get("embeddings_sha256"), "QQP embeddings")
    _hash_value(embeddings.get("text_ids_sha256"), "QQP text IDs")

    counts = prepared.get("counts")
    expected_count_fields = {
        "calibration_negative",
        "calibration_positive",
        "test_negative",
        "test_positive",
    }
    if not isinstance(counts, dict) or set(counts) != expected_count_fields:
        raise AnalysisError("QQP prepared split counts are invalid")
    calibration_pairs = sum(
        _integer(counts[name], "QQP prepared %s" % name)
        for name in ("calibration_negative", "calibration_positive")
    )
    test_pairs = sum(
        _integer(counts[name], "QQP prepared %s" % name)
        for name in ("test_negative", "test_positive")
    )
    if result.get("status") == "no_threshold_met_precision_gate":
        if (
            result.get("calibration_pairs") != calibration_pairs
            or result.get("test_pairs_not_evaluated") != test_pairs
        ):
            raise AnalysisError("failed QQP result counts do not match preparation")
    elif (
        result.get("calibration_pairs") != calibration_pairs
        or result.get("test_pairs") != test_pairs
    ):
        raise AnalysisError("QQP result counts do not match preparation")
    calibration_grid = _validate_qqp_calibration_grid(
        result_path.parent / "calibration_thresholds.csv",
        calibration_pairs,
        result,
    )
    verified_artifacts = {}
    for name, key in (
        ("pairs.jsonl", "pairs_sha256"),
        ("texts.jsonl", "texts_sha256"),
    ):
        candidate = root / "prepared" / name
        expected = prepared.get(key)
        if candidate.is_file() and isinstance(expected, str):
            observed = sha256_file(candidate)
            if observed != expected:
                raise AnalysisError("QQP prepared %s SHA-256 mismatch" % name)
            verified_artifacts["prepared/%s" % name] = observed
    embeddings_path = root / "embeddings" / "embeddings.npy"
    if embeddings_path.is_file():
        observed = sha256_file(embeddings_path)
        if observed != embeddings.get("embeddings_sha256"):
            raise AnalysisError("QQP embeddings.npy SHA-256 mismatch")
        verified_artifacts["embeddings/embeddings.npy"] = observed
    report.update(
        {
            "status": "partial_with_verified_siblings",
            "note": (
                "sibling stage manifests verify their own inputs, but "
                "result.json does not cryptographically link to them"
            ),
            "prepared_manifest_sha256": sha256_file(prepared_manifest),
            "embeddings_manifest_sha256": sha256_file(embeddings_manifest),
            "verified_artifacts": verified_artifacts,
            "archive_sha256": prepared["archive_sha256"],
            "tokenizer_revision": embeddings["tokenizer_revision"],
            "model_revision": embeddings["model_revision"],
            "calibration_grid": calibration_grid,
        }
    )
    return report


def _load_moss(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {
            "manifest": None,
            "runs": [],
            "report": _pending_report(None, "MOSS result directory not supplied"),
        }
    directory = Path(path)
    if not directory.is_dir():
        return {
            "manifest": None,
            "runs": [],
            "report": _pending_report(directory, "MOSS result directory is absent"),
        }
    manifest_path = directory / "manifest.json"
    runs_path = directory / "runs.csv"
    if not manifest_path.is_file():
        return {
            "manifest": None,
            "runs": [],
            "report": _pending_report(
                directory,
                "incomplete MOSS result: manifest.json is absent",
                status="incomplete",
            ),
        }
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != MOSS_SCHEMA:
        return {
            "manifest": None,
            "runs": [],
            "report": {
                "status": "stale",
                "path": str(directory.resolve()),
                "observed_schema": manifest.get("schema_version"),
                "required_schema": MOSS_SCHEMA,
            },
        }
    if not runs_path.is_file():
        raise AnalysisError("MOSS manifest endorses missing runs.csv")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise AnalysisError("MOSS manifest lacks artifact hashes")
    expected_names = (
        "pool.jsonl",
        "prepare-manifest.json",
        "requests.jsonl",
        "runs.csv",
    )
    verified_artifacts = {}
    for name in expected_names:
        artifact_path = directory / name
        if not artifact_path.is_file():
            raise AnalysisError("MOSS manifest endorses missing %s" % name)
        expected = _hash_value(artifacts.get(name), "MOSS %s" % name)
        observed = sha256_file(artifact_path)
        if expected != observed:
            raise AnalysisError("MOSS %s SHA-256 mismatch" % name)
        verified_artifacts[name] = observed
    observed = verified_artifacts["runs.csv"]
    rows, fields = _read_csv(runs_path)
    _require_exact_fields(fields, MOSS_FIELDS, "MOSS runs.csv")
    if not rows:
        raise AnalysisError("MOSS runs.csv is empty")
    for row in rows:
        if row["schema_version"] != MOSS_SCHEMA:
            raise AnalysisError("MOSS run schema mismatch")
        for field in ("requests", "valid_hits", "false_hits", "live_model_calls"):
            if _integer(row[field], "MOSS %s" % field) < 0:
                raise AnalysisError("MOSS counts must be nonnegative")
        _rate(row["safe_token_saving_ratio"], "MOSS token saving ratio")
    source = manifest.get("source") or {}
    source_kind = source.get("source_kind")
    if source_kind == "official":
        if source.get("revision") != MOSS_REVISION:
            raise AnalysisError("MOSS source revision mismatch")
        if source.get("archive_sha256") != MOSS_ARCHIVE_SHA256:
            raise AnalysisError("MOSS source checksum mismatch")
        provenance = "official_verified"
    else:
        provenance = "test_fixture"
    prepare_manifest = _read_json(directory / "prepare-manifest.json")
    if prepare_manifest.get("schema_version") != MOSS_SCHEMA:
        raise AnalysisError("MOSS prepare manifest schema mismatch")
    if prepare_manifest.get("stage") != "prepare":
        raise AnalysisError("MOSS prepare manifest has the wrong stage")
    if manifest.get("prepared_manifest_sha256") != verified_artifacts[
        "prepare-manifest.json"
    ]:
        raise AnalysisError("MOSS retained prepare-manifest link mismatch")
    if manifest.get("pool_sha256") != verified_artifacts["pool.jsonl"]:
        raise AnalysisError("MOSS pool provenance link mismatch")
    preparation = manifest.get("preparation")
    if not isinstance(preparation, dict):
        raise AnalysisError("MOSS replay manifest lacks retained preparation metadata")
    if preparation.get("source_irregularities") != prepare_manifest.get(
        "source_irregularities"
    ):
        raise AnalysisError("MOSS source-irregularity records disagree")
    with (directory / "requests.jsonl").open(
        "r", encoding="utf-8"
    ) as request_source:
        request_rows = sum(1 for line in request_source if line.strip())
    expected_requests = sum(
        _integer(row["requests"], "MOSS requests") for row in rows
    )
    if request_rows != expected_requests:
        raise AnalysisError("MOSS request row count does not match runs.csv")
    return {
        "manifest": manifest,
        "runs": rows,
        "report": {
            "status": "verified",
            "path": str(directory.resolve()),
            "schema_version": MOSS_SCHEMA,
            "manifest_sha256": sha256_file(manifest_path),
            "runs_sha256": observed,
            "artifacts": verified_artifacts,
            "runs": len(rows),
            "request_rows": request_rows,
            "source_provenance": provenance,
            "source_revision": source.get("revision"),
            "source_irregularities": preparation.get("source_irregularities"),
        },
    }


def _evidence_reference(
    root: Path, value: Any, label: str
) -> Tuple[Path, Dict[str, Any]]:
    if not isinstance(value, dict):
        raise AnalysisError("%s must be a path/SHA-256 object" % label)
    relative_value = value.get("path")
    if not isinstance(relative_value, str) or not relative_value:
        raise AnalysisError("%s lacks a relative path" % label)
    relative = Path(relative_value)
    if relative.is_absolute():
        raise AnalysisError("%s path must be relative" % label)
    if (
        relative.as_posix() != relative_value
        or any(part in (".", "..") for part in relative_value.split("/"))
    ):
        raise AnalysisError("%s path is not canonical" % label)
    root = Path(root).resolve()
    unresolved = root / relative
    component = root
    for part in relative.parts:
        component = component / part
        if component.is_symlink():
            raise AnalysisError("%s path contains a symlink" % label)
    candidate = unresolved.resolve()
    try:
        inside_root = os.path.commonpath((str(root), str(candidate))) == str(root)
    except ValueError:
        inside_root = False
    if not inside_root:
        raise AnalysisError("%s path escapes its evidence directory" % label)
    if not candidate.is_file():
        raise AnalysisError("%s referenced file is missing" % label)
    expected = _hash_value(value, label)
    observed = sha256_file(candidate)
    if observed != expected:
        raise AnalysisError("%s SHA-256 mismatch" % label)
    return candidate, {
        "path": relative.as_posix(),
        "sha256": observed,
        "bytes": candidate.stat().st_size,
    }


def _commit_value(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise AnalysisError("%s lacks a commit SHA" % label)
    commit = value.lower()
    if len(commit) != 40 or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise AnalysisError("%s lacks a 40-character commit SHA" % label)
    return commit


def _current_git_head() -> Dict[str, Any]:
    """Return the exact commit checked out at the analyzer's project root."""

    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(PROJECT_ROOT),
                "rev-parse",
                "--show-toplevel",
                "HEAD^{commit}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {
            "status": "unavailable",
            "head_commit": None,
            "reason": "Git metadata is unavailable at the project root",
        }
    lines = completed.stdout.splitlines()
    if completed.returncode != 0 or len(lines) != 2:
        return {
            "status": "unavailable",
            "head_commit": None,
            "reason": "Git metadata is unavailable at the project root",
        }
    try:
        repository_root = Path(lines[0]).resolve()
        head_commit = _commit_value(lines[1].strip(), "current Git HEAD")
    except (AnalysisError, OSError):
        return {
            "status": "unavailable",
            "head_commit": None,
            "reason": "Git metadata is invalid at the project root",
        }
    if repository_root != PROJECT_ROOT.resolve():
        return {
            "status": "unavailable",
            "head_commit": None,
            "reason": "Git metadata does not belong to the project root",
        }
    return {
        "status": "available",
        "head_commit": head_commit,
        "project_root": str(repository_root),
    }


PACKAGING_DOCUMENT_PATHS = {
    "docs/project/chart-map.md",
    "docs/project/completion-audit.md",
    "docs/project/draft-pr.md",
    "docs/project/report.pdf",
    "docs/project/report.tex",
    "docs/project/reproducibility.md",
}


def _is_packaging_change(status: str, path: str) -> bool:
    if path in PACKAGING_DOCUMENT_PATHS:
        return status in ("A", "M")
    if path == "artifacts/samples/README.md" or path.startswith(
        "artifacts/samples/analysis/"
    ):
        return status in ("A", "M")
    if (
        path == "artifacts/samples/SHA256SUMS"
        or path.startswith("artifacts/samples/qqp/")
        or path.startswith("artifacts/samples/verification/")
    ):
        return status == "A"
    return False


def _git_bytes(command: Sequence[str], label: str) -> bytes:
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AnalysisError("%s could not be verified" % label) from exc
    if completed.returncode != 0 or not isinstance(completed.stdout, bytes):
        raise AnalysisError("%s could not be verified" % label)
    return completed.stdout


def _require_clean_worktree(label: str) -> None:
    status = _git_bytes(
        [
            "git",
            "-c",
            "tar.umask=0002",
            "-C",
            str(PROJECT_ROOT),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        "%s current worktree state" % label,
    )
    if status:
        raise AnalysisError("%s binding requires a clean worktree" % label)


def _packaging_descendant_changes(
    source_commit: str, current_commit: str, label: str
) -> List[Dict[str, str]]:
    prefix = ["git", "-C", str(PROJECT_ROOT)]

    parents_raw = _git_bytes(
        prefix + ["rev-list", "--parents", "-n", "1", current_commit],
        "%s current commit parentage" % label,
    )
    try:
        parent_tokens = parents_raw.decode("ascii").split()
    except UnicodeDecodeError as exc:
        raise AnalysisError(
            "%s current commit parentage is invalid" % label
        ) from exc
    if parent_tokens != [current_commit, source_commit]:
        raise AnalysisError(
            "%s source commit does not match current Git HEAD as its direct sole parent"
            % label
        )

    raw_changes = _git_bytes(
        prefix
        + [
            "diff",
            "--name-status",
            "-z",
            "--no-renames",
            "%s..%s" % (source_commit, current_commit),
            "--",
        ],
        "%s packaging-descendant diff" % label,
    )
    if raw_changes and not raw_changes.endswith(b"\0"):
        raise AnalysisError(
            "%s packaging-descendant diff produced invalid path data" % label
        )
    try:
        tokens = [
            value.decode("utf-8")
            for value in raw_changes.split(b"\0")
            if value
        ]
    except UnicodeDecodeError as exc:
        raise AnalysisError(
            "%s packaging-descendant paths are not valid UTF-8" % label
        ) from exc

    changes = []
    rejected = []
    index = 0
    while index < len(tokens):
        status_value = tokens[index]
        index += 1
        path_count = 2 if status_value.startswith(("R", "C")) else 1
        if index + path_count > len(tokens):
            raise AnalysisError(
                "%s packaging-descendant diff produced invalid path data" % label
            )
        paths = tokens[index : index + path_count]
        index += path_count
        if status_value not in ("A", "M") or path_count != 1:
            rejected.append("%s:%s" % (status_value, " -> ".join(paths)))
            continue
        path = paths[0]
        changes.append({"status": status_value, "path": path})
        if not _is_packaging_change(status_value, path):
            rejected.append("%s:%s" % (status_value, path))
    if rejected:
        raise AnalysisError(
            "%s source commit does not match current Git HEAD; "
            "non-packaging or unsupported descendant changes: %s"
            % (label, ", ".join(rejected))
        )
    if len(changes) != len({item["path"] for item in changes}):
        raise AnalysisError(
            "%s packaging-descendant diff contains duplicate paths" % label
        )
    if not changes:
        raise AnalysisError(
            "%s packaging descendant contains no packaging changes" % label
        )
    return changes


def _commit_binding(
    source_commit: str, label: str, git_head: Dict[str, Any]
) -> Dict[str, Any]:
    current = git_head.get("head_commit")
    if git_head.get("status") != "available" or current is None:
        return {
            "status": "unavailable",
            "evidence_source_commit": source_commit,
            "current_git_head": None,
            "reason": git_head.get(
                "reason", "current Git HEAD could not be determined"
            ),
        }
    _require_clean_worktree(label)
    if source_commit == current:
        return {
            "status": "verified",
            "evidence_source_commit": source_commit,
            "current_git_head": current,
            "exact_match": True,
        }
    changes = _packaging_descendant_changes(source_commit, current, label)
    return {
        "status": "verified_packaging_descendant",
        "evidence_source_commit": source_commit,
        "current_git_head": current,
        "exact_match": False,
        "changed_paths": [item["path"] for item in changes],
        "changed_path_status": changes,
    }


def _pending_commit_binding(
    git_head: Dict[str, Any], reason: str
) -> Dict[str, Any]:
    if git_head.get("status") != "available":
        return {
            "status": "unavailable",
            "evidence_source_commit": None,
            "current_git_head": None,
            "reason": git_head.get(
                "reason", "current Git HEAD could not be determined"
            ),
        }
    return {
        "status": "not_evaluated",
        "evidence_source_commit": None,
        "current_git_head": git_head["head_commit"],
        "reason": reason,
    }


def _evidence_is_claimable(report: Dict[str, Any]) -> bool:
    return (
        report.get("status") == "verified"
        and report.get("commit_binding", {}).get("status")
        in ("verified", "verified_packaging_descendant")
    )


def _last_nonempty_line(path: Path, label: str) -> str:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeError) as exc:
        raise AnalysisError("%s is not valid UTF-8 text" % label) from exc
    nonempty = [line for line in lines if line]
    if not nonempty:
        raise AnalysisError("%s is empty" % label)
    return nonempty[-1]


def _jsonl_object_count(path: Path, label: str) -> int:
    count = 0
    try:
        with Path(path).open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AnalysisError(
                        "%s line %d is not valid JSON" % (label, line_number)
                    ) from exc
                if not isinstance(value, dict):
                    raise AnalysisError(
                        "%s line %d is not a JSON object" % (label, line_number)
                    )
                count += 1
    except (OSError, UnicodeError) as exc:
        raise AnalysisError("%s is not valid UTF-8 JSONL" % label) from exc
    return count


def _validate_ci_benchmark_artifacts(
    paths: Dict[str, Path], label: str
) -> Dict[str, Any]:
    manifest = _read_json(paths["manifest.json"])
    if manifest.get("schema_version") != RUN_SCHEMA:
        raise AnalysisError("%s manifest schema mismatch" % label)
    if manifest.get("timing_is_measured") is not False:
        raise AnalysisError("%s unexpectedly measures timing" % label)
    if not isinstance(manifest.get("config"), dict):
        raise AnalysisError("%s manifest config is missing" % label)
    trace_hashes = manifest.get("trace_hashes")
    if not isinstance(trace_hashes, dict) or not trace_hashes:
        raise AnalysisError("%s manifest trace hashes are missing" % label)

    request_rows = _jsonl_object_count(paths["requests.jsonl"], "%s requests" % label)
    run_rows, run_fields = _read_csv(paths["runs.csv"])
    _require_exact_fields(run_fields, RUN_FIELDS, "%s runs" % label)
    if not request_rows or not run_rows:
        raise AnalysisError("%s is empty" % label)
    run_ids = []
    total_requests = 0
    for row in run_rows:
        if row["schema_version"] != RUN_SCHEMA:
            raise AnalysisError("%s run uses a stale schema" % label)
        run_id = row["run_id"]
        if not run_id or run_id in run_ids:
            raise AnalysisError("%s has missing or duplicate run IDs" % label)
        run_ids.append(run_id)
        requests = _integer(row["requests"], "%s run requests" % label)
        if requests <= 0:
            raise AnalysisError("%s run request count is not positive" % label)
        total_requests += requests
        workload = row["workload"]
        if trace_hashes.get(workload) != row["trace_hash"]:
            raise AnalysisError("%s run trace hash does not match its manifest" % label)
    if manifest.get("run_ids") != run_ids:
        raise AnalysisError("%s manifest run IDs do not match runs.csv" % label)
    if request_rows != total_requests:
        raise AnalysisError("%s request rows do not match runs.csv" % label)
    return {
        "schema_version": RUN_SCHEMA,
        "timing_is_measured": False,
        "request_rows": request_rows,
        "run_rows": len(run_rows),
        "run_ids": run_ids,
        "trace_hashes": dict(sorted(trace_hashes.items())),
    }


def _sha256_list(path: Path, label: str) -> Dict[str, str]:
    entries: Dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AnalysisError("%s is not valid UTF-8 text" % label) from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise AnalysisError("%s line %d is invalid" % (label, line_number))
        digest = _hash_value(parts[0], "%s line %d" % (label, line_number))
        name = parts[1].strip()
        if name.startswith("*"):
            name = name[1:]
        if not name or name in entries or Path(name).name != name:
            raise AnalysisError("%s line %d has an invalid name" % (label, line_number))
        entries[name] = digest
    return entries


def _normalized_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _current_dependency_lock_sha256(label: str) -> str:
    lock_path = PROJECT_ROOT / "requirements-project.lock"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise AnalysisError(
            "%s cannot be bound to the current requirements-project.lock" % label
        )
    try:
        return sha256_file(lock_path)
    except OSError as exc:
        raise AnalysisError(
            "%s cannot be bound to the current requirements-project.lock" % label
        ) from exc


def _git_archive_sha256(source_commit: str, label: str) -> str:
    archive = _git_bytes(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "archive",
            "--format=tar",
            source_commit,
        ],
        "%s exact source archive" % label,
    )
    return hashlib.sha256(archive).hexdigest()


def _validate_source_archive(
    root: Path,
    value: Any,
    source_commit: str,
    git_head: Dict[str, Any],
    label: str,
) -> Dict[str, Any]:
    required = {
        "path",
        "sha256",
        "archive_sha256",
        "format",
        "source_commit",
        "tracked_file_count",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise AnalysisError("%s source archive metadata is invalid" % label)
    if value.get("format") != "git-archive-tar":
        raise AnalysisError("%s source archive format is invalid" % label)
    archive_commit = _commit_value(
        value.get("source_commit"), "%s source archive" % label
    )
    if archive_commit != source_commit:
        raise AnalysisError("%s source archive commit does not match" % label)
    archive_sha256 = _hash_value(
        value.get("archive_sha256"), "%s source archive" % label
    )
    tracked_file_count = value.get("tracked_file_count")
    if type(tracked_file_count) is not int or tracked_file_count <= 0:
        raise AnalysisError("%s source archive file count is invalid" % label)
    archive_path, archive_report = _evidence_reference(
        root, value, "%s source archive checksum" % label
    )
    expected_name = "source-%s.tar" % source_commit
    expected_line = "%s  %s\n" % (archive_sha256, expected_name)
    try:
        observed_line = archive_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AnalysisError(
            "%s source archive checksum is not valid UTF-8" % label
        ) from exc
    if observed_line != expected_line:
        raise AnalysisError(
            "%s source archive checksum line does not match its metadata" % label
        )

    git_recomputed = git_head.get("status") == "available"
    if git_recomputed:
        recomputed = _git_archive_sha256(source_commit, label)
        if recomputed != archive_sha256:
            raise AnalysisError(
                "%s source archive does not match Git commit %s"
                % (label, source_commit)
            )
    return {
        **archive_report,
        "archive_sha256": archive_sha256,
        "format": "git-archive-tar",
        "source_commit": source_commit,
        "tracked_file_count": tracked_file_count,
        "git_recomputed": git_recomputed,
    }


def _validate_host_environment(
    lock_path: Path, install_log: Path, installed_packages: Path
) -> Dict[str, Any]:
    if _last_nonempty_line(install_log, "clean-host install log") != (
        "[install] pip check PASS"
    ):
        raise AnalysisError(
            "clean-host install log does not end in [install] pip check PASS"
        )

    try:
        lock_lines = lock_path.read_text(encoding="utf-8").splitlines()
        installed_lines = installed_packages.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AnalysisError("clean-host environment records are not valid UTF-8") from exc

    locked: Dict[str, str] = {}
    pin = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+) \\$")
    for line in lock_lines:
        match = pin.fullmatch(line.strip())
        if match is None:
            continue
        name = _normalized_package_name(match.group(1))
        if name in locked:
            raise AnalysisError(
                "clean-host dependency lock duplicates exact pin %s" % name
            )
        locked[name] = match.group(2)
    if not locked:
        raise AnalysisError("clean-host dependency lock contains no exact pins")

    installed: Dict[str, str] = {}
    frozen = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)$")
    for line in installed_lines:
        if not line.strip():
            continue
        match = frozen.fullmatch(line.strip())
        if match is None:
            raise AnalysisError(
                "clean-host installed package line is not exact: %s" % line
            )
        name = _normalized_package_name(match.group(1))
        if name in installed:
            raise AnalysisError(
                "clean-host installed package list duplicates %s" % name
            )
        installed[name] = match.group(2)

    project_version = installed.pop("gptcache", None)
    if project_version is None:
        raise AnalysisError(
            "editable GPTCache checkout is absent from clean-host package list"
        )
    if installed != locked:
        missing = sorted(set(locked) - set(installed))
        extra = sorted(set(installed) - set(locked))
        mismatched = sorted(
            name
            for name in set(installed) & set(locked)
            if installed[name] != locked[name]
        )
        raise AnalysisError(
            "clean-host environment does not exactly match its dependency lock "
            "(missing=%s extra=%s mismatched=%s)"
            % (missing, extra, mismatched)
        )
    return {
        "install_sentinel": "[install] pip check PASS",
        "locked_package_count": len(locked),
        "installed_project": "gptcache==%s" % project_version,
    }


def _single_inspect(path: Path, label: str) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            payload = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError("%s is not valid JSON" % label) from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise AnalysisError("%s must contain one Docker inspect object" % label)
    return payload[0]


def _environment_map(value: Any, label: str) -> Dict[str, str]:
    if not isinstance(value, list):
        raise AnalysisError("%s environment is missing" % label)
    environment: Dict[str, str] = {}
    for item in value:
        if not isinstance(item, str) or "=" not in item:
            raise AnalysisError("%s environment entry is invalid" % label)
        key, item_value = item.split("=", 1)
        if not key or key in environment:
            raise AnalysisError("%s environment contains a duplicate key" % label)
        environment[key] = item_value
    return environment


def _validate_image_inspect(path: Path, expected_image_id: str) -> None:
    item = _single_inspect(path, "paired-container image inspection")
    if str(item.get("Id", "")).lower() != expected_image_id:
        raise AnalysisError(
            "paired-container inspected image ID does not match the recorded image ID"
        )
    if item.get("Os") != "linux" or item.get("Architecture") != "amd64":
        raise AnalysisError(
            "paired-container inspected image platform is not linux/amd64"
        )
    config = item.get("Config")
    if not isinstance(config, dict) or config.get("User") != "project":
        raise AnalysisError(
            "paired-container inspected image does not use the project user"
        )
    if config.get("WorkingDir") != "/workspace":
        raise AnalysisError(
            "paired-container inspected image working directory is not /workspace"
        )
    if config.get("Entrypoint") != ["bash", "scripts/verify_project.sh"]:
        raise AnalysisError(
            "paired-container inspected image entrypoint is not the verifier"
        )
    if config.get("Cmd") not in (None, []):
        raise AnalysisError(
            "paired-container inspected image command is not empty"
        )
    environment = _environment_map(
        config.get("Env"), "paired-container inspected image"
    )
    if any(
        environment.get(key) != value
        for key, value in CONTAINER_IMAGE_ENVIRONMENT.items()
    ):
        raise AnalysisError(
            "paired-container inspected image lacks the pinned dependency environment"
        )


def _validate_container_inspect(
    path: Path, expected_image_id: str, expected_mount_source: str, label: str
) -> str:
    item = _single_inspect(path, "%s runtime inspection" % label)
    container_id = str(item.get("Id", "")).lower()
    if len(container_id) != 64 or any(
        character not in "0123456789abcdef" for character in container_id
    ):
        raise AnalysisError("%s runtime inspection lacks a full container ID" % label)
    if str(item.get("Image", "")).lower() != expected_image_id:
        raise AnalysisError("%s did not run the recorded image ID" % label)
    if item.get("Path") != "bash" or item.get("Args") != [
        "scripts/verify_project.sh"
    ]:
        raise AnalysisError("%s did not execute the verifier entrypoint" % label)
    state = item.get("State")
    exit_code = state.get("ExitCode") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or state.get("Status") != "exited"
        or type(exit_code) is not int
        or exit_code != 0
        or state.get("OOMKilled") is not False
        or state.get("Error") != ""
    ):
        raise AnalysisError("%s did not exit successfully" % label)
    host = item.get("HostConfig")
    if not isinstance(host, dict) or host.get("NetworkMode") != "none":
        raise AnalysisError("%s runtime network mode was not none" % label)
    if host.get("Privileged") is not False:
        raise AnalysisError("%s runtime was privileged" % label)
    if host.get("CapAdd") not in (None, []):
        raise AnalysisError("%s runtime added capabilities" % label)
    dropped = host.get("CapDrop")
    if not isinstance(dropped, list) or {
        str(value).upper() for value in dropped
    } != {"ALL"}:
        raise AnalysisError("%s did not drop all capabilities" % label)
    security = host.get("SecurityOpt")
    if security not in (
        ["no-new-privileges:true"],
        ["no-new-privileges=true"],
    ):
        raise AnalysisError("%s did not enable no-new-privileges" % label)
    config = item.get("Config")
    if not isinstance(config, dict):
        raise AnalysisError("%s runtime configuration is missing" % label)
    if config.get("User") != "project" or config.get("WorkingDir") != "/workspace":
        raise AnalysisError("%s runtime user or working directory changed" % label)
    if config.get("Entrypoint") != ["bash", "scripts/verify_project.sh"]:
        raise AnalysisError("%s runtime entrypoint is not the verifier" % label)
    if config.get("Cmd") not in (None, []):
        raise AnalysisError("%s runtime command is not empty" % label)
    environment = _environment_map(config.get("Env"), "%s runtime" % label)
    required_environment = dict(CONTAINER_IMAGE_ENVIRONMENT)
    required_environment["CARMA_ARTIFACT_DIR"] = "/artifacts"
    if any(
        environment.get(key) != value
        for key, value in required_environment.items()
    ):
        raise AnalysisError("%s required environment is missing or changed" % label)
    mounts = item.get("Mounts")
    matching = (
        [
            mount
            for mount in mounts
            if isinstance(mount, dict)
            and mount.get("Destination") == "/artifacts"
        ]
        if isinstance(mounts, list)
        else []
    )
    if not isinstance(mounts, list) or len(mounts) != 1 or len(matching) != 1:
        raise AnalysisError("%s has no unique /artifacts bind mount" % label)
    mount = matching[0]
    source = mount.get("Source")
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not True
        or source != expected_mount_source
    ):
        raise AnalysisError(
            "%s /artifacts mount does not match its recorded artifact source" % label
        )
    return container_id


def _normalize_pytest_elapsed(raw: bytes) -> bytes:
    """Normalize elapsed text only on successful pytest summary lines."""

    return PYTEST_SUMMARY.sub(rb"\g<result> in <elapsed>", raw)


def _load_host_verification(
    path: Optional[Path], git_head: Dict[str, Any]
) -> Dict[str, Any]:
    if path is None:
        report = _pending_report(path, "clean-host verification unavailable")
        report["commit_binding"] = _pending_commit_binding(
            git_head, "clean-host evidence was not supplied"
        )
        return {
            "result": None,
            "report": report,
        }
    evidence_path = Path(path)
    if not evidence_path.is_file():
        report = _pending_report(path, "clean-host verification file is missing")
        report["commit_binding"] = _pending_commit_binding(
            git_head, "clean-host evidence could not be loaded"
        )
        return {
            "result": None,
            "report": report,
        }
    evidence = _read_json(evidence_path)
    if evidence.get("schema_version") != HOST_VERIFICATION_SCHEMA:
        report = _pending_report(
            path, "clean-host verification schema is stale", status="stale"
        )
        report["commit_binding"] = _pending_commit_binding(
            git_head, "clean-host evidence schema is stale"
        )
        return {
            "result": None,
            "report": report,
        }
    if evidence.get("status") != "pass":
        raise AnalysisError("clean-host verification status is not pass")
    source_commit = _commit_value(
        evidence.get("source_commit"), "clean-host verification"
    )
    commit_binding = _commit_binding(
        source_commit, "clean-host verification", git_head
    )
    if evidence.get("baseline_ancestor_verified") is not True:
        raise AnalysisError("clean-host baseline ancestry was not verified")
    python = evidence.get("python")
    if not isinstance(python, dict) or python.get("implementation") != "CPython":
        raise AnalysisError("clean-host verification did not use CPython")
    if python.get("version") != "3.12.13":
        raise AnalysisError("clean-host verification did not use Python 3.12.13")

    root = evidence_path.resolve().parent
    source_archive_report = _validate_source_archive(
        root,
        evidence.get("source_archive"),
        source_commit,
        git_head,
        "clean-host verification",
    )
    dependency = evidence.get("dependency_lock")
    if not isinstance(dependency, dict):
        raise AnalysisError("clean-host dependency lock metadata is missing")
    for key, expected in (
        ("require_hashes", True),
        ("only_binary", True),
        ("index_url", "https://pypi.org/simple"),
    ):
        if dependency.get(key) != expected:
            raise AnalysisError("clean-host dependency control %s is invalid" % key)
    dependency_path, dependency_report = _evidence_reference(
        root, dependency, "clean-host dependency lock"
    )
    current_lock_sha256 = _current_dependency_lock_sha256(
        "clean-host dependency lock"
    )
    if dependency_report["sha256"] != current_lock_sha256:
        raise AnalysisError(
            "clean-host dependency lock does not match current "
            "requirements-project.lock"
        )
    install_log_path, install_log_report = _evidence_reference(
        root,
        evidence.get("environment_install_log"),
        "clean-host environment install log",
    )
    installed_packages_path, installed_packages_report = _evidence_reference(
        root,
        evidence.get("installed_packages"),
        "clean-host installed packages",
    )
    environment_validation = _validate_host_environment(
        dependency_path, install_log_path, installed_packages_path
    )
    log_path, log_report = _evidence_reference(
        root, evidence.get("verification_log"), "clean-host verification log"
    )
    if _last_nonempty_line(log_path, "clean-host verification log") != "[verify] PASS":
        raise AnalysisError("clean-host verification log does not end in PASS")

    benchmark = evidence.get("benchmark_artifacts")
    expected_names = ("manifest.json", "requests.jsonl", "runs.csv")
    if not isinstance(benchmark, dict) or set(benchmark) != set(expected_names):
        raise AnalysisError("clean-host benchmark artifact set is incomplete")
    benchmark_report = {}
    benchmark_paths = {}
    for name in expected_names:
        artifact_path, artifact_report = _evidence_reference(
            root, benchmark[name], "clean-host benchmark %s" % name
        )
        benchmark_paths[name] = artifact_path
        benchmark_report[name] = artifact_report
    benchmark_validation = _validate_ci_benchmark_artifacts(
        benchmark_paths, "clean-host deterministic benchmark"
    )

    report = {
        "status": "verified",
        "path": str(evidence_path.resolve()),
        "schema_version": HOST_VERIFICATION_SCHEMA,
        "sha256": sha256_file(evidence_path),
        "source_commit": source_commit,
        "commit_binding": commit_binding,
        "source_archive": source_archive_report,
        "python": python,
        "platform": evidence.get("platform"),
        "dependency_lock": dependency_report,
        "current_dependency_lock_sha256": current_lock_sha256,
        "environment_install_log": install_log_report,
        "installed_packages": installed_packages_report,
        "environment_validation": environment_validation,
        "verification_log": log_report,
        "benchmark_artifacts": benchmark_report,
        "benchmark_validation": benchmark_validation,
        "benchmark_request_rows": benchmark_validation["request_rows"],
        "benchmark_run_rows": benchmark_validation["run_rows"],
    }
    return {"result": evidence, "report": report}


def _load_container_reproducibility(
    path: Optional[Path], git_head: Dict[str, Any]
) -> Dict[str, Any]:
    if path is None:
        report = _pending_report(path, "paired-container evidence unavailable")
        report["commit_binding"] = _pending_commit_binding(
            git_head, "paired-container evidence was not supplied"
        )
        return {
            "result": None,
            "report": report,
        }
    evidence_path = Path(path)
    if not evidence_path.is_file():
        report = _pending_report(path, "paired-container evidence file is missing")
        report["commit_binding"] = _pending_commit_binding(
            git_head, "paired-container evidence could not be loaded"
        )
        return {
            "result": None,
            "report": report,
        }
    evidence = _read_json(evidence_path)
    if evidence.get("schema_version") != CONTAINER_REPRODUCIBILITY_SCHEMA:
        report = _pending_report(
            path, "paired-container evidence schema is stale", status="stale"
        )
        report["commit_binding"] = _pending_commit_binding(
            git_head, "paired-container evidence schema is stale"
        )
        return {
            "result": None,
            "report": report,
        }
    if evidence.get("status") != "pass":
        raise AnalysisError("paired-container evidence status is not pass")
    source_commit = _commit_value(
        evidence.get("source_commit"), "paired-container evidence"
    )
    commit_binding = _commit_binding(
        source_commit, "paired-container evidence", git_head
    )
    image_id = evidence.get("image_id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise AnalysisError("paired-container image ID is invalid")
    _hash_value(image_id[len("sha256:") :], "paired-container image ID")
    if evidence.get("platform") != "linux/amd64":
        raise AnalysisError("paired-container platform is not linux/amd64")
    if evidence.get("fresh_container_count") != 2:
        raise AnalysisError("paired-container evidence does not contain two fresh runs")

    controls = evidence.get("controls")
    required_controls = {
        "runtime_network": "none",
        "capabilities_dropped": "ALL",
        "no_new_privileges": True,
        "dependency_hashes_required": True,
        "binary_only_dependencies": True,
        "dependency_index_url": "https://pypi.org/simple",
        "checkout_credentials_persisted": False,
        "generated_lock_check_passed": True,
    }
    if not isinstance(controls, dict) or any(
        controls.get(key) != expected for key, expected in required_controls.items()
    ):
        raise AnalysisError("paired-container security or dependency controls are invalid")
    comparisons = evidence.get("comparisons")
    required_comparisons = (
        "non_timing_logs_identical",
        "hash_lists_identical",
        "manifest_json_identical",
        "requests_jsonl_identical",
        "runs_csv_identical",
    )
    if not isinstance(comparisons, dict) or any(
        comparisons.get(name) is not True for name in required_comparisons
    ):
        raise AnalysisError("paired-container comparisons did not all pass")

    root = evidence_path.resolve().parent
    source_archive_report = _validate_source_archive(
        root,
        evidence.get("source_archive"),
        source_commit,
        git_head,
        "paired-container evidence",
    )
    _, dependency_report = _evidence_reference(
        root, evidence.get("dependency_lock"), "container dependency lock"
    )
    current_lock_sha256 = _current_dependency_lock_sha256(
        "container dependency lock"
    )
    if dependency_report["sha256"] != current_lock_sha256:
        raise AnalysisError(
            "container dependency lock does not match current "
            "requirements-project.lock"
        )
    _, build_log_report = _evidence_reference(
        root, evidence.get("build_log"), "container build log"
    )
    image_inspect_path, image_inspect_report = _evidence_reference(
        root, evidence.get("image_inspect"), "paired-container image inspection"
    )
    _validate_image_inspect(image_inspect_path, image_id)
    runs = evidence.get("runs")
    if not isinstance(runs, list) or len(runs) != 2:
        raise AnalysisError("paired-container run evidence is incomplete")
    run_reports = []
    observed_labels = []
    comparison_files = {name: [] for name in required_comparisons[:2]}
    benchmark_pairs = {name: [] for name in ("manifest.json", "requests.jsonl", "runs.csv")}
    container_ids = []
    for run in runs:
        if not isinstance(run, dict):
            raise AnalysisError("paired-container run entry is invalid")
        label = run.get("label")
        if label not in ("docker-run-1", "docker-run-2"):
            raise AnalysisError("paired-container run label is invalid")
        observed_labels.append(label)
        raw_path, raw_report = _evidence_reference(
            root, run.get("raw_log"), "%s raw log" % label
        )
        if _last_nonempty_line(raw_path, "%s raw log" % label) != "[verify] PASS":
            raise AnalysisError("%s raw log does not end in PASS" % label)
        non_timing_path, non_timing_report = _evidence_reference(
            root, run.get("non_timing_log"), "%s non-timing log" % label
        )
        if non_timing_path.read_bytes() != _normalize_pytest_elapsed(
            raw_path.read_bytes()
        ):
            raise AnalysisError(
                "%s non-timing log is not the normalized raw log" % label
            )
        hash_list_path, hash_list_report = _evidence_reference(
            root, run.get("hash_list"), "%s hash list" % label
        )
        container_inspect_path, container_inspect_report = _evidence_reference(
            root,
            run.get("container_inspect"),
            "%s runtime inspection" % label,
        )
        artifact_mount_source = run.get("artifact_mount_source")
        if (
            not isinstance(artifact_mount_source, str)
            or not Path(artifact_mount_source).is_absolute()
            or Path(artifact_mount_source).name != label
        ):
            raise AnalysisError(
                "%s artifact mount source does not structurally match its run label"
                % label
            )
        container_id = _validate_container_inspect(
            container_inspect_path, image_id, artifact_mount_source, label
        )
        container_ids.append(container_id)
        hash_list = _sha256_list(hash_list_path, "%s hash list" % label)
        comparison_files["non_timing_logs_identical"].append(non_timing_path)
        comparison_files["hash_lists_identical"].append(hash_list_path)
        benchmark = run.get("benchmark_artifacts")
        if not isinstance(benchmark, dict) or set(benchmark) != set(benchmark_pairs):
            raise AnalysisError("%s benchmark artifact set is incomplete" % label)
        benchmark_report = {}
        benchmark_paths = {}
        for name in benchmark_pairs:
            artifact_path, artifact_report = _evidence_reference(
                root, benchmark[name], "%s benchmark %s" % (label, name)
            )
            benchmark_pairs[name].append(artifact_path)
            benchmark_paths[name] = artifact_path
            benchmark_report[name] = artifact_report
        if set(hash_list) != set(benchmark_pairs):
            raise AnalysisError("%s hash list artifact set is incomplete" % label)
        for name, artifact_report in benchmark_report.items():
            if hash_list[name] != artifact_report["sha256"]:
                raise AnalysisError(
                    "%s hash list does not match benchmark %s" % (label, name)
                )
        benchmark_validation = _validate_ci_benchmark_artifacts(
            benchmark_paths, "%s deterministic benchmark" % label
        )
        run_reports.append(
            {
                "label": label,
                "raw_log": raw_report,
                "non_timing_log": non_timing_report,
                "hash_list": hash_list_report,
                "container_inspect": container_inspect_report,
                "container_id": container_id,
                "artifact_mount_source": artifact_mount_source,
                "benchmark_artifacts": benchmark_report,
                "benchmark_validation": benchmark_validation,
            }
        )
    if sorted(observed_labels) != ["docker-run-1", "docker-run-2"]:
        raise AnalysisError("paired-container run labels are not unique")
    if len(set(container_ids)) != 2:
        raise AnalysisError("paired-container evidence reused a container ID")
    for label, paths in comparison_files.items():
        if paths[0].read_bytes() != paths[1].read_bytes():
            raise AnalysisError("paired-container %s files differ" % label)
    for name, paths in benchmark_pairs.items():
        if paths[0].read_bytes() != paths[1].read_bytes():
            raise AnalysisError("paired-container benchmark %s files differ" % name)

    report = {
        "status": "verified",
        "path": str(evidence_path.resolve()),
        "schema_version": CONTAINER_REPRODUCIBILITY_SCHEMA,
        "sha256": sha256_file(evidence_path),
        "source_commit": source_commit,
        "commit_binding": commit_binding,
        "source_archive": source_archive_report,
        "image_id": image_id,
        "platform": evidence["platform"],
        "fresh_container_count": 2,
        "controls": required_controls,
        "comparisons": {name: True for name in required_comparisons},
        "dependency_lock": dependency_report,
        "current_dependency_lock_sha256": current_lock_sha256,
        "build_log": build_log_report,
        "image_inspect": image_inspect_report,
        "runs": run_reports,
    }
    return {"result": evidence, "report": report}


def _build_gate_audit(
    full: Dict[str, Any],
    integration: Dict[str, Any],
    qqp: Dict[str, Any],
    moss: Dict[str, Any],
    host: Dict[str, Any],
    container: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    gates: Dict[str, Dict[str, Any]] = {}

    host_contents_verified = host["report"].get("status") == "verified"
    container_contents_verified = container["report"].get("status") == "verified"
    host_verified = _evidence_is_claimable(host["report"])
    container_verified = _evidence_is_claimable(container["report"])
    if host_contents_verified and container_contents_verified:
        if host["report"]["source_commit"] != container["report"]["source_commit"]:
            raise AnalysisError(
                "host and paired-container evidence use different source commits"
            )
        if (
            host["report"]["dependency_lock"]["sha256"]
            != container["report"]["dependency_lock"]["sha256"]
        ):
            raise AnalysisError(
                "host and paired-container evidence use different dependency locks"
            )
        if (
            host["report"]["source_archive"]["archive_sha256"]
            != container["report"]["source_archive"]["archive_sha256"]
        ):
            raise AnalysisError(
                "host and paired-container evidence use different source archives"
            )

    integrity_failures = []
    for row in integration["rows"]:
        if _integer(row["false_hits"], "integration false hits"):
            integrity_failures.append("%s false_hits" % row["run_id"])
        if _integer(row["stale_candidates"], "integration stale candidates"):
            integrity_failures.append("%s stale_candidates" % row["run_id"])
    gate_1_status = (
        "fail" if integrity_failures else "pass" if host_verified else "pending"
    )
    gates["gate_1_correctness"] = {
        "status": gate_1_status,
        "claimable": gate_1_status in ("pass", "fail"),
        "criterion": "all relevant tests pass and all integrity failures are zero",
        "observed_integrity_failures": integrity_failures,
        "host_verification": host["report"],
        "reason": (
            "an observed integration integrity violation fails the gate"
            if integrity_failures
            else (
                "clean-host verifier passed and integration integrity failures are zero"
                if host_verified
                else (
                    "clean-host evidence could not be bound to current Git HEAD"
                    if host_contents_verified
                    else "verified clean-host test evidence was not supplied"
                )
            )
        ),
    }

    qqp_check = _qqp_gate_check(qqp.get("result"), qqp.get("report"))
    fhr_checks = _full_fhr_checks(full)
    if qqp_check["status"] == "fail":
        gate_2_status = "fail"
    else:
        gate_2_status = "pending"
    gates["gate_2_semantic_safety"] = {
        "status": gate_2_status,
        "claimable": gate_2_status == "fail",
        "criterion": (
            "QQP precision >= 0.99, Wilson lower >= 0.98, and CARMA FHR "
            "upper delta bound <= 0.001"
        ),
        "qqp": qqp_check,
        "synthetic_fhr_by_workload": fhr_checks,
        "reason": (
            "the QQP calibration precision prerequisite failed before held-out evaluation"
            if gate_2_status == "fail"
            else (
                "formal FHR adjudication is pending because the frozen "
                "contract does not identify its workload scope"
            )
        ),
    }

    synthetic = full.get("synthetic_gates") or {}
    claimable = (
        full["report"].get("status") == "verified"
        and full["report"].get("inferential") is True
    )
    synthetic_names = (
        "gate_3_phase_shift_vhr",
        "gate_4_scan_return_vhr",
        "gate_5_stationary_nonregression",
        "gate_6_token_saving_nonregression",
    )
    for name in synthetic_names:
        observed = synthetic.get(name)
        if claimable and observed is not None:
            gates[name] = {
                **observed,
                "status": "pass" if observed["passes"] else "fail",
                "claimable": True,
                "source": "full synthetic metadata reconciled to aggregate.csv",
            }
        else:
            gates[name] = {
                "status": "pending",
                "claimable": False,
                "criterion": _synthetic_criterion(name),
                "reason": "current inferential full artifacts are unavailable",
            }
            if observed is not None:
                gates[name]["diagnostic_observation"] = observed

    system = _system_diagnostics(integration)
    integration_seeds = integration["report"].get("distinct_seeds", [])
    five_full_seeds = (
        len(integration_seeds) == 5
        and integration["report"].get("comparable_across_seeds") is True
        and all(row.get("mode") == "full" for row in system)
        and len(system) == 5
    )
    conservative_status = (
        "pass"
        if five_full_seeds
        and all(row["all_individual_checks_pass"] for row in system)
        else "fail"
        if five_full_seeds
        else "pending"
    )
    gates["gate_7_system_overhead"] = {
        "status": "pending",
        "claimable": False,
        "criterion": (
            "p95 <= 1.25x LRU and <= +0.5 ms; throughput >= 0.90x; "
            "peak RSS <= 1.20x and <= +64 MiB"
        ),
        "diagnostic_by_seed": system,
        "distinct_seed_count": len(integration_seeds),
        "required_seed_count": 5,
        "conservative_require_every_seed_interpretation": {
            "status": conservative_status,
            "claimable_as_frozen_gate": False,
        },
        "reason": (
            "the frozen contract requires five seeds and does not define "
            "how their per-seed p95/resource values adjudicate one gate"
        ),
    }
    gate_8_status = "pass" if container_verified else "pending"
    gates["gate_8_reproducibility"] = {
        "status": gate_8_status,
        "claimable": gate_8_status == "pass",
        "criterion": (
            "two clean Docker CI runs have identical non-timing logs and "
            "all pinned downloads verify without credentials"
        ),
        "reason": (
            "two fresh locked containers passed and all declared comparisons match"
            if container_verified
            else (
                "paired Docker-run evidence could not be bound to current Git HEAD"
                if container_contents_verified
                else "verified paired Docker-run evidence was not supplied"
            )
        ),
        "container_reproducibility": container["report"],
        "partial_provenance": {
            "full_git": full["report"].get("git_provenance"),
            "qqp": qqp["report"].get("provenance"),
            "moss": moss["report"].get("source_provenance"),
        },
    }

    moss_failures = []
    for row in moss["runs"]:
        if _integer(row["live_model_calls"], "MOSS live calls") != 0:
            moss_failures.append("live_model_calls")
        if _integer(row["false_hits"], "MOSS false hits") != 0:
            moss_failures.append("false_hits")
    if not moss["runs"]:
        moss_status = "pending"
    elif moss_failures:
        moss_status = "fail"
    elif moss["report"].get("source_provenance") == "test_fixture":
        moss_status = "pending"
    else:
        moss_status = "pass"
    supplemental = {
        "moss_recorded_response": {
            "status": moss_status,
            "criterion": "recorded responses only, zero live model calls and false hits",
            "failures": sorted(set(moss_failures)),
            "source_provenance": moss["report"].get("source_provenance"),
        }
    }
    return gates, supplemental


def _qqp_gate_check(
    result: Optional[Dict[str, Any]], report: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    if result is None:
        return {"status": "pending", "reason": "QQP result unavailable"}
    if result["status"] != "ok":
        failed = {
            "status": "fail",
            "reason": "no calibration threshold met the prerequisite precision rule",
            "calibration_pairs": result["calibration_pairs"],
            "test_pairs_not_evaluated": result["test_pairs_not_evaluated"],
        }
        provenance = (report or {}).get("provenance")
        if isinstance(provenance, dict):
            grid = provenance.get("calibration_grid")
            if isinstance(grid, dict):
                failed["calibration_grid"] = grid
        return failed
    metrics = result["test"]
    precision = _number(metrics["precision"], "QQP test precision")
    wilson = _number(
        metrics["wilson_precision_lower_one_sided_95"], "QQP test Wilson bound"
    )
    passed = precision >= 0.99 and wilson >= 0.98
    return {
        "status": "pass" if passed else "fail",
        "precision": precision,
        "wilson_lower_one_sided_95": wilson,
        "required_precision": 0.99,
        "required_wilson_lower": 0.98,
        "selected_threshold": result["selected_threshold"],
        "test_pairs": result["test_pairs"],
    }


def _full_fhr_checks(full: Dict[str, Any]) -> Dict[str, Any]:
    if full["report"].get("status") != "verified":
        return {"status": "pending", "reason": "current full results unavailable"}
    capacity = int(full["metadata"]["test_capacity"])
    result = {}
    for workload in PRIMARY_WORKLOADS:
        row = _comparison(
            full["aggregate"], workload, "false_hit_rate", capacity, "BEST_BASELINE"
        )
        upper = _number(row["delta_ci_high"], "FHR upper bound")
        result[workload] = {
            "delta_mean": _number(row["delta_mean"], "FHR delta"),
            "delta_ci_low": _number(row["delta_ci_low"], "FHR CI"),
            "delta_ci_high": upper,
            "meets_0.001_upper_bound": upper <= 0.001,
        }
    return result


def _system_diagnostics(integration: Dict[str, Any]) -> List[Dict[str, Any]]:
    grouped: Dict[int, Dict[str, Dict[str, str]]] = {}
    for row in integration["rows"]:
        grouped.setdefault(_integer(row["seed"], "system seed"), {})[row["policy"]] = row
    diagnostics = []
    for seed, policies in sorted(grouped.items()):
        if "LRU" not in policies or "CARMA" not in policies:
            continue
        lru = policies["LRU"]
        carma = policies["CARMA"]
        p95_lru = _number(lru["total_p95_us"], "LRU p95")
        p95_carma = _number(carma["total_p95_us"], "CARMA p95")
        qps_lru = _number(lru["throughput_qps"], "LRU throughput")
        qps_carma = _number(carma["throughput_qps"], "CARMA throughput")
        rss_lru = _number(lru["rss_peak_sampled_bytes"], "LRU RSS")
        rss_carma = _number(carma["rss_peak_sampled_bytes"], "CARMA RSS")
        checks = {
            "p95_ratio_at_most_1.25": _ratio_value(p95_carma, p95_lru) <= 1.25,
            "p95_delta_us_at_most_500": p95_carma - p95_lru <= 500.0,
            "throughput_ratio_at_least_0.90": _ratio_value(qps_carma, qps_lru) >= 0.90,
            "rss_ratio_at_most_1.20": _ratio_value(rss_carma, rss_lru) <= 1.20,
            "rss_delta_bytes_at_most_64_mib": rss_carma - rss_lru <= 64 * 1024 * 1024,
        }
        diagnostics.append(
            {
                "seed": seed,
                "mode": carma["mode"],
                "p95_ratio": _round(_ratio_value(p95_carma, p95_lru)),
                "p95_delta_us": _round(p95_carma - p95_lru),
                "throughput_ratio": _round(_ratio_value(qps_carma, qps_lru)),
                "rss_ratio": _round(_ratio_value(rss_carma, rss_lru)),
                "rss_delta_bytes": int(rss_carma - rss_lru),
                "individual_checks": checks,
                "all_individual_checks_pass": all(checks.values()),
            }
        )
    return diagnostics


def _render_figures(
    full: Dict[str, Any], integration: Dict[str, Any], output: Path
) -> Dict[str, Dict[str, Any]]:
    report = {}
    if full["report"].get("status") == "verified":
        report["vhr-deltas"] = _plot_vhr_deltas(full, output)
        report["scan-return"] = _plot_scan_return(full, output)
        report["capacity-curve"] = _plot_capacity_curve(full, output)
    else:
        reason = "current full aggregate results unavailable"
        for name in ("vhr-deltas", "scan-return", "capacity-curve"):
            report[name] = {"status": "pending", "reason": reason, "files": []}
    if integration["rows"]:
        report["latency-resources"] = _plot_latency_resources(integration, output)
    else:
        report["latency-resources"] = {
            "status": "pending",
            "reason": "verified integration runs unavailable",
            "files": [],
        }
    return report


def _plot_vhr_deltas(full: Dict[str, Any], output: Path) -> Dict[str, Any]:
    capacity = int(full["metadata"]["test_capacity"])
    rows = [
        _comparison(
            full["aggregate"], workload, "valid_hit_rate", capacity, "BEST_BASELINE"
        )
        for workload in PRIMARY_WORKLOADS
    ]
    labels = ["Stationary", "Category shift", "Pollution scan"]
    values = [_number(row["delta_mean"], "VHR delta") * 100 for row in rows]
    lows = [_number(row["delta_ci_low"], "VHR CI") * 100 for row in rows]
    highs = [_number(row["delta_ci_high"], "VHR CI") * 100 for row in rows]
    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(PUBLICATION_FIGURE_WIDTH_IN, 5.2))
    y = list(range(len(labels)))
    axis.errorbar(
        values,
        y,
        xerr=[
            [value - low for value, low in zip(values, lows)],
            [high - value for value, high in zip(values, highs)],
        ],
        fmt="o",
        color="#1769aa",
        ecolor="#6e9fc5",
        markeredgecolor="#103b5c",
        capsize=4,
        linewidth=1.6,
        markersize=7,
    )
    axis.axvline(0, color="#30343b", linewidth=1.0)
    axis.set_yticks(y, labels)
    axis.invert_yaxis()
    axis.set_xlabel("CARMA minus stronger baseline (percentage points)")
    _title(
        figure,
        "Valid-hit-rate delta vs stronger baseline",
        (
            "Paired seed mean and 95%% bootstrap CI; primary capacity %d.\n%s."
            % (capacity, _full_scope_label(full))
        ),
    )
    _annotate_horizontal(axis, values, y, suffix=" pp")
    return _save_figure(figure, output, "vhr-deltas", len(rows))


def _plot_scan_return(full: Dict[str, Any], output: Path) -> Dict[str, Any]:
    capacity = int(full["metadata"]["test_capacity"])
    comparators = ("LRU", "LFU", "BEST_BASELINE")
    rows = [
        _comparison(
            full["aggregate"],
            "pollution_scan",
            "scan_return_valid_hit_rate",
            capacity,
            comparator,
        )
        for comparator in comparators
    ]
    labels = ["LRU", "LFU", "Stronger baseline"]
    values = [_number(row["delta_mean"], "scan delta") * 100 for row in rows]
    lows = [_number(row["delta_ci_low"], "scan CI") * 100 for row in rows]
    highs = [_number(row["delta_ci_high"], "scan CI") * 100 for row in rows]
    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(PUBLICATION_FIGURE_WIDTH_IN, 5.2))
    y = list(range(len(labels)))
    axis.errorbar(
        values,
        y,
        xerr=[
            [value - low for value, low in zip(values, lows)],
            [high - value for value, high in zip(values, highs)],
        ],
        fmt="D",
        color="#c46a16",
        ecolor="#d9a46f",
        markeredgecolor="#6b380d",
        capsize=4,
        linewidth=1.6,
        markersize=6,
    )
    axis.axvline(0, color="#30343b", linewidth=1.0)
    gate_threshold = 5.0
    gate = full.get("synthetic_gates", {}).get("gate_4_scan_return_vhr", {})
    if full["report"].get("inferential") is True and "passes" in gate:
        gate_outcome = "PASS" if gate["passes"] else "FAIL"
    else:
        gate_outcome = "diagnostic only"
    gate_color = "#6b380d"
    axis.axvline(
        gate_threshold,
        color=gate_color,
        linewidth=1.3,
        linestyle=(0, (4, 3)),
    )
    axis.text(
        gate_threshold + 0.04,
        0.52,
        "Gate 4: +5.0 pp",
        transform=axis.get_xaxis_transform(),
        rotation=90,
        rotation_mode="anchor",
        ha="center",
        va="bottom",
        fontsize=MIN_SOURCE_FONT_PT,
        color=gate_color,
    )
    badge_label = "Gate 4: %s" % gate_outcome.upper()
    axis.text(
        0.985,
        1.06,
        badge_label,
        transform=axis.transAxes,
        ha="right",
        va="bottom",
        fontsize=MIN_SOURCE_FONT_PT,
        fontweight="bold" if gate_outcome == "FAIL" else "normal",
        color=gate_color,
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": "#f7ece2",
            "edgecolor": gate_color,
            "linewidth": 0.8,
        },
        clip_on=False,
    )
    axis.set_xlim(
        min(-0.5, min(lows) - 0.4),
        max(5.8, max(highs) + 0.4),
    )
    axis.set_yticks(y, labels)
    axis.invert_yaxis()
    axis.set_xlabel("CARMA scan-return VHR delta (percentage points)")
    _title(
        figure,
        "Pollution scan return-phase valid-hit-rate delta",
        (
            "Paired seed mean and 95%% bootstrap CI; primary capacity %d.\n%s."
            % (capacity, _full_scope_label(full))
        ),
    )
    _annotate_horizontal(axis, values, y, suffix=" pp")
    return _save_figure(figure, output, "scan-return", len(rows))


def _plot_capacity_curve(full: Dict[str, Any], output: Path) -> Dict[str, Any]:
    metadata = full["metadata"]
    primary_capacity = int(metadata["test_capacity"])
    policies = ("LRU", "LFU", "CARMA")
    all_rows = full["aggregate"]
    values: Dict[Tuple[str, str], List[Tuple[int, float, float, float, int]]] = {}
    for row in all_rows:
        if row["row_type"] != "summary" or row["metric"] != "valid_hit_rate":
            continue
        if row["workload"] not in PRIMARY_WORKLOADS or row["policy"] not in policies:
            continue
        stage = row["stage"]
        capacity = _integer(row["capacity"], "capacity curve capacity")
        if not (
            stage == "capacity_sweep"
            or (stage == "primary_test" and capacity == primary_capacity)
        ):
            continue
        values.setdefault((row["workload"], row["policy"]), []).append(
            (
                capacity,
                _number(row["mean"], "capacity mean"),
                _number(row["ci_low"], "capacity CI"),
                _number(row["ci_high"], "capacity CI"),
                _integer(row["n"], "capacity n"),
            )
        )
    capacities = sorted(
        {
            item[0]
            for series in values.values()
            for item in series
        }
    )
    if len(capacities) < 2:
        return {
            "status": "pending",
            "reason": "capacity sweep has fewer than two verified capacities",
            "files": [],
        }
    plt = _pyplot()
    figure, axes_grid = plt.subplots(
        2,
        2,
        figsize=(PUBLICATION_FIGURE_WIDTH_IN, MAX_CAPACITY_FIGURE_HEIGHT_IN),
        sharex=True,
        sharey=True,
    )
    axes = (axes_grid[0, 0], axes_grid[0, 1], axes_grid[1, 0])
    legend_axis = axes_grid[1, 1]
    palette = {"LRU": "#7c838c", "LFU": "#79a9cf", "CARMA": "#1769aa"}
    markers = {"LRU": "o", "LFU": "s", "CARMA": "D"}
    labels = {
        "stationary": "Stationary",
        "phase_shift": "Category shift",
        "pollution_scan": "Pollution scan",
    }
    plotted = 0
    for axis, workload in zip(axes, PRIMARY_WORKLOADS):
        for policy in policies:
            series = sorted(values.get((workload, policy), []))
            if len(series) < 2:
                continue
            x = [item[0] for item in series]
            y = [100 * item[1] for item in series]
            low = [100 * item[2] for item in series]
            high = [100 * item[3] for item in series]
            axis.errorbar(
                x,
                y,
                yerr=[
                    [value - bound for value, bound in zip(y, low)],
                    [bound - value for value, bound in zip(y, high)],
                ],
                color=palette[policy],
                marker=markers[policy],
                markerfacecolor="white" if policy != "CARMA" else palette[policy],
                linewidth=1.6,
                capsize=2.5,
                label=policy,
            )
            plotted += len(series)
        axis.set_title(
            labels[workload], fontsize=MIN_SOURCE_FONT_PT + 1.0, loc="left"
        )
        axis.set_xticks(capacities)
        axis.set_ylim(*CAPACITY_RATE_DOMAIN_PERCENT)
        axis.set_yticks(CAPACITY_RATE_TICKS_PERCENT)
    legend_handles, legend_labels = axes[0].get_legend_handles_labels()
    legend_axis.axis("off")
    legend_axis.legend(
        legend_handles,
        legend_labels,
        frameon=False,
        loc="center",
    )
    figure.supxlabel(
        "Cache capacity (entries)",
        x=0.52,
        y=0.055,
        fontsize=MIN_SOURCE_FONT_PT,
        color="#30343b",
    )
    figure.supylabel(
        "Valid-hit rate (%)",
        x=0.055,
        y=0.46,
        fontsize=MIN_SOURCE_FONT_PT,
        color="#30343b",
    )
    _title(
        figure,
        "Valid-hit rate across cache capacities",
        (
            "95%% bootstrap CIs; fixed capacity-100 traces. Capacity 100 uses "
            "10 seeds.\nExploratory sweep points use 5; %s."
            % _full_scope_label(full)
        ),
        top=0.77,
        left=0.13,
        right=0.98,
        bottom=0.14,
        wspace=0.18,
        hspace=0.38,
    )
    report = _save_figure(figure, output, "capacity-curve", plotted)
    report["value_axis"] = {
        "metric": "valid_hit_rate",
        "unit": "percent",
        "domain": list(CAPACITY_RATE_DOMAIN_PERCENT),
        "ticks": list(CAPACITY_RATE_TICKS_PERCENT),
        "shared_across_panels": True,
    }
    return report


def _plot_latency_resources(
    integration: Dict[str, Any], output: Path
) -> Dict[str, Any]:
    policies = [
        policy
        for policy in ("LRU", "LFU", "CARMA")
        if any(row["policy"] == policy for row in integration["rows"])
    ]
    if "LRU" not in policies or "CARMA" not in policies:
        return {
            "status": "pending",
            "reason": "integration runs lack paired LRU and CARMA rows",
            "files": [],
        }
    metrics = (
        ("total_p95_us", "Post-embedding cache-path p95", 0.001, "ms"),
        ("policy_p95_us", "Policy-only p95 latency", 1.0, "µs"),
        ("throughput_qps", "Throughput", 1.0, "requests/s"),
        ("rss_peak_sampled_bytes", "Sampled peak RSS", 1 / (1024 * 1024), "MiB"),
    )
    grouped: Dict[str, List[Dict[str, str]]] = {
        policy: [row for row in integration["rows"] if row["policy"] == policy]
        for policy in policies
    }
    plt = _pyplot()
    figure, axes = plt.subplots(
        2, 2, figsize=(PUBLICATION_FIGURE_WIDTH_IN, 7.6)
    )
    palette = {"LRU": "#7c838c", "LFU": "#79a9cf", "CARMA": "#1769aa"}
    hatches = {"LRU": "//", "LFU": "..", "CARMA": ""}
    for axis, (field, title, scale, unit) in zip(axes.flat, metrics):
        means = [
            _mean([_number(row[field], field) * scale for row in grouped[policy]])
            for policy in policies
        ]
        bars = axis.bar(
            policies,
            means,
            color=[palette[policy] for policy in policies],
            edgecolor="#30343b",
            linewidth=0.8,
        )
        for bar, policy in zip(bars, policies):
            bar.set_hatch(hatches[policy])
        for index, policy in enumerate(policies):
            points = [
                _number(row[field], field) * scale for row in grouped[policy]
            ]
            axis.scatter(
                [index] * len(points),
                points,
                s=18,
                facecolor="white",
                edgecolor="#30343b",
                linewidth=0.7,
                zorder=3,
            )
        axis.set_title(title, fontsize=MIN_SOURCE_FONT_PT + 1.0, loc="left")
        axis.set_ylabel(unit)
        axis.set_ylim(bottom=0)
    seeds = integration["report"].get("distinct_seeds", [])
    modes = sorted({row["mode"] for row in integration["rows"]})
    _title(
        figure,
        "SQLite/FAISS latency and resource measurements",
        (
            "Precomputed vectors; embedding excluded. Descriptive per-seed "
            "means.\n"
            "n=%d seed(s), mode=%s; no across-seed Gate 7 rule is frozen."
        )
        % (len(seeds), "/".join(modes)),
        top=0.78,
        left=0.14,
        bottom=0.11,
        wspace=0.42,
        hspace=0.50,
    )
    return _save_figure(
        figure,
        output,
        "latency-resources",
        sum(len(items) for items in grouped.values()),
    )


def _pyplot() -> Any:
    import matplotlib

    matplotlib.use("Agg", force=True)
    matplotlib.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": MIN_SOURCE_FONT_PT,
            "axes.labelsize": MIN_SOURCE_FONT_PT,
            "axes.titlesize": MIN_SOURCE_FONT_PT + 1.0,
            "xtick.labelsize": MIN_SOURCE_FONT_PT,
            "ytick.labelsize": MIN_SOURCE_FONT_PT,
            "legend.fontsize": MIN_SOURCE_FONT_PT,
            "axes.edgecolor": "#30343b",
            "axes.labelcolor": "#30343b",
            "axes.titlecolor": "#20242a",
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": "#e5e8eb",
            "grid.linewidth": 0.7,
            "grid.alpha": 1.0,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "svg.fonttype": "none",
            "svg.hashsalt": ANALYSIS_SCHEMA,
        }
    )
    from matplotlib import pyplot

    return pyplot


def _full_scope_label(full: Dict[str, Any]) -> str:
    return (
        "inferential full protocol"
        if full["report"].get("inferential") is True
        else "smoke diagnostic, not inferential"
    )


def _title(
    figure: Any,
    title: str,
    subtitle: str,
    *,
    top: float = 0.77,
    left: float = 0.23,
    right: float = 0.97,
    bottom: float = 0.15,
    wspace: float = 0.30,
    hspace: float = 0.42,
) -> None:
    figure.suptitle(
        title,
        x=0.07,
        y=0.985,
        ha="left",
        fontsize=MIN_SOURCE_FONT_PT + 3.5,
        color="#20242a",
    )
    figure.text(
        0.07,
        0.925,
        subtitle,
        ha="left",
        va="top",
        fontsize=MIN_SOURCE_FONT_PT,
        color="#555d66",
        linespacing=1.25,
    )
    figure.subplots_adjust(
        top=top,
        left=left,
        right=right,
        bottom=bottom,
        wspace=wspace,
        hspace=hspace,
    )


def _annotate_horizontal(
    axis: Any, values: Sequence[float], positions: Sequence[int], suffix: str
) -> None:
    if not values:
        return
    span = max(values) - min(values)
    offset = max(0.06, span * 0.04)
    for value, position in zip(values, positions):
        axis.text(
            value + (offset if value >= 0 else -offset),
            position,
            "%+.2f%s" % (value, suffix),
            va="center",
            ha="left" if value >= 0 else "right",
            fontsize=MIN_SOURCE_FONT_PT,
            color="#30343b",
        )
    axis.margins(x=0.20)


def _save_figure(
    figure: Any, output: Path, stem: str, observations: int
) -> Dict[str, Any]:
    publication_contract = _validate_figure_publication_contract(figure, stem)
    svg = output / (stem + ".svg")
    png = output / (stem + ".png")
    figure.savefig(
        svg,
        format="svg",
        metadata={"Date": None, "Creator": ANALYSIS_SCHEMA},
    )
    figure.savefig(
        png,
        format="png",
        dpi=PNG_DPI,
        metadata={"Software": ANALYSIS_SCHEMA},
    )
    from matplotlib import pyplot

    pyplot.close(figure)
    return {
        "status": "rendered",
        "observations": observations,
        "files": [svg.name, png.name],
        "publication_contract": publication_contract,
        "sha256": {svg.name: sha256_file(svg), png.name: sha256_file(png)},
    }


def _validate_figure_publication_contract(
    figure: Any, stem: str
) -> Dict[str, Any]:
    """Enforce the lecturer's 10 pt rule after report-width scaling."""

    figure.canvas.draw()
    width, height = (float(value) for value in figure.get_size_inches())
    if width > MAX_PUBLICATION_FIGURE_WIDTH_IN + 1e-9:
        raise AnalysisError(
            "%s figure width %.3f exceeds %.3f inches"
            % (stem, width, MAX_PUBLICATION_FIGURE_WIDTH_IN)
        )
    text_sizes = [
        float(artist.get_fontsize())
        for artist in figure.findobj()
        if hasattr(artist, "get_text")
        and hasattr(artist, "get_fontsize")
        and artist.get_visible()
        and str(artist.get_text()).strip()
    ]
    if not text_sizes:
        raise AnalysisError("%s figure has no rendered text labels" % stem)
    minimum_source = min(text_sizes)
    if minimum_source < MIN_SOURCE_FONT_PT - 1e-9:
        raise AnalysisError(
            "%s figure uses %.3f pt text below the %.3f pt source minimum"
            % (stem, minimum_source, MIN_SOURCE_FONT_PT)
        )
    report_scale = min(1.0, REPORT_TEXT_WIDTH_IN / width)
    minimum_at_report_width = minimum_source * report_scale
    if minimum_at_report_width < MIN_REPORT_FONT_PT - 1e-9:
        raise AnalysisError(
            "%s figure scales to %.3f pt text below the %.3f pt report minimum"
            % (stem, minimum_at_report_width, MIN_REPORT_FONT_PT)
        )
    return {
        "source_width_in": _round(width),
        "source_height_in": _round(height),
        "report_width_in": REPORT_TEXT_WIDTH_IN,
        "scale_to_report_width": _round(report_scale),
        "minimum_source_font_pt": _round(minimum_source),
        "minimum_font_at_report_width_pt": _round(minimum_at_report_width),
        "required_minimum_report_font_pt": MIN_REPORT_FONT_PT,
        "rendered_text_labels_checked": len(text_sizes),
    }


def _summary_rows(
    gates: Dict[str, Dict[str, Any]], supplemental: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    rows = []
    for name, gate in gates.items():
        estimate = ""
        low = ""
        high = ""
        unit = ""
        if "observed_delta" in gate:
            estimate = gate["observed_delta"]
            low = gate.get("observed_ci_low", "")
            high = gate.get("observed_ci_high", "")
            unit = "fraction"
        elif name == "gate_2_semantic_safety" and "precision" in gate.get("qqp", {}):
            estimate = gate["qqp"]["precision"]
            low = gate["qqp"]["wilson_lower_one_sided_95"]
            unit = "fraction"
        rows.append(
            {
                "item": name,
                "category": "success_gate",
                "status": gate["status"],
                "estimate": estimate,
                "ci_low": low,
                "ci_high": high,
                "unit": unit,
                "criterion": gate.get("criterion", _synthetic_criterion(name)),
                "source": gate.get("source", "gate-audit.json"),
                "note": gate.get("reason", ""),
            }
        )
    for name, check in supplemental.items():
        rows.append(
            {
                "item": name,
                "category": "supplemental_check",
                "status": check["status"],
                "estimate": "",
                "ci_low": "",
                "ci_high": "",
                "unit": "",
                "criterion": check.get("criterion", ""),
                "source": "MOSS runs.csv/manifest.json",
                "note": ",".join(check.get("failures", [])),
            }
        )
    return rows


def _synthetic_criterion(name: str) -> str:
    return {
        "gate_3_phase_shift_vhr": "delta >= 0.02, CI low > 0, Holm p <= 0.05",
        "gate_4_scan_return_vhr": "delta >= 0.05, CI low > 0, Holm p <= 0.05",
        "gate_5_stationary_nonregression": "delta CI low >= -0.01",
        "gate_6_token_saving_nonregression": "each workload delta CI low >= -0.005",
    }.get(name, "")


def _comparison(
    rows: Sequence[Dict[str, str]],
    workload: str,
    metric: str,
    capacity: int,
    comparator: str,
) -> Dict[str, str]:
    matches = [
        row
        for row in rows
        if row["stage"] == "primary_test"
        and row["row_type"] == "comparison"
        and row["workload"] == workload
        and _integer(row["capacity"], "comparison capacity") == capacity
        and row["metric"] == metric
        and row["policy"] == "CARMA"
        and row["comparator"] == comparator
    ]
    if len(matches) != 1:
        raise AnalysisError(
            "expected one primary comparison for %s/%s/%s; found %d"
            % (workload, metric, comparator, len(matches))
        )
    return matches[0]


def _run_metric_values(
    rows: Sequence[Dict[str, str]], metric: str
) -> List[float]:
    return [_metric(row, metric) for row in sorted(rows, key=lambda item: int(item["seed"]))]


def _metric(row: Dict[str, str], metric: str) -> float:
    value = row.get(metric, "")
    if value in (None, ""):
        raise AnalysisError("run row lacks applicable metric %s" % metric)
    return _number(value, "run metric %s" % metric)


def _pending_report(
    path: Optional[Path], reason: str, status: str = "pending"
) -> Dict[str, Any]:
    return {
        "status": status,
        "path": str(Path(path).resolve()) if path is not None else None,
        "reason": reason,
    }


def _hash_value(value: Any, label: str) -> str:
    if isinstance(value, str):
        result = value
    elif isinstance(value, dict):
        result = value.get("sha256")
    else:
        result = None
    if not isinstance(result, str):
        raise AnalysisError("%s lacks a valid SHA-256" % label)
    result = result.lower()
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise AnalysisError("%s lacks a valid SHA-256" % label)
    return result


def _rows_value(value: Any) -> Optional[int]:
    if isinstance(value, dict) and value.get("rows") is not None:
        return _integer(value["rows"], "artifact rows")
    return None


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with Path(path).open("r", encoding="utf-8") as source:
            value = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError("invalid JSON artifact %s" % path) from exc
    if not isinstance(value, dict):
        raise AnalysisError("expected a JSON object at %s" % path)
    return value


def _read_csv(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    try:
        with Path(path).open("r", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            fields = list(reader.fieldnames or [])
            if not fields or len(fields) != len(set(fields)):
                raise AnalysisError("CSV has missing or duplicate headers: %s" % path)
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise AnalysisError("invalid CSV artifact %s" % path) from exc
    if any(None in row for row in rows):
        raise AnalysisError("CSV row has extra fields: %s" % path)
    return rows, fields


def _require_exact_fields(
    fields: Sequence[str], expected: Sequence[str], label: str
) -> None:
    if list(fields) != list(expected):
        missing = sorted(set(expected) - set(fields))
        unexpected = sorted(set(fields) - set(expected))
        raise AnalysisError(
            "%s header mismatch; missing=%s unexpected=%s"
            % (label, missing, unexpected)
        )


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8") as output:
        json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
        output.write("\n")


def _write_csv(
    path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]
) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisError("%s is not numeric" % label) from exc
    if not math.isfinite(result):
        raise AnalysisError("%s is not finite" % label)
    return result


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise AnalysisError("%s is not an integer" % label)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisError("%s is not an integer" % label) from exc
    if str(result) != str(value) and not (
        isinstance(value, float) and value.is_integer()
    ):
        raise AnalysisError("%s is not an exact integer" % label)
    return result


def _rate(value: Any, label: str) -> float:
    result = _number(value, label)
    if not 0.0 <= result <= 1.0:
        raise AnalysisError("%s is outside [0, 1]" % label)
    return result


def _equal_rate(value: Any, numerator: int, denominator: int, label: str) -> None:
    expected = numerator / denominator if denominator else 0.0
    if not _close(_number(value, label), expected, tolerance=1e-7):
        raise AnalysisError("%s does not reconcile to its counts" % label)


def _close(left: float, right: float, tolerance: float = 1e-8) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise AnalysisError("cannot compute an empty mean")
    return sum(values) / len(values)


def _ratio_value(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return math.inf if numerator > 0 else 1.0
    return numerator / denominator


def _round(value: float) -> float:
    return round(float(value), 10)


def _wilson_lower(
    successes: int, trials: int, z: float = 1.6448536269514722
) -> float:
    if trials == 0:
        return 0.0
    proportion = successes / trials
    squared = z * z
    center = proportion + squared / (2 * trials)
    adjustment = z * math.sqrt(
        proportion * (1 - proportion) / trials
        + squared / (4 * trials * trials)
    )
    return max(0.0, (center - adjustment) / (1 + squared / trials))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-dir", type=Path, default=None)
    parser.add_argument(
        "--integration-dir",
        type=Path,
        action="append",
        default=[],
        help="repeat for independent fresh-process system seeds",
    )
    parser.add_argument("--qqp-result", type=Path, default=None)
    parser.add_argument("--moss-dir", type=Path, default=None)
    parser.add_argument(
        "--host-verification",
        type=Path,
        default=None,
        help="clean-host carma-host-verification-v1 JSON evidence",
    )
    parser.add_argument(
        "--container-reproducibility",
        type=Path,
        default=None,
        help="paired-container carma-container-reproducibility-v1 JSON evidence",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = analyze_results(
        output_dir=args.output,
        full_dir=args.full_dir,
        integration_dirs=args.integration_dir,
        qqp_result=args.qqp_result,
        moss_dir=args.moss_dir,
        host_verification=args.host_verification,
        container_reproducibility=args.container_reproducibility,
    )
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    # A scientific gate failure is a successful analysis outcome. Technical
    # integrity failures raise before publication and produce a non-zero exit.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
