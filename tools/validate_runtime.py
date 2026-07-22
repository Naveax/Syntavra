#!/usr/bin/env python3
from __future__ import annotations

import json
import py_compile
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from syntavra_runtime.benchmark_harness import TIER_CONFIGS, validate_config
from syntavra_runtime.bootstrap import runtime_health
from syntavra_runtime.context_governor import pack_context
from syntavra_runtime.infinite_context import CONTEXT_TIERS, UnboundedContextCoordinator
from syntavra_runtime.integration_matrix import IntegrationMatrix
from syntavra_runtime.models import ContextItem
from syntavra_runtime.release_identity import CHANNEL, VERSION
from syntavra_runtime.paired_benchmark import CodingCorpusPlanner, PairedSchedule, default_arms

REQUIRED = [ROOT / "syntavra_runtime" / name for name in (
    "__init__.py", "cli.py", "bootstrap.py", "process_broker.py", "rollout.py",
    "output_firewall.py", "structural.py", "memory.py", "evidence.py", "history.py",
    "context_governor.py", "verifier_graph.py", "benchmark_harness.py",
    "claim_governance.py", "host_adapters.py", "hooks.py", "mcp_server.py",
    "structural_parsers.py", "installer.py", "sandbox.py", "compression.py",
    "session_runtime.py", "output_governor.py", "signalbench.py",
    "runtime_pipeline.py", "unified_config.py", "crypto.py", "backup.py",
    "identity.py", "observability.py", "migrations.py", "plugin_sdk.py",
    "job_scheduler.py", "policy_rollout.py", "streaming.py", "unified_cli.py",
    "release_identity.py", "integration_matrix.py", "zero_friction.py",
    "semantic_structure.py", "paired_benchmark.py", "infinite_context.py",
    "public_proof.py", "prerelease_cli.py", "platform.py", "platform_cli.py",
    "artifacts.py", "semantic_intelligence.py", "semantic_services.py",
    "runtime_evidence.py", "session_memory.py", "capability_security.py",
    "execution_sandbox.py", "sandbox_runtime.py", "autonomous_agent.py",
    "adapter_platform.py", "adapter_runtime.py", "secretless_gateway.py",
    "headless_runtime.py", "interactive_console.py", "reliability_lab.py",
    "update_manager.py",
)]
CONTROLS = {name: True for name in (
    "same_prompt", "same_model", "same_reasoning", "same_repository", "same_verifier",
    "same_permissions", "same_timeout", "balanced_cache", "no_artificial_sleep", "no_meaningless_duplication",
)}


def main() -> int:
    checks: list[tuple[str, bool]] = []
    checks.append(("required_runtime_files", all(path.is_file() for path in REQUIRED)))
    checks.append(("version", (ROOT / "VERSION").read_text().strip() == VERSION == "0.0.1"))
    checks.append(("release_channel", CHANNEL == "pre-release"))
    try:
        for path in sorted((ROOT / "syntavra_runtime").glob("*.py")):
            py_compile.compile(str(path), doraise=True)
        checks.append(("runtime_compile", True))
    except Exception:
        checks.append(("runtime_compile", False))
    for tier in ("20X", "30X", "100X"):
        checks.append((f"difficulty_shape_{tier}", validate_config({"tier": tier, "axes": TIER_CONFIGS[tier], "controls": CONTROLS})["ok"]))
    pack = pack_context(
        [ContextItem("task", "task", "task", 10, 10, mandatory=True), ContextItem("proof", "evidence", "proof", 10, 9)],
        budget=20,
        mandatory_roles=("task",),
    )
    checks.append(("context_pack", pack.mandatory_satisfied and pack.used <= 20))
    integration = IntegrationMatrix.validate()
    checks.append(("integration_targets", integration["ok"] and integration["providers"] >= 10 and integration["frameworks"] >= 15 and integration["hosts"] >= 18))
    tasks = CodingCorpusPlanner.generate_slots()
    schedule = PairedSchedule(tasks, default_arms(), repetitions=30)
    checks.append(("signalbench_schedule", len(tasks) == 150 and schedule.count == 27000))
    context_reports = UnboundedContextCoordinator.stress_tiers(active_budget=4096)
    checks.append(("unbounded_context_tiers", tuple(row["tier_tokens"] for row in context_reports) == CONTEXT_TIERS and all(row["within_budget"] and row["all_referenced"] and not row["forced_restart"] for row in context_reports)))
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        skill = root / "skills" / "syntavra"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("name: syntavra\n")
        health = runtime_health(project=root, skill_root=skill, state_root=root / ".state", codex_home=root / ".codex", host="codex")
        checks.append(("runtime_health_smoke", health.state == "RUNTIME_ACTIVE"))
        checks.append(("runtime_identity", health.details["version"] == VERSION and health.details["release_channel"] == CHANNEL))
        checks.append(("mcp_controlled", health.details["host_negotiation"]["mode"] == "MCP_CONTROLLED"))
        checks.append(("unified_components", all(health.checks.get(name) for name in (
            "host_installer", "zero_friction", "secure_sandbox", "reversible_compression",
            "long_session_runtime", "unbounded_context", "output_governor", "signalbench", "paired_benchmark",
        ))))
    result = {
        "ok": all(passed for _, passed in checks),
        "product": "Syntavra",
        "version": VERSION,
        "release_channel": CHANNEL,
        "checks": [{"name": name, "passed": passed} for name, passed in checks],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
