"""Policy adapters, simulation, metrics, and artifact writing for CARMA CI."""

import csv
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from benchmarks.carma.synthetic import Catalog, Request, build_trace, trace_hash
from gptcache.manager.eviction.memory_cache import MemoryCacheEviction


SCHEMA_VERSION = "carma-benchmark-v2"
DEFAULT_WORKLOAD_SIZES = {
    "stationary": 1000,
    "phase_shift": 1500,
    "pollution_scan": 1200,
    "novel": 200,
}
POLICIES = ("LRU", "LFU", "CARMA", "CARMA_NO_CLUSTER")
RECOVERY_WINDOW_FRACTION = 0.10
RECOVERY_TAIL_FRACTION = 0.25
RECOVERY_TARGET_FRACTION = 0.90


@dataclass(frozen=True)
class BenchmarkConfig:
    """Complete deterministic experiment configuration."""

    workloads: Tuple[str, ...] = tuple(DEFAULT_WORKLOAD_SIZES)
    policies: Tuple[str, ...] = POLICIES
    seed: int = 20260825
    capacity: int = 50
    request_count: Optional[int] = None
    hit_threshold: float = 0.97
    cluster_similarity_threshold: float = 0.70
    cell_threshold: float = 0.88
    demand_half_life: Optional[float] = 128.0
    quota_strength: float = 0.5
    ghost_support_threshold: float = 1.5
    admission_margin: float = 1.05
    centroid_alpha: float = 0.05
    entry_hit_weight: float = 0.25
    measure_latency: bool = False
    verify_determinism: bool = True
    topics: int = 6
    cells_per_topic: int = 4
    concepts_per_cell: int = 32

    def validate(self) -> None:
        if self.capacity < 2:
            raise ValueError("capacity must be at least 2")
        if self.request_count is not None and self.request_count <= 0:
            raise ValueError("request_count must be positive")
        if not 0 <= self.hit_threshold <= 1:
            raise ValueError("hit_threshold must be in [0, 1]")
        unknown_workloads = set(self.workloads) - set(DEFAULT_WORKLOAD_SIZES)
        if unknown_workloads:
            raise ValueError("unknown workloads: %s" % sorted(unknown_workloads))
        unknown_policies = set(self.policies) - set(POLICIES)
        if unknown_policies:
            raise ValueError("unknown policies: %s" % sorted(unknown_policies))
        if not self.workloads or not self.policies:
            raise ValueError("at least one workload and policy are required")


@dataclass
class InsertResult:
    admitted: bool
    evicted_ids: List[str]
    reason: str


