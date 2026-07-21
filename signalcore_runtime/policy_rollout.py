from __future__ import annotations

import hashlib
import hmac
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .state import StateDB
from .util import canonical_json, sha256_bytes


class RolloutError(RuntimeError):
    pass


_STAGES = ("shadow", "canary", "staged", "full", "quarantined", "rolled-back")


@dataclass(frozen=True)
class VerifiedPolicyObservation:
    scope: str
    policy_hash: str
    verifier_hash: str
    success: bool
    quality: float
    latency_ms: float
    cost: float = 0.0
    security_regressions: int = 0
    timestamp: float = 0.0
    receipt_signature: str = ""

    def payload(self) -> bytes:
        value = asdict(self)
        value["receipt_signature"] = ""
        return canonical_json(value)


@dataclass(frozen=True)
class RolloutDecision:
    scope: str
    policy_hash: str
    stage: str
    sample_count: int
    success_lower_bound: float
    mean_quality: float
    p95_latency_ms: float
    mean_cost: float
    drift_score: float
    security_regressions: int
    eligible: bool
    reasons: tuple[str, ...]


class PolicyRolloutManager:
    """Verifier-bound shadow/canary/staged/full rollout with automatic rollback."""

    def __init__(self, path: Path, *, signing_key: bytes | None = None):
        self.state = StateDB(path)
        self.signing_key = bytes(signing_key) if signing_key else None
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS verified_policy_observations(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    verifier_hash TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    quality REAL NOT NULL,
                    latency_ms REAL NOT NULL,
                    cost REAL NOT NULL,
                    security_regressions INTEGER NOT NULL,
                    observed_at REAL NOT NULL,
                    receipt_signature TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS verified_policy_scope_idx
                    ON verified_policy_observations(scope,policy_hash,sequence);
                CREATE TABLE IF NOT EXISTS policy_rollouts(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    percentage REAL NOT NULL,
                    previous_policy_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS policy_rollouts_scope_idx ON policy_rollouts(scope,sequence);
                CREATE TABLE IF NOT EXISTS policy_quarantine(
                    scope TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(scope,policy_hash)
                );
                """
            )

    def sign(self, observation: VerifiedPolicyObservation) -> str:
        if not self.signing_key:
            raise RolloutError("policy observation signing key is unavailable")
        return hmac.new(self.signing_key, observation.payload(), hashlib.sha256).hexdigest()

    def record(self, observation: VerifiedPolicyObservation, *, require_signature: bool = True) -> int:
        if not observation.scope or len(observation.policy_hash) != 64 or len(observation.verifier_hash) != 64:
            raise RolloutError("invalid policy/verifier identity")
        if not 0.0 <= observation.quality <= 1.0 or observation.latency_ms < 0 or observation.cost < 0:
            raise RolloutError("invalid observation metrics")
        if observation.security_regressions < 0:
            raise RolloutError("invalid security regression count")
        if require_signature:
            if not self.signing_key or not observation.receipt_signature:
                raise RolloutError("verifier-bound observation signature required")
            expected = self.sign(observation)
            if not hmac.compare_digest(expected, observation.receipt_signature):
                raise RolloutError("policy observation signature is invalid")
        observed = float(observation.timestamp or time.time())
        receipt_hash = sha256_bytes(observation.payload() + observation.receipt_signature.encode("ascii"))
        with self.state.transaction(immediate=True) as db:
            cursor = db.execute(
                "INSERT INTO verified_policy_observations(scope,policy_hash,verifier_hash,success,quality,latency_ms,cost,security_regressions,observed_at,receipt_signature,receipt_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    observation.scope, observation.policy_hash, observation.verifier_hash,
                    int(observation.success), observation.quality, observation.latency_ms,
                    observation.cost, observation.security_regressions, observed,
                    observation.receipt_signature, receipt_hash,
                ),
            )
            return int(cursor.lastrowid)

    def evaluate(
        self,
        scope: str,
        policy_hash: str,
        *,
        window: int = 200,
        minimum_samples: int = 30,
        success_floor: float = 0.95,
        quality_floor: float = 0.90,
        latency_ceiling_ms: float = math.inf,
        cost_ceiling: float = math.inf,
        drift_threshold: float = 0.20,
    ) -> RolloutDecision:
        with self.state.read() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM verified_policy_observations WHERE scope=? AND policy_hash=? ORDER BY sequence DESC LIMIT ?",
                (scope, policy_hash, max(1, min(window, 10000))),
            )]
            quarantined = db.execute(
                "SELECT reason FROM policy_quarantine WHERE scope=? AND policy_hash=?",
                (scope, policy_hash),
            ).fetchone()
        rows.reverse()
        successes = sum(int(row["success"]) for row in rows)
        lower = self._wilson(successes, len(rows))
        qualities = [float(row["quality"]) for row in rows]
        latencies = [float(row["latency_ms"]) for row in rows]
        costs = [float(row["cost"]) for row in rows]
        security = sum(int(row["security_regressions"]) for row in rows)
        drift = self._drift(qualities)
        reasons: list[str] = []
        if len(rows) < minimum_samples: reasons.append("insufficient-samples")
        if lower < success_floor: reasons.append("success-lower-bound")
        if (statistics.fmean(qualities) if qualities else 0.0) < quality_floor: reasons.append("quality-floor")
        if self._percentile(latencies, 0.95) > latency_ceiling_ms: reasons.append("latency-ceiling")
        if (statistics.fmean(costs) if costs else 0.0) > cost_ceiling: reasons.append("cost-ceiling")
        if drift > drift_threshold: reasons.append("workload-drift")
        if security: reasons.append("security-regression")
        if quarantined: reasons.append("permanent-quarantine")
        stage = self.active(scope).get("stage", "shadow")
        return RolloutDecision(
            scope, policy_hash, stage, len(rows), lower,
            statistics.fmean(qualities) if qualities else 0.0,
            self._percentile(latencies, 0.95), statistics.fmean(costs) if costs else 0.0,
            drift, security, not reasons, tuple(reasons),
        )

    def promote(
        self,
        decision: RolloutDecision,
        *,
        target_stage: str,
        percentage: float | None = None,
        cooldown_seconds: float = 300.0,
    ) -> int:
        if target_stage not in _STAGES or target_stage in {"quarantined", "rolled-back"}:
            raise RolloutError("invalid promotion target")
        if not decision.eligible:
            raise RolloutError("policy is not eligible for promotion: " + ",".join(decision.reasons))
        expected_order = {"shadow": 0, "canary": 1, "staged": 2, "full": 3}
        current = self.active(decision.scope)
        current_stage = str(current.get("stage") or "shadow")
        if target_stage != current_stage and expected_order[target_stage] > expected_order.get(current_stage, 0) + 1:
            raise RolloutError("policy promotion cannot skip rollout stages")
        if current and time.time() - float(current.get("created_at") or 0) < cooldown_seconds:
            raise RolloutError("policy rollout cooldown is active")
        default_percentage = {"shadow": 0.0, "canary": 0.05, "staged": 0.25, "full": 1.0}[target_stage]
        selected_percentage = default_percentage if percentage is None else float(percentage)
        if not 0.0 <= selected_percentage <= 1.0:
            raise RolloutError("rollout percentage must be between 0 and 1")
        previous = str(current.get("policy_hash") or "")
        with self.state.transaction(immediate=True) as db:
            cursor = db.execute(
                "INSERT INTO policy_rollouts(scope,policy_hash,stage,percentage,previous_policy_hash,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                (decision.scope, decision.policy_hash, target_stage, selected_percentage, previous, "verified-promotion", time.time()),
            )
            return int(cursor.lastrowid)

    def observe_and_auto_rollback(self, decision: RolloutDecision) -> dict[str, Any]:
        current = self.active(decision.scope)
        if not current or str(current.get("policy_hash")) != decision.policy_hash:
            return {"rolled_back": False, "reason": "policy-not-active"}
        if decision.security_regressions:
            self.quarantine(decision.scope, decision.policy_hash, "security-regression")
            return self.rollback(decision.scope, reason="security-regression")
        if not decision.eligible and decision.sample_count >= 10:
            return self.rollback(decision.scope, reason=",".join(decision.reasons))
        return {"rolled_back": False, "reason": "healthy"}

    def rollback(self, scope: str, *, reason: str) -> dict[str, Any]:
        current = self.active(scope)
        if not current:
            return {"rolled_back": False, "reason": "no-active-policy"}
        previous = str(current.get("previous_policy_hash") or "")
        with self.state.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO policy_rollouts(scope,policy_hash,stage,percentage,previous_policy_hash,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                (scope, previous, "rolled-back", 0.0, str(current.get("policy_hash") or ""), reason[:512], time.time()),
            )
        return {"rolled_back": True, "from": current.get("policy_hash"), "to": previous, "reason": reason}

    def quarantine(self, scope: str, policy_hash: str, reason: str) -> None:
        with self.state.transaction(immediate=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO policy_quarantine(scope,policy_hash,reason,created_at) VALUES(?,?,?,?)",
                (scope, policy_hash, reason[:512], time.time()),
            )

    def active(self, scope: str) -> dict[str, Any]:
        with self.state.read() as db:
            row = db.execute("SELECT * FROM policy_rollouts WHERE scope=? ORDER BY sequence DESC LIMIT 1", (scope,)).fetchone()
            return dict(row) if row else {}

    @staticmethod
    def _wilson(successes: int, total: int, z: float = 1.96) -> float:
        if total <= 0:
            return 0.0
        proportion = successes / total
        denominator = 1 + z * z / total
        centre = proportion + z * z / (2 * total)
        margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total)
        return max(0.0, (centre - margin) / denominator)

    @staticmethod
    def _percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))]

    @staticmethod
    def _drift(values: list[float]) -> float:
        if len(values) < 10:
            return 0.0
        midpoint = len(values) // 2
        earlier = statistics.fmean(values[:midpoint])
        later = statistics.fmean(values[midpoint:])
        return abs(later - earlier)
