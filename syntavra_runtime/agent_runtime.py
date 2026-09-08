from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .agent_context_runtime import (
    CompiledAgentContext,
    ConstantContextState,
    ContextBudgetExceeded,
    ProviderTokenCounter,
    compact_value,
)
from .agent_retrieval import QueryPushdownEngine
from .autonomous_agent import AgentMode, AgentRunReceipt, AgentTask, AutonomousCodingAgent, PatchProposal
from .evidence import EvidenceStore
from .execution_sandbox import ExecutionReceipt, SandboxPolicy
from .model_gateway import ModelGateway
from .project_model import ProjectModel, VerifierSpec
from .provider_call_observation import ProviderCallObservationLedger
from .tool_externalization import ToolOutputExternalizer
from .tool_externalization_types import ExternalizationPolicy, ToolPayload
from .util import stable_project_id


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


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
    provider_observation: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return (
            self.run.ok
            and self.verification_complete
            and all(bool(item.get("ok")) for item in self.post_verifiers)
            and self.delivery.ok
        )


class AgentContextAssembler:
    """Build a small initial packet; source bodies are pulled explicitly by range/symbol."""

    INSTRUCTION_FILES = (
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
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

    def __init__(
        self,
        project: Path,
        graph: Any,
        project_model: ProjectModel,
        *,
        max_bytes: int = 20_000,
        per_file_bytes: int = 4096,
    ) -> None:
        self.project = project.resolve(strict=True)
        self.graph = graph
        self.project_model = project_model
        self.max_bytes = max(8192, min(int(max_bytes), 64_000))
        self.per_file_bytes = max(1024, min(int(per_file_bytes), 16_384))

    def _read(self, root: Path, relative: str, remaining: int) -> dict[str, Any] | None:
        path = (root / relative).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError:
            return None
        if not path.is_file() or remaining <= 0:
            return None
        data = path.read_bytes()
        limit = min(remaining, self.per_file_bytes)
        bounded = data[:limit]
        return {
            "path": relative,
            "bytes": len(data),
            "visible_bytes": len(bounded),
            "truncated": len(data) > len(bounded),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": bounded.decode("utf-8", errors="replace"),
        }

    @staticmethod
    def _semantic(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        fields = (
            "node_id", "name", "qualified_name", "path", "kind", "language",
            "start_line", "end_line", "score", "query_backend",
        )
        output: list[dict[str, Any]] = []
        for raw in rows[:12]:
            item = {key: raw[key] for key in fields if key in raw}
            if item:
                output.append(item)
        return output

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
        # Do not auto-open semantic result paths. The model must request exact/ranged
        # source after the structural result identifies what is actually needed.
        candidates = [*self.INSTRUCTION_FILES, *self.PROJECT_FILES]
        for relative in dict.fromkeys(candidates):
            row = self._read(source_root, relative, remaining)
            if row is None:
                continue
            files.append(row)
            remaining -= int(row["visible_bytes"])
            if remaining <= 0:
                break
        return {
            "instruction": instruction,
            "repository": compact_value(self.project_model.describe(), string_limit=1024, list_limit=8),
            "semantic_results": self._semantic(semantic_results),
            "bootstrap_files": files,
            "bootstrap_policy": {
                "semantic_source_bodies_auto_loaded": False,
                "max_visible_bytes": self.max_bytes,
                "per_file_bytes": self.per_file_bytes,
            },
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
    """Model-backed agent loop with constant provider context and exact recovery."""

    SYSTEM_PROMPT = """You are the patch-planning component of Syntavra.
Return exactly one JSON object and no markdown.
Allowed actions:
- {"action":"search","query":"...","limit":8,"fields":["name","path"],"filters":{"kind":"function"}}
- {"action":"search_reduce","query":"...","operator":"count|sum|min|max|group|sort|top_k|sample","field":"score","group_by":"kind","limit":8,"fields":["kind","score"],"filters":{"kind":"function"},"descending":true,"sample_seed":"optional"}
- {"action":"search_inspect","query":"symbol or concept","fields":["name","path","start_line","end_line"],"context_lines":8}
- {"action":"inspect","path":"relative/path.py","start_line":1,"end_line":200}
- {"action":"inspect","paths":["relative/path.py"]}  # legacy bounded form
- {"action":"diff"}
- {"action":"impact","node_id":"..."}
- {"action":"verifiers"}
- {"action":"run_verifier","name":"..."}
- {"action":"edit","edits":[{"path":"...","operation":"replace","old":"...","new":"...","count":1}],"rationale":"..."}
- {"action":"patch","patch":"unified diff","rationale":"..."}
Prefer search_reduce when an aggregate, rank, top-k or deterministic sample answers the evidence need without exposing raw candidates.
A search_reduce result with source_window_complete=false is bounded candidate-window evidence, never global repository truth.
Prefer search_inspect when one structural match is likely: local fusion can return exact ranged source without replaying the intermediate candidate list.
Use search, ranged inspect, impact or a verifier when evidence is insufficient. Never invent file contents.
Repository search is fail-closed: only approved projected fields/filters can become provider-visible evidence.
Lossy repository reduction is fail-closed without exact local capture; raw source windows remain local and only compact results plus recovery handles become provider-visible.
Tool evidence is represented by compact receipts and exact recovery handles; request a fresh/ranged read when more evidence is required.
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
        externalizer: ToolOutputExternalizer | None = None,
        observation_ledger: ProviderCallObservationLedger | None = None,
        arm_id: str = "syntavra",
        repetition: int = 1,
        max_tool_rounds: int = 8,
        max_file_bytes: int = 32_000,
        max_inspect_total_bytes: int = 24_000,
        max_inspect_files: int = 4,
        max_verifier_runs: int = 3,
        provider_token_budget: int = 6_000,
        provider_byte_budget: int = 24_000,
    ) -> None:
        self.gateway = gateway
        self.project = project.resolve(strict=True)
        self.graph = graph
        self.retrieval = QueryPushdownEngine(graph, max_limit=20, default_limit=8)
        self.project_model = project_model
        self.sandbox = sandbox
        self.context_assembler = context_assembler or AgentContextAssembler(self.project, graph, project_model)
        self.journal = journal or AgentEventJournal()
        self.externalizer = externalizer
        self.observation_ledger = observation_ledger
        self.arm_id = str(arm_id)
        self.repetition = max(1, int(repetition))
        self.max_tool_rounds = max(1, min(int(max_tool_rounds), 64))
        self.max_file_bytes = max(4096, min(int(max_file_bytes), 128_000))
        self.max_inspect_total_bytes = max(4096, min(int(max_inspect_total_bytes), 128_000))
        self.max_inspect_files = max(1, min(int(max_inspect_files), 8))
        self.max_verifier_runs = max(0, min(int(max_verifier_runs), 8))
        self.provider_token_budget = max(256, int(provider_token_budget))
        self.provider_byte_budget = max(4096, int(provider_byte_budget))
        config = getattr(gateway, "config", None)
        envelope = getattr(config, "token_envelope", None)
        if envelope is not None:
            self.provider_token_budget = min(self.provider_token_budget, int(envelope.provider_input_budget_tokens))
        self.trace: list[dict[str, Any]] = []
        self.usage: dict[str, int] = {}
        self.context_receipts: list[CompiledAgentContext] = []
        self.provider = type(gateway).__name__
        self.model = getattr(config, "model", getattr(gateway, "model", "unknown"))
        self.counter = ProviderTokenCounter(str(self.model))
        self.edit_compiler = StructuredEditCompiler(max_file_bytes=max(self.max_file_bytes, 1_000_000))
        self._verifier_runs = 0
        self.task_id = ""
        self._state: ConstantContextState | None = None

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

    def _retrieval_options(self, action: Mapping[str, Any]) -> tuple[int, list[str] | None, Mapping[str, Any] | None]:
        raw_fields = action.get("fields")
        if raw_fields is not None and not isinstance(raw_fields, list):
            raise ValueError("repository retrieval fields must be a JSON array")
        fields = [str(item) for item in raw_fields] if raw_fields is not None else None
        raw_filters = action.get("filters")
        if raw_filters is not None and not isinstance(raw_filters, Mapping):
            raise ValueError("repository retrieval filters must be a JSON object")
        filters = dict(raw_filters) if raw_filters is not None else None
        raw_limit = action.get("limit")
        limit = self.retrieval.default_limit if raw_limit is None else int(raw_limit)
        return limit, fields, filters

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

    def _read_one(
        self,
        value: str,
        *,
        root: Path,
        start_line: int | None = None,
        end_line: int | None = None,
        max_bytes: int,
    ) -> dict[str, Any]:
        path = self._safe_path(root, value)
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        if start_line is not None or end_line is not None:
            start = int(start_line or 1)
            end = int(end_line or len(lines))
            if start < 1 or end < start or start > max(1, len(lines)):
                raise ValueError(f"inspect line range is invalid for {value}: {start}-{end}")
            end = min(end, len(lines))
            selected = "".join(lines[start - 1 : end])
            selected_bytes = selected.encode("utf-8")[:max_bytes]
            visible = selected_bytes.decode("utf-8", errors="ignore")
            actual_end = start + max(0, visible.count("\n"))
            return {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(data),
                "sha256": sha,
                "start_line": start,
                "end_line": min(end, actual_end),
                "requested_end_line": end,
                "truncated": len(selected.encode("utf-8")) > len(selected_bytes),
                "content": visible,
            }
        bounded = data[:max_bytes]
        visible = bounded.decode("utf-8", errors="replace")
        return {
            "path": path.relative_to(root).as_posix(),
            "bytes": len(data),
            "sha256": sha,
            "start_line": 1,
            "end_line": max(1, visible.count("\n") + 1),
            "truncated": len(data) > len(bounded),
            "content": visible,
        }

    def _inspect_action(self, action: Mapping[str, Any], *, root: Path) -> list[dict[str, Any]]:
        single = str(action.get("path") or "").strip()
        if single:
            paths = [single]
            start = int(action["start_line"]) if action.get("start_line") is not None else None
            end = int(action["end_line"]) if action.get("end_line") is not None else None
        else:
            raw_paths = action.get("paths") or []
            if not isinstance(raw_paths, list):
                raise ValueError("inspect action requires path or paths")
            paths = [str(item) for item in raw_paths]
            start = end = None
        paths = list(dict.fromkeys(paths))[: self.max_inspect_files]
        if not paths:
            raise ValueError("inspect action requires at least one path")
        remaining = self.max_inspect_total_bytes
        rows: list[dict[str, Any]] = []
        for value in paths:
            if remaining <= 0:
                break
            per_file = min(self.max_file_bytes, remaining)
            row = self._read_one(value, root=root, start_line=start, end_line=end, max_bytes=per_file)
            rows.append(row)
            remaining -= len(str(row["content"]).encode("utf-8"))
        return rows

    def _record_usage(self, values: Mapping[str, int]) -> None:
        for key, value in values.items():
            self.usage[key] = self.usage.get(key, 0) + max(0, int(value))

    def _verifiers(self) -> tuple[VerifierSpec, ...]:
        return self.project_model.discover_verifiers()

    def _run_verifier(self, name: str, workspace: Path, timeout_seconds: float) -> tuple[dict[str, Any], str]:
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
        stdout = str(receipt.stdout or "")
        stderr = str(receipt.stderr or "")
        raw = stdout.rstrip("\n") + ("\n[stderr]\n" + stderr if stderr else "")
        summary: dict[str, Any] = {
            "name": verifier.name,
            "argv": list(verifier.argv),
            "ok": receipt.ok,
            "exit_code": receipt.exit_code,
            "timed_out": receipt.timed_out,
        }
        if not receipt.ok:
            failure = (stderr or stdout)[-4096:]
            summary["failure_excerpt"] = failure
        return summary, raw

    def _ingest(self, *, key: str, tool: str, payload: Any, round_number: int, metadata: Mapping[str, Any] | None = None, path: str = "", command: str = "") -> None:
        if self._state is None:
            raise RuntimeError("agent context state is not initialized")
        raw = _canonical(payload)
        receipt = self._state.ingest(
            key=key,
            tool=tool,
            raw=raw,
            round_number=round_number,
            command=command,
            path=path,
            metadata=metadata,
        )
        self.journal.emit(
            "tool-evidence-ingested",
            tool=tool,
            key=key,
            status=receipt.status,
            raw_bytes=receipt.original_bytes,
            visible_bytes=receipt.visible_bytes,
            artifact=receipt.artifact_id,
            exact=receipt.exact_handle,
        )

    def _compile_messages(self, base: Mapping[str, Any]) -> tuple[list[dict[str, str]], CompiledAgentContext, int, str]:
        if self._state is None:
            raise RuntimeError("agent context state is not initialized")
        empty_system = self.counter.count_messages([], system=self.SYSTEM_PROMPT)
        available = self.provider_token_budget - empty_system.tokens
        if available < 128:
            raise ContextBudgetExceeded(
                f"system prompt leaves insufficient provider budget: {available} tokens"
            )
        compiled = self._state.compile(
            base,
            counter=self.counter,
            token_budget=available,
            byte_budget=max(1024, self.provider_byte_budget - len(self.SYSTEM_PROMPT.encode("utf-8"))),
        )
        messages = [{"role": "user", "content": compiled.text}]
        total = self.counter.count_messages(messages, system=self.SYSTEM_PROMPT)
        if total.tokens > self.provider_token_budget:
            raise ContextBudgetExceeded(
                f"compiled provider packet exceeds total token budget: {total.tokens}>{self.provider_token_budget}"
            )
        prepare = getattr(self.gateway, "prepare_input_tokens", None)
        if callable(prepare):
            prepare(total.tokens)
        self.context_receipts.append(compiled)
        return messages, compiled, total.tokens, total.method

    def _call_model(self, base: Mapping[str, Any], *, round_number: int) -> Any:
        messages, compiled, prepared_tokens, counting_method = self._compile_messages(base)
        self.journal.emit(
            "model-requested",
            round=round_number,
            prepared_input_tokens=prepared_tokens,
            context_bytes=compiled.visible_bytes,
            active_evidence=compiled.active_evidence,
            causal_receipts=compiled.causal_receipts,
            previews_dropped=compiled.previews_dropped,
            counting_method=counting_method,
        )
        started = time.monotonic()
        result = self.gateway.complete(messages, system=self.SYSTEM_PROMPT)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self.provider = result.provider
        self.model = result.model
        self._record_usage(result.usage)
        if self.observation_ledger is not None:
            response = result.raw if result.raw else {
                "text": result.text,
                "finish_reason": result.finish_reason,
                "provider": result.provider,
                "model": result.model,
            }
            self.observation_ledger.record_call(
                task_id=self.task_id,
                arm_id=self.arm_id,
                repetition=self.repetition,
                round_number=round_number,
                provider=result.provider,
                model=result.model,
                usage={"usage": dict(result.usage)},
                response_id=result.response_id,
                response=response,
                locally_counted_input_tokens=prepared_tokens,
                counting_method=counting_method,
                context_hash=compiled.digest,
                elapsed_ms=elapsed_ms,
            )
        return result

    def propose(self, task: AgentTask, context: Mapping[str, Any], previous_failure: Mapping[str, Any] | None) -> PatchProposal:
        usage_before = dict(self.usage)
        workspace = Path(str(context.get("workspace") or self.project)).resolve(strict=True)
        semantic_results = list(context.get("semantic_results") or ())[:20]
        base: dict[str, Any] = self.context_assembler.assemble(task.instruction, semantic_results, root=workspace)
        base.update(
            {
                "mode": task.mode.value,
                "attempt": context.get("attempt"),
                "changed_files": list(context.get("changed_files") or ()),
                "provider_context_mode": "constant-active-evidence",
            }
        )
        task_identity = _canonical(
            {
                "project": stable_project_id(self.project),
                "instruction": task.instruction,
                "verifier": list(task.verifier),
            }
        )
        self.task_id = "agent-" + hashlib.sha256(task_identity.encode("utf-8")).hexdigest()[:32]
        scope = f"{self.task_id}:{context.get('attempt', 1)}"
        self._state = ConstantContextState(externalizer=self.externalizer, scope_key=scope)

        current_diff = str(context.get("current_diff") or "")
        reduction_invalidation_fingerprint = hashlib.sha256(
            _canonical(
                {
                    "project": stable_project_id(self.project),
                    "task_id": self.task_id,
                    "attempt": int(context.get("attempt") or 1),
                    "current_diff_sha256": hashlib.sha256(current_diff.encode("utf-8")).hexdigest(),
                    "changed_files": list(context.get("changed_files") or ()),
                }
            ).encode("utf-8")
        ).hexdigest()
        if current_diff:
            self._ingest(
                key="repo.diff:current",
                tool="repo.diff",
                payload={"diff": current_diff},
                round_number=0,
                metadata={"changed_files": list(context.get("changed_files") or ())},
            )
        if previous_failure:
            self._ingest(
                key="agent.previous_failure",
                tool="agent.failure",
                payload=previous_failure,
                round_number=0,
                metadata={"mandatory_failure_evidence": True},
            )

        self.journal.emit("model-loop-started", attempt=context.get("attempt"), workspace=str(workspace), task_id=self.task_id)

        for round_number in range(1, self.max_tool_rounds + 1):
            result = self._call_model(base, round_number=round_number)
            action = self._action(result.text)
            name = str(action.get("action")).casefold()
            trace: dict[str, Any] = {"round": round_number, "action": name}
            self.journal.emit("model-action", round=round_number, action=name)

            if name == "search":
                query = str(action.get("query") or task.instruction)
                limit, fields, filters = self._retrieval_options(action)
                retrieval = self.retrieval.search(query, limit=limit, fields=fields, filters=filters)
                rows = list(retrieval.rows)
                trace.update(
                    query=retrieval.query,
                    results=len(rows),
                    raw_candidates=retrieval.raw_count,
                    filtered_candidates=retrieval.filtered_count,
                    projected_fields=list(retrieval.fields),
                    query_pushdown=True,
                )
                self.trace.append(trace)
                self._ingest(
                    key="repo.search:" + _short_hash(retrieval.query),
                    tool="repo.search",
                    payload={"query": retrieval.query, "results": rows},
                    round_number=round_number,
                    metadata={
                        "query": retrieval.query,
                        "result_count": len(rows),
                        "raw_candidate_count": retrieval.raw_count,
                        "filtered_candidate_count": retrieval.filtered_count,
                        "projected_fields": list(retrieval.fields),
                        "query_pushdown": True,
                    },
                    command=retrieval.query,
                )
                continue

            if name == "search_reduce":
                if self.externalizer is None:
                    raise RuntimeError("search_reduce requires an exact externalizer")
                query = str(action.get("query") or task.instruction)
                operator = str(action.get("operator") or "").casefold()
                limit, fields, filters = self._retrieval_options(action)
                raw_descending = action.get("descending")
                if raw_descending is not None and not isinstance(raw_descending, bool):
                    raise ValueError("repository reduction descending must be a JSON boolean")
                field = str(action.get("field") or "")
                group_by = str(action.get("group_by") or "")
                sample_seed = str(action.get("sample_seed") or "")[:128]

                def capture(raw: bytes, metadata: Mapping[str, Any]) -> Mapping[str, str]:
                    artifact = self.externalizer.externalize(
                        ToolPayload(
                            command=f"search_reduce {operator} {query}",
                            stdout=raw,
                            tool_name="repo.search_reduce.source",
                            scope_key=scope,
                            metadata={
                                **dict(metadata),
                                "invalidation_fingerprint": reduction_invalidation_fingerprint,
                                "lossy_reduction_source": True,
                                "provider_visible": False,
                            },
                        )
                    )
                    try:
                        exact_ok = bool(self.externalizer.evidence.verify(artifact.exact_handle))
                    except Exception:
                        exact_ok = False
                    if not exact_ok:
                        raise RuntimeError("search_reduce exact capture verification failed")
                    return {"artifact_id": artifact.artifact_id, "exact_handle": artifact.exact_handle}

                reduction = self.retrieval.reduce(
                    query,
                    operator=operator,
                    capture=capture,
                    limit=limit,
                    fields=fields,
                    filters=filters,
                    field=field,
                    group_by=group_by,
                    descending=raw_descending,
                    sample_seed=sample_seed,
                )
                trace.update(
                    query=reduction.query,
                    operator=reduction.operator,
                    raw_candidates=reduction.raw_count,
                    filtered_candidates=reduction.filtered_count,
                    source_window_complete=reduction.source_window_complete,
                    source_window_limit=reduction.source_window_limit,
                    projected_fields=list(reduction.fields),
                    source_artifact=reduction.exact_artifact_id,
                    source_exact=reduction.exact_handle,
                    exact_before_lossy=True,
                    query_pushdown=True,
                )
                self.trace.append(trace)
                reduction_key = _short_hash(
                    _canonical(
                        {
                            "query": reduction.query,
                            "operator": reduction.operator,
                            "field": reduction.field,
                            "group_by": reduction.group_by,
                            "filters": filters or {},
                        }
                    )
                )
                self._ingest(
                    key="repo.search_reduce:" + reduction_key,
                    tool="repo.search_reduce",
                    payload={
                        "query": reduction.query,
                        "operator": reduction.operator,
                        "value": reduction.value,
                        "raw_candidate_count": reduction.raw_count,
                        "filtered_candidate_count": reduction.filtered_count,
                        "source_window_complete": reduction.source_window_complete,
                        "source_window_limit": reduction.source_window_limit,
                        "source_artifact": reduction.exact_artifact_id,
                        "source_exact": reduction.exact_handle,
                    },
                    round_number=round_number,
                    metadata={
                        "query": reduction.query,
                        "operator": reduction.operator,
                        "raw_candidate_count": reduction.raw_count,
                        "filtered_candidate_count": reduction.filtered_count,
                        "source_window_complete": reduction.source_window_complete,
                        "source_window_limit": reduction.source_window_limit,
                        "projected_fields": list(reduction.fields),
                        "source_artifact": reduction.exact_artifact_id,
                        "source_exact": reduction.exact_handle,
                        "exact_before_lossy": True,
                        "query_pushdown": True,
                    },
                    command=reduction.query,
                )
                continue

            if name == "search_inspect":
                query = str(action.get("query") or task.instruction)
                limit, fields, filters = self._retrieval_options(action)
                requested_max_bytes = int(action.get("max_bytes") or 12_000)
                max_bytes = max(
                    1024,
                    min(requested_max_bytes, self.max_file_bytes, self.max_inspect_total_bytes, 32_000),
                )
                fused = self.retrieval.search_inspect(
                    query,
                    reader=lambda path, **kwargs: self._read_one(path, root=workspace, **kwargs),
                    limit=limit,
                    fields=fields,
                    filters=filters,
                    context_lines=int(action.get("context_lines") or 8),
                    max_bytes=max_bytes,
                )
                trace.update(
                    query=fused.query,
                    status=fused.status,
                    raw_candidates=fused.raw_count,
                    fused=fused.status == "FUSED_EXACT",
                    intermediate_rows_visible=fused.intermediate_rows_visible,
                    selected_identity=fused.selected_identity,
                )
                self.trace.append(trace)
                if fused.source is not None:
                    row = fused.source
                    key = f"repo.read:{row['path']}:{row['start_line']}:{row['end_line']}"
                    self._ingest(
                        key=key,
                        tool="repo.search_inspect",
                        payload={"query": fused.query, "status": fused.status, "source": row},
                        round_number=round_number,
                        metadata={
                            "query": fused.query,
                            "status": fused.status,
                            "path": row["path"],
                            "start_line": row["start_line"],
                            "end_line": row["end_line"],
                            "file_sha256": row["sha256"],
                            "truncated": row["truncated"],
                            "raw_candidate_count": fused.raw_count,
                            "fused": True,
                            "intermediate_rows_visible": False,
                        },
                        path=str(row["path"]),
                        command=fused.query,
                    )
                else:
                    candidates = list(fused.candidates)
                    self._ingest(
                        key="repo.search_inspect:" + _short_hash(fused.query),
                        tool="repo.search_inspect",
                        payload={"query": fused.query, "status": fused.status, "candidates": candidates},
                        round_number=round_number,
                        metadata={
                            "query": fused.query,
                            "status": fused.status,
                            "result_count": len(candidates),
                            "raw_candidate_count": fused.raw_count,
                            "fused": False,
                            "intermediate_rows_visible": True,
                        },
                        command=fused.query,
                    )
                continue

            if name == "inspect":
                rows = self._inspect_action(action, root=workspace)
                trace["paths"] = [row["path"] for row in rows]
                trace["visible_bytes"] = sum(len(str(row["content"]).encode("utf-8")) for row in rows)
                self.trace.append(trace)
                for row in rows:
                    key = f"repo.read:{row['path']}:{row['start_line']}:{row['end_line']}"
                    self._ingest(
                        key=key,
                        tool="repo.read",
                        payload=row,
                        round_number=round_number,
                        metadata={
                            "path": row["path"],
                            "start_line": row["start_line"],
                            "end_line": row["end_line"],
                            "file_sha256": row["sha256"],
                            "truncated": row["truncated"],
                        },
                        path=str(row["path"]),
                    )
                continue

            if name == "diff":
                diff = str(context.get("current_diff") or "")
                trace["bytes"] = len(diff.encode("utf-8"))
                self.trace.append(trace)
                self._ingest(
                    key="repo.diff:current",
                    tool="repo.diff",
                    payload={"diff": diff, "changed_files": list(context.get("changed_files") or ())},
                    round_number=round_number,
                    metadata={"changed_files": list(context.get("changed_files") or ())},
                )
                continue

            if name == "impact":
                node_id = str(action.get("node_id") or "")
                if not node_id:
                    raise ValueError("impact action requires node_id")
                result_value = self.graph.impact(node_id, max_depth=6)
                trace["node_id"] = node_id
                self.trace.append(trace)
                self._ingest(
                    key="repo.impact:" + node_id,
                    tool="repo.impact",
                    payload=result_value,
                    round_number=round_number,
                    metadata={"node_id": node_id},
                )
                continue

            if name == "verifiers":
                rows = [asdict(item) for item in self._verifiers()]
                trace["count"] = len(rows)
                self.trace.append(trace)
                self._ingest(
                    key="test.discover",
                    tool="test.discover",
                    payload={"verifiers": rows},
                    round_number=round_number,
                    metadata={"count": len(rows)},
                )
                continue

            if name == "run_verifier":
                verifier_name = str(action.get("name") or "")
                verifier_summary, raw_log = self._run_verifier(verifier_name, workspace, task.timeout_seconds)
                trace.update(name=verifier_summary["name"], ok=verifier_summary["ok"])
                self.trace.append(trace)
                self._ingest(
                    key="test.run:" + verifier_name,
                    tool="test.run",
                    payload={"summary": verifier_summary, "log": raw_log},
                    round_number=round_number,
                    metadata={**verifier_summary, "mandatory_failure_evidence": not bool(verifier_summary["ok"])},
                    command=" ".join(str(item) for item in verifier_summary["argv"]),
                )
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
        context_stats = {
            "turns": len(self.context_receipts),
            "max_visible_bytes": max((item.visible_bytes for item in self.context_receipts), default=0),
            "max_compiled_tokens": max((item.tokens for item in self.context_receipts), default=0),
            "raw_history_replayed": False,
        }
        observation = (
            self.observation_ledger.summary(task_id=self.task_id, arm_id=self.arm_id)
            if self.observation_ledger is not None and self.task_id
            else {}
        )
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
            metadata={
                "provider": result.provider,
                "model": result.model,
                "tool_trace": list(self.trace),
                "context": context_stats,
                "provider_observation": observation,
            },
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
        token_state = self.state_root / "agent-token-economy"
        token_state.mkdir(parents=True, exist_ok=True)
        self.evidence = EvidenceStore(token_state / "evidence", project_id=stable_project_id(self.project))
        self.externalizer = ToolOutputExternalizer(
            token_state / "tool-externalization.sqlite3",
            evidence=self.evidence,
            policy=ExternalizationPolicy.for_profile("compact"),
        )
        self.provider_observations = ProviderCallObservationLedger(token_state / "provider-observations.sqlite3")

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
        bootstrap_retrieval = QueryPushdownEngine(self.graph, max_limit=20, default_limit=12)
        semantic_results = list(bootstrap_retrieval.search(instruction, limit=12).rows)
        context_assembler = AgentContextAssembler(self.project, self.graph, self.project_model)
        provider = GatewayPatchProvider(
            gateway,
            project=self.project,
            graph=self.graph,
            project_model=self.project_model,
            sandbox=self.sandbox,
            context_assembler=context_assembler,
            journal=journal,
            externalizer=self.externalizer,
            observation_ledger=self.provider_observations,
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

        if provider.task_id:
            self.provider_observations.record_outcome(
                task_id=provider.task_id,
                arm_id=provider.arm_id,
                repetition=provider.repetition,
                verifier_ok=bool(run.ok and post_ok),
                delivery_ok=bool(delivery.ok),
                verification_complete=bool(verification_complete),
            )
            provider_observation = self.provider_observations.summary(task_id=provider.task_id, arm_id=provider.arm_id)
        else:
            provider_observation = {}
        if provider_observation and not provider_observation.get("provider_proof_complete"):
            limitations.append("provider call telemetry exists but complete provider-observed usage proof is not yet available")

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
            provider_observation=provider_observation,
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
