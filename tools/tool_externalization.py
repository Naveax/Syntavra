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

from signalcore_runtime.evidence import EvidenceStore
from signalcore_runtime.tool_externalization import ExternalizationPolicy, ToolOutputExternalizer, ToolPayload
from signalcore_runtime.util import stable_project_id


def jsonable(value: Any) -> Any:
    if is_dataclass(value): return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict): return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)): return [jsonable(item) for item in value]
    if isinstance(value, Path): return str(value)
    return value


def emit(value: Any) -> None:
    print(json.dumps(jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, default=str))


def engine(args: argparse.Namespace) -> ToolOutputExternalizer:
    project = Path(args.project).resolve(strict=False)
    state = Path(args.state_root).resolve(strict=False) if args.state_root else project / ".signalcore" / "runtime-v3"
    return ToolOutputExternalizer(
        state / "tool-externalization.sqlite3",
        evidence=EvidenceStore(state / "evidence", project_id=stable_project_id(project)),
        policy=ExternalizationPolicy.for_profile(getattr(args, "profile", "balanced")),
    )


def read_payload(args: argparse.Namespace) -> bytes:
    if getattr(args, "input", None): return Path(args.input).read_bytes()
    if getattr(args, "text", None) is not None: return args.text.encode("utf-8")
    return sys.stdin.buffer.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SignalCore lossless tool-output externalization")
    parser.add_argument("--project", default="."); parser.add_argument("--state-root")
    sub = parser.add_subparsers(dest="action", required=True)

    capture = sub.add_parser("capture")
    capture.add_argument("--command", default=""); capture.add_argument("--tool-name", default="shell")
    capture.add_argument("--path", default=""); capture.add_argument("--scope-key", default="default")
    capture.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced")
    capture.add_argument("--input"); capture.add_argument("--text")

    search = sub.add_parser("search")
    search.add_argument("query"); search.add_argument("--artifact-id"); search.add_argument("--scope-key")
    search.add_argument("--limit", type=int, default=8); search.add_argument("--pack-budget", type=int)
    search.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced")

    reveal = sub.add_parser("reveal")
    reveal.add_argument("--artifact-id"); reveal.add_argument("--lens", choices=("all","critical","failures","changes","delta","head","tail","query","salient","facets","schema"), default="salient")
    reveal.add_argument("--query", default=""); reveal.add_argument("--budget-bytes", type=int); reveal.add_argument("--continuation-token")
    reveal.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced")

    restore = sub.add_parser("restore")
    restore.add_argument("artifact_id"); restore.add_argument("--segment-index", type=int)
    restore.add_argument("--start-byte", type=int); restore.add_argument("--end-byte", type=int); restore.add_argument("--output")
    restore.add_argument("--profile", choices=("compact", "balanced", "audit"), default="balanced")

    describe = sub.add_parser("describe"); describe.add_argument("artifact_id"); describe.add_argument("--profile", choices=("compact","balanced","audit"), default="balanced")
    verify = sub.add_parser("verify"); verify.add_argument("artifact_id"); verify.add_argument("--profile", choices=("compact","balanced","audit"), default="balanced")
    proof = sub.add_parser("proof"); proof.add_argument("artifact_id"); proof.add_argument("segment_index", type=int); proof.add_argument("--profile", choices=("compact","balanced","audit"), default="balanced")
    lineage = sub.add_parser("lineage"); lineage.add_argument("artifact_id"); lineage.add_argument("--limit", type=int, default=128); lineage.add_argument("--profile", choices=("compact","balanced","audit"), default="balanced")
    stats = sub.add_parser("stats"); stats.add_argument("--profile", choices=("compact","balanced","audit"), default="balanced")

    args = parser.parse_args(argv); active = engine(args)
    if args.action == "capture":
        result = active.externalize(ToolPayload(command=args.command, stdout=read_payload(args), tool_name=args.tool_name, path=args.path, scope_key=args.scope_key)); emit(result); return 0 if result.quality_gate_passed else 3
    if args.action == "search":
        if args.pack_budget: emit(active.search_pack(args.query, artifact_id=args.artifact_id, scope_key=args.scope_key, budget_bytes=args.pack_budget, limit=args.limit))
        else: emit({"query":args.query,"hits":active.search(args.query, artifact_id=args.artifact_id, scope_key=args.scope_key, limit=args.limit)})
        return 0
    if args.action == "reveal": emit(active.reveal(args.artifact_id, lens=args.lens, query=args.query, budget_bytes=args.budget_bytes, continuation_token=args.continuation_token)); return 0
    if args.action == "restore":
        byte_range = None
        if args.start_byte is not None or args.end_byte is not None:
            if args.start_byte is None or args.end_byte is None: raise SystemExit("--start-byte and --end-byte must be supplied together")
            byte_range = (args.start_byte,args.end_byte)
        data = active.restore(args.artifact_id, segment_index=args.segment_index, byte_range=byte_range)
        if args.output: Path(args.output).write_bytes(data); emit({"artifact_id":args.artifact_id,"bytes":len(data),"output":args.output})
        else: sys.stdout.buffer.write(data)
        return 0
    if args.action == "describe": emit(active.artifact(args.artifact_id)); return 0
    if args.action == "verify": result=active.verify(args.artifact_id); emit(result); return 0 if result["ok"] else 3
    if args.action == "proof": result=active.segment_proof(args.artifact_id,args.segment_index); emit(result); return 0 if result["verified"] else 3
    if args.action == "lineage": emit({"artifact_id":args.artifact_id,"lineage":active.lineage(args.artifact_id,limit=args.limit)}); return 0
    if args.action == "stats": emit(active.stats()); return 0
    raise AssertionError(args.action)


if __name__ == "__main__": raise SystemExit(main())
