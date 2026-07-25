from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .autonomous_agent import AgentMode, AgentRunReceipt, AgentTask, AutonomousCodingAgent, PatchProposal
from .execution_sandbox import ExecutionReceipt, SandboxPolicy
from .model_gateway import ModelGateway
from .project_model import ProjectModel, VerifierSpec


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")


class AgentDeliveryMode(str, Enum):
    DIFF = "diff"
    WORKTREE = "worktree"
    APPLY = "apply"
    COMMIT = "commit"
    PR = "pr"


@dataclass(frozen=True)
class AgentEvent:
    sequence: int
    event_type: str
    created_at: float
    payload: Mapping[str, Any]


class EventSink(Protocol):
    def __call__(self, event: AgentEvent) -> None: ...


class AgentEventJournal:
    """Synchronous event stream used by JSONL, TUI and dashboard transports."""

    def __init__(self, sink: EventSink | None = None) -> None:
        self._sink = sink
        self._events: list[AgentEvent] = []

    def emit(self, event_type: str, **payload: Any) -> AgentEvent:
        event = AgentEvent(len(self._events) + 1, str(event_type), time.time(), dict(payload))
        self._events.append(event)
        if self._sink is not None:
            try:
                self._sink(event)
            except Exception:
                # Rendering/transport failures must not mutate agent execution.
                pass
        return event

    @property
    def events(self) -> tuple[AgentEvent, ...]:
        return tuple(self._events)


@dataclass(frozen=True)
class AgentDeliveryReceipt:
    mode: AgentDeliveryMode
    ok: bool
    workspace: str
    branch: str = ""
    commit: str = ""
    pull_request_url: str = ""
    applied_files: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class AgentProductReceipt:
    run: AgentRunReceipt
    provider: str
    model: str
    verifier: VerifierSpec
    post_verifiers: tuple[dict[str, Any], ...]
    tool_trace: tuple[dict[str, Any], ...]
    usage: Mapping[str, int]
    delivery: AgentDeliveryReceipt = field(default_factory=lambda: AgentDeliveryReceipt(AgentDeliveryMode.DIFF, True, ""))
    events: tuple[AgentEvent, ...] = ()
    verification_complete: bool = True
    delivery_options: tuple[str, ...] = tuple(item.value for item in AgentDeliveryMode)
    limitations: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.run.ok
            and self.verification_complete
            and all(bool(item.get("ok")) for item in self.post_verifiers)
            and self.delivery.ok
        )


class AgentContextAssembler:
    """Build a bounded repository packet rather than a bare lexical hit list."""

    INSTRUCTION_FILES = (
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        "README.md",
        ".github/copilot-instructions.md",
    )
    PROJECT_FILES = (
        "pyproject.toml",
        "package.json",
        "pnpm-workspace.yaml",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "CMakeLists.txt",
        "Makefile",
    )

    def __init__(self, project: Path, graph: Any, project_model: ProjectModel, *, max_bytes: int = 120_000) -> None:
        self.project = project.resolve(strict=True)
        self.graph = graph
        self.project_model = project_model
        self.max_bytes = max(16_384, min(int(max_bytes), 500_000))

    def _read(self, root: Path, relative: str, remaining: int) -> dict[str, Any] | None:
        path = (root / relative).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError:
            return None
        if not path.is_file() or remaining <= 0:
            return None
        data = path.read_bytes()
        bounded = data[:remaining]
        return {
            "path": relative,
            "bytes": len(data),
            "truncated": len(data) > len(bounded),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": bounded.decode("utf-8", errors="replace"),
        }

    def assemble(
        self,
        instruction: str,
        semantic_results: Sequence[Mapping[str, Any]],
        *,
        root: Path | None = None,
    ) -> dict[str, Any]:
        source_root = (root or self.project).resolve(strict=True)
        remaining = self.max_bytes
        files: list[dict[str, Any]] = []
        candidates = [*self.INSTRUCTION_FILES, *self.PROJECT_FILES]
        candidates.extend(str(item.get("path") or "") for item in semantic_results[:12])
        for relative in dict.fromkeys(item for item in candidates if item):
            row = self._read(source_root, relative, remaining)
            if row is None:
                continue
            files.append(row)
            remaining -= len(str(row["content"]).encode("utf-8"))
            if remaining <= 0:
                break
        return {
            "instruction": instruction,
            "repository": self.project_model.describe(),
            "semantic_results": list(semantic_results)[:20],
            "files": files,
            "bounded": remaining <= 0,
            "max_bytes": self.max_bytes,
        }


