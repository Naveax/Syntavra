from __future__ import annotations

import math
import time
from dataclasses import asdict
from typing import Any, Iterable

from .evidence import EvidenceStore
from .memory import MemoryRecord, PersistentMemory


class ZeroTokenMemoryEngine:
    """Offline memory fabric that reuses PersistentMemory and EvidenceStore.

    The default path never invokes a provider. Raw memory text remains owned by
    PersistentMemory; EvidenceStore only owns exact evidence bytes referenced by
    deterministic memory references.
    """

    schema_version = "syntavra.zero_token_memory_engine.v1"
    token_policy = "ZERO_DEFAULT"

    def __init__(self, memory: PersistentMemory, evidence: EvidenceStore):
        if memory.project_id != evidence.project_id:
            raise ValueError("memory/evidence project scope mismatch")
        self.memory = memory
        self.evidence = evidence

    def _reference(self, memory_id: str) -> str:
        return f"memory:{self.memory.project_id}:{self.memory.user_id}:{memory_id}"

    @staticmethod
    def _zero_receipt(operation: str, **payload: Any) -> dict[str, Any]:
        return {
            "schema_version": ZeroTokenMemoryEngine.schema_version,
            "operation": operation,
            "provider_calls": 0,
            "provider_input_tokens": 0,
            "provider_output_tokens": 0,
            "provider_tokens": 0,
            **payload,
        }

    def ingest(
        self,
        memory_class: str,
        text: str,
        *,
        confidence: float = 1.0,
        provenance: dict[str, Any] | None = None,
        expires_at: float | None = None,
        tags: Iterable[str] = (),
        evidence_data: bytes | None = None,
        evidence_handle: str | None = None,
        evidence_kind: str = "memory-evidence",
        evidence_metadata: dict[str, Any] | None = None,
        evidence_ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        if evidence_data is not None and evidence_handle is not None:
            raise ValueError("provide evidence_data or evidence_handle, not both")

        record = self.memory.add(
            memory_class,
            text,
            confidence=confidence,
            provenance=provenance,
            expires_at=expires_at,
            tags=tags,
        )
        reference = self._reference(record.memory_id)
        handle = evidence_handle
        retained = False

        if evidence_data is not None:
            ttl_seconds = evidence_ttl_seconds
            if ttl_seconds is None and expires_at is not None:
                ttl_seconds = max(1, int(math.ceil(float(expires_at) - time.time())))
            handle = self.evidence.put(
                bytes(evidence_data),
                kind=evidence_kind,
                metadata=evidence_metadata,
                ttl_seconds=ttl_seconds,
                reference=reference,
            )
            retained = True
        elif handle is not None:
            retained = self.evidence.retain(handle, reference)

        if handle is not None:
            try:
                record = self.memory.attach_evidence(record.memory_id, handle)
            except Exception:
                if retained:
                    self.evidence.release(handle, reference)
                raise

        return self._zero_receipt(
            "ingest",
            record=asdict(record),
            evidence_handle=handle,
            evidence_reference=reference if handle is not None else None,
        )

    def link(self, source_id: str, relation: str, target_id: str, *, weight: float = 1.0) -> dict[str, Any]:
        self.memory.link(source_id, relation, target_id, weight=weight)
        return self._zero_receipt(
            "link",
            source_id=source_id,
            relation=relation.strip(),
            target_id=target_id,
            weight=float(weight),
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        memory_classes: Iterable[str] = (),
        include_superseded: bool = False,
        include_expired: bool = False,
    ) -> dict[str, Any]:
        result = self.memory.search(
            query,
            limit=limit,
            memory_classes=memory_classes,
            include_superseded=include_superseded,
            include_expired=include_expired,
        )
        return self._zero_receipt("search", **result)

    @staticmethod
    def _evidence_handles(record: MemoryRecord) -> tuple[str, ...]:
        values = record.provenance.get("evidence_handles", ())
        if not isinstance(values, (list, tuple)):
            return ()
        return tuple(
            sorted(
                {
                    value
                    for value in values
                    if isinstance(value, str) and value.startswith("sc://sha256/")
                }
            )
        )

    def maintain(
        self,
        *,
        now: float | None = None,
        limit: int = 1000,
        force_reindex: bool = False,
        evidence_gc_ttl_seconds: float | None = None,
        max_delete_bytes: int | None = None,
    ) -> dict[str, Any]:
        cutoff = time.time() if now is None else float(now)
        expired = self.memory.expired_records(now=cutoff, limit=limit)

        # Preflight evidence metadata before changing authoritative memory. A bad
        # handle fails closed while all memory/evidence references are intact.
        for record in expired:
            for handle in self._evidence_handles(record):
                self.evidence.describe(handle)

        purge = self.memory.purge_expired(now=cutoff, limit=limit)
        deleted_ids = set(purge["memory_ids"])

        released = 0
        release_attempts = 0
        for record in expired:
            if record.memory_id not in deleted_ids:
                continue
            reference = self._reference(record.memory_id)
            for handle in self._evidence_handles(record):
                release_attempts += 1
                if self.evidence.release(handle, reference):
                    released += 1

        gc = self.evidence.gc(
            now=cutoff,
            ttl_seconds=evidence_gc_ttl_seconds,
            max_delete_bytes=max_delete_bytes,
            dry_run=False,
            limit=limit,
        )
        reindex = (
            self.memory.rebuild_index()
            if force_reindex
            else {
                "ok": True,
                "performed": False,
                "mode": "LAZY_NOOP_INDEX_IN_SYNC",
                "indexed": 0,
                "provider_calls": 0,
                "provider_tokens": 0,
            }
        )
        return self._zero_receipt(
            "maintain",
            expired_candidates=len(expired),
            memory_deleted=int(purge["deleted"]),
            reactivated_superseded=int(purge.get("reactivated_superseded", 0)),
            evidence_release_attempts=release_attempts,
            evidence_references_released=released,
            evidence_gc=gc,
            reindex=reindex,
        )

    def rebuild_index(self) -> dict[str, Any]:
        return self._zero_receipt("rebuild_index", reindex=self.memory.rebuild_index())
