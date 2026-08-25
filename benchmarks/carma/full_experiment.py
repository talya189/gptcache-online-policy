"""Validation-first CARMA experiment and paired statistical analysis.

The test traces are not evaluated until one configuration has been selected
from the complete validation grid.  Selection uses one predeclared objective:
mean per-trace VHR normalized by that trace's best validation VHR, subject to
zero false hits. Ties use a fixed lexicographic parameter order.
"""

import argparse
import csv
import hashlib
from importlib import metadata as importlib_metadata
import itertools
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from benchmarks.carma.runner import (
    RUN_FIELDS,
    SCHEMA_VERSION,
    BenchmarkConfig,
    CacheSimulation,
    RunResult,
)
from benchmarks.carma.statistics import (
    bootstrap_mean_ci,
    holm_adjust,
    paired_bootstrap_ci,
    wilcoxon_signed_rank,
)
from benchmarks.carma.synthetic import (
    CELL_WEIGHT,
    CONCEPT_WEIGHT,
    NEAR_GROUP_WEIGHT,
    TOPIC_WEIGHT,
    Concept,
    build_trace,
    trace_hash,
)


EXPERIMENT_SCHEMA = "carma-full-experiment-v2"
BASELINE_COMMIT = "c59fb3a6152a4458b2a070ca183b61c4b614095f"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_CONTRACT = PROJECT_ROOT / "docs/project/experiment-contract.md"
WORKLOADS = ("stationary", "phase_shift", "pollution_scan")
VALIDATION_SEEDS = (20260825, 20260826, 20260827)
TEST_SEEDS = tuple(range(20260901, 20260911))
DIAGNOSTIC_SEEDS = (20261001, 20261002, 20261003)
TOPIC_THRESHOLDS = (0.70, 0.80, 0.90)
CELL_THRESHOLDS = (0.95, 0.97)
HALF_LIVES = (100.0, 500.0, 2000.0, math.inf)
QUOTA_STRENGTHS = (0.0, 0.5, 1.0, 2.0)
HIT_THRESHOLD = 0.97
HARD_NEGATIVE_HIT_THRESHOLD = 0.95
GHOST_SUPPORT_THRESHOLD = 1.5
PRIMARY_CAPACITY = 100
FULL_VALIDATION_REQUESTS = 2000
FULL_TEST_REQUESTS = 10000
HARD_NEGATIVE_REQUESTS = 200
CAPACITY_SWEEP = (20, 50, 200)
BASE_METRICS = (
    "valid_hit_rate",
    "false_hit_rate",
    "opportunity_recall",
    "safe_token_saving_ratio",
)
POLLUTION_PHASE_METRICS = (
    "scan_return_valid_hit_rate",
)
SHIFT_PHASE_METRICS = ("shift_recovery_lag_mean_requests",)
METRICS = BASE_METRICS + POLLUTION_PHASE_METRICS + SHIFT_PHASE_METRICS
LOWER_IS_BETTER = {
    "false_hit_rate",
    "shift_recovery_lag_mean_requests",
}
ARTIFACT_NAMES = (
    "validation.csv",
    "validation_runs.csv",
    "runs.csv",
    "aggregate.csv",
    "requests.jsonl",
    "metadata.json",
)


