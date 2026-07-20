from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, Iterable

from .models import HookDecision


LONG_RUNNING_MARKERS = {
    "pytest", "unittest", "cargo", "npm", "pnpm", "yarn", "gradle", "mvn",
    "dotnet", "go", "make", "cmake", "ninja", "docker", "terraform",
}
DESTRUCTIVE_PATTERNS = (
    "rm -rf /",
    "rm -rf ~",
    "git reset --hard",
    "git clean -fdx",
    "format c:",
    "del /s /q c:\\",
)


def normalize_command(command: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(command, str):
        return tuple(shlex.split(command, posix=os.name != "nt"))
    return tuple(str(value) for value in command)


class HookEngine:
    def __init__(self, *, project_root: Path, broker_prefix: tuple[str, ...] = ("signalcore", "run", "--background", "--")):
        self.project_root = project_root.resolve(strict=True)
        self.broker_prefix = broker_prefix

    def pre_tool(self, payload: dict[str, Any]) -> HookDecision:
        tool = str(payload.get("tool") or payload.get("name") or "")
        raw_command = payload.get("command") or payload.get("argv") or ()
        command = normalize_command(raw_command)
        if tool not in {"shell", "bash", "exec", "command", "terminal"}:
            return HookDecision(True, "pass-through", command)
        joined = " ".join(command).casefold()
        if any(pattern in joined for pattern in DESTRUCTIVE_PATTERNS):
            return HookDecision(False, "blocked", command, ("destructive-command",))
        cwd = Path(payload.get("cwd") or self.project_root).resolve(strict=False)
        try:
            cwd.relative_to(self.project_root)
        except ValueError:
            return HookDecision(False, "blocked", command, ("cwd-outside-project",))
        executable = Path(command[0]).name.casefold() if command else ""
        long_running = executable in LONG_RUNNING_MARKERS or any(
            marker in joined for marker in (" test", " build", " install", " benchmark", " lint")
        )
        if long_running and command[: len(self.broker_prefix)] != self.broker_prefix:
            replacement = {
                "tool": tool,
                "argv": [*self.broker_prefix, *command],
                "cwd": str(cwd),
                "reason": "route-long-command-through-zero-poll-broker",
            }
            return HookDecision(True, "replace", command, ("long-running-command",), replacement)
        return HookDecision(True, "allow", command)

    @staticmethod
    def post_tool(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result") or {}
        if isinstance(result, str):
            return {"mode": "externalize", "text": result[:4096], "raw_length": len(result)}
        if isinstance(result, dict):
            value = dict(result)
            for key in ("stdout", "stderr", "output"):
                text = value.get(key)
                if isinstance(text, str) and len(text) > 4096:
                    value[key] = text[:4096] + "\n[truncated by SignalCore hook]"
                    value[f"{key}_raw_length"] = len(text)
            return {"mode": "bounded", "result": value}
        return {"mode": "pass-through", "result": result}


def run_hook(engine: HookEngine, phase: str, payload_text: str) -> str:
    payload = json.loads(payload_text)
    if phase == "pre":
        decision = engine.pre_tool(payload)
        return json.dumps({
            "allowed": decision.allowed,
            "mode": decision.mode,
            "command": decision.command,
            "reasons": decision.reasons,
            "replacement": decision.replacement,
        }, ensure_ascii=False, sort_keys=True)
    if phase == "post":
        return json.dumps(engine.post_tool(payload), ensure_ascii=False, sort_keys=True)
    raise ValueError(f"unknown hook phase: {phase}")
