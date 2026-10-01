# Gate 7 v2 curated INVALID analysis

This directory contains a reproducible, **descriptive-only** analysis of the
preserved Gate 7 v2 formal attempt
`20260827T131743602373Z-557c6ac0578c` at source commit
`557c6ac0578cb6b77c5ae51595b49abdc0407e10` and annotated tag
`gate7c-onnx-v2-formal-source`.

The formal result is **INVALID**, not PASS or FAIL. The preterminal auditor
reported `status=invalid`, `claimable=false`, and seven errors. The producer
then exited with code 4 and the message `formal preterminal adjudication is
malformed`; the ledger therefore contains only canonical `PROTOCOL_GENESIS`
and `START` rows, with no `TERMINAL`. No ordinary
`gate7-adjudication.json` was produced. The wrapper exit observation comes from
the contemporaneous formal-run process output and is contextual; it is not a
field in the three copied evidence files.

## Evidence quality findings

- The retained Python identity declared an absolute resolved interpreter path,
  while the frozen auditor required the literal lexical path
  `.venv/bin/python`.
- All five warm-up declarations had an extra `rows` key; the frozen auditor
  required exactly `{bytes, sha256}`.
- One resource stream had a `202142875 ns` sampling gap, exceeding the frozen
  `200000000 ns` maximum by `2142875 ns`.
- The manifest's frozen producer auditor identity had `{bytes, sha256}`, while
  the preterminal report identity had `{path, bytes, sha256}`. This inequality
  triggered the producer's fail-stop validation before a terminal ledger row
  could be appended.

The retained matrix is complete—15 isolated full-path ONNX runs and 45,000
requests—but the integrity failures prohibit a formal systems conclusion.
Numerically, 24 of the 25 frozen CARMA/LRU cells pass. The only failed cell is
seed `20261001` p95 delta: `+26230125 ns` against the frozen `<= 500000 ns`
bound. This is descriptive evidence only.

The latency-distribution supplement is likewise descriptive. It uses the exact
preserved `requests.jsonl` identity
`b23fe24164c7b11a7c164deff1b1f1e9571e41e39231bc04e269625f1eed6358`
(45,000 rows). Each policy contributes 3,000 requests from each of five seeds,
so the pooled empirical mixture gives every seed equal weight and contains
15,000 observations per policy. For `request_total_ns` and
`post_embedding_total_ns`, `latency-quantile-ecdf.csv` records the 1,001-point
grid `p=0.000, 0.001, ..., 1.000` using Hyndman--Fan Type 7 interpolation
(`h=(n-1)p`). The plotted curves are therefore a reproducible quantile-derived
ECDF approximation; they are not a formal Gate 7 adjudication.

The independently recomputed semantic guardrail is separately `FAIL`:
`25400` same-concept hits, `3` direct-negative hits, and `2` unlabeled
cross-concept hits. Semantic status does not overwrite the systems status, and
neither descriptive systems values nor semantic results repair an INVALID
attempt.

## Reproduce

From the repository root, using an environment with Matplotlib, a fresh clone
can rerender the tracked curated table and snapshots with:

```bash
.venv/bin/python artifacts/samples/analysis/gate7-v2-invalid/analyze_gate7_v2_invalid.py
```

The separate curation command is local-only because its default input is the
268 MB ignored formal request stream, which is preserved on the experiment
machine but is not shipped in a fresh GitHub clone:

```bash
.venv/bin/python artifacts/samples/analysis/gate7-v2-invalid/curate_latency_distributions.py
```

That command reads the preserved request stream without editing it, verifies
its exact SHA-256 and 45,000-row policy/seed matrix, and deterministically
regenerates the tracked `latency-quantile-ecdf.csv`. The fresh-clone analysis
command verifies the exact SHA-256 identities of that curated table and the
three immutable snapshots under
`artifacts/samples/verification/gate7-v2-invalid/`. It also recomputes the
canonical ledger hashes and chain. It then regenerates:

- `summary.json` — status, identities, evidence profile, diagnostics, systems
  count, and semantic aggregate.
- `run-summary.csv` — one row per isolated run, including full-path latency,
  stage timings, throughput, memory, structure, and semantic fields.
- `per-seed-checks.csv` — the exact 25 paired CARMA/LRU frozen checks.
- `invalidity-findings.csv` — the three preterminal error families plus the
  producer terminal-binding mismatch.
- `gate7-v2-per-seed-frozen-systems-checks.{png,svg,pdf}` — a static 5 x 5
  matrix with exact values and non-color PASS/FAIL labels.
- `gate7-v2-latency-distributions.{png,svg,pdf}` — two quantile-derived ECDF
  panels for full-request and post-embedding latency, pooled with equal seed
  weight and prominently marked `INVALID / NONCLAIMABLE`.
- `SHA256SUMS` — a nested inventory of the curated inputs, scripts, summaries,
  and chart outputs in this directory; the checksum file intentionally excludes
  itself.

Both chart families use an approximately 7.05-inch-wide source canvas and no
source text smaller than 11.5 pt, so insertion at the report's full text width
preserves at least 10 pt effective typography.

Do not rerun a selected seed or policy. The preserved formal evidence remains
the authoritative historical attempt and must not be edited.
