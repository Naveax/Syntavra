from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .tool_externalization import ToolOutputExternalizer
from .tool_externalization_types import ToolPayload


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _bounded_text(value: str, limit: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    if limit <= 64:
        return raw[:limit].decode("utf-8", errors="ignore")
    head = max(32, limit * 3 // 4)
    tail = max(16, limit - head - 32)
    return (
        raw[:head].decode("utf-8", errors="ignore")
        + "\n...[bounded]...\n"
        + raw[-tail:].decode("utf-8", errors="ignore")
    )


def compact_value(value: Any, *, string_limit: int = 2048, list_limit: int = 12, depth: int = 0) -> Any:
    """Deterministically bound optional model-visible metadata.

    This helper is not allowed to decide that user/security/exact evidence is
    expendable. Callers must restore mandatory fields exactly or fail the final
    provider budget instead of silently truncating them.
    """
    if depth >= 5:
        return "[depth-bounded]"
    if isinstance(value, str):
        return _bounded_text(value, string_limit)
    if isinstance(value, bytes):
        return _bounded_text(value.decode("utf-8", errors="replace"), string_limit)
    if isinstance(value, Mapping):
        return {
            str(key): compact_value(child, string_limit=string_limit, list_limit=list_limit, depth=depth + 1)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        rows = list(value)
        selected = rows[:list_limit]
        output = [compact_value(child, string_limit=string_limit, list_limit=list_limit, depth=depth + 1) for child in selected]
        if len(rows) > len(selected):
            output.append({"omitted_items": len(rows) - len(selected)})
        return output
    return value


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    method: str
    target_model: str
    exact_for_target: bool


class ProviderTokenCounter:
    """Best available local target-tokenizer counter with explicit precision labels."""

    def __init__(self, model: str = "") -> None:
        self.model = str(model or "")
        self._encoder: Any | None = None
        self._method = "LOCALLY_ESTIMATED:utf8-bytes-div-4"
        self._exact = False
        try:
            import tiktoken  # type: ignore

            if self.model:
                try:
                    self._encoder = tiktoken.encoding_for_model(self.model)
                    self._method = f"LOCALLY_TOKENIZED:encoding_for_model:{self.model}"
                    self._exact = True
                except KeyError:
                    self._encoder = tiktoken.get_encoding("o200k_base")
                    self._method = "LOCALLY_TOKENIZED:o200k_base-fallback"
            else:
                self._encoder = tiktoken.get_encoding("o200k_base")
                self._method = "LOCALLY_TOKENIZED:o200k_base-fallback"
        except (ImportError, KeyError):
            self._encoder = None

    def count_text(self, text: str) -> TokenCount:
        if self._encoder is not None:
            tokens = len(self._encoder.encode(text))
        else:
            tokens = max(1, (len(text.encode("utf-8")) + 3) // 4) if text else 0
        return TokenCount(tokens, self._method, self.model, self._exact)

    def count_messages(self, messages: Sequence[Mapping[str, str]], *, system: str = "") -> TokenCount:
        payload = {"system": system, "messages": list(messages)}
        return self.count_text(_canonical_json(payload))


@dataclass(frozen=True)
class ToolEvidenceReceipt:
    key: str
    tool: str
    status: str
    content_hash: str
    round_number: int
    original_bytes: int
    visible_bytes: int
    artifact_id: str = ""
    exact_handle: str = ""
    preview: str = ""
    repeated: bool = False
    baseline_artifact_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def provider_view(self, *, include_preview: bool = True) -> dict[str, Any]:
        row: dict[str, Any] = {
            "key": self.key,
            "tool": self.tool,
            "status": self.status,
            "sha256": self.content_hash,
            "round": self.round_number,
            "bytes": self.original_bytes,
        }
        if self.artifact_id:
            row["artifact"] = self.artifact_id
        if self.exact_handle:
            row["exact"] = self.exact_handle
        if self.baseline_artifact_id:
            row["baseline"] = self.baseline_artifact_id
        if self.repeated:
            row["repeated"] = True
        if include_preview and self.preview:
            row["evidence"] = self.preview
        if self.metadata:
            row["meta"] = compact_value(self.metadata, string_limit=512, list_limit=8)
        return row


@dataclass(frozen=True)
class CompiledAgentContext:
    text: str
    tokens: int
    visible_bytes: int
    counting_method: str
    exact_target_tokenizer: bool
    digest: str
    active_evidence: int
    causal_receipts: int
    previews_dropped: int


class ContextBudgetExceeded(RuntimeError):
    pass


class ConstantContextState:
    """Active-evidence state that prevents raw tool-history growth across rounds."""

    MANDATORY_BASE_KEYS = ("instruction", "user_instruction", "security_policy")

    def __init__(
        self,
        *,
        externalizer: ToolOutputExternalizer | None = None,
        scope_key: str = "agent",
        max_active: int = 8,
        max_causal: int = 16,
        preview_limit_bytes: int = 4096,
    ) -> None:
        self.externalizer = externalizer
        self.scope_key = str(scope_key)
        self.max_active = max(1, int(max_active))
        self.max_causal = max(1, int(max_causal))
        self.preview_limit_bytes = max(256, int(preview_limit_bytes))
        self._active: dict[str, ToolEvidenceReceipt] = {}
        self._active_order: deque[str] = deque()
        self._causal: deque[dict[str, Any]] = deque(maxlen=self.max_causal)

    @property
    def active(self) -> tuple[ToolEvidenceReceipt, ...]:
        return tuple(self._active[key] for key in self._active_order if key in self._active)

    @property
    def causal(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._causal)

    def _touch(self, key: str) -> None:
        try:
            self._active_order.remove(key)
        except ValueError:
            pass
        self._active_order.append(key)
        while len(self._active_order) > self.max_active:
            evicted = self._active_order.popleft()
            old = self._active.pop(evicted, None)
            if old is not None:
                self._causal.append(
                    {
                        "key": old.key,
                        "tool": old.tool,
                        "status": "EVICTED_TO_HANDLE",
                        "sha256": old.content_hash,
                        "artifact": old.artifact_id,
                        "exact": old.exact_handle,
                        "round": old.round_number,
                    }
                )

    def ingest(
        self,
        *,
        key: str,
        tool: str,
        raw: str | bytes,
        round_number: int,
        command: str = "",
        path: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolEvidenceReceipt:
        raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        digest = _sha256_bytes(raw_bytes)
        previous = self._active.get(key)
        if previous is not None and previous.content_hash == digest:
            receipt = ToolEvidenceReceipt(
                key=key,
                tool=tool,
                status="UNCHANGED",
                content_hash=digest,
                round_number=int(round_number),
                original_bytes=len(raw_bytes),
                visible_bytes=0,
                artifact_id=previous.artifact_id,
                exact_handle=previous.exact_handle,
                preview="",
                repeated=True,
                baseline_artifact_id=previous.baseline_artifact_id,
                metadata=dict(metadata or {}),
            )
            self._causal.append(receipt.provider_view(include_preview=False))
            self._touch(key)
            return receipt

        if previous is not None:
            self._causal.append(
                {
                    "key": previous.key,
                    "tool": previous.tool,
                    "status": "SUPERSEDED",
                    "sha256": previous.content_hash,
                    "artifact": previous.artifact_id,
                    "exact": previous.exact_handle,
                    "round": previous.round_number,
                }
            )

        artifact_id = ""
        exact_handle = ""
        baseline = ""
        repeated = False
        status = "BOUNDED_PREVIEW"
        preview: str
        if self.externalizer is not None:
            artifact = self.externalizer.externalize(
                ToolPayload(
                    command=command,
                    stdout=raw_bytes,
                    tool_name=tool,
                    path=path,
                    scope_key=self.scope_key,
                    metadata=dict(metadata or {}),
                )
            )
            if not artifact.quality_gate_passed:
                raise ContextBudgetExceeded(f"externalized evidence failed quality gate: {tool}:{key}")
            artifact_id = artifact.artifact_id
            exact_handle = artifact.exact_handle
            baseline = str(artifact.baseline_artifact_id or "")
            repeated = bool(artifact.repeated)
            status = str(artifact.mode).upper().replace("-", "_")
            preview = _bounded_text(str(artifact.preview), self.preview_limit_bytes)
        else:
            preview = _bounded_text(raw_bytes.decode("utf-8", errors="replace"), self.preview_limit_bytes)
            artifact_id = "volatile-" + digest[:20]

        receipt = ToolEvidenceReceipt(
            key=key,
            tool=tool,
            status=status,
            content_hash=digest,
            round_number=int(round_number),
            original_bytes=len(raw_bytes),
            visible_bytes=len(preview.encode("utf-8")),
            artifact_id=artifact_id,
            exact_handle=exact_handle,
            preview=preview,
            repeated=repeated,
            baseline_artifact_id=baseline,
            metadata=dict(metadata or {}),
        )
        self._active[key] = receipt
        self._touch(key)
        return receipt

    def _base_view(self, base: Mapping[str, Any]) -> Mapping[str, Any]:
        view = compact_value(base, string_limit=4096, list_limit=12)
        if not isinstance(view, Mapping):
            raise TypeError("agent base context must remain an object")
        output = dict(view)
        for key in self.MANDATORY_BASE_KEYS:
            if key in base:
                value = base[key]
                if not isinstance(value, str):
                    raise TypeError(f"mandatory context field must be text: {key}")
                output[key] = value
        return output

    def compile(
        self,
        base: Mapping[str, Any],
        *,
        counter: ProviderTokenCounter,
        token_budget: int,
        byte_budget: int,
    ) -> CompiledAgentContext:
        if token_budget <= 0 or byte_budget <= 0:
            raise ValueError("context budgets must be positive")
        base_view = self._base_view(base)
        active = [item.provider_view(include_preview=True) for item in self.active]
        previews_dropped = 0

        def render() -> tuple[dict[str, Any], str, TokenCount, int]:
            packet = {
                "task": base_view,
                "active_evidence": active,
                "causal_receipts": list(self._causal),
                "context_policy": {
                    "raw_history_replayed": False,
                    "exact_recovery_required": True,
                    "mandatory_instruction_truncation": False,
                    "active_evidence_count": len(active),
                    "causal_receipt_count": len(self._causal),
                },
            }
            text = _canonical_json(packet)
            count = counter.count_text(text)
            return packet, text, count, len(text.encode("utf-8"))

        packet, text, count, visible_bytes = render()
        for index in range(len(active)):
            if count.tokens <= token_budget and visible_bytes <= byte_budget:
                break
            if "evidence" in active[index]:
                active[index].pop("evidence", None)
                active[index]["evidence_omitted"] = True
                previews_dropped += 1
                packet, text, count, visible_bytes = render()

        if count.tokens > token_budget or visible_bytes > byte_budget:
            raise ContextBudgetExceeded(
                "mandatory/handle-only context exceeds provider budget: "
                f"tokens={count.tokens}/{token_budget}, bytes={visible_bytes}/{byte_budget}"
            )
        digest = _sha256_bytes(text.encode("utf-8"))
        return CompiledAgentContext(
            text=text,
            tokens=count.tokens,
            visible_bytes=visible_bytes,
            counting_method=count.method,
            exact_target_tokenizer=count.exact_for_target,
            digest=digest,
            active_evidence=len(active),
            causal_receipts=len(self._causal),
            previews_dropped=previews_dropped,
        )


__all__ = [
    "CompiledAgentContext",
    "ConstantContextState",
    "ContextBudgetExceeded",
    "ProviderTokenCounter",
    "TokenCount",
    "ToolEvidenceReceipt",
    "compact_value",
]
