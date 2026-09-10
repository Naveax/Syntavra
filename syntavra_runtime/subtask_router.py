from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence

from .semantic_wire_ir import HandleBinding, SemanticAtom, SemanticWireCompiler
from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class Subtask:
    id: str
    title: str
    objective: str
    capability: str
    context_paths: tuple[str, ...]
    dependencies: tuple[str, ...]
    max_output_tokens: int
    handoff: str


@dataclass(frozen=True)
class DelegationPlan:
    delegated: bool
    reason: str
    tasks: tuple[Subtask, ...]
    receipt_hash: str


class AutomaticSubtaskDelegator:
    CAPABILITIES = (
        (re.compile(r"(?i)\b(test|coverage|verify|benchmark)\b"), "verification"),
        (re.compile(r"(?i)\b(security|secret|permission|auth|sandbox)\b"), "security"),
        (re.compile(r"(?i)\b(ui|dashboard|extension|frontend|statusline)\b"), "interface"),
        (re.compile(r"(?i)\b(database|schema|migration|index|memory)\b"), "data"),
        (re.compile(r"(?i)\b(provider|model|routing|quota|rate limit)\b"), "provider"),
        (re.compile(r"(?i)\b(ast|symbol|call graph|class hierarchy|refactor)\b"), "code-intelligence"),
    )

    @staticmethod
    def _sentences(text: str) -> list[str]:
        return [row.strip(" -*\t") for row in re.split(r"(?:\n+|(?<=[.!?])\s+)", text) if len(row.strip()) >= 8]

    def plan(self, objective: str, *, context_paths: Sequence[str] = (), max_tasks: int = 8) -> DelegationPlan:
        sentences = self._sentences(objective)
        groups: dict[str, list[str]] = {}
        for sentence in sentences:
            capability = "implementation"
            for pattern, name in self.CAPABILITIES:
                if pattern.search(sentence):
                    capability = name
                    break
            groups.setdefault(capability, []).append(sentence)
        if len(groups) <= 1 and len(sentences) <= 3:
            body = {"delegated": False, "reason": "task is small enough for one agent", "tasks": ()}
            return DelegationPlan(**body, receipt_hash=sha256_bytes(canonical_json(body)))
        tasks: list[Subtask] = []
        previous: list[str] = []
        for index, (capability, items) in enumerate(sorted(groups.items()), 1):
            if len(tasks) >= max_tasks:
                break
            task_id = f"T{index:02d}"
            objective_text = " ".join(items)
            handoff = f"{task_id} {capability}: return decisions, changed paths, verification commands, blockers; omit narration."
            tasks.append(Subtask(task_id, capability.replace("-", " ").title(), objective_text, capability, tuple(context_paths), tuple(previous[-2:]), 1200, handoff))
            previous.append(task_id)
        body = {"delegated": True, "reason": "independent capability groups detected", "tasks": tuple(tasks)}
        return DelegationPlan(**body, receipt_hash=sha256_bytes(canonical_json({"delegated": True, "reason": body["reason"], "tasks": [asdict(item) for item in tasks]})))


