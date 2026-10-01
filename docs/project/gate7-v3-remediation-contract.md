# Gate 7 v3 real-text ONNX remediation contract

Protocol version: `gate7d-onnx-v3`
Evidence schema: `carma-gate7-onnx-v3`
Audit schema: `carma-gate7-adjudication-v3`
Ledger schema: `carma-gate7-attempt-ledger-v4`
Formal root: `artifacts/gate7-v3-onnx-attempts`
Drafted: 2026-08-28. It is not frozen until its final SHA-256 is substituted
into producer, auditor, and isolated bootstrap before any v3 formal START.

This contract governs a new prospective experiment. It does not edit, repair,
replace, delete, or reinterpret either prior Gate 7 attempt. Development smoke
tests with fake embeddings, nonformal seeds, or nonformal output paths are not
v3 confirmatory evidence.

## 1. Why v3 exists and what remains unchanged

The first real-text ONNX attempt, `gate7b-onnx-v1`, terminated after the first
CARMA child. All 3,000 requests completed, but the child reported one false hit
and one unknown answer. Forensic reconstruction showed that both counters
described the same cross-concept cache return. The response was not an unknown
payload: it came from another retained QQP concept. The v1 implementation
combined semantic-label disagreement with structural corruption and therefore
aborted before it could publish the complete request buffer or run the other
14 children.

That attempt remains **INVALID** for a Gate 7 numerical claim. Its original
bytes are retained without selective repair:

- attempt ID: `20260827T084100878176Z-b74ed9553e7c`;
- source HEAD: `b74ed9553e7c0a8bad71e3dbd90e047dbcd654dc`;
- v1 contract SHA-256:
  `e93b3f301373a0b1a1c9fa99378f555bd45b9c6e8f8717ac82ea817763ecdf4a`;
- v1 root-ledger SHA-256:
  `96539252388659e779ac09014895886156e9f405020ce278527b82bfea22aeb7`;
- v1 terminal-entry SHA-256:
  `768d681eac5f5f39e7517275ef7c2b29ff4f57d5fa7e2531c45e3db6142157a5`;
- v1 failure-record SHA-256:
  `41a7af508e769f24a91e72b221906fd7e50c6d20176032a9d58590ba9feb9f12`;
- preservation manifest:
  `docs/project/evidence/gate7-v1-invalid-attempt.json`, SHA-256
  `b7ee1befd512d1582c0816152b9b87c1cbed91ac3a924bbbebc2bcedc3c6dee1`;
- preservation archive:
  `docs/project/evidence/gate7-v1-invalid-attempt.tar.gz`, 484,539 bytes,
  SHA-256
  `cb326101dcc8323575be376d923630cfda6191c2e7adbc3b7fd6b01acf6b9147`.

The separately frozen v2 experiment, `gate7c-onnx-v2`, completed all fifteen
children and retained 45,000 request rows, but is also **INVALID** and
nonclaimable. Its preterminal report contains seven structural errors: one
lexical/resolved Python-identity mismatch, five identical warm-up declaration
shape mismatches, and one resource-cadence error. The manifest keyed the
auditor source as `{bytes, sha256}` while the report and terminal intent used
`{path, bytes, sha256}`; direct object equality then rejected terminalization.
The v2 ledger therefore retains `PROTOCOL_GENESIS` and one unmatched `START`
and may never be repaired or continued. Its adjudication-critical snapshots
remain under `artifacts/samples/verification/gate7-v2-invalid/`, and the full
attempt remains untouched under `artifacts/gate7-v2-onnx-attempts/`.
The tracked snapshot is an independent immutable preservation declaration,
not an alias for that mutable full-attempt tree. Its exact files are:

- `SHA256SUMS`, 269 bytes, SHA-256
  `069ee7e3951ac3a4f015841fe0a020168f501c2cc38e19b3505f87f7c619cb07`;
- `attempt-ledger.jsonl`, 21,346 bytes, SHA-256
  `84eee7963f4e5c27f8d962e31ddcdcec9779b1cd463a8952841946ffc8557f0c`;
- `gate7-preterminal-adjudication.json`, 293,720 bytes, SHA-256
  `e3e8df2787049d3b1ea4e622e7666502b715ae572431b4f8d4e4b213c12dd39b`;
- `manifest.json`, 695,528 bytes, SHA-256
  `7574127cce8f1c72e87a1528dc6709947860178f43667a92add155e89e1f1cf8`.

The snapshot binds attempt
`20260827T131743602373Z-557c6ac0578c`, directory
`attempt-20260827T131736Z-1784`, source
`557c6ac0578cb6b77c5ae51595b49abdc0407e10`, tag
`gate7c-onnx-v2-formal-source`, status `invalid`, claimability `false`, seven
reported structural errors, and the absence of a TERMINAL ledger row.

V3 changes only those structural and terminalization definitions. It does not
reuse any v2 timing, request, resource, seed-policy result, attempt directory,
or ledger row. A semantic-label hit remains reported by a separate guardrail.
Only broken provenance, inconsistent storage, stale candidates, capacity
excess, malformed evidence, provider/configuration drift, or a failed process
is structural invalidity. Semantic observations do not stop the matrix.

## 2. Gate 2 calibration is separate and remains failed

Gate 2 threshold selection uses `benchmarks/carma/qqp_v2.py`. Its first pass
reads `split` from every pair. For a held-out row it increments a count and
immediately continues; before selection it does not dereference that row's
label, endpoint IDs, concepts, embedding rows, or cosine similarity. Only
calibration endpoints are resolved and scored.

The frozen selection grid is `0.80, 0.81, ..., 0.99`. For every threshold,
precision is `TP/(TP+FP)`, false-positive rate is `FP/(FP+TN)`, false-hit rate
is `FP/(TP+FP+TN+FN)`, and the precision lower bound is the one-sided 95%
Wilson bound with
`z=1.6448536269514722`. The selection rule is the lowest grid threshold whose
Wilson precision lower bound is at least `0.99`. The threshold table and
selection transcript are written before a possible second pass. A second pass
may open and evaluate held-out pairs only if the first pass selected a
threshold. The table also retains the one-sided 95% Wilson upper bound for
false-hit rate; that diagnostic does not enter threshold selection.

