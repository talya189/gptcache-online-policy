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

# No command downloads the corpus. `source-metadata` fetches only small JSON
# metadata; `run` and `prepare` require an existing checksum-pinned local ZIP.
if [[ "${1:-}" == "run" ]]; then
  shift
  archive="${1:?usage: run_moss_benchmark.sh run ARCHIVE [OUTPUT_DIR] [args]}"
  shift
  if [[ -n "${1:-}" && "${1}" != -* ]]; then
    output="$1"
    shift
  else
    output="${PROJECT_ROOT}/artifacts/carma-moss"
  fi
  exec "${PYTHON_BIN}" -m benchmarks.carma.moss run \
    --archive "${archive}" \
    --output "${output}" \
    "$@"
fi

if [[ "${1:-}" == "source-metadata" ]]; then
  shift
  output="${1:-${PROJECT_ROOT}/artifacts/carma-moss-source.json}"
  exec "${PYTHON_BIN}" -m benchmarks.carma.moss source-metadata \
    --output "${output}"
fi

if [[ "$#" -eq 0 ]]; then
  exec "${PYTHON_BIN}" -m benchmarks.carma.moss --help
fi

exec "${PYTHON_BIN}" -m benchmarks.carma.moss "$@"
