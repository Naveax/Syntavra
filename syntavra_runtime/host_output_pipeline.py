from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol

from .security_scan import scan_text
from .tool_externalization import ToolOutputExternalizer
from .tool_externalization_types import ExternalizedArtifact, ToolPayload
from .usage_receipt_ledger import LedgerEntry, UsageReceiptLedger


class SessionLike(Protocol):
    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> Any: ...


_TEXT_KEYS = {
    "stdout", "stderr", "output", "content", "text", "body", "log", "logs", "diff", "patch",
    "trace", "traceback", "message", "data",
}
_SKIP_TOOL_PREFIXES = (
    "syntavra.output.",
    "syntavra.usage.",
    "syntavra.evidence.",
)


class OutputInterceptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapturedField:
    path: str
    artifact_id: str
    family: str
    mode: str
    original_bytes: int
    visible_bytes: int
    exact_handle: str
    merkle_root: str
    injection_risk: bool
    quality_gate_passed: bool


@dataclass(frozen=True)
class PipelineResult:
    mode: str
    result: Any
    captures: tuple[CapturedField, ...]
    usage_receipt_hash: str | None
    usage_chain_hash: str | None
    blocked: bool
    error: str | None = None


class HostOutputPipeline:
    """Mandatory exact-first interception for host, hook and MCP tool outputs."""

    def __init__(
        self,
        externalizer: ToolOutputExternalizer,
        *,
        usage_ledger: UsageReceiptLedger | None = None,
        sessions: SessionLike | None = None,
        fail_closed_threshold_bytes: int = 4096,
        capture_threshold_bytes: int = 256,
    ):
        self.externalizer = externalizer
        self.usage_ledger = usage_ledger
        self.sessions = sessions
        self.fail_closed_threshold_bytes = max(1024, int(fail_closed_threshold_bytes))
        self.capture_threshold_bytes = max(0, int(capture_threshold_bytes))

    @staticmethod
    def _field_path(parent: str, key: str | int) -> str:
        return f"{parent}.{key}" if parent else str(key)

    @staticmethod
    def _artifact_field(path: str, artifact: ExternalizedArtifact) -> CapturedField:
        return CapturedField(
            path=path,
            artifact_id=artifact.artifact_id,
            family=artifact.family,
            mode=artifact.mode,
            original_bytes=artifact.original_bytes,
            visible_bytes=artifact.visible_bytes,
            exact_handle=artifact.exact_handle,
            merkle_root=artifact.merkle_root,
            injection_risk=artifact.injection_risk,
            quality_gate_passed=artifact.quality_gate_passed,
        )

    @staticmethod
    def _metadata(payload: Mapping[str, Any], field_path: str) -> dict[str, Any]:
        ignored = {"result", "stdout", "stderr", "output", "content", "text", "body", "provider_response", "response", "usage", "provider_usage"}
        metadata = {
            str(key): value
            for key, value in payload.items()
            if key not in ignored and isinstance(value, (str, int, float, bool, type(None)))
        }
        metadata["host_field_path"] = field_path
        return metadata

    def _capture_value(
        self,
        value: str | bytes,
        *,
        payload: Mapping[str, Any],
        field_path: str,
        command: str,
        tool_name: str,
        file_path: str,
        scope_key: str,
    ) -> tuple[str, CapturedField | None]:
        raw = value if isinstance(value, bytes) else value.encode("utf-8")
        if not raw:
            return "", None
        if len(raw) < self.capture_threshold_bytes and field_path not in {"result", "stdout", "stderr", "output", "content", "text"}:
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value, None
        try:
            artifact = self.externalizer.externalize(ToolPayload(
                command=command,
                stdout=value,
                tool_name=tool_name,
                path=file_path,
                scope_key=scope_key,
                metadata=self._metadata(payload, field_path),
            ))
        except Exception as exc:
            if len(raw) >= self.fail_closed_threshold_bytes:
                raise OutputInterceptionError(f"large output could not be externalized at {field_path}: {type(exc).__name__}: {exc}") from exc
            text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
            return scan_text(text).redacted_text, None
        return artifact.preview, self._artifact_field(field_path, artifact)

    def _walk(
        self,
        value: Any,
        *,
        payload: Mapping[str, Any],
        field_path: str,
        command: str,
        tool_name: str,
        file_path: str,
        scope_key: str,
        depth: int = 0,
    ) -> tuple[Any, list[CapturedField]]:
        if depth > 8:
            return value, []
        if isinstance(value, (str, bytes)):
            rendered, capture = self._capture_value(
                value,
                payload=payload,
                field_path=field_path or "result",
                command=command,
                tool_name=tool_name,
                file_path=file_path,
                scope_key=scope_key,
            )
            return rendered, [capture] if capture else []
        if isinstance(value, Mapping):
            output: dict[str, Any] = {}
            captures: list[CapturedField] = []
            for key, item in value.items():
                name = str(key)
                child_path = self._field_path(field_path, name)
                should_descend = (
                    name.casefold() in _TEXT_KEYS
                    or isinstance(item, (dict, list, tuple))
                    or (isinstance(item, (str, bytes)) and len(item if isinstance(item, bytes) else item.encode("utf-8")) >= self.capture_threshold_bytes)
                )
                if should_descend:
                    transformed, nested = self._walk(
                        item, payload=payload, field_path=child_path, command=command,
                        tool_name=tool_name, file_path=file_path, scope_key=scope_key, depth=depth + 1,
                    )
                    output[name] = transformed
                    captures.extend(nested)
                else:
                    output[name] = item
            return output, captures
        if isinstance(value, (list, tuple)):
            output: list[Any] = []
            captures: list[CapturedField] = []
            for index, item in enumerate(value):
                transformed, nested = self._walk(
                    item, payload=payload, field_path=self._field_path(field_path, index), command=command,
                    tool_name=tool_name, file_path=file_path, scope_key=scope_key, depth=depth + 1,
                )
                output.append(transformed)
                captures.extend(nested)
            return output, captures
        return value, []

    def _append_session_event(self, session_id: str, payload: dict[str, Any]) -> None:
        if self.sessions is None or not session_id:
            return
        try:
            self.sessions.append(session_id, "host-output-externalized", payload)
            return
        except KeyError:
            create = getattr(self.sessions, "create_session", None)
            if callable(create):
                try:
                    create(session_id=session_id, metadata={"source": "host-output-pipeline"})
                    self.sessions.append(session_id, "host-output-externalized", payload)
                    return
                except Exception:
                    return
        except Exception:
            return

    def capture(self, payload: Mapping[str, Any], *, result: Any | None = None, tool_name: str | None = None) -> PipelineResult:
        active_tool = str(tool_name or payload.get("tool") or payload.get("name") or "tool")
        if active_tool.startswith(_SKIP_TOOL_PREFIXES):
            return PipelineResult("pass-through", result, tuple(), None, None, False)
        source = result if result is not None else payload.get("result")
        command_value = payload.get("command") or payload.get("argv") or ""
        if isinstance(command_value, (list, tuple)):
            command = " ".join(str(item) for item in command_value)
        else:
            command = str(command_value)
        file_path = str(payload.get("path") or payload.get("file") or "")
        session_id = str(payload.get("session_id") or "")
        scope_key = str(payload.get("scope_key") or session_id or f"host:{active_tool}")

        try:
            transformed, captures = self._walk(
                source,
                payload=payload,
                field_path="result",
                command=command,
                tool_name=active_tool,
                file_path=file_path,
                scope_key=scope_key,
            )
        except OutputInterceptionError as exc:
            return PipelineResult("blocked", None, tuple(), None, None, True, str(exc))

        ledger_entry: LedgerEntry | None = None
        if self.usage_ledger is not None:
            ledger_entry = self.usage_ledger.record_from_payload(payload)

        self._append_session_event(session_id, {
            "tool_name": active_tool,
            "command": command[:1000],
            "path": file_path,
            "scope_key": scope_key,
            "captures": [asdict(item) for item in captures],
            "usage_receipt_hash": ledger_entry.receipt.receipt_hash if ledger_entry else None,
            "usage_chain_hash": ledger_entry.chain_hash if ledger_entry else None,
        })

        mode = "externalized" if captures else "pass-through"
        return PipelineResult(
            mode,
            transformed,
            tuple(captures),
            ledger_entry.receipt.receipt_hash if ledger_entry else None,
            ledger_entry.chain_hash if ledger_entry else None,
            False,
        )

    def capture_hook_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = payload.get("result")
        captured = self.capture(payload, result=result)
        if captured.blocked:
            return {"mode": "blocked", "blocked": True, "reason": captured.error}
        return {
            "mode": captured.mode,
            "result": captured.result,
            "captures": [asdict(item) for item in captured.captures],
            "usage_receipt_hash": captured.usage_receipt_hash,
            "usage_chain_hash": captured.usage_chain_hash,
        }

    def capture_mcp_result(self, tool_name: str, arguments: Mapping[str, Any], result: Any) -> Any:
        payload = {**dict(arguments), "tool": tool_name, "result": result}
        captured = self.capture(payload, result=result, tool_name=tool_name)
        if captured.blocked:
            raise OutputInterceptionError(captured.error or "MCP output interception failed")
        if not captured.captures and not captured.usage_receipt_hash:
            return result
        return {
            "syntavra_output_mode": captured.mode,
            "result": captured.result,
            "captures": [asdict(item) for item in captured.captures],
            "usage_receipt_hash": captured.usage_receipt_hash,
            "usage_chain_hash": captured.usage_chain_hash,
        }

    @staticmethod
    def describe(result: PipelineResult) -> str:
        return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, default=str)
