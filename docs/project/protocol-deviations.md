# Post-execution protocol deviations

Recorded: 2026-08-26, after completion of the full synthetic, integration, and
QQP calibration runs.

This note is a deviation ledger, not an amendment to the frozen experiment
contract and not a claim that the completed run complied retroactively. It
applies to `artifacts/carma-full-20260826/`, the five
`artifacts/integration-full-2026090{1,2,3,4,5}/` directories, and
`artifacts/qqp-full/`.

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

### 5. Integration timing used precomputed synthetic vectors, not the frozen ONNX embedding

Contract commitment: use GPTCache `paraphrase-albert-onnx` on CPU as the
default integration embedding.

Observed: each of the five completed SQLite/FAISS integration manifests records
`precomputed_embeddings=true`. The runner replays the controlled synthetic
catalog's 2,982-dimensional vectors and does not invoke the ONNX embedding
model. Its `total` timer begins at cache search and therefore measures the
post-embedding cache path; embedding generation is excluded.

Impact: these runs are valid evidence about CARMA's policy cost, storage
cleanup, SQLite/FAISS consistency, and post-embedding cache-path behavior on
the frozen synthetic geometry. They are not evidence for embedding latency or
for a complete ONNX-backed request path. Although every seed satisfies the
individual numerical Gate 7 limits, the frozen contract also omitted an
across-seed aggregation rule. Gate 7 is therefore reported as a diagnostic
pending result rather than a confirmatory pass.

Prospective remediation: a new, separately identified integration run must
invoke the pinned ONNX model inside the measured request path, freeze how the
five seeds are aggregated, and publish that rule before results are observed.
It must not replace or relabel the existing precomputed-vector manifests.

Implementation status: this remediation is now encoded prospectively in
`docs/project/gate7-remediation-contract.md`, with a real-text GPTCache adapter
runner, retained per-request/resource evidence, counterbalanced policy order,
and an independent all-seeds adjudicator. Development smoke runs are excluded
from the claim. Until the complete clean-source five-seed matrix is executed,
the follow-up is pending and the historical status above is unchanged.

### 6. QQP held-out similarities were computed before the calibration abort

Contract commitment: select an answer threshold using calibration pairs only,
then evaluate the component-disjoint held-out split once if the calibration
precision prerequisite succeeds.

Observed: the frozen QQP implementation computed and grouped cosine
similarities for both split labels in one pass before scanning the calibration
threshold grid. No candidate met the calibration Wilson-lower-bound rule, so
the code returned immediately with
`status=no_threshold_met_precision_gate`: it did not select a threshold,
classify or aggregate held-out labels, inspect held-out metrics, or write a
held-out result. The retained result records all 56,963 test pairs as not
evaluated. Held-out cosine values nevertheless existed transiently in memory.

Impact: threshold selection read only calibration scores, so the observed
implementation provides no identified path for held-out outcomes to influence
the selected threshold (and no threshold was selected). It is still inaccurate
to describe the held-out split as computationally untouched before the abort.
Gate 2 fails at its calibration prerequisite; no held-out precision claim is
made.

Prospective remediation: compute calibration similarities first and return on
a failed prerequisite before loading or scoring held-out pairs. Only after a
threshold is frozen should a separate one-shot path compute held-out
similarities and metrics. The completed QQP artifacts are retained unchanged.

### 7. Gate 7 v4 changes the absolute-latency estimand after observing v3

Prior commitment and v3 result: Gate 7 v3 compared separate full-request p95
values and required both a CARMA/LRU p95 ratio at most `1.25` and a directional
CARMA-minus-LRU absolute delta at most `500,000 ns`. V3 completed all 15 policy
children and 45,000 requests. Its immutable, structurally clean preterminal
report records systems `FAIL`: seed `20261001` has a full-request p95 delta of
`1,307,374 ns`. Its semantic guardrail is separately `FAIL`. A manifest-contract
shape defect prevented `TERMINAL`, so the operational v3 result is permanently
`INVALID`, not a terminal `FAIL` or `PASS`.

Post-v3 design decision: v3 showed that the absolute full-request p95 delta
combines CARMA overhead with dominant common ONNX timing variation. V4 therefore
retains the user-visible full-request p95 ratio bound, moves the inherited
`500,000 ns` absolute-scale bound to the complete post-embedding cache path, and
adds a second `500,000 ns` bound on the nearest-rank p95 of exact request-paired
`CARMA.policy_exclusive_ns - LRU.policy_exclusive_ns` differences. It also
retains throughput and RSS guardrails. The six per-seed v4 checks are:

