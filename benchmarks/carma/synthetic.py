"""Deterministic synthetic semantic-cache workloads.

The embeddings have an explicit hierarchy:

* different cells in one topic have cosine similarity 0.72;
* different concepts in one cell have cosine similarity 0.88;
* a small number of distinct near-twin concepts have similarity 0.96;
* repeated requests for one labeled concept have similarity 1.0.

This makes policy clustering testable without using a model download.  The
``concept_id`` remains the independent answer-validity oracle.
"""

import bisect
import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np


TOPIC_WEIGHT = 0.72
CELL_WEIGHT = 0.16
NEAR_GROUP_WEIGHT = 0.08
CONCEPT_WEIGHT = 0.04


@dataclass(frozen=True)
class Concept:
    """One cacheable answer identity and its synthetic semantic metadata."""

    concept_id: str
    topic_id: int
    cell_id: int
    local_id: int
    embedding: np.ndarray
    token_cost: int


@dataclass(frozen=True)
class Request:
    """One immutable request in a generated trace."""

    index: int
    concept_id: str
    topic_id: int
    cell_id: int
    embedding: np.ndarray
    token_cost: int
    phase: str


class Catalog:
    """Builds exactly normalized embeddings from disjoint basis features."""

    def __init__(
        self,
        topics: int = 6,
        cells_per_topic: int = 4,
        concepts_per_cell: int = 32,
    ):
        if topics < 2 or cells_per_topic < 2 or concepts_per_cell < 4:
            raise ValueError("catalog dimensions are too small for the workloads")
        self.topics = topics
        self.cells_per_topic = cells_per_topic
        self.concepts_per_cell = concepts_per_cell
        self._concepts = self._build()
        self.by_id = {concept.concept_id: concept for concept in self._concepts}

    @property
    def concepts(self) -> Sequence[Concept]:
        return self._concepts

    def for_topic(self, topic_id: int) -> List[Concept]:
        return [c for c in self._concepts if c.topic_id == topic_id]

    def for_cell(self, topic_id: int, cell_id: int) -> List[Concept]:
        return [
            c
            for c in self._concepts
            if c.topic_id == topic_id and c.cell_id == cell_id
        ]

    def _build(self) -> Tuple[Concept, ...]:
        concept_count = self.topics * self.cells_per_topic * self.concepts_per_cell
        cell_count = self.topics * self.cells_per_topic

        # Locals 0 and 1 in each cell are deliberately distinct answers with a
        # shared near-group coordinate.  Every other concept gets its own
        # near-group coordinate.
        near_group_count = cell_count + concept_count - 2 * cell_count
        dimension = self.topics + cell_count + near_group_count + concept_count
        cell_offset = self.topics
        near_offset = cell_offset + cell_count
        concept_offset = near_offset + near_group_count

        concepts: List[Concept] = []
        next_unique_group = cell_count
        concept_number = 0
        for topic_id in range(self.topics):
            for cell_id in range(self.cells_per_topic):
                absolute_cell = topic_id * self.cells_per_topic + cell_id
                twin_group = absolute_cell
                for local_id in range(self.concepts_per_cell):
                    if local_id < 2:
                        near_group = twin_group
                    else:
                        near_group = next_unique_group
                        next_unique_group += 1

                    embedding = np.zeros(dimension, dtype=np.float32)
                    embedding[topic_id] = math.sqrt(TOPIC_WEIGHT)
                    embedding[cell_offset + absolute_cell] = math.sqrt(CELL_WEIGHT)
                    embedding[near_offset + near_group] = math.sqrt(
                        NEAR_GROUP_WEIGHT
                    )
                    embedding[concept_offset + concept_number] = math.sqrt(
                        CONCEPT_WEIGHT
                    )
                    embedding /= np.linalg.norm(embedding)

                    concept_id = "t%02d-c%02d-q%03d" % (
                        topic_id,
                        cell_id,
                        local_id,
                    )
                    token_cost = 16 + (
                        topic_id * 31 + cell_id * 17 + local_id * 13
                    ) % 241
                    concepts.append(
                        Concept(
                            concept_id=concept_id,
                            topic_id=topic_id,
                            cell_id=cell_id,
                            local_id=local_id,
                            embedding=embedding,
                            token_cost=token_cost,
                        )
                    )
                    concept_number += 1
        return tuple(concepts)


