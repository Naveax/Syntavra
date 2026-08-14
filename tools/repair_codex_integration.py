from __future__ import annotations

import argparse
import json
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any

from syntavra_runtime.codex_integration import parse_config, verify_entry
from syntavra_runtime.installer import HostInstaller
from syntavra_runtime.runtime_paths import default_state_root, discover_project_root


LEGACY_RUNTIME_NAMES = ("pre-release", "runtime-v3", "install")
LEGACY_CODEX_SKILL_RELATIVE = Path("skills") / "syntavra"


def repair_user_mcp_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove the obsolete JSON Syntavra MCP entry without touching other servers."""

    value = json.loads(json.dumps(config))
    servers = value.get("mcpServers")
    if not isinstance(servers, dict) or "syntavra" not in servers:
        return value, []
    servers.pop("syntavra", None)
    return value, ["removed-obsolete-json-syntavra-mcp-entry"]


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    destination = path.with_name(f"{path.name}.syntavra-backup-{stamp}")
    counter = 1
    while destination.exists():
        destination = path.with_name(f"{path.name}.syntavra-backup-{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, destination)
    return destination


def _migrate_paths(
    paths: list[tuple[str, Path]],
    destination_root: Path,
    *,
    apply: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stamp = time.strftime("%Y%m%dT%H%M%S")
    quarantine = destination_root / "legacy-import" / stamp
    for label, source in paths:
        if not source.exists():
            continue
        destination = quarantine / label
        rows.append({
            "source": str(source),
            "destination": str(destination),
            "action": "move" if apply else "would-move",
        })
        if apply:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError(destination)
            shutil.move(str(source), str(destination))
    return rows


def _legacy_repository_state(project: Path) -> list[tuple[str, Path]]:
    legacy = project / ".syntavra"
    return [
        (f"repository-state/{name}", legacy / name)
        for name in LEGACY_RUNTIME_NAMES
    ]


def _legacy_codex_skills(project: Path, codex_home: Path) -> list[tuple[str, Path]]:
    return [
        ("user-codex-skill", codex_home / LEGACY_CODEX_SKILL_RELATIVE),
        ("project-codex-skill", project / ".codex" / LEGACY_CODEX_SKILL_RELATIVE),
    ]


def _current_config_status(project: Path, codex_home: Path) -> dict[str, Any]:
    path = codex_home / "config.toml"
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "syntavra_present": False,
        "reasons": [],
    }
    if not path.is_file():
        return result
    text = path.read_text(encoding="utf-8", errors="strict")
    try:
        entry = parse_config(text)
    except tomllib.TOMLDecodeError as exc:
        result["reasons"] = [f"invalid-codex-config-toml:{exc}"]
        return result
    if not entry:
        return result
    result["syntavra_present"] = True
    result["reasons"] = verify_entry(entry, project=project, scope="user")
    return result


def repair(
    *,
    project: Path,
    codex_home: Path,
    apply: bool,
) -> dict[str, Any]:
    project = project.resolve(strict=False)
    codex_home = codex_home.resolve(strict=False)
    home = codex_home.parent
    state = default_state_root(project, namespace="pre-release", home=home)

    current = _current_config_status(project, codex_home)
    legacy_json_path = codex_home / "mcp.json"
    legacy_json: dict[str, Any] = {
        "path": str(legacy_json_path),
        "exists": legacy_json_path.is_file(),
        "changes": [],
        "backup": "",
    }
    repaired_legacy_json: dict[str, Any] | None = None
    if legacy_json_path.is_file():
        raw = json.loads(legacy_json_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError(f"legacy Codex MCP config root must be an object: {legacy_json_path}")
        repaired_legacy_json, changes = repair_user_mcp_config(raw)
        legacy_json["changes"] = changes

    legacy_paths = [*_legacy_repository_state(project), *_legacy_codex_skills(project, codex_home)]
    legacy_rows = _migrate_paths(legacy_paths, state, apply=False)
    legacy_requires_migration = bool(legacy_json["changes"] or legacy_rows)
    current_needs_repair = bool(current["syntavra_present"] and current["reasons"])
    install_current = legacy_requires_migration or current_needs_repair
    installer_result: dict[str, Any] | None = None

    if apply:
        if current.get("reasons") and str(current["reasons"][0]).startswith("invalid-codex-config-toml:"):
            raise ValueError(current["reasons"][0])
        if legacy_json["changes"] and repaired_legacy_json is not None:
            backup = _backup(legacy_json_path)
            legacy_json["backup"] = str(backup)
            legacy_json_path.write_text(
                json.dumps(repaired_legacy_json, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        legacy_rows = _migrate_paths(legacy_paths, state, apply=True)
        if install_current:
            skill_root = Path(__file__).resolve().parents[1] / "skills" / "syntavra"
            installer = HostInstaller(
                project=project,
                skill_root=skill_root,
                home=home,
            )
            installer_result = installer.install(["codex"], scope="user", dry_run=False)
            current = _current_config_status(project, codex_home)

    changed = bool(install_current or legacy_rows)
    current_ok = not current["reasons"] and (current["syntavra_present"] if install_current and apply else True)
    return {
        "ok": current_ok,
        "apply": apply,
        "project": str(project),
        "state_root": str(state),
        "state_outside_project": project not in state.parents and state != project,
        "current_codex_config": current,
        "legacy_json_config": legacy_json,
        "legacy_paths": legacy_rows,
        "current_install_required": install_current,
        "installer_result": installer_result,
        "changed": changed,
        "next": "restart Codex after the applied MCP migration" if apply and changed else "none",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair Syntavra/Codex project binding, current TOML integration and repository-state pollution"
    )
    parser.add_argument("--project", default="auto")
    parser.add_argument("--codex-home")
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = discover_project_root(args.project, strict=False)
    codex_home = (
        Path(args.codex_home).expanduser().resolve(strict=False)
        if args.codex_home
        else Path.home() / ".codex"
    )
    result = repair(project=project, codex_home=codex_home, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
