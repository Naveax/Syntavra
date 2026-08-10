from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Mapping, Sequence

from .release_identity import CHANNEL, VERSION


CODEX_CONFIG_PATH = ".codex/config.toml"
CODEX_SKILL_PATH = ".agents/skills/syntavra"
MANAGED_START = "# SYNTAVRA-MANAGED-MCP-START"
MANAGED_END = "# SYNTAVRA-MANAGED-MCP-END"
_TABLE_RE = re.compile(r"^\s*(\[\[?)(.+?)(\]\]?)\s*(?:#.*)?$")
_BRIDGE_ARGS = ["-m", "syntavra_runtime.codex_mcp_bridge"]


def _normalize_table_name(value: str) -> str:
    return value.strip().replace('"syntavra"', "syntavra").replace("'syntavra'", "syntavra")


def strip_syntavra_tables(text: str) -> str:
    """Remove only Syntavra's MCP tables while preserving unrelated Codex TOML."""

    marker_pattern = re.compile(
        re.escape(MANAGED_START) + r".*?" + re.escape(MANAGED_END) + r"\s*",
        flags=re.DOTALL,
    )
    text = marker_pattern.sub("", text)
    output: list[str] = []
    skipping = False
    for line in text.splitlines():
        match = _TABLE_RE.match(line)
        if match:
            name = _normalize_table_name(match.group(2))
            skipping = name == "mcp_servers.syntavra" or name.startswith("mcp_servers.syntavra.")
        if not skipping:
            output.append(line)
    return "\n".join(output).rstrip()


def mcp_entry(
    executable: Sequence[str],
    *,
    project: Path,
    scope: str,
) -> dict[str, Any]:
    """Return the current Codex stdio entry.

    The generic executable argument is retained for API compatibility, but Codex
    always launches the dedicated workspace bridge with the current interpreter so
    user/global integration cannot derive repository identity from process cwd.
    """

    if not executable:
        raise ValueError("Codex MCP executable must not be empty")
    entry: dict[str, Any] = {
        "command": sys.executable,
        "args": list(_BRIDGE_ARGS),
        "env": {
            "SYNTAVRA_VERSION": VERSION,
            "SYNTAVRA_CHANNEL": CHANNEL,
        },
    }
    if scope == "project":
        resolved = project.resolve(strict=False)
        entry["cwd"] = str(resolved)
        entry["env"]["SYNTAVRA_PROJECT"] = str(resolved)
    elif scope != "user":
        raise ValueError("scope must be project or user")
    return entry


def render_config(existing: str, entry: Mapping[str, Any]) -> str:
    """Render one current Codex `[mcp_servers.syntavra]` block, fail-closed on TOML."""

    if existing.strip():
        try:
            tomllib.loads(existing)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"refusing to modify invalid Codex TOML: {exc}") from exc

    base = strip_syntavra_tables(existing)
    command = str(entry.get("command") or "")
    args = [str(item) for item in entry.get("args", [])]
    env = entry.get("env") if isinstance(entry.get("env"), Mapping) else {}
    block = [
        MANAGED_START,
        "[mcp_servers.syntavra]",
        f"command = {json.dumps(command, ensure_ascii=False)}",
        f"args = {json.dumps(args, ensure_ascii=False)}",
        "enabled = true",
        "startup_timeout_sec = 20",
        "tool_timeout_sec = 120",
    ]
    if entry.get("cwd"):
        block.append(f"cwd = {json.dumps(str(entry['cwd']), ensure_ascii=False)}")
    block.extend(("", "[mcp_servers.syntavra.env]"))
    for key, value in sorted((str(k), str(v)) for k, v in env.items()):
        block.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    block.append(MANAGED_END)
    rendered = base + ("\n\n" if base else "") + "\n".join(block) + "\n"
    try:
        tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"generated Codex TOML is invalid: {exc}") from exc
    return rendered


def parse_config(text: str) -> dict[str, Any]:
    parsed = tomllib.loads(text)
    servers = parsed.get("mcp_servers")
    if not isinstance(servers, Mapping):
        return {}
    value = servers.get("syntavra")
    return dict(value) if isinstance(value, Mapping) else {}


def verify_entry(entry: Mapping[str, Any], *, project: Path, scope: str) -> list[str]:
    reasons: list[str] = []
    command = str(entry.get("command") or "")
    args = [str(item) for item in entry.get("args", [])] if isinstance(entry.get("args"), list) else []
    env = entry.get("env") if isinstance(entry.get("env"), Mapping) else {}
    if not command:
        reasons.append("missing-syntavra-mcp-command")
    if args != _BRIDGE_ARGS:
        reasons.append("invalid-syntavra-codex-bridge-args")
    resolved = str(project.resolve(strict=False))
    if scope == "project":
        if entry.get("cwd") != resolved:
            reasons.append("incorrect-project-cwd")
        if env.get("SYNTAVRA_PROJECT") != resolved:
            reasons.append("incorrect-project-env")
    elif scope == "user":
        if entry.get("cwd"):
            reasons.append("user-scope-static-cwd")
        if env.get("SYNTAVRA_PROJECT"):
            reasons.append("user-scope-static-project-env")
    else:
        reasons.append("invalid-scope")
    return reasons