class WeightedSampler:
    """Stable weighted sampling using Python's version-stable MT generator."""

    def __init__(self, values: Sequence[Concept], weights: Sequence[float]):
        if len(values) != len(weights) or not values:
            raise ValueError("values and weights must be non-empty and equally sized")
        self.values = tuple(values)
        total = 0.0
        cumulative = []
        for weight in weights:
            if weight < 0:
                raise ValueError("weights must be non-negative")
            total += weight
            cumulative.append(total)
        if total <= 0:
            raise ValueError("at least one weight must be positive")
        self.cumulative = tuple(cumulative)
        self.total = total

    def sample(self, rng: random.Random) -> Concept:
        position = rng.random() * self.total
        index = bisect.bisect_left(self.cumulative, position)
        if index == len(self.values):
            index -= 1
        return self.values[index]


def _zipf_sampler(values: Sequence[Concept], alpha: float = 1.2) -> WeightedSampler:
    ordered = sorted(values, key=lambda item: item.concept_id)
    return WeightedSampler(
        ordered,
        [1.0 / math.pow(rank + 1, alpha) for rank in range(len(ordered))],
    )


def _request(index: int, concept: Concept, phase: str) -> Request:
    return Request(
        index=index,
        concept_id=concept.concept_id,
        topic_id=concept.topic_id,
        cell_id=concept.cell_id,
        embedding=concept.embedding,
        token_cost=concept.token_cost,
        phase=phase,
    )


def stationary_trace(catalog: Catalog, count: int, seed: int) -> List[Request]:
    """Stable Zipf demand with an exact 15% one-shot pollution component."""

    rng = random.Random(seed)
    one_shot_count = int(round(count * 0.15))
    if one_shot_count >= len(catalog.concepts):
        raise ValueError("catalog does not contain enough concepts for 15% one-shots")
    shuffled = list(catalog.concepts)
    random.Random(seed ^ 0x5A17).shuffle(shuffled)
    one_shots = shuffled[:one_shot_count]
    reusable = shuffled[one_shot_count:]
    reusable_by_topic = {
        topic: [c for c in reusable if c.topic_id == topic]
        for topic in range(catalog.topics)
    }
    topic_values = [topic for topic, values in reusable_by_topic.items() if values]
    topic_sampler = WeightedSampler(
        topic_values,
        [1.0 / math.pow(rank + 1, 1.0) for rank in range(len(topic_values))],
    )
    concept_samplers = {
        topic: _zipf_sampler(reusable_by_topic[topic]) for topic in topic_values
    }
    one_shot_positions = set(
        random.Random(seed ^ 0xC1A0).sample(range(count), one_shot_count)
    )
    next_one_shot = 0
    requests = []
    for index in range(count):
        if index in one_shot_positions:
            concept = one_shots[next_one_shot]
            next_one_shot += 1
            phase = "stationary-one-shot"
        else:
            topic = topic_sampler.sample(rng)
            concept = concept_samplers[topic].sample(rng)
            phase = "stationary-reuse"
        requests.append(_request(index, concept, phase))
    return requests


