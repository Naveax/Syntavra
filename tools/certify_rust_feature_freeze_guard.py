#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.certify_python_authority import _assert_no_route_identity_copy
from tools.check_rust_feature_freeze import CONTRACT_RELATIVE, verify_baseline

WORKFLOW_RELATIVE = Path(".github/workflows/rust-feature-freeze-guard.yml")
RELEASE_GATE_RELATIVE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY_RELATIVE = Path("tests/runtime/test_release_action_pins.py")
REGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _head(repo: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, f"Rust freeze certifier must run against its own checkout: {repo} != {ROOT}")
    contract = _read_json(repo / CONTRACT_RELATIVE)
    _require(contract.get("schema_version") == 1, "Rust freeze schema drift")
    _require(contract.get("family") == "rust-feature-freeze-guard", "Rust freeze family drift")
    _require(contract.get("phase") == "python-first", "Rust freeze phase drift")
    _require(contract.get("claim") == "RUST_FEATURE_FREEZE_ENFORCED", "Rust freeze claim drift")
    _require(contract.get("strict") is True, "Rust freeze guard must remain strict")
    _assert_no_route_identity_copy(contract)

    policy = contract.get("policy") or {}
    for key in (
        "python_complete_required_for_resume",
        "route_identity_list_must_not_be_duplicated",
        "selector_ownership_is_not_behavioral_parity",
        "no_silent_fallback",
    ):
        _require(policy.get(key) is True, f"Rust freeze policy disabled: {key}")
    for key in (
        "rust_feature_development_allowed",
        "rust_production_promotion_allowed",
        "native_counter_change_allowed",
        "remaining71_parity_work_allowed",
        "promotion_authority_change_allowed",
    ):
        _require(policy.get(key) is False, f"Rust freeze policy unexpectedly allows: {key}")

    maintenance = contract.get("maintenance") or {}
    _require(
        maintenance.get("allowed_exception_types") == ["build-blocker", "security", "data-loss", "contract-blocker"],
        "Rust maintenance exception vocabulary drift",
    )
    _require(maintenance.get("ordinary_ci_uses_exception") is False, "ordinary CI must not use maintenance bypass")
    _require(maintenance.get("exception_may_change_native_paths") is True, "native repair exception policy drift")
    _require(maintenance.get("exception_may_change_remaining71_programs") is False, "maintenance may not resume Remaining-71 parity work")
    _require(maintenance.get("exception_may_change_promotion_authority") is False, "maintenance may not change promotion authority")
    _require(maintenance.get("explicit_reason_required") is True, "maintenance exception must require reason")

    post_complete = contract.get("post_python_complete") or {}
    _require(post_complete.get("rust_feature_development_allowed") is False, "retired Rust feature development unexpectedly enabled")
    _require(post_complete.get("remaining71_parity_work_allowed") is False, "retired Remaining-71 work unexpectedly enabled")
    _require(post_complete.get("rust_retired") is True, "Rust retirement state drift")
    _require(post_complete.get("explicit_reactivation_required") is True, "Rust explicit-reactivation gate drift")
    _require(post_complete.get("rust_production_promotion_allowed") is False, "post-completion production promotion must remain frozen")
    _require(post_complete.get("native_counter_change_allowed") is False, "post-completion native counter changes must remain frozen")
    _require(post_complete.get("promotion_authority_change_allowed") is False, "post-completion promotion authority changes must remain frozen")

    baseline = verify_baseline(repo, contract)
    _require(baseline["python_complete"] is True, "Python COMPLETE is not admitted")
    _require(baseline["rust_resume_allowed"] is False, "Rust must remain retired/frozen")
    _require(baseline["production_promoted"] == 174, "Rust promotion count drift")
    _require(baseline["remaining"] == 71, "Rust remaining count drift")

    registry = _read_json(repo / REGISTRY_RELATIVE)
    by_id = {item["id"]: item for item in registry.get("capabilities", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    _require((by_id.get("capability_completeness_registry_v1") or {}).get("state") == "certified", "completeness registry must be certified before Rust freeze admission")
    guard_entry = by_id.get("rust_feature_freeze_guard_v1") or {}
    _require(guard_entry.get("state") in {"implemented", "verified", "certified"}, "Rust freeze guard registry state is invalid")

    for relative in (WORKFLOW_RELATIVE, RELEASE_GATE_RELATIVE, PIN_POLICY_RELATIVE, Path("tools/check_rust_feature_freeze.py"), Path("tests/runtime/test_rust_feature_freeze_guard.py")):
        _require((repo / relative).is_file(), f"missing Rust freeze enforcement surface: {relative.as_posix()}")

    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: rust-feature-freeze-guard-${{ github.event.pull_request.number || github.ref }}" in workflow, "Rust freeze workflow concurrency not PR/ref scoped")
    _require("tests.runtime.test_rust_feature_freeze_guard" in workflow, "Rust freeze workflow lost regression suite")
    _require("tools/certify_rust_feature_freeze_guard.py" in workflow, "Rust freeze workflow lost certifier")
    _require("tools/check_rust_feature_freeze.py" in workflow, "Rust freeze workflow lost diff checker")
    _require("--maintenance-exception" not in workflow, "ordinary Rust freeze CI must not pass maintenance exception")
    _require("actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow, "Rust freeze checkout pin drift")
    _require("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow, "Rust freeze setup-python pin drift")

    release_gate = (repo / RELEASE_GATE_RELATIVE).read_text(encoding="utf-8")
    _require("tests.runtime.test_rust_feature_freeze_guard" in release_gate, "release-main gate lost Rust freeze regression")
    _require("tools/certify_rust_feature_freeze_guard.py" in release_gate, "release-main gate lost Rust freeze certifier")

    pin_policy = (repo / PIN_POLICY_RELATIVE).read_text(encoding="utf-8")
    _require('".github/workflows/rust-feature-freeze-guard.yml"' in pin_policy, "immutable action policy does not cover Rust freeze workflow")

    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact HEAD")
    return {
        "ok": True,
        "schema_version": 1,
        "family": "rust-feature-freeze-guard",
        "claim": "RUST_FEATURE_FREEZE_ENFORCED",
        "exact_head": exact_head,
        "guard_admission_ready": True,
        "python_complete_ready": baseline["python_complete"],
        "rust_resume_allowed": baseline["rust_resume_allowed"],
        "rust": {
            "implementation_coverage": baseline["implementation_coverage"],
            "production_promoted": baseline["production_promoted"],
            "remaining_parity_promotion": baseline["remaining"],
            "feature_development_frozen": not baseline["rust_resume_allowed"],
            "production_promotion_frozen": True,
        },
        "maintenance": {
            "ordinary_ci_bypass": False,
            "native_only_explicit_exception_supported": True,
            "promotion_authority_exception_supported": False,
        },
        "claim_boundary": "This certificate enforces Rust retirement/freeze independently of Python COMPLETE. Rust feature/parity work and production promotion remain closed at 174/245 with 71 remaining until an explicit future reactivation authority changes that state.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the Syntavra Rust feature-freeze guard.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {"ok": False, "schema_version": 1, "family": "rust-feature-freeze-guard", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
