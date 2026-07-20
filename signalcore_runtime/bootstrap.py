from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .evidence import EvidenceStore
from .host_adapters import negotiate
from .models import RuntimeHealth
from .rollout import discover_rollouts
from .state import StateDB
from .util import atomic_write_json, stable_project_id


def resolve_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def git_identity(project: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", "-C", str(project), *args],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    head = run("rev-parse", "HEAD")
    branch = run("branch", "--show-current")
    tree = run("write-tree")
    dirty = bool(run("status", "--porcelain") not in ("", "unknown"))
    return {"head": head, "branch": branch, "tree_hash": tree, "dirty": dirty}


def runtime_health(
    *,
    project: Path,
    skill_root: Path,
    state_root: Path,
    codex_home: Path,
    host: str,
    require_rollout: bool = False,
) -> RuntimeHealth:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    package = Path(__file__).resolve().parent
    required_modules = {
        "process_broker": "process_broker.py",
        "output_firewall": "output_firewall.py",
        "context_governor": "context_governor.py",
        "hook_engine": "hooks.py",
        "mcp_server": "mcp_server.py",
        "structural_intelligence": "structural_parsers.py",
        "host_installer": "installer.py",
        "secure_sandbox": "sandbox.py",
        "reversible_compression": "compression.py",
        "long_session_runtime": "session_runtime.py",
        "output_governor": "output_governor.py",
        "signalbench": "signalbench.py",
    }
    checks["skill_installed"] = (skill_root / "SKILL.md").is_file()
    checks["runtime_package"] = (package / "__init__.py").is_file()
    for name, filename in required_modules.items():
        checks[name] = (package / filename).is_file()
    try:
        state = StateDB(state_root / "runtime.sqlite3")
        checks["state_store"] = state.integrity_check()
    except Exception:
        checks["state_store"] = False
    try:
        evidence = EvidenceStore(state_root / "evidence", project_id=stable_project_id(project))
        handle = evidence.put(b"signalcore-health-v3", kind="health")
        checks["evidence_store"] = evidence.get(handle) == b"signalcore-health-v3"
    except Exception:
        checks["evidence_store"] = False
    negotiation = negotiate(host, runtime_available=checks["runtime_package"])
    checks["host_adapter"] = negotiation["mode"] != "UNSUPPORTED"
    rollouts = discover_rollouts(codex_home)
    checks["rollout_available"] = bool(rollouts) if require_rollout else True
    mandatory = (
        "skill_installed", "runtime_package", "state_store", "evidence_store",
        *required_modules.keys(), "host_adapter", "rollout_available",
    )
    for name in mandatory:
        if not checks.get(name, False):
            reasons.append(f"check-failed:{name}")
    if not checks["skill_installed"] and not checks["runtime_package"]:
        state_name = "NOT_INSTALLED"
    elif checks["skill_installed"] and not checks["runtime_package"]:
        state_name = "INSTRUCTION_ONLY"
    elif all(checks.get(name, False) for name in mandatory):
        state_name = "RUNTIME_ACTIVE"
    elif checks["runtime_package"] and checks["state_store"]:
        state_name = "RUNTIME_DEGRADED"
    else:
        state_name = "RUNTIME_FAILED"
    return RuntimeHealth(
        state_name,
        state_name == "RUNTIME_ACTIVE",
        checks,
        tuple(reasons),
        {
            "version": "0.3.0",
            "host": host,
            "host_negotiation": negotiation,
            "rollout_candidates": [str(path) for path in rollouts[:5]],
            "enforcement_boundary": negotiation["mode"],
            "runtime_plane": "unified-v3",
        },
    )


def start_runtime(
    task: str,
    *,
    project: Path,
    skill_root: Path,
    state_root: Path | None = None,
    codex_home: Path | None = None,
    host: str = "codex",
) -> dict[str, Any]:
    project = project.resolve(strict=True)
    state_root = state_root or project / ".signalcore" / "runtime-v3"
    health = runtime_health(
        project=project,
        skill_root=skill_root,
        state_root=state_root,
        codex_home=codex_home or resolve_codex_home(),
        host=host,
    )
    identity = git_identity(project)
    session_id = f"sc-{int(time.time())}-{os.getpid()}"
    payload = {
        "schema_version": 3,
        "session_id": session_id,
        "task": task,
        "project": str(project),
        "project_id": stable_project_id(project),
        "git": identity,
        "host": host,
        "activation_state": health.state,
        "started_at": time.time(),
    }
    session_dir = state_root / "sessions" / session_id
    atomic_write_json(session_dir / "session.json", payload)
    return {
        "session": payload,
        "health": {
            "state": health.state,
            "healthy": health.healthy,
            "checks": health.checks,
            "reasons": health.reasons,
            "details": health.details,
        },
    }
