"""Focused prospective checks for the post-execution protocol remediations."""

import json

from benchmarks.carma.full_experiment import (
    CompactCatalog,
    Parameters,
    _benchmark_config,
    _policy_order,
    _run_fixed_stage,
)
from benchmarks.carma.runner import RUN_FIELDS, CacheSimulation
from benchmarks.carma.synthetic import build_trace, trace_hash


POLICIES = ("LRU", "LFU", "CARMA")
SEED = 20260902
REQUESTS = 48
CAPACITY = 12
PARAMETERS = Parameters(
    topic_threshold=0.70,
    cell_threshold=0.97,
    demand_half_life=500.0,
    quota_strength=1.0,
)


def test_policy_order_is_seeded_stable_and_input_order_independent():
    assert _policy_order(POLICIES, SEED) == ("LFU", "CARMA", "LRU")
    assert _policy_order(tuple(reversed(POLICIES)), SEED) == (
        "LFU",
        "CARMA",
        "LRU",
    )
    assert _policy_order(POLICIES, SEED + 1) != _policy_order(POLICIES, SEED)


def test_streamed_fixed_stage_smoke_is_deterministic_and_quality_neutral(
    tmp_path,
):
    catalog = CompactCatalog(concepts_per_cell=16)
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    first_path.write_text("", encoding="utf-8")
    second_path.write_text("", encoding="utf-8")

    plain_rows, plain_hashes = _run_fixed_stage(
        stage="primary_test",
        catalog=catalog,
        parameters=PARAMETERS,
        seeds=(SEED,),
        workloads=("stationary",),
        request_count=REQUESTS,
        capacity=CAPACITY,
        policies=POLICIES,
    )
    first_rows, first_hashes = _run_fixed_stage(
        stage="primary_test",
        catalog=catalog,
        parameters=PARAMETERS,
        seeds=(SEED,),
        workloads=("stationary",),
        request_count=REQUESTS,
        capacity=CAPACITY,
        policies=POLICIES,
        request_path=first_path,
    )
    second_rows, second_hashes = _run_fixed_stage(
        stage="primary_test",
        catalog=catalog,
        parameters=PARAMETERS,
        seeds=(SEED,),
        workloads=("stationary",),
        request_count=REQUESTS,
        capacity=CAPACITY,
        policies=tuple(reversed(POLICIES)),
        request_path=second_path,
    )

    assert first_hashes == second_hashes == plain_hashes
    assert first_rows == second_rows == plain_rows
    assert first_path.read_bytes() == second_path.read_bytes()

    records = [
        json.loads(line)
        for line in first_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == REQUESTS * len(POLICIES)
    for record in records:
        assert record["experiment_schema_version"] == "carma-full-experiment-v2"
        assert record["stage"] == "primary_test"
        assert record["config_id"] == PARAMETERS.config_id

    blocks = tuple(
        records[offset]["policy"]
        for offset in range(0, len(records), REQUESTS)
    )
    assert blocks == _policy_order(POLICIES, SEED)

    requests = build_trace("stationary", catalog, REQUESTS, SEED, CAPACITY)
    digest = trace_hash(requests)
    config = _benchmark_config(PARAMETERS, SEED, CAPACITY, REQUESTS)
    fixed_order_summaries = {
        policy: CacheSimulation(
            policy,
            config,
            "stationary",
            requests,
            digest,
        ).execute().summary
        for policy in POLICIES
    }
    for row in first_rows:
        observed = {field: row[field] for field in RUN_FIELDS}
        assert observed == fixed_order_summaries[row["policy"]]
