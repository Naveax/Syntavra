#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from syntavra_runtime.signalbench_hardened import HardenedSignalBench, UsageReceipt
from syntavra_runtime.util import atomic_write_json


def load_rows(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8")); return value if isinstance(value, list) else value.get("results", [])


def load_receipts(path: Path | None) -> list[UsageReceipt]:
    if path is None: return []
    value = json.loads(path.read_text(encoding="utf-8")); rows = value if isinstance(value, list) else value.get("receipts", [])
    return [UsageReceipt(**row) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Failure-inclusive SignalBench comparison")
    parser.add_argument("--results", required=True); parser.add_argument("--receipts"); parser.add_argument("--baseline-arm", required=True); parser.add_argument("--candidate-arm", required=True); parser.add_argument("--minimum-pairs", type=int, default=10); parser.add_argument("--allow-unreceipted", action="store_true"); parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = HardenedSignalBench.compare(load_rows(Path(args.results)), baseline_arm=args.baseline_arm, candidate_arm=args.candidate_arm, receipts=load_receipts(Path(args.receipts) if args.receipts else None), minimum_pairs=max(1, args.minimum_pairs), require_receipts=not args.allow_unreceipted)
    if args.output: atomic_write_json(Path(args.output), result, mode=0o644)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)); return 0 if result["claimable_superiority"] else 3


if __name__ == "__main__": raise SystemExit(main())
