"""SignalCore 0.3.0 unified local-first runtime control plane."""

__version__ = "0.3.0"

# Keep the core MCP implementation stable; provider support is installed as an
# idempotent extension so existing tools and profiles retain their behavior.
from .provider_mcp_extension import install as _install_provider_mcp_extension

_install_provider_mcp_extension()
del _install_provider_mcp_extension
