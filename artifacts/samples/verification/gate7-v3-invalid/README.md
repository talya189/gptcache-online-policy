# Gate 7 v3 invalid-attempt preservation

This directory is a byte-for-byte, checksum-sealed teaching snapshot of the
adjudication-critical files from the first formal `gate7d-onnx-v3` attempt.
The full ignored attempt remains the authoritative raw evidence. This compact
copy does not add, repair, or imply a missing terminal event.

Exact identity:

- attempt: `20260827T222339037448Z-05543e34c9a5`;
- attempt directory: `attempt-20260827T222332Z-36292`;
- source commit: `05543e34c9a51e43d67ba483559d573bc3021dd0`;
- annotated source tag: `gate7d-onnx-v3-formal-source`;
- contract SHA-256:
  `70bd3eacc480d7a26a8d62d7a53f757fc1c45b09f955859d70c9e92aad85ccb2`.

The formal attempt is **operationally INVALID and nonclaimable**. Its ledger
contains exactly `PROTOCOL_GENESIS` and `START`, with no `TERMINAL`. The
separate immutable preterminal report is structurally clean and records a
claimable analytical **FAIL**: seed `20261001` exceeds the frozen full-request
p95 absolute-delta bound (`1,307,374 ns > 500,000 ns`). Its orthogonal semantic
guardrail is also **FAIL** (three direct-negative and two unlabeled
cross-component hits).

The deterministic terminalization defect is preserved in the bytes: the
manifest's `contract` object contains the three START binding fields plus
`publication_identity_unchanged`, while the bootstrap required exact equality
with the three-field START object. The historical attempt must never be
continued, terminalized after the fact, selectively rerun, or relabeled.

Verify this snapshot with:

```bash
(cd artifacts/samples/verification/gate7-v3-invalid && \
  shasum -a 256 -c SHA256SUMS)
```