_SERIALIZED_MEDIA = ("handle", "receipt", "swir", "text")
_LATENT_MEDIA = ("embedding", "hidden_state", "kv")
_ROUTABLE_MEDIA = frozenset((*_SERIALIZED_MEDIA, *_LATENT_MEDIA))
_FIDELITY_RANK = {"semantic": 1, "exact": 2}
_FALLBACK_PRIORITY = {
    "handle": 0,
    "receipt": 1,
    "swir": 2,
    "text": 3,
    "embedding": 4,
    "hidden_state": 5,
    "kv": 6,
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EndpointCapabilities:
    """Explicit handoff capabilities for one endpoint.

    Ownership is caller-declared authority, never inferred from a provider alias.
    Hosted endpoints are validated through ProviderGateway and may not advertise
    latent/KV media. Local/owned latent media require a compatibility id.
    """

    endpoint_id: str
    ownership: str
    security_domain: str
    supported_media: tuple[str, ...] = ("text",)
    provider: str = ""
    resolves_handles: bool = False
    resolves_receipts: bool = False
    latent_compatibility_id: str = ""

    def __post_init__(self) -> None:
        endpoint_id = self.endpoint_id.strip()
        ownership = self.ownership.strip().casefold()
        security_domain = self.security_domain.strip()
        provider = self.provider.strip()
        media = tuple(dict.fromkeys(str(item).strip().casefold() for item in self.supported_media if str(item).strip()))
        if not endpoint_id or not security_domain:
            raise ValueError("endpoint_id and security_domain are required")
        if ownership not in {"hosted", "local", "owned"}:
            raise ValueError("ownership must be hosted, local, or owned")
        if not media or "text" not in media:
            raise ValueError("every endpoint must preserve text fallback")
        unknown = sorted(set(media) - _ROUTABLE_MEDIA)
        if unknown:
            raise ValueError(f"unsupported communication media: {unknown!r}")
        latent = set(media) & set(_LATENT_MEDIA)
        if ownership == "hosted":
            if not provider:
                raise ValueError("hosted endpoint requires provider")
            from .provider_gateway import ProviderGateway

            ProviderGateway.capabilities(provider)
            if latent:
                raise ValueError("hosted endpoints cannot advertise embedding/hidden-state/KV handoff")
        elif latent and not self.latent_compatibility_id.strip():
            raise ValueError("local/owned latent media require latent_compatibility_id")
        object.__setattr__(self, "endpoint_id", endpoint_id)
        object.__setattr__(self, "ownership", ownership)
        object.__setattr__(self, "security_domain", security_domain)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "supported_media", media)
        object.__setattr__(self, "latent_compatibility_id", self.latent_compatibility_id.strip())


@dataclass(frozen=True)
class ReceiptReference:
    """Opaque typed reference to a receipt owned elsewhere."""

    kind: str
    receipt_hash: str
    recovery_ref: str = ""

    def __post_init__(self) -> None:
        kind = self.kind.strip()
        digest = self.receipt_hash.strip().casefold()
        if not kind:
            raise ValueError("receipt kind is required")
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("receipt_hash must be a lowercase SHA-256 digest")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "receipt_hash", digest)
        object.__setattr__(self, "recovery_ref", self.recovery_ref.strip())


@dataclass(frozen=True)
class MediumCostEvidence:
    medium: str
    end_to_end_cost_units: int
    fidelity: str = "semantic"
    verified: bool = True
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        medium = self.medium.strip().casefold()
        fidelity = self.fidelity.strip().casefold()
        if medium not in _ROUTABLE_MEDIA:
            raise ValueError(f"unsupported communication medium: {medium}")
        if int(self.end_to_end_cost_units) < 0:
            raise ValueError("end_to_end_cost_units must be non-negative")
        if fidelity not in _FIDELITY_RANK:
            raise ValueError("fidelity must be semantic or exact")
        if self.verified and not self.evidence_ref.strip():
            raise ValueError("verified cost evidence requires evidence_ref")
        object.__setattr__(self, "medium", medium)
        object.__setattr__(self, "fidelity", fidelity)
        object.__setattr__(self, "end_to_end_cost_units", int(self.end_to_end_cost_units))
        object.__setattr__(self, "evidence_ref", self.evidence_ref.strip())


@dataclass(frozen=True)
class ProviderWorkEvidence:
    baseline_visible_tokens: int
    selected_visible_tokens: int
    baseline_calls: int
    selected_calls: int
    paired_provider_observed: bool = False
    baseline_receipt_ref: str = ""
    selected_receipt_ref: str = ""

    def __post_init__(self) -> None:
        values = (self.baseline_visible_tokens, self.selected_visible_tokens, self.baseline_calls, self.selected_calls)
        if any(int(value) < 0 for value in values):
            raise ValueError("provider work counters must be non-negative")
        if self.paired_provider_observed and (not self.baseline_receipt_ref.strip() or not self.selected_receipt_ref.strip()):
            raise ValueError("paired provider observations require both receipt refs")


