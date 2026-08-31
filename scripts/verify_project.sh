#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly BASELINE_COMMIT="c59fb3a6152a4458b2a070ca183b61c4b614095f"
readonly TIKTOKEN_CACHE_KEY="9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
readonly TIKTOKEN_CACHE_SHA256="223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
readonly TIKTOKEN_CACHE_SIZE="1681126"

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
export PYTHONDONTWRITEBYTECODE=1

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

echo "[verify] source-bound offline tiktoken cache"
SOURCE_TIKTOKEN_CACHE_DIR="${PROJECT_ROOT}/assets/tiktoken-cache"
readonly SOURCE_TIKTOKEN_CACHE_DIR
if [[ -e "${SOURCE_TIKTOKEN_CACHE_DIR}" || -L "${SOURCE_TIKTOKEN_CACHE_DIR}" ]]; then
  TIKTOKEN_CACHE_DIR="${SOURCE_TIKTOKEN_CACHE_DIR}"
elif [[ "${TIKTOKEN_CACHE_DIR:-}" == "/opt/tiktoken-cache" ]]; then
  TIKTOKEN_CACHE_DIR="/opt/tiktoken-cache"
else
  echo "[verify] exact tiktoken cache is unavailable" >&2
  exit 2
fi
export TIKTOKEN_CACHE_DIR
unset DATA_GYM_CACHE_DIR
python -I - "${TIKTOKEN_CACHE_DIR}" "${TIKTOKEN_CACHE_KEY}" \
  "${TIKTOKEN_CACHE_SHA256}" "${TIKTOKEN_CACHE_SIZE}" <<'PY'
import hashlib
import sys
from pathlib import Path

cache_root = Path(sys.argv[1])
cache_file = cache_root / sys.argv[2]
expected_sha256 = sys.argv[3]
expected_size = int(sys.argv[4])
if cache_root.is_symlink() or not cache_root.is_dir():
    raise SystemExit("tiktoken cache root is not a real directory")
entries = list(cache_root.iterdir())
if entries != [cache_file] or cache_file.is_symlink() or not cache_file.is_file():
    raise SystemExit("tiktoken cache must contain exactly one regular cache object")
data = cache_file.read_bytes()
observed_sha256 = hashlib.sha256(data).hexdigest()
if len(data) != expected_size or observed_sha256 != expected_sha256:
    raise SystemExit("tiktoken cache object size or SHA-256 changed")
print("tiktoken_cache_sha256=%s" % observed_sha256)
PY

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
  -p no:cacheprovider \
  tests/unit_tests/eviction/test_memory_cache.py \
  tests/unit_tests/manager/test_eviction.py
python -m pytest -q -o addopts='' \
  -p no:cacheprovider \
  tests/unit_tests/manager/test_sql_scalar.py -k 'not duckdb'

expected_project_tests=(
  tests/project_tests/test_carma_analyze_results.py
  tests/project_tests/test_carma_failure_paths.py
  tests/project_tests/test_carma_integration.py
  tests/project_tests/test_carma_integration_protocol.py
  tests/project_tests/test_carma_phase_metrics.py
  tests/project_tests/test_carma_protocol_remediation.py
  tests/project_tests/test_gate7_audit.py
  tests/project_tests/test_gate7_onnx_integration.py
  tests/project_tests/test_gate7_trace.py
  tests/project_tests/test_gate7_v1_preservation.py
  tests/project_tests/test_gate7_v2_audit.py
  tests/project_tests/test_gate7_v2_isolated_bootstrap.py
  tests/project_tests/test_gate7_v2_onnx_integration.py
  tests/project_tests/test_gate7_v2_preservation.py
  tests/project_tests/test_gate7_v2_trace.py
  tests/project_tests/test_gate7_v3_audit.py
  tests/project_tests/test_gate7_v3_isolated_bootstrap.py
  tests/project_tests/test_gate7_v3_onnx_integration.py
  tests/project_tests/test_gate7_v3_preservation.py
  tests/project_tests/test_gate7_v4_audit.py
  tests/project_tests/test_gate7_v4_isolated_bootstrap.py
  tests/project_tests/test_gate7_v4_onnx_integration.py
  tests/project_tests/test_gate7_v4_preservation.py
  tests/project_tests/test_gate7_v5_audit.py
  tests/project_tests/test_gate7_v5_isolated_bootstrap.py
  tests/project_tests/test_gate7_v5_onnx_integration.py
  tests/project_tests/test_moss_benchmark.py
  tests/project_tests/test_qqp_gate7_assets.py
  tests/project_tests/test_qqp_v2.py
  tests/project_tests/test_qqp_wrapper.py
  tests/project_tests/test_reproducibility_evidence.py
  tests/unit_tests/eviction/test_carma.py
)
if ! project_test_listing="$(
  find tests -type f \
    \( -path 'tests/project_tests/test_*.py' \
       -o -name 'test_*carma*.py' \
       -o -name 'test_*online_cluster*.py' \) \
    -print | LC_ALL=C sort
)"; then
  echo "[verify] CARMA project-test discovery failed" >&2
  exit 2
fi
if [[ -z "${project_test_listing}" ]]; then
  echo "[verify] CARMA project-test discovery returned no files" >&2
  exit 2
fi
project_tests=()
while IFS= read -r test_file; do
  project_tests+=("${test_file}")
done <<< "${project_test_listing}"
if (( ${#project_tests[@]} != ${#expected_project_tests[@]} )); then
  echo "[verify] CARMA project-test manifest count changed" >&2
  exit 2
fi
for ((index = 0; index < ${#expected_project_tests[@]}; index++)); do
  if [[ "${project_tests[index]}" != "${expected_project_tests[index]}" ]]; then
    echo "[verify] CARMA project-test manifest changed at index ${index}" >&2
    exit 2
  fi
done
echo "[verify] CARMA project tests (${#project_tests[@]} files)"
python -m pytest -q -o addopts='' -p no:cacheprovider "${project_tests[@]}"

# The benchmark owns its CLI behind this stable wrapper. Once present it must
# write deterministic CI results below the selected artifact directory.
if [[ -x scripts/run_ci_benchmark.sh ]]; then
  echo "[verify] deterministic CI benchmark"
  scripts/run_ci_benchmark.sh "${ARTIFACT_DIR}/benchmark"
else
  echo "[verify] required scripts/run_ci_benchmark.sh is absent or not executable" >&2
  exit 2
fi

echo "[verify] PASS"
