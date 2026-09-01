# Gate 7 v5 claimable PASS figure

This directory contains the report figure and source table generated directly
from the canonical formal adjudication at:

`artifacts/gate7-v5-onnx-attempts/attempt-20260831T165407Z-31743/gate7-adjudication.json`

The canonical status is `pass`, `claimable` is `true`, and the auditor records
zero errors and zero warnings. All five seeds (`20261201`--`20261205`) pass all
six frozen latency, throughput, and RSS checks. The separate observed semantic
guardrail is `PASS_OBSERVED`: all 25,400 hits are same-concept, with zero
cross-concept, labeled-negative, stale-candidate, or response-mismatch hits.

Files:

- `gate7-v5-frozen-checks.png`: the report-ready six-panel figure.
- `gate7-v5-seed-checks.csv`: exact per-seed values used by the figure.
- `build_gate7_v5_figure.py`: deterministic figure/table generator; it refuses
  a non-PASS or nonclaimable adjudication.

Rebuild from the repository root with:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  artifacts/samples/analysis/gate7-v5-pass/build_gate7_v5_figure.py
```

The figure is a view of the immutable formal result. It does not edit the
attempt, change a bound, or relabel any v1--v4 evidence.
