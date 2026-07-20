from __future__ import annotations

import json
import os
import shlex
import time
from pathlib import Path
from typing import Any, Iterable

from .compression import ContentRouter
from .models import HookDecision


LONG_RUNNING_MARKERS = {
    "pytest", "unittest", "cargo", "npm", "pnpm", "yarn", "gradle", "mvn",
    "dotnet", "go", "make", "cmake", "ninja", "docker", "podman", "terraform",
    "tox", "nox", "ruff", "mypy", "eslint", "vitest", "jest", "ctest",
}
DESTRUCTIVE_PATTERNS = (
    "rm -rf /", "rm -rf ~", "git reset --hard", "git clean -fdx", "git clean -xdf",
    "format c:", "del /s /q c:\\", "remove-item -recurse -force c:\\", "mkfs.", ":(){:|:&};:",
)
NETWORK_MARKERS = {"curl", "wget", "Invoke-WebRequest", "iwr", "npm", "pip", "uv", "cargo", "go", "git"}


def normalize_command(command: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(command, str):
        return tuple(shlex.split(command, posix=os.name != "nt"))
    return tuple(str(value) for value in command)


class HookEngine:
    def __init__(
        self,
        *,
        project_root: Path,
        broker_prefix: tuple[str, ...] = ("signalcore", "run", "--background", "--"),
        sandbox_prefix: tuple[str, ...] = ("signalcore", "sandbox", "execute", "--"),
        compressor: ContentRouter | None = None,
    ):
        self.project_root = project_root.resolve(strict=True)
        self.broker_prefix = broker_prefix
        self.sandbox_prefix = sandbox_prefix
        self.compressor = compressor

    def _cwd(self, payload: dict[str, Any]) -> Path:
        cwd = Path(payload.get("cwd") or self.project_root).resolve(strict=False)
        try:
            cwd.relative_to(self.project_root)
        except ValueError as exc:
            raise PermissionError("cwd-outside-project") from exc
        return cwd

    def pre_tool(self, payload: dict[str, Any]) -> HookDecision:
        tool = str(payload.get("tool") or payload.get("name") or "")
        raw_command = payload.get("command") or payload.get("argv") or ()
        command = normalize_command(raw_command)
        if tool not in {"shell", "bash", "exec", "command", "terminal", "powershell"}:
            return HookDecision(True, "pass-through", command)
        joined = " ".join(command).casefold()
        if any(pattern in joined for pattern in DESTRUCTIVE_PATTERNS):
            return HookDecision(False, "blocked", command, ("destructive-command",))
        try:
            cwd = self._cwd(payload)
        except PermissionError:
            return HookDecision(False, "blocked", command, ("cwd-outside-project",))
        executable = Path(command[0]).name.casefold() if command else ""
        explicit_sandbox = bool(payload.get("sandbox")) or bool(payload.get("untrusted"))
        risky = explicit_sandbox or any(marker.casefold() == executable for marker in NETWORK_MARKERS) and bool(payload.get("network_untrusted"))
        if risky and command[: len(self.sandbox_prefix)] != self.sandbox_prefix:
            replacement = {
                "tool": tool,
                "argv": [*self.sandbox_prefix, *command],
                "cwd": str(cwd),
                "reason": "route-untrusted-command-through-secure-sandbox",
            }
            return HookDecision(True, "replace", command, ("sandbox-required",), replacement)
        long_running = executable in LONG_RUNNING_MARKERS or any(
            marker in joined for marker in (" test", " build", " install", " benchmark", " lint", " check", " compile")
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

    def post_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result") or {}
        if isinstance(result, str):
            if self.compressor and len(result.encode("utf-8")) > 4096:
                compressed = self.compressor.compress(result, hint=str(payload.get("content_type", "log")), budget_bytes=4096)
                return {"mode": "reversible-compressed", "compression": compressed.__dict__}
            return {"mode": "bounded", "text": result[:4096], "raw_length": len(result)}
        if isinstance(result, dict):
            value = dict(result)
            compressions: dict[str, Any] = {}
            for key in ("stdout", "stderr", "output", "content", "text"):
                text = value.get(key)
                if isinstance(text, str) and len(text.encode("utf-8")) > 4096:
                    if self.compressor:
                        compressed = self.compressor.compress(text, hint=str(payload.get("content_type", "log")), path=str(payload.get("path", "")), budget_bytes=4096)
                        value[key] = compressed.visible_text
                        compressions[key] = compressed.__dict__
                    else:
                        value[key] = text[:4096] + "\n[truncated by SignalCore hook]"
                        value[f"{key}_raw_length"] = len(text)
            return {"mode": "reversible-compressed" if compressions else "bounded", "result": value, "compressions": compressions}
        return {"mode": "pass-through", "result": result}

    def session_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "mode": "activate-runtime",
            "project": str(self.project_root),
            "task": payload.get("task") or payload.get("prompt") or "",
            "required_actions": ("runtime-status", "structural-index", "session-open"),
            "timestamp": time.time(),
        }

    @staticmethod
    def prompt_submit(payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt") or payload.get("text") or "")
        risk = "security-critical" if any(word in prompt.casefold() for word in ("security", "auth", "permission", "secret", "crypto")) else "normal"
        return {"mode": "classify-task", "risk": risk, "prompt_bytes": len(prompt.encode("utf-8"))}

    @staticmethod
    def pre_compact(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "mode": "checkpoint-before-compact",
            "session_id": payload.get("session_id"),
            "required": ("exact-history-checkpoint", "summary-dag-root", "evidence-handles"),
        }

    @staticmethod
    def post_compact(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "mode": "verify-after-compact",
            "session_id": payload.get("session_id"),
            "checks": ("history-chain", "summary-expansion", "mandatory-context-roles"),
        }

    @staticmethod
    def stop(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "flush-runtime", "session_id": payload.get("session_id"), "actions": ("flush-events", "checkpoint", "drain-completions")}

    @staticmethod
    def session_end(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "close-session", "session_id": payload.get("session_id"), "actions": ("final-checkpoint", "claim-boundary", "release-locks")}


def run_hook(engine: HookEngine, phase: str, payload_text: str) -> str:
    payload = json.loads(payload_text or "{}")
    handlers = {
        "pre": lambda: _decision(engine.pre_tool(payload)),
        "post": lambda: engine.post_tool(payload),
        "session-start": lambda: engine.session_start(payload),
        "prompt": lambda: engine.prompt_submit(payload),
        "pre-compact": lambda: engine.pre_compact(payload),
        "post-compact": lambda: engine.post_compact(payload),
        "stop": lambda: engine.stop(payload),
        "session-end": lambda: engine.session_end(payload),
    }
    if phase not in handlers:
        raise ValueError(f"unknown hook phase: {phase}")
    return json.dumps(handlers[phase](), ensure_ascii=False, sort_keys=True)


def _decision(decision: HookDecision) -> dict[str, Any]:
    return {
        "allowed": decision.allowed,
        "mode": decision.mode,
        "command": decision.command,
        "reasons": decision.reasons,
        "replacement": decision.replacement,
    }