@dataclass(frozen=True)
class Parameters:
    topic_threshold: float
    cell_threshold: float
    demand_half_life: float
    quota_strength: float

    @property
    def config_id(self) -> str:
        encoded = json.dumps(
            _json_safe(asdict(self)), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


class CompactCatalog:
    """Existing trace interface with compact exact hierarchy vectors.

    The original CI catalog uses exact one-hot coordinates per concept. Full
    10k pollution scans require thousands of concepts, so this pre-test
    scalability amendment reuses orthogonal cell-local feature blocks across
    topics. It preserves the advertised within-topic cosines exactly: 0.72
    across cells, 0.88 within a cell, and 0.96 for deliberate twins. Reuse
    across topics can add at most 0.12, far below every clustering threshold.
    Request order is still produced by the existing trace builders.
    """

    def __init__(
        self,
        topics: int = 6,
        cells_per_topic: int = 4,
        concepts_per_cell: int = 192,
    ):
        self.topics = topics
        self.cells_per_topic = cells_per_topic
        self.concepts_per_cell = concepts_per_cell
        self._concepts = self._build()
        self.by_id = {concept.concept_id: concept for concept in self._concepts}

    @property
    def concepts(self) -> Sequence[Concept]:
        return self._concepts

    def for_topic(self, topic_id: int) -> List[Concept]:
        return [concept for concept in self._concepts if concept.topic_id == topic_id]

    def for_cell(self, topic_id: int, cell_id: int) -> List[Concept]:
        return [
            concept
            for concept in self._concepts
            if concept.topic_id == topic_id and concept.cell_id == cell_id
        ]

    def _build(self) -> Tuple[Concept, ...]:
        cell_count = self.topics * self.cells_per_topic
        feature_offset = self.topics + cell_count
        features_per_cell = self.concepts_per_cell + 1
        dimension = feature_offset + self.cells_per_topic * features_per_cell
        concepts: List[Concept] = []
        for topic_id in range(self.topics):
            for cell_id in range(self.cells_per_topic):
                absolute_cell = topic_id * self.cells_per_topic + cell_id
                local_offset = feature_offset + cell_id * features_per_cell
                for local_id in range(self.concepts_per_cell):
                    concept_id = "t%02d-c%02d-q%03d" % (
                        topic_id,
                        cell_id,
                        local_id,
                    )
                    vector = np.zeros(dimension, dtype=np.float32)
                    vector[topic_id] = math.sqrt(TOPIC_WEIGHT)
                    vector[self.topics + absolute_cell] = math.sqrt(CELL_WEIGHT)
                    if local_id < 2:
                        vector[local_offset] = math.sqrt(NEAR_GROUP_WEIGHT)
                        vector[local_offset + 1 + local_id] = math.sqrt(
                            CONCEPT_WEIGHT
                        )
                    else:
                        vector[local_offset + 1 + local_id] = math.sqrt(
                            NEAR_GROUP_WEIGHT + CONCEPT_WEIGHT
                        )
                    vector /= np.linalg.norm(vector)
                    token_cost = 16 + (
                        topic_id * 31 + cell_id * 17 + local_id * 13
                    ) % 241
                    concepts.append(
                        Concept(
                            concept_id=concept_id,
                            topic_id=topic_id,
                            cell_id=cell_id,
                            local_id=local_id,
                            embedding=vector,
                            token_cost=token_cost,
                        )
                    )
        return tuple(concepts)


def parameter_grid() -> Tuple[Parameters, ...]:
    return tuple(
        Parameters(*values)
        for values in itertools.product(
            TOPIC_THRESHOLDS,
            CELL_THRESHOLDS,
            HALF_LIVES,
            QUOTA_STRENGTHS,
        )
    )


def _measure_catalog_geometry(catalog: CompactCatalog) -> Dict[str, Dict[str, float]]:
    """Measure constructive extrema and enforce threshold-relevant geometry."""

    twins: List[float] = []
    same_cell: List[float] = []
    same_topic: List[float] = []
    different_topic: List[float] = []
    local_samples = sorted({0, 1, 2, catalog.concepts_per_cell - 1})

    for topic_id in range(catalog.topics):
        cells = [
            catalog.for_cell(topic_id, cell_id)
            for cell_id in range(catalog.cells_per_topic)
        ]
        for concepts in cells:
            twins.append(_cosine(concepts[0], concepts[1]))
            for local_id in range(2, catalog.concepts_per_cell):
                same_cell.append(_cosine(concepts[0], concepts[local_id]))
                same_cell.append(
                    _cosine(concepts[local_id - 1], concepts[local_id])
                )
        for left_cell in range(catalog.cells_per_topic):
            for right_cell in range(left_cell + 1, catalog.cells_per_topic):
                for left_local in local_samples:
                    for right_local in local_samples:
                        same_topic.append(
                            _cosine(
                                cells[left_cell][left_local],
                                cells[right_cell][right_local],
                            )
                        )

    first_topic = [
        catalog.for_cell(0, cell_id)
        for cell_id in range(catalog.cells_per_topic)
    ]
    second_topic = [
        catalog.for_cell(1, cell_id)
        for cell_id in range(catalog.cells_per_topic)
    ]
    for left_cell in range(catalog.cells_per_topic):
        for right_cell in range(catalog.cells_per_topic):
            for left_local in local_samples:
                for right_local in local_samples:
                    different_topic.append(
                        _cosine(
                            first_topic[left_cell][left_local],
                            second_topic[right_cell][right_local],
                        )
                    )

    measured = {
        "deliberate_twins": _cosine_range(twins),
        "same_cell_non_twins": _cosine_range(same_cell),
        "same_topic_different_cell": _cosine_range(same_topic),
        "different_topic": _cosine_range(different_topic),
    }
    expected = {
        "deliberate_twins": 0.96,
        "same_cell_non_twins": 0.88,
        "same_topic_different_cell": 0.72,
    }
    for label, value in expected.items():
        bounds = measured[label]
        if any(abs(bounds[side] - value) > 1e-6 for side in ("min", "max")):
            raise RuntimeError("compact catalog geometry drifted for %s" % label)
    if measured["different_topic"]["max"] >= min(TOPIC_THRESHOLDS):
        raise RuntimeError("compact catalog aliases different topics")
    return measured


def _preflight_protocol(
    catalog: CompactCatalog, request_count: int, capacity: int
) -> Dict[str, Any]:
    """Validate frozen trace structure without opening a test or tuning seed."""

    seed = 20260000
    traces = {
        workload: build_trace(workload, catalog, request_count, seed, capacity)
        for workload in WORKLOADS
    }
    if any(len(rows) != request_count for rows in traces.values()):
        raise RuntimeError("protocol preflight produced a short trace")

    shift_counts = {
        "shift-%d" % phase: sum(
            int(row.phase == "shift-%d" % phase)
            for row in traces["phase_shift"]
        )
        for phase in range(5)
    }
    if max(shift_counts.values()) - min(shift_counts.values()) > 1:
        raise RuntimeError("phase-shift trace is not split into five equal phases")

    scan_counts = {
        phase: sum(int(row.phase == phase) for row in traces["pollution_scan"])
        for phase in ("scan-warm", "scan-unique", "scan-return")
    }
    expected_scan = {
        "scan-warm": 3 * request_count // 10,
        "scan-unique": 4 * request_count // 10,
        "scan-return": request_count
        - 3 * request_count // 10
        - 4 * request_count // 10,
    }
    if scan_counts != expected_scan:
        raise RuntimeError("pollution trace does not have a 30/40/30 split")
    scanned = [
        row.concept_id
        for row in traces["pollution_scan"]
        if row.phase == "scan-unique"
    ]
    if len(scanned) != len(set(scanned)):
        raise RuntimeError("pollution preflight contains a repeated scan concept")

    return {
        "status": "passed_before_validation",
        "seed": seed,
        "seed_disjoint_from_validation_test_and_diagnostic": (
            seed not in VALIDATION_SEEDS
            and seed not in TEST_SEEDS
            and seed not in DIAGNOSTIC_SEEDS
        ),
        "request_count": request_count,
        "capacity": capacity,
        "phase_shift_counts": shift_counts,
        "pollution_counts": scan_counts,
        "pollution_unique_concepts": len(set(scanned)),
        "trace_hashes": {
            workload: trace_hash(rows) for workload, rows in sorted(traces.items())
        },
    }


def run_experiment(
    mode: str,
    output_dir: Path,
    bootstrap_resamples: int,
    include_capacity_sweep: bool,
    include_no_decay_ablation: bool,
    include_policy_ablations: bool = False,
) -> Dict[str, Any]:
    if mode not in ("smoke", "full"):
        raise ValueError("mode must be smoke or full")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".carma-full-", dir=str(output_dir)
    ) as temporary:
        staging_dir = Path(temporary)
        metadata = _execute_experiment(
            mode=mode,
            output_dir=staging_dir,
            bootstrap_resamples=bootstrap_resamples,
            include_capacity_sweep=include_capacity_sweep,
            include_no_decay_ablation=include_no_decay_ablation,
            include_policy_ablations=include_policy_ablations,
        )
        # Metadata is replaced last, so interruption cannot make an older
        # manifest endorse a partially published set of new CSV files.
        for name in ARTIFACT_NAMES:
            os.replace(str(staging_dir / name), str(output_dir / name))
    return metadata


