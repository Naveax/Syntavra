#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, TypeVar

from syntavra_runtime.platform import ContextIRItem, SyntavraPlatform

T = TypeVar("T")


def timed(callable_: Callable[[], T]) -> tuple[T, float]:
    started = time.perf_counter()
    value = callable_()
    return value, (time.perf_counter() - started) * 1000.0


def run(*, scale: int = 1) -> dict[str, Any]:
    """Run an internal functional component measurement.

    This is not an external competitor benchmark. ``scale`` only expands the
    synthetic repository and event counts for release smoke or local stress runs.
    """

    scale = max(1, int(scale))
    module_count = 60 * scale
    repeated_log_lines = 5000 * scale
    event_count = 200 * scale
    decision_count = 1000 * scale

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        project = root / "repo"
        project.mkdir()
        for index in range(module_count):
            next_name = f"f{index + 1}" if index < module_count - 1 else "leaf"
            (project / f"module_{index}.py").write_text(
                f"def f{index}(value):\n    return {next_name}(value) if value else {index}\n",
                encoding="utf-8",
            )
        runtime = SyntavraPlatform(project, root / "state")

        log = "\n".join(["test_example ... ok"] * repeated_log_lines + ["FAILED tests/test_example.py:42 assertion error"])
        firewall, firewall_ms = timed(lambda: runtime.firewall.capture("pytest", log, exit_code=1))

        items = [
            ContextIRItem("system", "system", "text", "system", "Safety policy\n" * 100, 1.0, True),
            ContextIRItem("repo", "repository", "source", "repo-map", "\n".join(f"module_{i}.py f{i}" for i in range(module_count)) * 20, 0.8, True),
            ContextIRItem("tool", "task", "diagnostic", "pytest", log, 1.0, False),
            ContextIRItem("user", "user", "text", "user", "Fix the failing assertion", 1.0, False),
        ]
        context, context_ms = timed(lambda: runtime.context.compile(items, provider="openai", model="benchmark", budget_tokens=4096))
        raw_tokens = sum(max(1, len(item.content.encode("utf-8")) // 4) for item in items)

        graph, graph_ms = timed(lambda: runtime.graph.index_repository(project))
        query, query_ms = timed(lambda: runtime.graph.query("f42", limit=10))

        session = runtime.memory.open("benchmark", metadata={"goal": "repair"})
        for index in range(event_count):
            kind = "test-failure" if index % 19 == 0 else "decision" if index % 7 == 0 else "change"
            runtime.memory.append(
                session["session_id"],
                kind,
                {"index": index, "file": f"module_{index % module_count}.py", "error": "boom" if kind == "test-failure" else ""},
            )
        compact, compact_ms = timed(lambda: runtime.memory.compact(session["session_id"]))
        retrieval, retrieval_ms = timed(lambda: runtime.memory.retrieve(session["session_id"], "boom module_19"))

        decisions, decision_ms = timed(lambda: [
            runtime.security.decide("terminal.exec", {"argv": ["pytest", "-q"]}, sandboxed=True, user_authorized=True)
            for _ in range(decision_count)
        ])

        return {
            "product": "Syntavra",
            "version": "0.0.1",
            "channel": "pre-release",
            "claim": "INTERNAL_FUNCTIONAL_MEASUREMENT_ONLY",
            "external_superiority": False,
            "scale": scale,
            "firewall": {**asdict(firewall), "wall_time_ms": firewall_ms},
            "context": {
                "raw_estimated_tokens": raw_tokens,
                "compiled_tokens": context.used_tokens,
                "reduction_ratio": 1.0 - context.used_tokens / max(1, raw_tokens),
                "omitted_items": len(context.omitted),
                "artifact_count": len(context.artifacts),
                "wall_time_ms": context_ms,
            },
            "graph": {
                **graph,
                "query_results": len(query),
                "index_wall_time_ms": graph_ms,
                "query_wall_time_ms": query_ms,
            },
            "memory": {
                "events": compact["events"],
                "summary_views": len(compact["summaries"]),
                "retrieval_results": len(retrieval["results"]),
                "compact_wall_time_ms": compact_ms,
                "retrieval_wall_time_ms": retrieval_ms,
                "exact_recovery": retrieval["exact_recovery"],
            },
            "security": {
                "decisions": len(decisions),
                "allowed": sum(item.allowed for item in decisions),
                "wall_time_ms": decision_ms,
            },
            "adapters": runtime.status()["adapters"],
        }


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
