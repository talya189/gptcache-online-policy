"""Real SQLite/FAISS benchmark for GPTCache eviction policies.

The deterministic synthetic harness in :mod:`benchmarks.carma.runner` is useful
for algorithm experiments, but it deliberately replaces GPTCache storage with
Python dictionaries.  This benchmark complements it by replaying the same kind
of precomputed embeddings through ``SSDataManager``, SQLite, FAISS, and the
actual LRU, LFU, or CARMA eviction object.

The public invocation is an orchestrator.  Every policy is executed in a fresh
child process and a fresh temporary data directory, so allocator state, SQLite
files, FAISS indexes, and in-memory policy state cannot leak between policies.
"""

import argparse
import csv
import functools
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# Direct execution (``python benchmarks/carma/integration_benchmark.py``) puts
# this file's directory, rather than the repository root, on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import psutil

from benchmarks.carma.synthetic import Catalog, Request, build_trace, trace_hash
from gptcache.manager.factory import manager_factory


SCHEMA_VERSION = "carma-sqlite-faiss-v1"
POLICIES = ("LRU", "LFU", "CARMA")
MODES = {
    "smoke": {"requests": 300, "capacity": 40, "sample_every": 10},
    "full": {"requests": 3000, "capacity": 100, "sample_every": 25},
}
STAGES = ("total", "search", "policy", "storage", "unaccounted")


@dataclass(frozen=True)
class IntegrationConfig:
    """Configuration shared verbatim by every policy child process."""

    mode: str
    workload: str
    seed: int
    requests: int
    capacity: int
    hit_threshold: float
    sample_every: int
    topic_threshold: float
    cell_threshold: float
    demand_half_life: float
    quota_strength: float
    ghost_support_threshold: float
    admission_margin: float
    centroid_alpha: float
    entry_hit_weight: float

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError("unknown mode %s" % self.mode)
        if self.workload not in ("stationary", "phase_shift", "pollution_scan", "novel"):
            raise ValueError("unknown workload %s" % self.workload)
        if self.requests <= 0:
            raise ValueError("requests must be positive")
        if self.capacity < 2:
            raise ValueError("capacity must be at least 2")
        if not 0.0 <= self.hit_threshold <= 1.0:
            raise ValueError("hit-threshold must be in [0, 1]")
        if self.sample_every <= 0:
            raise ValueError("sample-every must be positive")


class StageRecorder:
    """Accumulate exclusive per-request storage and policy call time.

    CARMA's insertion callback may synchronously delete SQLite rows and FAISS
    vectors.  Policy wrappers therefore subtract storage calls nested inside
    the policy call; both categories remain useful without double-counting.
    """

    def __init__(self) -> None:
        self.active = False
        self.current = {"policy": 0, "storage": 0}

    def begin(self) -> None:
        self.current = {"policy": 0, "storage": 0}
        self.active = True

    def finish(self) -> Dict[str, int]:
        self.active = False
        return dict(self.current)

    def wrap(self, obj: Any, method_name: str, category: str) -> None:
        original = getattr(obj, method_name, None)
        if not callable(original):
            return

        @functools.wraps(original)
        def measured(*args: Any, **kwargs: Any) -> Any:
            if not self.active:
                return original(*args, **kwargs)
            storage_before = self.current["storage"]
            started = time.perf_counter_ns()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = time.perf_counter_ns() - started
                if category == "policy":
                    nested_storage = self.current["storage"] - storage_before
                    self.current["policy"] += max(0, elapsed - nested_storage)
                else:
                    self.current["storage"] += elapsed

        setattr(obj, method_name, measured)


def _instrument_manager(manager: Any, recorder: StageRecorder) -> None:
    for method_name in (
        "batch_insert",
        "get_data_by_id",
        "mark_deleted",
        "clear_deleted_data",
        "get_ids",
        "count",
        "flush",
    ):
        recorder.wrap(manager.s, method_name, "storage")
    for method_name in ("mul_add", "delete", "rebuild", "flush"):
        recorder.wrap(manager.v, method_name, "storage")
    # DataManager always calls put_with_metadata after an insertion.  Wrapping
    # only that method avoids double-counting the base implementation's put().
    recorder.wrap(manager.eviction_base, "put_with_metadata", "policy")
    recorder.wrap(manager.eviction_base, "get", "policy")


