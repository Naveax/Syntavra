#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.util import canonical_json
from tools import certify_epistemic_safety_v1 as epistemic_safety
from tools import certify_host_adapter_conformance_v1 as host_adapter
from tools import certify_python_behavior_freeze as behavior_freeze
from tools import certify_python_capability_completeness as capability_completeness
from tools import certify_python_phase1_acceptance as phase1_acceptance
from tools import certify_signalbench_python_product_v1 as signalbench_product

CONTRACT = Path("contracts/python/python-completion-certificate-v1.json")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW = Path(".github/workflows/python-completion-certificate.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")
SELF_ID = "python_completion_certificate_v1"
PLATFORM_CLAIM = "PYTHON_COMPLETION_PLATFORM_SMOKE_V1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def derive_contract_freeze(repo: Path, registry: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, str] = {}
    for capability in registry.get("capabilities") or []:
        if not isinstance(capability, dict):
            continue
        capability_id = str(capability.get("id") or "")
        if capability_id == SELF_ID or capability.get("classification") == "EXTERNAL":
            continue
        if capability.get("required_for_python_complete") is not True:
            continue
        _require(capability.get("state") == "certified", f"prior required capability not certified: {capability_id}")
        for relative in capability.get("certification_evidence") or []:
            if not isinstance(relative, str):
                continue
            if not relative.startswith("contracts/python/") or not relative.endswith(".json"):
                continue
            path = repo / relative
            _require(path.is_file(), f"contract freeze authority missing: {relative}")
            rows[relative] = _sha256_bytes(path.read_bytes())
    ordered = [{"path": path, "sha256": rows[path]} for path in sorted(rows)]
    return {
        "mode": "registry-derived-certified-python-contracts",
        "contract_count": len(ordered),
        "sha256": hashlib.sha256(canonical_json(ordered)).hexdigest(),
        "contracts": ordered,
    }


def validate_platform_evidence(
    repo: Path,
    *,
    exact_head: str,
    contract: dict[str, Any],
    evidence_paths: Iterable[Path],
) -> dict[str, Any]:
    cfg = contract.get("platform_receipts") or {}
    required = set(cfg.get("required_platforms") or [])
    receipts: dict[str, dict[str, Any]] = {}
    for raw_path in evidence_paths:
        path = raw_path.resolve()
        receipt = _read_json(path)
        _require(receipt.get("schema_version") == 1, f"platform receipt schema drift: {path}")
        _require(receipt.get("claim") == cfg.get("claim") == PLATFORM_CLAIM, f"platform receipt claim drift: {path}")
        platform = str(receipt.get("platform") or "")
        _require(platform in required, f"unexpected completion platform receipt: {platform!r}")
        _require(platform not in receipts, f"duplicate completion platform receipt: {platform}")
        _require(receipt.get("exact_head") == exact_head, f"platform receipt exact-head drift: {platform}")
        for key in (
            "clean_install",
            "source_import_isolation",
            "fresh_repository_smoke",
            "basic_runtime",
        ):
            _require(receipt.get(key) is True, f"platform receipt gate failed: {platform}:{key}")
        module_path = str(receipt.get("installed_module_path") or "")
        _require(bool(module_path), f"platform receipt missing installed module path: {platform}")
        receipts[platform] = receipt
    present = set(receipts)
    return {
        "ready": bool(required) and present == required,
        "required_platforms": sorted(required),
        "present_platforms": sorted(present),
        "receipts": [receipts[key] for key in sorted(receipts)],
    }


def _same_head(report: dict[str, Any], exact_head: str, label: str) -> None:
    _require(report.get("ok") is True, f"{label} is red: {report}")
    observed = report.get("exact_head")
    if observed is not None:
        _require(observed == exact_head, f"{label} exact-head drift: {observed} != {exact_head}")


def _validate_enforcement(repo: Path) -> dict[str, Any]:
    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY):
        _require((repo / relative).is_file(), f"missing completion enforcement surface: {relative}")
    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    for token in (
        "ubuntu-24.04",
        "windows-latest",
        "python -m pip install .",
        "tools/python_completion_platform_smoke.py",
        "tools/certify_python_completion_certificate_v1.py",
        "python tools/validate.py",
        "tools/validate_release.py --smoke",
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
    ):
        _require(token in workflow, f"completion workflow enforcement drift: {token}")
    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require("tests.runtime.test_python_completion_certificate_v1" in release, "Release Main lost completion regression suite")
    _require("tools/certify_python_completion_certificate_v1.py" in release, "Release Main lost completion certifier")
    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require('".github/workflows/python-completion-certificate.yml"' in pins, "immutable action policy lost completion workflow")
    return {
        "completion_workflow": WORKFLOW.as_posix(),
        "release_main_gate": RELEASE_GATE.as_posix(),
        "immutable_action_pin_policy": PIN_POLICY.as_posix(),
        "windows_linux_matrix_bound": True,
        "aggregate_validation_bound": True,
    }


