from __future__ import annotations

from pathlib import Path
from typing import Any

from .platform_common import CHANNEL, VERSION, sha256_bytes
from .artifacts import (
    ArtifactRecord,
    ArtifactStore,
    ContextCompiler,
    ContextIRItem,
    ContextPack,
    FirewallReceipt,
    OutputFirewall,
)
from .semantic_intelligence import IncrementalCodeIntelligenceGraph
from .python_semantic_resolution import install as _install_python_semantic_resolution
from .semantic_services import (
    LanguageServiceRegistry as CompatibilityLanguageServiceRegistry,
    SemanticIndexImporter,
)
from .runtime_evidence import RuntimeEvidenceGraph
from .session_memory import SessionMemory
from .capability_security import CapabilityDecision, CapabilitySecurity
from .secretless_gateway import SecretlessProviderGateway
from .adapter_platform import ADAPTERS, AdapterContract, CodingAgent, AdapterRegistry
from .adapter_runtime import AdapterPlatformRuntime
from .sandbox_runtime import HardenedSandboxBroker
from .autonomous_agent import AutonomousCodingAgent
from .headless_runtime import HeadlessRuntime
from .interactive_console import InteractiveConsole
from .reliability_lab import ReliabilityLaboratory
from .update_manager import DistributionManager

# Install the evidence-aware parser on the shared graph class. This runs for the
# normal package and for portable entry points that import the platform module.
_install_python_semantic_resolution(IncrementalCodeIntelligenceGraph)
del _install_python_semantic_resolution

# Keep the historical semantic-services JSON contract while preserving the
# richer universal-language status object used by the canonical language CLI.
if not getattr(IncrementalCodeIntelligenceGraph, "_syntavra_language_status_compat", False):
    _language_status_core = IncrementalCodeIntelligenceGraph.language_status

    def _language_status_compat(
        self: IncrementalCodeIntelligenceGraph,
        repository_root: Path | None = None,
    ) -> dict[str, Any]:
        value = _language_status_core(self, repository_root)
        registry = value.get("language_registry", {})
        analyzers = value.get("sandboxed_analyzers", {})
        lsp = value.get("lsp_services", {})
        universal_boundary = str(value.get("claim_boundary") or "")
        value["declared"] = int(registry.get("registered_languages", 0))
        value["available"] = (
            len(registry.get("adapters", ()))
            + int(analyzers.get("services", analyzers.get("declared", 0)) or 0)
            + int(lsp.get("services", lsp.get("declared", 0)) or 0)
        )
        value["universal_claim_boundary"] = universal_boundary
        value["claim_boundary"] = (
            "declared support is not live certification; unknown and future text languages remain navigable, "
            "while exact semantic claims require validated parser, analyzer, LSP, LSIF or SCIP evidence"
        )
        return value

    IncrementalCodeIntelligenceGraph.language_status = _language_status_compat
    IncrementalCodeIntelligenceGraph._syntavra_language_status_compat = True
    del _language_status_core

# Stable public name. This façade preserves the historical status payload while
# delegating all discovery and execution to the universal evidence-graded core.
NativeSandboxBroker = HardenedSandboxBroker


class LanguageServiceRegistry(CompatibilityLanguageServiceRegistry):
    def status(self, root: Path | None = None) -> dict[str, Any]:
        value = super().status(root)
        value["claim_boundary"] = (
            "declared support is not live certification; lexical fallback is universal, while exact semantic "
            "support requires a validated adapter, hash-pinned analyzer, hash-pinned LSP server, or fresh LSIF/SCIP evidence"
        )
        return value


