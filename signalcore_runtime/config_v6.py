from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .util import atomic_write_json, canonical_json, sha256_bytes


_SECRET_REF = re.compile(r"^secret://[A-Za-z0-9._/-]+$")


@dataclass(frozen=True)
class ConfigValue:
    path: str
    value: Any
    source: str
    scope: str


@dataclass(frozen=True)
class ConfigSnapshot:
    schema_version: int
    values: Mapping[str, Any]
    provenance: tuple[ConfigValue, ...]
    config_hash: str
    loaded_at: float
    warnings: tuple[str, ...] = ()

    def explain(self, dotted_path: str) -> ConfigValue | None:
        return next((item for item in reversed(self.provenance) if item.path == dotted_path), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "values": copy.deepcopy(dict(self.values)),
            "provenance": [asdict(item) for item in self.provenance],
            "config_hash": self.config_hash,
            "loaded_at": self.loaded_at,
            "warnings": list(self.warnings),
        }


_DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "runtime": {"profile": "balanced", "fail_closed": True},
    "provider": {"cache_policy": "auto", "timeout_seconds": 180.0},
    "routing": {"budget_bytes": 8192, "table": {"max_rows": 8, "max_columns": 12}},
    "security": {
        "evidence_encryption": "required",
        "control_authentication": "required",
        "remote_tls": "required",
        "dlp": "required",
    },
    "retention": {"evidence_ttl_days": 30, "max_store_bytes": 10 * 1024 * 1024 * 1024},
    "sandbox": {"strict": True, "network": "none", "image": ""},
    "observability": {"structured_logs": True, "metrics": True, "sample_rate": 1.0},
}


class ConfigError(RuntimeError):
    pass


def _deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        name = str(key)
        if isinstance(value, Mapping) and isinstance(target.get(name), dict):
            _deep_merge(target[name], value)
        else:
            target[name] = copy.deepcopy(value)


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, Mapping):
            result.update(_flatten(child, path))
        else:
            result[path] = child
    return result


