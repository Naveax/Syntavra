#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from syntavra_runtime.host_adapters import KNOWN_HOSTS
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

FIXTURE_RELATIVE = Path("contracts/python/setup-host-reference-v1.json")


def _head(repo: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _run(repo: Path, project: Path, state: Path, argv: list[str], *, home: Path | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["CODEX_HOME"] = str(home / ".codex")
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    proc = subprocess.run(
        [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", "--project", str(project), "--state-root", str(state), *argv],
        cwd=repo,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    try:
        value = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "value": value}


def _json(label: str, result: dict[str, Any], exit_code: int = 0) -> dict[str, Any]:
    if result["exit"] != exit_code or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit {exit_code}, got {result}")
    if not isinstance(result["value"], dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return result["value"]


def _error4(label: str, result: dict[str, Any]) -> dict[str, Any]:
    value = _json(label, result, 4)
    if value.get("ok") is not False or value.get("error", {}).get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: public error-envelope drift: {value}")
    return value


def _routes(fixture: dict[str, Any]) -> dict[str, Any]:
    authority = public_surface.python_public_route_sources()
    expected = fixture["public_routes"]
    observed = sorted(route for route in authority if route in set(expected))
    if observed != expected:
        missing = sorted(set(expected) - set(observed))
        raise AssertionError(f"setup/host route inventory drift: observed={observed}, missing={missing}")
    manifest = {row["route"]: row for row in execution_contract.route_execution_manifest()}
    owners: dict[str, str] = {}
    for route in expected:
        row = manifest.get(route)
        if not row or len(row["entrypoints"]) != 1 or row["unknown_sources"]:
            raise AssertionError(f"setup/host execution ownership drift: {row}")
        owners[route] = row["entrypoint"]
    return {
        "routes": expected,
        "route_count": len(expected),
        "route_sha256": public_surface._digest(expected),
        "ownership": owners,
    }


def _bootstrap_setup(repo: Path, root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    project = root / "setup-project"
    state = root / "setup-state"
    home = root / "setup-home"
    project.mkdir(); state.mkdir(); home.mkdir(); (project / ".git").mkdir(); (project / ".codex").mkdir()

    dry = _json("setup dry-run", _run(repo, project, state, ["setup"], home=home))
    if dry.get("ok") is not True or dry.get("dry_run") is not True:
        raise AssertionError(f"setup dry-run semantics drift: {dry}")
    if (state / "config.json").exists() or (project / ".agents" / "skills" / "syntavra").exists():
        raise AssertionError("setup dry-run mutated the temporary target")
    plan = dry.get("plan") or {}
    if plan.get("mental_model") != fixture["setup_mental_model"] or plan.get("detected_hosts") != ["codex"] or plan.get("installable_hosts") != ["codex"]:
        raise AssertionError(f"setup detection/plan drift: {dry}")

    applied = _json("setup apply", _run(repo, project, state, ["setup", "--apply"], home=home))
    if applied.get("ok") is not True or applied.get("dry_run") is not False or applied.get("profile") != "minimal":
        raise AssertionError(f"setup apply drift: {applied}")
    if applied.get("plan", {}).get("detected_hosts") != ["codex"] or len(applied.get("host_transactions") or []) != 1:
        raise AssertionError(f"setup host-transaction drift: {applied}")
    if not (state / "config.json").is_file() or not (state / "product.json").is_file() or not (project / ".codex" / "config.toml").is_file():
        raise AssertionError("setup apply did not materialize the isolated product/host bundle")

    status = _json("status after setup", _run(repo, project, state, ["status"], home=home))
    doctor = _json("doctor after setup", _run(repo, project, state, ["doctor"], home=home))
    if status.get("product") != "Syntavra" or doctor.get("ok") is not True or doctor.get("installed") is not True:
        raise AssertionError(f"setup status/doctor drift: status={status}, doctor={doctor}")
    if doctor.get("configured_hosts") != ["codex"] or doctor.get("runtime", {}).get("state") != "PRE_RELEASE_INSTALLED":
        raise AssertionError(f"setup doctor host state drift: {doctor}")

    compatibility_install = _json("compat install default dry-run", _run(repo, project, state, ["install"], home=home))
    if compatibility_install.get("ok") is not True or compatibility_install.get("dry_run") is not True:
        raise AssertionError(f"compat install dry-run drift: {compatibility_install}")

    product_path = state / "product.json"
    product_path.unlink()
    diagnosis = _json("repair diagnosis", _run(repo, project, state, ["repair"], home=home))
    if diagnosis.get("apply") is not False or "syntavra repair --apply" not in (diagnosis.get("actions") or []):
        raise AssertionError(f"repair diagnosis drift: {diagnosis}")
    repaired = _json("repair apply", _run(repo, project, state, ["repair", "--apply"], home=home))
    if repaired.get("apply") is not True or repaired.get("ok") is not True or not product_path.is_file():
        raise AssertionError(f"repair apply drift: {repaired}")

    upgraded = _json("upgrade locked", _run(repo, project, state, ["upgrade"], home=home))
    if upgraded.get("ok") is not True or upgraded.get("changed") is not False or upgraded.get("reason") != fixture["upgrade_reason"]:
        raise AssertionError(f"upgrade lock drift: {upgraded}")
    invalid_upgrade = _error4("upgrade invalid target", _run(repo, project, state, ["upgrade", "--target", "99.99.99"], home=home))

    wrapper = root / "wrappers" / "codex-wrapper"
    wrapped = _json("wrap", _run(repo, project, state, ["wrap", "codex", "--output", str(wrapper)], home=home))
    if wrapped.get("ok") is not True or not wrapper.is_file() or "SYNTAVRA_HOST" not in wrapper.read_text(encoding="utf-8"):
        raise AssertionError(f"wrapper drift: {wrapped}")
    unknown_wrapper = _error4("wrap unknown host", _run(repo, project, state, ["wrap", "not-a-host", "--output", str(root / "bad-wrapper")], home=home))

    initialized = _json(
        "init",
        _run(repo, project, state, ["--skill-root", str(repo / "skills" / "syntavra"), "--codex-home", str(home / ".codex"), "init", "fixture task"], home=home),
    )
    if initialized.get("session", {}).get("task") != "fixture task" or initialized.get("session", {}).get("project") != str(project):
        raise AssertionError(f"init bootstrap drift: {initialized}")
    if initialized.get("health", {}).get("state") not in {"RUNTIME_ACTIVE", "RUNTIME_DEGRADED"}:
        raise AssertionError(f"init health-state drift: {initialized}")
    session_file = state / "sessions" / initialized["session"]["session_id"] / "session.json"
    if not session_file.is_file():
        raise AssertionError("init did not persist isolated session state")

    uninstall = _json(
        "legacy uninstall dry-run",
        _run(repo, project, state, ["--skill-root", str(repo / "skills" / "syntavra"), "uninstall", "--home", str(home), "--dry-run"], home=home),
    )
    if uninstall.get("ok") is not True or uninstall.get("changes") != [] or uninstall.get("reason") != "not-installed":
        raise AssertionError(f"legacy uninstall no-install drift: {uninstall}")

    return {
        "dry_run": {
            "ok": dry["ok"], "dry_run": dry["dry_run"],
            "detected_hosts": dry["plan"]["detected_hosts"],
            "installable_hosts": dry["plan"]["installable_hosts"],
            "mental_model": dry["plan"]["mental_model"],
            "mutated_target": False,
        },
        "applied": {
            "ok": applied["ok"], "dry_run": applied["dry_run"],
            "host_transaction_count": len(applied["host_transactions"]),
            "config_exists": (state / "config.json").is_file(),
            "codex_config_exists": (project / ".codex" / "config.toml").is_file(),
        },
        "doctor": {
            "ok": doctor["ok"], "installed": doctor["installed"],
            "configured_hosts": doctor["configured_hosts"], "runtime_state": doctor["runtime"]["state"],
        },
        "compat_install_default_dry_run": compatibility_install["dry_run"],
        "repair": {"diagnosed_action": "syntavra repair --apply" in diagnosis["actions"], "apply_ok": repaired["ok"]},
        "upgrade": {"changed": upgraded["changed"], "reason": upgraded["reason"], "invalid_target_exit": 4, "invalid_target_error_type": invalid_upgrade["error"]["details"]["error"].split(":", 1)[0]},
        "wrapper": {"created": wrapper.is_file(), "unknown_host_exit": 4, "unknown_host_error_type": unknown_wrapper["error"]["details"]["error"].split(":", 1)[0]},
        "init": {"state": initialized["health"]["state"], "session_persisted": session_file.is_file()},
        "uninstall_not_installed": {"ok": uninstall["ok"], "reason": uninstall["reason"], "changes": uninstall["changes"]},
    }


def _host_detection(repo: Path, root: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    project = root / "host-project"
    state = root / "host-state"
    home = root / "host-home"
    project.mkdir(); state.mkdir(); home.mkdir(); (project / ".git").mkdir(); (project / ".codex").mkdir(); (home / ".claude").mkdir()

    implicit = _json("host implicit negotiate", _run(repo, project, state, ["host"], home=home))
    codex = _json("host negotiate codex", _run(repo, project, state, ["host", "negotiate", "codex"], home=home))
    claude = _json("host negotiate claude", _run(repo, project, state, ["host", "negotiate", "claude-code"], home=home))
    if implicit != codex or codex.get("host") != "codex" or claude.get("host") != "claude-code":
        raise AssertionError(f"host negotiation drift: implicit={implicit}, codex={codex}, claude={claude}")

    detected = _json("host detect", _run(repo, project, state, ["host", "detect", "--home", str(home)], home=home))
    rows = detected.get("hosts") or []
    by_host = {row.get("host"): row for row in rows if isinstance(row, dict)}
    for name in fixture["selected_hosts"]:
        if name not in by_host:
            raise AssertionError(f"host detection inventory missing {name}: {detected}")
    if not by_host["codex"].get("project_markers") or not by_host["claude-code"].get("user_markers"):
        raise AssertionError(f"host marker detection drift: {detected}")

    capabilities = _json("host capabilities", _run(repo, project, state, ["host", "capabilities"], home=home))
    if not isinstance(capabilities.get("platform"), str) or not isinstance(capabilities.get("python"), str):
        raise AssertionError(f"environment capability schema drift: {capabilities}")
    if not isinstance(capabilities.get("executables"), dict):
        raise AssertionError(f"environment executable capability schema drift: {capabilities}")

    return {
        "known_host_count": len(KNOWN_HOSTS),
        "known_host_sha256": hashlib.sha256("\n".join(sorted(KNOWN_HOSTS)).encode()).hexdigest(),
        "implicit_codex_equal": True,
        "codex": {"mode": codex.get("mode"), "enforced": codex.get("enforced"), "host": codex.get("host")},
        "claude_code": {"mode": claude.get("mode"), "enforced": claude.get("enforced"), "host": claude.get("host")},
        "detected_selected": {
            "codex_project_markers": list(by_host["codex"].get("project_markers") or []),
            "claude_user_markers": list(by_host["claude-code"].get("user_markers") or []),
        },
        "environment_schema_keys": sorted(capabilities),
        "platform_dynamic": capabilities["platform"],
        "python_dynamic": capabilities["python"],
    }


def _fabric_install(repo: Path, root: Path) -> dict[str, Any]:
    project = root / "fabric-project"
    state = root / "fabric-state"
    home = root / "fabric-home"
    project.mkdir(); state.mkdir(); home.mkdir(); (project / ".git").mkdir()
    skill_root = repo / "skills" / "syntavra"

    plan = _json("fabric platform plan", _run(repo, project, state, ["fabric", "platform-plan", "--host-name", "codex"], home=home))
    if plan.get("host") != "codex" or plan.get("scope") != "project" or plan.get("project") != str(project):
        raise AssertionError(f"fabric platform plan drift: {plan}")

    dry = _json("fabric install dry-run", _run(repo, project, state, ["fabric", "install", "codex", "--skill-root", str(skill_root), "--home", str(home), "--dry-run"], home=home))
    if dry.get("status") != "dry-run" or dry.get("verification", {}).get("dry_run") is not True:
        raise AssertionError(f"fabric install dry-run drift: {dry}")
    if (project / ".codex" / "config.toml").exists() or (project / ".agents" / "skills" / "syntavra").exists():
        raise AssertionError("fabric install dry-run mutated the isolated project")

    applied = _json("fabric install apply", _run(repo, project, state, ["fabric", "install", "codex", "--skill-root", str(skill_root), "--home", str(home)], home=home))
    transaction_id = str(applied.get("transaction_id") or "")
    if applied.get("status") != "applied" or applied.get("verification", {}).get("ok") is not True or not transaction_id.startswith("host-"):
        raise AssertionError(f"fabric install apply drift: {applied}")

    verified = _json("fabric verify install", _run(repo, project, state, ["fabric", "verify-install", "codex", "--skill-root", str(skill_root), "--home", str(home)], home=home))
    if verified.get("ok") is not True or verified.get("reasons") != []:
        raise AssertionError(f"fabric verify drift: {verified}")
    transactions = _json("fabric installations", _run(repo, project, state, ["fabric", "installations", "--host-name", "codex", "--skill-root", str(skill_root), "--home", str(home)], home=home))
    if not isinstance(transactions, list):
        raise AssertionError(f"fabric installations output drift: {transactions}")
    if len(transactions) != 1 or transactions[0].get("transaction_id") != transaction_id or transactions[0].get("status") != "applied":
        raise AssertionError(f"fabric transaction listing drift: {transactions}")

    rolled = _json("fabric rollback", _run(repo, project, state, ["fabric", "rollback-install", transaction_id, "--skill-root", str(skill_root), "--home", str(home)], home=home))
    if rolled.get("status") != "rolled-back" or rolled.get("verification") != {"ok": True, "rolled_back": True}:
        raise AssertionError(f"fabric rollback drift: {rolled}")
    after = _json("fabric verify after rollback", _run(repo, project, state, ["fabric", "verify-install", "codex", "--skill-root", str(skill_root), "--home", str(home)], home=home), 3)
    if after.get("ok") is not False or not {"missing-config", "missing-skill"}.issubset(set(after.get("reasons") or [])):
        raise AssertionError(f"fabric post-rollback verify drift: {after}")

    rolled_again = _json("fabric rollback idempotent", _run(repo, project, state, ["fabric", "rollback-install", transaction_id, "--skill-root", str(skill_root), "--home", str(home)], home=home))
    if rolled_again.get("status") != "rolled-back":
        raise AssertionError(f"fabric rollback idempotency drift: {rolled_again}")
    missing_rollback = _error4("fabric rollback unknown", _run(repo, project, state, ["fabric", "rollback-install", "host-missing", "--skill-root", str(skill_root), "--home", str(home)], home=home))
    unsupported = _error4("fabric unsupported host", _run(repo, project, state, ["fabric", "install", "not-a-host", "--skill-root", str(skill_root), "--home", str(home), "--dry-run"], home=home))
    doctor = _json("fabric doctor", _run(repo, project, state, ["fabric", "doctor"], home=home))

    with sqlite3.connect(state / "host-installations.sqlite3") as db:
        row = db.execute("SELECT status FROM host_install_transactions WHERE transaction_id=?", (transaction_id,)).fetchone()
    if row is None or row[0] != "rolled-back":
        raise AssertionError("fabric rollback durable transaction state drift")

    return {
        "plan": {"host": plan["host"], "scope": plan["scope"], "mode": plan["mode"], "verified_adapter": plan["verified_adapter"]},
        "dry_run": {"status": dry["status"], "change_count": len(dry.get("changes") or []), "mutated_target": False},
        "apply": {"status": applied["status"], "verification_ok": applied["verification"]["ok"], "transaction_id_shape": transaction_id.startswith("host-")},
        "verify": {"ok": verified["ok"], "reasons": verified["reasons"]},
        "transactions": {"count": len(transactions), "status": transactions[0]["status"]},
        "rollback": {"status": rolled["status"], "durable_status": row[0], "idempotent_status": rolled_again["status"]},
        "post_rollback_verify": {"exit": 3, "ok": after["ok"], "reasons": after["reasons"]},
        "negative": {
            "missing_rollback_exit": 4,
            "missing_rollback_error_type": missing_rollback["error"]["details"]["error"].split(":", 1)[0],
            "unsupported_host_exit": 4,
            "unsupported_host_error_type": unsupported["error"]["details"]["error"].split(":", 1)[0],
        },
        "doctor_keys": sorted(doctor),
    }


def _updates(repo: Path, root: Path) -> dict[str, Any]:
    project = root / "update-project"
    state = root / "update-state"
    home = root / "update-home"
    project.mkdir(); state.mkdir(); home.mkdir(); (project / ".git").mkdir()

    first_bytes = b"syntavra-update-fixture-v1\n"
    second_bytes = b"syntavra-update-fixture-v2\n"
    first_path = project / "update-v1.bin"; first_path.write_bytes(first_bytes)
    second_path = project / "update-v2.bin"; second_path.write_bytes(second_bytes)
    first_hash = hashlib.sha256(first_bytes).hexdigest(); second_hash = hashlib.sha256(second_bytes).hexdigest()

    def artifact(path: Path, digest: str) -> str:
        return json.dumps({"platform": "fixture", "architecture": "fixture", "filename": path.name, "sha256": digest, "size": path.stat().st_size}, separators=(",", ":"))

    first = _json("update install first", _run(repo, project, state, ["run", "update-install", first_path.name, artifact(first_path, first_hash), "--name", "fixture-tool"], home=home))
    if first.get("ok") is not True or first.get("status") != "installed" or first.get("installed_sha256") != first_hash:
        raise AssertionError(f"first update install drift: {first}")
    second = _json("update install second", _run(repo, project, state, ["run", "update-install", second_path.name, artifact(second_path, second_hash), "--name", "fixture-tool"], home=home))
    if second.get("ok") is not True or second.get("previous") != first_hash or second.get("installed_sha256") != second_hash:
        raise AssertionError(f"second update install drift: {second}")

    rolled = _json("update rollback", _run(repo, project, state, ["run", "update-rollback", "--name", "fixture-tool", "--sha256", first_hash], home=home))
    if rolled.get("ok") is not True or rolled.get("sha256") != first_hash:
        raise AssertionError(f"update rollback drift: {rolled}")
    target = Path(rolled["target"])
    if not target.is_file() or target.read_bytes() != first_bytes:
        raise AssertionError("update rollback did not restore exact previous artifact")

    no_backup = _json("update rollback missing", _run(repo, project, state, ["run", "update-rollback", "--name", "missing-tool"], home=home), 3)
    if no_backup != {"ok": False, "reason": "no matching backup"}:
        raise AssertionError(f"update missing-backup drift: {no_backup}")

    bad_artifact = _error4("update malformed artifact", _run(repo, project, state, ["run", "update-install", first_path.name, "{}", "--name", "bad-tool"], home=home))
    bad_checksum_json = artifact(first_path, "0" * 64)
    bad_checksum = _json("update bad checksum", _run(repo, project, state, ["run", "update-install", first_path.name, bad_checksum_json, "--name", "bad-checksum"], home=home), 3)
    if bad_checksum.get("ok") is not False or bad_checksum.get("status") not in {"failed", "rolled-back"} or "checksum mismatch" not in str(bad_checksum.get("detail")):
        raise AssertionError(f"update checksum fail-closed drift: {bad_checksum}")

    receipts = sorted((state / "unified" / "updates" / "update-receipts").glob("*.json")) if (state / "unified" / "updates" / "update-receipts").is_dir() else []
    return {
        "first": {"ok": first["ok"], "status": first["status"], "installed_sha256": first["installed_sha256"], "previous": first["previous"]},
        "second": {"ok": second["ok"], "status": second["status"], "installed_sha256": second["installed_sha256"], "previous": second["previous"]},
        "rollback": {"ok": rolled["ok"], "sha256": rolled["sha256"], "exact_restore": target.read_bytes() == first_bytes},
        "missing_backup": {"exit": 3, "value": no_backup},
        "malformed_artifact": {"exit": 4, "error_type": bad_artifact["error"]["details"]["error"].split(":", 1)[0]},
        "bad_checksum": {"exit": 3, "ok": bad_checksum["ok"], "status": bad_checksum["status"], "detail_has_checksum_mismatch": "checksum mismatch" in bad_checksum["detail"]},
        "target_under_temp_root": str(target).startswith(str(root)),
        "receipt_count": len(receipts),
    }


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-setup-host-") as directory:
        root = Path(directory)
        result = {
            "routes": _routes(fixture),
            "bootstrap_setup": _bootstrap_setup(repo, root, fixture),
            "host_detection": _host_detection(repo, root, fixture),
            "fabric_install": _fabric_install(repo, root),
            "updates": _updates(repo, root),
        }
    return {
        "ok": True,
        "schema_version": 1,
        "family": "setup-host",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        **result,
        "exit_policy": fixture["exit_policy"],
        "safety": fixture["safety"],
        "ownership_notes": fixture["ownership_notes"],
        "nondeterministic_fields": [
            "bootstrap session_id and started_at",
            "install/update transaction and receipt identifiers",
            "install/update timestamps and measured wall_time_ms",
            "host executable availability in environment_capabilities",
            "operating-system/platform and Python version fields",
            "temporary project/home/state paths"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python setup/repair/host reference behavior")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "setup-host",
            "engine": "python",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
