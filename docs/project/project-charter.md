# Project Charter: Online Cluster-Aware Caching for GPTCache

## Fixed sources

- Baseline: `zilliztech/GPTCache` at commit
  `c59fb3a6152a4458b2a070ca183b61c4b614095f`.
- Baseline paper: *GPTCache: An Open-Source Semantic Cache for LLM
  Applications Enabling Faster Answers and Cost Savings* (NLP-OSS 2023).
- Primary related work: *SCALM: Towards Semantic Caching for Automated Chat
  Services with Large Language Models* (arXiv:2406.00025v1).
- Assignment authority: the two lecturer PDFs stored one directory above this
  repository.

## Problem

GPTCache's in-memory semantic-cache manager applies one global count-bounded
LRU, LFU, FIFO, or random policy to opaque entry identifiers. It does not use
the semantic structure of the workload when deciding which entries to admit or
evict. SCALM shows that semantic patterns can improve cache hit and token-saving
ratios, but its workflow first derives and ranks patterns from a conversation
corpus.

The project will add a streaming, demand-adaptive semantic-cluster policy to
GPTCache. The policy must learn its cluster demand online, retain no dependency
on a paid LLM API, preserve the current cache-hit correctness threshold, and
remain compatible with existing eviction-policy callers.

This project will not copy or repackage the freshness/cost-aware FreCoS policy
or the learned/cost-aware SmartEvict policy. Token cost can be an evaluation
metric, but it is not an input to the new policy.

## Research hypothesis

At a fixed entry capacity, identical lookup threshold, identical embeddings,
and identical request order, a streaming semantic-cluster admission and
eviction policy can retain reusable semantic coverage better than global LRU
and LFU on skewed, scan-heavy, and phase-shifting workloads. The expected effect
is a higher valid-hit rate and token-saving ratio without a material increase in
false hits or cache-side latency.

The exact numerical success thresholds, parameter grid, seeds, and statistical
tests will be frozen in `experiment-contract.md` before any full experiment is
run. Failed gates will remain visible; they will not be redefined after results
are observed.

## Non-goals

- Changing the embedding model or similarity threshold to manufacture a gain.
- Calling a live paid LLM during the benchmark.
- Claiming GPU-serving improvements from a CPU trace replay.
- Treating raw cache hits as correct without a workload label or oracle.
- Publishing or opening a pull request without an explicit user-approved
  publishing step.

## Lecturer deliverables and evidence gates

### Correctness (40%)

- Backward-compatible default LRU behavior.
- Unit tests for clustering, admission, eviction, decay, tie-breaking,
  persistence reconstruction, and invalid configuration.
- Integration tests proving scalar and vector rows are removed together.
- Seeded oracle tests comparing policy decisions to a simple reference model.
- Existing relevant GPTCache tests remain green.

### Reproducibility (30%)

- Pinned Python and dependency definitions.
- A Dockerfile and one top-level verification command.
- A deterministic CI-sized workload with committed embeddings and expected
  result schema.
- Full benchmark commands that emit machine-readable CSV and JSON plus
  environment metadata.
- Figures generated only from committed result tables.

### Performance gain (15%)

- Vanilla LRU and LFU are run under the same workloads and capacities as the
  extension.
- Capacity, cluster threshold, demand decay, and policy-strength parameters are
  swept according to the frozen experiment contract.
- Repeated seeded runs report confidence intervals and corrected significance
  tests where multiple comparisons are made.
- Admission-only and eviction-only ablations isolate the source of any gain.

### Clarity (15%)

- One-page baseline justification.
- README installation and benchmark instructions.
- Documented parameters, trade-offs, and known limitations.
- An 8-12 page PDF report with labeled figures, tables, captions, and an
  appendix artifact map.

## Required final artifacts

- Feature implementation and tests on this non-default feature branch.
- `docs/project/baseline-justification.md` (one page maximum when rendered).
- Benchmark code, workload documentation, sample CSV/JSON logs, and full result
  tables.
- Reproducible analysis and figures.
- Dockerfile and CI workflow.
- Final 8-12 page PDF report plus source.
- A requirement-by-requirement completion audit and draft upstream PR text.

## Decision rule

The project is complete only when every evidence gate above is backed by a
current file, command result, rendered artifact, or benchmark output. A passing
unit test alone is not evidence of an end-to-end performance improvement, and a
performance plot alone is not evidence of correctness or reproducibility.
