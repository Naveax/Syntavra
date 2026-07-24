#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from common import DATA, PUBLIC_VERSION, atomic_write_text, dump_json, load_json

SKILL_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = DATA / "platforms.json"
BEGIN = "<!-- SYNTAVRA:BEGIN -->"
END = "<!-- SYNTAVRA:END -->"


def registry() -> dict[str, Any]:
    value = load_json(REGISTRY_PATH)
    if value.get("schema_version") != 1 or not isinstance(value.get("platforms"), list):
        raise RuntimeError("invalid platform registry")
    return value


def platform_map() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in registry()["platforms"]}


def compact_adapter(platform_id: str = "generic") -> str:
    return f"""{BEGIN}
## Syntavra {PUBLIC_VERSION}

For non-trivial coding-agent tasks, use Syntavra as a success-first context and evidence controller.

1. Read the canonical skill at `skills/syntavra/SKILL.md` or the installed platform-native copy.
2. Activate it for repository exploration, debugging, impact analysis, large outputs, long sessions, tool overload, or token/cost analysis.
3. Skip it for trivial edits and ordinary non-coding requests.
4. Prefer exact retrieval, deduplicate evidence, preserve security/exact outputs, and define a narrow verifier before broad exploration.
5. Run the standard-library scripts under `skills/syntavra/scripts/` when the host cannot invoke Agent Skills natively.
6. Never claim market superiority from forecasts or local tests; require paired provider benchmarks.

Adapter target: `{platform_id}`.
{END}"""


def cursor_adapter() -> str:
    body = compact_adapter("cursor")
    return "---\ndescription: Syntavra success-first context, evidence, and cost routing\nglobs:\nalwaysApply: false\n---\n\n" + body + "\n"


def continue_adapter() -> str:
    return "---\nname: Syntavra\ndescription: Success-first coding-agent context and evidence routing\nalwaysApply: false\n---\n\n" + compact_adapter("continue") + "\n"


def dedicated_rule(platform_id: str) -> str:
    if platform_id == "cursor":
        return cursor_adapter()
    if platform_id == "continue":
        return continue_adapter()
    return compact_adapter(platform_id) + "\n"


def _resolve_target(item: dict[str, Any], scope: str, project: Path, home: Path) -> Path:
    raw = item.get("project_target" if scope == "project" else "user_target")
    if not raw:
        raise ValueError(f"{item['id']} has no {scope} target")
    return (project if scope == "project" else home).joinpath(*Path(raw).parts).resolve(strict=False)


def _copy_skill_atomic(destination: Path, *, force: bool = False) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".syntavra-stage-", dir=destination.parent))
    backup: Path | None = None
    try:
        shutil.copytree(SKILL_ROOT, stage / "syntavra", symlinks=False, dirs_exist_ok=False)
        staged = stage / "syntavra"
        if not (staged / "SKILL.md").is_file():
            raise RuntimeError("staged skill is invalid")
        if destination.exists():
            if not force:
                existing = destination / "SKILL.md"
                if existing.is_file() and existing.read_bytes() == (staged / "SKILL.md").read_bytes():
                    shutil.rmtree(stage, ignore_errors=True)
                    return {"changed": False, "target": str(destination), "reason": "already installed"}
                raise FileExistsError(f"target already exists: {destination}; pass --force to replace")
            backup = destination.with_name(destination.name + ".syntavra-backup")
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(destination, backup)
        os.replace(staged, destination)
        shutil.rmtree(stage, ignore_errors=True)
        if backup and backup.exists():
            shutil.rmtree(backup)
        return {"changed": True, "target": str(destination), "mode": "native"}
    except Exception:
        if backup and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _merge_managed_block(path: Path, block: str, *, force: bool = False) -> dict[str, Any]:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if BEGIN in existing and END in existing:
        before, remainder = existing.split(BEGIN, 1)
        _, after = remainder.split(END, 1)
        updated = before.rstrip() + "\n\n" + block + after
    else:
        updated = existing.rstrip() + ("\n\n" if existing.strip() else "") + block + "\n"
    if updated == existing:
        return {"changed": False, "target": str(path), "reason": "already installed"}
    if path.exists() and not force and BEGIN not in existing and path.name not in {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}:
        raise FileExistsError(f"rule target already exists: {path}; pass --force to replace")
    atomic_write_text(path, updated, mode=0o644)
    return {"changed": True, "target": str(path), "mode": "managed-rule"}