The strict v2 run processed 7,729 calibration pairs and discovered 56,963
held-out rows by the split-only scan. No threshold qualified. Consequently,
the selected threshold is null, no second pass occurred, and zero held-out
endpoints or similarities were evaluated. The retained evidence is:

- calibration table:
  `docs/project/evidence/qqp-v2-calibration-thresholds.csv`, SHA-256
  `6cb322dc79b32fca9b3200ca16e1f28136589860b3e9aa558d70b64d44c7f038`;
- selection transcript:
  `docs/project/evidence/qqp-v2-threshold-selection.json`, SHA-256
  `eb53e05e1816765fb93f2bed05bd01e2ce233c98879a20838f6853b97c1bd08c`;
- result:
  `docs/project/evidence/qqp-v2-result.json`, SHA-256
  `3544e53f45f6fdd540a0489c4bc5a0a5750fcec96bb93204b9bc7df43016eca1`.

Therefore Gate 2 remains **FAIL / no qualifying threshold**. Gate 7 v3 retains
`0.97` solely as the already specified systems operating point needed for a
controlled comparison with v1. It is not a Gate 2-qualified semantic threshold,
and neither a Gate 7 systems pass nor a favorable semantic trace observation
may be used to say otherwise.

## 3. Formal question and frozen configuration

The formal systems question is whether CARMA stays inside all existing Gate 7
overhead bounds relative to LRU when each measured request begins with real
text, computes the pinned ONNX embedding, executes GPTCache's adapter,
similarity, SQLite, FAISS, eviction-policy, and response-return path, and
retains enough raw evidence for independent recomputation.

The frozen policies are GPTCache `LRU`, GPTCache `LFU`, and full `CARMA`.
Each policy uses immediate physical cleanup with `clean_size=1`. LRU is the
formal paired comparator; LFU is a mandatory identically run diagnostic.

The fixed values are:

- seeds: `20261001`, `20261002`, `20261003`, `20261004`, `20261005`;
- requests per child: 3,000;
- active cache capacity: 100;
- FAISS `top_k`: 1;
- answer hit threshold: 0.97;
- CARMA topic threshold: 0.70;
- CARMA cell threshold: 0.97;
- demand half-life: 500;
- quota strength: 1.0;
- ghost support threshold: 1.5;
- admission margin: 1.05;
- centroid alpha: 0.05;
- entry-hit weight: 0.25;
- GPTCache `auto_flush=20`, token counting disabled, `data_check=False`, and
  report persistence disabled;
- ONNX intra-op threads: 1;
- ONNX inter-op threads: 1;
- tokenizer/model maximum length: 512;
- discarded model warm-ups per child: 20;
- external resource-sampling target: 100 ms.

No seed, policy order, request, label, threshold, capacity, parameter, timing
boundary, aggregation rule, semantic rule, or gate bound may be altered after
observing a formal result. In particular, the label-zero pair at source index
1804 (the two near-identical “invest my money wisely” questions) remains a
negative source label. It may not be removed, relabeled, or used to retune the
threshold because it appeared in v1.

## 4. Pinned real-text and ONNX path

The only text source is the prepared QQP calibration split:

- archive: `examples/benchmark/similiar_qqp_full.json.gz`;
- archive SHA-256:
  `1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58`;
- prepared-pairs SHA-256:
  `c84d9897bd4838e8c12f45d59e04401031bbd5fa533570490db016cf40235126`;
- prepared-texts SHA-256:
  `645ece94cebf36d1d37a66410d925d7d2252b6d39dd42b473e866289d1576dc3`;
- identity normalization: Unicode NFKC, strip outer whitespace, collapse
  internal whitespace.

The measured runner computes an embedding for every request. It may not load
the precomputed Gate 2 embedding matrix into the request path. The model is:

- tokenizer repository `GPTCache/paraphrase-albert-small-v2`, revision
  `5fb246187b5489d59ce0db167e739192759defab`;
- ONNX repository `GPTCache/paraphrase-albert-onnx`, revision
  `5b562a100bc67e898ac89814e7a4668a18d65756`;
- `model.onnx` SHA-256
  `a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8`;
- exact model file map:
  `{"model.onnx":"a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8"}`;
- canonical model-map SHA-256
  `af8dd7ee021644802c30f27709e46ad77e65425d527ecdca8346995dea83d7ce`;
- exact tokenizer file map:
  - `config.json`:
    `69765de9af37e704755cab37bee53f251465c269ae3f0c95436ab250dd11e342`;
  - `special_tokens_map.json`:
    `129fed06908ddcc3e36105e41d753ff0b934e5cfb2e451ca0a48904acef41863`;
  - `spiece.model`:
    `fefb02b667a6c5c2fe27602d28e5fb3428f66ab89c7d6f388e7c8d44a02d0336`;
  - `tokenizer.json`:
    `d0a881fece9b11d4f8003a08ac7d8d65409e3aa573fc385faa8708cdd5a77087`;
  - `tokenizer_config.json`:
    `95f31ea415a8e447b1e2ca05b897a05c1f5857b0643579d42dbec5ac5c2555c1`;
- canonical tokenizer-map SHA-256
  `41f1aee1afa8c01eecd6f60e8097836cb8e97bae95bf2f7fe34d85c5764f9deb`;
- provider `CPUExecutionProvider` only;
- fixed 512-token padding/truncation;
- attention-mask-weighted mean pooling;
- finite, positive-norm, L2-normalized 768-dimensional `float32` output;
- `TOKENIZERS_PARALLELISM=false` and one worker/thread for each relevant
  ONNX, OMP, OpenBLAS, MKL, and NumExpr setting.

Model loading and the 20 warm-ups are outside request timing. Warm-up storage
is discarded before the measured cache is created. Provider fallback, GPU or
remote inference, live language-model calls, cross-request embedding memoizing,
and precomputed request embeddings are prohibited.

