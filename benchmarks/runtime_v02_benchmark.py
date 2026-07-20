#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gc
import json
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from signalcore_runtime.context_governor import pack_context
from signalcore_runtime.evidence import EvidenceStore
from signalcore_runtime.history import ImmutableHistory
from signalcore_runtime.models import ContextItem
from signalcore_runtime.output_firewall import summarize
from signalcore_runtime.structural import StructuralIndex
from signalcore_runtime.util import atomic_write_json


def measure_peak(function):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    value = function()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, peak, elapsed


def build_chain(root: Path, length: int = 81) -> None:
    for index in range(length):
        target = f"func_{index - 1}" if index else None
        body = f"def func_{index}():\n"
        body += f"    return {target}()\n" if target else "    return 0\n"
        name = f"test_module_{index:03d}.py" if index == length - 1 else f"module_{index:03d}.py"
        (root / name).write_text(body, encoding="utf-8")


def run(output: Path | None = None, *, output_lines: int = 350_000) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        repo = root / "repo"
        repo.mkdir()
        build_chain(repo)
        index = StructuralIndex(root / "structural.sqlite3", repository_root=repo, repository_id="bench")
        index.index()
        direct = index.inspect_impact("func_0", max_depth=1)
        transitive = index.inspect_impact("func_0", max_depth=100)
        expected_paths = 81
        direct_recall = len(direct["affected_paths"]) / expected_paths
        transitive_recall = len(transitive["affected_paths"]) / expected_paths

        stdout = root / "stdout.log"
        stderr = root / "stderr.log"
        with stdout.open("w", encoding="utf-8") as handle:
            for line in range(output_lines):
                handle.write(f"test_{line} ... ok\n")
            handle.write(f"{output_lines} passed in 42.0s\n")
        stderr.write_text("", encoding="utf-8")

        def baseline_read():
            text = stdout.read_bytes().decode("utf-8", errors="replace")
            lines = text.splitlines()
            return [line for line in lines if "passed" in line or "failed" in line][-30:]

        _, baseline_peak, baseline_seconds = measure_peak(baseline_read)
        store = EvidenceStore(root / "evidence", project_id="bench")

        def streaming_read():
            return summarize(
                ("pytest", "-q"),
                stdout_path=stdout,
                stderr_path=stderr,
                exit_code=0,
                duration_seconds=42,
                evidence=store,
            )

        firewall, streaming_peak, streaming_seconds = measure_peak(streaming_read)

        history = ImmutableHistory(root / "history.sqlite3", session_id="bench")
        for event in range(256):
            history.append("event", {"event": event})
        history_root = history.compact(leaf_size=8, fanout=4)
        expanded = history.expand_summary(history_root)

        pack = pack_context(
            [
                ContextItem("task", "task", "repair", 20, 10, mandatory=True, stable=True),
                ContextItem("definition", "evidence", "definition", 30, 9, stable=True),
                ContextItem("caller", "impact", "caller", 20, 8, dependencies=("definition",)),
                ContextItem("raw-log", "log", "noise", 1000, 0.5),
            ],
            budget=80,
            mandatory_roles=("task", "impact"),
        )

        result = {
            "schema_version": 2,
            "scope": "internal SignalCore 0.1-style paths versus SignalCore 0.2 implementations; not competitor evidence",
            "structural": {
                "files": expected_paths,
                "direct_only_recall": direct_recall,
                "transitive_recall": transitive_recall,
                "improvement_factor": transitive_recall / max(direct_recall, 1e-9),
                "deepest_impact": max(row["depth"] for row in transitive["transitive_references"]),
            },
            "output_firewall": {
                "raw_bytes": stdout.stat().st_size,
                "baseline_peak_bytes": baseline_peak,
                "streaming_peak_bytes": streaming_peak,
                "peak_memory_reduction_factor": baseline_peak / max(1, streaming_peak),
                "baseline_seconds": baseline_seconds,
                "streaming_seconds": streaming_seconds,
                "visible_bytes": firewall.visible_bytes,
                "final_summary_preserved": f"{output_lines} passed" in firewall.summary,
                "exact_evidence_verified": store.verify(firewall.evidence_handle),
            },
            "history": {
                "events": 256,
                "root": history_root,
                "exact_coverage": expanded["coverage"],
                "single_root": bool(history_root),
            },
            "context": {
                "budget": pack.budget,
                "used": pack.used,
                "mandatory_satisfied": pack.mandatory_satisfied,
                "selected": pack.selected_ids,
                "raw_log_dropped": "raw-log" in pack.dropped_ids,
            },
            "claim": "5X_NOT_PROVEN",
        }
        result["ok"] = (
            transitive_recall == 1.0
            and direct_recall < 0.05
            and streaming_peak < baseline_peak
            and firewall.visible_bytes <= 4300
            and result["output_firewall"]["final_summary_preserved"]
            and result["output_firewall"]["exact_evidence_verified"]
            and expanded["coverage"] == 256
            and pack.mandatory_satisfied
            and "raw-log" in pack.dropped_ids
        )
        if output:
            atomic_write_json(output, result, mode=0o644)
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--output-lines", type=int, default=350_000)
    args = parser.parse_args(argv)
    result = run(Path(args.output) if args.output else None, output_lines=args.output_lines)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
