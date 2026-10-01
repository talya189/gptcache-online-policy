# Prospective Gate 7 ONNX Remediation Contract

Frozen prospectively: 2026-08-27, before any confirmatory result from this
protocol is observed. Development-only fake and non-frozen-seed smoke runs do
not enter the adjudication and cannot alter this contract.

This contract governs one separately identified follow-up experiment for the
system-overhead claim in Gate 7. It does not amend, replace, or relabel the
historical experiment. Historical Gate 7 remains **pending** because its five
SQLite/FAISS runs used precomputed synthetic embeddings and the original
contract did not define an across-seed adjudication rule. A pass or failure
under this document must be reported as the **prospective Gate 7 ONNX
follow-up**, never as a retroactive result of the historical run.

The implementation may correct defects discovered by tests or structural
preflight before the confirmatory command is run. Any change to a frozen value
or rule below requires a new version of this contract and a new experiment
identity before observing confirmatory results. A failed numerical gate remains
a result.

## Question and fixed comparison

The experiment asks whether CARMA remains within the existing Gate 7 resource
and latency limits when real text enters `gptcache.adapter.adapter.adapt`, the
pinned ONNX embedding is computed inside every measured request, and GPTCache
executes its complete adapter, similarity, SQLite, FAISS, and response-return
path.

- Policies: GPTCache LRU, GPTCache LFU, and full CARMA, each with
  `clean_size=1` and immediate physical cleanup of evicted scalar and vector
  records.
- Formal comparator: paired LRU on the identical trace and seed. LFU is run
  and reported under identical conditions but is not a formal Gate 7
  comparator.
- Cache capacity: exactly 100 active entries.
- FAISS lookup: `top_k=1`.
- Answer cosine threshold: `0.97`.
- GPTCache configuration: `auto_flush=20`, token counting disabled,
  `data_check=False`, and report persistence disabled. The in-memory GPTCache
  operation counters remain part of normal adapter execution.
- CARMA parameters: `topic_threshold=0.70`, `cell_threshold=0.97`,
  `demand_half_life=500`, `quota_strength=1.0`,
  `ghost_support_threshold=1.5`, `admission_margin=1.05`,
  `centroid_alpha=0.05`, and `entry_hit_weight=0.25`.
- Confirmatory seeds: `20261001`, `20261002`, `20261003`, `20261004`, and
  `20261005`.

No policy parameter, threshold, capacity, seed, trace, policy order, timing
boundary, or aggregation rule may be selected using a confirmatory result.

## Pinned text and embedding identities

The request texts come only from the already prepared QQP **calibration**
split. Because `pairs.jsonl` combines both splits, every row is streamed far
enough to inspect its `split` field. A held-out row is then skipped immediately:
its endpoint IDs, concepts, and label are not validated, selected, resolved
through `texts.jsonl`, embedded, timed, or otherwise used by this experiment.
The source and preparation identities are:

- archive: `examples/benchmark/similiar_qqp_full.json.gz`;
- archive SHA-256:
  `1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58`;
- preparation: positive-component-disjoint split implemented by
  `benchmarks/carma/qqp.py`, with Unicode NFKC normalization, outer whitespace
  removal, and internal whitespace collapse;
- prepared-pair SHA-256:
  `c84d9897bd4838e8c12f45d59e04401031bbd5fa533570490db016cf40235126`;
- prepared-text SHA-256:
  `645ece94cebf36d1d37a66410d925d7d2252b6d39dd42b473e866289d1576dc3`.

The measured runner must compute, not load, one embedding for every request:

- tokenizer repository: `GPTCache/paraphrase-albert-small-v2`;
- tokenizer revision: `5fb246187b5489d59ce0db167e739192759defab`;
- model repository: `GPTCache/paraphrase-albert-onnx`;
- model revision: `5b562a100bc67e898ac89814e7a4668a18d65756`;
- model file: `model.onnx`, SHA-256
  `a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8`;
- maximum sequence length: 512, with `padding="max_length"` and truncation;
- pooling: attention-mask-weighted mean of token embeddings;
- output: finite, positive-norm, L2-normalized 768-dimensional `float32`
  vector;
- provider: `CPUExecutionProvider` only;
- ONNX execution: one worker, one intra-op thread, and one inter-op thread;
- `TOKENIZERS_PARALLELISM=false`.

