# CARMA / GPTCache project handoff

## Document purpose and reasoning boundary

This is the long-form engineering handoff for the CARMA course project. It is
written for a colleague who was not present during implementation and is
expected to ask detailed questions about scope, design, experiments, failures,
and evidence.

It contains the inspectable basis for the work: source and paper selection,
requirements, design rationale, alternatives, equations, implementation
contracts, test strategy, benchmark protocol, observed results, known
deviations, reproducibility controls, artifact provenance, exact commands, and
recommended next work. It does **not** disclose private hidden chain-of-thought.
Instead, every consequential conclusion is reconstructed as an auditable chain
of problem -> constraint -> decision -> evidence -> limitation.

The short version is:

- Baseline repository: `zilliztech/GPTCache` at commit
  `c59fb3a6152a4458b2a070ca183b61c4b614095f`.
- Baseline paper: *GPTCache: An Open-Source Semantic Cache for LLM
  Applications Enabling Faster Answers and Cost Savings*, NLP-OSS 2023,
  <https://doi.org/10.18653/v1/2023.nlposs-1.24>.
- Primary related paper: *SCALM: Towards Semantic Caching for Automated Chat
  Services with Large Language Models*, arXiv:2406.00025v1,
  <https://arxiv.org/abs/2406.00025>.
- Extension: CARMA, **Cluster-Adaptive Reuse and Miss-pressure Admission**, an
  opt-in deterministic online semantic admission and eviction policy.
- Branch: `feature/online-cluster-aware-cache`.
- Historical full-experiment commit:
  `28129f0b9785232827e741c0e2ff2bb96cc19423`.
- Previously verified source commit:
  `cfc7167e7604247432f980ed8fb54bc896364fe9`.
- Previously verified packaging commit:
  `e1775ccbe9b771182db9784085ebe665656be92e`.
- Published/private GitHub repository:
  <https://github.com/MatanGoldfarB/gptcache-online-policy>.
- Result: the implementation and reproducibility work are complete, but the
  research hypothesis is only partly supported. Gates 1, 3, 5, 6, and 8 pass;
  Gates 2 and 4 fail; Gate 7 is pending. The overall frozen audit is therefore
  `FAIL` under `fail > pending > pass`, even though the phase-shift result is
  strong and statistically supported.

Do not turn that last sentence into “the project failed.” The correct reading
is that the engineering deliverable is complete and several claims pass, while
two preregistered research claims were falsified and one was not fully
adjudicable. Keeping those negative and pending results visible is part of the
quality of the submission.

## 1. What the lecturer required

The project was built against the two lecturer PDFs stored outside this Git
repository. Their requirements were converted into the repository-local
charter and completion audit rather than being left as informal recollection.
The authoritative mappings are:

- `docs/project/project-charter.md`
- `docs/project/completion-audit.md`
- `docs/project/baseline-justification.md`
- `docs/project/report.pdf`

The weighted rubric is:

| Criterion | Weight | What had to be demonstrated |
| --- | ---: | --- |
| Correctness | 40% | A working enhancement, compatibility, focused and upstream tests, and correct cache/storage behavior |
| Reproducibility | 30% | Pinned environment, repeatable scripts, Docker/CI path, machine-readable results, and retained evidence |
| Performance | 15% | Fair comparisons, repeated workloads, parameters/ablations, metrics, and statistically defensible interpretation |
| Clarity | 15% | Baseline justification, README instructions, design explanation, figures, limitations, and an 8--12 page report |

That weighting materially shaped the work. Correctness and reproducibility were
treated as hard engineering requirements rather than being sacrificed to
obtain a better benchmark chart. The performance claim was preregistered so a
bad result could not be silently redefined after measurement.

The concrete lecturer-facing deliverables now present in the repository are:

- feature implementation and isolated feature-branch history;
- unit, property, failure-path, protocol, analyzer, and SQLite/FAISS tests;
- a one-page baseline/paper justification;
- a frozen experiment contract, protocol-deviation ledger, and machine audit;
- deterministic synthetic, integration, MOSS, and QQP tooling;
- pinned dependencies, an immutable container definition, CI, and a one-command
  evidence gate;
- curated, checksum-sealed result artifacts and publication figures;
- an 8--12 page final PDF report and its LaTeX source;
- a requirement-by-requirement audit and an unpublished upstream PR draft.

## 2. Why GPTCache was selected

### 2.1 Selection criteria

The baseline needed to be open source, locally runnable, sufficiently mature to
be meaningful, modular enough to extend, and measurable without paid APIs or a
GPU. GPTCache meets those conditions:

- it separates embedding, vector search, similarity evaluation, scalar
  storage, vector storage, and eviction;
- its SQLite plus FAISS path works locally on CPU;
- it already has LRU, LFU, FIFO, and random in-memory eviction policies;
- the selected baseline commit has relevant upstream tests and an end-to-end
  storage path that can be exercised without contacting an LLM;
- adding an opt-in policy is a bounded, reviewable systems extension rather
  than a full rewrite.

The baseline was pinned to
`c59fb3a6152a4458b2a070ca183b61c4b614095f`. Pinning matters because “GPTCache”
as a moving default branch would make interface behavior, tests, and measured
results unstable.

### 2.2 The specific gap in GPTCache

At the pinned commit, the in-memory eviction manager sees opaque row IDs and
applies a single global count-bounded policy. LRU knows recency. LFU knows
lifetime frequency. Neither knows whether:

- several entries occupy the same redundant paraphrase region;
- a first-seen candidate is likely a one-shot scan item;
- a burst of misses indicates a growing topic;
- an old hot topic should lose capacity after demand shifts.

That gap gives a concrete systems question: can semantic structure guide what
is retained without weakening the answer-matching threshold or depending on an
offline corpus analysis?

### 2.3 Alternatives and why they were not chosen

The project intentionally did not copy FreCoS, SmartEvict, or SCALM directly.

- FreCoS uses fixed cluster metadata plus freshness and regeneration cost.
  CARMA does not use freshness or token cost as policy inputs.
- SmartEvict learns a policy from recency, cost, hit, idle-time, and staleness
  features. CARMA has no trained model.
- SCALM performs corpus-oriented semantic pattern discovery and ranking.
  CARMA learns bounded semantic structure online from the request stream.

These exclusions are important for novelty and feasibility. They keep the
extension CPU-only, credential-free, deterministic, and directly integrable
with GPTCache’s existing eviction interface.

## 3. What knowledge came from the papers

### 3.1 GPTCache paper

The GPTCache paper establishes the baseline idea and system decomposition:
queries are embedded, candidate cache entries are retrieved by vector
similarity, a similarity evaluator decides whether reuse is safe, and stored
responses can reduce latency and model cost. For this project, the most
important engineering consequence is separation of concerns:

- retrieval similarity decides whether a cached answer is acceptable;
- an eviction policy decides which stored entries remain available;
- the eviction policy must never redefine semantic correctness.

CARMA therefore uses clusters only for admission and retention. A CARMA topic
or cell is not an answer-equivalence class and cannot turn a retrieval miss into
a hit.

### 3.2 SCALM paper

SCALM supplies the research motivation that semantic patterns can be useful
cache-management signals. It analyzes conversational corpora, derives
hierarchical patterns, ranks them using cache-hit and token-saving evidence,
and evaluates replay over MOSS/LMSYS-style data.

The useful transfer was the *principle* that semantic coverage and demand
structure matter. The deliberate departure is execution model: CARMA learns
topics, cells, reuse support, and miss pressure online, under strict bounds,
without offline priority ranks.

### 3.3 Derived research question

The project’s question became:

> Can a count-bounded, deterministic policy discover reusable semantic
> structure during the request stream and retain coverage better than global
> LRU/LFU, without offline ranking, a learned model, token cost as an input, or
> a paid LLM call?

The hypothesis predicted gains on skewed, scan-heavy, and phase-shifting
workloads at identical capacity, embeddings, lookup threshold, and request
order. It also required no material increase in false hits or cache-path cost.

## 4. CARMA at a conceptual level

CARMA stands for **Cluster-Adaptive Reuse and Miss-pressure Admission**. It
combines five ideas:

1. Online coarse topics group broad regions of the embedding space.
2. Fine cells group closer paraphrase-like residents within a topic.
3. Lazy exponential decay lets recent demand replace obsolete lifetime demand.
4. Topic quotas allocate finite capacity according to decayed demand and miss
   pressure.
5. Ghost cells retain bounded evidence about rejected candidates so the second
   similar miss can be distinguished from a one-shot scan.

The policy is opt-in. Existing LRU/LFU/FIFO/random behavior and the default LRU
path are unchanged.

## 5. CARMA state and equations

The implementation is in `gptcache/manager/eviction/carma.py`.

### 5.1 Bounded state

For cache capacity `C`, CARMA keeps:

- at most `min(64, C)` topics;
- at most `2C` cells, including empty ghost cells;
- one entry record per resident;
- normalized centroids for topics and cells.

An entry stores its row ID, topic ID, cell ID, decayed hit mass, statistics
tick, insertion tick, and last-hit tick. A topic stores its centroid, resident
IDs, cell IDs, decayed demand, decayed miss mass, and update tick. A cell stores
its centroid, topic owner, resident IDs, decayed semantic support, update tick,
and last-seen tick.

These bounds make memory independent of trace length. That is a real
requirement: retaining every past embedding would make an apparently clever
online policy an unbounded history store.

### 5.2 Logical time and decay

One logical tick occurs for each cache hit or inserted miss. A value `x` last
updated at tick `t0` is evaluated at tick `t` as:

```text
x(t) = x(t0) * 2 ** (-(t - t0) / demand_half_life)
```

The default half-life is `10 * maxsize`; infinity disables decay. Values are
decayed lazily when touched, so ordinary hits remain constant-time apart from a
periodic quota refresh.

The purpose is not cosmetic smoothing. Lifetime LFU can remain dominated by a
topic that was historically popular but is now cold. Decay is the component
expected to restore adaptability after a phase change.

### 5.3 Online assignment

An insertion embedding is normalized. It joins the highest-cosine topic if
similarity is at least `topic_threshold`; otherwise it creates a topic while
the topic bound allows. It then applies the same match/create rule to a cell
using `cell_threshold`.

Centroids use a fixed exponential moving average:

```text
centroid' = normalize((1 - alpha) * centroid + alpha * embedding)
```

with default `alpha = 0.05`.

All ties are deterministic. Topic and cell matches choose the lower state ID
after equal similarity. Stable order also governs quota remainders, donor
selection, victim selection, ghost pruning, and restore order.

### 5.4 Demand, miss pressure, and quotas

For topic `c`:

```text
p_c = (miss_mass_c + 1) / (demand_c + 2)
w_c = max(epsilon, demand_c * p_c) ** quota_strength
```

The default `quota_strength = 0.5` applies diminishing returns. Setting it to
zero gives equal weight to active topics.

Quotas are integer and sum exactly to capacity. Each selected active topic
receives one slot where possible, and the remaining slots are assigned by the
largest-remainder method. Quotas refresh on a full-cache miss and at least once
every 32 events.

Why combine demand with miss pressure? Demand alone favors already-served hot
topics. Miss pressure supplies evidence that a region is under-covered. The
Laplace-style constants keep a new topic finite and avoid division by zero.

### 5.5 Retention value

For resident entry `e` in cell `s`:

```text
V_e = support_s / max(1, residents_s) + 0.25 * hit_mass_e
```

The support share rewards a semantic region that has been repeatedly observed,
while division by resident count discounts redundant entries in the same cell.
The hit term preserves direct evidence that a specific resident is useful.

### 5.6 Admission and replacement

Below capacity, a valid candidate is admitted. At capacity:

1. If the candidate cannot obtain a threshold-matching/new cell because the
   bounded cell registry is full and no ghost is reclaimable, reject it as
   `reject_cell_capacity`.
2. If the candidate’s decayed cell support is below `1.5`, reject it and retain
   its now-empty cell as ghost history.
3. If quota-aware admission is enabled and its topic has zero quota, reject it.
4. If the candidate topic is under quota, select the most over-quota donor
   topic; otherwise compete within the candidate topic.
5. Select the weakest eligible resident by retention value.
6. Admit only if candidate value is at least `1.05 * victim_value`.

The threshold `1.5` has a precise interpretation under decay: one observation
has support 1; two observations no more than one half-life apart reach at least
1.5. A provisional value of 2.0 was corrected before benchmark results because
two decayed observations are strictly below 2.0.

The incoming row already exists in scalar/vector storage when the policy makes
this decision. Rejection therefore returns that same row ID as a victim and
requests immediate physical cleanup.

### 5.7 Deterministic tie-breaking

The ordering is part of correctness, not an implementation detail:

- topic/cell match: highest cosine, then lowest state ID;
- quota remainder: largest fractional remainder, highest weight, lowest topic
  ID;
- donor topic: largest quota excess, lowest weight per resident, lowest topic
  ID;
- victim: lowest retention value, oldest hit, oldest insertion, stable row-ID
  representation;
- ghost pruning: lowest decayed support, oldest observation, lowest cell ID.

There is a recorded `seed` for experiment identity, but the policy uses no
random tie-breaking.

Custom object IDs use encounter ordinals only while resident or while the
current batch is settling. The map is compacted after each successful batch.
This detail matters: a pre-publication audit found that the first implementation
retained ordinals for every historical rejected/evicted custom ID, which made
memory and rollback-copy cost grow with trace length. The publication source
fixes the leak and adds a 1,000-unique-ID boundedness regression.

## 6. Integration with GPTCache

### 6.1 Factory path

`gptcache/manager/eviction/manager.py` recognizes `policy="CARMA"` and creates
`ClusterAdaptiveEviction`. `gptcache/manager/factory.py` copies policy
parameters rather than mutating the caller’s dictionary. `SSDataManager` binds
its own storage cleanup callback when it constructs CARMA.

Binding internally avoids a subtle danger: a user-supplied eviction object
could exist without being connected to `SSDataManager._clear`, making logical
eviction diverge from persistent storage.

### 6.2 Metadata path

CARMA declares `accepts_embedding_metadata = True` and receives insertion IDs
plus normalized embeddings through `put_with_metadata`. Legacy `put(ids)`
still exists for interface compatibility. At a full cache, missing, zero,
non-finite, or dimensionally inconsistent embeddings fail safely by rejecting
the candidate when admission is enabled.

### 6.3 Immediate scalar/vector cleanup

CARMA declares `requires_immediate_cleanup = True`. `SSDataManager` therefore
does not wait for the older batched cleanup threshold. This is essential with
FAISS `top_k=1`: a rejected but undeleted vector could remain the nearest
neighbor and mask the valid resident behind it.

The storage manager performs vector deletion before scalar commit. Recovery is
targeted so an earlier successful tombstone is not accidentally restored when
a later deletion fails. If reconciliation cannot restore a consistent state,
the eviction policy is marked unhealthy and subsequent operations fail closed.

### 6.4 Batching and exactly-once callbacks

`put_with_metadata` validates the whole batch before the first transition. It
captures a policy snapshot, settles every item, deduplicates victims, and
filters out any ID re-admitted later in the same batch. Only final non-residents
reach the storage callback.

The callback executes while an operation-level lock blocks concurrent hits and
inserts, but outside the internal state lock to avoid callback re-entrancy
deadlocks. If it raises, CARMA restores the policy snapshot, records the
failure, marks itself unhealthy, and re-raises.

This two-lock design exists because merely calling an external callback “after
unlocking” is not enough: another operation could otherwise interleave and
invalidate the victim set before deletion completes.

### 6.5 Restart reconstruction

CARMA declares `requires_embedding_restore = True`. On startup,
`SSDataManager` reads existing scalar rows and embeddings, orders them
deterministically by persisted access time and stable ID, and reconstructs
topics, cells, and residents without counting restore operations as live demand
or misses.

Resident structure survives restart, but adaptive demand and entry hit mass do
not. They reset to zero and relearn. Persisting them would need a versioned
metadata schema plus migration rules across GPTCache backends. The current
project reports that limitation rather than inventing state that was never
stored.