class StructuredEditCompiler:
    """Compile exact, bounded structured edits into a git-apply compatible patch."""

    def __init__(self, *, max_file_bytes: int = 1_000_000, max_edits: int = 32) -> None:
        self.max_file_bytes = max(4096, int(max_file_bytes))
        self.max_edits = max(1, min(int(max_edits), 128))

    @staticmethod
    def _relative(value: str) -> str:
        path = Path(str(value))
        if path.is_absolute() or not path.parts or any(part in {"", ".", "..", ".git"} for part in path.parts):
            raise ValueError(f"invalid structured edit path: {value}")
        return path.as_posix()

    @staticmethod
    def _safe_path(root: Path, relative: str) -> Path:
        path = (root / relative).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise PermissionError(f"structured edit escapes repository: {relative}") from error
        return path

    def _read(self, root: Path, relative: str) -> tuple[bool, str]:
        path = self._safe_path(root, relative)
        if not path.exists():
            return False, ""
        if not path.is_file():
            raise ValueError(f"structured edit target is not a file: {relative}")
        data = path.read_bytes()
        if len(data) > self.max_file_bytes:
            raise ValueError(f"structured edit file exceeds limit: {relative}")
        if b"\x00" in data:
            raise ValueError(f"structured edit does not support binary files: {relative}")
        return True, data.decode("utf-8", errors="strict")

    @staticmethod
    def _line_replace(text: str, start: int, end: int, content: str) -> str:
        lines = text.splitlines(keepends=True)
        if start < 1 or end < start or end > len(lines):
            raise ValueError("structured line range is out of bounds")
        newline = "\r\n" if "\r\n" in text else "\n"
        replacement = content
        if replacement and not replacement.endswith(("\n", "\r")) and end < len(lines):
            replacement += newline
        return "".join([*lines[: start - 1], replacement, *lines[end:]])

    def compile(self, root: Path, edits: Sequence[Mapping[str, Any]]) -> str:
        root = root.resolve(strict=True)
        if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes)):
            raise TypeError("structured edits must be a sequence")
        if not edits or len(edits) > self.max_edits:
            raise ValueError("structured edit count is invalid")
        original: dict[str, tuple[bool, str]] = {}
        current: dict[str, str] = {}
        deleted: set[str] = set()

        for raw in edits:
            if not isinstance(raw, Mapping):
                raise TypeError("structured edit entry must be an object")
            relative = self._relative(str(raw.get("path") or ""))
            operation = str(raw.get("operation") or "replace").casefold()
            if relative not in original:
                original[relative] = self._read(root, relative)
                current[relative] = original[relative][1]
            exists, _ = original[relative]

            if operation == "create":
                if exists or current[relative]:
                    raise ValueError(f"cannot create existing file: {relative}")
                content = str(raw.get("content") or "")
                if "\x00" in content or len(content.encode("utf-8")) > self.max_file_bytes:
                    raise ValueError(f"invalid create content: {relative}")
                current[relative] = content
                deleted.discard(relative)
                continue
            if operation == "delete":
                if not exists:
                    raise ValueError(f"cannot delete missing file: {relative}")
                expected = str(raw.get("expected_sha256") or "")
                if expected and hashlib.sha256(current[relative].encode("utf-8")).hexdigest() != expected:
                    raise ValueError(f"delete precondition failed: {relative}")
                current[relative] = ""
                deleted.add(relative)
                continue
            if not exists:
                raise ValueError(f"structured edit target does not exist: {relative}")
            if operation == "replace":
                old = str(raw.get("old") or "")
                new = str(raw.get("new") or "")
                expected_count = int(raw.get("count", 1))
                if not old or expected_count < 1:
                    raise ValueError("replace requires a non-empty old value and positive count")
                actual = current[relative].count(old)
                if actual != expected_count:
                    raise ValueError(
                        f"replace precondition failed for {relative}: expected {expected_count}, found {actual}"
                    )
                current[relative] = current[relative].replace(old, new, expected_count)
            elif operation == "line-replace":
                current[relative] = self._line_replace(
                    current[relative],
                    int(raw.get("start_line", 0)),
                    int(raw.get("end_line", 0)),
                    str(raw.get("content") or ""),
                )
            else:
                raise ValueError(f"unsupported structured edit operation: {operation}")
            if len(current[relative].encode("utf-8")) > self.max_file_bytes:
                raise ValueError(f"structured edit result exceeds limit: {relative}")

        chunks: list[str] = []
        for relative in sorted(current):
            existed, before = original[relative]
            after = current[relative]
            if existed and before == after and relative not in deleted:
                continue
            header = [f"diff --git a/{relative} b/{relative}"]
            if not existed:
                header.append("new file mode 100644")
                fromfile, tofile = "/dev/null", f"b/{relative}"
            elif relative in deleted:
                header.append("deleted file mode 100644")
                fromfile, tofile = f"a/{relative}", "/dev/null"
            else:
                fromfile, tofile = f"a/{relative}", f"b/{relative}"
            before_diff = before.replace("\r\n", "\n").replace("\r", "\n")
            after_diff = after.replace("\r\n", "\n").replace("\r", "\n")
            body = list(
                difflib.unified_diff(
                    before_diff.splitlines(keepends=True),
                    after_diff.splitlines(keepends=True),
                    fromfile=fromfile,
                    tofile=tofile,
                    lineterm="",
                )
            )
            if not body:
                continue
            rendered = "\n".join(line.rstrip("\n") for line in [*header, *body]) + "\n"
            chunks.append(rendered)
        if not chunks:
            raise ValueError("structured edits produced no changes")
        return "".join(chunks)


