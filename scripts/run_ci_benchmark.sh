#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly OUTPUT_DIR="${1:-${PROJECT_ROOT}/artifacts/carma-ci}"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
readonly PYTHON_BIN

first_run="$(mktemp -d "${TMPDIR:-/tmp}/carma-ci-first.XXXXXX")"
second_run="$(mktemp -d "${TMPDIR:-/tmp}/carma-ci-second.XXXXXX")"

cleanup() {
  if [[ "${first_run}" == *carma-ci-first.* ]]; then
    rm -rf -- "${first_run}"
  fi
  if [[ "${second_run}" == *carma-ci-second.* ]]; then
    rm -rf -- "${second_run}"
  fi
}
trap cleanup EXIT

cd "${PROJECT_ROOT}"

run_once() {
  local destination="$1"
  "${PYTHON_BIN}" -m benchmarks.carma \
    --capacity 50 \
    --seed 20260825 \
    --workloads stationary phase_shift pollution_scan novel \
    --policies LRU LFU CARMA CARMA_NO_CLUSTER \
    --hit-threshold 0.97 \
    --cluster-similarity-threshold 0.70 \
    --cell-threshold 0.88 \
    --demand-half-life 128 \
    --quota-strength 0.5 \
    --ghost-support-threshold 1.5 \
    --output "${destination}" \
    >/dev/null
}

run_once "${first_run}"
run_once "${second_run}"

for artifact in requests.jsonl runs.csv manifest.json; do
  cmp "${first_run}/${artifact}" "${second_run}/${artifact}"
done

mkdir -p "${OUTPUT_DIR}"
for artifact in requests.jsonl runs.csv manifest.json; do
  cp "${first_run}/${artifact}" "${OUTPUT_DIR}/${artifact}"
done

echo "CARMA CI benchmark deterministic: ${OUTPUT_DIR}"
