#!/usr/bin/env bash
set -euo pipefail

mode=${1:-}
if [[ "$mode" != "full" && "$mode" != "smoke" ]]; then
  builtin printf '%s\n' \
    "Usage: $0 <full|smoke> [smoke-output-directory] [smoke runner arguments...]" >&2
  exit 2
fi

# A formal shell must itself start behind `/usr/bin/env -i`; otherwise Bash
# could execute BASH_ENV or import exported functions before this first line.
# Inspect only with builtins before invoking any external program.
if [[ "$mode" == "full" ]]; then
  if [[ "$0" != /* ]]; then
    builtin printf '%s\n' \
      "Formal mode requires the wrapper's absolute filesystem path." >&2
    exit 2
  fi
  if [[ "${CARMA_GATE7_WRAPPER_SHELL-}" != "gate7d-shell-v1" ]]; then
    builtin printf '%s\n' \
      "Formal mode requires the frozen env-i wrapper-shell marker." >&2
    exit 2
  fi
  if [[ -n "$(builtin compgen -A function)" || -n "$(builtin alias -p)" ]]; then
    builtin printf '%s\n' \
      "Formal mode forbids inherited shell functions and aliases." >&2
    exit 2
  fi
  if builtin shopt -q expand_aliases; then
    builtin printf '%s\n' "Formal mode forbids alias expansion." >&2
    exit 2
  fi
  while IFS= read -r exported_name; do
    case "$exported_name" in
      CARMA_GATE7_WRAPPER_SHELL|HOME|LANG|LC_ALL|LC_CTYPE|PATH|PWD|SHLVL|TMPDIR|TZ|__CF_USER_TEXT_ENCODING)
        ;;
      *)
        builtin printf 'Formal mode inherited unexpected environment key: %s\n' \
          "$exported_name" >&2
        exit 2
        ;;
    esac
  done < <(builtin compgen -e)
  if [[ "${PATH-}" != "/usr/bin:/bin:/usr/sbin:/sbin" \
        || "${LANG-}" != "C.UTF-8" \
        || "${LC_ALL-}" != "C.UTF-8" \
        || "${LC_CTYPE-}" != "C.UTF-8" \
        || "${TZ-}" != "UTC" \
        || "${TMPDIR-}" != "/tmp" \
        || "${SHLVL-}" != "1" \
        || "${HOME-}" != /* \
        || "${PWD-}" != /* ]]; then
    builtin printf '%s\n' \
      "Formal mode wrapper-shell environment differs from the frozen allowlist." >&2
    exit 2
  fi
fi

PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH

if [[ "$mode" == "full" ]]; then
  wrapper_shell_profile=env-i-v1
else
  wrapper_shell_profile=development-smoke
fi
wrapper_shell_home=$HOME
wrapper_shell_pwd=$PWD
wrapper_shell_tmpdir=${TMPDIR:-/tmp}
wrapper_shell_shlvl=$SHLVL
wrapper_shell_optional_cf=${__CF_USER_TEXT_ENCODING-}

script_dir=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")" && builtin pwd -P)
repo_dir=$(CDPATH= cd -- "$script_dir/.." && builtin pwd -P)
python_bin="$repo_dir/.venv/bin/python"
bootstrap_py="$repo_dir/scripts/gate7_v3_isolated_bootstrap.py"
producer_py="$repo_dir/benchmarks/carma/gate7_v3_onnx_integration_benchmark.py"
auditor_py="$repo_dir/benchmarks/carma/gate7_v3_audit.py"

if [[ ! -x "$python_bin" ]]; then
  echo "Missing benchmark environment: $python_bin" >&2
  echo "Create .venv and install requirements-benchmark.lock before running." >&2
  exit 2
fi
if [[ ! -f "$bootstrap_py" ]]; then
  echo "Missing Gate 7 v3 isolated bootstrap: $bootstrap_py" >&2
  exit 2
fi

shift

if [[ "$mode" == "full" && $# -ne 0 ]]; then
  echo "Formal mode accepts no output override or additional runner arguments." >&2
  exit 2
fi

if [[ $# -gt 0 && "$1" != --* ]]; then
  output_dir=$1
  shift
else
  if [[ "$mode" == "full" ]]; then
    attempt_stamp=$(/bin/date -u +%Y%m%dT%H%M%SZ)
    output_dir="$repo_dir/artifacts/gate7-v3-onnx-attempts/attempt-$attempt_stamp-$$"
  else
    output_dir="$repo_dir/artifacts/gate7-v3-onnx-smoke"
  fi
fi

if [[ "$mode" == "smoke" ]]; then
  formal_root="$repo_dir/artifacts/gate7-v3-onnx-attempts"
  resolved_output="$("$python_bin" -I -S -P -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())' "$output_dir")"
  if [[ "$resolved_output" == "$formal_root" || "$resolved_output" == "$formal_root"/* ]]; then
    echo "Smoke output may not use the retained formal-attempt root." >&2
    exit 2
  fi
  for argument in "$@"; do
    case "$argument" in
      --mode|--mode=*|--prepared|--prepared=*|--contract|--contract=*|--output|--output=*|--fake-embedding|--child-*)
        echo "Smoke argument is reserved by the wrapper: $argument" >&2
        exit 2
        ;;
    esac
  done
fi

gate7_runtime_root="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/gate7-runtime.XXXXXX")"
gate7_pycache_root="$gate7_runtime_root/pycache"
gate7_tmp_root="$gate7_runtime_root/tmp"
/bin/mkdir -m 700 "$gate7_pycache_root" "$gate7_tmp_root"
cleanup_gate7_wrapper() {
  case "$gate7_runtime_root" in
    "${TMPDIR:-/tmp}"/gate7-runtime.*)
      /bin/rm -rf -- "$gate7_runtime_root"
      ;;
  esac
}
trap cleanup_gate7_wrapper EXIT

benchmark_env=(
  "HOME=$HOME"
  "PATH=/usr/bin:/bin:/usr/sbin:/sbin"
  "LANG=C.UTF-8"
  "LC_ALL=C.UTF-8"
  "LC_CTYPE=C.UTF-8"
  "TZ=UTC"
  "TMPDIR=$gate7_tmp_root"
  "TOKENIZERS_PARALLELISM=false"
  "HF_HUB_OFFLINE=1"
  "TRANSFORMERS_OFFLINE=1"
  "HF_HOME=$HOME/.cache/huggingface"
  "HF_HUB_CACHE=$HOME/.cache/huggingface/hub"
  "PYTHONNOUSERSITE=1"
  "PYTHONSAFEPATH=1"
  "PYTHONHASHSEED=0"
  "PYTHONDONTWRITEBYTECODE=1"
  "PYTHONPYCACHEPREFIX=$gate7_pycache_root"
  "CARMA_GATE7_WRAPPER_SHELL=gate7d-shell-v1"
  "CARMA_GATE7_WRAPPER_SHELL_PROFILE=$wrapper_shell_profile"
  "CARMA_GATE7_WRAPPER_SHELL_HOME=$wrapper_shell_home"
  "CARMA_GATE7_WRAPPER_SHELL_PWD=$wrapper_shell_pwd"
  "CARMA_GATE7_WRAPPER_SHELL_TMPDIR=$wrapper_shell_tmpdir"
  "CARMA_GATE7_WRAPPER_SHELL_SHLVL=$wrapper_shell_shlvl"
  "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF=$wrapper_shell_optional_cf"
  "CARMA_GATE7_FORMAL_ENTRYPOINT=gate7d-wrapper-v1"
  "CARMA_GATE7_LAUNCH_MODE=$mode"
  "CARMA_GATE7_WRAPPER_PID=$$"
  "CARMA_GATE7_WRAPPER_PATH=$script_dir/run_gate7_v3_onnx_integration_benchmark.sh"
  "CARMA_ONNX_WORKERS=1"
  "CARMA_ONNX_THREADS=1"
  "OMP_NUM_THREADS=1"
  "OPENBLAS_NUM_THREADS=1"
  "MKL_NUM_THREADS=1"
  "NUMEXPR_NUM_THREADS=1"
  "VECLIB_MAXIMUM_THREADS=1"
)

run_isolated() {
  /usr/bin/env -i "${benchmark_env[@]}" \
    "$python_bin" -S -P "$bootstrap_py" "$@"
}

runner_args=(
  --mode "$mode"
  --prepared "$repo_dir/artifacts/qqp-full/prepared"
  --contract "$repo_dir/docs/project/gate7-v3-remediation-contract.md"
  --output "$output_dir"
)

if [[ "$mode" == "smoke" ]]; then
  runner_args+=(--fake-embedding)
fi

set +e
run_isolated --role parent -- "$producer_py" \
  "${runner_args[@]}" "$@"
runner_status=$?
set -e

# Full mode performs its preterminal audit and prepares the terminal intent
# while the producer lock is held. The parent bootstrap postchecks, reacquires
# that lock, and appends the completion-bound TERMINAL before returning here.
# If a manifest exists, run the ordinary offline audit as well; otherwise
# preserve the producer/failure-retention exit status.
if [[ "$mode" == "full" && ! -f "$output_dir/manifest.json" ]]; then
  exit "$runner_status"
fi

if [[ "$runner_status" -gt 3 ]]; then
  exit "$runner_status"
fi

set +e
run_isolated --role auditor -- "$auditor_py" \
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
