from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from syntavra_runtime.semantic_wire_ir import HandleBinding, SemanticAtom, SemanticWireCompiler
from syntavra_runtime.subtask_router import (
    AutomaticSubtaskDelegator,
    CommunicationMediumRouter,
    EndpointCapabilities,
    MediumCostEvidence,
    ProviderWorkEvidence,
    ReceiptReference,
    RouteDecision,
)


CONTRACT_RELATIVE = Path("contracts/python/u5-p0-04-communication-medium-router-v1.json")
PUBLIC_SURFACE_RELATIVE = Path("contracts/engine/dual-engine-public-surface-v2.json")
RUNTIME_RELATIVE = Path("syntavra_runtime/subtask_router.py")
SWIR_RELATIVE = Path("syntavra_runtime/semantic_wire_ir.py")
PROVIDER_RELATIVE = Path("syntavra_runtime/provider_gateway.py")
TEST_RELATIVE = Path("tests/runtime/test_communication_medium_router.py")
WORKFLOW_RELATIVE = Path(".github/workflows/u5-p0-04-communication-medium-router.yml")
DOC_RELATIVE = Path("docs/U5_P0_04_COMMUNICATION_MEDIUM_ROUTER.md")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, encoding="utf-8").strip()


def _endpoint(
    endpoint_id: str,
    *,
    ownership: str = "local",
    domain: str = "task",
    media: tuple[str, ...] = ("text",),
    provider: str = "",
    handles: bool = False,
    receipts: bool = False,
    latent_id: str = "",
) -> EndpointCapabilities:
    return EndpointCapabilities(endpoint_id, ownership, domain, media, provider, handles, receipts, latent_id)


def _load_contract(repo: Path) -> dict[str, Any]:
    path = repo / CONTRACT_RELATIVE
    _require(path.is_file(), f"missing contract: {CONTRACT_RELATIVE}")
    data = json.loads(path.read_text(encoding="utf-8"))
    _require(data["mechanism_id"] == "U5-P0-04", "mechanism id drift")
    _require(data["wave"] == "TE-U32", "wave drift")
    _require(data["classification"] == "NEW_HARDEN", "classification drift")
    matrix = data["endpoint_capability_matrix"]
    _require(matrix["text_fallback_required"] is True, "text fallback weakened")
    _require(matrix["hosted_latent_allowed"] is False, "hosted latent boundary weakened")
    _require(matrix["provider_alias_never_implies_local_ownership"] is True, "locality authority weakened")
    routing = data["routing_policy"]
    _require(routing["verified_end_to_end_cost_precedes_nominal_payload_size"] is True, "cost routing weakened")
    _require(routing["router_transports_payload"] is False, "parallel transport authority introduced")
    _require(data["provider_accounting"]["provider_savings_claim"] is False, "provider claim boundary weakened")
    return data


