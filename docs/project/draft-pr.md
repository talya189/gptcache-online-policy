# Upstream pull request record

> Published as [zilliztech/GPTCache PR #696](https://github.com/zilliztech/GPTCache/pull/696)
> on 2026-08-27 and marked ready for review on 2026-08-28. It targets upstream
> `dev` from public fork branch `MatanGoldfarB:feat/carma-online-policy`. The PR
> is open, mergeable, and DCO-passing. It has **not** been reviewed, merged, or
> otherwise adopted; the lecturer's exceptional adopted-PR condition therefore
> remains unmet.

## Title

`feat(eviction): add online cluster-adaptive CARMA policy`

## Summary

This change adds CARMA (Cluster-Adaptive Reuse and Miss-pressure Admission), an
opt-in, count-bounded eviction policy for GPTCache's scalar/vector data
manager. CARMA learns coarse semantic topics and fine-grained redundancy cells
online, decays old demand, apportions topic quotas, and applies a ghost-backed
admission rule before replacing a resident.

The factory default remains LRU and the public policy interface is preserved
for valid existing inputs. Shared vector normalization, validation, and cleanup
are hardened. The first supported integration path is SQLite plus FAISS. CARMA
does not call an LLM, train a model, use token cost as a policy feature, or
alter GPTCache's similarity threshold.

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
- Added an upstream-native configuration example, parameter documentation,
  unit/failure-path tests, manager compatibility tests, and a SQLite/FAISS
  integration test. Course reports, corpora, and Gate artifacts are excluded
  from the public patch.

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

The public port passed 60 focused host tests, 36 policy tests on Linux/Python
3.8, and 11 selected upstream regression tests; one optional Chroma test was
deselected because that dependency was absent. Pylint 2.10.2 reports 10.00/10,
changed-line coverage is 96% (763 executable changed lines, 28 uncovered),
wheel/sdist builds pass, the Sphinx HTML build completes, and `git diff
--check` passes. Both commits have DCO `Signed-off-by` trailers, and the live
PR's DCO check is green.

Those public-port results are separate from the private submission's
exact-source record. The latter remains under
`artifacts/samples/verification/publication-20260827-v2-source/`, where the
host verifier passed 8 upstream eviction tests, 2 SQLite/FAISS tests (1
optional deselected), and 453 project tests (4 skipped).

## Benchmark summary

The frozen synthetic experiment evaluated 96 CARMA configurations, selected on
disjoint validation seeds, then used ten paired 10,000-request test seeds at
capacity 100.

- Category shift: +4.885 valid-hit-rate percentage points versus the per-seed
  stronger LRU/LFU baseline; 95% paired CI [4.677, 5.088], Holm-adjusted
  `p=0.0234`.
- Stationary: +7.122 points; non-regression gate passes.
- Synthetic weighted-savings-proxy lower confidence bounds pass in all three
  workloads; these deterministic weights are not empirical model-token costs.
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

Two later real-text ONNX attempts remain separate from that historical
diagnostic. `gate7b-onnx-v1` is `INVALID` after its first child exposed a
cross-concept return and the v1 runner stopped. `gate7c-onnx-v2`, at exact
source `557c6ac0578cb6b77c5ae51595b49abdc0407e10`, completed all 15 isolated
children and retained 45,000 full GPTCache/ONNX request rows, but is also
`INVALID` and nonclaimable: its preterminal audit has seven errors and its
ledger never acquired `TERMINAL`. The descriptive v2 systems reconstruction
passes 24 of 25 checks; seed 20261001 fails the absolute p95-delta limit with
26,230,125 ns versus 500,000 ns. Thus even a structurally valid adjudication of
these exact measurements would be `FAIL`, never `PASS`. The semantic guardrail
separately fails on three direct-negative hits, with two additional unlabeled
cross-component hits. No ONNX attempt is presented as a passing Gate 7 result.

## Compatibility and scope

- CARMA is opt-in; no default changes.
- Current full storage support is SQLite plus FAISS.
- Restore reconstructs residents/topics/cells but intentionally relearns
  demand and hit mass from zero.
- Miss assignment and victim selection add CPU work; complexity and bounds are
  documented in `docs/project/policy-design.md`.
- Full benchmark artifacts are tied to their recorded clean commits. Later
  protocol-remediation code does not retroactively change those outputs.
- The original precomputed-vector Gate 7 result remains `PENDING`; the v1 and
  v2 ONNX attempts remain separately preserved `INVALID` evidence. Any future
  attempt needs a new version/root/ledger/tag and the complete 5x3 matrix; none
  of the v2 children may be selectively reused.

## Reviewer guide

1. Start with the public PR's `gptcache/manager/eviction/carma.py` and
   `examples/eviction/carma.py`.
2. Review factory/callback wiring and cleanup recovery in the scalar/vector
   manager changes.
3. Inspect `tests/unit_tests/eviction/`,
   `tests/unit_tests/manager/test_carma_compatibility.py`, and
   `tests/integration_tests/test_carma_sqlite_faiss.py`.
4. Treat the private benchmark/report material as course evidence, not content
   of the upstream patch.

## Published upstream port

The public branch is based exactly on upstream `dev` commit
`74926813b988266cb783499a660cd582de4299b4` and contains two signed commits:

- `0916ca3a3d64039226bcff3cb1e0d379eb891bc4` -- production policy,
  compatibility hooks, docs, and example;
- `4caa70f1daf60ec272f4d9f15da19207ed8ab71c` -- additional compatibility
  coverage.

The public fork is <https://github.com/MatanGoldfarB/GPTCache>, the branch is
`feat/carma-online-policy`, and the review target is
<https://github.com/zilliztech/GPTCache/pull/696>. The port preserves two
important review details:

- preserve upstream's `**kwargs` forwarding to the vector store inside CARMA's
  locked/rollback `mul_add` path in `gptcache/manager/data_manager.py`; and
- add narrowly scoped Pylint `broad-except` suppressions to three intentional
  marker/recovery/rebuild handlers. Upstream's pinned Pylint then reports
  10.00/10.

The port's full validation surface is recorded above. Missing optional service
dependencies are not counted as patch passes; the one explicitly deselected
test is identified rather than folded into the passing total. The Sphinx build
emits 99 upstream warnings, mainly missing optional dependencies and existing
toctree/header issues, but completes successfully.

The production scope is eight files: `data_manager.py`,
`eviction/base.py`, `eviction/carma.py`, `eviction/manager.py`,
`eviction_manager.py`, `factory.py`, `scalar_data/base.py`, and
`scalar_data/sql_storage.py`. With two README updates, one executable example,
and four upstream-layout test files, the PR changes 15 files (+2,696/-23).
Course reports, evidence, and benchmark corpora are excluded. If maintainers
prefer a smaller review unit, policy/API wiring and storage-atomicity hardening
can be split without importing any private submission artifact.

Publishing the review-ready PR establishes a real contribution path, but does not satisfy
the lecturer's exceptional adopted-PR route. Maintainer review, requested
changes, and adoption remain external outcomes.

## Checklist

- [x] Backward-compatible default behavior.
- [x] No credentials or live LLM calls in tests/benchmarks.
- [x] Unit, property, integration, and failure-path coverage.
- [x] Pinned host/container verification evidence retained.
- [x] Deterministic benchmark schemas and artifact hashes.
- [x] Invalid Gate 7 v1/v2 evidence preserved and labeled nonclaimable; no
  ordinary v2 adjudication was synthesized after the missing terminal event.
- [x] Documented parameters, complexity, restart behavior, and limitations.
- [x] Public fork and signed feature branch created without private course
  evidence.
- [x] PR #696 opened against upstream `dev`, marked ready for review, and live
  DCO check passes.
- [ ] Maintainer review and adoption. An open review-ready PR is not adoption.
