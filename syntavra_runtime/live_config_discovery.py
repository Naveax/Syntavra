from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from pathlib import Path
from typing import Any, Mapping

from .config_contract import MAX_CONFIG_WIRE_BYTES, WIRE_ENV_PREFIX, encode_config_wire
from .unified_config import ConfigError, _parse_env_value
from .util import canonical_json

MAX_CONFIG_FILE_BYTES = 128 * 1024
MAX_OVERRIDE_JSON_BYTES = 64 * 1024
_CANONICAL_HEX = re.compile(r"^[0-9a-f]+$")


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def _default_user_config(env: Mapping[str, str]) -> Path:
    home = str(env.get("USERPROFILE") or env.get("HOME") or "").strip()
    root = Path(home).expanduser() if home else Path.home()
    return _lexical_absolute(root) / ".config" / "syntavra" / "config.toml"


def _sorted_mapping(value: Mapping[str, Any], *, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = str(raw_key)
        if not key:
            raise ConfigError("live config keys must not be empty")
        path = f"{prefix}.{key}" if prefix else key
        child = value[raw_key]
        if isinstance(child, Mapping):
            result[key] = _sorted_mapping(child, prefix=path)
        elif child is None or isinstance(child, (bool, int, float, str)):
            result[key] = child
        else:
            raise ConfigError(
                f"live config values must be scalar leaves, got {type(child).__name__} at {path}"
            )
    return result


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("override JSON contains a duplicate key")
        result[key] = value
    return result


def decode_override_json_hex(value: str, *, scope: str) -> dict[str, Any]:
    """Decode one bounded canonical JSON object used as a transient override."""

    text = str(value)
    if len(text) > MAX_OVERRIDE_JSON_BYTES * 2:
        raise ConfigError(f"{scope} override exceeds the input limit")
    if len(text) % 2 or _CANONICAL_HEX.fullmatch(text) is None:
        raise ConfigError(f"{scope} override hex must be non-empty lowercase hexadecimal")
    raw = bytes.fromhex(text)
    if len(raw) > MAX_OVERRIDE_JSON_BYTES:
        raise ConfigError(f"{scope} override exceeds the input limit")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{scope} override is not UTF-8") from exc
    try:
        parsed = json.loads(
            decoded,
            object_pairs_hook=_strict_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ConfigError(f"{scope} override contains a non-finite number")
            ),
        )
    except ConfigError:
        raise
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{scope} override is invalid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise ConfigError(f"{scope} override must be a JSON object")
    normalized = _sorted_mapping(parsed)
    if canonical_json(normalized) != raw:
        raise ConfigError(f"{scope} override JSON is not canonical")
    return normalized


def _read_toml_layer(path: Path, *, scope: str) -> dict[str, Any]:
    candidate = _lexical_absolute(path)
    try:
        before = candidate.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"cannot inspect live {scope} config") from exc
    if stat.S_ISLNK(before.st_mode):
        raise ConfigError(f"live {scope} config must not be a symlink")
    if not stat.S_ISREG(before.st_mode):
        raise ConfigError(f"live {scope} config must be a regular file")
    if before.st_size > MAX_CONFIG_FILE_BYTES:
        raise ConfigError(f"live {scope} config exceeds the file limit")
    try:
        payload = candidate.read_bytes()
        after = candidate.lstat()
    except OSError as exc:
        raise ConfigError(f"cannot read live {scope} config") from exc
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(payload) != before.st_size:
        raise ConfigError(f"live {scope} config changed during discovery")
    try:
        parsed = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"live {scope} config is invalid TOML") from exc
    if not isinstance(parsed, Mapping):
        raise ConfigError(f"live {scope} config must be a mapping")
    return _sorted_mapping(parsed)


def _environment_layer(env: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in sorted(str(key) for key in env):
        if not name.startswith(WIRE_ENV_PREFIX):
            continue
        result[name] = _parse_env_value(str(env[name]))
    return result


def discover_live_config_wire(
    *,
    project_root: Path,
    env: Mapping[str, str] | None = None,
    user_config: Path | None = None,
    project_config: Path | None = None,
    session: Mapping[str, Any] | None = None,
    task: Mapping[str, Any] | None = None,
) -> bytes:
    """Build one immutable R6CFG1 phase without writing product state."""

    active_env = dict(os.environ if env is None else env)
    lexical_root = _lexical_absolute(project_root)
    try:
        root_metadata = lexical_root.lstat()
    except OSError as exc:
        raise ConfigError("cannot inspect live config project root") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise ConfigError("live config project root must be a non-symlink directory")
    user_path = user_config or _default_user_config(active_env)
    project_path = project_config or lexical_root / ".syntavra" / "config.toml"
    phase = {
        "user": _read_toml_layer(user_path, scope="user"),
        "project": _read_toml_layer(project_path, scope="project"),
        "environment": _environment_layer(active_env),
        "session": _sorted_mapping(session or {}),
        "task": _sorted_mapping(task or {}),
    }
    wire = encode_config_wire([phase])
    if len(wire) > MAX_CONFIG_WIRE_BYTES:
        raise ConfigError("discovered live config exceeds the canonical wire limit")
    return wire