def _runtime_probe() -> dict[str, Any]:
    router = CommunicationMediumRouter(SemanticWireCompiler(lambda text: len(text.split())))
    serialized_src = _endpoint("agent-a", media=("handle", "receipt", "swir", "text"))
    serialized_dst = _endpoint("agent-b", media=("handle", "receipt", "swir", "text"), handles=True, receipts=True)
    matrix = router.capability_matrix(serialized_src, serialized_dst)
    _require(all(matrix[name] for name in ("silence", "handle", "receipt", "swir", "text")), "serialized capability matrix drift")
    _require(not any(matrix[name] for name in ("embedding", "hidden_state", "kv")), "latent media appeared without explicit compatibility")

    handle = HandleBinding("H1", "artifact", "evidence:canonical", "d" * 64, recovery_ref="evidence:exact")
    handle_route = router.route(
        serialized_src,
        serialized_dst,
        "apply the verified patch after reading the exact canonical artifact",
        exact_required=True,
        handle=handle,
        cost_evidence=(
            MediumCostEvidence("handle", 1, "exact", True, "frozen:handle"),
            MediumCostEvidence("text", 9, "exact", True, "frozen:text"),
        ),
    )
    _require(handle_route.medium == "handle" and handle_route.payload == "H1", "exact handle route drift")

    delegation = AutomaticSubtaskDelegator().plan("Implement provider routing. Verify tests and coverage.")
    _require(delegation.delegated is True, "delegation probe did not split")
    receipt_route = router.route(
        serialized_src,
        serialized_dst,
        "delegation fallback",
        receipt=ReceiptReference("delegation-plan", delegation.receipt_hash),
        cost_evidence=(
            MediumCostEvidence("receipt", 1, "semantic", True, "frozen:receipt"),
            MediumCostEvidence("text", 8, "exact", True, "frozen:text"),
        ),
    )
    _require(receipt_route.medium == "receipt", "receipt route drift")
    _require(receipt_route.payload.endswith(delegation.receipt_hash), "delegation receipt was re-authored")

    natural = "apply patch after reading canonical source and run verifier"
    atoms = (SemanticAtom("PATCH", natural, "P"),)
    swir_route = router.route(
        serialized_src,
        serialized_dst,
        natural,
        swir_atoms=atoms,
        cost_evidence=(
            MediumCostEvidence("swir", 2, "semantic", True, "frozen:swir"),
            MediumCostEvidence("text", 8, "exact", True, "frozen:text"),
        ),
    )
    _require(swir_route.medium == "swir" and swir_route.payload == "P", "SWIR delegation drift")

    no_expansion = router.route(
        serialized_src,
        serialized_dst,
        "short",
        swir_atoms=(SemanticAtom("SHORT", "short", "this compact form is longer"),),
        cost_evidence=(MediumCostEvidence("text", 1, "exact", True, "frozen:text"),),
    )
    _require(no_expansion.medium == "text", "SWIR no-expansion did not preserve text")

    cost_route = router.route(
        serialized_src,
        serialized_dst,
        "one two three four five six seven eight",
        swir_atoms=(SemanticAtom("PAYLOAD", "one two three four five six seven eight", "P"),),
        cost_evidence=(
            MediumCostEvidence("swir", 50, "semantic", True, "paired:swir-total"),
            MediumCostEvidence("text", 5, "exact", True, "paired:text-total"),
        ),
    )
    _require(cost_route.medium == "text", "nominal compactness overrode verified end-to-end cost")

    hosted_latent_rejected = False
    try:
        _endpoint("remote", ownership="hosted", domain="provider", media=("text", "kv"), provider="openrouter")
    except ValueError:
        hosted_latent_rejected = True
    _require(hosted_latent_rejected, "hosted endpoint admitted KV")

    local_src = _endpoint("local-a", media=("text", "kv"), latent_id="llama-kv-v1")
    local_dst = _endpoint("local-b", ownership="owned", media=("text", "kv"), latent_id="llama-kv-v1")
    local_route = router.route(
        local_src,
        local_dst,
        "fallback payload",
        latent_refs={"kv": "kv://slot/7"},
        cost_evidence=(
            MediumCostEvidence("kv", 1, "exact", True, "local:kv"),
            MediumCostEvidence("text", 5, "exact", True, "local:text"),
        ),
    )
    _require(local_route.medium == "kv", "compatible local KV route missing")

    isolated = _endpoint("isolated", ownership="owned", domain="isolated", media=("text", "kv"), latent_id="llama-kv-v1")
    isolated_route = router.route(
        local_src,
        isolated,
        "fallback payload",
        latent_refs={"kv": "kv://slot/7"},
        cost_evidence=(MediumCostEvidence("text", 5, "exact", True, "local:text"),),
    )
    _require(isolated_route.medium == "text", "latent state crossed security domain")

    hosted = _endpoint("provider", ownership="hosted", domain="provider", provider="openai")
    provider_route = router.route(
        _endpoint("caller"),
        hosted,
        "provider payload",
        cost_evidence=(MediumCostEvidence("text", 4, "exact", True, "paired:route"),),
        provider_work=ProviderWorkEvidence(100, 30, 2, 1, True, "provider:baseline", "provider:selected"),
    )
    _require(provider_route.provider_visible_tokens_avoided == 70, "paired provider token accounting drift")
    _require(provider_route.provider_visible_calls_avoided == 1, "paired provider call accounting drift")
    _require(provider_route.provider_savings_claim is False, "U5-P0-04 promoted a provider savings claim")
    _require(RouteDecision.verify_receipt(provider_route.receipt()), "route receipt verification failed")

    status = router.status()
    _require(status["router_owns_transport"] is False, "router became transport authority")
    _require(status["router_owns_swir"] is False, "router became SWIR authority")
    _require(status["router_owns_receipt_semantics"] is False, "router redefined receipt semantics")

    return {
        "capability_matrix_defined": True,
        "exact_handle_route": True,
        "delegation_receipt_reused": True,
        "swir_delegated_to_existing_compiler": True,
        "swir_no_expansion_text_fallback": True,
        "verified_cost_beats_nominal_payload_size": True,
        "hosted_latent_rejected": True,
        "compatible_local_kv_route": True,
        "cross_domain_latent_rejected": True,
        "paired_provider_tokens_avoided": provider_route.provider_visible_tokens_avoided,
        "paired_provider_calls_avoided": provider_route.provider_visible_calls_avoided,
        "provider_savings_claim": False,
    }


