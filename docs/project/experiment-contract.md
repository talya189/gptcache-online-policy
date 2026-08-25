# Frozen Experiment Contract

Frozen: 2026-08-25; amended 2026-08-26, before full benchmark execution.

This file is the preregistration for the project. Test-set thresholds and
success gates must not be changed after full results are observed. A failed
gate remains a result.

## Fixed identities

- GPTCache baseline commit: `c59fb3a6152a4458b2a070ca183b61c4b614095f`.
- Primary related work: SCALM, arXiv `2406.00025v1`.
- QQP source: the full labeled archive already distributed under
  `examples/benchmark/similiar_qqp_full.json.gz`.
- Recorded-response source: `OpenMOSS-Team/moss-003-sft-data`, revision
  `42e216d3e3fb331c18d5fa6e7cb4f1c53eef24a4`.
- Default integration embedding: GPTCache `paraphrase-albert-onnx`, CPU.
- GPTCache lookup uses `top_k=1` so one request creates at most one policy hit.
- Synthetic confirmation uses answer threshold `0.97`. A separate paired
  hard-negative diagnostic uses threshold `0.95` so deliberate cosine-`0.96`
  twins exercise false-hit accounting. That diagnostic is not a tuning or
  configuration-selection input. QQP answer matching remains independently
  calibrated from human labels.

## Ground truth and leakage controls

Positive QQP pairs are unioned into concept components. Splitting is by entire
component, never by row. Twenty percent is calibration data and 80 percent is
an untouched semantic-safety test. Negative pairs whose endpoints collapse
into the same positive component are removed and counted. Policy-created
clusters never define answer correctness.

For MOSS, a concept is the stable hash of conversation ID, round, and prior
context. On a miss the recorded response is replayed. No live model or paid API
is part of the benchmark.

`valid_hit` means the returned answer ID is allowed for the request's ground
truth concept. `false_hit` means a raw hit returned another concept. A reuse
opportunity exists when the ground-truth concept appeared earlier.

## Fixed metrics

```text
valid_hit_rate = valid_hits / requests
hit_precision = valid_hits / (valid_hits + false_hits)
false_hit_rate = false_hits / requests
opportunity_recall = valid_hits / reuse_opportunities
safe_token_saving_ratio = valid-hit prompt+context+answer tokens / all tokens
scan_return_valid_hit_rate = valid hits in scan-return / scan-return requests
```

False hits save zero valid tokens. Report mean, p50, p95, and p99 cache-path
latency, throughput, CPU time/utilization, peak/mean RSS and USS, disk/I/O
bytes, admissions, rejections, evictions, clusters, and phase-recovery lag.

For each category-shift phase after the initial phase, the policy-independent
recovery target is 90% of the reuse-opportunity rate in that phase's final
quarter. Recovery lag is the earliest zero-based start of a contiguous window
covering 10% of the phase whose valid-hit rate meets the target. A zero or
unattained target is right-censored at the full phase length. Report mean and
maximum lag, failure count, and every transition's target/window details.

## Primary controls

- GPTCache LRU with `clean_size=1`.
- GPTCache LFU with `clean_size=1`.
- Full CARMA with `clean_size=1`.

Secondary controls are default batched LRU, no-cache latency, infinite-capacity
ceiling, FIFO/random negative controls, and a simulation-only Belady-by-concept
upper bound. Identical traces, embeddings, thresholds, capacities, and request
orders are reused across policies.

## Workloads

- Stationary Zipf: concept popularity exponent `1.2`, with 15% one-shot
  concepts.
- Category shift: five phases of 2,000 requests; 80% of demand moves to a new
  hot topic set and 20% remains background.
- Pollution scan: 3,000 hot-working-set requests, 4,000 unique requests, then
  3,000 requests from the original hot set. The working set is `0.8 * capacity`.
- Novel-long: unique requests drawn from the longest MOSS quartile and
  stratified into 32/128/256/512-token buckets.
- QQP semantic safety: positives plus negatives oversampled near the frozen
  answer threshold.

CI uses deterministic 512-request QQP, 1,000-request stationary,
1,500-request shift, 1,200-request scan, and 200-request novel-long traces at
capacities 256 or 50 with seed zero.

Full test runs use 10,000 requests per stationary/shift/scan trace at capacity
100 and ten paired seeds for primary comparisons. Capacity sweeps use
`{20, 50, 100, 200}` with five seeds (ten at capacity 100). The exploratory
capacity sweep replays the same capacity-100 trace at every cache capacity, so
cache size is not confounded with a changing working set. Scale sweeps use
`{1000, 3000, 5000, 10000}`. System runs use fresh processes and five seeds.

Pre-execution scalability amendment: the full synthetic catalog reuses
orthogonal cell-local feature blocks across topics. It preserves the exact
within-topic cosine hierarchy (`0.72`, `0.88`, and `0.96`); cross-topic cosine
is at most `0.12`, below every topic threshold. The full catalog therefore has
834 rather than more than 8,000 dimensions. The benchmark records measured
cosine extrema and aborts on geometry drift.

Pre-execution coverage amendment: the first full command was interrupted after
107.68 seconds while still evaluating validation configurations; it had not
selected a configuration, written an artifact, or constructed a held-out test
trace. The runner was amended to retain `scan-return` valid-hit rate and the
recovery-lag definition above before restarting validation from the beginning.
The same pre-restart audit corrected two builder drifts to the already-frozen
workloads: category shift now uses five phases with 80% hot demand, and the
pollution split is exactly 30% warm, 40% unique scan, and 30% return.