## 7. Safety and failure invariants

The important invariants are:

- resident count never exceeds `maxsize`;
- topic quotas are nonnegative and sum to capacity;
- every cell belongs to exactly one topic;
- every resident’s topic/cell ownership is internally consistent;
- scalar and vector stores converge on the same live ID set;
- each final victim is sent to cleanup exactly once;
- a batch failure cannot leave partial policy mutation silently accepted;
- invalid embeddings cannot cause capacity overflow;
- callback/recovery failure creates explicit unhealthy fail-stop behavior;
- snapshot and replay order are deterministic.

Representative adversarial cases covered by tests include:

- invalid configuration and malformed embeddings;
- unhashable IDs and IDs with colliding text representations;
- equal-similarity and equal-value ties;
- duplicate puts and unknown hits;
- ghost capacity exhaustion and topic recycling;
- multiple batch items displacing and then re-admitting the same ID;
- callback exceptions;
- vector deletion failing after scalar soft deletion;
- rollback that must preserve earlier tombstones;
- close/reopen reconstruction;
- randomized operation sequences checked against a simpler reference model.

The implementation is not “correct” merely because unit tests pass. It is
correct within the supported SQLite/FAISS path because the tests also exercise
the real scalar/vector lifecycle and validate final active vectors.

## 8. Test architecture

The verification stack has four layers:

1. Focused CARMA unit and property tests.
2. Relevant upstream GPTCache eviction and SQLite/FAISS regression tests.
3. Deterministic CI-sized traces with fixed schemas, hashes, and duplicated
   replay checks.
4. Fresh-process SQLite/FAISS integration runs with storage integrity checks.

Key paths:

- `tests/unit_tests/eviction/test_carma.py`
- `tests/project_tests/test_carma_integration.py`
- `tests/project_tests/test_carma_failure_paths.py`
- `tests/project_tests/test_carma_integration_protocol.py`
- `tests/project_tests/test_carma_protocol_remediation.py`
- `tests/project_tests/test_carma_phase_metrics.py`
- `tests/project_tests/test_carma_analyze_results.py`
- `tests/project_tests/test_reproducibility_evidence.py`
- `tests/project_tests/test_qqp_wrapper.py`
- `tests/project_tests/test_moss_benchmark.py`
- `tests/integration_tests/test_sqlite_faiss_onnx.py`

The exact verifier requires a frozen project-test manifest rather than
discovering arbitrary new tests. That makes evidence stable and prevents an
accidental missing test file from being interpreted as a shorter successful
suite.

The last fully packaged exact-source gate recorded 8 upstream eviction tests,
2 SQLite/FAISS tests with 1 optional case deselected, and 164 passing project
tests with 2 skipped. The publication source adds focused bounded-registry,
cell-capacity, constructor-validation, and seeded reference-victim regressions,
so its count is higher. Always read the current status JSON instead of copying
a count from prose.

## 9. Synthetic benchmark geometry

The synthetic workload is controlled by independent ground-truth concept IDs.
Embedding similarity can create a raw cache hit, but only matching concept IDs
make it a valid hit. This prevents a high raw hit rate from hiding unsafe
semantic reuse.

The constructed cosine hierarchy is:

```text
same concept                         1.00
deliberate near-twin wrong answers   0.96
different concepts in one cell      0.88
other topic-local relation           0.72
different topics                    <= 0.12
```

The primary synthetic answer threshold is `0.97`, which separates the
deliberate 0.96 hard negatives. A separate exploratory threshold-0.95
diagnostic intentionally triggers them to verify false-hit accounting. That
diagnostic is not used for tuning or for the main safety claim.

The full catalog reuses orthogonal cell-local feature blocks across topics. It
preserves the frozen cosine hierarchy in 834 dimensions instead of using more
than 8,000 dimensions. The benchmark measures extrema and aborts if geometry
drifts. This is a scalability device for the CPU grid, not a claim that the
vectors are natural-language embeddings.

## 10. Frozen workloads and metrics

### 10.1 Workloads

- **Stationary Zipf:** 10,000 requests, popularity exponent 1.2, and 15%
  one-shot concepts. Purpose: stable skew and non-regression.
- **Category shift:** five phases of 2,000 requests; 80% of demand moves to a
  new hot topic set and 20% remains background. Purpose: adaptation.
- **Pollution scan:** 3,000 hot-working-set requests, 4,000 unique requests,
  then 3,000 requests returning to the original hot set. The hot set is
  `0.8 * capacity`. Purpose: scan resistance and recovery.
- **Novel-long MOSS:** unique requests sampled from the longest quartile and
  stratified into 32/128/256/512-token buckets. Purpose: expected-miss,
  recorded-response, and token accounting.
- **QQP safety:** component-disjoint labeled question pairs. Purpose: calibrate
  answer similarity without leakage and protect the semantic-safety claim.

Primary synthetic comparisons use capacity 100, 10,000 requests, and ten
paired seeds. Capacity sweeps use capacities 20, 50, 100, and 200. Real-system
comparisons use five fresh processes per policy.

### 10.2 Metrics

```text
valid_hit_rate = valid_hits / requests
hit_precision = valid_hits / (valid_hits + false_hits)
false_hit_rate = false_hits / requests
opportunity_recall = valid_hits / reuse_opportunities
safe_token_saving_ratio = tokens attached to valid hits / all tokens
scan_return_valid_hit_rate = valid hits in return phase / return requests
```

False hits save zero valid tokens. System measurements include mean, p50, p95,
p99 latency, throughput, CPU time/utilization, sampled RSS/USS, disk/I/O where
available, admissions, rejections, evictions, and storage integrity.

Recovery lag is the earliest start of a contiguous window covering 10% of a
phase whose VHR reaches 90% of the reuse-opportunity rate in that phase’s final
quarter. Unattained targets are right-censored at the full phase length.

## 11. Tuning and statistics

### 11.1 Independent answer and policy calibration

Answer matching and CARMA clustering are deliberately separate.

QQP answer thresholds range from 0.80 through 0.99 in steps of 0.01. The
selection rule is the lowest calibration threshold whose one-sided 95% Wilson
precision lower bound is at least 0.99.

CARMA’s 96-point validation grid is:

- `topic_threshold`: 0.70, 0.80, 0.90;
- `cell_threshold`: 0.95, 0.97;
- `demand_half_life`: 100, 500, 2000, infinity;
- `quota_strength`: 0, 0.5, 1.0, 2.0.

Fixed values are `admission_margin=1.05`,
`ghost_support_threshold=1.5`, `centroid_alpha=0.05`, and
`entry_hit_weight=0.25`.

Three validation seeds and three 2,000-request workloads select the highest
workload-normalized VHR subject to zero synthetic false hits. Ties use the
frozen parameter order. The selected configuration was:

```text
topic_threshold       0.70
cell_threshold        0.97
demand_half_life      500
quota_strength        1.0
```

Test seeds were disjoint and were not used for retuning.

### 11.2 Statistics

- policies are paired by exact trace hash;
- deltas are computed per seed;
- confidence intervals use 10,000-resample paired bootstraps;
- preregistered comparisons use exact paired Wilcoxon tests;
- primary p-values are Holm-corrected;
- paired rank-biserial effect sizes are reported;
- Wilson intervals are required for semantic precision/rates where specified.

The stronger baseline is chosen per seed and metric as `max(LRU, LFU)` before
the CARMA-minus-baseline delta is formed. Comparing only against the weaker
baseline would overstate the contribution.

## 12. The eight frozen gates

| Gate | Criterion | Final adjudication |
| ---: | --- | --- |
| 1 | Relevant tests pass and integrity failures are zero | **PASS**, claimable |
| 2 | QQP precision >= 0.99, Wilson lower >= 0.98, and CARMA FHR upper delta <= 0.001 | **FAIL**, claimable negative result |
| 3 | Shift VHR gain >= 2 pp, CI above zero, Holm p <= .05 | **PASS**, claimable |
| 4 | Scan-return VHR gain >= 5 pp, CI above zero, Holm p <= .05 | **FAIL**, claimable negative result |
| 5 | Stationary VHR lower CI no worse than -1 pp | **PASS**, claimable |
| 6 | Safe-token lower CI no worse than -0.5 pp in each primary workload | **PASS**, claimable |
| 7 | Latency/throughput/RSS within frozen limits | **PENDING**, diagnostic only |
| 8 | Two clean Docker runs have identical non-timing logs and trace hashes | **PASS**, claimable |

