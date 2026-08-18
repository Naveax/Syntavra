#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTRACT_RELATIVE = Path("contracts/python/rust-feature-freeze-guard-v1.json")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _changed_paths(repo: Path, base: str, head: str) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "diff", "--name-status", "--find-renames", f"{base}...{head}"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"unable to diff {base}...{head}: {proc.stderr.strip()}")
    rows: list[dict[str, str]] = []
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        status = parts[0]
        paths = parts[1:]
        if status.startswith("R") and len(paths) == 2:
            rows.append({"status": status, "path": paths[0], "role": "rename-from"})
            rows.append({"status": status, "path": paths[1], "role": "rename-to"})
        elif paths:
            rows.append({"status": status, "path": paths[-1], "role": "path"})
    return rows


def _classify(path: str, contract: dict[str, Any]) -> str | None:
    protected = contract.get("protected") or {}
    if any(path.startswith(prefix) for prefix in protected.get("native_path_prefixes", [])):
        return "native"
    if any(path.startswith(prefix) for prefix in protected.get("remaining71_path_prefixes", [])):
        return "remaining71"
    if path in set(protected.get("promotion_authority_paths", [])):
        return "promotion-authority"
    return None


def verify_baseline(repo: Path, contract: dict[str, Any]) -> dict[str, Any]:
    authority = contract.get("authority") or {}
    expected = contract.get("expected") or {}
    python_authority = _read_json(repo / authority["python_authority"])
    registry = _read_json(repo / authority["capability_registry"])
    migration = _read_json(repo / authority["migration_baseline"])

    _require(python_authority.get("claim") == "PYTHON_FEATURE_DEVELOPMENT_AUTHORITY", "Python authority claim drift")
    freeze = python_authority.get("rust_freeze") or {}
    authority_expected = python_authority.get("expected") or {}
    _require(freeze.get("active") is True, "Python authority no longer freezes Rust")
    _require(freeze.get("feature_development_allowed") is False, "Rust feature development unexpectedly enabled")
    _require(freeze.get("production_promotion_allowed") is False, "Rust production promotion unexpectedly enabled")
    _require(freeze.get("native_counter_change_allowed") is False, "Rust native counter change unexpectedly enabled")
    _require(int(authority_expected.get("rust_implemented_native_routes", -1)) == int(expected["rust_implementation_coverage"]), "Rust implementation coverage drift")
    _require(int(authority_expected.get("rust_promoted_native_routes", -1)) == int(expected["rust_production_promoted"]), "Rust promoted baseline drift")
    _require(int(authority_expected.get("remaining_routes", -1)) == int(expected["remaining_parity_promotion"]), "Rust remaining baseline drift")

    python_complete = registry.get("python_complete") or {}
    _require(bool(python_complete.get("ready")) is bool(expected["python_complete"]), "Python COMPLETE readiness drift")
    _require(bool(python_complete.get("rust_resume_allowed")) is bool(expected["rust_resume_allowed"]), "Rust resume readiness drift")

    migration_baseline = migration.get("rust_baseline") or {}
    _require(int(migration_baseline.get("expected_promoted_native", -1)) == int(expected["rust_production_promoted"]), "migration promoted baseline drift")
    _require(int(migration_baseline.get("expected_remaining", -1)) == int(expected["remaining_parity_promotion"]), "migration remaining baseline drift")
    _require(int(migration_baseline.get("expected_remaining_owned", -1)) == int(expected["remaining_owned"]), "migration owned baseline drift")
    _require(int(migration_baseline.get("expected_unowned", -1)) == int(expected["unowned"]), "migration unowned baseline drift")
    _require(int(migration_baseline.get("atomic_promotion_target", -1)) == int(expected["atomic_promotion_target"]), "migration atomic target drift")
    migration_policy = migration.get("policy") or {}
    _require(migration_policy.get("no_native_counter_change_in_this_gate") is True, "migration baseline allows native counter change")
    _require(migration_policy.get("selector_ownership_is_not_behavioral_parity") is True, "migration selector/parity boundary drift")

    return {
        "python_complete": bool(python_complete.get("ready")),
        "rust_resume_allowed": bool(python_complete.get("rust_resume_allowed")),
        "implementation_coverage": int(expected["rust_implementation_coverage"]),
        "production_promoted": int(expected["rust_production_promoted"]),
        "remaining": int(expected["remaining_parity_promotion"]),
    }


def check(
    repo: Path,
    *,
    base: str,
    head: str,
    maintenance_exception: str | None = None,
    maintenance_reason: str | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "freeze guard schema drift")
    _require(contract.get("claim") == "RUST_FEATURE_FREEZE_ENFORCED", "freeze guard claim drift")
    _require(contract.get("strict") is True, "freeze guard must remain strict")
    baseline = verify_baseline(repo, contract)

    maintenance = contract.get("maintenance") or {}
    allowed_exceptions = list(maintenance.get("allowed_exception_types") or [])
    if maintenance_exception is not None:
        _require(maintenance_exception in allowed_exceptions, f"unknown maintenance exception: {maintenance_exception}")
        _require(bool((maintenance_reason or "").strip()), "maintenance exception requires explicit reason")

    changes = _changed_paths(repo, base, head)
    protected_changes: list[dict[str, str]] = []
    denied_changes: list[dict[str, str]] = []
    for row in changes:
        path_class = _classify(row["path"], contract)
        if path_class is None:
            continue
        enriched = {**row, "class": path_class}
        protected_changes.append(enriched)
        if maintenance_exception is None:
            denied_changes.append(enriched)
            continue
        if path_class != "native":
            denied_changes.append(enriched)

    ok = not denied_changes
    return {
        "ok": ok,
        "schema_version": 1,
        "claim": "RUST_FEATURE_FREEZE_ENFORCED" if ok else "RUST_FEATURE_FREEZE_VIOLATION",
        "base": base,
        "head": head,
        "baseline": baseline,
        "maintenance_exception": maintenance_exception,
        "maintenance_reason_present": bool((maintenance_reason or "").strip()),
        "changed_path_count": len(changes),
        "protected_change_count": len(protected_changes),
        "denied_change_count": len(denied_changes),
        "protected_changes": protected_changes,
        "denied_changes": denied_changes,
        "policy": (
            "Ordinary Python-first CI denies native, Remaining-71 parity-program, and promotion-authority changes. "
            "An explicit maintenance exception may admit native-only repair changes, but never Remaining-71 or production-promotion authority changes."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a Git diff against the Syntavra Rust feature-freeze policy.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--maintenance-exception")
    parser.add_argument("--maintenance-reason")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = check(
            Path(args.repo),
            base=args.base,
            head=args.head,
            maintenance_exception=args.maintenance_exception,
            maintenance_reason=args.maintenance_reason,
        )
    except Exception as exc:
        report = {"ok": False, "schema_version": 1, "claim": "RUST_FEATURE_FREEZE_GUARD_ERROR", "error": f"{type(exc).__name__}: {exc}"}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
