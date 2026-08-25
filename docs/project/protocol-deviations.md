# Post-execution synthetic protocol deviations

Recorded: 2026-08-26, after completion of the full synthetic run.

This note is a deviation ledger, not an amendment to the frozen experiment
contract and not a claim that the completed run complied retroactively. It
applies specifically to `artifacts/carma-full-20260826/`.

## Immutable run identity

The authoritative identity remains the one embedded in the existing
`metadata.json`:

- Git commit: `28129f0b9785232827e741c0e2ff2bb96cc19423`.
- Worktree at experiment start: clean, with an empty recorded status.
- Frozen contract SHA-256:
  `7a97eb9ee4891855b1a1025fbe5de703d6c73bc9c1c44968a8ffe9c1c742d776`.
- The metadata-recorded SHA-256 values for `full_experiment.py`, `runner.py`,
  `statistics.py`, `synthetic.py`, and `carma.py` identify the executed source.

Later documentation or code changes do not change that identity. In
particular, the existing five-file bundle must not be augmented with logs
replayed by later code and described as original observations. A future run
must receive its own commit, source hashes, contract hash, and metadata.

## Deviation ledger

### 1. The full synthetic bundle omitted request and resource JSONL

Contract commitment: emit per-request JSONL and resource JSONL in addition to
the validation, run, aggregate, and metadata summaries.

Observed: the completed bundle contains only `validation.csv`,
`validation_runs.csv`, `runs.csv`, `aggregate.csv`, and `metadata.json`.
`CacheSimulation` constructed request records in memory, but
`full_experiment.py` retained only run summaries. The synthetic configuration
also set `measure_latency=False` and had no process resource sampler. Separate
SQLite/FAISS integration runs produced their own `resources.jsonl`; those are
different runs with different provenance and do not fill the synthetic bundle
gap.

Impact: the omission does not change values already written to the four CSV
artifacts, but it removes the direct request-level audit trail and means the
synthetic bundle supplies no resource-time-series evidence. Resource and
latency conclusions must come only from the separately identified system runs.

Prospective remediation: the future-run synthetic path now streams each
completed request record to a staging `requests.jsonl`, across validation and
all emitted fixed stages, and hashes that file in new metadata. The callback is
observational and runs after request state and invariants are finalized. No
synthetic `resources.jsonl` has been invented: adding honest process sampling
requires a separately designed future run. This repairs the missing request
event stream only; synthetic timing remains disabled and the stream does not
invent stage-latency measurements. The historical bundle remains unchanged and
incomplete against this part of the output contract.

### 2. Synthetic policies ran in a fixed order

Contract commitment: randomize policy execution order within each seed.

Observed: each fixed synthetic stage iterated the declared policy tuple in a
fixed order. The resulting CSV was sorted later, so it does not record that
execution sequence.

Impact: this is a protocol deviation. Its likely effect on synthetic quality
metrics is limited because every policy received the identical trace and a new
`CacheSimulation` with isolated cache state, and synthetic timing was disabled.
It therefore has no identified shared-state or warmup path into valid-hit,
false-hit, or token-saving results. That limitation does not make the executed
order randomized or protocol-compliant.

Prospective remediation: future fixed stages rank policy names by a
domain-separated SHA-256 value derived from the seed. This produces a stable,
input-order-independent pseudo-random permutation that varies across seeds.
Validation has one policy and is unaffected. Focused tests verify the order and
request log are deterministic and that reordering isolated simulations leaves
their run-level quality summaries unchanged. This code change does not alter
the order used by the historical run.

### 3. Synthetic false-hit-rate intervals used bootstrap, not Wilson

Contract commitment: use Wilson intervals for precision and false-hit rates,
while using a paired bootstrap for seed-level policy differences.

Observed: `aggregate.csv` was generated with the deterministic percentile
bootstrap for metric summaries and the paired percentile bootstrap for
comparisons, including synthetic `false_hit_rate`. `statistics.py` did not
provide a Wilson calculation. The separately evaluated held-out QQP precision
used its own Wilson procedure and is not changed by this finding.

Impact: a bootstrap interval for a seed-level mean is not a Wilson binomial
interval and must not be relabeled as one. In particular, an all-zero bootstrap
interval does not supply the positive upper uncertainty bound that a Wilson
rate interval would provide. The existing synthetic false-hit intervals are
therefore descriptive outputs under the implemented bootstrap, not evidence of
compliance with the Wilson clause. The preregistered paired-bootstrap analyses
for non-rate primary deltas are not affected by this labeling distinction.

Prospective remediation: retain the historical numbers and label their method
truthfully. Before another inferential run, specify and test the Wilson
estimand for per-policy rates and the paired method for rate differences. Do
not overwrite the old aggregate or recompute it with later code as though the
result had been emitted at execution time.

### 4. Secondary controls and the scale sweep were incomplete

Contract commitment: add default batched LRU, no-cache latency,
infinite-capacity, FIFO, random, and simulation-only Belady-by-concept controls;
also run request-count scales `{1000, 3000, 5000, 10000}`.

Observed: the full synthetic bundle includes LRU, LFU, CARMA,
`CARMA_NO_CLUSTER`, no-decay, no-admission, and no-quota runs. It does not
include the listed secondary controls, and it contains no request-count scale
sweep. The capacity axis is present: capacities 20, 50, and 200 occur in the
exploratory sweep, while the capacity-100 primary stage supplies the frozen
capacity-100 runs. Capacity coverage should not be confused with the missing
scale sweep.

Impact: the omissions do not change the preregistered primary comparisons
against LRU, LFU, and the per-seed stronger baseline. They do limit claims
about batching, no-cache overhead, infinite-capacity headroom, negative-control
behavior, oracle headroom, and scaling with request count.

Prospective remediation: run any missing controls or scales only as a
separately identified post-execution supplement, or preregister them for a new
full run. Such results must not be inserted into the historical bundle or
treated as if they shared its preregistered execution status.

## Interpretation boundary

This ledger changes no selected configuration, threshold, seed, success gate,
trace, run summary, or artifact hash from the completed run. It narrows the
claims that may be made from that evidence and records which safeguards apply
only to future executions.
