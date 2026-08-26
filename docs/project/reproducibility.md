# Reproducibility Guide and Validation Record

## Supported environment

The project target is exactly Python 3.12.13 on CPU with SQLite and FAISS. The
human-reviewed direct and transitive pins live in `requirements-project.txt`;
`requirements-project.lock` authenticates every allowed wheel with SHA-256.
Optional GPTCache backends are intentionally excluded because their upstream
lazy imports can run a bare `pip` against an interpreter other than the active
environment.

Create a clean environment from the repository root with:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip --isolated --disable-pip-version-check install \
  --no-input \
  --index-url https://pypi.org/simple \
  --require-hashes \
  --only-binary=:all: \
  --requirement requirements-project.lock
.venv/bin/python -m pip --isolated --disable-pip-version-check install \
  --no-input --no-index --no-deps --no-build-isolation --editable .
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
3. disables package-index access before tests, making any runtime lazy-install
   attempt fail visibly;
4. runs the focused upstream SQLite/FAISS regression slice;
5. discovers and runs CARMA unit and integration tests; and
6. calls `scripts/run_ci_benchmark.sh OUTPUT_DIR` when that benchmark wrapper is
   present.

Set `CARMA_ARTIFACT_DIR` to retain benchmark outputs. Without it, verification
uses a private temporary directory and removes it on exit.

From a clean committed checkout with an exact CPython 3.12.13 interpreter, run
the complete local evidence workflow with:

```bash
CARMA_PYTHON=/absolute/path/to/python3.12 \
  bash scripts/run_reproducibility_gate.sh
```

The workflow refuses a dirty checkout, binds all evidence to the full current
commit, installs the primary hash lock into a fresh temporary virtual
environment, runs the host verifier, builds the committed Git archive for
`linux/amd64`, and executes two distinct fresh containers. It retains Docker
image and container inspections so the evidence writer can validate the actual
platform, image ID, network isolation, dropped capabilities,
`no-new-privileges`, artifact mount, and successful exit status. Only successful
pytest summary-line elapsed values are normalized; the rest of both logs must
be byte-identical.

The canonical output targets are `artifacts/ci/host-verification.json` and
`artifacts/container-reproducibility.json`, with the two retained run roots at
`artifacts/docker-run-1/` and `artifacts/docker-run-2/`. The writer never
overwrites evidence. Failed runs retain their partial logs for diagnosis, so a
rerun also requires archiving that root or selecting a new empty root:

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
is one subsequent non-merge commit `P` whose direct parent is `S` and whose
entire diff is restricted to `README.md`, `docs/project/**`, and
`artifacts/samples/**`. This lets the verified report, audit, and curated copy of
the evidence be packaged without pretending they existed before the gate. The
analyzer must report `verified_packaging_descendant` and the exact changed paths;
any dirty worktree, additional commit, or code, test, workflow, Docker, or lock
change invalidates the binding and requires a new run from a fresh root.

## Container and CI

`Dockerfile.project` pins the multi-architecture digest of
`python:3.12.13-slim-bookworm`, installs only the authenticated wheel lock from
the public PyPI index, copies an allowlisted build context, and runs as an
unprivileged user. For a debugging run that is not paired Gate 8 evidence, build
and retain its artifacts with:

```bash
docker build --no-cache --pull \
  --platform linux/amd64 \
  --file Dockerfile.project --tag carma-project:local .
CARMA_CONTAINER_OUTPUT="$(mktemp -d "$PWD/../carma-container-output.XXXXXX")"
chmod 0777 "$CARMA_CONTAINER_OUTPUT"
docker run --rm --network none \
  --platform linux/amd64 \
  --cap-drop ALL --security-opt=no-new-privileges \
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

## Validation record: 2026-08-25

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
