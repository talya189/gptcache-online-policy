# Draft upstream pull request

> Draft only. This text has not been published and no remote branch or pull
> request has been created.

## Title

`feat(eviction): add online cluster-adaptive CARMA policy`

## Summary

This change adds CARMA (Cluster-Adaptive Reuse and Miss-pressure Admission), an
opt-in, count-bounded eviction policy for GPTCache's scalar/vector data
manager. CARMA learns coarse semantic topics and fine-grained redundancy cells
online, decays old demand, apportions topic quotas, and applies a ghost-backed
admission rule before replacing a resident.

The existing LRU default and existing LRU/LFU/FIFO/random APIs are unchanged.
The first supported integration path is SQLite plus FAISS. CARMA does not call
an LLM, train a model, use token cost as a policy feature, or alter GPTCache's
similarity threshold.

## Motivation

GPTCache's current in-memory policies receive opaque row IDs and make one
global recency/frequency decision. They cannot distinguish:

- several redundant residents in one semantic cell;
- a repeated miss that deserves admission from a one-shot scan item; or
- a newly hot topic from stale lifetime frequency.

CARMA makes those signals available to eviction while keeping policy state
bounded by cache capacity.

## What changed

- Added `ClusterAdaptiveEviction` and factory policy name `CARMA`.
- Added embedding/metadata-aware insertion hooks while preserving existing
  eviction callers.
- Bound CARMA's eviction callback inside `SSDataManager` so rejected candidates
  and replaced victims are physically removed from scalar and vector storage.
- Added vector-first cleanup, targeted tombstone recovery, rollback, and an
  explicit unhealthy/fail-stop state after unrecoverable cleanup failure.
- Added read-only scalar metadata peeks for restart reconstruction without
  mutating access timestamps.
- Added deterministic clustering, quota, donor, victim, ghost-pruning, and
  arbitrary-ID tie rules.
- Added unit, property, failure-path, close/reopen, and SQLite/FAISS integration
  tests.
- Added pinned CI, Docker, synthetic, system, QQP, MOSS, and deterministic
  analysis tooling under project-specific paths.

## Configuration example

```python
manager = manager_factory(
    "sqlite,faiss",
    data_dir="./cache-data",
    vector_params={"dimension": 768, "top_k": 1},
    eviction_params={
        "eviction": "CARMA",
        "max_size": 100,
        "clean_size": 1,
        "policy_params": {
            "topic_threshold": 0.70,
            "cell_threshold": 0.97,
            "demand_half_life": 500,
            "quota_strength": 1.0,
            "ghost_support_threshold": 1.5,
            "admission_margin": 1.05,
        },
    },
)
```

## Correctness evidence

- Capacity, quota sums, deterministic ties, malformed metadata, duplicate and
  unknown IDs, and randomized event sequences are covered by policy tests.
- Rejection and replacement remove SQLite and FAISS rows immediately.
- Callback exceptions, re-admission within a batch, vector deletion failure,
  pre-existing tombstones, and close/reopen restore have focused regressions.
- Existing relevant GPTCache eviction and SQLite/FAISS tests remain in the
  single verification entrypoint.
- A Python 3.8 compatibility slice protects the repository's older supported
  syntax while Python 3.12.13 remains the pinned project runtime.

Exact commands are recorded in `docs/project/completion-audit.md`; final counts
and logs are retained under
`artifacts/samples/verification/publication-20260826-ci-refresh/`. The
exact-source host verifier passed 8 upstream eviction tests, 2 SQLite/FAISS
tests (1 optional deselected), and 179 project tests (2 skipped).

## Benchmark summary

The frozen synthetic experiment evaluated 96 CARMA configurations, selected on
disjoint validation seeds, then used ten paired 10,000-request test seeds at
capacity 100.

- Category shift: +4.885 valid-hit-rate percentage points versus the per-seed
  stronger LRU/LFU baseline; 95% paired CI [4.677, 5.088], Holm-adjusted
  `p=0.0234`.
- Stationary: +7.122 points; non-regression gate passes.
- Safe token-saving lower confidence bounds pass in all three workloads.
- Pollution return: CARMA reached 100%, but LFU reached 99.993%; the
  preregistered +5-point gate fails with only +0.0067 points. This negative
  result is intentionally retained.

Five fresh SQLite/FAISS seeds produced zero false hits and stale candidates.
Using precomputed synthetic vectors, worst-seed CARMA/LRU ratios were 1.032 for
post-embedding cache-path p95 latency, 0.997 for throughput, and 1.043 for
sampled peak RSS (+4.8 MiB). Policy-only p95 added roughly 354 microseconds.
These values are diagnostic rather than a formal Gate 7 pass: the frozen
contract named an ONNX embedding path and did not freeze an across-seed
aggregation rule.

## Compatibility and scope

- CARMA is opt-in; no default changes.
- Current full storage support is SQLite plus FAISS.
- Restore reconstructs residents/topics/cells but intentionally relearns
  demand and hit mass from zero.
- Miss assignment and victim selection add CPU work; complexity and bounds are
  documented in `docs/project/policy-design.md`.
- Full benchmark artifacts are tied to their recorded clean commits. Later
  protocol-remediation code does not retroactively change those outputs.

## Reviewer guide

1. Start with `gptcache/manager/eviction/carma.py` and
   `docs/project/policy-design.md`.
2. Review factory/callback wiring and cleanup recovery in the scalar/vector
   manager changes.
3. Run `PATH="$PWD/.venv/bin:$PATH" bash scripts/verify_project.sh`.
4. Inspect the focused failure-path tests before reviewing benchmark code.
5. Treat performance tooling as project evidence; it can be split from the
   minimal production-policy change if maintainers prefer a smaller upstream
   patch.

## Checklist

- [x] Backward-compatible default behavior.
- [x] No credentials or live LLM calls in tests/benchmarks.
- [x] Unit, property, integration, and failure-path coverage.
- [x] Pinned host/container verification evidence retained.
- [x] Deterministic benchmark schemas and artifact hashes.
- [x] Documented parameters, complexity, restart behavior, and limitations.
- [ ] Maintainer review of whether benchmark/report assets belong in the
  upstream PR or a separate evaluation repository.
