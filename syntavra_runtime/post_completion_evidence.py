from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .platform_common import _connect
from .post_completion_common import content_receipt, is_sha256, sha256_digest, sorted_unique


LIFECYCLE_ACTIONS = ("ingest", "normalize", "derive", "compress", "supersede", "revoke")


class JournalStore(Protocol):
    path: Any

    def journal(self, *, item_id: str | None = None) -> list[dict[str, Any]]: ...
    def verify_journal(self) -> dict[str, Any]: ...
    def _journal(
        self,
        db: Any,
        *,
        action: str,
        item_id: str,
        details: Mapping[str, Any] | None = None,
        actor: str = "evidence-store-v2",
        observed_at: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class MutationEvent:
    action: str
    item_id: str
    predecessor_ids: tuple[str, ...] = ()
    successor_id: str | None = None
    reason: str = ""
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.action not in LIFECYCLE_ACTIONS:
            raise ValueError(f"unsupported evidence lifecycle action: {self.action!r}")
        if not str(self.item_id).strip():
            raise ValueError("item_id is required")
        if self.action in {"derive", "compress", "supersede"} and not self.predecessor_ids:
            raise ValueError(f"{self.action} requires predecessor_ids")
        if self.action == "supersede" and not self.successor_id:
            raise ValueError("supersede requires successor_id")
        if self.action == "revoke" and not str(self.reason).strip():
            raise ValueError("revoke requires a reason")


class EvidenceMutationJournal:
    """Lifecycle overlay bound to the canonical EvidenceStoreV2 journal.

    This object owns no database. Detached mode may build deterministic event
    receipts, while persistence reuses the existing EvidenceStoreV2 SQLite path
    and hash-chained `_journal` authority.
    """

    def __init__(self, store: JournalStore | None = None):
        self.store = store

    @staticmethod
    def event(event: MutationEvent) -> dict[str, Any]:
        payload = {
            "action": event.action,
            "item_id": str(event.item_id),
            "predecessor_ids": list(sorted_unique(event.predecessor_ids)),
            "successor_id": event.successor_id,
            "reason": str(event.reason),
            "metadata": dict(event.metadata or {}),
        }
        return content_receipt("EVIDENCE_MUTATION_EVENT_V1", payload)

    def persist(
        self,
        event: MutationEvent,
        *,
        actor: str = "python-post-completion",
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        if self.store is None:
            raise RuntimeError("canonical EvidenceStoreV2 is required for persistence")
        receipt = self.event(event)
        details = {
            "lifecycle_action": event.action,
            "predecessor_ids": list(sorted_unique(event.predecessor_ids)),
            "successor_id": event.successor_id,
            "reason": str(event.reason),
            "metadata": dict(event.metadata or {}),
            "lifecycle_receipt_id": receipt["receipt_id"],
        }
        with _connect(self.store.path) as db:
            journal_event = self.store._journal(
                db,
                action=f"lifecycle-{event.action}",
                item_id=str(event.item_id),
                details=details,
                actor=actor,
                observed_at=observed_at,
            )
        return content_receipt(
            "EVIDENCE_MUTATION_JOURNAL_PERSIST_V1",
            {
                "ok": True,
                "item_id": str(event.item_id),
                "action": event.action,
                "journal_event": journal_event["event_hash"],
                "lifecycle_receipt_id": receipt["receipt_id"],
                "canonical_store_reused": True,
            },
        )

    def verify(self) -> dict[str, Any]:
        if self.store is None:
            return content_receipt(
                "EVIDENCE_MUTATION_JOURNAL_V1",
                {"ok": True, "mode": "detached", "canonical_store_required_for_persistence": True},
            )
        report = self.store.verify_journal()
        return content_receipt(
            "EVIDENCE_MUTATION_JOURNAL_V1",
            {
                "ok": bool(report.get("ok")),
                "mode": "canonical-store",
                "events": int(report.get("events", 0)),
                "head_hash": report.get("head_hash"),
                "failures": list(report.get("failures") or []),
            },
        )


class RecoveryResolver(Protocol):
    def __call__(self, kind: str, locator: Mapping[str, Any]) -> bytes | str | Mapping[str, Any]: ...


def _resolved_bytes(value: bytes | str | Mapping[str, Any]) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class RecoveryHandleIntegrityProof:
    @staticmethod
    def verify(handle: Any, resolver: RecoveryResolver, *, expected_bounds: Mapping[str, Any] | None = None) -> dict[str, Any]:
        kind = str(getattr(handle, "kind", ""))
        locator = dict(getattr(handle, "locator", {}) or {})
        integrity = str(getattr(handle, "integrity", ""))
        exact = bool(getattr(handle, "exact", False))
        failures: list[str] = []
        if not kind or not locator:
            failures.append("HANDLE_SHAPE_INVALID")
        if not is_sha256(integrity):
            failures.append("HANDLE_DIGEST_INVALID")
        if not exact:
            failures.append("HANDLE_NOT_EXACT")
        try:
            resolved = resolver(kind, locator)
        except Exception as exc:
            resolved = b""
            failures.append(f"RESOLUTION_FAILED:{type(exc).__name__}")
        digest = sha256_digest(_resolved_bytes(resolved))
        normalized_integrity = integrity if integrity.startswith("sha256:") else f"sha256:{integrity}"
        if is_sha256(integrity) and digest != normalized_integrity:
            failures.append("DIGEST_MISMATCH")
        if expected_bounds:
            for key, expected in expected_bounds.items():
                if locator.get(key) != expected:
                    failures.append(f"BOUNDARY_MISMATCH:{key}")
        if kind == "file-range":
            start = locator.get("start_line")
            end = locator.get("end_line")
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                failures.append("FILE_RANGE_INVALID")
        return content_receipt(
            "RECOVERY_HANDLE_INTEGRITY_PROOF_V1",
            {"ok": not failures, "kind": kind, "locator": locator, "resolved_sha256": digest, "failures": failures},
        )


class SecretScanner(Protocol):
    def redact(self, value: Any) -> tuple[Any, Mapping[str, Any]]: ...


class ArtifactBackend(Protocol):
    def put(self, value: bytes | str, **kwargs: Any) -> Any: ...
    def read(self, artifact_id: str) -> bytes: ...
    def verify(self, artifact_id: str | None = None) -> Mapping[str, Any]: ...


class SecretAwareArtifactStore:
    """Policy facade over the canonical ArtifactStore, not a parallel store."""

    def __init__(self, backend: ArtifactBackend, scanner: SecretScanner):
        self.backend = backend
        self.scanner = scanner

    def put(
        self,
        value: bytes | str,
        *,
        media_type: str = "text/plain",
        kind: str = "generic",
        metadata: Mapping[str, Any] | None = None,
        secret_policy: str = "reject",
        encrypted_reference: str | None = None,
    ) -> dict[str, Any]:
        if secret_policy not in {"reject", "pre-redacted", "encrypted-reference"}:
            raise ValueError("unsupported secret policy")
        raw_text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
        _, scan = self.scanner.redact(raw_text)
        detected = bool(dict(scan).get("redacted"))
        if detected and secret_policy == "reject":
            raise ValueError("secret-like material rejected")
        if secret_policy == "pre-redacted" and detected:
            raise ValueError("pre-redacted policy requires secret-free payload")
        if secret_policy == "encrypted-reference":
            if not encrypted_reference or not is_sha256(encrypted_reference):
                raise ValueError("encrypted-reference requires a content-addressed encrypted reference")
            stored: bytes | str = ""
            meta = {**dict(metadata or {}), "encrypted_reference": encrypted_reference, "secret_payload_externalized": True}
        else:
            stored = value
            meta = {**dict(metadata or {}), "secret_scan": dict(scan), "secret_policy": secret_policy}
        record = self.backend.put(stored, media_type=media_type, kind=kind, metadata=meta)
        return content_receipt(
            "SECRET_AWARE_ARTIFACT_STORE_V1",
            {
                "ok": True,
                "artifact_id": str(getattr(record, "artifact_id", "")),
                "secret_policy": secret_policy,
                "secret_detected": detected,
                "encrypted_reference": encrypted_reference,
            },
        )


class EvidenceRetentionGCPolicy:
    @staticmethod
    def plan(rows: Iterable[Mapping[str, Any]], *, protected_ids: Iterable[str] = ()) -> dict[str, Any]:
        protected = set(str(value) for value in protected_ids)
        material = [dict(row) for row in rows]
        parents_of_live: set[str] = set()
        for row in material:
            if row.get("pinned") or not row.get("expired"):
                parents_of_live.update(str(value) for value in row.get("parent_item_ids") or [])
        delete_candidates: list[str] = []
        retained: dict[str, str] = {}
        for row in sorted(material, key=lambda item: str(item.get("item_id", ""))):
            item_id = str(row.get("item_id", ""))
            if not item_id:
                continue
            if item_id in protected:
                retained[item_id] = "explicit-protection"
            elif row.get("pinned"):
                retained[item_id] = "pinned"
            elif item_id in parents_of_live:
                retained[item_id] = "provenance-parent"
            elif not row.get("expired"):
                retained[item_id] = "not-expired"
            else:
                delete_candidates.append(item_id)
        return content_receipt(
            "EVIDENCE_RETENTION_GC_POLICY_V1",
            {"ok": True, "delete_candidates": delete_candidates, "retained": retained, "provenance_safe": True},
        )


class EvidenceHashChain:
    @staticmethod
    def build(entries: Sequence[Mapping[str, Any]], *, seed: str | None = None) -> dict[str, Any]:
        previous = seed
        chain: list[dict[str, Any]] = []
        for index, raw in enumerate(entries):
            payload = dict(raw)
            body = {"index": index, "previous_hash": previous, "payload": payload}
            digest = sha256_digest(body)
            chain.append({**body, "event_hash": digest})
            previous = digest
        return content_receipt("EVIDENCE_HASH_CHAIN_V1", {"ok": True, "events": chain, "head_hash": previous})

    @staticmethod
    def verify(events: Sequence[Mapping[str, Any]], *, seed: str | None = None) -> dict[str, Any]:
        previous = seed
        failures: list[int] = []
        for index, raw in enumerate(events):
            row = dict(raw)
            body = {"index": index, "previous_hash": previous, "payload": dict(row.get("payload") or {})}
            expected = sha256_digest(body)
            if row.get("previous_hash") != previous or row.get("event_hash") != expected:
                failures.append(index)
            previous = str(row.get("event_hash") or "")
        return content_receipt("EVIDENCE_HASH_CHAIN_VERIFY_V1", {"ok": not failures, "failures": failures, "head_hash": previous or seed})


__all__ = [
    "EvidenceHashChain",
    "EvidenceMutationJournal",
    "EvidenceRetentionGCPolicy",
    "MutationEvent",
    "RecoveryHandleIntegrityProof",
    "SecretAwareArtifactStore",
]