The machine-readable authority is
`artifacts/samples/analysis/gate-audit.json`. Prose must agree with it. The
overall status is fail because the audit precedence is fail, then pending,
then pass; passing most gates cannot erase a failed preregistered hypothesis.

## 13. Main synthetic results

Ten-seed means at capacity 100:

| Workload | LRU VHR | LFU VHR | CARMA VHR | CARMA vs. stronger |
| --- | ---: | ---: | ---: | ---: |
| Stationary | 43.457% | 47.514% | 54.636% | +7.122 pp |
| Category shift | 63.376% | 52.181% | 68.261% | +4.885 pp |
| Pollution, whole trace | 58.400% | 59.198% | 59.200% | +0.002 pp |
| Pollution, return phase | 97.333% | 99.993% | 100.000% | +0.0067 pp |

### 13.1 What passed

The category-shift result passes Gate 3: +4.885 percentage points, paired 95%
CI `[4.677, 5.088]`, Holm-adjusted `p=0.0234375`.

Stationary non-regression passes with a lower CI of +6.572 points. Safe-token
deltas also pass in all three primary workloads:

- stationary: +9.503 pp, CI `[8.641, 10.443]`;
- category shift: +5.251 pp, CI `[5.009, 5.475]`;
- pollution: +0.0015 pp, CI `[0, 0.0037]`.

### 13.2 What failed

The scan-return trace is a useful negative result. CARMA achieves 100%, but LFU
already achieves 99.993%, missing only two of 30,000 return requests across the
ten seeds. The delta is +0.0067 points, CI `[0, 0.0167]`, Holm `p=1.0`, far
below the frozen +5 point threshold. The workload does not distinguish CARMA
from a very strong LFU baseline.

Do not claim “CARMA solved scan pollution” from the 100% value. The correct
claim is that it protected the hot set, but did not materially outperform the
stronger baseline under the frozen trace.

## 14. Ablations and what they mean

Mean VHR at capacity 100:

| Policy | Stationary | Category shift | Pollution |
| --- | ---: | ---: | ---: |
| Full CARMA | 54.636% | 68.261% | 59.200% |
| One global topic | 54.983% | 67.874% | 59.200% |
| No decay | 55.108% | 59.980% | 59.200% |
| No admission | 53.025% | 67.850% | 55.388% |
| No quota-aware eviction | 54.983% | 67.874% | 59.200% |

The clearest causal signal is decay: removing it costs 8.281 points on category
shift while slightly helping stationary traffic. This matches the intended
trade-off between adaptability and stable-frequency accumulation.

Admission matters most on pollution: disabling it costs 3.812 points.

One-topic and no-quota have identical observable VHR/eviction behavior on this
geometry despite different internal topic counts. Their individual effects are
therefore not separately identifiable here. Do not use the ablation table to
claim that quota or clustering independently causes the measured gain.

Recovery-lag evidence is also weak: CARMA averages 1,808 requests versus the
2,000-request censoring limit for both baselines, but 31/40 CARMA transitions
and all 40 baseline transitions are censored. The exploratory exact Wilcoxon
`p=0.0625` is not a confirmatory success.

## 15. Real SQLite/FAISS system results

The integration benchmark uses the real `SSDataManager`, SQLite, and FAISS
path. Each policy runs in a fresh process on the same 3,000-request pollution
trace. It verifies immediate physical deletion and performs a final round-trip
check of every active vector.

Across five seeds per policy:

- false hits: zero;
- stale FAISS candidates: zero;
- unknown answer IDs: zero;
- final scalar/vector counts: equal.

Five-seed means of each process summary:

| Policy | Mean | p50 | p95 | p99 |
| --- | ---: | ---: | ---: | ---: |
| LRU | 1.804 ms | 1.032 ms | 3.511 ms | 4.602 ms |
| LFU | 1.724 ms | 0.953 ms | 3.433 ms | 4.181 ms |
| CARMA | 1.723 ms | 0.875 ms | 3.469 ms | 3.952 ms |

CARMA adds about 354 microseconds to policy-only p95, which is consistent with
bounded cosine assignment and linear victim selection. Post-embedding request
p95 ranges from 0.936x to 1.032x LRU because each policy creates a different
mix of hits, admissions, and physical cleanup. The worst per-seed throughput
ratio is 0.997x LRU; the worst peak-RSS ratio is 1.043x, +4.8 MiB. Mean one-core
CPU utilization is 96.9% for CARMA versus 94.1% for LRU.

### Why Gate 7 is still pending

The frozen protocol specified the `paraphrase-albert-onnx` embedding on CPU,
but the executed integration runs replay precomputed 2,982-dimensional
synthetic vectors. The measured “total” starts at cache search, so embedding
generation is excluded. The contract also did not freeze a rule for combining
five seeds into one Gate 7 decision.

Every seed meets the numerical diagnostic thresholds, but the formal gate
cannot be promoted to pass. These are real post-embedding cache-path and
storage measurements, not full ONNX request-path measurements.

The artifacts retain only each process’s mean and quantiles, not individual
request latencies. The figure therefore shows the cross-seed distribution of
p95 summaries, not a reconstructable request-level CDF.

### Prospective Gate 7 ONNX remediation now implemented

Do not confuse this new implementation with the historical run above. The old
Gate 7 entry stays pending forever under its original evidence identity. A new
contract in `docs/project/gate7-remediation-contract.md` defines a separately
named `gate7b-onnx-v1` follow-up that can end in pass, fail, invalid, or pending.

The implementation consists of:

- `benchmarks/carma/gate7_trace.py`: calibration-only real-text QQP trace
  construction;
- `benchmarks/carma/onnx_integration_benchmark.py`: isolated GPTCache adapter,
  ONNX, SQLite, FAISS, timing, and resource runner;
- `benchmarks/carma/gate7_audit.py`: independent offline evidence verifier and
  adjudicator; and
- `scripts/run_onnx_integration_benchmark.sh`: reproducible smoke/full wrapper
  that runs the auditor after the producer completes.

#### Frozen comparison

- Contract SHA-256:
  `e93b3f301373a0b1a1c9fa99378f555bd45b9c6e8f8717ac82ea817763ecdf4a`;
  both the producer and independent auditor reject any other bytes.
- Five seeds: `20261001` through `20261005`.
- Policies: LRU, LFU, CARMA; capacity 100; `clean_size=1`; `top_k=1`.
- Cosine answer threshold: 0.97.
- CARMA: topic 0.70, cell 0.97, half-life 500, quota 1.0, ghost support
  1.5, admission margin 1.05, centroid alpha 0.05, entry-hit weight 0.25.
- Pinned tokenizer/model revisions and ONNX file SHA-256 are checked before a
  real run. Formal mode additionally verifies the exact prepared QQP pair,
  text, and archive hashes.
- CPU execution only, 512 tokens, one ONNX intra-op and inter-op thread, and
  one BLAS/tokenizer worker setting throughout.
- The all-seeds rule is intentionally conservative: every seed must satisfy
  both latency limits, the throughput limit, and both RSS limits.

The policy sequence is counterbalanced prospectively rather than always
running CARMA last:

| Seed | First | Second | Third |
| ---: | --- | --- | --- |
| 20261001 | CARMA | LFU | LRU |
| 20261002 | LFU | LRU | CARMA |
| 20261003 | LRU | CARMA | LFU |
| 20261004 | LRU | LFU | CARMA |
| 20261005 | CARMA | LRU | LFU |

#### Real request path

The measured call now enters `gptcache.adapter.adapter.adapt`; it is not a
manual manager-only replay. For every request the path is:

```text
raw QQP prompt
  -> GPTCache pre-embedding normalization
  -> pinned tokenizer and ONNX inference
  -> mean pooling and L2 normalization
  -> GPTCache DataManager FAISS search and SQLite lookup
  -> cosine threshold decision
  -> LRU/LFU/CARMA hit or admission/eviction callback
  -> deterministic recorded miss response or cached answer
  -> fully materialized response returned by adapt
```

