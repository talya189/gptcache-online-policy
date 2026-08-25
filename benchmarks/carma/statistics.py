"""Dependency-light paired statistics for CARMA experiments.

The Wilcoxon implementation is exact for the small paired samples used by the
benchmark.  It conditions on observed absolute ranks, including average ranks
for ties, and enumerates the sign distribution with dynamic programming.
"""

import math
import random
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class ConfidenceInterval:
    estimate: float
    low: float
    high: float


@dataclass(frozen=True)
class WilcoxonResult:
    statistic: float
    p_value: float
    nonzero_pairs: int
    rank_biserial: float


def bootstrap_mean_ci(
    values: Sequence[float],
    confidence: float = 0.95,
    resamples: int = 10000,
    seed: int = 20260825,
) -> ConfidenceInterval:
    """Percentile bootstrap interval for a seed-level mean."""

    clean = _finite(values)
    if not clean:
        return ConfidenceInterval(0.0, 0.0, 0.0)
    if resamples < 1:
        raise ValueError("resamples must be positive")
    rng = random.Random(seed)
    count = len(clean)
    means = []
    for _ in range(resamples):
        means.append(sum(clean[rng.randrange(count)] for _ in range(count)) / count)
    alpha = 1.0 - confidence
    return ConfidenceInterval(
        estimate=sum(clean) / count,
        low=_percentile(means, alpha / 2.0),
        high=_percentile(means, 1.0 - alpha / 2.0),
    )


def paired_bootstrap_ci(
    left: Sequence[float],
    right: Sequence[float],
    confidence: float = 0.95,
    resamples: int = 10000,
    seed: int = 20260825,
) -> ConfidenceInterval:
    """Paired percentile bootstrap interval for ``mean(left - right)``."""

    left_clean, right_clean = _paired_finite(left, right)
    differences = [a - b for a, b in zip(left_clean, right_clean)]
    return bootstrap_mean_ci(differences, confidence, resamples, seed)


def wilcoxon_signed_rank(
    left: Sequence[float], right: Sequence[float], zero_tolerance: float = 1e-15
) -> WilcoxonResult:
    """Exact two-sided paired Wilcoxon signed-rank test.

    Zero differences are removed. Average tied ranks are represented exactly
    after multiplying all ranks by two, making the subset-sum distribution an
    integer dynamic program.
    """

    left_clean, right_clean = _paired_finite(left, right)
    differences = [a - b for a, b in zip(left_clean, right_clean)]
    differences = [value for value in differences if abs(value) > zero_tolerance]
    if not differences:
        return WilcoxonResult(0.0, 1.0, 0, 0.0)

    absolute = [abs(value) for value in differences]
    ranks = _average_ranks(absolute)
    positive = sum(rank for rank, diff in zip(ranks, differences) if diff > 0)
    negative = sum(rank for rank, diff in zip(ranks, differences) if diff < 0)
    statistic = min(positive, negative)

    scaled_ranks = [int(round(rank * 2.0)) for rank in ranks]
    observed = int(round(positive * 2.0))
    distribution = {0: 1}
    for rank in scaled_ranks:
        updated = dict(distribution)
        for subtotal, ways in distribution.items():
            updated[subtotal + rank] = updated.get(subtotal + rank, 0) + ways
        distribution = updated

    total_assignments = 2 ** len(scaled_ranks)
    lower = sum(ways for value, ways in distribution.items() if value <= observed)
    upper = sum(ways for value, ways in distribution.items() if value >= observed)
    p_value = min(1.0, 2.0 * min(lower, upper) / total_assignments)
    rank_total = positive + negative
    rank_biserial = (positive - negative) / rank_total if rank_total else 0.0
    return WilcoxonResult(
        statistic=statistic,
        p_value=p_value,
        nonzero_pairs=len(differences),
        rank_biserial=rank_biserial,
    )


def holm_adjust(p_values: Sequence[float]) -> List[float]:
    """Holm step-down family-wise error correction in original order."""

    if not p_values:
        return []
    indexed = sorted(enumerate(p_values), key=lambda item: (item[1], item[0]))
    adjusted = [1.0] * len(indexed)
    running = 0.0
    total = len(indexed)
    for rank, (original_index, p_value) in enumerate(indexed):
        if not math.isfinite(p_value) or not 0 <= p_value <= 1:
            raise ValueError("p-values must be finite and in [0, 1]")
        candidate = min(1.0, (total - rank) * p_value)
        running = max(running, candidate)
        adjusted[original_index] = running
    return adjusted


def _average_ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = ((cursor + 1) + end) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def _finite(values: Iterable[float]) -> List[float]:
    clean = [float(value) for value in values]
    if any(not math.isfinite(value) for value in clean):
        raise ValueError("statistics require finite values")
    return clean


def _paired_finite(
    left: Sequence[float], right: Sequence[float]
) -> Tuple[List[float], List[float]]:
    if len(left) != len(right):
        raise ValueError("paired samples must have equal length")
    return _finite(left), _finite(right)


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] + fraction * (
        ordered[upper_index] - ordered[lower_index]
    )