def _execute_experiment(
    mode: str,
    output_dir: Path,
    bootstrap_resamples: int,
    include_capacity_sweep: bool,
    include_no_decay_ablation: bool,
    include_policy_ablations: bool,
) -> Dict[str, Any]:
    if mode not in ("smoke", "full"):
        raise ValueError("mode must be smoke or full")

    # Capture provenance before any potentially long replay begins. The
    # manifest can therefore identify the exact frozen inputs even if an
    # unrelated file is created in the checkout while the experiment runs.
    experiment_identity = _experiment_identity()
    environment = _environment_metadata()

    if mode == "full":
        validation_seeds = VALIDATION_SEEDS
        test_seeds = TEST_SEEDS
        validation_requests = FULL_VALIDATION_REQUESTS
        test_requests = FULL_TEST_REQUESTS
        validation_capacity = PRIMARY_CAPACITY
        test_capacity = PRIMARY_CAPACITY
        concepts_per_cell = 200
        diagnostic_seeds = DIAGNOSTIC_SEEDS
        diagnostic_requests = HARD_NEGATIVE_REQUESTS
    else:
        validation_seeds = VALIDATION_SEEDS[:1]
        test_seeds = TEST_SEEDS[:2]
        validation_requests = 120
        test_requests = 300
        validation_capacity = 50
        test_capacity = 50
        concepts_per_cell = 32
        diagnostic_seeds = DIAGNOSTIC_SEEDS[:1]
        diagnostic_requests = 100
        include_capacity_sweep = False
        include_no_decay_ablation = False
        include_policy_ablations = False

    catalog = CompactCatalog(concepts_per_cell=concepts_per_cell)
    catalog_geometry = _measure_catalog_geometry(catalog)
    protocol_preflight = _preflight_protocol(
        catalog, test_requests, test_capacity
    )
    request_path = output_dir / "requests.jsonl"
    with request_path.open("w", encoding="utf-8"):
        pass
    validation_rows, validation_run_rows, chosen, trace_hashes = _run_validation(
        catalog,
        validation_seeds,
        validation_requests,
        validation_capacity,
        request_path=request_path,
    )
    _write_csv(
        output_dir / "validation.csv",
        validation_rows,
        VALIDATION_FIELDS,
    )
    _write_csv(
        output_dir / "validation_runs.csv",
        validation_run_rows,
        VALIDATION_RUN_FIELDS,
    )
    no_decay_ablation_requested = include_no_decay_ablation
    no_decay_ablation_degenerate = bool(
        include_no_decay_ablation and math.isinf(chosen.demand_half_life)
    )
    no_decay_ablation_executed = bool(
        include_no_decay_ablation and not no_decay_ablation_degenerate
    )

    test_rows: List[Dict[str, Any]] = []
    primary_rows, primary_hashes = _run_fixed_stage(
        stage="primary_test",
        catalog=catalog,
        parameters=chosen,
        seeds=test_seeds,
        workloads=WORKLOADS,
        request_count=test_requests,
        capacity=test_capacity,
        policies=("LRU", "LFU", "CARMA", "CARMA_NO_CLUSTER"),
        request_path=request_path,
    )
    test_rows.extend(primary_rows)
    trace_hashes.update(primary_hashes)

    diagnostic_rows, diagnostic_hashes = _run_fixed_stage(
        stage="hard_negative_diagnostic",
        catalog=catalog,
        parameters=chosen,
        seeds=diagnostic_seeds,
        workloads=("novel",),
        request_count=diagnostic_requests,
        capacity=test_capacity,
        policies=("LRU", "LFU", "CARMA", "CARMA_NO_CLUSTER"),
        hit_threshold=HARD_NEGATIVE_HIT_THRESHOLD,
        request_path=request_path,
    )
    test_rows.extend(diagnostic_rows)
    trace_hashes.update(diagnostic_hashes)

    if no_decay_ablation_executed:
        no_decay = replace(chosen, demand_half_life=math.inf)
        ablation_rows, ablation_hashes = _run_fixed_stage(
            stage="primary_test",
            catalog=catalog,
            parameters=no_decay,
            seeds=test_seeds,
            workloads=WORKLOADS,
            request_count=test_requests,
            capacity=test_capacity,
            policies=("CARMA",),
            policy_labels={"CARMA": "CARMA_NO_DECAY"},
            request_path=request_path,
        )
        test_rows.extend(ablation_rows)
        trace_hashes.update(ablation_hashes)

    if include_policy_ablations:
        flag_ablations = (
            ("CARMA_NO_ADMISSION", {"admission_enabled": False}),
            ("CARMA_NO_QUOTA", {"quota_enabled": False}),
        )
        for label, overrides in flag_ablations:
            ablation_rows, ablation_hashes = _run_fixed_stage(
                stage="primary_test",
                catalog=catalog,
                parameters=chosen,
                seeds=test_seeds,
                workloads=WORKLOADS,
                request_count=test_requests,
                capacity=test_capacity,
                policies=("CARMA",),
                policy_labels={"CARMA": label},
                policy_overrides=overrides,
                request_path=request_path,
            )
            test_rows.extend(ablation_rows)
            trace_hashes.update(ablation_hashes)

    if include_capacity_sweep:
        for capacity in CAPACITY_SWEEP:
            sweep_rows, sweep_hashes = _run_fixed_stage(
                stage="capacity_sweep",
                catalog=catalog,
                parameters=chosen,
                seeds=test_seeds[:5],
                workloads=WORKLOADS,
                request_count=test_requests,
                capacity=capacity,
                policies=("LRU", "LFU", "CARMA"),
                trace_capacity=PRIMARY_CAPACITY,
                request_path=request_path,
            )
            test_rows.extend(sweep_rows)
            trace_hashes.update(sweep_hashes)

    test_rows.sort(
        key=lambda row: (
            row["stage"],
            row["capacity"],
            row["workload"],
            row["seed"],
            row["policy"],
        )
    )
    _write_csv(output_dir / "runs.csv", test_rows, TEST_RUN_FIELDS)

    aggregate_rows = _aggregate_runs(test_rows, bootstrap_resamples)
    _write_csv(output_dir / "aggregate.csv", aggregate_rows, AGGREGATE_FIELDS)
    synthetic_success_gates = _evaluate_synthetic_success_gates(
        aggregate_rows,
        capacity=test_capacity,
        inferential=mode == "full",
    )

    metadata = {
        "schema_version": EXPERIMENT_SCHEMA,
        "baseline_commit": BASELINE_COMMIT,
        "experiment_identity": experiment_identity,
        "environment": environment,
        "mode": mode,
        "inferential_test_run": mode == "full",
        "workloads": list(WORKLOADS),
        "metrics": list(METRICS),
        "catalog": {
            "implementation": "CompactCatalog",
            "topics": catalog.topics,
            "cells_per_topic": catalog.cells_per_topic,
            "concepts_per_cell": catalog.concepts_per_cell,
            "concept_count": len(catalog.concepts),
            "embedding_dimension": int(catalog.concepts[0].embedding.size),
            "feature_weights": {
                "topic": TOPIC_WEIGHT,
                "cell": CELL_WEIGHT,
                "near_group": NEAR_GROUP_WEIGHT,
                "concept": CONCEPT_WEIGHT,
            },
            "construction": (
                "Orthogonal cell-local feature blocks are reused across "
                "topics; within-topic hierarchy cosines remain exact."
            ),
            "trace_builders": "benchmarks.carma.synthetic.build_trace",
            "pre_test_scalability_amendment": {
                "status": "frozen before the full inferential test",
                "rationale": (
                    "The global one-hot catalog grows to more than 8,000 "
                    "dimensions for a 10k pollution trace. Compact blocks "
                    "make the CPU-only full protocol tractable."
                ),
                "comparability": (
                    "Within-topic 0.72/0.88/0.96 geometry is preserved. "
                    "Different topics can share at most 0.12, below the "
                    "smallest clustering threshold of 0.70."
                ),
            },
            "measured_cosine_ranges": catalog_geometry,
        },
        "protocol_preflight": protocol_preflight,
        "validation_grid": {
            "topic_threshold": list(TOPIC_THRESHOLDS),
            "cell_threshold": list(CELL_THRESHOLDS),
            "demand_half_life": _json_safe(list(HALF_LIVES)),
            "quota_strength": list(QUOTA_STRENGTHS),
            "configuration_count": len(parameter_grid()),
            "amendment": (
                "Cell thresholds 0.95 and 0.97 were fixed before test as a "
                "diagnostic response to CI cell over-merging."
            ),
        },
        "pre_test_protocol_amendments": {
            "status": "frozen before any full inferential test seed was run",
            "validation_requests": {
                "previous": 5000,
                "amended": FULL_VALIDATION_REQUESTS,
                "rationale": (
                    "At 96 configurations, three seeds, and three workloads, "
                    "2,000 requests produce 1,728,000 validation replays; "
                    "5,000 would produce 4,320,000. The amendment reduces "
                    "CPU replay work by 60% while retaining all three seeds."
                ),
            },
            "validation_grid": {
                "previous_configuration_count": 48,
                "amended_configuration_count": len(parameter_grid()),
                "previous_cell_threshold": 0.88,
                "amended_cell_thresholds": list(CELL_THRESHOLDS),
                "rationale": (
                    "The 0.95/0.97 diagnostic grid was declared after CI "
                    "exposed cell over-merging and before test traces opened."
                ),
            },
        },
        "selection_rule": (
            "Maximize mean per-trace VHR divided by the best validation VHR "
            "for that workload/seed, subject to total false_hits == 0; ties "
            "use ascending topic_threshold, cell_threshold, half-life rank, "
            "and quota_strength."
        ),
        "selection_uses_test_results": False,
        "request_log": {
            "included": True,
            "path": "requests.jsonl",
            "scope": "validation and every emitted fixed-stage run",
            "write_mode": "streamed after each completed synthetic request",
            "resource_samples_included": False,
            "timing_measured": False,
            "stage_latency_included": False,
        },
        "policy_execution_order": {
            "method": (
                "deterministic per-seed SHA-256 ranking of policy names "
                "with domain separator carma-policy-order-v1"
            ),
            "scope": "fixed synthetic stages; validation has one policy",
            "timing_measured": False,
        },
        "validation_seeds": list(validation_seeds),
        "test_seeds": list(test_seeds),
        "seeds_are_disjoint": not bool(set(validation_seeds) & set(test_seeds)),
        "validation_requests": validation_requests,
        "test_requests": test_requests,
        "validation_capacity": validation_capacity,
        "test_capacity": test_capacity,
        "hit_threshold": HIT_THRESHOLD,
        "hit_threshold_role": (
            "Confirmatory synthetic retrieval threshold. Its zero-FHR check "
            "is an implementation invariant, not the substantive semantic-"
            "safety claim."
        ),
        "hard_negative_diagnostic": {
            "selection_input": False,
            "inferential_role": "exploratory",
            "threshold": HARD_NEGATIVE_HIT_THRESHOLD,
            "seeds": list(diagnostic_seeds),
            "requests_per_seed": diagnostic_requests,
            "workload": "novel",
            "policies": ["LRU", "LFU", "CARMA", "CARMA_NO_CLUSTER"],
            "rationale": (
                "Distinct-answer twins at cosine 0.96 must exercise false-hit "
                "accounting on one identical paired trace per seed."
            ),
        },
        "ghost_support_threshold": GHOST_SUPPORT_THRESHOLD,
        "selected_config": _json_safe(asdict(chosen)),
        "selected_config_id": chosen.config_id,
        "capacity_sweep_included": include_capacity_sweep,
        "capacity_sweep_capacities": (
            list(CAPACITY_SWEEP) if include_capacity_sweep else []
        ),
        "capacity_sweep_seeds": (
            list(test_seeds[:5]) if include_capacity_sweep else []
        ),
        "capacity_sweep_design": (
            {
                "status": "exploratory",
                "trace_capacity": PRIMARY_CAPACITY,
                "trace_fixed_across_cache_capacities": True,
                "note": (
                    "Five paired seeds are reused at every capacity; the "
                    "pollution working set remains fixed at 0.8 * 100."
                ),
            }
            if include_capacity_sweep
            else None
        ),
        "no_decay_ablation_requested": no_decay_ablation_requested,
        "no_decay_ablation_included": no_decay_ablation_executed,
        "no_decay_ablation_executed": no_decay_ablation_executed,
        "no_decay_ablation_degenerate": no_decay_ablation_degenerate,
        "no_decay_ablation_note": (
            "Skipped because the selected configuration already has infinite "
            "half-life."
            if no_decay_ablation_degenerate
            else "CARMA_NO_DECAY fixes demand_half_life to infinity."
        ),
        "policy_flag_ablations_included": include_policy_ablations,
        "policy_flag_ablations": (
            {
                "CARMA_NO_ADMISSION": {"admission_enabled": False},
                "CARMA_NO_QUOTA": {"quota_enabled": False},
            }
            if include_policy_ablations
            else {}
        ),
        "semantic_safety_gate": (
            "Held-out QQP precision at its independently calibrated threshold; "
            "the synthetic 0.95 diagnostic is not used for model selection."
        ),
        "synthetic_success_gates": synthetic_success_gates,
        "bootstrap_resamples": bootstrap_resamples,
        "statistics": {
            "sampling_unit": "independent seed",
            "pairing_keys": ["workload", "capacity", "seed"],
            "confidence_level": 0.95,
            "confidence_interval": "deterministic percentile bootstrap",
            "paired_test": "exact two-sided Wilcoxon signed-rank",
            "effect_size": (
                "matched-pairs rank-biserial; positive means CARMA is higher "
                "and is beneficial except for false-hit rate and recovery lag"
            ),
            "multiple_testing": (
                "Holm adjustment over whole-trace primary VHR and pollution "
                "return-phase VHR comparisons against LRU, LFU, and the "
                "per-seed best baseline"
            ),
            "confirmatory_scope": (
                "Only primary-test whole-trace VHR and pollution return-phase "
                "VHR comparisons against LRU, LFU, and the per-seed best "
                "baseline are confirmatory."
            ),
            "exploratory_scope": (
                "All other comparison p-values are unadjusted exploratory "
                "diagnostics."
            ),
        },
        "trace_hashes": dict(sorted(trace_hashes.items())),
        "artifacts": {
            "validation.csv": _file_hash(output_dir / "validation.csv"),
            "validation_runs.csv": _file_hash(
                output_dir / "validation_runs.csv"
            ),
            "runs.csv": _file_hash(output_dir / "runs.csv"),
            "aggregate.csv": _file_hash(output_dir / "aggregate.csv"),
            "requests.jsonl": _file_hash(request_path),
        },
    }
    with (output_dir / "metadata.json").open("w", encoding="utf-8") as output:
        json.dump(metadata, output, sort_keys=True, indent=2, allow_nan=False)
        output.write("\n")
    return metadata