class PolicyAdapter:
    """Normalizes GPTCache LRU/LFU and CARMA into one benchmark interface."""

    def __init__(
        self,
        name: str,
        config: BenchmarkConfig,
        on_evict: Callable[[List[str]], None],
    ):
        self.name = name
        self.config = config
        self._external_on_evict = on_evict
        self._resident: Set[str] = set()
        self._current_evictions: List[str] = []
        self._to_internal: Dict[str, int] = {}
        self._from_internal: Dict[int, str] = {}

        if name in ("LRU", "LFU"):
            self.impl = MemoryCacheEviction(
                policy=name,
                maxsize=config.capacity,
                clean_size=1,
                on_evict=self._on_evict,
            )
        elif name in ("CARMA", "CARMA_NO_CLUSTER"):
            try:
                from gptcache.manager.eviction.carma import ClusterAdaptiveEviction
            except ImportError as exc:  # pragma: no cover - user-facing failure path
                raise RuntimeError(
                    "CARMA policy is unavailable; expected "
                    "gptcache.manager.eviction.carma.ClusterAdaptiveEviction"
                ) from exc
            self.impl = ClusterAdaptiveEviction(
                maxsize=config.capacity,
                clean_size=1,
                on_evict=self._on_evict,
                topic_threshold=config.cluster_similarity_threshold,
                cell_threshold=config.cell_threshold,
                demand_half_life=config.demand_half_life,
                quota_strength=config.quota_strength,
                ghost_support_threshold=config.ghost_support_threshold,
                admission_margin=config.admission_margin,
                centroid_alpha=config.centroid_alpha,
                entry_hit_weight=config.entry_hit_weight,
                one_topic=name == "CARMA_NO_CLUSTER",
                seed=config.seed,
            )
        else:  # guarded by config validation
            raise ValueError("unsupported policy %s" % name)

    def _on_evict(self, keys: List[Any]) -> None:
        stable_keys = [self._from_internal[int(key)] for key in keys]
        self._current_evictions.extend(stable_keys)
        for key in stable_keys:
            self._resident.discard(key)
        self._external_on_evict(stable_keys)

    def put(self, key: str, embedding: np.ndarray) -> InsertResult:
        self._current_evictions = []
        internal_key = self._internal_key(key)
        if self.name in ("LRU", "LFU"):
            self.impl.put([internal_key])
            self._resident.add(key)
            return InsertResult(True, list(self._current_evictions), "admit")

        outcomes = self.impl.put_with_metadata(
            [internal_key], embeddings=[embedding]
        )
        outcome = outcomes[0] if outcomes else {}
        admitted = bool(outcome.get("admitted", False))
        if admitted:
            self._resident.add(key)
        else:
            self._resident.discard(key)
        return InsertResult(
            admitted=admitted,
            evicted_ids=list(self._current_evictions),
            reason=str(outcome.get("action", "unknown")),
        )

    def get(self, key: str) -> bool:
        return bool(self.impl.get(self._internal_key(key)))

    def is_resident(self, key: str) -> bool:
        return key in self._resident

    def resident_keys(self) -> Tuple[str, ...]:
        return tuple(sorted(self._resident))

    def stats(self) -> Dict[str, Any]:
        if hasattr(self.impl, "stats"):
            return dict(self.impl.stats())
        return {"size": len(self._resident)}

    def _internal_key(self, key: str) -> int:
        """Use integer policy IDs so cachetools LFU ties ignore hash randomization."""

        if key in self._to_internal:
            return self._to_internal[key]
        encoded = hashlib.sha256(key.encode("utf-8")).digest()
        internal = int.from_bytes(encoded[:8], byteorder="big", signed=False)
        existing = self._from_internal.get(internal)
        if existing is not None and existing != key:
            raise RuntimeError("unexpected deterministic policy-key collision")
        self._to_internal[key] = internal
        self._from_internal[internal] = key
        return internal


@dataclass
class RunResult:
    request_records: List[Dict[str, Any]]
    summary: Dict[str, Any]


def phase_valid_hit_summary(
    records: Sequence[Dict[str, Any]], phase: str
) -> Dict[str, Any]:
    """Return exact hit metrics for one named phase of one policy run."""

    selected = [row for row in records if row.get("phase") == phase]
    valid_hits = sum(int(bool(row.get("valid_hit", False))) for row in selected)
    false_hits = sum(int(bool(row.get("false_hit", False))) for row in selected)
    opportunities = sum(
        int(bool(row.get("reuse_opportunity", False))) for row in selected
    )
    return {
        "phase": phase,
        "requests": len(selected),
        "valid_hits": valid_hits,
        "false_hits": false_hits,
        "reuse_opportunities": opportunities,
        "valid_hit_rate": (
            _ratio(valid_hits, len(selected)) if selected else None
        ),
        "false_hit_rate": (
            _ratio(false_hits, len(selected)) if selected else None
        ),
        "opportunity_recall": (
            _ratio(valid_hits, opportunities) if selected else None
        ),
    }


