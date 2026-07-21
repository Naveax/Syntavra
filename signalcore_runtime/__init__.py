"""SignalCore 0.6.0 Unified Production Core."""

__version__ = "0.6.0"

# Compatibility layers are installed first; V6 then hardens the canonical
# provider, sandbox, MCP, evidence and lifecycle surfaces idempotently.
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
from .job_scheduler import DurableJobScheduler, JobSpec
from .long_session_planner import ContextPlanPolicy, LongSessionPlanner
from .policy_rollout import PolicyRolloutManager, VerifiedPolicyObservation
from .policy_tuner import AdaptivePolicyTuner, PolicyObservation, PolicyRecommendation
from .runtime_pipeline import CanonicalRequestEnvelope, UnifiedRuntimePipeline
from .sdk import SDKInvocation, SignalCoreClient
from .service_manager import ProviderProxyServiceManager, ServicePlan, ServiceSpec

__all__ = [
    "__version__", "AdaptivePolicyTuner", "ArmExecutionPolicy", "ArmRunReceipt",
    "Authorizer", "BackupResult", "CanonicalRequestEnvelope", "CapabilityTokenIssuer",
    "ConfigManager", "ConfigSnapshot", "ContextPlanPolicy", "DataRoutePolicy",
    "DataRouteResult", "DataRouter", "DurableJobScheduler", "EvidenceStore", "JobSpec",
    "LongSessionPlanner", "PolicyObservation", "PolicyRecommendation", "PolicyRolloutManager",
    "Principal", "ProviderProxyServiceManager", "SDKInvocation", "SecureArmRunner",
    "ServicePlan", "ServiceSpec", "SignalCoreClient", "StateBackupManager",
    "UnifiedRuntimePipeline", "VerifiedPolicyObservation",
]
