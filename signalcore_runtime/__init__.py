"""SignalCore 0.3.0 unified local-first runtime control plane."""

__version__ = "0.3.0"

# Keep the core MCP and proxy implementations stable; optional surfaces are
# installed idempotently so existing behavior is preserved.
from .provider_mcp_extension import install as _install_provider_mcp_extension
from .provider_proxy_extension import install as _install_provider_proxy_extension

_install_provider_mcp_extension()
_install_provider_proxy_extension()
del _install_provider_mcp_extension
del _install_provider_proxy_extension
