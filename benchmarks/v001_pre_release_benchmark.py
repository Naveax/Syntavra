from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from syntavra_runtime.infinite_context import RecursiveExecutionEngine, RecursiveTask, UnboundedContextCoordinator
from syntavra_runtime.integration_matrix import IntegrationMatrix
from syntavra_runtime.public_proof import PublicProofGate
from syntavra_runtime.release_identity import identity
from syntavra_runtime.paired_benchmark import CodingCorpusPlanner, PairedSchedule, default_arms
from syntavra_runtime.semantic_structure import GraphEdge, GraphNode, SemanticGraph


def run() -> dict:
    started = time.perf_counter()
    tiers = UnboundedContextCoordinator.stress_tiers(active_budget=4096)
    tasks = CodingCorpusPlanner.generate_slots()
    schedule = PairedSchedule(tasks, default_arms(), repetitions=30)
    graph = SemanticGraph()
    for index in range(1000):
        node_id = f"n{index}"
        graph.add_node(GraphNode(node_id, "function", f"pkg.symbol_{index}", f"src/mod_{index % 50}.py", index + 1, index + 2, "python", f"sc://evidence/{node_id}", (index % 100) / 100))
        if index:
            graph.add_edge(GraphEdge(f"n{index - 1}", node_id, "calls", evidence_ref=f"sc://edge/{index}"))
    query_started = time.perf_counter()
    query = graph.query("symbol 999", limit=10)
    query_ms = (time.perf_counter() - query_started) * 1000
    recursive = RecursiveExecutionEngine(workers=8).execute(
        [RecursiveTask(str(index), index) for index in range(256)],
        lambda task: task.payload * task.payload,
        lambda rows: sum(row.output for row in rows),
    )
    result = {
        "identity": identity().to_dict(),
        "integration_coverage": IntegrationMatrix.validate(),
        "signalbench2": {"tasks": len(tasks), "arms": len(default_arms()), "repetitions": 30, "scheduled_runs": schedule.count, "claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN"},
        "infinite_context": {"tiers": tiers, "max_tier": max(row["tier_tokens"] for row in tiers), "all_passed": all(row["within_budget"] and row["all_referenced"] and not row["forced_restart"] for row in tiers)},
        "semantic_structure": {"nodes": len(graph.nodes), "edges": sum(map(len, graph.outbound.values())), "query_ms": query_ms, "top_result": query[0].node.node_id if query else None},
        "recursive_execution": {"tasks": recursive["tasks_executed"], "duplicates_suppressed": recursive["duplicates_suppressed"], "provenance": recursive["global_provenance_hash"]},
        "public_proof": {"workloads": PublicProofGate.workload_manifest()["workload_count"], "maturity": "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN"},
        "elapsed_ms": (time.perf_counter() - started) * 1000,
    }
    result["ok"] = result["integration_coverage"]["ok"] and result["infinite_context"]["all_passed"] and result["signalbench2"]["tasks"] >= 150
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run()
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