class SyntavraPlatform:
    """One facade for Syntavra's shared AI-engineering control plane."""

    def __init__(self, project: Path, state_root: Path):
        self.project = project.resolve(strict=False)
        self.state_root = state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(self.state_root / "artifacts")
        self.firewall = OutputFirewall(self.artifacts)
        self.context = ContextCompiler(self.artifacts)
        self.graph = IncrementalCodeIntelligenceGraph(self.state_root / "semantic-graph.sqlite3")
        self.runtime_evidence = RuntimeEvidenceGraph(self.state_root / "runtime-evidence.sqlite3")

        # Backward-compatible public attributes. They are façades over the same
        # universal, evidence-graded model rather than the historical fixed list.
        self.language_services = LanguageServiceRegistry()
        self.semantic_importer = SemanticIndexImporter(self.graph)

        project_id = sha256_bytes(str(self.project).encode("utf-8"))
        self.memory = SessionMemory(self.state_root / "session-memory.sqlite3", project_id=project_id)
        self.security = CapabilitySecurity(self.state_root / "security")
        self.sandbox = HardenedSandboxBroker(self.state_root)
        self.agent = CodingAgent(project=self.project, graph=self.graph, memory=self.memory, security=self.security)
        self.autonomous_agent = AutonomousCodingAgent(
            self.project,
            self.state_root,
            graph=self.graph,
            memory=self.memory,
            sandbox=self.sandbox,
        )
        self.adapters = AdapterPlatformRuntime(self.project, self.state_root)
        self.headless = HeadlessRuntime(self.state_root / "headless.sqlite3", self.state_root, broker=self.sandbox)
        self.console = InteractiveConsole()
        self.reliability = ReliabilityLaboratory(self.state_root)
        self.distribution = DistributionManager(self.state_root / "bin", self.state_root)

    def status(self) -> dict[str, Any]:
        return {
            "product": "Syntavra",
            "version": VERSION,
            "channel": CHANNEL,
            "project": str(self.project),
            "artifacts": self.artifacts.stats(),
            "semantic_graph": self.graph.stats(),
            "runtime_evidence": self.runtime_evidence.stats(),
            "language_platform": self.graph.language_status(self.project),
            "memory": self.memory.stats(),
            "headless": self.headless.stats(),
            "sandbox": self.sandbox.health(self.project),
            "adapters": AdapterRegistry.validate(),
            "providers": sorted(SecretlessProviderGateway.PROVIDERS),
            "capabilities": {
                "typed_context_compiler": True,
                "pre_context_output_firewall": True,
                "content_addressed_exact_recovery": True,
                "incremental_semantic_graph": True,
                "universal_future_language_fallback": True,
                "sandboxed_language_analyzers": True,
                "generic_hash_pinned_lsp": True,
                "atomic_lsif_scip_import": True,
                "runtime_evidence_graph": True,
                "multi_view_session_memory": True,
                "signed_single_use_capabilities": True,
                "secretless_provider_gateway": True,
                "cli_and_non_cli_adapters": True,
                "bounded_autonomous_agent": True,
                "probed_native_sandbox": True,
                "headless_job_runtime": True,
                "interactive_token_console": True,
                "fault_injection_laboratory": True,
                "atomic_update_manager": True,
            },
            "claim_boundary": "functional capabilities are internally tested; external superiority and live certification remain receipt-gated",
        }

    def doctor(self) -> dict[str, Any]:
        artifact_check = self.artifacts.verify()
        adapter_check = AdapterRegistry.validate()
        sandbox = self.sandbox.health(self.project)
        language_platform = self.graph.language_status(self.project)
        return {
            "ok": artifact_check["ok"] and adapter_check["ok"],
            "artifact_integrity": artifact_check,
            "adapters": adapter_check,
            "semantic_graph": self.graph.stats(),
            "runtime_evidence": self.runtime_evidence.stats(),
            "language_platform": language_platform,
            "memory": self.memory.stats(),
            "headless": self.headless.stats(),
            "sandbox": sandbox,
            "version_locked": VERSION == "0.0.1" and CHANNEL == "pre-release",
            "strict_native_sandbox_ready": sandbox["strict_ready"],
        }


def manifest() -> dict[str, Any]:
    return {
        "product": "Syntavra",
        "version": VERSION,
        "channel": CHANNEL,
        "runtime": "unified",
        "components": [
            "context-compiler",
            "output-firewall",
            "artifact-store",
            "semantic-intelligence",
            "runtime-evidence",
            "universal-language-platform",
            "sandboxed-language-services",
            "generic-lsp-bridge",
            "semantic-index-import",
            "session-memory",
            "capability-security",
            "execution-sandbox",
            "provider-gateway",
            "adapter-platform",
            "coding-agent",
            "headless-runtime",
            "interactive-console",
            "reliability-laboratory",
            "distribution-manager",
            "signalbench",
        ],
        "adapter_contract": AdapterRegistry.validate(),
        "external_claims": "NOT_PROVEN_WITHOUT_EXTERNAL_RECEIPTS",
    }


__all__ = [
    "ADAPTERS",
    "AdapterContract",
    "AdapterPlatformRuntime",
    "AdapterRegistry",
    "ArtifactRecord",
    "ArtifactStore",
    "AutonomousCodingAgent",
    "CapabilityDecision",
    "CapabilitySecurity",
    "CodingAgent",
    "ContextCompiler",
    "ContextIRItem",
    "ContextPack",
    "DistributionManager",
    "FirewallReceipt",
    "HeadlessRuntime",
    "HardenedSandboxBroker",
    "IncrementalCodeIntelligenceGraph",
    "InteractiveConsole",
    "LanguageServiceRegistry",
    "NativeSandboxBroker",
    "OutputFirewall",
    "ReliabilityLaboratory",
    "RuntimeEvidenceGraph",
    "SecretlessProviderGateway",
    "SemanticIndexImporter",
    "SessionMemory",
    "SyntavraPlatform",
    "manifest",
]