def _run_validation(
    catalog: CompactCatalog,
    seeds: Sequence[int],
    request_count: int,
    capacity: int,
    request_path: Optional[Path] = None,
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Parameters,
    Dict[str, str],
]:
    grids = parameter_grid()
    traces: Dict[Tuple[str, int], Tuple[Sequence[Any], str]] = {}
    trace_hashes: Dict[str, str] = {}
    for seed in seeds:
        for workload in WORKLOADS:
            requests = build_trace(workload, catalog, request_count, seed, capacity)
            digest = trace_hash(requests)
            traces[(workload, seed)] = (requests, digest)
            trace_hashes["validation/%s/%s" % (workload, seed)] = digest

    run_metrics: Dict[str, List[Dict[str, Any]]] = {
        parameters.config_id: [] for parameters in grids
    }
    trace_best: Dict[Tuple[str, int], float] = {
        key: 0.0 for key in traces
    }
    for parameters in grids:
        for (workload, seed), (requests, digest) in traces.items():
            config = _benchmark_config(parameters, seed, capacity, request_count)
            simulation = CacheSimulation(
                "CARMA", config, workload, requests, digest
            )
            result = _execute_simulation(
                simulation,
                request_path,
                _request_record_context("validation", parameters),
            )
            summary = result.summary
            run_metrics[parameters.config_id].append(summary)
            trace_best[(workload, seed)] = max(
                trace_best[(workload, seed)], summary["valid_hit_rate"]
            )

    rows = []
    for parameters in grids:
        summaries = run_metrics[parameters.config_id]
        normalized = []
        for summary in summaries:
            denominator = trace_best[(summary["workload"], summary["seed"])]
            normalized.append(
                summary["valid_hit_rate"] / denominator if denominator else 0.0
            )
        false_hits = sum(int(summary["false_hits"]) for summary in summaries)
        row = {
            "config_id": parameters.config_id,
            "topic_threshold": parameters.topic_threshold,
            "cell_threshold": parameters.cell_threshold,
            "demand_half_life": _half_life_label(parameters.demand_half_life),
            "quota_strength": parameters.quota_strength,
            "run_count": len(summaries),
            "mean_valid_hit_rate": _mean(
                [summary["valid_hit_rate"] for summary in summaries]
            ),
            "normalized_mean_valid_hit_rate": _mean(normalized),
            "mean_false_hit_rate": _mean(
                [summary["false_hit_rate"] for summary in summaries]
            ),
            "total_false_hits": false_hits,
            "mean_opportunity_recall": _mean(
                [summary["opportunity_recall"] for summary in summaries]
            ),
            "mean_safe_token_saving_ratio": _mean(
                [summary["safe_token_saving_ratio"] for summary in summaries]
            ),
            "passes_zero_false_hits": false_hits == 0,
            "selected": False,
        }
        rows.append(row)

    candidates = [row for row in rows if row["passes_zero_false_hits"]]
    if not candidates:
        raise RuntimeError("no validation configuration achieved zero false hits")
    chosen_row = min(
        candidates,
        key=lambda row: (
            -row["normalized_mean_valid_hit_rate"],
            row["topic_threshold"],
            row["cell_threshold"],
            _half_life_sort(row["demand_half_life"]),
            row["quota_strength"],
        ),
    )
    chosen_row["selected"] = True
    chosen = next(item for item in grids if item.config_id == chosen_row["config_id"])
    rows.sort(
        key=lambda row: (
            -row["normalized_mean_valid_hit_rate"],
            row["topic_threshold"],
            row["cell_threshold"],
            _half_life_sort(row["demand_half_life"]),
            row["quota_strength"],
        )
    )
    selection_by_config = {row["config_id"]: row for row in rows}
    run_rows: List[Dict[str, Any]] = []
    for parameters in grids:
        selection = selection_by_config[parameters.config_id]
        for summary in run_metrics[parameters.config_id]:
            denominator = trace_best[(summary["workload"], summary["seed"])]
            detail = {
                "config_id": parameters.config_id,
                "topic_threshold": parameters.topic_threshold,
                "cell_threshold": parameters.cell_threshold,
                "demand_half_life": _half_life_label(
                    parameters.demand_half_life
                ),
                "quota_strength": parameters.quota_strength,
                "normalized_valid_hit_rate": _round(
                    summary["valid_hit_rate"] / denominator
                    if denominator
                    else 0.0
                ),
                "config_total_false_hits": selection["total_false_hits"],
                "config_passes_zero_false_hits": selection[
                    "passes_zero_false_hits"
                ],
                "config_selected": selection["selected"],
            }
            detail.update(summary)
            run_rows.append(detail)
    run_rows.sort(
        key=lambda row: (row["config_id"], row["workload"], row["seed"])
    )
    return rows, run_rows, chosen, trace_hashes


