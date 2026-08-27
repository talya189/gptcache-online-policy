# Requirement-by-requirement completion audit

This audit maps the lecturer's `Final Project Guidelines.pdf` to concrete
repository evidence. Command outcomes and artifact hashes are finalized only
after the clean host/container gate; a failed experimental gate remains a
completed, negative result rather than being rewritten.

## 1. Project scope and goal

| Requirement | Status | Evidence |
|---|---:|---|
| Build on an open-source LLM caching library | Complete | GPTCache baseline commit `c59fb3a6152a4458b2a070ca183b61c4b614095f`; feature branch `feature/online-cluster-aware-cache` |
| Design and implement an enhanced policy | Complete | `gptcache/manager/eviction/carma.py`; `docs/project/policy-design.md` |
| Evaluate a measurable performance claim | Complete, mixed result | `artifacts/samples/full/`; Gates 3/5/6 pass and Gates 2/4 fail. The original precomputed-vector Gate 7 diagnostic remains `PENDING`; the later `gate7b-onnx-v1` and `gate7c-onnx-v2` formal attempts are preserved as separate `INVALID`, nonclaimable evidence generations. V2 completed all 15 children but its descriptive numerical reconstruction is 24/25 checks, hence counterfactual `FAIL`, never `PASS` |
| Explain a defensible claim to fame | Complete | `docs/project/report.tex`; adaptive category-shift gain and real-system overhead boundary, with failed scan and QQP claims retained |

## 2. Baseline framework and paper

| Requirement | Status | Evidence |
|---|---:|---|
| Mature, modifiable baseline | Complete | GPTCache modular storage/search/eviction path; `docs/project/baseline-validation.md` |
| One-page justification | Complete with sealed wording caveat | `docs/project/baseline-justification.md`; its source-sealed MOSS overstatement and implicit-default wording are corrected in the report/handoff because that file is outside the packaging allowlist |
| Default policy/features described | Complete | LRU default and LRU/LFU/FIFO/random gap documented in the repository README, report, and handoff |
| Related paper | Complete | SCALM, arXiv:2406.00025v1; comparison in policy design and report, with GPTCache, 2Q, ARC, LRFU, and TinyLFU in the report bibliography |

## 3. Performance test suite

| Requirement | Status | Evidence |
|---|---:|---|
| Repetitive/steady workload | Complete | Stationary Zipf traces in `benchmarks/carma/synthetic.py` |
| Novel long prompts | Partial | Pinned, token-bucket-stratified MOSS replay in `benchmarks/carma/moss.py` validates corpus parsing, recorded-response delivery, and real token accounting with an exact-key LRU cache. Its 200 unique requests produce zero hits, and it does not measure CARMA/LFU semantic behavior or valid end-to-end long-prompt CARMA overhead |
| Phase shift and pollution stress | Complete | Five frozen phases and 30/40/30 scan; structural preflight tests |
| Human-labeled semantic safety | Complete, negative result | Strict calibration-only Gate 2 v2 in `benchmarks/carma/qqp_v2.py`; no threshold qualified, the selected threshold is null, and all 56,963 held-out pairs remained unevaluated. At 0.96, precision is 0.97574893, its one-sided Wilson lower bound is 0.96420782, false-hit rate is 0.00219951, and its one-sided Wilson upper bound is 0.00326719 |
| Mean/p50/p95/p99 latency | Complete, with evidence-status qualification | Historical real integration `runs.csv` retains run summaries. Gate 7 v2 additionally retained 45,000 per-request full-path rows with embedding, FAISS, policy, SQLite, response-return, residual, and total timing, but the attempt is `INVALID` and these measurements are descriptive rather than a claimable gate result |
| Implementation/storage correctness | Complete | Raw/valid/false-hit accounting, policy invariants, failure-path tests, and zero stale candidates in the valid historical SQLite/FAISS runs |
| Semantic reuse correctness | Negative / not established | Gate 2 v2 selected no QQP threshold; the held-out split was not evaluated. Gate 7 v2's separate descriptive semantic reconstruction includes three direct-negative and two unlabeled cross-component hits and cannot establish safety |
| Memory/CPU/I/O | Complete for real system, with v2 qualification | Five historical SQLite/FAISS `resources.jsonl` traces plus Gate 7 v2 external samples for all 15 clean child processes; the v2 auditor found one 202,142,875 ns sampling gap above the frozen 200,000,000 ns maximum, which contributes to `INVALID` |
| Throughput | Complete | Historical integration requests/second plus v2 service throughput derived from `requests / sum(request_total_ns)`; v2 values remain descriptive because the attempt is nonclaimable |
| Automated scripts and CI | Complete for the formal source | `scripts/run_*benchmark.sh`; `.github/workflows/carma-ci.yml`; exact-source host and paired-container evidence; hosted branch/tag checks for source `557c6ac0578cb6b77c5ae51595b49abdc0407e10` |
| README how-to plus sample logs | Complete with Gate 7 caveat | Root `README.md` covers installation and development smoke paths; its source-sealed prospective Gate 7 command is historical and superseded by the report/handoff no-rerun and future-v3 instructions; checksum-verified `artifacts/samples/` bundle |

