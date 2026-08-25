"""Component-safe QQP preparation, GPTCache ONNX embedding, and calibration.

The bundled QQP archive is a tar.gz file despite its ``.json.gz`` suffix. This
module never uses policy-created clusters as answer ground truth: positive
human labels define connected answer concepts and negative labels remain hard
negative comparisons.
"""

import argparse
import csv
import concurrent.futures
import hashlib
import json
import math
import os
import tarfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


QQP_ARCHIVE_SHA256 = "1fcd814990dd8ebbc1cdacd41fca11e56739e2e4d72e4ac119334084ed7e2b58"
TOKENIZER_REPOSITORY = "GPTCache/paraphrase-albert-small-v2"
TOKENIZER_REVISION = "5fb246187b5489d59ce0db167e739192759defab"
MODEL_REPOSITORY = "GPTCache/paraphrase-albert-onnx"
MODEL_REVISION = "5b562a100bc67e898ac89814e7a4668a18d65756"
CALIBRATION_PERCENT = 20
SCHEMA_VERSION = "carma-qqp-v1"

_WORKER_TOKENIZER = None
_WORKER_SESSION = None
_WORKER_MAX_LENGTH = 512


def _initialize_onnx_worker(model_path: str, max_length: int) -> None:
    """Initialize one fixed-batch ONNX session per worker process."""

    global _WORKER_TOKENIZER, _WORKER_SESSION, _WORKER_MAX_LENGTH
    import onnxruntime
    from transformers import AutoTokenizer

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    _WORKER_TOKENIZER = AutoTokenizer.from_pretrained(
        TOKENIZER_REPOSITORY,
        revision=TOKENIZER_REVISION,
    )
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    _WORKER_SESSION = onnxruntime.InferenceSession(
        model_path,
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    _WORKER_MAX_LENGTH = max_length


def _onnx_worker_embedding(text: str) -> np.ndarray:
    if _WORKER_TOKENIZER is None or _WORKER_SESSION is None:
        raise RuntimeError("ONNX worker was not initialized")
    encoded = _WORKER_TOKENIZER(
        text,
        padding="max_length",
        truncation=True,
        max_length=_WORKER_MAX_LENGTH,
        return_tensors="np",
    )
    input_ids = np.asarray(encoded["input_ids"], dtype=np.int64)
    attention_mask = np.asarray(encoded["attention_mask"], dtype=np.int64)
    inputs = {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "token_type_ids": np.asarray(
            encoded.get("token_type_ids", np.zeros_like(input_ids)),
            dtype=np.int64,
        ),
    }
    token_embeddings = _WORKER_SESSION.run(None, inputs)[0]
    mask = np.expand_dims(attention_mask, -1).astype(np.float32)
    pooled = np.sum(token_embeddings * mask, axis=1) / np.maximum(
        np.sum(mask, axis=1), 1e-9
    )
    vector = pooled[0]
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 0:
        raise RuntimeError("model produced an invalid embedding")
    return (vector / norm).astype(np.float32)


class UnionFind:
    """Deterministic union-find over normalized question strings."""

    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}
        self.rank: Dict[str, int] = {}

    def add(self, item: str) -> None:
        if item not in self.parent:
            self.parent[item] = item
            self.rank[item] = 0

    def find(self, item: str) -> str:
        self.add(item)
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        rank_left = self.rank[root_left]
        rank_right = self.rank[root_right]
        if rank_left < rank_right or (
            rank_left == rank_right and root_right < root_left
        ):
            root_left, root_right = root_right, root_left
            rank_left, rank_right = rank_right, rank_left
        self.parent[root_right] = root_left
        if rank_left == rank_right:
            self.rank[root_left] += 1