def _run_fixed_stage(
    stage: str,
    catalog: CompactCatalog,
    parameters: Parameters,
    seeds: Sequence[int],
    workloads: Sequence[str],
    request_count: int,
    capacity: int,
    policies: Sequence[str],
    policy_labels: Optional[Dict[str, str]] = None,
    trace_capacity: Optional[int] = None,
    hit_threshold: float = HIT_THRESHOLD,
    policy_overrides: Optional[Dict[str, Any]] = None,
    request_path: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    labels = policy_labels or {}
    rows: List[Dict[str, Any]] = []
    hashes: Dict[str, str] = {}
    for seed in seeds:
        for workload in workloads:
            requests = build_trace(
                workload,
                catalog,
                request_count,
                seed,
                capacity if trace_capacity is None else trace_capacity,
            )
            digest = trace_hash(requests)
            hashes["%s/c%s/%s/%s" % (stage, capacity, workload, seed)] = digest
            config = _benchmark_config(
                parameters,
                seed,
                capacity,
                request_count,
                hit_threshold=hit_threshold,
            )
            for policy in _policy_order(policies, seed):
                label = labels.get(policy, policy)
                simulation = CacheSimulation(
                    policy, config, workload, requests, digest
                )
                if policy_overrides:
                    if policy != "CARMA":
                        raise ValueError("policy overrides require base CARMA")
                    for attribute, value in sorted(policy_overrides.items()):
                        if not hasattr(simulation.policy.impl, attribute):
                            raise RuntimeError(
                                "CARMA does not expose ablation flag %s" % attribute
                            )
                        setattr(simulation.policy.impl, attribute, value)
                    simulation.policy_name = label
                    simulation.policy.name = label
                    simulation.run_id = _variant_run_id(
                        simulation.run_id, label, policy_overrides
                    )
                result = _execute_simulation(
                    simulation,
                    request_path,
                    _request_record_context(stage, parameters),
                )
                summary = dict(result.summary)
                summary["stage"] = stage
                summary["config_id"] = parameters.config_id
                summary["topic_threshold"] = parameters.topic_threshold
                summary["cell_threshold"] = parameters.cell_threshold
                summary["demand_half_life"] = _half_life_label(
                    parameters.demand_half_life
                )
                summary["quota_strength"] = parameters.quota_strength
                summary["policy"] = label
                rows.append(summary)
    return rows, hashes


def _policy_order(policies: Sequence[str], seed: int) -> Tuple[str, ...]:
    """Return a stable pseudo-random policy permutation for one seed.

    Hash ranking is independent of the caller's input order, which makes the
    permutation reproducible across Python versions and repeated workloads.
    Policy simulations remain state-isolated; only their execution order is
    changed.
    """

    if len(set(policies)) != len(policies):
        raise ValueError("policy execution order requires unique names")

    def rank(policy: str) -> Tuple[bytes, str]:
        encoded = ("carma-policy-order-v1|%s|%s" % (seed, policy)).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).digest(), policy

    return tuple(sorted(policies, key=rank))