def _io_dict(process: psutil.Process) -> Dict[str, int]:
    try:
        counters = process.io_counters()
    except (AttributeError, NotImplementedError, psutil.Error):
        return {
            "io_counters_available": False,
            "read_count": 0,
            "write_count": 0,
            "read_bytes": 0,
            "write_bytes": 0,
        }
    return {
        "io_counters_available": True,
        "read_count": int(getattr(counters, "read_count", 0)),
        "write_count": int(getattr(counters, "write_count", 0)),
        "read_bytes": int(getattr(counters, "read_bytes", 0)),
        "write_bytes": int(getattr(counters, "write_bytes", 0)),
    }


def _resource_snapshot(
    process: psutil.Process,
    run_id: str,
    policy: str,
    kind: str,
    request_index: int,
    start_ns: int,
) -> Dict[str, Any]:
    memory = process.memory_info()
    cpu = process.cpu_times()
    try:
        uss = int(process.memory_full_info().uss)
    except (AttributeError, psutil.Error):
        uss = 0
    record: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "policy": policy,
        "kind": kind,
        "request_index": request_index,
        "elapsed_seconds": round((time.perf_counter_ns() - start_ns) / 1e9, 9),
        "rss_bytes": int(memory.rss),
        "vms_bytes": int(memory.vms),
        "uss_bytes": uss,
        "cpu_user_seconds": float(cpu.user),
        "cpu_system_seconds": float(cpu.system),
        "num_threads": int(process.num_threads()),
    }
    record.update(_io_dict(process))
    return record


def _delta(after: Dict[str, Any], before: Dict[str, Any], key: str) -> Any:
    return after[key] - before[key]


def _catalog_for(config: IntegrationConfig) -> Catalog:
    # pollution_scan needs requests/3 unique concepts outside topic zero.  Size
    # the deterministic catalog before timing while keeping vectors compact.
    topics = 6
    cells = 4
    concepts = 32
    if config.workload == "pollution_scan":
        scan_count = config.requests // 3
        concepts = max(concepts, int(math.ceil(scan_count / ((topics - 1) * cells))) + 2)
    elif config.workload in ("stationary", "novel"):
        concepts = max(concepts, int(math.ceil(config.requests / (topics * cells))) + 2)
    return Catalog(topics=topics, cells_per_topic=cells, concepts_per_cell=concepts)


def _precomputed_trace(config: IntegrationConfig) -> Tuple[Catalog, List[Request], str]:
    catalog = _catalog_for(config)
    requests = build_trace(
        config.workload,
        catalog,
        config.requests,
        config.seed,
        config.capacity,
    )
    return catalog, requests, trace_hash(requests)


