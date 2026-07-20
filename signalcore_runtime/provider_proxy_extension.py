from __future__ import annotations

from typing import Any


def install() -> None:
    """Install lifecycle helpers without replacing proxy transport behavior."""

    from .provider_proxy import ProviderProxyRuntime

    if getattr(ProviderProxyRuntime, "_signalcore_lifecycle_extension_v4", False):
        return

    def wait(self: Any) -> None:
        thread = self._thread
        if thread is None:
            raise RuntimeError("proxy is not running in background mode")
        try:
            while thread.is_alive():
                thread.join(timeout=1.0)
        except KeyboardInterrupt:
            self.shutdown()

    ProviderProxyRuntime.wait = wait
    ProviderProxyRuntime._signalcore_lifecycle_extension_v4 = True