def install(platform_id: str, *, scope: str, project: Path, home: Path, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    item = platform_map().get(platform_id)
    if not item:
        raise KeyError(f"unknown platform: {platform_id}")
    destination = _resolve_target(item, scope, project, home)
    if dry_run:
        return {"changed": False, "dry_run": True, "platform": platform_id, "support": item["support"], "evidence_level": item.get("evidence_level", "DECLARED"), "target": str(destination)}
    if item["support"] == "native":
        result = _copy_skill_atomic(destination, force=force)
    else:
        result = _merge_managed_block(destination, dedicated_rule(platform_id), force=force)
    return {"platform": platform_id, "support": item["support"], "verified": item["verified"], "evidence_level": item.get("evidence_level", "DECLARED"), "verified_scope": item.get("verified_scope", "none"), **result}


def uninstall(platform_id: str, *, scope: str, project: Path, home: Path) -> dict[str, Any]:
    item = platform_map().get(platform_id)
    if not item:
        raise KeyError(f"unknown platform: {platform_id}")
    destination = _resolve_target(item, scope, project, home)
    if item["support"] == "native":
        if destination.exists():
            shutil.rmtree(destination)
            return {"platform": platform_id, "changed": True, "target": str(destination)}
        return {"platform": platform_id, "changed": False, "target": str(destination)}
    if not destination.exists():
        return {"platform": platform_id, "changed": False, "target": str(destination)}
    existing = destination.read_text(encoding="utf-8")
    if BEGIN not in existing or END not in existing:
        return {"platform": platform_id, "changed": False, "target": str(destination), "reason": "managed block not found"}
    before, remainder = existing.split(BEGIN, 1)
    _, after = remainder.split(END, 1)
    updated = (before.rstrip() + "\n\n" + after.lstrip()).strip()
    if updated:
        atomic_write_text(destination, updated + "\n", mode=0o644)
    else:
        destination.unlink()
    return {"platform": platform_id, "changed": True, "target": str(destination)}


def status(platform_id: str, *, scope: str, project: Path, home: Path) -> dict[str, Any]:
    item = platform_map().get(platform_id)
    if not item:
        raise KeyError(f"unknown platform: {platform_id}")
    try:
        destination = _resolve_target(item, scope, project, home)
    except ValueError as exc:
        return {"platform": platform_id, "scope": scope, "supported_scope": False, "installed": False, "reason": str(exc)}
    installed = False
    if item["support"] == "native":
        installed = (destination / "SKILL.md").is_file()
    elif destination.is_file():
        text = destination.read_text(encoding="utf-8", errors="replace")
        installed = BEGIN in text and END in text
    return {"platform": platform_id, "scope": scope, "support": item["support"], "verified": item["verified"], "evidence_level": item.get("evidence_level", "DECLARED"), "verified_scope": item.get("verified_scope", "none"), "target": str(destination), "installed": installed}


def detect(project: Path, home: Path) -> list[dict[str, Any]]:
    markers = {
        "codex": [home / ".codex", project / ".codex"],
        "claude-code": [home / ".claude", project / ".claude"],
        "gemini-cli": [home / ".gemini", project / ".gemini"],
        "antigravity": [project / ".agent"],
        "windsurf": [home / ".codeium/windsurf", project / ".windsurf"],
        "opencode": [home / ".config/opencode", project / ".opencode"],
        "vscode-copilot": [project / ".vscode", project / ".github"],
        "cursor": [home / ".cursor", project / ".cursor"],
        "cline": [project / ".clinerules"],
        "continue": [home / ".continue", project / ".continue"],
        "junie": [project / ".junie"],
    }
    results = []
    for platform_id, paths in markers.items():
        present = [str(path) for path in paths if path.exists()]
        if present:
            results.append({"platform": platform_id, "markers": present})
    return results


def _expand_selection(selection: str) -> list[str]:
    known = platform_map()
    if selection == "all":
        return list(known)
    if selection == "all-native":
        return [key for key, item in known.items() if item["support"] == "native"]
    if selection == "all-verified":
        return [key for key, item in known.items() if item["verified"]]
    values = [value.strip() for value in selection.split(",") if value.strip()]
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise KeyError("unknown platforms: " + ", ".join(unknown))
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Syntavra cross-platform adapter and installer")
    parser.add_argument("command", choices=["list", "detect", "install", "uninstall", "status", "render"])
    parser.add_argument("--platforms", default="all-native")
    parser.add_argument("--scope", choices=["project", "user"], default="project")
    parser.add_argument("--project", default=".")
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    project = Path(args.project).expanduser().resolve()
    home = Path(args.home).expanduser().resolve()
    if args.command == "list":
        result: Any = registry()
    elif args.command == "detect":
        result = {"detected": detect(project, home)}
    elif args.command == "render":
        selected = _expand_selection(args.platforms)
        result = {platform_id: dedicated_rule(platform_id) for platform_id in selected}
    else:
        selected = _expand_selection(args.platforms)
        rows = []
        for platform_id in selected:
            try:
                if args.command == "install":
                    row = install(platform_id, scope=args.scope, project=project, home=home, force=args.force, dry_run=args.dry_run)
                elif args.command == "uninstall":
                    row = uninstall(platform_id, scope=args.scope, project=project, home=home)
                else:
                    row = status(platform_id, scope=args.scope, project=project, home=home)
            except ValueError as exc:
                row = {"platform": platform_id, "changed": False, "skipped": True, "reason": str(exc)}
            rows.append(row)
        result = {"command": args.command, "scope": args.scope, "results": rows}
    rendered = dump_json(result)
    if args.output:
        atomic_write_text(Path(args.output), rendered + "\n", mode=0o644)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
