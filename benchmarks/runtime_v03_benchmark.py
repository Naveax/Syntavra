#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signalcore_runtime.compression import ContentRouter, ReversibleContentStore
from signalcore_runtime.evidence import EvidenceStore
from signalcore_runtime.host_adapters import KNOWN_HOSTS
from signalcore_runtime.installer import HostInstaller
from signalcore_runtime.output_governor import OutputGovernor
from signalcore_runtime.sandbox import SandboxManager, SandboxPolicy
from signalcore_runtime.session_runtime import SessionRuntime
from signalcore_runtime.signalbench import ArmSpec, SignalBenchRunner, TaskSpec
from signalcore_runtime.structural import StructuralIndex
from signalcore_runtime.structural_parsers import ParserRegistry, parser_fixtures
from signalcore_runtime.util import atomic_write_json, sha256_bytes


def _measure(callable_):
    tracemalloc.start()
    started = time.perf_counter()
    value = callable_()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, elapsed, peak


def run(*, scale: int = 1) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        project = root / "project"
        project.mkdir()
        fixture_rows = list(parser_fixtures())
        for path, text, _ in fixture_rows:
            (project / path).write_text(text, encoding="utf-8")
        tests = project / "tests"
        tests.mkdir()
        (tests / "test_target.py").write_text("from main import target\ndef test_target(): assert target() == 1\n", encoding="utf-8")

        index = StructuralIndex(root / "structural.db", repository_root=project, repository_id="benchmark")
        index_result, index_seconds, index_peak = _measure(index.index)
        parser_registry = ParserRegistry(project)
        parsed = [parser_registry.parse(path, text) for path, text, _ in fixture_rows]
        parser_recall = sum(bool(row.symbols) for row in parsed) / len(parsed)
        call_recall = sum(any(edge.edge_type.startswith("calls") and edge.target.rsplit(".", 1)[-1] == "target" for edge in row.edges) for row in parsed) / len(parsed)
        impact = index.inspect_impact("target", max_depth=8)
        repo_map = index.repository_map("target caller", token_budget=1200, max_depth=8)

        evidence = EvidenceStore(root / "evidence", project_id="benchmark")
        compression = ReversibleContentStore(root / "compression.db", evidence=evidence, chunk_size=4096)
        router = ContentRouter(compression, repository_root=project)
        log = ("test unit_%06d ... ok\n" % 0) * (20_000 * scale) + "ERROR failed at src/app.py:42\n"
        compressed, compression_seconds, compression_peak = _measure(lambda: router.compress(log, path="pytest.log", budget_bytes=2048))
        roundtrip = compression.verify_roundtrip(compressed.compression_id)
        compression_ratio = compressed.original_bytes / max(1, compressed.visible_bytes)

        session_runtime = SessionRuntime(root / "sessions.db", project_id="benchmark")
        session = session_runtime.create_session(metadata={"benchmark": True})
        event_count = 256 * scale
        for number in range(event_count):
            session_runtime.append(session.session_id, "tool", {"index": number, "payload": "x" * 40})
        root_summary, session_seconds, session_peak = _measure(lambda: session_runtime.compact(session.session_id, leaf_size=8, fanout=4, force=True))
        expanded = session_runtime.expand_summary(root_summary)
        session_exact = session_runtime.verify(session.session_id)["ok"] and expanded["coverage"] == event_count

        skill_root = root / "skill"
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text("name: signal-core\n", encoding="utf-8")
        (project / ".claude").mkdir(exist_ok=True)
        installer = HostInstaller(project=project, skill_root=skill_root, home=root / "home")
        installer_first = installer.install(["claude-code", "cursor"])
        installer_second = installer.install(["claude-code", "cursor"])
        installer_doctor = installer.doctor()
        installer_uninstall = installer.uninstall()

        sandbox = SandboxManager(root / "sandbox", project=project, evidence=evidence)
        sandbox_policy = SandboxPolicy(network="inherit", backend="local-restricted", strict=False, timeout_seconds=10)
        sandbox_plan = sandbox.plan(["python", "-c", "print('ok')"], policy=sandbox_policy)

        output = OutputGovernor("compact").render({
            "result": "v0.3 internal benchmark completed",
            "changed_files": ["signalcore_runtime/structural.py:1"],
            "behavior": "Unified runtime surfaces exercised",
            "verification": "all internal invariants passed",
            "limitations": "NOT PROVEN against external competitors",
            "evidence": compressed.exact_handle,
        }, contract="implementation")

        task = TaskSpec("smoke", "known-edit", "smoke", str(project), "tree", ("python", "-c", "raise SystemExit(0)"))
        arms = [
            ArmSpec("plain", "extension", ("adapter", "{request}", "{output}"), "x", "same-model", "same", 1000),
            ArmSpec("signalcore", "extension", ("adapter", "{request}", "{output}"), "0.3.0", "same-model", "same", 1000),
        ]
        signalbench = SignalBenchRunner(root / "signalbench")
        signalbench_validation = signalbench.validate([task], arms)
        manifest = signalbench.write_manifest(root / "signalbench-manifest.json", [task], arms)

        checks = {
            "parser_symbol_recall_100pct": parser_recall == 1.0,
            "parser_call_recall_at_least_90pct": call_recall >= 0.9,
            "structural_index_all_files": index_result["total"] == len(fixture_rows) + 1,
            "transitive_impact_nonempty": bool(impact["affected_paths"]),
            "repository_map_bounded": repo_map["used"] <= repo_map["budget"] and bool(repo_map["selected"]),
            "compression_exact_roundtrip": roundtrip,
            "compression_ratio_at_least_20x": compression_ratio >= 20.0,
            "compression_visible_bound": compressed.visible_bytes <= 2048,
            "session_exact_recovery": session_exact,
            "installer_idempotent": installer_first["ok"] and installer_second["ok"] and installer_doctor["ok"] and installer_uninstall["ok"],
            "host_surface_at_least_14": len(KNOWN_HOSTS) >= 14,
            "sandbox_plan_fail_explicit": sandbox_plan.backend == "local-restricted" and bool(sandbox_plan.degraded_reasons),
            "output_governor_bounded": output["bytes"] <= 4096 and "NOT PROVEN" in output["text"],
            "signalbench_protocol_valid": signalbench_validation["ok"] and manifest["schema_version"] == 3,
        }
        result = {
            "schema_version": 1,
            "version": "0.3.0",
            "ok": all(checks.values()),
            "claim": "5X_NOT_PROVEN",
            "boundary": "Internal component benchmark only; no external competitor arm was executed.",
            "checks": checks,
            "structural": {
                "languages": len(fixture_rows),
                "symbol_recall": parser_recall,
                "call_recall": call_recall,
                "indexed_files": index_result["total"],
                "affected_paths": impact["affected_paths"],
                "repository_map_tokens": repo_map["used"],
                "seconds": index_seconds,
                "peak_python_bytes": index_peak,
            },
            "compression": {
                "original_bytes": compressed.original_bytes,
                "visible_bytes": compressed.visible_bytes,
                "ratio": compression_ratio,
                "roundtrip": roundtrip,
                "seconds": compression_seconds,
                "peak_python_bytes": compression_peak,
            },
            "session": {
                "events": event_count,
                "root_summary_id": root_summary,
                "coverage": expanded["coverage"],
                "exact": session_exact,
                "seconds": session_seconds,
                "peak_python_bytes": session_peak,
            },
            "installer": {"hosts": ["claude-code", "cursor"], "doctor": installer_doctor},
            "sandbox": {"backend": sandbox_plan.backend, "guarantees": sandbox_plan.guarantees, "degraded": sandbox_plan.degraded_reasons},
            "signalbench": {"validation": signalbench_validation, "manifest_hash": manifest["manifest_hash"]},
        }
        result["result_hash"] = sha256_bytes(json.dumps(result, sort_keys=True, separators=(",", ":")).encode())
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run(scale=max(1, args.scale))
    if args.output:
        atomic_write_json(Path(args.output), result, mode=0o644)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