def _set_dotted(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [item for item in path.split(".") if item]
    if not parts:
        raise ConfigError("empty config path")
    cursor = target
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            raise ConfigError(f"config path collides with scalar: {path}")
        cursor = child
    cursor[parts[-1]] = value


def _parse_env_value(value: str) -> Any:
    lowered = value.casefold()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class ConfigManager:
    """Canonical scoped configuration with provenance and last-good rollback."""

    def __init__(
        self,
        *,
        project_root: Path,
        state_root: Path,
        user_config: Path | None = None,
        project_config: Path | None = None,
        env_prefix: str = "SIGNALCORE_CFG__",
    ):
        self.project_root = Path(project_root).resolve(strict=False)
        self.state_root = Path(state_root).resolve(strict=False)
        self.user_config = user_config or Path.home() / ".config" / "signalcore" / "config.toml"
        self.project_config = project_config or self.project_root / ".signalcore" / "config.toml"
        self.env_prefix = env_prefix
        self.last_good_path = self.state_root / "config-last-good.json"
        self._lock = threading.RLock()
        self._snapshot: ConfigSnapshot | None = None
        self._mtimes: dict[str, int] = {}

    def load(
        self,
        *,
        session: Mapping[str, Any] | None = None,
        task: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> ConfigSnapshot:
        with self._lock:
            current_mtimes = self._source_mtimes()
            if not force and self._snapshot is not None and current_mtimes == self._mtimes and not session and not task:
                return self._snapshot
            values = copy.deepcopy(_DEFAULTS)
            provenance = [ConfigValue(path, value, "builtin", "default") for path, value in _flatten(_DEFAULTS).items()]
            warnings: list[str] = []
            try:
                self._merge_file(values, provenance, self.user_config, scope="user")
                self._merge_file(values, provenance, self.project_config, scope="project")
                self._merge_env(values, provenance)
                if session:
                    self._merge_layer(values, provenance, session, source="session-override", scope="session")
                if task:
                    self._merge_layer(values, provenance, task, source="task-override", scope="task")
                self._validate(values)
            except Exception as exc:
                fallback = self._last_good()
                if fallback is None:
                    raise ConfigError(str(exc)) from exc
                warnings.append(f"invalid-current-config-fell-back:{type(exc).__name__}")
                snapshot = ConfigSnapshot(
                    schema_version=int(fallback["schema_version"]),
                    values=fallback["values"],
                    provenance=tuple(ConfigValue(**item) for item in fallback.get("provenance", [])),
                    config_hash=str(fallback["config_hash"]),
                    loaded_at=time.time(),
                    warnings=tuple(warnings),
                )
                self._snapshot = snapshot
                self._mtimes = current_mtimes
                return snapshot
            snapshot = ConfigSnapshot(
                schema_version=int(values["schema_version"]),
                values=values,
                provenance=tuple(provenance),
                config_hash=sha256_bytes(canonical_json(values)),
                loaded_at=time.time(),
                warnings=tuple(warnings),
            )
            self.state_root.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.last_good_path, snapshot.to_dict(), mode=0o600)
            if not session and not task:
                self._snapshot = snapshot
                self._mtimes = current_mtimes
            return snapshot

    def reload_if_changed(self) -> ConfigSnapshot:
        return self.load(force=self._source_mtimes() != self._mtimes)

    def diff(self, before: ConfigSnapshot, after: ConfigSnapshot) -> dict[str, Any]:
        left = _flatten(before.values)
        right = _flatten(after.values)
        added = {key: right[key] for key in right.keys() - left.keys()}
        removed = {key: left[key] for key in left.keys() - right.keys()}
        changed = {
            key: {"before": left[key], "after": right[key]}
            for key in left.keys() & right.keys() if left[key] != right[key]
        }
        return {"added": added, "removed": removed, "changed": changed}

    def _merge_file(self, values: dict[str, Any], provenance: list[ConfigValue], path: Path, *, scope: str) -> None:
        if not path.is_file():
            return
        try:
            layer = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"invalid {scope} config: {path}") from exc
        self._merge_layer(values, provenance, layer, source=str(path), scope=scope)

    def _merge_env(self, values: dict[str, Any], provenance: list[ConfigValue]) -> None:
        for key, raw in os.environ.items():
            if not key.startswith(self.env_prefix):
                continue
            path = key[len(self.env_prefix):].casefold().replace("__", ".")
            value = _parse_env_value(raw)
            _set_dotted(values, path, value)
            provenance.append(ConfigValue(path, "[secret-ref]" if path.endswith("credential_ref") else value, key, "environment"))

    @staticmethod
    def _merge_layer(
        values: dict[str, Any],
        provenance: list[ConfigValue],
        layer: Mapping[str, Any],
        *,
        source: str,
        scope: str,
    ) -> None:
        _deep_merge(values, layer)
        provenance.extend(ConfigValue(path, value, source, scope) for path, value in _flatten(layer).items())

    @staticmethod
    def _validate(values: Mapping[str, Any]) -> None:
        if values.get("schema_version") != 1:
            raise ConfigError("unsupported config schema_version")
        profile = ((values.get("runtime") or {}).get("profile"))
        if profile not in {"compact", "balanced", "detailed", "audit", "terse"}:
            raise ConfigError("runtime.profile is invalid")
        security = values.get("security") or {}
        for key in ("evidence_encryption", "control_authentication", "remote_tls", "dlp"):
            if security.get(key) not in {"required", "preferred", "off"}:
                raise ConfigError(f"security.{key} is invalid")
        routing = values.get("routing") or {}
        if int(routing.get("budget_bytes") or 0) < 512:
            raise ConfigError("routing.budget_bytes must be at least 512")
        retention = values.get("retention") or {}
        if float(retention.get("evidence_ttl_days") or 0) < 0 or int(retention.get("max_store_bytes") or 0) < 0:
            raise ConfigError("retention values must be non-negative")
        for path, value in _flatten(values).items():
            if path.endswith("credential_ref") and value and not _SECRET_REF.match(str(value)):
                raise ConfigError(f"{path} must use secret:// reference syntax")
            if any(token in path.casefold() for token in ("password", "api_key", "secret_value", "token_value")):
                raise ConfigError(f"raw secret material is forbidden in config: {path}")

    def _source_mtimes(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for path in (self.user_config, self.project_config):
            try:
                result[str(path)] = path.stat().st_mtime_ns
            except OSError:
                result[str(path)] = -1
        return result

    def _last_good(self) -> dict[str, Any] | None:
        if not self.last_good_path.is_file():
            return None
        try:
            value = json.loads(self.last_good_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