There is no network or paid/live LLM call. On a miss, the local handler returns
`recorded-response:<concept_id>` and GPTCache saves it through its normal adapter
callback. This preserves cache behavior while preventing remote generation
latency from overwhelming the comparison.

#### Trace construction

Each seed receives 3,000 requests from the QQP calibration split only:

- 900 requests over 80 hot ground-truth concepts;
- 1,200 distinct scan concepts; and
- 900 returns with exactly the warm-phase hot multiplicities.

Concepts use their lexicographically smallest text ID. Candidate and phase
orders use domain-separated SHA-256 rankings. The serialized
`traces/seed-<seed>.jsonl` file is retained and its file hash is the trace hash.
All three policies for a seed receive that same exact file. Held-out Gate 2
rows are read only far enough to inspect the split label, then skipped; their
endpoint IDs, concepts, and labels are never selected, resolved through the
text catalog, embedded, timed, or used.

#### Isolation and timing

Every seed-policy run loads and warms its own pinned model in a new child,
creates a new SQLite database and FAISS index, and constructs a new eviction
object. The 20 warm-ups are outside the request timer and happen before storage
creation. Formal mode refuses a dirty or uncommitted worktree, fake embedding,
wrong seed/configuration, provider fallback, wrong embedding dimension, wrong
model/QQP hash, false hit, stale vector, unknown answer, or capacity violation.

Every request retains exclusive nanosecond fields for text preprocessing and
tokenization, ONNX inference, embedding postprocessing, FAISS search/mutation,
SQLite read/write, similarity decision, policy work, response return, residual,
and total latency. LRU and LFU `put` calls pass through the same measured policy
boundary as CARMA's `put_with_metadata`. Nested physical cleanup is charged to
SQLite/FAISS, not double-counted as policy-exclusive time. Raw request text is
not repeated in `requests.jsonl`; source text IDs and normalized-text hashes are.

Before the mandatory RSS start snapshot, the child allocates every fixed-key
request dictionary and physically touches one contiguous
`requests x embedding_dimension` float32 evidence matrix. Each request only
mutates its reserved row and copies its embedding into that matrix, so growing
evidence lists cannot bias one policy's sampled peak. It does no JSONL write or
`psutil` call inside a request timer. The parent samples RSS, VMS, USS, CPU,
threads, and available I/O every 100 ms plus mandatory start/end snapshots.
Timestamps must be strictly increasing, bracket the request loop, and have no
gap over 200 ms. The formal memory value is the maximum external sampled RSS;
lifetime OS high-water RSS is retained only as a startup-inclusive diagnostic.

`response_return_ns` includes the materialization callbacks and the measured
propagation from the final callback through the actual `adapt` return. Tokenizer
timing includes conversion into the ONNX input arrays. Every named stage gets a
whole-run nearest-rank summary, plus separate summaries for the four required
outcomes: hit, admitted without eviction, admitted with eviction, and rejected.

#### Evidence and independent audit

One successful full invocation produces:

```text
artifacts/gate7-onnx-attempts/
  attempt-ledger.jsonl             # hash-chained START/TERMINAL events
  attempt-<UTC>-<pid>/
    manifest.json
    runs.csv                       # 15 child summaries
    requests.jsonl                # 45,000 request rows
    resources.jsonl               # external process samples
    outcome-latency.jsonl         # whole run + 4 outcomes per child
    traces/seed-20261001.jsonl    # one retained trace per seed
    ...
    gate7-preterminal-adjudication.json # immutable, terminal-bound audit
    gate7-adjudication.json        # later independent offline audit
```

Formal attempts are serialized by an interprocess lock. Before results exist,
the producer appends a `START` event with the attempt ID, UTC start, Git HEAD,
output directory, and predecessor-list hash. The manifest binds its exact ledger
prefix as well as the clean start/end Git HEAD, historical baseline,
source/container/lock/contract hashes, model/tokenizer identity, exact config,
planned/actual order, child/storage identities, run summaries, and every
artifact's row count, byte count, and SHA-256. While retaining the root lock,
the producer invokes the independent auditor, saves an immutable preterminal
report, and appends a `TERMINAL` event binding the exact manifest, report,
verdict, and auditor identity. Historical eligibility uses that bound verdict,
so a later auditor revision cannot retroactively free another attempt.
Claimability requires the whole
retained attempt root—not a copied attempt directory—because the auditor checks
the ledger and every declared predecessor. Each child and the supervisor bind
to the same source snapshot before and after execution. The auditor does not
import the producer. It independently recomputes hashes,
semantic valid/false hits from returned concepts, timing identities and derived
fields, whole-run and outcome nearest-rank p50/p95/p99, throughput from
monotonic loop boundaries, resource/CPU/I/O summaries and cadence, the retained
trace from calibration source files, process/storage isolation, structural
counters, and each paired gate boolean.

The first-valid-attempt guarantee is scoped to this one complete, continuously
retained checkout root. It cannot establish global uniqueness across separately
created clones or after deleting the entire root; that stronger claim needs a
lecturer-issued token or protected external append-only ledger.

If any post-registration step fails before manifest publication, the incomplete
attempt retains `attempt-failure.json`, every trace already created, and all
resource samples accumulated across completed and active children. Child
stdout/stderr are retained when the error originated in a child process. The
attempt is invalid, never silently retried, and cannot be numerically
adjudicated; a retry uses a new attempt ID and reruns the entire five-seed,
three-policy matrix.

If the manifest was already published but the independent preterminal audit,
post-audit integrity check, or `TERMINAL` append fails, the producer does not
rewrite that manifest into a cleaner failure. Its unmatched `START` is a
deliberate fail-stop and blocks every later formal attempt until an external
forensic resolution versions the protocol.

Use:

```bash
# Fast orchestration test; fake embedding, therefore always pending.
scripts/run_onnx_integration_benchmark.sh smoke artifacts/gate7-onnx-smoke

# Confirmatory matrix; requires clean committed source and several CPU hours.
scripts/run_onnx_integration_benchmark.sh full
```

The fake three-policy smoke bundle audits as `PENDING` with zero errors and
warnings. A separate pinned-model development smoke confirmed the exact model
hash, CPU provider, 768-dimensional output, real adapter call, evidence schema,
and zero structural failures. These development runs are not numerical Gate 7
evidence. At roughly 0.45 seconds per request in the observed real-model smoke,
the frozen 45,000-request matrix is expected to take multiple hours on this Mac.
Until that clean full run exists and the auditor returns `PASS` or `FAIL`, the
prospective result is still pending and no full-path overhead claim is allowed.

## 16. MOSS recorded-response replay

The MOSS source is `OpenMOSS-Team/moss-003-sft-data` at revision
`42e216d3e3fb331c18d5fa6e7cb4f1c53eef24a4`, CC-BY-4.0:
<https://huggingface.co/datasets/OpenMOSS-Team/moss-003-sft-data>.

The benchmark performs a full streaming CRC-verified pass over 301,332 source
conversations and 823,634 actual turns, then retains the lowest stable-hash
2,048-turn sample. The frozen 200-request longest-quartile trace contains
557,243 context, prompt, and answer tokens.

All 200 requests are unique by design, so every request is an expected miss and
the exact recorded answer is replayed. There are no live model calls and no
false hits. Two runs produce byte-identical manifests, pool, requests, and CSV.

The source exposed two real data anomalies before any result was produced:

- 30,000 rows use sentinel `conversation_id=-1`;
- 2,727 rows declare a turn count that differs from the contiguous turn keys.

The parser disambiguates negative sentinel IDs with a SHA-256 of canonical row
JSON and treats contiguous `turn_1..turn_k` keys as authoritative while
retaining declared and actual counts. The manifest records both anomalies.

MOSS is a recorded-response and expected-miss negative control. It is not a
natural-language CARMA-vs-baseline semantic-hit comparison.

## 17. QQP semantic-safety result

