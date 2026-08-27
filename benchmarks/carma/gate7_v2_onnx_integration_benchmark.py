"""Prospective real-text ONNX system benchmark for Gate 7.

This module intentionally does not modify or reinterpret the historical
precomputed-vector integration runs.  It builds a new, versioned evidence
bundle whose measured request path starts with real text and includes the
pinned GPTCache ALBERT ONNX embedding before SQLite/FAISS lookup and eviction
policy work.

The public process is an orchestrator.  Every policy/seed block runs in a fresh
child process and a fresh temporary SQLite/FAISS directory.  The orchestrator
samples child resources from outside the measured process and writes buffered
request records only after the child has finished.
"""

from __future__ import annotations

import argparse
import base64
import csv
import fcntl
import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import re
import resource
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path
from typing import Any, Callable, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_RUNTIME_SENTINEL = getattr(
    sys, "_gate7_v2_bootstrap_attestation", None
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import psutil

from benchmarks.carma.integration_benchmark import (
    IntegrationConfig,
    _build_manager,
    _verify_storage,
)
from benchmarks.carma.qqp import (
    MODEL_REPOSITORY,
    MODEL_REVISION,
    TOKENIZER_REPOSITORY,
    TOKENIZER_REVISION,
)
from gptcache import Cache, Config
from gptcache.adapter.adapter import adapt


SCHEMA_VERSION = "carma-gate7-onnx-v2"
ATTEMPT_LEDGER_SCHEMA_VERSION = "carma-gate7-attempt-ledger-v3"
TERMINAL_INTENT_SCHEMA_VERSION = "carma-gate7-terminal-intent-v1"
BOOTSTRAP_COMPLETION_SCHEMA_VERSION = "carma-gate7-bootstrap-completion-v1"
DEPENDENCY_ATTESTATION_SCHEMA_VERSION = "carma-gate7-dependency-attestation-v1"
FORMAL_SOURCE_ANCHOR_SCHEMA_VERSION = "carma-gate7-formal-source-anchor-v1"
EXPERIMENT_ID = "gate7c-onnx-v2"
POLICIES = ("LRU", "LFU", "CARMA")
FULL_SEEDS = (20261001, 20261002, 20261003, 20261004, 20261005)
PRETERMINAL_STATUS_EXIT_STATUS = {
    "pass": 0,
    "fail": 1,
    "pending": 2,
    "invalid": 3,
}
PINNED_FULL_TRACE_SHA256 = {
    20261001: "25f773f174d14648c254aa355d9f7a983eafa6acad9c852f1af934ae42635c1b",
    20261002: "fc20d64d878d27bfcb0298bff35419a6c9aeae9ecbc686e314f61122593ce50f",
    20261003: "199178c7739cdf5d5682cf60963b7228c7e6f6bc1394dbf297f8fc8e4a3b386c",
    20261004: "806a58004bfd3fecf3b97603639ec279b338967c64e99842df36f17e704f9cc4",
    20261005: "a680defccd6e9973a65c9b34c37c44d62edfdf67a69ffcf260d7d59dbd9d270f",
}
PINNED_FULL_SEMANTIC_INDEX_SHA256 = {
    20261001: "bf7496e49b0a2adda8414aff26ef63b4771f3791dc411084a310787dd5af6832",
    20261002: "f96ef44b7a5d8cc5fc55a6024d516c6ad585c8b7519628e7f38e9dc085c05546",
    20261003: "c5d0a1b58453cfedb89ddd5bb890a4442c00c69d00a7647d52770eaf26240abd",
    20261004: "07897d6572e7925c0b540f6c20ce76f692ccb29f979844cb41b5a1945ce9fd6a",
    20261005: "7ccac25843a51d3c2417eaf69a5411510cfc0fe7f5d6876efb7acf6d30496a41",
}
DEFAULT_CONTRACT = PROJECT_ROOT / "docs" / "project" / "gate7-v2-remediation-contract.md"
DEFAULT_PREPARED = PROJECT_ROOT / "artifacts" / "qqp-full" / "prepared"
DEFAULT_QQP_ARCHIVE = (
    PROJECT_ROOT / "examples" / "benchmark" / "similiar_qqp_full.json.gz"
)
BENCHMARK_LOCK = PROJECT_ROOT / "requirements-benchmark.lock"
FORMAL_ATTEMPT_ROOT = PROJECT_ROOT / "artifacts" / "gate7-v2-onnx-attempts"
FORMAL_PREFLIGHT_FAILURE_ROOT = (
    PROJECT_ROOT / "artifacts" / "gate7-v2-onnx-preflight-failures"
)
POLICY_ORDER_NAMESPACE = "gate7b-policy-base-v1"
FORMAL_SOURCE_TAG = "gate7c-onnx-v2-formal-source"
FORMAL_SOURCE_REMOTE = "submission"
FORMAL_SOURCE_REMOTE_URL = (
    "https://github.com/MatanGoldfarB/gptcache-online-policy.git"
)
FORMAL_SOURCE_TAG_PAYLOAD_PATH = "source/formal-source-tag.raw"
DEPENDENCY_ATTESTATION_PATH = "source/dependency-attestation.json"
RETAINED_QQP_ARCHIVE_PATH = "source/similiar_qqp_full.json.gz"
RETAINED_PREPARED_DIRECTORY = "source/prepared"
FORMAL_ENTRYPOINT_SCHEMA_VERSION = "carma-gate7-formal-entrypoint-attestation-v1"
FORMAL_ENTRYPOINT_MARKER = "gate7c-wrapper-v1"
ISOLATED_BOOTSTRAP_SCHEMA_VERSION = "carma-gate7-isolated-bootstrap-v1"
WRAPPER_SHELL_STARTUP_SCHEMA_VERSION = (
    "carma-gate7-wrapper-shell-startup-v1"
)
PREIMPORT_SOURCE_SCHEMA_VERSION = "carma-gate7-preimport-source-v1"
ISOLATED_BOOTSTRAP = PROJECT_ROOT / "scripts" / "gate7_v2_isolated_bootstrap.py"
WARMUP_SCHEMA_VERSION = "carma-gate7-warmup-v1"
FORMAL_WRAPPER = PROJECT_ROOT / "scripts" / "run_gate7_v2_onnx_integration_benchmark.sh"
PINNED_CPYTHON_VERSION = "3.12.13"
PINNED_BENCHMARK_LOCK_SHA256 = (
    "8723b1875ff08ff16691e7b9d7166fc6b089fb362c8d27f2f43dde34be04d8ad"
)
PINNED_BENCHMARK_LOCK_PIN_COUNT = 50
PINNED_BENCHMARK_PIN_MAP_SHA256 = (
    "a9ad2cf1ad4db90d5c43d01eaaad84b739153616e31d5404dd817b47f195a759"
)
PINNED_LOCKED_RECORD_HASHED_FILE_COUNT = 10270
PINNED_LOCKED_RECORD_HASHED_BYTES = 362595746
PINNED_LOCKED_RECORD_AGGREGATE_SHA256 = (
    "1b59e03f6a6d52a8b64adf8f11eb8ed36c40f807d53248965a00c733f8dca2c3"
)
PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY = {
    "record_sha256": (
        "873280782d16563eef2982efbead80814577c01aea0253cea060d7cb3cc5b140"
    ),
    "hashed_file_count": 11,
    "hashed_bytes": 29856,
    "hashed_files_sha256": (
        "29bf983930625eacdddedcd24953d1849e9ebf923f233c09bd38506d67391b91"
    ),
}
LOCAL_GPTCACHE_VERSION = "0.1.44"
FORMAL_REQUIRED_ENVIRONMENT = {
    "PYTHONHASHSEED": "0",
    "TOKENIZERS_PARALLELISM": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "CARMA_GATE7_WRAPPER_SHELL": "gate7c-shell-v1",
    "CARMA_GATE7_FORMAL_ENTRYPOINT": "gate7c-wrapper-v1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONSAFEPATH": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "CARMA_ONNX_WORKERS": "1",
    "CARMA_ONNX_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "LC_CTYPE": "C.UTF-8",
    "TZ": "UTC",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
}
FORMAL_FORBIDDEN_ENVIRONMENT = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP")
FORMAL_FORBIDDEN_CACHE_ENVIRONMENT = (
    "TRANSFORMERS_CACHE",
    "XDG_CACHE_HOME",
    "PYTHONUSERBASE",
)
FORMAL_DYNAMIC_ENVIRONMENT = (
    "HOME",
    "TMPDIR",
    "PYTHONPYCACHEPREFIX",
    "HF_HOME",
    "HF_HUB_CACHE",
    "CARMA_GATE7_LAUNCH_MODE",
    "CARMA_GATE7_WRAPPER_SHELL_PROFILE",
    "CARMA_GATE7_WRAPPER_SHELL_HOME",
    "CARMA_GATE7_WRAPPER_SHELL_PWD",
    "CARMA_GATE7_WRAPPER_SHELL_TMPDIR",
    "CARMA_GATE7_WRAPPER_SHELL_SHLVL",
    "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF",
    "CARMA_GATE7_WRAPPER_PID",
    "CARMA_GATE7_WRAPPER_PATH",
)
FORMAL_BOOTSTRAP_EVIDENCE_ENVIRONMENT = (
    "CARMA_GATE7_BOOTSTRAP_SCHEMA",
    "CARMA_GATE7_BOOTSTRAP_ROLE",
    "CARMA_GATE7_BOOTSTRAP_ATTESTATION_SHA256",
)
FORMAL_OPTIONAL_OS_ENVIRONMENT = ("__CF_USER_TEXT_ENCODING",)
PINNED_MODEL_FILES = {
    "model.onnx": "a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8",
}
PINNED_MODEL_DIGEST_SHA256 = (
    "af8dd7ee021644802c30f27709e46ad77e65425d527ecdca8346995dea83d7ce"
)
PINNED_TOKENIZER_FILES = {
    "config.json": "69765de9af37e704755cab37bee53f251465c269ae3f0c95436ab250dd11e342",
    "special_tokens_map.json": "129fed06908ddcc3e36105e41d753ff0b934e5cfb2e451ca0a48904acef41863",
    "spiece.model": "fefb02b667a6c5c2fe27602d28e5fb3428f66ab89c7d6f388e7c8d44a02d0336",
    "tokenizer.json": "d0a881fece9b11d4f8003a08ac7d8d65409e3aa573fc385faa8708cdd5a77087",
    "tokenizer_config.json": "95f31ea415a8e447b1e2ca05b897a05c1f5857b0643579d42dbec5ac5c2555c1",
}
PINNED_TOKENIZER_DIGEST_SHA256 = (
    "41f1aee1afa8c01eecd6f60e8097836cb8e97bae95bf2f7fe34d85c5764f9deb"
)
# Compatibility scalar for run-ID construction; the formal asset identity is
# the exact file map plus its aggregate digest above.
PINNED_MODEL_SHA256 = PINNED_MODEL_FILES["model.onnx"]
PINNED_ARCHIVE_SHA256 = "1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58"
PINNED_PAIRS_SHA256 = "c84d9897bd4838e8c12f45d59e04401031bbd5fa533570490db016cf40235126"
PINNED_TEXTS_SHA256 = "645ece94cebf36d1d37a66410d925d7d2252b6d39dd42b473e866289d1576dc3"
HISTORICAL_BASELINE_COMMIT = "021f421e80d76ab6cde10e21cff909bda236728d"
# Frozen independently after the v2 remediation contract review.
PINNED_CONTRACT_SHA256 = "4cb2d289bf9516e73bdc53c3f021ab9dc0e6b850c243e38bc57cc49244573f30"

V1_PRESERVATION_MANIFEST = (
    PROJECT_ROOT / "docs" / "project" / "evidence" / "gate7-v1-invalid-attempt.json"
)
V1_PRESERVATION_ARCHIVE = (
    PROJECT_ROOT / "docs" / "project" / "evidence" / "gate7-v1-invalid-attempt.tar.gz"
)
PINNED_V1_PRESERVATION_MANIFEST_SHA256 = (
    "b7ee1befd512d1582c0816152b9b87c1cbed91ac3a924bbbebc2bcedc3c6dee1"
)
PINNED_V1_PRESERVATION_ARCHIVE_SHA256 = (
    "cb326101dcc8323575be376d923630cfda6191c2e7adbc3b7fd6b01acf6b9147"
)
PINNED_V1_PRESERVATION_ARCHIVE_BYTES = 484539
PINNED_V1_FAILURE_SHA256 = (
    "41a7af508e769f24a91e72b221906fd7e50c6d20176032a9d58590ba9feb9f12"
)
PINNED_V1_LEDGER_SHA256 = (
    "96539252388659e779ac09014895886156e9f405020ce278527b82bfea22aeb7"
)
GATE2_V2_SELECTION = (
    PROJECT_ROOT / "docs" / "project" / "evidence" / "qqp-v2-threshold-selection.json"
)
GATE2_V2_RESULT = (
    PROJECT_ROOT / "docs" / "project" / "evidence" / "qqp-v2-result.json"
)
GATE2_V2_THRESHOLDS = (
    PROJECT_ROOT / "docs" / "project" / "evidence" / "qqp-v2-calibration-thresholds.csv"
)
PINNED_GATE2_V2_SELECTION_SHA256 = (
    "eb53e05e1816765fb93f2bed05bd01e2ce233c98879a20838f6853b97c1bd08c"
)
PINNED_GATE2_V2_RESULT_SHA256 = (
    "3544e53f45f6fdd540a0489c4bc5a0a5750fcec96bb93204b9bc7df43016eca1"
)
PINNED_GATE2_V2_THRESHOLDS_SHA256 = (
    "6cb322dc79b32fca9b3200ca16e1f28136589860b3e9aa558d70b64d44c7f038"
)

SEMANTIC_RELATIONS = (
    "not_applicable",
    "positive_same_component",
    "negative_direct",
    "negative_component_derived",
    "unlabeled_cross_component",
)
SEMANTIC_HIT_RELATIONS = SEMANTIC_RELATIONS[1:]

# Child-local references only. They let the top-level child exception handler
# serialize the completed request prefix after an unexpected in-loop failure,
# without adding any checkpoint I/O to the measured request path.
_CHILD_DIAGNOSTIC_STATE: Dict[str, Any] = {}

EXCLUSIVE_STAGES = (
    "preprocess",
    "tokenize",
    "onnx_inference",
    "embedding_postprocess",
    "faiss_search",
    "faiss_mutation",
    "sqlite_read",
    "sqlite_write",
    "similarity_evaluation",
    "policy_exclusive",
    "response_materialization",
)
SUMMARY_STAGES = (
    "end_to_end",
    "post_embedding",
    "embedding",
    "faiss",
    "sqlite",
    "policy_exclusive",
    "policy_inclusive",
    "response_return",
    "residual",
)

OUTCOME_NAMES = (
    "hit",
    "admitted_without_eviction",
    "admitted_with_eviction",
    "rejected",
)
OUTCOME_REPORT_NAMES = ("all",) + OUTCOME_NAMES

OUTCOME_LATENCY_FIELDS: Dict[str, str] = {
    "text_preprocess_tokenize": "text_preprocess_tokenize_ns",
    "onnx_inference": "onnx_inference_ns",
    "embedding_postprocess": "embedding_postprocess_ns",
    "faiss_search": "faiss_search_ns",
    "sqlite_read": "sqlite_read_ns",
    "similarity_decision": "similarity_decision_ns",
    "policy_exclusive": "policy_exclusive_ns",
    "sqlite_write": "sqlite_write_ns",
    "faiss_mutation": "faiss_mutation_ns",
    "response_return": "response_return_ns",
    "residual": "residual_ns",
    "embedding": "embedding_ns",
    "cache_management": "cache_management_ns",
    "post_embedding": "post_embedding_total_ns",
    "request_total": "request_total_ns",
}


@dataclass(frozen=True)
class Gate7Config:
    """Configuration shared verbatim by every policy process."""

    mode: str
    requests: int
    capacity: int
    hit_threshold: float = 0.97
    topic_threshold: float = 0.70
    cell_threshold: float = 0.97
    demand_half_life: float = 500.0
    quota_strength: float = 1.0
    ghost_support_threshold: float = 1.5
    admission_margin: float = 1.05
    centroid_alpha: float = 0.05
    entry_hit_weight: float = 0.25
    onnx_intra_threads: int = 1
    onnx_inter_threads: int = 1
    warmup_requests: int = 20
    resource_interval_ms: int = 100
    max_length: int = 512
    fake_embedding: bool = False

    def validate(self) -> None:
        if self.mode not in ("smoke", "full"):
            raise ValueError("mode must be smoke or full")
        if self.requests < 10 or self.requests % 10:
            raise ValueError("requests must be at least 10 and divisible by 10")
        if self.capacity < 2:
            raise ValueError("capacity must be at least 2")
        if not 0.0 <= self.hit_threshold <= 1.0:
            raise ValueError("hit threshold must be in [0, 1]")
        if min(self.onnx_intra_threads, self.onnx_inter_threads) < 1:
            raise ValueError("ONNX thread counts must be positive")
        if self.warmup_requests < 1:
            raise ValueError("warmup requests must be positive")
        if self.resource_interval_ms < 10:
            raise ValueError("resource interval must be at least 10 ms")
        if self.max_length != 512:
            raise ValueError("the pinned ONNX model requires max_length=512")
        if self.mode == "full" and self.fake_embedding:
            raise ValueError("formal full mode forbids fake embeddings")
        if self.mode == "full":
            expected = {
                "requests": 3000,
                "capacity": 100,
                "hit_threshold": 0.97,
                "topic_threshold": 0.70,
                "cell_threshold": 0.97,
                "demand_half_life": 500.0,
                "quota_strength": 1.0,
                "ghost_support_threshold": 1.5,
                "admission_margin": 1.05,
                "centroid_alpha": 0.05,
                "entry_hit_weight": 0.25,
                "onnx_intra_threads": 1,
                "onnx_inter_threads": 1,
                "warmup_requests": 20,
                "resource_interval_ms": 100,
                "max_length": 512,
            }
            observed = asdict(self)
            mismatches = {
                key: (observed[key], value)
                for key, value in expected.items()
                if observed[key] != value
            }
            if mismatches:
                raise ValueError("formal full mode differs from frozen contract: %r" % mismatches)


def _assert_launch_mode(mode: str) -> None:
    """Bind the parsed producer mode to the wrapper/bootstrap launch mode."""

    observed = os.environ.get("CARMA_GATE7_LAUNCH_MODE")
    bootstrapped = getattr(sys, "_gate7_v2_bootstrap_attestation", None) is not None
    if observed is None:
        if mode == "full" or bootstrapped:
            raise RuntimeError("Gate 7 launch mode is missing")
        # Direct, unbootstrapped smoke is a development/test-only interface.
        return
    if observed not in ("full", "smoke") or observed != mode:
        raise RuntimeError(
            "Gate 7 parsed mode differs from the wrapper launch mode"
        )


class ChildExecutionError(RuntimeError):
    """A failed isolated child with the external samples retained so far."""

    def __init__(
        self,
        message: str,
        returncode: int,
        stdout: str,
        stderr: str,
        samples: Sequence[Mapping[str, Any]],
        diagnostic: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.returncode = int(returncode)
        self.stdout = stdout
        self.stderr = stderr
        self.samples = [dict(sample) for sample in samples]
        self.diagnostic = dict(diagnostic) if diagnostic is not None else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact_identity(path: Path, rows: Optional[int] = None) -> Dict[str, Any]:
    identity: Dict[str, Any] = {
        "sha256": sha256_file(path),
        "bytes": int(path.stat().st_size),
    }
    if rows is not None:
        identity["rows"] = int(rows)
    return identity


def _artifact_row_count(path: Path) -> int:
    """Mirror the offline auditor's retained-artifact row definition."""

    path = Path(path)
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            next(reader, None)
            return sum(1 for _ in reader)
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as source:
            return sum(1 for line in source if line.strip())
    with path.open("rb") as source:
        return sum(1 for _ in source)


def _v1_preservation_identity() -> Dict[str, Any]:
    """Return and verify the immutable v1 failure archive bound into v2."""

    if not V1_PRESERVATION_MANIFEST.is_file():
        raise RuntimeError("the preserved Gate 7 v1 identity manifest is missing")
    if not V1_PRESERVATION_ARCHIVE.is_file():
        raise RuntimeError("the preserved Gate 7 v1 evidence archive is missing")
    manifest_identity = _artifact_identity(V1_PRESERVATION_MANIFEST)
    archive_identity = _artifact_identity(V1_PRESERVATION_ARCHIVE)
    if manifest_identity["sha256"] != PINNED_V1_PRESERVATION_MANIFEST_SHA256:
        raise RuntimeError("the preserved Gate 7 v1 identity manifest changed")
    if (
        archive_identity["sha256"] != PINNED_V1_PRESERVATION_ARCHIVE_SHA256
        or archive_identity["bytes"] != PINNED_V1_PRESERVATION_ARCHIVE_BYTES
    ):
        raise RuntimeError("the preserved Gate 7 v1 evidence archive changed")
    try:
        declaration = json.loads(V1_PRESERVATION_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("the preserved Gate 7 v1 identity is unreadable") from exc
    if (
        not isinstance(declaration, dict)
        or declaration.get("schema_version")
        != "carma-gate7-v1-preservation-v1"
        or not isinstance(declaration.get("archive"), dict)
        or declaration["archive"].get("sha256")
        != PINNED_V1_PRESERVATION_ARCHIVE_SHA256
        or declaration["archive"].get("bytes")
        != PINNED_V1_PRESERVATION_ARCHIVE_BYTES
        or not isinstance(declaration.get("attempt"), dict)
        or declaration["attempt"].get("failure_sha256")
        != PINNED_V1_FAILURE_SHA256
        or not isinstance(declaration.get("files"), dict)
        or declaration["files"].get("gate7-onnx-attempts/attempt-ledger.jsonl", {}).get(
            "sha256"
        )
        != PINNED_V1_LEDGER_SHA256
    ):
        raise RuntimeError("the preserved Gate 7 v1 identity is inconsistent")
    return {
        "manifest_path": _project_path(V1_PRESERVATION_MANIFEST),
        "manifest_sha256": manifest_identity["sha256"],
        "archive_path": _project_path(V1_PRESERVATION_ARCHIVE),
        "archive_sha256": archive_identity["sha256"],
        "formal_root": "artifacts/gate7-onnx-attempts",
        "attempt_ledger_sha256": PINNED_V1_LEDGER_SHA256,
        "terminal_attempt_id": declaration["attempt"].get("attempt_id"),
        "terminal_entry_sha256": declaration["attempt"].get(
            "terminal_entry_sha256"
        ),
        "failure_sha256": PINNED_V1_FAILURE_SHA256,
    }


def _gate2_v2_evidence() -> Dict[str, Any]:
    """Verify the Gate 2 v2 result whose non-selection constrains Gate 7."""

    expected = (
        (GATE2_V2_SELECTION, PINNED_GATE2_V2_SELECTION_SHA256),
        (GATE2_V2_RESULT, PINNED_GATE2_V2_RESULT_SHA256),
        (GATE2_V2_THRESHOLDS, PINNED_GATE2_V2_THRESHOLDS_SHA256),
    )
    artifacts: Dict[str, Any] = {}
    for path, expected_sha256 in expected:
        if not path.is_file():
            raise RuntimeError("Gate 2 v2 evidence is missing: %s" % path.name)
        identity = _artifact_identity(path)
        if identity["sha256"] != expected_sha256:
            raise RuntimeError("Gate 2 v2 evidence changed: %s" % path.name)
        artifacts[_project_path(path)] = identity
    try:
        selection = json.loads(GATE2_V2_SELECTION.read_text(encoding="utf-8"))
        result = json.loads(GATE2_V2_RESULT.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Gate 2 v2 evidence is unreadable") from exc
    if (
        not isinstance(selection, dict)
        or not isinstance(result, dict)
        or selection.get("selected_threshold") is not None
        or selection.get("selection_uses_heldout") is not False
        or selection.get("heldout_endpoints_resolved") is not False
        or selection.get("heldout_similarities_computed") is not False
        or selection.get("heldout_similarity_count") != 0
        or selection.get("heldout_rows_discovered_by_split_only_scan") != 56963
        or result.get("status") != "no_threshold_met_precision_gate"
        or result.get("selected_threshold") is not None
        or result.get("selection_uses_heldout") is not False
        or result.get("heldout_endpoints_resolved") is not False
        or result.get("heldout_similarities_computed") is not False
        or result.get("heldout_similarity_count") != 0
        or result.get("test_pairs_not_evaluated") != 56963
    ):
        raise RuntimeError("Gate 2 v2 evidence contradicts its frozen non-selection")
    return {
        "status": "no_threshold_met_precision_gate",
        "selected_threshold": None,
        "heldout_evaluated": False,
        "artifacts": artifacts,
    }


def _source_identities() -> Dict[str, Any]:
    paths = (
        "benchmarks/carma/gate7_v2_onnx_integration_benchmark.py",
        "benchmarks/carma/gate7_v2_trace.py",
        "benchmarks/carma/gate7_v2_audit.py",
        "benchmarks/carma/qqp.py",
        "benchmarks/carma/qqp_v2.py",
        "benchmarks/carma/integration_benchmark.py",
        "scripts/run_gate7_v2_onnx_integration_benchmark.sh",
        "scripts/gate7_v2_isolated_bootstrap.py",
        "scripts/verify_project.sh",
        "tests/project_tests/test_gate7_v2_trace.py",
        "tests/project_tests/test_gate7_v2_onnx_integration.py",
        "tests/project_tests/test_gate7_v2_audit.py",
        "tests/project_tests/test_qqp_v2.py",
        "tests/project_tests/test_gate7_v1_preservation.py",
        "docs/project/evidence/gate7-v1-invalid-attempt.json",
        "docs/project/evidence/gate7-v1-invalid-attempt.tar.gz",
        "docs/project/evidence/qqp-v2-threshold-selection.json",
        "docs/project/evidence/qqp-v2-result.json",
        "docs/project/evidence/qqp-v2-calibration-thresholds.csv",
        "examples/benchmark/similiar_qqp_full.json.gz",
        "requirements-benchmark.lock",
        "requirements-project.lock",
        "Dockerfile.project",
    )
    identities: Dict[str, Any] = {}
    for relative in paths:
        path = PROJECT_ROOT / relative
        identities[relative] = (
            _artifact_identity(path) if path.is_file() else None
        )
    return identities


def _project_path(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(Path(path).resolve())


def _atomic_write(path: Path, writer: Callable[[Any], None], newline: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name,
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline=newline) as output:
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    def write(output: Any) -> None:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write("\n")

    _atomic_write(path, write)


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    def write(output: Any) -> None:
        for record in records:
            output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    _atomic_write(path, write)


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
    def write(output: Any) -> None:
        writer = csv.DictWriter(output, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fields})

    _atomic_write(path, write, newline="")


def _attempt_id_for(started_at_utc: datetime, head_commit: str) -> str:
    return "%s-%s" % (
        started_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        head_commit[:12],
    )


def _parse_attempt_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _valid_prior_record(
    value: Any, filename: str, directory: str
) -> bool:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("experiment_id") != EXPERIMENT_ID
        or not isinstance(value.get("config"), dict)
        or value["config"].get("mode") != "full"
        or not isinstance(value.get("prior_attempts"), list)
        or not isinstance(value.get("attempt_ledger"), dict)
        or not _formal_source_anchor_is_structurally_valid(
            value.get("formal_source_anchor")
        )
        or not isinstance(value.get("dependency_attestation"), dict)
        or not isinstance(
            value["dependency_attestation"].get("attestation_sha256"), str
        )
        or not isinstance(value.get("source_snapshot_sha256"), str)
        or not isinstance(value.get("entrypoint_attestation"), dict)
        or not isinstance(value.get("power_observations"), dict)
        or not isinstance(value.get("retained_inputs"), dict)
    ):
        return False
    expected_kind = (
        "prospective_gate7_followup"
        if filename == "manifest.json"
        else "prospective_gate7_failed_attempt"
    )
    if value.get("kind") != expected_kind:
        return False
    if filename == "manifest.json" and value.get("formal_claimable_mode") is not True:
        return False
    if filename == "attempt-failure.json" and value.get("status") != "invalid":
        return False
    started_at = _parse_attempt_timestamp(value.get("started_at_utc"))
    git = value.get("git")
    head_commit = git.get("head_commit") if isinstance(git, dict) else None
    if (
        started_at is None
        or not isinstance(head_commit, str)
        or len(head_commit) != 40
        or any(character not in "0123456789abcdef" for character in head_commit)
        or value.get("attempt_id") != _attempt_id_for(started_at, head_commit)
    ):
        return False
    attempt_policy = value.get("attempt_policy")
    return (
        isinstance(attempt_policy, dict)
        and attempt_policy.get("formal_root")
        == _project_path(FORMAL_ATTEMPT_ROOT)
        and attempt_policy.get("directory") == directory
        and attempt_policy.get("eligibility")
        == "first structurally valid complete attempt in the retained predecessor chain"
        and attempt_policy.get("rerun_scope")
        == "complete five-seed, three-policy matrix under a new attempt ID"
    )


def _prior_attempts(output_dir: Path) -> List[Dict[str, Any]]:
    parent = Path(output_dir).resolve().parent
    if not parent.is_dir():
        return []
    attempts: List[Dict[str, Any]] = []
    for sibling in parent.iterdir():
        if sibling.resolve() == Path(output_dir).resolve() or not sibling.is_dir():
            continue
        record_names = [
            filename
            for filename in ("manifest.json", "attempt-failure.json")
            if (sibling / filename).is_file()
        ]
        found_record = False
        if len(record_names) == 1:
            filename = record_names[0]
            candidate = sibling / filename
            try:
                value = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                value = None
            if _valid_prior_record(value, filename, sibling.name):
                found_record = True
                attempts.append(
                    {
                        "attempt_id": value.get("attempt_id"),
                        "kind": value.get("kind"),
                        "status": (
                            "producer_complete"
                            if filename == "manifest.json"
                            else "invalid_operational_failure"
                        ),
                        "directory": sibling.name,
                        "record": filename,
                        "record_sha256": sha256_file(candidate),
                        "record_bytes": int(candidate.stat().st_size),
                        "started_at_utc": value.get("started_at_utc"),
                    }
                )
        if not found_record:
            attempts.append(
                {
                    "attempt_id": None,
                    "kind": "unresolved_attempt_directory",
                    "status": "unresolved",
                    "directory": sibling.name,
                    "record": None,
                    "record_sha256": None,
                    "record_bytes": None,
                    "started_at_utc": None,
                }
            )
    attempts.sort(
        key=lambda item: (
            str(item.get("started_at_utc") or ""),
            str(item.get("attempt_id") or ""),
            str(item.get("directory") or ""),
        )
    )
    return attempts


def _attempts_sha256(attempts: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(attempt) for attempt in attempts],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_mapping_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _terminal_intent_is_structurally_valid(value: Any) -> bool:
    """Validate the target-to-bootstrap terminalization handoff object."""

    if not isinstance(value, Mapping):
        return False
    try:
        candidate = json.loads(json.dumps(dict(value), sort_keys=True))
    except (TypeError, ValueError):
        return False
    expected_fields = {
        "schema_version",
        "attempt_id",
        "directory",
        "terminal_record",
        "terminal_record_sha256",
        "terminal_record_bytes",
        "preterminal_report",
        "preterminal_report_sha256",
        "preterminal_report_bytes",
        "preterminal_report_status",
        "preterminal_report_claimable",
        "auditor_identity",
        "target_exit_status",
        "ledger_prefix",
        "intent_sha256",
    }
    directory = candidate.get("directory")
    status = candidate.get("preterminal_report_status")
    target_exit_status = candidate.get("target_exit_status")
    auditor_identity = candidate.get("auditor_identity")
    ledger_prefix = candidate.get("ledger_prefix")
    intent_sha256 = candidate.get("intent_sha256")
    unsigned = dict(candidate)
    unsigned.pop("intent_sha256", None)
    return bool(
        set(candidate) == expected_fields
        and candidate.get("schema_version")
        == TERMINAL_INTENT_SCHEMA_VERSION
        and isinstance(candidate.get("attempt_id"), str)
        and bool(candidate["attempt_id"])
        and isinstance(directory, str)
        and bool(directory)
        and directory not in (".", "..")
        and "/" not in directory
        and "\\" not in directory
        and "\x00" not in directory
        and candidate.get("terminal_record") == "manifest.json"
        and _valid_sha256(candidate.get("terminal_record_sha256"))
        and isinstance(candidate.get("terminal_record_bytes"), int)
        and not isinstance(candidate.get("terminal_record_bytes"), bool)
        and candidate["terminal_record_bytes"] > 0
        and candidate.get("preterminal_report")
        == "gate7-preterminal-adjudication.json"
        and _valid_sha256(candidate.get("preterminal_report_sha256"))
        and isinstance(candidate.get("preterminal_report_bytes"), int)
        and not isinstance(candidate.get("preterminal_report_bytes"), bool)
        and candidate["preterminal_report_bytes"] > 0
        and status in PRETERMINAL_STATUS_EXIT_STATUS
        and isinstance(candidate.get("preterminal_report_claimable"), bool)
        and candidate["preterminal_report_claimable"]
        == (status in ("pass", "fail"))
        and isinstance(target_exit_status, int)
        and not isinstance(target_exit_status, bool)
        and target_exit_status == PRETERMINAL_STATUS_EXIT_STATUS.get(status)
        and isinstance(auditor_identity, dict)
        and set(auditor_identity) == {"path", "sha256", "bytes"}
        and auditor_identity.get("path")
        == "benchmarks/carma/gate7_v2_audit.py"
        and _valid_sha256(auditor_identity.get("sha256"))
        and isinstance(auditor_identity.get("bytes"), int)
        and not isinstance(auditor_identity.get("bytes"), bool)
        and auditor_identity["bytes"] > 0
        and isinstance(ledger_prefix, dict)
        and set(ledger_prefix)
        == {
            "path",
            "prefix_rows",
            "prefix_sha256",
            "prefix_bytes",
            "entry_sha256",
            "event",
        }
        and ledger_prefix.get("path") == "attempt-ledger.jsonl"
        and isinstance(ledger_prefix.get("prefix_rows"), int)
        and not isinstance(ledger_prefix.get("prefix_rows"), bool)
        and ledger_prefix["prefix_rows"] >= 2
        and _valid_sha256(ledger_prefix.get("prefix_sha256"))
        and isinstance(ledger_prefix.get("prefix_bytes"), int)
        and not isinstance(ledger_prefix.get("prefix_bytes"), bool)
        and ledger_prefix["prefix_bytes"] > 0
        and _valid_sha256(ledger_prefix.get("entry_sha256"))
        and ledger_prefix.get("event") == "START"
        and _valid_sha256(intent_sha256)
        and intent_sha256 == _canonical_mapping_sha256(unsigned)
    )


def _bootstrap_completion_is_structurally_valid(
    value: Any,
    start: Optional[Mapping[str, Any]] = None,
    terminal_intent: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Validate and, when supplied, bind the parent-bootstrap completion."""

    if not isinstance(value, Mapping):
        return False
    try:
        candidate = json.loads(json.dumps(dict(value), sort_keys=True))
    except (TypeError, ValueError):
        return False
    expected_fields = {
        "schema_version",
        "phase",
        "role",
        "launch_mode",
        "bootstrap_attestation_sha256",
        "target_sha256",
        "preimport_source_sha256",
        "dependency_sha256",
        "python_environment_sha256",
        "target_exit_status",
        "terminal_intent_sha256",
        "checks_passed",
        "attestation_sha256",
    }
    digest_fields = (
        "bootstrap_attestation_sha256",
        "target_sha256",
        "preimport_source_sha256",
        "dependency_sha256",
        "python_environment_sha256",
        "terminal_intent_sha256",
        "attestation_sha256",
    )
    unsigned = dict(candidate)
    attestation_sha256 = unsigned.pop("attestation_sha256", None)
    if not (
        set(candidate) == expected_fields
        and candidate.get("schema_version")
        == BOOTSTRAP_COMPLETION_SCHEMA_VERSION
        and candidate.get("phase") == "post_target"
        and candidate.get("role") == "parent"
        and candidate.get("launch_mode") == "full"
        and all(_valid_sha256(candidate.get(field)) for field in digest_fields)
        and isinstance(candidate.get("target_exit_status"), int)
        and not isinstance(candidate.get("target_exit_status"), bool)
        and candidate["target_exit_status"] in (0, 1, 2, 3)
        and candidate.get("checks_passed") is True
        and attestation_sha256 == _canonical_mapping_sha256(unsigned)
    ):
        return False
    if terminal_intent is not None:
        if (
            not _terminal_intent_is_structurally_valid(terminal_intent)
            or candidate.get("terminal_intent_sha256")
            != terminal_intent.get("intent_sha256")
            or candidate.get("target_exit_status")
            != terminal_intent.get("target_exit_status")
        ):
            return False
    if start is not None:
        entrypoint = start.get("entrypoint_attestation")
        bootstrap = (
            entrypoint.get("bootstrap_attestation")
            if isinstance(entrypoint, Mapping)
            else None
        )
        if not isinstance(bootstrap, Mapping):
            return False
        try:
            bootstrap_value = _validated_bootstrap_observation(
                bootstrap, "parent"
            )
        except RuntimeError:
            return False
        python_observation = bootstrap_value.get("python")
        if not isinstance(python_observation, dict) or not isinstance(
            python_observation.get("environment"), dict
        ):
            return False
        if (
            entrypoint.get("bootstrap_attestation_sha256")
            != bootstrap_value.get("attestation_sha256")
            or candidate.get("bootstrap_attestation_sha256")
            != bootstrap_value.get("attestation_sha256")
            or candidate.get("target_sha256")
            != bootstrap_value.get("target_sha256")
            or candidate.get("preimport_source_sha256")
            != _canonical_mapping_sha256(bootstrap_value["preimport_source"])
            or candidate.get("dependency_sha256")
            != _canonical_mapping_sha256(bootstrap_value["dependency"])
            or candidate.get("python_environment_sha256")
            != _canonical_mapping_sha256(python_observation["environment"])
        ):
            return False
    return True


def _preimport_source_is_structurally_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("launch_mode") == "smoke":
        return value == {
            "schema_version": PREIMPORT_SOURCE_SCHEMA_VERSION,
            "enforced": False,
            "launch_mode": "smoke",
        }
    git_executable = value.get("git_executable")
    anchor = value.get("anchor")
    inventory = value.get("inventory")
    if not all(
        isinstance(item, dict)
        for item in (git_executable, anchor, inventory)
    ):
        return False
    integer_fields = (
        "tracked_file_count",
        "tracked_file_bytes",
        "runtime_file_count",
        "runtime_untracked_or_missing_file_count",
        "runtime_symlink_count",
    )
    remote = anchor.get("remote")
    return bool(
        set(value)
        == {
            "schema_version",
            "enforced",
            "launch_mode",
            "git_executable",
            "git_status_clean",
            "anchor",
            "inventory",
        }
        and value.get("schema_version") == PREIMPORT_SOURCE_SCHEMA_VERSION
        and value.get("enforced") is True
        and value.get("launch_mode") == "full"
        and set(git_executable) == {"path", "sha256", "bytes", "version"}
        and git_executable.get("path") == "/usr/bin/git"
        and _valid_sha256(git_executable.get("sha256"))
        and isinstance(git_executable.get("bytes"), int)
        and not isinstance(git_executable.get("bytes"), bool)
        and git_executable["bytes"] > 0
        and isinstance(git_executable.get("version"), str)
        and git_executable["version"].startswith("git version ")
        and value.get("git_status_clean") is True
        and set(anchor)
        == {
            "object_format",
            "head_commit",
            "head_tree",
            "tag_name",
            "tag_object_type",
            "tag_object_id",
            "peeled_commit",
            "tag_tree",
            "tag_payload_sha256",
            "tag_payload_bytes",
            "annotation",
            "contract_path",
            "contract_sha256",
            "contract_bytes",
            "remote",
        }
        and anchor.get("object_format") == "sha1"
        and all(
            _valid_sha1_object(anchor.get(field))
            for field in (
                "head_commit",
                "head_tree",
                "tag_object_id",
                "peeled_commit",
                "tag_tree",
            )
        )
        and anchor.get("head_commit") == anchor.get("peeled_commit")
        and anchor.get("head_tree") == anchor.get("tag_tree")
        and anchor.get("tag_name") == FORMAL_SOURCE_TAG
        and anchor.get("tag_object_type") == "tag"
        and _valid_sha256(anchor.get("tag_payload_sha256"))
        and isinstance(anchor.get("tag_payload_bytes"), int)
        and not isinstance(anchor.get("tag_payload_bytes"), bool)
        and anchor["tag_payload_bytes"] > 0
        and anchor.get("annotation")
        == {
            "experiment_id": EXPERIMENT_ID,
            "contract_sha256": PINNED_CONTRACT_SHA256,
        }
        and anchor.get("contract_path")
        == DEFAULT_CONTRACT.relative_to(PROJECT_ROOT).as_posix()
        and anchor.get("contract_sha256") == PINNED_CONTRACT_SHA256
        and isinstance(anchor.get("contract_bytes"), int)
        and not isinstance(anchor.get("contract_bytes"), bool)
        and anchor["contract_bytes"] > 0
        and isinstance(remote, dict)
        and set(remote)
        == {
            "remote_name",
            "fetch_url",
            "fetch_url_count",
            "push_url",
            "push_url_count",
            "tag_ref",
            "tag_object_id",
            "peeled_ref",
            "peeled_commit",
        }
        and remote.get("remote_name") == FORMAL_SOURCE_REMOTE
        and remote.get("fetch_url") == FORMAL_SOURCE_REMOTE_URL
        and remote.get("fetch_url_count") == 1
        and not isinstance(remote.get("fetch_url_count"), bool)
        and remote.get("push_url") == FORMAL_SOURCE_REMOTE_URL
        and remote.get("push_url_count") == 1
        and not isinstance(remote.get("push_url_count"), bool)
        and remote.get("tag_ref") == "refs/tags/%s" % FORMAL_SOURCE_TAG
        and remote.get("tag_object_id") == anchor.get("tag_object_id")
        and remote.get("peeled_ref")
        == "refs/tags/%s^{}" % FORMAL_SOURCE_TAG
        and remote.get("peeled_commit") == anchor.get("peeled_commit")
        and set(inventory)
        == {
            "runtime_roots",
            "tracked_file_count",
            "tracked_file_bytes",
            "tracked_file_map_sha256",
            "runtime_file_count",
            "runtime_untracked_or_missing_file_count",
            "runtime_symlink_count",
            "project_import_namespace",
        }
        and inventory.get("runtime_roots") == ["gptcache", "benchmarks"]
        and all(
            isinstance(inventory.get(field), int)
            and not isinstance(inventory.get(field), bool)
            and inventory[field] >= 0
            for field in integer_fields
        )
        and inventory["tracked_file_count"] > 0
        and inventory["tracked_file_bytes"] > 0
        and inventory["runtime_file_count"] > 0
        and inventory["runtime_untracked_or_missing_file_count"] == 0
        and inventory["runtime_symlink_count"] == 0
        and _valid_sha256(inventory.get("tracked_file_map_sha256"))
        and isinstance(inventory.get("project_import_namespace"), dict)
        and set(inventory["project_import_namespace"])
        == {
            "excluded_sealed_roots",
            "import_suffixes",
            "import_file_count",
            "untracked_or_ignored_import_file_count",
            "symlink_count",
        }
        and inventory["project_import_namespace"].get(
            "excluded_sealed_roots"
        )
        == [".git", ".venv"]
        and inventory["project_import_namespace"].get("import_suffixes")
        == [".py", ".pyc", ".pyo", ".so", ".pyd", ".dylib", ".dll"]
        and all(
            isinstance(
                inventory["project_import_namespace"].get(field), int
            )
            and not isinstance(
                inventory["project_import_namespace"].get(field), bool
            )
            for field in (
                "import_file_count",
                "untracked_or_ignored_import_file_count",
                "symlink_count",
            )
        )
        and isinstance(
            inventory["project_import_namespace"].get("import_file_count"),
            int,
        )
        and not isinstance(
            inventory["project_import_namespace"].get("import_file_count"),
            bool,
        )
        and inventory["project_import_namespace"]["import_file_count"] > 0
        and inventory["project_import_namespace"].get(
            "untracked_or_ignored_import_file_count"
        )
        == 0
        and inventory["project_import_namespace"].get("symlink_count") == 0
    )


def _wrapper_shell_startup_is_structurally_valid(
    value: Any, launch_mode: Any, environment: Any
) -> bool:
    if not isinstance(value, dict) or not isinstance(environment, dict):
        return False
    expected_profile = (
        "env-i-v1" if launch_mode == "full" else "development-smoke"
    )
    digest = value.get("attestation_sha256")
    unhashed = dict(value)
    unhashed.pop("attestation_sha256", None)
    home = value.get("home")
    pwd = value.get("pwd")
    tmpdir = value.get("tmpdir")
    shlvl = value.get("shlvl")
    optional_cf = value.get("optional_cf_user_text_encoding")
    return bool(
        launch_mode in ("full", "smoke")
        and set(value)
        == {
            "schema_version",
            "profile",
            "marker",
            "launch_mode",
            "outer_env_i_operator_root_required",
            "home",
            "pwd",
            "tmpdir",
            "shlvl",
            "optional_cf_user_text_encoding",
            "fixed_environment",
            "attestation_sha256",
        }
        and value.get("schema_version")
        == WRAPPER_SHELL_STARTUP_SCHEMA_VERSION
        and value.get("profile") == expected_profile
        and value.get("marker") == "gate7c-shell-v1"
        and value.get("launch_mode") == launch_mode
        and value.get("outer_env_i_operator_root_required")
        is (launch_mode == "full")
        and isinstance(home, str)
        and bool(home)
        and Path(home).is_absolute()
        and isinstance(pwd, str)
        and bool(pwd)
        and Path(pwd).resolve() == PROJECT_ROOT
        and isinstance(tmpdir, str)
        and bool(tmpdir)
        and isinstance(shlvl, str)
        and bool(shlvl)
        and isinstance(optional_cf, str)
        and (launch_mode != "full" or tmpdir == "/tmp")
        and (launch_mode != "full" or shlvl == "1")
        and value.get("fixed_environment")
        == {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LC_CTYPE": "C.UTF-8",
            "TZ": "UTC",
        }
        and environment.get("CARMA_GATE7_WRAPPER_SHELL")
        == "gate7c-shell-v1"
        and environment.get("CARMA_GATE7_LAUNCH_MODE") == launch_mode
        and environment.get("CARMA_GATE7_WRAPPER_SHELL_PROFILE")
        == expected_profile
        and environment.get("CARMA_GATE7_WRAPPER_SHELL_HOME") == home
        and environment.get("CARMA_GATE7_WRAPPER_SHELL_PWD") == pwd
        and environment.get("CARMA_GATE7_WRAPPER_SHELL_TMPDIR") == tmpdir
        and environment.get("CARMA_GATE7_WRAPPER_SHELL_SHLVL") == shlvl
        and environment.get("CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF")
        == optional_cf
        and _valid_sha256(digest)
        and digest == _canonical_mapping_sha256(unhashed)
    )


def _validated_bootstrap_observation(
    raw: Any, expected_role: str
) -> Dict[str, Any]:
    try:
        value = json.loads(json.dumps(dict(raw), sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("isolated-bootstrap observation is not JSON-safe") from exc
    expected_target = (
        "benchmarks/carma/gate7_v2_audit.py"
        if expected_role == "auditor"
        else "benchmarks/carma/gate7_v2_onnx_integration_benchmark.py"
    )
    expected_fields = {
        "schema_version",
        "role",
        "target",
        "target_sha256",
        "project_root",
        "venv_root",
        "site_packages",
        "python",
        "pyvenv",
        "lock",
        "dependency",
        "preimport_source",
        "preloaded_module_origins",
        "site_module_absent",
        "pth_executed",
        "attestation_sha256",
    }
    digest = value.get("attestation_sha256")
    unhashed = dict(value)
    unhashed.pop("attestation_sha256", None)
    python_observation = value.get("python")
    preimport_source = value.get("preimport_source")
    launch_mode = (
        preimport_source.get("launch_mode")
        if isinstance(preimport_source, dict)
        else None
    )
    if (
        set(value) != expected_fields
        or value.get("schema_version") != ISOLATED_BOOTSTRAP_SCHEMA_VERSION
        or value.get("role") != expected_role
        or value.get("target") != expected_target
        or not isinstance(value.get("target_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["target_sha256"]) is None
        or value.get("site_module_absent") is not True
        or value.get("pth_executed") is not False
        or not _preimport_source_is_structurally_valid(preimport_source)
        or not isinstance(python_observation, dict)
        or not _wrapper_shell_startup_is_structurally_valid(
            python_observation.get("wrapper_shell_startup"),
            launch_mode,
            python_observation.get("environment"),
        )
        or not isinstance(digest, str)
        or digest != _canonical_mapping_sha256(unhashed)
    ):
        raise RuntimeError("isolated-bootstrap observation is inconsistent")
    return value


def _bootstrap_common_observation(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the role-independent portion used for parent/child/auditor parity."""

    return {
        field: json.loads(json.dumps(value[field], sort_keys=True))
        for field in (
            "schema_version",
            "project_root",
            "venv_root",
            "site_packages",
            "python",
            "pyvenv",
            "lock",
            "dependency",
            "preimport_source",
            "preloaded_module_origins",
            "site_module_absent",
            "pth_executed",
        )
    }


def _bootstrap_attestation(expected_role: str) -> Dict[str, Any]:
    raw = getattr(sys, "_gate7_v2_bootstrap_attestation", None)
    if not isinstance(raw, Mapping):
        raise RuntimeError("formal process lacks the isolated-bootstrap sentinel")
    value = _validated_bootstrap_observation(raw, expected_role)
    digest = value["attestation_sha256"]
    launch_mode = os.environ.get("CARMA_GATE7_LAUNCH_MODE")
    if (
        os.environ.get("CARMA_GATE7_BOOTSTRAP_SCHEMA")
        != ISOLATED_BOOTSTRAP_SCHEMA_VERSION
        or os.environ.get("CARMA_GATE7_BOOTSTRAP_ROLE") != expected_role
        or os.environ.get("CARMA_GATE7_BOOTSTRAP_ATTESTATION_SHA256") != digest
        or launch_mode not in ("full", "smoke")
        or value["preimport_source"].get("launch_mode") != launch_mode
    ):
        raise RuntimeError("formal isolated-bootstrap sentinel is inconsistent")
    return value


def _bootstrap_runtime_unchanged(expected_role: str) -> Dict[str, Any]:
    """Recheck live invariants that a copied bootstrap observation cannot prove."""

    current_sentinel = getattr(sys, "_gate7_v2_bootstrap_attestation", None)
    if (
        _BOOTSTRAP_RUNTIME_SENTINEL is None
        or current_sentinel is not _BOOTSTRAP_RUNTIME_SENTINEL
    ):
        raise RuntimeError("formal bootstrap runtime sentinel object changed")
    observation = _bootstrap_attestation(expected_role)
    pycache_prefix = Path(os.environ.get("PYTHONPYCACHEPREFIX", ""))
    if (
        Path(sys.pycache_prefix or "") != pycache_prefix
        or pycache_prefix.is_symlink()
        or not pycache_prefix.is_dir()
        or any(pycache_prefix.iterdir())
    ):
        raise RuntimeError("formal pycache runtime boundary changed")
    if "site" in sys.modules:
        raise RuntimeError("formal process imported site after bootstrap")
    venv_root = str(observation["venv_root"])
    if sys.prefix != venv_root or sys.exec_prefix != venv_root:
        raise RuntimeError("formal virtual-environment prefix changed")
    expected_path = [
        *observation["python"]["initial_sys_path"],
        str(observation["site_packages"]),
        str(observation["project_root"]),
    ]
    observed_path = [str(Path(item).resolve()) for item in sys.path]
    if observed_path != expected_path:
        raise RuntimeError("formal verified import path changed")
    expected_environment = dict(observation["python"]["environment"])
    expected_environment.update(
        {
            "CARMA_GATE7_BOOTSTRAP_SCHEMA": ISOLATED_BOOTSTRAP_SCHEMA_VERSION,
            "CARMA_GATE7_BOOTSTRAP_ROLE": expected_role,
            "CARMA_GATE7_BOOTSTRAP_ATTESTATION_SHA256": observation[
                "attestation_sha256"
            ],
        }
    )
    if dict(sorted(os.environ.items())) != dict(
        sorted(expected_environment.items())
    ):
        raise RuntimeError("formal sealed process environment changed")
    target_path = PROJECT_ROOT / str(observation["target"])
    if (
        not target_path.is_file()
        or target_path.is_symlink()
        or sha256_file(target_path) != observation["target_sha256"]
    ):
        raise RuntimeError("formal bootstrap target identity changed")
    return observation


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _parse_benchmark_lock(path: Path = BENCHMARK_LOCK) -> Dict[str, str]:
    """Parse and verify the exact hashed benchmark-lock pin map."""

    path = Path(path)
    if not path.is_file() or sha256_file(path) != PINNED_BENCHMARK_LOCK_SHA256:
        raise RuntimeError("formal benchmark dependency lock identity changed")
    logical_lines: List[str] = []
    accumulator = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not accumulator and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            accumulator += line[:-1].strip() + " "
            continue
        accumulator += line.strip()
        logical_lines.append(accumulator.strip())
        accumulator = ""
    if accumulator:
        raise RuntimeError("formal benchmark dependency lock ends mid-requirement")
    pins: Dict[str, str] = {}
    for line in logical_lines:
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:\s|$)", line)
        if match is None:
            raise RuntimeError("formal benchmark dependency lock has an unpinned row")
        name = _normalized_distribution_name(match.group(1))
        if name in pins:
            raise RuntimeError("formal benchmark dependency lock repeats a package")
        pins[name] = match.group(2)
    if len(pins) != PINNED_BENCHMARK_LOCK_PIN_COUNT:
        raise RuntimeError("formal benchmark dependency lock pin count changed")
    if _canonical_mapping_sha256(pins) != PINNED_BENCHMARK_PIN_MAP_SHA256:
        raise RuntimeError("formal benchmark dependency lock pin map changed")
    return dict(sorted(pins.items()))


def _path_is_under(path: Path, parent: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
    except ValueError:
        return False
    return True


def _sha256_digest_bytes(path: Path) -> bytes:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def _verify_distribution_records(
    distributions: Mapping[str, importlib.metadata.Distribution],
    prefix: Path,
    site_roots: Sequence[str],
) -> Tuple[Dict[str, Any], Dict[str, Set[Path]]]:
    """Verify every hashed RECORD member and reject startup-file gaps."""

    locked_summaries: Dict[str, Any] = {}
    local_editable_summary: Optional[Dict[str, Any]] = None
    owned_files: Dict[str, Set[Path]] = {}
    hashed_owners: Dict[Path, str] = {}
    locked_hashed_files = 0
    locked_hashed_bytes = 0
    for name in sorted(distributions):
        distribution = distributions[name]
        record_text = distribution.read_text("RECORD")
        if record_text is None:
            raise RuntimeError("installed distribution has no RECORD: %s" % name)
        record_path = Path(distribution._path) / "RECORD"  # type: ignore[attr-defined]
        if not record_path.is_file() or not _path_is_under(record_path, prefix):
            raise RuntimeError("installed distribution RECORD escapes sys.prefix")
        hashed_files: Dict[str, str] = {}
        hashed_bytes = 0
        owned_files[name] = set()
        for row in csv.reader(record_text.splitlines()):
            if len(row) != 3 or not row[0]:
                raise RuntimeError("installed distribution RECORD is malformed: %s" % name)
            member = Path(distribution.locate_file(row[0])).resolve()
            if not _path_is_under(member, prefix):
                raise RuntimeError("installed RECORD member escapes sys.prefix: %s" % name)
            owned_files[name].add(member)
            encoded_hash = row[1]
            if not encoded_hash:
                continue
            if not encoded_hash.startswith("sha256="):
                raise RuntimeError("installed RECORD uses a non-SHA256 digest: %s" % name)
            if not member.is_file():
                raise RuntimeError("installed RECORD member is missing: %s" % row[0])
            try:
                expected_digest = base64.urlsafe_b64decode(
                    encoded_hash.split("=", 1)[1]
                    + "=" * (-len(encoded_hash.split("=", 1)[1]) % 4)
                )
                expected_size = int(row[2])
            except (ValueError, TypeError) as exc:
                raise RuntimeError("installed RECORD digest/size is malformed") from exc
            observed_size = int(member.stat().st_size)
            observed_digest = _sha256_digest_bytes(member)
            if observed_size != expected_size or observed_digest != expected_digest:
                raise RuntimeError("installed RECORD member identity changed: %s" % row[0])
            relative_member = str(member.relative_to(prefix))
            hashed_files[relative_member] = observed_digest.hex()
            hashed_owners[member] = name
            hashed_bytes += observed_size
        summary = {
            "record_sha256": sha256_file(record_path),
            "hashed_file_count": len(hashed_files),
            "hashed_bytes": hashed_bytes,
            "hashed_files_sha256": _canonical_mapping_sha256(hashed_files),
        }
        if name == "gptcache":
            local_editable_summary = summary
        else:
            locked_summaries[name] = summary
            locked_hashed_files += len(hashed_files)
            locked_hashed_bytes += hashed_bytes

    startup_files: Dict[str, Any] = {}
    custom_startup_absent = {
        "sitecustomize.py": True,
        "usercustomize.py": True,
    }
    for site_root_text in site_roots:
        site_root = Path(site_root_text)
        candidates = list(site_root.glob("*.pth"))
        candidates.extend(site_root / name for name in ("sitecustomize.py", "usercustomize.py"))
        for candidate in sorted(set(candidates)):
            if not candidate.exists():
                continue
            if candidate.name in custom_startup_absent:
                custom_startup_absent[candidate.name] = False
                raise RuntimeError(
                    "formal site-packages forbids sitecustomize.py/usercustomize.py"
                )
            resolved = candidate.resolve()
            owner = hashed_owners.get(resolved)
            if owner is None:
                raise RuntimeError(
                    "unowned or unhashed Python startup file under sys.prefix: %s"
                    % candidate.name
                )
            startup_files[str(resolved.relative_to(prefix))] = {
                "owner": owner,
                **_artifact_identity(resolved),
            }
    if local_editable_summary is None:
        raise RuntimeError("local editable GPTCache has no verified RECORD")
    if local_editable_summary != PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY:
        raise RuntimeError("local editable GPTCache RECORD identity changed")
    if (
        len(locked_summaries) != PINNED_BENCHMARK_LOCK_PIN_COUNT
        or locked_hashed_files != PINNED_LOCKED_RECORD_HASHED_FILE_COUNT
        or locked_hashed_bytes != PINNED_LOCKED_RECORD_HASHED_BYTES
    ):
        raise RuntimeError("locked distribution RECORD inventory changed")
    locked_aggregate_sha256 = _canonical_mapping_sha256(locked_summaries)
    if locked_aggregate_sha256 != PINNED_LOCKED_RECORD_AGGREGATE_SHA256:
        raise RuntimeError("locked distribution RECORD/member aggregate changed")
    owned_inventory: Dict[str, str] = {}
    for owner, paths in owned_files.items():
        for owned_path in paths:
            owned_inventory[str(owned_path.relative_to(prefix))] = owner
    all_owned_paths = {
        owned_path for paths in owned_files.values() for owned_path in paths
    }
    unowned_files: List[str] = []
    unowned_directories: List[str] = []
    owned_directories: Set[Path] = set()
    for site_root_text in site_roots:
        site_root = Path(site_root_text).resolve()
        for owned_path in all_owned_paths:
            if not _path_is_under(owned_path, site_root):
                continue
            parent = owned_path.parent
            while parent != site_root.parent:
                owned_directories.add(parent)
                if parent == site_root:
                    break
                parent = parent.parent
    for site_root_text in site_roots:
        site_root = Path(site_root_text)
        for candidate in site_root.rglob("*"):
            if candidate.is_dir():
                if candidate.resolve() not in owned_directories:
                    unowned_directories.append(
                        str(candidate.resolve().relative_to(prefix))
                    )
                continue
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if candidate.is_symlink() or not _path_is_under(resolved, prefix):
                unowned_files.append(str(candidate))
                continue
            if resolved in all_owned_paths:
                continue
            relative = str(resolved.relative_to(prefix))
            unowned_files.append(relative)
    if unowned_files or unowned_directories:
        raise RuntimeError(
            "formal site-packages contains unowned files/directories: %r / %r"
            % (sorted(unowned_files), sorted(unowned_directories))
        )
    record_integrity: Dict[str, Any] = {
        "locked_distribution_count": len(locked_summaries),
        "locked_hashed_file_count": locked_hashed_files,
        "locked_hashed_bytes": locked_hashed_bytes,
        "locked_aggregate_sha256": locked_aggregate_sha256,
        "startup_files": startup_files,
        "sitecustomize_absent": custom_startup_absent["sitecustomize.py"],
        "usercustomize_absent": custom_startup_absent["usercustomize.py"],
        "locked_distributions": locked_summaries,
        "local_editable": local_editable_summary,
        "sealed_site_packages": {
            "owned_file_count": len(owned_inventory),
            "owned_paths_sha256": _canonical_mapping_sha256(owned_inventory),
            "owned_directory_count": len(owned_directories),
            "owned_directories_sha256": hashlib.sha256(
                json.dumps(
                    sorted(str(path.relative_to(prefix)) for path in owned_directories),
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "unowned_files": [],
            "unowned_directories": [],
        },
    }
    record_integrity["aggregate_sha256"] = _canonical_mapping_sha256(
        record_integrity
    )
    return record_integrity, owned_files


def _project_shadow_capable_ignored_files() -> List[str]:
    """Find ignored native/startup artifacts capable of shadowing tagged code."""

    try:
        output = _git_text(
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--",
            "gptcache",
            "benchmarks",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("formal ignored-file scan failed") from exc
    ignored = set(output.splitlines()) if output else set()
    for filename in ("sitecustomize.py", "usercustomize.py"):
        candidate = PROJECT_ROOT / filename
        if candidate.exists():
            try:
                _git_text("check-ignore", "--", filename)
            except subprocess.CalledProcessError:
                continue
            ignored.add(filename)
    rejected = []
    for relative in sorted(ignored):
        path = Path(relative)
        suffix = path.suffix.lower()
        legacy_pyc = suffix == ".pyc" and "__pycache__" not in path.parts
        if (
            suffix in (".so", ".pyd", ".dylib")
            or legacy_pyc
            or path.name in ("sitecustomize.py", "usercustomize.py")
        ):
            rejected.append(path.as_posix())
    return rejected


def _dependency_attestation() -> Dict[str, Any]:
    """Fail closed over the complete formal interpreter/dependency boundary."""

    if (
        platform.python_implementation() != "CPython"
        or platform.python_version() != PINNED_CPYTHON_VERSION
    ):
        raise RuntimeError("formal Gate 7 requires exact CPython 3.12.13")
    expected_executable = (PROJECT_ROOT / ".venv" / "bin" / "python").absolute()
    observed_executable = Path(sys.executable).absolute()
    if observed_executable != expected_executable:
        raise RuntimeError("formal Gate 7 requires the project .venv interpreter")
    prefix = Path(sys.prefix).absolute()
    if prefix != (PROJECT_ROOT / ".venv").absolute():
        raise RuntimeError("formal Gate 7 requires the project .venv prefix")
    pyvenv_cfg = prefix / "pyvenv.cfg"
    if not pyvenv_cfg.is_file():
        raise RuntimeError("formal Gate 7 virtual environment metadata is missing")
    pyvenv_text = pyvenv_cfg.read_text(encoding="utf-8")
    if (
        "include-system-site-packages = false" not in pyvenv_text.splitlines()
        or ("version = %s" % PINNED_CPYTHON_VERSION) not in pyvenv_text.splitlines()
    ):
        raise RuntimeError("formal Gate 7 virtual environment metadata changed")

    observed_environment = {
        key: os.environ.get(key) for key in FORMAL_REQUIRED_ENVIRONMENT
    }
    if observed_environment != FORMAL_REQUIRED_ENVIRONMENT:
        raise RuntimeError("formal Gate 7 environment differs from the frozen values")
    present_forbidden = [
        key for key in FORMAL_FORBIDDEN_ENVIRONMENT if key in os.environ
    ]
    if present_forbidden:
        raise RuntimeError(
            "formal Gate 7 environment contains forbidden Python path variables"
        )
    forbidden_cache = [
        key for key in FORMAL_FORBIDDEN_CACHE_ENVIRONMENT if key in os.environ
    ]
    loader_variables = sorted(
        key
        for key in os.environ
        if key.startswith("DYLD_") or key.startswith("LD_")
    )
    if forbidden_cache or loader_variables:
        raise RuntimeError("formal Gate 7 environment contains loader/cache overrides")
    dynamic_environment = {
        key: os.environ.get(key) for key in FORMAL_DYNAMIC_ENVIRONMENT
    }
    if any(value is None for value in dynamic_environment.values()):
        raise RuntimeError("formal Gate 7 controlled process environment is incomplete")
    if dynamic_environment["CARMA_GATE7_LAUNCH_MODE"] != "full":
        raise RuntimeError("formal Gate 7 launch mode is not full")
    if (
        dynamic_environment["CARMA_GATE7_WRAPPER_SHELL_PROFILE"]
        != "env-i-v1"
        or Path(
            str(dynamic_environment["CARMA_GATE7_WRAPPER_SHELL_HOME"])
        ).resolve()
        != Path(str(dynamic_environment["HOME"])).resolve()
        or Path(
            str(dynamic_environment["CARMA_GATE7_WRAPPER_SHELL_PWD"])
        ).resolve()
        != PROJECT_ROOT
        or dynamic_environment["CARMA_GATE7_WRAPPER_SHELL_TMPDIR"]
        != "/tmp"
        or dynamic_environment["CARMA_GATE7_WRAPPER_SHELL_SHLVL"] != "1"
        or not isinstance(
            dynamic_environment["CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF"],
            str,
        )
    ):
        raise RuntimeError("formal Gate 7 wrapper-shell startup differs")
    expected_environment_keys = (
        set(FORMAL_REQUIRED_ENVIRONMENT)
        | set(FORMAL_DYNAMIC_ENVIRONMENT)
        | set(FORMAL_BOOTSTRAP_EVIDENCE_ENVIRONMENT)
        | {
            key
            for key in FORMAL_OPTIONAL_OS_ENVIRONMENT
            if key in os.environ
        }
    )
    unexpected_environment = sorted(set(os.environ) - expected_environment_keys)
    if unexpected_environment:
        raise RuntimeError(
            "formal Gate 7 environment contains unexpected variables: %r"
            % unexpected_environment
        )
    pycache_prefix_raw = os.environ.get("PYTHONPYCACHEPREFIX")
    if not pycache_prefix_raw:
        raise RuntimeError("formal Gate 7 requires an isolated pycache prefix")
    pycache_prefix = Path(pycache_prefix_raw)
    if (
        pycache_prefix.is_symlink()
        or not pycache_prefix.is_dir()
        or any(pycache_prefix.iterdir())
        or Path(sys.pycache_prefix or "") != pycache_prefix
    ):
        raise RuntimeError("formal Gate 7 pycache prefix is not a real empty directory")

    pins = _parse_benchmark_lock()
    expected_installed = {**pins, "gptcache": LOCAL_GPTCACHE_VERSION}
    site_roots = sorted(
        {
            str(Path(candidate).resolve())
            for candidate in (
                sysconfig.get_path("purelib"),
                sysconfig.get_path("platlib"),
            )
            if candidate
        }
    )
    installed: Dict[str, str] = {}
    third_party_origins: Dict[str, str] = {}
    distributions_by_name: Dict[str, importlib.metadata.Distribution] = {}
    gptcache_distribution: Optional[importlib.metadata.Distribution] = None
    for distribution in importlib.metadata.distributions(path=site_roots):
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise RuntimeError("installed distribution has no canonical name")
        name = _normalized_distribution_name(raw_name)
        if name in installed:
            raise RuntimeError("formal environment repeats an installed distribution")
        installed[name] = distribution.version
        distributions_by_name[name] = distribution
        distribution_root = Path(distribution.locate_file("")).resolve()
        if name == "gptcache":
            gptcache_distribution = distribution
        else:
            if not _path_is_under(distribution_root, prefix):
                raise RuntimeError(
                    "third-party distribution origin escapes sys.prefix: %s" % name
                )
            third_party_origins[name] = str(distribution_root.relative_to(prefix))
    installed = dict(sorted(installed.items()))
    if installed != dict(sorted(expected_installed.items())):
        raise RuntimeError("formal installed distribution map differs from the lock")
    record_integrity, owned_files = _verify_distribution_records(
        distributions_by_name, prefix, site_roots
    )
    if gptcache_distribution is None:
        raise RuntimeError("formal local GPTCache distribution is missing")
    try:
        direct_url = json.loads(gptcache_distribution.read_text("direct_url.json") or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("formal GPTCache editable provenance is unreadable") from exc
    from urllib.parse import unquote, urlparse

    parsed_url = urlparse(str(direct_url.get("url", "")))
    source_path = Path(unquote(parsed_url.path)).resolve()
    editable = (
        isinstance(direct_url.get("dir_info"), dict)
        and direct_url["dir_info"].get("editable") is True
    )
    if parsed_url.scheme != "file" or source_path != PROJECT_ROOT or not editable:
        raise RuntimeError("formal GPTCache is not the source-bound local editable")
    gptcache_spec = importlib.util.find_spec("gptcache")
    module_origin = (
        Path(gptcache_spec.origin).resolve()
        if gptcache_spec is not None and gptcache_spec.origin
        else None
    )
    if module_origin is None or not _path_is_under(module_origin, PROJECT_ROOT):
        raise RuntimeError("formal GPTCache import origin is not source-bound")
    import gptcache

    imported_gptcache_origin = Path(gptcache.__file__).resolve()
    if imported_gptcache_origin != module_origin:
        raise RuntimeError("imported GPTCache differs from tagged project source")
    required_module_owners = {
        "numpy": "numpy",
        "faiss": "faiss-cpu",
        "onnxruntime": "onnxruntime",
        "transformers": "transformers",
        "tokenizers": "tokenizers",
        "huggingface_hub": "huggingface-hub",
        "sqlalchemy": "sqlalchemy",
        "cachetools": "cachetools",
        "psutil": "psutil",
    }
    imported_origins: Dict[str, Any] = {}
    for module_name, owner in required_module_owners.items():
        spec = importlib.util.find_spec(module_name)
        origin = (
            Path(spec.origin).resolve()
            if spec is not None and spec.origin not in (None, "built-in", "frozen")
            else None
        )
        if (
            origin is None
            or not _path_is_under(origin, prefix)
            or origin not in owned_files.get(owner, set())
        ):
            raise RuntimeError(
                "formal imported module origin is not owned by its locked distribution: %s"
                % module_name
            )
        imported_origins[module_name] = {
            "owner": owner,
            "path": str(origin.relative_to(prefix)),
        }
    shadow_files = _project_shadow_capable_ignored_files()
    if shadow_files:
        raise RuntimeError(
            "formal project contains ignored shadow-capable runtime files: %r"
            % shadow_files
        )

    # Reproduce the dependency-consistency part of ``pip check`` without
    # importing pip internals: pip imports ``site`` even under ``-S``.  The
    # packaging parser/specifier implementation is itself locked and RECORD-
    # verified above, so this preserves the no-site/no-.pth postcondition.
    if "site" in sys.modules:
        raise RuntimeError("formal process imported site after isolated bootstrap")
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.version import InvalidVersion, Version

    dependency_problems: List[Dict[str, str]] = []
    for owner, distribution in sorted(distributions_by_name.items()):
        for raw_requirement in distribution.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as exc:
                raise RuntimeError(
                    "installed Requires-Dist metadata is malformed: %s" % owner
                ) from exc
            if requirement.marker is not None and not requirement.marker.evaluate(
                {"extra": ""}
            ):
                continue
            dependency_name = _normalized_distribution_name(requirement.name)
            observed_version = installed.get(dependency_name)
            if observed_version is None:
                dependency_problems.append(
                    {
                        "owner": owner,
                        "requirement": str(requirement),
                        "problem": "missing",
                    }
                )
                continue
            try:
                satisfies = requirement.specifier.contains(
                    Version(observed_version), prereleases=True
                )
            except InvalidVersion as exc:
                raise RuntimeError(
                    "installed distribution version is malformed: %s"
                    % dependency_name
                ) from exc
            if not satisfies:
                dependency_problems.append(
                    {
                        "owner": owner,
                        "requirement": str(requirement),
                        "problem": "conflict:%s" % observed_version,
                    }
                )
    if dependency_problems or "site" in sys.modules:
        raise RuntimeError("formal dependency environment fails pip check")

    build_dependencies = getattr(np.__config__, "CONFIG", {}).get(
        "Build Dependencies", {}
    )
    blas_name = str(build_dependencies.get("blas", {}).get("name", "")).lower()
    lapack_name = str(
        build_dependencies.get("lapack", {}).get("name", "")
    ).lower()
    if blas_name != "accelerate" or lapack_name != "accelerate":
        raise RuntimeError("formal NumPy BLAS/LAPACK must both use Accelerate")
    import faiss

    faiss_threads = int(faiss.omp_get_max_threads())
    if faiss_threads != 1:
        raise RuntimeError("formal FAISS maximum thread count must equal one")
    if "site" in sys.modules:
        raise RuntimeError("formal process imported site after isolated bootstrap")

    environment_attestation: Dict[str, Any] = {
        "required": dict(FORMAL_REQUIRED_ENVIRONMENT),
        "dynamic": dynamic_environment,
        "optional_os": {
            key: os.environ[key]
            for key in FORMAL_OPTIONAL_OS_ENVIRONMENT
            if key in os.environ
        },
        # Role/target-specific bootstrap values are bound separately.  Keeping
        # only their presence in this logical environment identity makes the
        # one parent/child environment digest comparable across all runs.
        "bootstrap_evidence_present": True,
        "unexpected_absent": True,
        "forbidden_absent": list(FORMAL_FORBIDDEN_ENVIRONMENT),
        "forbidden_cache_absent": list(FORMAL_FORBIDDEN_CACHE_ENVIRONMENT),
        "loader_variables_absent": True,
        "pycache_prefix": str(pycache_prefix.resolve()),
        "pycache_prefix_empty": True,
    }
    environment_attestation["environment_sha256"] = _canonical_mapping_sha256(
        environment_attestation
    )
    attestation: Dict[str, Any] = {
        "schema_version": DEPENDENCY_ATTESTATION_SCHEMA_VERSION,
        "python": {
            "implementation": "CPython",
            "version": PINNED_CPYTHON_VERSION,
            "executable": _project_path(expected_executable),
            "resolved_executable": str(observed_executable.resolve()),
            "sys_prefix": _project_path(prefix),
            "pyvenv_cfg": {
                "path": _project_path(pyvenv_cfg),
                **_artifact_identity(pyvenv_cfg),
            },
        },
        "lock": {
            "path": _project_path(BENCHMARK_LOCK),
            "sha256": PINNED_BENCHMARK_LOCK_SHA256,
            "pin_count": PINNED_BENCHMARK_LOCK_PIN_COUNT,
            "pin_map_sha256": PINNED_BENCHMARK_PIN_MAP_SHA256,
            "pins": pins,
        },
        "installed": {
            "packages": installed,
            "package_map_sha256": _canonical_mapping_sha256(installed),
            "third_party_origins": dict(sorted(third_party_origins.items())),
            "imported_origins": imported_origins,
            "gptcache": {
                "version": LOCAL_GPTCACHE_VERSION,
                "editable": True,
                "source_path": ".",
                "module_origin": str(module_origin.relative_to(PROJECT_ROOT)),
            },
        },
        "pip_check": {
            "status": "pass",
            "returncode": 0,
            "method": "in_process_requires_dist_consistency",
            "site_module_absent": True,
        },
        "environment": environment_attestation,
        "record_integrity": record_integrity,
        "project_shadow_scan": {
            "runtime_roots": ["gptcache", "benchmarks", "."],
            "rejected_files": [],
        },
        "numpy": {"blas": blas_name, "lapack": lapack_name},
        "faiss": {"max_threads": faiss_threads},
    }
    attestation["attestation_sha256"] = _canonical_mapping_sha256(attestation)
    return attestation


def _git_text(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments],
        cwd=str(PROJECT_ROOT),
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def _git_bytes(*arguments: str) -> bytes:
    return subprocess.check_output(
        ["git", *arguments], cwd=str(PROJECT_ROOT), stderr=subprocess.DEVNULL
    )


def _remote_tag_refs(remote: str, tag: str) -> Dict[str, str]:
    output = _git_text(
        "ls-remote",
        "--tags",
        remote,
        "refs/tags/%s" % tag,
        "refs/tags/%s^{}" % tag,
    )
    refs: Dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or parts[1] in refs:
            raise RuntimeError("formal source tag remote response is malformed")
        refs[parts[1]] = parts[0]
    return refs


def _formal_entrypoint_attestation() -> Dict[str, Any]:
    """Prove that the formal producer is a direct child of the frozen wrapper."""

    bootstrap = _bootstrap_attestation("parent")
    marker = os.environ.get("CARMA_GATE7_FORMAL_ENTRYPOINT")
    wrapper_pid_raw = os.environ.get("CARMA_GATE7_WRAPPER_PID")
    wrapper_path_raw = os.environ.get("CARMA_GATE7_WRAPPER_PATH")
    if marker != FORMAL_ENTRYPOINT_MARKER:
        raise RuntimeError("formal Gate 7 must enter through the frozen wrapper")
    try:
        wrapper_pid = int(str(wrapper_pid_raw))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("formal Gate 7 wrapper PID is malformed") from exc
    if wrapper_pid != os.getppid():
        raise RuntimeError("formal Gate 7 wrapper is not the producer parent")
    if not wrapper_path_raw:
        raise RuntimeError("formal Gate 7 wrapper path is missing")
    wrapper_path = Path(wrapper_path_raw)
    if wrapper_path.resolve() != FORMAL_WRAPPER.resolve() or wrapper_path.is_symlink():
        raise RuntimeError("formal Gate 7 wrapper path differs from the frozen path")
    try:
        parent = psutil.Process(wrapper_pid)
        parent_cmdline = parent.cmdline()
        parent_executable = str(Path(parent.exe()).resolve())
    except (psutil.Error, ProcessLookupError) as exc:
        raise RuntimeError("formal Gate 7 wrapper process is unavailable") from exc
    expected_parent_executable = str(Path("/bin/bash").resolve())
    if (
        parent_executable != expected_parent_executable
        or len(parent_cmdline) != 3
        or Path(parent_cmdline[0]).name != "bash"
        or parent_cmdline[1].startswith("-")
        or not Path(parent_cmdline[1]).is_absolute()
        or Path(parent_cmdline[1]).resolve() != FORMAL_WRAPPER.resolve()
        or parent_cmdline[2] != "full"
    ):
        raise RuntimeError(
            "formal Gate 7 parent argv is not a direct full wrapper execution"
        )
    head_commit = _git_text("rev-parse", "HEAD")
    relative_wrapper = _project_path(FORMAL_WRAPPER)
    try:
        head_bytes = _git_bytes("show", "%s:%s" % (head_commit, relative_wrapper))
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("formal Gate 7 wrapper is not tracked at HEAD") from exc
    wrapper_identity = _artifact_identity(FORMAL_WRAPPER)
    if wrapper_identity != {
        "sha256": hashlib.sha256(head_bytes).hexdigest(),
        "bytes": len(head_bytes),
    }:
        raise RuntimeError("formal Gate 7 wrapper bytes differ from HEAD")
    return {
        "schema_version": FORMAL_ENTRYPOINT_SCHEMA_VERSION,
        "marker": FORMAL_ENTRYPOINT_MARKER,
        "mode": "full",
        "wrapper_pid": wrapper_pid,
        "producer_parent_pid": os.getppid(),
        "wrapper_path": relative_wrapper,
        "wrapper_identity": wrapper_identity,
        "parent_executable": parent_executable,
        "parent_cmdline": parent_cmdline,
        "bootstrap_attestation": bootstrap,
        "bootstrap_attestation_sha256": bootstrap["attestation_sha256"],
    }


def _valid_sha1_object(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{40}", value) is not None
    )


def _formal_source_anchor_is_structurally_valid(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    remote = value.get("remote")
    annotation = value.get("annotation")
    expected_keys = {
        "schema_version",
        "tag_name",
        "remote_name",
        "object_format",
        "tag_object_type",
        "tag_object_id",
        "peeled_commit",
        "head_commit",
        "tree_id",
        "tag_payload_sha256",
        "tag_payload_bytes",
        "annotation",
        "remote",
        "retained_tag_payload_path",
    }
    return bool(
        set(value) == expected_keys
        and value.get("schema_version") == FORMAL_SOURCE_ANCHOR_SCHEMA_VERSION
        and value.get("tag_name") == FORMAL_SOURCE_TAG
        and value.get("remote_name") == FORMAL_SOURCE_REMOTE
        and value.get("object_format") == "sha1"
        and value.get("tag_object_type") == "tag"
        and _valid_sha1_object(value.get("tag_object_id"))
        and _valid_sha1_object(value.get("peeled_commit"))
        and value.get("head_commit") == value.get("peeled_commit")
        and _valid_sha1_object(value.get("tree_id"))
        and isinstance(value.get("tag_payload_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["tag_payload_sha256"])
        is not None
        and isinstance(value.get("tag_payload_bytes"), int)
        and not isinstance(value.get("tag_payload_bytes"), bool)
        and value["tag_payload_bytes"] > 0
        and annotation
        == {
            "experiment_id": EXPERIMENT_ID,
            "contract_sha256": PINNED_CONTRACT_SHA256,
        }
        and remote
        == {
            "fetch_urls": [FORMAL_SOURCE_REMOTE_URL],
            "push_urls": [FORMAL_SOURCE_REMOTE_URL],
            "tag_object_id": value.get("tag_object_id"),
            "peeled_commit": value.get("peeled_commit"),
        }
        and value.get("retained_tag_payload_path")
        == FORMAL_SOURCE_TAG_PAYLOAD_PATH
    )


def _capture_formal_source_anchor(
    contract_identity: Mapping[str, Any], expected_head: Optional[str] = None
) -> Tuple[Dict[str, Any], bytes]:
    """Resolve the local annotated tag and prove matching remote publication."""

    if contract_identity.get("sha256") != PINNED_CONTRACT_SHA256:
        raise RuntimeError("formal source anchor contract identity differs")
    try:
        object_format = _git_text("rev-parse", "--show-object-format")
        tag_ref = "refs/tags/%s" % FORMAL_SOURCE_TAG
        tag_object_id = _git_text("rev-parse", tag_ref)
        tag_object_type = _git_text("cat-file", "-t", tag_object_id)
        peeled_commit = _git_text("rev-parse", "%s^{commit}" % tag_ref)
        head_commit = _git_text("rev-parse", "HEAD")
        tree_id = _git_text("rev-parse", "%s^{tree}" % peeled_commit)
        payload = _git_bytes("cat-file", "tag", tag_object_id)
        remote_fetch_urls = _git_text(
            "remote", "get-url", "--all", FORMAL_SOURCE_REMOTE
        ).splitlines()
        remote_push_urls = _git_text(
            "remote", "get-url", "--push", "--all", FORMAL_SOURCE_REMOTE
        ).splitlines()
        remote_refs = _remote_tag_refs(FORMAL_SOURCE_REMOTE, FORMAL_SOURCE_TAG)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("formal source anchor could not be resolved") from exc
    if object_format != "sha1" or tag_object_type != "tag":
        raise RuntimeError("formal source anchor must be an annotated SHA-1 tag")
    if not all(
        _valid_sha1_object(value)
        for value in (tag_object_id, peeled_commit, head_commit, tree_id)
    ):
        raise RuntimeError("formal source anchor contains a malformed object ID")
    if peeled_commit != head_commit or (
        expected_head is not None and peeled_commit != expected_head
    ):
        raise RuntimeError("formal source tag does not peel to the clean HEAD")
    try:
        header_bytes, annotation_bytes = payload.split(b"\n\n", 1)
        annotation_text = annotation_bytes.decode("utf-8")
    except (IndexError, UnicodeDecodeError) as exc:
        raise RuntimeError("formal source tag annotation is unreadable") from exc
    object_digest = hashlib.sha1(
        ("tag %d\0" % len(payload)).encode("ascii") + payload
    ).hexdigest()
    headers: Dict[str, str] = {}
    try:
        for raw_header in header_bytes.decode("utf-8").splitlines():
            key, header_value = raw_header.split(" ", 1)
            if key in headers:
                raise ValueError("duplicate tag header")
            headers[key] = header_value
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("formal source tag headers are malformed") from exc
    if (
        object_digest != tag_object_id
        or headers.get("object") != peeled_commit
        or headers.get("type") != "commit"
        or headers.get("tag") != FORMAL_SOURCE_TAG
    ):
        raise RuntimeError("formal source tag raw payload contradicts its object")
    required_annotation_lines = {
        "experiment_id=%s" % EXPERIMENT_ID,
        "contract_sha256=%s" % PINNED_CONTRACT_SHA256,
    }
    if not required_annotation_lines.issubset(set(annotation_text.splitlines())):
        raise RuntimeError("formal source tag annotation omits frozen identities")
    if (
        remote_fetch_urls != [FORMAL_SOURCE_REMOTE_URL]
        or remote_push_urls != [FORMAL_SOURCE_REMOTE_URL]
    ):
        raise RuntimeError(
            "formal source remote URL differs from frozen repository"
        )
    expected_remote = {
        tag_ref: tag_object_id,
        "%s^{}" % tag_ref: peeled_commit,
    }
    if remote_refs != expected_remote:
        raise RuntimeError("formal source tag differs from remote submission")
    anchor = {
        "schema_version": FORMAL_SOURCE_ANCHOR_SCHEMA_VERSION,
        "tag_name": FORMAL_SOURCE_TAG,
        "remote_name": FORMAL_SOURCE_REMOTE,
        "object_format": object_format,
        "tag_object_type": tag_object_type,
        "tag_object_id": tag_object_id,
        "peeled_commit": peeled_commit,
        "head_commit": head_commit,
        "tree_id": tree_id,
        "tag_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "tag_payload_bytes": len(payload),
        "annotation": {
            "experiment_id": EXPERIMENT_ID,
            "contract_sha256": PINNED_CONTRACT_SHA256,
        },
        "remote": {
            "fetch_urls": remote_fetch_urls,
            "push_urls": remote_push_urls,
            "tag_object_id": remote_refs[tag_ref],
            "peeled_commit": remote_refs["%s^{}" % tag_ref],
        },
        "retained_tag_payload_path": FORMAL_SOURCE_TAG_PAYLOAD_PATH,
    }
    if not _formal_source_anchor_is_structurally_valid(anchor):
        raise RuntimeError("formal source anchor is structurally malformed")
    return anchor, payload


def _power_observation() -> Dict[str, Any]:
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, OSError, psutil.Error):
        battery = None
    return {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "available": battery is not None,
        "plugged": bool(battery.power_plugged) if battery is not None else None,
        "percent": float(battery.percent) if battery is not None else None,
        "seconds_left": (
            int(battery.secsleft)
            if battery is not None and battery.secsleft is not None
            else None
        ),
    }


def _require_not_observed_unplugged(observation: Mapping[str, Any], stage: str) -> None:
    if observation.get("available") is True and observation.get("plugged") is not True:
        raise RuntimeError("formal Gate 7 power is unplugged at %s" % stage)


def _write_binary_atomic(path: Path, payload: bytes) -> None:
    def writer(output: Any) -> None:
        output.write(payload)

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % destination.name,
        suffix=".tmp",
        dir=str(destination.parent),
    )
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary_name)
        with Path(temporary_name).open("rb") as copied:
            os.fsync(copied.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _prepared_input_identities(
    prepared_dir: Path, formal: bool
) -> Dict[str, Dict[str, Any]]:
    prepared_dir = Path(prepared_dir).resolve()
    if formal and prepared_dir != DEFAULT_PREPARED.resolve():
        raise RuntimeError("formal Gate 7 requires the frozen prepared QQP directory")
    paths = {
        name: prepared_dir / name
        for name in ("pairs.jsonl", "texts.jsonl", "manifest.json")
    }
    if any(not path.is_file() for path in paths.values()):
        raise RuntimeError("prepared QQP input snapshot is incomplete")
    identities = {name: _artifact_identity(path) for name, path in paths.items()}
    try:
        manifest = json.loads(paths["manifest.json"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("prepared QQP manifest is unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "carma-qqp-v1"
        or manifest.get("pairs_sha256") != identities["pairs.jsonl"]["sha256"]
        or manifest.get("texts_sha256") != identities["texts.jsonl"]["sha256"]
    ):
        raise RuntimeError("prepared QQP manifest contradicts retained files")
    if formal:
        expected = {
            "pairs.jsonl": PINNED_PAIRS_SHA256,
            "texts.jsonl": PINNED_TEXTS_SHA256,
        }
        if any(
            identities[name]["sha256"] != digest
            for name, digest in expected.items()
        ):
            raise RuntimeError("formal prepared QQP files differ from frozen hashes")
        if (
            manifest.get("archive_sha256") != PINNED_ARCHIVE_SHA256
            or not DEFAULT_QQP_ARCHIVE.is_file()
            or sha256_file(DEFAULT_QQP_ARCHIVE) != PINNED_ARCHIVE_SHA256
        ):
            raise RuntimeError("formal raw QQP archive differs from the frozen hash")
    return identities


def _retain_source_inputs(
    prepared_dir: Path,
    output_dir: Path,
    formal: bool,
    dependency_attestation: Optional[Mapping[str, Any]] = None,
    formal_source_anchor: Optional[Mapping[str, Any]] = None,
    tag_payload: Optional[bytes] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """Copy every trace/audit input into the immutable attempt directory."""

    source_identities = _prepared_input_identities(prepared_dir, formal)
    retained_prepared = Path(output_dir) / RETAINED_PREPARED_DIRECTORY
    for name in ("pairs.jsonl", "texts.jsonl", "manifest.json"):
        destination = retained_prepared / name
        _copy_file_atomic(Path(prepared_dir) / name, destination)
        if _artifact_identity(destination) != source_identities[name]:
            raise RuntimeError("retained prepared QQP copy changed during capture")
    archive_declaration: Optional[Dict[str, Any]] = None
    dependency_declaration: Optional[Dict[str, Any]] = None
    tag_declaration: Optional[Dict[str, Any]] = None
    if formal:
        if (
            not isinstance(dependency_attestation, Mapping)
            or not isinstance(formal_source_anchor, Mapping)
            or tag_payload is None
        ):
            raise RuntimeError("formal source retention lacks frozen attestations")
        archive_path = Path(output_dir) / RETAINED_QQP_ARCHIVE_PATH
        _copy_file_atomic(DEFAULT_QQP_ARCHIVE, archive_path)
        archive_identity = _artifact_identity(archive_path)
        if archive_identity["sha256"] != PINNED_ARCHIVE_SHA256:
            raise RuntimeError("retained raw QQP archive identity changed")
        archive_declaration = {
            "path": RETAINED_QQP_ARCHIVE_PATH,
            **archive_identity,
        }
        dependency_path = Path(output_dir) / DEPENDENCY_ATTESTATION_PATH
        _write_json(dependency_path, dict(dependency_attestation))
        dependency_declaration = {
            "path": DEPENDENCY_ATTESTATION_PATH,
            **_artifact_identity(dependency_path),
            "attestation_sha256": dependency_attestation.get(
                "attestation_sha256"
            ),
        }
        tag_path = Path(output_dir) / FORMAL_SOURCE_TAG_PAYLOAD_PATH
        _write_binary_atomic(tag_path, tag_payload)
        tag_identity = _artifact_identity(tag_path)
        if (
            tag_identity.get("sha256")
            != formal_source_anchor.get("tag_payload_sha256")
            or tag_identity.get("bytes")
            != formal_source_anchor.get("tag_payload_bytes")
        ):
            raise RuntimeError("retained formal source tag payload identity changed")
        tag_declaration = {
            "path": FORMAL_SOURCE_TAG_PAYLOAD_PATH,
            **tag_identity,
        }
    prepared_declaration = {
        name: {
            "path": "%s/%s" % (RETAINED_PREPARED_DIRECTORY, name),
            **identity,
        }
        for name, identity in source_identities.items()
    }
    declaration = {
        "trace_construction_prepared_dir": RETAINED_PREPARED_DIRECTORY,
        "audit_source_policy": "retained_attempt_copies_only",
        "archive": archive_declaration,
        "prepared": prepared_declaration,
        "dependency_attestation": dependency_declaration,
        "formal_source_tag_payload": tag_declaration,
    }
    return retained_prepared.resolve(), declaration


def _retained_source_artifacts(output_dir: Path) -> Dict[str, Dict[str, Any]]:
    source_root = Path(output_dir) / "source"
    if not source_root.is_dir():
        return {}
    artifacts: Dict[str, Dict[str, Any]] = {}
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        artifacts[str(path.relative_to(output_dir))] = _artifact_identity(
            path, rows=_artifact_row_count(path)
        )
    return artifacts


def _assert_retained_inputs(
    output_dir: Path,
    declaration: Mapping[str, Any],
    dependency_attestation: Optional[Mapping[str, Any]],
) -> None:
    expected_records: List[Mapping[str, Any]] = []
    prepared = declaration.get("prepared")
    if not isinstance(prepared, dict):
        raise RuntimeError("retained prepared input declaration is missing")
    expected_records.extend(prepared.values())
    for field in (
        "archive",
        "dependency_attestation",
        "formal_source_tag_payload",
    ):
        record = declaration.get(field)
        if record is not None:
            expected_records.append(record)
    for record in expected_records:
        if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
            raise RuntimeError("retained input declaration is malformed")
        path = Path(output_dir) / str(record["path"])
        if not path.is_file() or _artifact_identity(path) != {
            "sha256": record.get("sha256"),
            "bytes": record.get("bytes"),
        }:
            raise RuntimeError("retained attempt input changed: %s" % record["path"])
    dependency_record = declaration.get("dependency_attestation")
    if dependency_record is not None:
        if not isinstance(dependency_attestation, Mapping):
            raise RuntimeError("retained dependency attestation lacks its source object")
        try:
            retained_dependency = json.loads(
                (Path(output_dir) / str(dependency_record["path"])).read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("retained dependency attestation is unreadable") from exc
        if retained_dependency != dict(dependency_attestation):
            raise RuntimeError("retained dependency attestation content changed")


def _ledger_entry_sha256(entry: Mapping[str, Any]) -> str:
    unhashed = dict(entry)
    unhashed.pop("entry_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(unhashed)).hexdigest()


def _valid_ledger_directory(root: Path, value: Any) -> bool:
    """Require one real, non-symlink direct-child attempt directory."""

    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        return False
    root = Path(root).resolve()
    candidate = root / value
    return (
        candidate.is_dir()
        and not candidate.is_symlink()
        and candidate.resolve().parent == root
    )


def _ledger_prefix_identity_from_raw_lines(
    raw_lines: Sequence[bytes], entry: Mapping[str, Any]
) -> Dict[str, Any]:
    """Reconstruct an already-read ledger prefix without recursive parsing."""

    sequence = entry.get("sequence")
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence <= 0
        or sequence > len(raw_lines)
    ):
        raise RuntimeError("formal attempt ledger prefix sequence is invalid")
    payload = b"".join(raw_lines[:sequence])
    return {
        "path": "attempt-ledger.jsonl",
        "prefix_rows": sequence,
        "prefix_sha256": hashlib.sha256(payload).hexdigest(),
        "prefix_bytes": len(payload),
        "entry_sha256": entry["entry_sha256"],
        "event": entry["event"],
    }


def _terminal_intent_from_components(
    start: Mapping[str, Any],
    terminal_record_sha256: str,
    terminal_record_bytes: int,
    preterminal_report_sha256: str,
    preterminal_report_bytes: int,
    preterminal_report_status: str,
    preterminal_report_claimable: bool,
    auditor_identity: Mapping[str, Any],
    target_exit_status: int,
    ledger_prefix: Mapping[str, Any],
) -> Dict[str, Any]:
    intent: Dict[str, Any] = {
        "schema_version": TERMINAL_INTENT_SCHEMA_VERSION,
        "attempt_id": start["attempt_id"],
        "directory": start["directory"],
        "terminal_record": "manifest.json",
        "terminal_record_sha256": terminal_record_sha256,
        "terminal_record_bytes": terminal_record_bytes,
        "preterminal_report": "gate7-preterminal-adjudication.json",
        "preterminal_report_sha256": preterminal_report_sha256,
        "preterminal_report_bytes": preterminal_report_bytes,
        "preterminal_report_status": preterminal_report_status,
        "preterminal_report_claimable": preterminal_report_claimable,
        "auditor_identity": dict(auditor_identity),
        "target_exit_status": target_exit_status,
        "ledger_prefix": dict(ledger_prefix),
    }
    intent["intent_sha256"] = _canonical_mapping_sha256(intent)
    if not _terminal_intent_is_structurally_valid(intent):
        raise RuntimeError("formal terminal intent is structurally invalid")
    return intent


def _read_attempt_ledger(root: Path) -> Tuple[List[Dict[str, Any]], List[bytes]]:
    path = Path(root) / "attempt-ledger.jsonl"
    if not path.exists():
        return [], []
    raw_lines = path.read_bytes().splitlines(keepends=True)
    entries: List[Dict[str, Any]] = []
    previous_hash: Optional[str] = None
    starts: Dict[str, Dict[str, Any]] = {}
    terminal_attempts: set = set()
    seen_directories: set = set()
    open_attempt_id: Optional[str] = None
    start_count = 0
    genesis_seen = False
    genesis_anchor: Optional[Dict[str, Any]] = None
    for position, raw_line in enumerate(raw_lines, start=1):
        try:
            entry = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("formal attempt ledger is not valid JSONL") from exc
        if not isinstance(entry, dict):
            raise RuntimeError("formal attempt ledger row is not an object")
        if raw_line != (
            json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"):
            raise RuntimeError("formal attempt ledger is not canonically encoded")
        entry_hash = entry.get("entry_sha256")
        expected_hash = _ledger_entry_sha256(entry)
        event = entry.get("event")
        if (
            entry.get("schema_version") != ATTEMPT_LEDGER_SCHEMA_VERSION
            or entry.get("experiment_id") != EXPERIMENT_ID
            or entry.get("sequence") != position
            or entry.get("previous_entry_sha256") != previous_hash
            or entry_hash != expected_hash
            or event not in ("PROTOCOL_GENESIS", "START", "TERMINAL")
        ):
            raise RuntimeError("formal attempt ledger integrity check failed")

        if event == "PROTOCOL_GENESIS":
            preservation = entry.get("v1_preservation")
            contract = entry.get("v2_contract")
            if (
                position != 1
                or genesis_seen
                or not isinstance(preservation, dict)
                or preservation != _v1_preservation_identity()
                or not isinstance(contract, dict)
                or contract
                != {
                    "path": _project_path(DEFAULT_CONTRACT),
                    "sha256": PINNED_CONTRACT_SHA256,
                }
                or not _formal_source_anchor_is_structurally_valid(
                    entry.get("formal_source_anchor")
                )
                or _parse_attempt_timestamp(entry.get("recorded_at_utc")) is None
                or set(entry)
                != {
                    "schema_version",
                    "experiment_id",
                    "event",
                    "sequence",
                    "recorded_at_utc",
                    "v1_preservation",
                    "v2_contract",
                    "formal_source_anchor",
                    "previous_entry_sha256",
                    "entry_sha256",
                }
            ):
                raise RuntimeError("formal attempt ledger genesis check failed")
            genesis_seen = True
            genesis_anchor = dict(entry["formal_source_anchor"])
            previous_hash = str(entry_hash)
            entries.append(entry)
            continue

        if not genesis_seen:
            raise RuntimeError("formal attempt ledger has no protocol genesis")

        started_at = _parse_attempt_timestamp(entry.get("started_at_utc"))
        head_commit = entry.get("head_commit")
        attempt_id = entry.get("attempt_id")
        directory = entry.get("directory")
        common_identity_valid = (
            started_at is not None
            and isinstance(head_commit, str)
            and len(head_commit) == 40
            and not any(
                character not in "0123456789abcdef" for character in head_commit
            )
            and attempt_id == _attempt_id_for(started_at, head_commit)
            and _valid_ledger_directory(Path(root), directory)
        )
        if not common_identity_valid:
            raise RuntimeError("formal attempt ledger identity check failed")

        if event == "START":
            start_fields = {
                "schema_version",
                "experiment_id",
                "sequence",
                "event",
                "attempt_id",
                "started_at_utc",
                "head_commit",
                "directory",
                "prior_attempt_count",
                "prior_attempts_sha256",
                "contract",
                "source_snapshot_sha256",
                "formal_source_anchor",
                "dependency_attestation_sha256",
                "environment_sha256",
                "entrypoint_attestation",
                "pre_start_power",
                "retained_inputs",
                "previous_entry_sha256",
                "entry_sha256",
            }
            contract = entry.get("contract")
            entrypoint = entry.get("entrypoint_attestation")
            pre_start_power = entry.get("pre_start_power")
            if (
                set(entry) != start_fields
                or open_attempt_id is not None
                or attempt_id in starts
                or directory in seen_directories
                or entry.get("prior_attempt_count") != start_count
                or not isinstance(entry.get("prior_attempts_sha256"), str)
                or len(str(entry.get("prior_attempts_sha256"))) != 64
                or contract
                != {
                    "path": _project_path(DEFAULT_CONTRACT),
                    "sha256": PINNED_CONTRACT_SHA256,
                    "bytes": contract.get("bytes") if isinstance(contract, dict) else None,
                }
                or not isinstance(contract.get("bytes"), int)
                or isinstance(contract.get("bytes"), bool)
                or contract.get("bytes", 0) <= 0
                or not isinstance(entry.get("source_snapshot_sha256"), str)
                or len(entry.get("source_snapshot_sha256", "")) != 64
                or entry.get("formal_source_anchor") != genesis_anchor
                or not isinstance(entry.get("dependency_attestation_sha256"), str)
                or len(entry.get("dependency_attestation_sha256", "")) != 64
                or not isinstance(entry.get("environment_sha256"), str)
                or len(entry.get("environment_sha256", "")) != 64
                or not isinstance(entrypoint, dict)
                or entrypoint.get("schema_version")
                != FORMAL_ENTRYPOINT_SCHEMA_VERSION
                or not isinstance(pre_start_power, dict)
                or not isinstance(pre_start_power.get("available"), bool)
                or not isinstance(entry.get("retained_inputs"), dict)
            ):
                raise RuntimeError("formal attempt ledger START check failed")
            starts[str(attempt_id)] = entry
            seen_directories.add(str(directory))
            open_attempt_id = str(attempt_id)
            start_count += 1
        else:
            start = starts.get(str(attempt_id))
            terminal_record = entry.get("terminal_record")
            terminal_fields = {
                "schema_version",
                "experiment_id",
                "sequence",
                "event",
                "attempt_id",
                "started_at_utc",
                "head_commit",
                "directory",
                "formal_source_anchor",
                "dependency_attestation_sha256",
                "environment_sha256",
                "terminal_record",
                "terminal_record_sha256",
                "terminal_record_bytes",
                "preterminal_report",
                "preterminal_report_sha256",
                "preterminal_report_bytes",
                "preterminal_report_status",
                "preterminal_report_claimable",
                "auditor_identity",
                "bootstrap_completion",
                "previous_entry_sha256",
                "entry_sha256",
            }
            if (
                set(entry) != terminal_fields
                or start is None
                or open_attempt_id != attempt_id
                or attempt_id in terminal_attempts
                or entry.get("started_at_utc") != start.get("started_at_utc")
                or head_commit != start.get("head_commit")
                or directory != start.get("directory")
                or entry.get("formal_source_anchor")
                != start.get("formal_source_anchor")
                or entry.get("dependency_attestation_sha256")
                != start.get("dependency_attestation_sha256")
                or entry.get("environment_sha256")
                != start.get("environment_sha256")
                or terminal_record not in ("manifest.json", "attempt-failure.json")
                or not isinstance(entry.get("terminal_record_sha256"), str)
                or len(str(entry.get("terminal_record_sha256"))) != 64
                or not isinstance(entry.get("terminal_record_bytes"), int)
                or isinstance(entry.get("terminal_record_bytes"), bool)
                or int(entry.get("terminal_record_bytes")) <= 0
            ):
                raise RuntimeError("formal attempt ledger TERMINAL check failed")
            if terminal_record == "manifest.json":
                auditor_identity = entry.get("auditor_identity")
                if (
                    entry.get("preterminal_report")
                    != "gate7-preterminal-adjudication.json"
                    or not isinstance(entry.get("preterminal_report_sha256"), str)
                    or len(str(entry.get("preterminal_report_sha256"))) != 64
                    or not isinstance(entry.get("preterminal_report_bytes"), int)
                    or isinstance(entry.get("preterminal_report_bytes"), bool)
                    or int(entry.get("preterminal_report_bytes")) <= 0
                    or entry.get("preterminal_report_status")
                    not in ("pass", "fail", "pending", "invalid")
                    or not isinstance(entry.get("preterminal_report_claimable"), bool)
                    or entry.get("preterminal_report_claimable")
                    != (entry.get("preterminal_report_status") in ("pass", "fail"))
                    or not isinstance(auditor_identity, dict)
                    or auditor_identity.get("path")
                    != "benchmarks/carma/gate7_v2_audit.py"
                    or not isinstance(auditor_identity.get("sha256"), str)
                    or len(str(auditor_identity.get("sha256"))) != 64
                    or not isinstance(auditor_identity.get("bytes"), int)
                    or isinstance(auditor_identity.get("bytes"), bool)
                    or int(auditor_identity.get("bytes")) <= 0
                ):
                    raise RuntimeError(
                        "formal attempt ledger completed TERMINAL check failed"
                    )
                expected_intent = _terminal_intent_from_components(
                    start,
                    str(entry["terminal_record_sha256"]),
                    int(entry["terminal_record_bytes"]),
                    str(entry["preterminal_report_sha256"]),
                    int(entry["preterminal_report_bytes"]),
                    str(entry["preterminal_report_status"]),
                    bool(entry["preterminal_report_claimable"]),
                    auditor_identity,
                    PRETERMINAL_STATUS_EXIT_STATUS[
                        str(entry["preterminal_report_status"])
                    ],
                    _ledger_prefix_identity_from_raw_lines(raw_lines, start),
                )
                if not _bootstrap_completion_is_structurally_valid(
                    entry.get("bootstrap_completion"), start, expected_intent
                ):
                    raise RuntimeError(
                        "formal attempt ledger bootstrap completion check failed"
                    )
            elif (
                any(
                    entry.get(field) is not None
                    for field in (
                        "preterminal_report",
                        "preterminal_report_sha256",
                        "preterminal_report_bytes",
                        "auditor_identity",
                        "bootstrap_completion",
                    )
                )
                or entry.get("preterminal_report_status") != "invalid"
                or entry.get("preterminal_report_claimable") is not False
            ):
                raise RuntimeError(
                    "formal attempt ledger failure TERMINAL carries adjudication"
                )
            terminal_attempts.add(str(attempt_id))
            open_attempt_id = None
        previous_hash = entry_hash
        entries.append(entry)
    return entries, raw_lines


def _ensure_protocol_genesis(
    root: Path, formal_source_anchor: Mapping[str, Any]
) -> Dict[str, Any]:
    """Create the single v2 ledger genesis binding the preserved v1 failure."""

    root = Path(root)
    if not _formal_source_anchor_is_structurally_valid(formal_source_anchor):
        raise RuntimeError("formal protocol genesis source anchor is invalid")
    entries, _ = _read_attempt_ledger(root)
    if entries:
        if entries[0].get("event") != "PROTOCOL_GENESIS":
            raise RuntimeError("formal attempt ledger has no protocol genesis")
        if entries[0].get("formal_source_anchor") != dict(formal_source_anchor):
            raise RuntimeError("formal source anchor differs from protocol genesis")
        return dict(entries[0])
    entry: Dict[str, Any] = {
        "schema_version": ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "event": "PROTOCOL_GENESIS",
        "sequence": 1,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "v1_preservation": _v1_preservation_identity(),
        "v2_contract": {
            "path": _project_path(DEFAULT_CONTRACT),
            "sha256": PINNED_CONTRACT_SHA256,
        },
        "formal_source_anchor": dict(formal_source_anchor),
        "previous_entry_sha256": None,
    }
    entry["entry_sha256"] = _ledger_entry_sha256(entry)
    _append_attempt_ledger_entry(root, entry)
    return entry


def _append_attempt_ledger_entry(root: Path, entry: Mapping[str, Any]) -> None:
    """Append one canonical ledger event and force it to stable storage."""

    path = Path(root) / "attempt-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json_bytes(entry) + b"\n"
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("failed to append formal attempt ledger event")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ledger_prefix_identity(root: Path, entry: Mapping[str, Any]) -> Dict[str, Any]:
    ledger_path = Path(root) / "attempt-ledger.jsonl"
    entries, _ = _read_attempt_ledger(root)
    identity = _artifact_identity(ledger_path, rows=len(entries))
    return {
        "path": "attempt-ledger.jsonl",
        "prefix_rows": len(entries),
        "prefix_sha256": identity["sha256"],
        "prefix_bytes": identity["bytes"],
        "entry_sha256": entry["entry_sha256"],
        "event": entry["event"],
    }


def _formal_start_entry(
    output_dir: Path, attempt_id: str
) -> Optional[Dict[str, Any]]:
    """Independently resolve this directory's START from the canonical ledger."""

    output_dir = Path(output_dir).resolve()
    entries, _ = _read_attempt_ledger(output_dir.parent)
    matches = [
        entry
        for entry in entries
        if entry.get("event") == "START"
        and (
            entry.get("attempt_id") == attempt_id
            or entry.get("directory") == output_dir.name
        )
    ]
    if len(matches) > 1:
        raise RuntimeError("formal ledger repeats the attempted START identity")
    if matches and (
        matches[0].get("attempt_id") != attempt_id
        or matches[0].get("directory") != output_dir.name
    ):
        raise RuntimeError("formal ledger START identity contradicts the directory")
    return dict(matches[0]) if matches else None


def _quarantine_unregistered_formal_attempt(
    output_dir: Path, attempt_id: str, error: BaseException
) -> Path:
    """Move only a ledger-proven unregistered directory out of the formal root."""

    output_dir = Path(output_dir).resolve()
    formal_root = FORMAL_ATTEMPT_ROOT.resolve()
    if (
        output_dir.parent != formal_root
        or output_dir.is_symlink()
        or not output_dir.is_dir()
        or not attempt_id
    ):
        raise RuntimeError("refusing to quarantine an unsafe formal path")
    if _formal_start_entry(output_dir, attempt_id) is not None:
        raise RuntimeError("refusing to quarantine a START-registered attempt")
    quarantine_root = FORMAL_PREFLIGHT_FAILURE_ROOT.resolve()
    if _path_is_under(quarantine_root, formal_root):
        raise RuntimeError("preflight quarantine overlaps the formal root")
    quarantine_root.mkdir(parents=True, exist_ok=True)
    destination = quarantine_root / ("%s-unregistered" % attempt_id)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("preflight quarantine destination already exists")
    os.replace(output_dir, destination)
    diagnostic = {
        "schema_version": "carma-gate7-preflight-failure-v1",
        "experiment_id": EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "attempt_directory": output_dir.name,
        "registered_start": False,
        "quarantined_at_utc": datetime.now(timezone.utc).isoformat(),
        "error_type": type(error).__name__,
        "error_message": str(error)[:2000],
    }
    _write_json(destination / "preflight-failure.json", diagnostic)
    return destination


def _register_formal_attempt(
    output_dir: Path,
    attempt_id: str,
    started_at_utc: datetime,
    head_commit: str,
    prior_attempts: Sequence[Mapping[str, Any]],
    contract_identity: Mapping[str, Any],
    source_snapshot_sha256: str,
    formal_source_anchor: Mapping[str, Any],
    dependency_attestation_sha256: str,
    environment_sha256: str,
    entrypoint_attestation: Mapping[str, Any],
    pre_start_power: Mapping[str, Any],
    retained_inputs: Mapping[str, Any],
    dependency_attestation: Optional[Mapping[str, Any]] = None,
    final_pre_start_check: Optional[Callable[[], None]] = None,
    power_observer: Optional[Callable[[], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    root = Path(output_dir).resolve().parent
    if (
        contract_identity.get("sha256") != PINNED_CONTRACT_SHA256
        or not isinstance(contract_identity.get("bytes"), int)
        or not isinstance(source_snapshot_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_snapshot_sha256) is None
        or not _formal_source_anchor_is_structurally_valid(formal_source_anchor)
        or formal_source_anchor.get("head_commit") != head_commit
        or not isinstance(dependency_attestation_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", dependency_attestation_sha256) is None
        or not isinstance(environment_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", environment_sha256) is None
        or entrypoint_attestation.get("schema_version")
        != FORMAL_ENTRYPOINT_SCHEMA_VERSION
        or (
            power_observer is None
            and not isinstance(pre_start_power.get("available"), bool)
        )
        or not isinstance(retained_inputs, Mapping)
    ):
        raise RuntimeError("formal START attestations are malformed")
    _ensure_protocol_genesis(root, formal_source_anchor)
    entries, _ = _read_attempt_ledger(root)
    starts = [entry for entry in entries if entry.get("event") == "START"]
    terminals = {
        str(entry["attempt_id"]): entry
        for entry in entries
        if entry.get("event") == "TERMINAL"
    }
    if len(starts) != len(terminals):
        raise RuntimeError(
            "formal attempt ledger contains an unmatched START; resolve the retained attempt before registering another"
        )
    if len(starts) != len(prior_attempts):
        raise RuntimeError(
            "formal attempt ledger length differs from retained predecessor chain"
        )
    for position, (entry, predecessor) in enumerate(
        zip(starts, prior_attempts), start=1
    ):
        terminal = terminals.get(str(entry.get("attempt_id")))
        record_path = (
            root
            / str(predecessor.get("directory"))
            / str(predecessor.get("record"))
        )
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "formal attempt ledger predecessor is unreadable"
            ) from exc
        if (
            terminal is None
            or entry.get("attempt_id") != predecessor.get("attempt_id")
            or entry.get("started_at_utc") != predecessor.get("started_at_utc")
            or entry.get("directory") != predecessor.get("directory")
            or entry.get("prior_attempt_count") != position - 1
            or not isinstance(record, dict)
            or entry.get("prior_attempts_sha256")
            != _attempts_sha256(record.get("prior_attempts", []))
            or terminal.get("terminal_record") != predecessor.get("record")
            or terminal.get("terminal_record_sha256")
            != predecessor.get("record_sha256")
            or terminal.get("terminal_record_bytes")
            != predecessor.get("record_bytes")
            or record.get("formal_source_anchor")
            != entry.get("formal_source_anchor")
            or {
                key: record.get("contract", {}).get(key)
                for key in ("path", "sha256", "bytes")
            }
            != entry.get("contract")
            or record.get("source_snapshot_sha256")
            != entry.get("source_snapshot_sha256")
            or not isinstance(record.get("dependency_attestation"), dict)
            or record["dependency_attestation"].get("attestation_sha256")
            != entry.get("dependency_attestation_sha256")
            or record.get("environment_sha256")
            != entry.get("environment_sha256")
            or record.get("entrypoint_attestation")
            != entry.get("entrypoint_attestation")
            or record.get("retained_inputs") != entry.get("retained_inputs")
        ):
            raise RuntimeError(
                "formal attempt ledger differs from retained predecessor records"
            )
        if terminal.get("terminal_record") == "manifest.json":
            report_path = root / str(entry["directory"]) / str(
                terminal.get("preterminal_report")
            )
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "formal attempt ledger preterminal adjudication is unreadable"
                ) from exc
            if (
                not report_path.is_file()
                or terminal.get("preterminal_report_sha256")
                != sha256_file(report_path)
                or terminal.get("preterminal_report_bytes")
                != int(report_path.stat().st_size)
                or not isinstance(report, dict)
                or report.get("status")
                != terminal.get("preterminal_report_status")
                or report.get("claimable")
                != terminal.get("preterminal_report_claimable")
                or report.get("auditor_identity")
                != terminal.get("auditor_identity")
            ):
                raise RuntimeError(
                    "formal attempt ledger preterminal adjudication was altered"
                )
            if terminal.get("preterminal_report_status") in ("pass", "fail"):
                raise RuntimeError(
                    "a TERMINAL-bound claimable Gate 7 attempt already exists in the retained formal root"
                )
    # This is the final append boundary: potentially lengthy genesis and
    # predecessor validation is complete, while the root lock is still held.
    if final_pre_start_check is not None:
        final_pre_start_check()
    if dependency_attestation is not None:
        if (
            dependency_attestation.get("attestation_sha256")
            != dependency_attestation_sha256
        ):
            raise RuntimeError(
                "retained-input dependency identity differs from START"
            )
        _assert_retained_inputs(
            output_dir, retained_inputs, dependency_attestation
        )
    if power_observer is not None:
        observed_power = power_observer()
        if not isinstance(observed_power, Mapping) or not isinstance(
            pre_start_power, dict
        ):
            raise RuntimeError("formal pre-START power observation is malformed")
        pre_start_power.clear()
        pre_start_power.update(dict(observed_power))
    if not isinstance(pre_start_power.get("available"), bool):
        raise RuntimeError("formal pre-START power observation is malformed")
    _require_not_observed_unplugged(pre_start_power, "pre-START")
    entry: Dict[str, Any] = {
        "schema_version": ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "sequence": len(entries) + 1,
        "event": "START",
        "attempt_id": attempt_id,
        "started_at_utc": started_at_utc.isoformat(),
        "head_commit": head_commit,
        "directory": Path(output_dir).name,
        "prior_attempt_count": len(prior_attempts),
        "prior_attempts_sha256": _attempts_sha256(prior_attempts),
        "contract": {
            "path": _project_path(DEFAULT_CONTRACT),
            **dict(contract_identity),
        },
        "source_snapshot_sha256": source_snapshot_sha256,
        "formal_source_anchor": dict(formal_source_anchor),
        "dependency_attestation_sha256": dependency_attestation_sha256,
        "environment_sha256": environment_sha256,
        "entrypoint_attestation": dict(entrypoint_attestation),
        "pre_start_power": dict(pre_start_power),
        "retained_inputs": dict(retained_inputs),
        "previous_entry_sha256": (
            entries[-1]["entry_sha256"] if entries else None
        ),
    }
    entry["entry_sha256"] = _ledger_entry_sha256(entry)
    _append_attempt_ledger_entry(root, entry)
    return _ledger_prefix_identity(root, entry)


def _formal_terminal_evidence(
    output_dir: Path,
    terminal_record: str,
    preterminal_report: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate immutable terminal artifacts against the unmatched START."""

    output_dir = Path(output_dir).resolve()
    root = output_dir.parent
    entries, _ = _read_attempt_ledger(root)
    if not entries or entries[-1].get("event") != "START":
        raise RuntimeError("formal attempt has no unmatched terminal START")
    start = entries[-1]
    if start.get("directory") != output_dir.name:
        raise RuntimeError("formal terminal directory differs from the open START")
    if terminal_record not in ("manifest.json", "attempt-failure.json"):
        raise ValueError("unknown formal terminal record")
    record_path = output_dir / terminal_record
    if not record_path.is_file():
        raise RuntimeError("formal terminal record is missing")
    try:
        terminal_value = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("formal terminal record is unreadable") from exc
    terminal_power = (
        terminal_value.get("power_observations")
        if isinstance(terminal_value, dict)
        else None
    )
    terminal_contract = (
        terminal_value.get("contract")
        if isinstance(terminal_value, dict)
        else None
    )
    if (
        not isinstance(terminal_value, dict)
        or not isinstance(terminal_contract, dict)
        or {
            key: terminal_contract.get(key)
            for key in ("path", "sha256", "bytes")
        }
        != start.get("contract")
        or terminal_value.get("formal_source_anchor")
        != start.get("formal_source_anchor")
        or terminal_value.get("source_snapshot_sha256")
        != start.get("source_snapshot_sha256")
        or not isinstance(terminal_value.get("dependency_attestation"), dict)
        or terminal_value["dependency_attestation"].get("attestation_sha256")
        != start.get("dependency_attestation_sha256")
        or terminal_value.get("environment_sha256")
        != start.get("environment_sha256")
        or terminal_value.get("entrypoint_attestation")
        != start.get("entrypoint_attestation")
        or not isinstance(terminal_power, dict)
        or terminal_power.get("pre_start")
        != start.get("pre_start_power")
        or terminal_value.get("retained_inputs") != start.get("retained_inputs")
    ):
        raise RuntimeError("formal terminal record differs from its START attestations")

    report_path: Optional[Path] = None
    if terminal_record == "manifest.json":
        if not isinstance(preterminal_report, Mapping):
            raise RuntimeError("completed formal attempt has no preterminal report")
        report_name = "gate7-preterminal-adjudication.json"
        report_path = output_dir / report_name
        if not report_path.is_file():
            raise RuntimeError("formal preterminal adjudication artifact is missing")
        try:
            retained_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "formal preterminal adjudication artifact is unreadable"
            ) from exc
        if retained_report != dict(preterminal_report):
            raise RuntimeError(
                "formal preterminal adjudication argument differs from retained bytes"
            )
        status = preterminal_report.get("status")
        claimable = preterminal_report.get("claimable")
        auditor_identity = preterminal_report.get("auditor_identity")
        terminal_manifest = terminal_value
        source_identities = (
            terminal_manifest.get("source_identities")
            if isinstance(terminal_manifest, dict)
            else None
        )
        frozen_auditor = (
            source_identities.get("benchmarks/carma/gate7_v2_audit.py")
            if isinstance(source_identities, dict)
            else None
        )
        if (
            status not in ("pass", "fail", "pending", "invalid")
            or not isinstance(claimable, bool)
            or claimable != (status in ("pass", "fail"))
            or not isinstance(auditor_identity, dict)
            or preterminal_report.get("audit_phase") != "preterminal"
            or preterminal_report.get("attempt_id") != start["attempt_id"]
            or frozen_auditor != auditor_identity
        ):
            raise RuntimeError("formal preterminal adjudication is malformed")
    else:
        report_name = None
        status = "invalid"
        claimable = False
        auditor_identity = None

    return {
        "output_dir": output_dir,
        "root": root,
        "entries": entries,
        "start": start,
        "record_path": record_path,
        "report_path": report_path,
        "report_name": report_name,
        "status": status,
        "claimable": claimable,
        "auditor_identity": (
            dict(auditor_identity) if auditor_identity is not None else None
        ),
    }


def _prepare_formal_terminal_intent(
    output_dir: Path,
    preterminal_report: Mapping[str, Any],
    target_exit_status: int,
) -> Dict[str, Any]:
    """Prepare, but never append, the parent-bootstrap terminal handoff."""

    evidence = _formal_terminal_evidence(
        output_dir, "manifest.json", preterminal_report
    )
    status = str(evidence["status"])
    if (
        not isinstance(target_exit_status, int)
        or isinstance(target_exit_status, bool)
        or target_exit_status != PRETERMINAL_STATUS_EXIT_STATUS.get(status)
    ):
        raise RuntimeError(
            "formal terminal intent exit status contradicts preterminal status"
        )
    record_path = evidence["record_path"]
    report_path = evidence["report_path"]
    if not isinstance(record_path, Path) or not isinstance(report_path, Path):
        raise RuntimeError("formal terminal intent artifacts are malformed")
    start = evidence["start"]
    return _terminal_intent_from_components(
        start,
        sha256_file(record_path),
        int(record_path.stat().st_size),
        sha256_file(report_path),
        int(report_path.stat().st_size),
        status,
        bool(evidence["claimable"]),
        evidence["auditor_identity"],
        target_exit_status,
        _ledger_prefix_identity(evidence["root"], start),
    )


def _append_formal_attempt_terminal(
    output_dir: Path,
    terminal_record: str,
    preterminal_report: Optional[Mapping[str, Any]] = None,
    *,
    terminal_intent: Optional[Mapping[str, Any]] = None,
    bootstrap_completion: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Append only a nonclaimable failure TERMINAL from producer code.

    Completed-manifest TERMINAL capability belongs exclusively to the isolated
    parent bootstrap after its authoritative post-target seal.
    """

    if terminal_record != "attempt-failure.json":
        raise RuntimeError(
            "producer may append only an attempt-failure TERMINAL"
        )
    if (
        preterminal_report is not None
        or terminal_intent is not None
        or bootstrap_completion is not None
    ):
        raise RuntimeError(
            "failure terminal must not carry adjudication, terminal intent, or bootstrap completion"
        )

    evidence = _formal_terminal_evidence(
        output_dir, terminal_record, None
    )
    root = evidence["root"]
    entries = evidence["entries"]
    start = evidence["start"]
    record_path = evidence["record_path"]

    entry: Dict[str, Any] = {
        "schema_version": ATTEMPT_LEDGER_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "sequence": len(entries) + 1,
        "event": "TERMINAL",
        "attempt_id": start["attempt_id"],
        "started_at_utc": start["started_at_utc"],
        "head_commit": start["head_commit"],
        "directory": start["directory"],
        "formal_source_anchor": start["formal_source_anchor"],
        "dependency_attestation_sha256": start[
            "dependency_attestation_sha256"
        ],
        "environment_sha256": start["environment_sha256"],
        "terminal_record": terminal_record,
        "terminal_record_sha256": sha256_file(record_path),
        "terminal_record_bytes": int(record_path.stat().st_size),
        "preterminal_report": None,
        "preterminal_report_sha256": None,
        "preterminal_report_bytes": None,
        "preterminal_report_status": "invalid",
        "preterminal_report_claimable": False,
        "auditor_identity": None,
        "bootstrap_completion": None,
        "previous_entry_sha256": entries[-1]["entry_sha256"],
    }
    entry["entry_sha256"] = _ledger_entry_sha256(entry)
    _append_attempt_ledger_entry(root, entry)
    return _ledger_prefix_identity(root, entry)


@contextmanager
def _formal_attempt_lock(enabled: bool) -> Iterable[None]:
    """Serialize formal attempt discovery and execution on this checkout."""

    if not enabled:
        yield
        return
    FORMAL_ATTEMPT_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = FORMAL_ATTEMPT_ROOT / ".formal-attempt.lock"
    descriptor = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise RuntimeError(
                "another formal Gate 7 attempt already holds the checkout lock"
            ) from exc
        metadata = json.dumps(
            {
                "pid": os.getpid(),
                "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        os.ftruncate(descriptor, 0)
        os.write(descriptor, metadata)
        os.fsync(descriptor)
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _retain_attempt_failure(
    output_dir: Path,
    attempt_id: str,
    started_at_utc: datetime,
    seed: int,
    policy: str,
    order_position: int,
    error: BaseException,
    samples: Sequence[Mapping[str, Any]],
    config: Gate7Config,
    contract_identity: Mapping[str, Any],
    git_state: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    prior_attempts: Sequence[Mapping[str, Any]],
    attempt_ledger: Optional[Mapping[str, Any]],
    child_stdout: Optional[str] = None,
    child_stderr: Optional[str] = None,
    diagnostic_summaries: Sequence[Mapping[str, Any]] = (),
    diagnostic_requests: Sequence[Mapping[str, Any]] = (),
    structural_summary: Optional[Mapping[str, Any]] = None,
    child_diagnostic: Optional[Mapping[str, Any]] = None,
    dependency_attestation: Optional[Mapping[str, Any]] = None,
    formal_source_anchor: Optional[Mapping[str, Any]] = None,
    source_snapshot_sha256: Optional[str] = None,
    entrypoint_attestation: Optional[Mapping[str, Any]] = None,
    power_observations: Optional[Mapping[str, Any]] = None,
    retained_inputs: Optional[Mapping[str, Any]] = None,
) -> None:
    """Persist an operationally invalid attempt before propagating its error."""

    artifacts: Dict[str, Any] = {
        **_retained_source_artifacts(output_dir),
    }
    traces_dir = Path(output_dir) / "traces"
    if traces_dir.is_dir():
        for trace_path in sorted(traces_dir.glob("*.jsonl")):
            relative = str(trace_path.relative_to(output_dir))
            artifacts[relative] = _artifact_identity(
                trace_path,
                rows=sum(
                    1
                    for line in trace_path.read_text(encoding="utf-8").splitlines()
                    if line
                ),
            )
    semantic_dir = Path(output_dir) / "semantic-indexes"
    if semantic_dir.is_dir():
        for semantic_path in sorted(semantic_dir.glob("*.json")):
            relative = str(semantic_path.relative_to(output_dir))
            artifacts[relative] = _artifact_identity(semantic_path)
    if diagnostic_summaries:
        summaries_path = Path(output_dir) / "failed-run-summaries.jsonl"
        _write_jsonl(
            summaries_path, (dict(summary) for summary in diagnostic_summaries)
        )
        artifacts[summaries_path.name] = _artifact_identity(
            summaries_path, rows=len(diagnostic_summaries)
        )
    if diagnostic_requests:
        requests_path = Path(output_dir) / "failed-requests.jsonl"
        _write_jsonl(
            requests_path, (dict(request) for request in diagnostic_requests)
        )
        artifacts[requests_path.name] = _artifact_identity(
            requests_path, rows=len(diagnostic_requests)
        )
    if structural_summary is not None:
        structural_path = Path(output_dir) / "failed-structural-summary.json"
        _write_json(structural_path, dict(structural_summary))
        artifacts[structural_path.name] = _artifact_identity(structural_path)
    if samples:
        resources_path = Path(output_dir) / "failed-resources.jsonl"
        _write_jsonl(resources_path, (dict(sample) for sample in samples))
        artifacts[resources_path.name] = _artifact_identity(
            resources_path, rows=len(samples)
        )
    captured_stdout = (
        error.stdout if isinstance(error, ChildExecutionError) else child_stdout
    )
    captured_stderr = (
        error.stderr if isinstance(error, ChildExecutionError) else child_stderr
    )
    if captured_stdout is not None or captured_stderr is not None:
        for filename, content in (
            ("child.stdout.log", captured_stdout or ""),
            ("child.stderr.log", captured_stderr or ""),
        ):
            path = Path(output_dir) / filename

            def write_log(output: Any, value: str = content) -> None:
                output.write(value)

            _atomic_write(path, write_log)
            artifacts[filename] = _artifact_identity(path)
    child_diagnostic_summary = (
        dict(child_diagnostic) if child_diagnostic is not None else None
    )
    if child_diagnostic_summary is not None:
        child_diagnostic_summary.pop("completed_requests", None)
        child_diagnostic_summary["completed_requests_artifact"] = (
            "failed-requests.jsonl" if diagnostic_requests else None
        )
    failure = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "kind": "prospective_gate7_failed_attempt",
        "status": "invalid",
        "started_at_utc": started_at_utc.isoformat(),
        "failed_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "policy": policy,
        "order_position": order_position,
        "error_type": type(error).__name__,
        "error": str(error),
        "child_exit_status": (
            error.returncode if isinstance(error, ChildExecutionError) else None
        ),
        "config": asdict(config),
        "prior_attempt_exists": bool(prior_attempts),
        "prior_attempts": [dict(attempt) for attempt in prior_attempts],
        "attempt_policy": {
            "formal_root": _project_path(FORMAL_ATTEMPT_ROOT),
            "directory": Path(output_dir).name,
            "eligibility": "first structurally valid complete attempt in the retained predecessor chain",
            "rerun_scope": "complete five-seed, three-policy matrix under a new attempt ID",
        },
        "attempt_ledger": dict(attempt_ledger) if attempt_ledger else None,
        "contract": {
            "path": _project_path(DEFAULT_CONTRACT),
            **dict(contract_identity),
        },
        "git": dict(git_state),
        "source_identities": dict(source_snapshot),
        "source_snapshot_sha256": source_snapshot_sha256,
        "formal_source_anchor": (
            dict(formal_source_anchor) if formal_source_anchor else None
        ),
        "dependency_attestation": (
            dict(dependency_attestation) if dependency_attestation else None
        ),
        "environment_sha256": (
            dependency_attestation.get("environment", {}).get(
                "environment_sha256"
            )
            if dependency_attestation
            else None
        ),
        "entrypoint_attestation": (
            dict(entrypoint_attestation) if entrypoint_attestation else None
        ),
        "power_observations": (
            dict(power_observations) if power_observations else None
        ),
        "retained_inputs": dict(retained_inputs) if retained_inputs else None,
        "v1_lineage": _v1_preservation_identity(),
        "diagnostic_only": True,
        "numerically_claimable": False,
        "structural_summary": (
            dict(structural_summary) if structural_summary is not None else None
        ),
        "child_diagnostic": child_diagnostic_summary,
        "artifacts": artifacts,
        "rerun_rule": "retain this attempt and rerun the complete five-seed, three-policy matrix under a new attempt ID",
    }
    _write_json(Path(output_dir) / "attempt-failure.json", failure)


def _git_state() -> Dict[str, Any]:
    def command(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=str(PROJECT_ROOT), text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        commit = command("rev-parse", "HEAD")
        status = command("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError):
        return {"head_commit": None, "worktree_dirty": None, "status": None}
    return {
        "head_commit": commit,
        "worktree_dirty": bool(status),
        "status": status,
    }


def _assert_source_snapshot(
    expected: Mapping[str, Any], expected_head: Optional[str]
) -> None:
    observed = _source_identities()
    if observed != dict(expected):
        raise RuntimeError("Gate 7 source files changed during the benchmark attempt")
    if expected_head is not None:
        git_state = _git_state()
        if (
            git_state.get("head_commit") != expected_head
            or git_state.get("worktree_dirty") is not False
        ):
            raise RuntimeError(
                "formal Gate 7 Git state changed during the benchmark attempt"
            )


def _validate_sources_are_tracked_at_head(
    head_commit: str,
    source_identities: Mapping[str, Any],
    contract_path: Path,
) -> None:
    identities = dict(source_identities)
    identities[_project_path(contract_path)] = _artifact_identity(contract_path)
    for relative, identity in identities.items():
        if not isinstance(identity, dict):
            raise RuntimeError("formal source identity is missing: %s" % relative)
        try:
            blob = subprocess.check_output(
                ["git", "show", "%s:%s" % (head_commit, relative)],
                cwd=str(PROJECT_ROOT),
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                "formal source is not tracked at the recorded HEAD: %s" % relative
            ) from exc
        observed = {
            "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob),
        }
        if observed != {
            "sha256": identity.get("sha256"),
            "bytes": identity.get("bytes"),
        }:
            raise RuntimeError(
                "formal source bytes differ from recorded HEAD: %s" % relative
            )


def _environment() -> Dict[str, Any]:
    import cachetools
    import faiss
    import sqlalchemy

    try:
        import onnxruntime
    except ModuleNotFoundError:
        # The normal project/CI environment intentionally omits the optional
        # benchmark stack. Fake-embedding smoke tests must still exercise the
        # complete orchestration path there; formal real-ONNX mode fails much
        # earlier while resolving its pinned model/runtime assets.
        onnxruntime = None
    try:
        import transformers
    except ModuleNotFoundError:
        transformers = None

    try:
        load_average: Optional[Tuple[float, float, float]] = tuple(os.getloadavg())
    except (AttributeError, OSError):
        load_average = None
    filesystem: Dict[str, Any] = {
        "available": False,
        "device_sha256": None,
        "mountpoint": None,
        "fstype": None,
        "options": None,
    }
    try:
        root = str(PROJECT_ROOT.resolve())
        matches = [
            partition
            for partition in psutil.disk_partitions(all=True)
            if root == partition.mountpoint
            or root.startswith(partition.mountpoint.rstrip(os.sep) + os.sep)
        ]
        if matches:
            partition = max(matches, key=lambda item: len(item.mountpoint))
            filesystem = {
                "available": True,
                "device_sha256": hashlib.sha256(
                    partition.device.encode("utf-8")
                ).hexdigest(),
                "mountpoint": partition.mountpoint,
                "fstype": partition.fstype,
                "options": partition.opts,
            }
    except (OSError, psutil.Error):
        pass
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, OSError, psutil.Error):
        battery = None
    power = {
        "available": battery is not None,
        "percent": float(battery.percent) if battery is not None else None,
        "plugged": bool(battery.power_plugged) if battery is not None else None,
        "seconds_left": (
            int(battery.secsleft)
            if battery is not None and battery.secsleft is not None
            else None
        ),
    }
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "hostname_sha256": hashlib.sha256(
            platform.node().encode("utf-8")
        ).hexdigest(),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "load_average_at_manifest": load_average,
        "filesystem": filesystem,
        "power": power,
        "total_memory_bytes": int(psutil.virtual_memory().total),
        "numpy": np.__version__,
        "faiss": getattr(faiss, "__version__", "unknown"),
        "sqlalchemy": sqlalchemy.__version__,
        "cachetools": cachetools.__version__,
        "psutil": psutil.__version__,
        "onnxruntime": (
            onnxruntime.__version__ if onnxruntime is not None else None
        ),
        "onnxruntime_available": onnxruntime is not None,
        "transformers": (
            transformers.__version__ if transformers is not None else None
        ),
        "transformers_available": transformers is not None,
    }


def policy_schedule(seeds: Sequence[int]) -> Dict[int, Tuple[str, ...]]:
    """Return a deterministic near-balanced schedule independent of input order."""

    unique = sorted(set(int(seed) for seed in seeds))
    if len(unique) != len(seeds):
        raise ValueError("seeds must be unique")
    base = tuple(
        sorted(
            POLICIES,
            key=lambda policy: hashlib.sha256(
                (POLICY_ORDER_NAMESPACE + "|" + policy).encode("utf-8")
            ).digest(),
        )
    )
    patterns = (
        base,
        base[1:] + base[:1],
        base[2:] + base[:2],
        tuple(reversed(base)),
        tuple(reversed(base[1:] + base[:1])),
        tuple(reversed(base[2:] + base[:2])),
    )
    return {seed: patterns[index % len(patterns)] for index, seed in enumerate(unique)}


def _percentile_ns(values: Sequence[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(int(value) for value in values)
    index = max(0, int(math.ceil(quantile * len(ordered))) - 1)
    return ordered[index]


def _latency_summary(values: Sequence[int], name: str) -> Dict[str, float]:
    if not values:
        raise ValueError("cannot summarize an empty latency sequence")
    return {
        "%s_mean_us" % name: round(sum(values) / len(values) / 1000.0, 6),
        "%s_p50_us" % name: round(_percentile_ns(values, 0.50) / 1000.0, 6),
        "%s_p95_us" % name: round(_percentile_ns(values, 0.95) / 1000.0, 6),
        "%s_p99_us" % name: round(_percentile_ns(values, 0.99) / 1000.0, 6),
    }


def _outcome_name(row: Mapping[str, Any]) -> str:
    if row.get("raw_hit") is True:
        return "hit"
    if row.get("rejected") is True:
        return "rejected"
    if row.get("admitted") is True and row.get("evicted") is True:
        return "admitted_with_eviction"
    if row.get("admitted") is True and row.get("evicted") is False:
        return "admitted_without_eviction"
    raise RuntimeError("request has no frozen outcome classification")


def _outcome_latency_records(
    request_rows: Sequence[Mapping[str, Any]],
    run_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Summarize every named stage for the whole run and four outcomes."""

    grouped: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in request_rows:
        grouped[str(row["run_id"])].append(row)
    records: List[Dict[str, Any]] = []
    for run in run_rows:
        run_id = str(run["run_id"])
        by_outcome: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for row in grouped.get(run_id, []):
            by_outcome[_outcome_name(row)].append(row)
        if sum(len(values) for values in by_outcome.values()) != int(run["requests"]):
            raise RuntimeError("outcome summaries do not partition the request run")
        all_rows = grouped.get(run_id, [])
        for outcome in OUTCOME_REPORT_NAMES:
            rows = all_rows if outcome == "all" else by_outcome.get(outcome, [])
            latency: Dict[str, Any] = {}
            for stage, field in OUTCOME_LATENCY_FIELDS.items():
                values = [int(row[field]) for row in rows]
                latency[stage] = (
                    {
                        "mean_ns": sum(values) / len(values),
                        "p50_ns": _percentile_ns(values, 0.50),
                        "p95_ns": _percentile_ns(values, 0.95),
                        "p99_ns": _percentile_ns(values, 0.99),
                    }
                    if values
                    else {
                        "mean_ns": None,
                        "p50_ns": None,
                        "p95_ns": None,
                        "p99_ns": None,
                    }
                )
            records.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "experiment_id": EXPERIMENT_ID,
                    "attempt_id": run["attempt_id"],
                    "run_id": run_id,
                    "seed": int(run["seed"]),
                    "policy": run["policy"],
                    "outcome": outcome,
                    "samples": len(rows),
                    "latency": latency,
                }
            )
    return records


def _timer_overhead_ns(samples: int = 10000) -> Dict[str, int]:
    values = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        values.append(time.perf_counter_ns() - started)
    return {
        "samples": samples,
        "median_ns": _percentile_ns(values, 0.50),
        "p95_ns": _percentile_ns(values, 0.95),
    }


class ExclusiveStageRecorder:
    """Record exclusive nested storage/policy time without double counting."""

    def __init__(self) -> None:
        self.active = False
        self.current: DefaultDict[str, int] = defaultdict(int)
        self.stack: List[Dict[str, Any]] = []
        self.last_policy_result: Any = None

    def begin(self) -> None:
        if self.active:
            raise RuntimeError("stage recorder is already active")
        self.active = True
        self.current = defaultdict(int)
        self.stack = []
        self.last_policy_result = None

    def finish(self) -> Dict[str, int]:
        if self.stack:
            raise RuntimeError("stage recorder finished with active nested spans")
        self.active = False
        result = {stage: int(self.current.get(stage, 0)) for stage in EXCLUSIVE_STAGES}
        result["policy_inclusive"] = int(self.current.get("policy_inclusive", 0))
        return result

    def record(self, category: str, elapsed_ns: int) -> None:
        """Charge an already measured leaf operation to the active request."""

        if not self.active:
            return
        elapsed = int(elapsed_ns)
        if elapsed < 0:
            raise ValueError("stage duration cannot be negative")
        self.current[category] += elapsed

    def wrap(
        self,
        obj: Any,
        method_name: str,
        category: str,
        capture_policy_result: bool = False,
    ) -> None:
        original = getattr(obj, method_name, None)
        if not callable(original):
            return

        @functools.wraps(original)
        def measured(*args: Any, **kwargs: Any) -> Any:
            if not self.active:
                return original(*args, **kwargs)
            has_policy_ancestor = any(
                frame["category"] == "policy_exclusive" for frame in self.stack
            )
            frame = {
                "category": category,
                "started_ns": time.perf_counter_ns(),
                "child_ns": 0,
            }
            self.stack.append(frame)
            result: Any = None
            try:
                result = original(*args, **kwargs)
                return result
            finally:
                elapsed = time.perf_counter_ns() - int(frame["started_ns"])
                popped = self.stack.pop()
                if popped is not frame:
                    raise RuntimeError("stage recorder stack corruption")
                exclusive = max(0, elapsed - int(frame["child_ns"]))
                self.current[category] += exclusive
                if category == "policy_exclusive" and not has_policy_ancestor:
                    self.current["policy_inclusive"] += elapsed
                    if capture_policy_result:
                        self.last_policy_result = result
                if self.stack:
                    self.stack[-1]["child_ns"] += elapsed

        setattr(obj, method_name, measured)


class _TimedSimilarity(float):
    """Float whose adapter threshold comparison is charged to the decision stage."""

    def __new__(cls, value: float, recorder: ExclusiveStageRecorder) -> "_TimedSimilarity":
        instance = float.__new__(cls, value)
        instance.recorder = recorder
        return instance

    def __ge__(self, other: Any) -> bool:
        started = time.perf_counter_ns()
        result = bool(float(self) >= float(other))
        self.recorder.record(
            "similarity_evaluation", time.perf_counter_ns() - started
        )
        return result


class CosineDistanceEvaluation:
    """Convert normalized FAISS squared L2 distance back to cosine similarity."""

    def __init__(self, recorder: ExclusiveStageRecorder) -> None:
        self.recorder = recorder
        self.last_similarity: Optional[float] = None

    def evaluation(self, _query: Dict[str, Any], cached: Dict[str, Any], **_: Any) -> float:
        started = time.perf_counter_ns()
        distance = float(cached["search_result"][0])
        similarity = max(0.0, min(1.0, 1.0 - 0.5 * distance))
        self.last_similarity = similarity
        self.recorder.record(
            "similarity_evaluation", time.perf_counter_ns() - started
        )
        return _TimedSimilarity(similarity, self.recorder)

    @staticmethod
    def range() -> Tuple[float, float]:
        return 0.0, 1.0


def _instrument_manager(manager: Any, recorder: ExclusiveStageRecorder) -> None:
    for method_name in ("get_data_by_id", "peek_data_by_id", "get_ids", "count"):
        recorder.wrap(manager.s, method_name, "sqlite_read")
    for method_name in ("batch_insert", "mark_deleted", "clear_deleted_data", "flush"):
        recorder.wrap(manager.s, method_name, "sqlite_write")
    recorder.wrap(manager.v, "search", "faiss_search")
    for method_name in ("mul_add", "delete", "rebuild", "flush"):
        recorder.wrap(manager.v, method_name, "faiss_mutation")
    recorder.wrap(
        manager.eviction_base,
        "put_with_metadata",
        "policy_exclusive",
        capture_policy_result=True,
    )
    recorder.wrap(
        manager.eviction_base,
        "put",
        "policy_exclusive",
        capture_policy_result=True,
    )
    recorder.wrap(manager.eviction_base, "get", "policy_exclusive")


def _normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).strip().split())


class PinnedOnnxEmbedder:
    """Offline loader for the exact tokenizer/model revisions frozen by QQP."""

    def __init__(
        self,
        tokenizer_path: Path,
        model_path: Path,
        intra_threads: int,
        inter_threads: int,
        max_length: int,
    ) -> None:
        import onnxruntime
        from transformers import AutoConfig, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            str(tokenizer_path), local_files_only=True
        )
        config = AutoConfig.from_pretrained(str(tokenizer_path), local_files_only=True)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = intra_threads
        options.inter_op_num_threads = inter_threads
        options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        self.session = onnxruntime.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.dimension = int(config.hidden_size)
        self.max_length = max_length
        self.providers = list(self.session.get_providers())
        input_shapes = {item.name: tuple(item.shape) for item in self.session.get_inputs()}
        sequence_lengths = {
            int(shape[1])
            for shape in input_shapes.values()
            if len(shape) > 1 and isinstance(shape[1], int)
        }
        if len(sequence_lengths) > 1:
            raise RuntimeError("pinned ONNX inputs disagree on sequence length")
        if sequence_lengths and next(iter(sequence_lengths)) != max_length:
            raise ValueError("pinned ONNX model requires a different max length")

    def embed(self, text: str) -> Tuple[np.ndarray, Dict[str, int]]:
        tokenize_start = time.perf_counter_ns()
        encoded = self.tokenizer(
            text,
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
            return_tensors="np",
        )
        input_ids = np.asarray(encoded["input_ids"], dtype=np.int64)
        attention_mask = np.asarray(encoded["attention_mask"], dtype=np.int64)
        inputs = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": np.asarray(
                encoded.get("token_type_ids", np.zeros_like(input_ids)), dtype=np.int64
            ),
        }
        tokenize_ns = time.perf_counter_ns() - tokenize_start
        inference_start = time.perf_counter_ns()
        token_embeddings = self.session.run(None, inputs)[0]
        inference_ns = time.perf_counter_ns() - inference_start
        post_start = time.perf_counter_ns()
        mask = np.expand_dims(attention_mask, -1).astype(np.float32)
        pooled = np.sum(token_embeddings * mask, axis=1) / np.maximum(
            np.sum(mask, axis=1), 1e-9
        )
        vector = pooled[0]
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 0:
            raise RuntimeError("ONNX model produced an invalid embedding")
        normalized = (vector / norm).astype(np.float32)
        if normalized.shape != (self.dimension,):
            raise RuntimeError("ONNX model produced an unexpected embedding shape")
        if not np.all(np.isfinite(normalized)):
            raise RuntimeError("ONNX model produced a non-finite embedding")
        normalized_norm = float(np.linalg.norm(normalized))
        if not math.isclose(normalized_norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise RuntimeError("ONNX model produced a non-unit embedding")
        post_ns = time.perf_counter_ns() - post_start
        return normalized, {
            "tokenize": tokenize_ns,
            "onnx_inference": inference_ns,
            "embedding_postprocess": post_ns,
        }


class DeterministicFakeEmbedder:
    """Bounded smoke-only embedder; formal mode rejects this implementation."""

    dimension = 32
    providers = ["deterministic-fake"]

    def embed(self, text: str) -> Tuple[np.ndarray, Dict[str, int]]:
        started = time.perf_counter_ns()
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        raw = np.frombuffer(seed, dtype=np.uint8).astype(np.float32) - 127.5
        vector = raw / np.linalg.norm(raw)
        post_ns = time.perf_counter_ns() - started
        return vector, {
            "tokenize": 0,
            "onnx_inference": 0,
            "embedding_postprocess": post_ns,
        }


def _asset_map_digest(files: Mapping[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(files), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _tokenizer_file_map(tokenizer_path: Path) -> Dict[str, str]:
    root = Path(tokenizer_path)
    if not root.is_dir():
        raise RuntimeError("pinned tokenizer snapshot directory is missing")
    files: Dict[str, str] = {}
    for item in sorted(root.rglob("*")):
        if item.is_file():
            relative = item.relative_to(root).as_posix()
            files[relative] = sha256_file(item)
    return files


def _verify_frozen_onnx_assets(
    model_path: Path,
    tokenizer_path: Path,
    expected_model_files: Mapping[str, str],
    expected_model_digest_sha256: str,
    expected_tokenizer_files: Mapping[str, str],
    expected_tokenizer_digest_sha256: str,
    stage: str,
) -> Dict[str, Any]:
    """Hash the complete used asset set and enforce the frozen exact maps."""

    expected_model = dict(expected_model_files)
    expected_tokenizer = dict(expected_tokenizer_files)
    if (
        expected_model != PINNED_MODEL_FILES
        or expected_model_digest_sha256 != PINNED_MODEL_DIGEST_SHA256
        or expected_tokenizer != PINNED_TOKENIZER_FILES
        or expected_tokenizer_digest_sha256 != PINNED_TOKENIZER_DIGEST_SHA256
    ):
        raise RuntimeError(
            "%s expected ONNX asset identities differ from the frozen contract"
            % stage
        )
    model = Path(model_path)
    if not model.is_file():
        raise RuntimeError("%s pinned ONNX model file is missing" % stage)
    observed_model = {"model.onnx": sha256_file(model)}
    observed_tokenizer = _tokenizer_file_map(Path(tokenizer_path))
    observed_model_digest = _asset_map_digest(observed_model)
    observed_tokenizer_digest = _asset_map_digest(observed_tokenizer)
    if observed_model != expected_model:
        raise RuntimeError(
            "%s ONNX model exact file map differs from the frozen contract" % stage
        )
    if observed_model_digest != expected_model_digest_sha256:
        raise RuntimeError(
            "%s ONNX model aggregate digest differs from the frozen contract"
            % stage
        )
    if observed_tokenizer != expected_tokenizer:
        raise RuntimeError(
            "%s tokenizer exact file map differs from the frozen contract" % stage
        )
    if observed_tokenizer_digest != expected_tokenizer_digest_sha256:
        raise RuntimeError(
            "%s tokenizer aggregate digest differs from the frozen contract" % stage
        )
    return {
        "model_files": observed_model,
        "model_digest_sha256": observed_model_digest,
        "tokenizer_files": observed_tokenizer,
        "tokenizer_digest_sha256": observed_tokenizer_digest,
    }


def _resolve_onnx_assets() -> Dict[str, Any]:
    from huggingface_hub import hf_hub_download, snapshot_download

    model_path = Path(
        hf_hub_download(
            repo_id=MODEL_REPOSITORY,
            filename="model.onnx",
            revision=MODEL_REVISION,
        )
    ).resolve()
    tokenizer_path = Path(
        snapshot_download(
            repo_id=TOKENIZER_REPOSITORY,
            revision=TOKENIZER_REVISION,
            allow_patterns=("*.json", "*.txt", "tokenizer.*", "*.model"),
        )
    ).resolve()
    identity = _verify_frozen_onnx_assets(
        model_path,
        tokenizer_path,
        PINNED_MODEL_FILES,
        PINNED_MODEL_DIGEST_SHA256,
        PINNED_TOKENIZER_FILES,
        PINNED_TOKENIZER_DIGEST_SHA256,
        "parent preflight",
    )
    return {
        "kind": "pinned_onnx",
        "model_repository": MODEL_REPOSITORY,
        "model_revision": MODEL_REVISION,
        "model_path": str(model_path),
        "model_sha256": identity["model_files"]["model.onnx"],
        "model_files": identity["model_files"],
        "model_digest_sha256": identity["model_digest_sha256"],
        "tokenizer_repository": TOKENIZER_REPOSITORY,
        "tokenizer_revision": TOKENIZER_REVISION,
        "tokenizer_path": str(tokenizer_path),
        "tokenizer_files": identity["tokenizer_files"],
        "tokenizer_digest_sha256": identity["tokenizer_digest_sha256"],
        "provider": "CPUExecutionProvider",
    }


def _fake_assets() -> Dict[str, Any]:
    return {
        "kind": "deterministic_fake_smoke_only",
        "model_repository": None,
        "model_revision": None,
        "model_path": None,
        "model_sha256": hashlib.sha256(b"gate7-deterministic-fake-v1").hexdigest(),
        "model_files": {},
        "model_digest_sha256": None,
        "tokenizer_repository": None,
        "tokenizer_revision": None,
        "tokenizer_path": None,
        "tokenizer_files": {},
        "tokenizer_digest_sha256": None,
        "provider": "deterministic-fake",
    }


def _integration_config(config: Gate7Config, seed: int) -> IntegrationConfig:
    return IntegrationConfig(
        mode="full" if config.mode == "full" else "smoke",
        workload="pollution_scan",
        seed=seed,
        requests=config.requests,
        capacity=config.capacity,
        hit_threshold=config.hit_threshold,
        sample_every=max(1, config.requests),
        topic_threshold=config.topic_threshold,
        cell_threshold=config.cell_threshold,
        demand_half_life=config.demand_half_life,
        quota_strength=config.quota_strength,
        ghost_support_threshold=config.ghost_support_threshold,
        admission_margin=config.admission_margin,
        centroid_alpha=config.centroid_alpha,
        entry_hit_weight=config.entry_hit_weight,
    )


def _run_id(
    policy: str,
    seed: int,
    trace_hash: str,
    config: Gate7Config,
    model_sha256: str,
    attempt_id: str,
) -> str:
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy": policy,
        "seed": seed,
        "trace_hash": trace_hash,
        "config": asdict(config),
        "model_sha256": model_sha256,
        "attempt_id": attempt_id,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _load_trace(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            required = {
                "index",
                "request_id",
                "phase",
                "occurrence",
                "reuse_opportunity",
                "text_id",
                "text",
                "concept_id",
                "response_id",
                "response_payload",
            }
            if set(row) != required:
                raise ValueError("trace row %d has unexpected fields" % line_number)
            if int(row["index"]) != len(rows):
                raise ValueError("trace indices must be contiguous")
            rows.append(row)
    if not rows:
        raise ValueError("trace is empty")
    return rows


def _trace_digest(rows: Sequence[Dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _answer_payload(cache_data: Any) -> Optional[str]:
    if cache_data is None or not cache_data.answers:
        return None
    return str(cache_data.answers[0].answer)


def _materialize_response(payload: str) -> Dict[str, Any]:
    prefix = "recorded-response-v2:"
    if not isinstance(payload, str) or not payload.startswith(prefix):
        raise ValueError("recorded response payload has an unexpected schema")
    identity = payload[len(prefix) :]
    parts = identity.split(":")
    if len(parts) != 2 or not all(parts):
        raise ValueError("recorded response payload has an invalid v2 identity")
    concept_id, source_text_id = parts
    return {
        "concept_id": concept_id,
        "response_id": concept_id,
        "source_text_id": source_text_id,
        "text": payload,
    }


def _cache_question_text(cache_data: Any) -> Optional[str]:
    """Extract the stored scalar question without consulting its answer."""

    if cache_data is None:
        return None
    question = getattr(cache_data, "question", None)
    if isinstance(question, str):
        return question
    content = getattr(question, "content", None)
    return content if isinstance(content, str) else None


def _trace_identity_maps(
    trace: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, str], Dict[str, str], Set[str]]:
    """Build exact normalized-text provenance maps for one frozen trace."""

    text_id_by_normalized: Dict[str, str] = {}
    concept_by_text_id: Dict[str, str] = {}
    known_responses: Set[str] = set()
    for request in trace:
        normalized = _normalize_text(str(request["text"]))
        text_id = str(request["text_id"])
        concept_id = str(request["concept_id"])
        previous_text_id = text_id_by_normalized.setdefault(normalized, text_id)
        previous_concept = concept_by_text_id.setdefault(text_id, concept_id)
        if previous_text_id != text_id or previous_concept != concept_id:
            raise RuntimeError("trace text provenance is not one-to-one")
        known_responses.add(str(request["response_id"]))
    return text_id_by_normalized, concept_by_text_id, known_responses


def _semantic_guardrail_status(counters: Mapping[str, int]) -> str:
    labeled = int(counters.get("direct_negative_hits", 0)) + int(
        counters.get("component_derived_negative_hits", 0)
    )
    if labeled:
        return "FAIL"
    if int(counters.get("unlabeled_cross_concept_hits", 0)):
        return "PENDING_INDETERMINATE"
    if int(counters.get("hits", 0)) == 0:
        return "NO_HITS"
    return "PASS_OBSERVED"


def _classify_request_semantics(
    classifier: Callable[..., Any],
    semantic_index: Any,
    raw_hit: bool,
    provenance_resolved: bool,
    provenance_consistent: bool,
    query_text_id: str,
    query_concept_id: str,
    cached_text_id: Optional[str],
    cached_concept_id: Optional[str],
) -> Dict[str, Any]:
    """Classify only hits whose three independent provenance legs agree."""

    not_applicable = {
        "semantic_relation": "not_applicable",
        "semantic_status": "not_applicable",
        "semantic_label": None,
        "semantic_evidence_kind": "none",
        "semantic_source_indices": (),
    }
    if not raw_hit:
        return {**not_applicable, "hit_class": "miss"}
    if not (provenance_resolved and provenance_consistent):
        return {**not_applicable, "hit_class": "unresolved"}

    semantic = classifier(
        semantic_index,
        True,
        query_text_id,
        query_concept_id,
        str(cached_text_id),
        str(cached_concept_id),
    )
    relation = str(semantic.relation)
    hit_class = {
        "positive_same_component": "same_concept",
        "negative_direct": "direct_negative",
        "negative_component_derived": "component_derived_negative",
        "unlabeled_cross_component": "unlabeled_cross_concept",
    }.get(relation)
    if hit_class is None:
        raise RuntimeError("semantic classifier returned an invalid hit relation")
    return {
        "semantic_relation": relation,
        "semantic_status": str(semantic.status),
        "semantic_label": semantic.label,
        "semantic_evidence_kind": str(semantic.evidence_kind),
        "semantic_source_indices": tuple(semantic.source_indices),
        "hit_class": hit_class,
    }


def _structural_summary(
    requests: Sequence[Mapping[str, Any]],
    storage_failure: Optional[str] = None,
) -> Dict[str, Any]:
    reason_counts: DefaultDict[str, int] = defaultdict(int)
    failing_requests = 0
    for request in requests:
        if request.get("structural_failure") is True:
            failing_requests += 1
        reasons = str(request.get("structural_failure_reasons") or "")
        for reason in (value for value in reasons.split("|") if value):
            reason_counts[reason] += 1
    if storage_failure:
        reason_counts["storage_verification"] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "structural_valid": failing_requests == 0 and storage_failure is None,
        "structural_failure_requests": failing_requests,
        "structural_failure_flags_total": sum(reason_counts.values()),
        "structural_failure_reason_counts": dict(sorted(reason_counts.items())),
        "storage_failure": storage_failure,
    }


def _high_water_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform.startswith("linux"):
        return value * 1024
    return value


def _write_control_state(control_dir: Path, state: str, request_index: int) -> None:
    _write_json(
        Path(control_dir) / "state.json",
        {
            "state": state,
            "pid": os.getpid(),
            "request_index": request_index,
            "monotonic_ns": time.perf_counter_ns(),
        },
    )


def _wait_for_ack(path: Path, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not Path(path).is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError("resource supervisor acknowledgement timed out")
        time.sleep(0.005)


def _policy_action(policy: str, raw_hit: bool, recorder: ExclusiveStageRecorder) -> Dict[str, Any]:
    if raw_hit:
        return {"action": "hit", "admitted": False, "rejected": False, "evicted": False}
    if policy != "CARMA":
        return {
            "action": "admit_or_replace",
            "admitted": True,
            "rejected": False,
            "evicted": None,
        }
    result = recorder.last_policy_result
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        return {"action": "unknown", "admitted": None, "rejected": None, "evicted": None}
    outcome = result[0]
    action = str(outcome.get("action", "unknown"))
    admitted = bool(outcome.get("admitted", False))
    victims = outcome.get("evicted_ids") or []
    return {
        "action": action,
        "admitted": admitted,
        "rejected": action.startswith("reject"),
        "evicted": bool(victims and victims != [outcome.get("key")]),
    }


def _cache_size(manager: Any) -> int:
    entries = getattr(manager.eviction_base, "_entries", None)
    if isinstance(entries, dict):
        return len(entries)
    cache = getattr(manager.eviction_base, "_cache", None)
    if cache is not None:
        return len(cache)
    raise RuntimeError("eviction policy does not expose an in-memory size")


def _request_record_template(
    request: Mapping[str, Any],
    attempt_id: str,
    run_id: str,
    seed: int,
    policy: str,
    order_position: int,
    trace_hash: str,
) -> Dict[str, Any]:
    """Allocate the complete fixed-schema request row before RSS sampling."""

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "seed": seed,
        "policy": policy,
        "order_position": order_position,
        "trace_hash": trace_hash,
        "request_index": int(request["index"]),
        "request_id": request["request_id"],
        "phase": request["phase"],
        "occurrence": int(request["occurrence"]),
        "reuse_opportunity": bool(request["reuse_opportunity"]),
        "text_id": request["text_id"],
        "text_sha256": "0" * 64,
        "concept_id": request["concept_id"],
        "expected_response_id": request["response_id"],
        "returned_response_id": "",
        "returned_concept_id": "",
        "top_candidate_id": None,
        "top_candidate_text_id": None,
        "candidate_cache_data_seen": False,
        "candidate_cache_data_present": False,
        "candidate_cache_lookup_count": 0,
        "cached_question_text_sha256": None,
        "cached_source_text_id": None,
        "cached_source_concept_id": None,
        "cached_answer_sha256": None,
        "cached_answer_raw_identity": None,
        "cached_answer_source_text_id": None,
        "cached_answer_source_concept_id": None,
        "payload_source_text_id": None,
        "payload_source_concept_id": None,
        "provenance_resolved": True,
        "provenance_consistent": True,
        "semantic_relation": "not_applicable",
        "semantic_status": "not_applicable",
        "semantic_label": None,
        "semantic_evidence_kind": "none",
        "semantic_source_indices": (),
        "hit_class": "miss",
        "response_id_mismatch": False,
        "source_response_mismatch": False,
        "unrecognized_response_id": False,
        "stale_candidate": False,
        "capacity_exceeded": False,
        "structural_failure": False,
        "structural_failure_reasons": "",
        "raw_hit": False,
        "similarity": -1.0,
        "policy_action": "unclassified",
        "admitted": None,
        "rejected": None,
        "evicted": None,
        "cache_size_before": 0,
        "cache_size_after": 0,
        "preprocess_ns": 0,
        "tokenize_ns": 0,
        "text_preprocess_tokenize_ns": 0,
        "onnx_inference_ns": 0,
        "embedding_postprocess_ns": 0,
        "embedding_elapsed_ns": 0,
        "embedding_ns": 0,
        "faiss_search_ns": 0,
        "faiss_mutation_ns": 0,
        "faiss_ns": 0,
        "sqlite_read_ns": 0,
        "sqlite_write_ns": 0,
        "sqlite_ns": 0,
        "similarity_evaluation_ns": 0,
        "similarity_decision_ns": 0,
        "policy_exclusive_ns": 0,
        "policy_inclusive_ns": 0,
        "response_materialization_ns": 0,
        "response_propagation_ns": 0,
        "response_return_ns": 0,
        "cache_management_ns": 0,
        "post_embedding_elapsed_ns": 0,
        "post_embedding_ns": 0,
        "post_embedding_total_ns": 0,
        "residual_ns": 0,
        "end_to_end_ns": 0,
        "request_total_ns": 0,
        "exclusive_reconciles": False,
    }


def _trace_row_value(row: Any, field: str) -> Any:
    return row[field] if isinstance(row, Mapping) else getattr(row, field)


def _build_warmup_artifact(
    trace: Sequence[Any],
    ordered_hot_text_ids: Sequence[str],
    seed: int,
    trace_sha256: str,
    warmup_requests: int,
) -> Dict[str, Any]:
    text_by_id = {
        str(_trace_row_value(row, "text_id")): str(_trace_row_value(row, "text"))
        for row in trace
    }
    ordered_hot = [str(value) for value in ordered_hot_text_ids]
    if (
        not ordered_hot
        or len(set(ordered_hot)) != len(ordered_hot)
        or any(text_id not in text_by_id for text_id in ordered_hot)
    ):
        raise RuntimeError("canonical warm-up hot text IDs are invalid")
    warmup_ids = [
        ordered_hot[index % len(ordered_hot)] for index in range(warmup_requests)
    ]
    return {
        "schema_version": WARMUP_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "seed": int(seed),
        "trace_sha256": trace_sha256,
        "warmup_requests": int(warmup_requests),
        "ordered_hot_text_ids": ordered_hot,
        "warmup_text_ids": warmup_ids,
        "warmup_texts": [text_by_id[text_id] for text_id in warmup_ids],
    }


def _load_and_verify_warmup_artifact(
    path: Path,
    expected_sha256: str,
    trace: Sequence[Mapping[str, Any]],
    seed: int,
    expected_trace_sha256: str,
    warmup_requests: int,
) -> Tuple[Dict[str, Any], List[str]]:
    if sha256_file(path) != expected_sha256:
        raise RuntimeError("child warm-up artifact hash differs from orchestrator")
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("child warm-up artifact is unreadable") from exc
    expected_keys = {
        "schema_version",
        "experiment_id",
        "seed",
        "trace_sha256",
        "warmup_requests",
        "ordered_hot_text_ids",
        "warmup_text_ids",
        "warmup_texts",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schema_version") != WARMUP_SCHEMA_VERSION
        or value.get("experiment_id") != EXPERIMENT_ID
        or value.get("seed") != seed
        or value.get("trace_sha256") != expected_trace_sha256
        or value.get("warmup_requests") != warmup_requests
        or not isinstance(value.get("ordered_hot_text_ids"), list)
        or not isinstance(value.get("warmup_text_ids"), list)
        or not isinstance(value.get("warmup_texts"), list)
    ):
        raise RuntimeError("child warm-up artifact metadata is malformed")
    ordered_hot = value["ordered_hot_text_ids"]
    warmup_ids = value["warmup_text_ids"]
    warmup_texts = value["warmup_texts"]
    text_by_id = {str(row["text_id"]): str(row["text"]) for row in trace}
    expected_ids = [
        ordered_hot[index % len(ordered_hot)] for index in range(warmup_requests)
    ] if ordered_hot else []
    if (
        len(set(ordered_hot)) != len(ordered_hot)
        or any(not isinstance(text_id, str) or text_id not in text_by_id for text_id in ordered_hot)
        or warmup_ids != expected_ids
        or len(warmup_texts) != warmup_requests
        or warmup_texts != [text_by_id[text_id] for text_id in warmup_ids]
    ):
        raise RuntimeError("child warm-up artifact content differs from the trace")
    return value, list(warmup_texts)


def _execute_child(
    policy: str,
    seed: int,
    order_position: int,
    attempt_id: str,
    trace_path: Path,
    warmup_path: Path,
    control_dir: Path,
    expected_trace_hash: str,
    semantic_index_path: Path,
    expected_semantic_index_hash: str,
    config: Gate7Config,
    assets: Dict[str, Any],
    expected_warmup_hash: str,
    expected_dependency_attestation_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    from benchmarks.carma.gate7_v2_trace import (
        classify_semantic_relation,
        load_semantic_index_artifact,
    )

    child_bootstrap = (
        _bootstrap_attestation("child")
        if getattr(sys, "_gate7_v2_bootstrap_attestation", None) is not None
        else None
    )
    trace = _load_trace(trace_path)
    observed_trace_hash = _trace_digest(trace)
    if observed_trace_hash != expected_trace_hash:
        raise RuntimeError("child trace hash differs from orchestrator")
    if len(trace) != config.requests:
        raise RuntimeError("child trace request count differs from configuration")
    if sha256_file(Path(semantic_index_path)) != expected_semantic_index_hash:
        raise RuntimeError("child semantic-index hash differs from orchestrator")
    try:
        semantic_index_value = json.loads(
            Path(semantic_index_path).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("child semantic-index artifact is unreadable") from exc
    semantic_index = load_semantic_index_artifact(
        semantic_index_value,
        expected_seed=seed,
        expected_trace_sha256=expected_trace_hash,
    )
    (
        text_id_by_normalized,
        concept_by_text_id,
        known_response_ids,
    ) = _trace_identity_maps(trace)
    for request in trace:
        expected_payload = _materialize_response(str(request["response_payload"]))
        if (
            expected_payload["concept_id"] != request["concept_id"]
            or expected_payload["response_id"] != request["response_id"]
            or expected_payload["source_text_id"] != request["text_id"]
        ):
            raise RuntimeError("trace response payload identity is inconsistent")
    warmup_artifact, warmup_texts = _load_and_verify_warmup_artifact(
        Path(warmup_path),
        expected_warmup_hash,
        trace,
        seed,
        expected_trace_hash,
        config.warmup_requests,
    )
    dependency_attestation_pre: Optional[Dict[str, Any]] = None
    dependency_imported_origins_pre: Optional[Dict[str, Any]] = None
    if config.mode == "full":
        if not isinstance(expected_dependency_attestation_sha256, str):
            raise RuntimeError("formal child lacks dependency attestation identity")
        dependency_attestation_pre = _dependency_attestation()
        if (
            dependency_attestation_pre.get("attestation_sha256")
            != expected_dependency_attestation_sha256
        ):
            raise RuntimeError("formal child dependency attestation differs from parent")
        dependency_imported_origins_pre = dict(
            dependency_attestation_pre["installed"]["imported_origins"]
        )
    asset_identity_pre: Optional[Dict[str, Any]] = None
    if not config.fake_embedding:
        asset_identity_pre = _verify_frozen_onnx_assets(
            Path(assets["model_path"]),
            Path(assets["tokenizer_path"]),
            assets.get("model_files", {}),
            str(assets.get("model_digest_sha256")),
            assets.get("tokenizer_files", {}),
            str(assets.get("tokenizer_digest_sha256")),
            "child pre-load",
        )
        if assets.get("model_sha256") != asset_identity_pre["model_files"].get(
            "model.onnx"
        ):
            raise RuntimeError(
                "child model scalar hash differs from the frozen exact file map"
            )
    run_id = _run_id(
        policy,
        seed,
        expected_trace_hash,
        config,
        assets["model_sha256"],
        attempt_id,
    )
    bridge = _integration_config(config, seed)
    if config.fake_embedding:
        embedder: Any = DeterministicFakeEmbedder()
    else:
        embedder = PinnedOnnxEmbedder(
            Path(assets["tokenizer_path"]),
            Path(assets["model_path"]),
            config.onnx_intra_threads,
            config.onnx_inter_threads,
            config.max_length,
        )
        if list(embedder.providers) != ["CPUExecutionProvider"]:
            raise RuntimeError(
                "formal ONNX provider set differs from the pinned CPU-only provider"
            )
    for warmup_text in warmup_texts:
        warm_vector, _ = embedder.embed(warmup_text)
        if warm_vector.size != embedder.dimension:
            raise RuntimeError("warm-up embedding dimension mismatch")

    # Force the full embedding-evidence allocation resident and allocate every
    # request dictionary/key before the supervisor's mandatory start sample.
    # Only scalar values and existing string references change in the loop.
    embedding_buffer = np.full(
        (len(trace), int(embedder.dimension)), np.nan, dtype="<f4"
    )
    request_buffer: List[Dict[str, Any]] = [
        _request_record_template(
            request,
            attempt_id,
            run_id,
            seed,
            policy,
            order_position,
            expected_trace_hash,
        )
        for request in trace
    ]
    _CHILD_DIAGNOSTIC_STATE.clear()
    _CHILD_DIAGNOSTIC_STATE.update(
        {
            "attempt_id": attempt_id,
            "run_id": run_id,
            "seed": seed,
            "policy": policy,
            "order_position": order_position,
            "trace_hash": expected_trace_hash,
            "request_buffer": request_buffer,
        }
    )

    process = psutil.Process(os.getpid())
    post_warmup_memory = process.memory_info()
    post_warmup_rss = int(post_warmup_memory.rss)
    post_warmup_uss: Optional[int] = None
    try:
        post_warmup_uss = int(process.memory_full_info().uss)
    except (AttributeError, psutil.Error):
        pass

    control_dir = Path(control_dir)
    control_dir.mkdir(parents=True, exist_ok=True)
    _write_control_state(control_dir, "ready", -1)
    _wait_for_ack(control_dir / "start.ack")
    _write_control_state(control_dir, "running", -1)

    stage_values: Dict[str, List[int]] = {name: [] for name in SUMMARY_STAGES}
    embedding_digest = hashlib.sha256()
    counters = defaultdict(int)
    timer_overhead = _timer_overhead_ns()
    cpu_start = process.cpu_times()
    loop_start_monotonic_ns: Optional[int] = None
    loop_end_monotonic_ns: Optional[int] = None

    with tempfile.TemporaryDirectory(prefix="gate7-%s-" % policy.lower()) as root:
        storage_instance_sha256 = hashlib.sha256(
            str(Path(root).resolve()).encode("utf-8")
        ).hexdigest()
        manager = _build_manager(policy, root, int(embedder.dimension), bridge)
        recorder = ExclusiveStageRecorder()
        _instrument_manager(manager, recorder)

        adapter_state: Dict[str, Any] = {}
        _CHILD_DIAGNOSTIC_STATE["adapter_state"] = adapter_state
        original_search = manager.search
        original_get_scalar_data = manager.get_scalar_data

        @functools.wraps(original_search)
        def captured_search(*args: Any, **kwargs: Any) -> Any:
            result = original_search(*args, **kwargs)
            adapter_state["search_results"] = list(result or [])
            return result

        @functools.wraps(original_get_scalar_data)
        def captured_get_scalar_data(*args: Any, **kwargs: Any) -> Any:
            result = original_get_scalar_data(*args, **kwargs)
            adapter_state["candidate_cache_data_seen"] = True
            adapter_state["candidate_cache_data_present"] = result is not None
            adapter_state["candidate_cache_lookup_count"] = int(
                adapter_state.get("candidate_cache_lookup_count", 0)
            ) + 1
            adapter_state["candidate_cache_question"] = _cache_question_text(result)
            adapter_state["candidate_cache_answer"] = _answer_payload(result)
            if args and isinstance(args[0], (list, tuple)) and len(args[0]) > 1:
                adapter_state["candidate_cache_row_id"] = int(args[0][1])
            return result

        manager.search = captured_search
        manager.get_scalar_data = captured_get_scalar_data
        similarity_evaluation = CosineDistanceEvaluation(recorder)

        def pre_embedding_func(data: Dict[str, Any], **_: Any) -> str:
            started = time.perf_counter_ns()
            normalized = _normalize_text(str(data["prompt"]))
            recorder.record("preprocess", time.perf_counter_ns() - started)
            adapter_state["normalized_text"] = normalized
            return normalized

        def embedding_func(text: str, **_: Any) -> np.ndarray:
            started = time.perf_counter_ns()
            vector, parts = embedder.embed(text)
            adapter_state["embedding_elapsed_ns"] = time.perf_counter_ns() - started
            adapter_state["embedding_parts"] = dict(parts)
            adapter_state["embedding_complete_ns"] = time.perf_counter_ns()
            recorder.record("tokenize", int(parts["tokenize"]))
            recorder.record("onnx_inference", int(parts["onnx_inference"]))
            recorder.record(
                "embedding_postprocess", int(parts["embedding_postprocess"])
            )
            request_index = int(adapter_state["request_index"])
            embedding_buffer[request_index, :] = vector
            return vector

        def local_response_handler(*_: Any, **__: Any) -> str:
            started = time.perf_counter_ns()
            adapter_state["raw_hit"] = False
            payload = str(adapter_state["response_payload"])
            recorder.record(
                "response_materialization", time.perf_counter_ns() - started
            )
            adapter_state["response_materialization_complete_ns"] = (
                time.perf_counter_ns()
            )
            return payload

        def materialize_hit(payload: Any) -> Dict[str, Any]:
            started = time.perf_counter_ns()
            adapter_state["raw_hit"] = True
            try:
                response = _materialize_response(str(payload))
            except (TypeError, ValueError) as error:
                adapter_state["response_parse_error"] = repr(error)
                response = {
                    "concept_id": "",
                    "response_id": "",
                    "source_text_id": "",
                    "text": str(payload),
                }
            recorder.record(
                "response_materialization", time.perf_counter_ns() - started
            )
            adapter_state["response_materialization_complete_ns"] = (
                time.perf_counter_ns()
            )
            return response

        def select_first(messages: Sequence[Any]) -> Any:
            started = time.perf_counter_ns()
            if not messages:
                raise RuntimeError("GPTCache returned an empty hit answer set")
            response = messages[0]
            recorder.record(
                "response_materialization", time.perf_counter_ns() - started
            )
            adapter_state["response_materialization_complete_ns"] = (
                time.perf_counter_ns()
            )
            return response

        def save_and_materialize(
            payload: Any,
            update_cache_func: Callable[..., Any],
            *_: Any,
            **__: Any,
        ) -> Dict[str, Any]:
            try:
                update_cache_func(str(payload))
                adapter_state["save_completed"] = True
            except Exception as error:
                adapter_state["update_error"] = repr(error)
                raise
            started = time.perf_counter_ns()
            response = _materialize_response(str(payload))
            recorder.record(
                "response_materialization", time.perf_counter_ns() - started
            )
            adapter_state["response_materialization_complete_ns"] = (
                time.perf_counter_ns()
            )
            return response

        chat_cache = Cache()
        chat_cache.init(
            pre_embedding_func=pre_embedding_func,
            embedding_func=embedding_func,
            data_manager=manager,
            similarity_evaluation=similarity_evaluation,
            post_process_messages_func=select_first,
            config=Config(
                similarity_threshold=config.hit_threshold,
                auto_flush=20,
                enable_token_counter=False,
                data_check=False,
                disable_report=True,
            ),
        )

        for request in trace:
            cache_size_before = _cache_size(manager)
            adapter_state.clear()
            adapter_state.update(
                {
                    "request_index": int(request["index"]),
                    "response_payload": request["response_payload"],
                    "search_results": [],
                    "candidate_cache_data_seen": False,
                    "candidate_cache_data_present": False,
                    "candidate_cache_lookup_count": 0,
                    "candidate_cache_question": None,
                    "candidate_cache_answer": None,
                    "candidate_cache_row_id": None,
                }
            )
            similarity_evaluation.last_similarity = None
            recorder.begin()
            total_start = time.perf_counter_ns()
            if loop_start_monotonic_ns is None:
                loop_start_monotonic_ns = total_start
            response = adapt(
                local_response_handler,
                materialize_hit,
                save_and_materialize,
                prompt=request["text"],
                cache_obj=chat_cache,
                top_k=1,
                temperature=0.0,
            )
            # The service boundary is the adapter return.  All provenance,
            # semantic classification, and evidence checks below are required
            # for acceptance but deliberately excluded from request latency.
            total_stop = time.perf_counter_ns()
            total_ns = total_stop - total_start
            loop_end_monotonic_ns = total_stop
            measured = recorder.finish()

            if adapter_state.get("update_error"):
                raise RuntimeError(
                    "GPTCache adapter suppressed a cache update failure: %s"
                    % adapter_state["update_error"]
                )
            if not isinstance(response, dict):
                raise RuntimeError("GPTCache adapter returned an unmaterialized response")
            if "raw_hit" not in adapter_state:
                raise RuntimeError("GPTCache adapter did not classify the request")
            if np.isnan(embedding_buffer[int(request["index"])]).any():
                raise RuntimeError("GPTCache adapter did not compute an embedding")
            response_complete_ns = adapter_state.get(
                "response_materialization_complete_ns"
            )
            if not isinstance(response_complete_ns, int):
                raise RuntimeError(
                    "GPTCache adapter did not cross a measured response boundary"
                )
            response_propagation_ns = total_stop - response_complete_ns
            if response_propagation_ns < 0:
                raise RuntimeError("response propagation time is negative")
            response_return_ns = (
                measured["response_materialization"] + response_propagation_ns
            )

            search_results = adapter_state["search_results"]
            top_candidate_id = None
            if search_results and int(search_results[0][1]) >= 0:
                top_candidate_id = int(search_results[0][1])
            stale_candidate = bool(
                top_candidate_id is not None
                and adapter_state["candidate_cache_data_seen"]
                and not adapter_state["candidate_cache_data_present"]
            )
            similarity = (
                -1.0
                if similarity_evaluation.last_similarity is None
                else float(similarity_evaluation.last_similarity)
            )
            raw_hit = bool(adapter_state["raw_hit"])

            cached_question = adapter_state.get("candidate_cache_question")
            cached_question_text_sha256 = None
            top_candidate_text_id = None
            cached_source_text_id = None
            cached_source_concept_id = None
            if isinstance(cached_question, str):
                normalized_cached_question = _normalize_text(cached_question)
                cached_question_text_sha256 = hashlib.sha256(
                    normalized_cached_question.encode("utf-8")
                ).hexdigest()
                top_candidate_text_id = text_id_by_normalized.get(
                    normalized_cached_question
                )
                if raw_hit:
                    cached_source_text_id = top_candidate_text_id
                    cached_source_concept_id = concept_by_text_id.get(
                        str(cached_source_text_id)
                    )

            candidate_answer = adapter_state.get("candidate_cache_answer")
            candidate_answer_identity: Optional[Dict[str, Any]] = None
            cached_answer_sha256 = None
            if isinstance(candidate_answer, str):
                cached_answer_sha256 = hashlib.sha256(
                    candidate_answer.encode("utf-8")
                ).hexdigest()
                try:
                    candidate_answer_identity = _materialize_response(candidate_answer)
                except ValueError:
                    candidate_answer_identity = None
            cached_answer_source_text_id = (
                candidate_answer_identity.get("source_text_id")
                if candidate_answer_identity is not None
                else None
            )
            cached_answer_source_concept_id = (
                candidate_answer_identity.get("concept_id")
                if candidate_answer_identity is not None
                else None
            )

            payload_source_text_id = str(response.get("source_text_id") or "")
            payload_source_concept_id = str(response.get("concept_id") or "")
            lookup_count = int(adapter_state["candidate_cache_lookup_count"])
            candidate_row_matches = (
                top_candidate_id is not None
                and adapter_state.get("candidate_cache_row_id") == top_candidate_id
            )
            provenance_resolved = bool(
                not raw_hit
                or (
                    lookup_count == 1
                    and adapter_state["candidate_cache_data_present"]
                    and candidate_row_matches
                    and cached_source_text_id is not None
                    and cached_source_concept_id is not None
                    and candidate_answer_identity is not None
                )
            )
            source_response_mismatch = bool(
                raw_hit
                and (
                    (
                        candidate_answer_identity is not None
                        and (
                            payload_source_text_id
                            != cached_answer_source_text_id
                            or payload_source_concept_id
                            != cached_answer_source_concept_id
                            or candidate_answer != response.get("text")
                        )
                    )
                    or (
                        cached_source_text_id is not None
                        and (
                            payload_source_text_id != cached_source_text_id
                            or payload_source_concept_id
                            != cached_source_concept_id
                            or (
                                candidate_answer_identity is not None
                                and (
                                    cached_answer_source_text_id
                                    != cached_source_text_id
                                    or cached_answer_source_concept_id
                                    != cached_source_concept_id
                                )
                            )
                        )
                    )
                )
            )
            if not raw_hit:
                source_response_mismatch = bool(
                    payload_source_text_id != request["text_id"]
                    or payload_source_concept_id != request["concept_id"]
                )
            provenance_consistent = bool(
                provenance_resolved
                and not source_response_mismatch
                and not adapter_state.get("response_parse_error")
            )

            semantic_outcome = _classify_request_semantics(
                classify_semantic_relation,
                semantic_index,
                raw_hit,
                provenance_resolved,
                provenance_consistent,
                str(request["text_id"]),
                str(request["concept_id"]),
                cached_source_text_id,
                cached_source_concept_id,
            )
            semantic_relation = semantic_outcome["semantic_relation"]
            semantic_status = semantic_outcome["semantic_status"]
            semantic_label = semantic_outcome["semantic_label"]
            semantic_evidence_kind = semantic_outcome[
                "semantic_evidence_kind"
            ]
            semantic_source_indices = semantic_outcome[
                "semantic_source_indices"
            ]
            hit_class = semantic_outcome["hit_class"]

            if semantic_relation not in SEMANTIC_RELATIONS:
                raise RuntimeError("semantic classifier returned an unknown relation")
            response_id_mismatch = response["response_id"] != request["response_id"]
            unrecognized_response_id = response["response_id"] not in known_response_ids
            action = _policy_action(policy, raw_hit, recorder)
            cache_size_after = _cache_size(manager)
            if policy != "CARMA" and not raw_hit:
                action["evicted"] = cache_size_before >= config.capacity
            capacity_exceeded = max(cache_size_before, cache_size_after) > config.capacity
            structural_reasons = []
            if raw_hit and not provenance_resolved:
                structural_reasons.append("unresolved_provenance")
            if source_response_mismatch or adapter_state.get("response_parse_error"):
                structural_reasons.append("response_provenance_corruption")
            if unrecognized_response_id:
                structural_reasons.append("unrecognized_response_id")
            if stale_candidate:
                structural_reasons.append("stale_candidate")
            if capacity_exceeded:
                structural_reasons.append("capacity_exceeded")
            structural_failure = bool(structural_reasons)
            counters["max_cache_size"] = max(
                counters["max_cache_size"], cache_size_before, cache_size_after
            )
            counters["hits"] += int(raw_hit)
            counters["misses"] += int(not raw_hit)
            relation_counter = {
                "positive_same_component": "same_concept_hits",
                "negative_direct": "direct_negative_hits",
                "negative_component_derived": "component_derived_negative_hits",
                "unlabeled_cross_component": "unlabeled_cross_concept_hits",
            }.get(semantic_relation)
            if raw_hit and relation_counter is not None:
                counters[relation_counter] += 1
            counters["unresolved_provenance_hits"] += int(
                raw_hit
                and not (provenance_resolved and provenance_consistent)
            )
            counters["response_id_mismatches"] += int(response_id_mismatch)
            counters["source_response_mismatches"] += int(
                source_response_mismatch
            )
            counters["unrecognized_response_ids"] += int(
                unrecognized_response_id
            )
            counters["stale_candidates"] += int(stale_candidate)
            counters["capacity_excess_requests"] += int(capacity_exceeded)
            counters["structural_failure_requests"] += int(structural_failure)
            counters["admissions"] += int(action["admitted"] is True)
            counters["rejections"] += int(action["rejected"] is True)
            counters["evictions"] += int(action["evicted"] is True)

            embedding_parts = adapter_state["embedding_parts"]
            preprocess_ns = measured["preprocess"]
            text_preprocess_tokenize_ns = preprocess_ns + int(
                embedding_parts["tokenize"]
            )
            embedding_contract_ns = (
                text_preprocess_tokenize_ns
                + int(embedding_parts["onnx_inference"])
                + int(embedding_parts["embedding_postprocess"])
            )
            post_embedding_contract_ns = total_ns - embedding_contract_ns
            if post_embedding_contract_ns < 0:
                raise RuntimeError("derived post-embedding time is negative")
            post_embedding_elapsed_ns = max(
                0,
                loop_end_monotonic_ns
                - int(adapter_state.get("embedding_complete_ns", loop_end_monotonic_ns)),
            )
            cache_management_ns = (
                measured["policy_exclusive"]
                + measured["sqlite_write"]
                + measured["faiss_mutation"]
            )
            exclusive = {
                "preprocess": preprocess_ns,
                "tokenize": int(embedding_parts["tokenize"]),
                "onnx_inference": int(embedding_parts["onnx_inference"]),
                "embedding_postprocess": int(embedding_parts["embedding_postprocess"]),
                "faiss_search": measured["faiss_search"],
                "faiss_mutation": measured["faiss_mutation"],
                "sqlite_read": measured["sqlite_read"],
                "sqlite_write": measured["sqlite_write"],
                "similarity_evaluation": measured["similarity_evaluation"],
                "policy_exclusive": measured["policy_exclusive"],
                "response_return": response_return_ns,
            }
            exclusive_sum = sum(exclusive.values())
            if exclusive_sum > total_ns:
                raise RuntimeError("exclusive stage time exceeds end-to-end time")
            residual_ns = total_ns - exclusive_sum
            faiss_ns = measured["faiss_search"] + measured["faiss_mutation"]
            sqlite_ns = measured["sqlite_read"] + measured["sqlite_write"]

            stage_values["end_to_end"].append(total_ns)
            stage_values["post_embedding"].append(post_embedding_contract_ns)
            stage_values["embedding"].append(embedding_contract_ns)
            stage_values["faiss"].append(faiss_ns)
            stage_values["sqlite"].append(sqlite_ns)
            stage_values["policy_exclusive"].append(measured["policy_exclusive"])
            stage_values["policy_inclusive"].append(measured["policy_inclusive"])
            stage_values["response_return"].append(response_return_ns)
            stage_values["residual"].append(residual_ns)

            request_buffer[int(request["index"])].update(
                {
                    "text_sha256": hashlib.sha256(
                        str(adapter_state["normalized_text"]).encode("utf-8")
                    ).hexdigest(),
                    "returned_response_id": response["response_id"],
                    "returned_concept_id": response["concept_id"],
                    "top_candidate_id": top_candidate_id,
                    "top_candidate_text_id": top_candidate_text_id,
                    "candidate_cache_data_seen": bool(
                        adapter_state["candidate_cache_data_seen"]
                    ),
                    "candidate_cache_data_present": bool(
                        adapter_state["candidate_cache_data_present"]
                    ),
                    "candidate_cache_lookup_count": lookup_count,
                    "cached_question_text_sha256": cached_question_text_sha256,
                    "cached_source_text_id": cached_source_text_id,
                    "cached_source_concept_id": cached_source_concept_id,
                    "cached_answer_sha256": cached_answer_sha256,
                    "cached_answer_raw_identity": candidate_answer,
                    "cached_answer_source_text_id": cached_answer_source_text_id,
                    "cached_answer_source_concept_id": (
                        cached_answer_source_concept_id
                    ),
                    "payload_source_text_id": payload_source_text_id,
                    "payload_source_concept_id": payload_source_concept_id,
                    "provenance_resolved": provenance_resolved,
                    "provenance_consistent": provenance_consistent,
                    "semantic_relation": semantic_relation,
                    "semantic_status": semantic_status,
                    "semantic_label": semantic_label,
                    "semantic_evidence_kind": semantic_evidence_kind,
                    "semantic_source_indices": semantic_source_indices,
                    "hit_class": hit_class,
                    "response_id_mismatch": response_id_mismatch,
                    "source_response_mismatch": source_response_mismatch,
                    "unrecognized_response_id": unrecognized_response_id,
                    "stale_candidate": stale_candidate,
                    "capacity_exceeded": capacity_exceeded,
                    "structural_failure": structural_failure,
                    "structural_failure_reasons": "|".join(structural_reasons),
                    "raw_hit": raw_hit,
                    "similarity": round(similarity, 10),
                    "policy_action": action["action"],
                    "admitted": action["admitted"],
                    "rejected": action["rejected"],
                    "evicted": action["evicted"],
                    "cache_size_before": cache_size_before,
                    "cache_size_after": cache_size_after,
                    "preprocess_ns": preprocess_ns,
                    "tokenize_ns": int(embedding_parts["tokenize"]),
                    "text_preprocess_tokenize_ns": text_preprocess_tokenize_ns,
                    "onnx_inference_ns": int(embedding_parts["onnx_inference"]),
                    "embedding_postprocess_ns": int(
                        embedding_parts["embedding_postprocess"]
                    ),
                    "embedding_elapsed_ns": int(adapter_state["embedding_elapsed_ns"]),
                    "embedding_ns": embedding_contract_ns,
                    "faiss_search_ns": measured["faiss_search"],
                    "faiss_mutation_ns": measured["faiss_mutation"],
                    "faiss_ns": faiss_ns,
                    "sqlite_read_ns": measured["sqlite_read"],
                    "sqlite_write_ns": measured["sqlite_write"],
                    "sqlite_ns": sqlite_ns,
                    "similarity_evaluation_ns": measured["similarity_evaluation"],
                    "similarity_decision_ns": measured["similarity_evaluation"],
                    "policy_exclusive_ns": measured["policy_exclusive"],
                    "policy_inclusive_ns": measured["policy_inclusive"],
                    "response_materialization_ns": measured[
                        "response_materialization"
                    ],
                    "response_propagation_ns": response_propagation_ns,
                    "response_return_ns": response_return_ns,
                    "cache_management_ns": cache_management_ns,
                    "post_embedding_elapsed_ns": post_embedding_elapsed_ns,
                    "post_embedding_ns": post_embedding_contract_ns,
                    "post_embedding_total_ns": post_embedding_contract_ns,
                    "residual_ns": residual_ns,
                    "end_to_end_ns": total_ns,
                    "request_total_ns": total_ns,
                    "exclusive_reconciles": exclusive_sum + residual_ns == total_ns,
                }
            )

        if loop_start_monotonic_ns is None or loop_end_monotonic_ns is None:
            raise RuntimeError("measured request loop did not execute")
        _write_control_state(control_dir, "loop_complete", len(trace) - 1)
        _wait_for_ack(control_dir / "end.ack")
        asset_identity_post: Optional[Dict[str, Any]] = None
        if not config.fake_embedding:
            asset_identity_post = _verify_frozen_onnx_assets(
                Path(assets["model_path"]),
                Path(assets["tokenizer_path"]),
                assets.get("model_files", {}),
                str(assets.get("model_digest_sha256")),
                assets.get("tokenizer_files", {}),
                str(assets.get("tokenizer_digest_sha256")),
                "child post-loop",
            )
            if asset_identity_post != asset_identity_pre:
                raise RuntimeError(
                    "child ONNX asset identities changed across the measured loop"
                )
        dependency_attestation_post: Optional[Dict[str, Any]] = None
        dependency_imported_origins_post: Optional[Dict[str, Any]] = None
        if config.mode == "full":
            dependency_attestation_post = _dependency_attestation()
            if (
                dependency_attestation_post != dependency_attestation_pre
                or dependency_attestation_post.get("attestation_sha256")
                != expected_dependency_attestation_sha256
            ):
                raise RuntimeError(
                    "formal child dependency attestation changed across the loop"
                )
            dependency_imported_origins_post = dict(
                dependency_attestation_post["installed"]["imported_origins"]
            )
        if not np.all(np.isfinite(embedding_buffer)):
            raise RuntimeError("embedding evidence buffer is incomplete")
        for request, vector in zip(trace, embedding_buffer):
            embedding_digest.update(str(request["text_id"]).encode("utf-8"))
            embedding_digest.update(np.asarray(vector, dtype="<f4").tobytes())
        storage_failure: Optional[str] = None
        try:
            verification = _verify_storage(manager, config.capacity)
        except Exception as error:  # retained as structural diagnostic evidence
            storage_failure = "%s: %s" % (type(error).__name__, error)
            verification = {
                "final_scalar_count": -1,
                "final_vector_count": -1,
                "deleted_scalar_count": -1,
                "verified_entries": -1,
            }
        policy_stats = (
            dict(manager.eviction_base.stats())
            if callable(getattr(manager.eviction_base, "stats", None))
            else {}
        )
        manager.close()

    cpu_end = process.cpu_times()
    if not all(record.get("exclusive_reconciles") is True for record in request_buffer):
        raise RuntimeError("request record buffer is incomplete")
    requests_out = list(request_buffer)
    structural = _structural_summary(requests_out, storage_failure)
    classified_hits = sum(
        int(counters[name])
        for name in (
            "same_concept_hits",
            "direct_negative_hits",
            "component_derived_negative_hits",
            "unlabeled_cross_concept_hits",
            "unresolved_provenance_hits",
        )
    )
    if classified_hits != int(counters["hits"]):
        raise RuntimeError("semantic hit classes do not partition raw hits")
    labeled_negative_hits = int(counters["direct_negative_hits"]) + int(
        counters["component_derived_negative_hits"]
    )
    cross_concept_hits = labeled_negative_hits + int(
        counters["unlabeled_cross_concept_hits"]
    )
    semantic_guardrail_status = _semantic_guardrail_status(counters)
    service_seconds = sum(stage_values["end_to_end"]) / 1e9
    loop_seconds = (loop_end_monotonic_ns - loop_start_monotonic_ns) / 1e9
    summary: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "child_pid": os.getpid(),
        "child_exit_status": 0,
        "storage_instance_sha256": storage_instance_sha256,
        "mode": config.mode,
        "seed": seed,
        "policy": policy,
        "order_position": order_position,
        "trace_hash": expected_trace_hash,
        "requests": len(trace),
        "capacity": config.capacity,
        "embedding_dimension": int(embedder.dimension),
        "embedding_norm_verified": True,
        "provider_verified": (
            config.fake_embedding
            or list(embedder.providers) == ["CPUExecutionProvider"]
        ),
        "warmup_artifact_sha256": expected_warmup_hash,
        "dependency_attestation_pre_verified": (
            dependency_attestation_pre is not None
        ),
        "dependency_attestation_post_verified": (
            dependency_attestation_post is not None
        ),
        "dependency_attestation_sha256": (
            dependency_attestation_post.get("attestation_sha256")
            if dependency_attestation_post is not None
            else None
        ),
        "bootstrap_attestation_sha256": (
            child_bootstrap.get("attestation_sha256")
            if child_bootstrap is not None
            else None
        ),
        "environment_sha256": (
            dependency_attestation_post["environment"].get("environment_sha256")
            if dependency_attestation_post is not None
            else None
        ),
        "imported_origins_pre_sha256": (
            _canonical_mapping_sha256(dependency_imported_origins_pre)
            if dependency_imported_origins_pre is not None
            else None
        ),
        "imported_origins_post_sha256": (
            _canonical_mapping_sha256(dependency_imported_origins_post)
            if dependency_imported_origins_post is not None
            else None
        ),
        "asset_integrity_pre_verified": asset_identity_pre is not None,
        "asset_integrity_post_verified": asset_identity_post is not None,
        "model_asset_digest_sha256": (
            asset_identity_post["model_digest_sha256"]
            if asset_identity_post is not None
            else None
        ),
        "tokenizer_asset_digest_sha256": (
            asset_identity_post["tokenizer_digest_sha256"]
            if asset_identity_post is not None
            else None
        ),
        "request_buffer_rows_reserved": len(request_buffer),
        "embedding_buffer_bytes_reserved": int(embedding_buffer.nbytes),
        "hits": int(counters["hits"]),
        "misses": int(counters["misses"]),
        "same_concept_hits": int(counters["same_concept_hits"]),
        "direct_negative_hits": int(counters["direct_negative_hits"]),
        "component_derived_negative_hits": int(
            counters["component_derived_negative_hits"]
        ),
        "unlabeled_cross_concept_hits": int(
            counters["unlabeled_cross_concept_hits"]
        ),
        "unresolved_provenance_hits": int(
            counters["unresolved_provenance_hits"]
        ),
        "labeled_negative_hits": labeled_negative_hits,
        "cross_concept_hits": cross_concept_hits,
        "response_id_mismatches": int(counters["response_id_mismatches"]),
        "source_response_mismatches": int(
            counters["source_response_mismatches"]
        ),
        "unrecognized_response_ids": int(
            counters["unrecognized_response_ids"]
        ),
        "stale_candidates": int(counters["stale_candidates"]),
        "capacity_excess_requests": int(counters["capacity_excess_requests"]),
        "structural_failure_requests": int(
            structural["structural_failure_requests"]
        ),
        "structural_failure_flags_total": int(
            structural["structural_failure_flags_total"]
        ),
        "structural_valid": bool(structural["structural_valid"]),
        "storage_failure": storage_failure,
        "semantic_guardrail_status": semantic_guardrail_status,
        "semantic_index_sha256": semantic_index.sha256,
        "semantic_index_artifact_sha256": expected_semantic_index_hash,
        "max_cache_size": int(counters["max_cache_size"]),
        "admissions": int(policy_stats.get("admissions", counters["admissions"])),
        "rejections": int(policy_stats.get("rejections", counters["rejections"])),
        "evictions": int(policy_stats.get("evictions", counters["evictions"])),
        "service_seconds": round(service_seconds, 9),
        "loop_seconds": round(loop_seconds, 9),
        "throughput_qps": round(len(trace) / service_seconds, 6),
        "service_throughput_qps": round(len(trace) / service_seconds, 6),
        "loop_throughput_qps": round(len(trace) / loop_seconds, 6),
        "cpu_user_seconds": round(float(cpu_end.user - cpu_start.user), 9),
        "cpu_system_seconds": round(float(cpu_end.system - cpu_start.system), 9),
        "rss_post_warmup_bytes": post_warmup_rss,
        "uss_post_warmup_bytes": post_warmup_uss,
        "rss_high_water_bytes": _high_water_rss_bytes(),
        "embedding_digest_sha256": embedding_digest.hexdigest(),
        "timer_overhead_median_ns": timer_overhead["median_ns"],
        "timer_overhead_p95_ns": timer_overhead["p95_ns"],
        "loop_start_monotonic_ns": loop_start_monotonic_ns,
        "loop_end_monotonic_ns": loop_end_monotonic_ns,
        **verification,
    }
    for stage in SUMMARY_STAGES:
        summary.update(_latency_summary(stage_values[stage], stage))
    return {
        "summary": summary,
        "requests": requests_out,
        "structural": structural,
        "semantic_guardrail": {
            "status": semantic_guardrail_status,
            "labeled_negative_hits": labeled_negative_hits,
            "unlabeled_cross_concept_hits": int(
                counters["unlabeled_cross_concept_hits"]
            ),
        },
        "semantic_index_sha256": semantic_index.sha256,
        "semantic_index_artifact_sha256": expected_semantic_index_hash,
        "timer_overhead": timer_overhead,
        "providers": list(embedder.providers),
        "warmup_artifact": warmup_artifact,
        "dependency_attestation_sha256": (
            dependency_attestation_post.get("attestation_sha256")
            if dependency_attestation_post is not None
            else None
        ),
        "bootstrap_attestation": child_bootstrap,
        "environment_sha256": (
            dependency_attestation_post["environment"].get("environment_sha256")
            if dependency_attestation_post is not None
            else None
        ),
        "imported_module_origins_pre": dependency_imported_origins_pre,
        "imported_module_origins_post": dependency_imported_origins_post,
        "asset_integrity": {
            "pre_verified": asset_identity_pre is not None,
            "post_verified": asset_identity_post is not None,
            "model_files": (
                asset_identity_post["model_files"]
                if asset_identity_post is not None
                else {}
            ),
            "model_digest_sha256": (
                asset_identity_post["model_digest_sha256"]
                if asset_identity_post is not None
                else None
            ),
            "tokenizer_files": (
                asset_identity_post["tokenizer_files"]
                if asset_identity_post is not None
                else {}
            ),
            "tokenizer_digest_sha256": (
                asset_identity_post["tokenizer_digest_sha256"]
                if asset_identity_post is not None
                else None
            ),
        },
    }


def _resource_sample(
    process: psutil.Process,
    attempt_id: str,
    run_id: str,
    seed: int,
    policy: str,
    sample_index: int,
    kind: str,
    scope: str,
    request_index: Optional[int],
) -> Optional[Dict[str, Any]]:
    try:
        memory = process.memory_info()
        cpu = process.cpu_times()
        try:
            uss: Optional[int] = int(process.memory_full_info().uss)
        except (AttributeError, psutil.Error):
            uss = None
        try:
            io = process.io_counters()
        except (AttributeError, NotImplementedError, psutil.Error):
            io = None
        try:
            system_load_1m: Optional[float] = float(os.getloadavg()[0])
        except (AttributeError, OSError):
            system_load_1m = None
        return {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "child_pid": int(process.pid),
            "seed": seed,
            "policy": policy,
            "sample_index": sample_index,
            "kind": kind,
            "scope": scope,
            "request_index": request_index,
            "monotonic_ns": time.perf_counter_ns(),
            "rss_bytes": int(memory.rss),
            "vms_bytes": int(memory.vms),
            "uss_bytes": uss,
            "uss_available": uss is not None,
            "cpu_user_seconds": float(cpu.user),
            "cpu_system_seconds": float(cpu.system),
            "num_threads": int(process.num_threads()),
            "system_load_1m": system_load_1m,
            "io_counters_available": io is not None,
            "io_read_count": int(getattr(io, "read_count", 0)) if io else None,
            "io_write_count": int(getattr(io, "write_count", 0)) if io else None,
            "io_read_bytes": int(getattr(io, "read_bytes", 0)) if io else None,
            "io_write_bytes": int(getattr(io, "write_bytes", 0)) if io else None,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
        return None


def _child_environment(
    formal: bool = True, bootstrapped: Optional[bool] = None
) -> Dict[str, str]:
    """Build the complete pre-bootstrap child environment from an allowlist."""

    if bootstrapped is None:
        bootstrapped = formal
    if formal and not bootstrapped:
        raise RuntimeError("formal Gate 7 children must use the isolated bootstrap")
    environment = dict(FORMAL_REQUIRED_ENVIRONMENT)
    if formal:
        for key in FORMAL_DYNAMIC_ENVIRONMENT:
            value = os.environ.get(key)
            if value is None:
                raise RuntimeError(
                    "Gate 7 child environment is missing required value %s" % key
                )
            environment[key] = value
        expected_keys = set(FORMAL_REQUIRED_ENVIRONMENT) | set(
            FORMAL_DYNAMIC_ENVIRONMENT
        )
    else:
        # Development smoke children still receive a deterministic allowlist,
        # but do not require wrapper-owned paths or wrapper process identity.
        home = os.environ.get("HOME", str(Path.home()))
        environment.update(
            {
                "HOME": home,
                "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
                "HF_HOME": os.environ.get(
                    "HF_HOME", str(Path(home) / ".cache" / "huggingface")
                ),
                "HF_HUB_CACHE": os.environ.get(
                    "HF_HUB_CACHE",
                    str(Path(home) / ".cache" / "huggingface" / "hub"),
                ),
                "CARMA_GATE7_LAUNCH_MODE": os.environ.get(
                    "CARMA_GATE7_LAUNCH_MODE", "smoke"
                ),
                "CARMA_GATE7_WRAPPER_SHELL_PROFILE": os.environ.get(
                    "CARMA_GATE7_WRAPPER_SHELL_PROFILE",
                    "development-smoke",
                ),
                "CARMA_GATE7_WRAPPER_SHELL_HOME": os.environ.get(
                    "CARMA_GATE7_WRAPPER_SHELL_HOME", home
                ),
                "CARMA_GATE7_WRAPPER_SHELL_PWD": os.environ.get(
                    "CARMA_GATE7_WRAPPER_SHELL_PWD", str(PROJECT_ROOT)
                ),
                "CARMA_GATE7_WRAPPER_SHELL_TMPDIR": os.environ.get(
                    "CARMA_GATE7_WRAPPER_SHELL_TMPDIR",
                    os.environ.get("TMPDIR", tempfile.gettempdir()),
                ),
                "CARMA_GATE7_WRAPPER_SHELL_SHLVL": os.environ.get(
                    "CARMA_GATE7_WRAPPER_SHELL_SHLVL",
                    os.environ.get("SHLVL", "1"),
                ),
                "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF": os.environ.get(
                    "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF",
                    os.environ.get("__CF_USER_TEXT_ENCODING", ""),
                ),
            }
        )
        expected_keys = set(FORMAL_REQUIRED_ENVIRONMENT) | {
            "HOME",
            "TMPDIR",
            "HF_HOME",
            "HF_HUB_CACHE",
            "CARMA_GATE7_LAUNCH_MODE",
            "CARMA_GATE7_WRAPPER_SHELL_PROFILE",
            "CARMA_GATE7_WRAPPER_SHELL_HOME",
            "CARMA_GATE7_WRAPPER_SHELL_PWD",
            "CARMA_GATE7_WRAPPER_SHELL_TMPDIR",
            "CARMA_GATE7_WRAPPER_SHELL_SHLVL",
            "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF",
        }
        if bootstrapped:
            for key in (
                "PYTHONPYCACHEPREFIX",
                "CARMA_GATE7_WRAPPER_PID",
                "CARMA_GATE7_WRAPPER_PATH",
            ):
                value = os.environ.get(key)
                if value is None:
                    raise RuntimeError(
                        "bootstrapped Gate 7 smoke child is missing required value %s"
                        % key
                    )
                environment[key] = value
                expected_keys.add(key)
        elif os.environ.get("PYTHONPYCACHEPREFIX"):
            environment["PYTHONPYCACHEPREFIX"] = os.environ[
                "PYTHONPYCACHEPREFIX"
            ]
            expected_keys.add("PYTHONPYCACHEPREFIX")
    for key in FORMAL_OPTIONAL_OS_ENVIRONMENT:
        if key in os.environ:
            environment[key] = os.environ[key]
            expected_keys.add(key)
    if set(environment) != expected_keys:
        raise RuntimeError("Gate 7 child environment allowlist is inconsistent")
    expected_launch_mode = "full" if formal else "smoke"
    if environment["CARMA_GATE7_LAUNCH_MODE"] != expected_launch_mode:
        raise RuntimeError("Gate 7 child launch mode differs from its run mode")
    expected_shell_profile = "env-i-v1" if formal else "development-smoke"
    if (
        environment["CARMA_GATE7_WRAPPER_SHELL_PROFILE"]
        != expected_shell_profile
        or Path(environment["CARMA_GATE7_WRAPPER_SHELL_HOME"]).resolve()
        != Path(environment["HOME"]).resolve()
        or Path(environment["CARMA_GATE7_WRAPPER_SHELL_PWD"]).resolve()
        != PROJECT_ROOT
        or not environment["CARMA_GATE7_WRAPPER_SHELL_TMPDIR"]
        or not environment["CARMA_GATE7_WRAPPER_SHELL_SHLVL"]
        or (formal and environment["CARMA_GATE7_WRAPPER_SHELL_TMPDIR"] != "/tmp")
        or (formal and environment["CARMA_GATE7_WRAPPER_SHELL_SHLVL"] != "1")
    ):
        raise RuntimeError("Gate 7 child wrapper-shell startup differs")
    if any(
        key.startswith("CARMA_GATE7_BOOTSTRAP_")
        or key.startswith("DYLD_")
        or key.startswith("LD_")
        for key in environment
    ):
        raise RuntimeError("Gate 7 child environment contains a forbidden prefix")
    return environment


def _child_failure_diagnostics(error: BaseException) -> Dict[str, Any]:
    """Snapshot only fully completed rows after an unexpected child failure."""

    request_buffer = _CHILD_DIAGNOSTIC_STATE.get("request_buffer")
    completed_requests = (
        [
            dict(request)
            for request in request_buffer
            if isinstance(request, Mapping)
            and request.get("exclusive_reconciles") is True
        ]
        if isinstance(request_buffer, list)
        else []
    )
    adapter_state = _CHILD_DIAGNOSTIC_STATE.get("adapter_state")
    failed_request_index = (
        adapter_state.get("request_index")
        if isinstance(adapter_state, Mapping)
        else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "kind": "child_failure_diagnostics",
        "attempt_id": _CHILD_DIAGNOSTIC_STATE.get("attempt_id"),
        "run_id": _CHILD_DIAGNOSTIC_STATE.get("run_id"),
        "seed": _CHILD_DIAGNOSTIC_STATE.get("seed"),
        "policy": _CHILD_DIAGNOSTIC_STATE.get("policy"),
        "order_position": _CHILD_DIAGNOSTIC_STATE.get("order_position"),
        "trace_hash": _CHILD_DIAGNOSTIC_STATE.get("trace_hash"),
        "error_type": type(error).__name__,
        "error": str(error),
        "failed_request_index": failed_request_index,
        "completed_request_count": len(completed_requests),
        "completed_requests": completed_requests,
    }


def _run_child_with_sampling(
    command: Sequence[str],
    result_path: Path,
    attempt_id: str,
    run_id: str,
    seed: int,
    policy: str,
    interval_ms: int,
    control_dir: Path,
    formal_environment: bool,
    bootstrapped_environment: bool,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    process_handle = subprocess.Popen(
        list(command),
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_environment(formal_environment, bootstrapped_environment),
    )
    observed = psutil.Process(process_handle.pid)
    samples: List[Dict[str, Any]] = []
    sample_index = 0
    state = "startup"
    request_index: Optional[int] = None
    start_acknowledged = False
    end_acknowledged = False
    next_periodic = time.monotonic()
    while process_handle.poll() is None:
        state_path = Path(control_dir) / "state.json"
        if state_path.is_file():
            try:
                state_row = json.loads(state_path.read_text(encoding="utf-8"))
                state = str(state_row["state"])
                request_index = int(state_row.get("request_index", -1))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        if state == "ready" and not start_acknowledged:
            sample = _resource_sample(
                observed,
                attempt_id,
                run_id,
                seed,
                policy,
                sample_index,
                "loop_start",
                "formal",
                -1,
            )
            if sample is not None:
                samples.append(sample)
                sample_index += 1
            (Path(control_dir) / "start.ack").write_text("ok\n", encoding="utf-8")
            start_acknowledged = True
            next_periodic = time.monotonic() + interval_ms / 1000.0
        elif state == "loop_complete" and not end_acknowledged:
            sample = _resource_sample(
                observed,
                attempt_id,
                run_id,
                seed,
                policy,
                sample_index,
                "loop_end",
                "formal",
                request_index,
            )
            if sample is not None:
                samples.append(sample)
                sample_index += 1
            (Path(control_dir) / "end.ack").write_text("ok\n", encoding="utf-8")
            end_acknowledged = True
        elif state == "running" and time.monotonic() >= next_periodic:
            sample = _resource_sample(
                observed,
                attempt_id,
                run_id,
                seed,
                policy,
                sample_index,
                "periodic",
                "formal",
                request_index,
            )
            if sample is not None:
                samples.append(sample)
                sample_index += 1
            next_periodic = time.monotonic() + interval_ms / 1000.0
        time.sleep(0.005)
    stdout, stderr = process_handle.communicate()
    if process_handle.returncode != 0:
        diagnostic: Optional[Dict[str, Any]] = None
        if result_path.is_file():
            try:
                candidate = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                candidate = None
            if (
                isinstance(candidate, dict)
                and candidate.get("kind") == "child_failure_diagnostics"
            ):
                diagnostic = candidate
        raise ChildExecutionError(
            "%s seed %d child failed (exit %d)"
            % (policy, seed, process_handle.returncode),
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
            diagnostic,
        )
    if not result_path.is_file():
        raise ChildExecutionError(
            "child completed without a result artifact",
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        )
    if not start_acknowledged or not end_acknowledged:
        raise ChildExecutionError(
            "resource supervisor did not observe both loop boundaries",
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChildExecutionError(
            "child result artifact is unreadable",
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        ) from exc
    if not isinstance(result, dict):
        raise ChildExecutionError(
            "child result artifact is not an object",
            int(process_handle.returncode),
            stdout,
            stderr,
            samples,
        )
    result["_supervisor_capture"] = {"stdout": stdout, "stderr": stderr}
    return result, samples


def _config_args(config: Gate7Config) -> List[str]:
    args = [
        "--mode", config.mode,
        "--requests", str(config.requests),
        "--capacity", str(config.capacity),
        "--hit-threshold", str(config.hit_threshold),
        "--topic-threshold", str(config.topic_threshold),
        "--cell-threshold", str(config.cell_threshold),
        "--demand-half-life", str(config.demand_half_life),
        "--quota-strength", str(config.quota_strength),
        "--ghost-support-threshold", str(config.ghost_support_threshold),
        "--admission-margin", str(config.admission_margin),
        "--centroid-alpha", str(config.centroid_alpha),
        "--entry-hit-weight", str(config.entry_hit_weight),
        "--onnx-intra-threads", str(config.onnx_intra_threads),
        "--onnx-inter-threads", str(config.onnx_inter_threads),
        "--warmup-requests", str(config.warmup_requests),
        "--resource-interval-ms", str(config.resource_interval_ms),
        "--max-length", str(config.max_length),
    ]
    if config.fake_embedding:
        args.append("--fake-embedding")
    return args


def _runs_fields() -> Tuple[str, ...]:
    base = (
        "schema_version", "attempt_id", "run_id", "child_pid", "child_exit_status",
        "storage_instance_sha256", "mode", "seed", "policy", "order_position",
        "trace_hash", "requests", "capacity", "embedding_dimension",
        "embedding_norm_verified", "provider_verified",
        "warmup_artifact_sha256",
        "dependency_attestation_pre_verified",
        "dependency_attestation_post_verified",
        "dependency_attestation_sha256",
        "environment_sha256",
        "bootstrap_attestation_sha256",
        "imported_origins_pre_sha256", "imported_origins_post_sha256",
        "asset_integrity_pre_verified", "asset_integrity_post_verified",
        "model_asset_digest_sha256", "tokenizer_asset_digest_sha256",
        "request_buffer_rows_reserved", "embedding_buffer_bytes_reserved",
        "hits", "misses",
        "same_concept_hits", "direct_negative_hits",
        "component_derived_negative_hits", "unlabeled_cross_concept_hits",
        "unresolved_provenance_hits", "labeled_negative_hits",
        "cross_concept_hits", "response_id_mismatches",
        "source_response_mismatches", "unrecognized_response_ids",
        "stale_candidates", "capacity_excess_requests",
        "structural_failure_requests", "structural_failure_flags_total",
        "structural_valid", "storage_failure", "semantic_guardrail_status",
        "semantic_index_sha256", "semantic_index_artifact_sha256",
        "max_cache_size",
        "admissions", "rejections", "evictions", "service_seconds", "loop_seconds",
        "throughput_qps", "service_throughput_qps", "loop_throughput_qps",
        "loop_start_monotonic_ns", "loop_end_monotonic_ns",
        "cpu_user_seconds", "cpu_system_seconds", "cpu_total_seconds",
        "cpu_user_lifecycle_seconds", "cpu_system_lifecycle_seconds",
        "resource_window_seconds", "cpu_utilization_percent",
        "io_read_count_delta", "io_write_count_delta", "io_read_bytes_delta",
        "io_write_bytes_delta", "rss_post_warmup_bytes",
        "uss_post_warmup_bytes", "rss_high_water_bytes", "rss_start_bytes", "rss_end_bytes",
        "rss_mean_bytes", "rss_peak_sampled_bytes", "rss_peak_bytes",
        "rss_incremental_peak_bytes", "rss_end_minus_start_bytes",
        "uss_start_bytes", "uss_end_bytes", "uss_mean_bytes", "uss_peak_bytes",
        "uss_incremental_peak_bytes", "uss_end_minus_start_bytes",
        "embedding_digest_sha256",
        "timer_overhead_median_ns", "timer_overhead_p95_ns", "final_scalar_count",
        "final_vector_count", "deleted_scalar_count", "verified_entries",
    )
    latency = tuple(
        "%s_%s_us" % (stage, statistic)
        for stage in SUMMARY_STAGES
        for statistic in ("mean", "p50", "p95", "p99")
    )
    return base + latency


def _serialize_trace(path: Path, rows: Sequence[Any]) -> None:
    _write_jsonl(path, (asdict(row) for row in rows))


def _create_attempt_directory(output_dir: Path, formal: bool) -> bool:
    """Create and own a new formal directory; smoke may reuse an empty one."""

    output_dir = Path(output_dir)
    output_existed = output_dir.exists()
    if formal and (output_existed or output_dir.is_symlink()):
        raise FileExistsError("formal output directory already exists")
    if output_existed and any(output_dir.iterdir()):
        raise FileExistsError("output directory already exists and is not empty")
    output_dir.mkdir(parents=True, exist_ok=not formal)
    return not output_existed


def _run_bundle_impl(
    prepared_dir: Path,
    output_dir: Path,
    contract_path: Path,
    seeds: Sequence[int],
    policies: Sequence[str],
    config: Gate7Config,
    failure_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute an isolated multi-seed Gate 7 evidence bundle."""

    from benchmarks.carma.gate7_v2_trace import (
        build_gate7_semantic_index,
        build_gate7_trace,
        semantic_index_artifact,
    )

    started_at_utc = datetime.now(timezone.utc)
    config.validate()
    _assert_launch_mode(config.mode)
    prepared_dir = Path(prepared_dir).resolve()
    output_dir = Path(output_dir).resolve()
    contract_path = Path(contract_path).resolve()
    if not contract_path.is_file():
        raise FileNotFoundError("Gate 7 remediation contract does not exist")
    if config.mode != "full" and _path_is_under(
        output_dir, FORMAL_ATTEMPT_ROOT.resolve()
    ):
        raise RuntimeError("non-full Gate 7 runs may not write in the formal root")
    git_state = _git_state()
    source_snapshot = _source_identities()
    source_snapshot_sha256 = _canonical_mapping_sha256(source_snapshot)
    contract_identity = _artifact_identity(contract_path)
    gate2_v2 = _gate2_v2_evidence()
    prepared_input_identities = _prepared_input_identities(
        prepared_dir, config.mode == "full"
    )
    dependency_attestation: Optional[Dict[str, Any]] = None
    formal_source_anchor: Optional[Dict[str, Any]] = None
    formal_tag_payload: Optional[bytes] = None
    entrypoint_attestation: Optional[Dict[str, Any]] = None
    if (
        config.mode == "full"
        and output_dir.parent != FORMAL_ATTEMPT_ROOT.resolve()
    ):
        raise RuntimeError(
            "formal output must be one direct child of %s"
            % FORMAL_ATTEMPT_ROOT.resolve()
        )
    if config.mode == "full" and (
        git_state.get("head_commit") is None
        or git_state.get("worktree_dirty") is not False
    ):
        raise RuntimeError(
            "formal full mode requires a clean, committed Git worktree"
        )
    if config.mode == "full":
        entrypoint_attestation = _formal_entrypoint_attestation()
        if contract_path != DEFAULT_CONTRACT.resolve():
            raise RuntimeError(
                "formal full mode requires the tracked default Gate 7 contract"
            )
        if contract_identity["sha256"] != PINNED_CONTRACT_SHA256:
            raise RuntimeError(
                "formal Gate 7 contract hash differs from the frozen identity"
            )
        _validate_sources_are_tracked_at_head(
            str(git_state["head_commit"]), source_snapshot, contract_path
        )
        dependency_attestation = _dependency_attestation()
        formal_source_anchor, formal_tag_payload = _capture_formal_source_anchor(
            contract_identity, str(git_state["head_commit"])
        )
    attempt_id = _attempt_id_for(
        started_at_utc,
        str(git_state.get("head_commit") or "uncommitted"),
    )
    prior_attempts = _prior_attempts(output_dir) if config.mode == "full" else []
    unresolved_attempts = [
        attempt for attempt in prior_attempts if attempt["status"] == "unresolved"
    ]
    if unresolved_attempts:
        raise RuntimeError(
            "formal attempt root contains unresolved predecessor directories: %r"
            % [attempt["directory"] for attempt in unresolved_attempts]
        )
    policies = tuple(policies)
    if not policies or len(set(policies)) != len(policies):
        raise ValueError("policies must be non-empty and unique")
    if any(policy not in POLICIES for policy in policies):
        raise ValueError("unknown policy")
    seeds = tuple(int(seed) for seed in seeds)
    if not seeds:
        raise ValueError("at least one seed is required")
    if config.mode == "full":
        if seeds != FULL_SEEDS:
            raise ValueError("formal full mode requires the five frozen seeds")
        if set(policies) != set(POLICIES):
            raise ValueError("formal full mode requires LRU, LFU, and CARMA")
    assets = _fake_assets() if config.fake_embedding else _resolve_onnx_assets()
    schedule = policy_schedule(seeds)

    # Populate the minimum cleanup identity before any attempt-scoped bytes are
    # created, so a catchable pre-START copy/check/power failure cannot leave a
    # sibling that wedges the canonical formal root.
    failure_context.update(
        {
            "output_dir": output_dir,
            "attempt_id": attempt_id,
            "started_at_utc": started_at_utc,
            "config": config,
            "prior_attempts": prior_attempts,
            "attempt_ledger": None,
            "attempt_directory_created_by_run": False,
        }
    )
    failure_context["attempt_directory_created_by_run"] = (
        _create_attempt_directory(output_dir, config.mode == "full")
    )
    retained_prepared_dir, retained_inputs = _retain_source_inputs(
        prepared_dir,
        output_dir,
        config.mode == "full",
        dependency_attestation,
        formal_source_anchor,
        formal_tag_payload,
    )
    pre_start_power: Dict[str, Any] = {}
    power_observations: Optional[Dict[str, Any]] = None
    final_pre_start_check: Optional[Callable[[], None]] = None
    if config.mode == "full":
        def final_pre_start_check() -> None:
            fresh_git = _git_state()
            fresh_sources = _source_identities()
            fresh_contract = _artifact_identity(contract_path)
            fresh_dependency = _dependency_attestation()
            fresh_anchor, fresh_tag_payload = _capture_formal_source_anchor(
                fresh_contract, str(git_state["head_commit"])
            )
            fresh_entrypoint = _formal_entrypoint_attestation()
            fresh_prepared = _prepared_input_identities(prepared_dir, True)
            if (
                fresh_git != git_state
                or fresh_sources != source_snapshot
                or _canonical_mapping_sha256(fresh_sources)
                != source_snapshot_sha256
                or fresh_contract != contract_identity
                or fresh_dependency != dependency_attestation
                or fresh_anchor != formal_source_anchor
                or fresh_tag_payload != formal_tag_payload
                or fresh_entrypoint != entrypoint_attestation
                or fresh_prepared != prepared_input_identities
            ):
                raise RuntimeError("formal Gate 7 pre-START attestations changed")
    failure_context.update(
        {
            "output_dir": output_dir,
            "attempt_id": attempt_id,
            "started_at_utc": started_at_utc,
            "seed": -1,
            "policy": "not_started",
            "order_position": -1,
            "config": config,
            "contract_identity": contract_identity,
            "git_state": git_state,
            "source_snapshot": source_snapshot,
            "source_snapshot_sha256": source_snapshot_sha256,
            "dependency_attestation": dependency_attestation,
            "formal_source_anchor": formal_source_anchor,
            "entrypoint_attestation": entrypoint_attestation,
            "power_observations": power_observations,
            "retained_inputs": retained_inputs,
            "retained_prepared_dir": retained_prepared_dir,
            "prior_attempts": prior_attempts,
            "attempt_ledger": None,
            "resource_records": [],
            "current_samples": [],
            "child_stdout": None,
            "child_stderr": None,
        }
    )
    if config.mode == "full":
        attempt_ledger = _register_formal_attempt(
            output_dir,
            attempt_id,
            started_at_utc,
            str(git_state["head_commit"]),
            prior_attempts,
            contract_identity,
            source_snapshot_sha256,
            formal_source_anchor or {},
            str(dependency_attestation["attestation_sha256"]),
            str(
                dependency_attestation["environment"]["environment_sha256"]
            ),
            entrypoint_attestation or {},
            pre_start_power,
            retained_inputs,
            dependency_attestation=dependency_attestation,
            final_pre_start_check=final_pre_start_check,
            power_observer=_power_observation,
        )
        power_observations = {
            "pre_start": dict(pre_start_power),
            "end": None,
            "availability_limitation": (
                "power source unavailable to psutil; uninterrupted AC cannot be attested"
                if pre_start_power.get("available") is False
                else "point observations cannot prove uninterrupted AC between observations"
            ),
        }
        failure_context["attempt_ledger"] = attempt_ledger
        failure_context["power_observations"] = power_observations
    rows: List[Dict[str, Any]] = []
    request_records: List[Dict[str, Any]] = []
    resource_records: List[Dict[str, Any]] = []
    failure_context["resource_records"] = resource_records
    failure_context["diagnostic_summaries"] = rows
    failure_context["diagnostic_requests"] = request_records
    trace_metadata: Dict[str, Any] = {}
    actual_orders: Dict[str, List[str]] = {}
    trace_artifacts: Dict[str, Dict[str, Any]] = {}
    semantic_index_artifacts: Dict[str, Dict[str, Any]] = {}
    warmup_artifacts: Dict[str, Dict[str, Any]] = {}
    child_bootstrap_attestations: Dict[str, Dict[str, Any]] = {}
    child_imported_module_origins: Dict[str, Dict[str, Any]] = {}
    traces_dir = output_dir / "traces"
    semantic_index_dir = output_dir / "semantic-indexes"
    warmup_dir = output_dir / "warmups"
    traces_dir.mkdir(parents=True, exist_ok=True)
    semantic_index_dir.mkdir(parents=True, exist_ok=True)
    warmup_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gate7-orchestrator-") as temporary:
        temporary_root = Path(temporary)
        source_snapshot_path = temporary_root / "source-snapshot.json"
        source_snapshot_path.write_text(
            json.dumps(source_snapshot, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        seed_inputs: Dict[int, Tuple[Any, str, Dict[str, Any], Any]] = {}
        for seed in sorted(seeds):
            failure_context.update(
                {
                    "seed": seed,
                    "policy": "trace_semantic_preflight",
                    "order_position": -1,
                }
            )
            trace, trace_hash, metadata = build_gate7_trace(
                retained_prepared_dir,
                seed=seed,
                requests=config.requests,
                capacity=config.capacity,
            )
            selected_concept_ids = sorted(
                {str(row.concept_id) for row in trace}
            )
            semantic_index = build_gate7_semantic_index(
                retained_prepared_dir, selected_concept_ids
            )
            if metadata.get("semantic_index_sha256") != semantic_index.sha256:
                raise RuntimeError(
                    "trace metadata and rebuilt semantic index disagree"
                )
            if config.mode == "full":
                pinned_source = {
                    "source_archive_sha256": PINNED_ARCHIVE_SHA256,
                    "source_pairs_sha256": PINNED_PAIRS_SHA256,
                    "source_texts_sha256": PINNED_TEXTS_SHA256,
                }
                mismatches = {
                    key: (metadata.get(key), expected)
                    for key, expected in pinned_source.items()
                    if metadata.get(key) != expected
                }
                if mismatches:
                    raise RuntimeError(
                        "formal QQP provenance differs from the frozen contract: %r"
                        % mismatches
                    )
                pinned_hashes = {
                    "trace_sha256": (
                        trace_hash,
                        PINNED_FULL_TRACE_SHA256[seed],
                    ),
                    "semantic_index_sha256": (
                        semantic_index.sha256,
                        PINNED_FULL_SEMANTIC_INDEX_SHA256[seed],
                    ),
                }
                hash_mismatches = {
                    key: values
                    for key, values in pinned_hashes.items()
                    if values[0] != values[1]
                }
                if hash_mismatches:
                    raise RuntimeError(
                        "formal trace/semantic identity differs from the frozen "
                        "contract for seed %d: %r" % (seed, hash_mismatches)
                    )
            seed_inputs[seed] = (trace, trace_hash, metadata, semantic_index)

        # All five frozen trace and semantic identities are checked before the
        # first child can execute. This prevents partial execution under a
        # mixed or drifted workload definition.
        for seed in sorted(seeds):
            failure_context.update(
                {"seed": seed, "policy": "artifact_build", "order_position": -1}
            )
            trace, trace_hash, metadata, semantic_index = seed_inputs[seed]
            trace_path = traces_dir / ("seed-%d.jsonl" % seed)
            _serialize_trace(trace_path, trace)
            if _trace_digest(_load_trace(trace_path)) != trace_hash:
                raise RuntimeError("serialized trace hash differs from trace builder")
            trace_artifacts[str(trace_path.relative_to(output_dir))] = _artifact_identity(
                trace_path, rows=len(trace)
            )
            trace_metadata[str(seed)] = metadata
            semantic_index_path = semantic_index_dir / ("seed-%d.json" % seed)
            _write_json(
                semantic_index_path,
                semantic_index_artifact(semantic_index, seed, trace_hash),
            )
            semantic_index_identity = _artifact_identity(
                semantic_index_path,
                rows=_artifact_row_count(semantic_index_path),
            )
            semantic_index_artifacts[
                str(semantic_index_path.relative_to(output_dir))
            ] = semantic_index_identity
            warmup_path = warmup_dir / ("seed-%d.json" % seed)
            _write_json(
                warmup_path,
                _build_warmup_artifact(
                    trace,
                    metadata["hot_text_ids"],
                    seed,
                    trace_hash,
                    config.warmup_requests,
                ),
            )
            warmup_identity = _artifact_identity(
                warmup_path,
                rows=_artifact_row_count(warmup_path),
            )
            warmup_artifacts[
                str(warmup_path.relative_to(output_dir))
            ] = warmup_identity
            order = tuple(policy for policy in schedule[seed] if policy in policies)
            actual_orders[str(seed)] = list(order)
            seed_embedding_digests = set()

            for order_position, policy in enumerate(order):
                failure_context.update(
                    {
                        "seed": seed,
                        "policy": policy,
                        "order_position": order_position,
                        "current_samples": [],
                    }
                )
                run_id = _run_id(
                    policy,
                    seed,
                    trace_hash,
                    config,
                    assets["model_sha256"],
                    attempt_id,
                )
                result_path = temporary_root / ("result-%d-%s.json" % (seed, policy.lower()))
                control_dir = temporary_root / ("control-%d-%s" % (seed, policy.lower()))
                control_dir.mkdir()
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    *_config_args(config),
                    "--child-policy", policy,
                    "--child-seed", str(seed),
                    "--child-order-position", str(order_position),
                    "--child-attempt-id", attempt_id,
                    "--child-trace", str(trace_path),
                    "--child-warmup-texts", str(warmup_path),
                    "--child-warmup-hash", warmup_identity["sha256"],
                    "--child-control-dir", str(control_dir),
                    "--child-trace-hash", trace_hash,
                    "--child-semantic-index", str(semantic_index_path),
                    "--child-semantic-index-hash", semantic_index_identity["sha256"],
                    "--child-result", str(result_path),
                    "--child-model-sha256", assets["model_sha256"],
                    "--child-source-snapshot", str(source_snapshot_path),
                ]
                bootstrap_child = (
                    config.mode == "full"
                    or getattr(sys, "_gate7_v2_bootstrap_attestation", None)
                    is not None
                )
                if config.mode == "full":
                    command.extend(
                        [
                            "--child-dependency-attestation-sha256",
                            str(
                                failure_context["dependency_attestation"][
                                    "attestation_sha256"
                                ]
                            ),
                        ]
                    )
                if config.mode == "full":
                    command.extend(
                        ["--child-head-commit", str(git_state["head_commit"])]
                    )
                if assets["model_path"]:
                    command.extend(
                        [
                            "--child-model-path",
                            assets["model_path"],
                            "--child-model-files-json",
                            json.dumps(
                                assets["model_files"],
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            "--child-model-digest-sha256",
                            assets["model_digest_sha256"],
                        ]
                    )
                if assets["tokenizer_path"]:
                    command.extend(
                        [
                            "--child-tokenizer-path",
                            assets["tokenizer_path"],
                            "--child-tokenizer-files-json",
                            json.dumps(
                                assets["tokenizer_files"],
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            "--child-tokenizer-digest-sha256",
                            assets["tokenizer_digest_sha256"],
                        ]
                    )
                if bootstrap_child:
                    command = [
                        sys.executable,
                        "-S",
                        "-P",
                        str(ISOLATED_BOOTSTRAP),
                        "--role",
                        "child",
                        "--",
                        *command[1:],
                    ]
                samples: List[Dict[str, Any]] = []
                try:
                    _assert_source_snapshot(
                        source_snapshot,
                        str(git_state["head_commit"])
                        if config.mode == "full"
                        else None,
                    )
                    result, samples = _run_child_with_sampling(
                        command,
                        result_path,
                        attempt_id,
                        run_id,
                        seed,
                        policy,
                        config.resource_interval_ms,
                        control_dir,
                        config.mode == "full",
                        bootstrap_child,
                    )
                    supervisor_capture = result.pop("_supervisor_capture", {})
                    failure_context["child_stdout"] = supervisor_capture.get(
                        "stdout"
                    )
                    failure_context["child_stderr"] = supervisor_capture.get(
                        "stderr"
                    )
                    failure_context["current_samples"] = samples
                    _assert_source_snapshot(
                        source_snapshot,
                        str(git_state["head_commit"])
                        if config.mode == "full"
                        else None,
                    )
                except BaseException as error:
                    failure_context["current_samples"] = (
                        error.samples
                        if isinstance(error, ChildExecutionError)
                        else samples
                    )
                    diagnostic = (
                        error.diagnostic
                        if isinstance(error, ChildExecutionError)
                        else None
                    )
                    if (
                        isinstance(diagnostic, dict)
                        and diagnostic.get("attempt_id") == attempt_id
                        and diagnostic.get("run_id") == run_id
                        and diagnostic.get("seed") == seed
                        and diagnostic.get("policy") == policy
                        and isinstance(diagnostic.get("completed_requests"), list)
                        and diagnostic.get("completed_request_count")
                        == len(diagnostic["completed_requests"])
                    ):
                        completed_prefix = [
                            dict(request)
                            for request in diagnostic["completed_requests"]
                            if isinstance(request, dict)
                        ]
                        failure_context["diagnostic_requests"] = (
                            request_records + completed_prefix
                        )
                        failure_context["child_diagnostic"] = diagnostic
                        failure_context["structural_summary"] = {
                            "schema_version": SCHEMA_VERSION,
                            "experiment_id": EXPERIMENT_ID,
                            "structural_valid": False,
                            "structural_failure_requests": 1,
                            "structural_failure_flags_total": 1,
                            "structural_failure_reason_counts": {
                                "child_execution_failure": 1
                            },
                            "storage_failure": str(error),
                        }
                    raise
                summary = result["summary"]
                if summary["run_id"] != run_id:
                    raise RuntimeError("child run identity differs from orchestrator")
                if summary["trace_hash"] != trace_hash:
                    raise RuntimeError("child trace identity differs from orchestrator")
                if (
                    summary.get("semantic_index_sha256")
                    != semantic_index.sha256
                    or result.get("semantic_index_sha256")
                    != semantic_index.sha256
                    or summary.get("semantic_index_artifact_sha256")
                    != semantic_index_identity["sha256"]
                    or result.get("semantic_index_artifact_sha256")
                    != semantic_index_identity["sha256"]
                ):
                    raise RuntimeError(
                        "child semantic-index identity differs from orchestrator"
                    )
                if int(summary["requests"]) != config.requests:
                    raise RuntimeError("child request count differs from orchestrator")
                if int(summary["order_position"]) != order_position:
                    raise RuntimeError("child policy order differs from the orchestrator")
                if (
                    summary.get("warmup_artifact_sha256")
                    != warmup_identity["sha256"]
                    or result.get("warmup_artifact")
                    != _build_warmup_artifact(
                        trace,
                        metadata["hot_text_ids"],
                        seed,
                        trace_hash,
                        config.warmup_requests,
                    )
                ):
                    raise RuntimeError("child warm-up evidence differs from orchestrator")
                if config.mode == "full":
                    expected_dependency_digest = failure_context[
                        "dependency_attestation"
                    ]["attestation_sha256"]
                    expected_environment_digest = failure_context[
                        "dependency_attestation"
                    ]["environment"]["environment_sha256"]
                    expected_imported_origins = failure_context[
                        "dependency_attestation"
                    ]["installed"]["imported_origins"]
                    expected_origins_digest = _canonical_mapping_sha256(
                        expected_imported_origins
                    )
                    if (
                        result.get("dependency_attestation_sha256")
                        != expected_dependency_digest
                        or summary.get("dependency_attestation_sha256")
                        != expected_dependency_digest
                        or summary.get("dependency_attestation_pre_verified")
                        is not True
                        or summary.get("dependency_attestation_post_verified")
                        is not True
                        or result.get("environment_sha256")
                        != expected_environment_digest
                        or summary.get("environment_sha256")
                        != expected_environment_digest
                        or result.get("imported_module_origins_pre")
                        != expected_imported_origins
                        or result.get("imported_module_origins_post")
                        != expected_imported_origins
                        or summary.get("imported_origins_pre_sha256")
                        != expected_origins_digest
                        or summary.get("imported_origins_post_sha256")
                        != expected_origins_digest
                    ):
                        raise RuntimeError(
                            "child dependency attestation differs from orchestrator"
                        )
                    child_imported_module_origins[run_id] = {
                        "pre": result["imported_module_origins_pre"],
                        "post": result["imported_module_origins_post"],
                        "sha256": expected_origins_digest,
                    }
                child_bootstrap = result.get("bootstrap_attestation")
                if bootstrap_child:
                    if not isinstance(child_bootstrap, dict):
                        raise RuntimeError("bootstrapped child omitted its attestation")
                    child_bootstrap = _validated_bootstrap_observation(
                        child_bootstrap, "child"
                    )
                    child_bootstrap_digest = child_bootstrap[
                        "attestation_sha256"
                    ]
                    parent_bootstrap = _bootstrap_attestation("parent")
                    if (
                        summary.get("bootstrap_attestation_sha256")
                        != child_bootstrap_digest
                        or _bootstrap_common_observation(child_bootstrap)
                        != _bootstrap_common_observation(parent_bootstrap)
                    ):
                        raise RuntimeError(
                            "child bootstrap attestation is malformed"
                        )
                    child_bootstrap_attestations[
                        str(child_bootstrap_digest)
                    ] = child_bootstrap
                elif child_bootstrap is not None:
                    raise RuntimeError(
                        "direct smoke child unexpectedly returned bootstrap evidence"
                    )
                if int(summary["child_exit_status"]) != 0:
                    raise RuntimeError("successful child recorded a nonzero exit status")
                if any(
                    int(sample["child_pid"]) != int(summary["child_pid"])
                    for sample in samples
                ):
                    raise RuntimeError("resource samples identify the wrong child process")
                if not config.fake_embedding and result.get("providers") != [
                    "CPUExecutionProvider"
                ]:
                    raise RuntimeError("child used an unexpected ONNX provider")
                if not config.fake_embedding:
                    expected_child_assets = {
                        "pre_verified": True,
                        "post_verified": True,
                        "model_files": assets["model_files"],
                        "model_digest_sha256": assets[
                            "model_digest_sha256"
                        ],
                        "tokenizer_files": assets["tokenizer_files"],
                        "tokenizer_digest_sha256": assets[
                            "tokenizer_digest_sha256"
                        ],
                    }
                    if result.get("asset_integrity") != expected_child_assets:
                        raise RuntimeError(
                            "child ONNX asset evidence differs from orchestrator"
                        )
                    if (
                        summary.get("asset_integrity_pre_verified") is not True
                        or summary.get("asset_integrity_post_verified") is not True
                        or summary.get("model_asset_digest_sha256")
                        != assets["model_digest_sha256"]
                        or summary.get("tokenizer_asset_digest_sha256")
                        != assets["tokenizer_digest_sha256"]
                    ):
                        raise RuntimeError(
                            "child ONNX asset summary is incomplete"
                        )
                if config.mode == "full" and int(summary["embedding_dimension"]) != 768:
                    raise RuntimeError("formal embedding dimension is not 768")
                formal_samples = [sample for sample in samples if sample["scope"] == "formal"]
                if len(formal_samples) < 2:
                    raise RuntimeError("resource supervisor retained fewer than two formal samples")
                rss_values = [int(sample["rss_bytes"]) for sample in formal_samples]
                uss_values = [
                    int(sample["uss_bytes"])
                    for sample in formal_samples
                    if sample["uss_bytes"] is not None
                ]
                sampled_peak = max(rss_values)
                summary["rss_peak_sampled_bytes"] = sampled_peak
                summary["rss_peak_bytes"] = sampled_peak
                summary["rss_start_bytes"] = rss_values[0]
                summary["rss_end_bytes"] = rss_values[-1]
                summary["rss_end_minus_start_bytes"] = (
                    rss_values[-1] - rss_values[0]
                )
                summary["rss_mean_bytes"] = round(sum(rss_values) / len(rss_values), 3)
                summary["uss_peak_bytes"] = max(uss_values) if uss_values else None
                summary["uss_mean_bytes"] = (
                    round(sum(uss_values) / len(uss_values), 3) if uss_values else None
                )
                summary["uss_start_bytes"] = uss_values[0] if uss_values else None
                summary["uss_end_bytes"] = uss_values[-1] if uss_values else None
                summary["uss_incremental_peak_bytes"] = (
                    max(0, max(uss_values) - uss_values[0]) if uss_values else None
                )
                summary["uss_end_minus_start_bytes"] = (
                    uss_values[-1] - uss_values[0] if uss_values else None
                )
                summary["rss_incremental_peak_bytes"] = max(
                    0,
                    int(summary["rss_peak_bytes"]) - int(summary["rss_start_bytes"]),
                )
                summary["cpu_user_lifecycle_seconds"] = summary["cpu_user_seconds"]
                summary["cpu_system_lifecycle_seconds"] = summary[
                    "cpu_system_seconds"
                ]
                cpu_user_delta = max(
                    0.0,
                    float(formal_samples[-1]["cpu_user_seconds"])
                    - float(formal_samples[0]["cpu_user_seconds"]),
                )
                cpu_system_delta = max(
                    0.0,
                    float(formal_samples[-1]["cpu_system_seconds"])
                    - float(formal_samples[0]["cpu_system_seconds"]),
                )
                resource_window_seconds = max(
                    0.0,
                    (
                        int(formal_samples[-1]["monotonic_ns"])
                        - int(formal_samples[0]["monotonic_ns"])
                    )
                    / 1e9,
                )
                summary["cpu_user_seconds"] = round(cpu_user_delta, 9)
                summary["cpu_system_seconds"] = round(cpu_system_delta, 9)
                summary["cpu_total_seconds"] = round(
                    cpu_user_delta + cpu_system_delta, 9
                )
                summary["resource_window_seconds"] = round(
                    resource_window_seconds, 9
                )
                summary["cpu_utilization_percent"] = (
                    round(
                        100.0
                        * (cpu_user_delta + cpu_system_delta)
                        / resource_window_seconds,
                        6,
                    )
                    if resource_window_seconds > 0
                    else None
                )
                io_supported = all(
                    sample["io_counters_available"] for sample in formal_samples
                )
                for field in (
                    "io_read_count",
                    "io_write_count",
                    "io_read_bytes",
                    "io_write_bytes",
                ):
                    summary["%s_delta" % field] = (
                        max(
                            0,
                            int(formal_samples[-1][field])
                            - int(formal_samples[0][field]),
                        )
                        if io_supported
                        else None
                    )
                observed_structural = _structural_summary(
                    result["requests"], summary.get("storage_failure")
                )
                if result.get("structural") != observed_structural:
                    raise RuntimeError(
                        "child structural diagnostics differ from request evidence"
                    )
                failure_context["diagnostic_summaries"] = rows + [summary]
                failure_context["diagnostic_requests"] = (
                    request_records + list(result["requests"])
                )
                failure_context["structural_summary"] = observed_structural
                if not observed_structural["structural_valid"]:
                    raise RuntimeError(
                        "Gate 7 v2 child has structural failures: %r"
                        % observed_structural["structural_failure_reason_counts"]
                    )
                rows.append(summary)
                request_records.extend(result["requests"])
                resource_records.extend(samples)
                failure_context["diagnostic_summaries"] = rows
                failure_context["diagnostic_requests"] = request_records
                failure_context["structural_summary"] = None
                failure_context["current_samples"] = []
                failure_context["child_stdout"] = None
                failure_context["child_stderr"] = None
                seed_embedding_digests.add(summary["embedding_digest_sha256"])
            if len(seed_embedding_digests) != 1:
                raise RuntimeError("policies produced different embedding digests for one trace")

    _assert_retained_inputs(
        output_dir, retained_inputs, dependency_attestation
    )
    if config.mode == "full":
        end_power = _power_observation()
        _require_not_observed_unplugged(end_power, "end observation")
        power_observations = failure_context["power_observations"]
        power_observations["end"] = end_power
        if (
            power_observations["pre_start"].get("available") is False
            or end_power.get("available") is False
        ):
            power_observations["availability_limitation"] = (
                "at least one endpoint power observation was unavailable; "
                "uninterrupted AC cannot be attested"
            )
    _assert_source_snapshot(
        source_snapshot,
        str(git_state["head_commit"]) if config.mode == "full" else None,
    )
    end_git_state = _git_state()
    runs_path = output_dir / "runs.csv"
    requests_path = output_dir / "requests.jsonl"
    resources_path = output_dir / "resources.jsonl"
    outcome_latency_path = output_dir / "outcome-latency.jsonl"
    manifest_path = output_dir / "manifest.json"
    _write_csv(runs_path, rows, _runs_fields())
    _write_jsonl(requests_path, request_records)
    _write_jsonl(resources_path, resource_records)
    outcome_latency_records = _outcome_latency_records(request_records, rows)
    _write_jsonl(outcome_latency_path, outcome_latency_records)

    _assert_source_snapshot(
        source_snapshot,
        str(git_state["head_commit"]) if config.mode == "full" else None,
    )
    end_git_state = _git_state()

    completed_at_utc = datetime.now(timezone.utc)
    semantic_totals = {
        field: sum(int(row[field]) for row in rows)
        for field in (
            "same_concept_hits",
            "direct_negative_hits",
            "component_derived_negative_hits",
            "unlabeled_cross_concept_hits",
            "unresolved_provenance_hits",
        )
    }
    semantic_status = _semantic_guardrail_status(
        {**semantic_totals, "hits": sum(int(row["hits"]) for row in rows)}
    )
    expected_run_count = len(seeds) * len(policies)
    matrix_complete = len(rows) == expected_run_count
    all_structurally_valid = matrix_complete and all(
        bool(row["structural_valid"]) for row in rows
    )
    artifacts = {
        "runs.csv": {
            "path": "runs.csv",
            **_artifact_identity(runs_path, rows=len(rows)),
        },
        "requests.jsonl": {
            "path": "requests.jsonl",
            **_artifact_identity(requests_path, rows=len(request_records)),
        },
        "resources.jsonl": {
            "path": "resources.jsonl",
            **_artifact_identity(resources_path, rows=len(resource_records)),
        },
        "outcome-latency.jsonl": {
            "path": "outcome-latency.jsonl",
            **_artifact_identity(
                outcome_latency_path, rows=len(outcome_latency_records)
            ),
        },
        **trace_artifacts,
        **semantic_index_artifacts,
        **warmup_artifacts,
        **_retained_source_artifacts(output_dir),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "attempt_id": attempt_id,
        "kind": "prospective_gate7_followup",
        "started_at_utc": started_at_utc.isoformat(),
        "completed_at_utc": completed_at_utc.isoformat(),
        "elapsed_seconds": round(
            (completed_at_utc - started_at_utc).total_seconds(), 6
        ),
        "prior_attempt_exists": bool(prior_attempts),
        "prior_attempts": prior_attempts,
        "attempt_policy": {
            "formal_root": _project_path(FORMAL_ATTEMPT_ROOT),
            "directory": output_dir.name,
            "eligibility": "first structurally valid complete attempt in the retained predecessor chain",
            "rerun_scope": "complete five-seed, three-policy matrix under a new attempt ID",
        },
        "attempt_ledger": failure_context.get("attempt_ledger"),
        "v1_lineage": {
            **_v1_preservation_identity(),
            "relationship": (
                "method-correcting successor; v1 remains invalid and is not "
                "reclassified"
            ),
        },
        "historical_gate7_preserved": True,
        "historical_artifacts_modified": False,
        "precomputed_embeddings": False,
        "embedding_in_request_path": True,
        "gptcache_adapter_path": True,
        "actual_onnx": not config.fake_embedding,
        "formal_claimable_mode": False,
        "contract": {
            "path": _project_path(contract_path),
            **contract_identity,
        },
        "git": {
            **git_state,
            "end_head_commit": end_git_state.get("head_commit"),
            "end_worktree_dirty": end_git_state.get("worktree_dirty"),
            "source_snapshot_unchanged": _source_identities() == source_snapshot,
            "baseline_commit": HISTORICAL_BASELINE_COMMIT,
        },
        "source_identities": source_snapshot,
        "source_snapshot_sha256": failure_context.get(
            "source_snapshot_sha256"
        ),
        "formal_source_anchor": failure_context.get("formal_source_anchor"),
        "dependency_attestation": failure_context.get(
            "dependency_attestation"
        ),
        "environment_sha256": (
            failure_context["dependency_attestation"]["environment"].get(
                "environment_sha256"
            )
            if isinstance(failure_context.get("dependency_attestation"), dict)
            else None
        ),
        "entrypoint_attestation": failure_context.get(
            "entrypoint_attestation"
        ),
        "power_observations": failure_context.get("power_observations"),
        "retained_inputs": failure_context.get("retained_inputs"),
        "environment": _environment(),
        "config": asdict(config),
        "seeds": list(seeds),
        "policies": list(POLICIES if config.mode == "full" else policies),
        "policy_order_namespace": POLICY_ORDER_NAMESPACE,
        "planned_policy_orders": {
            str(seed): [policy for policy in schedule[seed] if policy in policies]
            for seed in sorted(seeds)
        },
        "policy_orders": actual_orders,
        "process_isolation": "one fresh child process and TemporaryDirectory per seed/policy",
        "storage_isolation": "fresh SQLite database and FAISS index per seed/policy",
        "trace_source": (
            "Gate 7 v2 frozen QQP trace and selected-component semantic index; "
            "held-out rows excluded"
        ),
        "trace_metadata": trace_metadata,
        "trace_artifacts": trace_artifacts,
        "semantic_index_artifacts": semantic_index_artifacts,
        "warmup_artifacts": warmup_artifacts,
        "child_bootstrap_attestations": child_bootstrap_attestations,
        "child_imported_module_origins": child_imported_module_origins,
        "semantic_guardrail": {
            "status": semantic_status,
            "rule": (
                "fail on any direct or component-derived labeled-negative hit; "
                "pending on unlabeled cross-component hits; otherwise pass"
            ),
            "disjoint_hit_counts": semantic_totals,
            "labeled_negative_hits": (
                semantic_totals["direct_negative_hits"]
                + semantic_totals["component_derived_negative_hits"]
            ),
            "does_not_change_gate7_system_adjudication": True,
        },
        "gate2_v2": gate2_v2,
        "gate7_operating_point": {
            "frozen_similarity_threshold": 0.97,
            "run_similarity_threshold": config.hit_threshold,
            "qualification": (
                "unqualified frozen systems operating point; Gate 2 v2 did not "
                "select or qualify a threshold"
            ),
            "derived_from_gate2_v2": False,
        },
        "gate7_systems_status_inputs": {
            "expected_run_count": expected_run_count,
            "observed_run_count": len(rows),
            "matrix_complete": matrix_complete,
            "all_children_structurally_valid": all_structurally_valid,
            "actual_onnx_required_for_formal_claim": True,
            "actual_onnx": not config.fake_embedding,
            "numerical_adjudication_source": "independent Gate 7 v2 auditor",
            "semantic_guardrail_is_separate": True,
        },
        "model": {
            key: value
            for key, value in assets.items()
            if key not in ("model_path", "tokenizer_path")
        },
        "onnx": {
            "provider": assets["provider"],
            "intra_op_threads": config.onnx_intra_threads,
            "inter_op_threads": config.onnx_inter_threads,
            "max_length": config.max_length,
            "warmup_requests": config.warmup_requests,
            "warmup_rule": "first hot text IDs in canonical order; cycle only in development smoke mode",
            "model_load_and_warmup_in_request_timing": False,
            "provider_verified_each_child": all(
                bool(row["provider_verified"]) for row in rows
            ),
            "embedding_norm_verified_each_request": all(
                bool(row["embedding_norm_verified"]) for row in rows
            ),
        },
        "gptcache": {
            "entrypoint": "gptcache.adapter.adapter.adapt",
            "top_k": 1,
            "similarity": "cosine reconstructed from normalized FAISS squared L2",
            "similarity_threshold": config.hit_threshold,
            "auto_flush": 20,
            "token_counter": False,
            "data_check": False,
            "report_persistence": False,
            "miss_handler": (
                "deterministic recorded-response-v2 payload carrying independent "
                "concept and source-text provenance; no network or live LLM"
            ),
        },
        "timing_scope": {
            "end_to_end": (
                "raw text entering GPTCache adapt until adapt returns after "
                "response materialization"
            ),
            "embedding": "tokenization, ONNX inference, mean pooling, and normalization",
            "post_embedding": "GPTCache adapter, SQLite/FAISS lookup or save, similarity, policy, and response materialization after embedding",
            "policy_exclusive": "put/put_with_metadata/get excluding nested SQLite/FAISS work",
            "policy_inclusive": "outer policy calls including synchronous nested cleanup",
            "response_return": "local recorded JSON response conversion plus propagation from the final response callback through adapt return",
            "post_return_evidence": (
                "provenance resolution, semantic classification, structural "
                "checks, embedding evidence derivation, and post-loop asset "
                "rehashing occur after adapter return and are excluded from "
                "service latency but required before evidence acceptance"
            ),
            "residual": "orchestration, normalization, conversions, and timer overhead not assigned elsewhere",
            "request_logging": "buffered and written after each child exits; excluded from request timing",
            "outcome_categories": list(OUTCOME_NAMES),
            "outcome_report_rows": list(OUTCOME_REPORT_NAMES),
            "quantiles": "nearest rank: one-based ceil(q*N) over raw samples",
        },
        "resource_measurement": {
            "method": "external parent psutil sampling plus child OS high-water RSS",
            "interval_ms": config.resource_interval_ms,
            "formal_peak": "maximum mandatory or periodic external sampled RSS",
            "os_high_water_role": "diagnostic only; includes process startup before the formal window",
            "incremental_baseline": "child RSS after model warmup and before manager construction",
            "maximum_valid_sample_gap_ms": 2 * config.resource_interval_ms,
            "fixed_buffers_reserved_before_start": True,
        },
        "adjudication": {
            "experimental_unit": "paired seed",
            "aggregation": "every one of five seeds must satisfy every bound",
            "formal_comparator": "LRU",
            "secondary_comparator": "LFU",
            "p95_ratio_max": 1.25,
            "p95_delta_us_max": 500.0,
            "throughput_ratio_min": 0.90,
            "rss_ratio_max": 1.20,
            "rss_delta_bytes_max": 64 * 1024 * 1024,
        },
        "run_summaries": rows,
        "artifacts": artifacts,
    }
    # This is the final pre-publication integrity gate.  It deliberately runs
    # after environment collection and manifest construction so the atomic
    # manifest publication is based on the immediately observed source,
    # contract, and Git state rather than an earlier snapshot.
    publication_sources = _source_identities()
    publication_contract = _artifact_identity(contract_path)
    publication_git = _git_state()
    publication_dependency: Optional[Dict[str, Any]] = None
    publication_anchor: Optional[Dict[str, Any]] = None
    publication_tag_payload: Optional[bytes] = None
    publication_entrypoint: Optional[Dict[str, Any]] = None
    publication_bootstrap_runtime: Optional[Dict[str, Any]] = None
    if config.mode == "full":
        publication_dependency = _dependency_attestation()
        publication_anchor, publication_tag_payload = _capture_formal_source_anchor(
            publication_contract, str(git_state["head_commit"])
        )
        publication_entrypoint = _formal_entrypoint_attestation()
        publication_bootstrap_runtime = _bootstrap_runtime_unchanged("parent")
        _assert_retained_inputs(
            output_dir, retained_inputs, dependency_attestation
        )
    sources_unchanged = publication_sources == source_snapshot
    contract_unchanged = publication_contract == contract_identity
    git_unchanged = (
        publication_git.get("head_commit") == git_state.get("head_commit")
        and publication_git.get("worktree_dirty") is False
    )
    dependency_unchanged = publication_dependency == dependency_attestation
    anchor_unchanged = (
        publication_anchor == formal_source_anchor
        and publication_tag_payload == formal_tag_payload
    )
    entrypoint_unchanged = publication_entrypoint == entrypoint_attestation
    bootstrap_runtime_unchanged = (
        publication_bootstrap_runtime
        == (
            entrypoint_attestation.get("bootstrap_attestation")
            if isinstance(entrypoint_attestation, dict)
            else None
        )
    )
    formal_claimable = (
        config.mode == "full"
        and not config.fake_embedding
        and sources_unchanged
        and contract_unchanged
        and git_state.get("worktree_dirty") is False
        and git_unchanged
        and dependency_unchanged
        and anchor_unchanged
        and entrypoint_unchanged
        and bootstrap_runtime_unchanged
    )
    if config.mode == "full" and not formal_claimable:
        raise RuntimeError(
            "formal Gate 7 source, contract, Git, dependency, anchor, or entrypoint changed before manifest publication"
        )
    manifest["formal_claimable_mode"] = formal_claimable
    manifest["git"]["end_head_commit"] = publication_git.get("head_commit")
    manifest["git"]["end_worktree_dirty"] = publication_git.get(
        "worktree_dirty"
    )
    manifest["git"]["source_snapshot_unchanged"] = sources_unchanged
    manifest["contract"]["publication_identity_unchanged"] = contract_unchanged
    manifest["publication_integrity"] = {
        "source_snapshot_unchanged": sources_unchanged,
        "contract_unchanged": contract_unchanged,
        "git_unchanged": git_unchanged,
        "dependency_attestation_unchanged": dependency_unchanged,
        "formal_source_anchor_unchanged": anchor_unchanged,
        "entrypoint_attestation_unchanged": entrypoint_unchanged,
        "bootstrap_runtime_unchanged": bootstrap_runtime_unchanged,
        "retained_inputs_unchanged": True,
    }
    _write_json(manifest_path, manifest)
    failure_context["completed"] = True
    return manifest


def _run_preterminal_audit(output_dir: Path) -> Tuple[Dict[str, Any], int]:
    """Run the independent auditor before the formal TERMINAL ledger event."""

    output_dir = Path(output_dir).resolve()
    report_path = output_dir / "gate7-preterminal-adjudication.json"
    if report_path.exists():
        raise RuntimeError("formal preterminal adjudication path already exists")
    auditor_path = PROJECT_ROOT / "benchmarks" / "carma" / "gate7_v2_audit.py"
    command = [
        sys.executable,
        "-S",
        "-P",
        str(ISOLATED_BOOTSTRAP),
        "--role",
        "auditor",
        "--",
        str(auditor_path),
        str(output_dir),
        "--output",
        str(report_path),
        "--preterminal",
    ]
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_environment(True),
        check=False,
    )
    expected_status = {0: "pass", 1: "fail", 2: "pending", 3: "invalid"}
    if completed.returncode not in expected_status:
        raise RuntimeError(
            "independent preterminal auditor failed with exit %d: %s"
            % (completed.returncode, completed.stderr.strip())
        )
    if not report_path.is_file():
        raise RuntimeError("independent preterminal auditor wrote no report")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "independent preterminal auditor report is unreadable"
        ) from exc
    status = expected_status[completed.returncode]
    if (
        not isinstance(report, dict)
        or report.get("status") != status
        or report.get("claimable") != (status in ("pass", "fail"))
        or not isinstance(report.get("auditor_identity"), dict)
    ):
        raise RuntimeError(
            "independent preterminal auditor report contradicts its exit status"
        )
    auditor_bootstrap = _validated_bootstrap_observation(
        report.get("bootstrap_attestation"), "auditor"
    )
    if (
        report.get("bootstrap_attestation_sha256")
        != auditor_bootstrap["attestation_sha256"]
    ):
        raise RuntimeError(
            "independent preterminal auditor bootstrap digest is inconsistent"
        )
    try:
        manifest = json.loads(
            (output_dir / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("formal manifest is unreadable after audit") from exc
    entrypoint = manifest.get("entrypoint_attestation")
    parent_bootstrap = (
        entrypoint.get("bootstrap_attestation")
        if isinstance(entrypoint, dict)
        else None
    )
    parent_bootstrap = _validated_bootstrap_observation(
        parent_bootstrap, "parent"
    )
    common_bootstrap = _bootstrap_common_observation(parent_bootstrap)
    children = manifest.get("child_bootstrap_attestations")
    if not isinstance(children, dict) or not children:
        raise RuntimeError("formal manifest omits child bootstrap observations")
    for declared_digest, raw_child in children.items():
        child = _validated_bootstrap_observation(raw_child, "child")
        if (
            declared_digest != child["attestation_sha256"]
            or _bootstrap_common_observation(child) != common_bootstrap
        ):
            raise RuntimeError(
                "formal child bootstrap observation differs from the parent"
            )
    if _bootstrap_common_observation(auditor_bootstrap) != common_bootstrap:
        raise RuntimeError(
            "formal auditor bootstrap observation differs from the producer"
        )
    expected_auditor_identity = {
        "path": "benchmarks/carma/gate7_v2_audit.py",
        **_artifact_identity(
            PROJECT_ROOT / "benchmarks" / "carma" / "gate7_v2_audit.py"
        ),
    }
    if report.get("auditor_identity") != expected_auditor_identity:
        raise RuntimeError(
            "independent preterminal auditor did not identify its exact source bytes"
        )
    return report, int(completed.returncode)


def run_bundle(
    prepared_dir: Path,
    output_dir: Path,
    contract_path: Path,
    seeds: Sequence[int],
    policies: Sequence[str],
    config: Gate7Config,
) -> Dict[str, Any]:
    """Run one attempt and retain any post-start failure at attempt scope."""

    failure_context: Dict[str, Any] = {}
    if config.mode == "full" and hasattr(
        sys, "_gate7_v2_terminal_intent"
    ):
        delattr(sys, "_gate7_v2_terminal_intent")
    with _formal_attempt_lock(config.mode == "full"):
        try:
            manifest = _run_bundle_impl(
                prepared_dir,
                output_dir,
                contract_path,
                seeds,
                policies,
                config,
                failure_context,
            )
            if config.mode == "full":
                preterminal_report, audit_exit_status = _run_preterminal_audit(
                    Path(output_dir)
                )
                _assert_source_snapshot(
                    failure_context["source_snapshot"],
                    str(failure_context["git_state"]["head_commit"]),
                )
                if _artifact_identity(Path(contract_path).resolve()) != dict(
                    failure_context["contract_identity"]
                ):
                    raise RuntimeError(
                        "formal Gate 7 contract changed during preterminal audit"
                    )
                # This is the final in-target runtime gate.  It intentionally
                # follows the independent auditor and prepares only a terminal
                # intent.  The parent bootstrap must repeat its private
                # post-target seal before it can append a claimable TERMINAL.
                terminal_dependency = _dependency_attestation()
                terminal_contract = _artifact_identity(
                    Path(contract_path).resolve()
                )
                terminal_git = _git_state()
                terminal_anchor, terminal_tag_payload = (
                    _capture_formal_source_anchor(
                        terminal_contract,
                        str(failure_context["git_state"]["head_commit"]),
                    )
                )
                terminal_entrypoint = _formal_entrypoint_attestation()
                _assert_retained_inputs(
                    Path(output_dir),
                    failure_context["retained_inputs"],
                    failure_context["dependency_attestation"],
                )
                if (
                    terminal_dependency
                    != failure_context["dependency_attestation"]
                    or terminal_contract
                    != failure_context["contract_identity"]
                    or terminal_git != failure_context["git_state"]
                    or terminal_git.get("worktree_dirty") is not False
                    or terminal_anchor
                    != failure_context["formal_source_anchor"]
                    or terminal_tag_payload
                    != (
                        Path(output_dir) / FORMAL_SOURCE_TAG_PAYLOAD_PATH
                    ).read_bytes()
                    or terminal_entrypoint
                    != failure_context["entrypoint_attestation"]
                ):
                    raise RuntimeError(
                        "formal Gate 7 runtime changed before successful TERMINAL"
                    )
                terminal_runtime = _bootstrap_runtime_unchanged("parent")
                if terminal_runtime != failure_context[
                    "entrypoint_attestation"
                ]["bootstrap_attestation"]:
                    raise RuntimeError(
                        "formal bootstrap observation changed before TERMINAL"
                    )
                terminal_intent = _prepare_formal_terminal_intent(
                    Path(output_dir),
                    preterminal_report,
                    audit_exit_status,
                )
                # Use a canonical JSON round trip so the bootstrap receives an
                # independent deep dict rather than aliases into target state.
                sys._gate7_v2_terminal_intent = json.loads(  # type: ignore[attr-defined]
                    _canonical_json_bytes(terminal_intent).decode("utf-8")
                )
                # Transient return-only metadata: manifest.json and the
                # preterminal report are immutable inputs to the intent.
                manifest["_preterminal_audit_exit_status"] = audit_exit_status
            return manifest
        except BaseException as error:
            if config.mode == "full" and hasattr(
                sys, "_gate7_v2_terminal_intent"
            ):
                delattr(sys, "_gate7_v2_terminal_intent")
            output = failure_context.get("output_dir")
            registered_start: Optional[Dict[str, Any]] = None
            if (
                config.mode == "full"
                and isinstance(output, Path)
                and output.is_dir()
                and isinstance(failure_context.get("attempt_id"), str)
            ):
                registered_start = _formal_start_entry(
                    output, str(failure_context["attempt_id"])
                )
                if registered_start is None:
                    if failure_context.get("attempt_directory_created_by_run") is True:
                        _quarantine_unregistered_formal_attempt(
                            output,
                            str(failure_context["attempt_id"]),
                            error,
                        )
                    # No START exists, so this preflight failure must never be
                    # converted into a canonical failure TERMINAL.
                    raise
                if not isinstance(failure_context.get("attempt_ledger"), dict):
                    failure_context["attempt_ledger"] = _ledger_prefix_identity(
                        output.parent, registered_start
                    )
            if (
                isinstance(output, Path)
                and output.is_dir()
                and not (output / "manifest.json").exists()
                and not (output / "attempt-failure.json").exists()
                and (
                    config.mode != "full"
                    or registered_start is not None
                )
            ):
                retained_samples = list(failure_context.get("resource_records", []))
                retained_samples.extend(failure_context.get("current_samples", []))
                power_observations = failure_context.get("power_observations")
                if (
                    config.mode == "full"
                    and isinstance(power_observations, dict)
                    and power_observations.get("end") is None
                ):
                    power_observations["end"] = _power_observation()
                    if (
                        power_observations.get("pre_start", {}).get("available")
                        is False
                        or power_observations["end"].get("available") is False
                    ):
                        power_observations["availability_limitation"] = (
                            "at least one endpoint power observation was unavailable; "
                            "uninterrupted AC cannot be attested"
                        )
                _retain_attempt_failure(
                    output,
                    str(failure_context["attempt_id"]),
                    failure_context["started_at_utc"],
                    int(failure_context.get("seed", -1)),
                    str(failure_context.get("policy", "unknown")),
                    int(failure_context.get("order_position", -1)),
                    error,
                    retained_samples,
                    failure_context["config"],
                    failure_context["contract_identity"],
                    failure_context["git_state"],
                    failure_context["source_snapshot"],
                    failure_context.get("prior_attempts", []),
                    failure_context.get("attempt_ledger"),
                    failure_context.get("child_stdout"),
                    failure_context.get("child_stderr"),
                    failure_context.get("diagnostic_summaries", ()),
                    failure_context.get("diagnostic_requests", ()),
                    failure_context.get("structural_summary"),
                    failure_context.get("child_diagnostic"),
                    failure_context.get("dependency_attestation"),
                    failure_context.get("formal_source_anchor"),
                    failure_context.get("source_snapshot_sha256"),
                    failure_context.get("entrypoint_attestation"),
                    power_observations,
                    failure_context.get("retained_inputs"),
                )
                if config.mode == "full":
                    _append_formal_attempt_terminal(
                        output, "attempt-failure.json"
                    )
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the prospective real-text ONNX Gate 7 benchmark."
    )
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--prepared", type=Path, default=DEFAULT_PREPARED)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--seeds", nargs="+", type=int, default=None)
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--requests", type=int, default=None)
    parser.add_argument("--capacity", type=int, default=None)
    parser.add_argument("--hit-threshold", type=float, default=0.97)
    parser.add_argument("--topic-threshold", type=float, default=0.70)
    parser.add_argument("--cell-threshold", type=float, default=0.97)
    parser.add_argument("--demand-half-life", type=float, default=500.0)
    parser.add_argument("--quota-strength", type=float, default=1.0)
    parser.add_argument("--ghost-support-threshold", type=float, default=1.5)
    parser.add_argument("--admission-margin", type=float, default=1.05)
    parser.add_argument("--centroid-alpha", type=float, default=0.05)
    parser.add_argument("--entry-hit-weight", type=float, default=0.25)
    parser.add_argument("--onnx-intra-threads", type=int, default=1)
    parser.add_argument("--onnx-inter-threads", type=int, default=1)
    parser.add_argument("--warmup-requests", type=int, default=20)
    parser.add_argument("--resource-interval-ms", type=int, default=100)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--fake-embedding", action="store_true")
    parser.add_argument("--child-policy", choices=POLICIES, help=argparse.SUPPRESS)
    parser.add_argument("--child-seed", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--child-order-position", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--child-attempt-id", help=argparse.SUPPRESS)
    parser.add_argument("--child-trace", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-warmup-texts", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-warmup-hash", help=argparse.SUPPRESS)
    parser.add_argument("--child-control-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-trace-hash", help=argparse.SUPPRESS)
    parser.add_argument("--child-semantic-index", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-semantic-index-hash", help=argparse.SUPPRESS)
    parser.add_argument("--child-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-model-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-tokenizer-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-model-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--child-model-files-json", help=argparse.SUPPRESS)
    parser.add_argument("--child-model-digest-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--child-tokenizer-files-json", help=argparse.SUPPRESS)
    parser.add_argument("--child-tokenizer-digest-sha256", help=argparse.SUPPRESS)
    parser.add_argument("--child-source-snapshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child-head-commit", help=argparse.SUPPRESS)
    parser.add_argument(
        "--child-dependency-attestation-sha256", help=argparse.SUPPRESS
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> Gate7Config:
    requests = args.requests
    if requests is None:
        requests = 3000 if args.mode == "full" else 60
    capacity = args.capacity
    if capacity is None:
        capacity = 100 if args.mode == "full" else 10
    return Gate7Config(
        mode=args.mode,
        requests=requests,
        capacity=capacity,
        hit_threshold=args.hit_threshold,
        topic_threshold=args.topic_threshold,
        cell_threshold=args.cell_threshold,
        demand_half_life=args.demand_half_life,
        quota_strength=args.quota_strength,
        ghost_support_threshold=args.ghost_support_threshold,
        admission_margin=args.admission_margin,
        centroid_alpha=args.centroid_alpha,
        entry_hit_weight=args.entry_hit_weight,
        onnx_intra_threads=args.onnx_intra_threads,
        onnx_inter_threads=args.onnx_inter_threads,
        warmup_requests=args.warmup_requests,
        resource_interval_ms=args.resource_interval_ms,
        max_length=args.max_length,
        fake_embedding=bool(args.fake_embedding),
    )


def _parse_child_asset_map(raw: str, label: str) -> Dict[str, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("%s file map is not valid JSON" % label) from exc
    if not isinstance(value, dict) or not all(
        isinstance(name, str)
        and bool(name)
        and isinstance(digest, str)
        and len(digest) == 64
        for name, digest in value.items()
    ):
        raise ValueError("%s file map is malformed" % label)
    return dict(value)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = _config_from_args(args)
    config.validate()
    _assert_launch_mode(config.mode)
    if args.child_policy:
        required = (
            args.child_seed,
            args.child_order_position,
            args.child_attempt_id,
            args.child_trace,
            args.child_warmup_texts,
            args.child_warmup_hash,
            args.child_control_dir,
            args.child_trace_hash,
            args.child_semantic_index,
            args.child_semantic_index_hash,
            args.child_result,
            args.child_model_sha256,
            args.child_source_snapshot,
        )
        if any(value is None for value in required):
            raise ValueError("child mode is missing an internal argument")
        if not config.fake_embedding and (
            args.child_model_path is None
            or args.child_tokenizer_path is None
            or args.child_model_files_json is None
            or args.child_model_digest_sha256 is None
            or args.child_tokenizer_files_json is None
            or args.child_tokenizer_digest_sha256 is None
        ):
            raise ValueError(
                "ONNX child mode needs local paths and frozen asset identities"
            )
        if (
            config.mode == "full"
            and args.child_dependency_attestation_sha256 is None
        ):
            raise ValueError("formal child mode lacks dependency attestation identity")
        assets = _fake_assets() if config.fake_embedding else {
            "kind": "pinned_onnx",
            "model_path": str(args.child_model_path),
            "tokenizer_path": str(args.child_tokenizer_path),
            "model_sha256": str(args.child_model_sha256),
            "model_files": _parse_child_asset_map(
                str(args.child_model_files_json), "model"
            ),
            "model_digest_sha256": str(args.child_model_digest_sha256),
            "tokenizer_files": _parse_child_asset_map(
                str(args.child_tokenizer_files_json), "tokenizer"
            ),
            "tokenizer_digest_sha256": str(
                args.child_tokenizer_digest_sha256
            ),
        }
        expected_sources = json.loads(
            Path(args.child_source_snapshot).read_text(encoding="utf-8")
        )
        if not isinstance(expected_sources, dict):
            raise ValueError("child source snapshot is not an object")
        _assert_source_snapshot(
            expected_sources,
            str(args.child_head_commit)
            if args.child_head_commit is not None
            else None,
        )
        try:
            result = _execute_child(
                args.child_policy,
                int(args.child_seed),
                int(args.child_order_position),
                str(args.child_attempt_id),
                Path(args.child_trace),
                Path(args.child_warmup_texts),
                Path(args.child_control_dir),
                str(args.child_trace_hash),
                Path(args.child_semantic_index),
                str(args.child_semantic_index_hash),
                config,
                assets,
                str(args.child_warmup_hash),
                (
                    str(args.child_dependency_attestation_sha256)
                    if args.child_dependency_attestation_sha256 is not None
                    else None
                ),
            )
        except BaseException as error:
            _write_json(
                Path(args.child_result), _child_failure_diagnostics(error)
            )
            raise
        _assert_source_snapshot(
            expected_sources,
            str(args.child_head_commit)
            if args.child_head_commit is not None
            else None,
        )
        _write_json(Path(args.child_result), result)
        return 0

    seeds = tuple(args.seeds or (FULL_SEEDS if args.mode == "full" else (FULL_SEEDS[0],)))
    output = args.output
    if output is None:
        if args.mode == "full":
            output = FORMAL_ATTEMPT_ROOT / (
                "attempt-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                + "-"
                + str(os.getpid())
            )
        else:
            output = PROJECT_ROOT / "artifacts" / "gate7-v2-onnx-smoke"
    manifest = run_bundle(
        args.prepared,
        output,
        args.contract,
        seeds,
        args.policies,
        config,
    )
    print(
        "wrote %d isolated runs to %s"
        % (manifest["artifacts"]["runs.csv"]["rows"], Path(output).resolve())
    )
    return int(manifest.get("_preterminal_audit_exit_status", 0))


if __name__ == "__main__":
    raise SystemExit(main())
