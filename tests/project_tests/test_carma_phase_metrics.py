"""Focused phase-level benchmark metric tests."""

import json

from benchmarks.carma.full_experiment import _evaluate_synthetic_success_gates
from benchmarks.carma.runner import (
    _phase_metrics,
    phase_recovery_summary,
    phase_valid_hit_summary,
)
from benchmarks.carma.synthetic import (
    Catalog,
    phase_shift_trace,
    pollution_scan_trace,
)


def _row(index, phase, valid_hit=False, opportunity=True):
    return {
        "request_index": index,
        "phase": phase,
        "valid_hit": valid_hit,
        "reuse_opportunity": opportunity,
    }


def test_scan_return_valid_hit_rate_uses_only_return_phase_requests():
    records = [
        _row(0, "scan-warm", True),
        _row(1, "scan-unique", False),
        _row(2, "scan-return", True),
        _row(3, "scan-return", False),
        _row(4, "scan-return", True),
        _row(5, "scan-return", True),
    ]

    phase = phase_valid_hit_summary(records, "scan-return")
    result = _phase_metrics(records)

    assert phase["requests"] == 4
    assert phase["valid_hits"] == 3
    assert phase["valid_hit_rate"] == 0.75
    assert result["scan_return_requests"] == phase["requests"]
    assert result["scan_return_valid_hits"] == phase["valid_hits"]
    assert result["scan_return_valid_hit_rate"] == phase["valid_hit_rate"]
    assert result["shift_recovery_lag_mean_requests"] is None


def test_phase_recovery_lag_has_fixed_window_target_and_censoring():
    records = [_row(index, "shift-0", True) for index in range(10)]
    records.extend(
        _row(
            10 + offset,
            "shift-1",
            valid_hit=offset >= 3,
            opportunity=True,
        )
        for offset in range(20)
    )
    records.extend(
        _row(30 + offset, "shift-2", valid_hit=False, opportunity=True)
        for offset in range(20)
    )

    first = phase_recovery_summary(records)
    second = phase_recovery_summary(records)
    mapped = _phase_metrics(records)

    assert first == second
    assert first["transitions"] == [
        {
            "phase": "shift-1",
            "requests": 20,
            "window_requests": 2,
            "tail_requests": 5,
            "oracle_tail_opportunity_rate": 1.0,
            "target_valid_hit_rate": 0.9,
            "recovered": True,
            "lag_requests": 3,
        },
        {
            "phase": "shift-2",
            "requests": 20,
            "window_requests": 2,
            "tail_requests": 5,
            "oracle_tail_opportunity_rate": 1.0,
            "target_valid_hit_rate": 0.9,
            "recovered": False,
            "lag_requests": 20,
        },
    ]
    assert first["phase_recovery_lag_mean_requests"] == 11.5
    assert first["phase_recovery_lag_max_requests"] == 20
    assert first["phase_recovery_failures"] == 1
    assert mapped["shift_recovery_lag_mean_requests"] == 11.5
    assert json.loads(mapped["shift_recovery_transitions_json"]) == first[
        "transitions"
    ]


def test_zero_opportunity_phase_is_censored_not_reported_as_instant_recovery():
    records = [_row(index, "shift-0", True) for index in range(5)]
    records.extend(
        _row(5 + offset, "shift-1", valid_hit=False, opportunity=False)
        for offset in range(10)
    )

    result = phase_recovery_summary(records)
    transition = result["transitions"][0]

    assert transition["target_valid_hit_rate"] == 0.0
    assert transition["recovered"] is False
    assert transition["lag_requests"] == 10


def test_frozen_shift_and_scan_phase_shapes_match_the_contract():
    catalog = Catalog()
    shifted = phase_shift_trace(catalog, count=100, seed=7)
    phase_sizes = [
        sum(row.phase == "shift-%d" % phase for row in shifted)
        for phase in range(5)
    ]
    assert phase_sizes == [
        20,
        20,
        20,
        20,
        20,
    ]

    scanned = pollution_scan_trace(catalog, count=100, seed=7, capacity=20)
    assert sum(row.phase == "scan-warm" for row in scanned) == 30
    assert sum(row.phase == "scan-unique" for row in scanned) == 40
    assert sum(row.phase == "scan-return" for row in scanned) == 30
    unique_ids = [row.concept_id for row in scanned if row.phase == "scan-unique"]
    assert len(unique_ids) == len(set(unique_ids)) == 40


def test_synthetic_gate_boundaries_are_programmatically_adjudicated():
    def row(workload, metric, delta, low, high, p_holm=""):
        return {
            "stage": "primary_test",
            "row_type": "comparison",
            "workload": workload,
            "capacity": 100,
            "metric": metric,
            "policy": "CARMA",
            "comparator": "BEST_BASELINE",
            "delta_mean": delta,
            "delta_ci_low": low,
            "delta_ci_high": high,
            "p_holm": p_holm,
        }

    aggregate = [
        row("phase_shift", "valid_hit_rate", 0.02, 0.0001, 0.04, 0.05),
        row(
            "pollution_scan",
            "scan_return_valid_hit_rate",
            0.05,
            0.0001,
            0.08,
            0.05,
        ),
        row("stationary", "valid_hit_rate", 0.0, -0.01, 0.01),
    ]
    aggregate.extend(
        row(workload, "safe_token_saving_ratio", 0.0, -0.005, 0.01)
        for workload in ("stationary", "phase_shift", "pollution_scan")
    )

    gates = _evaluate_synthetic_success_gates(aggregate)
    assert all(
        value["passes"]
        for key, value in gates.items()
        if key.startswith("gate_")
    )

    aggregate[1]["delta_mean"] = 0.049999
    gates = _evaluate_synthetic_success_gates(aggregate)
    assert gates["gate_4_scan_return_vhr"]["passes"] is False
