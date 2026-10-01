#!/usr/bin/env python3
"""Build the Gate 7 v5 frozen-check figure from the canonical adjudication."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ADJUDICATION = (
    PROJECT_ROOT
    / "artifacts/gate7-v5-onnx-attempts"
    / "attempt-20260831T165407Z-31743"
    / "gate7-adjudication.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = json.loads(args.adjudication.read_text(encoding="utf-8"))
    if report.get("status") != "pass" or report.get("claimable") is not True:
        raise SystemExit("refusing to chart a non-PASS or nonclaimable adjudication")

    seeds = sorted(report["seed_adjudication"], key=int)
    bounds = report["frozen_bounds"]
    rows = []
    for seed in seeds:
        result = report["seed_adjudication"][seed]
        if not result["passes"] or not all(result["checks"].values()):
            raise SystemExit(f"seed {seed} does not pass every frozen check")
        values = result["paired_values"]
        rows.append(
            {
                "seed": seed,
                "full_request_p95_ratio": values["p95_ratio"],
                "post_embedding_p95_delta_ms": values[
                    "post_embedding_p95_delta_ns"
                ]
                / 1_000_000,
                "paired_policy_exclusive_p95_delta_ms": values[
                    "paired_policy_exclusive_p95_delta_ns"
                ]
                / 1_000_000,
                "throughput_ratio": values["throughput_ratio"],
                "peak_rss_ratio": values["rss_ratio"],
                "peak_rss_delta_mib": values["rss_delta_bytes"] / (1024 * 1024),
                "all_six_checks_pass": True,
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "gate7-v5-seed-checks.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 8.5,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
        }
    )
    fig, axes = plt.subplots(2, 3, figsize=(10.6, 5.55))
    x = list(range(len(seeds)))
    labels = [seed[-2:] for seed in seeds]
    blue = "#1769AA"
    green = "#237A45"
    ink = "#20252B"
    grid = "#D9DEE3"

    panels = [
        (
            "full_request_p95_ratio",
            "Full-request p95 ratio",
            bounds["p95_ratio_max"],
            "≤ 1.25",
            (0.96, 1.28),
            lambda value: f"{value:.3f}",
        ),
        (
            "post_embedding_p95_delta_ms",
            "Post-embedding p95 delta",
            bounds["post_embedding_p95_delta_ns_max"] / 1_000_000,
            "≤ 0.50 ms",
            (-0.25, 0.55),
            lambda value: f"{value:+.3f}",
        ),
        (
            "paired_policy_exclusive_p95_delta_ms",
            "Paired policy-exclusive p95 delta",
            bounds["paired_policy_exclusive_p95_delta_ns_max"] / 1_000_000,
            "≤ 0.50 ms",
            (0.0, 0.55),
            lambda value: f"{value:.3f}",
        ),
        (
            "throughput_ratio",
            "Throughput ratio",
            bounds["throughput_ratio_min"],
            "≥ 0.90",
            (0.88, 1.025),
            lambda value: f"{value:.3f}",
        ),
        (
            "peak_rss_ratio",
            "Peak RSS ratio",
            bounds["rss_ratio_max"],
            "≤ 1.20",
            (0.99, 1.21),
            lambda value: f"{value:.3f}",
        ),
        (
            "peak_rss_delta_mib",
            "Peak RSS delta",
            bounds["rss_delta_bytes_max"] / (1024 * 1024),
            "≤ 64 MiB",
            (0.0, 68.0),
            lambda value: f"{value:.1f}",
        ),
    ]

    for ax, (key, title, threshold, threshold_label, ylim, formatter) in zip(
        axes.flat, panels
    ):
        values = [row[key] for row in rows]
        ax.plot(x, values, color=blue, marker="o", linewidth=2.0, markersize=5.5)
        ax.axhline(threshold, color=ink, linestyle=(0, (4, 3)), linewidth=1.2)
        if key == "throughput_ratio":
            ax.axhspan(threshold, ylim[1], color=green, alpha=0.055, zorder=0)
        else:
            ax.axhspan(ylim[0], threshold, color=green, alpha=0.055, zorder=0)
        if key == "post_embedding_p95_delta_ms":
            ax.axhline(0, color="#8A929A", linewidth=0.8)
        ax.set_title(title, loc="left", fontweight="bold", color=ink)
        ax.set_xlim(-0.35, len(x) - 0.65)
        ax.set_ylim(*ylim)
        ax.set_xticks(x, labels)
        ax.set_xlabel("Seed suffix (202612xx)")
        ax.grid(axis="y", color=grid, linewidth=0.7)
        ax.spines[["top", "right"]].set_visible(False)
        ax.text(
            0.98,
            0.94,
            threshold_label,
            transform=ax.transAxes,
            ha="right",
            va="top",
            color=ink,
            fontsize=8,
        )
        span = ylim[1] - ylim[0]
        for xpos, value in zip(x, values):
            offset = 0.025 * span
            va = "bottom"
            ypos = value + offset
            if value > ylim[1] - 0.10 * span:
                ypos = value - offset
                va = "top"
            ax.text(
                xpos,
                ypos,
                formatter(value),
                ha="center",
                va=va,
                color=blue,
                fontsize=7.3,
            )

    fig.suptitle(
        "Gate 7 v5 per-seed checks against frozen bounds",
        x=0.012,
        y=0.985,
        ha="left",
        fontsize=15,
        fontweight="bold",
        color=ink,
    )
    fig.text(
        0.012,
        0.942,
        "Claimable PASS  •  15 fresh real-ONNX processes  •  45,000 requests  •  all 30 checks pass",
        ha="left",
        va="top",
        fontsize=9.5,
        color=green,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.905), h_pad=0.95, w_pad=1.05)

    fig.savefig(
        args.output_dir / "gate7-v5-frozen-checks.png",
        dpi=240,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(fig)


if __name__ == "__main__":
    main()
