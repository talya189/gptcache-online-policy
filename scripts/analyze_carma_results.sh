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

if [[ "$#" -eq 0 ]]; then
  exec "${PYTHON_BIN}" -m benchmarks.carma.analyze_results \
    --full-dir "${PROJECT_ROOT}/artifacts/carma-full-full" \
    --integration-dir "${PROJECT_ROOT}/artifacts/carma-integration-full" \
    --qqp-result "${PROJECT_ROOT}/artifacts/qqp/evaluation/result.json" \
    --moss-dir "${PROJECT_ROOT}/artifacts/carma-moss" \
    --host-verification "${PROJECT_ROOT}/artifacts/ci/host-verification.json" \
    --container-reproducibility "${PROJECT_ROOT}/artifacts/container-reproducibility.json" \
    --output "${PROJECT_ROOT}/artifacts/carma-analysis"
fi

exec "${PYTHON_BIN}" -m benchmarks.carma.analyze_results "$@"
