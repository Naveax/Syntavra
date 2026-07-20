#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "benchmarks" / "hardening_v3_benchmark.py"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one benchmark match, found {count}: {old!r}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from signalcore_runtime.readiness_gate import ReadinessEvidence, SignalCoreReadinessGate\n",
        "from signalcore_runtime.readiness_gate import ReadinessEvidence, SignalCoreReadinessGate\n"
        "from signalcore_runtime.real_task_receipts import load_verified_real_tasks\n",
    )
    text = replace_once(
        text,
        "        evidence_gate = ReadinessEvidence(\n",
        "        real_tasks = load_verified_real_tasks(\n"
        "            ROOT / 'benchmarks' / 'results' / 'real-tasks'\n"
        "        )\n\n"
        "        evidence_gate = ReadinessEvidence(\n",
    )
    text = replace_once(
        text,
        "            real_repository_tasks=0,\n",
        "            real_repository_tasks=real_tasks['verified_count'],\n",
    )
    text = replace_once(
        text,
        '            "boundary": "Internal hardening benchmark. Real repository tasks and external competitor arms are intentionally zero and cannot satisfy the 10/10 gate.",\n',
        '            "boundary": "Internal hardening benchmark with cryptographically verified real-task receipts. External competitor arms remain zero, so superiority is not proven.",\n',
    )
    text = replace_once(
        text,
        '            "externalization_stats": stats,\n',
        '            "externalization_stats": stats,\n'
        '            "real_repository_tasks": real_tasks,\n',
    )
    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
