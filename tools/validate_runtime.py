#!/usr/bin/env python3
from __future__ import annotations

import json
import py_compile
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signalcore_runtime.benchmark_harness import TIER_CONFIGS, validate_config
from signalcore_runtime.bootstrap import runtime_health
from signalcore_runtime.context_governor import pack_context
from signalcore_runtime.models import ContextItem

REQUIRED = [ROOT / "signalcore_runtime" / name for name in (
    "__init__.py", "cli.py", "bootstrap.py", "process_broker.py", "rollout.py",
    "output_firewall.py", "structural.py", "memory.py", "evidence.py", "history.py",
    "context_governor.py", "verifier_graph.py", "benchmark_harness.py",
    "claim_governance.py", "host_adapters.py", "hooks.py", "mcp_server.py",
    "structural_parsers.py", "installer.py", "sandbox.py", "compression.py",
    "session_runtime.py", "output_governor.py", "signalbench.py",
)]
CONTROLS = {name: True for name in (
    "same_prompt", "same_model", "same_reasoning", "same_repository", "same_verifier",
    "same_permissions", "same_timeout", "balanced_cache", "no_artificial_sleep", "no_meaningless_duplication",
)}


def main() -> int:
    checks = []
    checks.append(("required_runtime_files", all(path.is_file() for path in REQUIRED)))
    checks.append(("version", (ROOT / "VERSION").read_text().strip() == "0.3.0"))
    try:
        for path in sorted((ROOT / "signalcore_runtime").glob("*.py")):
            py_compile.compile(str(path), doraise=True)
        checks.append(("runtime_compile", True))
    except Exception:
        checks.append(("runtime_compile", False))
    for tier in ("20X", "30X", "100X"):
        checks.append((f"difficulty_shape_{tier}", validate_config({"tier": tier, "axes": TIER_CONFIGS[tier], "controls": CONTROLS})["ok"]))
    pack = pack_context(
        [
            ContextItem("task", "task", "task", 10, 10, mandatory=True),
            ContextItem("proof", "evidence", "proof", 10, 9),
        ],
        budget=20,
        mandatory_roles=("task",),
    )
    checks.append(("context_pack", pack.mandatory_satisfied and pack.used <= 20))
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        skill = root / "skills" / "signal-core"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("name: signal-core\n")
        health = runtime_health(
            project=root,
            skill_root=skill,
            state_root=root / ".state",
            codex_home=root / ".codex",
            host="codex",
        )
        checks.append(("runtime_health_smoke", health.state == "RUNTIME_ACTIVE"))
        checks.append(("mcp_controlled", health.details["host_negotiation"]["mode"] == "MCP_CONTROLLED"))
        checks.append(("unified_components", all(health.checks.get(name) for name in (
            "host_installer", "secure_sandbox", "reversible_compression", "long_session_runtime", "output_governor", "signalbench"
        ))))
    result = {"ok": all(passed for _, passed in checks), "checks": [{"name": name, "passed": passed} for name, passed in checks]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
