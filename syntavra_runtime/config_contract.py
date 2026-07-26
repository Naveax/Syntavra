from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .release_identity import identity
from .unified_config import (
    ConfigError,
    ConfigSnapshot,
    ConfigValue,
    _DEFAULTS,
    _deep_merge,
    _flatten,
    _set_dotted,
)
from .util import canonical_json, sha256_bytes

LAYER_ORDER = ("user", "project", "environment", "session", "task")
SOURCE_NAMES = {
    "user": "user-config",
    "project": "project-config",
    "session": "session-override",
    "task": "task-override",
}
WIRE_HEADER = "R6CFG1"


def _environment_path(name: str) -> str:
    if name.startswith("SYNTAVRA_CFG__"):
        return name[len("SYNTAVRA_CFG__") :].casefold().replace("__", ".")
    return name


def _normalize_layer(scope: str, layer: Mapping[str, Any]) -> tuple[dict[str, Any], list[ConfigValue]]:
    if scope == "environment":
        values: dict[str, Any] = {}
        provenance: list[ConfigValue] = []
        for raw_path, value in layer.items():
            path = _environment_path(str(raw_path))
            source = (
                str(raw_path)
                if str(raw_path).startswith("SYNTAVRA_CFG__")
                else "SYNTAVRA_CFG__" + path.upper().replace(".", "__")
            )
            _set_dotted(values, path, value)
            provenance.append(
                ConfigValue(
                    path=path,
                    value="[secret-ref]" if path.endswith("credential_ref") else copy.deepcopy(value),
                    source=source,
                    scope="environment",
                )
            )
        return values, provenance

    values = copy.deepcopy(dict(layer))
    source = SOURCE_NAMES[scope]
    provenance = [
        ConfigValue(path=path, value=copy.deepcopy(value), source=source, scope=scope)
        for path, value in _flatten(values).items()
    ]
    return values, provenance


def _projection(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "values": copy.deepcopy(dict(snapshot.values)),
        "provenance": [asdict(item) for item in snapshot.provenance],
        "config_hash": snapshot.config_hash,
        "warnings": list(snapshot.warnings),
    }


def resolve_config_phases(phases: Sequence[Mapping[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """Resolve deterministic fixture phases using ConfigManager's canonical semantics.

    Every successful phase becomes the in-memory last-good snapshot. A later
    invalid phase falls back to that snapshot and records the same warning code
    used by ConfigManager.
    """

    last_good: ConfigSnapshot | None = None
    current: ConfigSnapshot | None = None

    for phase in phases:
        values = copy.deepcopy(_DEFAULTS)
        provenance = [
            ConfigValue(path=path, value=copy.deepcopy(value), source="builtin", scope="default")
            for path, value in _flatten(_DEFAULTS).items()
        ]
        try:
            for scope in LAYER_ORDER:
                raw_layer = phase.get(scope) or {}
                if not isinstance(raw_layer, Mapping):
                    raise ConfigError(f"{scope} layer must be a mapping")
                layer, layer_provenance = _normalize_layer(scope, raw_layer)
                _deep_merge(values, layer)
                provenance.extend(layer_provenance)
            from .unified_config import ConfigManager

            ConfigManager._validate(values)
            current = ConfigSnapshot(
                schema_version=int(values["schema_version"]),
                values=values,
                provenance=tuple(provenance),
                config_hash=sha256_bytes(canonical_json(values)),
                loaded_at=0.0,
                warnings=(),
            )
            last_good = current
        except Exception as exc:
            if last_good is None:
                raise ConfigError(str(exc)) from exc
            current = ConfigSnapshot(
                schema_version=last_good.schema_version,
                values=copy.deepcopy(dict(last_good.values)),
                provenance=last_good.provenance,
                config_hash=last_good.config_hash,
                loaded_at=0.0,
                warnings=(f"invalid-current-config-fell-back:{type(exc).__name__}",),
            )

    if current is None:
        raise ConfigError("at least one config phase is required")
    return _projection(current)


def status_projection(config: Mapping[str, Any]) -> dict[str, Any]:
    release = identity()
    return {
        "product": "Syntavra",
        "product_version": release.version,
        "release_channel": release.channel,
        "stability": release.stability,
        "version_locked": release.version_locked,
        "reference_engine": "python",
        "candidate_engine": "rust",
        "candidate_stability": "experimental",
        "config_schema_version": int(config["schema_version"]),
        "config_hash": str(config["config_hash"]),
        "warnings": list(config.get("warnings", [])),
        "general_command_routing": "blocked",
        "mutation": "read-only",
    }


def _encode_scalar(value: Any) -> tuple[str, str]:
    if value is None:
        return "n", ""
    if isinstance(value, bool):
        return "b", "true" if value else "false"
    if isinstance(value, int):
        return "i", str(value)
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ConfigError("non-finite config numbers are forbidden")
        return "f", repr(value)
    if isinstance(value, str):
        return "s", value
    raise ConfigError(f"R6 fixture values must be scalar, got {type(value).__name__}")


def _hex(value: str) -> str:
    return value.encode("utf-8").hex()


def encode_config_wire(phases: Sequence[Mapping[str, Mapping[str, Any]]]) -> bytes:
    lines = [WIRE_HEADER]
    for phase_index, phase in enumerate(phases):
        lines.append(f"phase\t{phase_index}")
        for scope in LAYER_ORDER:
            raw_layer = phase.get(scope) or {}
            if scope == "environment":
                rows = []
                for raw_path, value in raw_layer.items():
                    path = _environment_path(str(raw_path))
                    source = (
                        str(raw_path)
                        if str(raw_path).startswith("SYNTAVRA_CFG__")
                        else "SYNTAVRA_CFG__" + path.upper().replace(".", "__")
                    )
                    rows.append((path, value, source))
            else:
                source = SOURCE_NAMES[scope]
                rows = [(path, value, source) for path, value in _flatten(raw_layer).items()]
            for path, value, source in rows:
                type_code, raw_value = _encode_scalar(value)
                lines.append(
                    "\t".join(
                        (
                            "a",
                            scope,
                            _hex(source),
                            _hex(path),
                            type_code,
                            _hex(raw_value),
                        )
                    )
                )
    return ("\n".join(lines) + "\n").encode("utf-8")
