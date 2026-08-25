#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly OUTPUT_ROOT="${1:-${PROJECT_ROOT}/artifacts/qqp}"
readonly ARCHIVE="${PROJECT_ROOT}/examples/benchmark/similiar_qqp_full.json.gz"

cd "${PROJECT_ROOT}"
python -m benchmarks.carma.qqp prepare \
  --archive "${ARCHIVE}" \
  --output "${OUTPUT_ROOT}/prepared"
python -m benchmarks.carma.qqp embed \
  --prepared "${OUTPUT_ROOT}/prepared" \
  --output "${OUTPUT_ROOT}/embeddings" \
  --batch-size "${CARMA_QQP_BATCH_SIZE:-32}"
python -m benchmarks.carma.qqp calibrate \
  --prepared "${OUTPUT_ROOT}/prepared" \
  --embeddings "${OUTPUT_ROOT}/embeddings" \
  --output "${OUTPUT_ROOT}/evaluation"
