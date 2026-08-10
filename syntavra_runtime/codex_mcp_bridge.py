from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from .bootstrap import resolve_codex_home
from .mcp_server import MCPServer
from .runtime_paths import discover_project_root, resolve_state_root


_BIND_TOOL = {
    "name": "syntavra.project.bind",
    "description": "Bind Syntavra to the active Git workspace before repository or process tools",
    "inputSchema": {
        "type": "object",
        "properties": {
            "project_root": {"type": "string"},
        },
        "required": ["project_root"],
    },
}


class CodexWorkspaceMCPBridge:
    """Fail-closed Codex MCP bridge with explicit workspace identity.

    Codex CLI usually starts stdio MCP servers from the expected working directory,
    but editor/desktop hosts have historically differed. Repository identity is too
    important to inherit from process cwd. User/global installs therefore start
    unbound and require an explicit active-workspace bind from the Agent Skill.
    Project-scoped installs carry ``SYNTAVRA_PROJECT`` and bind immediately.
    """

    def __init__(
        self,
        *,
        skill_root: Path | None = None,
        codex_home: Path | None = None,
        host: str = "codex",
    ) -> None:
        self.skill_root = (skill_root or self._skill_root()).resolve(strict=True)
        self.codex_home = (codex_home or resolve_codex_home()).resolve(strict=False)
        self.host = host
        self.server: MCPServer | None = None
        self.project: Path | None = None
        self.state_root: Path | None = None

        configured = str(os.environ.get("SYNTAVRA_PROJECT", "")).strip()
        if configured and configured.casefold() not in {"auto", "cwd", "."}:
            self.bind(configured)

    @staticmethod
    def _skill_root() -> Path:
        repository_skill = Path(__file__).resolve().parents[1] / "skills" / "syntavra"
        if (repository_skill / "SKILL.md").is_file():
            return repository_skill
        bundled = Path(__file__).resolve().parent / "bundled_skill"
        if not (bundled / "SKILL.md").is_file():
            raise FileNotFoundError("Syntavra skill is unavailable")
        return bundled

    @staticmethod
    def _canonical_git_root(value: str | Path) -> Path:
        start = Path(value).expanduser().resolve(strict=True)
        if not start.is_dir():
            raise NotADirectoryError(start)
        root = discover_project_root("auto", cwd=start, strict=False)
        marker = root / ".git"
        if not (marker.exists() or marker.is_file()):
            raise ValueError(f"workspace is not inside a Git worktree: {start}")
        return root.resolve(strict=True)

    def bind(self, value: str | Path) -> dict[str, Any]:
        project = self._canonical_git_root(value)
        state = resolve_state_root(project, None, namespace="pre-release")
        changed = self.project != project or self.server is None
        if changed:
            self.server = MCPServer(
                project=project,
                state_root=state,
                skill_root=self.skill_root,
                codex_home=self.codex_home,
                host=self.host,
            )
            self.project = project
            self.state_root = state
        return {
            "bound": True,
            "changed": changed,
            "project_root": str(project),
            "state_root": str(state),
            "state_outside_project": project not in state.parents and state != project,
        }

    def _bootstrap_server(self) -> MCPServer:
        """Create a protocol/catalog server only; repository tools remain blocked."""

        if self.server is not None:
            return self.server
        fallback = discover_project_root("auto", cwd=Path.cwd(), strict=False)
        state = resolve_state_root(fallback, None, namespace="codex-bootstrap")
        return MCPServer(
            project=fallback,
            state_root=state,
            skill_root=self.skill_root,
            codex_home=self.codex_home,
            host=self.host,
        )

    @staticmethod
    def _error(request_id: Any, *, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")

        if method == "tools/list":
            response = self._bootstrap_server().handle(message)
            assert response is not None
            tools = list(response["result"].get("tools") or [])
            if not any(row.get("name") == _BIND_TOOL["name"] for row in tools):
                tools.insert(0, dict(_BIND_TOOL))
            response["result"]["tools"] = tools
            response["result"].setdefault("_meta", {}).setdefault("syntavra", {})["workspace_binding"] = {
                "required": self.server is None,
                "bound": self.server is not None,
                "project_root": str(self.project) if self.project is not None else None,
            }
            return response

        if method == "tools/call":
            params = message.get("params") or {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if name == _BIND_TOOL["name"]:
                try:
                    result = self.bind(str(arguments.get("project_root") or ""))
                except (OSError, ValueError) as exc:
                    return self._error(
                        request_id,
                        code=-32003,
                        message="Syntavra workspace bind failed",
                        data={"reason": str(exc), "bound": False},
                    )
                return {"jsonrpc": "2.0", "id": request_id, "result": result}
            if self.server is None:
                return self._error(
                    request_id,
                    code=-32002,
                    message="Syntavra workspace binding required",
                    data={
                        "reason": "call syntavra.project.bind with the active Git workspace before repository/process tools",
                        "bound": False,
                    },
                )
            return self.server.handle(message)

        # initialize, ping and notifications are repository-independent.
        return self._bootstrap_server().handle(message)

    def serve(self, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> int:
        for line in input_stream:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            else:
                response = self.handle(message)
            if response is not None:
                output_stream.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
                output_stream.flush()
        return 0


def main() -> int:
    return CodexWorkspaceMCPBridge().serve()


if __name__ == "__main__":
    raise SystemExit(main())
