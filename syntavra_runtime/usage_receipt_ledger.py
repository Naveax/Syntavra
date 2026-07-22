from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .signalbench_hardened import HardenedSignalBench, UsageReceipt


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dig(value: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = value
        found = True
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                found = False
                break
            current = current[part]
        if found:
            return current
    return None


@dataclass(frozen=True)
class NormalizedUsage:
    provider: str
    fresh_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    source_fields: tuple[str, ...]

    @property
    def total_tokens(self) -> int:
        return self.fresh_input_tokens + self.cached_input_tokens + self.output_tokens + self.reasoning_tokens


@dataclass(frozen=True)
class LedgerEntry:
    sequence: int
    receipt: UsageReceipt
    previous_chain_hash: str
    chain_hash: str
    signature_mode: str
    signature: str
    created_at: float
    raw_usage_hash: str


def normalize_provider_usage(provider: str, payload: Mapping[str, Any]) -> NormalizedUsage:
    """Normalize common provider usage shapes without inventing missing values."""
    name = provider.strip().casefold()
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else payload
    if not isinstance(usage, Mapping):
        raise ValueError("provider usage payload must be an object")

    fields: list[str] = []

    def take(*paths: str, default: int = 0) -> int:
        value = _dig(usage, *paths)
        if value is not None:
            fields.append(next(path for path in paths if _dig(usage, path) is not None))
        return max(0, _int(value, default))

    cached = take(
        "input_tokens_details.cached_tokens",
        "prompt_tokens_details.cached_tokens",
        "cache_read_input_tokens",
        "cached_input_tokens",
        "cachedContentTokenCount",
    )
    raw_input = take(
        "input_tokens",
        "prompt_tokens",
        "promptTokenCount",
        "inputTokenCount",
    )
    fresh = max(0, raw_input - cached)
    output = take(
        "output_tokens",
        "completion_tokens",
        "candidatesTokenCount",
        "outputTokenCount",
    )
    reasoning = take(
        "output_tokens_details.reasoning_tokens",
        "completion_tokens_details.reasoning_tokens",
        "reasoning_tokens",
        "thoughtsTokenCount",
    )

    if not fields:
        raise ValueError("no recognized provider usage fields")
    if fresh + cached + output + reasoning <= 0:
        raise ValueError("provider usage contains no positive token counts")
    return NormalizedUsage(name or "unknown", fresh, cached, output, reasoning, tuple(dict.fromkeys(fields)))


class UsageReceiptLedger:
    """Append-only, hash-chained provider usage ledger with optional HMAC attestation."""

    schema_version = 1

    def __init__(self, path: Path, *, signing_key: bytes | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        env_key = os.environ.get("SYNTAVRA_RECEIPT_SIGNING_KEY")
        self.signing_key = signing_key if signing_key is not None else (env_key.encode("utf-8") if env_key else None)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def _db(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS usage_receipt_ledger(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    arm_id TEXT NOT NULL,
                    repetition INTEGER NOT NULL,
                    cache_mode TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    request_id_hash TEXT NOT NULL,
                    provider_response_hash TEXT NOT NULL,
                    fresh_input_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    quota_cost REAL NOT NULL,
                    hardware_hash TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL,
                    previous_chain_hash TEXT NOT NULL,
                    chain_hash TEXT NOT NULL UNIQUE,
                    signature_mode TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    raw_usage_hash TEXT NOT NULL,
                    raw_usage_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(task_id,arm_id,repetition,cache_mode,request_id_hash)
                );
                CREATE INDEX IF NOT EXISTS usage_receipt_identity_idx
                    ON usage_receipt_ledger(task_id,arm_id,repetition,cache_mode);
                """
            )

    def _sign(self, chain_hash: str) -> tuple[str, str]:
        if self.signing_key:
            signature = hmac.new(self.signing_key, chain_hash.encode("ascii"), hashlib.sha256).hexdigest()
            return "hmac-sha256", signature
        return "hash-chain-only", ""

    def record(
        self,
        *,
        task_id: str,
        arm_id: str,
        repetition: int,
        cache_mode: str,
        provider: str,
        request_id: str,
        provider_response: Mapping[str, Any],
        quota_cost: float,
        hardware_hash: str,
        usage_payload: Mapping[str, Any] | None = None,
    ) -> LedgerEntry:
        if not task_id or not arm_id or repetition <= 0 or not cache_mode:
            raise ValueError("usage receipt identity is incomplete")
        if not request_id:
            raise ValueError("provider request id is required")
        if len(hardware_hash) != 64 or any(ch not in "0123456789abcdef" for ch in hardware_hash.casefold()):
            raise ValueError("hardware hash must be lowercase sha256")
        if not math.isfinite(float(quota_cost)) or float(quota_cost) <= 0:
            raise ValueError("quota cost must be positive and finite")

        raw_usage = usage_payload or provider_response
        normalized = normalize_provider_usage(provider, raw_usage)
        request_hash = _sha256(request_id.encode("utf-8"))
        response_hash = _sha256(_canonical(provider_response))
        raw_usage_json = json.dumps(raw_usage, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        raw_usage_hash = _sha256(raw_usage_json.encode("utf-8"))
        receipt = UsageReceipt.seal(
            task_id=task_id,
            arm_id=arm_id,
            repetition=int(repetition),
            cache_mode=cache_mode,
            provider=normalized.provider,
            request_id_hash=request_hash,
            provider_response_hash=response_hash,
            fresh_input_tokens=normalized.fresh_input_tokens,
            cached_input_tokens=normalized.cached_input_tokens,
            output_tokens=normalized.output_tokens,
            reasoning_tokens=normalized.reasoning_tokens,
            quota_cost=float(quota_cost),
            hardware_hash=hardware_hash,
        )
        reasons = receipt.validate()
        if reasons:
            raise ValueError("invalid usage receipt: " + ",".join(reasons))

        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT chain_hash FROM usage_receipt_ledger ORDER BY sequence DESC LIMIT 1").fetchone()
            previous_chain = str(previous[0]) if previous else "0" * 64
            created_at = time.time()
            envelope = {
                "schema_version": self.schema_version,
                "receipt_hash": receipt.receipt_hash,
                "previous_chain_hash": previous_chain,
                "raw_usage_hash": raw_usage_hash,
                "created_at": created_at,
            }
            chain_hash = _sha256(_canonical(envelope))
            signature_mode, signature = self._sign(chain_hash)
            try:
                cursor = db.execute(
                    """
                    INSERT INTO usage_receipt_ledger(
                        task_id,arm_id,repetition,cache_mode,provider,request_id_hash,provider_response_hash,
                        fresh_input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,quota_cost,hardware_hash,
                        receipt_hash,previous_chain_hash,chain_hash,signature_mode,signature,raw_usage_hash,raw_usage_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        receipt.task_id, receipt.arm_id, receipt.repetition, receipt.cache_mode, receipt.provider,
                        receipt.request_id_hash, receipt.provider_response_hash, receipt.fresh_input_tokens,
                        receipt.cached_input_tokens, receipt.output_tokens, receipt.reasoning_tokens,
                        receipt.quota_cost, receipt.hardware_hash, receipt.receipt_hash, previous_chain,
                        chain_hash, signature_mode, signature, raw_usage_hash, raw_usage_json, created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise ValueError("duplicate or conflicting provider usage receipt") from exc
            sequence = int(cursor.lastrowid)
            db.commit()
        return LedgerEntry(sequence, receipt, previous_chain, chain_hash, signature_mode, signature, created_at, raw_usage_hash)

    def record_from_payload(self, payload: Mapping[str, Any]) -> LedgerEntry | None:
        usage = payload.get("provider_usage") or payload.get("usage")
        provider_response = payload.get("provider_response") or payload.get("response")
        if not isinstance(usage, Mapping) or not isinstance(provider_response, Mapping):
            return None
        required = ("task_id", "arm_id", "repetition", "cache_mode", "provider", "request_id", "quota_cost", "hardware_hash")
        if any(payload.get(key) in (None, "") for key in required):
            return None
        return self.record(
            task_id=str(payload["task_id"]),
            arm_id=str(payload["arm_id"]),
            repetition=int(payload["repetition"]),
            cache_mode=str(payload["cache_mode"]),
            provider=str(payload["provider"]),
            request_id=str(payload["request_id"]),
            provider_response=provider_response,
            quota_cost=float(payload["quota_cost"]),
            hardware_hash=str(payload["hardware_hash"]),
            usage_payload=usage,
        )

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> UsageReceipt:
        return UsageReceipt(
            task_id=str(row["task_id"]), arm_id=str(row["arm_id"]), repetition=int(row["repetition"]),
            cache_mode=str(row["cache_mode"]), provider=str(row["provider"]),
            request_id_hash=str(row["request_id_hash"]), provider_response_hash=str(row["provider_response_hash"]),
            fresh_input_tokens=int(row["fresh_input_tokens"]), cached_input_tokens=int(row["cached_input_tokens"]),
            output_tokens=int(row["output_tokens"]), reasoning_tokens=int(row["reasoning_tokens"]),
            quota_cost=float(row["quota_cost"]), hardware_hash=str(row["hardware_hash"]),
            receipt_hash=str(row["receipt_hash"]),
        )

    def receipts(self, *, task_id: str | None = None, arm_id: str | None = None) -> list[UsageReceipt]:
        sql = "SELECT * FROM usage_receipt_ledger WHERE 1=1"
        params: list[Any] = []
        if task_id:
            sql += " AND task_id=?"; params.append(task_id)
        if arm_id:
            sql += " AND arm_id=?"; params.append(arm_id)
        sql += " ORDER BY sequence"
        with self._db() as db:
            return [self._receipt_from_row(row) for row in db.execute(sql, params)]

    def verify(self, *, require_hmac: bool = False) -> dict[str, Any]:
        reasons: list[str] = []
        previous = "0" * 64
        count = 0
        with self._db() as db:
            rows = db.execute("SELECT * FROM usage_receipt_ledger ORDER BY sequence").fetchall()
        for expected, row in enumerate(rows, 1):
            count += 1
            if int(row["sequence"]) != expected:
                reasons.append(f"sequence-gap:{expected}->{row['sequence']}")
            if str(row["previous_chain_hash"]) != previous:
                reasons.append(f"previous-chain-mismatch:{row['sequence']}")
            receipt = self._receipt_from_row(row)
            for reason in receipt.validate():
                reasons.append(f"receipt:{row['sequence']}:{reason}")
            raw_usage_hash = _sha256(str(row["raw_usage_json"]).encode("utf-8"))
            if raw_usage_hash != str(row["raw_usage_hash"]):
                reasons.append(f"raw-usage-hash-mismatch:{row['sequence']}")
            envelope = {
                "schema_version": self.schema_version,
                "receipt_hash": receipt.receipt_hash,
                "previous_chain_hash": previous,
                "raw_usage_hash": str(row["raw_usage_hash"]),
                "created_at": float(row["created_at"]),
            }
            chain_hash = _sha256(_canonical(envelope))
            if chain_hash != str(row["chain_hash"]):
                reasons.append(f"chain-hash-mismatch:{row['sequence']}")
            mode = str(row["signature_mode"])
            if mode == "hmac-sha256":
                if not self.signing_key:
                    reasons.append(f"hmac-key-unavailable:{row['sequence']}")
                else:
                    expected_signature = hmac.new(self.signing_key, chain_hash.encode("ascii"), hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(expected_signature, str(row["signature"])):
                        reasons.append(f"signature-mismatch:{row['sequence']}")
            elif require_hmac:
                reasons.append(f"hmac-required:{row['sequence']}")
            previous = chain_hash
        return {
            "ok": not reasons,
            "entries": count,
            "last_chain_hash": previous,
            "attestation": "HMAC" if rows and all(str(row["signature_mode"]) == "hmac-sha256" for row in rows) else "HASH_CHAIN_ONLY",
            "reasons": reasons,
        }

    def compare(
        self,
        rows: list[Mapping[str, Any]],
        *,
        baseline_arm: str,
        candidate_arm: str,
        minimum_pairs: int = 10,
        require_hmac: bool = True,
    ) -> dict[str, Any]:
        verification = self.verify(require_hmac=require_hmac)
        if not verification["ok"]:
            return {
                "claimable_superiority": False,
                "claim": "NOT_PROVEN",
                "receipt_ledger_verification": verification,
                "reason": "usage-receipt-ledger-invalid",
            }
        result = HardenedSignalBench.compare(
            rows,
            baseline_arm=baseline_arm,
            candidate_arm=candidate_arm,
            receipts=self.receipts(),
            minimum_pairs=minimum_pairs,
            require_receipts=True,
        )
        result["receipt_ledger_verification"] = verification
        return result

    def export(self) -> dict[str, Any]:
        verification = self.verify()
        with self._db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM usage_receipt_ledger ORDER BY sequence")]
        for row in rows:
            row.pop("raw_usage_json", None)
        payload = {"schema_version": self.schema_version, "verification": verification, "entries": rows}
        payload["export_hash"] = _sha256(_canonical(payload))
        return payload
