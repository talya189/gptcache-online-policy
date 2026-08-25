#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly BASELINE_COMMIT="c59fb3a6152a4458b2a070ca183b61c4b614095f"

if [[ -n "${CARMA_ARTIFACT_DIR:-}" ]]; then
  ARTIFACT_DIR="${CARMA_ARTIFACT_DIR}"
  mkdir -p "${ARTIFACT_DIR}"
  ARTIFACT_DIR="$(cd "${ARTIFACT_DIR}" && pwd -P)"
  CLEAN_ARTIFACT_DIR=0
else
  ARTIFACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/carma-verify.XXXXXX")"
  CLEAN_ARTIFACT_DIR=1
fi

cleanup() {
  if [[ "${CLEAN_ARTIFACT_DIR}" -eq 1 && "${ARTIFACT_DIR}" == *carma-verify.* ]]; then
    rm -rf -- "${ARTIFACT_DIR}"
  fi
}
trap cleanup EXIT

cd "${PROJECT_ROOT}"

echo "[verify] interpreter and dependency integrity"
python - <<'PY'
import platform
import sys

if sys.version_info[:3] != (3, 12, 13):
    raise SystemExit(
        f"CARMA requires Python 3.12.13; found {platform.python_version()}"
    )
print(f"python={platform.python_version()} platform={platform.platform()}")
PY
python -m pip check

echo "[verify] pinned GPTCache ancestry"
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
  git cat-file -e "${BASELINE_COMMIT}^{commit}"
  git merge-base --is-ancestor "${BASELINE_COMMIT}" HEAD
else
  echo "[verify] Git metadata unavailable; host CI owns the ancestry check"
fi

# A runtime lazy-install attempt must fail instead of silently widening the
# environment. All dependencies required by these checks are installed above.
export PIP_NO_INDEX=1
export PYTHONHASHSEED=0

echo "[verify] upstream SQLite/FAISS regression slice"
python -m pytest -q -o addopts='' \
  tests/unit_tests/eviction/test_memory_cache.py \
  tests/unit_tests/manager/test_eviction.py
python -m pytest -q -o addopts='' \
  tests/unit_tests/manager/test_sql_scalar.py -k 'not duckdb'

project_tests=()
while IFS= read -r test_file; do
  project_tests+=("${test_file}")
done < <(
  find tests -type f \
    \( -path 'tests/project_tests/test_*.py' \
       -o -name 'test_*carma*.py' \
       -o -name 'test_*online_cluster*.py' \) \
    -print | LC_ALL=C sort
)

if (( ${#project_tests[@]} > 0 )); then
  echo "[verify] CARMA project tests"
  python -m pytest -q -o addopts='' "${project_tests[@]}"
else
  echo "[verify] CARMA project tests not present yet; discovery skipped"
fi

# The benchmark owns its CLI behind this stable wrapper. Once present it must
# write deterministic CI results below the selected artifact directory.
if [[ -x scripts/run_ci_benchmark.sh ]]; then
  echo "[verify] deterministic CI benchmark"
  scripts/run_ci_benchmark.sh "${ARTIFACT_DIR}/benchmark"
else
  echo "[verify] scripts/run_ci_benchmark.sh not present yet; benchmark skipped"
fi

echo "[verify] PASS"
