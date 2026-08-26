#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly ARCHIVE="${PROJECT_ROOT}/examples/benchmark/similiar_qqp_full.json.gz"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

usage() {
  printf 'Usage: %s [OUTPUT_DIR]\n' "${0##*/}"
  printf 'Run pinned QQP preparation, ONNX embedding, and held-out calibration.\n'
  printf 'Environment: PYTHON_BIN, CARMA_QQP_BATCH_SIZE, CARMA_ONNX_WORKERS, CARMA_ONNX_THREADS.\n'
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ "$#" -gt 1 ]]; then
  usage >&2
  exit 2
fi

readonly OUTPUT_ROOT="${1:-${PROJECT_ROOT}/artifacts/qqp}"

cd "${PROJECT_ROOT}"
export PYTHONHASHSEED=0
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

"${PYTHON_BIN}" -m benchmarks.carma.qqp prepare \
  --archive "${ARCHIVE}" \
  --output "${OUTPUT_ROOT}/prepared"
"${PYTHON_BIN}" -m benchmarks.carma.qqp embed \
  --prepared "${OUTPUT_ROOT}/prepared" \
  --output "${OUTPUT_ROOT}/embeddings" \
  --batch-size "${CARMA_QQP_BATCH_SIZE:-32}"
"${PYTHON_BIN}" -m benchmarks.carma.qqp calibrate \
  --prepared "${OUTPUT_ROOT}/prepared" \
  --embeddings "${OUTPUT_ROOT}/embeddings" \
  --output "${OUTPUT_ROOT}/evaluation"
