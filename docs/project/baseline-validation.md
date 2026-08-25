# Baseline Validation Record

Date: 2026-08-25 (Asia/Jerusalem)

## Source identity

- Repository: `https://github.com/zilliztech/GPTCache.git`
- Commit: `c59fb3a6152a4458b2a070ca183b61c4b614095f`
- Local branch: `feature/online-cluster-aware-cache`
- Working tree was clean before project files were added.

## Environment used for the baseline check

- macOS on Apple Silicon
- Python 3.12.13
- numpy 2.5.2
- cachetools 7.1.7
- requests 2.34.2
- SQLAlchemy 2.0.52
- faiss-cpu 1.15.0
- pytest 9.1.1

These versions record the observed baseline environment; the reproducible
project lock will be created separately and verified in a clean environment.

## Passing checks

The unmodified in-memory eviction suite passed:

```text
......                                                                   [100%]
6 passed in 0.11s
```

A direct SQLite + FAISS + LRU smoke check also passed. It inserted two
orthogonal vectors, retrieved the expected nearest entry, marked it as recently
used, inserted a third entry at capacity, and verified that scalar capacity and
LRU retention were correct:

```text
baseline sqlite+faiss+lru smoke: PASS
```

## Upstream reproducibility defects observed before modification

A broader relevant test selection produced 12 passes and one collection/runtime
failure in `tests/unit_tests/manager/test_factory.py::TestFactory::test_normal`.
The failing path imports ChromaDB. GPTCache's lazy installer invoked a bare
`pip`, printed a successful installation message, and the import still failed
inside the active virtual environment with `ModuleNotFoundError: chromadb`.

Two additional optional suites failed collection for the same class of reason:
`redis_om` and `docarray` were unavailable to the active interpreter even though
the lazy-import path attempted installation.

This is baseline evidence, not a regression introduced by the project. The
project will avoid runtime package installation, pin its benchmark dependencies,
and separate required core checks from optional backend checks.

## Baseline behavior relevant to the extension

- The default semantic storage manager creates an in-memory LRU eviction
  manager with a count limit of 1,000 entries.
- The current memory manager also accepts LFU, FIFO, and random replacement.
- `EvictionBase.put` and `EvictionBase.get` receive only opaque entry IDs.
- `SSDataManager.import_data` has normalized embeddings at insertion time, and
  scalar storage persists each embedding, so a backward-compatible metadata
  hook can support both live insertion and restart reconstruction.
- The scalar and vector stores are deleted through `SSDataManager._clear`; the
  new policy must use that callback rather than deleting only its own metadata.

## Baseline conclusion

The CPU SQLite/FAISS path is viable for the project and the core eviction code
is small enough to modify and test. Reproducibility must be strengthened around
dependency installation, and the semantic policy requires a metadata-aware
extension that does not break existing `put(ids)` and `get(id)` callers.
