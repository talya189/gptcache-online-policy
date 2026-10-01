# Immutable Gate 7 v2 INVALID evidence snapshots

These three files are exact byte copies of the retained formal Gate 7 v2
attempt evidence. They are included so the curated analysis can be reproduced
without reading or modifying the raw formal-attempt directory.

| Snapshot | Original retained path | SHA-256 |
|---|---|---|
| `attempt-ledger.jsonl` | `artifacts/gate7-v2-onnx-attempts/attempt-ledger.jsonl` | `84eee7963f4e5c27f8d962e31ddcdcec9779b1cd463a8952841946ffc8557f0c` |
| `manifest.json` | `artifacts/gate7-v2-onnx-attempts/attempt-20260827T131736Z-1784/manifest.json` | `7574127cce8f1c72e87a1528dc6709947860178f43667a92add155e89e1f1cf8` |
| `gate7-preterminal-adjudication.json` | `artifacts/gate7-v2-onnx-attempts/attempt-20260827T131736Z-1784/gate7-preterminal-adjudication.json` | `e3e8df2787049d3b1ea4e622e7666502b715ae572431b4f8d4e4b213c12dd39b` |

Verify the copies from this directory with:

```bash
shasum -a 256 -c SHA256SUMS
```

The preterminal report records `status=invalid`, `claimable=false`, and seven
errors. The ledger contains exactly two canonical rows—`PROTOCOL_GENESIS` and
`START`—and no `TERMINAL`. The producer subsequently exited with code 4 and
reported `formal preterminal adjudication is malformed`; this wrapper
observation was captured in the contemporaneous process output, not inside
these three files. No ordinary `gate7-adjudication.json` existed, so there is
no such snapshot here.

These files are immutable historical evidence. Do not edit them, do not use
them to resume the unmatched attempt, and do not selectively rerun any seed or
policy. Any future experiment must use a separately versioned protocol and a
new attempt identity.
