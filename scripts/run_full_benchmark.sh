#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

cd "${PROJECT_ROOT}"
export PYTHONHASHSEED=0
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# Friendly form: run_full_benchmark.sh [smoke|full] [OUTPUT_DIR] [extra args]
# With no arguments, run the bounded smoke protocol.
if [[ "${1:-}" == "smoke" || "${1:-}" == "full" ]]; then
  mode="$1"
  shift
  if [[ -n "${1:-}" && "${1}" != -* ]]; then
    output="$1"
    shift
  else
    output="${PROJECT_ROOT}/artifacts/carma-full-${mode}"
  fi
  exec "${PYTHON_BIN}" -m benchmarks.carma.full_experiment \
    --mode "${mode}" \
    --output "${output}" \
    "$@"
fi

if [[ "$#" -eq 0 ]]; then
  exec "${PYTHON_BIN}" -m benchmarks.carma.full_experiment \
    --mode smoke \
    --output "${PROJECT_ROOT}/artifacts/carma-full-smoke"
fi

exec "${PYTHON_BIN}" -m benchmarks.carma.full_experiment "$@"