def phase_recovery_summary(
    records: Sequence[Dict[str, Any]], phase_prefix: str = "shift-"
) -> Dict[str, Any]:
    """Return deterministic, oracle-targeted recovery lags after phase shifts.

    The policy-independent target is 90% of the reuse-opportunity rate in the
    final quarter of a phase. Lag is the earliest zero-based start of a
    contiguous 10%-of-phase window whose valid-hit rate reaches the target.
    A zero or unattained target is right-censored at the phase length.
    """

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in records:
        phase = str(row.get("phase", ""))
        if phase.startswith(phase_prefix):
            grouped.setdefault(phase, []).append(row)
    ordered = sorted(
        grouped.items(),
        key=lambda item: min(int(row["request_index"]) for row in item[1]),
    )

    transitions: List[Dict[str, Any]] = []
    for phase, phase_rows in ordered[1:]:
        phase_rows = sorted(phase_rows, key=lambda row: int(row["request_index"]))
        count = len(phase_rows)
        window = max(1, int(math.floor(count * RECOVERY_WINDOW_FRACTION)))
        tail_count = max(1, int(math.ceil(count * RECOVERY_TAIL_FRACTION)))
        tail = phase_rows[-tail_count:]
        tail_opportunities = sum(
            int(bool(row.get("reuse_opportunity", False))) for row in tail
        )
        oracle_tail_rate = tail_opportunities / tail_count
        target = RECOVERY_TARGET_FRACTION * oracle_tail_rate

        prefix_hits = [0]
        for row in phase_rows:
            prefix_hits.append(
                prefix_hits[-1] + int(bool(row.get("valid_hit", False)))
            )
        recovered = False
        lag = count
        if target > 0:
            for start in range(0, count - window + 1):
                hits = prefix_hits[start + window] - prefix_hits[start]
                if hits / window + 1e-12 >= target:
                    recovered = True
                    lag = start
                    break

        transitions.append(
            {
                "phase": phase,
                "requests": count,
                "window_requests": window,
                "tail_requests": tail_count,
                "oracle_tail_opportunity_rate": round(oracle_tail_rate, 8),
                "target_valid_hit_rate": round(target, 8),
                "recovered": recovered,
                "lag_requests": lag,
            }
        )

    lags = [int(row["lag_requests"]) for row in transitions]
    return {
        "transitions": transitions,
        "transition_count": len(transitions),
        "phase_recovery_lag_mean_requests": (
            round(sum(lags) / len(lags), 8) if lags else None
        ),
        "phase_recovery_lag_max_requests": max(lags) if lags else None,
        "phase_recovery_failures": (
            sum(int(not bool(row["recovered"])) for row in transitions)
            if transitions
            else None
        ),
    }


