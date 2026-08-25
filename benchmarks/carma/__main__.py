"""Command-line entry point for the deterministic CARMA benchmark."""

import argparse
import math
from pathlib import Path
from typing import Optional, Sequence

from benchmarks.carma.runner import (
    DEFAULT_WORKLOAD_SIZES,
    POLICIES,
    BenchmarkConfig,
    run_benchmark,
)


def _half_life(value: str) -> Optional[float]:
    normalized = value.strip().lower()
    if normalized in ("none", "default"):
        return None
    if normalized in ("inf", "infinity"):
        return math.inf
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("demand half-life must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay deterministic synthetic semantic-cache traces through "
            "GPTCache LRU, LFU, CARMA, and its no-clustering ablation."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/carma/results"),
        help="artifact directory (default: benchmarks/carma/results)",
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=tuple(DEFAULT_WORKLOAD_SIZES),
        default=tuple(DEFAULT_WORKLOAD_SIZES),
    )
    parser.add_argument(
        "--policies",
        nargs="+",
        choices=POLICIES,
        default=POLICIES,
    )
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--capacity", type=int, default=50)
    parser.add_argument(
        "--requests",
        type=int,
        default=None,
        help="override each workload's CI request count",
    )
    parser.add_argument("--hit-threshold", type=float, default=0.97)
    parser.add_argument(
        "--cluster-similarity-threshold", type=float, default=0.70
    )
    parser.add_argument("--cell-threshold", type=float, default=0.88)
    parser.add_argument(
        "--demand-half-life", type=_half_life, default=128.0
    )
    parser.add_argument("--quota-strength", type=float, default=0.5)
    parser.add_argument("--ghost-support-threshold", type=float, default=1.5)
    parser.add_argument("--admission-margin", type=float, default=1.05)
    parser.add_argument("--centroid-alpha", type=float, default=0.05)
    parser.add_argument("--entry-hit-weight", type=float, default=0.25)
    parser.add_argument(
        "--measure-latency",
        action="store_true",
        help="measure wall latency; omitted in deterministic CI mode",
    )
    parser.add_argument(
        "--skip-determinism-check",
        action="store_true",
        help="skip the second non-timing replay used to verify determinism",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = BenchmarkConfig(
        workloads=tuple(args.workloads),
        policies=tuple(args.policies),
        seed=args.seed,
        capacity=args.capacity,
        request_count=args.requests,
        hit_threshold=args.hit_threshold,
        cluster_similarity_threshold=args.cluster_similarity_threshold,
        cell_threshold=args.cell_threshold,
        demand_half_life=args.demand_half_life,
        quota_strength=args.quota_strength,
        ghost_support_threshold=args.ghost_support_threshold,
        admission_margin=args.admission_margin,
        centroid_alpha=args.centroid_alpha,
        entry_hit_weight=args.entry_hit_weight,
        measure_latency=args.measure_latency,
        verify_determinism=not args.skip_determinism_check,
    )
    summaries = run_benchmark(config, args.output)
    print("wrote %d runs to %s" % (len(summaries), args.output))
    print(
        "policy,workload,valid_hit_rate,false_hit_rate,"
        "opportunity_recall,p95_latency_us"
    )
    for summary in summaries:
        print(
            "%s,%s,%.8f,%.8f,%.8f,%.6f"
            % (
                summary["policy"],
                summary["workload"],
                summary["valid_hit_rate"],
                summary["false_hit_rate"],
                summary["opportunity_recall"],
                summary["p95_latency_us"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
