from __future__ import annotations

import copy
import math
import re
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
WIRE_ENV_PREFIX = "SYNTAVRA_CFG__"
MAX_CONFIG_WIRE_BYTES = 256 * 1024
_HEX = re.compile(r"^[0-9A-Fa-f]*$")


def _environment_path(name: str) -> str:
    if name.startswith(WIRE_ENV_PREFIX):
        return name[len(WIRE_ENV_PREFIX) :].casefold().replace("__", ".")
    return name


def _normalize_layer(scope: str, layer: Mapping[str, Any]) -> tuple[dict[str, Any], list[ConfigValue]]:
    if scope == "environment":
        values: dict[str, Any] = {}
        provenance: list[ConfigValue] = []
        for raw_path, value in layer.items():
            path = _environment_path(str(raw_path))
            source = (
                str(raw_path)
                if str(raw_path).startswith(WIRE_ENV_PREFIX)
                else WIRE_ENV_PREFIX + path.upper().replace(".", "__")
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
    """Resolve deterministic fixture phases using ConfigManager's canonical semantics."""

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
        if not math.isfinite(value):
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
                        if str(raw_path).startswith(WIRE_ENV_PREFIX)
                        else WIRE_ENV_PREFIX + path.upper().replace(".", "__")
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


def _decode_wire_text(value: str) -> str:
    if len(value) % 2 or _HEX.fullmatch(value) is None:
        raise ConfigError("config wire hex field is invalid")
    try:
        return bytes.fromhex(value).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("config wire text field is not UTF-8") from exc


def _decode_scalar(type_code: str, encoded: str) -> Any:
    raw = _decode_wire_text(encoded)
    if type_code == "n" and raw == "":
        return None
    if type_code == "b" and raw in {"true", "false"}:
        return raw == "true"
    if type_code == "i":
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError("config wire integer is invalid") from exc
    if type_code == "f":
        try:
            value = float(raw)
        except ValueError as exc:
            raise ConfigError("config wire float is invalid") from exc
        if not math.isfinite(value):
            raise ConfigError("config wire float is non-finite")
        return value
    if type_code == "s":
        return raw
    raise ConfigError("config wire scalar type is invalid")


def decode_config_wire(
    input_bytes: bytes,
    *,
    maximum_bytes: int = MAX_CONFIG_WIRE_BYTES,
) -> list[dict[str, dict[str, Any]]]:
    raw = bytes(input_bytes)
    if len(raw) > maximum_bytes:
        raise ConfigError("config wire exceeds the input limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("config wire is not UTF-8") from exc
    if not text.endswith("\n"):
        raise ConfigError("config wire must be newline terminated")

    lines = text.splitlines()
    if not lines or lines[0] != WIRE_HEADER:
        raise ConfigError("config wire header is invalid")

    phases: list[dict[str, dict[str, Any]]] = []
    current: dict[str, dict[str, Any]] | None = None
    seen: set[tuple[str, str]] = set()
    expected_phase = 0
    last_rank = -1

    for line in lines[1:]:
        if not line:
            raise ConfigError("config wire contains an empty line")
        fields = line.split("\t")
        if len(fields) == 2 and fields[0] == "phase":
            try:
                phase_index = int(fields[1])
            except ValueError as exc:
                raise ConfigError("config wire phase index is invalid") from exc
            if phase_index != expected_phase:
                raise ConfigError("config wire phase order is invalid")
            if current is not None:
                phases.append(current)
            current = {}
            seen = set()
            expected_phase += 1
            last_rank = -1
            continue

        if len(fields) != 6 or fields[0] != "a":
            raise ConfigError("config wire line is invalid")
        if current is None:
            raise ConfigError("config wire assignment precedes its phase")

        _, scope, encoded_source, encoded_path, type_code, encoded_value = fields
        if scope not in LAYER_ORDER:
            raise ConfigError("config wire scope is invalid")
        rank = LAYER_ORDER.index(scope)
        if rank < last_rank:
            raise ConfigError("config wire scope order is invalid")
        last_rank = rank

        source = _decode_wire_text(encoded_source)
        path = _decode_wire_text(encoded_path)
        if not path or any(not part for part in path.split(".")):
            raise ConfigError("config wire path is invalid")
        key = (scope, path)
        if key in seen:
            raise ConfigError("config wire contains a duplicate assignment")
        seen.add(key)
        value = _decode_scalar(type_code, encoded_value)

        layer = current.setdefault(scope, {})
        if scope == "environment":
            if not source.startswith(WIRE_ENV_PREFIX) or _environment_path(source) != path:
                raise ConfigError("config wire environment source is invalid")
            layer[source] = value
        else:
            if source != SOURCE_NAMES[scope]:
                raise ConfigError("config wire source is invalid")
            _set_dotted(layer, path, value)

    if current is not None:
        phases.append(current)
    if not phases:
        raise ConfigError("config wire requires at least one phase")
    if encode_config_wire(phases) != raw:
        raise ConfigError("config wire is not canonical")
    return phases


def decode_config_wire_hex(
    value: str,
    *,
    maximum_bytes: int = MAX_CONFIG_WIRE_BYTES,
) -> bytes:
    text = str(value)
    if len(text) > maximum_bytes * 2:
        raise ConfigError("config wire exceeds the input limit")
    if len(text) % 2 or _HEX.fullmatch(text) is None:
        raise ConfigError("config wire hex is invalid")
    raw = bytes.fromhex(text)
    decode_config_wire(raw, maximum_bytes=maximum_bytes)
    return raw


def resolve_config_wire(input_bytes: bytes) -> dict[str, Any]:
    return resolve_config_phases(decode_config_wire(input_bytes))
