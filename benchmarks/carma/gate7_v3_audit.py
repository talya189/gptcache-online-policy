"""Independent offline adjudicator for prospective Gate 7 v3 ONNX evidence.

The benchmark runner is deliberately not imported here.  Frozen protocol
values, integrity checks, recomputations, and pass/fail rules live in this
separate module so a defect in the producer cannot silently define what its
own output means.

Status has four possible values:

``invalid``
    Existing evidence is internally inconsistent, malformed, or has failed an
    integrity/structural check.  It cannot be used for a Gate 7 claim.
``pending``
    Valid evidence is developmental or does not contain all five frozen,
    three-policy seed blocks.  No confirmatory conclusion is available.
``fail``
    A complete, structurally valid formal experiment violates at least one
    frozen numerical bound.
``pass``
    A complete, structurally valid formal experiment satisfies every frozen
    bound for every seed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "carma-gate7-onnx-v3"
AUDIT_SCHEMA_VERSION = "carma-gate7-adjudication-v3"
ATTEMPT_LEDGER_SCHEMA_VERSION = "carma-gate7-attempt-ledger-v4"
TERMINAL_INTENT_SCHEMA_VERSION = "carma-gate7-terminal-intent-v2"
BOOTSTRAP_COMPLETION_SCHEMA_VERSION = "carma-gate7-bootstrap-completion-v2"
DEPENDENCY_ATTESTATION_SCHEMA_VERSION = "carma-gate7-dependency-attestation-v2"
FORMAL_SOURCE_ANCHOR_SCHEMA_VERSION = "carma-gate7-formal-source-anchor-v1"
FORMAL_ENTRYPOINT_SCHEMA_VERSION = "carma-gate7-formal-entrypoint-attestation-v1"
ISOLATED_BOOTSTRAP_SCHEMA_VERSION = "carma-gate7-isolated-bootstrap-v2"
PREIMPORT_SOURCE_SCHEMA_VERSION = "carma-gate7-preimport-source-v1"
WRAPPER_SHELL_STARTUP_SCHEMA_VERSION = "carma-gate7-wrapper-shell-startup-v1"
WARMUP_SCHEMA_VERSION = "carma-gate7-warmup-v1"
EXPERIMENT_ID = "gate7d-onnx-v3"
PRETERMINAL_REPORT_NAME = "gate7-preterminal-adjudication.json"
TERMINAL_RECORD_NAMES = ("manifest.json", "attempt-failure.json")
ADJUDICATION_STATUSES = ("pass", "fail", "pending", "invalid")
ADJUDICATION_EXIT_STATUSES = {
    "pass": 0,
    "fail": 1,
    "pending": 2,
    "invalid": 3,
}
PROTOCOL_FULL_SEEDS = (20261001, 20261002, 20261003, 20261004, 20261005)
FULL_SEEDS = PROTOCOL_FULL_SEEDS
POLICIES = ("LRU", "LFU", "CARMA")
POLICY_ORDER_NAMESPACE = "gate7b-policy-base-v1"
CANDIDATE_NAMESPACE = "gate7b-qqp-candidate-v1"
PHASE_NAMESPACE = "gate7b-qqp-phase-v1"
DEFAULT_CONTRACT_PATH = "docs/project/gate7-v3-remediation-contract.md"
# Replaced exactly once after the v3 contract review and before source tagging.
PINNED_CONTRACT_SHA256 = "70bd3eacc480d7a26a8d62d7a53f757fc1c45b09f955859d70c9e92aad85ccb2"
FORMAL_ATTEMPT_ROOT = "artifacts/gate7-v3-onnx-attempts"
RETAINED_QQP_ARCHIVE_PATH = "source/similiar_qqp_full.json.gz"
RETAINED_PREPARED_DIRECTORY = "source/prepared"
DEPENDENCY_ATTESTATION_PATH = "source/dependency-attestation.json"
FORMAL_SOURCE_TAG_PAYLOAD_PATH = "source/formal-source-tag.raw"
FORMAL_SOURCE_TAG = "gate7d-onnx-v3-formal-source"
FORMAL_SOURCE_REMOTE = "submission"
FORMAL_SOURCE_REMOTE_URL = (
    "https://github.com/MatanGoldfarB/gptcache-online-policy.git"
)
FORMAL_ENTRYPOINT_MARKER = "gate7d-wrapper-v1"
FORMAL_WRAPPER_SHELL_MARKER = "gate7d-shell-v1"
FORMAL_WRAPPER_PATH = "scripts/run_gate7_v3_onnx_integration_benchmark.sh"
ISOLATED_BOOTSTRAP_PATH = "scripts/gate7_v3_isolated_bootstrap.py"
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
LOCAL_GPTCACHE_VERSION = "0.1.44"
PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY = {
    "record_sha256": "873280782d16563eef2982efbead80814577c01aea0253cea060d7cb3cc5b140",
    "hashed_file_count": 11,
    "hashed_bytes": 29856,
    "hashed_files_sha256": "29bf983930625eacdddedcd24953d1849e9ebf923f233c09bd38506d67391b91",
}
FORMAL_REQUIRED_ENVIRONMENT = {
    "PYTHONHASHSEED": "0",
    "TOKENIZERS_PARALLELISM": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "CARMA_GATE7_WRAPPER_SHELL": FORMAL_WRAPPER_SHELL_MARKER,
    "CARMA_GATE7_FORMAL_ENTRYPOINT": FORMAL_ENTRYPOINT_MARKER,
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
FORMAL_OPTIONAL_OS_ENVIRONMENT = ("__CF_USER_TEXT_ENCODING",)
FROZEN_PTH_FILES = {
    "lib/python3.12/site-packages/__editable__.gptcache-0.1.44.pth": {
        "owner": "gptcache",
        "sha256": "08aa0783cabb1c39329892a6374c68c9b6d7a2d59002d955e7ab057d549d5593",
        "bytes": 89,
    },
    "lib/python3.12/site-packages/a1_coverage.pth": {
        "owner": "coverage",
        "sha256": "ef2ed06d19867ec669c09a804060666a9cd5e383af0a9d11aa2de79b77d448e8",
        "bytes": 205,
    },
    "lib/python3.12/site-packages/coloredlogs.pth": {
        "owner": "coloredlogs",
        "sha256": "dda83a855986efa5cd87f0248b0199c0086eb0e8e7fece7d6741959c5ce39536",
        "bytes": 147,
    },
    "lib/python3.12/site-packages/distutils-precedence.pth": {
        "owner": "setuptools",
        "sha256": "2638ce9e2500e572a5e0de7faed6661eb569d1b696fcba07b0dd223da5f5d224",
        "bytes": 151,
    },
}

V1_PRESERVATION_MANIFEST_PATH = (
    "docs/project/evidence/gate7-v1-invalid-attempt.json"
)
V1_PRESERVATION_ARCHIVE_PATH = (
    "docs/project/evidence/gate7-v1-invalid-attempt.tar.gz"
)
PINNED_V1_PRESERVATION_MANIFEST_SHA256 = (
    "b7ee1befd512d1582c0816152b9b87c1cbed91ac3a924bbbebc2bcedc3c6dee1"
)
PINNED_V1_PRESERVATION_MANIFEST_BYTES = 2217
PINNED_V1_PRESERVATION_ARCHIVE_SHA256 = (
    "cb326101dcc8323575be376d923630cfda6191c2e7adbc3b7fd6b01acf6b9147"
)
PINNED_V1_PRESERVATION_ARCHIVE_BYTES = 484539
PINNED_V1_LEDGER_SHA256 = (
    "96539252388659e779ac09014895886156e9f405020ce278527b82bfea22aeb7"
)
PINNED_V1_LEDGER_BYTES = 1390
PINNED_V1_TERMINAL_ENTRY_SHA256 = (
    "768d681eac5f5f39e7517275ef7c2b29ff4f57d5fa7e2531c45e3db6142157a5"
)
PINNED_V1_FAILURE_SHA256 = (
    "41a7af508e769f24a91e72b221906fd7e50c6d20176032a9d58590ba9feb9f12"
)
PINNED_V1_ATTEMPT_ID = "20260827T084100878176Z-b74ed9553e7c"
PINNED_V1_ATTEMPT_DIRECTORY = "attempt-20260827T084100Z-89406"
PINNED_V1_ROOT = "artifacts/gate7-onnx-attempts"
V2_PRESERVATION_ROOT = (
    "artifacts/samples/verification/gate7-v2-invalid"
)
PINNED_V2_PRESERVATION_FILES = {
    "SHA256SUMS": {
        "sha256": "069ee7e3951ac3a4f015841fe0a020168f501c2cc38e19b3505f87f7c619cb07",
        "bytes": 269,
    },
    "attempt-ledger.jsonl": {
        "sha256": "84eee7963f4e5c27f8d962e31ddcdcec9779b1cd463a8952841946ffc8557f0c",
        "bytes": 21346,
    },
    "gate7-preterminal-adjudication.json": {
        "sha256": "e3e8df2787049d3b1ea4e622e7666502b715ae572431b4f8d4e4b213c12dd39b",
        "bytes": 293720,
    },
    "manifest.json": {
        "sha256": "7574127cce8f1c72e87a1528dc6709947860178f43667a92add155e89e1f1cf8",
        "bytes": 695528,
    },
}
PINNED_V2_ATTEMPT_ID = "20260827T131743602373Z-557c6ac0578c"
PINNED_V2_ATTEMPT_DIRECTORY = "attempt-20260827T131736Z-1784"
PINNED_V2_SOURCE_COMMIT = "557c6ac0578cb6b77c5ae51595b49abdc0407e10"
PINNED_V2_SOURCE_TAG = "gate7c-onnx-v2-formal-source"

GATE2_V2_SELECTION_PATH = (
    "docs/project/evidence/qqp-v2-threshold-selection.json"
)
GATE2_V2_RESULT_PATH = "docs/project/evidence/qqp-v2-result.json"
GATE2_V2_THRESHOLDS_PATH = (
    "docs/project/evidence/qqp-v2-calibration-thresholds.csv"
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

SEMANTIC_INDEX_SCHEMA_VERSION = "carma-gate7-semantic-index-v2"
SEMANTIC_ARTIFACT_SCHEMA_VERSION = "carma-gate7-semantic-index-artifact-v2"
SEMANTIC_HIT_CLASSES = (
    "miss",
    "same_concept",
    "direct_negative",
    "component_derived_negative",
    "unlabeled_cross_concept",
    "unresolved",
)
SEMANTIC_RELATIONS = (
    "not_applicable",
    "positive_same_component",
    "negative_direct",
    "negative_component_derived",
    "unlabeled_cross_component",
)
SEMANTIC_STATUSES = ("not_applicable", "valid", "invalid", "indeterminate")
EXPECTED_POLICY_ORDERS: Dict[int, Tuple[str, ...]] = {
    20261001: ("CARMA", "LFU", "LRU"),
    20261002: ("LFU", "LRU", "CARMA"),
    20261003: ("LRU", "CARMA", "LFU"),
    20261004: ("LRU", "LFU", "CARMA"),
    20261005: ("CARMA", "LRU", "LFU"),
}

FROZEN_TRACE_SEMANTIC_HASHES: Dict[int, Tuple[str, str]] = {
    20261001: (
        "25f773f174d14648c254aa355d9f7a983eafa6acad9c852f1af934ae42635c1b",
        "bf7496e49b0a2adda8414aff26ef63b4771f3791dc411084a310787dd5af6832",
    ),
    20261002: (
        "fc20d64d878d27bfcb0298bff35419a6c9aeae9ecbc686e314f61122593ce50f",
        "f96ef44b7a5d8cc5fc55a6024d516c6ad585c8b7519628e7f38e9dc085c05546",
    ),
    20261003: (
        "199178c7739cdf5d5682cf60963b7228c7e6f6bc1394dbf297f8fc8e4a3b386c",
        "c5d0a1b58453cfedb89ddd5bb890a4442c00c69d00a7647d52770eaf26240abd",
    ),
    20261004: (
        "806a58004bfd3fecf3b97603639ec279b338967c64e99842df36f17e704f9cc4",
        "07897d6572e7925c0b540f6c20ce76f692ccb29f979844cb41b5a1945ce9fd6a",
    ),
    20261005: (
        "a680defccd6e9973a65c9b34c37c44d62edfdf67a69ffcf260d7d59dbd9d270f",
        "7ccac25843a51d3c2417eaf69a5411510cfc0fe7f5d6876efb7acf6d30496a41",
    ),
}

FROZEN_CONFIG: Dict[str, Any] = {
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

FROZEN_MODEL: Dict[str, Any] = {
    "kind": "pinned_onnx",
    "model_repository": "GPTCache/paraphrase-albert-onnx",
    "model_revision": "5b562a100bc67e898ac89814e7a4668a18d65756",
    "model_sha256": "a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8",
    "model_files": {
        "model.onnx": "a173875cdc1ed10fc67ed1d4900d10b5414bd20052eb33785ffa1cad0162afb8",
    },
    "model_digest_sha256": "af8dd7ee021644802c30f27709e46ad77e65425d527ecdca8346995dea83d7ce",
    "tokenizer_repository": "GPTCache/paraphrase-albert-small-v2",
    "tokenizer_revision": "5fb246187b5489d59ce0db167e739192759defab",
    "tokenizer_files": {
        "config.json": "69765de9af37e704755cab37bee53f251465c269ae3f0c95436ab250dd11e342",
        "special_tokens_map.json": "129fed06908ddcc3e36105e41d753ff0b934e5cfb2e451ca0a48904acef41863",
        "spiece.model": "fefb02b667a6c5c2fe27602d28e5fb3428f66ab89c7d6f388e7c8d44a02d0336",
        "tokenizer.json": "d0a881fece9b11d4f8003a08ac7d8d65409e3aa573fc385faa8708cdd5a77087",
        "tokenizer_config.json": "95f31ea415a8e447b1e2ca05b897a05c1f5857b0643579d42dbec5ac5c2555c1",
    },
    "tokenizer_digest_sha256": "41f1aee1afa8c01eecd6f60e8097836cb8e97bae95bf2f7fe34d85c5764f9deb",
    "provider": "CPUExecutionProvider",
}

FROZEN_SOURCE_HASHES = {
    "source_archive_sha256": "1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58",
    "source_pairs_sha256": "c84d9897bd4838e8c12f45d59e04401031bbd5fa533570490db016cf40235126",
    "source_texts_sha256": "645ece94cebf36d1d37a66410d925d7d2252b6d39dd42b473e866289d1576dc3",
}

REQUIRED_SOURCE_IDENTITIES = (
    "benchmarks/carma/gate7_v3_onnx_integration_benchmark.py",
    "benchmarks/carma/gate7_v2_trace.py",
    "benchmarks/carma/gate7_v3_audit.py",
    "benchmarks/carma/qqp.py",
    "benchmarks/carma/qqp_v2.py",
    "benchmarks/carma/integration_benchmark.py",
    "scripts/run_gate7_v3_onnx_integration_benchmark.sh",
    "scripts/gate7_v3_isolated_bootstrap.py",
    "scripts/verify_project.sh",
    "tests/project_tests/test_gate7_v2_trace.py",
    "tests/project_tests/test_gate7_v3_onnx_integration.py",
    "tests/project_tests/test_gate7_v3_audit.py",
    "tests/project_tests/test_gate7_v3_isolated_bootstrap.py",
    "tests/project_tests/test_gate7_v2_preservation.py",
    "tests/project_tests/test_qqp_v2.py",
    "tests/project_tests/test_gate7_v1_preservation.py",
    V1_PRESERVATION_MANIFEST_PATH,
    V1_PRESERVATION_ARCHIVE_PATH,
    V2_PRESERVATION_ROOT + "/SHA256SUMS",
    V2_PRESERVATION_ROOT + "/attempt-ledger.jsonl",
    V2_PRESERVATION_ROOT + "/gate7-preterminal-adjudication.json",
    V2_PRESERVATION_ROOT + "/manifest.json",
    GATE2_V2_SELECTION_PATH,
    GATE2_V2_RESULT_PATH,
    GATE2_V2_THRESHOLDS_PATH,
    "examples/benchmark/similiar_qqp_full.json.gz",
    "requirements-benchmark.lock",
    "requirements-project.lock",
    "Dockerfile.project",
)

BOUNDS: Dict[str, Any] = {
    "p95_ratio_max": 1.25,
    "p95_delta_ns_max": 500_000,
    "throughput_ratio_min": 0.90,
    "rss_ratio_max": 1.20,
    "rss_delta_bytes_max": 64 * 1024 * 1024,
}

REQUIRED_ARTIFACTS = (
    "runs.csv",
    "requests.jsonl",
    "resources.jsonl",
    "outcome-latency.jsonl",
)

TIMING_FIELDS = (
    "text_preprocess_tokenize_ns",
    "onnx_inference_ns",
    "embedding_postprocess_ns",
    "faiss_search_ns",
    "sqlite_read_ns",
    "similarity_decision_ns",
    "policy_exclusive_ns",
    "sqlite_write_ns",
    "faiss_mutation_ns",
    "response_return_ns",
    "residual_ns",
    "request_total_ns",
)

SUMMARY_STAGE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "end_to_end": ("request_total_ns", "end_to_end_ns"),
    "post_embedding": ("post_embedding_total_ns", "post_embedding_ns"),
    "embedding": ("embedding_ns",),
    "faiss": ("faiss_ns",),
    "sqlite": ("sqlite_ns",),
    "policy_exclusive": ("policy_exclusive_ns",),
    "policy_inclusive": ("policy_inclusive_ns",),
    "response_return": ("response_return_ns",),
    "residual": ("residual_ns",),
}

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

TRACE_PROJECTION = (
    "request_index",
    "request_id",
    "phase",
    "occurrence",
    "reuse_opportunity",
    "text_id",
    "concept_id",
    "expected_response_id",
)

TRACE_FIELDS = {
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


class _Audit:
    def __init__(self) -> None:
        self.errors: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []

    def error(self, code: str, message: str, **context: Any) -> None:
        item: Dict[str, Any] = {"code": code, "message": message}
        if context:
            item["context"] = context
        self.errors.append(item)

    def warn(self, code: str, message: str, **context: Any) -> None:
        item: Dict[str, Any] = {"code": code, "message": message}
        if context:
            item["context"] = context
        self.warnings.append(item)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _v2_preservation_identity(
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verify and identify the tracked snapshot of the invalid v2 attempt."""

    base = (
        Path(project_root)
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    root = base / V2_PRESERVATION_ROOT
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("the tracked Gate 7 v2 preservation root is missing")
    resolved_root = root.resolve()
    observed: Dict[str, Any] = {}
    for name, expected in PINNED_V2_PRESERVATION_FILES.items():
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve().parent != resolved_root
        ):
            raise RuntimeError("a tracked Gate 7 v2 preservation file is unsafe")
        identity = {
            "sha256": _sha256_file(path),
            "bytes": int(path.stat().st_size),
        }
        if identity != expected:
            raise RuntimeError("a tracked Gate 7 v2 preservation file changed")
        observed[name] = identity

    checksum_payload = "".join(
        "%s  %s\n" % (PINNED_V2_PRESERVATION_FILES[name]["sha256"], name)
        for name in (
            "attempt-ledger.jsonl",
            "gate7-preterminal-adjudication.json",
            "manifest.json",
        )
    )
    if (root / "SHA256SUMS").read_text(encoding="utf-8") != checksum_payload:
        raise RuntimeError("the tracked Gate 7 v2 checksum declaration changed")
    try:
        ledger = [
            json.loads(line)
            for line in (root / "attempt-ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        report = json.loads(
            (root / "gate7-preterminal-adjudication.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("the tracked Gate 7 v2 preservation is unreadable") from exc
    anchor = manifest.get("formal_source_anchor") if isinstance(manifest, dict) else None
    if (
        len(ledger) != 2
        or not all(isinstance(entry, dict) for entry in ledger)
        or [entry.get("event") for entry in ledger] != ["PROTOCOL_GENESIS", "START"]
        or ledger[-1].get("attempt_id") != PINNED_V2_ATTEMPT_ID
        or ledger[-1].get("directory") != PINNED_V2_ATTEMPT_DIRECTORY
        or ledger[-1].get("head_commit") != PINNED_V2_SOURCE_COMMIT
        or not isinstance(manifest, dict)
        or manifest.get("attempt_id") != PINNED_V2_ATTEMPT_ID
        or not isinstance(anchor, dict)
        or anchor.get("head_commit") != PINNED_V2_SOURCE_COMMIT
        or anchor.get("tag_name") != PINNED_V2_SOURCE_TAG
        or not isinstance(report, dict)
        or report.get("attempt_id") != PINNED_V2_ATTEMPT_ID
        or report.get("status") != "invalid"
        or report.get("claimable") is not False
        or report.get("error_count") != 7
        or not isinstance(report.get("errors"), list)
        or len(report["errors"]) != 7
    ):
        raise RuntimeError("the tracked Gate 7 v2 preservation is inconsistent")
    return {
        "schema_version": "carma-gate7-v2-preservation-v1",
        "snapshot_root": V2_PRESERVATION_ROOT,
        "formal_root": "artifacts/gate7-v2-onnx-attempts",
        "attempt_id": PINNED_V2_ATTEMPT_ID,
        "attempt_directory": PINNED_V2_ATTEMPT_DIRECTORY,
        "status": "invalid",
        "claimable": False,
        "terminal_present": False,
        "invalid_error_count": 7,
        "source_commit": PINNED_V2_SOURCE_COMMIT,
        "source_tag": PINNED_V2_SOURCE_TAG,
        "files": observed,
    }


def _canonical_mapping_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _parse_benchmark_lock(audit: _Audit) -> Dict[str, str]:
    """Independently parse the exact retained source lock, not producer output."""

    lock_path = Path(__file__).resolve().parents[2] / "requirements-benchmark.lock"
    if (
        not lock_path.is_file()
        or _sha256_file(lock_path) != PINNED_BENCHMARK_LOCK_SHA256
    ):
        audit.error(
            "dependency_lock",
            "executing auditor does not have the frozen benchmark lock bytes",
        )
        return {}
    logical_lines: List[str] = []
    accumulator = ""
    try:
        raw_lines = lock_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        audit.error("dependency_lock", "cannot read benchmark lock", detail=str(exc))
        return {}
    for raw_line in raw_lines:
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
        audit.error("dependency_lock", "benchmark lock ends mid-requirement")
    pins: Dict[str, str] = {}
    for position, line in enumerate(logical_lines):
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:\s|$)", line)
        if match is None:
            audit.error(
                "dependency_lock",
                "benchmark lock contains an unpinned row",
                position=position,
            )
            continue
        name = _normalized_distribution_name(match.group(1))
        if name in pins:
            audit.error(
                "dependency_lock",
                "benchmark lock repeats a normalized distribution name",
                distribution=name,
            )
            continue
        pins[name] = match.group(2)
    pins = dict(sorted(pins.items()))
    if (
        len(pins) != PINNED_BENCHMARK_LOCK_PIN_COUNT
        or _canonical_mapping_sha256(pins) != PINNED_BENCHMARK_PIN_MAP_SHA256
    ):
        audit.error(
            "dependency_lock",
            "benchmark lock pin map differs from the frozen contract",
        )
    return pins


def _auditor_identity() -> Dict[str, Any]:
    path = Path(__file__).resolve()
    return {
        "path": "benchmarks/carma/gate7_v3_audit.py",
        "sha256": _sha256_file(path),
        "bytes": int(path.stat().st_size),
    }


def _auditor_identity_matches_source(
    auditor_identity: Any, source_identity: Any
) -> bool:
    """Compare a self-describing auditor with its path-keyed source entry."""

    return bool(
        isinstance(auditor_identity, Mapping)
        and set(auditor_identity) == {"path", "sha256", "bytes"}
        and auditor_identity.get("path") == "benchmarks/carma/gate7_v3_audit.py"
        and _valid_sha256(auditor_identity.get("sha256"))
        and isinstance(auditor_identity.get("bytes"), int)
        and not isinstance(auditor_identity.get("bytes"), bool)
        and auditor_identity.get("bytes", 0) > 0
        and isinstance(source_identity, Mapping)
        and set(source_identity) == {"sha256", "bytes"}
        and source_identity
        == {
            "sha256": auditor_identity.get("sha256"),
            "bytes": auditor_identity.get("bytes"),
        }
    )


def _json_loads(value: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError("non-finite JSON number: %s" % token)

    return json.loads(value, parse_constant=reject_constant)


def _load_json(path: Path, audit: _Audit, code: str) -> Optional[Dict[str, Any]]:
    try:
        value = _json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        audit.error(code, "cannot read valid JSON", path=str(path), detail=str(exc))
        return None
    if not isinstance(value, dict):
        audit.error(code, "JSON root must be an object", path=str(path))
        return None
    return value


def _load_jsonl(path: Path, audit: _Audit, code: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        audit.error(code, "cannot read JSONL artifact", path=str(path), detail=str(exc))
        return rows
    for line_number, raw_line in enumerate(
        raw_bytes.splitlines(keepends=True), start=1
    ):
        has_lf = raw_line.endswith(b"\n")
        raw_payload = raw_line[:-1] if has_lf else raw_line
        if not raw_payload.strip():
            audit.error(
                code,
                "blank JSONL rows are not canonical evidence",
                path=str(path),
                line=line_number,
            )
            continue
        try:
            line = raw_payload.decode("utf-8")
            row = _json_loads(line)
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            audit.error(
                code,
                "invalid JSONL row",
                path=str(path),
                line=line_number,
                detail=str(exc),
            )
            continue
        if not isinstance(row, dict):
            audit.error(
                code,
                "JSONL row must be an object",
                path=str(path),
                line=line_number,
            )
            continue
        canonical = (
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        if not has_lf or raw_line != canonical:
            audit.error(
                code,
                "JSONL row bytes are not canonical compact sorted UTF-8 with LF",
                path=str(path),
                line=line_number,
            )
            continue
        rows.append(row)
    return rows


def _load_csv(path: Path, audit: _Audit) -> List[Dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None:
                audit.error("runs_csv_header", "runs.csv has no header")
                return []
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                audit.error("runs_csv_header", "runs.csv has duplicate column names")
            return [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        audit.error("runs_csv_read", "cannot read runs.csv", detail=str(exc))
        return []


def _row_count(path: Path) -> int:
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


def _safe_artifact_path(
    bundle: Path, name: str, declaration: Mapping[str, Any]
) -> Optional[Path]:
    declared_path = declaration.get("path", name)
    if (
        not isinstance(declared_path, str)
        or not declared_path
        or "\\" in declared_path
        or "\x00" in declared_path
    ):
        return None
    relative = Path(declared_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != declared_path
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return None
    candidate = bundle / relative
    cursor = bundle
    if cursor.is_symlink():
        return None
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    return candidate


def _verify_artifacts(
    bundle: Path, manifest: Mapping[str, Any], audit: _Audit
) -> Dict[str, Dict[str, Any]]:
    declarations = manifest.get("artifacts")
    if not isinstance(declarations, dict):
        audit.error("artifact_manifest", "manifest artifacts must be an object")
        return {}
    for required in REQUIRED_ARTIFACTS:
        if required not in declarations:
            audit.error("artifact_missing_declaration", "required artifact is undeclared", artifact=required)

    required_paths: Dict[str, str] = {}
    for required in REQUIRED_ARTIFACTS:
        declaration = declarations.get(required)
        if not isinstance(declaration, Mapping):
            continue
        declared_path = declaration.get("path")
        if isinstance(declared_path, str):
            if declared_path in required_paths:
                audit.error(
                    "artifact_path_alias",
                    "required artifacts must not share one retained path",
                    artifact=required,
                    other=required_paths[declared_path],
                    declared=declared_path,
                )
            else:
                required_paths[declared_path] = required
        if (
            declared_path != required
            or Path(required).parent != Path(".")
        ):
            audit.error(
                "artifact_path",
                "required artifact must declare its exact direct-child path",
                artifact=required,
                declared=declared_path,
            )
            continue

    computed: Dict[str, Dict[str, Any]] = {}
    for name, raw_declaration in declarations.items():
        if not isinstance(name, str) or not isinstance(raw_declaration, dict):
            audit.error("artifact_manifest", "artifact declaration is malformed", artifact=str(name))
            continue
        path = _safe_artifact_path(bundle, name, raw_declaration)
        if path is None:
            audit.error("artifact_path", "artifact path leaves bundle or is invalid", artifact=name)
            continue
        if not path.is_file():
            audit.error("artifact_missing", "declared artifact is missing", artifact=name, path=str(path))
            continue
        try:
            values = {
                "path": str(path.relative_to(bundle)),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": _row_count(path),
            }
        except (OSError, UnicodeError, csv.Error) as exc:
            audit.error("artifact_read", "cannot inspect declared artifact", artifact=name, detail=str(exc))
            continue
        computed[name] = values
        for field in ("sha256", "rows", "bytes"):
            if field not in raw_declaration:
                audit.warn(
                    "artifact_field_missing",
                    "artifact declaration omits a reproducibility field",
                    artifact=name,
                    field=field,
                )
                continue
            observed = raw_declaration[field]
            expected = values[field]
            if observed != expected:
                audit.error(
                    "artifact_integrity",
                    "artifact declaration does not match retained bytes",
                    artifact=name,
                    field=field,
                    declared=observed,
                    computed=expected,
                )
    return computed


def _is_production_formal(manifest: Mapping[str, Any]) -> bool:
    config = manifest.get("config")
    return bool(
        isinstance(config, Mapping)
        and config.get("mode") == "full"
        and manifest.get("formal_claimable_mode") is True
        and manifest.get("seeds") == list(PROTOCOL_FULL_SEEDS)
    )


def _is_full_mode(manifest: Mapping[str, Any]) -> bool:
    config = manifest.get("config")
    return bool(isinstance(config, Mapping) and config.get("mode") == "full")


def _validate_retained_inputs(
    bundle: Path,
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> Optional[Path]:
    """Authenticate the only QQP source tree a formal audit may consume."""

    formal = _is_production_formal(manifest)
    retained = manifest.get("retained_inputs")
    if not isinstance(retained, Mapping):
        if formal:
            audit.error(
                "retained_inputs",
                "formal evidence omits the retained source-input declaration",
            )
        return None
    expected_retained_fields = {
        "trace_construction_prepared_dir",
        "audit_source_policy",
        "archive",
        "prepared",
        "dependency_attestation",
        "formal_source_tag_payload",
    }
    if not formal and set(retained) != expected_retained_fields:
        # Developmental/synthetic evidence may carry only the ledger-binding
        # placeholder.  The complete retained-input schema is mandatory only
        # for the frozen five-seed formal protocol.
        return None
    if set(retained) != expected_retained_fields:
        audit.error(
            "retained_inputs",
            "retained source declaration has unexpected or missing fields",
            observed=sorted(retained),
            expected=sorted(expected_retained_fields),
        )
    if (
        retained.get("trace_construction_prepared_dir")
        != RETAINED_PREPARED_DIRECTORY
        or retained.get("audit_source_policy")
        != "retained_attempt_copies_only"
    ):
        audit.error(
            "retained_inputs",
            "formal trace/audit source policy is not the frozen retained-copy policy",
        )

    source_root = bundle / "source"
    if source_root.is_symlink() or not source_root.is_dir():
        audit.error(
            "retained_inputs",
            "retained source directory is missing or is a symbolic link",
        )
        return None
    actual_source_files: set = set()
    for path in source_root.rglob("*"):
        if path.is_symlink():
            audit.error(
                "retained_inputs",
                "retained source tree contains a symbolic link",
                path=str(path.relative_to(bundle)),
            )
            continue
        if path.is_file():
            actual_source_files.add(str(path.relative_to(bundle)))
    declared_source_files = {
        name for name in artifacts if isinstance(name, str) and name.startswith("source/")
    }
    if actual_source_files != declared_source_files:
        audit.error(
            "retained_inputs_artifacts",
            "every retained source file must have one verified artifact declaration",
            actual=sorted(actual_source_files),
            declared=sorted(declared_source_files),
        )

    def verify_declaration(
        declaration: Any, expected_path: str, code: str
    ) -> Optional[Path]:
        if not isinstance(declaration, Mapping) or set(declaration) != {
            "path",
            "sha256",
            "bytes",
        }:
            audit.error(
                code,
                "retained input identity has unexpected or missing fields",
                expected_path=expected_path,
            )
            return None
        if declaration.get("path") != expected_path:
            audit.error(
                code,
                "retained input has a noncanonical path",
                declared=declaration.get("path"),
                expected=expected_path,
            )
            return None
        computed = artifacts.get(expected_path)
        if not isinstance(computed, Mapping) or any(
            declaration.get(field) != computed.get(field)
            for field in ("sha256", "bytes")
        ):
            audit.error(
                code,
                "retained input identity differs from verified artifact bytes",
                path=expected_path,
            )
            return None
        path = (bundle / expected_path).resolve()
        try:
            path.relative_to(bundle.resolve())
        except ValueError:
            audit.error(code, "retained input path escapes the audited bundle")
            return None
        if path.is_symlink() or not path.is_file():
            audit.error(code, "retained input is missing or is a symbolic link")
            return None
        return path

    prepared = retained.get("prepared")
    expected_prepared_names = {"manifest.json", "pairs.jsonl", "texts.jsonl"}
    if not isinstance(prepared, Mapping) or set(prepared) != expected_prepared_names:
        audit.error(
            "retained_prepared",
            "retained prepared declaration must contain exactly manifest/pairs/texts",
        )
        return None
    prepared_paths: Dict[str, Path] = {}
    for name in sorted(expected_prepared_names):
        path = verify_declaration(
            prepared.get(name),
            "%s/%s" % (RETAINED_PREPARED_DIRECTORY, name),
            "retained_prepared",
        )
        if path is not None:
            prepared_paths[name] = path
    if set(prepared_paths) != expected_prepared_names:
        return None

    prepared_manifest = _load_json(
        prepared_paths["manifest.json"], audit, "retained_prepared_manifest"
    )
    pairs_sha256 = _sha256_file(prepared_paths["pairs.jsonl"])
    texts_sha256 = _sha256_file(prepared_paths["texts.jsonl"])
    if (
        not isinstance(prepared_manifest, Mapping)
        or prepared_manifest.get("schema_version") != "carma-qqp-v1"
        or prepared_manifest.get("pairs_sha256") != pairs_sha256
        or prepared_manifest.get("texts_sha256") != texts_sha256
    ):
        audit.error(
            "retained_prepared_manifest",
            "retained prepared manifest does not authenticate pairs/texts bytes",
        )
    if formal and (
        pairs_sha256 != FROZEN_SOURCE_HASHES["source_pairs_sha256"]
        or texts_sha256 != FROZEN_SOURCE_HASHES["source_texts_sha256"]
    ):
        audit.error(
            "retained_prepared_identity",
            "formal retained prepared data differs from frozen QQP identities",
            pairs_sha256=pairs_sha256,
            texts_sha256=texts_sha256,
        )

    archive = retained.get("archive")
    if formal:
        archive_path = verify_declaration(
            archive, RETAINED_QQP_ARCHIVE_PATH, "retained_archive"
        )
        if (
            archive_path is None
            or _sha256_file(archive_path)
            != FROZEN_SOURCE_HASHES["source_archive_sha256"]
            or not isinstance(prepared_manifest, Mapping)
            or prepared_manifest.get("archive_sha256")
            != FROZEN_SOURCE_HASHES["source_archive_sha256"]
        ):
            audit.error(
                "retained_archive",
                "formal retained raw QQP archive differs from the frozen source",
            )
    elif archive is not None:
        verify_declaration(archive, RETAINED_QQP_ARCHIVE_PATH, "retained_archive")
    return (bundle / RETAINED_PREPARED_DIRECTORY).resolve()


def _valid_record_summary(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value)
        == {
            "record_sha256",
            "hashed_file_count",
            "hashed_bytes",
            "hashed_files_sha256",
        }
        and _valid_sha256(value.get("record_sha256"))
        and _valid_sha256(value.get("hashed_files_sha256"))
        and isinstance(value.get("hashed_file_count"), int)
        and not isinstance(value.get("hashed_file_count"), bool)
        and value.get("hashed_file_count") >= 0
        and isinstance(value.get("hashed_bytes"), int)
        and not isinstance(value.get("hashed_bytes"), bool)
        and value.get("hashed_bytes") >= 0
    )


def _validate_preimport_source(
    value: Any,
    manifest: Mapping[str, Any],
    audit: _Audit,
    context: str,
) -> bool:
    """Validate the stdlib-only bootstrap's formal source-boundary proof."""

    expected_fields = {
        "schema_version",
        "enforced",
        "launch_mode",
        "git_executable",
        "git_status_clean",
        "anchor",
        "inventory",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        audit.error(
            "bootstrap_preimport_source",
            "formal bootstrap has no exact pre-import source-boundary observation",
            context=context,
        )
        return False

    git_executable = value.get("git_executable")
    source_anchor = manifest.get("formal_source_anchor")
    anchor = value.get("anchor")
    contract = manifest.get("contract")
    inventory = value.get("inventory")
    expected_anchor = None
    if (
        _formal_source_anchor_structurally_valid(source_anchor)
        and isinstance(source_anchor, Mapping)
        and isinstance(contract, Mapping)
    ):
        expected_anchor = {
            "object_format": source_anchor.get("object_format"),
            "head_commit": source_anchor.get("head_commit"),
            "head_tree": source_anchor.get("tree_id"),
            "tag_name": source_anchor.get("tag_name"),
            "tag_object_type": source_anchor.get("tag_object_type"),
            "tag_object_id": source_anchor.get("tag_object_id"),
            "peeled_commit": source_anchor.get("peeled_commit"),
            "tag_tree": source_anchor.get("tree_id"),
            "tag_payload_sha256": source_anchor.get("tag_payload_sha256"),
            "tag_payload_bytes": source_anchor.get("tag_payload_bytes"),
            "annotation": source_anchor.get("annotation"),
            "remote": {
                "remote_name": FORMAL_SOURCE_REMOTE,
                "fetch_url": FORMAL_SOURCE_REMOTE_URL,
                "fetch_url_count": 1,
                "push_url": FORMAL_SOURCE_REMOTE_URL,
                "push_url_count": 1,
                "tag_ref": "refs/tags/%s" % FORMAL_SOURCE_TAG,
                "tag_object_id": source_anchor.get("tag_object_id"),
                "peeled_ref": "refs/tags/%s^{}" % FORMAL_SOURCE_TAG,
                "peeled_commit": source_anchor.get("peeled_commit"),
            },
            "contract_path": DEFAULT_CONTRACT_PATH,
            "contract_sha256": PINNED_CONTRACT_SHA256,
            "contract_bytes": contract.get("bytes"),
        }

    inventory_integer_fields = (
        "tracked_file_count",
        "tracked_file_bytes",
        "runtime_file_count",
        "runtime_untracked_or_missing_file_count",
        "runtime_symlink_count",
    )
    project_namespace = (
        inventory.get("project_import_namespace")
        if isinstance(inventory, Mapping)
        else None
    )
    valid = bool(
        value.get("schema_version") == PREIMPORT_SOURCE_SCHEMA_VERSION
        and value.get("enforced") is True
        and value.get("launch_mode") == "full"
        and value.get("git_status_clean") is True
        and isinstance(git_executable, Mapping)
        and set(git_executable) == {"path", "sha256", "bytes", "version"}
        and git_executable.get("path") == "/usr/bin/git"
        and _valid_sha256(git_executable.get("sha256"))
        and isinstance(git_executable.get("bytes"), int)
        and not isinstance(git_executable.get("bytes"), bool)
        and git_executable.get("bytes", 0) > 0
        and isinstance(git_executable.get("version"), str)
        and git_executable.get("version", "").startswith("git version ")
        and isinstance(anchor, Mapping)
        and expected_anchor is not None
        and anchor == expected_anchor
        and contract.get("path") == DEFAULT_CONTRACT_PATH
        and contract.get("sha256") == PINNED_CONTRACT_SHA256
        and isinstance(contract.get("bytes"), int)
        and not isinstance(contract.get("bytes"), bool)
        and contract.get("bytes", 0) > 0
        and anchor.get("object_format") == "sha1"
        and _valid_sha1(anchor.get("head_commit"))
        and _valid_sha1(anchor.get("head_tree"))
        and _valid_sha1(anchor.get("tag_object_id"))
        and _valid_sha1(anchor.get("peeled_commit"))
        and _valid_sha1(anchor.get("tag_tree"))
        and anchor.get("head_commit") == anchor.get("peeled_commit")
        and anchor.get("head_tree") == anchor.get("tag_tree")
        and isinstance(inventory, Mapping)
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
            and inventory.get(field, -1) >= 0
            for field in inventory_integer_fields
        )
        and inventory.get("tracked_file_count", 0) > 0
        and inventory.get("tracked_file_bytes", 0) > 0
        and inventory.get("runtime_file_count", 0) > 0
        and inventory.get("tracked_file_count", 0)
        >= inventory.get("runtime_file_count", 0)
        and _valid_sha256(inventory.get("tracked_file_map_sha256"))
        and inventory.get("runtime_untracked_or_missing_file_count") == 0
        and inventory.get("runtime_symlink_count") == 0
        and isinstance(project_namespace, Mapping)
        and set(project_namespace)
        == {
            "excluded_sealed_roots",
            "import_suffixes",
            "import_file_count",
            "untracked_or_ignored_import_file_count",
            "symlink_count",
        }
        and project_namespace.get("excluded_sealed_roots") == [".git", ".venv"]
        and project_namespace.get("import_suffixes")
        == [".py", ".pyc", ".pyo", ".so", ".pyd", ".dylib", ".dll"]
        and all(
            isinstance(project_namespace.get(field), int)
            and not isinstance(project_namespace.get(field), bool)
            for field in (
                "import_file_count",
                "untracked_or_ignored_import_file_count",
                "symlink_count",
            )
        )
        and project_namespace.get("import_file_count", 0) > 0
        and project_namespace.get("untracked_or_ignored_import_file_count") == 0
        and project_namespace.get("symlink_count") == 0
    )
    if not valid:
        audit.error(
            "bootstrap_preimport_source",
            "pre-import source boundary does not bind clean tagged runtime source",
            context=context,
        )
    return valid


def _validate_wrapper_shell_startup(
    value: Any,
    environment: Any,
    project_root: Any,
    audit: _Audit,
    context: str,
) -> bool:
    """Validate the wrapper's first-line shell-startup observation."""

    expected_fields = {
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
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_fields
        or not isinstance(environment, Mapping)
    ):
        audit.error(
            "bootstrap_wrapper_shell",
            "formal bootstrap has no exact wrapper-shell startup observation",
            context=context,
        )
        return False
    digest = value.get("attestation_sha256")
    unhashed = dict(value)
    unhashed.pop("attestation_sha256", None)
    valid = bool(
        value.get("schema_version") == WRAPPER_SHELL_STARTUP_SCHEMA_VERSION
        and value.get("profile") == "env-i-v1"
        and value.get("marker") == FORMAL_WRAPPER_SHELL_MARKER
        and value.get("launch_mode") == "full"
        and value.get("outer_env_i_operator_root_required") is True
        and value.get("home") == environment.get("HOME")
        and value.get("home")
        == environment.get("CARMA_GATE7_WRAPPER_SHELL_HOME")
        and isinstance(project_root, str)
        and value.get("pwd") == project_root
        and value.get("pwd")
        == environment.get("CARMA_GATE7_WRAPPER_SHELL_PWD")
        and value.get("tmpdir") == "/tmp"
        and value.get("tmpdir")
        == environment.get("CARMA_GATE7_WRAPPER_SHELL_TMPDIR")
        and value.get("shlvl") == "1"
        and value.get("shlvl")
        == environment.get("CARMA_GATE7_WRAPPER_SHELL_SHLVL")
        and isinstance(value.get("optional_cf_user_text_encoding"), str)
        and value.get("optional_cf_user_text_encoding")
        == environment.get("CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF")
        and environment.get("CARMA_GATE7_WRAPPER_SHELL_PROFILE") == "env-i-v1"
        and value.get("fixed_environment")
        == {
            "PATH": FORMAL_REQUIRED_ENVIRONMENT["PATH"],
            "LANG": FORMAL_REQUIRED_ENVIRONMENT["LANG"],
            "LC_ALL": FORMAL_REQUIRED_ENVIRONMENT["LC_ALL"],
            "LC_CTYPE": FORMAL_REQUIRED_ENVIRONMENT["LC_CTYPE"],
            "TZ": FORMAL_REQUIRED_ENVIRONMENT["TZ"],
        }
        and _valid_sha256(digest)
        and digest == _canonical_mapping_sha256(unhashed)
    )
    if not valid:
        audit.error(
            "bootstrap_wrapper_shell",
            "wrapper-shell startup evidence differs from the frozen env-i profile",
            context=context,
        )
    return valid


def _validate_bootstrap_attestation(
    value: Any,
    role: str,
    manifest: Mapping[str, Any],
    audit: _Audit,
    context: str,
) -> Optional[Mapping[str, Any]]:
    expected_target = (
        "benchmarks/carma/gate7_v3_audit.py"
        if role == "auditor"
        else "benchmarks/carma/gate7_v3_onnx_integration_benchmark.py"
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
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        audit.error(
            "bootstrap_attestation",
            "isolated-bootstrap attestation has unexpected or missing fields",
            context=context,
        )
        return None
    digest = value.get("attestation_sha256")
    unhashed = dict(value)
    unhashed.pop("attestation_sha256", None)
    source_identities = manifest.get("source_identities")
    target_identity = (
        source_identities.get(expected_target)
        if isinstance(source_identities, Mapping)
        else None
    )
    if (
        value.get("schema_version") != ISOLATED_BOOTSTRAP_SCHEMA_VERSION
        or value.get("role") != role
        or value.get("target") != expected_target
        or not _valid_sha256(digest)
        or digest != _canonical_mapping_sha256(unhashed)
        or not isinstance(target_identity, Mapping)
        or value.get("target_sha256") != target_identity.get("sha256")
        or value.get("site_module_absent") is not True
        or value.get("pth_executed") is not False
    ):
        audit.error(
            "bootstrap_attestation",
            "isolated-bootstrap identity/digest differs from the frozen target",
            context=context,
        )
    lock = value.get("lock")
    if lock != {
        "path": "requirements-benchmark.lock",
        "sha256": PINNED_BENCHMARK_LOCK_SHA256,
        "pin_count": PINNED_BENCHMARK_LOCK_PIN_COUNT,
        "pin_map_sha256": PINNED_BENCHMARK_PIN_MAP_SHA256,
    }:
        audit.error(
            "bootstrap_attestation",
            "isolated bootstrap does not bind the frozen dependency lock",
            context=context,
        )
    python = value.get("python")
    expected_flags = {
        "no_site": 1,
        "safe_path": True,
        "ignore_environment": 0,
        "isolated": 0,
        "dont_write_bytecode": 1,
        "hash_randomization": 0,
    }
    environment = python.get("environment") if isinstance(python, Mapping) else None
    expected_environment_keys = (
        set(FORMAL_REQUIRED_ENVIRONMENT)
        | set(FORMAL_DYNAMIC_ENVIRONMENT)
        | set(FORMAL_OPTIONAL_OS_ENVIRONMENT)
    )
    if (
        not isinstance(python, Mapping)
        or set(python)
        != {
            "flags",
            "initial_sys_path",
            "base_prefix",
            "executable",
            "executable_resolved",
            "executable_sha256",
            "wrapper_pid",
            "environment",
            "wrapper_shell_startup",
        }
        or python.get("flags") != expected_flags
        or not isinstance(python.get("initial_sys_path"), list)
        or not all(isinstance(item, str) and item for item in python.get("initial_sys_path", ()))
        or not isinstance(environment, Mapping)
        or not set(environment).issubset(expected_environment_keys)
        or not set(FORMAL_REQUIRED_ENVIRONMENT).issubset(environment)
        or not set(FORMAL_DYNAMIC_ENVIRONMENT).issubset(environment)
        or any(environment.get(key) != expected for key, expected in FORMAL_REQUIRED_ENVIRONMENT.items())
        or environment.get("CARMA_GATE7_LAUNCH_MODE") != "full"
        or any(key in environment for key in FORMAL_FORBIDDEN_ENVIRONMENT)
        or any(key in environment for key in FORMAL_FORBIDDEN_CACHE_ENVIRONMENT)
        or any(str(key).startswith(("DYLD_", "LD_")) for key in environment)
    ):
        audit.error(
            "bootstrap_attestation",
            "isolated bootstrap did not preserve the frozen -S -P environment",
            context=context,
        )
    _validate_wrapper_shell_startup(
        python.get("wrapper_shell_startup") if isinstance(python, Mapping) else None,
        environment,
        value.get("project_root"),
        audit,
        context,
    )
    pyvenv = value.get("pyvenv")
    if (
        not isinstance(pyvenv, Mapping)
        or pyvenv.get("path") != ".venv/pyvenv.cfg"
        or not _valid_sha256(pyvenv.get("sha256"))
        or not isinstance(pyvenv.get("values"), Mapping)
        or pyvenv["values"].get("version") != PINNED_CPYTHON_VERSION
        or str(pyvenv["values"].get("include-system-site-packages", "")).lower()
        != "false"
    ):
        audit.error(
            "bootstrap_attestation",
            "isolated bootstrap virtual-environment identity is malformed",
            context=context,
        )
    dependency = value.get("dependency")
    if not isinstance(dependency, Mapping):
        audit.error(
            "bootstrap_attestation",
            "isolated bootstrap dependency observation is missing",
            context=context,
        )
    else:
        pins = _parse_benchmark_lock(audit)
        expected_installed = dict(sorted({**pins, "gptcache": LOCAL_GPTCACHE_VERSION}.items()))
        pth_files = dependency.get("pth_files")
        if (
            set(dependency)
            != {
                "installed_packages",
                "installed_package_map_sha256",
                "locked_distribution_count",
                "locked_hashed_file_count",
                "locked_hashed_bytes",
                "locked_record_aggregate_sha256",
                "local_editable",
                "pth_files",
                "unhashed_existing_file_count",
                "unhashed_existing_sha256",
                "site_file_count",
                "site_directory_count",
                "sealed_site_packages",
            }
            or not isinstance(dependency.get("unhashed_existing_file_count"), int)
            or isinstance(dependency.get("unhashed_existing_file_count"), bool)
            or dependency.get("unhashed_existing_file_count", -1) < 0
            or not _valid_sha256(dependency.get("unhashed_existing_sha256"))
            or not isinstance(dependency.get("site_file_count"), int)
            or dependency.get("site_file_count", 0) <= 0
            or not isinstance(dependency.get("site_directory_count"), int)
            or dependency.get("site_directory_count", 0) <= 0
            or
            dependency.get("installed_packages") != expected_installed
            or dependency.get("installed_package_map_sha256")
            != _canonical_mapping_sha256(expected_installed)
            or dependency.get("locked_distribution_count")
            != PINNED_BENCHMARK_LOCK_PIN_COUNT
            or dependency.get("locked_hashed_file_count")
            != PINNED_LOCKED_RECORD_HASHED_FILE_COUNT
            or dependency.get("locked_hashed_bytes")
            != PINNED_LOCKED_RECORD_HASHED_BYTES
            or dependency.get("locked_record_aggregate_sha256")
            != PINNED_LOCKED_RECORD_AGGREGATE_SHA256
            or dependency.get("local_editable")
            != PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY
            or pth_files != FROZEN_PTH_FILES
            or dependency.get("sealed_site_packages") is not True
        ):
            audit.error(
                "bootstrap_attestation",
                "isolated bootstrap dependency boundary differs from frozen RECORD evidence",
                context=context,
            )
    _validate_preimport_source(
        value.get("preimport_source"), manifest, audit, context
    )
    origins = value.get("preloaded_module_origins")
    if not isinstance(origins, Mapping) or any(
        not isinstance(name, str)
        or not isinstance(origin, str)
        or not (
            origin.startswith("<stdlib>/")
            or origin == ISOLATED_BOOTSTRAP_PATH
        )
        for name, origin in (origins.items() if isinstance(origins, Mapping) else ())
    ):
        audit.error(
            "bootstrap_attestation",
            "pre-bootstrap imports are not confined to stdlib/bootstrap source",
            context=context,
        )
    return dict(value) if _valid_sha256(digest) else None


def _validate_dependency_attestation(
    bundle: Path,
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    run_rows: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> Optional[Mapping[str, Any]]:
    if not _is_production_formal(manifest):
        return None
    retained = manifest.get("retained_inputs")
    declaration = (
        retained.get("dependency_attestation")
        if isinstance(retained, Mapping)
        else None
    )
    if not isinstance(declaration, Mapping) or set(declaration) != {
        "path",
        "sha256",
        "bytes",
        "attestation_sha256",
    }:
        audit.error(
            "dependency_attestation",
            "formal retained-input declaration omits dependency attestation identity",
        )
        return None
    if declaration.get("path") != DEPENDENCY_ATTESTATION_PATH:
        audit.error(
            "dependency_attestation",
            "retained dependency attestation has a noncanonical path",
        )
        return None
    computed = artifacts.get(DEPENDENCY_ATTESTATION_PATH)
    path = bundle / DEPENDENCY_ATTESTATION_PATH
    if (
        not isinstance(computed, Mapping)
        or any(
            declaration.get(field) != computed.get(field)
            for field in ("sha256", "bytes")
        )
        or not path.is_file()
        or path.is_symlink()
    ):
        audit.error(
            "dependency_attestation",
            "retained dependency attestation identity differs from artifact bytes",
        )
        return None
    retained_value = _load_json(path, audit, "dependency_attestation")
    manifest_value = manifest.get("dependency_attestation")
    if not isinstance(retained_value, Mapping) or retained_value != manifest_value:
        audit.error(
            "dependency_attestation",
            "manifest dependency attestation differs from its retained source copy",
        )
        return retained_value
    expected_top_fields = {
        "schema_version",
        "python",
        "lock",
        "installed",
        "pip_check",
        "environment",
        "record_integrity",
        "project_shadow_scan",
        "numpy",
        "faiss",
        "attestation_sha256",
    }
    digest = retained_value.get("attestation_sha256")
    unhashed = dict(retained_value)
    unhashed.pop("attestation_sha256", None)
    if (
        set(retained_value) != expected_top_fields
        or retained_value.get("schema_version")
        != DEPENDENCY_ATTESTATION_SCHEMA_VERSION
        or not _valid_sha256(digest)
        or digest != _canonical_mapping_sha256(unhashed)
        or declaration.get("attestation_sha256") != digest
    ):
        audit.error(
            "dependency_attestation",
            "dependency attestation schema or canonical digest is invalid",
        )

    pins = _parse_benchmark_lock(audit)
    expected_installed = dict(sorted({**pins, "gptcache": LOCAL_GPTCACHE_VERSION}.items()))
    lock = retained_value.get("lock")
    if lock != {
        "path": "requirements-benchmark.lock",
        "sha256": PINNED_BENCHMARK_LOCK_SHA256,
        "pin_count": PINNED_BENCHMARK_LOCK_PIN_COUNT,
        "pin_map_sha256": PINNED_BENCHMARK_PIN_MAP_SHA256,
        "pins": pins,
    }:
        audit.error(
            "dependency_attestation",
            "retained dependency lock evidence differs from independent parsing",
        )
    python = retained_value.get("python")
    if (
        not isinstance(python, Mapping)
        or set(python)
        != {
            "implementation",
            "version",
            "executable",
            "resolved_executable",
            "sys_prefix",
            "pyvenv_cfg",
        }
        or python.get("implementation") != "CPython"
        or python.get("version") != PINNED_CPYTHON_VERSION
        or python.get("executable") != ".venv/bin/python"
        or python.get("sys_prefix") != ".venv"
        or not isinstance(python.get("resolved_executable"), str)
        or not python.get("resolved_executable")
        or not Path(str(python.get("resolved_executable"))).is_absolute()
        or not isinstance(python.get("pyvenv_cfg"), Mapping)
        or python["pyvenv_cfg"].get("path") != ".venv/pyvenv.cfg"
        or not _valid_sha256(python["pyvenv_cfg"].get("sha256"))
        or not isinstance(python["pyvenv_cfg"].get("bytes"), int)
        or isinstance(python["pyvenv_cfg"].get("bytes"), bool)
        or python["pyvenv_cfg"].get("bytes", 0) <= 0
    ):
        audit.error(
            "dependency_attestation",
            "formal CPython/virtual-environment identity is malformed",
        )
    installed = retained_value.get("installed")
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
    if not isinstance(installed, Mapping):
        audit.error("dependency_attestation", "installed package evidence is missing")
    else:
        origins = installed.get("third_party_origins")
        imported = installed.get("imported_origins")
        gptcache = installed.get("gptcache")
        if (
            set(installed)
            != {
                "packages",
                "package_map_sha256",
                "third_party_origins",
                "imported_origins",
                "gptcache",
            }
            or
            installed.get("packages") != expected_installed
            or installed.get("package_map_sha256")
            != _canonical_mapping_sha256(expected_installed)
            or not isinstance(origins, Mapping)
            or set(origins) != set(pins)
            or any(
                not isinstance(origin, str)
                or not origin
                or origin.startswith("/")
                or ".." in Path(origin).parts
                for origin in (origins.values() if isinstance(origins, Mapping) else ())
            )
            or not isinstance(imported, Mapping)
            or set(imported) != set(required_module_owners)
            or any(
                not isinstance(imported.get(module), Mapping)
                or imported[module].get("owner") != owner
                or not isinstance(imported[module].get("path"), str)
                or imported[module]["path"].startswith("/")
                or ".." in Path(imported[module]["path"]).parts
                for module, owner in required_module_owners.items()
            )
            or gptcache
            != {
                "version": LOCAL_GPTCACHE_VERSION,
                "editable": True,
                "source_path": ".",
                "module_origin": "gptcache/__init__.py",
            }
        ):
            audit.error(
                "dependency_attestation",
                "installed/imported package boundary differs from frozen lock/source ownership",
            )
    if retained_value.get("pip_check") != {
        "status": "pass",
        "returncode": 0,
        "method": "in_process_requires_dist_consistency",
        "site_module_absent": True,
    }:
        audit.error("dependency_attestation", "pip check is not retained as passing")
    environment = retained_value.get("environment")
    if not isinstance(environment, Mapping):
        audit.error("dependency_attestation", "environment attestation is missing")
        environment_digest = None
    else:
        environment_digest = environment.get("environment_sha256")
        unhashed_environment = dict(environment)
        unhashed_environment.pop("environment_sha256", None)
        dynamic = environment.get("dynamic")
        optional_os = environment.get("optional_os")
        if (
            set(environment)
            != {
                "required",
                "dynamic",
                "optional_os",
                "bootstrap_evidence_present",
                "unexpected_absent",
                "forbidden_absent",
                "forbidden_cache_absent",
                "loader_variables_absent",
                "pycache_prefix",
                "pycache_prefix_empty",
                "environment_sha256",
            }
            or
            environment.get("required") != FORMAL_REQUIRED_ENVIRONMENT
            or not isinstance(dynamic, Mapping)
            or set(dynamic) != set(FORMAL_DYNAMIC_ENVIRONMENT)
            or any(not isinstance(value, str) for value in dynamic.values())
            or any(
                not value
                for key, value in dynamic.items()
                if key != "CARMA_GATE7_WRAPPER_SHELL_OPTIONAL_CF"
            )
            or dynamic.get("CARMA_GATE7_LAUNCH_MODE") != "full"
            or dynamic.get("CARMA_GATE7_WRAPPER_SHELL_PROFILE") != "env-i-v1"
            or dynamic.get("CARMA_GATE7_WRAPPER_SHELL_HOME")
            != dynamic.get("HOME")
            or dynamic.get("CARMA_GATE7_WRAPPER_SHELL_TMPDIR") != "/tmp"
            or dynamic.get("CARMA_GATE7_WRAPPER_SHELL_SHLVL") != "1"
            or not isinstance(optional_os, Mapping)
            or set(optional_os).difference(FORMAL_OPTIONAL_OS_ENVIRONMENT)
            or any(not isinstance(value, str) or not value for value in optional_os.values())
            or environment.get("bootstrap_evidence_present") is not True
            or environment.get("unexpected_absent") is not True
            or environment.get("forbidden_absent")
            != list(FORMAL_FORBIDDEN_ENVIRONMENT)
            or environment.get("forbidden_cache_absent")
            != list(FORMAL_FORBIDDEN_CACHE_ENVIRONMENT)
            or environment.get("loader_variables_absent") is not True
            or not isinstance(environment.get("pycache_prefix"), str)
            or not environment.get("pycache_prefix")
            or environment.get("pycache_prefix_empty") is not True
            or not _valid_sha256(environment_digest)
            or environment_digest != _canonical_mapping_sha256(unhashed_environment)
            or manifest.get("environment_sha256") != environment_digest
        ):
            audit.error(
                "dependency_attestation",
                "formal process environment digest or values are inconsistent",
            )
    record = retained_value.get("record_integrity")
    if not isinstance(record, Mapping):
        audit.error("dependency_attestation", "RECORD integrity evidence is missing")
    else:
        locked = record.get("locked_distributions")
        startup = record.get("startup_files")
        sealed = record.get("sealed_site_packages")
        aggregate = record.get("aggregate_sha256")
        unhashed_record = dict(record)
        unhashed_record.pop("aggregate_sha256", None)
        valid_locked_summaries = bool(
            isinstance(locked, Mapping)
            and all(_valid_record_summary(summary) for summary in locked.values())
        )
        locked_file_count = (
            sum(summary["hashed_file_count"] for summary in locked.values())
            if valid_locked_summaries
            else -1
        )
        locked_bytes = (
            sum(summary["hashed_bytes"] for summary in locked.values())
            if valid_locked_summaries
            else -1
        )
        if (
            set(record)
            != {
                "locked_distribution_count",
                "locked_hashed_file_count",
                "locked_hashed_bytes",
                "locked_aggregate_sha256",
                "startup_files",
                "sitecustomize_absent",
                "usercustomize_absent",
                "locked_distributions",
                "local_editable",
                "sealed_site_packages",
                "aggregate_sha256",
            }
            or not isinstance(locked, Mapping)
            or set(locked) != set(pins)
            or not valid_locked_summaries
            or _canonical_mapping_sha256(locked)
            != PINNED_LOCKED_RECORD_AGGREGATE_SHA256
            or record.get("locked_aggregate_sha256")
            != PINNED_LOCKED_RECORD_AGGREGATE_SHA256
            or record.get("locked_distribution_count")
            != PINNED_BENCHMARK_LOCK_PIN_COUNT
            or record.get("locked_hashed_file_count")
            != PINNED_LOCKED_RECORD_HASHED_FILE_COUNT
            or locked_file_count != PINNED_LOCKED_RECORD_HASHED_FILE_COUNT
            or record.get("locked_hashed_bytes")
            != PINNED_LOCKED_RECORD_HASHED_BYTES
            or locked_bytes != PINNED_LOCKED_RECORD_HASHED_BYTES
            or record.get("local_editable")
            != PINNED_LOCAL_GPTCACHE_RECORD_SUMMARY
            or startup != FROZEN_PTH_FILES
            or record.get("sitecustomize_absent") is not True
            or record.get("usercustomize_absent") is not True
            or not isinstance(sealed, Mapping)
            or set(sealed)
            != {
                "owned_file_count",
                "owned_paths_sha256",
                "owned_directory_count",
                "owned_directories_sha256",
                "unowned_files",
                "unowned_directories",
            }
            or sealed.get("unowned_files") != []
            or sealed.get("unowned_directories") != []
            or not isinstance(sealed.get("owned_file_count"), int)
            or sealed.get("owned_file_count", 0) <= 0
            or not _valid_sha256(sealed.get("owned_paths_sha256"))
            or not isinstance(sealed.get("owned_directory_count"), int)
            or sealed.get("owned_directory_count", 0) <= 0
            or not _valid_sha256(sealed.get("owned_directories_sha256"))
            or not _valid_sha256(aggregate)
            or aggregate != _canonical_mapping_sha256(unhashed_record)
        ):
            audit.error(
                "dependency_attestation",
                "RECORD/site-packages seal differs from frozen per-distribution aggregate",
            )
    if retained_value.get("project_shadow_scan") != {
        "runtime_roots": ["gptcache", "benchmarks", "."],
        "rejected_files": [],
    }:
        audit.error(
            "dependency_attestation",
            "project shadow-capable runtime scan did not pass",
        )
    if retained_value.get("numpy") != {"blas": "accelerate", "lapack": "accelerate"}:
        audit.error("dependency_attestation", "NumPy does not attest Accelerate BLAS/LAPACK")
    if retained_value.get("faiss") != {"max_threads": 1}:
        audit.error("dependency_attestation", "FAISS thread count is not frozen to one")

    for run_id, row in run_rows.items():
        if (
            _bool_value(row.get("dependency_attestation_pre_verified")) is not True
            or _bool_value(row.get("dependency_attestation_post_verified")) is not True
            or row.get("dependency_attestation_sha256") != digest
            or row.get("environment_sha256") != environment_digest
        ):
            audit.error(
                "dependency_run_binding",
                "child pre/post dependency evidence differs from retained parent attestation",
                run_id=run_id,
            )
    return retained_value


def _validate_formal_source_anchor(
    bundle: Path,
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
    preterminal: bool,
) -> Optional[Mapping[str, Any]]:
    if not _is_production_formal(manifest):
        return None
    anchor = manifest.get("formal_source_anchor")
    if not _formal_source_anchor_structurally_valid(anchor):
        audit.error(
            "formal_source_anchor",
            "formal manifest has no structurally valid annotated source anchor",
        )
        return None
    assert isinstance(anchor, Mapping)
    source_identities = manifest.get("source_identities")
    source_snapshot = manifest.get("source_snapshot_sha256")
    git = manifest.get("git")
    if (
        not isinstance(source_identities, Mapping)
        or not _valid_sha256(source_snapshot)
        or source_snapshot != _canonical_mapping_sha256(source_identities)
        or not isinstance(git, Mapping)
        or anchor.get("head_commit") != git.get("head_commit")
        or anchor.get("peeled_commit") != git.get("head_commit")
    ):
        audit.error(
            "formal_source_anchor",
            "formal source snapshot, Git HEAD, and annotated tag are not identical",
        )
    retained = manifest.get("retained_inputs")
    declaration = (
        retained.get("formal_source_tag_payload")
        if isinstance(retained, Mapping)
        else None
    )
    computed = artifacts.get(FORMAL_SOURCE_TAG_PAYLOAD_PATH)
    payload_path = bundle / FORMAL_SOURCE_TAG_PAYLOAD_PATH
    if (
        not isinstance(declaration, Mapping)
        or set(declaration) != {"path", "sha256", "bytes"}
        or declaration.get("path") != FORMAL_SOURCE_TAG_PAYLOAD_PATH
        or not isinstance(computed, Mapping)
        or any(
            declaration.get(field) != computed.get(field)
            for field in ("sha256", "bytes")
        )
        or not payload_path.is_file()
        or payload_path.is_symlink()
    ):
        audit.error(
            "formal_source_tag_payload",
            "retained annotated-tag payload is missing or not artifact-bound",
        )
        return anchor
    try:
        payload = payload_path.read_bytes()
    except OSError as exc:
        audit.error(
            "formal_source_tag_payload",
            "retained annotated-tag payload is unreadable",
            detail=str(exc),
        )
        return anchor
    object_id = hashlib.sha1(
        ("tag %d\0" % len(payload)).encode("ascii") + payload
    ).hexdigest()
    try:
        header_bytes, annotation_bytes = payload.split(b"\n\n", 1)
        headers: Dict[str, str] = {}
        for raw_header in header_bytes.decode("utf-8").splitlines():
            key, value = raw_header.split(" ", 1)
            if key in headers:
                raise ValueError("duplicate tag header")
            headers[key] = value
        annotation_lines = set(annotation_bytes.decode("utf-8").splitlines())
    except (ValueError, UnicodeDecodeError) as exc:
        audit.error(
            "formal_source_tag_payload",
            "retained annotated-tag object is malformed",
            detail=str(exc),
        )
        return anchor
    required_annotation = {
        "experiment_id=%s" % EXPERIMENT_ID,
        "contract_sha256=%s" % PINNED_CONTRACT_SHA256,
    }
    if (
        hashlib.sha256(payload).hexdigest() != anchor.get("tag_payload_sha256")
        or len(payload) != anchor.get("tag_payload_bytes")
        or object_id != anchor.get("tag_object_id")
        or headers.get("object") != anchor.get("peeled_commit")
        or headers.get("type") != "commit"
        or headers.get("tag") != FORMAL_SOURCE_TAG
        or not required_annotation.issubset(annotation_lines)
    ):
        audit.error(
            "formal_source_tag_payload",
            "retained raw tag bytes contradict the formal source anchor",
        )

    if preterminal:
        project_root = Path(__file__).resolve().parents[2]
        try:
            tag_ref = "refs/tags/%s" % FORMAL_SOURCE_TAG
            local_tag = subprocess.check_output(
                ["git", "rev-parse", tag_ref],
                cwd=str(project_root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            local_type = subprocess.check_output(
                ["git", "cat-file", "-t", local_tag],
                cwd=str(project_root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            local_payload = subprocess.check_output(
                ["git", "cat-file", "tag", local_tag],
                cwd=str(project_root),
                stderr=subprocess.DEVNULL,
            )
            local_head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(project_root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            local_tree = subprocess.check_output(
                ["git", "rev-parse", "%s^{tree}" % local_tag],
                cwd=str(project_root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            audit.error(
                "formal_source_anchor_local",
                "preterminal audit cannot resolve the local annotated source tag",
                detail=str(exc),
            )
        else:
            if (
                local_tag != anchor.get("tag_object_id")
                or local_type != "tag"
                or local_payload != payload
                or local_head != anchor.get("head_commit")
                or local_tree != anchor.get("tree_id")
            ):
                audit.error(
                    "formal_source_anchor_local",
                    "local tag/HEAD/tree differs from retained formal source anchor",
                )
        try:
            tag_ref = "refs/tags/%s" % FORMAL_SOURCE_TAG
            peeled_ref = "%s^{}" % tag_ref
            remote_fetch_urls = subprocess.check_output(
                [
                    "git",
                    "remote",
                    "get-url",
                    "--all",
                    FORMAL_SOURCE_REMOTE,
                ],
                cwd=str(project_root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).splitlines()
            remote_push_urls = subprocess.check_output(
                [
                    "git",
                    "remote",
                    "get-url",
                    "--push",
                    "--all",
                    FORMAL_SOURCE_REMOTE,
                ],
                cwd=str(project_root),
                text=True,
                stderr=subprocess.DEVNULL,
            ).splitlines()
            remote_output = subprocess.check_output(
                [
                    "git",
                    "ls-remote",
                    "--tags",
                    FORMAL_SOURCE_REMOTE,
                    tag_ref,
                    peeled_ref,
                ],
                cwd=str(project_root),
                text=True,
                stderr=subprocess.DEVNULL,
            )
            remote_refs: Dict[str, str] = {}
            for line in remote_output.splitlines():
                parts = line.split()
                if len(parts) != 2 or parts[1] in remote_refs:
                    raise ValueError("malformed or duplicate remote tag ref")
                remote_refs[parts[1]] = parts[0]
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            audit.error(
                "formal_source_anchor_remote",
                "preterminal audit could not independently resolve the submission remote tag",
                detail=str(exc),
            )
        else:
            expected_remote = {
                tag_ref: anchor.get("tag_object_id"),
                peeled_ref: anchor.get("peeled_commit"),
            }
            if (
                remote_fetch_urls != [FORMAL_SOURCE_REMOTE_URL]
                or remote_push_urls != [FORMAL_SOURCE_REMOTE_URL]
                or remote_refs != expected_remote
                or anchor.get("remote")
                != {
                    "fetch_urls": remote_fetch_urls,
                    "push_urls": remote_push_urls,
                    "tag_object_id": remote_refs.get(tag_ref),
                    "peeled_commit": remote_refs.get(peeled_ref),
                }
            ):
                audit.error(
                    "formal_source_anchor_remote",
                    "fresh submission remote tag/peeled refs differ from retained formal source anchor",
                )
    return anchor


def _validate_power_observations(
    manifest: Mapping[str, Any], audit: _Audit
) -> None:
    if not _is_production_formal(manifest):
        return
    power = manifest.get("power_observations")
    if not isinstance(power, Mapping) or set(power) != {
        "pre_start",
        "end",
        "availability_limitation",
    }:
        audit.error(
            "power_observations",
            "formal manifest has no exact pre-START/end power observation pair",
        )
        return
    pre_start = power.get("pre_start")
    end = power.get("end")
    if not _power_observation_structurally_valid(
        pre_start
    ) or not _power_observation_structurally_valid(end):
        audit.error(
            "power_observations",
            "formal power observation has malformed fields or timestamp",
        )
        return
    assert isinstance(pre_start, Mapping) and isinstance(end, Mapping)
    if any(
        observation.get("available") is True
        and observation.get("plugged") is not True
        for observation in (pre_start, end)
    ):
        audit.error(
            "power_observations",
            "an available formal power observation reports the machine unplugged",
        )
    unavailable = any(
        observation.get("available") is False
        for observation in (pre_start, end)
    )
    expected_limitation = (
        "at least one endpoint power observation was unavailable; "
        "uninterrupted AC cannot be attested"
        if unavailable
        else "point observations cannot prove uninterrupted AC between observations"
    )
    if power.get("availability_limitation") != expected_limitation:
        audit.error(
            "power_observations",
            "formal power limitation disclosure contradicts endpoint availability",
        )


def _bootstrap_common_observation(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
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


def _bootstrap_matches_dependency(
    bootstrap: Mapping[str, Any],
    dependency_attestation: Mapping[str, Any],
) -> bool:
    observed = bootstrap.get("dependency")
    installed = dependency_attestation.get("installed")
    record = dependency_attestation.get("record_integrity")
    environment = dependency_attestation.get("environment")
    python = bootstrap.get("python")
    dependency_python = dependency_attestation.get("python")
    project_root = bootstrap.get("project_root")
    bootstrap_environment = (
        python.get("environment") if isinstance(python, Mapping) else None
    )
    if not all(
        isinstance(value, Mapping)
        for value in (
            observed,
            installed,
            record,
            environment,
            python,
            dependency_python,
            bootstrap_environment,
        )
    ):
        return False
    if (
        not isinstance(project_root, str)
        or not project_root
        or not os.path.isabs(project_root)
    ):
        return False
    assert isinstance(observed, Mapping)
    assert isinstance(installed, Mapping)
    assert isinstance(record, Mapping)
    assert isinstance(environment, Mapping)
    assert isinstance(python, Mapping)
    assert isinstance(dependency_python, Mapping)
    assert isinstance(bootstrap_environment, Mapping)
    dependency_lexical = dependency_python.get("executable")
    bootstrap_lexical = python.get("executable")
    expected_bootstrap_lexical = (
        os.path.normpath(os.path.join(project_root, dependency_lexical))
        if isinstance(dependency_lexical, str)
        and dependency_lexical
        and not os.path.isabs(dependency_lexical)
        else None
    )
    expected_preboot_environment = {
        **dict(environment.get("required", {})),
        **dict(environment.get("dynamic", {})),
        **dict(environment.get("optional_os", {})),
    }
    return bool(
        observed.get("installed_packages") == installed.get("packages")
        and expected_bootstrap_lexical is not None
        and isinstance(bootstrap_lexical, str)
        and os.path.normpath(bootstrap_lexical) == expected_bootstrap_lexical
        and dependency_python.get("resolved_executable")
        == python.get("executable_resolved")
        and observed.get("installed_package_map_sha256")
        == installed.get("package_map_sha256")
        and observed.get("locked_distribution_count")
        == record.get("locked_distribution_count")
        and observed.get("locked_hashed_file_count")
        == record.get("locked_hashed_file_count")
        and observed.get("locked_hashed_bytes")
        == record.get("locked_hashed_bytes")
        and observed.get("locked_record_aggregate_sha256")
        == record.get("locked_aggregate_sha256")
        and observed.get("local_editable") == record.get("local_editable")
        and observed.get("pth_files") == record.get("startup_files")
        and bootstrap_environment == expected_preboot_environment
    )


def _validate_entrypoint_and_bootstraps(
    manifest: Mapping[str, Any],
    run_rows: Mapping[str, Mapping[str, Any]],
    dependency_attestation: Optional[Mapping[str, Any]],
    audit: _Audit,
    preterminal: bool,
) -> Optional[Mapping[str, Any]]:
    if not _is_production_formal(manifest):
        return None
    entrypoint = manifest.get("entrypoint_attestation")
    if not _formal_entrypoint_structurally_valid(entrypoint):
        audit.error(
            "entrypoint_attestation",
            "formal evidence lacks the exact frozen wrapper entrypoint schema",
        )
        return None
    assert isinstance(entrypoint, Mapping)
    source_identities = manifest.get("source_identities")
    wrapper_source = (
        source_identities.get(FORMAL_WRAPPER_PATH)
        if isinstance(source_identities, Mapping)
        else None
    )
    wrapper_identity = entrypoint.get("wrapper_identity")
    parent_cmdline = entrypoint.get("parent_cmdline")
    bootstrap_value = entrypoint.get("bootstrap_attestation")
    bootstrap_project_root = (
        bootstrap_value.get("project_root")
        if isinstance(bootstrap_value, Mapping)
        else None
    )
    direct_parent_argv = bool(
        isinstance(parent_cmdline, list)
        and len(parent_cmdline) == 3
        and isinstance(parent_cmdline[0], str)
        and Path(parent_cmdline[0]).name == "bash"
        and isinstance(parent_cmdline[1], str)
        and Path(parent_cmdline[1]).is_absolute()
        and not parent_cmdline[1].startswith("-")
        and isinstance(bootstrap_project_root, str)
        and Path(parent_cmdline[1]).resolve()
        == (Path(bootstrap_project_root) / FORMAL_WRAPPER_PATH).resolve()
        and parent_cmdline[2] == "full"
    )
    if (
        not isinstance(entrypoint.get("wrapper_pid"), int)
        or isinstance(entrypoint.get("wrapper_pid"), bool)
        or entrypoint.get("wrapper_pid", 0) <= 1
        or entrypoint.get("producer_parent_pid") != entrypoint.get("wrapper_pid")
        or not isinstance(wrapper_identity, Mapping)
        or set(wrapper_identity) != {"sha256", "bytes"}
        or wrapper_identity != wrapper_source
        or entrypoint.get("parent_executable")
        != str(Path("/bin/bash").resolve())
        or not direct_parent_argv
    ):
        audit.error(
            "entrypoint_attestation",
            "formal wrapper identity, process lineage, or full-mode command is inconsistent",
        )
    parent_bootstrap = _validate_bootstrap_attestation(
        entrypoint.get("bootstrap_attestation"),
        "parent",
        manifest,
        audit,
        "parent entrypoint",
    )
    if (
        parent_bootstrap is None
        or entrypoint.get("bootstrap_attestation_sha256")
        != parent_bootstrap.get("attestation_sha256")
    ):
        audit.error(
            "entrypoint_attestation",
            "entrypoint bootstrap digest differs from its nested parent observation",
        )
        return parent_bootstrap
    if (
        dependency_attestation is None
        or not _bootstrap_matches_dependency(
            parent_bootstrap, dependency_attestation
        )
    ):
        audit.error(
            "bootstrap_dependency_binding",
            "parent bootstrap dependency/environment boundary differs from retained attestation",
        )

    child_map = manifest.get("child_bootstrap_attestations")
    if not isinstance(child_map, Mapping) or not child_map:
        audit.error(
            "child_bootstrap_attestations",
            "formal manifest has no child bootstrap observation map",
        )
        child_map = {}
    validated_children: Dict[str, Mapping[str, Any]] = {}
    for declared_digest, child_value in child_map.items():
        child = _validate_bootstrap_attestation(
            child_value,
            "child",
            manifest,
            audit,
            "child bootstrap %s" % declared_digest,
        )
        if (
            child is None
            or declared_digest != child.get("attestation_sha256")
            or _bootstrap_common_observation(child)
            != _bootstrap_common_observation(parent_bootstrap)
            or dependency_attestation is None
            or not _bootstrap_matches_dependency(child, dependency_attestation)
        ):
            audit.error(
                "child_bootstrap_attestations",
                "child bootstrap does not share the sealed parent dependency/runtime boundary",
                digest=declared_digest,
            )
        elif isinstance(declared_digest, str):
            validated_children[declared_digest] = child
    referenced_child_digests = {
        str(row.get("bootstrap_attestation_sha256"))
        for row in run_rows.values()
    }
    if (
        referenced_child_digests != set(validated_children)
        or any(
            row.get("dependency_attestation_sha256")
            != dependency_attestation.get("attestation_sha256")
            or row.get("environment_sha256")
            != dependency_attestation.get("environment", {}).get(
                "environment_sha256"
            )
            for row in run_rows.values()
        )
    ):
        audit.error(
            "child_bootstrap_attestations",
            "run summaries do not exactly bind the retained child/dependency/environment observations",
        )
    imported_origins = (
        dependency_attestation.get("installed", {}).get("imported_origins")
        if isinstance(dependency_attestation, Mapping)
        and isinstance(dependency_attestation.get("installed"), Mapping)
        else None
    )
    imported_digest = (
        _canonical_mapping_sha256(imported_origins)
        if isinstance(imported_origins, Mapping)
        else None
    )
    child_origins = manifest.get("child_imported_module_origins")
    expected_child_origins = {
        run_id: {
            "pre": imported_origins,
            "post": imported_origins,
            "sha256": imported_digest,
        }
        for run_id in run_rows
    }
    if (
        child_origins != expected_child_origins
        or any(
            row.get("imported_origins_pre_sha256") != imported_digest
            or row.get("imported_origins_post_sha256") != imported_digest
            for row in run_rows.values()
        )
    ):
        audit.error(
            "child_import_origin_binding",
            "child imported-module origins do not exactly match locked distribution ownership",
        )

    runtime_value = getattr(sys, "_gate7_v3_bootstrap_attestation", None)
    if preterminal:
        runtime = _validate_bootstrap_attestation(
            runtime_value,
            "auditor",
            manifest,
            audit,
            "executing preterminal auditor",
        )
        if (
            runtime is None
            or _bootstrap_common_observation(runtime)
            != _bootstrap_common_observation(parent_bootstrap)
            or dependency_attestation is None
            or not _bootstrap_matches_dependency(runtime, dependency_attestation)
        ):
            audit.error(
                "auditor_bootstrap_attestation",
                "preterminal auditor did not enter through the shared sealed bootstrap boundary",
            )
        return runtime
    return None


def _normalize_text(text: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).strip().split())


def _load_retained_traces(
    bundle: Path,
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> Dict[int, List[Dict[str, Any]]]:
    metadata_by_seed = manifest.get("trace_metadata")
    if not isinstance(metadata_by_seed, dict):
        return {}
    result: Dict[int, List[Dict[str, Any]]] = {}
    for raw_seed, metadata in metadata_by_seed.items():
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError):
            audit.error("trace_seed", "trace metadata seed key is not an integer", seed=raw_seed)
            continue
        if not isinstance(metadata, dict):
            continue
        trace_hash = metadata.get("trace_sha256")
        matches = [
            (name, values)
            for name, values in artifacts.items()
            if name.startswith("traces/")
            and values.get("sha256") == trace_hash
        ]
        if len(matches) != 1:
            audit.error(
                "trace_artifact_binding",
                "seed must bind to exactly one retained trace artifact",
                seed=seed,
                matching_artifacts=[name for name, _ in matches],
            )
            continue
        name, values = matches[0]
        path = bundle / str(values["path"])
        rows = _load_jsonl(path, audit, "trace_jsonl")
        seen_concepts: set = set()
        for position, row in enumerate(rows):
            context = {"seed": seed, "request_position": position}
            if set(row) != TRACE_FIELDS:
                audit.error(
                    "trace_schema",
                    "retained trace row has unexpected fields",
                    fields=sorted(row),
                    **context,
                )
            if row.get("index") != position:
                audit.error(
                    "trace_order",
                    "retained trace indices are not contiguous",
                    observed=row.get("index"),
                    **context,
                )
            concept_id = row.get("concept_id")
            expected_reuse = concept_id in seen_concepts
            if row.get("reuse_opportunity") is not expected_reuse:
                audit.error(
                    "trace_reuse",
                    "reuse-opportunity flag is not reproducible from prior concepts",
                    observed=row.get("reuse_opportunity"),
                    computed=expected_reuse,
                    **context,
                )
            seen_concepts.add(concept_id)
            expected_payload = "recorded-response-v2:%s:%s" % (
                concept_id,
                row.get("text_id"),
            )
            if row.get("response_id") != concept_id or row.get(
                "response_payload"
            ) != expected_payload:
                audit.error(
                    "trace_response_rule",
                    "retained trace response identity violates the frozen rule",
                    **context,
                )
            if row.get("text") != _normalize_text(row.get("text")):
                audit.error(
                    "trace_text_normalization",
                    "retained trace text is not in frozen normalized form",
                    **context,
                )
        declared_rows = metadata.get("requests")
        if declared_rows != len(rows):
            audit.error(
                "trace_artifact_rows",
                "retained trace row count differs from trace metadata",
                seed=seed,
                declared=declared_rows,
                computed=len(rows),
            )
        result[seed] = rows
    return result


def _validate_warmup_artifacts(
    bundle: Path,
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    retained_traces: Mapping[int, Sequence[Mapping[str, Any]]],
    run_rows: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> None:
    if not _is_production_formal(manifest):
        return
    declarations = manifest.get("warmup_artifacts")
    expected_paths = {
        "warmups/seed-%d.json" % seed for seed in PROTOCOL_FULL_SEEDS
    }
    if not isinstance(declarations, Mapping) or set(declarations) != expected_paths:
        audit.error(
            "warmup_artifacts",
            "formal manifest does not declare exactly one warm-up artifact per frozen seed",
        )
        return
    config = manifest.get("config")
    warmup_requests = (
        config.get("warmup_requests") if isinstance(config, Mapping) else None
    )
    if (
        not isinstance(warmup_requests, int)
        or isinstance(warmup_requests, bool)
        or warmup_requests <= 0
    ):
        audit.error(
            "warmup_artifacts",
            "formal warm-up request count is not a positive integer",
        )
        return
    metadata_by_seed = manifest.get("trace_metadata")
    for seed in PROTOCOL_FULL_SEEDS:
        path_name = "warmups/seed-%d.json" % seed
        declaration = declarations.get(path_name)
        computed = artifacts.get(path_name)
        path = bundle / path_name
        if (
            not isinstance(declaration, Mapping)
            or set(declaration) != {"sha256", "bytes", "rows"}
            or not isinstance(computed, Mapping)
            or any(
                declaration.get(field) != computed.get(field)
                for field in ("sha256", "bytes", "rows")
            )
            or not path.is_file()
            or path.is_symlink()
        ):
            audit.error(
                "warmup_artifacts",
                "warm-up artifact is missing or not byte/row-bound",
                seed=seed,
            )
            continue
        observed = _load_json(path, audit, "warmup_artifacts")
        trace = retained_traces.get(seed)
        metadata = (
            metadata_by_seed.get(str(seed))
            if isinstance(metadata_by_seed, Mapping)
            else None
        )
        if not isinstance(trace, Sequence) or not isinstance(metadata, Mapping):
            audit.error(
                "warmup_artifacts",
                "warm-up artifact lacks its retained trace/metadata source",
                seed=seed,
            )
            continue
        ordered_hot = metadata.get("hot_text_ids")
        text_by_id = {
            str(row.get("text_id")): str(row.get("text"))
            for row in trace
            if isinstance(row, Mapping)
        }
        if (
            not isinstance(ordered_hot, list)
            or not ordered_hot
            or len(set(ordered_hot)) != len(ordered_hot)
            or any(text_id not in text_by_id for text_id in ordered_hot)
        ):
            audit.error(
                "warmup_artifacts",
                "trace metadata hot text IDs are not a canonical warm-up source",
                seed=seed,
            )
            continue
        warmup_ids = [
            ordered_hot[index % len(ordered_hot)]
            for index in range(warmup_requests)
        ]
        expected = {
            "schema_version": WARMUP_SCHEMA_VERSION,
            "experiment_id": EXPERIMENT_ID,
            "seed": seed,
            "trace_sha256": metadata.get("trace_sha256"),
            "warmup_requests": warmup_requests,
            "ordered_hot_text_ids": ordered_hot,
            "warmup_text_ids": warmup_ids,
            "warmup_texts": [text_by_id[text_id] for text_id in warmup_ids],
        }
        if observed != expected:
            audit.error(
                "warmup_artifacts",
                "retained warm-up artifact differs from independent trace-derived input",
                seed=seed,
            )
        expected_hash = declaration.get("sha256")
        seed_runs = [
            row
            for row in run_rows.values()
            if _int_value(row.get("seed"), audit, "warmup_artifacts", "seed")
            == seed
        ]
        if len(seed_runs) != len(POLICIES) or any(
            row.get("warmup_artifact_sha256") != expected_hash
            for row in seed_runs
        ):
            audit.error(
                "warmup_artifacts",
                "all three policy runs do not bind the same seed warm-up bytes",
                seed=seed,
            )


def _reconstruct_prepared_semantics(
    pairs_path: Path,
    texts_path: Path,
    audit: _Audit,
    code: str,
    *,
    seed: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Rebuild positive components and deterministic concept IDs from bytes."""

    context = {"seed": seed} if seed is not None else {}
    text_rows = _load_jsonl(texts_path, audit, code)
    pair_rows = _load_jsonl(pairs_path, audit, code)
    expected_pair_fields = {
        "schema_version",
        "source_index",
        "split",
        "label",
        "text_a_id",
        "text_b_id",
        "concept_a",
        "concept_b",
    }
    valid_pairs: List[Dict[str, Any]] = []
    seen_source_indices: set = set()
    exact_labels: Dict[Tuple[str, str], int] = {}
    calibration_text_ids: set = set()
    for position, row in enumerate(pair_rows):
        # The Gate 2 protocol forbids inspecting held-out labels or endpoints.
        # Read only the split discriminator and ignore the rest of a test row.
        split = row.get("split")
        if split == "test":
            continue
        if split != "calibration":
            audit.error(
                code,
                "prepared pair row has an invalid split discriminator",
                position=position,
                observed=split,
                **context,
            )
            continue
        label = row.get("label")
        source_index = row.get("source_index")
        text_a = row.get("text_a_id")
        text_b = row.get("text_b_id")
        if (
            set(row) != expected_pair_fields
            or row.get("schema_version") != "carma-qqp-v1"
            or isinstance(label, bool)
            or label not in (0, 1)
            or isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or source_index < 0
            or not isinstance(text_a, str)
            or not isinstance(text_b, str)
            or not isinstance(row.get("concept_a"), str)
            or not isinstance(row.get("concept_b"), str)
        ):
            audit.error(
                code,
                "prepared pair row has an invalid frozen schema or endpoint",
                position=position,
                **context,
            )
            continue
        if source_index in seen_source_indices:
            audit.error(
                code,
                "prepared calibration/test rows repeat a source index",
                source_index=source_index,
                **context,
            )
            continue
        seen_source_indices.add(source_index)
        if text_a == text_b:
            if label == 0:
                audit.error(
                    code,
                    "prepared negative row repeats one text ID",
                    source_index=source_index,
                    **context,
                )
                continue
        else:
            pair = _pair(text_a, text_b)
            previous_label = exact_labels.setdefault(pair, int(label))
            if previous_label != label:
                audit.error(
                    code,
                    "prepared endpoint pair has conflicting labels",
                    source_index=source_index,
                    **context,
                )
                continue
        valid_pairs.append(dict(row))
        calibration_text_ids.update((text_a, text_b))

    text_by_id: Dict[str, str] = {}
    for position, row in enumerate(text_rows):
        text_id = row.get("text_id")
        if text_id not in calibration_text_ids:
            continue
        text = row.get("text")
        if (
            set(row) != {"text_id", "text"}
            or not isinstance(text_id, str)
            or not text_id
            or not isinstance(text, str)
            or not text
            or text != _normalize_text(text)
            or hashlib.sha256(text.encode("utf-8")).hexdigest()[:24] != text_id
        ):
            audit.error(
                code,
                "prepared calibration text violates normalization or deterministic text identity",
                position=position,
                **context,
            )
            continue
        if text_id in text_by_id:
            audit.error(
                code,
                "prepared calibration texts repeat a text ID",
                text_id=text_id,
                **context,
            )
            continue
        text_by_id[text_id] = text
    missing_text_ids = calibration_text_ids.difference(text_by_id)
    if missing_text_ids:
        audit.error(
            code,
            "prepared calibration pair endpoint is absent from valid retained texts",
            missing_text_ids=sorted(missing_text_ids),
            **context,
        )
        return None
    if not text_by_id:
        audit.error(code, "prepared calibration contains no valid text rows", **context)
        return None

    parent = {text_id: text_id for text_id in text_by_id}

    def find(text_id: str) -> str:
        root = text_id
        while parent[root] != root:
            root = parent[root]
        while parent[text_id] != text_id:
            following = parent[text_id]
            parent[text_id] = root
            text_id = following
        return root

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        parent[high] = low

    for row in valid_pairs:
        if row["label"] == 1:
            union(str(row["text_a_id"]), str(row["text_b_id"]))

    component_text_ids: DefaultDict[str, List[str]] = defaultdict(list)
    for text_id in sorted(calibration_text_ids):
        component_text_ids[find(text_id)].append(text_id)
    concept_by_text_id: Dict[str, str] = {}
    split_by_concept: Dict[str, str] = {}
    members_by_concept: Dict[str, Tuple[str, ...]] = {}
    for text_ids in component_text_ids.values():
        normalized_members = sorted(text_by_id[text_id] for text_id in text_ids)
        identity = hashlib.sha256(
            "\n".join(normalized_members).encode("utf-8")
        ).hexdigest()
        concept_id = "qqp-" + identity[:20]
        if concept_id in members_by_concept:
            audit.error(
                code,
                "deterministic concept-ID collision",
                concept_id=concept_id,
                **context,
            )
            return None
        bucket = int(
            hashlib.sha256(concept_id.encode("utf-8")).hexdigest()[:8], 16
        ) % 100
        split_by_concept[concept_id] = (
            "calibration" if bucket < 20 else "test"
        )
        members_by_concept[concept_id] = tuple(sorted(text_ids))
        for text_id in text_ids:
            concept_by_text_id[text_id] = concept_id

    for row in valid_pairs:
        if row.get("split") != "calibration":
            continue
        text_a = str(row["text_a_id"])
        text_b = str(row["text_b_id"])
        concept_a = concept_by_text_id[text_a]
        concept_b = concept_by_text_id[text_b]
        source_index = int(row["source_index"])
        if (
            row.get("concept_a") != concept_a
            or row.get("concept_b") != concept_b
        ):
            audit.error(
                code,
                "prepared concept field differs from independent positive-component reconstruction",
                source_index=source_index,
                declared=[row.get("concept_a"), row.get("concept_b")],
                computed=[concept_a, concept_b],
                **context,
            )
        expected_split_a = split_by_concept[concept_a]
        expected_split_b = split_by_concept[concept_b]
        if (
            expected_split_a != expected_split_b
            or row.get("split") != expected_split_a
        ):
            audit.error(
                code,
                "prepared split differs from the deterministic component bucket",
                source_index=source_index,
                **context,
            )
        if row.get("label") == 1 and concept_a != concept_b:
            audit.error(
                code,
                "positive prepared row crosses reconstructed components",
                source_index=source_index,
                **context,
            )
        if row.get("label") == 0 and concept_a == concept_b:
            audit.error(
                code,
                "negative prepared row lies inside a reconstructed component",
                source_index=source_index,
                **context,
            )

    return {
        "pairs": valid_pairs,
        "text_by_id": text_by_id,
        "concept_by_text_id": concept_by_text_id,
        "members_by_concept": members_by_concept,
        "split_by_concept": split_by_concept,
    }


def _load_calibration_catalog(
    prepared_dir: Path, audit: _Audit
) -> Optional[Dict[str, Tuple[str, str]]]:
    """Rebuild canonical calibration concepts without importing the producer."""

    pairs_path = prepared_dir / "pairs.jsonl"
    texts_path = prepared_dir / "texts.jsonl"
    manifest_path = prepared_dir / "manifest.json"
    if not all(path.is_file() for path in (pairs_path, texts_path, manifest_path)):
        audit.error(
            "trace_source_missing",
            "prepared QQP sources needed for independent trace reconstruction are missing",
            prepared_dir=str(prepared_dir),
        )
        return None
    source_manifest = _load_json(manifest_path, audit, "trace_source_manifest")
    if source_manifest is None:
        return None
    observed_pairs_hash = _sha256_file(pairs_path)
    observed_texts_hash = _sha256_file(texts_path)
    if source_manifest.get("pairs_sha256") != observed_pairs_hash:
        audit.error(
            "trace_source_integrity",
            "prepared QQP pairs hash differs from its manifest",
        )
    if source_manifest.get("texts_sha256") != observed_texts_hash:
        audit.error(
            "trace_source_integrity",
            "prepared QQP texts hash differs from its manifest",
        )

    reconstructed = _reconstruct_prepared_semantics(
        pairs_path,
        texts_path,
        audit,
        "trace_source_components",
    )
    if reconstructed is None:
        return None
    text_by_id = reconstructed["text_by_id"]
    return {
        concept_id: (
            min(text_ids),
            text_by_id[min(text_ids)],
        )
        for concept_id, text_ids in reconstructed["members_by_concept"].items()
        if reconstructed["split_by_concept"][concept_id] == "calibration"
    }


def _rebuild_expected_trace(
    canonical: Mapping[str, Tuple[str, str]],
    seed: int,
    requests: int,
    capacity: int,
) -> List[Dict[str, Any]]:
    warm_count = 3 * requests // 10
    scan_count = 4 * requests // 10
    return_count = 3 * requests // 10
    hot_count = (4 * capacity) // 5

    def digest(namespace: str, *values: Any) -> bytes:
        payload = namespace + "|" + "|".join(str(value) for value in values)
        return hashlib.sha256(payload.encode("utf-8")).digest()

    ranked = sorted(
        canonical,
        key=lambda concept_id: (
            digest(CANDIDATE_NAMESPACE, seed, canonical[concept_id][0]),
            canonical[concept_id][0],
        ),
    )
    if len(ranked) < hot_count + scan_count:
        raise ValueError("calibration catalog is too small for the frozen trace")
    hot = ranked[:hot_count]
    scan = ranked[hot_count : hot_count + scan_count]

    def phase_rows(phase: str, concepts: Sequence[str], count: int) -> List[Tuple[bytes, str, int]]:
        occurrences: DefaultDict[str, int] = defaultdict(int)
        values: List[Tuple[bytes, str, int]] = []
        for position in range(count):
            concept_id = concepts[position % len(concepts)]
            occurrence = occurrences[concept_id]
            occurrences[concept_id] += 1
            text_id = canonical[concept_id][0]
            values.append(
                (
                    digest(PHASE_NAMESPACE, seed, phase, text_id, occurrence),
                    concept_id,
                    occurrence,
                )
            )
        values.sort(key=lambda item: (item[0], canonical[item[1]][0], item[2]))
        return values

    phases = (
        ("warm", phase_rows("warm", hot, warm_count)),
        ("scan", phase_rows("scan", scan, scan_count)),
        ("return", phase_rows("return", hot, return_count)),
    )
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for phase, values in phases:
        for _, concept_id, occurrence in values:
            text_id, text = canonical[concept_id]
            index = len(rows)
            rows.append(
                {
                    "index": index,
                    "request_id": "gate7b-%d-%04d" % (seed, index),
                    "phase": phase,
                    "occurrence": occurrence,
                    "reuse_opportunity": concept_id in seen,
                    "text_id": text_id,
                    "text": text,
                    "concept_id": concept_id,
                    "response_id": concept_id,
                    "response_payload": "recorded-response-v2:%s:%s"
                    % (concept_id, text_id),
                }
            )
            seen.add(concept_id)
    return rows


def _validate_trace_selection_from_source(
    manifest: Mapping[str, Any],
    retained: Mapping[int, Sequence[Mapping[str, Any]]],
    retained_prepared_dir: Optional[Path],
    audit: _Audit,
) -> None:
    metadata_by_seed = manifest.get("trace_metadata")
    config = manifest.get("config")
    production_formal = _is_production_formal(manifest)
    if not isinstance(metadata_by_seed, dict) or not metadata_by_seed:
        return
    if retained_prepared_dir is None or not retained_prepared_dir.is_dir():
        if production_formal:
            audit.error(
                "trace_source_missing",
                "formal trace source is unavailable from retained attempt copies",
            )
        return
    canonical = _load_calibration_catalog(retained_prepared_dir, audit)
    if canonical is None or not isinstance(config, dict):
        return
    try:
        requests = int(config["requests"])
        capacity = int(config["capacity"])
    except (KeyError, TypeError, ValueError):
        audit.error(
            "trace_source_rebuild",
            "trace configuration cannot be used for independent reconstruction",
        )
        return
    for seed, observed in retained.items():
        try:
            expected = _rebuild_expected_trace(
                canonical, seed, requests, capacity
            )
        except (TypeError, ValueError) as exc:
            audit.error(
                "trace_source_rebuild",
                "independent trace reconstruction failed",
                seed=seed,
                detail=str(exc),
            )
            continue
        if list(observed) != expected:
            audit.error(
                "trace_source_rebuild",
                "retained trace differs from independent frozen selection reconstruction",
                seed=seed,
            )


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_sha1(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _formal_source_anchor_structurally_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    expected_fields = {
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
        set(value) == expected_fields
        and value.get("schema_version") == FORMAL_SOURCE_ANCHOR_SCHEMA_VERSION
        and value.get("tag_name") == FORMAL_SOURCE_TAG
        and value.get("remote_name") == FORMAL_SOURCE_REMOTE
        and value.get("object_format") == "sha1"
        and value.get("tag_object_type") == "tag"
        and _valid_sha1(value.get("tag_object_id"))
        and _valid_sha1(value.get("peeled_commit"))
        and value.get("head_commit") == value.get("peeled_commit")
        and _valid_sha1(value.get("tree_id"))
        and _valid_sha256(value.get("tag_payload_sha256"))
        and isinstance(value.get("tag_payload_bytes"), int)
        and not isinstance(value.get("tag_payload_bytes"), bool)
        and value.get("tag_payload_bytes", 0) > 0
        and value.get("annotation")
        == {
            "experiment_id": EXPERIMENT_ID,
            "contract_sha256": PINNED_CONTRACT_SHA256,
        }
        and value.get("remote")
        == {
            "fetch_urls": [FORMAL_SOURCE_REMOTE_URL],
            "push_urls": [FORMAL_SOURCE_REMOTE_URL],
            "tag_object_id": value.get("tag_object_id"),
            "peeled_commit": value.get("peeled_commit"),
        }
        and value.get("retained_tag_payload_path")
        == FORMAL_SOURCE_TAG_PAYLOAD_PATH
    )


def _power_observation_structurally_valid(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "observed_at_utc",
        "available",
        "plugged",
        "percent",
        "seconds_left",
    }:
        return False
    timestamp = value.get("observed_at_utc")
    try:
        parsed = datetime.fromisoformat(
            timestamp[:-1] + "+00:00"
            if isinstance(timestamp, str) and timestamp.endswith("Z")
            else str(timestamp)
        )
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return False
    available = value.get("available")
    if not isinstance(available, bool):
        return False
    if available is False:
        return all(value.get(field) is None for field in ("plugged", "percent", "seconds_left"))
    return bool(
        isinstance(value.get("plugged"), bool)
        and isinstance(value.get("percent"), (int, float))
        and not isinstance(value.get("percent"), bool)
        and math.isfinite(float(value.get("percent")))
        and 0.0 <= float(value.get("percent")) <= 100.0
        and isinstance(value.get("seconds_left"), (int, float))
        and not isinstance(value.get("seconds_left"), bool)
        and math.isfinite(float(value.get("seconds_left")))
    )


def _formal_entrypoint_structurally_valid(value: Any) -> bool:
    bootstrap = value.get("bootstrap_attestation") if isinstance(value, Mapping) else None
    project_root = (
        bootstrap.get("project_root") if isinstance(bootstrap, Mapping) else None
    )
    parent_cmdline = value.get("parent_cmdline") if isinstance(value, Mapping) else None
    wrapper_argument = (
        parent_cmdline[1]
        if isinstance(parent_cmdline, list)
        and len(parent_cmdline) == 3
        and isinstance(parent_cmdline[1], str)
        else None
    )
    return bool(
        isinstance(value, Mapping)
        and set(value)
        == {
            "schema_version",
            "marker",
            "mode",
            "wrapper_pid",
            "producer_parent_pid",
            "wrapper_path",
            "wrapper_identity",
            "parent_executable",
            "parent_cmdline",
            "bootstrap_attestation",
            "bootstrap_attestation_sha256",
        }
        and value.get("schema_version") == FORMAL_ENTRYPOINT_SCHEMA_VERSION
        and value.get("marker") == FORMAL_ENTRYPOINT_MARKER
        and value.get("mode") == "full"
        and value.get("wrapper_path") == FORMAL_WRAPPER_PATH
        and value.get("parent_executable") == str(Path("/bin/bash").resolve())
        and isinstance(parent_cmdline, list)
        and len(parent_cmdline) == 3
        and isinstance(parent_cmdline[0], str)
        and Path(parent_cmdline[0]).name == "bash"
        and isinstance(wrapper_argument, str)
        and Path(wrapper_argument).is_absolute()
        and not wrapper_argument.startswith("-")
        and isinstance(project_root, str)
        and Path(wrapper_argument).resolve()
        == (Path(project_root) / FORMAL_WRAPPER_PATH).resolve()
        and parent_cmdline[2] == "full"
        and isinstance(value.get("bootstrap_attestation"), Mapping)
        and _valid_sha256(value.get("bootstrap_attestation_sha256"))
    )


def _semantic_declaration_for_seed(
    declarations: Mapping[str, Any], expected_path: str
) -> Optional[Mapping[str, Any]]:
    value = declarations.get(expected_path)
    if isinstance(value, Mapping):
        return value
    return None


def _semantic_pair_records(
    value: Any,
    endpoint_fields: Tuple[str, str],
    field: str,
    seed: int,
    audit: _Audit,
) -> Dict[Tuple[str, str], Tuple[int, ...]]:
    result: Dict[Tuple[str, str], Tuple[int, ...]] = {}
    if not isinstance(value, list):
        audit.error(
            "semantic_index",
            "semantic-index pair records must be a list",
            seed=seed,
            field=field,
        )
        return result
    ordering: List[Tuple[str, str]] = []
    for position, row in enumerate(value):
        expected_fields = {endpoint_fields[0], endpoint_fields[1], "source_indices"}
        if not isinstance(row, dict) or set(row) != expected_fields:
            audit.error(
                "semantic_index",
                "semantic-index pair record has unexpected fields",
                seed=seed,
                field=field,
                position=position,
            )
            continue
        left = row.get(endpoint_fields[0])
        right = row.get(endpoint_fields[1])
        sources = row.get("source_indices")
        if (
            not isinstance(left, str)
            or not left
            or not isinstance(right, str)
            or not right
            or left >= right
            or not isinstance(sources, list)
            or not sources
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in sources
            )
            or sources != sorted(set(sources))
        ):
            audit.error(
                "semantic_index",
                "semantic-index pair record is noncanonical",
                seed=seed,
                field=field,
                position=position,
            )
            continue
        pair = (left, right)
        if pair in result:
            audit.error(
                "semantic_index",
                "semantic-index contains a duplicate pair record",
                seed=seed,
                field=field,
                pair=list(pair),
            )
            continue
        result[pair] = tuple(sources)
        ordering.append(pair)
    if ordering != sorted(ordering):
        audit.error(
            "semantic_index",
            "semantic-index pair records are not sorted",
            seed=seed,
            field=field,
        )
    return result


def _rebuild_semantic_maps_from_pairs(
    pairs_path: Path,
    selected_concepts: Iterable[str],
    audit: _Audit,
    seed: int,
) -> Optional[Dict[str, Any]]:
    """Rebuild v2 negative evidence directly from calibration pair rows."""

    selected = set(selected_concepts)
    reconstructed = _reconstruct_prepared_semantics(
        pairs_path,
        pairs_path.with_name("texts.jsonl"),
        audit,
        "semantic_index_source",
        seed=seed,
    )
    if reconstructed is None:
        return None
    missing = selected.difference(reconstructed["members_by_concept"])
    noncalibration = {
        concept_id
        for concept_id in selected
        if reconstructed["split_by_concept"].get(concept_id) != "calibration"
    }
    if missing or noncalibration:
        audit.error(
            "semantic_index_source",
            "selected semantic-index concepts are not reconstructed calibration components",
            seed=seed,
            missing=sorted(missing),
            noncalibration=sorted(noncalibration),
        )
        return None
    members = {
        text_id: concept_id
        for concept_id in selected
        for text_id in reconstructed["members_by_concept"][concept_id]
    }
    direct: DefaultDict[Tuple[str, str], set] = defaultdict(set)
    component: DefaultDict[Tuple[str, str], set] = defaultdict(set)
    derived_concepts = reconstructed["concept_by_text_id"]
    for row in reconstructed["pairs"]:
        if row.get("split") != "calibration" or row.get("label") != 0:
            continue
        text_a = str(row["text_a_id"])
        text_b = str(row["text_b_id"])
        concept_a = derived_concepts[text_a]
        concept_b = derived_concepts[text_b]
        if concept_a in selected and concept_b in selected:
            source_index = int(row["source_index"])
            direct[_pair(text_a, text_b)].add(source_index)
            component[_pair(concept_a, concept_b)].add(source_index)
    canonical: Dict[str, str] = {}
    for concept_id in sorted(selected):
        text_ids = sorted(
            text_id
            for text_id, observed_concept in members.items()
            if observed_concept == concept_id
        )
        if not text_ids:
            audit.error(
                "semantic_index_source",
                "selected concept is absent from prepared calibration rows",
                seed=seed,
                concept_id=concept_id,
            )
            return None
        canonical[concept_id] = text_ids[0]
    return {
        "text_id_to_concept_id": dict(sorted(members.items())),
        "canonical_text_by_concept": canonical,
        "direct_negative_pairs": {
            pair: tuple(sorted(indices)) for pair, indices in sorted(direct.items())
        },
        "component_negative_pairs": {
            pair: tuple(sorted(indices))
            for pair, indices in sorted(component.items())
        },
    }


def _load_semantic_indexes(
    bundle: Path,
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    retained_traces: Mapping[int, Sequence[Mapping[str, Any]]],
    retained_prepared_dir: Optional[Path],
    audit: _Audit,
) -> Dict[int, Dict[str, Any]]:
    """Load and independently validate every retained v2 semantic index."""

    declarations = manifest.get("semantic_index_artifacts")
    if not isinstance(declarations, dict):
        audit.error(
            "semantic_index_manifest",
            "manifest semantic_index_artifacts must be an object",
        )
        return {}
    raw_seeds = manifest.get("seeds")
    seeds: List[int] = []
    if isinstance(raw_seeds, list):
        for value in raw_seeds:
            try:
                if isinstance(value, bool):
                    raise ValueError
                seeds.append(int(value))
            except (TypeError, ValueError, OverflowError):
                audit.error(
                    "semantic_index_manifest",
                    "manifest seed cannot identify a semantic index",
                    observed=value,
                )
    result: Dict[int, Dict[str, Any]] = {}
    for seed in seeds:
        relative = "semantic-indexes/seed-%d.json" % seed
        declaration = _semantic_declaration_for_seed(
            declarations, relative
        )
        if declaration is None:
            audit.error(
                "semantic_index_manifest",
                "manifest omits a semantic-index identity for one seed",
                seed=seed,
                expected_path=relative,
            )
            continue
        declared_path = declaration.get("path", relative)
        if declared_path != relative:
            audit.error(
                "semantic_index_path",
                "semantic-index path is not the frozen per-seed path",
                seed=seed,
                observed=declared_path,
                expected=relative,
            )
            continue
        computed_identity = artifacts.get(relative)
        if not isinstance(computed_identity, Mapping):
            audit.error(
                "semantic_index_manifest",
                "semantic-index must also be a verified manifest artifact",
                seed=seed,
                path=relative,
            )
            continue
        for field in ("sha256", "bytes"):
            if declaration.get(field) != computed_identity.get(field):
                audit.error(
                    "semantic_index_identity",
                    "semantic-index identity differs from verified artifact bytes",
                    seed=seed,
                    field=field,
                    declared=declaration.get(field),
                    computed=computed_identity.get(field),
                )
        path = bundle / relative
        value = _load_json(path, audit, "semantic_index_read")
        if value is None:
            continue
        expected_artifact_fields = {
            "schema_version",
            "semantic_index_schema_version",
            "seed",
            "trace_sha256",
            "semantic_index_sha256",
            "canonical_row_count",
            "counts",
            "source_pairs_sha256",
            "source_texts_sha256",
            "source_manifest_sha256",
            "selected_concepts",
            "component_members",
            "direct_negative_pairs",
            "component_negative_pairs",
        }
        if set(value) != expected_artifact_fields:
            audit.error(
                "semantic_index_schema",
                "semantic-index artifact has unexpected or missing fields",
                seed=seed,
                observed=sorted(value),
                expected=sorted(expected_artifact_fields),
            )
        if value.get("schema_version") != SEMANTIC_ARTIFACT_SCHEMA_VERSION:
            audit.error(
                "semantic_index_schema",
                "semantic-index artifact has the wrong schema version",
                seed=seed,
                observed=value.get("schema_version"),
            )
        if value.get("semantic_index_schema_version") != SEMANTIC_INDEX_SCHEMA_VERSION:
            audit.error(
                "semantic_index_schema",
                "semantic-index canonical data has the wrong schema version",
                seed=seed,
                observed=value.get("semantic_index_schema_version"),
            )
        if value.get("seed") != seed:
            audit.error(
                "semantic_index_identity",
                "semantic-index seed differs from its path",
                seed=seed,
                observed=value.get("seed"),
            )
        trace_hash = value.get("trace_sha256")
        trace_metadata = manifest.get("trace_metadata")
        expected_trace_hash = None
        if isinstance(trace_metadata, dict):
            metadata = trace_metadata.get(str(seed))
            if isinstance(metadata, dict):
                expected_trace_hash = metadata.get("trace_sha256")
        if (
            not _valid_sha256(trace_hash)
            or trace_hash != expected_trace_hash
        ):
            audit.error(
                "semantic_index_identity",
                "semantic-index trace identity differs from the retained trace",
                seed=seed,
                declared=trace_hash,
                expected=expected_trace_hash,
            )
        for field in (
            "source_pairs_sha256",
            "source_texts_sha256",
            "source_manifest_sha256",
        ):
            expected = FROZEN_SOURCE_HASHES.get(field)
            if expected is None and isinstance(trace_metadata, dict):
                metadata = trace_metadata.get(str(seed))
                if isinstance(metadata, dict):
                    expected = metadata.get(field)
            if expected is not None and value.get(field) != expected:
                audit.error(
                    "semantic_index_source",
                    "semantic-index source identity differs from the frozen source",
                    seed=seed,
                    field=field,
                    observed=value.get(field),
                    expected=expected,
                )
            elif expected is None and not _valid_sha256(value.get(field)):
                audit.error(
                    "semantic_index_source",
                    "semantic-index source identity is not a SHA-256",
                    seed=seed,
                    field=field,
                )
        selected_records = value.get("selected_concepts")
        canonical: Dict[str, str] = {}
        if not isinstance(selected_records, list) or not selected_records:
            audit.error(
                "semantic_index",
                "semantic-index selected_concepts must be a nonempty list",
                seed=seed,
            )
            selected_records = []
        selected_order: List[Tuple[str, str]] = []
        for position, record in enumerate(selected_records):
            if not isinstance(record, dict) or set(record) != {
                "concept_id",
                "canonical_text_id",
            }:
                audit.error(
                    "semantic_index",
                    "selected concept record has unexpected fields",
                    seed=seed,
                    position=position,
                )
                continue
            concept_id = record.get("concept_id")
            text_id = record.get("canonical_text_id")
            if not all(isinstance(item, str) and item for item in (concept_id, text_id)):
                audit.error(
                    "semantic_index",
                    "selected concept record has an invalid identity",
                    seed=seed,
                    position=position,
                )
                continue
            if concept_id in canonical:
                audit.error(
                    "semantic_index",
                    "semantic-index repeats a selected concept",
                    seed=seed,
                    concept_id=concept_id,
                )
                continue
            canonical[concept_id] = text_id
            selected_order.append((concept_id, text_id))
        if selected_order != sorted(selected_order):
            audit.error(
                "semantic_index",
                "selected concept records are not sorted",
                seed=seed,
            )

        raw_mapping = value.get("component_members")
        mapping: Dict[str, str] = {}
        if not isinstance(raw_mapping, list) or not raw_mapping:
            audit.error(
                "semantic_index",
                "semantic-index text-to-concept mapping must be nonempty",
                seed=seed,
            )
        else:
            member_order: List[Tuple[str, str]] = []
            for position, record in enumerate(raw_mapping):
                if not isinstance(record, dict) or set(record) != {
                    "text_id",
                    "concept_id",
                }:
                    audit.error(
                        "semantic_index",
                        "component member record has unexpected fields",
                        seed=seed,
                        position=position,
                    )
                    continue
                text_id = record.get("text_id")
                concept_id = record.get("concept_id")
                if not (
                    isinstance(text_id, str)
                    and text_id
                    and isinstance(concept_id, str)
                    and concept_id
                    and concept_id in canonical
                ):
                    audit.error(
                        "semantic_index",
                        "semantic-index mapping contains an invalid identity",
                        seed=seed,
                        text_id=text_id,
                        concept_id=concept_id,
                    )
                    continue
                if text_id in mapping:
                    audit.error(
                        "semantic_index",
                        "semantic-index repeats a component member",
                        seed=seed,
                        text_id=text_id,
                    )
                    continue
                mapping[text_id] = concept_id
                member_order.append((text_id, concept_id))
            if member_order != sorted(member_order):
                audit.error(
                    "semantic_index",
                    "component member records are not sorted",
                    seed=seed,
                )
        if any(mapping.get(text_id) != concept_id for concept_id, text_id in canonical.items()):
            audit.error(
                "semantic_index",
                "canonical text is not a member of its selected concept",
                seed=seed,
            )
        for trace_row in retained_traces.get(seed, ()):
            text_id = trace_row.get("text_id")
            concept_id = trace_row.get("concept_id")
            if mapping.get(text_id) != concept_id:
                audit.error(
                    "semantic_index_trace",
                    "semantic-index mapping differs from the retained trace",
                    seed=seed,
                    text_id=text_id,
                    trace_concept_id=concept_id,
                    index_concept_id=mapping.get(text_id),
                )
        direct_pairs = _semantic_pair_records(
            value.get("direct_negative_pairs"),
            ("text_id_a", "text_id_b"),
            "direct_negative_pairs",
            seed,
            audit,
        )
        component_pairs = _semantic_pair_records(
            value.get("component_negative_pairs"),
            ("concept_id_a", "concept_id_b"),
            "component_negative_pairs",
            seed,
            audit,
        )
        if any(left not in mapping or right not in mapping for left, right in direct_pairs):
            audit.error(
                "semantic_index",
                "direct-negative pair references a text outside the index mapping",
                seed=seed,
            )
        known_concepts = set(mapping.values())
        if any(
            left not in known_concepts or right not in known_concepts
            for left, right in component_pairs
        ):
            audit.error(
                "semantic_index",
                "component-negative pair references a concept outside the index mapping",
                seed=seed,
            )
        for text_pair, sources in direct_pairs.items():
            if text_pair[0] in mapping and text_pair[1] in mapping:
                concept_pair = _pair(mapping[text_pair[0]], mapping[text_pair[1]])
                if not set(sources).issubset(component_pairs.get(concept_pair, ())):
                    audit.error(
                        "semantic_index",
                        "direct-negative evidence is not supported by its component pair",
                        seed=seed,
                        pair=list(text_pair),
                    )

        production_formal = _is_production_formal(manifest)
        if retained_prepared_dir is not None and retained_prepared_dir.is_dir():
            pairs_path = retained_prepared_dir / "pairs.jsonl"
            if (
                not pairs_path.is_file()
                or _sha256_file(pairs_path) != value.get("source_pairs_sha256")
            ):
                audit.error(
                    "semantic_index_source",
                    "prepared pairs bytes differ from the semantic-index source identity",
                    seed=seed,
                    path=str(pairs_path),
                )
            else:
                rebuilt = _rebuild_semantic_maps_from_pairs(
                    pairs_path, canonical, audit, seed
                )
                if rebuilt is not None and (
                    rebuilt["text_id_to_concept_id"] != mapping
                    or rebuilt["canonical_text_by_concept"] != canonical
                    or rebuilt["direct_negative_pairs"] != direct_pairs
                    or rebuilt["component_negative_pairs"] != component_pairs
                ):
                    audit.error(
                        "semantic_index_source_rebuild",
                        "semantic-index mappings/negative evidence differ from independent calibration reconstruction",
                        seed=seed,
                    )
        elif production_formal:
            audit.error(
                "semantic_index_source_missing",
                "formal semantic index cannot be rebuilt without retained prepared QQP data",
                seed=seed,
            )

        counts = value.get("counts")
        if not isinstance(counts, dict) or any(
            not isinstance(name, str)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for name, count in (counts.items() if isinstance(counts, dict) else ())
        ):
            audit.error(
                "semantic_index",
                "semantic-index counts are malformed",
                seed=seed,
            )
            counts = {}
        canonical_direct_components = set()
        for text_pair in direct_pairs:
            if text_pair[0] not in mapping or text_pair[1] not in mapping:
                continue
            concept_pair = _pair(mapping[text_pair[0]], mapping[text_pair[1]])
            if {
                canonical.get(concept_pair[0]),
                canonical.get(concept_pair[1]),
            } == set(text_pair):
                canonical_direct_components.add(concept_pair)
        component_pair_set = set(component_pairs)
        derived_component_pairs = component_pair_set.difference(
            canonical_direct_components
        )
        distinct_pairs = len(canonical) * (len(canonical) - 1) // 2
        expected_counts = {
            "selected_concepts": len(canonical),
            "selected_texts": len(mapping),
            "direct_negative_pairs": len(canonical_direct_components),
            "direct_negative_text_pairs": len(direct_pairs),
            "component_negative_pairs": len(component_pair_set),
            "negative_component_derived_pairs": len(derived_component_pairs),
            "unlabeled_cross_component_pairs": distinct_pairs
            - len(canonical_direct_components)
            - len(derived_component_pairs),
            "distinct_candidate_pairs": distinct_pairs,
        }
        for count_name, expected_count in expected_counts.items():
            if counts.get(count_name) != expected_count:
                audit.error(
                    "semantic_index_counts",
                    "semantic-index count cannot be independently reproduced",
                    seed=seed,
                    field=count_name,
                    declared=counts.get(count_name),
                    computed=expected_count,
                )
        canonical_rows: List[Dict[str, Any]] = []
        canonical_rows.extend(
            {
                "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
                "kind": "selected_concept",
                "concept_id": concept_id,
                "canonical_text_id": text_id,
            }
            for concept_id, text_id in sorted(canonical.items())
        )
        canonical_rows.extend(
            {
                "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
                "kind": "component_member",
                "text_id": text_id,
                "concept_id": concept_id,
            }
            for text_id, concept_id in sorted(mapping.items())
        )
        canonical_rows.extend(
            {
                "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
                "kind": "direct_negative_pair",
                "text_id_a": pair[0],
                "text_id_b": pair[1],
                "source_indices": list(sources),
            }
            for pair, sources in sorted(direct_pairs.items())
        )
        canonical_rows.extend(
            {
                "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
                "kind": "component_negative_pair",
                "concept_id_a": pair[0],
                "concept_id_b": pair[1],
                "source_indices": list(sources),
            }
            for pair, sources in sorted(component_pairs.items())
        )
        canonical_lines = sorted(
            json.dumps(row, sort_keys=True, separators=(",", ":"))
            for row in canonical_rows
        )
        canonical_digest = hashlib.sha256()
        for line in canonical_lines:
            canonical_digest.update(line.encode("utf-8"))
            canonical_digest.update(b"\n")
        if (
            value.get("canonical_row_count") != len(canonical_lines)
            or value.get("semantic_index_sha256") != canonical_digest.hexdigest()
        ):
            audit.error(
                "semantic_index_canonical_hash",
                "semantic-index canonical row count/hash cannot be independently reproduced",
                seed=seed,
            )
        if isinstance(trace_metadata, dict):
            metadata = trace_metadata.get(str(seed))
            if isinstance(metadata, dict) and (
                metadata.get("semantic_index_schema_version")
                != SEMANTIC_INDEX_SCHEMA_VERSION
                or metadata.get("semantic_index_sha256")
                != value.get("semantic_index_sha256")
                or metadata.get("semantic_index_canonical_row_count")
                != value.get("canonical_row_count")
                or metadata.get("semantic_index_counts") != value.get("counts")
            ):
                audit.error(
                    "semantic_index_trace",
                    "trace metadata semantic-index identity differs from retained artifact",
                    seed=seed,
                )
        result[seed] = {
            "path": relative,
            "sha256": computed_identity.get("sha256"),
            "trace_sha256": trace_hash,
            "source_pairs_sha256": value.get("source_pairs_sha256"),
            "text_id_to_concept_id": mapping,
            "direct_negative_pairs": direct_pairs,
            "component_negative_pairs": component_pairs,
            "raw": value,
        }
    expected_keys = {
        "semantic-indexes/seed-%d.json" % seed for seed in seeds
    }
    unexpected = [
        key
        for key in declarations
        if key not in expected_keys
    ]
    if unexpected:
        audit.error(
            "semantic_index_manifest",
            "manifest contains cross-version or unexpected semantic-index identities",
            unexpected=sorted(unexpected),
        )
    if len(declarations) != len(set(seeds)):
        audit.error(
            "semantic_index_manifest",
            "manifest must contain exactly one semantic-index identity per seed",
            declared=len(declarations),
            expected=len(set(seeds)),
        )
    return result


def _pair(left: str, right: str) -> Tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _max_cache_size(rows: Sequence[Mapping[str, Any]]) -> int:
    """Return a no-throw extent; malformed values are rejected elsewhere."""

    values: List[int] = []
    for row in rows:
        for field in ("cache_size_before", "cache_size_after"):
            value = row.get(field)
            if isinstance(value, bool):
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            values.append(parsed)
    return max(values, default=0)


def _semantic_expectation(
    row: Mapping[str, Any],
    index: Mapping[str, Any],
    trace_by_text_id: Mapping[str, Mapping[str, Any]],
    capacity: int,
) -> Dict[str, Any]:
    """Recompute one row without trusting producer classifications/counters."""

    raw_hit = row.get("raw_hit") is True
    request_text_id = row.get("text_id")
    request_concept = index["text_id_to_concept_id"].get(request_text_id)
    expected_response_id = row.get("expected_response_id")
    returned_response_id = row.get("returned_response_id")
    returned_concept_id = row.get("returned_concept_id")
    cached_text_id = row.get("cached_source_text_id")
    cached_concept = row.get("cached_source_concept_id")
    payload_text_id = row.get("payload_source_text_id")
    payload_concept = row.get("payload_source_concept_id")
    cached_answer_raw = row.get("cached_answer_raw_identity")
    cached_answer_text_id = row.get("cached_answer_source_text_id")
    cached_answer_concept = row.get("cached_answer_source_concept_id")
    known_concepts = set(index["text_id_to_concept_id"].values())

    cache_mapping_valid = (
        isinstance(cached_text_id, str)
        and index["text_id_to_concept_id"].get(cached_text_id) == cached_concept
    )
    payload_mapping_valid = (
        isinstance(payload_text_id, str)
        and index["text_id_to_concept_id"].get(payload_text_id) == payload_concept
    )
    cached_answer_valid = False
    if isinstance(cached_answer_raw, str):
        prefix = "recorded-response-v2:"
        parts = (
            cached_answer_raw[len(prefix) :].split(":")
            if cached_answer_raw.startswith(prefix)
            else []
        )
        cached_answer_valid = bool(
            len(parts) == 2
            and all(parts)
            and parts[0] == cached_answer_concept
            and parts[1] == cached_answer_text_id
            and index["text_id_to_concept_id"].get(cached_answer_text_id)
            == cached_answer_concept
            and row.get("cached_answer_sha256")
            == hashlib.sha256(cached_answer_raw.encode("utf-8")).hexdigest()
        )
    hash_valid = False
    cached_trace = trace_by_text_id.get(str(cached_text_id))
    if isinstance(cached_trace, Mapping):
        expected_hash = hashlib.sha256(
            _normalize_text(cached_trace.get("text")).encode("utf-8")
        ).hexdigest()
        hash_valid = row.get("cached_question_text_sha256") == expected_hash
    lookup_valid = (
        raw_hit
        and isinstance(row.get("top_candidate_id"), int)
        and not isinstance(row.get("top_candidate_id"), bool)
        and row.get("candidate_cache_data_seen") is True
        and row.get("candidate_cache_data_present") is True
        and cache_mapping_valid
        and cached_answer_valid
        and hash_valid
        and row.get("top_candidate_text_id") == cached_text_id
        and row.get("candidate_cache_lookup_count") == 1
    )
    provenance_resolved = (not raw_hit) or lookup_valid
    response_id_mismatch = returned_response_id != expected_response_id
    source_response_mismatch = bool(
        (
            raw_hit
            and provenance_resolved
            and (
                payload_text_id != cached_text_id
                or payload_concept != cached_concept
                or cached_answer_text_id != cached_text_id
                or cached_answer_concept != cached_concept
                or cached_answer_text_id != payload_text_id
                or cached_answer_concept != payload_concept
                or returned_concept_id != cached_concept
                or returned_response_id != cached_concept
            )
        )
        or (
            not raw_hit
            and (
                payload_text_id != request_text_id
                or payload_concept != request_concept
                or returned_concept_id != request_concept
                or returned_response_id != request_concept
            )
        )
    )
    provenance_consistent = bool(
        provenance_resolved
        and not source_response_mismatch
        and (
            not raw_hit
            or (
                lookup_valid
                and payload_mapping_valid
                and cached_text_id == payload_text_id
                and cached_concept == payload_concept
                and cached_answer_text_id == cached_text_id
                and cached_answer_concept == cached_concept
                and payload_concept == returned_concept_id
                and payload_concept == returned_response_id
            )
        )
    )
    unrecognized_response_id = returned_response_id not in known_concepts
    stale_candidate = bool(
        row.get("top_candidate_id") is not None
        and row.get("candidate_cache_data_seen") is True
        and row.get("candidate_cache_data_present") is False
    )
    try:
        capacity_exceeded = max(
            int(row.get("cache_size_before")), int(row.get("cache_size_after"))
        ) > capacity
    except (TypeError, ValueError, OverflowError):
        capacity_exceeded = True
    structural_reasons: List[str] = []
    if raw_hit and not provenance_resolved:
        structural_reasons.append("unresolved_provenance")
    if source_response_mismatch or (
        provenance_resolved and not provenance_consistent
    ):
        structural_reasons.append("response_provenance_corruption")
    if unrecognized_response_id:
        structural_reasons.append("unrecognized_response_id")
    if stale_candidate:
        structural_reasons.append("stale_candidate")
    if capacity_exceeded:
        structural_reasons.append("capacity_exceeded")
    structural_failure = bool(structural_reasons)

    if not raw_hit:
        hit_class = "miss"
        semantic_relation = "not_applicable"
        semantic_status = "not_applicable"
        semantic_label = None
        semantic_evidence_kind = "none"
        semantic_source_indices: List[int] = []
    elif not provenance_resolved or not provenance_consistent:
        hit_class = "unresolved"
        semantic_relation = "not_applicable"
        semantic_status = "not_applicable"
        semantic_label = None
        semantic_evidence_kind = "none"
        semantic_source_indices = []
    elif cached_concept == request_concept:
        hit_class = "same_concept"
        semantic_relation = "positive_same_component"
        semantic_status = "valid"
        semantic_label = 1
        semantic_evidence_kind = "positive_component"
        semantic_source_indices = []
    elif _pair(str(request_text_id), str(cached_text_id)) in index[
        "direct_negative_pairs"
    ]:
        hit_class = "direct_negative"
        semantic_relation = "negative_direct"
        semantic_status = "invalid"
        semantic_label = 0
        semantic_evidence_kind = "direct_pair_label"
        semantic_source_indices = list(
            index["direct_negative_pairs"][
                _pair(str(request_text_id), str(cached_text_id))
            ]
        )
    elif _pair(str(request_concept), str(cached_concept)) in index[
        "component_negative_pairs"
    ]:
        hit_class = "component_derived_negative"
        semantic_relation = "negative_component_derived"
        semantic_status = "invalid"
        semantic_label = 0
        semantic_evidence_kind = "component_pair_label"
        semantic_source_indices = list(
            index["component_negative_pairs"][
                _pair(str(request_concept), str(cached_concept))
            ]
        )
    else:
        hit_class = "unlabeled_cross_concept"
        semantic_relation = "unlabeled_cross_component"
        semantic_status = "indeterminate"
        semantic_label = None
        semantic_evidence_kind = "no_label"
        semantic_source_indices = []
    return {
        "hit_class": hit_class,
        "semantic_relation": semantic_relation,
        "semantic_status": semantic_status,
        "semantic_label": semantic_label,
        "semantic_evidence_kind": semantic_evidence_kind,
        "semantic_source_indices": semantic_source_indices,
        "provenance_resolved": provenance_resolved,
        "provenance_consistent": provenance_consistent,
        "response_id_mismatch": response_id_mismatch,
        "source_response_mismatch": source_response_mismatch,
        "unrecognized_response_id": unrecognized_response_id,
        "stale_candidate": stale_candidate,
        "capacity_exceeded": capacity_exceeded,
        "structural_failure": structural_failure,
        "structural_failure_reasons": "|".join(structural_reasons),
    }


def _recompute_semantic_guardrail(
    grouped_requests: Mapping[str, Sequence[Mapping[str, Any]]],
    run_rows: Mapping[str, Mapping[str, Any]],
    retained_traces: Mapping[int, Sequence[Mapping[str, Any]]],
    semantic_indexes: Mapping[int, Mapping[str, Any]],
    audit: _Audit,
) -> Dict[str, Any]:
    per_run: Dict[str, Dict[str, Any]] = {}
    aggregate: Counter = Counter()
    trace_lookup = {
        seed: {str(row.get("text_id")): row for row in rows}
        for seed, rows in retained_traces.items()
    }
    class_to_counter = {
        "same_concept": "same_concept_hits",
        "direct_negative": "direct_negative_hits",
        "component_derived_negative": "component_derived_negative_hits",
        "unlabeled_cross_concept": "unlabeled_cross_concept_hits",
        "unresolved": "unresolved_provenance_hits",
    }
    for run_id, rows in grouped_requests.items():
        summary = run_rows.get(run_id)
        if not isinstance(summary, Mapping):
            continue
        seed = _int_value(
            summary.get("seed"), audit, "semantic_run", "seed", run_id=run_id
        )
        capacity = _int_value(
            summary.get("capacity"),
            audit,
            "semantic_run",
            "capacity",
            run_id=run_id,
        )
        if seed is None or capacity is None or seed not in semantic_indexes:
            continue
        if summary.get("semantic_index_sha256") != semantic_indexes[seed][
            "raw"
        ].get("semantic_index_sha256"):
            audit.error(
                "semantic_index_run_binding",
                "run summary is not bound to the verified canonical semantic index",
                run_id=run_id,
                declared=summary.get("semantic_index_sha256"),
                expected=semantic_indexes[seed]["raw"].get(
                    "semantic_index_sha256"
                ),
            )
        if summary.get("semantic_index_artifact_sha256") != semantic_indexes[
            seed
        ].get("sha256"):
            audit.error(
                "semantic_index_run_binding",
                "run summary is not bound to the verified semantic-index artifact bytes",
                run_id=run_id,
                declared=summary.get("semantic_index_artifact_sha256"),
                expected=semantic_indexes[seed].get("sha256"),
            )
        counters: Counter = Counter()
        for position, row in enumerate(rows):
            expected = _semantic_expectation(
                row, semantic_indexes[seed], trace_lookup.get(seed, {}), capacity
            )
            for field, value in expected.items():
                if row.get(field) != value:
                    audit.error(
                        "semantic_recomputation",
                        "request semantic/provenance field differs from independent recomputation",
                        run_id=run_id,
                        request_position=position,
                        field=field,
                        declared=row.get(field),
                        computed=value,
                    )
            hit_class = expected["hit_class"]
            if hit_class == "miss":
                counters["misses"] += 1
            else:
                counters["hits"] += 1
                counters[class_to_counter[hit_class]] += 1
            counters["labeled_negative_hits"] += int(
                hit_class in ("direct_negative", "component_derived_negative")
            )
            counters["cross_concept_hits"] += int(
                hit_class
                in (
                    "direct_negative",
                    "component_derived_negative",
                    "unlabeled_cross_concept",
                )
            )
            for flag, counter_name in (
                ("response_id_mismatch", "response_id_mismatches"),
                ("source_response_mismatch", "source_response_mismatches"),
                ("unrecognized_response_id", "unrecognized_response_ids"),
                ("stale_candidate", "stale_candidates"),
                ("capacity_exceeded", "capacity_excess_requests"),
                ("structural_failure", "structural_failure_requests"),
            ):
                counters[counter_name] += int(expected[flag])
            counters["structural_failure_flags_total"] += len(
                [
                    reason
                    for reason in str(
                        expected["structural_failure_reasons"]
                    ).split("|")
                    if reason
                ]
            )
        counters["max_cache_size"] = _max_cache_size(rows)
        guardrail = (
            "FAIL"
            if counters["labeled_negative_hits"]
            else "PENDING_INDETERMINATE"
            if counters["unlabeled_cross_concept_hits"]
            else "NO_HITS"
            if counters["hits"] == 0
            else "PASS_OBSERVED"
        )
        expected_summary = {
            "hits": counters["hits"],
            "misses": counters["misses"],
            "same_concept_hits": counters["same_concept_hits"],
            "direct_negative_hits": counters["direct_negative_hits"],
            "component_derived_negative_hits": counters[
                "component_derived_negative_hits"
            ],
            "unlabeled_cross_concept_hits": counters[
                "unlabeled_cross_concept_hits"
            ],
            "unresolved_provenance_hits": counters[
                "unresolved_provenance_hits"
            ],
            "labeled_negative_hits": counters["labeled_negative_hits"],
            "cross_concept_hits": counters["cross_concept_hits"],
            "response_id_mismatches": counters["response_id_mismatches"],
            "source_response_mismatches": counters[
                "source_response_mismatches"
            ],
            "unrecognized_response_ids": counters["unrecognized_response_ids"],
            "stale_candidates": counters["stale_candidates"],
            "capacity_excess_requests": counters["capacity_excess_requests"],
            "structural_failure_requests": counters[
                "structural_failure_requests"
            ],
            "structural_failure_flags_total": counters[
                "structural_failure_flags_total"
            ],
            "max_cache_size": counters["max_cache_size"],
        }
        for field, computed in expected_summary.items():
            observed = _int_value(
                summary.get(field),
                audit,
                "semantic_run_counter",
                field,
                run_id=run_id,
            )
            if observed != computed:
                audit.error(
                    "semantic_run_counter",
                    "run semantic/structural counter differs from request evidence",
                    run_id=run_id,
                    field=field,
                    declared=observed,
                    computed=computed,
                )
        if summary.get("semantic_guardrail_status") != guardrail:
            audit.error(
                "semantic_guardrail_declaration",
                "run semantic guardrail status differs from recomputed observations",
                run_id=run_id,
                declared=summary.get("semantic_guardrail_status"),
                computed=guardrail,
            )
        if _bool_value(summary.get("structural_valid")) is not (
            counters["structural_failure_requests"] == 0
        ):
            audit.error(
                "structural_declaration",
                "run structural_valid differs from request evidence",
                run_id=run_id,
            )
        if summary.get("storage_failure") not in (None, ""):
            audit.error(
                "structural_declaration",
                "completed run retains a storage verification failure",
                run_id=run_id,
                observed=summary.get("storage_failure"),
            )
        per_run[run_id] = {
            **expected_summary,
            "semantic_guardrail_status": guardrail,
            "structural_valid": counters["structural_failure_requests"] == 0,
        }
        aggregate.update(counters)
    aggregate_status = (
        "FAIL"
        if aggregate["labeled_negative_hits"]
        else "PENDING_INDETERMINATE"
        if aggregate["unlabeled_cross_concept_hits"]
        else "NO_HITS"
        if aggregate["hits"] == 0
        else "PASS_OBSERVED"
    )
    return {
        "semantic_guardrail_status": aggregate_status,
        "per_run": per_run,
        "aggregate_counts": dict(sorted(aggregate.items())),
    }


def _int_value(
    value: Any, audit: _Audit, code: str, field: str, **context: Any
) -> Optional[int]:
    if isinstance(value, bool):
        audit.error(code, "integer field cannot be boolean", field=field, **context)
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        audit.error(code, "field is not an integer", field=field, value=value, **context)
        return None
    if isinstance(value, float) and not value.is_integer():
        audit.error(code, "integer field has a fractional value", field=field, value=value, **context)
        return None
    return parsed


def _decimal_value(value: Any) -> Optional[Decimal]:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _bool_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return None


def _float_matches(observed: Any, expected: float, tolerance: str) -> bool:
    parsed = _decimal_value(observed)
    if parsed is None:
        return False
    return abs(parsed - Decimal(str(expected))) <= Decimal(tolerance)


def nearest_rank(values: Sequence[int], quantile: float) -> int:
    """Return the frozen one-based nearest-rank quantile."""

    if not values:
        raise ValueError("nearest_rank requires at least one value")
    if not 0.0 < quantile <= 1.0:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(int(value) for value in values)
    return ordered[int(math.ceil(quantile * len(ordered))) - 1]


def _latency_recomputation(values: Sequence[int]) -> Dict[str, Any]:
    return {
        "samples": len(values),
        "mean_ns": sum(values) / len(values),
        "p50_ns": nearest_rank(values, 0.50),
        "p95_ns": nearest_rank(values, 0.95),
        "p99_ns": nearest_rank(values, 0.99),
    }


def _field_from_aliases(row: Mapping[str, Any], aliases: Sequence[str]) -> Optional[Any]:
    for name in aliases:
        if name in row:
            return row[name]
    return None


def _validate_request_row(
    row: Mapping[str, Any], audit: _Audit, run_id: str, position: int, capacity: int
) -> bool:
    context = {"run_id": run_id, "request_position": position}
    valid = True
    for field in ("top_candidate_text_id", "candidate_cache_lookup_count"):
        if field not in row:
            audit.error(
                "request_provenance_schema",
                "v2 request omits a fixed candidate-provenance field",
                field=field,
                **context,
            )
            valid = False
    lookup_count = _int_value(
        row.get("candidate_cache_lookup_count"),
        audit,
        "request_provenance_schema",
        "candidate_cache_lookup_count",
        **context,
    )
    if lookup_count is None or lookup_count < 0:
        if lookup_count is not None:
            audit.error(
                "request_provenance_schema",
                "candidate cache lookup count cannot be negative",
                value=lookup_count,
                **context,
            )
        valid = False
    for field in TIMING_FIELDS:
        value = _int_value(row.get(field), audit, "request_timing", field, **context)
        if value is None or value < 0:
            if value is not None:
                audit.error("request_timing", "timing field is negative", field=field, value=value, **context)
            valid = False
    if not valid:
        return False

    timings = {field: int(row[field]) for field in TIMING_FIELDS}
    exclusive_sum = sum(
        timings[field]
        for field in TIMING_FIELDS
        if field != "request_total_ns"
    )
    if exclusive_sum != timings["request_total_ns"]:
        audit.error(
            "request_timing_reconciliation",
            "exclusive timing stages do not sum to request_total_ns",
            expected=timings["request_total_ns"],
            computed=exclusive_sum,
            **context,
        )
        valid = False
    derived_fields = (
        "preprocess_ns",
        "tokenize_ns",
        "embedding_elapsed_ns",
        "embedding_ns",
        "faiss_ns",
        "sqlite_ns",
        "similarity_evaluation_ns",
        "policy_inclusive_ns",
        "response_materialization_ns",
        "response_propagation_ns",
        "cache_management_ns",
        "post_embedding_elapsed_ns",
        "post_embedding_ns",
        "post_embedding_total_ns",
        "end_to_end_ns",
    )
    derived: Dict[str, Optional[int]] = {}
    for field in derived_fields:
        value = _int_value(
            row.get(field), audit, "request_derived_timing", field, **context
        )
        derived[field] = value
        if value is None or value < 0:
            if value is not None:
                audit.error(
                    "request_derived_timing",
                    "derived timing field is negative",
                    field=field,
                    value=value,
                    **context,
                )
            valid = False

    def require_equal(field: str, expected: int, message: str) -> None:
        nonlocal valid
        if derived.get(field) != expected:
            audit.error(
                "request_derived_timing",
                message,
                field=field,
                observed=derived.get(field),
                computed=expected,
                **context,
            )
            valid = False

    if derived.get("preprocess_ns") is not None and derived.get("tokenize_ns") is not None:
        require_equal(
            "preprocess_ns",
            timings["text_preprocess_tokenize_ns"]
            - int(derived["tokenize_ns"]),
            "preprocess_ns + tokenize_ns does not reconcile with text preprocessing",
        )
    embedding = (
        timings["text_preprocess_tokenize_ns"]
        + timings["onnx_inference_ns"]
        + timings["embedding_postprocess_ns"]
    )
    require_equal("embedding_ns", embedding, "embedding_ns does not reconcile")
    post_embedding = timings["request_total_ns"] - embedding
    require_equal(
        "post_embedding_total_ns",
        post_embedding,
        "post_embedding_total_ns does not reconcile",
    )
    require_equal(
        "post_embedding_ns",
        post_embedding,
        "post_embedding_ns does not reconcile",
    )
    cache_management = (
        timings["policy_exclusive_ns"]
        + timings["sqlite_write_ns"]
        + timings["faiss_mutation_ns"]
    )
    require_equal(
        "cache_management_ns",
        cache_management,
        "cache_management_ns does not reconcile",
    )
    require_equal(
        "faiss_ns",
        timings["faiss_search_ns"] + timings["faiss_mutation_ns"],
        "faiss_ns does not reconcile",
    )
    require_equal(
        "sqlite_ns",
        timings["sqlite_read_ns"] + timings["sqlite_write_ns"],
        "sqlite_ns does not reconcile",
    )
    require_equal(
        "similarity_evaluation_ns",
        timings["similarity_decision_ns"],
        "similarity evaluation/decision aliases do not reconcile",
    )
    if (
        derived.get("response_materialization_ns") is not None
        and derived.get("response_propagation_ns") is not None
    ):
        require_equal(
            "response_materialization_ns",
            timings["response_return_ns"]
            - int(derived["response_propagation_ns"]),
            "response materialization + propagation does not reconcile with response return",
        )
    require_equal(
        "end_to_end_ns",
        timings["request_total_ns"],
        "end_to_end_ns does not reconcile with request_total_ns",
    )
    embedding_elapsed = derived.get("embedding_elapsed_ns")
    embedding_inner = (
        int(derived.get("tokenize_ns") or 0)
        + timings["onnx_inference_ns"]
        + timings["embedding_postprocess_ns"]
    )
    if (
        embedding_elapsed is None
        or embedding_elapsed < embedding_inner
        or embedding_elapsed > timings["request_total_ns"]
    ):
        audit.error(
            "request_derived_timing",
            "embedding elapsed timing is outside its exclusive/request bounds",
            observed=embedding_elapsed,
            lower_bound=embedding_inner,
            upper_bound=timings["request_total_ns"],
            **context,
        )
        valid = False
    post_elapsed = derived.get("post_embedding_elapsed_ns")
    if post_elapsed is None or post_elapsed > post_embedding:
        audit.error(
            "request_derived_timing",
            "post-embedding elapsed timing exceeds the independently derived post-embedding bound",
            observed=post_elapsed,
            upper_bound=post_embedding,
            **context,
        )
        valid = False
    policy_inclusive = derived.get("policy_inclusive_ns")
    if (
        policy_inclusive is None
        or policy_inclusive < timings["policy_exclusive_ns"]
        or policy_inclusive > timings["request_total_ns"]
    ):
        audit.error(
            "request_derived_timing",
            "policy-inclusive timing is outside exclusive/request bounds",
            observed=policy_inclusive,
            lower_bound=timings["policy_exclusive_ns"],
            upper_bound=timings["request_total_ns"],
            **context,
        )
        valid = False
    if row.get("exclusive_reconciles") is not True:
        audit.error("request_timing_reconciliation", "producer did not mark exclusive reconciliation true", **context)
        valid = False

    request_index = _int_value(row.get("request_index"), audit, "request_identity", "request_index", **context)
    if request_index != position:
        audit.error(
            "request_order",
            "request indices are not contiguous within the run",
            observed=request_index,
            expected=position,
            **context,
        )
    for field in ("cache_size_before", "cache_size_after"):
        value = _int_value(row.get(field), audit, "request_cache_size", field, **context)
        if value is None:
            valid = False
        elif value < 0 or value > capacity:
            audit.error(
                "capacity_violation",
                "request cache size is outside the frozen capacity",
                field=field,
                value=value,
                capacity=capacity,
                **context,
            )
            valid = False
    raw_hit = row.get("raw_hit")
    if not isinstance(raw_hit, bool):
        audit.error("request_hit_flags", "raw_hit must be boolean", **context)
    for field in (
        "candidate_cache_data_seen",
        "candidate_cache_data_present",
        "provenance_resolved",
        "provenance_consistent",
        "response_id_mismatch",
        "source_response_mismatch",
        "unrecognized_response_id",
        "stale_candidate",
        "capacity_exceeded",
        "structural_failure",
    ):
        if not isinstance(row.get(field), bool):
            audit.error(
                "request_structural_flag",
                "v2 request structural flags must be booleans",
                field=field,
                observed=row.get(field),
                **context,
            )
    policy_action = row.get("policy_action")
    if raw_hit is True and policy_action != "hit":
        audit.error(
            "request_action_semantics",
            "a raw hit must have the hit policy action",
            policy_action=policy_action,
            **context,
        )
    if row.get("schema_version") != SCHEMA_VERSION:
        audit.error("request_schema", "request row has the wrong schema", **context)
    if row.get("experiment_id") != EXPERIMENT_ID:
        audit.error("request_experiment", "request row has the wrong experiment identity", **context)
    return valid


def _validate_summary_latency(
    summary: Mapping[str, Any], stages: Mapping[str, Dict[str, Any]], audit: _Audit, run_id: str
) -> None:
    for stage, values in stages.items():
        expected_mean_us = round(float(values["mean_ns"]) / 1000.0, 6)
        expectations = {
            "%s_mean_us" % stage: expected_mean_us,
            "%s_p50_us" % stage: round(values["p50_ns"] / 1000.0, 6),
            "%s_p95_us" % stage: round(values["p95_ns"] / 1000.0, 6),
            "%s_p99_us" % stage: round(values["p99_ns"] / 1000.0, 6),
        }
        for field, expected in expectations.items():
            if field not in summary:
                audit.error("run_latency_field", "runs.csv lacks a latency summary field", run_id=run_id, field=field)
            elif not _float_matches(summary[field], expected, "0.000001"):
                audit.error(
                    "run_latency_mismatch",
                    "runs.csv latency is not reproducible from requests.jsonl",
                    run_id=run_id,
                    field=field,
                    declared=summary[field],
                    computed=expected,
                )


def _canonical_summary_stage_value(
    row: Mapping[str, Any], stage: str
) -> int:
    embedding = (
        int(row["text_preprocess_tokenize_ns"])
        + int(row["onnx_inference_ns"])
        + int(row["embedding_postprocess_ns"])
    )
    values = {
        "end_to_end": int(row["request_total_ns"]),
        "post_embedding": int(row["request_total_ns"]) - embedding,
        "embedding": embedding,
        "faiss": int(row["faiss_search_ns"])
        + int(row["faiss_mutation_ns"]),
        "sqlite": int(row["sqlite_read_ns"])
        + int(row["sqlite_write_ns"]),
        "policy_exclusive": int(row["policy_exclusive_ns"]),
        "policy_inclusive": int(row["policy_inclusive_ns"]),
        "response_return": int(row["response_return_ns"]),
        "residual": int(row["residual_ns"]),
    }
    return values[stage]


def _canonical_outcome_stage_value(
    row: Mapping[str, Any], stage: str
) -> int:
    if stage == "embedding":
        return _canonical_summary_stage_value(row, "embedding")
    if stage == "cache_management":
        return (
            int(row["policy_exclusive_ns"])
            + int(row["sqlite_write_ns"])
            + int(row["faiss_mutation_ns"])
        )
    if stage == "post_embedding":
        return _canonical_summary_stage_value(row, "post_embedding")
    if stage == "request_total":
        return int(row["request_total_ns"])
    return int(row[OUTCOME_LATENCY_FIELDS[stage]])


def _recompute_requests(
    rows: Sequence[Mapping[str, Any]],
    run_rows: Mapping[str, Mapping[str, str]],
    audit: _Audit,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Mapping[str, Any]]]]:
    grouped: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row_number, row in enumerate(rows, start=1):
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            audit.error("request_run_id", "request row lacks a run ID", row=row_number)
            continue
        if run_id not in run_rows:
            audit.error("request_unknown_run", "request row references an unknown run", row=row_number, run_id=run_id)
            continue
        grouped[run_id].append(row)

    recomputed: Dict[str, Dict[str, Any]] = {}
    for run_id, summary in run_rows.items():
        run_requests = grouped.get(run_id, [])
        expected_count = _int_value(summary.get("requests"), audit, "run_field", "requests", run_id=run_id)
        capacity = _int_value(summary.get("capacity"), audit, "run_field", "capacity", run_id=run_id)
        if expected_count is None or capacity is None:
            continue
        if len(run_requests) != expected_count:
            audit.error(
                "request_count",
                "request row count does not match runs.csv",
                run_id=run_id,
                declared=expected_count,
                computed=len(run_requests),
            )

        run_requests = sorted(
            run_requests,
            key=lambda row: int(row.get("request_index", -1))
            if isinstance(row.get("request_index"), (int, str)) and str(row.get("request_index")).lstrip("-").isdigit()
            else -1,
        )
        valid_timings = True
        for position, row in enumerate(run_requests):
            valid_timings = _validate_request_row(row, audit, run_id, position, capacity) and valid_timings
            for field in ("seed", "policy", "trace_hash"):
                declared = summary.get(field)
                observed = str(row.get(field))
                if observed != str(declared):
                    audit.error(
                        "request_run_identity",
                        "request identity differs from runs.csv",
                        run_id=run_id,
                        field=field,
                        declared=declared,
                        observed=row.get(field),
                    )
        if not run_requests or not valid_timings:
            continue

        summary_seed = _int_value(
            summary.get("seed"), audit, "run_field", "seed", run_id=run_id
        )
        summary_policy = summary.get("policy")
        if summary_seed is None or summary_policy not in POLICIES:
            continue

        stages: Dict[str, Dict[str, Any]] = {}
        for stage in SUMMARY_STAGE_FIELDS:
            stage_samples = [
                _canonical_summary_stage_value(row, stage)
                for row in run_requests
            ]
            stages[stage] = _latency_recomputation(stage_samples)
        _validate_summary_latency(summary, stages, audit, run_id)

        totals = [int(row["request_total_ns"]) for row in run_requests]
        service_seconds = sum(totals) / 1_000_000_000.0
        if not _float_matches(summary.get("service_seconds"), round(service_seconds, 9), "0.000000001"):
            audit.error(
                "service_seconds_mismatch",
                "service_seconds is not reproducible from request totals",
                run_id=run_id,
                declared=summary.get("service_seconds"),
                computed=round(service_seconds, 9),
            )
        service_qps = len(run_requests) / service_seconds if service_seconds > 0 else 0.0
        for field in ("throughput_qps", "service_throughput_qps"):
            if not _float_matches(
                summary.get(field), round(service_qps, 6), "0.000001"
            ):
                audit.error(
                    "service_throughput_mismatch",
                    "formal service throughput is not reproducible from retained request totals",
                    run_id=run_id,
                    field=field,
                    declared=summary.get(field),
                    computed=round(service_qps, 6),
                )

        loop_seconds_value = _decimal_value(summary.get("loop_seconds"))
        loop_seconds = float(loop_seconds_value) if loop_seconds_value is not None else 0.0
        if loop_seconds <= 0:
            audit.error("loop_seconds", "loop_seconds must be finite and positive", run_id=run_id)
        loop_start = summary.get("loop_start_monotonic_ns")
        loop_end = summary.get("loop_end_monotonic_ns")
        if loop_start not in (None, "") and loop_end not in (None, ""):
            start_ns = _int_value(loop_start, audit, "loop_timer", "loop_start_monotonic_ns", run_id=run_id)
            end_ns = _int_value(loop_end, audit, "loop_timer", "loop_end_monotonic_ns", run_id=run_id)
            if start_ns is not None and end_ns is not None:
                computed_loop_seconds = (end_ns - start_ns) / 1_000_000_000.0
                if computed_loop_seconds <= 0 or not _float_matches(
                    summary.get("loop_seconds"), round(computed_loop_seconds, 9), "0.000000001"
                ):
                    audit.error(
                        "loop_timer_mismatch",
                        "loop_seconds does not reconcile with monotonic boundaries",
                        run_id=run_id,
                        computed=round(computed_loop_seconds, 9),
                    )
        else:
            audit.warn(
                "loop_boundaries_missing",
                "throughput can be recomputed from loop_seconds but its raw timer boundaries are absent",
                run_id=run_id,
            )
        loop_qps = len(run_requests) / loop_seconds if loop_seconds > 0 else 0.0
        if not _float_matches(
            summary.get("loop_throughput_qps"), round(loop_qps, 6), "0.000001"
        ):
            audit.error(
                "loop_throughput_mismatch",
                "diagnostic loop throughput is not reproducible from loop_seconds",
                run_id=run_id,
                declared=summary.get("loop_throughput_qps"),
                computed=round(loop_qps, 6),
            )

        hits = sum(row.get("raw_hit") is True for row in run_requests)
        max_cache_size = _max_cache_size(run_requests)
        count_expectations = {
            "hits": hits,
            "misses": len(run_requests) - hits,
            "response_id_mismatches": sum(
                row.get("response_id_mismatch") is True for row in run_requests
            ),
            "source_response_mismatches": sum(
                row.get("source_response_mismatch") is True for row in run_requests
            ),
            "unrecognized_response_ids": sum(
                row.get("unrecognized_response_id") is True for row in run_requests
            ),
            "stale_candidates": sum(
                row.get("stale_candidate") is True for row in run_requests
            ),
            "capacity_excess_requests": sum(
                row.get("capacity_exceeded") is True for row in run_requests
            ),
            "structural_failure_requests": sum(
                row.get("structural_failure") is True for row in run_requests
            ),
            "max_cache_size": max_cache_size,
        }
        for field, expected in count_expectations.items():
            observed = _int_value(summary.get(field), audit, "run_counter", field, run_id=run_id)
            if observed != expected:
                audit.error(
                    "run_counter_mismatch",
                    "runs.csv counter is not reproducible from request records",
                    run_id=run_id,
                    field=field,
                    declared=observed,
                    computed=expected,
                )

        recomputed[run_id] = {
            "run_id": run_id,
            "seed": summary_seed,
            "policy": summary_policy,
            "trace_hash": summary["trace_hash"],
            "request_count": len(run_requests),
            "latency": stages,
            "service_seconds": service_seconds,
            "loop_seconds": loop_seconds,
            "throughput_qps_unrounded": service_qps,
            "loop_throughput_qps_unrounded": loop_qps,
            "hit_counts": count_expectations,
        }
        grouped[run_id] = run_requests
    return recomputed, dict(grouped)


def _request_outcome(row: Mapping[str, Any]) -> Optional[str]:
    if row.get("raw_hit") is True:
        return "hit"
    if row.get("rejected") is True:
        return "rejected"
    if row.get("admitted") is True and row.get("evicted") is True:
        return "admitted_with_eviction"
    if row.get("admitted") is True and row.get("evicted") is False:
        return "admitted_without_eviction"
    return None


def _recompute_outcome_latency(
    declared_rows: Sequence[Mapping[str, Any]],
    requests: Mapping[str, Sequence[Mapping[str, Any]]],
    run_rows: Mapping[str, Mapping[str, str]],
    audit: _Audit,
) -> Dict[str, Dict[str, Any]]:
    declared: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for row_number, row in enumerate(declared_rows, start=1):
        run_id = row.get("run_id")
        outcome = row.get("outcome")
        if not isinstance(run_id, str) or run_id not in run_rows:
            audit.error(
                "outcome_run_id",
                "outcome latency row references an unknown run",
                row=row_number,
                run_id=run_id,
            )
            continue
        if outcome not in OUTCOME_REPORT_NAMES:
            audit.error(
                "outcome_name",
                "outcome latency row has an unknown category",
                row=row_number,
                outcome=outcome,
            )
            continue
        key = (run_id, str(outcome))
        if key in declared:
            audit.error(
                "outcome_duplicate",
                "outcome latency rows duplicate a run/category",
                run_id=run_id,
                outcome=outcome,
            )
            continue
        declared[key] = row

    recomputed: Dict[str, Dict[str, Any]] = {}
    for run_id, summary in run_rows.items():
        by_outcome: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for request in requests.get(run_id, []):
            outcome = _request_outcome(request)
            if outcome is None:
                audit.error(
                    "outcome_partition",
                    "request does not belong to one frozen outcome category",
                    run_id=run_id,
                    request_index=request.get("request_index"),
                )
                continue
            by_outcome[outcome].append(request)
        run_result: Dict[str, Any] = {}
        all_requests = list(requests.get(run_id, []))
        for outcome in OUTCOME_REPORT_NAMES:
            row = declared.get((run_id, outcome))
            if row is None:
                audit.error(
                    "outcome_missing",
                    "run lacks a required outcome latency row",
                    run_id=run_id,
                    outcome=outcome,
                )
                continue
            for field in ("schema_version", "experiment_id", "attempt_id", "seed", "policy"):
                expected = (
                    SCHEMA_VERSION
                    if field == "schema_version"
                    else EXPERIMENT_ID
                    if field == "experiment_id"
                    else summary.get(field)
                )
                if str(row.get(field)) != str(expected):
                    audit.error(
                        "outcome_identity",
                        "outcome latency identity differs from its run",
                        run_id=run_id,
                        outcome=outcome,
                        field=field,
                        observed=row.get(field),
                        expected=expected,
                    )
            outcome_requests = (
                all_requests if outcome == "all" else by_outcome.get(outcome, [])
            )
            sample_count = len(outcome_requests)
            if _int_value(
                row.get("samples"),
                audit,
                "outcome_samples",
                "samples",
                run_id=run_id,
                outcome=outcome,
            ) != sample_count:
                audit.error(
                    "outcome_samples",
                    "outcome sample count is not reproducible",
                    run_id=run_id,
                    outcome=outcome,
                    computed=sample_count,
                )
            latency = row.get("latency")
            if not isinstance(latency, dict):
                audit.error(
                    "outcome_latency",
                    "outcome latency payload must be an object",
                    run_id=run_id,
                    outcome=outcome,
                )
                continue
            stage_result: Dict[str, Any] = {}
            for stage, field in OUTCOME_LATENCY_FIELDS.items():
                observed = latency.get(stage)
                if not isinstance(observed, dict):
                    audit.error(
                        "outcome_latency",
                        "outcome latency lacks a stage object",
                        run_id=run_id,
                        outcome=outcome,
                        stage=stage,
                    )
                    continue
                values = [
                    _canonical_outcome_stage_value(request, stage)
                    for request in outcome_requests
                ]
                expected_values: Dict[str, Any]
                if values:
                    expected_values = {
                        "mean_ns": sum(values) / len(values),
                        "p50_ns": nearest_rank(values, 0.50),
                        "p95_ns": nearest_rank(values, 0.95),
                        "p99_ns": nearest_rank(values, 0.99),
                    }
                else:
                    expected_values = {
                        "mean_ns": None,
                        "p50_ns": None,
                        "p95_ns": None,
                        "p99_ns": None,
                    }
                for statistic, expected in expected_values.items():
                    value = observed.get(statistic)
                    if expected is None:
                        matches = value is None
                    elif statistic == "mean_ns":
                        matches = _float_matches(value, float(expected), "0.000000001")
                    else:
                        matches = value == expected
                    if not matches:
                        audit.error(
                            "outcome_latency_mismatch",
                            "outcome latency is not reproducible from request rows",
                            run_id=run_id,
                            outcome=outcome,
                            stage=stage,
                            statistic=statistic,
                            declared=value,
                            computed=expected,
                        )
                stage_result[stage] = expected_values
            run_result[outcome] = {
                "samples": sample_count,
                "latency": stage_result,
            }
        recomputed[run_id] = run_result
    return recomputed


def _recompute_resources(
    rows: Sequence[Mapping[str, Any]],
    run_rows: Mapping[str, Mapping[str, str]],
    audit: _Audit,
    interval_ms: int,
) -> Dict[str, Dict[str, Any]]:
    grouped: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row_number, row in enumerate(rows, start=1):
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or run_id not in run_rows:
            audit.error("resource_unknown_run", "resource row references an unknown run", row=row_number, run_id=run_id)
            continue
        grouped[run_id].append(row)

    recomputed: Dict[str, Dict[str, Any]] = {}
    for run_id, summary in run_rows.items():
        samples = grouped.get(run_id, [])
        if not samples:
            audit.error("resource_missing", "run has no external resource samples", run_id=run_id)
            continue
        def sample_sort_key(row: Mapping[str, Any]) -> int:
            value = row.get("sample_index")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lstrip("-").isdigit():
                return int(value)
            return -1

        samples = sorted(samples, key=sample_sort_key)
        sample_indices = [
            _int_value(
                row.get("sample_index"),
                audit,
                "resource_order",
                "sample_index",
                run_id=run_id,
                sample_position=position,
            )
            for position, row in enumerate(samples)
        ]
        if sample_indices != list(range(len(samples))):
            audit.error("resource_order", "resource sample indices are not contiguous", run_id=run_id)
        if samples[0].get("kind") != "loop_start" or samples[-1].get("kind") != "loop_end":
            audit.error("resource_boundaries", "resource samples do not retain mandatory loop boundaries", run_id=run_id)
        if sum(row.get("kind") == "loop_start" for row in samples) != 1 or sum(
            row.get("kind") == "loop_end" for row in samples
        ) != 1:
            audit.error("resource_boundaries", "run must contain exactly one start and one end snapshot", run_id=run_id)
        if any(
            row.get("kind") != "periodic" for row in samples[1:-1]
        ):
            audit.error(
                "resource_cadence",
                "all interior resource snapshots must be periodic samples",
                run_id=run_id,
            )

        monotonic_values: List[Optional[int]] = []
        completion_values: List[Optional[int]] = []
        for position, row in enumerate(samples):
            context = {"run_id": run_id, "sample_position": position}
            monotonic_value = _int_value(
                row.get("monotonic_ns"),
                audit,
                "resource_monotonic",
                "monotonic_ns",
                **context,
            )
            started_value = _int_value(
                row.get("sample_started_monotonic_ns"),
                audit,
                "resource_collection_timing",
                "sample_started_monotonic_ns",
                **context,
            )
            completed_value = _int_value(
                row.get("sample_completed_monotonic_ns"),
                audit,
                "resource_collection_timing",
                "sample_completed_monotonic_ns",
                **context,
            )
            collection_value = _int_value(
                row.get("sample_collection_ns"),
                audit,
                "resource_collection_timing",
                "sample_collection_ns",
                **context,
            )
            if (
                monotonic_value is not None
                and started_value is not None
                and completed_value is not None
                and collection_value is not None
                and (
                    monotonic_value != started_value
                    or completed_value < started_value
                    or collection_value != completed_value - started_value
                )
            ):
                audit.error(
                    "resource_collection_timing",
                    "resource sample start/completion timing does not reconcile",
                    **context,
                )
            monotonic_values.append(started_value)
            completion_values.append(completed_value)

        for position in range(1, len(samples)):
            previous_completed = completion_values[position - 1]
            current_started = monotonic_values[position]
            if (
                previous_completed is not None
                and current_started is not None
                and current_started < previous_completed
            ):
                audit.error(
                    "resource_collection_timing",
                    "synchronous resource samples must not overlap",
                    run_id=run_id,
                    sample_position=position,
                    previous_completed_monotonic_ns=previous_completed,
                    sample_started_monotonic_ns=current_started,
                )

        valid_monotonic = all(value is not None for value in monotonic_values)
        gaps: List[int] = []
        if valid_monotonic:
            monotonic_ns = [int(value) for value in monotonic_values if value is not None]
            gaps = [
                current - previous
                for previous, current in zip(monotonic_ns, monotonic_ns[1:])
            ]
            if any(gap <= 0 for gap in gaps):
                audit.error(
                    "resource_monotonic",
                    "resource sample times must be strictly increasing",
                    run_id=run_id,
                )
            maximum_gap_ns = 2 * int(interval_ms) * 1_000_000
            if gaps and max(gaps) > maximum_gap_ns:
                audit.error(
                    "resource_cadence",
                    "external sampling has a gap larger than two frozen intervals",
                    run_id=run_id,
                    maximum_observed_gap_ns=max(gaps),
                    maximum_allowed_gap_ns=maximum_gap_ns,
                )
        else:
            monotonic_ns = []

        rss: List[int] = []
        uss: List[int] = []
        for position, row in enumerate(samples):
            context = {"run_id": run_id, "sample_position": position}
            if row.get("schema_version") != SCHEMA_VERSION:
                audit.error("resource_schema", "resource row has the wrong schema", **context)
            for field in ("seed", "policy"):
                if str(row.get(field)) != str(summary.get(field)):
                    audit.error("resource_identity", "resource identity differs from runs.csv", field=field, **context)
            observed_child_pid = _int_value(
                row.get("child_pid"),
                audit,
                "resource_child_pid",
                "child_pid",
                **context,
            )
            expected_child_pid = _int_value(
                summary.get("child_pid"),
                audit,
                "resource_child_pid",
                "child_pid",
                run_id=run_id,
            )
            if observed_child_pid != expected_child_pid:
                audit.error(
                    "resource_child_pid",
                    "resource sample child PID differs from runs.csv",
                    observed=observed_child_pid,
                    expected=expected_child_pid,
                    **context,
                )
            if row.get("scope") != "formal":
                audit.error("resource_scope", "resource sample is outside formal scope", **context)
            vms_value = _int_value(
                row.get("vms_bytes"), audit, "resource_value", "vms_bytes", **context
            )
            thread_count = _int_value(
                row.get("num_threads"),
                audit,
                "resource_value",
                "num_threads",
                **context,
            )
            if vms_value is not None and vms_value < 0:
                audit.error("resource_value", "VMS must be non-negative", **context)
            if thread_count is not None and thread_count <= 0:
                audit.error("resource_value", "thread count must be positive", **context)
            rss_value = _int_value(row.get("rss_bytes"), audit, "resource_value", "rss_bytes", **context)
            if rss_value is not None and rss_value >= 0:
                rss.append(rss_value)
            else:
                audit.error("resource_value", "RSS must be non-negative", **context)
            if row.get("uss_bytes") is not None:
                uss_value = _int_value(row.get("uss_bytes"), audit, "resource_value", "uss_bytes", **context)
                if uss_value is not None and uss_value >= 0:
                    uss.append(uss_value)
                else:
                    audit.error("resource_value", "USS must be null or non-negative", **context)
            if row.get("uss_available") is not (row.get("uss_bytes") is not None):
                audit.error(
                    "resource_availability",
                    "USS availability flag disagrees with the retained value",
                    **context,
                )
        if len(rss) != len(samples):
            continue
        rss_mean = sum(rss) / len(rss)
        rss_peak = max(rss)
        rss_expectations = {
            "rss_start_bytes": rss[0],
            "rss_end_bytes": rss[-1],
            "rss_end_minus_start_bytes": rss[-1] - rss[0],
            "rss_mean_bytes": round(rss_mean, 3),
            "rss_peak_sampled_bytes": rss_peak,
            "rss_peak_bytes": rss_peak,
            "rss_incremental_peak_bytes": max(0, rss_peak - rss[0]),
        }
        for field, expected in rss_expectations.items():
            tolerance = "0.001" if field == "rss_mean_bytes" else "0"
            if not _float_matches(summary.get(field), expected, tolerance):
                audit.error(
                    "resource_summary_mismatch",
                    "runs.csv resource value is not reproducible",
                    run_id=run_id,
                    field=field,
                    declared=summary.get(field),
                    computed=expected,
                )
        uss_mean: Optional[float] = None
        uss_peak: Optional[int] = None
        uss_availability = [row.get("uss_available") for row in samples]
        if not all(isinstance(value, bool) for value in uss_availability):
            audit.error(
                "resource_availability",
                "USS availability flags must be booleans",
                run_id=run_id,
            )
        elif len(set(uss_availability)) != 1:
            audit.error(
                "resource_availability",
                "USS availability changed within one run",
                run_id=run_id,
            )
        if uss:
            uss_mean = sum(uss) / len(uss)
            uss_peak = max(uss)
            if not _float_matches(summary.get("uss_mean_bytes"), round(uss_mean, 3), "0.001"):
                audit.error("resource_summary_mismatch", "USS mean is not reproducible", run_id=run_id)
            if not _float_matches(summary.get("uss_peak_bytes"), uss_peak, "0"):
                audit.error("resource_summary_mismatch", "USS peak is not reproducible", run_id=run_id)
            uss_expectations = {
                "uss_start_bytes": uss[0],
                "uss_end_bytes": uss[-1],
                "uss_incremental_peak_bytes": max(0, uss_peak - uss[0]),
                "uss_end_minus_start_bytes": uss[-1] - uss[0],
            }
            for field, expected in uss_expectations.items():
                if not _float_matches(summary.get(field), expected, "0"):
                    audit.error(
                        "resource_summary_mismatch",
                        "USS boundary/delta is not reproducible",
                        run_id=run_id,
                        field=field,
                        computed=expected,
                    )
        elif any(
            summary.get(field) not in (None, "")
            for field in (
                "uss_start_bytes",
                "uss_end_bytes",
                "uss_mean_bytes",
                "uss_peak_bytes",
                "uss_incremental_peak_bytes",
                "uss_end_minus_start_bytes",
            )
        ):
            audit.error("resource_summary_mismatch", "USS summary exists without supported USS samples", run_id=run_id)

        cpu_user = [_decimal_value(row.get("cpu_user_seconds")) for row in samples]
        cpu_system = [_decimal_value(row.get("cpu_system_seconds")) for row in samples]
        if any(value is None for value in cpu_user + cpu_system):
            audit.error(
                "resource_cpu",
                "CPU counters must be finite in every resource sample",
                run_id=run_id,
            )
            cpu_user_delta = None
            cpu_system_delta = None
        else:
            cpu_user_delta = max(Decimal(0), cpu_user[-1] - cpu_user[0])
            cpu_system_delta = max(Decimal(0), cpu_system[-1] - cpu_system[0])
            cpu_total_delta = cpu_user_delta + cpu_system_delta
            resource_window_seconds = (
                Decimal(monotonic_ns[-1] - monotonic_ns[0]) / Decimal(1_000_000_000)
                if len(monotonic_ns) >= 2
                else Decimal(0)
            )
            cpu_expectations = {
                "cpu_user_seconds": round(float(cpu_user_delta), 9),
                "cpu_system_seconds": round(float(cpu_system_delta), 9),
                "cpu_total_seconds": round(float(cpu_total_delta), 9),
                "resource_window_seconds": round(float(resource_window_seconds), 9),
                "cpu_utilization_percent": (
                    round(100.0 * float(cpu_total_delta / resource_window_seconds), 6)
                    if resource_window_seconds > 0
                    else None
                ),
            }
            for field, expected in cpu_expectations.items():
                if expected is None:
                    if summary.get(field) not in (None, ""):
                        audit.error(
                            "resource_summary_mismatch",
                            "CPU summary exists for a zero resource window",
                            run_id=run_id,
                            field=field,
                        )
                elif not _float_matches(
                    summary.get(field),
                    expected,
                    "0.000001" if field == "cpu_utilization_percent" else "0.000000001",
                ):
                    audit.error(
                        "resource_summary_mismatch",
                        "runs.csv CPU value is not reproducible",
                        run_id=run_id,
                        field=field,
                        declared=summary.get(field),
                        computed=expected,
                    )

        loop_start = _int_value(
            summary.get("loop_start_monotonic_ns"),
            audit,
            "resource_coverage",
            "loop_start_monotonic_ns",
            run_id=run_id,
        )
        loop_end = _int_value(
            summary.get("loop_end_monotonic_ns"),
            audit,
            "resource_coverage",
            "loop_end_monotonic_ns",
            run_id=run_id,
        )
        if monotonic_ns and loop_start is not None and loop_end is not None:
            if monotonic_ns[0] > loop_start or monotonic_ns[-1] < loop_end:
                audit.error(
                    "resource_coverage",
                    "mandatory resource snapshots do not cover the request loop",
                    run_id=run_id,
                    sample_start_ns=monotonic_ns[0],
                    loop_start_ns=loop_start,
                    sample_end_ns=monotonic_ns[-1],
                    loop_end_ns=loop_end,
                )

        io_availability = [row.get("io_counters_available") for row in samples]
        if not all(isinstance(value, bool) for value in io_availability):
            audit.error(
                "resource_io",
                "I/O availability flags must be booleans",
                run_id=run_id,
            )
        elif len(set(io_availability)) != 1:
            audit.error(
                "resource_io",
                "I/O counter availability changed within one run",
                run_id=run_id,
            )
        elif io_availability[0]:
            for field in (
                "io_read_count",
                "io_write_count",
                "io_read_bytes",
                "io_write_bytes",
            ):
                values = [
                    _int_value(
                        row.get(field),
                        audit,
                        "resource_io",
                        field,
                        run_id=run_id,
                        sample_position=position,
                    )
                    for position, row in enumerate(samples)
                ]
                if any(value is None for value in values):
                    continue
                delta = max(0, int(values[-1]) - int(values[0]))
                if _int_value(
                    summary.get(field + "_delta"),
                    audit,
                    "resource_summary_mismatch",
                    field + "_delta",
                    run_id=run_id,
                ) != delta:
                    audit.error(
                        "resource_summary_mismatch",
                        "runs.csv I/O delta is not reproducible",
                        run_id=run_id,
                        field=field + "_delta",
                        computed=delta,
                    )
        else:
            if any(
                row.get(field) is not None
                for row in samples
                for field in (
                    "io_read_count",
                    "io_write_count",
                    "io_read_bytes",
                    "io_write_bytes",
                )
            ):
                audit.error(
                    "resource_io",
                    "unsupported I/O counters must be retained as null",
                    run_id=run_id,
                )
            for field in (
                "io_read_count_delta",
                "io_write_count_delta",
                "io_read_bytes_delta",
                "io_write_bytes_delta",
            ):
                if summary.get(field) not in (None, ""):
                    audit.error(
                        "resource_summary_mismatch",
                        "runs.csv has an I/O delta without supported counters",
                        run_id=run_id,
                        field=field,
                    )
        recomputed[run_id] = {
            "samples": len(samples),
            "rss_start_bytes": rss[0],
            "rss_end_bytes": rss[-1],
            "rss_mean_bytes_unrounded": rss_mean,
            "rss_peak_bytes": rss_peak,
            "rss_incremental_peak_bytes": max(0, rss_peak - rss[0]),
            "rss_end_minus_start_bytes": rss[-1] - rss[0],
            "uss_supported_samples": len(uss),
            "uss_start_bytes": uss[0] if uss else None,
            "uss_end_bytes": uss[-1] if uss else None,
            "uss_mean_bytes_unrounded": uss_mean,
            "uss_peak_bytes": uss_peak,
            "uss_incremental_peak_bytes": (
                max(0, uss_peak - uss[0])
                if uss and uss_peak is not None
                else None
            ),
            "uss_end_minus_start_bytes": uss[-1] - uss[0] if uss else None,
            "cpu_user_start_seconds": float(cpu_user[0]) if cpu_user[0] is not None else None,
            "cpu_user_end_seconds": float(cpu_user[-1]) if cpu_user[-1] is not None else None,
            "cpu_system_start_seconds": float(cpu_system[0]) if cpu_system[0] is not None else None,
            "cpu_system_end_seconds": float(cpu_system[-1]) if cpu_system[-1] is not None else None,
            "maximum_sample_gap_ns": max(gaps) if valid_monotonic and gaps else 0,
            "resource_window_seconds": (
                (monotonic_ns[-1] - monotonic_ns[0]) / 1_000_000_000.0
                if len(monotonic_ns) >= 2
                else 0.0
            ),
        }
    return recomputed


def _validate_run_structure(
    run_rows: Mapping[str, Mapping[str, str]],
    audit: _Audit,
    expected_mode: Optional[str],
    formal_claimable: bool,
) -> Dict[int, Dict[str, str]]:
    by_seed: DefaultDict[int, Dict[str, str]] = defaultdict(dict)
    child_pids: Dict[int, str] = {}
    storage_instances: Dict[str, str] = {}
    for run_id, row in run_rows.items():
        seed = _int_value(row.get("seed"), audit, "run_field", "seed", run_id=run_id)
        policy = row.get("policy")
        if seed is None or policy not in POLICIES:
            if policy not in POLICIES:
                audit.error("run_policy", "run has an unknown policy", run_id=run_id, policy=policy)
            continue
        if policy in by_seed[seed]:
            audit.error("duplicate_seed_policy", "seed has duplicate policy runs", seed=seed, policy=policy)
        by_seed[seed][policy] = run_id
        if row.get("schema_version") != SCHEMA_VERSION:
            audit.error("run_schema", "runs.csv row has the wrong schema", run_id=run_id)
        if not isinstance(expected_mode, str) or row.get("mode") != expected_mode:
            audit.error(
                "run_mode",
                "run mode differs from the manifest configuration",
                run_id=run_id,
                declared=row.get("mode"),
                expected=expected_mode,
            )
        child_exit_status = _int_value(
            row.get("child_exit_status"),
            audit,
            "child_exit_status",
            "child_exit_status",
            run_id=run_id,
        )
        if child_exit_status != 0:
            audit.error(
                "child_exit_status",
                "retained run is not a successful child execution",
                run_id=run_id,
                value=child_exit_status,
            )
        child_pid = _int_value(
            row.get("child_pid"), audit, "child_pid", "child_pid", run_id=run_id
        )
        if child_pid is None or child_pid <= 0:
            audit.error("child_pid", "child PID must be a positive integer", run_id=run_id)
        elif child_pid in child_pids:
            audit.error(
                "child_pid_reuse",
                "fresh policy processes must have unique retained child PIDs",
                run_id=run_id,
                other_run_id=child_pids[child_pid],
                child_pid=child_pid,
            )
        else:
            child_pids[child_pid] = run_id
        storage_identity = row.get("storage_instance_sha256")
        if (
            not isinstance(storage_identity, str)
            or len(storage_identity) != 64
            or any(character not in "0123456789abcdef" for character in storage_identity)
        ):
            audit.error(
                "storage_instance",
                "storage instance identity must be a lowercase SHA-256",
                run_id=run_id,
            )
        elif storage_identity in storage_instances:
            audit.error(
                "storage_instance_reuse",
                "fresh policy runs must have unique storage identities",
                run_id=run_id,
                other_run_id=storage_instances[storage_identity],
            )
        else:
            storage_instances[storage_identity] = run_id
        embedding_dimension = _int_value(
            row.get("embedding_dimension"),
            audit,
            "embedding_dimension",
            "embedding_dimension",
            run_id=run_id,
        )
        if embedding_dimension is None or embedding_dimension <= 0:
            audit.error(
                "embedding_dimension",
                "embedding dimension must be positive",
                run_id=run_id,
                value=embedding_dimension,
            )
        elif formal_claimable and embedding_dimension != 768:
            audit.error(
                "embedding_dimension",
                "formal Gate 7 ONNX evidence requires 768-dimensional embeddings",
                run_id=run_id,
                value=embedding_dimension,
            )
        for field in ("embedding_norm_verified", "provider_verified"):
            if _bool_value(row.get(field)) is not True:
                audit.error(
                    "embedding_verification",
                    "formal run lacks a successful embedding/provider verification flag",
                    run_id=run_id,
                    field=field,
                    observed=row.get(field),
                )
        if formal_claimable:
            for field in (
                "asset_integrity_pre_verified",
                "asset_integrity_post_verified",
            ):
                if _bool_value(row.get(field)) is not True:
                    audit.error(
                        "asset_integrity",
                        "formal run lacks a successful retained-asset integrity flag",
                        run_id=run_id,
                        field=field,
                        observed=row.get(field),
                    )
            for field, expected in (
                ("model_asset_digest_sha256", FROZEN_MODEL["model_digest_sha256"]),
                (
                    "tokenizer_asset_digest_sha256",
                    FROZEN_MODEL["tokenizer_digest_sha256"],
                ),
            ):
                if row.get(field) != expected:
                    audit.error(
                        "asset_integrity",
                        "formal run asset digest differs from the frozen manifest identity",
                        run_id=run_id,
                        field=field,
                        declared=row.get(field),
                        expected=expected,
                    )
        request_buffer_rows = _int_value(
            row.get("request_buffer_rows_reserved"),
            audit,
            "buffer_reservation",
            "request_buffer_rows_reserved",
            run_id=run_id,
        )
        embedding_buffer_bytes = _int_value(
            row.get("embedding_buffer_bytes_reserved"),
            audit,
            "buffer_reservation",
            "embedding_buffer_bytes_reserved",
            run_id=run_id,
        )
        declared_requests = _int_value(
            row.get("requests"),
            audit,
            "buffer_reservation",
            "requests",
            run_id=run_id,
        )
        if declared_requests is not None and request_buffer_rows != declared_requests:
            audit.error(
                "buffer_reservation",
                "request buffer was not reserved at full run capacity",
                run_id=run_id,
                declared=request_buffer_rows,
                expected=declared_requests,
            )
        if (
            declared_requests is not None
            and embedding_dimension is not None
            and embedding_buffer_bytes != declared_requests * embedding_dimension * 4
        ):
            audit.error(
                "buffer_reservation",
                "embedding evidence buffer has the wrong reserved byte capacity",
                run_id=run_id,
                declared=embedding_buffer_bytes,
                expected=declared_requests * embedding_dimension * 4,
            )
        for field in (
            "source_response_mismatches",
            "unrecognized_response_ids",
            "stale_candidates",
            "capacity_excess_requests",
            "structural_failure_requests",
            "structural_failure_flags_total",
            "deleted_scalar_count",
        ):
            value = _int_value(row.get(field), audit, "structural_counter", field, run_id=run_id)
            if value != 0:
                audit.error("structural_failure", "run has a nonzero structural failure counter", run_id=run_id, field=field, value=value)
        if _bool_value(row.get("structural_valid")) is not True:
            audit.error(
                "structural_failure",
                "run is not declared structurally valid",
                run_id=run_id,
                observed=row.get("structural_valid"),
            )
        if row.get("storage_failure") not in (None, ""):
            audit.error(
                "structural_failure",
                "run records a storage verification failure",
                run_id=run_id,
                observed=row.get("storage_failure"),
            )
        capacity = _int_value(row.get("capacity"), audit, "run_field", "capacity", run_id=run_id)
        max_size = _int_value(row.get("max_cache_size"), audit, "run_field", "max_cache_size", run_id=run_id)
        scalar = _int_value(row.get("final_scalar_count"), audit, "run_field", "final_scalar_count", run_id=run_id)
        vector = _int_value(row.get("final_vector_count"), audit, "run_field", "final_vector_count", run_id=run_id)
        verified = _int_value(row.get("verified_entries"), audit, "run_field", "verified_entries", run_id=run_id)
        if capacity is not None and max_size is not None and max_size > capacity:
            audit.error("capacity_violation", "run exceeded configured capacity", run_id=run_id)
        if scalar is not None and vector is not None and verified is not None and not (scalar == vector == verified):
            audit.error("storage_divergence", "final SQLite/FAISS/verification counts diverge", run_id=run_id)
        if capacity is not None and scalar is not None and scalar > capacity:
            audit.error("capacity_violation", "final storage exceeds capacity", run_id=run_id)
    return dict(by_seed)


def _trace_projection(rows: Sequence[Mapping[str, Any]]) -> List[Tuple[Any, ...]]:
    return [tuple(row.get(field) for field in TRACE_PROJECTION) for row in rows]


def _validate_pairing_and_trace(
    manifest: Mapping[str, Any],
    by_seed: Mapping[int, Mapping[str, str]],
    run_rows: Mapping[str, Mapping[str, str]],
    requests: Mapping[str, Sequence[Mapping[str, Any]]],
    retained_traces: Mapping[int, Sequence[Mapping[str, Any]]],
    audit: _Audit,
) -> None:
    policy_orders = manifest.get("policy_orders")
    planned_policy_orders = manifest.get("planned_policy_orders")
    trace_metadata = manifest.get("trace_metadata")
    if not isinstance(policy_orders, dict):
        audit.error("policy_orders", "manifest policy_orders must be an object")
        policy_orders = {}
    if not isinstance(planned_policy_orders, dict):
        audit.error("planned_policy_orders", "manifest planned_policy_orders must be an object")
        planned_policy_orders = {}
    if planned_policy_orders != policy_orders:
        audit.error(
            "planned_actual_policy_order",
            "planned and actual policy-order maps differ",
            planned=planned_policy_orders,
            actual=policy_orders,
        )
    if not isinstance(trace_metadata, dict):
        audit.error("trace_metadata", "manifest trace_metadata must be an object")
        trace_metadata = {}

    for seed, policy_runs in by_seed.items():
        hashes = {run_rows[run_id].get("trace_hash") for run_id in policy_runs.values()}
        if len(hashes) != 1:
            audit.error("trace_pairing", "policies within a seed have different trace hashes", seed=seed)
        embeddings = {
            run_rows[run_id].get("embedding_digest_sha256") for run_id in policy_runs.values()
        }
        if len(embeddings) != 1:
            audit.error("embedding_pairing", "policies within a seed have different embedding digests", seed=seed)
        projections = {
            policy: _trace_projection(requests.get(run_id, []))
            for policy, run_id in policy_runs.items()
        }
        if projections:
            first = next(iter(projections.values()))
            for policy, projection in projections.items():
                if projection != first:
                    audit.error("trace_pairing", "policy request traces differ within a seed", seed=seed, policy=policy)
        retained = retained_traces.get(seed)
        if retained is None:
            audit.error(
                "trace_artifact_binding",
                "seed has no independently loaded retained trace",
                seed=seed,
            )
        else:
            retained_projection = [
                (
                    row.get("index"),
                    row.get("request_id"),
                    row.get("phase"),
                    row.get("occurrence"),
                    row.get("reuse_opportunity"),
                    row.get("text_id"),
                    row.get("concept_id"),
                    row.get("response_id"),
                )
                for row in retained
            ]
            for policy, projection in projections.items():
                if projection != retained_projection:
                    audit.error(
                        "trace_request_binding",
                        "request rows do not reproduce the retained trace",
                        seed=seed,
                        policy=policy,
                    )
            for policy, run_id in policy_runs.items():
                run_requests = requests.get(run_id, [])
                if len(run_requests) != len(retained):
                    continue
                for position, (request_row, trace_row) in enumerate(
                    zip(run_requests, retained)
                ):
                    expected_text_hash = hashlib.sha256(
                        _normalize_text(trace_row.get("text")).encode("utf-8")
                    ).hexdigest()
                    if request_row.get("text_sha256") != expected_text_hash:
                        audit.error(
                            "trace_text_binding",
                            "request text hash differs from the retained raw text",
                            seed=seed,
                            policy=policy,
                            request_position=position,
                        )
                    if request_row.get("expected_response_id") != trace_row.get(
                        "response_id"
                    ):
                        audit.error(
                            "trace_response_binding",
                            "request expected response differs from the retained trace",
                            seed=seed,
                            policy=policy,
                            request_position=position,
                        )
        declared_order = policy_orders.get(str(seed))
        if not isinstance(declared_order, list):
            audit.error("policy_order", "seed lacks a declared policy order", seed=seed)
            continue
        actual_order: List[Optional[str]] = [None] * len(declared_order)
        for policy, run_id in policy_runs.items():
            position = _int_value(run_rows[run_id].get("order_position"), audit, "policy_order", "order_position", seed=seed, policy=policy)
            if position is None or not 0 <= position < len(actual_order) or actual_order[position] is not None:
                audit.error("policy_order", "policy order positions are invalid", seed=seed, policy=policy, position=position)
            else:
                actual_order[position] = policy
        if actual_order != declared_order:
            audit.error("policy_order", "runs.csv order differs from manifest order", seed=seed, declared=declared_order, actual=actual_order)

        metadata = trace_metadata.get(str(seed))
        if not isinstance(metadata, dict):
            audit.error("trace_metadata", "seed lacks trace construction metadata", seed=seed)
            continue
        trace_hash = next(iter(hashes)) if len(hashes) == 1 else None
        if metadata.get("trace_sha256") != trace_hash:
            audit.error("trace_metadata", "trace metadata hash differs from policy runs", seed=seed)
        if metadata.get("selection_split") != "calibration":
            audit.error("trace_provenance", "trace is not calibration-only", seed=seed)

        first_run_id = next(iter(policy_runs.values()))
        projected_rows = requests.get(first_run_id, [])
        request_count = _int_value(
            run_rows[first_run_id].get("requests"),
            audit,
            "trace_structure",
            "requests",
            seed=seed,
        )
        capacity = _int_value(
            run_rows[first_run_id].get("capacity"),
            audit,
            "trace_structure",
            "capacity",
            seed=seed,
        )
        if request_count is None or capacity is None or request_count % 10:
            continue
        warm_count = 3 * request_count // 10
        scan_count = 4 * request_count // 10
        return_count = 3 * request_count // 10
        phases = [row.get("phase") for row in projected_rows]
        expected_phases = (
            ["warm"] * warm_count
            + ["scan"] * scan_count
            + ["return"] * return_count
        )
        if phases != expected_phases:
            audit.error(
                "trace_phase_sequence",
                "trace does not have the frozen contiguous 30/40/30 phases",
                seed=seed,
            )
        request_ids = [row.get("request_id") for row in projected_rows]
        if len(request_ids) != len(set(request_ids)):
            audit.error("trace_request_ids", "request IDs are not unique within a seed", seed=seed)
        expected_phase_counts = {
            "warm": warm_count,
            "scan": scan_count,
            "return": return_count,
        }
        if metadata.get("phase_counts") != expected_phase_counts:
            audit.error(
                "trace_metadata",
                "trace metadata phase counts differ from the frozen split",
                seed=seed,
                expected=expected_phase_counts,
                observed=metadata.get("phase_counts"),
            )
        if metadata.get("requests") != request_count or metadata.get("capacity") != capacity:
            audit.error(
                "trace_metadata",
                "trace metadata request count or capacity differs from the run",
                seed=seed,
            )
        hot_size = (4 * capacity) // 5
        hot_concepts = metadata.get("hot_concept_ids")
        scan_concepts = metadata.get("scan_concept_ids")
        hot_texts = metadata.get("hot_text_ids")
        scan_texts = metadata.get("scan_text_ids")
        if (
            not isinstance(hot_concepts, list)
            or not all(isinstance(value, str) for value in hot_concepts)
            or len(hot_concepts) != hot_size
            or len(set(hot_concepts)) != hot_size
            or metadata.get("hot_set_size") != hot_size
        ):
            audit.error("trace_hot_set", "trace metadata has the wrong hot-set structure", seed=seed)
        if (
            not isinstance(scan_concepts, list)
            or not all(isinstance(value, str) for value in scan_concepts)
            or len(scan_concepts) != scan_count
            or len(set(scan_concepts)) != scan_count
        ):
            audit.error("trace_scan_set", "trace metadata has the wrong distinct scan set", seed=seed)
        if (
            isinstance(hot_concepts, list)
            and all(isinstance(value, str) for value in hot_concepts)
            and isinstance(scan_concepts, list)
            and all(isinstance(value, str) for value in scan_concepts)
            and set(hot_concepts) & set(scan_concepts)
        ):
            audit.error("trace_set_overlap", "hot and scan concepts overlap", seed=seed)
        if (
            not isinstance(hot_texts, list)
            or not all(isinstance(value, str) for value in hot_texts)
            or len(hot_texts) != hot_size
            or len(set(hot_texts)) != hot_size
            or not isinstance(scan_texts, list)
            or not all(isinstance(value, str) for value in scan_texts)
            or len(scan_texts) != scan_count
            or len(set(scan_texts)) != scan_count
        ):
            audit.error("trace_text_sets", "trace metadata text sets have invalid cardinality", seed=seed)
        elif set(hot_texts) & set(scan_texts):
            audit.error("trace_set_overlap", "hot and scan text IDs overlap", seed=seed)

        warm_rows = projected_rows[:warm_count]
        scan_rows = projected_rows[warm_count : warm_count + scan_count]
        return_rows = projected_rows[warm_count + scan_count :]
        warm_concept_counts = Counter(row.get("concept_id") for row in warm_rows)
        return_concept_counts = Counter(row.get("concept_id") for row in return_rows)
        if warm_concept_counts != return_concept_counts:
            audit.error("trace_hot_multiplicity", "warm and return hot multiplicities differ", seed=seed)
        if (
            isinstance(hot_concepts, list)
            and all(isinstance(value, str) for value in hot_concepts)
            and set(warm_concept_counts) != set(hot_concepts)
        ):
            audit.error("trace_hot_coverage", "warm phase does not cover exactly the declared hot set", seed=seed)
        if (
            isinstance(scan_concepts, list)
            and all(isinstance(value, str) for value in scan_concepts)
            and [row.get("concept_id") for row in scan_rows]
            and set(row.get("concept_id") for row in scan_rows) != set(scan_concepts)
        ):
            audit.error("trace_scan_coverage", "scan phase differs from the declared scan set", seed=seed)
        for phase_name, phase_rows in (
            ("warm", warm_rows),
            ("scan", scan_rows),
            ("return", return_rows),
        ):
            occurrences: DefaultDict[Any, List[int]] = defaultdict(list)
            for row in phase_rows:
                occurrence = row.get("occurrence")
                if isinstance(occurrence, int) and not isinstance(occurrence, bool):
                    occurrences[row.get("concept_id")].append(occurrence)
                else:
                    audit.error(
                        "trace_occurrence",
                        "trace occurrence is not an integer",
                        seed=seed,
                        phase=phase_name,
                    )
            for concept_id, values in occurrences.items():
                if sorted(values) != list(range(len(values))):
                    audit.error(
                        "trace_occurrence",
                        "per-phase concept occurrences are not contiguous from zero",
                        seed=seed,
                        phase=phase_name,
                        concept_id=concept_id,
                    )


GATE2_THRESHOLD_FIELDS = (
    "threshold",
    "true_positive",
    "false_positive",
    "true_negative",
    "false_negative",
    "precision",
    "recall",
    "false_positive_rate",
    "wilson_precision_lower_one_sided_95",
    "false_hit_rate",
    "false_hit_rate_definition",
    "wilson_false_hit_rate_upper_one_sided_95",
)
GATE2_CALIBRATION_POSITIVES = 5652
GATE2_CALIBRATION_NEGATIVES = 2077
GATE2_CALIBRATION_PAIRS = 7729
GATE2_WILSON_Z = 1.6448536269514722


def _wilson_one_sided(successes: int, trials: int, *, upper: bool) -> float:
    """Recompute the frozen one-sided 95% Wilson endpoint."""

    if trials <= 0 or successes < 0 or successes > trials:
        raise ValueError("Wilson inputs must satisfy 0 <= successes <= trials")
    proportion = successes / trials
    z_squared = GATE2_WILSON_Z * GATE2_WILSON_Z
    denominator = 1.0 + z_squared / trials
    center = (proportion + z_squared / (2.0 * trials)) / denominator
    margin = (
        GATE2_WILSON_Z
        * math.sqrt(
            proportion * (1.0 - proportion) / trials
            + z_squared / (4.0 * trials * trials)
        )
        / denominator
    )
    return min(1.0, center + margin) if upper else max(0.0, center - margin)


def _validate_gate2_threshold_csv(path: Path, audit: _Audit) -> None:
    """Independently recompute every retained Gate 2 calibration row."""

    try:
        with path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            fields = tuple(reader.fieldnames or ())
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        audit.error(
            "gate2_v2_thresholds",
            "cannot read the frozen Gate 2 threshold table",
            path=str(path),
            detail=str(exc),
        )
        return

    if fields != GATE2_THRESHOLD_FIELDS:
        audit.error(
            "gate2_v2_thresholds",
            "Gate 2 threshold table has the wrong columns or column order",
            observed=list(fields),
            expected=list(GATE2_THRESHOLD_FIELDS),
        )
    if len(rows) != 20:
        audit.error(
            "gate2_v2_thresholds",
            "Gate 2 threshold table must contain the complete 0.80..0.99 grid",
            observed_rows=len(rows),
            expected_rows=20,
        )

    qualifying_thresholds: List[float] = []
    expected_thresholds = [Decimal("0.80") + Decimal(index) / 100 for index in range(20)]
    metric_tolerance = 1e-15
    previous_counts: Optional[Dict[str, int]] = None
    for index, row in enumerate(rows):
        context = {"row": index + 2}
        try:
            threshold = Decimal(row.get("threshold", ""))
            counts = {
                field: int(row.get(field, ""))
                for field in (
                    "true_positive",
                    "false_positive",
                    "true_negative",
                    "false_negative",
                )
            }
            metrics = {
                field: float(row.get(field, ""))
                for field in (
                    "precision",
                    "recall",
                    "false_positive_rate",
                    "wilson_precision_lower_one_sided_95",
                    "false_hit_rate",
                    "wilson_false_hit_rate_upper_one_sided_95",
                )
            }
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 threshold row contains a non-numeric value",
                **context,
            )
            continue
        if not all(math.isfinite(value) for value in metrics.values()):
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 threshold row contains a non-finite metric",
                **context,
            )
            continue
        if index >= len(expected_thresholds) or threshold != expected_thresholds[index]:
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 threshold grid is not exactly 0.80..0.99 in step-0.01 order",
                observed=str(threshold),
                expected=(str(expected_thresholds[index]) if index < 20 else None),
                **context,
            )

        tp = counts["true_positive"]
        fp = counts["false_positive"]
        tn = counts["true_negative"]
        fn = counts["false_negative"]
        if previous_counts is not None and (
            tp > previous_counts["true_positive"]
            or fp > previous_counts["false_positive"]
            or tn < previous_counts["true_negative"]
            or fn < previous_counts["false_negative"]
        ):
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 confusion counts are not realizable by increasing one frozen threshold",
                previous=previous_counts,
                observed=counts,
                **context,
            )
        previous_counts = dict(counts)
        if min(counts.values()) < 0:
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 confusion-matrix counts cannot be negative",
                **context,
            )
            continue
        if tp + fn != GATE2_CALIBRATION_POSITIVES:
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 positive calibration count is not conserved",
                observed=tp + fn,
                expected=GATE2_CALIBRATION_POSITIVES,
                **context,
            )
        if fp + tn != GATE2_CALIBRATION_NEGATIVES:
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 negative calibration count is not conserved",
                observed=fp + tn,
                expected=GATE2_CALIBRATION_NEGATIVES,
                **context,
            )
        if tp + fp + tn + fn != GATE2_CALIBRATION_PAIRS:
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 calibration total is not conserved",
                observed=tp + fp + tn + fn,
                expected=GATE2_CALIBRATION_PAIRS,
                **context,
            )
        if tp + fp <= 0:
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 precision denominator must be positive",
                **context,
            )
            continue

        recomputed = {
            "precision": tp / (tp + fp),
            "recall": tp / GATE2_CALIBRATION_POSITIVES,
            "false_positive_rate": fp / GATE2_CALIBRATION_NEGATIVES,
            "wilson_precision_lower_one_sided_95": _wilson_one_sided(
                tp, tp + fp, upper=False
            ),
            "false_hit_rate": fp / GATE2_CALIBRATION_PAIRS,
            "wilson_false_hit_rate_upper_one_sided_95": _wilson_one_sided(
                fp, GATE2_CALIBRATION_PAIRS, upper=True
            ),
        }
        for field, expected in recomputed.items():
            if abs(metrics[field] - expected) > metric_tolerance:
                audit.error(
                    "gate2_v2_thresholds",
                    "Gate 2 metric is not reproducible from its retained counts",
                    field=field,
                    observed=metrics[field],
                    computed=expected,
                    **context,
                )
        if row.get("false_hit_rate_definition") != "FP / (TP + FP + TN + FN)":
            audit.error(
                "gate2_v2_thresholds",
                "Gate 2 false-hit rate does not declare the frozen FP/all-pairs denominator",
                **context,
            )
        if recomputed["wilson_precision_lower_one_sided_95"] >= 0.99:
            qualifying_thresholds.append(float(threshold))

    if qualifying_thresholds:
        audit.error(
            "gate2_v2_thresholds",
            "Gate 2 claims no selection although a retained calibration row meets the Wilson gate",
            qualifying_thresholds=qualifying_thresholds,
        )


def _formal_protocol_checks(
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> List[str]:
    pending_reasons: List[str] = []
    config = manifest.get("config")
    if not isinstance(config, dict):
        audit.error("config", "manifest config must be an object")
        return pending_reasons
    if config.get("mode") != "full":
        pending_reasons.append("bundle is not marked as a formal full-mode experiment")
        return pending_reasons
    if manifest.get("formal_claimable_mode") is not True:
        audit.error(
            "formal_claimable_mode",
            "a completed full-mode bundle must be emitted as formal claimable evidence",
        )

    project_root = Path(__file__).resolve().parents[2]
    gate2_specs = (
        (
            GATE2_V2_SELECTION_PATH,
            PINNED_GATE2_V2_SELECTION_SHA256,
        ),
        (GATE2_V2_RESULT_PATH, PINNED_GATE2_V2_RESULT_SHA256),
        (GATE2_V2_THRESHOLDS_PATH, PINNED_GATE2_V2_THRESHOLDS_SHA256),
    )
    gate2_artifacts: Dict[str, Dict[str, Any]] = {}
    for relative, expected_sha256 in gate2_specs:
        path = project_root / relative
        if not path.is_file():
            audit.error(
                "gate2_v2_identity",
                "frozen Gate 2 v2 evidence is missing",
                path=relative,
            )
            continue
        identity = {
            "sha256": _sha256_file(path),
            "bytes": int(path.stat().st_size),
        }
        gate2_artifacts[relative] = identity
        if identity["sha256"] != expected_sha256:
            audit.error(
                "gate2_v2_identity",
                "frozen Gate 2 v2 evidence hash changed",
                path=relative,
                observed=identity["sha256"],
                expected=expected_sha256,
            )
    selection = _load_json(
        project_root / GATE2_V2_SELECTION_PATH,
        audit,
        "gate2_v2_selection",
    )
    result = _load_json(
        project_root / GATE2_V2_RESULT_PATH,
        audit,
        "gate2_v2_result",
    )
    thresholds_path = project_root / GATE2_V2_THRESHOLDS_PATH
    if thresholds_path.is_file():
        _validate_gate2_threshold_csv(thresholds_path, audit)
    threshold_identity = gate2_artifacts.get(GATE2_V2_THRESHOLDS_PATH, {})
    expected_threshold_reference = {
        "path": "calibration-thresholds.csv",
        "sha256": threshold_identity.get("sha256"),
        "bytes": threshold_identity.get("bytes"),
    }
    expected_threshold_grid = [round(0.80 + index / 100, 2) for index in range(20)]
    if (
        not isinstance(selection, dict)
        or selection.get("schema_version") != "carma-qqp-v2"
        or selection.get("kind") != "qqp_threshold_selection_transcript"
        or selection.get("status") != "no_threshold_selected"
        or selection.get("selected_threshold") is not None
        or selection.get("selected_calibration_metrics") is not None
        or selection.get("calibration_pairs") != GATE2_CALIBRATION_PAIRS
        or selection.get("calibration_endpoints_resolved") is not True
        or selection.get("calibration_similarities_computed")
        != GATE2_CALIBRATION_PAIRS
        or selection.get("threshold_grid") != expected_threshold_grid
        or selection.get("minimum_wilson_precision_lower") != 0.99
        or selection.get("calibration_thresholds") != expected_threshold_reference
        or selection.get("selection_uses_heldout") is not False
        or selection.get("heldout_endpoints_resolved") is not False
        or selection.get("heldout_similarities_computed") is not False
        or selection.get("heldout_similarity_count") != 0
        or selection.get("heldout_rows_discovered_by_split_only_scan") != 56963
        or selection.get("heldout_row_fields_read_before_selection") != ["split"]
    ):
        audit.error(
            "gate2_v2_protocol",
            "Gate 2 v2 selection is not a calibration-only no-selection result",
        )
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "carma-qqp-v2"
        or result.get("status") != "no_threshold_met_precision_gate"
        or result.get("selected_threshold") is not None
        or result.get("calibration") is not None
        or result.get("calibration_pairs") != GATE2_CALIBRATION_PAIRS
        or result.get("calibration_thresholds") != expected_threshold_reference
        or result.get("selection_transcript")
        != {
            "path": "threshold-selection.json",
            "sha256": gate2_artifacts.get(GATE2_V2_SELECTION_PATH, {}).get(
                "sha256"
            ),
            "bytes": gate2_artifacts.get(GATE2_V2_SELECTION_PATH, {}).get(
                "bytes"
            ),
        }
        or result.get("selection_uses_heldout") is not False
        or result.get("heldout_endpoints_resolved") is not False
        or result.get("heldout_similarities_computed") is not False
        or result.get("heldout_similarity_count") != 0
        or result.get("heldout_rows_discovered_by_split_only_scan") != 56963
        or result.get("test_pairs_not_evaluated") != 56963
    ):
        audit.error(
            "gate2_v2_protocol",
            "Gate 2 v2 result contradicts the frozen no-threshold/no-heldout decision",
        )
    expected_gate2_manifest = {
        "status": "no_threshold_met_precision_gate",
        "selected_threshold": None,
        "heldout_evaluated": False,
        "artifacts": gate2_artifacts,
    }
    if manifest.get("gate2_v2") != expected_gate2_manifest:
        audit.error(
            "gate2_v2_manifest",
            "formal manifest does not bind the frozen Gate 2 v2 evidence",
            observed=manifest.get("gate2_v2"),
            expected=expected_gate2_manifest,
        )
    expected_operating_point = {
        "frozen_similarity_threshold": 0.97,
        "run_similarity_threshold": config.get("hit_threshold"),
        "qualification": (
            "unqualified frozen systems operating point; Gate 2 v2 did not "
            "select or qualify a threshold"
        ),
        "derived_from_gate2_v2": False,
    }
    if manifest.get("gate7_operating_point") != expected_operating_point:
        audit.error(
            "gate7_operating_point",
            "Gate 7 threshold must be disclosed as an unqualified frozen systems operating point",
            observed=manifest.get("gate7_operating_point"),
            expected=expected_operating_point,
        )

    declarations = manifest.get("artifacts")
    if isinstance(declarations, dict):
        for name, declaration in declarations.items():
            if not isinstance(declaration, dict):
                continue
            required_identity_fields = ["sha256", "bytes"]
            if str(name).endswith((".csv", ".jsonl")):
                required_identity_fields.append("rows")
            for field in required_identity_fields:
                if field not in declaration:
                    audit.error(
                        "formal_artifact_field",
                        "formal artifact declaration omits a required integrity field",
                        artifact=name,
                        field=field,
                    )

    required_flags = {
        "historical_gate7_preserved": True,
        "historical_artifacts_modified": False,
        "precomputed_embeddings": False,
        "embedding_in_request_path": True,
        "gptcache_adapter_path": True,
        "actual_onnx": True,
    }
    for field, expected in required_flags.items():
        if manifest.get(field) != expected:
            audit.error("formal_flag", "formal manifest flag violates the frozen protocol", field=field, expected=expected, observed=manifest.get(field))
    v1_lineage = manifest.get("v1_lineage")
    expected_v1_lineage = {
        "manifest_path": V1_PRESERVATION_MANIFEST_PATH,
        "manifest_sha256": PINNED_V1_PRESERVATION_MANIFEST_SHA256,
        "archive_path": V1_PRESERVATION_ARCHIVE_PATH,
        "archive_sha256": PINNED_V1_PRESERVATION_ARCHIVE_SHA256,
        "formal_root": PINNED_V1_ROOT,
        "attempt_ledger_sha256": PINNED_V1_LEDGER_SHA256,
        "terminal_attempt_id": PINNED_V1_ATTEMPT_ID,
        "terminal_entry_sha256": PINNED_V1_TERMINAL_ENTRY_SHA256,
        "failure_sha256": PINNED_V1_FAILURE_SHA256,
        "relationship": (
            "method-correcting successor; v1 remains invalid and is not reclassified"
        ),
    }
    if v1_lineage != expected_v1_lineage:
        audit.error(
            "v1_lineage",
            "formal v3 manifest does not exactly preserve the v1 invalid-attempt lineage",
            observed=v1_lineage,
            expected=expected_v1_lineage,
        )
    if config.get("fake_embedding") is not False:
        audit.error("formal_embedding", "formal experiment must explicitly disable fake embeddings")
    attempt_policy = manifest.get("attempt_policy")
    if (
        not isinstance(attempt_policy, dict)
        or attempt_policy.get("formal_root") != FORMAL_ATTEMPT_ROOT
        or attempt_policy.get("eligibility")
        != "first structurally valid complete attempt in the retained predecessor chain"
        or attempt_policy.get("rerun_scope")
        != "complete five-seed, three-policy matrix under a new attempt ID"
    ):
        audit.error(
            "attempt_policy",
            "formal manifest lacks the frozen attempt-retention/eligibility policy",
        )
    contract = manifest.get("contract")
    if not isinstance(contract, dict):
        audit.error(
            "contract_identity",
            "formal evidence must identify the frozen remediation contract",
        )
    else:
        if contract.get("path") != DEFAULT_CONTRACT_PATH:
            audit.error(
                "contract_identity",
                "formal evidence used a non-default remediation contract path",
                observed=contract.get("path"),
                expected=DEFAULT_CONTRACT_PATH,
            )
        if contract.get("sha256") != PINNED_CONTRACT_SHA256:
            audit.error(
                "contract_identity",
                "formal evidence contract hash differs from the frozen identity",
                observed=contract.get("sha256"),
                expected=PINNED_CONTRACT_SHA256,
            )
        if not isinstance(contract.get("bytes"), int) or contract.get("bytes") <= 0:
            audit.error(
                "contract_identity",
                "formal evidence contract identity lacks a positive byte count",
            )
    for field, expected in FROZEN_CONFIG.items():
        if config.get(field) != expected:
            audit.error("frozen_config", "formal configuration differs from the frozen contract", field=field, expected=expected, observed=config.get(field))
    declared_seeds = manifest.get("seeds")
    if not isinstance(declared_seeds, list) or tuple(declared_seeds) != FULL_SEEDS:
        pending_reasons.append("the five frozen seeds are not all declared in order")
    declared_policies = manifest.get("policies")
    if (
        not isinstance(declared_policies, list)
        or not all(isinstance(value, str) for value in declared_policies)
        or set(declared_policies) != set(POLICIES)
    ):
        pending_reasons.append("LRU, LFU, and CARMA are not all declared")
    if manifest.get("policy_order_namespace") != POLICY_ORDER_NAMESPACE:
        audit.error("policy_order_namespace", "formal policy-order namespace differs from the contract")
    policy_orders = manifest.get("policy_orders", {})
    if not isinstance(policy_orders, dict):
        audit.error("policy_orders", "formal policy_orders must be an object")
        policy_orders = {}
    for seed in FULL_SEEDS:
        if policy_orders.get(str(seed)) != list(EXPECTED_POLICY_ORDERS[seed]):
            audit.error("frozen_policy_order", "formal seed order differs from the frozen schedule", seed=seed)
    planned_policy_orders = manifest.get("planned_policy_orders", {})
    if not isinstance(planned_policy_orders, dict):
        audit.error("planned_policy_orders", "formal planned_policy_orders must be an object")
        planned_policy_orders = {}
    for seed in FULL_SEEDS:
        if planned_policy_orders.get(str(seed)) != list(EXPECTED_POLICY_ORDERS[seed]):
            audit.error(
                "frozen_planned_policy_order",
                "formal planned seed order differs from the frozen schedule",
                seed=seed,
            )
    git = manifest.get("git")
    if not isinstance(git, dict) or git.get("worktree_dirty") is not False or not git.get("head_commit"):
        audit.error("git_state", "formal evidence requires a recorded clean Git HEAD")
    elif (
        git.get("end_head_commit") != git.get("head_commit")
        or git.get("end_worktree_dirty") is not False
        or git.get("source_snapshot_unchanged") is not True
    ):
        audit.error(
            "git_state",
            "formal evidence did not preserve one clean source snapshot through completion",
        )
    model = manifest.get("model")
    if not isinstance(model, dict):
        audit.error("model", "manifest model must be an object")
    elif model != FROZEN_MODEL:
        audit.error(
            "frozen_model",
            "model/tokenizer file identity differs from the frozen contract",
            observed=model,
            expected=FROZEN_MODEL,
        )
    source_identities = manifest.get("source_identities")
    if not isinstance(source_identities, dict):
        audit.error("source_identities", "formal source_identities must be an object")
    else:
        for source_name in REQUIRED_SOURCE_IDENTITIES:
            identity = source_identities.get(source_name)
            if not isinstance(identity, dict):
                audit.error(
                    "source_identity_missing",
                    "formal source identity is absent",
                    source=source_name,
                )
                continue
            sha256 = identity.get("sha256")
            byte_count = identity.get("bytes")
            if (
                not isinstance(sha256, str)
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
            ):
                audit.error(
                    "source_identity_sha256",
                    "formal source identity lacks a valid SHA-256",
                    source=source_name,
                )
            if (
                not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count <= 0
            ):
                audit.error(
                    "source_identity_bytes",
                    "formal source identity lacks a positive byte count",
                    source=source_name,
                )
        raw_archive_relative = "examples/benchmark/similiar_qqp_full.json.gz"
        raw_archive_identity = source_identities.get(raw_archive_relative)
        raw_archive_path = project_root / raw_archive_relative
        expected_archive_sha256 = FROZEN_SOURCE_HASHES["source_archive_sha256"]
        if (
            not isinstance(raw_archive_identity, dict)
            or raw_archive_identity.get("sha256") != expected_archive_sha256
        ):
            audit.error(
                "source_archive_identity",
                "formal source snapshot does not bind the frozen raw QQP archive",
                declared=(
                    raw_archive_identity.get("sha256")
                    if isinstance(raw_archive_identity, dict)
                    else None
                ),
                expected=expected_archive_sha256,
            )
        if (
            not raw_archive_path.is_file()
            or _sha256_file(raw_archive_path) != expected_archive_sha256
        ):
            audit.error(
                "source_archive_identity",
                "executing auditor cannot authenticate the frozen raw QQP archive bytes",
                path=raw_archive_relative,
                expected=expected_archive_sha256,
            )
    onnx = manifest.get("onnx")
    onnx_expected = {
        "provider": "CPUExecutionProvider",
        "intra_op_threads": 1,
        "inter_op_threads": 1,
        "max_length": 512,
        "warmup_requests": 20,
        "model_load_and_warmup_in_request_timing": False,
        "provider_verified_each_child": True,
        "embedding_norm_verified_each_request": True,
    }
    if not isinstance(onnx, dict):
        audit.error("onnx", "manifest ONNX settings must be an object")
    else:
        for field, expected in onnx_expected.items():
            if onnx.get(field) != expected:
                audit.error("frozen_onnx", "ONNX setting differs from the frozen contract", field=field, expected=expected, observed=onnx.get(field))
    timing_scope = manifest.get("timing_scope")
    if (
        not isinstance(timing_scope, dict)
        or timing_scope.get("quantiles")
        != "nearest rank: one-based ceil(q*N) over raw samples"
        or timing_scope.get("outcome_categories") != list(OUTCOME_NAMES)
        or timing_scope.get("outcome_report_rows")
        != list(OUTCOME_REPORT_NAMES)
    ):
        audit.error(
            "timing_definition",
            "formal manifest lacks the frozen quantile/outcome definitions",
        )
    resource_measurement = manifest.get("resource_measurement")
    if (
        not isinstance(resource_measurement, dict)
        or resource_measurement.get("maximum_valid_sample_gap_ms") != 200
        or resource_measurement.get("cadence_timestamp")
        != "sample_started_monotonic_ns"
        or resource_measurement.get("deadline_schedule") != "absolute_monotonic"
        or resource_measurement.get("collection_timing")
        != (
            "sample_started_monotonic_ns, sample_completed_monotonic_ns, "
            "and sample_collection_ns reconcile exactly"
        )
        or resource_measurement.get("fixed_buffers_reserved_before_start") is not True
    ):
        audit.error(
            "resource_definition",
            "formal manifest lacks the frozen sampling/buffer definitions",
        )
    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        audit.error("environment", "formal manifest environment must be an object")
    else:
        for field in ("filesystem", "power", "load_average_at_manifest"):
            if field not in environment:
                audit.error(
                    "environment",
                    "formal manifest omits a machine observation",
                    field=field,
                )
    trace_metadata = manifest.get("trace_metadata")
    if isinstance(trace_metadata, dict):
        for seed in FULL_SEEDS:
            metadata = trace_metadata.get(str(seed))
            if not isinstance(metadata, dict):
                continue
            if seed in FROZEN_TRACE_SEMANTIC_HASHES:
                expected_trace_hash, expected_semantic_hash = (
                    FROZEN_TRACE_SEMANTIC_HASHES[seed]
                )
                if metadata.get("trace_sha256") != expected_trace_hash:
                    audit.error(
                        "frozen_trace_hash",
                        "formal trace hash differs from the v2 regression pin",
                        seed=seed,
                        observed=metadata.get("trace_sha256"),
                        expected=expected_trace_hash,
                    )
                if metadata.get("semantic_index_sha256") != expected_semantic_hash:
                    audit.error(
                        "frozen_semantic_hash",
                        "formal semantic-index hash differs from the v2 regression pin",
                        seed=seed,
                        observed=metadata.get("semantic_index_sha256"),
                        expected=expected_semantic_hash,
                    )
            for field, expected in FROZEN_SOURCE_HASHES.items():
                if metadata.get(field) != expected:
                    audit.error(
                        "frozen_trace_source",
                        "formal trace source identity differs from the frozen contract",
                        seed=seed,
                        field=field,
                        expected=expected,
                        observed=metadata.get(field),
                    )
            trace_hash = metadata.get("trace_sha256")
            matching_trace_artifacts = [
                name
                for name, values in artifacts.items()
                if "trace" in name.lower()
                and str(seed) in name
                and values.get("sha256") == trace_hash
            ]
            if not matching_trace_artifacts:
                audit.error(
                    "formal_trace_artifact",
                    "formal bundle does not retain a per-seed trace artifact matching its trace hash",
                    seed=seed,
                )
            expected_trace_name = "traces/seed-%d.jsonl" % seed
            if expected_trace_name not in artifacts:
                audit.error(
                    "formal_trace_artifact",
                    "formal bundle uses the wrong retained trace filename",
                    seed=seed,
                    expected=expected_trace_name,
                )
    if manifest.get("seeds") == list(PROTOCOL_FULL_SEEDS):
        project_root = Path(__file__).resolve().parents[2]
        local_contract = project_root / DEFAULT_CONTRACT_PATH
        if (
            not local_contract.is_file()
            or _sha256_file(local_contract) != PINNED_CONTRACT_SHA256
        ):
            audit.error(
                "contract_identity",
                "executing auditor does not have the pinned contract bytes",
            )
        if isinstance(git, dict) and isinstance(source_identities, dict):
            head_commit = git.get("head_commit")
            identities_to_verify = dict(source_identities)
            identities_to_verify[DEFAULT_CONTRACT_PATH] = contract
            for relative, identity in identities_to_verify.items():
                if not isinstance(identity, dict) or not isinstance(head_commit, str):
                    continue
                try:
                    blob = subprocess.check_output(
                        ["git", "show", "%s:%s" % (head_commit, relative)],
                        cwd=str(project_root),
                        stderr=subprocess.DEVNULL,
                    )
                except (OSError, subprocess.CalledProcessError):
                    audit.error(
                        "git_source_binding",
                        "recorded source cannot be loaded from the recorded Git HEAD",
                        source=relative,
                    )
                    continue
                if (
                    hashlib.sha256(blob).hexdigest() != identity.get("sha256")
                    or len(blob) != identity.get("bytes")
                ):
                    audit.error(
                        "git_source_binding",
                        "recorded source identity differs from the recorded Git blob",
                        source=relative,
                    )
    return pending_reasons


def adjudicate_seed_pair(
    seed: int,
    carma: Mapping[str, Any],
    lru: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply all five frozen bounds to one recomputed CARMA/LRU pair."""

    carma_p95 = int(carma["p95_request_total_ns"])
    lru_p95 = int(lru["p95_request_total_ns"])
    carma_qps = float(carma["throughput_qps_unrounded"])
    lru_qps = float(lru["throughput_qps_unrounded"])
    carma_rss = int(carma["rss_peak_bytes"])
    lru_rss = int(lru["rss_peak_bytes"])
    if lru_p95 <= 0 or lru_qps <= 0 or lru_rss <= 0:
        raise ValueError("LRU denominators must be positive")
    values = {
        "p95_ratio": carma_p95 / lru_p95,
        "p95_delta_ns": carma_p95 - lru_p95,
        "throughput_ratio": carma_qps / lru_qps,
        "rss_ratio": carma_rss / lru_rss,
        "rss_delta_bytes": carma_rss - lru_rss,
    }
    checks = {
        "p95_ratio": values["p95_ratio"] <= BOUNDS["p95_ratio_max"],
        "p95_delta": values["p95_delta_ns"] <= BOUNDS["p95_delta_ns_max"],
        "throughput_ratio": values["throughput_ratio"] >= BOUNDS["throughput_ratio_min"],
        "rss_ratio": values["rss_ratio"] <= BOUNDS["rss_ratio_max"],
        "rss_delta": values["rss_delta_bytes"] <= BOUNDS["rss_delta_bytes_max"],
    }
    return {
        "seed": seed,
        "carma": dict(carma),
        "lru": dict(lru),
        "paired_values": values,
        "checks": checks,
        "passes": all(checks.values()),
    }


def _seed_adjudications(
    by_seed: Mapping[int, Mapping[str, str]],
    request_results: Mapping[str, Mapping[str, Any]],
    resource_results: Mapping[str, Mapping[str, Any]],
    audit: _Audit,
) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for seed, runs in sorted(by_seed.items()):
        if "CARMA" not in runs or "LRU" not in runs:
            continue
        pair: Dict[str, Dict[str, Any]] = {}
        for policy in ("CARMA", "LRU"):
            run_id = runs[policy]
            request = request_results.get(run_id)
            resource = resource_results.get(run_id)
            if request is None or resource is None or "end_to_end" not in request.get("latency", {}):
                break
            pair[policy] = {
                "run_id": run_id,
                "trace_hash": request["trace_hash"],
                "p95_request_total_ns": request["latency"]["end_to_end"]["p95_ns"],
                "throughput_qps_unrounded": request["throughput_qps_unrounded"],
                "rss_peak_bytes": resource["rss_peak_bytes"],
            }
        if len(pair) != 2:
            continue
        try:
            results[str(seed)] = adjudicate_seed_pair(seed, pair["CARMA"], pair["LRU"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            audit.error("adjudication_input", "cannot adjudicate a paired seed", seed=seed, detail=str(exc))
    return results


def _validate_attempt_identity(
    manifest: Mapping[str, Any],
    run_rows: Mapping[str, Mapping[str, Any]],
    request_rows: Sequence[Mapping[str, Any]],
    resource_rows: Sequence[Mapping[str, Any]],
    outcome_rows: Sequence[Mapping[str, Any]],
    audit: _Audit,
) -> Optional[str]:
    manifest_attempt = manifest.get("attempt_id")
    if not isinstance(manifest_attempt, str) or not manifest_attempt.strip():
        audit.error("attempt_id", "manifest attempt_id must be a nonempty string")
        manifest_attempt = None
    observed: DefaultDict[str, List[str]] = defaultdict(list)
    if manifest_attempt is not None:
        observed[manifest_attempt].append("manifest")
    collections: Tuple[Tuple[str, Iterable[Mapping[str, Any]]], ...] = (
        ("runs.csv", run_rows.values()),
        ("requests.jsonl", request_rows),
        ("resources.jsonl", resource_rows),
        ("outcome-latency.jsonl", outcome_rows),
    )
    for source_name, rows in collections:
        for row_number, row in enumerate(rows, start=1):
            value = row.get("attempt_id")
            if not isinstance(value, str) or not value.strip():
                audit.error(
                    "attempt_id",
                    "evidence row has no nonempty attempt_id",
                    source=source_name,
                    row=row_number,
                )
                continue
            observed[value].append("%s:%d" % (source_name, row_number))
            if manifest_attempt is not None and value != manifest_attempt:
                audit.error(
                    "attempt_id_mismatch",
                    "evidence row belongs to a different attempt",
                    source=source_name,
                    row=row_number,
                    manifest_attempt_id=manifest_attempt,
                    observed_attempt_id=value,
                )
    if len(observed) > 1:
        audit.error(
            "attempt_id_mismatch",
            "bundle contains more than one attempt identity",
            attempt_ids=sorted(observed),
        )
    return manifest_attempt


def _validate_prior_attempt_eligibility(
    bundle: Path,
    manifest: Mapping[str, Any],
    audit: _Audit,
    enforce_eligibility: bool,
    preterminal: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    prior_attempts = manifest.get("prior_attempts")
    prior_flag = manifest.get("prior_attempt_exists")
    result: Dict[str, Any] = {
        "declared_prior_attempts": 0,
        "valid_complete_predecessors": [],
        "eligible": True,
    }
    ledger_state = _validate_attempt_ledger(
        bundle, manifest, audit, preterminal
    )
    if not isinstance(prior_attempts, list):
        audit.error(
            "prior_attempt_chain",
            "formal manifest prior_attempts must be a list",
        )
        return result, ledger_state
    result["declared_prior_attempts"] = len(prior_attempts)
    if prior_flag is not bool(prior_attempts):
        audit.error(
            "prior_attempt_chain",
            "prior_attempt_exists disagrees with the predecessor list",
        )
    manifest_attempt_id = manifest.get("attempt_id")
    manifest_started_at = _parse_attempt_timestamp(
        manifest.get("started_at_utc"), audit, "current"
    )
    _validate_attempt_time_identity(
        manifest, manifest_started_at, audit, "current"
    )
    attempt_policy = manifest.get("attempt_policy")
    if (
        not isinstance(attempt_policy, dict)
        or attempt_policy.get("directory") != bundle.name
    ):
        audit.error(
            "prior_attempt_directory",
            "manifest attempt directory differs from the audited bundle directory",
            declared=(
                attempt_policy.get("directory")
                if isinstance(attempt_policy, dict)
                else None
            ),
            observed=bundle.name,
        )
    config = manifest.get("config")
    expected_formal_root = (
        Path(__file__).resolve().parents[2] / FORMAL_ATTEMPT_ROOT
    ).resolve()
    if (
        isinstance(config, dict)
        and config.get("mode") == "full"
        and manifest.get("seeds") == list(PROTOCOL_FULL_SEEDS)
        and bundle.parent.resolve() != expected_formal_root
    ):
        audit.error(
            "formal_root",
            "formal v3 attempt is outside the frozen v3 attempt root",
            observed=str(bundle.parent.resolve()),
            expected=str(expected_formal_root),
        )

    current_start = ledger_state.get("current_start")
    current_sequence = (
        current_start.get("sequence")
        if isinstance(current_start, dict)
        else None
    )
    prior_ledger_attempts = [
        attempt
        for attempt in ledger_state.get("attempts", [])
        if isinstance(current_sequence, int)
        and int(attempt.get("terminal_row", 0)) < current_sequence
    ]
    discovered_prior_attempts: List[Dict[str, Any]] = []
    for attempt in prior_ledger_attempts:
        start = attempt.get("start")
        terminal = attempt.get("terminal")
        record = attempt.get("record")
        if not all(isinstance(value, dict) for value in (start, terminal, record)):
            continue
        record_name = terminal.get("terminal_record")
        discovered_prior_attempts.append(
            {
                "attempt_id": start.get("attempt_id"),
                "kind": record.get("kind"),
                "status": (
                    "producer_complete"
                    if record_name == "manifest.json"
                    else "invalid_operational_failure"
                ),
                "directory": start.get("directory"),
                "record": record_name,
                "record_sha256": terminal.get("terminal_record_sha256"),
                "record_bytes": terminal.get("terminal_record_bytes"),
                "started_at_utc": start.get("started_at_utc"),
            }
        )
    result["discovered_prior_attempts"] = len(discovered_prior_attempts)
    if prior_attempts != discovered_prior_attempts:
        audit.error(
            "prior_attempt_chain",
            "declared predecessor chain differs from retained earlier sibling attempts",
            declared=prior_attempts,
            discovered=discovered_prior_attempts,
        )

    # Eligibility is historical data, not a new interpretation by whatever
    # auditor happens to execute today.  Only the immutable report status bound
    # by that predecessor's TERMINAL event can block a later attempt.
    for attempt in prior_ledger_attempts:
        start = attempt.get("start")
        terminal = attempt.get("terminal")
        if not isinstance(start, dict) or not isinstance(terminal, dict):
            continue
        predecessor_status = terminal.get("preterminal_report_status")
        if (
            terminal.get("terminal_record") == "manifest.json"
            and predecessor_status in ("pass", "fail")
        ):
            result["valid_complete_predecessors"].append(
                {
                    "attempt_id": start.get("attempt_id"),
                    "status": predecessor_status,
                    "directory": start.get("directory"),
                }
            )
    if result["valid_complete_predecessors"]:
        result["eligible"] = False
        if enforce_eligibility:
            audit.error(
                "attempt_ineligible",
                "a TERMINAL-bound pass/fail predecessor is already eligible",
                predecessors=result["valid_complete_predecessors"],
            )
    return result, ledger_state


def _parse_attempt_timestamp(
    value: Any, audit: _Audit, position: Any
) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        audit.error(
            "prior_attempt_timestamp",
            "formal attempt has no parseable UTC start timestamp",
            position=position,
        )
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        audit.error(
            "prior_attempt_timestamp",
            "formal attempt has no parseable UTC start timestamp",
            position=position,
            observed=value,
        )
        return None
    return parsed.astimezone(timezone.utc)


def _validate_attempt_time_identity(
    record: Mapping[str, Any],
    started_at: Optional[datetime],
    audit: _Audit,
    position: Any,
) -> None:
    """Bind chronology to the producer's timestamp-and-Git attempt identity."""

    git = record.get("git")
    head_commit = git.get("head_commit") if isinstance(git, dict) else None
    if (
        started_at is None
        or not isinstance(head_commit, str)
        or len(head_commit) != 40
        or any(character not in "0123456789abcdef" for character in head_commit)
    ):
        audit.error(
            "prior_attempt_identity",
            "formal attempt cannot bind its start time to a full Git commit",
            position=position,
        )
        return
    expected = "%s-%s" % (
        started_at.strftime("%Y%m%dT%H%M%S%fZ"),
        head_commit[:12],
    )
    if record.get("attempt_id") != expected:
        audit.error(
            "prior_attempt_identity",
            "attempt ID does not match its UTC start time and Git commit",
            position=position,
            observed=record.get("attempt_id"),
            expected=expected,
        )


def _attempts_sha256(attempts: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(attempt) for attempt in attempts],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _valid_attempt_directory_name(value: Any) -> bool:
    """Return whether a ledger directory is one safe direct-child name."""

    return bool(
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
        and Path(value).parts == (value,)
    )


def _validate_protocol_genesis(
    entry: Mapping[str, Any], audit: _Audit
) -> None:
    """Verify the one v3 genesis and its immutable v1/v2 provenance bindings."""

    expected_keys = {
        "schema_version",
        "experiment_id",
        "event",
        "sequence",
        "recorded_at_utc",
        "v1_preservation",
        "v2_preservation",
        "v3_contract",
        "formal_source_anchor",
        "previous_entry_sha256",
        "entry_sha256",
    }
    if set(entry) != expected_keys:
        audit.error(
            "attempt_ledger_genesis",
            "protocol genesis has unexpected or missing fields",
            observed=sorted(entry),
            expected=sorted(expected_keys),
        )
    recorded_at = entry.get("recorded_at_utc")
    if not isinstance(recorded_at, str):
        audit.error(
            "attempt_ledger_genesis",
            "protocol genesis has no UTC recording time",
        )
    else:
        normalized = recorded_at[:-1] + "+00:00" if recorded_at.endswith("Z") else recorded_at
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = None
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            audit.error(
                "attempt_ledger_genesis",
                "protocol genesis recorded_at_utc is not an aware UTC timestamp",
                observed=recorded_at,
            )
    expected_v1 = {
        "manifest_path": V1_PRESERVATION_MANIFEST_PATH,
        "manifest_sha256": PINNED_V1_PRESERVATION_MANIFEST_SHA256,
        "archive_path": V1_PRESERVATION_ARCHIVE_PATH,
        "archive_sha256": PINNED_V1_PRESERVATION_ARCHIVE_SHA256,
        "formal_root": PINNED_V1_ROOT,
        "attempt_ledger_sha256": PINNED_V1_LEDGER_SHA256,
        "terminal_attempt_id": PINNED_V1_ATTEMPT_ID,
        "terminal_entry_sha256": PINNED_V1_TERMINAL_ENTRY_SHA256,
        "failure_sha256": PINNED_V1_FAILURE_SHA256,
    }
    if entry.get("v1_preservation") != expected_v1:
        audit.error(
            "attempt_ledger_genesis",
            "protocol genesis does not exactly bind the preserved v1 invalid attempt",
            observed=entry.get("v1_preservation"),
            expected=expected_v1,
        )
    project_root = Path(__file__).resolve().parents[2]
    try:
        expected_v2 = _v2_preservation_identity(project_root)
    except (OSError, RuntimeError, UnicodeError) as exc:
        expected_v2 = None
        audit.error(
            "v2_preservation",
            "local tracked v2 INVALID preservation differs from its frozen identity",
            detail=str(exc),
        )
    if entry.get("v2_preservation") != expected_v2:
        audit.error(
            "attempt_ledger_genesis",
            "protocol genesis does not exactly bind the preserved v2 invalid attempt",
            observed=entry.get("v2_preservation"),
            expected=expected_v2,
        )
    expected_contract = {
        "path": DEFAULT_CONTRACT_PATH,
        "sha256": PINNED_CONTRACT_SHA256,
    }
    if entry.get("v3_contract") != expected_contract:
        audit.error(
            "attempt_ledger_genesis",
            "protocol genesis does not bind the frozen v3 contract",
            observed=entry.get("v3_contract"),
            expected=expected_contract,
        )
    if not _formal_source_anchor_structurally_valid(
        entry.get("formal_source_anchor")
    ):
        audit.error(
            "attempt_ledger_genesis",
            "protocol genesis does not bind a structurally valid annotated source anchor",
        )

    preservation_manifest_path = project_root / V1_PRESERVATION_MANIFEST_PATH
    preservation_archive_path = project_root / V1_PRESERVATION_ARCHIVE_PATH
    if (
        not preservation_manifest_path.is_file()
        or _sha256_file(preservation_manifest_path)
        != PINNED_V1_PRESERVATION_MANIFEST_SHA256
        or int(preservation_manifest_path.stat().st_size)
        != PINNED_V1_PRESERVATION_MANIFEST_BYTES
    ):
        audit.error(
            "v1_preservation",
            "local v1 preservation manifest differs from its frozen identity",
        )
    if (
        not preservation_archive_path.is_file()
        or _sha256_file(preservation_archive_path)
        != PINNED_V1_PRESERVATION_ARCHIVE_SHA256
        or int(preservation_archive_path.stat().st_size)
        != PINNED_V1_PRESERVATION_ARCHIVE_BYTES
    ):
        audit.error(
            "v1_preservation",
            "local v1 preservation archive differs from its frozen identity",
        )
    preserved = _load_json(
        preservation_manifest_path, audit, "v1_preservation"
    ) if preservation_manifest_path.is_file() else None
    if isinstance(preserved, dict):
        attempt = preserved.get("attempt")
        archive = preserved.get("archive")
        files = preserved.get("files")
        ledger_identity = (
            files.get("gate7-onnx-attempts/attempt-ledger.jsonl")
            if isinstance(files, dict)
            else None
        )
        if (
            preserved.get("schema_version")
            != "carma-gate7-v1-preservation-v1"
            or not isinstance(attempt, dict)
            or attempt.get("attempt_id") != PINNED_V1_ATTEMPT_ID
            or attempt.get("directory") != PINNED_V1_ATTEMPT_DIRECTORY
            or attempt.get("status") != "invalid"
            or attempt.get("failure_sha256") != PINNED_V1_FAILURE_SHA256
            or attempt.get("terminal_entry_sha256")
            != PINNED_V1_TERMINAL_ENTRY_SHA256
            or not isinstance(archive, dict)
            or archive.get("path") != V1_PRESERVATION_ARCHIVE_PATH
            or archive.get("sha256") != PINNED_V1_PRESERVATION_ARCHIVE_SHA256
            or archive.get("bytes") != PINNED_V1_PRESERVATION_ARCHIVE_BYTES
            or not isinstance(ledger_identity, dict)
            or ledger_identity.get("sha256") != PINNED_V1_LEDGER_SHA256
            or ledger_identity.get("bytes") != PINNED_V1_LEDGER_BYTES
        ):
            audit.error(
                "v1_preservation",
                "v1 preservation manifest is internally inconsistent",
            )


def _terminal_intent_from_retained_evidence(
    start: Mapping[str, Any],
    terminal: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    """Reconstruct the target's immutable intent from retained evidence."""

    report_status = terminal.get("preterminal_report_status")
    intent: Dict[str, Any] = {
        "schema_version": TERMINAL_INTENT_SCHEMA_VERSION,
        "attempt_id": start.get("attempt_id"),
        "directory": start.get("directory"),
        "terminal_record": "manifest.json",
        "terminal_record_sha256": terminal.get("terminal_record_sha256"),
        "terminal_record_bytes": terminal.get("terminal_record_bytes"),
        "preterminal_report": PRETERMINAL_REPORT_NAME,
        "preterminal_report_sha256": terminal.get("preterminal_report_sha256"),
        "preterminal_report_bytes": terminal.get("preterminal_report_bytes"),
        "preterminal_report_status": report_status,
        "preterminal_report_claimable": terminal.get(
            "preterminal_report_claimable"
        ),
        "auditor_identity": terminal.get("auditor_identity"),
        "target_exit_status": ADJUDICATION_EXIT_STATUSES.get(report_status),
        "ledger_prefix": manifest.get("attempt_ledger"),
    }
    intent["intent_sha256"] = _canonical_mapping_sha256(intent)
    return intent


def _validate_bootstrap_completion(
    completion: Any,
    start: Mapping[str, Any],
    terminal: Mapping[str, Any],
    manifest: Mapping[str, Any],
    audit: _Audit,
    row: int,
) -> bool:
    """Validate the parent's post-target seal and terminal-intent receipt."""

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
    if not isinstance(completion, Mapping) or set(completion) != expected_fields:
        audit.error(
            "attempt_ledger_bootstrap_completion",
            "manifest TERMINAL lacks the exact parent-bootstrap completion schema",
            row=row,
        )
        return False

    unsigned_completion = dict(completion)
    unsigned_completion.pop("attestation_sha256", None)
    completion_self_hash_valid = bool(
        _valid_sha256(completion.get("attestation_sha256"))
        and completion.get("attestation_sha256")
        == _canonical_mapping_sha256(unsigned_completion)
    )

    entrypoint = start.get("entrypoint_attestation")
    parent_bootstrap = (
        entrypoint.get("bootstrap_attestation")
        if isinstance(entrypoint, Mapping)
        else None
    )
    parent_bootstrap_sha256 = (
        entrypoint.get("bootstrap_attestation_sha256")
        if isinstance(entrypoint, Mapping)
        else None
    )
    parent_unsigned: Optional[Dict[str, Any]] = None
    preimport_source = None
    dependency = None
    python_environment = None
    if isinstance(parent_bootstrap, Mapping):
        parent_unsigned = dict(parent_bootstrap)
        parent_unsigned.pop("attestation_sha256", None)
        preimport_source = parent_bootstrap.get("preimport_source")
        dependency = parent_bootstrap.get("dependency")
        python = parent_bootstrap.get("python")
        python_environment = (
            python.get("environment") if isinstance(python, Mapping) else None
        )

    source_identities = manifest.get("source_identities")
    producer_identity = (
        source_identities.get(
            "benchmarks/carma/gate7_v3_onnx_integration_benchmark.py"
        )
        if isinstance(source_identities, Mapping)
        else None
    )
    expected_exit_status = ADJUDICATION_EXIT_STATUSES.get(
        terminal.get("preterminal_report_status")
    )
    intent = _terminal_intent_from_retained_evidence(start, terminal, manifest)
    ledger_prefix = intent.get("ledger_prefix")

    valid = bool(
        completion.get("schema_version")
        == BOOTSTRAP_COMPLETION_SCHEMA_VERSION
        and completion.get("phase") == "post_target"
        and completion.get("role") == "parent"
        and completion.get("launch_mode") == "full"
        and completion.get("checks_passed") is True
        and completion_self_hash_valid
        and isinstance(parent_bootstrap, Mapping)
        and parent_unsigned is not None
        and _valid_sha256(parent_bootstrap.get("attestation_sha256"))
        and parent_bootstrap.get("attestation_sha256")
        == _canonical_mapping_sha256(parent_unsigned)
        and completion.get("bootstrap_attestation_sha256")
        == parent_bootstrap.get("attestation_sha256")
        and completion.get("bootstrap_attestation_sha256")
        == parent_bootstrap_sha256
        and isinstance(producer_identity, Mapping)
        and _valid_sha256(producer_identity.get("sha256"))
        and completion.get("target_sha256")
        == parent_bootstrap.get("target_sha256")
        and completion.get("target_sha256")
        == producer_identity.get("sha256")
        and isinstance(preimport_source, Mapping)
        and completion.get("preimport_source_sha256")
        == _canonical_mapping_sha256(preimport_source)
        and isinstance(dependency, Mapping)
        and completion.get("dependency_sha256")
        == _canonical_mapping_sha256(dependency)
        and isinstance(python_environment, Mapping)
        and completion.get("python_environment_sha256")
        == _canonical_mapping_sha256(python_environment)
        and isinstance(expected_exit_status, int)
        and not isinstance(completion.get("target_exit_status"), bool)
        and completion.get("target_exit_status") == expected_exit_status
        and _valid_sha256(completion.get("terminal_intent_sha256"))
        and completion.get("terminal_intent_sha256")
        == intent.get("intent_sha256")
        and isinstance(ledger_prefix, Mapping)
        and set(ledger_prefix)
        == {
            "path",
            "prefix_rows",
            "prefix_sha256",
            "prefix_bytes",
            "entry_sha256",
            "event",
        }
    )
    if not valid:
        audit.error(
            "attempt_ledger_bootstrap_completion",
            "parent-bootstrap completion is malformed or differs from retained START, manifest, preterminal report, or terminal intent",
            row=row,
        )
    return valid


def _validate_attempt_ledger(
    bundle: Path,
    manifest: Mapping[str, Any],
    audit: _Audit,
    preterminal: bool,
) -> Dict[str, Any]:
    """Validate the two-phase START/TERMINAL ledger and bound bytes."""

    state: Dict[str, Any] = {
        "entries": [],
        "attempts": [],
        "current_start": None,
        "current_terminal": None,
        "current_terminal_expectation": None,
    }
    declaration = manifest.get("attempt_ledger")
    if not isinstance(declaration, dict):
        audit.error(
            "attempt_ledger",
            "formal manifest omits the root attempt-ledger START prefix",
        )
        return state
    if declaration.get("path") != "attempt-ledger.jsonl":
        audit.error("attempt_ledger", "formal manifest names an unexpected ledger")
    if declaration.get("event") != "START":
        audit.error(
            "attempt_ledger",
            "formal manifest must identify its START event",
        )
    ledger_path = bundle.parent / "attempt-ledger.jsonl"
    if not ledger_path.is_file():
        audit.error("attempt_ledger", "formal attempt ledger is missing")
        return state

    raw_lines = ledger_path.read_bytes().splitlines(keepends=True)
    entries: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []
    previous_hash: Optional[str] = None
    seen_attempts: set = set()
    seen_directories: set = set()
    open_start: Optional[Dict[str, Any]] = None
    genesis_count = 0
    genesis_anchor: Optional[Mapping[str, Any]] = None
    for row, raw_line in enumerate(raw_lines, start=1):
        try:
            entry = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            audit.error("attempt_ledger", "ledger is not valid JSONL", row=row)
            entries.append({})
            continue
        if not isinstance(entry, dict):
            audit.error("attempt_ledger", "ledger row is not an object", row=row)
            entries.append({})
            continue
        canonical = (
            json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if canonical != raw_line:
            audit.error("attempt_ledger", "ledger row is not canonical", row=row)
        entry_hash = entry.get("entry_sha256")
        unhashed = dict(entry)
        unhashed.pop("entry_sha256", None)
        computed_hash = hashlib.sha256(
            json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        event = entry.get("event")
        common_chain_invalid = (
            entry.get("schema_version") != ATTEMPT_LEDGER_SCHEMA_VERSION
            or entry.get("experiment_id") != EXPERIMENT_ID
            or entry.get("sequence") != row
            or entry.get("previous_entry_sha256") != previous_hash
            or entry_hash != computed_hash
            or event not in ("PROTOCOL_GENESIS", "START", "TERMINAL")
        )
        if common_chain_invalid:
            audit.error(
                "attempt_ledger",
                "ledger event hash chain or identity is invalid",
                row=row,
            )

        if event == "PROTOCOL_GENESIS":
            genesis_count += 1
            if row != 1 or genesis_count != 1 or previous_hash is not None:
                audit.error(
                    "attempt_ledger_genesis",
                    "protocol genesis must be the unique first ledger row",
                    row=row,
                )
            _validate_protocol_genesis(entry, audit)
            if isinstance(entry.get("formal_source_anchor"), Mapping):
                genesis_anchor = entry["formal_source_anchor"]
            if isinstance(entry_hash, str):
                previous_hash = entry_hash
            entries.append(entry)
            continue

        if genesis_count != 1:
            audit.error(
                "attempt_ledger_genesis",
                "START/TERMINAL appears before the unique protocol genesis",
                row=row,
            )
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
        expected_fields = start_fields if event == "START" else terminal_fields
        if set(entry) != expected_fields:
            audit.error(
                "attempt_ledger",
                "ledger event has unexpected or missing fields",
                row=row,
                event=event,
                observed=sorted(entry),
                expected=sorted(expected_fields),
            )
        started_at = _parse_attempt_timestamp(
            entry.get("started_at_utc"), audit, "ledger:%d" % row
        )
        head_commit = entry.get("head_commit")
        expected_attempt_id = (
            "%s-%s"
            % (
                started_at.strftime("%Y%m%dT%H%M%S%fZ"),
                head_commit[:12],
            )
            if started_at is not None
            and isinstance(head_commit, str)
            and len(head_commit) == 40
            and all(character in "0123456789abcdef" for character in head_commit)
            else None
        )
        attempt_id = entry.get("attempt_id")
        directory = entry.get("directory")
        if (
            expected_attempt_id is None
            or attempt_id != expected_attempt_id
            or not _valid_attempt_directory_name(directory)
        ):
            audit.error(
                "attempt_ledger",
                "ledger attempt identity is invalid",
                row=row,
            )

        if event == "START":
            if open_start is not None:
                audit.error(
                    "attempt_ledger_unresolved_start",
                    "a START appears before the prior START has a TERMINAL",
                    row=row,
                )
            if attempt_id in seen_attempts or directory in seen_directories:
                audit.error(
                    "attempt_ledger",
                    "START reuses an attempt ID or directory",
                    row=row,
                )
            contract = entry.get("contract")
            if (
                entry.get("prior_attempt_count") != len(attempts)
                or not _valid_sha256(entry.get("prior_attempts_sha256"))
                or not isinstance(contract, Mapping)
                or set(contract) != {"path", "sha256", "bytes"}
                or contract.get("path") != DEFAULT_CONTRACT_PATH
                or contract.get("sha256") != PINNED_CONTRACT_SHA256
                or not isinstance(contract.get("bytes"), int)
                or isinstance(contract.get("bytes"), bool)
                or contract.get("bytes", 0) <= 0
                or not _valid_sha256(entry.get("source_snapshot_sha256"))
                or entry.get("formal_source_anchor") != genesis_anchor
                or not _valid_sha256(
                    entry.get("dependency_attestation_sha256")
                )
                or not _valid_sha256(entry.get("environment_sha256"))
                or not _formal_entrypoint_structurally_valid(
                    entry.get("entrypoint_attestation")
                )
                or not _power_observation_structurally_valid(
                    entry.get("pre_start_power")
                )
                or not isinstance(entry.get("retained_inputs"), Mapping)
            ):
                audit.error(
                    "attempt_ledger",
                    "START predecessor count/hash is inconsistent",
                    row=row,
                )
            if isinstance(attempt_id, str):
                seen_attempts.add(attempt_id)
            if isinstance(directory, str):
                seen_directories.add(directory)
            open_start = entry
        elif event == "TERMINAL":
            if open_start is None:
                audit.error(
                    "attempt_ledger_terminal",
                    "TERMINAL has no immediately preceding START",
                    row=row,
                )
            else:
                if any(
                    entry.get(field) != open_start.get(field)
                    for field in (
                        "attempt_id",
                        "started_at_utc",
                        "head_commit",
                        "directory",
                    )
                ):
                    audit.error(
                        "attempt_ledger_terminal",
                        "TERMINAL identity differs from its START",
                        row=row,
                    )
                if any(
                    entry.get(field) != open_start.get(field)
                    for field in (
                        "formal_source_anchor",
                        "dependency_attestation_sha256",
                        "environment_sha256",
                    )
                ):
                    audit.error(
                        "attempt_ledger_terminal",
                        "TERMINAL attestation bindings differ from its START",
                        row=row,
                    )
                attempts.append(
                    {
                        "start": open_start,
                        "terminal": entry,
                        "start_row": row - 1,
                        "terminal_row": row,
                    }
                )
            open_start = None
        if isinstance(entry_hash, str):
            previous_hash = entry_hash
        entries.append(entry)

    if genesis_count != 1:
        audit.error(
            "attempt_ledger_genesis",
            "formal v3 ledger must contain exactly one protocol genesis",
            count=genesis_count,
        )

    current_id = manifest.get("attempt_id")
    current_open = (
        open_start is not None
        and open_start.get("attempt_id") == current_id
        and open_start.get("directory") == bundle.name
    )
    if open_start is not None and not (preterminal and current_open):
        audit.error(
            "attempt_ledger_unresolved_start",
            "ledger retains a START without a TERMINAL",
            attempt_id=open_start.get("attempt_id"),
        )

    # Every TERMINAL content-addresses the immutable terminal record.  A
    # successful producer record also binds the preterminal adjudication.
    formal_root = bundle.parent.resolve()
    for attempt in attempts:
        start = attempt["start"]
        terminal = attempt["terminal"]
        directory = start.get("directory")
        if not _valid_attempt_directory_name(directory):
            audit.error(
                "attempt_ledger_directory",
                "TERMINAL attempt has an unsafe directory name",
                row=attempt["terminal_row"],
                directory=directory,
            )
            continue
        assert isinstance(directory, str)
        attempt_dir = formal_root / directory
        try:
            resolved_attempt_dir = attempt_dir.resolve(strict=True)
        except OSError as exc:
            audit.error(
                "attempt_ledger_directory",
                "ledger-backed attempt directory is missing or unreadable",
                row=attempt["terminal_row"],
                directory=directory,
                detail=str(exc),
            )
            continue
        if (
            attempt_dir.is_symlink()
            or not attempt_dir.is_dir()
            or resolved_attempt_dir.parent != formal_root
            or resolved_attempt_dir != attempt_dir
        ):
            audit.error(
                "attempt_ledger_directory",
                "ledger-backed attempt directory is not one regular direct child",
                row=attempt["terminal_row"],
                directory=directory,
            )
            continue
        record_name = terminal.get("terminal_record")
        if record_name not in TERMINAL_RECORD_NAMES:
            audit.error(
                "attempt_ledger_terminal",
                "TERMINAL names an unsupported terminal record",
                row=attempt["terminal_row"],
            )
            continue
        record_path = attempt_dir / str(record_name)
        retained_terminal_names = [
            name
            for name in TERMINAL_RECORD_NAMES
            if (attempt_dir / name).is_file()
        ]
        if retained_terminal_names != [record_name]:
            audit.error(
                "attempt_ledger_terminal",
                "attempt directory does not retain exactly its bound terminal record",
                row=attempt["terminal_row"],
                retained=retained_terminal_names,
            )
        if not record_path.is_file():
            audit.error(
                "attempt_ledger_terminal",
                "TERMINAL-bound record is missing",
                path=str(record_path),
            )
            continue
        if (
            terminal.get("terminal_record_sha256") != _sha256_file(record_path)
            or terminal.get("terminal_record_bytes")
            != int(record_path.stat().st_size)
        ):
            audit.error(
                "attempt_ledger_terminal",
                "retained terminal bytes differ from the TERMINAL binding",
                row=attempt["terminal_row"],
            )
        record = _load_json(record_path, audit, "attempt_terminal_record")
        attempt["record"] = record
        if record is None:
            continue
        record_git = record.get("git")
        record_prior = record.get("prior_attempts")
        record_declaration = record.get("attempt_ledger")
        record_config = record.get("config")
        record_dependency = record.get("dependency_attestation")
        record_power = record.get("power_observations")
        record_contract = record.get("contract")
        record_contract_start_projection = (
            {
                key: record_contract.get(key)
                for key in ("path", "sha256", "bytes")
            }
            if isinstance(record_contract, Mapping)
            else None
        )
        start_row = int(attempt["start_row"])
        start_prefix = b"".join(raw_lines[:start_row])
        if (
            record.get("experiment_id") != EXPERIMENT_ID
            or not isinstance(record_config, dict)
            or record_config.get("mode") != "full"
            or record.get("attempt_id") != start.get("attempt_id")
            or record.get("started_at_utc") != start.get("started_at_utc")
            or not isinstance(record_git, dict)
            or record_git.get("head_commit") != start.get("head_commit")
            or not isinstance(record_prior, list)
            or start.get("prior_attempt_count") != len(record_prior)
            or start.get("prior_attempts_sha256")
            != _attempts_sha256(record_prior)
            or not isinstance(record_declaration, dict)
            or record_declaration.get("path") != "attempt-ledger.jsonl"
            or record_declaration.get("prefix_rows") != start_row
            or record_declaration.get("prefix_sha256")
            != hashlib.sha256(start_prefix).hexdigest()
            or record_declaration.get("prefix_bytes") != len(start_prefix)
            or record_declaration.get("entry_sha256")
            != start.get("entry_sha256")
            or record_contract_start_projection != start.get("contract")
            or record.get("source_snapshot_sha256")
            != start.get("source_snapshot_sha256")
            or record.get("formal_source_anchor")
            != start.get("formal_source_anchor")
            or not isinstance(record_dependency, Mapping)
            or record_dependency.get("attestation_sha256")
            != start.get("dependency_attestation_sha256")
            or record.get("environment_sha256")
            != start.get("environment_sha256")
            or record.get("entrypoint_attestation")
            != start.get("entrypoint_attestation")
            or not isinstance(record_power, Mapping)
            or record_power.get("pre_start") != start.get("pre_start_power")
            or record.get("retained_inputs") != start.get("retained_inputs")
            or terminal.get("formal_source_anchor")
            != start.get("formal_source_anchor")
            or terminal.get("dependency_attestation_sha256")
            != start.get("dependency_attestation_sha256")
            or terminal.get("environment_sha256")
            != start.get("environment_sha256")
        ):
            audit.error(
                "attempt_ledger_terminal",
                "terminal record differs from its START registration",
                row=attempt["terminal_row"],
            )

        if record_name == "manifest.json" and (
            record.get("kind") != "prospective_gate7_followup"
            or record.get("formal_claimable_mode") is not True
        ):
            audit.error(
                "attempt_ledger_terminal",
                "manifest TERMINAL is not a formal completed producer record",
                row=attempt["terminal_row"],
            )
        if record_name == "attempt-failure.json" and (
            record.get("kind") != "prospective_gate7_failed_attempt"
            or record.get("status") != "invalid"
        ):
            audit.error(
                "attempt_ledger_terminal",
                "failure TERMINAL is not a retained invalid operational record",
                row=attempt["terminal_row"],
            )

        if record_name == "manifest.json":
            report_name = terminal.get("preterminal_report")
            report_path = attempt_dir / str(report_name)
            if report_name != PRETERMINAL_REPORT_NAME or not report_path.is_file():
                audit.error(
                    "attempt_ledger_preterminal",
                    "manifest TERMINAL lacks the immutable preterminal report",
                    row=attempt["terminal_row"],
                )
                continue
            if (
                terminal.get("preterminal_report_sha256")
                != _sha256_file(report_path)
                or terminal.get("preterminal_report_bytes")
                != int(report_path.stat().st_size)
            ):
                audit.error(
                    "attempt_ledger_preterminal",
                    "preterminal bytes differ from their TERMINAL binding",
                    row=attempt["terminal_row"],
                )
            report = _load_json(
                report_path, audit, "attempt_preterminal_report"
            )
            attempt["preterminal_report"] = report
            report_status = terminal.get("preterminal_report_status")
            report_claimable = terminal.get("preterminal_report_claimable")
            auditor_identity = terminal.get("auditor_identity")
            source_identities = record.get("source_identities")
            frozen_auditor = (
                source_identities.get("benchmarks/carma/gate7_v3_audit.py")
                if isinstance(source_identities, dict)
                else None
            )
            auditor_identity_valid = (
                isinstance(auditor_identity, dict)
                and auditor_identity.get("path")
                == "benchmarks/carma/gate7_v3_audit.py"
                and isinstance(auditor_identity.get("sha256"), str)
                and len(auditor_identity["sha256"]) == 64
                and all(
                    character in "0123456789abcdef"
                    for character in auditor_identity["sha256"]
                )
                and isinstance(auditor_identity.get("bytes"), int)
                and not isinstance(auditor_identity.get("bytes"), bool)
                and auditor_identity["bytes"] > 0
            )
            if (
                report is None
                or report.get("schema_version") != AUDIT_SCHEMA_VERSION
                or report.get("audit_phase") != "preterminal"
                or report.get("attempt_id") != start.get("attempt_id")
                or report_status not in ADJUDICATION_STATUSES
                or report.get("status") != report_status
                or not isinstance(report_claimable, bool)
                or report_claimable != (report_status in ("pass", "fail"))
                or report.get("claimable") is not report_claimable
                or not auditor_identity_valid
                or report.get("auditor_identity") != auditor_identity
                or not _auditor_identity_matches_source(
                    auditor_identity, frozen_auditor
                )
            ):
                audit.error(
                    "attempt_ledger_preterminal",
                    "bound status, claimability, or auditor identity is inconsistent",
                    row=attempt["terminal_row"],
                )
            _validate_bootstrap_completion(
                terminal.get("bootstrap_completion"),
                start,
                terminal,
                record,
                audit,
                int(attempt["terminal_row"]),
            )
        elif (
            terminal.get("preterminal_report") is not None
            or terminal.get("preterminal_report_sha256") is not None
            or terminal.get("preterminal_report_bytes") is not None
            or terminal.get("preterminal_report_status") != "invalid"
            or terminal.get("preterminal_report_claimable") is not False
            or terminal.get("auditor_identity") is not None
            or terminal.get("bootstrap_completion") is not None
        ):
            audit.error(
                "attempt_ledger_terminal",
                "failure TERMINAL has an invalid preterminal or bootstrap-completion binding",
                row=attempt["terminal_row"],
            )

    matching_starts = [
        entry
        for entry in entries
        if entry.get("event") == "START"
        and entry.get("attempt_id") == current_id
        and entry.get("directory") == bundle.name
    ]
    if len(matching_starts) != 1:
        audit.error(
            "attempt_ledger",
            "audited manifest lacks exactly one matching START",
            matches=len(matching_starts),
        )
    else:
        current_start = matching_starts[0]
        state["current_start"] = current_start
        current_start_row = entries.index(current_start) + 1
        prior_attempts = manifest.get("prior_attempts")
        git = manifest.get("git")
        expected = {
            "attempt_id": current_id,
            "started_at_utc": manifest.get("started_at_utc"),
            "head_commit": git.get("head_commit") if isinstance(git, dict) else None,
            "directory": bundle.name,
            "prior_attempt_count": (
                len(prior_attempts) if isinstance(prior_attempts, list) else None
            ),
            "prior_attempts_sha256": (
                _attempts_sha256(prior_attempts)
                if isinstance(prior_attempts, list)
                else None
            ),
            "contract": (
                {
                    key: manifest.get("contract", {}).get(key)
                    for key in ("path", "sha256", "bytes")
                }
                if isinstance(manifest.get("contract"), Mapping)
                else None
            ),
            "source_snapshot_sha256": manifest.get(
                "source_snapshot_sha256"
            ),
            "formal_source_anchor": manifest.get("formal_source_anchor"),
            "dependency_attestation_sha256": (
                manifest.get("dependency_attestation", {}).get(
                    "attestation_sha256"
                )
                if isinstance(manifest.get("dependency_attestation"), Mapping)
                else None
            ),
            "environment_sha256": manifest.get("environment_sha256"),
            "entrypoint_attestation": manifest.get("entrypoint_attestation"),
            "pre_start_power": (
                manifest.get("power_observations", {}).get("pre_start")
                if isinstance(manifest.get("power_observations"), Mapping)
                else None
            ),
            "retained_inputs": manifest.get("retained_inputs"),
        }
        if any(current_start.get(key) != value for key, value in expected.items()):
            audit.error(
                "attempt_ledger",
                "current START differs from the audited manifest",
            )
        prefix = b"".join(raw_lines[:current_start_row])
        if (
            declaration.get("prefix_rows") != current_start_row
            or declaration.get("prefix_sha256")
            != hashlib.sha256(prefix).hexdigest()
            or declaration.get("prefix_bytes") != len(prefix)
            or declaration.get("entry_sha256")
            != current_start.get("entry_sha256")
        ):
            audit.error(
                "attempt_ledger",
                "manifest names the wrong START prefix",
            )

    current_attempt = next(
        (
            attempt
            for attempt in attempts
            if attempt["start"].get("attempt_id") == current_id
            and attempt["start"].get("directory") == bundle.name
        ),
        None,
    )
    if current_attempt is not None:
        state["current_terminal"] = current_attempt.get("terminal")
        if current_attempt["terminal"].get("terminal_record") != "manifest.json":
            audit.error(
                "attempt_ledger_terminal",
                "audited completed manifest is not the current TERMINAL record",
            )
        report = current_attempt.get("preterminal_report")
        if isinstance(report, dict):
            state["current_terminal_expectation"] = {
                "status": report.get("status"),
                "claimable": report.get("claimable"),
            }
    if preterminal and current_attempt is not None:
        audit.error(
            "attempt_ledger_preterminal",
            "preterminal audit requires current START without TERMINAL",
        )
    if not preterminal and current_attempt is None:
        audit.error(
            "attempt_ledger_terminal",
            "default formal audit requires a current TERMINAL",
        )

    registered_directory_counts = Counter(
        entry.get("directory")
        for entry in entries
        if entry.get("event") == "START"
        and _valid_attempt_directory_name(entry.get("directory"))
    )
    for directory, count in registered_directory_counts.items():
        attempt_dir = formal_root / directory
        try:
            resolved_attempt_dir = attempt_dir.resolve(strict=True)
        except OSError as exc:
            audit.error(
                "attempt_ledger_directory",
                "START-backed attempt directory is missing or unreadable",
                directory=directory,
                detail=str(exc),
            )
            continue
        if (
            count != 1
            or attempt_dir.is_symlink()
            or not attempt_dir.is_dir()
            or resolved_attempt_dir.parent != formal_root
            or resolved_attempt_dir != attempt_dir
        ):
            audit.error(
                "attempt_ledger_directory",
                "START does not uniquely bind one regular direct-child directory",
                directory=directory,
                registrations=count,
            )
    for sibling in formal_root.iterdir():
        if not sibling.is_dir():
            continue
        registrations = registered_directory_counts.get(sibling.name, 0)
        if sibling.is_symlink() or registrations != 1:
            audit.error(
                "attempt_ledger",
                "formal root contains a directory not uniquely backed by one START",
                directory=sibling.name,
                registrations=registrations,
            )

    state["entries"] = entries
    state["attempts"] = attempts
    return state


def analyze_bundle(
    bundle_dir: Path,
    enforce_prior_attempts: bool = True,
    preterminal: bool = False,
) -> Dict[str, Any]:
    """Audit and adjudicate one retained Gate 7 evidence bundle."""

    bundle = Path(bundle_dir).resolve()
    audit = _Audit()
    manifest_path = bundle / "manifest.json"
    if not bundle.is_dir():
        audit.error("bundle_missing", "bundle directory does not exist", path=str(bundle))
        manifest: Dict[str, Any] = {}
    elif not manifest_path.is_file():
        audit.error("manifest_missing", "bundle has no manifest.json", path=str(manifest_path))
        manifest = {}
    else:
        manifest = _load_json(manifest_path, audit, "manifest_read") or {}

    auditor_identity = _auditor_identity()
    if manifest:
        if manifest.get("schema_version") != SCHEMA_VERSION:
            audit.error("manifest_schema", "manifest has the wrong schema version", observed=manifest.get("schema_version"))
        if manifest.get("kind") != "prospective_gate7_followup":
            audit.error("manifest_kind", "manifest is not the prospective Gate 7 follow-up")
        if manifest.get("experiment_id") != EXPERIMENT_ID:
            audit.error(
                "manifest_experiment",
                "manifest has the wrong prospective experiment identity",
                observed=manifest.get("experiment_id"),
            )
        if _is_full_mode(manifest):
            if manifest.get("formal_claimable_mode") is not True:
                audit.error(
                    "formal_claimable_mode",
                    "full-mode evidence cannot opt out of the formal integrity contract",
                )
            source_identities = manifest.get("source_identities")
            producer_auditor = (
                source_identities.get(auditor_identity["path"])
                if isinstance(source_identities, dict)
                else None
            )
            if not _auditor_identity_matches_source(
                auditor_identity, producer_auditor
            ):
                audit.error(
                    "auditor_identity_mismatch",
                    "executing auditor differs from the verifier frozen by the producer",
                    producer=producer_auditor,
                    executing=auditor_identity,
                )
        elif preterminal:
            audit.error(
                "preterminal_mode",
                "preterminal adjudication is reserved for formal bundles",
            )
    artifacts = _verify_artifacts(bundle, manifest, audit) if manifest else {}
    retained_prepared_dir = (
        _validate_retained_inputs(bundle, manifest, artifacts, audit)
        if manifest
        else None
    )
    retained_traces = (
        _load_retained_traces(bundle, manifest, artifacts, audit)
        if manifest
        else {}
    )
    if manifest:
        _validate_formal_source_anchor(
            bundle, manifest, artifacts, audit, preterminal
        )
        _validate_power_observations(manifest, audit)
    if manifest:
        _validate_trace_selection_from_source(
            manifest, retained_traces, retained_prepared_dir, audit
        )

    runs_path = bundle / str(artifacts.get("runs.csv", {}).get("path", "runs.csv"))
    requests_path = bundle / str(artifacts.get("requests.jsonl", {}).get("path", "requests.jsonl"))
    resources_path = bundle / str(artifacts.get("resources.jsonl", {}).get("path", "resources.jsonl"))
    outcomes_path = bundle / str(
        artifacts.get("outcome-latency.jsonl", {}).get(
            "path", "outcome-latency.jsonl"
        )
    )
    run_list = _load_csv(runs_path, audit) if runs_path.is_file() else []
    request_list = _load_jsonl(requests_path, audit, "requests_jsonl") if requests_path.is_file() else []
    resource_list = _load_jsonl(resources_path, audit, "resources_jsonl") if resources_path.is_file() else []
    outcome_list = (
        _load_jsonl(outcomes_path, audit, "outcome_latency_jsonl")
        if outcomes_path.is_file()
        else []
    )

    run_rows: Dict[str, Dict[str, str]] = {}
    for row_number, row in enumerate(run_list, start=1):
        run_id = row.get("run_id")
        if not run_id:
            audit.error("run_id", "runs.csv row lacks a run ID", row=row_number)
        elif run_id in run_rows:
            audit.error("run_id", "runs.csv contains duplicate run IDs", run_id=run_id)
        else:
            run_rows[run_id] = row

    dependency_attestation = None
    auditor_bootstrap_attestation = None
    if manifest:
        dependency_attestation = _validate_dependency_attestation(
            bundle, manifest, artifacts, run_rows, audit
        )
        auditor_bootstrap_attestation = _validate_entrypoint_and_bootstraps(
            manifest,
            run_rows,
            dependency_attestation,
            audit,
            preterminal,
        )
        _validate_warmup_artifacts(
            bundle,
            manifest,
            artifacts,
            retained_traces,
            run_rows,
            audit,
        )

    attempt_id = _validate_attempt_identity(
        manifest, run_rows, request_list, resource_list, outcome_list, audit
    )
    if manifest.get("formal_claimable_mode") is True:
        attempt_eligibility, ledger_state = _validate_prior_attempt_eligibility(
            bundle,
            manifest,
            audit,
            enforce_prior_attempts,
            preterminal,
        )
    else:
        attempt_eligibility = {
            "declared_prior_attempts": 0,
            "valid_complete_predecessors": [],
            "eligible": True,
        }
        ledger_state = {
            "current_terminal_expectation": None,
            "entries": [],
            "attempts": [],
        }

    manifest_config = manifest.get("config")
    expected_run_mode = (
        manifest_config.get("mode")
        if isinstance(manifest_config, Mapping)
        else None
    )
    by_seed = _validate_run_structure(
        run_rows,
        audit,
        expected_run_mode,
        manifest.get("formal_claimable_mode") is True,
    )
    request_results, grouped_requests = _recompute_requests(request_list, run_rows, audit)
    semantic_indexes = _load_semantic_indexes(
        bundle,
        manifest,
        artifacts,
        retained_traces,
        retained_prepared_dir,
        audit,
    ) if manifest else {}
    semantic_results = _recompute_semantic_guardrail(
        grouped_requests,
        run_rows,
        retained_traces,
        semantic_indexes,
        audit,
    )
    if manifest:
        aggregate_semantic = semantic_results.get("aggregate_counts", {})
        semantic_hit_fields = (
            "same_concept_hits",
            "direct_negative_hits",
            "component_derived_negative_hits",
            "unlabeled_cross_concept_hits",
            "unresolved_provenance_hits",
        )
        expected_semantic_guardrail = {
            "status": semantic_results.get("semantic_guardrail_status"),
            "rule": (
                "fail on any direct or component-derived labeled-negative hit; "
                "pending on unlabeled cross-component hits; otherwise pass"
            ),
            "disjoint_hit_counts": {
                field: int(aggregate_semantic.get(field, 0))
                for field in semantic_hit_fields
            },
            "labeled_negative_hits": int(
                aggregate_semantic.get("direct_negative_hits", 0)
            )
            + int(
                aggregate_semantic.get("component_derived_negative_hits", 0)
            ),
            "does_not_change_gate7_system_adjudication": True,
        }
        if manifest.get("semantic_guardrail") != expected_semantic_guardrail:
            audit.error(
                "semantic_guardrail_manifest",
                "manifest semantic guardrail differs from independent request recomputation",
                observed=manifest.get("semantic_guardrail"),
                expected=expected_semantic_guardrail,
            )
    raw_config = manifest.get("config") if isinstance(manifest, dict) else None
    raw_interval = (
        raw_config.get("resource_interval_ms")
        if isinstance(raw_config, dict)
        else None
    )
    resource_interval_ms = _int_value(
        raw_interval,
        audit,
        "resource_interval",
        "resource_interval_ms",
    )
    if resource_interval_ms is None or resource_interval_ms <= 0:
        resource_interval_ms = 100
    resource_results = _recompute_resources(
        resource_list, run_rows, audit, resource_interval_ms
    )
    outcome_results = _recompute_outcome_latency(
        outcome_list, grouped_requests, run_rows, audit
    )
    _validate_pairing_and_trace(
        manifest,
        by_seed,
        run_rows,
        grouped_requests,
        retained_traces,
        audit,
    )
    pending_reasons = _formal_protocol_checks(manifest, artifacts, audit) if manifest else []
    seed_results = _seed_adjudications(by_seed, request_results, resource_results, audit)

    complete_blocks = all(
        seed in by_seed and set(by_seed[seed]) == set(POLICIES)
        for seed in FULL_SEEDS
    ) and set(by_seed) == set(FULL_SEEDS)
    if not complete_blocks:
        pending_reasons.append("fewer than five complete frozen three-policy seed blocks are present")
    if set(int(seed) for seed in seed_results) != set(FULL_SEEDS):
        pending_reasons.append("fewer than five CARMA/LRU seed pairs are reproducibly adjudicable")

    if audit.errors:
        status = "invalid"
        rationale = "retained evidence failed integrity or structural validation"
    elif pending_reasons:
        status = "pending"
        rationale = "valid evidence is developmental or incomplete for the frozen claim"
    elif all(result["passes"] for result in seed_results.values()):
        status = "pass"
        rationale = "all five structurally valid seed pairs satisfy all five frozen bounds"
    else:
        status = "fail"
        rationale = "at least one structurally valid seed pair violates a frozen numerical bound"

    terminal_expectation = ledger_state.get("current_terminal_expectation")
    if (
        manifest.get("formal_claimable_mode") is True
        and not preterminal
        and isinstance(terminal_expectation, dict)
        and (
            terminal_expectation.get("status") != status
            or terminal_expectation.get("claimable")
            is not (status in ("pass", "fail"))
        )
    ):
        audit.error(
            "attempt_ledger_preterminal",
            "fresh terminal audit disagrees with the bound preterminal decision",
            bound_status=terminal_expectation.get("status"),
            recomputed_status=status,
        )
        status = "invalid"
        rationale = "retained evidence disagrees with its TERMINAL-bound preterminal adjudication"

    semantic_guardrail_status = semantic_results["semantic_guardrail_status"]
    combined_disclosure = {
        "system_gate_status": status.upper(),
        "semantic_guardrail_status": semantic_guardrail_status,
        "system_gate_basis": "five frozen latency, throughput, and RSS bounds",
        "semantic_guardrail_basis": (
            "independently recomputed QQP same/direct/component/unlabeled hit relations"
        ),
        "interpretation": (
            "System Gate 7 and semantic quality are orthogonal; neither status "
            "overwrites the other."
        ),
    }

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_phase": "preterminal" if preterminal else "terminal",
        "bundle": str(bundle),
        "auditor_identity": auditor_identity,
        "bootstrap_attestation": (
            dict(auditor_bootstrap_attestation)
            if isinstance(auditor_bootstrap_attestation, Mapping)
            else None
        ),
        "bootstrap_attestation_sha256": (
            auditor_bootstrap_attestation.get("attestation_sha256")
            if isinstance(auditor_bootstrap_attestation, Mapping)
            else None
        ),
        "attempt_id": attempt_id,
        "attempt_eligibility": attempt_eligibility,
        "status": status,
        "claimable": status in ("pass", "fail"),
        "semantic_guardrail_status": semantic_guardrail_status,
        "combined_disclosure": combined_disclosure,
        "rationale": rationale,
        "error_count": len(audit.errors),
        "warning_count": len(audit.warnings),
        "errors": audit.errors,
        "warnings": audit.warnings,
        "pending_reasons": list(dict.fromkeys(pending_reasons)),
        "artifact_recomputation": artifacts,
        "run_recomputation": request_results,
        "resource_recomputation": resource_results,
        "outcome_latency_recomputation": outcome_results,
        "semantic_index_recomputation": {
            str(seed): {
                "path": value.get("path"),
                "sha256": value.get("sha256"),
                "trace_sha256": value.get("trace_sha256"),
                "source_pairs_sha256": value.get("source_pairs_sha256"),
                "mapped_texts": len(value.get("text_id_to_concept_id", {})),
                "direct_negative_pairs": len(value.get("direct_negative_pairs", ())),
                "component_negative_pairs": len(
                    value.get("component_negative_pairs", ())
                ),
            }
            for seed, value in sorted(semantic_indexes.items())
        },
        "semantic_recomputation": semantic_results,
        "seed_adjudication": seed_results,
        "frozen_bounds": dict(BOUNDS),
        "completeness": {
            "expected_seeds": list(FULL_SEEDS),
            "expected_policies": list(POLICIES),
            "observed_seed_policies": {
                str(seed): sorted(policies) for seed, policies in sorted(by_seed.items())
            },
            "complete_five_seed_blocks": complete_blocks,
        },
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    """Create a report once; a later invocation must never replace its bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(
            str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444
        )
    except FileExistsError as exc:
        raise FileExistsError(
            "immutable adjudication already exists: %s" % path
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(path, 0o444)
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _formal_output_must_be_immutable(bundle: Path) -> bool:
    """Recognize formal bundles without trusting adjudication success."""

    resolved_bundle = Path(bundle).resolve()
    project_root = Path(__file__).resolve().parents[2]
    formal_root = (project_root / FORMAL_ATTEMPT_ROOT).resolve()
    if resolved_bundle.parent == formal_root:
        return True
    manifest_path = resolved_bundle / "manifest.json"
    try:
        manifest = _json_loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, Mapping):
        return False
    config = manifest.get("config")
    return bool(
        (isinstance(config, Mapping) and config.get("mode") == "full")
        or manifest.get("formal_claimable_mode") is True
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a Gate 7 ONNX evidence bundle.")
    parser.add_argument("bundle", type=Path, help="bundle directory containing manifest.json")
    parser.add_argument(
        "--preterminal",
        action="store_true",
        help=(
            "audit a completed formal manifest before TERMINAL registration and "
            "create its immutable gate7-preterminal-adjudication.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output path (default: BUNDLE/gate7-adjudication.json)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    immutable_formal_output = _formal_output_must_be_immutable(args.bundle)
    report = analyze_bundle(args.bundle, preterminal=args.preterminal)
    if args.preterminal:
        expected = (args.bundle / PRETERMINAL_REPORT_NAME).resolve()
        output = Path(args.output).resolve() if args.output else expected
        if output != expected:
            raise SystemExit(
                "--preterminal output must be %s" % expected
            )
        _write_immutable_json(output, report)
    else:
        output = args.output or (args.bundle / "gate7-adjudication.json")
        if immutable_formal_output:
            _write_immutable_json(Path(output), report)
        else:
            _atomic_write_json(Path(output), report)
    print("Gate 7 ONNX follow-up: %s" % report["status"].upper())
    print("Adjudication: %s" % Path(output).resolve())
    return {"pass": 0, "fail": 1, "pending": 2, "invalid": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "BOUNDS",
    "FULL_SEEDS",
    "adjudicate_seed_pair",
    "analyze_bundle",
    "nearest_rank",
]
