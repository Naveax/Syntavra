"""SignalCore v0.0.1 pre-release unified dominance runtime."""

__version__ = "0.0.1"
__release_channel__ = "pre-release"

# Compatibility layers are installed first; the production core then hardens
# the canonical provider, sandbox, MCP, evidence and lifecycle surfaces.
from .provider_mcp_extension import install as _install_provider_mcp_extension
from .provider_proxy_extension import install as _install_provider_proxy_extension
from .ecosystem_mcp_extension import install as _install_ecosystem_mcp_extension
from .product_v5_extension import install as _install_product_v5_extension
from .v6_extension import install as _install_v6_extension

_install_provider_mcp_extension()
_install_provider_proxy_extension()
_install_ecosystem_mcp_extension()
_install_product_v5_extension()
_install_v6_extension()

# Old compatibility modules must report the locked public identity even though
# their file names preserve historical implementation-layer labels.
from .mcp_server import MCPServer as _MCPServer
from . import cli as _legacy_cli
_MCPServer.VERSION = __version__
_legacy_cli.VERSION = __version__

del _MCPServer
del _legacy_cli
del _install_provider_mcp_extension
del _install_provider_proxy_extension
del _install_ecosystem_mcp_extension
del _install_product_v5_extension
del _install_v6_extension

from .arm_runner import ArmExecutionPolicy, ArmRunReceipt, SecureArmRunner
from .backup import BackupResult, StateBackupManager
from .config_v6 import ConfigManager, ConfigSnapshot
from .data_router import DataRoutePolicy, DataRouteResult, DataRouter
from .evidence import EvidenceStore
from .identity import Authorizer, CapabilityTokenIssuer, Principal
from .infinite_context import ActiveContextPlan, RecursiveExecutionEngine, RecursiveTask, UnboundedContextCoordinator
from .integration_matrix import IntegrationMatrix, IntegrationSpec
from .job_scheduler import DurableJobScheduler, JobSpec
from .long_context_quality import LongContextQualityGate, LongContextReceipt
from .long_session_planner import ContextPlanPolicy, LongSessionPlanner
from .policy_rollout import PolicyRolloutManager, VerifiedPolicyObservation
from .policy_tuner import AdaptivePolicyTuner, PolicyObservation, PolicyRecommendation
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
    "DurableJobScheduler", "EvidenceStore", "GraphEdge", "GraphNode", "IntegrationMatrix",
    "IntegrationSpec", "JobSpec", "LongContextQualityGate", "LongContextReceipt", "LongSessionPlanner",
    "MCPProfile", "MeasuredBenchmarkGate", "PairedSchedule", "PlatformAdapter",
    "PlatformAdapterRegistry", "PolicyObservation", "PolicyRecommendation", "PolicyRolloutManager",
    "Principal", "ProductSurface", "ProviderProxyServiceManager", "ProviderUsageReceipt", "ProxyPreset",
    "ProxyProductRegistry", "PublicProofGate", "ReceiptValidator", "RecursiveExecutionEngine",
    "RecursiveTask", "ReleaseIdentity", "SDKInvocation", "SecureArmRunner", "ServicePlan", "ServiceSpec",
    "SessionAnalyticsStore", "SessionContinuityController", "SignalCoreClient", "StateBackupManager",
    "StructuralGraphV2", "SuperiorityGate", "ToolRouteDecision", "ToolRoutingEnforcer",
    "UnifiedRuntimePipeline", "UnboundedContextCoordinator", "VerifiedPolicyObservation",
    "VersionLockError", "WorkloadSpec", "ZeroFrictionManager",
]
