# Curated publication-package checksum index

This is a report-only packaging index, not experimental evidence and not a
formal Gate 7 adjudication. It exists because the source-generation root
`artifacts/samples/SHA256SUMS` is immutable under the exact-source packaging
rule while later, separately labeled evidence generations are added below
`artifacts/samples/verification/`.

`SHA256SUMS` covers every current file under `artifacts/samples/` except the
package-wide manifest itself. It includes the frozen root manifest and all
nested manifests. Verify it from this directory with:

```bash
shasum -a 256 -c SHA256SUMS
```

This index proves byte inventory only. Each evidence package retains its own
scope, source identity, and claim status.
