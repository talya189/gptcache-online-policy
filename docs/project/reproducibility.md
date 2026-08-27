# Reproducibility Guide and Validation Record

## Supported environment

The project target is exactly Python 3.12.13 on CPU with SQLite and FAISS. The
human-reviewed direct and transitive pins live in `requirements-project.txt`;
`requirements-project.lock` authenticates every allowed wheel with SHA-256.
Optional GPTCache backends are intentionally excluded because their upstream
lazy imports can run a bare `pip` against an interpreter other than the active
environment.

Tiktoken 0.14.0 otherwise fetches the `cl100k_base` merge table lazily from the
[official public encoding URL](https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken).
The exact 1,681,126-byte object is therefore retained at
`assets/tiktoken-cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4` with SHA-256
`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`.
This is offline execution closure only; it does not change or add a MOSS result.

Create a clean environment from the repository root with:

```bash
python3.12 -I -c \
  'import platform, sys; assert sys.version_info[:3] == (3, 12, 13), platform.python_version()'
python3.12 -I -m venv .venv
.venv/bin/python -I -m pip --isolated --disable-pip-version-check install \
  --no-input \
  --index-url https://pypi.org/simple \
  --require-hashes \
  --only-binary=:all: \
  --requirement requirements-project.lock
.venv/bin/python -I -m pip --isolated --disable-pip-version-check install \
  --no-input --no-index --no-deps --no-build-isolation --editable .
.venv/bin/python -I -m pip check
PATH="$PWD/.venv/bin:$PATH" bash scripts/verify_project.sh
```

The final editable-install command is deliberately dependency-free: only the
audited lock may define the environment. `requirements-python38.lock` protects
the floor-version compatibility job, and the flattened
`requirements-benchmark.lock` protects the optional full-analysis environment.
Regenerate all three from their exact-pin inputs with
`python scripts/generate_hashed_locks.py`, then review the lock diff.

The final local evidence gate creates its own temporary clean environment, so
the manual environment above is useful for development but is not itself Gate
1 or Gate 8 evidence.

## Verification contract

`scripts/verify_project.sh` is the single host and container entrypoint. It:

1. requires exactly Python 3.12.13 and a consistent installed dependency graph;
2. verifies that the feature branch descends from GPTCache commit
   `c59fb3a6152a4458b2a070ca183b61c4b614095f` when Git metadata is available;
3. selects only the source-bound host cache or root-owned image cache and
   verifies its single-file path, size, and SHA-256 before tiktoken is imported;
4. disables package-index access before tests, making any runtime lazy-install
   attempt fail visibly;
5. runs the focused upstream SQLite/FAISS regression slice;
6. requires and runs the frozen 19-file CARMA unit/integration test manifest;
   and
7. requires and calls `scripts/run_ci_benchmark.sh OUTPUT_DIR`.

Set `CARMA_ARTIFACT_DIR` to retain benchmark outputs. Without it, verification
uses a private temporary directory and removes it on exit.

From a clean committed checkout with an exact CPython 3.12.13 interpreter, run
the complete local evidence workflow with:

```bash
CARMA_PYTHON=/absolute/path/to/python3.12 \
  bash scripts/run_reproducibility_gate.sh
```

The workflow refuses a dirty checkout and materializes one deterministic Git
archive of the full current commit. Both the fresh hashed host installation and
verification and the `linux/amd64` Docker build use that same extracted tree,
never the live checkout. Before either status is published, the writer
reproduces the archive from Git, checks its SHA-256 record, and compares every
tracked byte and executable bit with the execution tree; unexpected files such
as ignored bytecode shadows are rejected. The two distinct fresh containers
must then run the exact verifier entrypoint. Retained Docker image and container
inspections bind the evidence to the image ID, platform, user, working directory,
entrypoint/arguments, network isolation, dropped capabilities, exact
`no-new-privileges=true`, single artifact bind mount, and successful non-OOM
exit. At least three successful pytest summaries are required. Only their
summary-line elapsed values are normalized; the rest of both logs must be
byte-identical.

The canonical output targets are `artifacts/ci/host-verification.json` and
`artifacts/container-reproducibility.json`. Their independently referenced
archive-digest records are `artifacts/ci/source-archive.sha256` and
`artifacts/source-archive.sha256`; the two retained run roots are
`artifacts/docker-run-1/` and `artifacts/docker-run-2/`. The writer never
overwrites evidence. Failed runs retain their partial logs for diagnosis, so a
rerun requires archiving the whole evidence root or selecting a fresh empty
root:

```bash
CARMA_EVIDENCE_ROOT="$(mktemp -d "$PWD/../carma-repro-evidence.XXXXXX")" \
CARMA_PYTHON=/absolute/path/to/python3.12 \
  bash scripts/run_reproducibility_gate.sh
```

When a custom root is used, its status files are at
`$CARMA_EVIDENCE_ROOT/ci/host-verification.json` and
`$CARMA_EVIDENCE_ROOT/container-reproducibility.json`.

The producer always binds evidence to a clean exact source commit `S`. Analyze
against `S` directly whenever possible. The only permitted self-packaging step
is one subsequent non-merge commit `P` whose direct parent is `S`. Its diff is
limited to the following report-only boundary:

- the repository-level `handoff.md`;
- existing or new versions of exactly `docs/project/report.tex`,
  `docs/project/report.pdf`, `docs/project/completion-audit.md`,
  `docs/project/reproducibility.md`, `docs/project/draft-pr.md`, and
  `docs/project/chart-map.md`;
- additions or modifications below `artifacts/samples/analysis/**` and to
  `artifacts/samples/README.md`; and
- additions only for `artifacts/samples/SHA256SUMS`,
  `artifacts/samples/qqp/**`, and `artifacts/samples/verification/**`.

The existing `artifacts/samples/qqp/NOTICE.md` is frozen. Experiment contracts,
raw full/integration/MOSS evidence, source, tests, workflow, Docker inputs,
locks, and repository `README.md` are outside this boundary. The analyzer must
report `verified_packaging_descendant` and the exact changed paths; any dirty
worktree, additional commit, deletion, rename, or disallowed path invalidates
the binding and requires a new run from a fresh root.

Consequently, the source-sealed root README's prospective Gate 7 command is
historical and must not be used after the completed-invalid v2 attempt; the
report and handoff contain the authoritative no-rerun/future-v3 rule. The
source-sealed baseline justification also overstates the MOSS path as
“SCALM-style”; the report and handoff correctly identify it as an exact-key
LRU all-miss negative control. These wording corrections are documented here
rather than placed in a disallowed packaging path.

## Final-source synthetic replay audit

To close the gap between the historical full synthetic run and the final
behavior-hardened source, an independent audit reran the complete deterministic
experiment from the then-current clean report-only packaging commit
`defba4aa78b24cb7115352ed2d58177d227b60ba`. Its direct parent is verified
source `557c6ac0578cb6b77c5ae51595b49abdc0407e10`, and the packaging diff changes
only allowlisted documentation/evidence paths.

The command was:

```bash
.venv/bin/python -m benchmarks.carma.full_experiment \
  --mode full \
  --output /fresh/empty/output-directory
```

The replay executed 5,180,400 policy-request evaluations. Its four published
summary outputs are byte-for-byte identical to `artifacts/samples/full/`:

| File | Bytes | SHA-256 |
|---|---:|---|
| `aggregate.csv` | 61,081 | `64974947bacd35dace1661a9ced515990ffcb131fc353b7d9ae6d13afddb564c` |
| `runs.csv` | 232,106 | `fc049a34792fd3f144e20861b00b6552fea2ca5c3896c5f7207704a777bb99db` |
| `validation.csv` | 10,433 | `dc121bdea10ad50e3c3e618048ebca1d51383bdbd5da444e81c52edac4bf4098` |
| `validation_runs.csv` | 572,151 | `1da8c2855fc7ab87df0ecf6bef2f546449c7e6861d678da1fd9ede92757cd6b3` |

The compact record and its checksum manifest are under
`artifacts/samples/verification/final-source-synthetic-replay/`. The temporary
4.1 GB request log is intentionally not published. This post-publication replay
confirms deterministic synthetic-summary reproducibility; it does not rewrite
the historical experiment and has no role in Gate 7 adjudication.

## Container and CI

`Dockerfile.project` pins the multi-architecture digest of
`python:3.12.13-slim-bookworm`, installs only the authenticated wheel lock from
the public PyPI index, copies and re-hashes the exact tiktoken cache into
root-owned `/opt/tiktoken-cache`, copies an allowlisted build context, and runs
as an unprivileged user. For a debugging run that is not paired Gate 8 evidence,
build and retain its artifacts with:

```bash
docker build --no-cache --pull \
  --platform linux/amd64 \
  --file Dockerfile.project --tag carma-project:local .
CARMA_CONTAINER_OUTPUT="$(mktemp -d "$PWD/../carma-container-output.XXXXXX")"
chmod 0777 "$CARMA_CONTAINER_OUTPUT"
docker run --rm --network none \
  --platform linux/amd64 \
  --cap-drop ALL --security-opt=no-new-privileges=true \
  --mount "type=bind,src=$CARMA_CONTAINER_OUTPUT,dst=/artifacts" \
  --env CARMA_ARTIFACT_DIR=/artifacts \
  carma-project:local
printf 'retained artifacts: %s\n' "$CARMA_CONTAINER_OUTPUT"
```