The parent hashes the complete logical model and tokenizer file sets and rejects
any missing, extra, or changed file, or aggregate-digest drift, before START.
It passes all four expected map/digest identities to every child. Independently,
each child requires those expected identities to equal the constants above and
rehashes the exact local files before model load or warm-up. After its measured
loop and resource end acknowledgement, the child rehashes the complete sets a
second time. A pre-load or post-loop mismatch prevents a successful child
result. The manifest retains `model_files`, `model_digest_sha256`,
`tokenizer_files`, and `tokenizer_digest_sha256` verbatim.

Formal input is self-contained at attempt scope. Before START, the producer
copies the raw archive to `source/similiar_qqp_full.json.gz`, the prepared
`pairs.jsonl`, `texts.jsonl`, and `manifest.json` to `source/prepared/`, the
dependency attestation to `source/dependency-attestation.json`, and the raw
annotated-tag payload to `source/formal-source-tag.raw`. Every copy is
byte/hash bound in `retained_inputs` and the artifact catalog. Trace
construction reads only the retained `source/prepared/` copy. The auditor must
use these retained copies; mutable external preparation/cache paths are not
audit inputs. Smoke bundles retain their prepared fixture files but need not
carry the frozen raw archive or formal provenance files.

## 5. Deterministic trace and identical policy input

For each seed, calibration concepts are built from positive-label connected
components. A concept's canonical text is the lexicographically smallest text
ID in that component. Candidate ranking is bytewise SHA-256 order under
`gate7b-qqp-candidate-v1|<seed>|<text_id>`. The first 80 concepts form the hot
set and the next 1,200 concepts form the cold scan set. They are disjoint by
text ID and concept.

The 3,000 requests are:

1. `warm`, indices 0--899: 11 full passes over 80 hot texts plus 20 texts;
2. `scan`, indices 900--2099: each of 1,200 cold texts exactly once;
3. `return`, indices 2100--2999: the same hot multiplicities as `warm`.

Within a phase, occurrences are ordered by bytewise SHA-256 under
`gate7b-qqp-phase-v1|<seed>|<phase>|<text_id>|<occurrence>`. Every row retains
the request index/ID, phase, occurrence, normalized text, text ID, source
concept ID, reuse opportunity, deterministic response ID, and deterministic
payload. The v2 payload is
`recorded-response-v2:<concept_id>:<text_id>`, allowing independent response
provenance without a remote generator.

The producer serializes and hashes a seed trace before any policy child starts.
All three policies receive the same byte-identical trace file for that seed.
The manifest proves phase counts, hot/cold cardinality, hot multiplicity,
calibration-only provenance, and per-policy trace equality. Trace generation
uses the same candidate and phase namespaces as v1, so the selected text/order
projection is unchanged even though v2's provenance-bearing response payload
changes the full serialized bytes.

The expected v2 trace and semantic-index hashes are frozen as follows:

| Seed | Trace SHA-256 | Semantic-index SHA-256 |
| ---: | --- | --- |
| 20261001 | `25f773f174d14648c254aa355d9f7a983eafa6acad9c852f1af934ae42635c1b` | `bf7496e49b0a2adda8414aff26ef63b4771f3791dc411084a310787dd5af6832` |
| 20261002 | `fc20d64d878d27bfcb0298bff35419a6c9aeae9ecbc686e314f61122593ce50f` | `f96ef44b7a5d8cc5fc55a6024d516c6ad585c8b7519628e7f38e9dc085c05546` |
| 20261003 | `199178c7739cdf5d5682cf60963b7228c7e6f6bc1394dbf297f8fc8e4a3b386c` | `c5d0a1b58453cfedb89ddd5bb890a4442c00c69d00a7647d52770eaf26240abd` |
| 20261004 | `806a58004bfd3fecf3b97603639ec279b338967c64e99842df36f17e704f9cc4` | `07897d6572e7925c0b540f6c20ce76f692ccb29f979844cb41b5a1945ce9fd6a` |
| 20261005 | `a680defccd6e9973a65c9b34c37c44d62edfdf67a69ffcf260d7d59dbd9d270f` | `7ccac25843a51d3c2417eaf69a5411510cfc0fe7f5d6876efb7acf6d30496a41` |

Formal preflight must reproduce all ten hashes before the first measured
child. A mismatch requires a new protocol version; it may not be accepted as
an implementation-detail change after results are observed.

For every seed, the producer also writes one canonical
`warmups/seed-<seed>.json` object with schema `carma-gate7-warmup-v1`, the
experiment and seed, trace SHA-256, warm-up request count, ordered hot text
IDs, selected warm-up text IDs, and their exact text strings. Selection is
derived only from the ordered trace hot IDs. The artifact is written once and
all three policies receive its identical bytes and expected SHA-256. Each
child verifies the file hash, schema, trace/seed binding, ordering, IDs, and
texts before model load or warm-up. Its run summary records the same hash. A
mutation, policy-specific warm-up, or hash disagreement is structural
invalidity. Both `warmup_artifacts` and the general artifact catalog declare
the exact three-field identity `{sha256, bytes, rows}`. Here `rows` is the
physical line count of the canonical pretty-printed JSON file; it is not the
number of warm-up requests. The auditor independently recomputes all three
fields and rejects missing or extra identity fields.

## 6. Semantic index and classification

Before policy execution, the producer builds one deterministic semantic index
per seed from the prepared calibration labels and the seed's selected concepts.
It retains the exact serialized index in
`semantic-indexes/seed-<seed>.json`, records its file identity, and binds its
canonical semantic-index hash to every corresponding request/run summary.

Positive labels define connected components. A label-zero source row defines a
direct negative relation between its endpoint texts. A negative edge between
members of two positive components defines a component-derived negative
relation between those components. For a raw cache hit, classification priority
is:

1. same positive component -> `positive_same_component`, status `valid`;
2. exact label-zero endpoint pair -> `negative_direct`, status `invalid`;
3. components connected by any label-zero pair ->
   `negative_component_derived`, status `invalid`;
