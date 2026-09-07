from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence


_TOKEN_COUNTER = Callable[[str], int]
_HANDLE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}(?:[0-9]{1,6})?$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,15}$")


@dataclass(frozen=True)
class HandleBinding:
    """Reference-only semantic binding used by Semantic Wire IR.

    The binding never owns the raw payload. ``canonical_ref`` and ``recovery_ref``
    point at canonical/exact owners elsewhere in Syntavra. ``dependency_digest``
    is used by callers to invalidate stale bindings when repository/evidence state
    changes.
    """

    handle: str
    kind: str
    canonical_ref: str
    dependency_digest: str
    recovery_ref: str = ""
    display: str = ""
    protocol_version: int = 1

    def __post_init__(self) -> None:
        if not _HANDLE_RE.fullmatch(self.handle):
            raise ValueError(f"invalid semantic handle: {self.handle}")
        if not self.kind.strip() or not self.canonical_ref.strip():
            raise ValueError("semantic handle requires kind and canonical_ref")
        if not self.dependency_digest.strip():
            raise ValueError("semantic handle requires dependency_digest")
        if int(self.protocol_version) != 1:
            raise ValueError("unsupported Semantic Wire IR protocol version")


@dataclass(frozen=True)
class SemanticAtom:
    """One unit of meaning with a natural and optional compact representation."""

    code: str
    natural: str
    compact: str = ""
    exact_required: bool = False
    recovery_ref: str = ""
    mandatory: bool = True
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _CODE_RE.fullmatch(self.code):
            raise ValueError(f"invalid semantic atom code: {self.code}")
        if self.mandatory and not self.natural.strip():
            raise ValueError("mandatory semantic atom requires natural text")
        if self.exact_required and self.compact and not self.recovery_ref:
            raise ValueError("exact-required compact atom requires recovery_ref")


@dataclass(frozen=True)
class WireGrammar:
    version: int = 1
    prefix: str = ""
    operators: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if int(self.version) != 1:
            raise ValueError("unsupported wire grammar version")
        for key, value in self.operators.items():
            if not _CODE_RE.fullmatch(str(key)) or not str(value).strip():
                raise ValueError(f"invalid wire grammar operator: {key}")

    def rendered(self) -> str:
        rows = [self.prefix.strip()] if self.prefix.strip() else []
        rows.extend(f"{key}={value}" for key, value in sorted(self.operators.items()))
        return "\n".join(rows)


@dataclass(frozen=True)
class WirePacket:
    protocol_version: int
    mode: str
    text: str
    natural_tokens: int
    selected_tokens: int
    grammar_tokens: int
    payload_tokens: int
    saved_tokens: int
    savings_ratio: float
    atom_codes: tuple[str, ...]
    recovery_refs: tuple[str, ...]
    reasons: tuple[str, ...]
    digest: str

    @property
    def expanded(self) -> bool:
        return self.selected_tokens > self.natural_tokens


class SemanticWireCompiler:
    """Tokenizer-aware no-expansion compiler for provider-visible context.

    V1 is intentionally small. It provides the safety boundary required before
    richer task-family DSLs are admitted: the target provider tokenizer decides
    whether compact representation actually wins, exact-required compact atoms
    require recovery handles, and ambiguous/unsupported atoms fall back to the
    natural packet rather than guessing.
    """

    def __init__(self, token_counter: _TOKEN_COUNTER, *, grammar: WireGrammar | None = None) -> None:
        self.token_counter = token_counter
        self.grammar = grammar or WireGrammar()

    def _count(self, text: str) -> int:
        value = int(self.token_counter(text))
        if value < 0:
            raise ValueError("token counter returned a negative count")
        return value

    @staticmethod
    def _digest(value: Mapping[str, object]) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def compile(
        self,
        atoms: Iterable[SemanticAtom],
        *,
        grammar_already_in_fixed_prefix: bool = False,
        force_natural: bool = False,
    ) -> WirePacket:
        rows = tuple(atoms)
        if not rows:
            raise ValueError("Semantic Wire IR requires at least one atom")
        if len({row.code for row in rows}) != len(rows):
            raise ValueError("semantic atom codes must be unique within a packet")

        natural_parts = [row.natural.strip() for row in rows if row.mandatory or row.natural.strip()]
        natural_text = "\n".join(part for part in natural_parts if part)
        if not natural_text:
            raise ValueError("Semantic Wire IR natural packet cannot be empty")
        natural_tokens = self._count(natural_text)

        reasons: list[str] = []
        compact_parts: list[str] = []
        recovery_refs: list[str] = []
        wire_safe = not force_natural
        if force_natural:
            reasons.append("FORCED_NATURAL")

        for row in rows:
            compact = row.compact.strip()
            if not compact:
                wire_safe = False
                reasons.append(f"NO_COMPACT_FORM:{row.code}")
                continue
            if row.exact_required and not row.recovery_ref:
                wire_safe = False
                reasons.append(f"EXACT_RECOVERY_MISSING:{row.code}")
                continue
            compact_parts.append(compact)
            if row.recovery_ref:
                recovery_refs.append(row.recovery_ref)

        grammar_text = self.grammar.rendered()
        grammar_tokens = 0 if grammar_already_in_fixed_prefix else self._count(grammar_text) if grammar_text else 0
        compact_text = " ".join(compact_parts)
        payload_tokens = self._count(compact_text) if compact_text else 0
        wire_tokens = grammar_tokens + payload_tokens

        if wire_safe and wire_tokens < natural_tokens:
            selected = compact_text
            if grammar_text and not grammar_already_in_fixed_prefix:
                selected = grammar_text + "\n" + selected
            mode = "wire"
            selected_tokens = wire_tokens
            reasons.append("TOKENIZER_MEASURED_WIN")
        else:
            mode = "natural"
            selected = natural_text
            selected_tokens = natural_tokens
            if wire_safe and wire_tokens >= natural_tokens:
                reasons.append("NO_EXPANSION_GATE")

        saved = max(0, natural_tokens - selected_tokens)
        ratio = saved / natural_tokens if natural_tokens else 0.0
        digest = self._digest(
            {
                "v": 1,
                "mode": mode,
                "text": selected,
                "natural_tokens": natural_tokens,
                "selected_tokens": selected_tokens,
                "grammar_tokens": grammar_tokens,
                "payload_tokens": payload_tokens,
                "codes": [row.code for row in rows],
                "recovery_refs": sorted(set(recovery_refs)),
            }
        )
        return WirePacket(
            protocol_version=1,
            mode=mode,
            text=selected,
            natural_tokens=natural_tokens,
            selected_tokens=selected_tokens,
            grammar_tokens=grammar_tokens,
            payload_tokens=payload_tokens,
            saved_tokens=saved,
            savings_ratio=ratio,
            atom_codes=tuple(row.code for row in rows),
            recovery_refs=tuple(sorted(set(recovery_refs))),
            reasons=tuple(dict.fromkeys(reasons)),
            digest=digest,
        )


