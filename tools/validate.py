#!/usr/bin/env python3
from __future__ import annotations

import json
import py_compile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "signal-core"
REQUIRED = [
    ROOT / "README.md", ROOT / "COMPATIBILITY.md", ROOT / "AGENTS.md", ROOT / "llms.txt",
    ROOT / "gemini-extension.json", ROOT / ".claude-plugin" / "marketplace.json",
    SKILL / "SKILL.md", SKILL / "data" / "platforms.json", SKILL / "scripts" / "platforms.py",
    SKILL / "scripts" / "profile_loader.py",
    SKILL / "profiles" / "roblox_studio" / "profile.json",
    SKILL / "profiles" / "roblox_studio" / "activation.py",
    ROOT / "ROBLOX_STUDIO_MODE.md",
]


def main() -> int:
    checks = []
    checks.append(("required_files", all(path.is_file() for path in REQUIRED)))
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    checks.append(("version", version == "0.0.1"))
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    checks.append(("skill_identity", "name: signal-core" in skill_text and 'version: "0.0.1"' in skill_text))
    platforms = json.loads((SKILL / "data" / "platforms.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in platforms["platforms"]]
    checks.append(("platform_registry", len(ids) >= 20 and len(ids) == len(set(ids))))
    checks.append(("native_core", {"codex", "claude-code", "gemini-cli", "antigravity", "antigravity-cli", "windsurf", "opencode", "vscode-copilot"}.issubset(ids)))
    roblox = json.loads((SKILL / "profiles" / "roblox_studio" / "profile.json").read_text(encoding="utf-8"))
    activation = roblox.get("activation", {})
    checks.append(("roblox_profile_hidden", roblox.get("discoverable") is False and roblox.get("direct_invocation") is False))
    checks.append(("roblox_profile_studio_only", activation.get("mode") == "signed_studio_session" and activation.get("allow_cli") is False and activation.get("allow_ide") is False))
    checks.append(("roblox_profile_fail_closed", activation.get("require_process_attestation") is True and activation.get("single_use_nonce") is True))
    checks.append(("pairing_key_not_vendored", not any(path.name == "pairing.key" for path in ROOT.rglob("*"))))
    try:
        for path in sorted((SKILL / "scripts").glob("*.py")):
            py_compile.compile(str(path), doraise=True)
        for path in sorted((SKILL / "profiles").rglob("*.py")):
            py_compile.compile(str(path), doraise=True)
        py_compile.compile(str(ROOT / "tools" / "install.py"), doraise=True)
        checks.append(("python_compile", True))
    except Exception:
        checks.append(("python_compile", False))
    forbidden = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s]+")
    scans = [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts and path.suffix not in {".pyc"}]
    checks.append(("secret_scan", not any(forbidden.search(path.read_text(encoding="utf-8", errors="ignore")) for path in scans)))
    result = {"ok": all(passed for _, passed in checks), "version": version, "checks": [{"name": name, "passed": passed} for name, passed in checks]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