def _request_record_context(
    stage: str, parameters: Parameters
) -> Dict[str, Any]:
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA,
        "stage": stage,
        "config_id": parameters.config_id,
        "topic_threshold": parameters.topic_threshold,
        "cell_threshold": parameters.cell_threshold,
        "demand_half_life": _half_life_label(parameters.demand_half_life),
        "quota_strength": parameters.quota_strength,
    }


def _execute_simulation(
    simulation: CacheSimulation,
    request_path: Optional[Path],
    context: Dict[str, Any],
) -> RunResult:
    """Execute one isolated policy run and optionally append request JSONL."""

    if request_path is None:
        return simulation.execute()

    with request_path.open("a", encoding="utf-8") as output:

        def stream(record: Dict[str, Any]) -> None:
            row = dict(context)
            row.update(record)
            output.write(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )

        return simulation.execute(record_sink=stream)


def _benchmark_config(
    parameters: Parameters,
    seed: int,
    capacity: int,
    request_count: int,
    hit_threshold: float = HIT_THRESHOLD,
) -> BenchmarkConfig:
    return BenchmarkConfig(
        workloads=WORKLOADS,
        policies=("CARMA",),
        seed=seed,
        capacity=capacity,
        request_count=request_count,
        hit_threshold=hit_threshold,
        cluster_similarity_threshold=parameters.topic_threshold,
        cell_threshold=parameters.cell_threshold,
        demand_half_life=parameters.demand_half_life,
        quota_strength=parameters.quota_strength,
        ghost_support_threshold=GHOST_SUPPORT_THRESHOLD,
        measure_latency=False,
        verify_determinism=False,
    )