`.github/workflows/carma-ci.yml` repeats the same verification on Ubuntu 24.04
both directly and in the pinned container on every pushed commit and pull
request. A separate clean job installs `requirements-benchmark.lock` with
binary-only hash enforcement and runs `pip check`. The container job is
configured to run two fresh network-isolated instances, retain their outputs
and Docker inspections, derive pytest-only non-timing logs, compare all retained
non-timing data, and upload the paired evidence. GitHub Actions are referenced
by immutable commit hashes and checkout credentials are not persisted. These
controls passed for the v2 formal source commit and annotated-tag build. Every
later packaging commit still requires its own successful hosted run; hosted
status does not replace the retained exact-source host/container evidence.

A successful host job writes `artifacts/ci/host-verification.json` with schema
`carma-host-verification-v1`. A successful paired-container comparison writes
`artifacts/container-reproducibility.json` with schema
`carma-container-reproducibility-v1`. Each status file is created only after its
checks pass and names the retained logs, locks, artifacts, and SHA-256 values;
absence is not a passing result. Host install/package inventory and Docker
image/runtime inspection files are also hash-bound into these status files.
Each status additionally records the reproducible source-archive digest, source
commit, format, and tracked-file count; the analyzer checks that both status
files bind to the same exact archive.

## Gate 7 full-path ONNX evidence record

Gate 7 now has three deliberately separate histories. They answer related
questions but do not share an evidence identity:

| Generation | Evidence status | Reproducibility meaning |
|---|---:|---|
| Historical precomputed-vector integration | `PENDING`, diagnostic | Real SQLite/FAISS cache-path timing, but no ONNX embedding inside the request timer and no frozen across-seed adjudication rule |
| `gate7b-onnx-v1` | `INVALID`, nonclaimable | The first real-text child exposed a cross-concept return and the v1 runner stopped after conflating semantic disagreement with structural corruption; the retained failure cannot be repaired or selectively resumed |
| `gate7c-onnx-v2` | `INVALID`, nonclaimable | The complete 15-child full-path matrix exists, but its preterminal evidence check failed and the ledger never terminalized |

The historical precomputed evidence remains authoritative for its original
diagnostic and permanently remains `PENDING`. The v1 preservation manifest and
archive are `docs/project/evidence/gate7-v1-invalid-attempt.json` and
`docs/project/evidence/gate7-v1-invalid-attempt.tar.gz`. V2 lives under its own
formal root, `artifacts/gate7-v2-onnx-attempts/`, and links to those prior
identities without rewriting them.

### Frozen v2 identity and measured scope

The exact v2 source is
`557c6ac0578cb6b77c5ae51595b49abdc0407e10`. It is anchored by annotated tag
`gate7c-onnx-v2-formal-source`, tag object
`550e33a38ae59c992f25fc20023f5717c5fdd1ce`, and contract SHA-256
`4cb2d289bf9516e73bdc53c3f021ab9dc0e6b850c243e38bc57cc49244573f30`.
The attempt is `20260827T131743602373Z-557c6ac0578c`; its retained directory is
`artifacts/gate7-v2-onnx-attempts/attempt-20260827T131736Z-1784/`.

All 15 children exited zero: five seeds (`20261001` through `20261005`) by LRU,
LFU, and CARMA, each in a clean process. Each seed used one identical retained
3,000-request real-text trace across policies, capacity 100, the same pinned
tokenizer/model and ONNX CPU provider, SQLite/FAISS configuration, seed, and
warm-up rule. Policy order was prospectively randomized and retained:

| Seed | First | Second | Third |
|---:|---|---|---|
| 20261001 | CARMA | LFU | LRU |
| 20261002 | LFU | LRU | CARMA |
| 20261003 | LRU | CARMA | LFU |
| 20261004 | LRU | LFU | CARMA |
| 20261005 | CARMA | LRU | LFU |

The bundle contains 45,000 request rows. The measured request enters the real
GPTCache adapter with raw text, computes the actual 768-dimensional ONNX
embedding, executes similarity, FAISS, SQLite, and policy work, and returns a
materialized response. Each request separates embedding, FAISS, policy, SQLite,
response-return, residual, and total time. The retained summaries include
p50/p95/p99, service throughput, external CPU/memory samples, and storage and
semantic counters. These facts establish that the intended full-path workload
ran; they do not make the resulting attempt claimable.