@dataclass(frozen=True)
class RouteDecision:
    source_endpoint: str
    destination_endpoint: str
    medium: str
    payload: str
    fidelity: str
    payload_tokens: int
    end_to_end_cost_units: int | None
    cost_verified: bool
    baseline_provider_visible_tokens: int
    selected_provider_visible_tokens: int
    provider_visible_tokens_avoided: int
    baseline_provider_calls: int
    selected_provider_calls: int
    provider_visible_calls_avoided: int
    provider_savings_claim: bool
    capability_matrix: tuple[tuple[str, bool], ...]
    reason_codes: tuple[str, ...]
    receipt_hash: str

    def receipt(self) -> dict[str, object]:
        body: dict[str, object] = {
            "source_endpoint": self.source_endpoint,
            "destination_endpoint": self.destination_endpoint,
            "medium": self.medium,
            "payload": self.payload,
            "fidelity": self.fidelity,
            "payload_tokens": self.payload_tokens,
            "end_to_end_cost_units": self.end_to_end_cost_units,
            "cost_verified": self.cost_verified,
            "baseline_provider_visible_tokens": self.baseline_provider_visible_tokens,
            "selected_provider_visible_tokens": self.selected_provider_visible_tokens,
            "provider_visible_tokens_avoided": self.provider_visible_tokens_avoided,
            "baseline_provider_calls": self.baseline_provider_calls,
            "selected_provider_calls": self.selected_provider_calls,
            "provider_visible_calls_avoided": self.provider_visible_calls_avoided,
            "provider_savings_claim": self.provider_savings_claim,
            "capability_matrix": dict(self.capability_matrix),
            "reason_codes": list(self.reason_codes),
        }
        return {**body, "receipt_hash": self.receipt_hash}

    @staticmethod
    def verify_receipt(receipt: Mapping[str, object]) -> bool:
        body = dict(receipt)
        provided = str(body.pop("receipt_hash", ""))
        expected = sha256_bytes(canonical_json(body))
        if provided != expected:
            raise ValueError("communication route receipt hash mismatch")
        return True


@dataclass(frozen=True)
class _MediumCandidate:
    medium: str
    payload: str
    fidelity: str
    payload_tokens: int


