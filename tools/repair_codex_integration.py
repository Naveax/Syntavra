from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

from syntavra_runtime.runtime_paths import default_state_root, discover_project_root


LEGACY_RUNTIME_NAMES = ("pre-release", "runtime-v3", "install")


def _strip_option(argv: list[str], name: str) -> list[str]:
    result: list[str] = []
    index = 0
    prefix = name + "="
    while index < len(argv):
        value = str(argv[index])
        if value == name:
            index += 2
            continue
        if value.startswith(prefix):
            index += 1
            continue
        result.append(value)
        index += 1
    return result


def repair_user_mcp_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove legacy user-scope project/state pins without touching other servers."""

    value = json.loads(json.dumps(config))
    servers = value.get("mcpServers")
    if not isinstance(servers, dict):
        return value, []
    entry = servers.get("syntavra")
    if not isinstance(entry, dict):
        return value, []

    changes: list[str] = []
    argv = [str(item) for item in entry.get("args", [])] if isinstance(entry.get("args"), list) else []
    stripped = _strip_option(_strip_option(argv, "--project"), "--state-root")
    if stripped != argv:
        entry["args"] = stripped
        changes.append("removed-static-project-or-state-argv")
    if "cwd" in entry:
        entry.pop("cwd", None)
        changes.append("removed-static-cwd")
    env = entry.get("env")
    if isinstance(env, dict):
        for name in ("SYNTAVRA_PROJECT", "SYNTAVRA_STATE_ROOT"):
            if name in env:
                env.pop(name, None)
                changes.append(f"removed-{name.casefold()}")
    return value, changes


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    destination = path.with_name(f"{path.name}.syntavra-backup-{stamp}")
    counter = 1
    while destination.exists():
        destination = path.with_name(f"{path.name}.syntavra-backup-{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, destination)
    return destination


def _migrate_legacy_state(project: Path, destination_root: Path, *, apply: bool) -> list[dict[str, Any]]:
    legacy = project / ".syntavra"
    rows: list[dict[str, Any]] = []
    if not legacy.is_dir():
        return rows
    stamp = time.strftime("%Y%m%dT%H%M%S")
    quarantine = destination_root / "legacy-import" / stamp
    for name in LEGACY_RUNTIME_NAMES:
        source = legacy / name
        if not source.exists():
            continue
        destination = quarantine / name
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
    if apply and legacy.is_dir():
        try:
            legacy.rmdir()
        except OSError:
            # Preserve unknown/user-managed files such as an explicit engine.json.
            pass
    return rows


def repair(
    *,
    project: Path,
    codex_home: Path,
    apply: bool,
) -> dict[str, Any]:
    state = default_state_root(project, namespace="pre-release")
    config_path = codex_home / "mcp.json"
    config_result: dict[str, Any] = {
        "path": str(config_path),
        "exists": config_path.is_file(),
        "changes": [],
        "backup": "",
    }
    if config_path.is_file():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError(f"Codex MCP config root must be an object: {config_path}")
        repaired, changes = repair_user_mcp_config(raw)
        config_result["changes"] = changes
        if changes and apply:
            backup = _backup(config_path)
            config_result["backup"] = str(backup)
            config_path.write_text(
                json.dumps(repaired, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    legacy_rows = _migrate_legacy_state(project, state, apply=apply)
    return {
        "ok": True,
        "apply": apply,
        "project": str(project),
        "state_root": str(state),
        "state_outside_project": project.resolve(strict=False) not in state.resolve(strict=False).parents,
        "codex_config": config_result,
        "legacy_state": legacy_rows,
        "changed": bool(config_result["changes"] or legacy_rows),
        "next": "restart Codex after an applied MCP config repair" if apply and config_result["changes"] else "none",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair legacy Syntavra/Codex project binding and repository state pollution")
    parser.add_argument("--project", default="auto")
    parser.add_argument("--codex-home")
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = discover_project_root(args.project, strict=False)
    codex_home = Path(args.codex_home).expanduser().resolve(strict=False) if args.codex_home else Path.home() / ".codex"
    result = repair(project=project, codex_home=codex_home, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
