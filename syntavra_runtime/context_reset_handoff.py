from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .session_memory import SessionMemory
from .util import canonical_json, sha256_bytes


DECISIONS = frozenset({"CONTINUE", "COMPACT", "RESET", "BRANCH"})


@dataclass(frozen=True)
class RepositoryHandoffState:
    repository: str
    branch: str
    head_sha: str
    clean: bool = True
    upstream_ref: str = ""
    worktree_recovery_ref: str = ""

    def __post_init__(self) -> None:
        if not self.repository.strip():
            raise ValueError("repository identity is required")
        if not self.branch.strip():
            raise ValueError("repository branch is required")
        sha = self.head_sha.strip().lower()
        if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
            raise ValueError("head_sha must be a full lowercase Git SHA")
        if not self.clean and not self.worktree_recovery_ref.strip():
            raise ValueError("dirty worktree requires a recovery reference")
        object.__setattr__(self, "head_sha", sha)


@dataclass(frozen=True)
class SecurityHandoffState:
    tainted: bool = False
    unresolved_critical_evidence: int = 0
    capability_denied: bool = False
    secret_material_present: bool = False
    policy_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if int(self.unresolved_critical_evidence) < 0:
            raise ValueError("unresolved_critical_evidence must be non-negative")
        object.__setattr__(self, "unresolved_critical_evidence", int(self.unresolved_critical_evidence))
        object.__setattr__(
            self,
            "policy_refs",
            tuple(sorted(set(str(value) for value in self.policy_refs if str(value)))),
        )
        if self.secret_material_present:
            raise ValueError("handoff may preserve secret state, never secret material")
        if self.tainted and not self.policy_refs:
            raise ValueError("tainted handoff requires policy/evidence references")


class ContextResetHandoff:
    """Verified session lifecycle handoff built on the existing SessionMemory authority."""

    @staticmethod
    def _normalize_refs(values: Sequence[str]) -> tuple[str, ...]:
        return tuple(sorted(set(str(value) for value in values if str(value))))

    def prepare(
        self,
        memory: SessionMemory,
        session_id: str,
        decision: str,
        *,
        repository: RepositoryHandoffState,
        evidence_refs: Sequence[str] = (),
        recovery_handles: Sequence[str] = (),
        security: SecurityHandoffState | None = None,
        reason_codes: Sequence[str] = (),
    ) -> dict[str, Any]:
        action = str(decision).upper().strip()
        if action not in DECISIONS:
            raise ValueError(f"unsupported context lifecycle decision: {decision}")
        security = security or SecurityHandoffState()
        evidence = self._normalize_refs(evidence_refs)
        recovery = self._normalize_refs(recovery_handles)
        reasons = self._normalize_refs(reason_codes)

        verification = memory.verify(session_id)
        if verification.get("ok") is not True:
            raise RuntimeError("session event chain is not verified")
        if action in {"RESET", "BRANCH"} and security.unresolved_critical_evidence and not evidence:
            raise RuntimeError("critical evidence must remain referenced across reset/branch")

        compacted: dict[str, Any] | None = None
        if action == "COMPACT":
            compacted = memory.compact(session_id)
            if compacted.get("exact_history_preserved") is not True:
                raise RuntimeError("compaction did not preserve exact session history")

        checkpoint = memory.checkpoint(session_id, f"context-handoff:{action.lower()}")
        checkpoint_ref = f"session-checkpoint:{checkpoint['checkpoint_id']}"
        effective_recovery = self._normalize_refs((*recovery, checkpoint_ref))

        basis = {
            "schema_version": 1,
            "decision": action,
            "project_id": memory.project_id,
            "source_session_id": session_id,
            "checkpoint": {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "sequence": checkpoint["sequence"],
                "event_hash": checkpoint["event_hash"],
            },
            "repository": asdict(repository),
            "evidence_refs": list(evidence),
            "recovery_handles": list(effective_recovery),
            "security": asdict(security),
            "reason_codes": list(reasons),
        }
        basis_hash = sha256_bytes(canonical_json(basis))
        if action in {"RESET", "BRANCH"}:
            target_session_id = f"ctx-{basis_hash[:24]}"
            target = memory.open(
                target_session_id,
                parents=(session_id,),
                metadata={
                    "handoff_decision": action,
                    "handoff_checkpoint": checkpoint["checkpoint_id"],
                    "handoff_receipt": basis_hash,
                },
            )
        else:
            target_session_id = session_id
            target = None

        receipt_basis = {
            **basis,
            "target_session_id": target_session_id,
            "compaction_summary_ids": [
                row["summary_id"] for row in (compacted or {}).get("summaries", [])
            ],
        }
        receipt_hash = sha256_bytes(canonical_json(receipt_basis))
        return {
            "schema_version": 1,
            "claim": "CONTEXT_RESET_HANDOFF_V1",
            "ok": True,
            "decision": action,
            "source_session_id": session_id,
            "target_session_id": target_session_id,
            "checkpoint": receipt_basis["checkpoint"],
            "repository": receipt_basis["repository"],
            "evidence_refs": list(evidence),
            "recovery_handles": list(effective_recovery),
            "security": receipt_basis["security"],
            "reason_codes": list(reasons),
            "compaction_summary_ids": receipt_basis["compaction_summary_ids"],
            "target_created": bool(target and not target.get("restored")),
            "verified": {
                "session_chain": True,
                "repository_recoverable": repository.clean or bool(repository.worktree_recovery_ref),
                "secret_material_excluded": True,
                "exact_history_preserved": True,
            },
            "receipt": {
                **receipt_basis,
                "receipt_hash": receipt_hash,
            },
        }

    @staticmethod
    def status() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "claim": "CONTEXT_RESET_HANDOFF_V1",
            "decisions": sorted(DECISIONS),
            "session_memory_authority_reused": True,
            "new_persistent_store": False,
            "secret_material_allowed": False,
            "content_addressed_receipts": True,
            "git_state_preserved": True,
            "security_state_preserved": True,
            "evidence_state_preserved": True,
        }


__all__ = [
    "DECISIONS",
    "RepositoryHandoffState",
    "SecurityHandoffState",
    "ContextResetHandoff",
]
