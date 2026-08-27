#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.python_phase_state import validate_python_complete_state

from syntavra_runtime.adapter_platform import ADAPTERS, AdapterRegistry
from syntavra_runtime.adapter_runtime import AdapterMaturity, AdapterPlatformRuntime
from syntavra_runtime.host_adapters import KNOWN_HOSTS, host_spec, negotiate
from syntavra_runtime.integration_matrix import IntegrationMatrix
from syntavra_runtime.product_surface import PlatformAdapterRegistry
from syntavra_runtime.zero_friction import ZeroFrictionManager

CONTRACT = Path("contracts/python/host-adapter-conformance-v1.json")
REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")
WORKFLOW = Path(".github/workflows/host-adapter-conformance.yml")
RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, "host adapter conformance certifier must run against its own checkout")
    contract = _read_json(repo / CONTRACT)
    _require(contract.get("schema_version") == 1, "host adapter conformance schema drift")
    _require(contract.get("family") == "host-adapter-conformance", "host adapter family drift")
    _require(contract.get("phase") == "python-first", "host adapter phase drift")
    _require(contract.get("claim") == "HOST_ADAPTER_CONFORMANCE_V1", "host adapter claim drift")
    _require(contract.get("strict") is True, "host adapter contract must remain strict")

    aliases = contract.get("canonical_aliases") or {}
    canonical_rows = IntegrationMatrix.records("host")
    canonical = [row["integration_id"] for row in canonical_rows]
    _require(len(canonical) == 18 and len(set(canonical)) == 18, "canonical host inventory drift")
    _require(set(aliases) == set(canonical), "canonical alias source coverage drift")
    _require(len(set(aliases.values())) == 18, "canonical aliases are not one-to-one")
    legacy_ids = {item.adapter_id for item in ADAPTERS}
    for host, adapter_id in aliases.items():
        _require(host in KNOWN_HOSTS, f"missing canonical host contract: {host}")
        _require(adapter_id in legacy_ids, f"missing legacy adapter alias target: {host}->{adapter_id}")

    matrix = IntegrationMatrix.validate()
    product = PlatformAdapterRegistry.validate()
    legacy = AdapterRegistry.validate()
    _require(matrix.get("ok") is True and matrix.get("hosts") == 18, "integration matrix host gate failed")
    _require(product.get("ok") is True and product.get("adapters") == 18, "runtime product adapter registry drift")
    _require(product.get("missing_matrix_hosts") == [], "runtime product registry missing canonical hosts")
    _require(product.get("extra_adapters") == [], "runtime product registry has noncanonical hosts")
    _require(legacy.get("ok") is True and legacy.get("inventory_gate") is True, "legacy adapter contract registry failed")
    _require(legacy.get("live_certified") == 0, "internal adapter registry fabricated live certification")

    negotiation: dict[str, str] = {}
    for row in canonical_rows:
        host = row["integration_id"]
        claims = set(row["capabilities"])
        spec = host_spec(host)
        decision = negotiate(host, runtime_available=True, installed=None)
        if "mcp" in claims:
            _require(spec.supports_mcp, f"{host}: matrix claims MCP without host capability")
        if "pre-tool" in claims:
            _require(spec.supports_pre_tool_hook, f"{host}: matrix claims pre-tool without hook capability")
        if "post-tool" in claims:
            _require(spec.supports_post_tool_hook, f"{host}: matrix claims post-tool without hook capability")
        _require(decision["mode"] != "UNSUPPORTED", f"{host}: canonical host negotiates to unsupported")
        negotiation[host] = str(decision["mode"])
    _require(negotiation.get("aider") == "INSTRUCTION_ONLY", "Aider instruction-only negotiation drift")

    with tempfile.TemporaryDirectory(prefix="syntavra-host-conformance-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        manager = ZeroFrictionManager(project, state_root=state)
        plan = manager.install_plan(all_hosts=True, profile="minimal")
        _require(set(plan.installable_hosts) == set(canonical), "fresh repo all-host install plan drift")
        _require(plan.contract_only_hosts == (), "canonical matrix contains contract-only host")
        dry = manager.install(all_hosts=True, dry_run=True, profile="minimal")
        _require(dry.get("ok") is True, f"all-host dry-run failed: {dry}")
        _require(len(dry.get("host_results", [])) == 18, "all-host dry-run did not cover 18 hosts")
        _require(all(row.get("status") == "dry-run" for row in dry["host_results"]), "all-host dry-run wrote host state")

        fresh_project = root / "fresh-project"
        fresh_state = root / "fresh-state"
        fresh_project.mkdir()
        fresh = ZeroFrictionManager(fresh_project, state_root=fresh_state)
        before = fresh.doctor()
        _require(before.get("ok") is True and before.get("ready_to_install") is True, "fresh-repo doctor failed")
        install = fresh.install(dry_run=False, profile="minimal")
        _require(install.get("ok") is True and install.get("host_results") == [], "fresh repo fabricated host installation")
        after = fresh.doctor()
        _require(after.get("ok") is True and after.get("installed") is True, "fresh repo zero-code install doctor failed")

        adapter_project = root / "adapter-project"
        adapter_project.mkdir()
        runtime = AdapterPlatformRuntime(adapter_project, root / "adapter-state")
        denied = runtime.certify("codex-cli", {})
        _require(denied.ok is False and denied.maturity == AdapterMaturity.ENFORCED, "missing external receipt did not fail closed")
        admitted = runtime.certify("codex-cli", {
            "host": "codex",
            "host_version": "external-fixture",
            "clean_install": True,
            "tool_interception": True,
            "context_interception": True,
            "security_denial": True,
            "session_restore": True,
            "artifact_hash": "sha256:" + "0" * 64,
        })
        _require(admitted.ok is True and admitted.maturity == AdapterMaturity.CERTIFIED, "valid external receipt shape was not admitted")
        _require("external execution receipt" in admitted.claim_boundary, "external live certification boundary drift")

    for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY):
        _require((repo / relative).is_file(), f"missing host adapter enforcement surface: {relative}")
    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    _require("tests.runtime.test_host_adapter_conformance_v1" in workflow, "host adapter workflow lost regression suite")
    _require("tools/certify_host_adapter_conformance_v1.py" in workflow, "host adapter workflow lost certifier")
    for pin in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(pin in workflow, f"host adapter workflow pin drift: {pin}")
    release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
    _require("tests.runtime.test_host_adapter_conformance_v1" in release, "Release Main lost host adapter regression")
    _require("tools/certify_host_adapter_conformance_v1.py" in release, "Release Main lost host adapter certifier")
    pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
    _require('".github/workflows/host-adapter-conformance.yml"' in pins, "immutable pin policy lost host adapter workflow")

    registry = _read_json(repo / REGISTRY)
    by_id = {item["id"]: item for item in registry["capabilities"]}
    _require(by_id["output_intelligence_v1"]["state"] == "certified", "Output Intelligence must be certified first")
    lifecycle_state = by_id["host_adapter_conformance_v1"]["state"]
    _require(lifecycle_state in {"partial", "implemented", "verified", "certified"}, "invalid host adapter lifecycle state")
    validate_python_complete_state(registry)

    exact_head = _head(repo)
    return {
        "ok": True,
        "schema_version": 1,
        "claim": "HOST_ADAPTER_CONFORMANCE_V1",
        "exact_head": exact_head,
        "runtime_ready": True,
        "lifecycle_state": lifecycle_state,
        "admission_ready": lifecycle_state in {"implemented", "verified", "certified"},
        "canonical_host_count": 18,
        "canonical_alias_count": len(aliases),
        "all_host_dry_run_count": 18,
        "fresh_repo_zero_code_smoke": True,
        "aider_mode": negotiation["aider"],
        "external_receipt_boundary": True,
        "internal_live_certified": legacy["live_certified"],
        "python_complete_ready": True,
        "rust_resume_allowed": False,
        "rust": {
            "production_promoted": 174,
            "remaining_parity_promotion": 71,
            "feature_development_frozen": True,
        },
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra Host Adapter Conformance v1")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:  # pragma: no cover
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": "HOST_ADAPTER_CONFORMANCE_V1",
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