`CARMA_ONNX_WORKERS`, `CARMA_ONNX_THREADS`, `OMP_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, and `NUMEXPR_NUM_THREADS` are each
fixed to `1` for the confirmatory command. Provider fallback, remote inference,
GPU execution, precomputed request embeddings, and cross-request embedding
memoization are prohibited.

Model/tokenizer download, model construction, and exactly 20 discarded
single-request embedding warm-ups are outside request timing. The warm-up texts
are the first 20 hot text IDs in the seed's canonical hot ordering. Storage is
created fresh after warm-up, so warm-up cannot populate the measured cache.

## Frozen QQP pollution trace

Each seed produces one 3,000-request, calibration-only pollution trace. The
same serialized trace and trace SHA-256 are supplied verbatim to all three
policies for that seed. Trace construction is completed before starting any
policy child.

### Candidate construction

Read `pairs.jsonl` in file order and retain only rows whose `split` is
`calibration`. For every retained endpoint, associate its `text_id` with the
corresponding `concept_a` or `concept_b`. Abort if one text ID maps to more than
one concept. Resolve text through `texts.jsonl`; abort on a missing or duplicate
text ID.

For every calibration concept, define its canonical text as its
lexicographically smallest text ID. These canonical records are the only trace
candidates, ensuring that distinct selected IDs also represent distinct
ground-truth concepts.

For seed `s`, rank canonical candidates by the bytewise ascending value of:

```text
SHA-256("gate7b-qqp-candidate-v1|" + decimal(s) + "|" + text_id)
```

Use the first 80 candidates as the hot working set and the next 1,200 as the
cold scan set. The selections are disjoint by both text ID and ground-truth
concept. Abort unless exactly 80 hot and 1,200 cold candidates are available.

### Request sequence

The phases are fixed at 30/40/30 percent:

1. `warm`, requests 0--899: eleven complete passes over the 80 hot records
   followed by the first 20 records of a twelfth pass;
2. `scan`, requests 900--2099: every one of the 1,200 cold records exactly
   once;
3. `return`, requests 2100--2999: the same multiplicities as `warm`.

Within each phase, obtain the final order by sorting the phase's occurrence
records by:

```text
SHA-256("gate7b-qqp-phase-v1|" + decimal(s) + "|" + phase
        + "|" + text_id + "|" + decimal(zero_based_occurrence))
