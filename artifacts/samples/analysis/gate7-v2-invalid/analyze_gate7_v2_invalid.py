#!/usr/bin/env python3
"""Reproduce the curated analysis of the preserved Gate 7 v2 INVALID attempt.

This script reads the immutable evidence snapshots under
``artifacts/samples/verification/gate7-v2-invalid`` plus the byte-bound curated
latency table beside this script.  It validates their exact SHA-256 identities,
checks the canonical two-row ledger chain, and then emits the descriptive
CSV/JSON summaries, the 5 x 5 frozen-systems-check matrix, and a two-panel
equal-seed latency-distribution figure.

The numerical results are descriptive.  They cannot convert the formal
attempt from INVALID into PASS or FAIL.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle
from matplotlib.ticker import FixedLocator, FuncFormatter


SCHEMA_VERSION = "carma-gate7-v2-invalid-curated-analysis-v1"
LATENCY_SCHEMA_VERSION = "carma-gate7-v2-latency-quantiles-v1"
ATTEMPT_ID = "20260827T131743602373Z-557c6ac0578c"
SOURCE_COMMIT = "557c6ac0578cb6b77c5ae51595b49abdc0407e10"
SOURCE_TAG = "gate7c-onnx-v2-formal-source"
AUDITOR_PATH = "benchmarks/carma/gate7_v2_audit.py"
EXPECTED_HASHES = {
    "attempt-ledger.jsonl": (
        "84eee7963f4e5c27f8d962e31ddcdcec9779b1cd463a8952841946ffc8557f0c"
    ),
    "manifest.json": (
        "7574127cce8f1c72e87a1528dc6709947860178f43667a92add155e89e1f1cf8"
    ),
    "gate7-preterminal-adjudication.json": (
        "e3e8df2787049d3b1ea4e622e7666502b715ae572431b4f8d4e4b213c12dd39b"
    ),
}
LATENCY_CURATED_FILENAME = "latency-quantile-ecdf.csv"
LATENCY_CURATED_SHA256 = "cc8dbbd56ffc41b507c2a4427798ff6601632bca63a8c3bca1934d1cad7f53c5"
LATENCY_SOURCE_RELATIVE_PATH = (
    "artifacts/gate7-v2-onnx-attempts/"
    "attempt-20260827T131736Z-1784/requests.jsonl"
)
LATENCY_SOURCE_SHA256 = "b23fe24164c7b11a7c164deff1b1f1e9571e41e39231bc04e269625f1eed6358"
LATENCY_SOURCE_ROWS = 45_000
LATENCY_SEEDS = (20261001, 20261002, 20261003, 20261004, 20261005)
LATENCY_POLICIES = ("CARMA", "LRU", "LFU")
LATENCY_METRICS = ("request_total_ns", "post_embedding_total_ns")
LATENCY_ROWS_PER_SEED_POLICY = 3_000
LATENCY_POOLED_COUNT = 15_000
LATENCY_QUANTILE_STEPS = 1_000
LATENCY_POOLING_METHOD = "equal_seed_empirical_mixture"
LATENCY_QUANTILE_METHOD = "Hyndman-Fan_Type_7_linear_h=(n-1)*p"
LATENCY_FIELDS = (
    "schema_version",
    "attempt_id",
    "formal_status",
    "claimable",
    "source_relative_path",
    "source_sha256",
    "source_row_count",
    "pooling_method",
    "quantile_method",
    "seed_count",
    "rows_per_seed_policy",
    "pooled_count",
    "policy",
    "metric",
    "unit",
    "quantile_probability",
    "latency_ns",
)
MIN_SOURCE_FONT_PT = 11.5
FIGURE_WIDTH_IN = 7.05
CHECKSUM_FILES = (
    "README.md",
    "analyze_gate7_v2_invalid.py",
    "chart-contract.md",
    "curate_latency_distributions.py",
    "gate7-v2-latency-distributions.pdf",
    "gate7-v2-latency-distributions.png",
    "gate7-v2-latency-distributions.svg",
    "gate7-v2-per-seed-frozen-systems-checks.pdf",
    "gate7-v2-per-seed-frozen-systems-checks.png",
    "gate7-v2-per-seed-frozen-systems-checks.svg",
    "invalidity-findings.csv",
    LATENCY_CURATED_FILENAME,
    "per-seed-checks.csv",
    "run-summary.csv",
    "summary.json",
)
CHECKSUM_PACKAGE_INPUTS = {
    "README.md",
    "analyze_gate7_v2_invalid.py",
    "chart-contract.md",
    "curate_latency_distributions.py",
    LATENCY_CURATED_FILENAME,
}
CHECK_ORDER = (
    "p95_ratio",
    "p95_delta",
    "throughput_ratio",
    "rss_ratio",
    "rss_delta",
)
CHECK_SPECS = {
    "p95_ratio": {
        "value_key": "p95_ratio",
        "unit": "ratio",
        "operator": "<=",
        "bound_key": "p95_ratio_max",
        "column_label": "p95 ratio\n≤1.25",
    },
    "p95_delta": {
        "value_key": "p95_delta_ns",
        "unit": "ns",
        "operator": "<=",
        "bound_key": "p95_delta_ns_max",
        "column_label": "p95 Δ\n≤0.500 ms",
    },
    "throughput_ratio": {
        "value_key": "throughput_ratio",
        "unit": "ratio",
        "operator": ">=",
        "bound_key": "throughput_ratio_min",
        "column_label": "QPS ratio\n≥0.90",
    },
    "rss_ratio": {
        "value_key": "rss_ratio",
        "unit": "ratio",
        "operator": "<=",
        "bound_key": "rss_ratio_max",
        "column_label": "RSS ratio\n≤1.20",
    },
    "rss_delta": {
        "value_key": "rss_delta_bytes",
        "unit": "bytes",
        "operator": "<=",
        "bound_key": "rss_delta_bytes_max",
        "column_label": "RSS Δ\n≤64 MiB",
    },
}
RUN_FIELDS = (
    "attempt_id",
    "seed",
    "policy",
    "order_position",
    "run_id",
    "requests",
    "child_exit_status",
    "end_to_end_p50_us",
    "end_to_end_p95_us",
    "end_to_end_p99_us",
    "embedding_p95_us",
    "faiss_p95_us",
    "policy_exclusive_p95_us",
    "sqlite_p95_us",
    "response_return_p95_us",
    "service_throughput_qps",
    "rss_peak_bytes",
    "structural_valid",
    "semantic_guardrail_status",
    "hits",
    "same_concept_hits",
    "direct_negative_hits",
    "unlabeled_cross_concept_hits",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_and_validate_ledger(path: Path) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"ledger row {line_number} is not an object")
        rows.append(value)
    if len(rows) != 2:
        raise ValueError(f"expected exactly two ledger rows, found {len(rows)}")
    if [row.get("event") for row in rows] != ["PROTOCOL_GENESIS", "START"]:
        raise ValueError("ledger must end at unmatched START with no TERMINAL")
    if [row.get("sequence") for row in rows] != [1, 2]:
        raise ValueError("ledger sequence is not canonical 1, 2")
    previous: str | None = None
    for index, row in enumerate(rows, 1):
        observed = row.get("entry_sha256")
        payload = dict(row)
        payload.pop("entry_sha256", None)
        computed = _canonical_sha256(payload)
        if observed != computed:
            raise ValueError(f"ledger row {index} canonical SHA-256 mismatch")
        if row.get("previous_entry_sha256") != previous:
            raise ValueError(f"ledger row {index} chain pointer mismatch")
        previous = str(observed)
    return rows


def _read_and_validate_latency_curves(
    path: Path,
) -> tuple[dict[str, dict[str, list[tuple[float, float]]]], Mapping[str, Any]]:
    """Validate the byte-bound curated quantile table and return ms curves."""

    observed_sha256 = _sha256(path)
    if observed_sha256 != LATENCY_CURATED_SHA256:
        raise ValueError(
            f"curated latency table identity mismatch: {observed_sha256}"
        )

    expected_metadata = {
        "schema_version": LATENCY_SCHEMA_VERSION,
        "attempt_id": ATTEMPT_ID,
        "formal_status": "INVALID",
        "claimable": "false",
        "source_relative_path": LATENCY_SOURCE_RELATIVE_PATH,
        "source_sha256": LATENCY_SOURCE_SHA256,
        "source_row_count": str(LATENCY_SOURCE_ROWS),
        "pooling_method": LATENCY_POOLING_METHOD,
        "quantile_method": LATENCY_QUANTILE_METHOD,
        "seed_count": str(len(LATENCY_SEEDS)),
        "rows_per_seed_policy": str(LATENCY_ROWS_PER_SEED_POLICY),
        "pooled_count": str(LATENCY_POOLED_COUNT),
        "unit": "ns",
    }
    curves: dict[str, dict[str, list[tuple[float, float]]]] = {
        metric: {policy: [] for policy in LATENCY_POLICIES}
        for metric in LATENCY_METRICS
    }
    row_count = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != LATENCY_FIELDS:
            raise ValueError("curated latency table header mismatch")
        for line_number, row in enumerate(reader, 2):
            for field, expected in expected_metadata.items():
                if row.get(field) != expected:
                    raise ValueError(
                        f"curated latency row {line_number} has invalid {field}"
                    )
            policy = row.get("policy")
            metric = row.get("metric")
            if policy not in LATENCY_POLICIES or metric not in LATENCY_METRICS:
                raise ValueError(
                    f"curated latency row {line_number} policy/metric mismatch"
                )
            points = curves[str(metric)][str(policy)]
            expected_step = len(points)
            if expected_step > LATENCY_QUANTILE_STEPS:
                raise ValueError("curated latency group contains too many rows")
            expected_probability = f"{expected_step / LATENCY_QUANTILE_STEPS:.3f}"
            if row.get("quantile_probability") != expected_probability:
                raise ValueError(
                    f"curated latency row {line_number} quantile grid mismatch"
                )
            try:
                latency_ns = Decimal(str(row.get("latency_ns")))
            except InvalidOperation as error:
                raise ValueError(
                    f"curated latency row {line_number} latency is not numeric"
                ) from error
            if not latency_ns.is_finite() or latency_ns < 0:
                raise ValueError(
                    f"curated latency row {line_number} latency is invalid"
                )
            latency_ms = float(latency_ns / Decimal(1_000_000))
            if points and latency_ms < points[-1][1]:
                raise ValueError(
                    f"curated latency row {line_number} is not monotone"
                )
            points.append((expected_step / LATENCY_QUANTILE_STEPS, latency_ms))
            row_count += 1

    expected_rows = (
        len(LATENCY_POLICIES)
        * len(LATENCY_METRICS)
        * (LATENCY_QUANTILE_STEPS + 1)
    )
    if row_count != expected_rows:
        raise ValueError(
            f"expected {expected_rows} curated latency rows, found {row_count}"
        )
    for metric in LATENCY_METRICS:
        for policy in LATENCY_POLICIES:
            if len(curves[metric][policy]) != LATENCY_QUANTILE_STEPS + 1:
                raise ValueError(f"incomplete curated latency curve: {metric}/{policy}")

    quantile_steps = {
        "p50": 500,
        "p95": 950,
        "p99": 990,
        "p99_9": 999,
        "maximum": 1000,
    }
    exact_quantiles_ms = {
        metric: {
            policy: {
                label: curves[metric][policy][step][1]
                for label, step in quantile_steps.items()
            }
            for policy in LATENCY_POLICIES
        }
        for metric in LATENCY_METRICS
    }
    metadata: Mapping[str, Any] = {
        "curated_table": LATENCY_CURATED_FILENAME,
        "curated_table_sha256": observed_sha256,
        "curated_rows": row_count,
        "source_relative_path": LATENCY_SOURCE_RELATIVE_PATH,
        "source_sha256": LATENCY_SOURCE_SHA256,
        "source_rows": LATENCY_SOURCE_ROWS,
        "pooling_method": LATENCY_POOLING_METHOD,
        "equal_seed_count": len(LATENCY_SEEDS),
        "rows_per_seed_policy": LATENCY_ROWS_PER_SEED_POLICY,
        "pooled_count_per_policy": LATENCY_POOLED_COUNT,
        "quantile_method": LATENCY_QUANTILE_METHOD,
        "quantile_grid": {
            "minimum": 0.0,
            "maximum": 1.0,
            "step": 1 / LATENCY_QUANTILE_STEPS,
            "points_per_policy_metric": LATENCY_QUANTILE_STEPS + 1,
        },
        "metrics": list(LATENCY_METRICS),
        "policies": list(LATENCY_POLICIES),
        "quantiles_ms": exact_quantiles_ms,
        "formal_use": "descriptive_only_because_attempt_invalid_and_nonclaimable",
    }
    return curves, metadata


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _normalize_svg(path: Path) -> None:
    """Strip renderer-added line-end spaces without changing SVG semantics."""

    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join(line.rstrip() for line in lines) + "\n",
        encoding="utf-8",
    )


def _format_cell(check: str, value: float | int) -> str:
    if check == "p95_ratio":
        return f"{float(value):.4f}"
    if check == "p95_delta":
        return f"{float(value) / 1_000_000:+.3f} ms"
    if check == "throughput_ratio":
        return f"{float(value):.4f}"
    if check == "rss_ratio":
        return f"{float(value):.4f}"
    if check == "rss_delta":
        return f"{float(value) / (1024 * 1024):+.2f} MiB"
    raise KeyError(check)


def _render_matrix(
    output_dir: Path,
    seed_rows: Sequence[Mapping[str, Any]],
) -> None:
    pass_blue = "#4E79A7"
    fail_orange = "#F28E2B"
    ink = "#202B33"
    quiet = "#5F6B73"
    background = "#FFFFFF"

    statuses = [
        [1 if bool(row["checks"][check]) else 0 for check in CHECK_ORDER]
        for row in seed_rows
    ]
    fig, ax = plt.subplots(figsize=(FIGURE_WIDTH_IN, 4.85), facecolor=background)
    ax.imshow(
        statuses,
        cmap=ListedColormap([fail_orange, pass_blue]),
        vmin=0,
        vmax=1,
        aspect="auto",
        interpolation="nearest",
    )

    ax.set_xticks(range(len(CHECK_ORDER)))
    ax.set_xticklabels(
        [CHECK_SPECS[check]["column_label"] for check in CHECK_ORDER],
        fontsize=MIN_SOURCE_FONT_PT,
        fontweight="semibold",
        color=ink,
    )
    ax.set_yticks(range(len(seed_rows)))
    ax.set_yticklabels(
        [str(row["seed"]) for row in seed_rows],
        fontsize=MIN_SOURCE_FONT_PT,
        fontweight="semibold",
        color=ink,
    )
    ax.tick_params(
        axis="x",
        top=True,
        bottom=False,
        labeltop=True,
        labelbottom=False,
        length=0,
        pad=10,
        labelsize=MIN_SOURCE_FONT_PT,
    )
    ax.tick_params(axis="y", length=0, pad=7, labelsize=MIN_SOURCE_FONT_PT)
    ax.set_ylabel(
        "Seed",
        fontsize=MIN_SOURCE_FONT_PT,
        fontweight="semibold",
        color=ink,
        labelpad=10,
    )

    for row_index, row in enumerate(seed_rows):
        for column_index, check in enumerate(CHECK_ORDER):
            passed = bool(row["checks"][check])
            value_key = str(CHECK_SPECS[check]["value_key"])
            value = row["paired_values"][value_key]
            ax.text(
                column_index,
                row_index - 0.10,
                "PASS" if passed else "FAIL",
                ha="center",
                va="center",
                color=background,
                fontsize=MIN_SOURCE_FONT_PT,
                fontweight="bold",
            )
            ax.text(
                column_index,
                row_index + 0.20,
                _format_cell(check, value),
                ha="center",
                va="center",
                color=background,
                fontsize=MIN_SOURCE_FONT_PT,
                fontfamily="monospace",
            )
            ax.add_patch(
                Rectangle(
                    (column_index - 0.5, row_index - 0.5),
                    1,
                    1,
                    fill=False,
                    edgecolor=ink,
                    linewidth=1.5,
                )
            )

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xlim(-0.5, len(CHECK_ORDER) - 0.5)
    ax.set_ylim(len(seed_rows) - 0.5, -0.5)

    fig.suptitle(
        "Gate 7 v2 per-seed frozen systems checks",
        x=0.08,
        y=0.975,
        ha="left",
        fontsize=15.5,
        fontweight="bold",
        color=ink,
    )
    fig.text(
        0.08,
        0.905,
        "15 isolated full-path ONNX runs · descriptive only",
        ha="left",
        fontsize=MIN_SOURCE_FONT_PT,
        color=quiet,
    )
    fig.text(
        0.08,
        0.095,
        "INVALID / NONCLAIMABLE — no formal Gate 7 conclusion.",
        ha="left",
        fontsize=MIN_SOURCE_FONT_PT,
        fontweight="bold",
        color=fail_orange,
    )
    fig.text(
        0.08,
        0.04,
        "Descriptive: 24/25 pass; only seed 20261001 p95 Δ fails.",
        ha="left",
        fontsize=MIN_SOURCE_FONT_PT,
        color=ink,
    )
    fig.subplots_adjust(left=0.155, right=0.985, top=0.74, bottom=0.20)

    stem = output_dir / "gate7-v2-per-seed-frozen-systems-checks"
    fixed_time = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    fig.savefig(
        stem.with_suffix(".png"),
        dpi=200,
        facecolor=background,
        metadata={
            "Software": "Matplotlib",
            "Title": "Gate 7 v2 per-seed frozen systems checks",
        },
    )
    fig.savefig(
        stem.with_suffix(".svg"),
        facecolor=background,
        metadata={
            "Creator": "Matplotlib",
            "Date": "2026-08-27",
            "Title": "Gate 7 v2 per-seed frozen systems checks",
        },
    )
    _normalize_svg(stem.with_suffix(".svg"))
    fig.savefig(
        stem.with_suffix(".pdf"),
        facecolor=background,
        metadata={
            "Creator": "Matplotlib",
            "CreationDate": fixed_time,
            "ModDate": fixed_time,
            "Title": "Gate 7 v2 per-seed frozen systems checks",
        },
    )
    plt.close(fig)


def _render_latency_distributions(
    output_dir: Path,
    curves: Mapping[str, Mapping[str, Sequence[tuple[float, float]]]],
) -> None:
    """Render two quantile-derived ECDF panels from the validated curation."""

    ink = "#202B33"
    quiet = "#5F6B73"
    grid = "#D9DEE3"
    invalid_orange = "#D66A16"
    background = "#FFFFFF"
    policy_styles = {
        "CARMA": {"color": "#4E79A7", "linestyle": "-"},
        "LRU": {"color": "#F28E2B", "linestyle": "--"},
        "LFU": {"color": "#6B7280", "linestyle": ":"},
    }
    panels = (
        ("request_total_ns", "(a) Full request total", "linear"),
        ("post_embedding_total_ns", "(b) Post-embedding total", "log"),
    )

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(FIGURE_WIDTH_IN, 5.25),
        facecolor=background,
        sharey=True,
    )
    legend_handles = []
    for axis_index, (metric, panel_title, x_scale) in enumerate(panels):
        ax = axes[axis_index]
        for policy in LATENCY_POLICIES:
            points = curves[metric][policy]
            probabilities = [point[0] * 100 for point in points]
            latencies_ms = [point[1] for point in points]
            (line,) = ax.plot(
                latencies_ms,
                probabilities,
                label=policy,
                color=policy_styles[policy]["color"],
                linestyle=policy_styles[policy]["linestyle"],
                linewidth=2.2,
            )
            if axis_index == 0:
                legend_handles.append(line)

        ax.set_title(
            panel_title,
            fontsize=12.5,
            fontweight="semibold",
            color=ink,
            pad=8,
        )
        ax.set_xlabel("Latency (ms)", fontsize=MIN_SOURCE_FONT_PT, color=ink, labelpad=7)
        ax.set_ylim(0, 100)
        ax.set_yticks((0, 25, 50, 75, 100))
        ax.tick_params(
            axis="both",
            which="both",
            labelsize=MIN_SOURCE_FONT_PT,
            colors=ink,
        )
        ax.grid(axis="both", color=grid, linewidth=0.8, alpha=0.8)
        ax.axhline(95, color=quiet, linewidth=1.0, linestyle=(0, (3, 3)), alpha=0.8)
        ax.text(
            0.985,
            0.95,
            "p95",
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=MIN_SOURCE_FONT_PT,
            color=quiet,
        )
        for spine in ax.spines.values():
            spine.set_color(quiet)
            spine.set_linewidth(0.9)

        if x_scale == "log":
            ax.set_xscale("log")
            ticks = (1, 2, 5, 10, 20, 50)
            ax.xaxis.set_major_locator(FixedLocator(ticks))
            ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
            ax.minorticks_off()
            ax.text(
                0.03,
                0.04,
                "log x-axis",
                transform=ax.transAxes,
                ha="left",
                va="bottom",
                fontsize=MIN_SOURCE_FONT_PT,
                color=quiet,
            )

    axes[0].set_ylabel(
        "Cumulative requests (%)",
        fontsize=MIN_SOURCE_FONT_PT,
        color=ink,
        labelpad=8,
    )
    fig.suptitle(
        "Gate 7 v2 latency distributions",
        x=0.07,
        y=0.985,
        ha="left",
        fontsize=14.5,
        fontweight="bold",
        color=invalid_orange,
    )
    fig.text(
        0.07,
        0.925,
        "INVALID / NONCLAIMABLE · descriptive supplement only",
        ha="left",
        fontsize=12.5,
        fontweight="bold",
        color=invalid_orange,
    )
    fig.text(
        0.07,
        0.88,
        "Quantile-derived ECDF · equal-seed pool: 5 × 3,000 requests/policy",
        ha="left",
        fontsize=MIN_SOURCE_FONT_PT,
        color=quiet,
    )
    fig.legend(
        handles=legend_handles,
        labels=list(LATENCY_POLICIES),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.84),
        ncol=3,
        frameon=False,
        fontsize=MIN_SOURCE_FONT_PT,
        handlelength=3.0,
        columnspacing=2.0,
    )
    fig.text(
        0.07,
        0.035,
        "45,000 preserved requests · exact source SHA-256 and method: curated CSV",
        ha="left",
        fontsize=MIN_SOURCE_FONT_PT,
        color=ink,
    )
    fig.subplots_adjust(left=0.115, right=0.985, top=0.72, bottom=0.18, wspace=0.24)

    stem = output_dir / "gate7-v2-latency-distributions"
    fixed_time = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    fig.savefig(
        stem.with_suffix(".png"),
        dpi=240,
        facecolor=background,
        metadata={
            "Software": "Matplotlib",
            "Title": "Gate 7 v2 latency distributions — INVALID / NONCLAIMABLE",
        },
    )
    fig.savefig(
        stem.with_suffix(".svg"),
        facecolor=background,
        metadata={
            "Creator": "Matplotlib",
            "Date": "2026-08-27",
            "Title": "Gate 7 v2 latency distributions — INVALID / NONCLAIMABLE",
        },
    )
    _normalize_svg(stem.with_suffix(".svg"))
    fig.savefig(
        stem.with_suffix(".pdf"),
        facecolor=background,
        metadata={
            "Creator": "Matplotlib",
            "CreationDate": fixed_time,
            "ModDate": fixed_time,
            "Title": "Gate 7 v2 latency distributions — INVALID / NONCLAIMABLE",
        },
    )
    plt.close(fig)


def _write_checksums(output_dir: Path, package_dir: Path) -> None:
    """Write a deterministic nested checksum inventory, excluding itself."""

    lines = []
    for name in sorted(CHECKSUM_FILES):
        path = (
            package_dir / name
            if name in CHECKSUM_PACKAGE_INPUTS
            else output_dir / name
        )
        if not path.is_file():
            raise FileNotFoundError(f"checksum inventory target is missing: {path}")
        lines.append(f"{_sha256(path)}  {name}\n")
    (output_dir / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=repo_root / "artifacts/samples/verification/gate7-v2-invalid",
        help="directory containing the three immutable evidence snapshots",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=script_dir,
        help="directory for generated CSV, JSON, PNG, SVG, and PDF artifacts",
    )
    parser.add_argument(
        "--latency-curation",
        type=Path,
        default=script_dir / LATENCY_CURATED_FILENAME,
        help="byte-bound equal-seed latency quantile/ECDF CSV",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    package_dir = Path(__file__).resolve().parent
    evidence_dir = args.evidence_dir.resolve()
    output_dir = args.output_dir.resolve()
    latency_curation = args.latency_curation.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    observed_hashes: dict[str, str] = {}
    for name, expected in EXPECTED_HASHES.items():
        path = evidence_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        observed = _sha256(path)
        observed_hashes[name] = observed
        if observed != expected:
            raise ValueError(f"immutable evidence identity mismatch for {name}: {observed}")

    manifest = _read_json(evidence_dir / "manifest.json")
    preterminal = _read_json(evidence_dir / "gate7-preterminal-adjudication.json")
    ledger_rows = _read_and_validate_ledger(evidence_dir / "attempt-ledger.jsonl")
    latency_curves, latency_metadata = _read_and_validate_latency_curves(
        latency_curation
    )

    if manifest.get("attempt_id") != ATTEMPT_ID or preterminal.get("attempt_id") != ATTEMPT_ID:
        raise ValueError("attempt identity mismatch")
    anchor = manifest.get("formal_source_anchor")
    if not isinstance(anchor, Mapping):
        raise ValueError("missing formal source anchor")
    if anchor.get("peeled_commit") != SOURCE_COMMIT or anchor.get("tag_name") != SOURCE_TAG:
        raise ValueError("formal source identity mismatch")
    if preterminal.get("schema_version") != "carma-gate7-adjudication-v2":
        raise ValueError("unexpected preterminal schema")
    if (
        preterminal.get("audit_phase") != "preterminal"
        or preterminal.get("status") != "invalid"
        or preterminal.get("claimable") is not False
        or preterminal.get("error_count") != 7
    ):
        raise ValueError("preterminal INVALID classification mismatch")

    run_summaries = manifest.get("run_summaries")
    if not isinstance(run_summaries, list) or len(run_summaries) != 15:
        raise ValueError("expected exactly 15 manifest run summaries")
    request_declaration = manifest.get("artifacts", {}).get("requests.jsonl")
    if not isinstance(request_declaration, Mapping) or request_declaration.get("rows") != 45_000:
        raise ValueError("expected the manifest to bind 45,000 request rows")

    seed_adjudication = preterminal.get("seed_adjudication")
    if not isinstance(seed_adjudication, Mapping) or len(seed_adjudication) != 5:
        raise ValueError("expected exactly five seed adjudications")
    seed_rows = [seed_adjudication[str(seed)] for seed in sorted(int(key) for key in seed_adjudication)]
    if any(not isinstance(row, Mapping) for row in seed_rows):
        raise ValueError("malformed seed adjudication")

    bounds = preterminal.get("frozen_bounds")
    if not isinstance(bounds, Mapping):
        raise ValueError("missing frozen bounds")
    check_rows: list[dict[str, Any]] = []
    passed_checks = 0
    for seed_row in seed_rows:
        checks = seed_row.get("checks")
        values = seed_row.get("paired_values")
        carma = seed_row.get("carma")
        lru = seed_row.get("lru")
        if not all(isinstance(value, Mapping) for value in (checks, values, carma, lru)):
            raise ValueError("malformed paired seed evidence")
        for check in CHECK_ORDER:
            spec = CHECK_SPECS[check]
            passed = bool(checks[check])
            passed_checks += int(passed)
            check_rows.append(
                {
                    "seed": seed_row["seed"],
                    "check": check,
                    "observed_value": values[spec["value_key"]],
                    "unit": spec["unit"],
                    "operator": spec["operator"],
                    "frozen_bound": bounds[spec["bound_key"]],
                    "passed": str(passed).lower(),
                    "carma_run_id": carma["run_id"],
                    "lru_run_id": lru["run_id"],
                }
            )
    if passed_checks != 24:
        raise ValueError(f"expected 24/25 descriptive systems checks, observed {passed_checks}/25")

    run_rows: list[dict[str, Any]] = []
    for run in sorted(run_summaries, key=lambda value: (value["seed"], value["order_position"])):
        run_rows.append({field: run.get(field) for field in RUN_FIELDS})

    semantic = preterminal.get("semantic_recomputation", {})
    aggregate_counts = semantic.get("aggregate_counts") if isinstance(semantic, Mapping) else None
    if not isinstance(aggregate_counts, Mapping):
        raise ValueError("missing independently recomputed semantic counts")
    expected_semantic = {
        "same_concept_hits": 25_400,
        "direct_negative_hits": 3,
        "unlabeled_cross_concept_hits": 2,
    }
    if any(aggregate_counts.get(key) != value for key, value in expected_semantic.items()):
        raise ValueError("semantic aggregate mismatch")

    dependency_python = manifest.get("dependency_attestation", {}).get("python")
    if not isinstance(dependency_python, Mapping):
        raise ValueError("missing dependency Python identity")
    warmups = manifest.get("warmup_artifacts")
    if not isinstance(warmups, Mapping) or len(warmups) != 5:
        raise ValueError("missing warm-up declarations")
    warmup_extra_keys = {
        path: sorted(set(declaration) - {"bytes", "sha256"})
        for path, declaration in warmups.items()
        if isinstance(declaration, Mapping)
    }
    producer_auditor = manifest.get("source_identities", {}).get(AUDITOR_PATH)
    report_auditor = preterminal.get("auditor_identity")
    if not isinstance(producer_auditor, Mapping) or not isinstance(report_auditor, Mapping):
        raise ValueError("missing auditor identity evidence")

    resource_error = next(
        error
        for error in preterminal["errors"]
        if isinstance(error, Mapping) and error.get("code") == "resource_cadence"
    )
    resource_context = resource_error.get("context")
    if not isinstance(resource_context, Mapping):
        raise ValueError("missing resource cadence context")

    declared_artifacts = manifest.get("artifacts")
    if not isinstance(declared_artifacts, Mapping):
        raise ValueError("missing manifest artifact declarations")
    primary_artifact_rows = {
        name: declared_artifacts[name]["rows"]
        for name in (
            "runs.csv",
            "requests.jsonl",
            "resources.jsonl",
            "outcome-latency.jsonl",
        )
    }
    semantic_run_status_counts: dict[str, int] = {}
    for run in run_summaries:
        status = str(run.get("semantic_guardrail_status"))
        semantic_run_status_counts[status] = semantic_run_status_counts.get(status, 0) + 1

    invalidity_rows = [
        {
            "finding_id": "dependency-python-path",
            "classification_layer": "preterminal-auditor",
            "occurrence_count": 1,
            "observed": dependency_python.get("executable"),
            "required": ".venv/bin/python",
            "effect": "one dependency_attestation error",
        },
        {
            "finding_id": "warmup-declaration-shape",
            "classification_layer": "preterminal-auditor",
            "occurrence_count": 5,
            "observed": "each declaration has keys bytes,rows,sha256",
            "required": "each declaration must have exactly bytes,sha256",
            "effect": "five warmup_artifacts errors",
        },
        {
            "finding_id": "resource-cadence-gap",
            "classification_layer": "preterminal-auditor",
            "occurrence_count": 1,
            "observed": str(resource_context.get("maximum_observed_gap_ns")),
            "required": f"<= {resource_context.get('maximum_allowed_gap_ns')} ns",
            "effect": "one resource_cadence error",
        },
        {
            "finding_id": "producer-auditor-identity-shape",
            "classification_layer": "producer-terminal-binding",
            "occurrence_count": 1,
            "observed": ",".join(sorted(producer_auditor)),
            "required": ",".join(sorted(report_auditor)),
            "effect": "wrapper exit 4; unmatched START; no TERMINAL",
        },
    ]

    summary = {
        "schema_version": SCHEMA_VERSION,
        "classification": {
            "formal_status": "INVALID",
            "claimable": False,
            "audit_phase": "preterminal",
            "preterminal_error_count": 7,
            "ordinary_terminal_report_present": False,
            "ledger_terminal_present": False,
            "wrapper_observation": {
                "exit_code": 4,
                "message": "formal preterminal adjudication is malformed",
                "basis": (
                    "contemporaneous formal-run process output; the wrapper observation is contextual "
                    "and is not encoded in the three copied evidence files"
                ),
            },
        },
        "identity": {
            "attempt_id": ATTEMPT_ID,
            "source_commit": SOURCE_COMMIT,
            "source_tag": SOURCE_TAG,
        },
        "evidence_profile": {
            "source_hashes": dict(sorted(observed_hashes.items())),
            "ledger_rows": len(ledger_rows),
            "ledger_events": [row["event"] for row in ledger_rows],
            "ledger_canonical_chain_valid": True,
            "actual_onnx": manifest.get("actual_onnx"),
            "embedding_in_request_path": manifest.get("embedding_in_request_path"),
            "run_count": len(run_summaries),
            "request_count": request_declaration["rows"],
            "primary_artifact_rows": primary_artifact_rows,
            "complete_five_seed_blocks": preterminal.get("completeness", {}).get(
                "complete_five_seed_blocks"
            ),
            "seeds": manifest.get("seeds"),
            "policies": manifest.get("policies"),
            "policy_orders": manifest.get("policy_orders"),
            "config": manifest.get("config"),
        },
        "invalidity_diagnostics": {
            "preterminal_errors": preterminal["errors"],
            "dependency_python_path": {
                "observed": dependency_python.get("executable"),
                "required_literal": ".venv/bin/python",
            },
            "warmup_declaration_extra_keys": warmup_extra_keys,
            "resource_cadence": dict(resource_context),
            "producer_auditor_identity": dict(producer_auditor),
            "preterminal_auditor_identity": dict(report_auditor),
            "producer_auditor_identity_equal": producer_auditor == report_auditor,
        },
        "descriptive_systems_checks": {
            "passed": passed_checks,
            "total": len(check_rows),
            "failed": len(check_rows) - passed_checks,
            "frozen_bounds": dict(bounds),
            "failed_cells": [
                {"seed": row["seed"], "check": row["check"], "observed_value": row["observed_value"]}
                for row in check_rows
                if row["passed"] == "false"
            ],
            "seed_adjudication": {
                str(row["seed"]): {
                    "passes": row["passes"],
                    "checks": dict(row["checks"]),
                    "paired_values": dict(row["paired_values"]),
                    "carma_run_id": row["carma"]["run_id"],
                    "lru_run_id": row["lru"]["run_id"],
                }
                for row in seed_rows
            },
            "formal_use": "descriptive_only_because_attempt_invalid",
        },
        "semantic_guardrail": {
            "status": preterminal.get("semantic_guardrail_status"),
            "hits": aggregate_counts["hits"],
            "cross_concept_hits": aggregate_counts["cross_concept_hits"],
            "same_concept_hits": aggregate_counts["same_concept_hits"],
            "direct_negative_hits": aggregate_counts["direct_negative_hits"],
            "unlabeled_cross_concept_hits": aggregate_counts[
                "unlabeled_cross_concept_hits"
            ],
            "run_status_counts": dict(sorted(semantic_run_status_counts.items())),
        },
        "descriptive_latency_distributions": latency_metadata,
    }

    _write_csv(output_dir / "run-summary.csv", RUN_FIELDS, run_rows)
    _write_csv(
        output_dir / "per-seed-checks.csv",
        (
            "seed",
            "check",
            "observed_value",
            "unit",
            "operator",
            "frozen_bound",
            "passed",
            "carma_run_id",
            "lru_run_id",
        ),
        check_rows,
    )
    _write_csv(
        output_dir / "invalidity-findings.csv",
        (
            "finding_id",
            "classification_layer",
            "occurrence_count",
            "observed",
            "required",
            "effect",
        ),
        invalidity_rows,
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    matplotlib.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": MIN_SOURCE_FONT_PT,
            "pdf.fonttype": 42,
            "svg.fonttype": "none",
            "svg.hashsalt": "gate7-v2-invalid-curated-analysis-v1",
        }
    )
    _render_matrix(output_dir, seed_rows)
    _render_latency_distributions(output_dir, latency_curves)
    _write_checksums(output_dir, package_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "formal_status": "INVALID",
                "runs": len(run_summaries),
                "requests": request_declaration["rows"],
                "descriptive_checks_passed": f"{passed_checks}/{len(check_rows)}",
                "latency_curated_rows": latency_metadata["curated_rows"],
                "latency_curated_sha256": latency_metadata[
                    "curated_table_sha256"
                ],
                "output_dir": str(output_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