class CacheSimulation:
    """A labeled semantic-cache replay around a real eviction policy."""

    def __init__(
        self,
        policy_name: str,
        config: BenchmarkConfig,
        workload: str,
        requests: Sequence[Request],
        workload_hash: str,
    ):
        self.policy_name = policy_name
        self.config = config
        self.workload = workload
        self.requests = requests
        self.workload_hash = workload_hash
        self.answers: Dict[str, str] = {}
        self.embeddings: Dict[str, np.ndarray] = {}
        self.seen_concepts: Set[str] = set()
        self.admissions = 0
        self.rejections = 0
        self.evictions = 0
        self.policy = PolicyAdapter(policy_name, config, self._evict)
        self.run_id = _stable_run_id(policy_name, workload_hash, config)

    def _evict(self, keys: List[str]) -> None:
        for key in keys:
            if key in self.answers:
                self.evictions += 1
            self.answers.pop(key, None)
            self.embeddings.pop(key, None)

    def _nearest(self, query: np.ndarray) -> Tuple[Optional[str], float]:
        best_key: Optional[str] = None
        best_similarity = -1.0
        for key in sorted(self.embeddings):
            similarity = max(
                -1.0, min(1.0, float(np.dot(query, self.embeddings[key])))
            )
            if similarity > best_similarity:
                best_key = key
                best_similarity = similarity
        return best_key, best_similarity

    def execute(self) -> RunResult:
        records: List[Dict[str, Any]] = []
        latencies_ns: List[int] = []
        wall_start = time.perf_counter_ns() if self.config.measure_latency else 0

        for request in self.requests:
            request_start = time.perf_counter_ns() if self.config.measure_latency else 0
            cache_before = len(self.answers)
            opportunity = request.concept_id in self.seen_concepts
            selected_key, similarity = self._nearest(request.embedding)
            raw_hit = bool(
                selected_key is not None
                and similarity + 1e-12 >= self.config.hit_threshold
            )
            returned_answer_id: Optional[str] = None
            admitted = False
            admission_reason = "hit" if raw_hit else ""
            evicted_ids: List[str] = []

            if raw_hit:
                assert selected_key is not None
                returned_answer_id = self.answers[selected_key]
                if not self.policy.get(selected_key):
                    raise RuntimeError("policy/store residency diverged on hit")
            else:
                result = self.policy.put(request.concept_id, request.embedding)
                admitted = result.admitted
                admission_reason = result.reason
                evicted_ids = result.evicted_ids
                if admitted:
                    self.answers[request.concept_id] = request.concept_id
                    self.embeddings[request.concept_id] = request.embedding
                    self.admissions += 1
                else:
                    self.rejections += 1

            valid_hit = bool(raw_hit and returned_answer_id == request.concept_id)
            false_hit = bool(raw_hit and not valid_hit)
            # A wrong semantic answer did not satisfy the reuse opportunity.
            false_miss = bool(opportunity and not valid_hit)
            self.seen_concepts.add(request.concept_id)
            cache_after = len(self.answers)

            if cache_after > self.config.capacity:
                raise RuntimeError("cache capacity invariant violated")
            if set(self.answers) != set(self.policy.resident_keys()):
                raise RuntimeError("policy and simulated storage diverged")

            latency_ns = (
                time.perf_counter_ns() - request_start
                if self.config.measure_latency
                else 0
            )
            latencies_ns.append(latency_ns)
            record = {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "policy": self.policy_name,
                "workload": self.workload,
                "trace_hash": self.workload_hash,
                "seed": self.config.seed,
                "capacity": self.config.capacity,
                "request_index": request.index,
                "phase": request.phase,
                "concept_id": request.concept_id,
                "topic_id": request.topic_id,
                "cell_id": request.cell_id,
                "token_cost": request.token_cost,
                "reuse_opportunity": opportunity,
                "selected_cache_id": selected_key if raw_hit else None,
                "returned_answer_id": returned_answer_id,
                "similarity": round(similarity, 8) if selected_key is not None else None,
                "raw_hit": raw_hit,
                "valid_hit": valid_hit,
                "false_hit": false_hit,
                "false_miss": false_miss,
                "admitted": admitted,
                "admission_reason": admission_reason,
                "evicted_ids": evicted_ids,
                "cache_entries_before": cache_before,
                "cache_entries_after": cache_after,
                "latency_ns": latency_ns,
            }
            records.append(record)

        wall_ns = (
            time.perf_counter_ns() - wall_start if self.config.measure_latency else 0
        )
        summary = self._summarize(records, latencies_ns, wall_ns)
        return RunResult(records, summary)

    def _summarize(
        self,
        records: Sequence[Dict[str, Any]],
        latencies_ns: Sequence[int],
        wall_ns: int,
    ) -> Dict[str, Any]:
        count = len(records)
        raw_hits = sum(int(row["raw_hit"]) for row in records)
        valid_hits = sum(int(row["valid_hit"]) for row in records)
        false_hits = sum(int(row["false_hit"]) for row in records)
        false_misses = sum(int(row["false_miss"]) for row in records)
        opportunities = sum(int(row["reuse_opportunity"]) for row in records)
        valid_tokens = sum(
            int(row["token_cost"]) for row in records if row["valid_hit"]
        )
        total_tokens = sum(int(row["token_cost"]) for row in records)
        sorted_latencies = sorted(latencies_ns)
        policy_stats = self.policy.stats()
        deterministic_digest = _records_digest(records)
        summary = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "policy": self.policy_name,
            "workload": self.workload,
            "seed": self.config.seed,
            "trace_hash": self.workload_hash,
            "deterministic_digest": deterministic_digest,
            "requests": count,
            "capacity": self.config.capacity,
            "raw_hits": raw_hits,
            "valid_hits": valid_hits,
            "false_hits": false_hits,
            "misses": count - raw_hits,
            "false_misses": false_misses,
            "reuse_opportunities": opportunities,
            "valid_hit_rate": _ratio(valid_hits, count),
            "false_hit_rate": _ratio(false_hits, count),
            "hit_precision": _ratio(valid_hits, raw_hits),
            "opportunity_recall": _ratio(valid_hits, opportunities),
            "safe_token_saving_ratio": _ratio(valid_tokens, total_tokens),
            "admissions": self.admissions,
            "rejections": self.rejections,
            "evictions": self.evictions,
            "final_cache_entries": len(self.answers),
            "mean_latency_us": round(
                sum(sorted_latencies) / max(1, count) / 1000.0, 6
            ),
            "p50_latency_us": round(_nearest_rank(sorted_latencies, 0.50) / 1000.0, 6),
            "p95_latency_us": round(_nearest_rank(sorted_latencies, 0.95) / 1000.0, 6),
            "p99_latency_us": round(_nearest_rank(sorted_latencies, 0.99) / 1000.0, 6),
            "throughput_qps": (
                round(count / (wall_ns / 1_000_000_000.0), 6)
                if wall_ns > 0
                else 0.0
            ),
            "policy_topics": int(policy_stats.get("topics", 0)),
            "policy_cells": int(policy_stats.get("cells", 0)),
            "policy_ghost_cells": int(policy_stats.get("ghost_cells", 0)),
        }
        summary.update(_phase_metrics(records))
        return summary