4. different components with no retained label ->
   `unlabeled_cross_component`, status `indeterminate`.

A miss is `not_applicable`. The index retains source indices for negative
evidence. It must never infer that an unlabeled pair is negative, and must
never turn model similarity or cache behavior into a ground-truth label.

Each cache insertion stores source concept/text IDs in the payload. On lookup,
the runner independently resolves the top candidate's stored question through
the canonical normalized-question map, parses the payload provenance, and
requires those two sources to agree. Unresolved candidate/question provenance,
malformed or unrecognized payload IDs, or disagreement between stored-question
and payload provenance is structural invalidity. A recognized cross-concept
payload is not an “unknown answer”; it is classified by the semantic index.

The semantic guardrail for a complete attempt is:

- `FAIL` if any direct or component-derived labeled-negative hit occurs;
- `PENDING_INDETERMINATE` if no labeled-negative hit occurs but at least one
  unlabeled cross-component hit occurs;
- `PASS_OBSERVED` if every raw hit is same-component;
- `NO_HITS` if no raw hit occurs.

These are descriptive trace-level statuses, not a population precision claim.
They do not override Gate 2 and do not determine the Gate 7 systems result.

## 7. Structural validity, systems status, and combined disclosure

Three axes are reported separately:

1. **structural status**: whether evidence, provenance, storage, provider,
   timing, source, and ledger invariants are valid;
2. **Gate 7 systems status**: `PASS`, `FAIL`, `PENDING`, or `INVALID` under the
   frozen numerical rules;
3. **semantic guardrail status**: the classification in Section 6.

Semantic-label hits do not abort a child or matrix. All five seeds and all
three policies must run even after a semantic `FAIL` or indeterminate hit.
Conversely, a system `PASS` may coexist with semantic `FAIL`, and both must be
shown. A combined disclosure may summarize the pair (for example, “systems
PASS / semantic FAIL”), but must not collapse them into a single favorable
status.

Structural failures include process/provider/config drift, malformed trace or
semantic index, unresolved/inconsistent provenance, stale FAISS candidates,
scalar/vector disagreement, cache capacity above 100, missing/nonfinite timing,
artifact/hash mismatch, ledger inconsistency, and source/worktree mutation.
Any structural failure makes the systems evidence `INVALID`, not a numerical
pass or fail.

## 8. Counterbalanced order and fresh-process isolation

Policies run sequentially and never concurrently. The fixed order is:

| Seed | First | Second | Third |
| ---: | --- | --- | --- |
| 20261001 | CARMA | LFU | LRU |
| 20261002 | LFU | LRU | CARMA |
| 20261003 | LRU | CARMA | LFU |
| 20261004 | LRU | LFU | CARMA |
| 20261005 | CARMA | LRU | LFU |

This is a deterministic SHA-256-derived randomized/counterbalanced schedule.
The base permutation is the bytewise SHA-256 ordering of
`gate7b-policy-base-v1|<policy>`; its rotations and reversals are assigned to
the sorted frozen seeds. It is reproducible from the documented namespace,
independent of caller input order, and counterbalances first/last positions;
it does not depend on an undocumented runtime RNG draw. The schedule is the
same as v1 for controlled comparability. Planned and actual order are retained.
Starting a child out of order is structural invalidity.

Every seed-policy block uses a new child process, new ONNX session, new
temporary SQLite database, new FAISS index, new policy object, and new storage
directory. No model allocator, cache row, embedding, policy state, or raw
result is shared between children. All 15 children use the same physical
machine, OS, Python environment, dependency locks, and inherited power/storage
configuration. Each child performs its own model warm-up and then signals the
supervisor before measurement.

## 9. Full-path timing and request evidence

`time.perf_counter_ns()` is the monotonic timer. The request timer begins just
before real prompt text enters the measured adapter path and ends immediately
when `gptcache.adapter.adapter.adapt` returns after response materialization.
The timer does not include the post-return provenance resolution, semantic
classification, structural validation, embedding-evidence derivation, or asset
rehashing. Those steps remain mandatory before the child evidence is accepted.

Mutually exclusive timing fields are:

- `text_preprocess_tokenize_ns`;
- `onnx_inference_ns`;
- `embedding_postprocess_ns`;
- `faiss_search_ns`;
- `sqlite_read_ns`;
- `similarity_decision_ns`;
- `policy_exclusive_ns`;
- `sqlite_write_ns`;
- `faiss_mutation_ns`;
- `response_return_ns`;
- `residual_ns`.

Nested SQLite/FAISS work is charged to storage, not policy-exclusive time.
LRU, LFU, and CARMA use the same instrumented boundaries. Derived fields are:

```text
text_preprocess_tokenize_ns = preprocess_ns + tokenize_ns
embedding_ns = text_preprocess_tokenize_ns + onnx_inference_ns
             + embedding_postprocess_ns
faiss_ns = faiss_search_ns + faiss_mutation_ns
sqlite_ns = sqlite_read_ns + sqlite_write_ns
similarity_decision_ns = similarity_evaluation_ns
response_return_ns = response_materialization_ns + response_propagation_ns
cache_management_ns = policy_exclusive_ns + sqlite_write_ns
                    + faiss_mutation_ns
post_embedding_ns = post_embedding_total_ns
                  = request_total_ns - embedding_ns
end_to_end_ns = request_total_ns
request_total_ns = preprocess_ns + tokenize_ns + onnx_inference_ns
                 + embedding_postprocess_ns + faiss_search_ns
                 + faiss_mutation_ns + sqlite_read_ns + sqlite_write_ns
                 + similarity_evaluation_ns + policy_exclusive_ns
                 + response_return_ns + residual_ns
```