class GatewayPatchProvider:
    """Model-backed bounded tool loop for the verified agent executor."""

    SYSTEM_PROMPT = """You are the patch-planning component of Syntavra.
Return exactly one JSON object and no markdown.
Allowed actions:
- {"action":"search","query":"..."}
- {"action":"inspect","paths":["relative/path.py"]}
- {"action":"diff"}
- {"action":"impact","node_id":"..."}
- {"action":"verifiers"}
- {"action":"run_verifier","name":"..."}
- {"action":"edit","edits":[{"path":"...","operation":"replace","old":"...","new":"...","count":1}],"rationale":"..."}
- {"action":"patch","patch":"unified diff","rationale":"..."}
Use search, inspect, impact or a verifier when evidence is insufficient. Never invent file contents.
Patch or structured edits must stay inside the repository and must be suitable for git apply.
"""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        project: Path,
        graph: Any,
        project_model: ProjectModel,
        sandbox: Any | None = None,
        context_assembler: AgentContextAssembler | None = None,
        journal: AgentEventJournal | None = None,
        max_tool_rounds: int = 8,
        max_file_bytes: int = 120_000,
        max_verifier_runs: int = 3,
    ) -> None:
        self.gateway = gateway
        self.project = project.resolve(strict=True)
        self.graph = graph
        self.project_model = project_model
        self.sandbox = sandbox
        self.context_assembler = context_assembler or AgentContextAssembler(self.project, graph, project_model)
        self.journal = journal or AgentEventJournal()
        self.max_tool_rounds = max(1, min(int(max_tool_rounds), 16))
        self.max_file_bytes = max(4096, min(int(max_file_bytes), 500_000))
        self.max_verifier_runs = max(0, min(int(max_verifier_runs), 8))
        self.trace: list[dict[str, Any]] = []
        self.usage: dict[str, int] = {}
        self.provider = type(gateway).__name__
        self.model = getattr(getattr(gateway, "config", None), "model", getattr(gateway, "model", "unknown"))
        self.edit_compiler = StructuredEditCompiler(max_file_bytes=self.max_file_bytes)
        self._verifier_runs = 0

    @staticmethod
    def _action(text: str) -> dict[str, Any]:
        cleaned = _JSON_FENCE_RE.sub("", text.strip()).strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("model response is not a JSON action")
            value = json.loads(cleaned[start : end + 1])
        if not isinstance(value, dict) or not value.get("action"):
            raise ValueError("model action must be a JSON object with an action")
        return value

    @staticmethod
    def _safe_path(root: Path, value: str) -> Path:
        candidate = (root / value).resolve(strict=True)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise PermissionError(f"model requested a path outside the repository: {value}") from error
        if not candidate.is_file():
            raise ValueError(f"model requested a non-file path: {value}")
        return candidate

    def _inspect(self, paths: Sequence[str], *, root: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for value in list(dict.fromkeys(str(item) for item in paths))[:12]:
            path = self._safe_path(root, value)
            data = path.read_bytes()
            bounded = data[: self.max_file_bytes]
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "truncated": len(data) > len(bounded),
                    "content": bounded.decode("utf-8", errors="replace"),
                }
            )
        return rows

    def _record_usage(self, values: Mapping[str, int]) -> None:
        for key, value in values.items():
            self.usage[key] = self.usage.get(key, 0) + max(0, int(value))

    def _verifiers(self) -> tuple[VerifierSpec, ...]:
        return self.project_model.discover_verifiers()

    def _run_verifier(self, name: str, workspace: Path, timeout_seconds: float) -> dict[str, Any]:
        if self.sandbox is None:
            raise RuntimeError("run_verifier is unavailable because no sandbox is configured")
        if self._verifier_runs >= self.max_verifier_runs:
            raise RuntimeError("model verifier-run budget exhausted")
        matches = [item for item in self._verifiers() if item.name == name]
        if len(matches) != 1:
            raise ValueError(f"unknown or ambiguous verifier: {name}")
        self._verifier_runs += 1
        verifier = matches[0]
        receipt: ExecutionReceipt = self.sandbox.run(
            verifier.argv,
            policy=SandboxPolicy(
                workspace=workspace,
                timeout_seconds=max(1.0, timeout_seconds),
                strict_native=False,
            ),
        )
        return {
            "name": verifier.name,
            "argv": list(verifier.argv),
            "ok": receipt.ok,
            "exit_code": receipt.exit_code,
            "timed_out": receipt.timed_out,
            "stdout": receipt.stdout[-24_000:],
            "stderr": receipt.stderr[-24_000:],
        }

    @staticmethod
    def _append_tool(messages: list[dict[str, str]], action: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        messages.extend(
            (
                {"role": "assistant", "content": json.dumps(action, ensure_ascii=False)},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
            )
        )

    def propose(self, task: AgentTask, context: Mapping[str, Any], previous_failure: Mapping[str, Any] | None) -> PatchProposal:
        usage_before = dict(self.usage)
        workspace = Path(str(context.get("workspace") or self.project)).resolve(strict=True)
        semantic_results = list(context.get("semantic_results") or ())[:20]
        working: dict[str, Any] = self.context_assembler.assemble(
            task.instruction,
            semantic_results,
            root=workspace,
        )
        working.update(
            {
                "mode": task.mode.value,
                "attempt": context.get("attempt"),
                "current_diff": str(context.get("current_diff") or "")[-120_000:],
                "changed_files": list(context.get("changed_files") or ()),
                "previous_failure": previous_failure,
            }
        )
        messages: list[dict[str, str]] = [
            {"role": "user", "content": json.dumps(working, ensure_ascii=False, sort_keys=True)}
        ]
        self.journal.emit("model-loop-started", attempt=context.get("attempt"), workspace=str(workspace))

        for round_number in range(1, self.max_tool_rounds + 1):
            self.journal.emit("model-requested", round=round_number)
            result = self.gateway.complete(messages, system=self.SYSTEM_PROMPT)
            self.provider = result.provider
            self.model = result.model
            self._record_usage(result.usage)
            action = self._action(result.text)
            name = str(action.get("action")).casefold()
            trace: dict[str, Any] = {"round": round_number, "action": name}
            self.journal.emit("model-action", round=round_number, action=name)

            if name == "search":
                query = str(action.get("query") or task.instruction)
                rows = self.graph.query(query, limit=20)
                trace.update(query=query, results=len(rows))
                self.trace.append(trace)
                self._append_tool(messages, action, {"tool": "repo.search", "query": query, "results": rows})
                continue
            if name == "inspect":
                raw_paths = action.get("paths") or []
                if not isinstance(raw_paths, list):
                    raise ValueError("inspect action paths must be a list")
                rows = self._inspect([str(item) for item in raw_paths], root=workspace)
                trace["paths"] = [row["path"] for row in rows]
                self.trace.append(trace)
                self._append_tool(messages, action, {"tool": "repo.read", "files": rows})
                continue
            if name == "diff":
                diff = str(context.get("current_diff") or "")[-120_000:]
                trace["bytes"] = len(diff.encode("utf-8"))
                self.trace.append(trace)
                self._append_tool(messages, action, {"tool": "repo.diff", "diff": diff})
                continue
            if name == "impact":
                node_id = str(action.get("node_id") or "")
                if not node_id:
                    raise ValueError("impact action requires node_id")
                result_value = self.graph.impact(node_id, max_depth=6)
                trace["node_id"] = node_id
                self.trace.append(trace)
                self._append_tool(messages, action, {"tool": "repo.impact", "result": result_value})
                continue
            if name == "verifiers":
                rows = [asdict(item) for item in self._verifiers()]
                trace["count"] = len(rows)
                self.trace.append(trace)
                self._append_tool(messages, action, {"tool": "test.discover", "verifiers": rows})
                continue
            if name == "run_verifier":
                verifier_result = self._run_verifier(
                    str(action.get("name") or ""), workspace, task.timeout_seconds
                )
                trace.update(name=verifier_result["name"], ok=verifier_result["ok"])
                self.trace.append(trace)
                self._append_tool(messages, action, {"tool": "test.run", "result": verifier_result})
                continue
            if name == "edit":
                edits = action.get("edits") or []
                if not isinstance(edits, list):
                    raise ValueError("edit action requires an edits list")
                patch = self.edit_compiler.compile(workspace, edits)
                rationale = str(action.get("rationale") or "structured edit")
                trace.update(edits=len(edits), patch_bytes=len(patch.encode("utf-8")))
                self.trace.append(trace)
                self.journal.emit("patch-proposed", round=round_number, source="structured-edit", bytes=trace["patch_bytes"])
                return self._proposal(patch, rationale, result, usage_before)
            if name == "patch":
                patch = str(action.get("patch") or "")
                rationale = str(action.get("rationale") or "")
                trace["patch_bytes"] = len(patch.encode("utf-8"))
                self.trace.append(trace)
                self.journal.emit("patch-proposed", round=round_number, source="unified-diff", bytes=trace["patch_bytes"])
                return self._proposal(patch, rationale, result, usage_before)
            raise ValueError(f"unsupported model action: {name}")
        raise RuntimeError("model exhausted the bounded tool loop without producing a patch")

    def _proposal(self, patch: str, rationale: str, result: Any, usage_before: Mapping[str, int]) -> PatchProposal:
        return PatchProposal(
            patch=patch,
            rationale=rationale,
            estimated_tokens=max(
                0,
                int(
                    self.usage.get("input_tokens", 0)
                    + self.usage.get("output_tokens", 0)
                    - usage_before.get("input_tokens", 0)
                    - usage_before.get("output_tokens", 0)
                ),
            ),
            metadata={"provider": result.provider, "model": result.model, "tool_trace": list(self.trace)},
        )


class AgentDeliveryManager:
    """Explicitly-authorized delivery from the verified isolated worktree."""

    def __init__(self, project: Path) -> None:
        self.project = project.resolve(strict=True)

    @staticmethod
    def _run(argv: Sequence[str], *, cwd: Path, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(item) for item in argv],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def _branch(value: str) -> str:
        candidate = value.strip()
        if not _SAFE_BRANCH_RE.fullmatch(candidate) or ".." in candidate or candidate.endswith(("/", ".lock")):
            raise ValueError("delivery branch name is invalid")
        return candidate

    def deliver(
        self,
        run: AgentRunReceipt,
        *,
        mode: AgentDeliveryMode,
        authorized: bool,
        branch_name: str = "",
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
    ) -> AgentDeliveryReceipt:
        workspace = Path(run.workspace).resolve(strict=True)
        if mode == AgentDeliveryMode.DIFF:
            return AgentDeliveryReceipt(mode, True, str(workspace), applied_files=tuple(run.changed_files))
        if mode == AgentDeliveryMode.WORKTREE:
            return AgentDeliveryReceipt(mode, True, str(workspace), applied_files=tuple(run.changed_files))
        if not authorized:
            return AgentDeliveryReceipt(mode, False, str(workspace), error="explicit authorization required for repository delivery")
        if not run.ok:
            return AgentDeliveryReceipt(mode, False, str(workspace), error="unverified agent run cannot be delivered")

        if mode == AgentDeliveryMode.APPLY:
            if not run.final_diff.strip():
                return AgentDeliveryReceipt(mode, False, str(workspace), error="verified run produced no diff")
            check = subprocess.run(
                ["git", "apply", "--check", "-"],
                cwd=self.project,
                input=run.final_diff,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
            if check.returncode != 0:
                return AgentDeliveryReceipt(mode, False, str(workspace), error=check.stderr.strip() or "git apply --check failed")
            applied = subprocess.run(
                ["git", "apply", "-"],
                cwd=self.project,
                input=run.final_diff,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
            return AgentDeliveryReceipt(
                mode,
                applied.returncode == 0,
                str(workspace),
                applied_files=tuple(run.changed_files),
                error=applied.stderr.strip() if applied.returncode else "",
            )

        branch = self._branch(branch_name or f"syntavra/agent-{run.run_id.rsplit(':', 1)[-1][:12]}")
        switch = self._run(("git", "switch", "-c", branch), cwd=workspace)
        if switch.returncode != 0:
            return AgentDeliveryReceipt(mode, False, str(workspace), branch=branch, error=switch.stderr.strip())
        add = self._run(("git", "add", "-A"), cwd=workspace)
        if add.returncode != 0:
            return AgentDeliveryReceipt(mode, False, str(workspace), branch=branch, error=add.stderr.strip())
        message = commit_message.strip() or f"fix: {run.task.instruction[:72]}"
        commit = self._run(("git", "commit", "-m", message), cwd=workspace)
        if commit.returncode != 0:
            return AgentDeliveryReceipt(mode, False, str(workspace), branch=branch, error=commit.stderr.strip())
        sha_result = self._run(("git", "rev-parse", "HEAD"), cwd=workspace)
        commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""
        if mode == AgentDeliveryMode.COMMIT:
            return AgentDeliveryReceipt(mode, True, str(workspace), branch=branch, commit=commit_sha, applied_files=tuple(run.changed_files))

        if shutil.which("gh") is None:
            return AgentDeliveryReceipt(mode, False, str(workspace), branch=branch, commit=commit_sha, error="gh CLI is required for PR delivery")
        push = self._run(("git", "push", "-u", "origin", branch), cwd=workspace, timeout=300)
        if push.returncode != 0:
            return AgentDeliveryReceipt(mode, False, str(workspace), branch=branch, commit=commit_sha, error=push.stderr.strip())
        title = pr_title.strip() or message
        body = pr_body.strip() or "Created by Syntavra after all discovered verifiers passed."
        created = self._run(("gh", "pr", "create", "--draft", "--title", title, "--body", body, "--head", branch), cwd=workspace, timeout=300)
        url = next((line.strip() for line in created.stdout.splitlines() if line.strip().startswith("http")), "")
        return AgentDeliveryReceipt(
            mode,
            created.returncode == 0 and bool(url),
            str(workspace),
            branch=branch,
            commit=commit_sha,
            pull_request_url=url,
            applied_files=tuple(run.changed_files),
            error=created.stderr.strip() if created.returncode else "",
        )


class AgentRuntime:
    """User-task-to-verified-delivery product surface over the bounded agent core."""

    def __init__(
        self,
        *,
        project: Path,
        state_root: Path,
        graph: Any,
        memory: Any | None = None,
        sandbox: Any | None = None,
    ) -> None:
        self.project = project.resolve(strict=True)
        self.state_root = state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.graph = graph
        self.memory = memory
        self.sandbox = sandbox
        self.project_model = ProjectModel(self.project)
        self.delivery = AgentDeliveryManager(self.project)

    def run(
        self,
        instruction: str,
        gateway: ModelGateway,
        *,
        mode: AgentMode = AgentMode.REVIEW_REQUIRED,
        max_attempts: int = 3,
        timeout_seconds: float = 900.0,
        token_budget: int | None = None,
        cost_budget: float | None = None,
        authorized: bool = False,
        session_id: str | None = None,
        run_post_verifiers: bool = True,
        delivery_mode: AgentDeliveryMode | str = AgentDeliveryMode.DIFF,
        branch_name: str = "",
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
        event_sink: EventSink | None = None,
    ) -> AgentProductReceipt:
        if not instruction.strip():
            raise ValueError("agent instruction cannot be empty")
        delivery_mode = AgentDeliveryMode(delivery_mode)
        journal = AgentEventJournal(event_sink)
        journal.emit("agent-started", instruction=instruction, mode=mode.value, delivery=delivery_mode.value)
        stats = self.graph.stats()
        if int(stats.get("files", 0)) == 0:
            journal.emit("repository-index-started")
            self.graph.index_repository(self.project)
            journal.emit("repository-index-finished", stats=self.graph.stats())
        verifiers = self.project_model.discover_verifiers()
        if not verifiers:
            raise RuntimeError("agent cannot run safely because no project verifier was discovered")
        primary = verifiers[0]
        semantic_results = self.graph.query(instruction, limit=20)
        context_assembler = AgentContextAssembler(self.project, self.graph, self.project_model)
        provider = GatewayPatchProvider(
            gateway,
            project=self.project,
            graph=self.graph,
            project_model=self.project_model,
            sandbox=self.sandbox,
            context_assembler=context_assembler,
            journal=journal,
        )
        agent = AutonomousCodingAgent(
            self.project,
            self.state_root,
            graph=self.graph,
            memory=self.memory,
            sandbox=self.sandbox,
        )
        journal.emit("verification-plan", primary=asdict(primary), post=[asdict(item) for item in verifiers[1:]])
        run = agent.execute(
            AgentTask(
                instruction=instruction,
                verifier=primary.argv,
                mode=mode,
                max_attempts=max_attempts,
                timeout_seconds=timeout_seconds,
                token_budget=token_budget,
                cost_budget=cost_budget,
                retain_workspace=True,
                metadata={
                    "verifier_discovery": [asdict(item) for item in verifiers],
                    "semantic_results": semantic_results,
                },
            ),
            provider,
            session_id=session_id,
            authorized=authorized,
        )
        journal.emit("primary-run-finished", ok=run.ok, state=run.state.value, stop_reason=run.stop_reason)

        post: list[dict[str, Any]] = []
        if run.ok and run_post_verifiers and self.sandbox is not None:
            workspace = Path(run.workspace)
            for verifier in verifiers[1:]:
                journal.emit("post-verifier-started", name=verifier.name)
                receipt: ExecutionReceipt = self.sandbox.run(
                    verifier.argv,
                    policy=SandboxPolicy(workspace=workspace, timeout_seconds=timeout_seconds, strict_native=False),
                )
                row = {
                    "name": verifier.name,
                    "argv": list(verifier.argv),
                    "ok": receipt.ok,
                    "exit_code": receipt.exit_code,
                    "timed_out": receipt.timed_out,
                }
                post.append(row)
                journal.emit("post-verifier-finished", **row)
                if not receipt.ok:
                    break

        post_ok = all(bool(item.get("ok")) for item in post)
        verification_complete = len(verifiers) == 1 or (
            run_post_verifiers
            and self.sandbox is not None
            and len(post) == len(verifiers) - 1
            and post_ok
        )
        non_mutating_delivery = delivery_mode in {AgentDeliveryMode.DIFF, AgentDeliveryMode.WORKTREE}
        if run.ok and post_ok and (verification_complete or non_mutating_delivery):
            delivery = self.delivery.deliver(
                run,
                mode=delivery_mode,
                authorized=authorized,
                branch_name=branch_name,
                commit_message=commit_message,
                pr_title=pr_title,
                pr_body=pr_body,
            )
        else:
            delivery = AgentDeliveryReceipt(delivery_mode, False, run.workspace, error="verification did not pass")
        journal.emit("delivery-finished", mode=delivery.mode.value, ok=delivery.ok, branch=delivery.branch, commit=delivery.commit)

        limitations: list[str] = []
        if len(verifiers) > 1 and not run_post_verifiers:
            limitations.append("post verifiers were explicitly disabled")
        if len(verifiers) > 1 and self.sandbox is None:
            limitations.append("post verifiers were discovered but no sandbox was injected")
        if delivery_mode == AgentDeliveryMode.PR:
            limitations.append("PR delivery requires authenticated git push and gh CLI; live host certification is receipt-gated")
        return AgentProductReceipt(
            run=run,
            provider=provider.provider,
            model=provider.model,
            verifier=primary,
            post_verifiers=tuple(post),
            tool_trace=tuple(provider.trace),
            usage=dict(provider.usage),
            delivery=delivery,
            events=journal.events,
            verification_complete=verification_complete,
            limitations=tuple(limitations),
        )


__all__ = [
    "AgentContextAssembler",
    "AgentDeliveryManager",
    "AgentDeliveryMode",
    "AgentDeliveryReceipt",
    "AgentEvent",
    "AgentEventJournal",
    "AgentProductReceipt",
    "AgentRuntime",
    "GatewayPatchProvider",
    "StructuredEditCompiler",
]
