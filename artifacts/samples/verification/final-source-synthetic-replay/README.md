# Final-source synthetic replay audit

This compact record documents an independent full replay of the deterministic
synthetic experiment from the then-current report-only packaging commit
`defba4aa78b24cb7115352ed2d58177d227b60ba`. That commit is the direct
report-only child of verified source commit
`557c6ac0578cb6b77c5ae51595b49abdc0407e10`; the experiment and CARMA source
bytes are identical across the two commits. Later amendments to the same
report-only packaging child do not change those source bytes.

The replay used a clean worktree, Python 3.12.13, and the ordinary full-mode
entrypoint:

```bash
.venv/bin/python -m benchmarks.carma.full_experiment \
  --mode full \
  --output /fresh/empty/output-directory
```

It executed 5,180,400 policy-request evaluations. The four published summary
tables produced by the replay are byte-for-byte identical to the historical
files under `artifacts/samples/full/`; `replay-audit.json` records their exact
sizes and SHA-256 values.

The 4.1 GB replay request log and temporary output directory are intentionally
not copied into the repository. This is a post-publication reproducibility
check, not a retroactive replacement for the frozen experiment and not a Gate
7 result.