def _validate_public_surface(repo: Path) -> dict[str, Any]:
    data = json.loads((repo / PUBLIC_SURFACE_RELATIVE).read_text(encoding="utf-8"))
    python_surface = data.get("python_surface")
    _require(isinstance(python_surface, dict), "python public surface missing")
    _require(python_surface.get("module_count") == 234, "U5-P0-04 must not add a runtime module")
    _require(python_surface.get("public_command_count") == 245, "U5-P0-04 must not change public command count")
    return {
        "module_count": python_surface["module_count"],
        "public_command_count": python_surface["public_command_count"],
        "command_paths_sha256": python_surface["command_paths_sha256"],
    }


def _validate_enforcement(repo: Path) -> dict[str, str]:
    for relative in (RUNTIME_RELATIVE, SWIR_RELATIVE, PROVIDER_RELATIVE, TEST_RELATIVE, WORKFLOW_RELATIVE, DOC_RELATIVE):
        _require((repo / relative).is_file(), f"missing U5-P0-04 enforcement surface: {relative.as_posix()}")
    workflow = (repo / WORKFLOW_RELATIVE).read_text(encoding="utf-8")
    _require("group: u5-p0-04-communication-medium-router-${{ github.event.pull_request.number || github.ref }}" in workflow, "workflow concurrency is not PR/ref scoped")
    _require("tests.runtime.test_communication_medium_router" in workflow, "workflow lost dedicated regressions")
    _require("tools/validate_u5_p0_04_communication_medium_router.py" in workflow, "workflow lost exact-head certifier")
    _require("tools/verify_dual_engine_public_surface.py" in workflow, "workflow lost public-surface drift guard")
    for pin in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        _require(pin in workflow, f"action pin drift: {pin}")
    return {
        "runtime": RUNTIME_RELATIVE.as_posix(),
        "tests": TEST_RELATIVE.as_posix(),
        "workflow": WORKFLOW_RELATIVE.as_posix(),
        "doc": DOC_RELATIVE.as_posix(),
    }


def certify(repo: Path) -> dict[str, Any]:
    contract = _load_contract(repo)
    runtime = _runtime_probe()
    public_surface = _validate_public_surface(repo)
    enforcement = _validate_enforcement(repo)
    exact_head = _git(repo, "rev-parse", "HEAD")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=all")
    _require(not dirty, "exact-head certification requires a clean repository")
    return {
        "ok": True,
        "claim": "U5_P0_04_COMMUNICATION_MEDIUM_ROUTER",
        "mechanism_id": contract["mechanism_id"],
        "wave": contract["wave"],
        "exact_head": exact_head,
        "admission_ready": True,
        "runtime": runtime,
        "public_surface": public_surface,
        "enforcement": enforcement,
        "provider_savings_claim": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = certify(args.repo.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
