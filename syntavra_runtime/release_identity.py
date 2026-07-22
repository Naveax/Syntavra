from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

VERSION = "0.0.1"
CHANNEL = "pre-release"
STABILITY = "pre-alpha"
VERSION_LOCKED = True


class VersionLockError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseIdentity:
    version: str = VERSION
    channel: str = CHANNEL
    stability: str = STABILITY
    version_locked: bool = VERSION_LOCKED
    public_superiority_claim: str = "EXTERNAL_SUPERIORITY_NOT_PROVEN"
    infinite_context_claim: str = "UNBOUNDED_EXTERNAL_HISTORY_WITH_BOUNDED_ACTIVE_WINDOW"

    @property
    def display_version(self) -> str:
        return f"v{self.version} ({self.channel})"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def require_version(self, requested: str) -> None:
        normalized = requested.strip().lstrip("v")
        if normalized != self.version:
            raise VersionLockError(
                f"Syntavra version is locked to {self.version}; explicit owner approval is required to change it"
            )


def identity() -> ReleaseIdentity:
    return ReleaseIdentity()


def prerelease_metadata() -> dict[str, Any]:
    value = identity().to_dict()
    value.update({
        "development_status": "Development Status :: 2 - Pre-Alpha",
        "publish_as_prerelease": True,
        "stable_api": False,
        "version_change_requires_owner_instruction": True,
    })
    return value


def validate_repository_identity(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=False)
    checks: list[dict[str, Any]] = []

    def add(name: str, actual: str | None, expected: str = VERSION) -> None:
        checks.append({"name": name, "passed": actual == expected, "actual": actual, "expected": expected})

    version_file = root / "VERSION"
    add("VERSION", version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else None)

    pyproject = root / "pyproject.toml"
    pyproject_text = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else ""
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, flags=re.MULTILINE)
    add("pyproject", match.group(1) if match else None)

    package_json = root / "sdk" / "typescript" / "package.json"
    if package_json.is_file():
        add("typescript", str(json.loads(package_json.read_text(encoding="utf-8")).get("version")))

    codemeta = root / "codemeta.json"
    if codemeta.is_file():
        add("codemeta", str(json.loads(codemeta.read_text(encoding="utf-8")).get("version")))

    return {
        "ok": all(row["passed"] for row in checks),
        "identity": identity().to_dict(),
        "checks": checks,
    }