These are exact integer equalities on every retained request row, not rounded
summary relationships. `embedding_elapsed_ns`, `policy_inclusive_ns`, and
`post_embedding_elapsed_ns` are overlapping diagnostics and are deliberately
excluded from the exclusive sum. Negative values, absent fields, an alias
mismatch, or a failed exclusive-sum reconciliation are structural errors.
Quantiles are computed directly from samples; quantiles of different stages
are never added. p50/p95/p99 use nearest rank
`ceil(q*N)`. Formal service throughput is
`N / (sum(request_total_ns) / 1e9)`. Both `service_throughput_qps` and the
legacy-compatible `throughput_qps` carry that same value, computed from
unrounded request rows. `loop_throughput_qps` is retained only as a diagnostic
from first measured-request start to last adapter return. It is not adjudicated
because mandatory post-return provenance/evidence work occurs between requests
and would otherwise contaminate policy comparisons.

The child preallocates and physically touches fixed-capacity request and
embedding-evidence buffers before the resource baseline. It performs no
per-request disk logging or resource sampling inside the measured loop. After
the loop it writes every request, including timings, action/outcome, hit,
candidate, response, storage, capacity, provenance, semantic classification,
and structural flags. On a catchable Python exception, the child's top-level
handler serializes the fully completed in-memory request prefix before exit;
the parent then retains that prefix with prior summaries and external resource
samples. A native crash, SIGKILL, or host loss cannot run that handler, so only
parent-held samples and captured process output may survive. Universal crash
recovery would require per-request/shared-memory checkpointing that this
timing protocol deliberately does not add. The manifest/failure record must
state which diagnostic classes are present rather than implying unavailable
rows were retained.

Every request includes, at minimum, query source IDs, top candidate ID,
candidate cache-data presence, cached-question hash, question-derived source
IDs, payload-derived source IDs, provenance resolution/consistency, semantic
relation/status/label/evidence/source indices, raw hit, returned response IDs,
response mismatch flags, stale/capacity/storage flags, and all timing fields.
Run summaries contain an exhaustive hit partition: same-component,
direct-negative, component-derived-negative, unlabeled cross-component, and
unresolved. Their sum equals raw hits.

## 10. External CPU and memory evidence

The supervisor samples the child outside the measured process from the ready
signal until measured-loop completion. Mandatory start/end samples and 100 ms
periodic samples record monotonic time, request index when available, RSS, VMS,
USS when supported, user/system CPU time, thread count, and process I/O where
supported. Unsupported values are null with availability metadata.

Cadence is measured start-to-start. Every resource row records
`sample_started_monotonic_ns`, `sample_completed_monotonic_ns`, and
`sample_collection_ns`; legacy-compatible `monotonic_ns` equals the start
timestamp exactly, completion is not earlier than start, and collection time
equals their difference. Collection is synchronous: each next sample start is
not earlier than the preceding sample completion. Periodic deadlines advance
from the preceding absolute monotonic deadline rather than from collection
completion, preventing polling and `psutil` collection costs from accumulating
as schedule drift.

Peak RSS is the maximum mandatory/periodic sample inside the formal window.
The OS lifetime high-water value is diagnostic only. A periodic gap greater
than 200 ms invalidates the run. Per-run evidence reports mean/peak memory,
start-to-end and peak-above-start memory, CPU, I/O, throughput, and full-path
and stage p50/p95/p99 values.

### Formal dependency and startup attestation

Formal parent, every formal child, and the preterminal auditor enter through
the stdlib-only launcher `scripts/gate7_v3_isolated_bootstrap.py` using exact
flags `-S -P` and roles `parent`, `child`, and `auditor`. `-I` is prohibited:
it would ignore the required hash-seed, pycache, and no-bytecode environment.
Before adding either site-packages or the project root to `sys.path`, the
launcher verifies:

- exact CPython `3.12.13`, lexical interpreter
  `.venv/bin/python`, its resolved binary observation, `pyvenv.cfg`, and the
  project virtual-environment prefix;
- `requirements-benchmark.lock` SHA-256
  `8723b1875ff08ff16691e7b9d7166fc6b089fb362c8d27f2f43dde34be04d8ad`,
  50 exact pins, and canonical pin-map SHA-256
  `a9ad2cf1ad4db90d5c43d01eaaad84b739153616e31d5404dd817b47f195a759`;
- an installed distribution map equal to those pins plus only the local,
  editable, source-bound `gptcache==0.1.44`;
- the local editable GPTCache RECORD summary exactly equal to
  `{record_sha256: 873280782d16563eef2982efbead80814577c01aea0253cea060d7cb3cc5b140,
  hashed_file_count: 11, hashed_bytes: 29856,
  hashed_files_sha256: 29bf983930625eacdddedcd24953d1849e9ebf923f233c09bd38506d67391b91}`;
- every hashed member of every installed RECORD, including every `.pth`
  file, and the frozen locked-distribution RECORD/member aggregate SHA-256
  `1b59e03f6a6d52a8b64adf8f11eb8ed36c40f807d53248965a00c733f8dca2c3`
  over 10,270 hashed files and 362,595,746 bytes;
- a sealed site-packages walk: every actual file has a RECORD owner, every
  directory has a declared descendant, unhashed installed bytecode is absent,
  symlinks/special entries are rejected, and `sitecustomize.py` and
  `usercustomize.py` are absent;
- no non-stdlib module was preloaded, no `.pth` executed, and `site` remained
  absent before target activation.

For a `full` launch, the stdlib-only bootstrap also verifies the source
boundary before the project root is importable: tracked Git state is clean,
there are no nonignored untracked files, the local annotated tag is a real tag
whose peeled commit and tree equal HEAD, and the `gptcache`/`benchmarks`
runtime roots exactly match their tracked Git files. A second scan covers the
whole project import namespace except sealed `.git` and `.venv`: every
`.py`, `.pyc`, `.pyo`, `.so`, `.pyd`, `.dylib`, and `.dll` must be tracked,
and every project symlink is rejected. This closes root-level, ignored, and
artifact-directory import shadows. The bootstrap retains that pre-import
observation; later producer checks do not substitute for it. Before project
activation it also requires one exact `submission` fetch URL and one exact
push URL, queries the authorized HTTPS URL with `git ls-remote`, and binds the
remote annotated-tag object and peeled HEAD into `preimport_source.anchor.remote`.

