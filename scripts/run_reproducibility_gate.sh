#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly BASELINE_COMMIT="c59fb3a6152a4458b2a070ca183b61c4b614095f"

unset BASH_ENV CDPATH ENV PYTHONHOME PYTHONPATH
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1

if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_CANDIDATE="${CARMA_PYTHON:-${PROJECT_ROOT}/.venv/bin/python}"
else
  PYTHON_CANDIDATE="${CARMA_PYTHON:-python3.12}"
fi
if ! command -v "${PYTHON_CANDIDATE}" >/dev/null 2>&1; then
  echo "CARMA_PYTHON is not executable: ${PYTHON_CANDIDATE}" >&2
  exit 2
fi
PYTHON_BIN="$("${PYTHON_CANDIDATE}" -I -c 'import sys; print(sys.executable)')"
readonly PYTHON_BIN

cd "${PROJECT_ROOT}"
SOURCE_COMMIT="$(git rev-parse --verify HEAD)"
readonly SOURCE_COMMIT
if [[ ! "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "HEAD did not resolve to a full commit ID" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain=v1 --untracked-files=all)" ]]; then
  echo "The reproducibility gate requires a clean exact-HEAD checkout." >&2
  git status --short >&2
  exit 2
fi
git cat-file -e "${BASELINE_COMMIT}^{commit}"
git merge-base --is-ancestor "${BASELINE_COMMIT}" "${SOURCE_COMMIT}"