```

Every request stores `request_id`, phase, occurrence, text ID, normalized text,
and ground-truth concept. The deterministic response ID is the ground-truth
concept ID, and its response payload is the UTF-8 string
`"recorded-response:" + concept_id`. On a miss the runner materializes that
recorded payload locally. No live language model, remote API, network call, or
paid service is part of the request path. A hit is valid only when its response
concept equals the request's ground-truth concept; any other raw hit is a false
hit.

The manifest must prove the 900/1,200/900 phase counts, 80 hot concepts, 1,200
distinct cold concepts, complete hot-set presence before the scan, identical
hot multiplicities in warm and return, calibration-only provenance, and an
identical trace hash across the three policy runs.

## Counterbalanced policy order

Policies run sequentially on the same otherwise idle machine. The deterministic
pseudo-random base order is obtained by ranking policy names by
`SHA-256("gate7b-policy-base-v1|" + policy)`; it is
`CARMA, LFU, LRU`. Seed offsets zero through two use the three cyclic rotations
of that base. Offsets three and four use the reversals of rotations zero and
one. This freezes the following schedule:

| Seed | First | Second | Third |
| ---: | --- | --- | --- |
| 20261001 | CARMA | LFU | LRU |
| 20261002 | LFU | LRU | CARMA |
| 20261003 | LRU | CARMA | LFU |
| 20261004 | LRU | LFU | CARMA |
| 20261005 | CARMA | LRU | LFU |

Every policy therefore occupies each ordinal position either once or twice,
the closest possible balance for five three-policy blocks. Both planned and
actual order are recorded. A child that starts out of order invalidates the
block rather than being silently reordered after results are known.

## Process, storage, and machine isolation

Each policy executes in a new child process with a new temporary directory, a
new SQLite database, a new FAISS index, and a new eviction object. Model loading
and warm-up happen independently in every policy process. No initialized
model, allocator, embedding, cache row, FAISS vector, policy state, or raw
request result is shared between policy children.

All 15 confirmatory children run on the same physical machine, operating-system
installation, operator-selected power mode, Python environment, dependency
lock, and inherited temporary-storage configuration. Policies are never run
concurrently. Stable power mode and the absence of unrelated heavy work are
operator-controlled conditions, not continuously attested facts. The supervisor
records wall time, machine and CPU identity, logical/physical CPU count, OS,
the project filesystem, power state when available at finalization, and system
load in the manifest and periodic resource samples. Confirmatory execution
requires a clean Git
worktree whose HEAD contains this contract, the runner, analyzer, tests, and
dependency locks used by the run. A process crash, provider fallback, capacity
violation, stale FAISS candidate, unequal scalar/vector count, unknown answer
ID, false hit, trace mismatch, or missing artifact invalidates that seed block;
it is not a numerical Gate 7 failure or pass.

The supervisor snapshots the contract, source-file identities, and Git HEAD
before the first child. Each child verifies that snapshot before and after its
measured work, the supervisor rechecks it around every child, and the bundle is
finalized only after a clean end-of-attempt Git/source recheck. The formal
contract path is exactly `docs/project/gate7-remediation-contract.md`;
alternate `--contract` files are development-only. Every snapshotted source
and the contract must match the blob stored at the recorded HEAD.

Development smoke traces and instrumentation tests must use different seeds or
an explicit `development` identity. Formal output directories are unique direct
children of `artifacts/gate7-onnx-attempts/`. The producer holds a root-scoped
interprocess lock from predecessor discovery through final manifest or failure
retention, preventing two formal attempts from both claiming an empty history.
An attempt ID is its UTC start time to microsecond precision plus the first 12
hexadecimal characters of its clean Git HEAD.

Before result-producing work begins, the producer appends a `START` event to
the root's canonical hash-chained `attempt-ledger.jsonl`. The event binds its
sequence, attempt ID, start time, Git HEAD, output directory, predecessor
count, predecessor-list hash, and previous ledger-entry hash. The manifest or
failure record retains the exact `START`-prefix row/byte counts and SHA-256.
Before registration, the producer records the identity and hash of every
earlier retained full-attempt manifest or failure record in that common root;
any unresolved nonempty attempt directory, malformed predecessor record,
missing ledger event, unterminated earlier `START`, or ledger/predecessor
disagreement aborts preflight.

After a complete manifest is written, the producer runs the independent
auditor as a subprocess while still holding the root lock and writes the
immutable `gate7-preterminal-adjudication.json`. It then appends a `TERMINAL`
event that binds the manifest byte count and SHA-256, the preterminal
adjudication byte count and SHA-256, its status and claimable flag, and the
auditor source identity. A pre-manifest failed attempt instead receives a
`TERMINAL` event binding `attempt-failure.json`. If manifest publication
succeeds but the independent audit, post-audit integrity check, or terminal
append cannot complete, the unmatched `START` is a deliberate fail-stop: no
later formal attempt may start until an external forensic resolution versions
the protocol. The producer does not rename or overwrite the already-published
manifest to manufacture a clean failure. The final offline audit requires
terminal-bound retained bytes to match the event. Historical eligibility is determined from
the original ledger-bound terminal verdict, not by re-running an earlier
attempt with a newer auditor.

Claimability therefore requires the complete retained attempt root, including
the ledger and predecessor directories, not an isolated copied bundle. The
independent auditor validates the ledger chain and recorded prefix,
reconstructs the chronological predecessor set from sibling records, checks it
against the manifest, and binds each timestamp to its attempt ID and Git
commit. Registered later directories do not invalidate an earlier attempt that
is audited while a successor is still running. A selectively repeated policy
child cannot replace its paired confirmatory block. If an operational failure
requires a rerun, retain the failed attempt and rerun the complete five-seed,
three-policy matrix under a new attempt ID. Only the first structurally valid
complete attempt is eligible unless the contract is versioned again before
results are inspected.

This first-attempt guarantee is scoped to the one complete, continuously
retained `artifacts/gate7-onnx-attempts/` root in this formal checkout. It
detects selective reruns, mutations, missing predecessors, and auditor-version
changes within that root. It does not prove global uniqueness across separately
created clones or after deletion of the whole root. A global first-attempt
claim would additionally require an external immutable authority such as a
lecturer-issued run token or a protected CI-controlled append-only ledger.

## Exclusive timing contract

Use `time.perf_counter_ns()` for monotonic request and stage timing. The
confirmatory runner retains one record for every request. The request timer
starts immediately before the raw prompt enters GPTCache's `adapt` entrypoint
and stops when `adapt` returns the fully materialized response object.

The following mutually exclusive stage fields are required:

- `text_preprocess_tokenize_ns`: normalization, tokenization, padding,
  truncation, and conversion to ONNX input arrays;
- `onnx_inference_ns`: the `InferenceSession.run` call only;
- `embedding_postprocess_ns`: attention-mask pooling, validation, conversion
  to `float32`, and L2 normalization;
- `faiss_search_ns`: vector search only, excluding embedding normalization;
- `sqlite_read_ns`: scalar lookup and cached-response reads;
- `similarity_decision_ns`: threshold comparison and hit/miss classification;
- `policy_exclusive_ns`: LRU, LFU, or CARMA access, admission, rejection, or
  victim-selection work, excluding any nested measured SQLite or FAISS call;
- `sqlite_write_ns`: insert, update, mark-delete, and physical-delete work;
- `faiss_mutation_ns`: vector add, delete, rebuild, or flush work;
- `response_return_ns`: decode/copy/materialization of the deterministic
  response plus propagation across the measured cache-call return boundary;
- `residual_ns`: request orchestration and timer overhead not assigned above.

Nested calls are charged to their innermost named stage. In particular,
SQLite/FAISS cleanup invoked synchronously by a policy callback is storage time,
not policy-exclusive time. LRU and LFU insertion/eviction must pass through the
same measured policy boundary as CARMA insertion/eviction; timing only
CARMA's metadata-aware insertion is prohibited.

Derived fields are:

```text
embedding_ns = text_preprocess_tokenize_ns
             + onnx_inference_ns
             + embedding_postprocess_ns

