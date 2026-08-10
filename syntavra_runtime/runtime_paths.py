from __future__ import annotations

import os
from pathlib import Path

from .util import stable_project_id


_AUTO_PROJECT_VALUES = {"", ".", "auto", "cwd"}


def discover_project_root(
    value: str | Path | None = None,
    *,
    cwd: Path | None = None,
    strict: bool = False,
) -> Path:
    """Resolve the active project without binding global integrations to Syntavra itself.

    Explicit non-auto paths remain authoritative. The default/auto form starts at
    the process working directory and walks upward to the nearest Git worktree.
    This is intentionally independent from the installed Syntavra package path.
    """

    raw = str(value if value is not None else os.environ.get("SYNTAVRA_PROJECT", "auto")).strip()
    if raw.casefold() not in _AUTO_PROJECT_VALUES:
        return Path(raw).expanduser().resolve(strict=strict)

    start = (cwd or Path.cwd()).expanduser().resolve(strict=False)
    for candidate in (start, *start.parents):
        marker = candidate / ".git"
        if marker.exists() or marker.is_file():
            return candidate.resolve(strict=strict)
    return start.resolve(strict=strict)


def state_home(*, home: Path | None = None) -> Path:
    """Return Syntavra's out-of-tree per-user state directory."""

    override = os.environ.get("SYNTAVRA_STATE_HOME") or os.environ.get("SYNTAVRA_HOME")
    if override:
        return Path(override).expanduser().resolve(strict=False)

    # An explicit home is an isolation/testing boundary and must outrank ambient
    # LOCALAPPDATA/XDG variables from the machine running the process.
    if home is not None:
        base = home.expanduser().resolve(strict=False)
        return (base / ".local" / "state" / "syntavra").resolve(strict=False)

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return (Path(local) / "Syntavra").resolve(strict=False)

    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return (Path(xdg) / "syntavra").expanduser().resolve(strict=False)

    base = Path.home().expanduser().resolve(strict=False)
    return (base / ".local" / "state" / "syntavra").resolve(strict=False)


def default_state_root(
    project: Path,
    *,
    namespace: str = "pre-release",
    home: Path | None = None,
) -> Path:
    """Return an isolated state root keyed by the exact active project identity."""

    normalized_namespace = namespace.strip().replace("\\", "-").replace("/", "-") or "runtime"
    return state_home(home=home) / "projects" / stable_project_id(project) / normalized_namespace


def resolve_state_root(
    project: Path,
    value: str | Path | None = None,
    *,
    namespace: str = "pre-release",
    home: Path | None = None,
) -> Path:
    """Resolve an explicit state root or the safe out-of-tree default."""

    raw = str(value or os.environ.get("SYNTAVRA_STATE_ROOT", "")).strip()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    return default_state_root(project, namespace=namespace, home=home).resolve(strict=False)


def project_identity(project: Path) -> dict[str, str]:
    """Small, model-safe identity used to detect accidental cross-project routing."""

    root = project.resolve(strict=False)
    return {
        "project_id": stable_project_id(root),
        "project_name": root.name,
    }