@dataclass(frozen=True)
class AnswerField:
    name: str
    wire_key: str
    required: bool = False
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip() or not _CODE_RE.fullmatch(self.wire_key):
            raise ValueError("invalid answer field")


class CompactAnswerCodec:
    """Compact provider-output contract with deterministic local rendering.

    The provider should be instructed to emit the encoded fields directly. If a
    verbose answer is generated first and encoded afterwards, provider output
    tokens have already been spent and that transformation must not be counted as
    provider-output savings.
    """

    def __init__(self, fields: Sequence[AnswerField]) -> None:
        self.fields = tuple(fields)
        if not self.fields or len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("answer codec requires unique fields")
        if len({field.wire_key for field in self.fields}) != len(self.fields):
            raise ValueError("answer wire keys must be unique")
        self._by_name = {field.name: field for field in self.fields}
        self._by_key = {field.wire_key: field for field in self.fields}

    def grammar(self) -> str:
        rows = ["Return only SWIR answer fields separated by spaces."]
        for field in self.fields:
            labels = ",".join(f"{key}:{value}" for key, value in sorted(field.labels.items()))
            suffix = f" values[{labels}]" if labels else ""
            rows.append(f"{field.wire_key}={field.name}{suffix}")
        return "\n".join(rows)

    def encode(self, values: Mapping[str, object]) -> str:
        missing = [field.name for field in self.fields if field.required and field.name not in values]
        if missing:
            raise ValueError("missing required answer fields: " + ",".join(missing))
        parts: list[str] = []
        for field in self.fields:
            if field.name not in values:
                continue
            raw = str(values[field.name])
            reverse = {label: code for code, label in field.labels.items()}
            encoded = reverse.get(raw, raw)
            if any(character.isspace() for character in encoded):
                encoded = json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))
            parts.append(f"{field.wire_key}:{encoded}")
        return " ".join(parts)

    def decode(self, wire: str) -> dict[str, str]:
        output: dict[str, str] = {}
        for part in wire.split():
            if ":" not in part:
                raise ValueError(f"invalid compact answer segment: {part}")
            key, raw = part.split(":", 1)
            field = self._by_key.get(key)
            if field is None:
                raise ValueError(f"unknown compact answer key: {key}")
            value = field.labels.get(raw, raw)
            output[field.name] = value
        missing = [field.name for field in self.fields if field.required and field.name not in output]
        if missing:
            raise ValueError("compact answer missing required fields: " + ",".join(missing))
        return output

    def render(self, wire: str, templates: Mapping[str, str]) -> str:
        """Render only deterministic templates supplied by the caller.

        Templates use ``str.format`` with decoded answer fields. Missing template
        fields fail instead of fabricating prose.
        """

        values = self.decode(wire)
        rows: list[str] = []
        for name, template in templates.items():
            if name not in values:
                continue
            rows.append(str(template).format(**values))
        return "\n".join(rows)


__all__ = [
    "AnswerField",
    "CompactAnswerCodec",
    "HandleBinding",
    "SemanticAtom",
    "SemanticWireCompiler",
    "WireGrammar",
    "WirePacket",
]
