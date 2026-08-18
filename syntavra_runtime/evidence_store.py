from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .platform_common import _connect
from .secret_redaction import SecretRedactor
from .universal_context_item import UniversalContextItem


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_projection(item: UniversalContextItem) -> dict[str, Any]:
    body = item.to_dict()
    return {
        "schema_version": body["schema_version"],
        "item_id": body["item_id"],
        "kind": body["kind"],
        "representation": body["representation"],
        "content": body["content"],
        "content_sha256": body["content_sha256"],
        "provenance": body["provenance"],
        "recovery": body["recovery"],
        "metadata": body["metadata"],
    }


def _evaluation_projection(item: UniversalContextItem) -> dict[str, Any]:
    body = item.to_dict()
    return {"trust": body["trust"], "freshness": body["freshness"]}


class EvidenceStoreV2:
    """Content-addressed UniversalContextItem store with lineage and audit journal.

    Stable evidence identity is immutable. Trust/freshness are mutable evaluation
    layers and updates are journaled rather than silently replacing evidence.
    Secret-bearing items are rejected by default instead of being mutated in place.
    """

    def __init__(self, path: Path, *, secret_policy: str = "reject"):
        if secret_policy not in {"reject", "allow-pre-redacted"}:
            raise ValueError(f"unsupported secret policy: {secret_policy!r}")
        self.path = path
        self.secret_policy = secret_policy
        self._redactor = SecretRedactor()
        with _connect(path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_items (
                    item_id TEXT PRIMARY KEY,
                    content_sha256 TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    representation TEXT NOT NULL,
                    stable_json TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS evidence_lineage (
                    parent_item_id TEXT NOT NULL,
                    child_item_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(parent_item_id, child_item_id, relation),
                    FOREIGN KEY(child_item_id) REFERENCES evidence_items(item_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_lineage_child
                    ON evidence_lineage(child_item_id, relation);
                CREATE INDEX IF NOT EXISTS idx_evidence_lineage_parent
                    ON evidence_lineage(parent_item_id, relation);
                CREATE INDEX IF NOT EXISTS idx_evidence_expiry
                    ON evidence_items(expires_at, pinned);
                CREATE TABLE IF NOT EXISTS evidence_journal (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_hash TEXT NOT NULL UNIQUE,
                    previous_hash TEXT,
                    action TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )

    def _secret_receipt(self, item: UniversalContextItem) -> dict[str, Any]:
        _redacted, receipt = self._redactor.redact(item.to_dict())
        if receipt["redacted"] and self.secret_policy == "reject":
            raise ValueError(
                "evidence item contains secret-like material; create an explicit sanitized UniversalContextItem before storage"
            )
        return receipt

    def _journal(
        self,
        db: Any,
        *,
        action: str,
        item_id: str,
        details: Mapping[str, Any] | None = None,
        actor: str = "evidence-store-v2",
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        timestamp = observed_at or _now()
        previous = db.execute(
            "SELECT event_hash FROM evidence_journal ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous[0]) if previous else None
        body = {
            "previous_hash": previous_hash,
            "action": action,
            "item_id": item_id,
            "observed_at": timestamp,
            "actor": actor,
            "details": dict(details or {}),
        }
        event_hash = _sha256(body)
        db.execute(
            """INSERT INTO evidence_journal
               (event_hash,previous_hash,action,item_id,observed_at,actor,details_json)
               VALUES(?,?,?,?,?,?,?)""",
            (
                event_hash,
                previous_hash,
                action,
                item_id,
                timestamp,
                actor,
                _canonical(body["details"]),
            ),
        )
        return {"event_hash": event_hash, **body}

    def put(
        self,
        item: UniversalContextItem,
        *,
        expires_at: str | None = None,
        pinned: bool = False,
        actor: str = "evidence-store-v2",
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        if not item.verify_integrity():
            raise ValueError("UniversalContextItem integrity check failed")
        secret_receipt = self._secret_receipt(item)
        stable = _stable_projection(item)
        evaluation = _evaluation_projection(item)
        stable_json = _canonical(stable)
        evaluation_json = _canonical(evaluation)
        timestamp = observed_at or _now()

        with _connect(self.path) as db:
            existing = db.execute(
                "SELECT stable_json,evaluation_json,pinned,expires_at FROM evidence_items WHERE item_id = ?",
                (item.item_id,),
            ).fetchone()
            if existing is None:
                db.execute(
                    """INSERT INTO evidence_items
                       (item_id,content_sha256,kind,representation,stable_json,evaluation_json,created_at,updated_at,expires_at,pinned)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item.item_id,
                        item.content_sha256,
                        item.kind,
                        item.representation,
                        stable_json,
                        evaluation_json,
                        timestamp,
                        timestamp,
                        expires_at,
                        1 if pinned else 0,
                    ),
                )
                action = "put-new"
                details = {
                    "content_sha256": item.content_sha256,
                    "pinned": bool(pinned),
                    "expires_at": expires_at,
                    "secret_scan": secret_receipt,
                }
            else:
                if str(existing["stable_json"]) != stable_json:
                    raise ValueError("stable evidence identity collision: same item_id has different immutable payload")
                evaluation_changed = str(existing["evaluation_json"]) != evaluation_json
                new_pinned = bool(existing["pinned"]) or bool(pinned)
                new_expiry = expires_at if expires_at is not None else existing["expires_at"]
                db.execute(
                    """UPDATE evidence_items
                       SET evaluation_json = ?, updated_at = ?, expires_at = ?, pinned = ?
                       WHERE item_id = ?""",
                    (evaluation_json, timestamp, new_expiry, 1 if new_pinned else 0, item.item_id),
                )
                action = "evaluation-update" if evaluation_changed else "observe-existing"
                details = {
                    "evaluation_changed": evaluation_changed,
                    "pinned": new_pinned,
                    "expires_at": new_expiry,
                    "secret_scan": secret_receipt,
                }

            for parent_id in item.provenance.parent_item_ids:
                db.execute(
                    """INSERT OR IGNORE INTO evidence_lineage
                       (parent_item_id,child_item_id,relation,created_at)
                       VALUES(?,?,?,?)""",
                    (parent_id, item.item_id, "DERIVED_FROM", timestamp),
                )

            event = self._journal(
                db,
                action=action,
                item_id=item.item_id,
                details=details,
                actor=actor,
                observed_at=timestamp,
            )
        return {
            "ok": True,
            "action": action,
            "item_id": item.item_id,
            "content_sha256": item.content_sha256,
            "journal_event": event["event_hash"],
        }

    def get(self, item_id: str) -> UniversalContextItem | None:
        with _connect(self.path) as db:
            row = db.execute(
                "SELECT stable_json,evaluation_json FROM evidence_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        if row is None:
            return None
        stable = json.loads(str(row["stable_json"]))
        evaluation = json.loads(str(row["evaluation_json"]))
        payload = {**stable, **evaluation}
        item = UniversalContextItem.from_dict(payload)
        if not item.verify_integrity():
            raise ValueError("stored UniversalContextItem failed integrity verification")
        return item

    def require(self, item_id: str) -> UniversalContextItem:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        return item

    def lineage(self, item_id: str, *, direction: str = "parents") -> list[dict[str, Any]]:
        if direction not in {"parents", "children"}:
            raise ValueError("direction must be parents or children")
        if direction == "parents":
            sql = "SELECT parent_item_id AS item_id, relation, created_at FROM evidence_lineage WHERE child_item_id = ? ORDER BY parent_item_id, relation"
        else:
            sql = "SELECT child_item_id AS item_id, relation, created_at FROM evidence_lineage WHERE parent_item_id = ? ORDER BY child_item_id, relation"
        with _connect(self.path) as db:
            return [dict(row) for row in db.execute(sql, (item_id,)).fetchall()]

    def pin(self, item_id: str, *, pinned: bool = True, actor: str = "evidence-store-v2") -> dict[str, Any]:
        timestamp = _now()
        with _connect(self.path) as db:
            exists = db.execute("SELECT 1 FROM evidence_items WHERE item_id = ?", (item_id,)).fetchone()
            if exists is None:
                raise KeyError(item_id)
            db.execute(
                "UPDATE evidence_items SET pinned = ?, updated_at = ? WHERE item_id = ?",
                (1 if pinned else 0, timestamp, item_id),
            )
            event = self._journal(
                db,
                action="pin" if pinned else "unpin",
                item_id=item_id,
                details={"pinned": bool(pinned)},
                actor=actor,
                observed_at=timestamp,
            )
        return {"ok": True, "item_id": item_id, "pinned": bool(pinned), "journal_event": event["event_hash"]}

    def set_expiry(self, item_id: str, expires_at: str | None, *, actor: str = "evidence-store-v2") -> dict[str, Any]:
        timestamp = _now()
        with _connect(self.path) as db:
            exists = db.execute("SELECT 1 FROM evidence_items WHERE item_id = ?", (item_id,)).fetchone()
            if exists is None:
                raise KeyError(item_id)
            db.execute(
                "UPDATE evidence_items SET expires_at = ?, updated_at = ? WHERE item_id = ?",
                (expires_at, timestamp, item_id),
            )
            event = self._journal(
                db,
                action="retention-update",
                item_id=item_id,
                details={"expires_at": expires_at},
                actor=actor,
                observed_at=timestamp,
            )
        return {"ok": True, "item_id": item_id, "expires_at": expires_at, "journal_event": event["event_hash"]}

    def prune_expired(self, *, before: str | None = None, actor: str = "evidence-store-v2") -> dict[str, Any]:
        cutoff = before or _now()
        with _connect(self.path) as db:
            rows = db.execute(
                """SELECT item_id FROM evidence_items
                   WHERE pinned = 0 AND expires_at IS NOT NULL AND expires_at <= ?
                   ORDER BY item_id""",
                (cutoff,),
            ).fetchall()
            removed = [str(row["item_id"]) for row in rows]
            for item_id in removed:
                self._journal(
                    db,
                    action="prune-expired",
                    item_id=item_id,
                    details={"cutoff": cutoff},
                    actor=actor,
                    observed_at=cutoff,
                )
                db.execute("DELETE FROM evidence_items WHERE item_id = ?", (item_id,))
        return {"ok": True, "cutoff": cutoff, "removed": removed, "count": len(removed)}

    def verify_item(self, item_id: str) -> dict[str, Any]:
        item = self.require(item_id)
        recovery = [
            {
                "kind": handle.kind,
                "exact": bool(handle.exact),
                "integrity": handle.integrity,
                "integrity_shape_ok": handle.integrity.startswith("sha256:") and len(handle.integrity) == 71,
            }
            for handle in item.recovery
        ]
        ok = item.verify_integrity() and all(row["integrity_shape_ok"] for row in recovery)
        return {
            "ok": ok,
            "item_id": item.item_id,
            "content_sha256": item.content_sha256,
            "recovery": recovery,
            "parent_count": len(item.provenance.parent_item_ids),
        }

    def verify_journal(self) -> dict[str, Any]:
        with _connect(self.path) as db:
            rows = db.execute("SELECT * FROM evidence_journal ORDER BY sequence").fetchall()
        previous_hash: str | None = None
        failures: list[int] = []
        for row in rows:
            details = json.loads(str(row["details_json"]))
            body = {
                "previous_hash": previous_hash,
                "action": str(row["action"]),
                "item_id": str(row["item_id"]),
                "observed_at": str(row["observed_at"]),
                "actor": str(row["actor"]),
                "details": details,
            }
            expected = _sha256(body)
            if row["previous_hash"] != previous_hash or row["event_hash"] != expected:
                failures.append(int(row["sequence"]))
            previous_hash = str(row["event_hash"])
        return {
            "ok": not failures,
            "events": len(rows),
            "head_hash": previous_hash,
            "failures": failures,
        }

    def journal(self, *, item_id: str | None = None) -> list[dict[str, Any]]:
        with _connect(self.path) as db:
            if item_id is None:
                rows = db.execute("SELECT * FROM evidence_journal ORDER BY sequence").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM evidence_journal WHERE item_id = ? ORDER BY sequence",
                    (item_id,),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def stats(self) -> dict[str, Any]:
        with _connect(self.path) as db:
            items = int(db.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0])
            lineage = int(db.execute("SELECT COUNT(*) FROM evidence_lineage").fetchone()[0])
            journal = int(db.execute("SELECT COUNT(*) FROM evidence_journal").fetchone()[0])
            pinned = int(db.execute("SELECT COUNT(*) FROM evidence_items WHERE pinned = 1").fetchone()[0])
        return {"ok": True, "items": items, "lineage": lineage, "journal": journal, "pinned": pinned}


__all__ = ["EvidenceStoreV2"]
