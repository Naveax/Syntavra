from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

from .util import canonical_json


class PluginError(RuntimeError):
    pass


_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_ALLOWED_KINDS = {"provider", "router", "parser", "host", "security", "verifier", "policy", "storage", "sandbox", "mcp"}
_ALLOWED_PERMISSIONS = {
    "network", "filesystem-read", "filesystem-write", "evidence-read", "evidence-write",
    "session-read", "session-write", "provider-call", "process-execute", "admin",
}


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    version: str
    api_version: str = "1"
    kind: str = "router"
    permissions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    entrypoint: str = ""
    signature: str = ""

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.plugin_id):
            raise PluginError("invalid plugin id")
        if self.api_version != "1":
            raise PluginError("unsupported plugin API version")
        if self.kind not in _ALLOWED_KINDS:
            raise PluginError("unsupported plugin kind")
        unknown = set(self.permissions) - _ALLOWED_PERMISSIONS
        if unknown:
            raise PluginError("unknown plugin permissions: " + ",".join(sorted(unknown)))
        if not self.version or len(self.version) > 64:
            raise PluginError("invalid plugin version")

    def unsigned_payload(self) -> bytes:
        value = asdict(self)
        value["signature"] = ""
        return canonical_json(value)


class SyntavraPlugin(Protocol):
    manifest: PluginManifest

    def health(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class PluginRecord:
    manifest: PluginManifest
    enabled: bool
    failures: int
    quarantined: bool
    registered_at: float


class PluginRegistry:
    """Explicit, permissioned plugin registry with optional HMAC signatures.

    Runtime import discovery is intentionally absent. Callers register concrete plugin
    objects after their own package/import policy has approved them.
    """

    def __init__(
        self,
        *,
        allowed_permissions: Iterable[str] = (),
        signing_key: bytes | None = None,
        require_signatures: bool = False,
        failure_limit: int = 3,
    ):
        self.allowed_permissions = frozenset(str(item) for item in allowed_permissions)
        self.signing_key = bytes(signing_key) if signing_key else None
        self.require_signatures = bool(require_signatures)
        self.failure_limit = max(1, int(failure_limit))
        self._plugins: dict[str, SyntavraPlugin] = {}
        self._records: dict[str, PluginRecord] = {}
        self._lock = threading.RLock()

    def sign(self, manifest: PluginManifest) -> str:
        if not self.signing_key:
            raise PluginError("plugin signing key is unavailable")
        return hmac.new(self.signing_key, manifest.unsigned_payload(), hashlib.sha256).hexdigest()

    def register(self, plugin: SyntavraPlugin) -> PluginRecord:
        manifest = plugin.manifest
        manifest.validate()
        denied = set(manifest.permissions) - self.allowed_permissions
        if denied:
            raise PluginError("plugin permissions denied: " + ",".join(sorted(denied)))
        if self.require_signatures or manifest.signature:
            if not self.signing_key or not manifest.signature:
                raise PluginError("signed plugin manifest required")
            expected = self.sign(manifest)
            if not hmac.compare_digest(manifest.signature, expected):
                raise PluginError("plugin manifest signature is invalid")
        with self._lock:
            if manifest.plugin_id in self._plugins:
                raise PluginError("plugin id already registered")
            record = PluginRecord(manifest, True, 0, False, time.time())
            self._plugins[manifest.plugin_id] = plugin
            self._records[manifest.plugin_id] = record
            return record

    def invoke(self, plugin_id: str, method: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            record = self._records.get(plugin_id)
            if plugin is None or record is None or not record.enabled or record.quarantined:
                raise PluginError("plugin unavailable")
        if method.startswith("_"):
            raise PluginError("private plugin methods are forbidden")
        target = getattr(plugin, method, None)
        if not callable(target):
            raise PluginError("plugin method not found")
        try:
            return target(*args, **kwargs)
        except Exception as exc:
            with self._lock:
                current = self._records[plugin_id]
                failures = current.failures + 1
                self._records[plugin_id] = PluginRecord(
                    current.manifest,
                    failures < self.failure_limit,
                    failures,
                    failures >= self.failure_limit,
                    current.registered_at,
                )
            raise PluginError(f"plugin call failed: {type(exc).__name__}") from exc

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(self._records[key]) for key in sorted(self._records)]

    def enable(self, plugin_id: str) -> None:
        with self._lock:
            current = self._records[plugin_id]
            if current.quarantined:
                raise PluginError("quarantined plugin requires administrative reset")
            self._records[plugin_id] = PluginRecord(current.manifest, True, current.failures, False, current.registered_at)

    def disable(self, plugin_id: str) -> None:
        with self._lock:
            current = self._records[plugin_id]
            self._records[plugin_id] = PluginRecord(current.manifest, False, current.failures, current.quarantined, current.registered_at)
