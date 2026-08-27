#!/usr/bin/env python3
"""Build the compact, descriptive Gate 7 v2 latency quantile table.

The source request stream is preserved evidence and is opened read-only.  The
output is an equal-seed empirical mixture: every policy contributes exactly
3,000 observations from each of five seeds (15,000 observations per policy).
For each policy and latency metric, the script emits the 1,001 points p=0.000,
0.001, ..., 1.000 using Hyndman--Fan Type 7 linear interpolation.

The table reproduces the plotted quantile/ECDF approximation.  It is strictly
descriptive because the underlying formal attempt is INVALID and nonclaimable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Sequence


SCHEMA_VERSION = "carma-gate7-v2-latency-quantiles-v1"
ATTEMPT_ID = "20260827T131743602373Z-557c6ac0578c"
SOURCE_RELATIVE_PATH = (
    "artifacts/gate7-v2-onnx-attempts/"
    "attempt-20260827T131736Z-1784/requests.jsonl"
)
SOURCE_SHA256 = "b23fe24164c7b11a7c164deff1b1f1e9571e41e39231bc04e269625f1eed6358"
SOURCE_ROWS = 45_000
SEEDS = (20261001, 20261002, 20261003, 20261004, 20261005)
POLICIES = ("CARMA", "LRU", "LFU")
METRICS = ("request_total_ns", "post_embedding_total_ns")
ROWS_PER_SEED_POLICY = 3_000
POOLED_COUNT = ROWS_PER_SEED_POLICY * len(SEEDS)
QUANTILE_STEPS = 1_000
POOLING_METHOD = "equal_seed_empirical_mixture"
QUANTILE_METHOD = "Hyndman-Fan_Type_7_linear_h=(n-1)*p"
FIELDS = (
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _type7_decimal(values: Sequence[int], step: int) -> str:
    """Return the exact Type-7 quantile at p=step/1000 as a decimal string."""

    numerator = (len(values) - 1) * step
    lower_index, remainder = divmod(numerator, QUANTILE_STEPS)
    upper_index = min(lower_index + 1, len(values) - 1)
    scaled = (
        values[lower_index] * QUANTILE_STEPS
        + (values[upper_index] - values[lower_index]) * remainder
    )
    integer, fractional = divmod(scaled, QUANTILE_STEPS)
    return str(integer) if fractional == 0 else f"{integer}.{fractional:03d}".rstrip("0")


def _parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requests",
        type=Path,
        default=repo_root / SOURCE_RELATIVE_PATH,
        help="preserved Gate 7 v2 requests.jsonl (read-only input)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=script_dir / "latency-quantile-ecdf.csv",
        help="curated quantile CSV to write",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source = args.requests.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    observed_sha256 = _sha256(source)
    if observed_sha256 != SOURCE_SHA256:
        raise ValueError(
            f"preserved request-stream identity mismatch: {observed_sha256}"
        )

    cell_counts: Counter[tuple[str, int]] = Counter()
    values: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    row_count = 0
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"request row {line_number} is not an object")
            if row.get("attempt_id") != ATTEMPT_ID:
                raise ValueError(f"request row {line_number} attempt mismatch")
            policy = row.get("policy")
            seed = row.get("seed")
            if policy not in POLICIES or seed not in SEEDS:
                raise ValueError(f"request row {line_number} policy/seed mismatch")
            cell_counts[(str(policy), int(seed))] += 1
            for metric in METRICS:
                value = row.get(metric)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(
                        f"request row {line_number} has invalid {metric}: {value!r}"
                    )
                values[(str(policy), metric)].append(value)
            row_count += 1

    if row_count != SOURCE_ROWS:
        raise ValueError(f"expected {SOURCE_ROWS} request rows, found {row_count}")
    expected_cells = {
        (policy, seed): ROWS_PER_SEED_POLICY
        for policy in POLICIES
        for seed in SEEDS
    }
    if dict(cell_counts) != expected_cells:
        raise ValueError("request stream is not a complete equal-size policy/seed matrix")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for metric in METRICS:
            for policy in POLICIES:
                pooled = sorted(values[(policy, metric)])
                if len(pooled) != POOLED_COUNT:
                    raise ValueError(f"unexpected pooled count for {policy}/{metric}")
                for step in range(QUANTILE_STEPS + 1):
                    writer.writerow(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "attempt_id": ATTEMPT_ID,
                            "formal_status": "INVALID",
                            "claimable": "false",
                            "source_relative_path": SOURCE_RELATIVE_PATH,
                            "source_sha256": SOURCE_SHA256,
                            "source_row_count": SOURCE_ROWS,
                            "pooling_method": POOLING_METHOD,
                            "quantile_method": QUANTILE_METHOD,
                            "seed_count": len(SEEDS),
                            "rows_per_seed_policy": ROWS_PER_SEED_POLICY,
                            "pooled_count": POOLED_COUNT,
                            "policy": policy,
                            "metric": metric,
                            "unit": "ns",
                            "quantile_probability": f"{step / QUANTILE_STEPS:.3f}",
                            "latency_ns": _type7_decimal(pooled, step),
                        }
                    )

    print(
        json.dumps(
            {
                "status": "ok",
                "formal_status": "INVALID",
                "claimable": False,
                "source_rows": row_count,
                "curated_rows": len(POLICIES) * len(METRICS) * (QUANTILE_STEPS + 1),
                "output": str(output),
                "output_sha256": _sha256(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
