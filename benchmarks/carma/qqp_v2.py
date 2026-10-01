"""Strict two-pass QQP threshold selection and held-out evaluation.

Version 1 correctly avoided reporting held-out metrics when no threshold met
the calibration rule, but it still resolved and scored every held-out pair
before making that decision.  This module makes the protocol observable:
the first pass dereferences calibration endpoints only, writes and hash-binds
the selection transcript, and opens the pair file a second time only when a
threshold was selected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from benchmarks.carma import qqp


SCHEMA_VERSION = "carma-qqp-v2"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SELECTION_ARTIFACT_NAME = "threshold-selection.json"
THRESHOLD_TABLE_NAME = "calibration-thresholds.csv"
RESULT_NAME = "result.json"
THRESHOLDS = tuple(step / 100.0 for step in range(80, 100))
MIN_WILSON_PRECISION_LOWER = 0.99
SELECTION_RULE = (
    "lowest threshold in the frozen 0.80..0.99 step-0.01 grid whose "
    "one-sided 95% Wilson precision lower bound is at least 0.99"
)


def _logical_path(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def _identity(path: Path, logical_path: Optional[str] = None) -> Dict[str, Any]:
    path = Path(path)
    return {
        "path": logical_path if logical_path is not None else _logical_path(path),
        "bytes": path.stat().st_size,
        "sha256": qqp.sha256_file(path),
    }


def _optional_identity(path: Path) -> Optional[Dict[str, Any]]:
    return _identity(path) if Path(path).is_file() else None


def _input_identities(
    prepared_dir: Path, embeddings_dir: Path
) -> Dict[str, Any]:
    prepared_dir = Path(prepared_dir)
    embeddings_dir = Path(embeddings_dir)
    return {
        "prepared_pairs": _identity(prepared_dir / "pairs.jsonl"),
        "prepared_manifest": _optional_identity(prepared_dir / "manifest.json"),
        "prepared_texts": _optional_identity(prepared_dir / "texts.jsonl"),
        "embedding_text_ids": _identity(embeddings_dir / "text_ids.json"),
        "embedding_matrix": _identity(embeddings_dir / "embeddings.npy"),
        "embedding_manifest": _optional_identity(embeddings_dir / "manifest.json"),
    }


def _load_embedding_index(
    embeddings_dir: Path,
) -> Tuple[np.ndarray, Dict[str, int], Dict[str, Any]]:
    embeddings_dir = Path(embeddings_dir)
    ids_path = embeddings_dir / "text_ids.json"
    matrix_path = embeddings_dir / "embeddings.npy"
    text_ids = json.loads(ids_path.read_text(encoding="utf-8"))
    if not isinstance(text_ids, list) or not all(
        isinstance(value, str) and value for value in text_ids
    ):
        raise ValueError("embedding text_ids.json must be a list of non-empty strings")
    if len(set(text_ids)) != len(text_ids):
        raise ValueError("embedding text IDs are not unique")
    matrix = np.load(matrix_path, mmap_mode="r")
    if matrix.ndim != 2 or len(text_ids) != matrix.shape[0]:
        raise ValueError("embedding ID/matrix row mismatch")
    identities = {
        "embedding_text_ids": _identity(ids_path),
        "embedding_matrix": _identity(matrix_path),
        "embedding_manifest": _optional_identity(embeddings_dir / "manifest.json"),
    }
    return matrix, {value: index for index, value in enumerate(text_ids)}, identities


def _score_calibration_pair(
    pair: Dict[str, Any],
    row_by_id: Dict[str, int],
    matrix: np.ndarray,
    line_number: int,
) -> Tuple[float, int]:
    label = pair.get("label")
    if label not in (0, 1):
        raise ValueError("invalid calibration label at pair row %d" % line_number)
    try:
        left_id = pair["text_a_id"]
        right_id = pair["text_b_id"]
    except KeyError as exc:
        raise ValueError(
            "calibration pair row %d lacks endpoint IDs" % line_number
        ) from exc
    if not isinstance(left_id, str) or not isinstance(right_id, str):
        raise ValueError("invalid calibration endpoint ID at pair row %d" % line_number)
    try:
        left = row_by_id[left_id]
        right = row_by_id[right_id]
    except KeyError as exc:
        raise ValueError(
            "calibration pair row %d lacks an embedding" % line_number
        ) from exc
    return float(np.dot(matrix[left], matrix[right])), int(label)


def _score_heldout_pair(
    pair: Dict[str, Any],
    row_by_id: Dict[str, int],
    matrix: np.ndarray,
    line_number: int,
) -> Tuple[float, int]:
    label = pair.get("label")
    if label not in (0, 1):
        raise ValueError("invalid held-out label at pair row %d" % line_number)
    try:
        left_id = pair["text_a_id"]
        right_id = pair["text_b_id"]
    except KeyError as exc:
        raise ValueError("held-out pair row %d lacks endpoint IDs" % line_number) from exc
    if not isinstance(left_id, str) or not isinstance(right_id, str):
        raise ValueError("invalid held-out endpoint ID at pair row %d" % line_number)
    try:
        left = row_by_id[left_id]
        right = row_by_id[right_id]
    except KeyError as exc:
        raise ValueError("held-out pair row %d lacks an embedding" % line_number) from exc
    return float(np.dot(matrix[left], matrix[right])), int(label)


def _metrics(
    scored_labels: Sequence[Tuple[float, int]], threshold: float
) -> Dict[str, Any]:
    value = dict(qqp._classification_metrics(scored_labels, threshold))
    false_hits = int(value["false_positive"])
    trials = len(scored_labels)
    value["false_hit_rate"] = false_hits / trials if trials else 0.0
    value["false_hit_rate_definition"] = "FP / (TP + FP + TN + FN)"
    value["wilson_false_hit_rate_upper_one_sided_95"] = (
        1.0 - qqp._wilson_lower(trials - false_hits, trials)
        if trials
        else 0.0
    )
    return value


def _write_selection(
    output_dir: Path,
    selection: Dict[str, Any],
) -> Dict[str, Any]:
    path = Path(output_dir) / SELECTION_ARTIFACT_NAME
    qqp._write_json(path, selection)
    return _identity(path, SELECTION_ARTIFACT_NAME)


def calibrate(
    prepared_dir: Path,
    embeddings_dir: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    """Select on calibration, then conditionally evaluate held-out rows.

    The split field is the only held-out row field read in pass one.  In
    particular, held-out labels and endpoint IDs are not dereferenced until a
    selection transcript has been written and a threshold exists.
    """

    prepared_dir = Path(prepared_dir)
    embeddings_dir = Path(embeddings_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pairs_path = prepared_dir / "pairs.jsonl"
    matrix, row_by_id, embedding_identities = _load_embedding_index(embeddings_dir)
    input_identities = {
        "prepared_pairs": _identity(pairs_path),
        "prepared_manifest": _optional_identity(prepared_dir / "manifest.json"),
        "prepared_texts": _optional_identity(prepared_dir / "texts.jsonl"),
        **embedding_identities,
    }

    calibration: List[Tuple[float, int]] = []
    heldout_rows_discovered = 0
    for line_number, pair in enumerate(qqp._read_jsonl(pairs_path), 1):
        split = pair.get("split")
        if split == "test":
            # Protocol-critical: do not read label, endpoint IDs, concepts, or
            # embeddings from this row before threshold selection is frozen.
            heldout_rows_discovered += 1
            continue
        if split != "calibration":
            raise ValueError("invalid split at pair row %d" % line_number)
        calibration.append(
            _score_calibration_pair(pair, row_by_id, matrix, line_number)
        )
    if not calibration:
        raise ValueError("no calibration pairs were available")
    if _input_identities(prepared_dir, embeddings_dir) != input_identities:
        raise RuntimeError("QQP inputs changed during calibration selection")

    threshold_rows: List[Dict[str, Any]] = []
    selected: Optional[float] = None
    for threshold in THRESHOLDS:
        metrics = _metrics(calibration, threshold)
        threshold_rows.append(metrics)
        if (
            selected is None
            and metrics["wilson_precision_lower_one_sided_95"]
            >= MIN_WILSON_PRECISION_LOWER
        ):
            selected = threshold
    threshold_path = output_dir / THRESHOLD_TABLE_NAME
    qqp._write_csv(threshold_path, threshold_rows)

    selection_status = "selected" if selected is not None else "no_threshold_selected"
    selection = {
        "schema_version": SCHEMA_VERSION,
        "kind": "qqp_threshold_selection_transcript",
        "status": selection_status,
        "selection_rule": SELECTION_RULE,
        "threshold_grid": list(THRESHOLDS),
        "minimum_wilson_precision_lower": MIN_WILSON_PRECISION_LOWER,
        "selected_threshold": selected,
        "selected_calibration_metrics": (
            _metrics(calibration, selected) if selected is not None else None
        ),
        "calibration_pairs": len(calibration),
        "calibration_endpoints_resolved": True,
        "calibration_similarities_computed": len(calibration),
        "heldout_rows_discovered_by_split_only_scan": heldout_rows_discovered,
        "heldout_row_fields_read_before_selection": ["split"],
        "heldout_endpoints_resolved": False,
        "heldout_similarities_computed": False,
        "heldout_similarity_count": 0,
        "selection_uses_heldout": False,
        "embedding_matrix_scope": "precomputed full prepared-text corpus",
        "selection_embedding_access_scope": "calibration pair endpoints only",
        "calibration_thresholds": _identity(
            threshold_path, THRESHOLD_TABLE_NAME
        ),
        "inputs": input_identities,
    }
    selection_identity = _write_selection(output_dir, selection)

    common = {
        "schema_version": SCHEMA_VERSION,
        "selection_rule": SELECTION_RULE,
        "selected_threshold": selected,
        "calibration": _metrics(calibration, selected) if selected is not None else None,
        "calibration_pairs": len(calibration),
        "heldout_rows_discovered_by_split_only_scan": heldout_rows_discovered,
        "selection_uses_heldout": False,
        "selection_transcript": selection_identity,
        "calibration_thresholds": _identity(
            threshold_path, THRESHOLD_TABLE_NAME
        ),
        "inputs": input_identities,
    }
    if selected is None:
        result = {
            **common,
            "status": "no_threshold_met_precision_gate",
            "test_pairs_not_evaluated": heldout_rows_discovered,
            "heldout_endpoints_resolved": False,
            "heldout_similarities_computed": False,
            "heldout_similarity_count": 0,
        }
        qqp._write_json(output_dir / RESULT_NAME, result)
        return result

    # The selection identity is now written and held constant for this
    # invocation. Reopen the pair stream and evaluate only the held-out rows;
    # calibration fields are not read in pass two.
    heldout: List[Tuple[float, int]] = []
    for line_number, pair in enumerate(qqp._read_jsonl(pairs_path), 1):
        split = pair.get("split")
        if split == "calibration":
            continue
        if split != "test":
            raise ValueError("invalid split at pair row %d" % line_number)
        heldout.append(_score_heldout_pair(pair, row_by_id, matrix, line_number))
    if len(heldout) != heldout_rows_discovered:
        raise RuntimeError("held-out row count changed after threshold selection")
    if _input_identities(prepared_dir, embeddings_dir) != input_identities:
        raise RuntimeError("QQP inputs changed during held-out evaluation")
    if (
        _identity(
            output_dir / SELECTION_ARTIFACT_NAME,
            SELECTION_ARTIFACT_NAME,
        )
        != selection_identity
    ):
        raise RuntimeError("selection transcript changed during held-out evaluation")

    result = {
        **common,
        "status": "ok",
        "test": _metrics(heldout, selected),
        "test_pairs": len(heldout),
        "heldout_endpoints_resolved": True,
        "heldout_similarities_computed": True,
        "heldout_similarity_count": len(heldout),
        "positive_similarity_quantiles": qqp._quantiles(
            [score for score, label in heldout if label == 1]
        ),
        "negative_similarity_quantiles": qqp._quantiles(
            [score for score, label in heldout if label == 0]
        ),
    }
    qqp._write_json(output_dir / RESULT_NAME, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--embeddings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = calibrate(args.prepared, args.embeddings, args.output)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
