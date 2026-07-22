#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
PROFILES_ROOT = SKILL_ROOT / "profiles"


class ProfileLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedProfile:
    profile_id: str
    profile_version: str
    root: Path
    config: dict[str, Any]
    session: Any


def _profile_root(profile_id: str) -> Path:
    if not profile_id or any(value in profile_id for value in ("/", "\\", "..", "\x00")):
        raise ProfileLoadError("invalid profile id")
    root = (PROFILES_ROOT / profile_id).resolve(strict=False)
    root.relative_to(PROFILES_ROOT.resolve(strict=False))
    return root


def profile_metadata(profile_id: str) -> dict[str, Any]:
    root = _profile_root(profile_id)
    path = root / "profile.json"
    if not path.is_file():
        raise ProfileLoadError(f"unknown profile: {profile_id}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("profile_id") != profile_id or value.get("schema_version") != 1:
        raise ProfileLoadError("invalid profile metadata")
    return value


def discoverable_profiles() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(PROFILES_ROOT.glob("*/profile.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if value.get("discoverable") is True:
            rows.append(value)
    return rows


def load_profile(
    profile_id: str,
    *,
    activation_envelope: dict[str, Any] | None,
    state_root: Path,
) -> LoadedProfile:
    metadata = profile_metadata(profile_id)
    if not metadata.get("enabled", False):
        raise ProfileLoadError(f"profile is disabled: {profile_id}")
    if profile_id != "roblox_studio":
        raise ProfileLoadError(f"profile loader has no activation adapter for: {profile_id}")
    root = _profile_root(profile_id)
    module_path = root / "activation.py"
    spec = importlib.util.spec_from_file_location("syntavra_roblox_activation", module_path)
    if spec is None or spec.loader is None:
        raise ProfileLoadError("Roblox Studio activation module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    activation = metadata["activation"]
    session = module.verify_studio_envelope(
        activation_envelope,
        state_root=state_root,
        allowed_capabilities=metadata["allowed_capabilities"],
        accepted_process_names=activation["accepted_process_names"],
        maximum_ttl_seconds=int(activation["maximum_session_ttl_seconds"]),
        clock_skew_seconds=int(activation["clock_skew_seconds"]),
        require_process_attestation=bool(activation["require_process_attestation"]),
    )
    return LoadedProfile(
        profile_id=metadata["profile_id"],
        profile_version=metadata["profile_version"],
        root=root,
        config=metadata,
        session=session,
    )
