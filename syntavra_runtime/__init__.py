"""Syntavra 0.0.1 pre-release unified AI engineering runtime."""

__version__ = "0.0.1"
__release_channel__ = "pre-release"

import os as _os

if _os.environ.get("SYNTAVRA_PORTABLE_BOOTSTRAP") == "1":
    __all__ = ["__version__", "__release_channel__"]
else:
    from .platform_adapter_extension import install as _install_platform_adapter_extension
    from .provider_mcp_extension import install as _install_provider_mcp_extension
    from .provider_proxy_extension import install as _install_provider_proxy_extension
    from .ecosystem_mcp_extension import install as _install_ecosystem_mcp_extension
    from .product_extension import install as _install_product_extension
    from .production_extension import install as _install_production_extension
    from .mcp_enforcement_extension import install as _install_mcp_enforcement_extension

    _install_platform_adapter_extension()
    _install_provider_mcp_extension()
    _install_provider_proxy_extension()
    _install_ecosystem_mcp_extension()
    _install_product_extension()
    _install_production_extension()
    _install_mcp_enforcement_extension()

    from .mcp_server import MCPServer as _MCPServer
    from . import cli as _legacy_cli
    _MCPServer.VERSION = __version__
    _legacy_cli.VERSION = __version__

    del _MCPServer
    del _legacy_cli
    del _install_platform_adapter_extension
    del _install_provider_mcp_extension
    del _install_provider_proxy_extension
    del _install_ecosystem_mcp_extension
    del _install_product_extension
    del _install_production_extension
    del _install_mcp_enforcement_extension

    from .adapter_runtime import AdapterMaturity, AdapterPlatformRuntime, AdapterReceipt
    from .arm_runner import ArmExecutionPolicy, ArmRunReceipt, SecureArmRunner
    from .autonomous_agent import (
        AgentAttempt,
        AgentMode,
        AgentRunReceipt,
        AgentState,
        AgentTask,
        AutonomousCodingAgent,
        CallablePatchProvider,
        PatchProposal,
        PatchProvider,
    )
    from .backup import BackupResult, StateBackupManager
    from .unified_config import ConfigManager, ConfigSnapshot
    from .platform import (
        ADAPTERS,
        AdapterContract,
        AdapterRegistry,
        ArtifactStore,
        CapabilityDecision,
        CapabilitySecurity,
        CodingAgent,
        ContextCompiler,
        ContextIRItem,
        ContextPack,
        DistributionManager,
        FirewallReceipt,
        HeadlessRuntime,
        IncrementalCodeIntelligenceGraph,
        InteractiveConsole,
        LanguageServiceRegistry,
        NativeSandboxBroker,
        OutputFirewall,
        ReliabilityLaboratory,
        RuntimeEvidenceGraph,
        SecretlessProviderGateway,
        SemanticIndexImporter,
        SessionMemory,
        SyntavraPlatform,
        manifest as platform_manifest,
    )
    from .data_router import DataRoutePolicy, DataRouteResult, DataRouter
    from .evidence import EvidenceStore
    from .execution_sandbox import ExecutionReceipt, SandboxBackend, SandboxPolicy
    from .external_benchmarks import (
        ExternalBenchmarkGate,
        ExternalBenchmarkReceipt,
        ExternalSuiteRegistry,
        ExternalSuiteRunner,
        ExternalSuiteSpec,
    )
    from .headless_runtime import HeadlessJob, JobState
    from .identity import Authorizer, CapabilityTokenIssuer, Principal
    from .infinite_context import ActiveContextPlan, RecursiveExecutionEngine, RecursiveTask, UnboundedContextCoordinator
    from .integration_matrix import IntegrationMatrix, IntegrationSpec
    from .interactive_console import ConsoleSnapshot, TokenPanel
    from .job_scheduler import DurableJobScheduler, JobSpec
    from .long_context_quality import LongContextQualityGate, LongContextReceipt
    from .long_session_planner import ContextPlanPolicy, LongSessionPlanner
    from .mcp_policy import MCPAuthorizationDecision, MCPToolPolicy
    from .policy_rollout import PolicyRolloutManager, VerifiedPolicyObservation
    from .policy_tuner import AdaptivePolicyTuner, PolicyObservation, PolicyRecommendation
    from .product_maturity import DistributionReceipt, OnboardingReceipt, ProductMaturityGate, ReleaseReceipt
    from .product_surface import (
        MCPProfile,
        MeasuredBenchmarkGate,
        PlatformAdapter,
        PlatformAdapterRegistry,
        ProductSurface,
        ProviderUsageReceipt,
        ReceiptValidator,
        SessionAnalyticsStore,
        ToolRouteDecision,
        ToolRoutingEnforcer,
    )
    from .proxy_product import ProxyPreset, ProxyProductRegistry
    from .public_proof import BetaReceipt, PublicProofGate, WorkloadSpec
    from .release_identity import ReleaseIdentity, VersionLockError
    from .reliability_lab import FaultInjector, FaultResult, FuzzResult, ReliabilityReport
    from .runtime_pipeline import CanonicalRequestEnvelope, UnifiedRuntimePipeline
    from .runtime_evidence import EvidenceEdge, EvidenceNode
    from .sdk import SDKInvocation, SyntavraClient
    from .semantic_services import LSPClient, LSPProtocolError, LanguageServiceSpec, LanguageServiceStatus
    from .service_manager import ProviderProxyServiceManager, ServicePlan, ServiceSpec
    from .session_product import SessionContinuityController
    from .paired_benchmark import CodingCorpusPlanner, PairedSchedule, SuperiorityGate
    from .semantic_structure import GraphEdge, GraphNode, SemanticGraph
    from .update_manager import UpdateArtifact, UpdateManifest, UpdateReceipt
    from .zero_friction import ZeroFrictionManager

    __all__ = [
        "__version__", "__release_channel__", "ADAPTERS", "ActiveContextPlan",
        "AdapterContract", "AdapterMaturity", "AdapterPlatformRuntime", "AdapterReceipt",
        "AdapterRegistry", "AdaptivePolicyTuner", "AgentAttempt", "AgentMode", "AgentRunReceipt",
        "AgentState", "AgentTask", "ArmExecutionPolicy", "ArmRunReceipt", "ArtifactStore",
        "Authorizer", "AutonomousCodingAgent", "BackupResult", "BetaReceipt",
        "CallablePatchProvider", "CanonicalRequestEnvelope", "CapabilityDecision",
        "CapabilitySecurity", "CapabilityTokenIssuer", "CodingAgent", "CodingCorpusPlanner",
        "ConfigManager", "ConfigSnapshot", "ConsoleSnapshot", "ContextCompiler", "ContextIRItem",
        "ContextPack", "ContextPlanPolicy", "DataRoutePolicy", "DataRouteResult", "DataRouter",
        "DistributionManager", "DistributionReceipt", "DurableJobScheduler", "EvidenceEdge",
        "EvidenceNode", "EvidenceStore", "ExecutionReceipt", "ExternalBenchmarkGate",
        "ExternalBenchmarkReceipt", "ExternalSuiteRegistry", "ExternalSuiteRunner",
        "ExternalSuiteSpec", "FaultInjector", "FaultResult", "FirewallReceipt", "FuzzResult",
        "GraphEdge", "GraphNode", "HeadlessJob", "HeadlessRuntime",
        "IncrementalCodeIntelligenceGraph", "IntegrationMatrix", "IntegrationSpec",
        "InteractiveConsole", "JobSpec", "JobState", "LSPClient", "LSPProtocolError",
        "LanguageServiceRegistry", "LanguageServiceSpec", "LanguageServiceStatus",
        "LongContextQualityGate", "LongContextReceipt", "LongSessionPlanner", "MCPAuthorizationDecision",
        "MCPProfile", "MCPToolPolicy", "MeasuredBenchmarkGate", "NativeSandboxBroker",
        "OnboardingReceipt", "OutputFirewall", "PairedSchedule", "PatchProposal", "PatchProvider",
        "PlatformAdapter", "PlatformAdapterRegistry", "PolicyObservation", "PolicyRecommendation",
        "PolicyRolloutManager", "Principal", "ProductMaturityGate", "ProductSurface",
        "ProviderProxyServiceManager", "ProviderUsageReceipt", "ProxyPreset", "ProxyProductRegistry",
        "PublicProofGate", "ReceiptValidator", "RecursiveExecutionEngine", "RecursiveTask",
        "ReleaseIdentity", "ReleaseReceipt", "ReliabilityLaboratory", "ReliabilityReport",
        "RuntimeEvidenceGraph", "SDKInvocation", "SandboxBackend", "SandboxPolicy",
        "SecureArmRunner", "SemanticGraph", "SemanticIndexImporter", "SecretlessProviderGateway",
        "ServicePlan", "ServiceSpec", "SessionAnalyticsStore", "SessionContinuityController",
        "SessionMemory", "StateBackupManager", "SuperiorityGate", "SyntavraClient",
        "SyntavraPlatform", "TokenPanel", "ToolRouteDecision", "ToolRoutingEnforcer",
        "UnifiedRuntimePipeline", "UnboundedContextCoordinator", "UpdateArtifact", "UpdateManifest",
        "UpdateReceipt", "VerifiedPolicyObservation", "VersionLockError", "WorkloadSpec",
        "ZeroFrictionManager", "platform_manifest",
    ]

del _os
