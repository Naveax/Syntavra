from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from signalcore_runtime.long_session_planner import ContextPlanPolicy, LongSessionPlanner
from signalcore_runtime.session_runtime import SessionRuntime
from signalcore_runtime.util import atomic_write_json, sha256_bytes


def run(*, events: int = 2048, token_budget: int = 4096) -> dict:
    if events < 128:
        raise ValueError("events must be at least 128")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="signalcore-long-session-v4-") as temp:
        runtime = SessionRuntime(Path(temp) / "sessions.sqlite3", project_id="benchmark-project")
        session = runtime.create_session(session_id="bench-long-session")
        append_started = time.perf_counter()
        decision_version = 0
        raw_bytes = 0
        for index in range(1, events + 1):
            if index % 97 == 0:
                decision_version += 1
                payload = {
                    "subject": "authentication-refresh",
                    "decision": f"use refresh strategy v{decision_version}",
                    "decision_id": f"auth-v{decision_version}",
                }
                if decision_version > 1:
                    payload["supersedes"] = f"auth-v{decision_version - 1}"
                event_type = "decision"
            elif index % 113 == 0:
                payload = {
                    "error": f"transient provider failure {index}",
                    "path": f"src/provider_{index % 11}.py:{index % 300 + 1}",
                }
                event_type = "error"
            else:
                payload = {
                    "task": f"inspect module {index % 37}",
                    "result": f"verified batch {index}",
                    "path": f"src/module_{index % 37}.py:{index % 250 + 1}",
                    "sequence_hint": index,
                }
                event_type = "observation"
            raw_bytes += len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            runtime.append(session.session_id, event_type, payload)
        append_seconds = time.perf_counter() - append_started

        compact_started = time.perf_counter()
        root = runtime.compact(session.session_id, leaf_size=32, fanout=8, force=True)
        compact_seconds = time.perf_counter() - compact_started

        planner = LongSessionPlanner(runtime)
        policy = ContextPlanPolicy(
            token_budget=token_budget,
            recent_events=24,
            summary_preview_chars=4096,
            event_preview_chars=768,
            max_candidates=events,
        )
        queries = (
            "what is the current authentication refresh decision",
            "find provider failures and exact source paths",
            "summarize verified module work",
            "show the most recent critical decision and errors",
            "which module 17 observations matter",
        )
        stress = planner.stress_report(session.session_id, queries, policy=policy)
        representative = planner.plan(session.session_id, queries[0], policy=policy)
        visible_bytes = len(json.dumps(representative["sections"], ensure_ascii=False).encode("utf-8"))
        verification = runtime.verify(session.session_id)
        latest_decisions = [
            section
            for section in representative["sections"]
            if "auth-v" in section["text"] and section["temporal_status"] == "current"
        ]

        result = {
            "schema_version": 1,
            "claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "workload": {
                "events": events,
                "queries": len(queries),
                "token_budget": token_budget,
                "raw_payload_bytes": raw_bytes,
            },
            "quality": {
                "chain_ok": verification["ok"],
                "exact_history_events": verification["events"],
                "root_summary_id": root,
                "all_exactly_referenced": stress["all_exactly_referenced"],
                "all_within_budget": stress["all_within_budget"],
                "current_decision_selected": bool(latest_decisions),
            },
            "efficiency": {
                "append_seconds": append_seconds,
                "compact_seconds": compact_seconds,
                "p95_planning_ms": stress["p95_planning_ms"],
                "representative_visible_bytes": visible_bytes,
                "visible_byte_reduction": 1.0 - (visible_bytes / max(1, raw_bytes)),
            },
            "stress": stress,
            "boundary": (
                "This benchmark measures SignalCore internal long-session mechanisms. "
                "It does not compare external products or provider task quality."
            ),
            "seconds": time.perf_counter() - started,
        }
        result["result_hash"] = sha256_bytes(
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=2048)
    parser.add_argument("--token-budget", type=int, default=4096)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run(events=args.events, token_budget=args.token_budget)
    if args.output:
        atomic_write_json(Path(args.output), result, mode=0o644)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(result["quality"].values()) else 3


if __name__ == "__main__":
    raise SystemExit(main())
