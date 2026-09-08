from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .usage_receipt_ledger import normalize_provider_usage


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _cleared_input_tokens(response: Mapping[str, Any] | str) -> int:
    if not isinstance(response, Mapping):
        return 0
    context_management = response.get("context_management")
    if not isinstance(context_management, Mapping):
        return 0
    edits = context_management.get("applied_edits")
    if not isinstance(edits, list):
        return 0
    total = 0
    for item in edits:
        if not isinstance(item, Mapping):
            continue
        value = item.get("cleared_input_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            total += value
    return total


@dataclass(frozen=True)
class ProviderCallObservation:
    sequence: int
    task_id: str
    arm_id: str
    repetition: int
    round_number: int
    provider: str
    model: str
    provider_observed: bool
    fresh_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    locally_counted_input_tokens: int
    counting_method: str
    context_hash: str
    response_hash: str
    response_id_hash: str
    elapsed_ms: int
    created_at: float
    cleared_input_tokens: int = 0

    @property
    def provider_total_tokens(self) -> int:
        # Cleared tokens are a provider context-editing diagnostic, not billed work.
        return self.fresh_input_tokens + self.cached_input_tokens + self.output_tokens + self.reasoning_tokens


class ProviderCallObservationLedger:
    """Truthful per-call telemetry that never upgrades local estimates into billed proof.

    This ledger is deliberately weaker than UsageReceiptLedger: missing provider usage,
    response IDs or prices stay missing. It provides the bridge from runtime integration
    to paired provider-billed certification without fabricating cost evidence.
    """

    schema_version = 2

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS provider_call_observations(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    arm_id TEXT NOT NULL,
                    repetition INTEGER NOT NULL,
                    round_number INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider_observed INTEGER NOT NULL,
                    fresh_input_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    cleared_input_tokens INTEGER NOT NULL DEFAULT 0,
                    locally_counted_input_tokens INTEGER NOT NULL,
                    counting_method TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    response_hash TEXT NOT NULL,
                    response_id_hash TEXT NOT NULL,
                    elapsed_ms INTEGER NOT NULL,
                    raw_usage_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(task_id,arm_id,repetition,round_number)
                );
                CREATE INDEX IF NOT EXISTS provider_call_observations_task_idx
                    ON provider_call_observations(task_id,arm_id,repetition);

                CREATE TABLE IF NOT EXISTS provider_task_outcomes(
                    task_id TEXT NOT NULL,
                    arm_id TEXT NOT NULL,
                    repetition INTEGER NOT NULL,
                    verifier_ok INTEGER NOT NULL,
                    delivery_ok INTEGER NOT NULL,
                    verification_complete INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(task_id,arm_id,repetition)
                );
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(provider_call_observations)").fetchall()
            }
            if "cleared_input_tokens" not in columns:
                db.execute(
                    "ALTER TABLE provider_call_observations "
                    "ADD COLUMN cleared_input_tokens INTEGER NOT NULL DEFAULT 0"
                )

    def record_call(
        self,
        *,
        task_id: str,
        arm_id: str,
        repetition: int,
        round_number: int,
        provider: str,
        model: str,
        usage: Mapping[str, Any],
        response_id: str,
        response: Mapping[str, Any] | str,
        locally_counted_input_tokens: int,
        counting_method: str,
        context_hash: str,
        elapsed_ms: int,
    ) -> ProviderCallObservation:
        if not task_id or not arm_id or repetition <= 0 or round_number <= 0:
            raise ValueError("provider observation identity is incomplete")
        fresh = cached = output = reasoning = 0
        provider_observed = False
        try:
            normalized = normalize_provider_usage(provider, usage)
            fresh = normalized.fresh_input_tokens
            cached = normalized.cached_input_tokens
            output = normalized.output_tokens
            reasoning = normalized.reasoning_tokens
            provider_observed = bool(response_id) and normalized.total_tokens > 0
        except ValueError:
            provider_observed = False
        cleared = _cleared_input_tokens(response)
        raw_usage_json = json.dumps(usage, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        response_hash = _sha256(_canonical(response))
        response_id_hash = _sha256(response_id.encode("utf-8")) if response_id else ""
        created_at = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                INSERT OR REPLACE INTO provider_call_observations(
                    task_id,arm_id,repetition,round_number,provider,model,provider_observed,
                    fresh_input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,cleared_input_tokens,
                    locally_counted_input_tokens,counting_method,context_hash,response_hash,
                    response_id_hash,elapsed_ms,raw_usage_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id, arm_id, int(repetition), int(round_number), str(provider), str(model), int(provider_observed),
                    fresh, cached, output, reasoning, cleared, max(0, int(locally_counted_input_tokens)), str(counting_method),
                    str(context_hash), response_hash, response_id_hash, max(0, int(elapsed_ms)), raw_usage_json, created_at,
                ),
            )
            sequence = int(cursor.lastrowid)
            db.commit()
        return ProviderCallObservation(
            sequence=sequence,
            task_id=task_id,
            arm_id=arm_id,
            repetition=int(repetition),
            round_number=int(round_number),
            provider=str(provider),
            model=str(model),
            provider_observed=provider_observed,
            fresh_input_tokens=fresh,
            cached_input_tokens=cached,
            output_tokens=output,
            reasoning_tokens=reasoning,
            locally_counted_input_tokens=max(0, int(locally_counted_input_tokens)),
            counting_method=str(counting_method),
            context_hash=str(context_hash),
            response_hash=response_hash,
            response_id_hash=response_id_hash,
            elapsed_ms=max(0, int(elapsed_ms)),
            created_at=created_at,
            cleared_input_tokens=cleared,
        )

    def record_outcome(
        self,
        *,
        task_id: str,
        arm_id: str,
        repetition: int,
        verifier_ok: bool,
        delivery_ok: bool,
        verification_complete: bool,
    ) -> None:
        with self._db() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO provider_task_outcomes(
                    task_id,arm_id,repetition,verifier_ok,delivery_ok,verification_complete,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    task_id, arm_id, int(repetition), int(bool(verifier_ok)), int(bool(delivery_ok)),
                    int(bool(verification_complete)), time.time(),
                ),
            )

    def observations(self, *, task_id: str, arm_id: str | None = None) -> list[ProviderCallObservation]:
        query = "SELECT * FROM provider_call_observations WHERE task_id=?"
        params: list[Any] = [task_id]
        if arm_id is not None:
            query += " AND arm_id=?"
            params.append(arm_id)
        query += " ORDER BY repetition,round_number"
        with self._db() as db:
            rows = db.execute(query, params).fetchall()
        return [
            ProviderCallObservation(
                sequence=int(row["sequence"]),
                task_id=str(row["task_id"]),
                arm_id=str(row["arm_id"]),
                repetition=int(row["repetition"]),
                round_number=int(row["round_number"]),
                provider=str(row["provider"]),
                model=str(row["model"]),
                provider_observed=bool(row["provider_observed"]),
                fresh_input_tokens=int(row["fresh_input_tokens"]),
                cached_input_tokens=int(row["cached_input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                reasoning_tokens=int(row["reasoning_tokens"]),
                locally_counted_input_tokens=int(row["locally_counted_input_tokens"]),
                counting_method=str(row["counting_method"]),
                context_hash=str(row["context_hash"]),
                response_hash=str(row["response_hash"]),
                response_id_hash=str(row["response_id_hash"]),
                elapsed_ms=int(row["elapsed_ms"]),
                created_at=float(row["created_at"]),
                cleared_input_tokens=int(row["cleared_input_tokens"]),
            )
            for row in rows
        ]

    def summary(self, *, task_id: str, arm_id: str | None = None) -> dict[str, Any]:
        rows = self.observations(task_id=task_id, arm_id=arm_id)
        provider_observed_calls = sum(1 for row in rows if row.provider_observed)
        return {
            "schema_version": self.schema_version,
            "task_id": task_id,
            "arm_id": arm_id,
            "calls": len(rows),
            "provider_observed_calls": provider_observed_calls,
            "provider_proof_complete": bool(rows) and provider_observed_calls == len(rows),
            "fresh_input_tokens": sum(row.fresh_input_tokens for row in rows),
            "cached_input_tokens": sum(row.cached_input_tokens for row in rows),
            "output_tokens": sum(row.output_tokens for row in rows),
            "reasoning_tokens": sum(row.reasoning_tokens for row in rows),
            "cleared_input_tokens": sum(row.cleared_input_tokens for row in rows),
            "locally_counted_input_tokens": sum(row.locally_counted_input_tokens for row in rows),
            "elapsed_ms": sum(row.elapsed_ms for row in rows),
            "counting_methods": sorted({row.counting_method for row in rows}),
        }

    def paired_token_comparison(self, *, task_id: str, baseline_arm: str, candidate_arm: str) -> dict[str, Any]:
        baseline = self.summary(task_id=task_id, arm_id=baseline_arm)
        candidate = self.summary(task_id=task_id, arm_id=candidate_arm)
        if not baseline["provider_proof_complete"] or not candidate["provider_proof_complete"]:
            raise ValueError("paired provider comparison requires complete provider-observed usage on both arms")
        b_total = baseline["fresh_input_tokens"] + baseline["cached_input_tokens"] + baseline["output_tokens"] + baseline["reasoning_tokens"]
        c_total = candidate["fresh_input_tokens"] + candidate["cached_input_tokens"] + candidate["output_tokens"] + candidate["reasoning_tokens"]
        return {
            "task_id": task_id,
            "baseline_arm": baseline_arm,
            "candidate_arm": candidate_arm,
            "baseline_provider_tokens": b_total,
            "candidate_provider_tokens": c_total,
            "provider_token_reduction_ratio": 0.0 if b_total <= 0 else 1.0 - c_total / b_total,
            "claim_level": "PAIRED_PROVIDER_OBSERVED_TOKENS",
        }


__all__ = ["ProviderCallObservation", "ProviderCallObservationLedger"]
