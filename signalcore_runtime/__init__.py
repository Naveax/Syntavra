"""SignalCore v0.0.1 pre-release unified dominance runtime."""

__version__ = "0.0.1"
__release_channel__ = "pre-release"

import os as _os

if _os.environ.get("SIGNALCORE_PORTABLE_BOOTSTRAP") == "1":
    __all__ = ["__version__", "__release_channel__"]
else:
    from .platform_adapter_extension import install as _install_platform_adapter_extension
    from .provider_mcp_extension import install as _install_provider_mcp_extension
    from .provider_proxy_extension import install as _install_provider_proxy_extension
    from .ecosystem_mcp_extension import install as _install_ecosystem_mcp_extension
    from .product_v5_extension import install as _install_product_v5_extension
    from .v6_extension import install as _install_v6_extension
    from .mcp_enforcement_extension import install as _install_mcp_enforcement_extension

    _install_platform_adapter_extension()
    _install_provider_mcp_extension()
    _install_provider_proxy_extension()
    _install_ecosystem_mcp_extension()
    _install_product_v5_extension()
    _install_v6_extension()
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
    del _install_product_v5_extension
    del _install_v6_extension
    del _install_mcp_enforcement_extension

    from .arm_runner import ArmExecutionPolicy, ArmRunReceipt, SecureArmRunner
    from .backup import BackupResult, StateBackupManager
    from .config_v6 import ConfigManager, ConfigSnapshot
    from .competitive_runtime_v7 import (
        ADAPTERS_V2, AdapterContractV2, CapabilityDecisionV2, CapabilitySecurityV2,
        CompetitiveRuntimeV7, ContentAddressedArtifactStoreV2, ContextCompilerV2, ContextIRItem,
        ContextPackV2, FirewallReceiptV2, IncrementalCodeIntelligenceGraph, ReferenceCodingAgentV2,
        SecretlessProviderGatewayV2, SessionMemoryDAGV2, UniversalAdapterRegistryV2,
        UniversalOutputFirewallV2, manifest as competitive_v7_manifest,
    )
    from .data_router import DataRoutePolicy, DataRouteResult, DataRouter
    from .evidence import EvidenceStore
    from .external_benchmarks import (
        ExternalBenchmarkGate,
        ExternalBenchmarkReceipt,
        ExternalSuiteRegistry,
        ExternalSuiteRunner,
        ExternalSuiteSpec,
    )
    from .identity import Authorizer, CapabilityTokenIssuer, Principal
    from .infinite_context import ActiveContextPlan, RecursiveExecutionEngine, RecursiveTask, UnboundedContextCoordinator
    from .integration_matrix import IntegrationMatrix, IntegrationSpec
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
    from .runtime_pipeline import CanonicalRequestEnvelope, UnifiedRuntimePipeline
    from .sdk import SDKInvocation, SignalCoreClient
    from .service_manager import ProviderProxyServiceManager, ServicePlan, ServiceSpec
    from .session_product import SessionContinuityController
    from .signalbench_v2 import CodingCorpusPlanner, PairedSchedule, SuperiorityGate
    from .structural_v2 import GraphEdge, GraphNode, StructuralGraphV2
    from .zero_friction import ZeroFrictionManager

    __all__ = [
        "__version__", "__release_channel__", "ActiveContextPlan", "AdaptivePolicyTuner",
        "ArmExecutionPolicy", "ArmRunReceipt", "Authorizer", "BackupResult", "BetaReceipt",
        "CanonicalRequestEnvelope", "CapabilityTokenIssuer", "CodingCorpusPlanner", "ConfigManager",
        "ConfigSnapshot", "ContextPlanPolicy", "DataRoutePolicy", "DataRouteResult", "DataRouter",
        "DistributionReceipt", "DurableJobScheduler", "EvidenceStore", "ExternalBenchmarkGate",
        "ExternalBenchmarkReceipt", "ExternalSuiteRegistry", "ExternalSuiteRunner", "ExternalSuiteSpec",
        "GraphEdge", "GraphNode", "IntegrationMatrix", "IntegrationSpec", "JobSpec",
        "LongContextQualityGate", "LongContextReceipt", "LongSessionPlanner", "MCPAuthorizationDecision",
        "MCPProfile", "MCPToolPolicy", "MeasuredBenchmarkGate", "OnboardingReceipt", "PairedSchedule",
        "PlatformAdapter", "PlatformAdapterRegistry", "PolicyObservation", "PolicyRecommendation",
        "PolicyRolloutManager", "Principal", "ProductMaturityGate", "ProductSurface",
        "ProviderProxyServiceManager", "ProviderUsageReceipt", "ProxyPreset", "ProxyProductRegistry",
        "PublicProofGate", "ReceiptValidator", "RecursiveExecutionEngine", "RecursiveTask",
        "ReleaseIdentity", "ReleaseReceipt", "SDKInvocation", "SecureArmRunner", "ServicePlan",
        "ServiceSpec", "SessionAnalyticsStore", "SessionContinuityController", "SignalCoreClient",
        "StateBackupManager", "StructuralGraphV2", "SuperiorityGate", "ToolRouteDecision",
        "ToolRoutingEnforcer", "UnifiedRuntimePipeline", "UnboundedContextCoordinator",
        "VerifiedPolicyObservation", "VersionLockError", "WorkloadSpec", "ZeroFrictionManager",
        "ADAPTERS_V2", "AdapterContractV2", "CapabilityDecisionV2", "CapabilitySecurityV2",
        "CompetitiveRuntimeV7", "ContentAddressedArtifactStoreV2", "ContextCompilerV2",
        "ContextIRItem", "ContextPackV2", "FirewallReceiptV2", "IncrementalCodeIntelligenceGraph",
        "ReferenceCodingAgentV2", "SecretlessProviderGatewayV2", "SessionMemoryDAGV2",
        "UniversalAdapterRegistryV2", "UniversalOutputFirewallV2", "competitive_v7_manifest",
    ]

del _os