EVIDENCE_ROOT="${CARMA_EVIDENCE_ROOT:-${PROJECT_ROOT}/artifacts}"
if [[ "${EVIDENCE_ROOT}" != /* ]]; then
  EVIDENCE_ROOT="${PROJECT_ROOT}/${EVIDENCE_ROOT}"
fi
EVIDENCE_ROOT="$("${PYTHON_BIN}" -c \
  'import os, sys; print(os.path.realpath(sys.argv[1]))' "${EVIDENCE_ROOT}")"
case "${EVIDENCE_ROOT}" in
  "${PROJECT_ROOT}/artifacts"|"${PROJECT_ROOT}/artifacts/"*) ;;
  "${PROJECT_ROOT}"|"${PROJECT_ROOT}/"*)
    echo "Evidence inside the checkout must stay under artifacts/: ${EVIDENCE_ROOT}" >&2
    exit 2
    ;;
esac
mkdir -p "${EVIDENCE_ROOT}"
EVIDENCE_ROOT="$(cd "${EVIDENCE_ROOT}" && pwd -P)"
readonly EVIDENCE_ROOT
readonly HOST_ROOT="${EVIDENCE_ROOT}/ci"

planned_outputs=(
  "${HOST_ROOT}"
  "${HOST_ROOT}/benchmark"
  "${HOST_ROOT}/host-verification.log"
  "${HOST_ROOT}/host-verification.json"
  "${HOST_ROOT}/host-install.log"
  "${HOST_ROOT}/host-packages.txt"
  "${HOST_ROOT}/requirements-project.lock"
  "${HOST_ROOT}/source-archive.sha256"
  "${EVIDENCE_ROOT}/container-reproducibility.json"
  "${EVIDENCE_ROOT}/container-source-commit.txt"
  "${EVIDENCE_ROOT}/container-image-id.txt"
  "${EVIDENCE_ROOT}/container-image-inspect.json"
  "${EVIDENCE_ROOT}/docker-build.log"
  "${EVIDENCE_ROOT}/requirements-project.lock"
  "${EVIDENCE_ROOT}/requirements-project.lock.sha256"
  "${EVIDENCE_ROOT}/source-archive.sha256"
)
for label in 1 2; do
  planned_outputs+=(
    "${EVIDENCE_ROOT}/docker-run-${label}"
    "${EVIDENCE_ROOT}/docker-run-${label}.log"
    "${EVIDENCE_ROOT}/docker-run-${label}.nontiming.log"
    "${EVIDENCE_ROOT}/docker-run-${label}.sha256"
    "${EVIDENCE_ROOT}/docker-run-${label}.inspect.json"
  )
done
for output in "${planned_outputs[@]}"; do
  if [[ -e "${output}" || -L "${output}" ]]; then
    echo "Refusing to overwrite retained evidence: ${output}" >&2
    exit 2
  fi
done

BUILD_CONTEXT=""
HOST_ENV=""
SOURCE_ARCHIVE=""
CONTAINER_IDS=()
cleanup() {
  local container_id
  for container_id in "${CONTAINER_IDS[@]}"; do
    docker rm --force "${container_id}" >/dev/null 2>&1 || true
  done
  if [[ -n "${BUILD_CONTEXT}" && -d "${BUILD_CONTEXT}" \
        && "$(basename "${BUILD_CONTEXT}")" == carma-gate-context.* ]]; then
    rm -rf -- "${BUILD_CONTEXT}"
  fi
  if [[ -n "${HOST_ENV}" && -d "${HOST_ENV}" \
        && "$(basename "${HOST_ENV}")" == carma-gate-host.* ]]; then
    rm -rf -- "${HOST_ENV}"
  fi
  if [[ -n "${SOURCE_ARCHIVE}" && -f "${SOURCE_ARCHIVE}" \
        && "$(basename "${SOURCE_ARCHIVE}")" == carma-gate-source.*.tar ]]; then
    rm -f -- "${SOURCE_ARCHIVE}"
  fi
}
trap cleanup EXIT

export PATH="$(dirname "${PYTHON_BIN}"):${PATH}"
export PIP_CONFIG_FILE=/dev/null
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_INDEX_URL=https://pypi.org/simple
export PIP_NO_INPUT=1

echo "[repro] exact interpreter and dependency graph"
"${PYTHON_BIN}" -I - <<'PY'
import platform
import sys

if platform.python_implementation() != "CPython" or sys.version_info[:3] != (3, 12, 13):
    raise SystemExit(
        "local reproducibility evidence requires CPython 3.12.13; found %s %s"
        % (platform.python_implementation(), platform.python_version())
    )
PY
"${PYTHON_BIN}" -I scripts/generate_hashed_locks.py --check

echo "[repro] exact-HEAD source archive"
mkdir -p "${HOST_ROOT}"
if [[ -L "${HOST_ROOT}" \
      || "$(cd "${HOST_ROOT}" && pwd -P)" != "${EVIDENCE_ROOT}/ci" ]]; then
  echo "Host evidence root is not a canonical real child: ${HOST_ROOT}" >&2
  exit 2
fi
BUILD_CONTEXT="$(mktemp -d "${TMPDIR:-/tmp}/carma-gate-context.XXXXXX")"
SOURCE_ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/carma-gate-source.XXXXXX.tar")"
git -c tar.umask=0002 archive --format=tar \
  --output="${SOURCE_ARCHIVE}" "${SOURCE_COMMIT}"
SOURCE_ARCHIVE_SHA256="$("${PYTHON_BIN}" -I -c \
  'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' \
  "${SOURCE_ARCHIVE}")"
readonly SOURCE_ARCHIVE_SHA256
printf '%s  source-%s.tar\n' "${SOURCE_ARCHIVE_SHA256}" "${SOURCE_COMMIT}" \
  > "${HOST_ROOT}/source-archive.sha256"
cp "${HOST_ROOT}/source-archive.sha256" \
  "${EVIDENCE_ROOT}/source-archive.sha256"
tar -xf "${SOURCE_ARCHIVE}" -C "${BUILD_CONTEXT}"
"${PYTHON_BIN}" -I "${BUILD_CONTEXT}/scripts/generate_hashed_locks.py" \
  --project-root "${BUILD_CONTEXT}" --check

echo "[repro] clean hashed host environment"
HOST_ENV="$(mktemp -d "${TMPDIR:-/tmp}/carma-gate-host.XXXXXX")"
"${PYTHON_BIN}" -I -m venv "${HOST_ENV}"
HOST_PYTHON="${HOST_ENV}/bin/python"
readonly HOST_PYTHON
(
  cd "${BUILD_CONTEXT}"
  "${HOST_PYTHON}" -I -m pip --isolated --disable-pip-version-check install \
    --no-input \
    --index-url https://pypi.org/simple \
    --require-hashes \
    --only-binary=:all: \
    --requirement requirements-project.lock &&
  "${HOST_PYTHON}" -I -m pip --isolated --disable-pip-version-check install \
    --no-input --no-index --no-deps --no-build-isolation --editable . &&
  "${HOST_PYTHON}" -I -m pip check &&
  echo "[install] pip check PASS"
) 2>&1 | tee "${HOST_ROOT}/host-install.log"
"${HOST_PYTHON}" -I -m pip list --format=freeze \
  > "${HOST_ROOT}/host-packages.txt"

echo "[repro] clean host verification"
export PATH="${HOST_ENV}/bin:${PATH}"
(
  cd "${BUILD_CONTEXT}"
  CARMA_ARTIFACT_DIR="${HOST_ROOT}" PYTHON_BIN="${HOST_PYTHON}" \
    bash scripts/verify_project.sh
) 2>&1 | tee "${HOST_ROOT}/host-verification.log"
"${HOST_PYTHON}" -I "${PROJECT_ROOT}/scripts/write_reproducibility_evidence.py" \
  --project-root "${PROJECT_ROOT}" host \
  --evidence-root "${HOST_ROOT}" \
  --execution-root "${BUILD_CONTEXT}" \
  --expected-source-commit "${SOURCE_COMMIT}"

echo "[repro] exact-HEAD linux/amd64 image build"
readonly IMAGE_TAG="carma-project:repro-${SOURCE_COMMIT:0:12}-$$"
printf '%s\n' "${SOURCE_COMMIT}" > "${EVIDENCE_ROOT}/container-source-commit.txt"
docker build \
  --no-cache \
  --pull \
  --progress=plain \
  --platform linux/amd64 \
  --file "${BUILD_CONTEXT}/Dockerfile.project" \
  --tag "${IMAGE_TAG}" "${BUILD_CONTEXT}" \
  2>&1 | tee "${EVIDENCE_ROOT}/docker-build.log"
docker image inspect --format '{{.Id}}' "${IMAGE_TAG}" \
  > "${EVIDENCE_ROOT}/container-image-id.txt"
docker image inspect "${IMAGE_TAG}" \
  > "${EVIDENCE_ROOT}/container-image-inspect.json"

echo "[repro] two fresh network-isolated linux/amd64 containers"
for label in 1 2; do
  run_root="${EVIDENCE_ROOT}/docker-run-${label}"
  mkdir -p "${run_root}"
  chmod 0777 "${run_root}"
  container_id="$(docker create \
    --network none \
    --cap-drop ALL \
    --security-opt=no-new-privileges=true \
    --platform linux/amd64 \
    --mount "type=bind,src=${run_root},dst=/artifacts" \
    --env CARMA_ARTIFACT_DIR=/artifacts \
    "${IMAGE_TAG}")"
  CONTAINER_IDS+=("${container_id}")
  set +e
  docker start --attach "${container_id}" \
    2>&1 | tee "${EVIDENCE_ROOT}/docker-run-${label}.log"
  run_pipeline_status=("${PIPESTATUS[@]}")
  set -e
  run_status="${run_pipeline_status[0]:-1}"
  tee_status="${run_pipeline_status[1]:-1}"
  docker inspect "${container_id}" \
    > "${EVIDENCE_ROOT}/docker-run-${label}.inspect.json"
  docker rm "${container_id}" >/dev/null
  if [[ "${run_status}" -ne 0 || "${tee_status}" -ne 0 ]]; then
    echo "Container ${label} pipeline failed: docker=${run_status} tee=${tee_status}" >&2
    if [[ "${run_status}" -ne 0 ]]; then
      exit "${run_status}"
    fi
    exit "${tee_status}"
  fi
done

echo "[repro] derive, compare, and record paired-container evidence"
"${HOST_PYTHON}" -I "${PROJECT_ROOT}/scripts/write_reproducibility_evidence.py" \
  --project-root "${PROJECT_ROOT}" container \
  --evidence-root "${EVIDENCE_ROOT}" \
  --execution-root "${BUILD_CONTEXT}" \
  --expected-source-commit "${SOURCE_COMMIT}"

printf '[repro] PASS\nhost evidence: %s\ncontainer evidence: %s\n' \
  "${HOST_ROOT}/host-verification.json" \
  "${EVIDENCE_ROOT}/container-reproducibility.json"
printf 'analyzer inputs: --host-verification %q --container-reproducibility %q\n' \
  "${HOST_ROOT}/host-verification.json" \
  "${EVIDENCE_ROOT}/container-reproducibility.json"