## 4. Extension implementation

| Requirement | Status | Evidence |
|---|---:|---|
| Scoped feature history and publication | Complete | Development is isolated in the scoped feature history; the packaged submission commit is intentionally mirrored on `main` and `feature/online-cluster-aware-cache` |
| Baseline interface compatibility | Complete, qualified | CARMA is opt-in; the factory default remains LRU and the public interface is preserved for valid existing inputs. Shared vector normalization, validation, and cleanup were hardened |
| Tunable parameters documented | Complete | Topic/cell thresholds, half-life, quota strength, ghost threshold, margin in README/policy design |
| Unit tests for new policy | Complete | `tests/unit_tests/eviction/test_carma.py` |
| Storage integration tests | Complete | `tests/project_tests/test_carma_integration.py` and failure-path suite |
| Safe scalar/vector eviction | Complete | Vector-first cleanup, targeted recovery, fail-stop unhealthy state |

## 5. Evaluation and analysis

| Requirement | Status | Evidence |
|---|---:|---|
| Vanilla vs. extension on identical workloads | Complete | Exact trace-hash pairing for LRU, LFU, CARMA |
| Parameter sweep | Complete for primary grid/capacity | 96 validation configs; capacities 20/50/100/200 |
| Ablation study | Complete with identifiability caveat | One-global-topic (`CARMA_NO_CLUSTER`), no decay, no admission, and no quota; one-global-topic/no-quota are observationally identical |
| Repeated seeds and confidence intervals | Complete | Ten paired primary seeds; 10,000-resample CIs; exact Wilcoxon/Holm |
| Latency and hit-rate plots | Complete with evidence-status labels | Historical deterministic figures remain under `artifacts/samples/analysis/`. The separate `artifacts/samples/analysis/gate7-v2-invalid/` package includes descriptive per-request latency distributions and the supplementary frozen-check matrix, each explicitly labeled `INVALID` and nonclaimable |
| Relative improvements | Complete | Summary/gate audit plus report tables |
| Honest significance discussion | Complete | Gate 4 failure, recovery censoring, and protocol deviations are explicit |

## 6. Publication and reporting