RECORD is not trusted as self-authenticating: the per-distribution raw RECORD
SHA and hashed-member-map summary must reproduce the tracked aggregate above,
and the editable GPTCache summary must reproduce its separately frozen map.
The bootstrap is the authoritative full site seal and repeats that seal after
the target exits. After activation, the producer and every child independently
repeat the pinned installed-package map, raw RECORD and hashed-member
aggregates, editable GPTCache summary, dependency/import-origin checks, and
attestation digest at their specified boundaries. Those target-side checks do
not replace the bootstrap's stricter lexical path, symlink, duplicate-owner,
unhashed-bytecode, and complete site-tree checks. Requires-Dist consistency is
evaluated in-process with the locked `packaging` parser; no ordinary Python or
`pip` subprocess may bypass `-S -P` or execute `site`.

The wrapper-launched Python environment uses `env -i`. Required static values are
`PYTHONHASHSEED=0`, `PYTHONNOUSERSITE=1`, `PYTHONSAFEPATH=1`,
`PYTHONDONTWRITEBYTECODE=1`, offline Hugging Face/Transformers settings,
`TOKENIZERS_PARALLELISM=false`, wrapper-shell marker
`gate7d-shell-v1`, formal-entrypoint marker `gate7d-wrapper-v1`, every ONNX/OMP/
OpenBLAS/MKL/NumExpr/vecLib thread control at one, `LANG`, `LC_ALL`, and
`LC_CTYPE` equal to `C.UTF-8`, `TZ=UTC`, and
`PATH=/usr/bin:/bin:/usr/sbin:/sbin`. Wrapper-owned dynamic values are HOME,
TMPDIR, Hugging Face cache paths, wrapper PID/path, explicit
`CARMA_GATE7_LAUNCH_MODE=full|smoke`, forwarded wrapper-shell profile, HOME,
PWD, TMPDIR, SHLVL and optional macOS text-encoding observation, and a unique real, non-symlink, empty
`PYTHONPYCACHEPREFIX`; the producer's parsed mode must equal that launch mode
in every parent and child. macOS may inject only
`__CF_USER_TEXT_ENCODING`, which is retained in the common environment
identity. `PYTHONHOME`, `PYTHONPATH`, `PYTHONSTARTUP`, user/cache overrides,
and all `DYLD_*`/`LD_*` variables are forbidden. Child environments are built
from this allowlist rather than inherited wholesale.

The attestation uses schema `carma-gate7-dependency-attestation-v2`. Its
`python.executable` is the non-resolved project-relative lexical value
`.venv/bin/python`; `python.resolved_executable` separately records the
absolute resolved base binary. Both must independently match the corresponding
isolated-bootstrap observations. It also retains the logical environment
digest, installed and required-module origins,
RECORD/sealed-inventory evidence, in-process dependency consistency, NumPy
Accelerate BLAS/LAPACK observation, and FAISS maximum thread count one. The
bootstrap observation uses schema `carma-gate7-isolated-bootstrap-v2`; its
canonical digest binds the role, target bytes, startup flags and paths,
environment, interpreter/venv/lock/dependency observations, and preloaded
module origins. Its nested `python.wrapper_shell_startup` uses schema
`carma-gate7-wrapper-shell-startup-v1` and binds profile `env-i-v1`, marker,
full launch mode, HOME/PWD/TMPDIR/SHLVL, optional macOS value, fixed locale/
path/timezone values, and the fact that the outer env-i operator root is
required. Parent, child, and auditor observations must have identical
role-independent fields; only role, target, target hash, and self-digest may
differ. The parent entrypoint, every child result/run, and the preterminal
auditor report retain and cross-bind those observations.

This is a sealed venv/site import boundary on the fixed recorded host,
sufficient for same-machine policy parity. It is not a complete hermetic
interpreter or machine image: stdlib, `lib-dynload`, system dylibs, kernel,
hardware, and Accelerate implementation bytes are observed but not all
source-pinned. Claims must retain that limitation.

## 11. V3 ledger, prior lineage, and attempt eligibility

The v3 root is separate from both immutable prior roots. Its first canonical ledger entry is
exactly one `PROTOCOL_GENESIS` event. The genesis binds:

- the v1 preservation manifest/archive paths, byte identities, v1 formal-root
  name, v1 root-ledger SHA-256, terminal attempt ID, terminal-entry SHA-256,
  and failure-record SHA-256;
- the retained v2 INVALID snapshot identities without importing any v2 result
  into the v3 predecessor chain;
- this v3 contract path and SHA-256;
- schema `carma-gate7-attempt-ledger-v4`, experiment `gate7d-onnx-v3`,
  sequence 1, a UTC timestamp, null previous-entry hash, and its own canonical
  entry hash;
- the full formal source anchor described below. Every later START must equal
that genesis anchor exactly.

Producer, independent auditor, and isolated bootstrap each recompute all four
tracked v2 snapshot identities and their internal preservation declaration.
The four snapshot paths are also mandatory entries in the formal source-
identity map. Missing, symlinked, changed, or internally inconsistent snapshot
bytes fail before a v3 claim can be terminalized. The protocol-critical source
map also includes `tests/project_tests/test_gate7_v3_isolated_bootstrap.py`
and `tests/project_tests/test_gate7_v2_preservation.py` in addition to the v3
producer/auditor tests and the reused v2 trace test.

The source anchor is the annotated SHA-1 tag
`gate7d-onnx-v3-formal-source` on remote `submission`. Both the fetch URL and
push URL must be exactly
`https://github.com/MatanGoldfarB/gptcache-online-policy.git`. Fetch and push
are queried with `git remote get-url --all` and each must be exactly that
singleton list; both lists are retained in the anchor, GENESIS, and every
START, so repointing the remote name or adding another URL is rejected. Local
object type must be `tag`; its peeled commit
must equal clean HEAD; its commit tree, raw tag payload SHA-256/byte count, tag
object ID, and matching remote tag-object and peeled refs are retained. The raw
annotation must contain exact lines
`experiment_id=gate7d-onnx-v3` and
`contract_sha256=<the independently pinned contract SHA-256>`. The raw payload
is copied into the attempt and bound by the anchor. Retries must reproduce the
GENESIS anchor; a moved/deleted local or remote tag is a fail-stop.

