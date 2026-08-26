# CARMA Policy Design

## Name and scope

CARMA is **Cluster-Adaptive Reuse and Miss-pressure Admission**. It is an
opt-in, count-bounded eviction policy for GPTCache's `SSDataManager`. The first
supported storage combination is SQLite plus FAISS. Existing LRU, LFU, FIFO,
and random policies remain the defaults and retain their public API.

CARMA is deliberately online and CPU-only. It does not call an LLM, use token
cost as a policy input, train a model, or alter GPTCache's answer-matching
threshold.

## Event model and decay

One logical tick is recorded for each cache hit or inserted miss. For a value
`x` last updated at tick `t0`, its value at tick `t` is

```text
x(t) = x(t0) * 2 ** (-(t - t0) / demand_half_life)
```

The default half-life is `10 * maxsize`. Infinity disables decay. Lazy decay
keeps hit handling constant-time.

CARMA keeps three bounded state types:

- An entry stores its row ID, topic and cell IDs, decayed hit mass, insertion
  tick, and last-hit tick.
- A topic stores a normalized centroid, resident IDs, decayed demand, and
  decayed miss mass.
- A cell stores a normalized centroid, its topic, resident IDs, decayed
  semantic support, and last-seen tick.

There are at most `min(64, maxsize)` topics and `2 * maxsize` cells. Empty,
low-support cells are retained temporarily as bounded *ghost history*: a
second semantically similar miss can therefore challenge an incumbent even
when the first was rejected.

## Online assignment

An inserted normalized embedding joins the highest-cosine topic when the
similarity is at least `topic_threshold`; otherwise it creates a topic while
the topic bound permits. Within the topic it similarly joins or creates a cell
using `cell_threshold`. Matching ties use the lowest numeric state ID.
Centroids use a fixed exponential moving-average rate and are renormalized.

On a hit, topic demand, cell support, and entry hit mass increase by one. On a
miss, topic demand, topic miss mass, and cell support increase by one.

## Quotas and retention

For topic `c`, define recent miss pressure and quota weight as

```text
p_c = (miss_mass_c + 1) / (demand_c + 2)
w_c = max(epsilon, demand_c * p_c) ** quota_strength
```

`quota_strength=0` makes active topics equal; the default `0.5` applies
diminishing returns. Integer quotas are recomputed on full-cache misses and at
least every 32 events. Each active topic receives one slot where possible, and
remaining slots are assigned by largest remainder so quotas are non-negative
and sum exactly to capacity.

For resident entry `e` in semantic cell `s`, retention value is

```text
V_e = support_s / max(1, residents_s) + 0.25 * hit_mass_e
```

This rewards demonstrated reuse while discounting redundant residents in the
same semantic cell.

## Admission and replacement

Below capacity, an insertion is admitted. At capacity:

1. A below-threshold candidate is rejected if the bounded cell registry is full
   and no empty ghost cell can be reclaimed; it is never merged into an
   unrelated resident cell merely because the registry is full.
2. A candidate whose decayed cell support is below 1.5 is rejected; its empty
   cell remains as ghost history. A first observation has support 1, while two
   observations no more than one half-life apart reach at least 1.5.
3. A candidate whose topic quota is zero is rejected.
4. If the candidate topic is under quota, the donor is the most over-quota
   topic. Otherwise, the candidate competes inside its own topic.
5. The weakest eligible resident is selected by retention value.
6. The candidate is admitted only when its post-replacement value is at least
   `admission_margin` times the victim value (default `1.05`).

The newly inserted row participates in the decision, so rejection evicts that
same row. CARMA requests immediate scalar and vector deletion; this prevents a
rejected FAISS vector from masking a valid neighbor at `top_k=1`.

## Determinism and safety

- Topic/cell match: highest cosine, then lowest state ID.
- Quota remainder: largest fraction, then highest weight, then lowest topic ID.
- Donor: largest quota excess, then lowest weight per resident, then lowest
  topic ID.
- Victim: lowest retention value, oldest hit, oldest insertion, then stable
  row-ID representation.
- Ghost pruning: lowest decayed support, oldest observation, then lowest cell
  ID.

All mutable state is protected by a re-entrant lock. Storage callbacks execute
outside the lock and receive each victim exactly once. Missing, zero-length,
non-finite, or dimensionally inconsistent embeddings fail safely: the inserted
row is rejected once the cache is full, and capacity is never exceeded.

## Restart behavior

At startup, `SSDataManager` reconstructs topics and cells from embeddings
already stored in scalar storage. Restore events do not count as live demand or
misses. Resident support is rebuilt, but demand and hit mass intentionally
restart at zero and relearn online. This limitation is reported rather than
hidden; persisting adaptive statistics would require a versioned metadata
schema across GPTCache backends.

## Complexity

- Hit: `O(1)` excluding an occasional quota refresh.
- Miss assignment: `O((topics + cells) * embedding_dimension)` with bounded
  topic/cell counts.
- Transactional insertion snapshot and victim selection: `O(maxsize + cells)`
  state-copy work plus `O(maxsize)` victim selection. Centroid arrays are
  immutable-by-replacement and are shared by the rollback snapshot.
- Memory: `O((maxsize + cells) * embedding_dimension + maxsize + cells)`,
  bounded independently of trace length. Custom-ID tie ordinals are compacted
  to current residents after every insertion batch.

## Distinction from related systems

- SCALM derives hierarchical patterns from a corpus and initializes eviction
  priority from offline high/mid/low ranks. CARMA discovers bounded structure
  during replay and uses decayed miss pressure, quotas, and ghost admission.
- FreCoS combines fixed cluster metadata with freshness and regeneration cost.
  CARMA uses neither freshness nor cost.
- SmartEvict trains a dueling model over recency, cost, hits, idle time, and
  staleness. CARMA has no learned model and no cost feature.

The realistic claim is therefore conditional: CARMA should resist one-shot
scan pollution and adapt better than lifetime LFU after topic shifts. It is not
expected to dominate on every stationary or rapid-drift workload.
