# Curated project evidence

This directory is the compact evidence bundle referenced by the
final report. `SHA256SUMS` is generated only after the final host/container and
analysis gates finish.

- `full/` contains the five untouched files from the preregistered synthetic
  run at commit `28129f0b9785232827e741c0e2ff2bb96cc19423`.
- `integration/<seed>/` contains `runs.csv`, `resources.jsonl`, and the checksum-linked
  manifest for each of five fresh SQLite/FAISS seeds.
- `moss/` contains the checksum-linked 2,048-turn pool, 200-request replay,
  manifests, result CSV, and attribution notice. The 393 MB source archive is
  excluded.
- `qqp/` is reserved for final manifests, calibration/test outputs, and the
  result needed to audit the human-labeled safety check. Source pairs, model
  weights, and embeddings are excluded.
- `analysis/` currently contains deterministic report figures; final
  reconciliation adds the summary and gate audit.
- `verification/` is reserved for final clean-host and paired-container
  evidence.

The ignored working directories under `artifacts/` remain the authoritative
full local runs. The analyzer rejects incomplete, stale, or hash-mismatched
inputs; curated copies are checked byte-for-byte against those run directories
before commit.