The first v3 attempt appends `START` sequence 2 chained to genesis, then one
`TERMINAL` sequence 3 chained to START. Later whole-matrix attempts, if
eligible after a terminal structural invalidity, continue monotonically as
START/TERMINAL pairs. Genesis is never repeated. A dangling START, malformed
chain, missing predecessor directory/record, or identity mismatch is a
fail-stop.

The producer holds a root-scoped interprocess lock from genesis/predecessor
validation through immutable preterminal-report publication and terminal-intent
preparation. After genesis and all potentially lengthy
predecessor/report walks have completed, and directly at the START append
boundary, it re-reads the
clean Git state, contract, canonical source-snapshot digest, source tag/remote,
dependency/environment attestation, wrapper/bootstrap entrypoint, prepared
inputs, then verifies every retained-copy byte identity, and only then captures
the point power observation. START binds attempt ID,
clean HEAD, UTC start, directory, predecessor count/digest, contract path/hash/
bytes, source-snapshot SHA-256, full source anchor, dependency and environment
digests, full parent entrypoint/bootstrap attestation, pre-START power row,
retained-input declaration, previous-entry hash, and its own hash.

A successful manifest is audited in a fresh `auditor` bootstrap subprocess
while the root lock remains held. Its immutable
`gate7-preterminal-adjudication.json` retains status/claimability, exact auditor
source identity, and full auditor bootstrap observation. This preterminal
auditor independently re-queries both singleton remote URL lists and remote
`submission`, and requires the URLs, tag object, and peeled ref to equal the
retained anchor. The later offline auditor
validates the retained historical remote observation and raw tag object but
does not claim a new live remote observation. After the preterminal process
returns, the producer again verifies live `site` absence, exact current
`sys.path`, prefix/exec-prefix, sealed environment, sentinel object identity,
private empty pycache, target bytes, full dependency attestation, source/
contract/Git/tag/remote, retained inputs, and entrypoint. It then publishes on
the private process state a canonical
`carma-gate7-terminal-intent-v2` object binding the unmatched START prefix,
manifest, preterminal report, status/claimability, auditor identity, and mapped
target exit status. The producer does **not** append a completed-manifest
TERMINAL.

The manifest source map is keyed by path and therefore stores the auditor as
`{sha256, bytes}`. The report, terminal intent, and TERMINAL are self-describing
and store `{path, sha256, bytes}`. Producer, auditor, and bootstrap separately
require the literal v3 auditor path and compare the two content fields; they
must not compare those differently shaped objects directly.

When the target exits with status 0, 1, 2, or 3, the isolated parent bootstrap
first completes its authoritative private-baseline post-target checks: target
bytes, source/tag/remote observation, Python environment and path boundary,
empty private pycache, and the entire sealed dependency tree must still equal
their pre-import values. Only after those checks pass does the bootstrap
reacquire the same root lock, require that the ledger still ends at the exact
START prefix named by the intent, independently rehash the immutable manifest
and report, reconstruct their START bindings, and append the completed
TERMINAL. The unmatched START prevents another conforming process from
registering during the handoff between the producer lock and the bootstrap
lock. A missing/malformed intent, an unexpected target status, any post-target
rejection, or any changed ledger/evidence byte leaves the START unmatched and
is operationally INVALID; immutable manifest or preterminal bytes are not
renamed or rewritten into a failure.

The bootstrap appends one canonical line with `O_APPEND`, flushes it with
`fsync`, and immediately rereads the complete ledger. A process or host crash
during that physical write can leave a truncated/noncanonical trailing row
rather than a clean unmatched START. That state is still a permanent
nonclaimable fail-stop: neither producer nor auditor may repair, ignore, or
continue past it under this protocol.

TERMINAL repeats the full source anchor and dependency/environment digests and
binds the exact terminal-record path/hash/bytes. For a completed manifest it
also binds the exact preterminal report path/hash/bytes, status, claimability,
and auditor identity, plus a non-null
`carma-gate7-bootstrap-completion-v2` receipt. That receipt binds the original
bootstrap-attestation digest, producer-target hash, canonical pre-import
source hash, sealed-dependency hash, Python-environment hash, target exit
status, terminal-intent hash, `phase=post_target`, `role=parent`,
`launch_mode=full`, `checks_passed=true`, and its own canonical self-hash. The
ordinary auditor reconstructs every receipt value from START, manifest,
report, and ledger bytes rather than trusting the receipt. For
`attempt-failure.json`, report path/hash/bytes and auditor identity are null,
report status is `invalid`, claimability is false, and bootstrap completion is
null. A catchable post-START failure before manifest publication publishes the
failure record, retained partial evidence and child stdout/stderr when
available, then the producer appends that nonclaimable invalid TERMINAL while
its original lock remains held. Every previous START must have exactly one
valid TERMINAL before a new registration.

A catchable failure before START is different: because no canonical attempt
was registered, it must not receive a TERMINAL. The producer independently
re-reads the ledger, and only when neither this attempt ID nor directory has a
START does it atomically move the exact directory to sibling
`artifacts/gate7-v3-onnx-preflight-failures/` and write a small
`preflight-failure.json` diagnostic there. A START-registered directory is
never moved or deleted. A host kill between directory creation and the caught
cleanup can still leave unregistered crash debris; that is a fail-stop for
manual forensic recovery, not an automatically reusable attempt.

A retry never reruns one seed or policy. It retains the failed attempt and runs
the complete 5x3 matrix under a new ID. Only the first structurally valid
complete v3 attempt is eligible. This protects the continuously retained local
root; it does not claim global uniqueness across deleted clones without an
external append-only authority.

