"""Structural checks for the full SQLite/FAISS timing workload."""

from benchmarks.carma.integration_benchmark import (
    IntegrationConfig,
    _precomputed_trace,
)


def test_full_integration_catalog_supports_the_frozen_40_percent_scan():
    config = IntegrationConfig(
        mode="full",
        workload="pollution_scan",
        seed=20260901,
        requests=3000,
        capacity=100,
        hit_threshold=0.97,
        sample_every=25,
        topic_threshold=0.70,
        cell_threshold=0.97,
        demand_half_life=500.0,
        quota_strength=1.0,
        ghost_support_threshold=1.5,
        admission_margin=1.05,
        centroid_alpha=0.05,
        entry_hit_weight=0.25,
    )

    catalog, requests, digest = _precomputed_trace(config)
    scanned = [row for row in requests if row.phase == "scan-unique"]

    assert len(requests) == 3000
    assert len(scanned) == 1200
    assert len({row.concept_id for row in scanned}) == 1200
    assert len([row for row in requests if row.phase == "scan-warm"]) == 900
    assert len([row for row in requests if row.phase == "scan-return"]) == 900
    assert len(catalog.concepts) >= 1200
    assert len(digest) == 64
