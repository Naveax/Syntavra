from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .platform_common import CHANNEL, VERSION, canonical_json, sha256_bytes
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
from .semantic_services import LanguageServiceRegistry, SemanticIndexImporter
from .runtime_evidence import RuntimeEvidenceGraph
from .session_memory import SessionMemory
from .capability_security import CapabilityDecision, CapabilitySecurity
from .secretless_gateway import SecretlessProviderGateway
from .adapter_platform import ADAPTERS, AdapterContract, CodingAgent, AdapterRegistry
from .adapter_runtime import AdapterPlatformRuntime
from .execution_sandbox import NativeSandboxBroker
from .autonomous_agent import AutonomousCodingAgent
from .headless_runtime import HeadlessRuntime
from .interactive_console import InteractiveConsole
from .reliability_lab import ReliabilityLaboratory
from .update_manager import DistributionManager


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
        self.language_services = LanguageServiceRegistry()
        self.semantic_importer = SemanticIndexImporter(self.runtime_evidence)
        project_id = sha256_bytes(str(self.project).encode("utf-8"))
        self.memory = SessionMemory(self.state_root / "session-memory.sqlite3", project_id=project_id)
        self.security = CapabilitySecurity(self.state_root / "security")
        self.sandbox = NativeSandboxBroker(self.state_root)
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
            "version": VERSION,
            "channel": CHANNEL,
            "project": str(self.project),
            "artifacts": self.artifacts.stats(),
            "semantic_graph": self.graph.stats(),
            "runtime_evidence": self.runtime_evidence.stats(),
            "language_services": self.language_services.status(),
            "memory": self.memory.stats(),
            "headless": self.headless.stats(),
            "sandbox": self.sandbox.describe(self.project),
            "adapters": AdapterRegistry.validate(),
            "providers": sorted(SecretlessProviderGateway.PROVIDERS),
            "capabilities": {
                "typed_context_compiler": True,
                "pre_context_output_firewall": True,
                "content_addressed_exact_recovery": True,
                "incremental_semantic_graph": True,
                "lsp_scip_lsif_services": True,
                "runtime_evidence_graph": True,
                "multi_view_session_memory": True,
                "signed_single_use_capabilities": True,
                "secretless_provider_gateway": True,
                "cli_and_non_cli_adapters": True,
                "bounded_autonomous_agent": True,
                "native_sandbox_broker": True,
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
        sandbox = self.sandbox.describe(self.project)
        language_services = self.language_services.status()
        return {
            "ok": artifact_check["ok"] and adapter_check["ok"],
            "artifact_integrity": artifact_check,
            "adapters": adapter_check,
            "semantic_graph": self.graph.stats(),
            "runtime_evidence": self.runtime_evidence.stats(),
            "language_services": language_services,
            "memory": self.memory.stats(),
            "headless": self.headless.stats(),
            "sandbox": sandbox,
            "version_locked": VERSION == "0.0.1" and CHANNEL == "pre-release",
            "strict_native_sandbox_ready": sandbox["strict_ready"],
        }


def manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "channel": CHANNEL,
        "runtime": "unified",
        "components": [
            "context-compiler",
            "output-firewall",
            "artifact-store",
            "semantic-intelligence",
            "runtime-evidence",
            "language-services",
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
