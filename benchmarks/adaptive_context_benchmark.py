#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signalcore_runtime.adaptive_context import AdaptiveContextEngine, AdaptivePolicy, ToolObservation
from signalcore_runtime.evidence import EvidenceStore


def fixtures():
    diff = []
    for index in range(120): diff += [f"diff --git a/src/m{index}.py b/src/m{index}.py", f"--- a/src/m{index}.py", f"+++ b/src/m{index}.py", "@@ -1,3 +1,4 @@", f"-def calculate_{index}(value):", f"+def calculate_{index}(value: int) -> int:", f"+    # migration guard {index}", "     return value * 2"]
    tests = [f"tests/test_case_{index}.py ." for index in range(500)]
    for index in (33, 144, 311, 498): tests += ["================ FAILURES ================", f"> assert compute({index}) == {index+1}", f"E AssertionError: {index} != {index+1}", f"tests/test_case_{index}.py:42: AssertionError"]
    tests += ["4 failed, 496 passed in 12.44s"]
    issues = [{"id": index, "title": f"Issue {index}", "state": "open" if index % 3 else "closed", "labels": ["bug", f"team-{index%8}"], "body": f"Detailed reproduction {index}. " * 30, "comments": [{"author": f"u{j}", "body": f"comment {j} on {index}" * 8} for j in range(5)]} for index in range(300)]
    logs = []
    for index in range(5000):
        logs.append(f"2026-07-20T12:{index%60:02d}:00Z request={index} status={500 if index in (177,2088,4901) else 200} latency_ms={10+index%300} route=/api/{index%35}")
        if index == 2088: logs.append("FATAL auth refresh rejected at src/security.py:91 request=2088")
    code = ["from __future__ import annotations", "import os", "import json"]
    for index in range(900): code += [f"def handler_{index}(value: int) -> int:", f"    # TODO optimize handler {index}" if index % 200 == 0 else "    value += 1", "    return value", ""]
    search = [f"src/package_{index%40}/module_{index}.py:{index%200+1}: target_symbol_{index}" for index in range(2200)]
    return [
        ("git-diff", ToolObservation(command="git diff --cached", stdout="\n".join(diff), scope_key="bench"), "calculate_119"),
        ("pytest", ToolObservation(command="cd repo && pytest -q", stdout="\n".join(tests), scope_key="bench"), "test_case_311 AssertionError"),
        ("json-issues", ToolObservation(command="gh issue list --json", stdout=json.dumps(issues), path="issues.json", scope_key="bench"), "Issue 287 team-7"),
        ("service-log", ToolObservation(command="service logs", stdout="\n".join(logs), path="service.log", scope_key="bench"), "auth refresh security.py"),
        ("code-read", ToolObservation(command="cat src/handlers.py", stdout="\n".join(code), path="src/handlers.py", tool_name="read", scope_key="bench"), "handler_799"),
        ("search-list", ToolObservation(command="rg target_symbol src", stdout="\n".join(search), scope_key="bench"), "target_symbol_2101"),
        ("small", ToolObservation(command="git status", stdout="On branch main\nnothing to commit, working tree clean\n", scope_key="bench"), "main"),
    ]


def run_profile(profile: str):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); engine = AdaptiveContextEngine(root / "adaptive.db", evidence=EvidenceStore(root / "evidence", project_id=f"bench-{profile}"), policy=AdaptivePolicy.for_profile(profile)); rows = []
        tracemalloc.start(); started = time.perf_counter()
        for name, observation, query in fixtures():
            result = engine.process(observation); verification = engine.verify(result.capture_id); hits = engine.search(result.capture_id, query)
            rows.append({"name": name, "family": result.family, "mode": result.mode, "raw_bytes": result.original_bytes, "visible_bytes": result.visible_bytes, "savings_ratio": result.savings_ratio, "quality_gate": result.quality_gate_passed, "roundtrip": engine.restore(result.capture_id) == observation.text.encode(), "verification_reasons": verification["reasons"], "search_hit": bool(hits), "chunks": result.chunk_count})
        repeat = fixtures()[4][1]; first = engine.process(ToolObservation(**{**repeat.__dict__, "scope_key": "repeat"})); second = engine.process(ToolObservation(**{**repeat.__dict__, "scope_key": "repeat"}))
        elapsed = time.perf_counter() - started; _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop(); total_raw = sum(row["raw_bytes"] for row in rows) + first.original_bytes; total_visible = sum(row["visible_bytes"] for row in rows) + second.visible_bytes
        return {"profile": profile, "rows": rows, "aggregate": {"raw_bytes": total_raw, "visible_bytes": total_visible, "savings_ratio": 1 - total_visible / max(1, total_raw), "all_quality_gates": all(row["quality_gate"] for row in rows), "all_roundtrips": all(row["roundtrip"] for row in rows), "all_search_queries_found": all(row["search_hit"] for row in rows), "repeat_mode": second.mode, "repeat_visible_bytes": second.visible_bytes, "seconds": elapsed, "peak_python_bytes": peak}, "engine_stats": engine.stats()}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output"); args = parser.parse_args()
    result = {"schema_version": 1, "boundary": "Internal deterministic mixed-fixture benchmark; no competitor or live provider arm was executed.", "claim": "SUPERIORITY_NOT_PROVEN", "profiles": [run_profile(name) for name in ("compact", "balanced", "audit")]}
    if args.output: Path(args.output).parent.mkdir(parents=True, exist_ok=True); Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True)); return 0 if all(profile["aggregate"]["all_quality_gates"] and profile["aggregate"]["all_roundtrips"] and profile["aggregate"]["all_search_queries_found"] for profile in result["profiles"]) else 3


if __name__ == "__main__": raise SystemExit(main())