Failed-run capacity correction: the next full command completed validation
and began primary-test computation, then stopped after 267.47 seconds before
publishing or printing any selected configuration or result. The exception
showed that 192 concepts per cell supplied 3,840 non-hot concepts, fewer than
the frozen 4,000-request unique scan. No observed performance value informed a
decision. The catalog was increased to 200 concepts per cell (exactly 4,000
non-hot concepts; 834 dimensions), with all thresholds, seeds, gates, and
selection rules unchanged. A disjoint seed-`20260000` structural preflight now
constructs every trace, verifies five equal shift phases and the 30/40/30 scan
with 4,000 distinct cold concepts, and must pass before validation begins.

## Frozen tuning procedure

Answer matching and CARMA clustering are calibrated independently.

- Answer cosine threshold candidates: `0.80` through `0.99` by `0.01`. Select
  the lowest calibration threshold whose one-sided 95% Wilson precision lower
  bound is at least `0.99`.
- `topic_threshold`: `{0.70, 0.80, 0.90}`.
- `cell_threshold`: `{0.95, 0.97}`.
- `demand_half_life`: `{100, 500, 2000, infinity}` request events.
- `quota_strength`: `{0, 0.5, 1.0, 2.0}`.
- Fixed `admission_margin=1.05`, `ghost_support_threshold=1.5`,
  `centroid_alpha=0.05`, and entry hit weight `0.25`.

Pre-execution amendment: the ghost threshold was changed from the provisional
value 2.0 to 1.5 before any benchmark run. With exponential decay, two distinct
events have support strictly below 2.0; 1.5 precisely means two observations
within one configured half-life and preserves rejection of a one-shot scan.

Pre-execution tuning amendment: the CI diagnostics showed that provisional
`cell_threshold=0.88` merges distinct answer concepts whose constructed cosine
is also `0.88`. It was replaced by the strictly separated candidates `0.95`
and `0.97`, producing 96 configurations. Validation traces were reduced from
5,000 to 2,000 requests before any inferential test was run. This changes the
validation workload from 4.32 million to 1.728 million policy requests while
retaining three workloads and three paired validation seeds; it makes the
CPU-only grid tractable without inspecting the disjoint test seeds.

The 96 CARMA configurations are evaluated only on 2,000-request validation
traces, three paired seeds each. Select the configuration with the highest
mean workload-normalized valid-hit rate subject to zero synthetic false hits;
ties use ascending topic threshold, cell threshold, half-life rank, and quota
strength. No test-set retuning is allowed. The synthetic zero-false-hit check
at `0.97` is an implementation invariant, not the substantive semantic-safety
claim; the latter is decided by held-out QQP.

Ablations are: one global topic, no decay, admission disabled, quota-aware
eviction disabled, and full CARMA.

## Statistics

- Randomize policy execution order inside each seed and pair policies by exact
  trace hash.
- Compute policy-minus-baseline deltas per seed.
- Use a 10,000-resample paired bootstrap over seed-level differences.
- Use exact two-sided paired Wilcoxon tests for preregistered hypotheses and
  Holm-correct primary p-values.
- Report paired rank-biserial effect size. Use Wilson intervals for precision
  and false-hit rates.
- Primary-test whole-trace valid-hit-rate and pollution return-phase
  valid-hit-rate comparisons against LRU, LFU, and the per-seed stronger
  baseline are confirmatory and share the Holm family. Other emitted p-values,
  recovery-lag results, hard-negative results, and capacity sweeps are
  explicitly exploratory.

## Success gates

1. All relevant upstream and extension tests pass; capacity violations, stale
   vector returns, unknown answer IDs, and nondeterministic non-timing logs are
   all zero.
2. Held-out QQP hit precision is at least 99% and its Wilson lower bound is at
   least 98%. CARMA's false-hit-rate increase over the stronger paired baseline
   has an upper 95% bound no greater than 0.1 percentage point.
3. On the 10,000-request category-shift test at capacity 100, CARMA improves
   valid-hit rate by at least two absolute points over `max(LRU, LFU)`, with the
   paired bootstrap confidence interval above zero and Holm-adjusted `p <= .05`.
4. Return-phase scan valid-hit rate improves by at least five points over the
   stronger baseline, with its confidence interval above zero and
   Holm-adjusted `p <= .05`.
5. Stationary valid-hit-rate delta has a lower confidence bound no worse than
   minus one point.
6. Safe token-saving-ratio delta has a lower confidence bound no worse than
   minus 0.5 point in each of the three primary workloads.
7. CARMA p95 cache-path latency is at most 1.25 times LRU and no more than
   0.5 ms higher; throughput is at least 90% of LRU; peak RSS is at most 1.20
   times LRU and no more than 64 MiB higher.
8. Two clean Docker CI runs produce identical non-timing logs and trace hashes;
   pinned downloads verify revisions/checksums and require no credentials.

## Output contract

Per-request JSONL includes schema/run/trace IDs, request and phase IDs, concept
and category, reuse status, token counts, expected/returned/cache-entry IDs,
raw/valid/false hit flags, similarity, admission/eviction and cache sizes, plus
total and stage latency. Resource JSONL records monotonic time, memory, CPU, and
I/O. `validation.csv` contains one row per configuration, while
`validation_runs.csv` retains every seed/workload/configuration result needed
to recompute selection. `runs.csv` contains one row per test run;
`aggregate.csv` contains summaries, paired deltas, confidence intervals,
corrected p-values, and effect sizes.

Timing fields are excluded when asserting deterministic log equality.
