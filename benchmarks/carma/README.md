# CARMA deterministic CI benchmark

This harness evaluates GPTCache's LRU and LFU policies, CARMA, and CARMA with
all embeddings forced into one topic (`CARMA_NO_CLUSTER`). It uses no model,
network, GPU, or paid API.

The generated embeddings have known cosine structure:

- different cells in one topic: `0.72`;
- different answer concepts in one cell: `0.88`;
- deliberately unsafe near-twin answers: `0.96`;
- the same labeled answer concept: `1.0`.

The independent `concept_id` determines whether a returned answer is valid.
Consequently, a high raw hit rate cannot hide false semantic reuse.
The CI default hit threshold is `0.97`, the lowest synthetic setting above the
deliberate `0.96` near-twin negatives. Run with `--hit-threshold 0.95` to verify
that the hard-negative fixture exposes unsafe false hits.

## CI command

Run all four bounded profiles and verify each non-timing replay twice:

```bash
python -m benchmarks.carma --output /tmp/carma-ci
```

Default profile sizes are:

| Workload | Requests | Purpose |
| --- | ---: | --- |
| `stationary` | 1,000 | Stable Zipf plus exactly 15% one-shot traffic |
| `phase_shift` | 1,500 | Five rotating 80% hot-topic phases |
| `pollution_scan` | 1,200 | `0.8C` hot set, unique scan, hot-set return |
| `novel` | 200 | Unique traffic and near-twin false hits |

For a faster smoke run:

```bash
python -m benchmarks.carma \
  --workloads stationary phase_shift pollution_scan novel \
  --policies LRU LFU CARMA CARMA_NO_CLUSTER \
  --requests 80 \
  --capacity 16 \
  --output /tmp/carma-smoke
```

`pollution_scan` needs enough catalog entries for its unique middle phase; the
default catalog supports all documented CI sizes.

## Timed run

Timing is deliberately disabled in deterministic CI mode. Enable it explicitly
for measured mean, p50, p95, p99, and throughput values:

```bash
python -m benchmarks.carma \
  --measure-latency \
  --skip-determinism-check \
  --output /tmp/carma-timed
```

Measured timings are machine-dependent. Every request field other than
`latency_ns`, and every summary field other than latency/throughput, remains
covered by `deterministic_digest`.

## Tunable CARMA parameters

The values below are the small deterministic **CI-harness defaults**, chosen
for bounded test coverage. They are not the published research operating
point. The research and Gate 7 configuration is `topic=0.70`, `cell=0.97`,
`demand_half_life=500`, and `quota_strength=1.0` (with the remaining values
unchanged), as frozen in the experiment and Gate 7 contracts.

```text
--cluster-similarity-threshold 0.70
--cell-threshold 0.88
--demand-half-life 128
--quota-strength 0.5
--ghost-support-threshold 1.5
--admission-margin 1.05
--centroid-alpha 0.05
--entry-hit-weight 0.25
```

Use `--demand-half-life infinity` for the no-decay setting. The
`CARMA_NO_CLUSTER` policy is the preregistered no-clustering ablation: it keeps
the same admission and quota machinery but forces all valid embeddings into a
single coarse topic.

## Artifacts

Each run overwrites three files inside the selected output directory:

- `requests.jsonl`: one labeled request record per policy/workload;
- `runs.csv`: aggregate valid/false hits, opportunity recall, safe token-saving
  ratio, admission/eviction counts, and latency percentiles;
- `manifest.json`: exact configuration, trace hashes, deterministic run IDs,
  and determinism scope.

Definitions:

- **valid hit:** returned answer has the request's `concept_id`;
- **false hit:** similarity lookup returned another concept's answer;
- **reuse opportunity:** the concept appeared previously in the trace;
- **opportunity recall:** valid hits divided by reuse opportunities;
- **safe token-saving ratio:** token cost of valid hits divided by total token
  cost; false hits never count as savings.

Capacity is measured in cache entries because that is the unit used by
GPTCache's in-memory eviction interface. All policies use `clean_size=1` so a
batched cleanup policy cannot receive an accidental capacity advantage.
