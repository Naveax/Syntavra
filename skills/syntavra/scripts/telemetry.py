#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from common import dump_json
from posterior import BetaPosterior
from store import project_id, transaction, connect


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    money_cost: float = 0.0
    tool_calls: int = 0
    retries: int = 0

    @property
    def active_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_tokens) + self.output_tokens + self.reasoning_tokens


def normalize_usage(payload: dict[str, Any]) -> ProviderUsage:
    # Handles OpenAI/Codex/Anthropic/generic usage shapes without provider SDK dependencies.
    usage = payload.get("usage", payload)
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    reasoning_tokens = int(usage.get("reasoning_tokens", 0) or 0)
    cached = int(usage.get("cached_input_tokens", usage.get("cached_tokens", 0)) or 0)
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details", {})) or {}
    cached = max(cached, int(details.get("cached_tokens", details.get("cache_read_input_tokens", 0)) or 0))
    return ProviderUsage(
        input_tokens=input_tokens,
        cached_tokens=cached,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        money_cost=float(usage.get("money_cost", usage.get("cost_usd", usage.get("total_cost", 0.0))) or 0.0),
        tool_calls=int(usage.get("tool_calls", 0) or 0),
        retries=int(usage.get("retry_count", usage.get("retries", 0)) or 0),
    )


def record_task(project: str | Path, event: dict[str, Any]) -> str:
    event_id = str(event.get("id") or uuid.uuid4())
    with transaction(project) as con:
        con.execute(
            """
            INSERT OR REPLACE INTO task_events(id,task_hash,task_family,model,platform,repository_fingerprint,profile,success,active_tokens,latency_ms,money_cost,evidence_coverage,duplicate_ratio,retries,created,metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id, str(event["task_hash"]), str(event["task_family"]), str(event.get("model", "unknown")),
                str(event.get("platform", "unknown")), str(event.get("repository_fingerprint", "")), str(event["profile"]),
                1 if event.get("success") else 0, int(event.get("active_tokens", 0)), float(event.get("latency_ms", 0)),
                float(event.get("money_cost", 0)), float(event.get("evidence_coverage", 0)), float(event.get("duplicate_ratio", 0)),
                int(event.get("retries", 0)), float(event.get("created", time.time())),
                json.dumps(event.get("metadata", {}), ensure_ascii=False, sort_keys=True),
            ),
        )
    return event_id


def record_tool(project: str | Path, event: dict[str, Any]) -> str:
    event_id = str(event.get("id") or uuid.uuid4())
    with transaction(project) as con:
        con.execute(
            """
            INSERT OR REPLACE INTO tool_events(id,task_id,task_family,profile,engine,tool,success,input_tokens,cached_tokens,output_tokens,reasoning_tokens,latency_ms,money_cost,useful_evidence,duplicate_evidence,retry_generated,credit,created,metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id, str(event["task_id"]), str(event["task_family"]), str(event["profile"]), str(event["engine"]), str(event["tool"]),
                1 if event.get("success") else 0, int(event.get("input_tokens", 0)), int(event.get("cached_tokens", 0)),
                int(event.get("output_tokens", 0)), int(event.get("reasoning_tokens", 0)), float(event.get("latency_ms", 0)),
                float(event.get("money_cost", 0)), int(event.get("useful_evidence", 0)), int(event.get("duplicate_evidence", 0)),
                1 if event.get("retry_generated") else 0, float(event.get("credit", 0)), float(event.get("created", time.time())),
                json.dumps(event.get("metadata", {}), ensure_ascii=False, sort_keys=True),
            ),
        )
    return event_id


def assign_credits(project: str | Path, task_id: str) -> list[dict[str, Any]]:
    con = connect(project)
    try:
        task = con.execute("SELECT * FROM task_events WHERE id=?", (task_id,)).fetchone()
        tools = con.execute("SELECT * FROM tool_events WHERE task_id=? ORDER BY created,id", (task_id,)).fetchall()
        if not task:
            raise KeyError(task_id)
        results: list[dict[str, Any]] = []
        with transaction(project) as writer:
            total = max(1, len(tools))
            for index, row in enumerate(tools):
                success = bool(task["success"]) and bool(row["success"])
                position = 0.85 + 0.15 * ((index + 1) / total)
                credit = (1.0 if success else -0.75) * position
                credit += min(2.0, int(row["useful_evidence"]) * 0.35)
                credit -= min(2.0, int(row["duplicate_evidence"]) * 0.5)
                credit -= 0.8 * int(row["retry_generated"])
                credit -= min(0.8, (int(row["input_tokens"]) + int(row["output_tokens"])) / 6000.0)
                credit -= min(0.5, float(row["latency_ms"]) / 30000.0)
                writer.execute("UPDATE tool_events SET credit=? WHERE id=?", (credit, row["id"]))
                results.append({"tool_event_id": row["id"], "tool": row["tool"], "credit": credit})
        return results
    finally:
        con.close()


def profile_stats(project: str | Path, task_family: str, profile: str, model: str = "*") -> dict[str, Any]:
    con = connect(project)
    try:
        query = "SELECT * FROM task_events WHERE task_family=? AND profile=?"
        params: list[Any] = [task_family, profile]
        if model != "*":
            query += " AND model=?"; params.append(model)
        rows = con.execute(query, params).fetchall()
    finally:
        con.close()
    posterior = BetaPosterior(2.0, 1.0)
    active: list[int] = []
    latency: list[float] = []
    cost: list[float] = []
    coverage: list[float] = []
    duplicates: list[float] = []
    retries: list[int] = []
    for row in rows:
        posterior = posterior.update(float(row["success"]))
        active.append(int(row["active_tokens"])); latency.append(float(row["latency_ms"])); cost.append(float(row["money_cost"]))
        coverage.append(float(row["evidence_coverage"])); duplicates.append(float(row["duplicate_ratio"])); retries.append(int(row["retries"]))
    def mean(values: list[float | int], default: float) -> float:
        return sum(values) / len(values) if values else default
    return {
        "observations": len(rows), "success_mean": posterior.mean, "success_lower_90": posterior.lower(),
        "active_tokens_mean": mean(active, 0), "latency_ms_mean": mean(latency, 0), "money_cost_mean": mean(cost, 0),
        "coverage_mean": mean(coverage, 0), "duplicate_ratio_mean": mean(duplicates, 0), "retries_mean": mean(retries, 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Syntavra normalized usage telemetry")
    parser.add_argument("--project", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    norm = sub.add_parser("normalize"); norm.add_argument("json_file")
    stats = sub.add_parser("stats"); stats.add_argument("task_family"); stats.add_argument("profile"); stats.add_argument("--model", default="*")
    credit = sub.add_parser("credit"); credit.add_argument("task_id")
    args = parser.parse_args()
    if args.command == "normalize":
        payload = json.loads(Path(args.json_file).read_text(encoding="utf-8")); print(dump_json(asdict(normalize_usage(payload))))
    elif args.command == "stats":
        print(dump_json(profile_stats(args.project, args.task_family, args.profile, args.model)))
    else:
        print(dump_json(assign_credits(args.project, args.task_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