def _run_id(policy: str, config: IntegrationConfig, workload_hash: str) -> str:
    identity = {
        "policy": policy,
        "trace_hash": workload_hash,
        "config": asdict(config),
        "schema_version": SCHEMA_VERSION,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _policy_params(config: IntegrationConfig) -> Dict[str, Any]:
    return {
        "topic_threshold": config.topic_threshold,
        "cell_threshold": config.cell_threshold,
        "demand_half_life": config.demand_half_life,
        "quota_strength": config.quota_strength,
        "ghost_support_threshold": config.ghost_support_threshold,
        "admission_margin": config.admission_margin,
        "centroid_alpha": config.centroid_alpha,
        "entry_hit_weight": config.entry_hit_weight,
        "seed": config.seed,
    }


def _build_manager(policy: str, root: str, dimension: int, config: IntegrationConfig) -> Any:
    eviction_params: Dict[str, Any] = {
        "eviction": policy,
        "max_size": config.capacity,
        "clean_size": 1,
    }
    if policy == "CARMA":
        eviction_params["policy_params"] = _policy_params(config)
    manager = manager_factory(
        "sqlite,faiss",
        data_dir=root,
        vector_params={"dimension": dimension, "top_k": 1},
        eviction_params=eviction_params,
    )
    # Upstream LRU/LFU intentionally defer physical deletion until enough rows
    # are soft-marked.  top_k=1 can then surface a tombstone instead of a valid
    # neighbor.  This benchmark forces the existing cleanup callback to delete
    # each victim immediately, giving all policies the same exact capacity and
    # measuring that SQLite/FAISS cleanup inside request latency.
    manager.eviction_manager.MAX_MARK_COUNT = 0
    manager.eviction_manager.MAX_MARK_RATE = 0.0
    return manager


def _percentile_ns(values: Sequence[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, int(math.ceil(quantile * len(ordered))) - 1)
    return int(ordered[index])


def _latency_summary(values: Sequence[int], name: str) -> Dict[str, float]:
    count = max(1, len(values))
    return {
        "%s_mean_us" % name: round(sum(values) / count / 1000.0, 6),
        "%s_p50_us" % name: round(_percentile_ns(values, 0.50) / 1000.0, 6),
        "%s_p95_us" % name: round(_percentile_ns(values, 0.95) / 1000.0, 6),
        "%s_p99_us" % name: round(_percentile_ns(values, 0.99) / 1000.0, 6),
    }


def _answer_text(cache_data: Any) -> Optional[str]:
    if cache_data is None or not cache_data.answers:
        return None
    return str(cache_data.answers[0].answer)


def _verify_storage(manager: Any, capacity: int) -> Dict[str, int]:
    active_ids = manager.s.get_ids(deleted=False)
    deleted_ids = manager.s.get_ids(deleted=True)
    scalar_count = int(manager.s.count())
    all_scalar_count = int(manager.s.count(is_all=True))
    vector_count = int(manager.v.count())
    if deleted_ids:
        raise RuntimeError("soft-deleted scalar rows remain: %s" % deleted_ids[:5])
    if scalar_count != len(active_ids) or all_scalar_count != scalar_count:
        raise RuntimeError("SQLite active/total counts diverged")
    if scalar_count != vector_count:
        raise RuntimeError("SQLite/FAISS counts diverged: %d != %d" % (scalar_count, vector_count))
    if scalar_count > capacity:
        raise RuntimeError("storage capacity exceeded")

    verified = 0
    for cache_id in active_ids:
        cache_data = manager.s.get_data_by_id(cache_id)
        if cache_data is None:
            raise RuntimeError("active scalar ID %r cannot be read" % cache_id)
        result = manager.search(cache_data.embedding_data, top_k=1)
        if not result or int(result[0][1]) != int(cache_id):
            raise RuntimeError("FAISS cannot round-trip active ID %r" % cache_id)
        verified += 1
    return {
        "final_scalar_count": scalar_count,
        "final_vector_count": vector_count,
        "deleted_scalar_count": len(deleted_ids),
        "verified_entries": verified,
    }


def _execute_policy(policy: str, config: IntegrationConfig) -> Dict[str, Any]:
    catalog, requests, workload_hash = _precomputed_trace(config)
    dimension = int(requests[0].embedding.shape[0])
    run_id = _run_id(policy, config, workload_hash)
    process = psutil.Process(os.getpid())
    resource_records: List[Dict[str, Any]] = []
    latency: Dict[str, List[int]] = {stage: [] for stage in STAGES}
    seen: set = set()
    hits = 0
    valid_hits = 0
    false_hits = 0
    misses = 0
    opportunities = 0
    valid_tokens = 0
    total_tokens = 0
    stale_candidates = 0

    with tempfile.TemporaryDirectory(prefix="gptcache-%s-" % policy.lower()) as root:
        manager = _build_manager(policy, root, dimension, config)
        recorder = StageRecorder()
        _instrument_manager(manager, recorder)
        lifecycle_start_ns = time.perf_counter_ns()
        start_resource = _resource_snapshot(
            process, run_id, policy, "loop_start", -1, lifecycle_start_ns
        )
        resource_records.append(start_resource)
        rss_peak = int(start_resource["rss_bytes"])
        loop_start_ns = time.perf_counter_ns()

        for index, request in enumerate(requests):
            opportunity = request.concept_id in seen
            opportunities += int(opportunity)
            total_tokens += int(request.token_cost)
            recorder.begin()
            request_start = time.perf_counter_ns()

            search_start = time.perf_counter_ns()
            search_results = manager.search(request.embedding, top_k=1) or []
            search_ns = time.perf_counter_ns() - search_start

            cache_data = None
            similarity = -1.0
            if search_results and int(search_results[0][1]) >= 0:
                cache_data = manager.get_scalar_data(search_results[0])
                if cache_data is None:
                    stale_candidates += 1
                distance = float(search_results[0][0])
                similarity = max(-1.0, min(1.0, 1.0 - 0.5 * distance))

            raw_hit = bool(
                cache_data is not None
                and similarity + 1e-12 >= config.hit_threshold
            )
            if raw_hit:
                manager.hit_cache_callback(search_results[0])
                hits += 1
                answer = _answer_text(cache_data)
                if answer == request.concept_id:
                    valid_hits += 1
                    valid_tokens += int(request.token_cost)
                else:
                    false_hits += 1
            else:
                misses += 1
                manager.save(
                    request.concept_id,
                    request.concept_id,
                    request.embedding,
                )

            total_ns = time.perf_counter_ns() - request_start
            measured = recorder.finish()
            policy_ns = int(measured["policy"])
            storage_ns = int(measured["storage"])
            unaccounted_ns = max(0, total_ns - search_ns - policy_ns - storage_ns)
            latency["total"].append(total_ns)
            latency["search"].append(search_ns)
            latency["policy"].append(policy_ns)
            latency["storage"].append(storage_ns)
            latency["unaccounted"].append(unaccounted_ns)
            seen.add(request.concept_id)

            if (index + 1) % config.sample_every == 0 or index + 1 == len(requests):
                sample = _resource_snapshot(
                    process,
                    run_id,
                    policy,
                    "sample",
                    index,
                    lifecycle_start_ns,
                )
                rss_peak = max(rss_peak, int(sample["rss_bytes"]))
                resource_records.append(sample)

        loop_wall_ns = time.perf_counter_ns() - loop_start_ns
        loop_end_resource = _resource_snapshot(
            process,
            run_id,
            policy,
            "loop_end",
            len(requests) - 1,
            lifecycle_start_ns,
        )
        resource_records.append(loop_end_resource)
        rss_peak = max(rss_peak, int(loop_end_resource["rss_bytes"]))

        # Verification and close are deliberately outside request latency.
        verification = _verify_storage(manager, config.capacity)
        policy_stats = (
            dict(manager.eviction_base.stats())
            if callable(getattr(manager.eviction_base, "stats", None))
            else {}
        )
        manager.close()
        lifecycle_end_resource = _resource_snapshot(
            process,
            run_id,
            policy,
            "lifecycle_end",
            len(requests) - 1,
            lifecycle_start_ns,
        )
        resource_records.append(lifecycle_end_resource)
        rss_peak = max(rss_peak, int(lifecycle_end_resource["rss_bytes"]))

    if stale_candidates:
        raise RuntimeError("%s observed %d stale FAISS candidates" % (policy, stale_candidates))
    if false_hits:
        raise RuntimeError("%s returned %d wrong semantic answers" % (policy, false_hits))

    cpu_user = float(_delta(loop_end_resource, start_resource, "cpu_user_seconds"))
    cpu_system = float(_delta(loop_end_resource, start_resource, "cpu_system_seconds"))
    loop_seconds = loop_wall_ns / 1e9
    summary: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "policy": policy,
        "mode": config.mode,
        "workload": config.workload,
        "seed": config.seed,
        "trace_hash": workload_hash,
        "requests": len(requests),
        "capacity": config.capacity,
        "embedding_dimension": dimension,
        "top_k": 1,
        "hit_threshold": config.hit_threshold,
        "hits": hits,
        "misses": misses,
        "valid_hits": valid_hits,
        "false_hits": false_hits,
        "stale_candidates": stale_candidates,
        "reuse_opportunities": opportunities,
        "hit_rate": round(hits / len(requests), 8),
        "opportunity_recall": round(valid_hits / opportunities, 8) if opportunities else 0.0,
        "safe_token_saving_ratio": round(valid_tokens / total_tokens, 8) if total_tokens else 0.0,
        "admissions": int(policy_stats.get("admissions", misses)),
        "rejections": int(policy_stats.get("rejections", 0)),
        "evictions": int(
            policy_stats.get(
                "evictions",
                max(0, misses - int(verification["final_scalar_count"])),
            )
        ),
        "policy_topics": int(policy_stats.get("topics", 0)),
        "policy_cells": int(policy_stats.get("cells", 0)),
        "policy_ghost_cells": int(policy_stats.get("ghost_cells", 0)),
        "wall_seconds": round(loop_seconds, 9),
        "throughput_qps": round(len(requests) / loop_seconds, 6),
        "cpu_user_seconds": round(cpu_user, 9),
        "cpu_system_seconds": round(cpu_system, 9),
        "cpu_percent_one_core": round(100.0 * (cpu_user + cpu_system) / loop_seconds, 6),
        "rss_start_bytes": int(start_resource["rss_bytes"]),
        "rss_end_bytes": int(loop_end_resource["rss_bytes"]),
        "rss_peak_sampled_bytes": rss_peak,
        "rss_delta_bytes": int(_delta(loop_end_resource, start_resource, "rss_bytes")),
        "io_read_bytes": int(_delta(loop_end_resource, start_resource, "read_bytes")),
        "io_write_bytes": int(_delta(loop_end_resource, start_resource, "write_bytes")),
        "io_read_count": int(_delta(loop_end_resource, start_resource, "read_count")),
        "io_write_count": int(_delta(loop_end_resource, start_resource, "write_count")),
        "io_counters_available": bool(
            start_resource["io_counters_available"]
            and loop_end_resource["io_counters_available"]
        ),
    }
    summary.update(verification)
    for stage in STAGES:
        summary.update(_latency_summary(latency[stage], stage))
    return {
        "summary": summary,
        "resources": resource_records,
        "verification": verification,
        "catalog": {
            "topics": catalog.topics,
            "cells_per_topic": catalog.cells_per_topic,
            "concepts_per_cell": catalog.concepts_per_cell,
            "embedding_dimension": dimension,
        },
    }


RUN_FIELDS = (
    "schema_version", "run_id", "policy", "mode", "workload", "seed",
    "trace_hash", "requests", "capacity", "embedding_dimension", "top_k",
    "hit_threshold", "hits", "misses", "valid_hits", "false_hits",
    "stale_candidates", "reuse_opportunities", "hit_rate",
    "opportunity_recall", "safe_token_saving_ratio", "admissions",
    "rejections", "evictions", "final_scalar_count", "final_vector_count",
    "deleted_scalar_count", "verified_entries", "wall_seconds",
    "throughput_qps", "cpu_user_seconds", "cpu_system_seconds",
    "cpu_percent_one_core", "rss_start_bytes", "rss_end_bytes",
    "rss_peak_sampled_bytes", "rss_delta_bytes", "io_read_bytes",
    "io_write_bytes", "io_read_count", "io_write_count",
    "io_counters_available", "policy_topics", "policy_cells",
    "policy_ghost_cells",
) + tuple(
    "%s_%s_us" % (stage, statistic)
    for stage in STAGES
    for statistic in ("mean", "p50", "p95", "p99")
)


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


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    def write(output: Any) -> None:
        csv_writer = csv.DictWriter(output, fieldnames=RUN_FIELDS)
        csv_writer.writeheader()
        for row in rows:
            csv_writer.writerow({field: row[field] for field in RUN_FIELDS})

    _atomic_write(path, write, newline="")


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    def write(output: Any) -> None:
        for record in records:
            output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    _atomic_write(path, write)


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    def write(output: Any) -> None:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write("\n")

    _atomic_write(path, write)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_state() -> Dict[str, Any]:
    def command(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=str(PROJECT_ROOT), text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        head = command("rev-parse", "HEAD")
        status = command("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError):
        return {"head_commit": None, "worktree_dirty": None}
    return {"head_commit": head, "worktree_dirty": bool(status)}


def _environment() -> Dict[str, Any]:
    import cachetools
    import faiss
    import sqlalchemy

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "numpy": np.__version__,
        "faiss": getattr(faiss, "__version__", "unknown"),
        "sqlalchemy": sqlalchemy.__version__,
        "cachetools": cachetools.__version__,
        "psutil": psutil.__version__,
    }


def _overhead_vs_lru(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    by_policy = {row["policy"]: row for row in rows}
    baseline = by_policy.get("LRU")
    if baseline is None:
        return {}
    result: Dict[str, Dict[str, float]] = {}
    for policy, row in by_policy.items():
        if policy == "LRU":
            continue
        metrics: Dict[str, float] = {}
        for field in ("total_p50_us", "total_p95_us", "policy_p95_us"):
            base = float(baseline[field])
            metrics["%s_percent" % field] = (
                round(100.0 * (float(row[field]) / base - 1.0), 6) if base else 0.0
            )
        base_qps = float(baseline["throughput_qps"])
        metrics["throughput_qps_percent"] = (
            round(100.0 * (float(row["throughput_qps"]) / base_qps - 1.0), 6)
            if base_qps
            else 0.0
        )
        result[policy] = metrics
    return result


def _config_args(config: IntegrationConfig) -> List[str]:
    return [
        "--mode", config.mode,
        "--workload", config.workload,
        "--seed", str(config.seed),
        "--requests", str(config.requests),
        "--capacity", str(config.capacity),
        "--hit-threshold", str(config.hit_threshold),
        "--sample-every", str(config.sample_every),
        "--topic-threshold", str(config.topic_threshold),
        "--cell-threshold", str(config.cell_threshold),
        "--demand-half-life", str(config.demand_half_life),
        "--quota-strength", str(config.quota_strength),
        "--ghost-support-threshold", str(config.ghost_support_threshold),
        "--admission-margin", str(config.admission_margin),
        "--centroid-alpha", str(config.centroid_alpha),
        "--entry-hit-weight", str(config.entry_hit_weight),
    ]


def _orchestrate(
    config: IntegrationConfig,
    policies: Sequence[str],
    output_dir: Path,
) -> List[Dict[str, Any]]:
    config.validate()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    _, expected_requests, expected_trace_hash = _precomputed_trace(config)
    rows: List[Dict[str, Any]] = []
    resources: List[Dict[str, Any]] = []
    catalogs: Dict[str, Dict[str, Any]] = {}

    with tempfile.TemporaryDirectory(prefix="carma-integration-results-") as result_root:
        for policy in policies:
            result_path = Path(result_root) / (policy.lower() + ".json")
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                *_config_args(config),
                "--child-policy", policy,
                "--child-result", str(result_path),
            ]
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                check=False,
                env=dict(os.environ, PYTHONHASHSEED="0"),
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "%s child failed (exit %d)\nstdout:\n%s\nstderr:\n%s"
                    % (policy, completed.returncode, completed.stdout, completed.stderr)
                )
            with result_path.open("r", encoding="utf-8") as source:
                result = json.load(source)
            summary = result["summary"]
            if summary["trace_hash"] != expected_trace_hash:
                raise RuntimeError("%s child used a different trace" % policy)
            if int(summary["requests"]) != len(expected_requests):
                raise RuntimeError("%s child request count differs" % policy)
            rows.append(summary)
            resources.extend(result["resources"])
            catalogs[policy] = result["catalog"]

    if len({json.dumps(value, sort_keys=True) for value in catalogs.values()}) != 1:
        raise RuntimeError("policy children used different catalogs")

    runs_path = output_dir / "runs.csv"
    resources_path = output_dir / "resources.jsonl"
    manifest_path = output_dir / "manifest.json"
    _write_csv(runs_path, rows)
    _write_jsonl(resources_path, resources)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": "c59fb3a6152a4458b2a070ca183b61c4b614095f",
        "git": _git_state(),
        "environment": _environment(),
        "config": asdict(config),
        "policies": list(policies),
        "trace_hash": expected_trace_hash,
        "identical_trace_verified": True,
        "top_k": 1,
        "precomputed_embeddings": True,
        "process_isolation": "one fresh child process and TemporaryDirectory per policy",
        "timing_scope": {
            "total": "per request: search, scalar lookup, policy hit or full save",
            "search": "SSDataManager.search including normalization and FAISS top_k=1",
            "policy": "eviction put/get exclusive of nested measured cleanup storage",
            "storage": "measured SQLite and FAISS read/write/add/delete calls; excludes search",
            "unaccounted": (
                "Python orchestration, normalization, data conversion, and timer overhead"
            ),
            "verification_and_close": (
                "excluded from request latency; lifecycle resource sample retained"
            ),
        },
        "resource_measurement": (
            "RSS, CPU times, thread count, and process I/O counters are sampled "
            "through psutil. io_counters_available is false, with zero I/O deltas, "
            "on platforms where psutil has no per-process I/O API (including macOS)."
        ),
        "storage_cleanup": (
            "EvictionManager thresholds are set to zero in each benchmark manager so "
            "LRU/LFU and CARMA physically delete every victim immediately."
        ),
        "verification": {
            "stale_candidates_required": 0,
            "false_hits_required": 0,
            "scalar_vector_counts_equal": True,
            "every_active_embedding_round_trips_through_faiss": True,
        },
        "catalog": next(iter(catalogs.values())),
        "run_ids": [row["run_id"] for row in rows],
        "overhead_vs_lru": _overhead_vs_lru(rows),
        "overhead_interpretation": (
            "policy_p95_us is the closest policy-only comparison. Total latency and QPS "
            "are end-to-end outcomes and include each policy's different hit, miss, "
            "admission, rejection, and storage-cleanup mix."
        ),
        "artifacts": {
            "runs.csv": {"sha256": _sha256_file(runs_path), "rows": len(rows)},
            "resources.jsonl": {
                "sha256": _sha256_file(resources_path),
                "rows": len(resources),
            },
        },
    }
    _write_json(manifest_path, manifest)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark real GPTCache SQLite+FAISS with LRU, LFU, and CARMA."
    )
    parser.add_argument("--mode", choices=tuple(MODES), default="smoke")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--workload",
        choices=("stationary", "phase_shift", "pollution_scan", "novel"),
        default="pollution_scan",
    )
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--requests", type=int, default=None)
    parser.add_argument("--capacity", type=int, default=None)
    parser.add_argument("--sample-every", type=int, default=None)
    parser.add_argument("--hit-threshold", type=float, default=0.97)
    parser.add_argument("--topic-threshold", type=float, default=0.70)
    parser.add_argument("--cell-threshold", type=float, default=0.88)
    parser.add_argument("--demand-half-life", type=float, default=128.0)
    parser.add_argument("--quota-strength", type=float, default=0.5)
    parser.add_argument("--ghost-support-threshold", type=float, default=1.5)
    parser.add_argument("--admission-margin", type=float, default=1.05)
    parser.add_argument("--centroid-alpha", type=float, default=0.05)
    parser.add_argument("--entry-hit-weight", type=float, default=0.25)
    parser.add_argument("--child-policy", choices=POLICIES, help=argparse.SUPPRESS)
    parser.add_argument("--child-result", type=Path, help=argparse.SUPPRESS)
    return parser


