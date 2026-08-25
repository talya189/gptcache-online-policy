# Reproducibility Guide and Validation Record

## Supported environment

The project target is Python 3.12.13 on CPU with SQLite and FAISS. The complete
project dependency set, including transitive packages and build tools, is
exactly pinned in `requirements-project.txt`. Optional GPTCache backends are
intentionally excluded because their upstream lazy imports can run a bare
`pip` against an interpreter other than the active environment.

Create a clean environment from the repository root with:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --requirement requirements-project.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation --editable .
PATH="$PWD/.venv/bin:$PATH" bash scripts/verify_project.sh
```

The final editable-install command is deliberately dependency-free: only the
audited lock may define the environment.

## Verification contract

`scripts/verify_project.sh` is the single host and container entrypoint. It:

1. requires Python 3.12 and a consistent installed dependency graph;
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

## Container and CI

`Dockerfile.project` pins the multi-architecture digest of
`python:3.12.13-slim-bookworm`, installs only `requirements-project.txt`, copies
only executable project inputs, and runs as an unprivileged user. Build and run
it with:

```bash
docker build --file Dockerfile.project --tag carma-project:local .
docker run --rm carma-project:local
```

`.github/workflows/carma-ci.yml` repeats the same verification on Ubuntu 24.04
both directly and in the pinned container. GitHub Actions are referenced by
immutable commit hashes, and benchmark artifacts are uploaded when present.

## Validation record: 2026-08-25

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