RUN_FIELDS = (
    "schema_version",
    "run_id",
    "policy",
    "workload",
    "seed",
    "trace_hash",
    "deterministic_digest",
    "requests",
    "capacity",
    "raw_hits",
    "valid_hits",
    "false_hits",
    "misses",
    "false_misses",
    "reuse_opportunities",
    "valid_hit_rate",
    "false_hit_rate",
    "hit_precision",
    "opportunity_recall",
    "safe_token_saving_ratio",
    "admissions",
    "rejections",
    "evictions",
    "final_cache_entries",
    "mean_latency_us",
    "p50_latency_us",
    "p95_latency_us",
    "p99_latency_us",
    "throughput_qps",
    "policy_topics",
    "policy_cells",
    "policy_ghost_cells",
    "scan_return_requests",
    "scan_return_valid_hits",
    "scan_return_false_hits",
    "scan_return_reuse_opportunities",
    "scan_return_valid_hit_rate",
    "scan_return_false_hit_rate",
    "scan_return_opportunity_recall",
    "shift_recovery_lag_mean_requests",
    "shift_recovery_lag_max_requests",
    "shift_recovery_failures",
    "shift_recovery_transitions_json",
)


def run_benchmark(config: BenchmarkConfig, output_dir: Path) -> List[Dict[str, Any]]:
    """Execute all configured workload/policy pairs and write stable artifacts."""

    config.validate()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(
        topics=config.topics,
        cells_per_topic=config.cells_per_topic,
        concepts_per_cell=config.concepts_per_cell,
    )

    all_records: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []
    traces: Dict[str, str] = {}
    for workload in config.workloads:
        request_count = (
            config.request_count
            if config.request_count is not None
            else DEFAULT_WORKLOAD_SIZES[workload]
        )
        requests = build_trace(
            workload,
            catalog,
            request_count,
            config.seed,
            config.capacity,
        )
        workload_hash = trace_hash(requests)
        traces[workload] = workload_hash
        for policy in config.policies:
            result = CacheSimulation(
                policy,
                config,
                workload,
                requests,
                workload_hash,
            ).execute()
            if config.verify_determinism:
                repeat = CacheSimulation(
                    policy,
                    config,
                    workload,
                    requests,
                    workload_hash,
                ).execute()
                if (
                    result.summary["deterministic_digest"]
                    != repeat.summary["deterministic_digest"]
                ):
                    raise RuntimeError(
                        "non-timing result was not deterministic for %s/%s"
                        % (workload, policy)
                    )
            all_records.extend(result.request_records)
            summaries.append(result.summary)

    _write_jsonl(output_dir / "requests.jsonl", all_records)
    _write_csv(output_dir / "runs.csv", summaries)
    _write_manifest(output_dir / "manifest.json", config, traces, summaries)
    return summaries


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=RUN_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in RUN_FIELDS})


