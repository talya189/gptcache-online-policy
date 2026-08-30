#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

readonly OUTPUT_DIR="${1:-${PROJECT_ROOT}/artifacts/gate7-analysis}"
integration_args=()
for seed_dir in "${PROJECT_ROOT}"/artifacts/samples/integration/*; do
  [[ -d "${seed_dir}" ]] || continue
  integration_args+=(--integration-dir "${seed_dir}")
done

if [[ "${#integration_args[@]}" -ne 10 ]]; then
  echo "Gate 7 requires exactly five retained integration seed directories." >&2
  exit 2
fi

cd "${PROJECT_ROOT}"
export PYTHONHASHSEED=0
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

"${PYTHON_BIN}" -m benchmarks.carma.analyze_results \
  "${integration_args[@]}" \
  --output "${OUTPUT_DIR}" \
  > /dev/null

"${PYTHON_BIN}" -c '
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as source:
    audit = json.load(source)
gate = audit["gates"]["gate_7_system_overhead"]
print("Gate 7 v2: %s" % gate["status"].upper())
if gate["status"] != "pass":
    for reason in gate.get("protocol_readiness", {}).get("reasons", []):
        print("- %s" % reason)
    raise SystemExit(1)
' "${OUTPUT_DIR}/gate-audit.json"
