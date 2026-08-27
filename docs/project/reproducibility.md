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
6. requires and runs the frozen ten-file CARMA unit/integration test manifest;
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
are prospective CI controls; they do not replace the historical validation
record below until the workflow has actually passed and its uploaded hosted
evidence is retained.

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

## Gate 7 v2 ONNX protocol

Gate 7 v2 is a separate full-request-path experiment and independent audit.
The historical v1 precomputed-vector evidence remains immutable and is linked
into the v2 genesis rather than rewritten. Install `requirements-benchmark.lock`
in the exact project `.venv`, run development smoke checks outside the formal
root, and commit the complete intended source before creating the formal tag:

```bash
# Development only: fake embeddings, non-formal output, never claimable.
/bin/bash scripts/run_gate7_v2_onnx_integration_benchmark.sh \
  smoke artifacts/gate7-v2-onnx-smoke

# After the contract hash is frozen in producer, auditor, and isolated-bootstrap
# source, from a clean source commit that has passed the reproducibility gate
# and hosted CI:
contract_sha256=$(shasum -a 256 \
  docs/project/gate7-v2-remediation-contract.md | awk '{print $1}')
git tag -a gate7c-onnx-v2-formal-source -F - <<EOF
experiment_id=gate7c-onnx-v2
contract_sha256=$contract_sha256
EOF
git push submission refs/tags/gate7c-onnx-v2-formal-source

# Formal execution accepts no output override or other runner arguments. The
# operator-controlled outer env-i boundary is the shell root of trust: it
# prevents Bash startup hooks or exported functions from running before the
# wrapper can validate the environment visible at its first executable line.
/usr/bin/env -i \
  CARMA_GATE7_WRAPPER_SHELL=gate7c-shell-v1 \
  HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 LC_CTYPE=C.UTF-8 TZ=UTC TMPDIR=/tmp \
  /usr/bin/caffeinate -dimsu \
  /bin/bash "$PWD/scripts/run_gate7_v2_onnx_integration_benchmark.sh" full
```

The formal wrapper clears inherited environment state and launches the parent,
every policy child, the preterminal auditor, and the ordinary auditor through
`scripts/gate7_v2_isolated_bootstrap.py` with Python `-S -P`. Before any
third-party import, that bootstrap requires exact CPython 3.12.13, the project
`.venv`, the frozen benchmark lock/package map, byte-verified RECORD members,
the exact local editable GPTCache RECORD, an unexecuted `.pth` boundary, no
site customization, no unowned site-packages path, and a small allowlisted
environment. It verifies the same state again on process exit. This closes the
Python dependency/import boundary on the recorded host; it does not make the
kernel, hardware, stdlib, system libraries, or Accelerate implementation a
fully hermetic machine image.

Formal mode serializes attempts with an interprocess lock and creates exactly
one hash-chained `PROTOCOL_GENESIS`, followed by one `START`/`TERMINAL` pair per
whole-matrix attempt in
`artifacts/gate7-v2-onnx-attempts/attempt-ledger.jsonl`. Genesis and every START
bind the clean tagged HEAD, the exact `submission` fetch/push repository URL
`https://github.com/MatanGoldfarB/gptcache-online-policy.git`, the remote
annotated tag object and raw annotation,
contract, dependency/bootstrap/environment observations, prepared QQP inputs,
v1 terminal lineage, source identities, and pre-run power state. A dangling or
malformed predecessor is a fail-stop. A seed or policy is never selectively
rerun: any eligible retry is a new randomized five-seed by three-policy matrix.

Each policy runs in a fresh child process. All policies use the same retained
real-text trace, capacity, seeds, tokenizer/model, ONNX CPU provider, SQLite and
FAISS configuration, warmup rule, and fixed per-seed randomized policy order.
Per-request evidence separates embedding, FAISS, policy, SQLite, response-return,
and total latency. Retained whole-run/resource evidence supports service and
loop throughput, p50/p95/p99 latency, CPU/I/O, sample cadence, and peak RSS.
The formal comparison uses `requests / sum(request_total_ns)` as service
throughput; loop-wall throughput remains diagnostic only.

Before the producer lock is released, the producer launches
`benchmarks/carma/gate7_v2_audit.py --preterminal` in a fresh sealed auditor
process, rechecks its own live runtime and all source/input/tag identities, and
prepares a canonical terminal intent. It does not append a completed-manifest
TERMINAL. After the target exits, the isolated parent bootstrap repeats its
authoritative private source, target, environment, pycache, import-path, and
sealed-dependency checks. Only then does it reacquire the same root lock,
revalidate the still-unmatched START and immutable manifest/report bytes, and
append TERMINAL with a self-hashed bootstrap-completion receipt bound to the
intent and target exit status. A post-target failure therefore leaves an
unmatched START and can never create claimable evidence. Catchable failures
before manifest publication retain a producer-written invalid failure
TERMINAL with null bootstrap completion.

The ordinary auditor independently reconstructs the intent and completion
hashes, validates the complete ledger and retained bundle, and writes
`gate7-adjudication.json`. Only a structurally valid, complete full bundle can
be `pass` or `fail`; development smoke evidence is `pending`, and any
evidence-integrity or protocol violation is `invalid`. Gate 2 remains a
separate semantic qualification: its threshold is calibration-only and frozen
before any held-out endpoint resolution; if no threshold qualifies, held-out
evaluation is not performed and Gate 2 remains failed without falsifying the
system-performance result.

All formal attempt bytes and the root ledger are append-only evidence. Preserve
the entire root even after a failure. A late rejection after manifest
publication can intentionally leave a dangling START that requires external
forensic resolution; the producer does not rewrite immutable evidence into a
cleaner outcome. A process/host crash during the bootstrap's single physical
TERMINAL append can instead leave a truncated noncanonical row; that is also a
nonclaimable fail-stop and is never repaired or skipped. These protections are
scoped to this retained checkout root. Global uniqueness across clones would
require an external append-only run authority.

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
