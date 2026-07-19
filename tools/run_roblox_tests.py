#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-commit", default="WORKTREE")
    args = parser.parse_args()
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests" / "roblox_profile"))
    result = unittest.TestResult()
    started = time.perf_counter()
    suite.run(result)
    payload = {
        "source_commit": args.source_commit,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "duration_ms": (time.perf_counter() - started) * 1000,
        "status": "PASS" if result.wasSuccessful() else "FAIL",
        "maturity": "INTERNALLY_VERIFIED",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.wasSuccessful() else 2


if __name__ == "__main__":
    raise SystemExit(main())