def normalize_question(text: Any) -> str:
    """Apply the documented identity normalization, not semantic rewriting."""

    if not isinstance(text, str):
        raise ValueError("QQP question text must be a string")
    return " ".join(unicodedata.normalize("NFKC", text).strip().split())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pairs(archive: Path) -> List[Dict[str, Any]]:
    """Read the single JSON member from GPTCache's bundled QQP tarball."""

    archive = Path(archive)
    observed_hash = sha256_file(archive)
    if observed_hash != QQP_ARCHIVE_SHA256:
        raise ValueError(
            "QQP archive checksum mismatch: expected %s, got %s"
            % (QQP_ARCHIVE_SHA256, observed_hash)
        )
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = [member for member in bundle.getmembers() if member.isfile()]
        if len(members) != 1:
            raise ValueError("expected exactly one file in the QQP archive")
        extracted = bundle.extractfile(members[0])
        if extracted is None:
            raise ValueError("could not read the QQP JSON member")
        records = json.load(extracted)
    if not isinstance(records, list):
        raise ValueError("QQP JSON root must be a list")
    return records


def prepare(archive: Path, output_dir: Path) -> Dict[str, Any]:
    """Create component-disjoint calibration/test manifests."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_pairs = load_pairs(archive)

    normalized: List[Tuple[str, str, int]] = []
    union_find = UnionFind()
    for index, record in enumerate(raw_pairs):
        if not isinstance(record, dict) or record.get("label") not in (0, 1):
            raise ValueError("invalid QQP record at index %d" % index)
        left = normalize_question(record.get("text_a"))
        right = normalize_question(record.get("text_b"))
        if not left or not right:
            raise ValueError("empty normalized question at index %d" % index)
        label = int(record["label"])
        normalized.append((left, right, label))
        union_find.add(left)
        union_find.add(right)
        if label == 1:
            union_find.union(left, right)

    members: Dict[str, List[str]] = {}
    for question in sorted(union_find.parent):
        members.setdefault(union_find.find(question), []).append(question)
    concept_by_question: Dict[str, str] = {}
    split_by_concept: Dict[str, str] = {}
    for component in sorted(members.values(), key=lambda values: values[0]):
        identity = hashlib.sha256("\n".join(component).encode("utf-8")).hexdigest()
        concept_id = "qqp-" + identity[:20]
        bucket = int(hashlib.sha256(concept_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        split = "calibration" if bucket < CALIBRATION_PERCENT else "test"
        split_by_concept[concept_id] = split
        for question in component:
            concept_by_question[question] = concept_id

    pair_rows = []
    contradictions = 0
    cross_split = 0
    counts = {
        "calibration_positive": 0,
        "calibration_negative": 0,
        "test_positive": 0,
        "test_negative": 0,
    }
    used_questions = set()
    for source_index, (left, right, label) in enumerate(normalized):
        left_concept = concept_by_question[left]
        right_concept = concept_by_question[right]
        if label == 0 and left_concept == right_concept:
            contradictions += 1
            continue
        left_split = split_by_concept[left_concept]
        right_split = split_by_concept[right_concept]
        if left_split != right_split:
            cross_split += 1
            continue
        split = left_split
        row = {
            "schema_version": SCHEMA_VERSION,
            "source_index": source_index,
            "split": split,
            "label": label,
            "text_a_id": _text_id(left),
            "text_b_id": _text_id(right),
            "concept_a": left_concept,
            "concept_b": right_concept,
        }
        pair_rows.append(row)
        counts["%s_%s" % (split, "positive" if label else "negative")] += 1
        used_questions.add(left)
        used_questions.add(right)

    questions_by_id = {_text_id(question): question for question in used_questions}
    if len(questions_by_id) != len(used_questions):
        raise RuntimeError("text ID collision in QQP preparation")
    text_rows = [
        {"text_id": text_id, "text": questions_by_id[text_id]}
        for text_id in sorted(questions_by_id)
    ]

    _write_jsonl(output_dir / "pairs.jsonl", pair_rows)
    _write_jsonl(output_dir / "texts.jsonl", text_rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "archive": str(Path(archive)),
        "archive_sha256": QQP_ARCHIVE_SHA256,
        "normalization": "Unicode NFKC, strip, collapse whitespace",
        "split_rule": "component SHA-256 bucket; 20 calibration / 80 test",
        "raw_pairs": len(raw_pairs),
        "positive_components": len(members),
        "unique_questions_raw": len(union_find.parent),
        "unique_questions_kept": len(text_rows),
        "kept_pairs": len(pair_rows),
        "contradictory_negative_pairs_dropped": contradictions,
        "cross_split_pairs_dropped": cross_split,
        "counts": counts,
        "pairs_sha256": sha256_file(output_dir / "pairs.jsonl"),
        "texts_sha256": sha256_file(output_dir / "texts.jsonl"),
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def embed(
    prepared_dir: Path,
    output_dir: Path,
    batch_size: int = 32,
    max_length: int = 512,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate resumable, normalized GPTCache ALBERT ONNX embeddings."""

    try:
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from transformers import AutoConfig, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "QQP embedding needs requirements-benchmark.txt"
        ) from exc

    prepared_dir = Path(prepared_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    text_rows = list(_read_jsonl(prepared_dir / "texts.jsonl"))
    if limit is not None:
        text_rows = text_rows[:limit]
    if not text_rows:
        raise ValueError("no QQP texts to embed")
    if batch_size < 1 or max_length < 8:
        raise ValueError("batch_size and max_length must be positive")

    tokenizer = AutoTokenizer.from_pretrained(
        TOKENIZER_REPOSITORY,
        revision=TOKENIZER_REVISION,
    )
    config = AutoConfig.from_pretrained(
        TOKENIZER_REPOSITORY,
        revision=TOKENIZER_REVISION,
    )
    model_path = hf_hub_download(
        repo_id=MODEL_REPOSITORY,
        filename="model.onnx",
        revision=MODEL_REVISION,
    )
    session_options = onnxruntime.SessionOptions()
    session_options.intra_op_num_threads = max(1, int(os.environ.get("CARMA_ONNX_THREADS", "1")))
    session_options.inter_op_num_threads = 1
    session = onnxruntime.InferenceSession(
        model_path,
        sess_options=session_options,
        providers=["CPUExecutionProvider"],
    )
    worker_count = max(1, int(os.environ.get("CARMA_ONNX_WORKERS", "1")))
    dimension = int(config.hidden_size)
    embeddings_path = output_dir / "embeddings.npy"
    ids_path = output_dir / "text_ids.json"
    progress_path = output_dir / "progress.json"
    text_ids = [row["text_id"] for row in text_rows]
    ids_hash = hashlib.sha256("\n".join(text_ids).encode("utf-8")).hexdigest()

    if embeddings_path.exists():
        matrix = np.load(embeddings_path, mmap_mode="r+")
        if matrix.shape != (len(text_rows), dimension):
            raise ValueError("existing embedding matrix has the wrong shape")
        saved_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        if saved_ids != text_ids:
            raise ValueError("existing embedding IDs do not match prepared texts")
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        start = int(progress["next_index"])
    else:
        matrix = np.lib.format.open_memmap(
            embeddings_path,
            mode="w+",
            dtype=np.float32,
            shape=(len(text_rows), dimension),
        )
        ids_path.write_text(json.dumps(text_ids, separators=(",", ":")), encoding="utf-8")
        start = 0
        _write_json(progress_path, {"next_index": 0, "text_ids_sha256": ids_hash})

    input_shapes = {item.name: tuple(item.shape) for item in session.get_inputs()}
    fixed_single_batch = all(shape and shape[0] == 1 for shape in input_shapes.values())
    fixed_sequence_lengths = {
        int(shape[1])
        for shape in input_shapes.values()
        if len(shape) > 1 and isinstance(shape[1], int)
    }
    if len(fixed_sequence_lengths) > 1:
        raise RuntimeError("ONNX inputs disagree on fixed sequence length")
    model_length = next(iter(fixed_sequence_lengths), max_length)
    if model_length != max_length:
        raise ValueError(
            "model requires max_length=%d; received %d" % (model_length, max_length)
        )

    executor = (
        concurrent.futures.ProcessPoolExecutor(
            max_workers=worker_count,
            initializer=_initialize_onnx_worker,
            initargs=(model_path, max_length),
        )
        if fixed_single_batch and worker_count > 1
        else None
    )
    try:
        for begin in range(start, len(text_rows), batch_size):
            end = min(len(text_rows), begin + batch_size)
            texts = [row["text"] for row in text_rows[begin:end]]
            if executor is not None:
                vectors = list(executor.map(_onnx_worker_embedding, texts, chunksize=1))
                pooled = np.stack(vectors)
                matrix[begin:end] = pooled
                matrix.flush()
                _write_json(
                    progress_path,
                    {"next_index": end, "text_ids_sha256": ids_hash},
                )
                continue
            encoded = tokenizer(
                texts,
                padding="max_length",
                truncation=True,
                max_length=max_length,
                return_tensors="np",
            )
            encoded_inputs = {
                "input_ids": np.asarray(encoded["input_ids"], dtype=np.int64),
                "attention_mask": np.asarray(encoded["attention_mask"], dtype=np.int64),
                "token_type_ids": np.asarray(
                    encoded.get("token_type_ids", np.zeros_like(encoded["input_ids"])),
                    dtype=np.int64,
                ),
            }
            if fixed_single_batch:
                def run_row(row_index: int) -> np.ndarray:
                    inputs = {
                        name: values[row_index : row_index + 1]
                        for name, values in encoded_inputs.items()
                    }
                    token_embeddings = session.run(None, inputs)[0]
                    mask = np.expand_dims(
                        inputs["attention_mask"], -1
                    ).astype(np.float32)
                    return np.sum(token_embeddings * mask, axis=1) / np.maximum(
                        np.sum(mask, axis=1), 1e-9
                    )

                pooled_rows = [run_row(i) for i in range(len(texts))]
                pooled = np.concatenate(pooled_rows, axis=0)
            else:
                inputs = {
                    name: values for name, values in encoded_inputs.items()
                }
                token_embeddings = session.run(None, inputs)[0]
                mask = np.expand_dims(inputs["attention_mask"], -1).astype(np.float32)
                pooled = np.sum(token_embeddings * mask, axis=1) / np.maximum(
                    np.sum(mask, axis=1), 1e-9
                )
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            if not np.all(np.isfinite(norms)) or np.any(norms <= 0):
                raise RuntimeError("model produced an invalid embedding")
            matrix[begin:end] = (pooled / norms).astype(np.float32)
            matrix.flush()
            _write_json(
                progress_path,
                {"next_index": end, "text_ids_sha256": ids_hash},
            )
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tokenizer_repository": TOKENIZER_REPOSITORY,
        "tokenizer_revision": TOKENIZER_REVISION,
        "model_repository": MODEL_REPOSITORY,
        "model_revision": MODEL_REVISION,
        "rows": len(text_rows),
        "dimension": dimension,
        "dtype": "float32",
        "normalized": True,
        "max_length": max_length,
        "onnx_workers": worker_count,
        "text_ids_sha256": ids_hash,
        "embeddings_sha256": sha256_file(embeddings_path),
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def calibrate(
    prepared_dir: Path,
    embeddings_dir: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    """Select a threshold on calibration only, then evaluate held-out QQP."""

    prepared_dir = Path(prepared_dir)
    embeddings_dir = Path(embeddings_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    text_ids = json.loads((embeddings_dir / "text_ids.json").read_text(encoding="utf-8"))
    matrix = np.load(embeddings_dir / "embeddings.npy", mmap_mode="r")
    if len(text_ids) != matrix.shape[0]:
        raise ValueError("embedding ID/matrix row mismatch")
    row_by_id = {text_id: index for index, text_id in enumerate(text_ids)}

    by_split: Dict[str, List[Tuple[float, int]]] = {
        "calibration": [],
        "test": [],
    }
    missing = 0
    for pair in _read_jsonl(prepared_dir / "pairs.jsonl"):
        try:
            left = row_by_id[pair["text_a_id"]]
            right = row_by_id[pair["text_b_id"]]
        except KeyError:
            missing += 1
            continue
        similarity = float(np.dot(matrix[left], matrix[right]))
        by_split[pair["split"]].append((similarity, int(pair["label"])))
    if missing:
        raise ValueError("%d prepared pairs lack embeddings" % missing)

    threshold_rows = []
    selected: Optional[float] = None
    for step in range(80, 100):
        threshold = step / 100.0
        metrics = _classification_metrics(by_split["calibration"], threshold)
        row = {"threshold": threshold, **metrics}
        threshold_rows.append(row)
        if selected is None and metrics["wilson_precision_lower_one_sided_95"] >= 0.99:
            selected = threshold
    _write_csv(output_dir / "calibration_thresholds.csv", threshold_rows)
    if selected is None:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "no_threshold_met_precision_gate",
            "selected_threshold": None,
            "calibration_pairs": len(by_split["calibration"]),
            "test_pairs_not_evaluated": len(by_split["test"]),
        }
        _write_json(output_dir / "result.json", result)
        return result

    calibration_metrics = _classification_metrics(by_split["calibration"], selected)
    test_metrics = _classification_metrics(by_split["test"], selected)
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "selected_threshold": selected,
        "selection_rule": "lowest 0.80..0.99 threshold with one-sided Wilson precision lower bound >= 0.99",
        "calibration": calibration_metrics,
        "test": test_metrics,
        "calibration_pairs": len(by_split["calibration"]),
        "test_pairs": len(by_split["test"]),
        "positive_similarity_quantiles": _quantiles(
            [score for score, label in by_split["test"] if label == 1]
        ),
        "negative_similarity_quantiles": _quantiles(
            [score for score, label in by_split["test"] if label == 0]
        ),
    }
    _write_json(output_dir / "result.json", result)
    return result


def _classification_metrics(
    scored_labels: Sequence[Tuple[float, int]], threshold: float
) -> Dict[str, Any]:
    true_positive = sum(1 for score, label in scored_labels if score >= threshold and label == 1)
    false_positive = sum(1 for score, label in scored_labels if score >= threshold and label == 0)
    false_negative = sum(1 for score, label in scored_labels if score < threshold and label == 1)
    true_negative = sum(1 for score, label in scored_labels if score < threshold and label == 0)
    predicted_positive = true_positive + false_positive
    positives = true_positive + false_negative
    precision = true_positive / predicted_positive if predicted_positive else 0.0
    recall = true_positive / positives if positives else 0.0
    return {
        "threshold": threshold,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "false_positive_rate": false_positive / max(1, false_positive + true_negative),
        "wilson_precision_lower_one_sided_95": _wilson_lower(
            true_positive, predicted_positive
        ),
    }


def _wilson_lower(successes: int, trials: int, z: float = 1.6448536269514722) -> float:
    if trials == 0:
        return 0.0
    proportion = successes / trials
    z_squared = z * z
    center = proportion + z_squared / (2 * trials)
    adjustment = z * math.sqrt(
        proportion * (1 - proportion) / trials
        + z_squared / (4 * trials * trials)
    )
    return max(0.0, (center - adjustment) / (1 + z_squared / trials))


def _quantiles(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {}
    array = np.asarray(values, dtype=np.float64)
    return {
        "p00": float(np.quantile(array, 0.00)),
        "p10": float(np.quantile(array, 0.10)),
        "p50": float(np.quantile(array, 0.50)),
        "p90": float(np.quantile(array, 0.90)),
        "p99": float(np.quantile(array, 0.99)),
        "p100": float(np.quantile(array, 1.00)),
    }


def _text_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("invalid JSON object at %s:%d" % (path, line_number))
                yield value


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8") as output:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write("\n")


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot write an empty CSV")
    with Path(path).open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--archive", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)

    embed_parser = subparsers.add_parser("embed")
    embed_parser.add_argument("--prepared", type=Path, required=True)
    embed_parser.add_argument("--output", type=Path, required=True)
    embed_parser.add_argument("--batch-size", type=int, default=32)
    embed_parser.add_argument("--max-length", type=int, default=512)
    embed_parser.add_argument("--limit", type=int, default=None)

    calibrate_parser = subparsers.add_parser("calibrate")
    calibrate_parser.add_argument("--prepared", type=Path, required=True)
    calibrate_parser.add_argument("--embeddings", type=Path, required=True)
    calibrate_parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.archive, args.output)
    elif args.command == "embed":
        result = embed(
            args.prepared,
            args.output,
            batch_size=args.batch_size,
            max_length=args.max_length,
            limit=args.limit,
        )
    else:
        result = calibrate(args.prepared, args.embeddings, args.output)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("status") != "no_threshold_met_precision_gate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
