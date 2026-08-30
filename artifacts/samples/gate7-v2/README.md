# Gate 7 v2 audit

This directory is the machine-readable, post-report Gate 7 v2 adjudication.
It was generated from the five retained integration seed directories with:

```bash
scripts/run_gate7_check.sh artifacts/samples/gate7-v2
```

`gate-audit.json` records Gate 7 as `pass`: all five distinct full-mode seeds
pass every latency, throughput, and RSS check. The audit's top-level status is
`pending` only because this focused command intentionally does not supply the
other seven gates' artifacts. Their historical adjudication remains in
`artifacts/samples/analysis/gate-audit.json`.

The v2 scope is the post-embedding SQLite/FAISS cache path with deterministic
precomputed vectors. It does not include or claim ONNX embedding latency.
