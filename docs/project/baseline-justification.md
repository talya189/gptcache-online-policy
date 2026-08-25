# Baseline and Paper Justification

## Selection

This project uses GPTCache at commit
`c59fb3a6152a4458b2a070ca183b61c4b614095f` as its software baseline and
SCALM (*Towards Semantic Caching for Automated Chat Services with Large
Language Models*, arXiv:2406.00025v1) as its primary related paper. GPTCache's
own paper, published at NLP-OSS 2023, documents the baseline system.

GPTCache is a strong course-project baseline because it is open source,
locally runnable, and already separates embedding, vector search, similarity
evaluation, scalar/vector storage, and eviction. Its SQLite plus FAISS path can
be exercised on CPU without an API key. The chosen commit's core in-memory
eviction tests pass locally, and a direct end-to-end storage smoke test passes.
That gives a measurable working baseline rather than a paper-only prototype.

## Gap

The baseline applies a single count-bounded LRU, LFU, FIFO, or random policy to
opaque row IDs. Its replacement decision cannot see whether entries belong to
a reusable semantic region, a redundant paraphrase cell, or a one-shot scan.
This makes scan pollution and changing topic demand plausible failure modes.

SCALM is directly relevant because it evaluates semantic caching with
conversation replay and shows that semantic patterns can guide adaptive cache
management. Its design, however, derives hierarchical patterns and priority
ranks from a corpus before replay. The proposed extension addresses a different
question: can bounded semantic structure, reuse, and miss pressure be learned
online, without offline ranking, token-cost input, or a trained policy?

## Extension and evaluation

CARMA (Cluster-Adaptive Reuse and Miss-pressure Admission) adds an opt-in
online policy. It incrementally groups insertions, retains bounded ghost
evidence for repeated misses, allocates capacity by decayed topic demand and
miss pressure, and discounts redundant residents. It preserves GPTCache's
answer-matching threshold and existing policy interface.

The primary comparison uses identical traces, embeddings, capacities, lookup
thresholds, and request order for LRU, LFU, and CARMA. Labeled QQP data checks
semantic correctness; pinned MOSS recorded responses provide SCALM-style
token-aware replay without a live LLM. The preregistered outcomes are valid-hit
rate, false-hit rate/precision, safe token-saving ratio, latency percentiles,
throughput, and resource use. Parameter sweeps, component ablations, paired
seeds, confidence intervals, and corrected tests separate a real gain from a
favorable single trace.

This scope is feasible within one repository, produces a concrete systems
modification, and directly supports the lecturer's correctness,
reproducibility, performance, and clarity criteria.
