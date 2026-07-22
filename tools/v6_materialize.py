from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.0.1"
CHANNEL = "pre-release"


class VersionMaterializationError(RuntimeError):
    pass


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _json(path: str) -> dict:
    return json.loads(_read(path))


def _skill_version(path: str) -> str | None:
    match = re.search(r'^version:\s*["\']?([^"\'\s]+)', _read(path), flags=re.MULTILINE)
    return match.group(1) if match else None


def verify_locked_identity() -> dict:
    pyproject = _read("pyproject.toml")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.MULTILINE)
    versions = {
        "VERSION": _read("VERSION").strip(),
        "pyproject": match.group(1) if match else None,
        "installer": _json("package.json").get("version"),
        "installer_lock": _json("package-lock.json").get("version"),
        "typescript": _json("sdk/typescript/package.json").get("version"),
        "typescript_lock": _json("sdk/typescript/package-lock.json").get("version"),
        "marketplace": _json(".claude-plugin/marketplace.json").get("version"),
        "gemini": _json("gemini-extension.json").get("version"),
        "codemeta": _json("codemeta.json").get("version"),
        "skill": _skill_version("skills/signal-core/SKILL.md"),
        "bundled_skill": _skill_version("signalcore_runtime/bundled_skill/SKILL.md"),
        "pre_release": _json("release/pre-release.json").get("version"),
    }
    wrong = {name: value for name, value in versions.items() if value != VERSION}
    prerelease = _json("release/pre-release.json")
    if prerelease.get("channel") != CHANNEL or prerelease.get("version_locked") is not True:
        wrong["release_channel"] = prerelease.get("channel")
    return {"ok": not wrong, "version": VERSION, "channel": CHANNEL, "versions": versions, "wrong": wrong}


def main() -> int:
    result = verify_locked_identity()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["ok"]:
        raise VersionMaterializationError(
            "legacy materialization is disabled; SignalCore must remain at v0.0.1 pre-release"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