def phase_shift_trace(catalog: Catalog, count: int, seed: int) -> List[Request]:
    """Demand whose hot topic rotates through three equal-length CI phases."""

    rng = random.Random(seed)
    phases = 3
    concept_samplers = {
        topic: _zipf_sampler(catalog.for_topic(topic))
        for topic in range(catalog.topics)
    }
    requests = []
    for index in range(count):
        phase_index = min(phases - 1, index * phases // count)
        hot_topic = phase_index % catalog.topics
        if rng.random() < 0.85:
            topic = hot_topic
        else:
            alternatives = [t for t in range(catalog.topics) if t != hot_topic]
            topic = alternatives[int(rng.random() * len(alternatives))]
        concept = concept_samplers[topic].sample(rng)
        requests.append(_request(index, concept, "shift-%d" % phase_index))
    return requests


def pollution_scan_trace(
    catalog: Catalog, count: int, seed: int, capacity: int
) -> List[Request]:
    """Warm a compact hot set, scan unique cold entries, then revisit hot data."""

    rng = random.Random(seed)
    warm_count = count // 3
    scan_count = count // 3
    return_count = count - warm_count - scan_count
    working_size = max(4, min(int(capacity * 0.80), capacity - 1))
    hot_candidates = sorted(catalog.for_topic(0), key=lambda item: item.concept_id)
    hot_set = hot_candidates[:working_size]
    hot_sampler = _zipf_sampler(hot_set, alpha=1.1)

    scan_candidates = sorted(
        [c for c in catalog.concepts if c.topic_id != 0],
        key=lambda item: item.concept_id,
    )
    if scan_count > len(scan_candidates):
        raise ValueError("catalog does not contain enough unique scan concepts")
    # Rotate rather than shuffle to preserve adjacency of deliberate near twins.
    rotation = seed % len(scan_candidates)
    scan_candidates = scan_candidates[rotation:] + scan_candidates[:rotation]

    requests = []
    for _ in range(warm_count):
        requests.append(_request(len(requests), hot_sampler.sample(rng), "scan-warm"))
    for concept in scan_candidates[:scan_count]:
        requests.append(_request(len(requests), concept, "scan-unique"))
    for _ in range(return_count):
        requests.append(
            _request(len(requests), hot_sampler.sample(rng), "scan-return")
        )
    return requests


def novel_trace(catalog: Catalog, count: int, seed: int) -> List[Request]:
    """All-unique traffic, including deterministic near-twin hard negatives."""

    candidates = sorted(catalog.concepts, key=lambda item: item.concept_id)
    if count > len(candidates):
        raise ValueError("catalog does not contain enough unique concepts")
    # Topic rotation changes which concepts are used while keeping twin pairs
    # adjacent, so distinct concepts with similarity 0.96 exercise false hits.
    block = catalog.cells_per_topic * catalog.concepts_per_cell
    rotation = (seed % catalog.topics) * block
    candidates = candidates[rotation:] + candidates[:rotation]
    return [_request(i, concept, "novel") for i, concept in enumerate(candidates[:count])]


WORKLOAD_BUILDERS = {
    "stationary": stationary_trace,
    "phase_shift": phase_shift_trace,
    "pollution_scan": pollution_scan_trace,
    "novel": novel_trace,
}


def build_trace(
    workload: str,
    catalog: Catalog,
    count: int,
    seed: int,
    capacity: int,
) -> List[Request]:
    """Build one workload by its stable CLI name."""

    if count <= 0:
        raise ValueError("request count must be positive")
    if workload not in WORKLOAD_BUILDERS:
        raise ValueError("unknown workload %s" % workload)
    if workload == "pollution_scan":
        return pollution_scan_trace(catalog, count, seed, capacity)
    return WORKLOAD_BUILDERS[workload](catalog, count, seed)


def trace_hash(requests: Iterable[Request]) -> str:
    """Hash all semantic inputs, including exact little-endian float32 vectors."""

    digest = hashlib.sha256()
    for request in requests:
        metadata = {
            "cell_id": request.cell_id,
            "concept_id": request.concept_id,
            "index": request.index,
            "phase": request.phase,
            "token_cost": request.token_cost,
            "topic_id": request.topic_id,
        }
        digest.update(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
        digest.update(np.asarray(request.embedding, dtype="<f4").tobytes(order="C"))
    return digest.hexdigest()