def _write_manifest(
    path: Path,
    config: BenchmarkConfig,
    traces: Dict[str, str],
    summaries: Sequence[Dict[str, Any]],
) -> None:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "config": _json_safe(asdict(config)),
        "trace_hashes": dict(sorted(traces.items())),
        "run_ids": [row["run_id"] for row in summaries],
        "timing_is_measured": config.measure_latency,
        "determinism_scope": "all request fields except latency_ns and summary timing fields",
    }
    with path.open("w", encoding="utf-8") as output:
        json.dump(manifest, output, sort_keys=True, indent=2)
        output.write("\n")


def _stable_run_id(policy: str, workload_hash: str, config: BenchmarkConfig) -> str:
    identity = {
        "capacity": config.capacity,
        "admission_margin": config.admission_margin,
        "cell_threshold": config.cell_threshold,
        "centroid_alpha": config.centroid_alpha,
        "cluster_similarity_threshold": config.cluster_similarity_threshold,
        "demand_half_life": config.demand_half_life,
        "entry_hit_weight": config.entry_hit_weight,
        "hit_threshold": config.hit_threshold,
        "policy": policy,
        "quota_strength": config.quota_strength,
        "ghost_support_threshold": config.ghost_support_threshold,
        "seed": config.seed,
        "trace_hash": workload_hash,
    }
    encoded = json.dumps(
        _json_safe(identity), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _records_digest(records: Sequence[Dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in records:
        stable = {key: value for key, value in row.items() if key != "latency_ns"}
        digest.update(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _phase_metrics(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Return workload-applicable scan and shift phase diagnostics.

    For each post-initial shift phase, a policy-independent target is 90% of
    the reuse-opportunity rate in that phase's final quarter. Recovery is the
    earliest start of a contiguous 10%-of-phase window whose valid-hit rate
    meets the target. An unattained or zero target is right-censored at the
    full phase length. Non-applicable scalar fields are ``None`` rather than
    numeric zeros, so downstream analyses cannot mistake them for results.
    """

    scan = phase_valid_hit_summary(records, "scan-return")
    recovery = phase_recovery_summary(records)
    result: Dict[str, Any] = {
        "scan_return_requests": None,
        "scan_return_valid_hits": None,
        "scan_return_false_hits": None,
        "scan_return_reuse_opportunities": None,
        "scan_return_valid_hit_rate": None,
        "scan_return_false_hit_rate": None,
        "scan_return_opportunity_recall": None,
        "shift_recovery_lag_mean_requests": None,
        "shift_recovery_lag_max_requests": None,
        "shift_recovery_failures": None,
        "shift_recovery_transitions_json": "",
    }
    if scan["requests"]:
        result.update(
            {
                "scan_return_requests": scan["requests"],
                "scan_return_valid_hits": scan["valid_hits"],
                "scan_return_false_hits": scan["false_hits"],
                "scan_return_reuse_opportunities": scan[
                    "reuse_opportunities"
                ],
                "scan_return_valid_hit_rate": scan["valid_hit_rate"],
                "scan_return_false_hit_rate": scan["false_hit_rate"],
                "scan_return_opportunity_recall": scan[
                    "opportunity_recall"
                ],
            }
        )
    if recovery["transition_count"]:
        result.update(
            {
                "shift_recovery_lag_mean_requests": recovery[
                    "phase_recovery_lag_mean_requests"
                ],
                "shift_recovery_lag_max_requests": recovery[
                    "phase_recovery_lag_max_requests"
                ],
                "shift_recovery_failures": recovery[
                    "phase_recovery_failures"
                ],
                "shift_recovery_transitions_json": json.dumps(
                    recovery["transitions"],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
    return result


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 8) if denominator else 0.0


def _nearest_rank(sorted_values: Sequence[int], quantile: float) -> int:
    if not sorted_values:
        return 0
    index = max(0, int(math.ceil(quantile * len(sorted_values))) - 1)
    return int(sorted_values[index])


def _json_safe(value: Any) -> Any:
    """Convert non-finite floats to portable JSON strings."""

    if isinstance(value, float) and not math.isfinite(value):
        if math.isinf(value):
            return "infinity" if value > 0 else "-infinity"
        return "nan"
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