QQP source pairs come from the full archive distributed in the GPTCache
repository. Positive pairs are unioned into concept components. Splitting is by
entire component, never individual row, so paraphrase-connected questions
cannot leak across calibration and test. Negatives whose endpoints collapse
into the same positive component are removed and counted.

The calibration set has 7,729 pairs: 5,652 positive and 2,077 negative. None of
the 20 thresholds from 0.80 to 0.99 met the prerequisite rule. The strongest
Wilson-lower-bound row was threshold 0.96:

```text
TP = 684
FP = 17
TN = 2060
FN = 4968
precision = 0.97574893
recall = 0.12101911
one-sided 95% Wilson precision lower = 0.96420782
```

This misses both the 0.99 point-precision and 0.98 lower-bound criteria. No
answer threshold was selected. All 56,963 component-disjoint test pairs are
recorded as not evaluated. There is no held-out precision or false-hit claim.

The code did compute held-out cosine similarities transiently in the same pass
before the calibration abort, but it did not classify, aggregate, inspect, or
write held-out outcomes. Threshold selection read calibration scores only.
This is still recorded as a protocol deviation because “computationally
untouched” would be inaccurate.

Gate 2 therefore fails at the calibration prerequisite. It is not acceptable
to lower the precision rule after observing this result.

## 18. Historical protocol deviations

`docs/project/protocol-deviations.md` is the authoritative ledger. Six issues
were discovered after the full synthetic/integration/QQP execution:

1. The historical synthetic bundle omitted promised per-request and resource
   JSONL. It has validation/run/aggregate CSV and metadata only.
2. Synthetic policies ran in fixed rather than randomized order. State was
   isolated and timing disabled, limiting the likely bias but not erasing the
   deviation.
3. Synthetic false-hit intervals used seed bootstrap rather than Wilson. They
   are descriptive and cannot substitute for QQP’s Wilson safety rule.
4. Secondary controls and the request-count scale sweep were not completed.
   Capacity sweeps and component ablations were completed.
5. Integration timing used precomputed vectors, not the specified ONNX
   embedding; Gate 7 is diagnostic/pending.
6. QQP held-out cosine similarities were computed before the calibration
   abort, although no held-out decision metric was produced.

Prospective code remedies were added where safe, but the old artifact bundle
was never rewritten or augmented and then mislabeled as original evidence.
Historical results remain bound to commit
`28129f0b9785232827e741c0e2ff2bb96cc19423` and the source hashes recorded in
its metadata.

This distinction between “future code is fixed” and “past execution complied”
is one of the most important audit concepts in the project.

## 19. Reproducibility design

### 19.1 Exact environment

The primary target is exactly CPython 3.12.13, CPU, SQLite, and FAISS.

- `requirements-project.txt` is the reviewed exact-pin input.
- `requirements-project.lock` authenticates every permitted wheel with
  SHA-256.
- `requirements-python38.lock` protects the older GPTCache syntax slice.
- `requirements-benchmark.lock` pins the optional full-analysis environment.
- `scripts/generate_hashed_locks.py --check` validates lock regeneration.

The editable project install uses `--no-index --no-deps --no-build-isolation`
after the authenticated dependency lock, so `setup.py` cannot silently alter
the audited environment.

### 19.2 Offline tokenizer closure

Tiktoken 0.14.0 otherwise lazily downloads the `cl100k_base` merge table. The
exact 1,681,126-byte object is committed at:

`assets/tiktoken-cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4`

with SHA-256:

`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`

The verifier accepts only the source-bound host cache or root-owned image cache,
checks exact path/size/hash before import, and disables package-index access
before runtime tests. This closes a subtle “tests pass only because the network
was available” gap.

### 19.3 One-command verifier

`scripts/verify_project.sh` checks Python, dependency consistency, baseline
ancestry, tokenizer cache, focused upstream tests, the frozen project-test
manifest, and the deterministic CI benchmark.

`scripts/run_reproducibility_gate.sh` is the higher-level evidence producer. It:

1. requires a clean exact Git `HEAD`;
2. creates one deterministic Git archive of that source commit;
3. builds a fresh hashed host environment from the archive;
4. runs the verifier from the extracted archive, not the live checkout;
5. builds a pinned `linux/amd64` Docker image from the same archive;
6. runs two distinct fresh containers with no network, all capabilities
   dropped, `no-new-privileges=true`, an unprivileged user, and one artifact
   mount;
7. compares benchmark artifacts, trace hashes, and normalized logs byte for
   byte;
8. writes status JSON last and refuses to overwrite evidence.

The Dockerfile pins the multi-architecture digest of
`python:3.12.13-slim-bookworm`, uses an allowlisted build context, and installs
only authenticated wheels.

### 19.4 Evidence cannot be casually moved between commits

Host and container status files record:

- exact source commit;
- reproducible source-archive digest and tracked-file count;
- dependency lock and installed-package hashes;
- benchmark artifacts and hashes;
- Docker image/configuration/runtime inspection;
- normalized log and paired-run identities.

The analyzer recomputes the Git archive and checks these references. A copied
JSON file is not enough to claim a different source passed.

## 20. Source commit versus packaging commit

This is a non-obvious but critical rule.

Evidence is produced from a clean exact source commit `S`. The repository may
then have at most one direct, non-merge child `P` used solely to package report
and evidence. The analyzer recognizes `P` only if every changed path is inside
the narrow packaging allowlist implemented in
`benchmarks/carma/analyze_results.py` and documented in
`docs/project/reproducibility.md`.

Allowed packaging changes are limited to selected report documents, analysis
outputs, the sample README, and additions under selected evidence paths. Code,
tests, locks, workflows, experiment contracts, raw experiment bundles, and the
root README are not allowed in `P`.

Why this exists: a report needs generated evidence that cannot exist before the
source has been run, but allowing arbitrary post-verification edits would make
“tested commit” meaningless.

For each handoff/publication refresh, `handoff.md` belongs in the new source
commit `S`; the exact host/container gate is run from that clean commit. The
curated publication evidence is then added in exactly one permitted child `P`
under a dated directory in `artifacts/samples/verification/`, with its own
checksum manifest. At published `HEAD`, the analyzer must report
`verified_packaging_descendant` for Gates 1 and 8. Discover the exact pair with:

```bash
git rev-parse HEAD       # packaging commit P
git rev-parse HEAD^      # verified source commit S
git show --stat --oneline HEAD
```

Any later commit makes that binding nonclaimable until a new exact-source gate
is run. A colleague should branch from the published head for development and
treat new results as a new evidence generation.

## 21. Important repository paths

| Path | Purpose |
| --- | --- |
| `gptcache/manager/eviction/carma.py` | CARMA state, assignment, decay, quotas, admission, victim selection, rollback |
| `gptcache/manager/eviction/manager.py` | Opt-in CARMA construction |
| `gptcache/manager/factory.py` | Data-manager and policy parameter wiring |
| `gptcache/manager/data_manager.py` | Embedding metadata, immediate cleanup, restart rebuild, recovery/fail-stop |
| `tests/unit_tests/eviction/test_carma.py` | Unit and property behavior |
| `tests/project_tests/` | Integration, failure, protocol, analyzer, MOSS, QQP, and evidence tests |
| `benchmarks/carma/synthetic.py` | Controlled catalog and workload builders |
| `benchmarks/carma/runner.py` | Deterministic simulation and request accounting |
| `benchmarks/carma/full_experiment.py` | Validation, selection, tests, sweeps, and ablations |
| `benchmarks/carma/integration_benchmark.py` | Fresh-process SQLite/FAISS system benchmark |
| `benchmarks/carma/qqp.py` | Leakage-controlled QQP preparation/calibration |
| `benchmarks/carma/moss.py` | Pinned MOSS preparation and recorded replay |
| `benchmarks/carma/statistics.py` | Bootstrap, Wilcoxon, Holm, effects |
| `benchmarks/carma/analyze_results.py` | Artifact verification, gate adjudication, figures |
| `scripts/verify_project.sh` | Single deterministic project verifier |
| `scripts/run_reproducibility_gate.sh` | Exact-source host plus paired-container evidence |
| `scripts/analyze_carma_results.sh` | Final analyzer wrapper |
| `Dockerfile.project` | Pinned unprivileged offline-verification image |
| `.github/workflows/carma-ci.yml` | Immutable-reference host/container CI |
| `docs/project/experiment-contract.md` | Frozen protocol and gates |
| `docs/project/protocol-deviations.md` | Post-execution deviation ledger |
| `docs/project/policy-design.md` | Concise algorithm design |
| `docs/project/reproducibility.md` | Environment and provenance contract |
| `docs/project/completion-audit.md` | Lecturer rubric mapping |
| `docs/project/report.pdf` | Final 12-page lecturer report |
| `artifacts/samples/` | Curated checksum-sealed evidence bundle |