## 12. Required artifacts and independent audit

A complete attempt contains:

- root `attempt-ledger.jsonl`;
- retained raw `source/similiar_qqp_full.json.gz`, raw tag payload,
  dependency attestation, and prepared `pairs.jsonl`, `texts.jsonl`, and
  `manifest.json`;
- five `traces/seed-<seed>.jsonl` files;
- five `semantic-indexes/seed-<seed>.json` files;
- five `warmups/seed-<seed>.json` files;
- `requests.jsonl` with 45,000 rows;
- `resources.jsonl`;
- `outcome-latency.jsonl`;
- `runs.csv` with 15 rows;
- `manifest.json`, atomically published as the producer's final data artifact
  before preterminal audit;
- immutable `gate7-preterminal-adjudication.json`;
- later offline `gate7-adjudication.json`.

The manifest records contract, Git, source, dependency, QQP, Gate 2, trace,
semantic-index, model/tokenizer/provider, machine, config, order, process,
storage, timing, resource, artifact, structural, systems, and semantic fields.
It also retains the source anchor, common logical environment digest, parent
entrypoint/bootstrap observation, every child bootstrap observation, every
child pre/post imported-module origin map, point power observations, retained
input declaration, and warm-up identities.
Every JSONL file uses canonical sorted-key finite JSON and records row/byte/hash
identity. Each required direct-child artifact declaration also carries a
literal `path` equal to its manifest key, and required paths are unique. The
independent auditor imports no producer code. It recomputes
hashes, row counts, traces, semantic index identities/classifications, hit
partitions, storage/capacity/provenance checks, quantiles, throughput, resource
peaks, paired differences/ratios, Gate 7 booleans, semantic status, ledger
chain, and first-valid-attempt eligibility from retained evidence.

## 13. Frozen Gate 7 systems adjudication

For each seed, pair CARMA with LRU by seed and exact trace hash. All five
conditions must hold for that seed:

```text
CARMA p95 request_total / LRU p95 request_total <= 1.25
CARMA p95 request_total - LRU p95 request_total <= 500,000 ns
CARMA service_throughput_qps / LRU service_throughput_qps >= 0.90
CARMA peak RSS / LRU peak RSS <= 1.20
CARMA peak RSS - LRU peak RSS <= 67,108,864 bytes
```

Throughput is recomputed from unrounded per-request total nanoseconds, not the
rounded CSV value or loop-duration diagnostic. Ratios use unrounded values.
Both latency and both memory bounds apply
simultaneously. The Gate 7 systems result is `PASS` only if all 15 children are
structurally valid and all five seeds satisfy all five conditions. A complete
valid matrix with any failed condition is `FAIL`. An incomplete developmental
bundle is `PENDING`; structurally unusable evidence is `INVALID`. There is no
averaging, majority vote, pooled-request quantile, favorable-seed removal,
selective rerun, or post-result bootstrap override.

LFU results, policy/cache-only timing, post-embedding timing, incremental
memory, and across-seed summaries are mandatory diagnostics but do not change
the frozen decision. Gate 2 and the semantic guardrail remain separately
reported whatever the systems outcome.

## 14. Formal execution boundary

Formal mode requires a clean committed worktree, this exact contract digest
pinned independently in producer, auditor, and isolated bootstrap, all v3 and
preservation tests, available checksum-matching QQP/model/tokenizer inputs, CPU
provider, AC power, the matching annotated local/remote source anchor, the
sealed fixed-host venv, and an absent or validly continued v3 formal root. The
only formal entrypoint is:

```bash
/usr/bin/env -i \
  CARMA_GATE7_WRAPPER_SHELL=gate7d-shell-v1 \
  HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 LC_CTYPE=C.UTF-8 TZ=UTC TMPDIR=/tmp \
  /usr/bin/caffeinate -dimsu \
  /bin/bash "$PWD/scripts/run_gate7_v3_onnx_integration_benchmark.sh" full
```

That absolute outer `/usr/bin/env -i ... /usr/bin/caffeinate ... /bin/bash ...
full` operator command is the startup root of trust. The wrapper-shell marker
and later process, environment, and bootstrap checks establish the state
observed at the wrapper's first executable line; they do not independently
prove that `env -i` or `caffeinate` executed before Bash startup processing.
This fixed-host protocol also assumes the trusted operator launches the
committed wrapper/bootstrap bytes and that no adversary can modify and restore
those live files before their first identity check. Defending a malicious host
or self-restoring launcher would require an external content-addressed or
signed execution authority and is outside this experiment's systems claim.

Test completion and AC power are operator preconditions, not claims inferred
from merely invoking the wrapper. Before START, the operator retains the clean
host/container verification output and checks the machine's power source. The
producer independently enforces the committed clean tree, contract/source/data
hashes, annotated remote anchor, wrapper PID/path/parent command, isolated
bootstrap, dependency boundary, provider, configuration, and ledger/root
state. Full wrapper mode accepts no output override or extra argument. Smoke
mode cannot target the formal root and cannot pass formal/internal reserved
arguments. Direct non-full producer invocation likewise rejects any output at
or below that root.

The parent process must be the recorded `/bin/bash` executable with exactly
three argv entries: bash argv0, the wrapper path, and `full`. The wrapper path
argument must itself be absolute and resolve to the tracked wrapper, and no option, prefix, suffix, or
additional argument is allowed. Merely embedding the path and word `full` in
an arbitrary parent command such as `bash -c` does not satisfy entrypoint
attestation. The manifest retains both `parent_executable` and the exact argv.

The producer records pre-START and end power observations. If the operating
system exposes battery state and either point is unplugged, the run fails
closed. If observation is unavailable, the evidence says so; even two plugged
point observations cannot prove uninterrupted AC between them. This limitation
remains explicit in the manifest and report.

After formal registration, no source, contract, ledger, trace, semantic index,
or retained evidence may be edited while the run is active. Any material
protocol change requires a new contract version and experiment identity before
another confirmatory result is observed.