def _aggregate_runs(
    rows: Sequence[Dict[str, Any]], bootstrap_resamples: int
) -> List[Dict[str, Any]]:
    aggregate: List[Dict[str, Any]] = []
    groups: Dict[Tuple[str, str, int, str], List[Dict[str, Any]]] = {}
    for row in rows:
        key = (row["stage"], row["workload"], int(row["capacity"]), row["policy"])
        groups.setdefault(key, []).append(row)

    for (stage, workload, capacity, policy), group in sorted(groups.items()):
        for metric in _metrics_for_workload(workload):
            values = [
                float(row[metric])
                for row in sorted(group, key=lambda item: item["seed"])
            ]
            interval = bootstrap_mean_ci(
                values,
                resamples=bootstrap_resamples,
                seed=_statistics_seed(stage, workload, capacity, policy, metric),
            )
            aggregate.append(
                _aggregate_row(
                    stage=stage,
                    row_type="summary",
                    workload=workload,
                    capacity=capacity,
                    metric=metric,
                    policy=policy,
                    comparator="",
                    n=len(values),
                    mean=interval.estimate,
                    ci_low=interval.low,
                    ci_high=interval.high,
                )
            )

    comparison_indices = []
    comparison_p_values = []
    comparison_keys = sorted(
        {
            (row["stage"], row["workload"], int(row["capacity"]))
            for row in rows
            if row["policy"] == "CARMA"
        }
    )
    for stage, workload, capacity in comparison_keys:
        stage_rows = [
            row
            for row in rows
            if row["stage"] == stage
            and row["workload"] == workload
            and int(row["capacity"]) == capacity
        ]
        by_policy = _values_by_seed(stage_rows)
        if "CARMA" not in by_policy:
            continue
        comparators = [
            name
            for name in (
                "LRU",
                "LFU",
                "CARMA_NO_CLUSTER",
                "CARMA_NO_DECAY",
                "CARMA_NO_ADMISSION",
                "CARMA_NO_QUOTA",
            )
            if name in by_policy
        ]
        if "LRU" in by_policy and "LFU" in by_policy:
            comparators.append("BEST_BASELINE")
        for metric in _metrics_for_workload(workload):
            for comparator in comparators:
                if comparator == "BEST_BASELINE":
                    common = sorted(
                        set(by_policy["CARMA"])
                        & set(by_policy["LRU"])
                        & set(by_policy["LFU"])
                    )
                    carma_values = [
                        by_policy["CARMA"][seed][metric] for seed in common
                    ]
                    right = []
                    for seed in common:
                        lru = by_policy["LRU"][seed][metric]
                        lfu = by_policy["LFU"][seed][metric]
                        right.append(
                            min(lru, lfu)
                            if metric in LOWER_IS_BETTER
                            else max(lru, lfu)
                        )
                else:
                    common = sorted(
                        set(by_policy["CARMA"]) & set(by_policy[comparator])
                    )
                    carma_values = [
                        by_policy["CARMA"][seed][metric] for seed in common
                    ]
                    right = [
                        by_policy[comparator][seed][metric] for seed in common
                    ]
                interval = paired_bootstrap_ci(
                    carma_values,
                    right,
                    resamples=bootstrap_resamples,
                    seed=_statistics_seed(
                        "delta", stage, workload, capacity, comparator, metric
                    ),
                )
                wilcoxon = wilcoxon_signed_rank(carma_values, right)
                aggregate.append(
                    _aggregate_row(
                        stage=stage,
                        row_type="comparison",
                        workload=workload,
                        capacity=capacity,
                        metric=metric,
                        policy="CARMA",
                        comparator=comparator,
                        n=len(carma_values),
                        delta_mean=interval.estimate,
                        delta_ci_low=interval.low,
                        delta_ci_high=interval.high,
                        wilcoxon_statistic=wilcoxon.statistic,
                        p_value=wilcoxon.p_value,
                        nonzero_pairs=wilcoxon.nonzero_pairs,
                        rank_biserial=wilcoxon.rank_biserial,
                    )
                )
                if (
                    stage == "primary_test"
                    and (
                        metric == "valid_hit_rate"
                        or (
                            workload == "pollution_scan"
                            and metric == "scan_return_valid_hit_rate"
                        )
                    )
                    and comparator in (
                        "LRU",
                        "LFU",
                        "BEST_BASELINE",
                    )
                ):
                    comparison_indices.append(len(aggregate) - 1)
                    comparison_p_values.append(wilcoxon.p_value)

    adjusted = holm_adjust(comparison_p_values)
    for index, p_value in zip(comparison_indices, adjusted):
        aggregate[index]["p_holm"] = _round(p_value)
        aggregate[index]["test_family"] = "primary_vhr"
    for row in aggregate:
        if row["row_type"] == "comparison" and not row["test_family"]:
            row["test_family"] = "exploratory_unadjusted"
    aggregate.sort(
        key=lambda row: (
            row["stage"],
            row["row_type"],
            row["capacity"],
            row["workload"],
            row["metric"],
            row["policy"],
            row["comparator"],
        )
    )
    return aggregate


def _evaluate_synthetic_success_gates(
    aggregate: Sequence[Dict[str, Any]],
    capacity: int = PRIMARY_CAPACITY,
    inferential: bool = True,
) -> Dict[str, Any]:
    """Adjudicate the preregistered synthetic gates without report judgment."""

    def comparison(workload: str, metric: str) -> Dict[str, Any]:
        matches = [
            row
            for row in aggregate
            if row["stage"] == "primary_test"
            and row["row_type"] == "comparison"
            and row["workload"] == workload
            and int(row["capacity"]) == capacity
            and row["metric"] == metric
            and row["policy"] == "CARMA"
            and row["comparator"] == "BEST_BASELINE"
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "expected one primary BEST_BASELINE comparison for %s/%s"
                % (workload, metric)
            )
        return matches[0]

    shift = comparison("phase_shift", "valid_hit_rate")
    scan_return = comparison(
        "pollution_scan", "scan_return_valid_hit_rate"
    )
    stationary = comparison("stationary", "valid_hit_rate")
    token_rows = [
        comparison(workload, "safe_token_saving_ratio")
        for workload in WORKLOADS
    ]

    gate_3_passes = (
        float(shift["delta_mean"]) >= 0.02
        and float(shift["delta_ci_low"]) > 0
        and float(shift["p_holm"]) <= 0.05
    )
    gate_4_passes = (
        float(scan_return["delta_mean"]) >= 0.05
        and float(scan_return["delta_ci_low"]) > 0
        and float(scan_return["p_holm"]) <= 0.05
    )
    gate_5_passes = float(stationary["delta_ci_low"]) >= -0.01
    gate_6_passes = all(
        float(row["delta_ci_low"]) >= -0.005 for row in token_rows
    )
    return {
        "status": "inferential" if inferential else "smoke_non_inferential",
        "evaluated_capacity": capacity,
        "claimable": inferential,
        "scope": (
            "Synthetic gates 3-6 only; correctness, QQP safety, system "
            "overhead, and Docker reproducibility are adjudicated elsewhere."
        ),
        "gate_3_phase_shift_vhr": {
            "required_delta": 0.02,
            "required_ci_low_strictly_above": 0.0,
            "required_holm_p_at_most": 0.05,
            "observed_delta": shift["delta_mean"],
            "observed_ci_low": shift["delta_ci_low"],
            "observed_ci_high": shift["delta_ci_high"],
            "observed_holm_p": shift["p_holm"],
            "passes": gate_3_passes,
        },
        "gate_4_scan_return_vhr": {
            "required_delta": 0.05,
            "required_ci_low_strictly_above": 0.0,
            "required_holm_p_at_most": 0.05,
            "observed_delta": scan_return["delta_mean"],
            "observed_ci_low": scan_return["delta_ci_low"],
            "observed_ci_high": scan_return["delta_ci_high"],
            "observed_holm_p": scan_return["p_holm"],
            "passes": gate_4_passes,
        },
        "gate_5_stationary_nonregression": {
            "required_ci_low_at_least": -0.01,
            "observed_delta": stationary["delta_mean"],
            "observed_ci_low": stationary["delta_ci_low"],
            "observed_ci_high": stationary["delta_ci_high"],
            "passes": gate_5_passes,
        },
        "gate_6_token_saving_nonregression": {
            "required_each_workload_ci_low_at_least": -0.005,
            "workloads": {
                row["workload"]: {
                    "observed_delta": row["delta_mean"],
                    "observed_ci_low": row["delta_ci_low"],
                    "observed_ci_high": row["delta_ci_high"],
                }
                for row in token_rows
            },
            "passes": gate_6_passes,
        },
    }