## 22. Exact commands for a colleague

Run from the repository root.

### 22.1 Inspect identity first

```bash
git status --short --branch
git log -1 --format=fuller
git merge-base --is-ancestor \
  c59fb3a6152a4458b2a070ca183b61c4b614095f HEAD
git remote -v
```

Do not run the evidence gate from a dirty worktree.

### 22.2 Create the exact environment

```bash
python3.12 -I -c \
  'import platform, sys; assert sys.version_info[:3] == (3, 12, 13), platform.python_version()'
python3.12 -I -m venv .venv
.venv/bin/python -I -m pip --isolated --disable-pip-version-check install \
  --no-input --index-url https://pypi.org/simple \
  --require-hashes --only-binary=:all: \
  --requirement requirements-project.lock
.venv/bin/python -I -m pip --isolated --disable-pip-version-check install \
  --no-input --no-index --no-deps --no-build-isolation --editable .
.venv/bin/python -I -m pip check
```

### 22.3 Fast local verification

```bash
PATH="$PWD/.venv/bin:$PATH" bash scripts/verify_project.sh
```

To retain the CI benchmark output:

```bash
CARMA_ARTIFACT_DIR=/absolute/new/output/path \
  PATH="$PWD/.venv/bin:$PATH" \
  bash scripts/verify_project.sh
```

The output directory must be fresh where the called writer requires it.

### 22.4 Deterministic CI benchmark only

```bash
.venv/bin/python -m benchmarks.carma --output /tmp/carma-ci
```

Fast smoke profile:

```bash
.venv/bin/python -m benchmarks.carma \
  --workloads stationary phase_shift pollution_scan novel \
  --policies LRU LFU CARMA CARMA_NO_CLUSTER \
  --requests 80 --capacity 16 \
  --output /tmp/carma-smoke
```

### 22.5 Complete exact-source evidence gate

```bash
evidence_root="$(mktemp -d "$PWD/../carma-repro-evidence.XXXXXX")"
CARMA_EVIDENCE_ROOT="$evidence_root" \
CARMA_PYTHON="$PWD/.venv/bin/python" \
  bash scripts/run_reproducibility_gate.sh
```

Expected status files:

```text
$evidence_root/ci/host-verification.json
$evidence_root/container-reproducibility.json
```

The writer intentionally refuses to overwrite retained evidence. Use a fresh
root for every attempt and keep failed partial output for diagnosis.

### 22.6 Analyze the frozen full evidence

The uncurated full local directories are ignored because some are large. If
they are available at the documented paths:

```bash
scripts/analyze_carma_results.sh \
  --full-dir artifacts/carma-full-20260826 \
  --integration-dir artifacts/integration-full-20260901 \
  --integration-dir artifacts/integration-full-20260902 \
  --integration-dir artifacts/integration-full-20260903 \
  --integration-dir artifacts/integration-full-20260904 \
  --integration-dir artifacts/integration-full-20260905 \
  --qqp-result artifacts/qqp-full/evaluation/result.json \
  --moss-dir artifacts/moss-full \
  --host-verification /path/to/evidence/ci/host-verification.json \
  --container-reproducibility /path/to/evidence/container-reproducibility.json \
  --output /tmp/carma-analysis
```

The published compact bundle is under `artifacts/samples/`. Verify it with:

```bash
(cd artifacts/samples && shasum -a 256 -c SHA256SUMS)
```

Also verify the current publication-refresh sub-manifest:

```bash
(cd artifacts/samples/verification/publication-20260826-ci-refresh && \
  shasum -a 256 -c SHA256SUMS)
```

### 22.7 Open the report

The final report is `docs/project/report.pdf`; its source is
`docs/project/report.tex`. If recompilation is necessary and Tectonic is
installed:

```bash
(cd docs/project && tectonic --keep-logs --keep-intermediates report.tex)
```

Re-render and visually inspect every page after any report change. A successful
LaTeX exit code does not guarantee readable figures or an 8--12 page layout.

## 23. Commit history as an engineering narrative

The branch deliberately records small stages rather than one opaque final
commit. Important milestones are:

- `1afea17`: initial CARMA implementation;
- `c55579d`: frozen reproducible evaluation pipeline;
- `b88a31a` through `28129f0`: provenance, workload alignment, MOSS, and full
  trace-capacity preflight;
- `831cc88`: artifact verifier and result rendering;
- `bf50838`: prospective remediation for protocol-audit gaps;
- `3360575`: explicit integration-embedding deviation;
- `0265689` onward: verification adjudication and provenance binding;
- `ce0d5bf` onward: exact host/container evidence production and hardening;
- `1adb173`: audited project report assembly;
- `cfc7167`: final verified source for the first complete evidence package;
- `e1775cc`: restricted direct-child report/evidence packaging.

The publication audit then found and fixed two issues before the refreshed
source gate: historical custom-ID ordinals were not pruned, and a full cell
registry could fall through to a below-threshold nearest cell. The fixes bound
the ordinal registry to live residents, compact its sequence, reject
unassignable cells explicitly, and add regression/reference tests. The exact
fix commit is the verified source parent of the published packaging `HEAD`.
The deterministic default CI manifest, 15,600-row request log, and 16-row run
table remained byte-identical to the previously packaged evidence, confirming
that the fixes close non-default/adversarial behavior without rewriting the
historical default benchmark outcome.

Later micro-commits are not noise. They document specific failure modes found
during adversarial verification: macOS shell portability, source archive
temporary-file handling, no-Git archive isolation, missing lock validator in
the image, runtime tokenizer network closure, immutable offline probe source,
and exact Docker security-option attestation.

Use this command to inspect the whole sequence:

```bash
git log --reverse --format='%h %ad %s' --date=short \
  c59fb3a6152a4458b2a070ca183b61c4b614095f..HEAD
```

## 24. Known limitations and prohibited overclaims

Do not claim any of the following from the current evidence:

- that all eight gates pass;
- that held-out QQP precision is known;
- that CARMA materially beats LFU on the frozen scan-return phase;
- that Gate 7 formally passes;
- that the system timings include ONNX embedding generation;
- that integration artifacts retain per-request latency samples;
- that MOSS demonstrates semantic CARMA hit-rate superiority;
- that synthetic vectors establish natural-language generalization;
- that clustering and quota effects are separately identified;
- that adaptive counters survive restart;
- that GPU, distributed, Redis, or other optional backends were validated;
- that the missing scale sweep and secondary controls were executed;
- that prospective fixes retroactively change the historical experiment.

Safe claims are narrower:

- CARMA is an implemented, opt-in, deterministic, bounded GPTCache policy.
- The supported SQLite/FAISS path has strong unit, failure, restart, and
  integrity evidence.
- Under the frozen controlled synthetic category-shift workload, CARMA improves
  VHR over the stronger paired LRU/LFU baseline by 4.885 points with a positive
  paired CI and Holm-corrected significance.
- Stationary VHR and safe-token non-regression gates pass.
- The QQP calibration prerequisite and scan-return improvement gate fail.
- Real post-embedding SQLite/FAISS measurements are favorable diagnostics, but
  the formal overhead gate remains pending.
- Exact-source host and paired network-isolated container verification pass for
  the attested source/package relationship.

## 25. Recommended next work

### 25.1 Highest-value research rerun

Design a new pollution workload *before* observing results. The current trace
saturates LFU near 100%, so a five-point advantage is structurally implausible.
A better preregistered workload could enlarge the hot set or interleave
recurrent cold items, while preserving fair pairing and preventing post-hoc
threshold changes.