class CommunicationMediumRouter:
    """Decision-only router across already-owned handoff representations."""

    def __init__(self, swir_compiler: SemanticWireCompiler) -> None:
        self.swir_compiler = swir_compiler

    @staticmethod
    def capability_matrix(source: EndpointCapabilities, destination: EndpointCapabilities) -> dict[str, bool]:
        shared = set(source.supported_media) & set(destination.supported_media)
        latent_compatible = (
            source.ownership in {"local", "owned"}
            and destination.ownership in {"local", "owned"}
            and source.security_domain == destination.security_domain
            and bool(source.latent_compatibility_id)
            and source.latent_compatibility_id == destination.latent_compatibility_id
        )
        return {
            "silence": True,
            "handle": "handle" in shared and destination.resolves_handles,
            "receipt": "receipt" in shared and destination.resolves_receipts,
            "swir": "swir" in shared,
            "text": "text" in shared,
            "embedding": "embedding" in shared and latent_compatible,
            "hidden_state": "hidden_state" in shared and latent_compatible,
            "kv": "kv" in shared and latent_compatible,
        }

    def _count(self, text: str) -> int:
        value = int(self.swir_compiler.token_counter(text))
        if value < 0:
            raise ValueError("token counter returned a negative count")
        return value

    @staticmethod
    def _provider_accounting(
        source: EndpointCapabilities,
        destination: EndpointCapabilities,
        evidence: ProviderWorkEvidence | None,
    ) -> tuple[int, int, int, int, int, int, tuple[str, ...]]:
        if evidence is None:
            return (0, 0, 0, 0, 0, 0, ())
        if source.ownership != "hosted" and destination.ownership != "hosted":
            raise ValueError("provider-visible work evidence requires a hosted endpoint")
        baseline_tokens = int(evidence.baseline_visible_tokens)
        selected_tokens = int(evidence.selected_visible_tokens)
        baseline_calls = int(evidence.baseline_calls)
        selected_calls = int(evidence.selected_calls)
        if evidence.paired_provider_observed:
            return (
                baseline_tokens,
                selected_tokens,
                max(0, baseline_tokens - selected_tokens),
                baseline_calls,
                selected_calls,
                max(0, baseline_calls - selected_calls),
                ("PAIRED_PROVIDER_WORK_RECORDED",),
            )
        return (
            baseline_tokens,
            selected_tokens,
            0,
            baseline_calls,
            selected_calls,
            0,
            ("UNPAIRED_PROVIDER_WORK_NOT_COUNTED_AS_AVOIDED",),
        )

    @staticmethod
    def _make_decision(
        *,
        source: EndpointCapabilities,
        destination: EndpointCapabilities,
        candidate: _MediumCandidate,
        selected_cost_units: int | None,
        cost_verified: bool,
        capability_matrix: Mapping[str, bool],
        provider_accounting: tuple[int, int, int, int, int, int, tuple[str, ...]],
        reason_codes: Sequence[str],
    ) -> RouteDecision:
        baseline_tokens, selected_tokens, avoided_tokens, baseline_calls, selected_calls, avoided_calls, provider_reasons = provider_accounting
        reasons = tuple(dict.fromkeys((*reason_codes, *provider_reasons)))
        body: dict[str, object] = {
            "source_endpoint": source.endpoint_id,
            "destination_endpoint": destination.endpoint_id,
            "medium": candidate.medium,
            "payload": candidate.payload,
            "fidelity": candidate.fidelity,
            "payload_tokens": candidate.payload_tokens,
            "end_to_end_cost_units": selected_cost_units,
            "cost_verified": cost_verified,
            "baseline_provider_visible_tokens": baseline_tokens,
            "selected_provider_visible_tokens": selected_tokens,
            "provider_visible_tokens_avoided": avoided_tokens,
            "baseline_provider_calls": baseline_calls,
            "selected_provider_calls": selected_calls,
            "provider_visible_calls_avoided": avoided_calls,
            "provider_savings_claim": False,
            "capability_matrix": dict(capability_matrix),
            "reason_codes": list(reasons),
        }
        receipt_hash = sha256_bytes(canonical_json(body))
        decision = RouteDecision(
            source.endpoint_id,
            destination.endpoint_id,
            candidate.medium,
            candidate.payload,
            candidate.fidelity,
            candidate.payload_tokens,
            selected_cost_units,
            cost_verified,
            baseline_tokens,
            selected_tokens,
            avoided_tokens,
            baseline_calls,
            selected_calls,
            avoided_calls,
            False,
            tuple((key, bool(value)) for key, value in capability_matrix.items()),
            reasons,
            receipt_hash,
        )
        RouteDecision.verify_receipt(decision.receipt())
        return decision

    def route(
        self,
        source: EndpointCapabilities,
        destination: EndpointCapabilities,
        natural_text: str,
        *,
        handoff_required: bool = True,
        exact_required: bool = False,
        handle: HandleBinding | None = None,
        receipt: ReceiptReference | None = None,
        swir_atoms: Sequence[SemanticAtom] = (),
        latent_refs: Mapping[str, str] | None = None,
        cost_evidence: Sequence[MediumCostEvidence] = (),
        provider_work: ProviderWorkEvidence | None = None,
        grammar_already_in_fixed_prefix: bool = False,
    ) -> RouteDecision:
        matrix = self.capability_matrix(source, destination)
        provider_accounting = self._provider_accounting(source, destination, provider_work)
        evidence_by_medium: dict[str, MediumCostEvidence] = {}
        for row in cost_evidence:
            if row.medium in evidence_by_medium:
                raise ValueError(f"duplicate cost evidence for medium: {row.medium}")
            evidence_by_medium[row.medium] = row

        if not handoff_required:
            return self._make_decision(
                source=source,
                destination=destination,
                candidate=_MediumCandidate("silence", "", "exact", 0),
                selected_cost_units=0,
                cost_verified=True,
                capability_matrix=matrix,
                provider_accounting=provider_accounting,
                reason_codes=("HANDOFF_NOT_REQUIRED",),
            )
        if not natural_text.strip():
            raise ValueError("required handoff must preserve non-empty text fallback")

        required_rank = _FIDELITY_RANK["exact" if exact_required else "semantic"]
        candidates: list[_MediumCandidate] = []
        reasons: list[str] = []

        if handle is not None:
            if matrix["handle"]:
                fidelity = "exact" if handle.recovery_ref else "semantic"
                if _FIDELITY_RANK[fidelity] >= required_rank:
                    candidates.append(_MediumCandidate("handle", handle.handle, fidelity, self._count(handle.handle)))
                else:
                    reasons.append("EXACT_HANDLE_RECOVERY_MISSING")
            else:
                reasons.append("HANDLE_UNAVAILABLE")

        if receipt is not None:
            if matrix["receipt"]:
                fidelity = "exact" if receipt.recovery_ref else "semantic"
                if _FIDELITY_RANK[fidelity] >= required_rank:
                    payload = f"{receipt.kind}:{receipt.receipt_hash}"
                    candidates.append(_MediumCandidate("receipt", payload, fidelity, self._count(payload)))
                else:
                    reasons.append("EXACT_RECEIPT_RECOVERY_MISSING")
            else:
                reasons.append("RECEIPT_UNAVAILABLE")

        if swir_atoms:
            if matrix["swir"]:
                expected_natural = "\n".join(
                    row.natural.strip()
                    for row in swir_atoms
                    if (row.mandatory or row.natural.strip()) and row.natural.strip()
                )
                if expected_natural != natural_text.strip():
                    reasons.append("SWIR_NATURAL_BASELINE_MISMATCH")
                elif exact_required and not all(row.exact_required and row.recovery_ref for row in swir_atoms):
                    reasons.append("SWIR_EXACT_RECOVERY_INCOMPLETE")
                else:
                    packet = self.swir_compiler.compile(
                        swir_atoms,
                        grammar_already_in_fixed_prefix=grammar_already_in_fixed_prefix,
                    )
                    if packet.mode == "wire":
                        fidelity = "exact" if exact_required else "semantic"
                        candidates.append(_MediumCandidate("swir", packet.text, fidelity, packet.selected_tokens))
                    else:
                        reasons.append("SWIR_NO_EXPANSION_TEXT_FALLBACK")
            else:
                reasons.append("SWIR_UNAVAILABLE")

        candidates.append(_MediumCandidate("text", natural_text, "exact", self._count(natural_text)))

        for medium, reference in sorted((latent_refs or {}).items()):
            normalized = str(medium).strip().casefold()
            if normalized not in _LATENT_MEDIA:
                raise ValueError(f"latent_refs contains non-latent medium: {medium}")
            if not str(reference).strip():
                raise ValueError(f"latent reference is empty: {normalized}")
            if not matrix[normalized]:
                reasons.append(f"LATENT_UNAVAILABLE:{normalized}")
                continue
            evidence = evidence_by_medium.get(normalized)
            if evidence is None or not evidence.verified:
                reasons.append(f"LATENT_REQUIRES_VERIFIED_COST_AND_FIDELITY:{normalized}")
                continue
            if _FIDELITY_RANK[evidence.fidelity] < required_rank:
                reasons.append(f"LATENT_FIDELITY_INSUFFICIENT:{normalized}")
                continue
            candidates.append(_MediumCandidate(normalized, str(reference).strip(), evidence.fidelity, 0))

        eligible = [candidate for candidate in candidates if _FIDELITY_RANK[candidate.fidelity] >= required_rank]
        if not eligible:
            raise ValueError("no faithful communication medium is available")

        verified: list[tuple[int, int, _MediumCandidate]] = []
        for candidate in eligible:
            evidence = evidence_by_medium.get(candidate.medium)
            if evidence is None or not evidence.verified:
                continue
            if _FIDELITY_RANK[evidence.fidelity] < required_rank:
                continue
            verified.append((evidence.end_to_end_cost_units, _FALLBACK_PRIORITY[candidate.medium], candidate))

        if verified:
            selected_cost, _, selected = min(verified, key=lambda row: (row[0], row[1]))
            reasons.append("VERIFIED_END_TO_END_COST_WIN")
            cost_verified = True
        else:
            serialized = [candidate for candidate in eligible if candidate.medium in _SERIALIZED_MEDIA]
            if not serialized:
                raise ValueError("no verified latent route and no serialized fallback")
            selected = min(serialized, key=lambda candidate: _FALLBACK_PRIORITY[candidate.medium])
            selected_cost = None
            reasons.append("SAFE_SERIALIZED_PRIORITY_FALLBACK")
            cost_verified = False

        return self._make_decision(
            source=source,
            destination=destination,
            candidate=selected,
            selected_cost_units=selected_cost,
            cost_verified=cost_verified,
            capability_matrix=matrix,
            provider_accounting=provider_accounting,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def status() -> dict[str, object]:
        return {
            "mechanism_id": "U5-P0-04",
            "wave": "TE-U32",
            "router_owns_transport": False,
            "router_owns_swir": False,
            "router_owns_receipt_semantics": False,
            "router_infers_locality_from_provider_alias": False,
            "text_fallback_required": True,
            "hosted_latent_allowed": False,
            "provider_savings_claim": False,
        }


__all__ = [
    "AutomaticSubtaskDelegator",
    "CommunicationMediumRouter",
    "DelegationPlan",
    "EndpointCapabilities",
    "MediumCostEvidence",
    "ProviderWorkEvidence",
    "ReceiptReference",
    "RouteDecision",
    "Subtask",
]
