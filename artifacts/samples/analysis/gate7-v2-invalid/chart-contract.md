# Chart contracts: Gate 7 v2 descriptive INVALID evidence

## Frozen systems matrix

- **Analytical question:** Which of the five frozen CARMA/LRU systems checks
  passed for each of the five paired seeds?
- **Supported takeaway:** 24 of 25 cells pass; only seed `20261001` p95 delta
  fails (`+26.230 ms` observed versus the frozen `<= +0.500 ms` bound).
- **Qualification:** All values are descriptive only because the formal attempt
  is `INVALID`; the chart does not state or imply a formal Gate 7 PASS or FAIL.
- **Family and variant:** Matrix & Cohort; annotated binary heatmap/matrix.
- **Data sufficiency and grain:** 25 cells at one paired-seed/check grain,
  derived from five complete seed blocks and 15 isolated full-path ONNX runs.
- **Renderer and outputs:** Reproducible Matplotlib static renderer; PDF, PNG,
  and SVG exports in this directory. The source canvas is 7.05 inches wide and
  all source text is at least 11.5 pt. PNG is the final visual-QA surface.
- **Title:** `Gate 7 v2 per-seed frozen systems checks`.
- **Subtitle:** States the 15-run full-path ONNX scope and that the view is
  descriptive only because the formal attempt is INVALID.
- **Palette:** Hard two-root cap—blue for pass and orange for fail, with dark
  charcoal cell outlines and neutral text/background.
- **Non-color distinction:** Every cell includes an explicit `PASS` or `FAIL`
  label plus its numeric observed value; column labels include each frozen
  bound.

## Equal-seed latency distributions

- **Analytical question:** What are the pooled latency distributions of CARMA,
  LRU, and LFU for the full request path and the post-embedding path when every
  frozen seed receives equal weight?
- **Supported takeaway:** The two panels expose distribution shape and tail
  behavior for all three policies under the same five-seed, 15-run workload.
  They support descriptive comparison only; no curve is a formal winner.
- **Qualification:** The figure is prominently labeled `INVALID /
  NONCLAIMABLE`. It cannot repair, replace, or override the formal result.
- **Family and variant:** Distribution; two-panel quantile-derived empirical
  CDF approximation. The full-request panel uses a linear latency axis; the
  more right-skewed post-embedding panel uses an explicitly labeled log axis.
- **Data sufficiency and grain:** The byte-bound source contains 45,000 request
  rows. Each policy has exactly 3,000 requests for each of five seeds (15,000
  observations/policy), so ordinary pooling is exactly an equal-seed empirical
  mixture. The curated table has 1,001 Type-7 quantiles per policy/metric on a
  0.001 probability grid (6,006 rows total).
- **Source identity:** `requests.jsonl` SHA-256
  `b23fe24164c7b11a7c164deff1b1f1e9571e41e39231bc04e269625f1eed6358`.
  The curated CSV repeats the source hash, row counts, pooling method, quantile
  method, and group counts on every row and is itself byte-bound by the
  analysis script.
- **Renderer and outputs:** Reproducible Matplotlib static renderer;
  `gate7-v2-latency-distributions.{pdf,png,svg}`. The source canvas is 7.05
  inches wide and all source text is at least 11.5 pt. PNG is the final
  visual-QA surface.
- **Title:** `Gate 7 v2 latency distributions — INVALID / NONCLAIMABLE`.
- **Subtitle:** States the quantile-derived ECDF method, equal-seed pool, five
  seeds, and 3,000 requests per seed/policy.
- **Palette:** Hard two-root cap plus neutral—blue CARMA, orange LRU, neutral
  gray LFU, with dark charcoal text and quiet neutral grid lines.
- **Non-color distinction:** CARMA is solid, LRU dashed, and LFU dotted; policy
  names are also present in the legend. The log-scaled panel is text-labeled.