def _config_from_args(args: argparse.Namespace) -> IntegrationConfig:
    defaults = MODES[args.mode]
    return IntegrationConfig(
        mode=args.mode,
        workload=args.workload,
        seed=args.seed,
        requests=args.requests if args.requests is not None else defaults["requests"],
        capacity=args.capacity if args.capacity is not None else defaults["capacity"],
        hit_threshold=args.hit_threshold,
        sample_every=(
            args.sample_every if args.sample_every is not None else defaults["sample_every"]
        ),
        topic_threshold=args.topic_threshold,
        cell_threshold=args.cell_threshold,
        demand_half_life=args.demand_half_life,
        quota_strength=args.quota_strength,
        ghost_support_threshold=args.ghost_support_threshold,
        admission_margin=args.admission_margin,
        centroid_alpha=args.centroid_alpha,
        entry_hit_weight=args.entry_hit_weight,
    )


def _print_summary(rows: Sequence[Dict[str, Any]], output_dir: Path) -> None:
    print("wrote %d isolated SQLite/FAISS runs to %s" % (len(rows), output_dir.resolve()))
    print("policy,hit_rate,p50_total_us,p95_total_us,p95_policy_us,qps,rss_peak_mib")
    for row in rows:
        print(
            "%s,%.6f,%.3f,%.3f,%.3f,%.3f,%.3f"
            % (
                row["policy"],
                row["hit_rate"],
                row["total_p50_us"],
                row["total_p95_us"],
                row["policy_p95_us"],
                row["throughput_qps"],
                row["rss_peak_sampled_bytes"] / (1024.0 * 1024.0),
            )
        )
    overhead = _overhead_vs_lru(rows).get("CARMA")
    if overhead:
        print(
            "CARMA vs LRU policy-only p95 change: %+.2f%%; "
            "end-to-end p50 %+.2f%%, p95 %+.2f%%, QPS %+.2f%% "
            "(end-to-end includes the different hit/miss/admission mix)"
            % (
                overhead["policy_p95_us_percent"],
                overhead["total_p50_us_percent"],
                overhead["total_p95_us_percent"],
                overhead["throughput_qps_percent"],
            )
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = _config_from_args(args)
    config.validate()
    if bool(args.child_policy) != bool(args.child_result):
        raise SystemExit("--child-policy and --child-result must be used together")
    if args.child_policy:
        result = _execute_policy(args.child_policy, config)
        _write_json(args.child_result, result)
        return 0
    output_dir = (
        args.output
        if args.output is not None
        else Path("artifacts/carma-integration-%s" % config.mode)
    )
    rows = _orchestrate(config, tuple(args.policies), output_dir)
    _print_summary(rows, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