The manifest is 695,528 bytes with SHA-256
`7574127cce8f1c72e87a1528dc6709947860178f43667a92add155e89e1f1cf8`.
The immutable preterminal report is 293,720 bytes with SHA-256
`e3e8df2787049d3b1ea4e622e7666502b715ae572431b4f8d4e4b213c12dd39b`.
The canonical two-row ledger SHA-256 is
`84eee7963f4e5c27f8d962e31ddcdcec9779b1cd463a8952841946ffc8557f0c`.
The compact teaching copy is under
`artifacts/samples/verification/gate7-v2-invalid/`; the ignored original formal
root remains authoritative.

### Why v2 is `INVALID`

The independent preterminal auditor returned `status=invalid`,
`claimable=false`, with seven errors:

1. The retained dependency attestation records the runtime's absolute Python
   executable path while the frozen auditor requires the lexical
   `.venv/bin/python` identity.
2. Each of the five warm-up files exists and is SHA-bound, but its manifest
   identity has an unexpected `rows` key; the exact-schema validator rejects
   all five identities.
3. Run `cca0d14de5ac69d49e0c` has a maximum external resource-sampling gap of
   202,142,875 ns, above the frozen 200,000,000 ns maximum.

The parent then exited 4 with `formal preterminal adjudication is malformed`.
The immediate terminalization check also compares two differently shaped but
content-equivalent auditor identities: the frozen source entry is
`{bytes, sha256}`, while the preterminal report uses
`{path, bytes, sha256}`. That shape bug prevented construction of the terminal
intent. The canonical root ledger consequently contains exactly
`PROTOCOL_GENESIS` and `START`; it has no `TERMINAL`.

There is no ordinary `gate7-adjudication.json`, and the ordinary auditor must
not be run now. Under the frozen fail-stop model, an unmatched START after
manifest publication is itself historical evidence. A later audit cannot add
the missing parent bootstrap completion, turn the two-row ledger into a valid
three-row chain, or make the attempt claimable.

### Descriptive results, not a formal Gate 7 claim

The invalid preterminal report still independently reconstructs all five
CARMA/LRU seed comparisons. Four seeds pass every frozen systems bound. Seed
20261001 fails only the absolute p95 delta: CARMA minus LRU is 26,230,125 ns
against the maximum 500,000 ns. The remaining 24 of 25 numerical checks pass.
Therefore the counterfactual outcome, if this exact numerical evidence had
been structurally valid, is `FAIL`; it is never a `PASS`. The actual recorded
v2 status remains `INVALID`, not `FAIL`, because evidence integrity has
precedence over numerical adjudication.

The separate semantic guardrail also reconstructs `FAIL`: 25,405 hits comprise
25,400 same-component hits, three direct-negative hits, and two unlabeled
cross-component hits. Per-run statuses are three `FAIL`, two
`PENDING_INDETERMINATE`, and ten `PASS_OBSERVED`. This semantic disclosure does
not alter the systems-gate status, but it prohibits a semantic-safety claim.

Gate 2 v2 remains independently `FAIL / no qualifying threshold`. Calibration
selected no threshold, so held-out evaluation did not occur. At threshold 0.96,
precision is 0.97574893, the one-sided Wilson lower bound is 0.96420782,
false-hit rate is 0.00219951, and the one-sided Wilson upper bound is
0.00326719.

### Preservation and future rerun rule

Do not edit the v2 manifest, preterminal report, ledger, or any other retained
attempt byte. Do not append a terminal record, run the ordinary auditor, retry
one seed or policy, or reuse the v2 root for another attempt. A future formal
run must use a new experiment/protocol version, new contract, new formal root,
new ledger genesis, new clean source commit and annotated tag, and a complete
fresh five-seed by three-policy execution. The new version must fix both
identity-shape defects and reconsider the resource-cadence bound or sampler
implementation prospectively, before observing new results.

The general Gate 7 design remains useful: sealed Python bootstraps, identical
traces and configuration, clean process isolation, randomized order,
per-request stage timing, external resource sampling, append-only evidence,
and independent reconstruction. Its lesson is that complete measurements are
not equivalent to valid evidence when terminalization and schema contracts do
not close.

Verify and regenerate only the curated forensic copy with:

```bash
(cd artifacts/samples/verification/gate7-v2-invalid && \
  shasum -a 256 -c SHA256SUMS)
.venv/bin/python \
  artifacts/samples/analysis/gate7-v2-invalid/analyze_gate7_v2_invalid.py
```

That script checks the exact copied ledger/manifest/preterminal hashes and
recreates the descriptive tables and figure. It does not import or invoke the
formal ordinary auditor, append to the ledger, or modify the authoritative raw
attempt.