| Requirement | Status | Evidence |
|---|---:|---|
| README install/benchmark instructions | Complete with Gate 7 caveat | Root `README.md` CARMA section for ordinary use; formal v2/future-v3 instructions are corrected in the report, handoff, and reproducibility record because the root README is outside the packaging allowlist |
| Clean GitHub repository | Complete | Private publication target: `https://github.com/MatanGoldfarB/gptcache-online-policy`; `main` and the feature branch point to the packaged submission commit |
| Docker/environment reproducibility | Complete | Exact-source host evidence and two byte-matched network-isolated `linux/amd64` container runs for source `557c6ac0578cb6b77c5ae51595b49abdc0407e10` under `artifacts/samples/verification/publication-20260827-v2-source/` |
| Single 8--12 page PDF | Complete | `docs/project/report.tex` and visually verified `docs/project/report.pdf` |
| Introduction/related work | Complete in source | Report Section 1 |
| Extension design | Complete in source | Report Sections 2--3 |
| Experimental setup | Complete in source | Report Section 4 |
| Results and figures | Complete | Report Sections 5--7 and machine-readable `artifacts/samples/analysis/gate-audit.json` |
| Discussion/trade-offs | Complete in source | Report Section 8 |
| Grounded conclusion/future work | Complete in source | Report Section 9 |
| Appendix artifact map | Complete | Report artifact map, sample README, checksum manifest, and this audit |
| Upstream contribution | Open, ready for review | [zilliztech/GPTCache #696](https://github.com/zilliztech/GPTCache/pull/696) targets `dev` from public fork branch `MatanGoldfarB:feat/carma-online-policy`; two signed commits, live DCO success, mergeable at publication time; exact record in `docs/project/draft-pr.md` |
| Exceptional adopted-upstream-PR route | Not achieved; external | The contribution is public and ready for review, but no upstream maintainer has reviewed, merged, or otherwise adopted it. An open PR is not adoption |

## 7. Weighted success criteria

### Correctness (40%)

Implementation and storage correctness are complete: evidence includes policy
unit/property tests, real factory integration, failure-path
rollback/fail-stop regressions, relevant upstream tests, exact capacity
invariants, zero stale candidates, and zero unknown/false answer IDs in the
primary real-system runs. The current formal-source host verifier passed 8
upstream plus 2 SQLite/FAISS tests (1 optional deselected) and 453 project tests
(4 skipped); its hash-bound output is retained under
`artifacts/samples/verification/publication-20260827-v2-source/host/`.
Semantic reuse correctness is a separate negative result: strict QQP
calibration selected no threshold, so the held-out split was not evaluated,
and the invalid Gate 7 v2 trace cannot establish semantic safety.

### Reproducibility (30%)

Implemented controls include exact Python/dependency pins, an immutable
container base and CI actions, a single offline verifier, deterministic CI
logs, recorded source and model revisions/checksums, source/contract hashes,
atomic outputs, and analyzer tamper tests. One exact 527-file Git archive fed
the clean host and two fresh network-isolated `linux/amd64` containers; both
containers emitted byte-identical benchmark artifacts and normalized logs.
The attested image ID is
`sha256:a5203e8d3f3beba85c5dc542b3573e724fb4da26cca65e82b3fb90a1a26985b1`.
Historical protocol deviations remain separately recorded and are not
retroactively repaired in the original result bundle. The current exact-source
evidence is retained under
`artifacts/samples/verification/publication-20260827-v2-source/` and binds both
host and paired-container status to
`557c6ac0578cb6b77c5ae51595b49abdc0407e10`.

An independent full synthetic replay was also run from the then-current clean
report-only packaging commit
`defba4aa78b24cb7115352ed2d58177d227b60ba`, whose non-document source is the
verified parent above. It executed 5,180,400 policy-request evaluations and
reproduced `aggregate.csv`, `runs.csv`, `validation.csv`, and
`validation_runs.csv` byte-for-byte. The compact checksummed audit is retained
under `artifacts/samples/verification/final-source-synthetic-replay/`. This is
a post-publication reproducibility check, not a retroactive replacement for the
frozen experiment and not a Gate 7 result.

### Performance gain (15%)

This criterion is mixed rather than globally passed. Category shift improves
by 4.885 percentage points with a positive paired CI and corrected
significance. Stationary and synthetic weighted-savings-proxy gates pass. The original
precomputed-vector Gate 7 diagnostic remains `PENDING` under its own frozen
identity. The separately frozen `gate7c-onnx-v2` execution did measure the full
raw-text GPTCache/ONNX path for 45,000 requests in 15 clean processes, but it
is `INVALID` and nonclaimable: the canonical ledger contains only genesis and
start, the preterminal audit has seven errors, and no terminal or ordinary
adjudication exists. Its descriptive reconstruction passes 24 of 25 systems
checks; seed 20261001 fails the absolute p95 delta with 26,230,125 ns against a
500,000 ns limit. Therefore even a structurally valid version of these exact
numbers would be `FAIL`, not `PASS`. The preregistered pollution return-phase
gate also fails because LFU is already 99.993%, while Gate 2 v2 fails at
calibration and never evaluates held-out pairs.

### Clarity (15%)

The one-page baseline choice, design equations, frozen contract, deviation
ledger, README, machine-readable analysis, labeled figures, final report,
artifact map, and this completion audit tell one traceable story. Smoke,
exploratory, confirmatory, and failed results are labeled separately.

## 8. Gate 7 evidence-status audit

The three Gate 7 stories are separate experiments and must not be merged:

| Evidence generation | Frozen status | What it establishes |
|---|---:|---|
| Historical precomputed-vector SQLite/FAISS integration | `PENDING`, diagnostic only | Real post-embedding cache/storage measurements; embedding generation and a frozen across-seed rule were absent |
| `gate7b-onnx-v1` | `INVALID`, nonclaimable | The first real-text ONNX child completed 3,000 requests and exposed a cross-concept return, but the v1 runner classified the semantic event as structural corruption and stopped before the other 14 children |
| `gate7c-onnx-v2` | `INVALID`, nonclaimable | All 15 children exited zero and retained 45,000 full-path requests, but the evidence failed preterminal validation and never acquired a terminal ledger entry |

The v2 identity is exact source
`557c6ac0578cb6b77c5ae51595b49abdc0407e10`, annotated tag
`gate7c-onnx-v2-formal-source` (tag object
`550e33a38ae59c992f25fc20023f5717c5fdd1ce`), contract SHA-256
`4cb2d289bf9516e73bdc53c3f021ab9dc0e6b850c243e38bc57cc49244573f30`,
and attempt `20260827T131743602373Z-557c6ac0578c`. The retained manifest SHA-256
is `7574127cce8f1c72e87a1528dc6709947860178f43667a92add155e89e1f1cf8`;
the retained preterminal report SHA-256 is
`e3e8df2787049d3b1ea4e622e7666502b715ae572431b4f8d4e4b213c12dd39b`.

The preterminal report records seven errors: one lexical Python executable
identity mismatch, five warm-up identity schema mismatches (the files and
hashes exist, but the manifest identities carry an unexpected `rows` key), and
one 202,142,875 ns resource-sampling gap above the 200,000,000 ns maximum. The
wrapper then exited 4 with `formal preterminal adjudication is malformed`.
Forensic inspection found a separate terminalization comparison bug: the
frozen auditor source identity is `{bytes, sha256}` while the report identity
is `{path, bytes, sha256}`. The canonical v2 ledger therefore contains only
`PROTOCOL_GENESIS` and `START`, with no `TERMINAL`. There is no ordinary
`gate7-adjudication.json`; running the ordinary auditor after this fail-stop
would not repair or complete the historical attempt and is prohibited.

The curated, non-authoritative teaching copy is under
`artifacts/samples/verification/gate7-v2-invalid/`; the derived tables, chart,
and calculation notes are under
`artifacts/samples/analysis/gate7-v2-invalid/`. The ignored original attempt
root remains the authoritative evidence. Any future Gate 7 attempt requires a
new protocol version, formal root, ledger genesis, annotated tag, contract, and
complete five-seed by three-policy rerun. V2 must not be edited, terminalized,
or selectively reused.

## Final verification commands

```bash
# Requires a clean checkout and fresh evidence paths. This runs the host
# verifier, builds the exact committed archive, inspects two distinct
# linux/amd64 containers, compares them, and publishes status JSON last.
scripts/run_reproducibility_gate.sh

scripts/analyze_carma_results.sh \
  --full-dir artifacts/carma-full-20260826 \
  --integration-dir artifacts/integration-full-20260901 \
  --integration-dir artifacts/integration-full-20260902 \
  --integration-dir artifacts/integration-full-20260903 \
  --integration-dir artifacts/integration-full-20260904 \
  --integration-dir artifacts/integration-full-20260905 \
  --qqp-result artifacts/qqp-full/evaluation/result.json \
  --moss-dir artifacts/moss-full \
  --host-verification artifacts/samples/verification/publication-20260827-v2-source/host/host-verification.json \
  --container-reproducibility artifacts/samples/verification/publication-20260827-v2-source/container-reproducibility.json \
  --output artifacts/analysis-final

# Reproduce the descriptive-only Gate 7 v2 INVALID analysis from immutable
# curated snapshots. This does not run the ordinary formal auditor and cannot
# terminalize or repair the historical attempt.
.venv/bin/python \
  artifacts/samples/analysis/gate7-v2-invalid/analyze_gate7_v2_invalid.py
(cd artifacts/samples/verification/gate7-v2-invalid && \
  shasum -a 256 -c SHA256SUMS)

# Verify the compact final-source synthetic replay record. Re-running the full
# experiment is optional and executes 5,180,400 policy-request evaluations.
(cd artifacts/samples/verification/final-source-synthetic-replay && \
  shasum -a 256 -c SHA256SUMS)

# Verify the complete curated sample inventory, including every nested
# manifest. This package index is byte inventory, not experiment adjudication.
(cd artifacts/samples/verification/publication-package-20260827 && \
  shasum -a 256 -c SHA256SUMS)
```