### 25.2 Complete Gate 7 properly

The protocol, real GPTCache/ONNX runner, retained evidence, counterbalanced
order, and independent all-seeds adjudicator are implemented. The remaining
work is execution, not experimental design: commit a reviewed clean source
identity, run the full 15-child wrapper into a fresh ignored artifact root, keep
any failed partial attempt, and report the auditor's result without selectively
rerunning a favorable policy. A Linux replication remains valuable after the
same frozen Mac-host run, but it must use a separately identified machine block
rather than being pooled post hoc.

### 25.3 Semantic safety

The existing embedding/threshold family did not meet the QQP precision
prerequisite. Any next attempt should preregister a new embedding, evaluator,
or calibration method and use a new component-disjoint split identity. Never
reuse the held-out set for iterative tuning.

### 25.4 Policy engineering

- Persist adaptive state with a versioned backend-neutral schema and tested
  migrations.
- Replace `O(C)` victim scans with per-topic heaps or another deterministic
  indexed structure.
- Test multiple embedding models, dimensions, capacities, and Linux hosts.
- Add backend-specific validation for Redis/distributed configurations only
  after defining consistent rollback semantics.
- Design traces where topic pressures diverge enough to separate quota and
  clustering effects.

### 25.5 Reproducibility maintenance

Any code or contract change creates a new source identity. Regenerate hash
locks only from reviewed exact-pin inputs. Run the full exact-source gate from
a clean commit, store evidence in a new root, and preserve the source/package
relationship. Never edit a retained result bundle in place.

## 26. Troubleshooting guide

### Gate refuses to start because the tree is dirty

Inspect `git status --short --branch`. Do not stash/delete unfamiliar work
blindly. Commit the intended source or move unrelated local work safely, then
use a fresh evidence root.

### Evidence writer refuses to overwrite output

This is intentional. Failed runs retain partial logs. Choose a new directory
with `mktemp -d` and compare the old failure before discarding anything.

### Python version mismatch

The exact gate requires CPython 3.12.13, not merely Python 3.12. Use the
repository `.venv` only after confirming its interpreter version.

### Tiktoken tries to access the network

Check the committed cache file path, exact size/hash, and the environment value
selected by the verifier. Do not “fix” the gate by allowing runtime network
access; that would remove the offline-closure guarantee.

### Docker build/run succeeds but Gate 8 is absent

Look for both run inspections, normalized logs, artifact hash lists, and
`container-reproducibility.json`. The status JSON is written last. A build or a
single successful container is not paired reproducibility evidence.

### Analyzer makes Gates 1/8 nonclaimable

Check current `HEAD`, evidence source commit, worktree cleanliness, parentage,
and the exact diff from source to packaging commit. One extra documentation
commit or a disallowed path is enough to invalidate the binding.

### Integration count mismatch or stale FAISS result

Treat it as a correctness failure. Inspect vector-first deletion, scalar commit,
targeted recovery, active vector round-trip results, and unhealthy policy state.
Do not report the performance metrics from that run as valid.

### QQP returns `no_threshold_met_precision_gate`

That is a valid terminal negative result, not an exception to bypass. Do not
evaluate the held-out set or lower the frozen threshold rule in place.

## 27. How to answer likely colleague questions

**Why not use clusters to decide whether an answer is correct?**  Because
clustering is approximate and evolves online. GPTCache’s retrieval similarity
threshold remains the correctness boundary; CARMA only manages retention.

**Why two semantic levels?**  Topics support capacity allocation across broad
demand regions; cells estimate redundancy/reuse inside a region. One level
would mix those roles.

**Why keep rejected cells?**  Without bounded ghost history, every full-cache
miss looks like a first observation. A second similar miss would have no way to
demonstrate reuse pressure.

**Why is support divided by resident count?**  Ten residents in one cell should
not automatically be ten times more valuable than one representative. The
division rewards coverage and discounts redundancy.

**Why use logical ticks instead of wall time?**  Logical time is deterministic,
portable, and tied to workload events. Wall-clock decay would make replay and
container equality machine-dependent.

**Why can the research audit say FAIL when the implementation is complete?**
Because the audit represents preregistered claims, not task completion. Two
hypotheses failed and one gate is pending; hiding them would be worse science.

**Why is the report package a different commit from the verified source?**
Generated evidence can only exist after the source runs. A single restricted
child allows packaging while preventing arbitrary untested changes.

**Why not persist all adaptive state now?**  A correct implementation needs a
versioned schema and migrations across backends. Relearning from residents is
safer than silently serializing an unstable internal structure.

**Why use a synthetic benchmark at all?**  It supplies exact concept labels,
controlled similarity geometry, reproducible phase changes, and clean policy
isolation. Its role is causal/control evidence, not external-validity proof.

**Why include MOSS if every request misses?**  It validates pinned natural data,
long-prompt/token accounting, deterministic sampling, recorded response replay,
and expected-miss behavior without an API. It is explicitly a negative control.

## 28. Publication and collaboration state

The project is published as a **private** GitHub repository because this is
coursework. The upstream GPTCache remote remains available as `origin`;
the personal submission repository should use a separate remote such as
`submission` so nobody accidentally pushes to `zilliztech/GPTCache`.

Expected remote layout:

```text
origin      https://github.com/zilliztech/GPTCache.git
submission https://github.com/MatanGoldfarB/gptcache-online-policy.git
```

The feature branch and `main` point at the same final packaging commit for an
easy landing page, the full inherited GPTCache history is preserved, and all
41 inherited tags are published. On a personal GitHub repository, an outside collaborator can receive
write/push access but not owner/admin repository-management powers. True admin
access requires an organization-owned repository. Talya’s invitation therefore
must use the highest permission actually available on this personal repository.
The supplied username was confirmed as `talya189`. GitHub's collaborator API
reports that account as an active collaborator with `write` permission. This is
the highest ordinary outside-collaborator permission granted on this personal
repository; it permits clone, pull, branch, commit, and push workflows but is
not owner/admin repository-management authority.

The first hosted Actions run exposed one runner-availability issue before any
compatibility test executed: GitHub's Python manifest has no 3.8.20 x64 build
for `ubuntu-22.04`. The project workflow therefore pins the available Python
3.8.18 build for that floor-version slice. This changes only the hosted
interpreter patch version; the Python 3.8 compatibility claim and hashed
dependency lock remain the same.

After publication, verify rather than assume:

```bash
git ls-remote submission refs/heads/main \
  refs/heads/feature/online-cluster-aware-cache
git rev-parse HEAD
```

Then confirm in GitHub that the remote branch SHA equals local `HEAD`, the
repository is private, and the collaborator invitation/permission status is
the requested one.

## 29. Final handoff checklist

- [x] `git status` is clean at handoff.
- [x] Published `HEAD` is the direct packaging child of the exact verified
  source commit.
- [x] Post-packaging analyzer reports `verified_packaging_descendant`.
- [x] Gates 1/3/5/6/8 pass, 2/4 fail, and 7 is pending in the current audit.
- [x] Prospective Gate 7 contract, real-text trace, GPTCache/ONNX runner,
  independent auditor, and wrapper are implemented and tested.
- [x] Fake-embedding and pinned-real-ONNX development smoke paths complete
  structurally; the fake bundle independently audits as pending with no errors.
- [ ] The prospective five-seed, 15-child full ONNX bundle has completed and
  received a claimable independent `PASS` or `FAIL` verdict.
- [x] Curated checksum manifests verify every listed file.
- [x] `docs/project/report.pdf` is present, 8--12 pages, and visually checked.
- [x] GitHub repository is private and both `main` and feature branch resolve
  to the expected final commit.
- [x] Talya (`talya189`) has active personal-repository `write` access; true
  owner/admin control would require an organization-owned repository.
- [x] No API key, credential, raw model weight, oversized raw source archive,
  or unlicensed data was committed.
- [x] Any future modification is treated as a new source/evidence generation.

If only one lesson is retained from this handoff, it should be this: every
performance statement in this project is conditional on an exact workload,
metric, comparator, commit, and evidence status. Preserve those conditions and
the work remains defensible; detach the numbers from them and it becomes easy
to overclaim.
