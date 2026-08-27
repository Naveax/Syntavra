#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from syntavra_runtime.context_reset_handoff import (
    ContextResetHandoff,
    RepositoryHandoffState,
    SecurityHandoffState,
)
from syntavra_runtime.session_memory import SessionMemory

CONTRACT = ROOT / "contracts" / "python" / "context-reset-handoff-v1.json"
REGISTRY = ROOT / "contracts" / "python" / "capability-completeness-registry-v1.json"


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _runtime_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        memory = SessionMemory(Path(tmp) / "session.db", project_id="certify-context-reset")
        memory.open("source")
        memory.append("source", "task", {"goal": "certify context handoff", "importance": 1.0})
        engine = ContextResetHandoff()
        repository = RepositoryHandoffState(
            repository="Naveax/Syntavra",
            branch="certification",
            head_sha="1" * 40,
            clean=True,
        )
        result = engine.prepare(
            memory,
            "source",
            "RESET",
            repository=repository,
            evidence_refs=("evidence:certification",),
            security=SecurityHandoffState(unresolved_critical_evidence=1),
            reason_codes=("CONTEXT_PRESSURE_RECOVERABLE",),
        )
        _require(result["ok"] is True, "reset handoff failed")
        _require(result["decision"] == "RESET", "reset decision drift")
        _require(result["target_session_id"] != "source", "reset did not create a target session")
        _require(result["verified"]["session_chain"] is True, "session chain was not verified")
        _require(result["verified"]["secret_material_excluded"] is True, "secret exclusion drift")
        _require(memory.verify("source")["ok"] is True, "source history was not preserved")
        status = engine.status()
        _require(status["new_persistent_store"] is False, "parallel persistent store introduced")
        _require(status["secret_material_allowed"] is False, "secret material transport enabled")
        return {
            "decisions": status["decisions"],
            "session_memory_authority_reused": status["session_memory_authority_reused"],
            "new_persistent_store": status["new_persistent_store"],
            "secret_material_allowed": status["secret_material_allowed"],
            "git_state_preserved": status["git_state_preserved"],
            "security_state_preserved": status["security_state_preserved"],
            "evidence_state_preserved": status["evidence_state_preserved"],
            "content_addressed_receipts": status["content_addressed_receipts"],
        }


def certify() -> dict[str, Any]:
    contract = _read_json(CONTRACT)
    _require(contract.get("schema_version") == 1, "context handoff schema drift")
    _require(contract.get("family") == "context-reset-handoff", "context handoff family drift")
    _require(contract.get("phase") == "python-first", "context handoff phase drift")
    _require(contract.get("claim") == "CONTEXT_RESET_HANDOFF_V1", "context handoff claim drift")
    _require(contract.get("strict") is True, "context handoff must remain strict")
    _require(
        set(contract.get("decision_surface") or ()) == {"CONTINUE", "COMPACT", "RESET", "BRANCH"},
        "context handoff decision surface drift",
    )

    for relative in (
        "syntavra_runtime/context_reset_handoff.py",
        "syntavra_runtime/session_memory.py",
        "syntavra_runtime/adaptive_context_policy.py",
        "tests/runtime/test_context_reset_handoff.py",
        ".github/workflows/context-reset-handoff.yml",
    ):
        _require((ROOT / relative).is_file(), f"missing context handoff surface: {relative}")

    registry = _read_json(REGISTRY)
    rows = {
        row["id"]: row
        for row in registry.get("capabilities", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    _require(
        (rows.get("adaptive_context_policy_v1") or {}).get("state") == "certified",
        "Adaptive Context Policy must be certified before Context Reset Handoff",
    )
    _require(
        (rows.get("context_reset_handoff_v1") or {}).get("state")
        in {"partial", "implemented", "verified", "certified"},
        "Context Reset Handoff registry state is invalid",
    )

    admission = contract.get("admission") or {}
    _require(admission.get("python_complete_must_remain_false") is True, "Python COMPLETE boundary drift")
    _require(admission.get("rust_resume_must_remain_false") is True, "Rust resume boundary drift")
    _require(admission.get("rust_production_promoted") == 174, "Rust production promotion drift")
    _require(admission.get("rust_remaining_parity_promotion") == 71, "Rust Remaining-71 drift")

    runtime = _runtime_smoke()
    exact_head = _head()
    _require(len(exact_head) == 40, "unable to resolve exact repository head")
    return {
        "schema_version": 1,
        "family": "context-reset-handoff",
        "claim": "CONTEXT_RESET_HANDOFF_V1",
        "ok": True,
        "exact_head": exact_head,
        "admission_ready": True,
        "python_complete_ready": True,
        "rust_resume_allowed": False,
        "runtime": runtime,
        "rust": {
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
        },
        "claim_boundary": contract.get("claim_boundary"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Context Reset Handoff v1")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        report = certify()
        payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(payload, encoding="utf-8")
        print(payload, end="")
        return 0
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "family": "context-reset-handoff",
            "claim": "CONTEXT_RESET_HANDOFF_V1",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        payload = json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(payload, encoding="utf-8")
        print(payload, end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
