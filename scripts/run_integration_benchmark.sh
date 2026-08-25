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

# Friendly positional form:
#   scripts/run_integration_benchmark.sh smoke [OUTPUT_DIR] [extra CLI args]
#   scripts/run_integration_benchmark.sh full  [OUTPUT_DIR] [extra CLI args]
# The complete Python CLI also passes through unchanged:
#   scripts/run_integration_benchmark.sh --mode smoke --output /tmp/results
if [[ "${1:-}" == "smoke" || "${1:-}" == "full" ]]; then
  mode="$1"
  shift
  if [[ -n "${1:-}" && "${1}" != -* ]]; then
    output="$1"
    shift
  else
    output="${PROJECT_ROOT}/artifacts/carma-integration-${mode}"
  fi
  exec "${PYTHON_BIN}" benchmarks/carma/integration_benchmark.py \
    --mode "${mode}" \
    --output "${output}" \
    "$@"
fi

if [[ "$#" -eq 0 ]]; then
  exec "${PYTHON_BIN}" benchmarks/carma/integration_benchmark.py \
    --mode smoke \
    --output "${PROJECT_ROOT}/artifacts/carma-integration-smoke"
fi

exec "${PYTHON_BIN}" benchmarks/carma/integration_benchmark.py "$@"