The root `artifacts/samples/SHA256SUMS` is the sealed 73-file historical bundle,
not a recursive inventory of later publication additions. Each later analysis
or verification group is sealed by its colocated nested `SHA256SUMS`. There are
eight manifests in the final package. The report-only
`verification/publication-package-20260827/SHA256SUMS` index additionally covers
every current sample file except itself, including the frozen root and nested
manifests; it inventories bytes without changing any evidence status.

## Historical validation record: 2026-08-25

The bullets below are retained as the pre-hardening historical record. They do
not validate the generated hash locks or paired-container controls added later.

- A fresh Python 3.12 virtual environment installed every pin, passed
  `pip check`, and imported GPTCache, NumPy, FAISS, and SQLAlchemy.
- Binary-only dependency resolution succeeded for both Linux x86-64 and Linux
  ARM64 (`manylinux_2_28`) under Python 3.12.
- The Docker Hub manifest still resolved the recorded multi-architecture image
  digest `sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2`.
- The verification entrypoint passed 10 focused upstream tests and 23 CARMA
  project tests; the DuckDB-only case was explicitly deselected.
- Local container execution was not verified because the Docker daemon was not
  running. The image definition was inspected, and the CI container job remains
  the required execution check before final submission.

## Publication exact-source validation record: 2026-08-26

The later final gate supersedes the execution limitations in the historical
record above. It ran from clean source commit
`9ee91f1ec6b10d1c2831a15fb80e5032353e3af4` and one 468-file Git archive with
SHA-256 `ed24bc311dd5a454fcba22c8fd58ed635266343a440c375ec3047f27d31aceda`.
The complete gate passed and produced:

- a macOS arm64 host verification under exact CPython 3.12.13, including 8
  upstream eviction tests, 2 SQLite/FAISS tests (1 deselected), 179 passing
  project tests (2 skipped), `pip check`, and the deterministic CI benchmark;
- two distinct fresh `linux/amd64` containers from image
  `sha256:150c957a35b476874392ae185e78b6709934e675f71ec02d2a62004c0d0e91b2`,
  each unprivileged, network-isolated, with all capabilities dropped and
  `no-new-privileges=true`; and
- byte-identical container benchmark manifests, 15,600-row request logs,
  16-row run tables, normalized verifier logs, and artifact hash lists.

The authoritative host status SHA-256 is
`365fa5ee1a97d731738d2d4d968d951fd0c55153a7f2a7d3a27ede71a1dff3ab`;
the paired-container status SHA-256 is
`e840d06adb33da01f9b3e6020be912fa5517030b3ee7b94db96f223ae3b381d1`.
Their complete referenced topology is retained under
`artifacts/samples/verification/publication-20260826-ci-refresh/`, and every
file in that refresh is covered by its colocated `SHA256SUMS`. This refresh
also includes the hosted-workflow correction from unavailable Python 3.8.20 to
the newest Ubuntu 22.04 x64 build in GitHub's manifest, Python 3.8.18; the
patch-neutral `cp38` dependency lock is unchanged.

## Gate 7 v2 source validation record: 2026-08-27

The current source gate ran from clean commit
`557c6ac0578cb6b77c5ae51595b49abdc0407e10` and one 527-file Git archive with
SHA-256 `51aea800e364c1d52bed566f48c4c49c1836d0d6a0b40b3bb8203867c14b58b4`.
It passed on the macOS arm64 host and in two fresh byte-matched,
network-isolated `linux/amd64` containers. Each verifier run reports 8 upstream
tests passed, 2 SQLite/FAISS tests passed with 1 optional deselection, and 453
project tests passed with 4 platform skips. The two containers use image
`sha256:a5203e8d3f3beba85c5dc542b3573e724fb4da26cca65e82b3fb90a1a26985b1`.

The host status SHA-256 is
`2219c514d078825abe8844a65be71b26e5ac6691238b7517f33fe9fda11b8fca`;
the paired-container status SHA-256 is
`e187409b7b210c07d8b981fe195ca7edc70fb14b76fdcce8b1766351ba7ab2b9`;
and the exact-source analyzer result SHA-256 is
`8aa53ca5c5a3757fc526b88d3eb2afdcefb70369a940c6b2056014b429e5fc90`.
The complete 32-file topology is retained at
`artifacts/samples/verification/publication-20260827-v2-source/` and sealed by
its colocated `SHA256SUMS`.

This record validates source `S`; it does not make the invalid Gate 7 v2
attempt claimable. The final publication package must be exactly one non-merge
direct child `P` of this source and stay inside the packaging allowlist above.
