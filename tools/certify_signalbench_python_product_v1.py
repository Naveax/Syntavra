#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.python_phase_state import validate_python_complete_state

from syntavra_runtime.signalbench import ArmSpec, RunResult, SignalBenchProtocol, SignalBenchRunner, TaskSpec
from syntavra_runtime.signalbench_hardened import UsageReceipt

CONTRACT = Path("contracts/python/signalbench-python-product-v1.json")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW = Path(".github/workflows/signalbench-python-product.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def fixture_result(arm: str, repetition: int, quota: float) -> RunResult:
    values = dict(
        run_id=f"{arm}-{repetition}", task_id="certify-task", arm_id=arm, repetition=repetition,
        success=True, verifier_success=True, verified_work=1.0, wall_seconds=1.0, exit_code=0,
        fresh_input_tokens=100, cached_input_tokens=20, output_tokens=10, reasoning_tokens=5,
        quota_cost=quota, model_turns=1, tool_calls=1, wait_calls=0, compactions=0,
        security_regressions=0, verifier_skips=0, repository_tree="a" * 40,
        prompt_hash="b" * 64, verifier_hash="c" * 64, permissions_hash="d" * 64,
        cache_mode="cold", artifact_dir="artifact", provider_observed=True, provider="openai",
        model="fixture-model", request_id_hash=(f"{repetition:064x}" if arm == "base" else f"{repetition + 1000:064x}"),
        provider_receipt_hash="1" * 64, arm_version="1.2.3" if arm == "base" else "4.5.6",
        reasoning="high", context_window=200000, hardware_hash="2" * 64,
        provider_response_hash=("3" if arm == "base" else "4") * 64, usage_receipt_hash="",
        repository_commit="6" * 40, task_hash="5" * 64, timeout_seconds=1200.0,
    )
    receipt = UsageReceipt.seal(
        task_id=values["task_id"], arm_id=values["arm_id"], repetition=values["repetition"],
        cache_mode=values["cache_mode"], provider=values["provider"], request_id_hash=values["request_id_hash"],
        provider_response_hash=values["provider_response_hash"], fresh_input_tokens=values["fresh_input_tokens"],
        cached_input_tokens=values["cached_input_tokens"], output_tokens=values["output_tokens"],
        reasoning_tokens=values["reasoning_tokens"], quota_cost=quota, hardware_hash=values["hardware_hash"],
    )
    return RunResult(**{**values, "usage_receipt_hash": receipt.receipt_hash})


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    require(repo == ROOT, "SignalBench product certifier must run against its own checkout")
    contract = read_json(repo / CONTRACT)
    require(contract.get("schema_version") == 1, "SignalBench product schema drift")
    require(contract.get("family") == "signalbench-python-product", "SignalBench product family drift")
    require(contract.get("claim") == "SIGNALBENCH_PYTHON_PRODUCT_V1", "SignalBench product claim drift")
    require(contract.get("strict") is True, "SignalBench product must remain strict")
    frozen = contract.get("frozen_identity") or {}
    for key in (
        "repository_git_tree_must_match", "repository_commit_must_match", "repository_worktree_must_be_clean",
        "git_workspace_preserved", "host_environment_isolated", "explicit_environment_inheritance_only", "exact_product_version_required",
        "exact_model_required", "exact_reasoning_required", "context_window_bound", "hardware_identity_bound",
        "provider_identity_bound", "prompt_verifier_permissions_cache_bound", "template_placeholders_fail_closed",
    ):
        require(frozen.get(key) is True, f"SignalBench frozen identity disabled: {key}")
    measurement = contract.get("measurement") or {}
    require(measurement.get("provider_observed_usage_required") is True, "provider observed usage gate disabled")
    require(measurement.get("sealed_usage_receipts_required") is True, "usage receipt gate disabled")
    require(measurement.get("failure_inclusive") is True, "failure-inclusive metric disabled")
    require(measurement.get("minimum_paired_samples_for_superiority") >= 10, "paired sample floor weakened")
    require(measurement.get("security_regressions_allowed") == 0, "security regression gate weakened")
    require(measurement.get("verifier_skips_allowed") == 0, "verifier skip gate weakened")
    require(measurement.get("duplicate_result_keys_fail_closed") is True, "duplicate result gate disabled")
    require(measurement.get("duplicate_receipts_fail_closed") is True, "duplicate receipt gate disabled")
    require(measurement.get("missing_arm_pairs_fail_closed") is True, "missing arm gate disabled")
    require(measurement.get("receipt_hashes_require_sha256_hex") is True, "receipt hash format gate disabled")
    require(contract.get("runtime", {}).get("usage_receipt_ledger") == "syntavra_runtime/usage_receipt_ledger.py:UsageReceiptLedger", "usage receipt ledger authority drift")
    require((repo / "syntavra_runtime/usage_receipt_ledger.py").is_file(), "usage receipt ledger implementation missing")

    with tempfile.TemporaryDirectory(prefix="syntavra-signalbench-product-") as temp:
        root = Path(temp)
        project = root / "repo"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "signalbench@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "SignalBench Fixture"], check=True)
        (project / "fixture.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "--", "fixture.txt"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-q", "-m", "fixture"], check=True)
        commit = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
        tree = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD^{tree}"], text=True).strip()
        task = TaskSpec("certify-task", "known-edit", "fix", str(project), tree, (sys.executable, "-c", "pass"), repository_commit=commit)
        arms = [
  ArmSpec("base", "host", (sys.executable, "adapter.py"), "1.2.3", "fixture-model", "high", 200000),
  ArmSpec("candidate", "host", (sys.executable, "adapter.py"), "4.5.6", "fixture-model", "high", 200000),
        ]
        frozen_report = SignalBenchRunner(root / "validation").validate_product([task], arms)
        require(frozen_report["ok"] is True, f"valid frozen product rejected: {frozen_report}")
        wrong_tree = TaskSpec(**{**asdict(task), "repository_tree": "0" * len(tree)})
        require("task:certify-task:repository-tree-mismatch" in SignalBenchRunner(root / "validation").validate_product([wrong_tree], arms)["reasons"], "tree mismatch did not fail closed")
        wrong_commit = TaskSpec(**{**asdict(task), "repository_commit": "0" * len(commit)})
        require("task:certify-task:repository-commit-mismatch" in SignalBenchRunner(root / "validation").validate_product([wrong_commit], arms)["reasons"], "commit mismatch did not fail closed")
        placeholder = ArmSpec("base", "host", (sys.executable, "adapter.py"), "pin-exact-version", "fixture-model", "high", 200000)
        require(any("version-not-exact" in item for item in SignalBenchRunner(root / "validation").validate_product([task], [placeholder, arms[1]])["reasons"]), "placeholder version did not fail closed")

    rows = []
    for repetition in range(1, 11):
        rows.extend([fixture_result("base", repetition, 10.0), fixture_result("candidate", repetition, 1.0)])
    comparison = SignalBenchRunner.compare(rows, baseline_arm="base", candidate_arm="candidate")
    require(comparison.get("claimable_superiority") is True, f"valid hardened fixture did not pass: {comparison}")
    require(comparison.get("comparison_authority") == "HardenedSignalBench.compare", "legacy compare authority still active")
    tampered = RunResult(**{**asdict(rows[-1]), "quota_cost": 0.5})
    tamper_report = SignalBenchRunner.compare(rows[:-1] + [tampered], baseline_arm="base", candidate_arm="candidate")
    require(tamper_report.get("claimable_superiority") is False and tamper_report.get("receipt_errors"), "receipt tampering did not fail closed")
    missing_report = SignalBenchRunner.compare(rows[:-1], baseline_arm="base", candidate_arm="candidate")
    require(missing_report.get("claimable_superiority") is False and any(item.get("reason") == "missing-arm" for item in missing_report.get("invalid", [])), "missing arm did not fail closed")
    duplicate_report = SignalBenchRunner.compare(rows + [rows[-1]], baseline_arm="base", candidate_arm="candidate")
    require(duplicate_report.get("claimable_superiority") is False and any(item.get("reason") == "duplicate-result-key" for item in duplicate_report.get("invalid", [])), "duplicate result did not fail closed")

    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY):
        require((repo / relative).is_file(), f"missing SignalBench enforcement surface: {relative}")
    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    require("tests.runtime.test_signalbench_python_product_v1" in workflow, "SignalBench workflow lost regression suite")
    require("tools/certify_signalbench_python_product_v1.py" in workflow, "SignalBench workflow lost certifier")
    for pin in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        require(pin in workflow, f"SignalBench workflow pin drift: {pin}")
    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    require("tests.runtime.test_signalbench_python_product_v1" in release, "Release Main lost SignalBench regression")
    require("tools/certify_signalbench_python_product_v1.py" in release, "Release Main lost SignalBench certifier")
    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    require('".github/workflows/signalbench-python-product.yml"' in pins, "immutable pin policy lost SignalBench workflow")
    cli = (repo / "syntavra_runtime/cli.py").read_text(encoding="utf-8")
    require("runner.validate_product" in cli, "public SignalBench validate route is not product-grade")

    registry = read_json(repo / REGISTRY)
    by_id = {item["id"]: item for item in registry["capabilities"]}
    require(by_id["observability_attribution_v1"]["state"] == "certified", "Observability Attribution must be certified first")
    lifecycle_state = by_id["signalbench_python_product_v1"]["state"]
    require(lifecycle_state in {"partial", "implemented", "verified", "certified"}, "invalid SignalBench lifecycle state")
    validate_python_complete_state(registry)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return {
        "ok": True,
        "schema_version": 1,
        "claim": "SIGNALBENCH_PYTHON_PRODUCT_V1",
        "exact_head": head,
        "runtime_ready": True,
        "lifecycle_state": lifecycle_state,
        "admission_ready": lifecycle_state in {"implemented", "verified", "certified"},
        "frozen_repository_identity": True,
        "exact_repository_commit": True,
        "git_workspace_preserved": True,
        "host_environment_isolated": True,
        "exact_arm_identity": True,
        "provider_observed_usage": True,
        "sealed_usage_receipts": True,
        "usage_receipt_ledger_authority": True,
        "hardened_comparison_authority": True,
        "failure_inclusive": True,
        "external_superiority_proven": False,
        "python_complete_ready": True,
        "rust_resume_allowed": False,
        "rust": {"production_promoted": 174, "remaining_parity_promotion": 71, "feature_development_frozen": True},
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra SignalBench Python Product v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:
        report = {"ok": False, "schema_version": 1, "claim": "SIGNALBENCH_PYTHON_PRODUCT_V1", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
