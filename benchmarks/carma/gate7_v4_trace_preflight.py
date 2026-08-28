"""Read-only trace/semantic identity preflight for the Gate 7 v4 protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from benchmarks.carma import gate7_v4_onnx_integration_benchmark as gate7
from benchmarks.carma.gate7_v2_trace import (
    build_gate7_semantic_index,
    build_gate7_trace,
)


def compute_identities(prepared: Path = gate7.DEFAULT_PREPARED) -> Dict[str, Any]:
    """Regenerate all frozen v4 identities without running a policy child."""

    prepared = Path(prepared).resolve()
    gate7._prepared_input_identities(prepared, formal=True)
    identities: Dict[str, Any] = {}
    for seed in gate7.FULL_SEEDS:
        trace, trace_sha256, metadata = build_gate7_trace(
            prepared,
            seed=seed,
            requests=3000,
            capacity=100,
        )
        selected_concepts = sorted({str(row.concept_id) for row in trace})
        semantic_index = build_gate7_semantic_index(
            prepared, selected_concepts
        )
        if metadata.get("semantic_index_sha256") != semantic_index.sha256:
            raise RuntimeError(
                "trace metadata and semantic index disagree for seed %d" % seed
            )
        observed = {
            "trace_sha256": trace_sha256,
            "semantic_index_sha256": semantic_index.sha256,
            "request_rows": len(trace),
            "selected_concepts": len(selected_concepts),
        }
        expected = {
            "trace_sha256": gate7.PINNED_FULL_TRACE_SHA256[seed],
            "semantic_index_sha256": (
                gate7.PINNED_FULL_SEMANTIC_INDEX_SHA256[seed]
            ),
        }
        if any(observed[key] != value for key, value in expected.items()):
            raise RuntimeError(
                "frozen trace identity mismatch for seed %d" % seed
            )
        identities[str(seed)] = observed
    return {
        "schema_version": "carma-gate7-v4-trace-preflight-v1",
        "status": "verified",
        "policy_children_executed": 0,
        "requests_per_trace": 3000,
        "capacity": 100,
        "seeds": list(gate7.FULL_SEEDS),
        "identities": identities,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared",
        type=Path,
        default=gate7.DEFAULT_PREPARED,
        help="frozen prepared QQP directory",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(compute_identities(args.prepared), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
