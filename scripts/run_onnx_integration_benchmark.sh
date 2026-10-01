#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
python_bin="$repo_dir/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
  echo "Missing benchmark environment: $python_bin" >&2
  echo "Create .venv and install requirements-benchmark.lock before running." >&2
  exit 2
fi

mode=${1:-full}
if [[ "$mode" != "full" && "$mode" != "smoke" ]]; then
  echo "Usage: $0 [full|smoke] [output-directory] [additional runner arguments...]" >&2
  exit 2
fi
shift || true

if [[ $# -gt 0 && "$1" != --* ]]; then
  output_dir=$1
  shift
else
  if [[ "$mode" == "full" ]]; then
    attempt_stamp=$(date -u +%Y%m%dT%H%M%SZ)
    output_dir="$repo_dir/artifacts/gate7-onnx-attempts/attempt-$attempt_stamp-$$"
  else
    output_dir="$repo_dir/artifacts/gate7-onnx-smoke"
  fi
fi

export TOKENIZERS_PARALLELISM=false
export CARMA_ONNX_WORKERS=1
export CARMA_ONNX_THREADS=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

runner_args=(
  --mode "$mode"
  --prepared "$repo_dir/artifacts/qqp-full/prepared"
  --contract "$repo_dir/docs/project/gate7-remediation-contract.md"
  --output "$output_dir"
)

if [[ "$mode" == "smoke" ]]; then
  runner_args+=(--fake-embedding)
fi

set +e
"$python_bin" "$repo_dir/benchmarks/carma/onnx_integration_benchmark.py" \
  "${runner_args[@]}" "$@"
runner_status=$?
set -e

# Full mode performs its preterminal audit and terminal ledger append while the
# root lock is still held. If it reached a manifest, run the ordinary offline
# audit as well; otherwise preserve the producer/failure-retention exit status.
if [[ "$mode" == "full" && ! -f "$output_dir/manifest.json" ]]; then
  exit "$runner_status"
fi

if [[ "$runner_status" -gt 3 ]]; then
  exit "$runner_status"
fi

set +e
"$python_bin" "$repo_dir/benchmarks/carma/gate7_audit.py" \
  "$output_dir" --output "$output_dir/gate7-adjudication.json"
audit_status=$?
set -e

# A valid development smoke bundle is intentionally pending. Treat that one
# expected status as a successful smoke; formal fail/pending/invalid statuses
# retain the auditor's nonzero exit code.
if [[ "$mode" == "smoke" && "$audit_status" -eq 2 ]]; then
  exit 0
fi
exit "$audit_status"