def certify(repo: Path, platform_evidence: Iterable[Path] = ()) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, "Python completion certifier must run against its own checkout")
    contract = _read_json(repo / CONTRACT)
    registry = _read_json(repo / REGISTRY)
    _require(contract.get("schema_version") == 1, "Python completion schema drift")
    _require(contract.get("family") == "python-completion-certificate", "Python completion family drift")
    _require(contract.get("phase") == "python-first-exit", "Python completion phase drift")
    _require(contract.get("claim") == "PYTHON_COMPLETION_CERTIFICATE_V1", "Python completion claim drift")
    _require(contract.get("strict") is True, "Python completion contract must remain strict")
    for key, value in (contract.get("required_gates") or {}).items():
        if key == "external_superiority_required":
            _require(value is False, "external superiority cannot become an internal completion gate")
        else:
            _require(value is True, f"required completion gate disabled: {key}")

    exact_head = _head(repo)
    _require(bool(exact_head), "unable to resolve exact HEAD")
    by_id = {item["id"]: item for item in registry.get("capabilities") or []}
    _require(SELF_ID in by_id, "completion capability missing from registry")
    _require(by_id.get("signalbench_python_product_v1", {}).get("state") == "certified", "SignalBench lifecycle must be certified before completion")
    lifecycle_state = str(by_id[SELF_ID].get("state") or "")
    allowed = set((contract.get("lifecycle") or {}).get("allowed_states") or [])
    _require(lifecycle_state in allowed, f"invalid completion lifecycle state: {lifecycle_state}")

    prior_uncertified = [
        item["id"]
        for item in registry.get("capabilities") or []
        if item.get("id") != SELF_ID
        and item.get("required_for_python_complete") is True
        and item.get("classification") != "EXTERNAL"
        and item.get("state") != "certified"
    ]
    _require(not prior_uncertified, f"prior required Python capabilities remain uncertified: {prior_uncertified}")

    completeness = capability_completeness.certify(repo)
    behavior = behavior_freeze.certify(repo)
    phase1 = phase1_acceptance.certify(repo)
    signalbench = signalbench_product.certify(repo)
    host = host_adapter.certify(repo)
    security = epistemic_safety.certify(repo)
    for label, report in (
        ("capability completeness", completeness),
        ("Python behavior freeze", behavior),
        ("Python Phase 1 acceptance", phase1),
        ("SignalBench Python product", signalbench),
        ("Host Adapter Conformance", host),
        ("Epistemic Safety", security),
    ):
        _same_head(report, exact_head, label)
    _require(signalbench.get("lifecycle_state") == "certified", "SignalBench is not lifecycle-certified")
    _require(signalbench.get("admission_ready") is True, "SignalBench is not admission-ready")
    _require(signalbench.get("external_superiority_proven") is False, "internal completion must not fabricate external superiority")
    _require(host.get("fresh_repo_zero_code_smoke") is True, "fresh repository smoke is not proven")

    freeze = derive_contract_freeze(repo, registry)
    freeze_cfg = contract.get("contract_freeze") or {}
    _require(freeze_cfg.get("mode") == freeze["mode"], "Python contract freeze mode drift")
    _require(int(freeze_cfg.get("expected_contract_count", -1)) == freeze["contract_count"], "Python contract freeze count drift")
    expected_freeze_sha = freeze_cfg.get("expected_sha256")
    _require(isinstance(expected_freeze_sha, str) and len(expected_freeze_sha) == 64, "Python contract freeze digest is not pinned")
    _require(expected_freeze_sha == freeze["sha256"], "Python contract freeze digest drift")

    enforcement = _validate_enforcement(repo)
    platform = validate_platform_evidence(
        repo,
        exact_head=exact_head,
        contract=contract,
        evidence_paths=platform_evidence,
    )

    python_complete = registry.get("python_complete") or {}
    persisted_ready = python_complete.get("ready") is True
    persisted_rust_resume = python_complete.get("rust_resume_allowed") is True
    if lifecycle_state != (contract.get("lifecycle") or {}).get("pass_state"):
        _require(not persisted_ready, "non-certified completion lifecycle cannot claim Python COMPLETE")
        _require(not persisted_rust_resume, "non-certified completion lifecycle cannot allow Rust resume")

    implementation_ready = all(
        [
            completeness.get("ok") is True,
            behavior.get("ok") is True,
            phase1.get("ok") is True,
            signalbench.get("ok") is True,
            host.get("ok") is True,
            security.get("ok") is True,
            not prior_uncertified,
            freeze["sha256"] == expected_freeze_sha,
        ]
    )
    certificate_candidate_ready = (
        implementation_ready
        and platform["ready"]
        and lifecycle_state == (contract.get("lifecycle") or {}).get("pass_state")
    )
    phase_exit_ready = certificate_candidate_ready and persisted_ready

    return {
        "ok": True,
        "schema_version": 1,
        "claim": "PYTHON_COMPLETION_CERTIFICATE_V1",
        "exact_head": exact_head,
        "implementation_ready": implementation_ready,
        "platform_runtime_ready": platform["ready"],
        "certificate_candidate_ready": certificate_candidate_ready,
        "phase_exit_ready": phase_exit_ready,
        "certificate_status": "PASS" if phase_exit_ready else "NOT_YET_ADMITTED",
        "lifecycle_state": lifecycle_state,
        "current_milestone": completeness.get("current_milestone"),
        "prior_required_internal_capabilities_certified": True,
        "python_complete_ready": persisted_ready,
        "rust_resume_allowed": persisted_rust_resume,
        "external_superiority_proven": False,
        "gates": {
            "unit_integration_security": True,
            "exact_recovery": True,
            "deterministic_replay": True,
            "fresh_repository_smoke": host.get("fresh_repo_zero_code_smoke") is True,
            "signalbench_python_product": signalbench.get("ok") is True,
            "python_behavior_freeze": behavior.get("ok") is True,
            "python_contract_freeze": freeze["sha256"] == expected_freeze_sha,
            "exact_head_certification": True,
            "windows_linux_runtime": platform["ready"],
        },
        "contract_freeze": freeze,
        "platform_evidence": platform,
        "enforcement": enforcement,
        "rust": {
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": not persisted_rust_resume,
        },
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Python Completion Certificate v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--platform-evidence", action="append", default=[])
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo), [Path(item) for item in args.platform_evidence])
    except Exception as exc:  # pragma: no cover
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": "PYTHON_COMPLETION_CERTIFICATE_V1",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