cache_management_ns = policy_exclusive_ns
                    + sqlite_write_ns
                    + faiss_mutation_ns

post_embedding_total_ns = request_total_ns - embedding_ns

request_total_ns = sum(all exclusive named stages, including residual_ns)
```

The implementation must abort if any time is negative, if the exclusive sum
differs from `request_total_ns` by more than the explicitly measured timer
rounding allowance, or if a required stage is absent. Stage latency is reported
separately for hits, admitted misses without eviction, admitted misses with
eviction, and rejected admissions as well as for the whole run. Quantiles of
stages are computed directly from their samples; stage quantiles must never be
added or subtracted as though percentiles were additive.

For each latency field, p50, p95, and p99 use the nearest-rank definition:
sort the `N=3000` samples and select one-based rank `ceil(q*N)`. Throughput is
`3000 / request_loop_seconds`, where the loop timer starts with the first
request timer and stops with the last completed return. Initialization,
download, model warm-up, verification, resource serialization, and artifact
writing are excluded from request-loop throughput and latency.

## Buffered request evidence

The child reserves an identical fixed-capacity request-record buffer before the
resource baseline for every policy. The complete fixed-key request dictionaries
and a physically touched `requests x embedding_dimension` little-endian
`float32` matrix are allocated before the ready signal. The loop mutates those
reserved rows and copies each computed embedding into its reserved matrix row;
it does not grow a list of retained embedding arrays or request dictionaries.
The child converts the reserved records to canonical JSON only after request
timing and resource sampling stop. Synchronous per-request file writes, logging,
compression, console output, or IPC are prohibited during the loop.

`requests.jsonl` contains, at minimum:

- schema, experiment, attempt, run, trace, request, seed, policy, and phase IDs;
- text ID, concept ID, occurrence, and reuse-opportunity flag;
- raw/valid/false hit, returned concept, similarity, and top candidate ID;
- admission, rejection, eviction, action, and cache size before/after;
- every exclusive timing field and all derived timing fields above.

Records are ordered by seed block, actual policy execution position, and
contiguous request index. Each run's request IDs are unique and its indices are
zero-based and contiguous. Records are encoded as canonical UTF-8 JSON Lines
with sorted keys and no non-finite numbers, and hashed after writing. The
manifest records row count, byte count, and SHA-256.

## External resource sampling

The supervisor, not the measured child loop, samples the policy process every
100 ms from the child's post-warm-up ready signal through its measured-loop
complete signal. The child performs no `psutil` call while a request timer is
active. Each `resources.jsonl` row records monotonic supervisor time, child
request index when available, RSS, VMS, USS when supported, user/system CPU
time, thread count, and process I/O counters when supported. Unsupported fields
are explicit nulls with an availability flag, never fabricated zeros.

The supervisor also takes mandatory start and end snapshots. Peak RSS for the
formal gate is the maximum observed RSS over the mandatory and periodic
external samples. The child's operating-system lifetime high-water value is a
diagnostic only because it includes startup before the formal window. Report
mean/peak RSS and USS, start-to-end and peak-above-start memory, CPU time, I/O,
throughput, and p50/p95/p99 latency for every run. The fixed request and
embedding-evidence buffers are allocated before the start snapshot so their
cost is equally present in every policy baseline and formal peak.

Sample timestamps must be strictly increasing, the mandatory samples must
cover the child request-loop boundaries, and every interior sample is marked
periodic. The 100 ms target permits at most one missed target under scheduler
jitter: a gap greater than 200 ms invalidates the affected run rather than
allowing an understated memory peak into adjudication.

## Manifest and artifact contract

One confirmatory invocation writes a source-addressed five-seed bundle.
Verification also requires the complete retained attempt root, the
checksum-pinned prepared QQP source, and the recorded Git objects; an isolated
bundle is not a content-addressed standalone proof. Individual files are
written atomically and `manifest.json` is written last. The attempt root and
bundle contain:

- `attempt-ledger.jsonl` at the common attempt root, containing hash-chained
  `START` and `TERMINAL` events;
- `traces/seed-<seed>.jsonl`, one exact retained real-text trace per seed;
- `requests.jsonl`, containing all policy request rows keyed by seed, run, and
  policy;
- `resources.jsonl`, containing all external samples keyed the same way;
- `outcome-latency.jsonl`, containing one independently reproducible whole-run
  latency summary plus one for each of the four frozen request outcomes, for
  every child;
- `runs.csv`, with one row per seed-policy child (15 rows total);
- `manifest.json`;
- immutable `gate7-preterminal-adjudication.json`, produced independently and
  bound by the terminal ledger event while the root lock is held;
- a machine-readable `gate7-adjudication.json` produced by a later independent
  offline audit of the terminalized bundle.

If a child crashes or any structural invariant aborts an attempt after its
output directory has been initialized but before manifest publication, that
directory retains
`attempt-failure.json` and all external samples accumulated from every completed
or active child so far. Captured stdout/stderr are also retained when the
failure arose from a child process. That failed attempt keeps its original
attempt ID and is never eligible for numerical adjudication. A retry uses a new
output directory and attempt ID and reruns the complete five-seed, three-policy
matrix. A failure after manifest publication is the unmatched-`START` fail-stop
described above and cannot be retried under this protocol without external
resolution.

The manifest records:

- contract path, SHA-256, schema version, experiment ID, attempt ID, UTC start
  and end, the formal attempt root, and the complete retained predecessor chain;
- Git HEAD, baseline commit, dirty-worktree flag, and hashes of the runner,
  analyzer, QQP preparation code, dependency locks, and container definition;
- archive, prepared pair/text, trace, model, tokenizer, and resolved ONNX-file
  identities and hashes;
- every frozen seed, threshold, capacity, policy, CARMA parameter, provider,
  thread setting, warm-up setting, timer definition, sampling interval, and
  quantile definition;
- planned and actual policy order, child PID, process exit status, and fresh
  storage paths or non-sensitive path hashes;
- Python and dependency versions, machine/CPU/OS identity, architecture,
  filesystem, available resource counters, and power/load observations;
- request/resource/run artifact names, row counts, byte counts, and SHA-256;
- trace structural checks, trace equality, storage counts, capacity maximum,
  stale/unknown/false-hit counts, embedding dimension/norm checks, and provider
  verification;
- per-run latency, throughput, CPU, memory, I/O, hit/miss, admission, rejection,
  and eviction summaries.

The offline verifier recomputes every artifact hash, row count, quantile,
throughput value, resource peak, paired ratio/difference, and gate boolean from
raw retained records. A summary that cannot be reproduced exactly enough under
the declared rounding rules is invalid.

## Frozen Gate 7 adjudication

The formal latency input is full `request_total_ns`, which includes real text
processing and ONNX embedding. `embedding_ns`, `post_embedding_total_ns`,
`cache_management_ns`, and `policy_exclusive_ns` are mandatory diagnostic
breakdowns and cannot replace the formal full-path value after results are
observed.

For each seed, pair CARMA with LRU by seed and exact trace hash. All five checks
below must be true for that seed:

```text
CARMA p95 request_total / LRU p95 request_total <= 1.25
CARMA p95 request_total - LRU p95 request_total <= 0.5 ms
CARMA throughput / LRU throughput >= 0.90
CARMA peak RSS / LRU peak RSS <= 1.20
CARMA peak RSS - LRU peak RSS <= 64 MiB
```

Both latency bounds and both memory bounds apply simultaneously. Ratios use
unrounded values; `0.5 ms` is exactly 500,000 ns and `64 MiB` is exactly
67,108,864 bytes. Report rounded values only after evaluating each boolean.

The prospective Gate 7 ONNX follow-up passes **only if all five seeds are
structurally valid and every seed satisfies all five checks**. There is no
averaging, majority vote, pooled-request quantile, favorable-seed exclusion, or
post-result bootstrap override. A complete valid experiment in which one or
more checks fail is a Gate 7 follow-up failure. Missing, invalid, or fewer than
five complete paired seed blocks leave the follow-up pending and unclaimable.

LFU results, policy-only timing, post-embedding timing, incremental memory, and
across-seed descriptive summaries are required diagnostics, but none changes
the frozen pass rule. Regardless of the new outcome, the historical Gate 7
entry and its precomputed-vector artifacts remain pending and unchanged.
