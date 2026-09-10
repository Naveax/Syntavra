#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json

from syntavra_runtime.agent_retrieval import QueryPushdownEngine


ROWS = 50
SECRET_PAD = 20_000


class Graph:
    def __init__(self, rows):
        self.rows = list(rows)

    def query(self, query: str, *, limit: int = 64):
        return [dict(row, query_seen=query) for row in self.rows[:limit]]


class ExactCapture:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def __call__(self, raw: bytes, metadata):
        digest = hashlib.sha256(raw).hexdigest()
        handle = "sc://sha256/" + digest
        self.objects[handle] = bytes(raw)
        return {"artifact_id": "capture-" + digest[:24], "exact_handle": handle}


def run() -> dict[str, object]:
    rows = [
        {
            "node_id": f"n{index}",
            "name": f"symbol{index}",
            "path": f"src/pkg_{index % 5}/module_{index}.py",
            "kind": "function" if index % 2 == 0 else "class",
            "language": "python",
            "start_line": index * 7 + 1,
            "end_line": index * 7 + 5,
            "score": index / 100.0,
            "metadata_json": "INTERNAL_ONLY_" + ("x" * SECRET_PAD),
        }
        for index in range(ROWS)
    ]
    graph = Graph(rows)
    capture = ExactCapture()
    engine = QueryPushdownEngine(graph, max_limit=64, default_limit=8)

    raw_provider_baseline = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    reduced = engine.reduce(
        "symbol",
        operator="group",
        group_by="kind",
        fields=["kind"],
        capture=capture,
        limit=8,
    )
    provider_candidate = json.dumps(
        {
            "query": reduced.query,
            "operator": reduced.operator,
            "value": reduced.value,
            "source_window_complete": reduced.source_window_complete,
            "source_artifact": reduced.exact_artifact_id,
            "source_exact": reduced.exact_handle,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    exact = capture.objects.get(reduced.exact_handle, b"")
    reduction_ratio = 1.0 - len(provider_candidate) / max(1, len(raw_provider_baseline))
    gates = {
        "source_window_complete": reduced.source_window_complete,
        "exact_raw_capture_present": bool(exact),
        "forbidden_metadata_not_provider_visible": b"INTERNAL_ONLY_" not in provider_candidate,
        "candidate_smaller_than_raw": len(provider_candidate) < len(raw_provider_baseline),
        "local_structural_reduction_above_90pct": reduction_ratio > 0.90,
        "no_provider_savings_claim": True,
    }
    report = {
        "schema_version": 1,
        "family": "syntavra-te-p0-08-query-pushdown-reduction",
        "claim_boundary": "LOCAL_STRUCTURAL_RETRIEVAL_REGRESSION_ONLY_NOT_PROVIDER_BILLED_PROOF",
        "rows": ROWS,
        "raw_provider_baseline_bytes": len(raw_provider_baseline),
        "candidate_provider_bytes": len(provider_candidate),
        "exact_capture_bytes": len(exact),
        "local_structural_byte_reduction_ratio": reduction_ratio,
        "result": reduced.value,
        "gates": gates,
    }
    if not all(gates.values()):
        raise AssertionError(report)
    return report


def main() -> int:
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