1. full-request p95 ratio `<= 1.25`;
2. marginal post-embedding p95 delta `<= 500,000 ns`;
3. request-paired policy-exclusive delta p95 `<= 500,000 ns`;
4. service-throughput ratio `>= 0.90`;
5. peak-RSS ratio `<= 1.20`; and
6. peak-RSS delta `<= 67,108,864 bytes`.

Scientific boundary: this is a post-v3 metric change, not a repair of the v3
contract. A read-only retrospective recomputation from the preserved raw v3
rows would pass all 30 v4 systems checks. The largest marginal post-embedding
p95 delta is `251,418 ns`, and the largest request-paired policy-exclusive p95
delta is `425,709 ns`. Those favorable post-hoc values may explain the design,
but cannot establish either a v3 or v4 `PASS`. V3 remains operationally
`INVALID`, its v3-rule preterminal systems result remains `FAIL`, and its
semantic result remains `FAIL`.

Executed safeguard: v4 used a new protocol/schema/root/tag with previously
unexecuted seeds `20261101`--`20261105` and fresh, frozen traces. Every one of
the 30 seed-check booleans was required to pass; the old full-request absolute
delta was diagnostic only. The governing contract is
`docs/project/gate7-v4-remediation-contract.md`, SHA-256
`acc55a60e25aa36682a7b66da20d2a982f16ca70e1bbe6aca13fad96eb1bd808`.
V4 did run. Its first attempt was terminally invalid because of stale duplicate
package metadata. Its second completed all 15 processes and 45,000 requests but
terminalized `INVALID` with seven structural audit errors. Independent
descriptive recomputation also fails: all five seeds exceed the paired
policy-exclusive limit, and one exceeds the post-embedding limit. The v4
records remain immutable and nonclaimable.

Retrospective provenance: the ignored authoritative source is
`artifacts/gate7-v3-onnx-attempts/attempt-20260827T222332Z-36292/requests.jsonl`,
45,000 rows, SHA-256
`e9d418e63e1c86acdacdeaf7c9d26c7347bae63b8f935fb461463c91b16d6dff`.
That identity is bound by the tracked v3 manifest. The compact Git preservation
snapshot does not include the 128.7 MB raw request file, so the retrospective
calculation must not be represented as independently reconstructable from the
compact snapshot alone.

### 8. Gate 7 v5 changes controls and hot-path work after observing v4

Prior v4 result: the complete second v4 matrix could not support a claim because
the wrapper and auditor used inconsistent path-bound identity shapes, the
warm-up schema comparison disagreed with the producer, and one resource gap
violated the brittle absolute 200 ms cadence rule. Its measured CARMA path also
exceeded the paired policy-exclusive p95 bound on every seed. V4 remains
`INVALID`; none of its evidence was edited or selectively rerun.

Post-v4 design decision: v5 keeps the same real-text workload, pinned ONNX
model, capacity, comparator policies, timing boundaries, and all six numerical
bounds. It changes only prospectively disclosed controls and implementation:
the identity checks now use the same path-bound shape; cadence requires at
least 99.9% of gaps within 200 ms plus a 1 s hard ceiling; transaction snapshots
store field tuples and rebuild dataclasses only on exceptional rollback; and a
quota refresh is deferred past first-occurrence rejection. An 18,000-event
differential replay confirmed unchanged admission, eviction, and resident
decisions. New seeds `20261201`--`20261205` and fresh traces prevent reusing v4
measurements to tune the outcome.

Frozen safeguard and outcome: the v5 contract is
`docs/project/gate7-v5-remediation-contract.md`, SHA-256
`9905baab3f4fde636adc6eefc2298a3ac473b96bf4ff5ba186fb79224de355dd`.
The sole eligible attempt ran from clean commit
`f59d64d93a5599149538003078403f1b33e2bab1` and annotated tag
`gate7f-onnx-v5-talya-formal-source`. It completed all 15 fresh processes and
45,000 requests with no selective rerun. The canonical terminal status is
claimable `PASS`: all 30 frozen systems checks pass, the semantic guardrail is
`PASS_OBSERVED`, and the ordinary auditor records zero errors and warnings. A
second isolated audit reproduced the canonical adjudication byte-for-byte.
This v5 result does not repair or reclassify v1--v4.

## Interpretation boundary

This ledger changes no selected configuration, threshold, seed, success gate,
trace, run summary, or artifact hash from the completed runs. It narrows the
claims that may be made from that evidence and records which safeguards apply
only to future executions.