def _values_by_seed(
    rows: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[int, Dict[str, float]]]:
    result: Dict[str, Dict[int, Dict[str, float]]] = {}
    for row in rows:
        policy = row["policy"]
        seed = int(row["seed"])
        if seed in result.setdefault(policy, {}):
            raise RuntimeError("duplicate policy/workload/capacity/seed result")
        result[policy][seed] = {
            metric: float(row[metric])
            for metric in _metrics_for_workload(str(row["workload"]))
        }
    return result


def _metrics_for_workload(workload: str) -> Tuple[str, ...]:
    if workload == "pollution_scan":
        return BASE_METRICS + POLLUTION_PHASE_METRICS
    if workload == "phase_shift":
        return BASE_METRICS + SHIFT_PHASE_METRICS
    return BASE_METRICS


def _aggregate_row(**kwargs: Any) -> Dict[str, Any]:
    row = {field: "" for field in AGGREGATE_FIELDS}
    row.update(kwargs)
    for field in (
        "mean",
        "ci_low",
        "ci_high",
        "delta_mean",
        "delta_ci_low",
        "delta_ci_high",
        "wilcoxon_statistic",
        "p_value",
        "p_holm",
        "rank_biserial",
    ):
        if row[field] != "":
            row[field] = _round(float(row[field]))
    return row


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

TEST_RUN_FIELDS = (
    "stage",
    "config_id",
    "topic_threshold",
    "cell_threshold",
    "demand_half_life",
    "quota_strength",
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


def _write_csv(
    path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _cosine(left: Concept, right: Concept) -> float:
    return float(np.dot(left.embedding, right.embedding))


def _cosine_range(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        raise ValueError("cosine range requires at least one value")
    return {"min": _round(min(values)), "max": _round(max(values))}


def _half_life_label(value: float) -> Any:
    return "infinity" if math.isinf(value) else int(value)


def _half_life_sort(value: Any) -> float:
    return math.inf if value == "infinity" else float(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "infinity" if value > 0 else "-infinity"
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _mean(values: Sequence[float]) -> float:
    return _round(sum(values) / len(values)) if values else 0.0


def _round(value: float) -> float:
    return round(float(value), 10)


def _statistics_seed(*parts: Any) -> int:
    encoded = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")


def _variant_run_id(
    base_run_id: str, label: str, overrides: Dict[str, Any]
) -> str:
    encoded = json.dumps(
        {
            "base_run_id": base_run_id,
            "label": label,
            "overrides": _json_safe(overrides),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _experiment_identity() -> Dict[str, Any]:
    if not EXPERIMENT_CONTRACT.is_file():
        raise RuntimeError("experiment contract is missing")
    source_paths = (
        Path(__file__).resolve(),
        PROJECT_ROOT / "benchmarks/carma/runner.py",
        PROJECT_ROOT / "benchmarks/carma/statistics.py",
        PROJECT_ROOT / "benchmarks/carma/synthetic.py",
        PROJECT_ROOT / "gptcache/manager/eviction/carma.py",
    )
    sources = {
        str(path.relative_to(PROJECT_ROOT)): _file_hash(path)
        for path in source_paths
    }
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            text=True,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
            cwd=str(PROJECT_ROOT),
            text=True,
        ).splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "full experiment requires an identifiable Git checkout"
        ) from exc
    return {
        "git_head_commit": head,
        "git_worktree_clean_at_start": not status,
        "git_status_at_start": status,
        "experiment_contract_path": str(
            EXPERIMENT_CONTRACT.relative_to(PROJECT_ROOT)
        ),
        "experiment_contract_sha256": _file_hash(EXPERIMENT_CONTRACT),
        "source_sha256": sources,
    }


def _environment_metadata() -> Dict[str, Any]:
    packages = {}
    for name in ("cachetools", "numpy", "pytest", "scipy"):
        try:
            packages[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
        "packages": packages,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run validation-selected CARMA experiments and paired statistics."
    )
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/carma/full-results"),
    )
    parser.add_argument("--bootstrap-resamples", type=int, default=None)
    parser.add_argument("--skip-capacity-sweep", action="store_true")
    parser.add_argument("--skip-no-decay-ablation", action="store_true")
    parser.add_argument("--skip-policy-ablations", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    resamples = args.bootstrap_resamples
    if resamples is None:
        resamples = 1000 if args.mode == "smoke" else 10000
    metadata = run_experiment(
        mode=args.mode,
        output_dir=args.output,
        bootstrap_resamples=resamples,
        include_capacity_sweep=(
            args.mode == "full" and not args.skip_capacity_sweep
        ),
        include_no_decay_ablation=(
            args.mode == "full" and not args.skip_no_decay_ablation
        ),
        include_policy_ablations=(
            args.mode == "full" and not args.skip_policy_ablations
        ),
    )
    print("mode=%s output=%s" % (args.mode, args.output))
    print(
        "selected_config_id=%s selected_config=%s"
        % (metadata["selected_config_id"], metadata["selected_config"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
