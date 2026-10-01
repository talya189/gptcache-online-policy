# Curated project evidence

This directory is the compact, checksum-sealed evidence bundle referenced by
the final report. `SHA256SUMS` covers every file below except itself.

- `full/` contains the five untouched files from the preregistered synthetic
  run at commit `28129f0b9785232827e741c0e2ff2bb96cc19423`.
- `integration/<seed>/` contains `runs.csv`, `resources.jsonl`, and the checksum-linked
  manifest for each of five fresh SQLite/FAISS seeds.
- `moss/` contains the checksum-linked 2,048-turn pool, 200-request replay,
  manifests, result CSV, and attribution notice. The 393 MB source archive is
  excluded.
- `qqp/` contains only the prepared-data and embedding manifests, the complete
  calibration grid, the result, and the attribution notice. Source pairs,
  texts, model weights, and the embedding array are excluded. No threshold met
  the preregistered calibration precision rule, so held-out metrics were not
  produced.
- `analysis/` contains the ten deterministic final outputs: four figures in
  PNG and SVG form, `summary.csv`, and `gate-audit.json`.
- `verification/host/` and `verification/container/` preserve the relative
  topology of the clean-host and two fresh-container evidence for source commit
  `cfc7167e7604247432f980ed8fb54bc896364fe9`.

The final audit passes gates 1, 3, 5, 6, and 8; fails the preregistered gates 2
and 4; and leaves gate 7 pending because its frozen cross-seed adjudication rule
is undefined. The failed and pending gates are retained as results, not revised
after observation.

The ignored working directories under `artifacts/` remain the authoritative
full local runs. The curated files here were copied byte-for-byte from those
directories after analyzer reconciliation.
