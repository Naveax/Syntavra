#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from syntavra_runtime.adaptive_context import AdaptiveContextEngine, AdaptivePolicy, ToolObservation
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.util import stable_project_id


def emit(value: Any) -> None:
    if is_dataclass(value): value = asdict(value)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def active_engine(args: argparse.Namespace) -> AdaptiveContextEngine:
    project = Path(args.project).resolve(strict=False)
    state = Path(args.state_root).resolve(strict=False) if args.state_root else project / ".syntavra" / "runtime-v3"
    return AdaptiveContextEngine(state / "adaptive-context.sqlite3", evidence=EvidenceStore(state / "evidence", project_id=stable_project_id(project)), policy=AdaptivePolicy.for_profile(getattr(args, "profile", "balanced")))


def payload(args: argparse.Namespace) -> str:
    if getattr(args, "input", None): return Path(args.input).read_text(encoding="utf-8", errors="replace")
    if getattr(args, "text", None) is not None: return args.text
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Syntavra Adaptive Context Engine")
    parser.add_argument("--project", default="."); parser.add_argument("--state-root")
    sub = parser.add_subparsers(dest="action", required=True)
    capture = sub.add_parser("capture"); capture.add_argument("--command", default=""); capture.add_argument("--tool-name", default="shell"); capture.add_argument("--path", default=""); capture.add_argument("--scope-key", default="default"); capture.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced"); capture.add_argument("--input"); capture.add_argument("--text")
    search = sub.add_parser("search"); search.add_argument("capture_id"); search.add_argument("query"); search.add_argument("--limit", type=int, default=8); search.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced")
    restore = sub.add_parser("restore"); restore.add_argument("capture_id"); restore.add_argument("--chunk-index", type=int); restore.add_argument("--output"); restore.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced")
    verify = sub.add_parser("verify"); verify.add_argument("capture_id"); verify.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced")
    stats = sub.add_parser("stats"); stats.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced")
    args = parser.parse_args(argv); engine = active_engine(args)
    if args.action == "capture":
        result = engine.process(ToolObservation(command=args.command, stdout=payload(args), tool_name=args.tool_name, path=args.path, scope_key=args.scope_key)); emit(result); return 0 if result.quality_gate_passed else 3
    if args.action == "search": emit({"capture_id": args.capture_id, "query": args.query, "hits": [asdict(hit) for hit in engine.search(args.capture_id, args.query, limit=args.limit)]}); return 0
    if args.action == "restore":
        data = engine.restore(args.capture_id, chunk_index=args.chunk_index)
        if args.output: Path(args.output).write_bytes(data); emit({"capture_id": args.capture_id, "bytes": len(data), "output": args.output})
        else: sys.stdout.buffer.write(data)
        return 0
    if args.action == "verify": result = engine.verify(args.capture_id); emit(result); return 0 if result["ok"] else 3
    if args.action == "stats": emit(engine.stats()); return 0
    raise AssertionError(args.action)


if __name__ == "__main__": raise SystemExit(main())
