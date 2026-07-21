from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Iterator

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class PolicyObservation:
    family: str
    host: str
    model: str
    raw_bytes: int
    visible_bytes: int
    latency_ms: float
    success: bool
    quality: float = 1.0
    cache_hit: bool = False
    security_regressions: int = 0
    recorded_at: float = 0.0

    def __post_init__(self) -> None:
        if self.raw_bytes < 0 or self.visible_bytes < 0:
            raise ValueError("byte counts cannot be negative")
        if self.visible_bytes > self.raw_bytes and self.raw_bytes > 0:
            raise ValueError("visible_bytes cannot exceed raw_bytes")
        if self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if not 0.0 <= self.quality <= 1.0:
            raise ValueError("quality must be between 0 and 1")
        if self.security_regressions < 0:
            raise ValueError("security_regressions cannot be negative")


@dataclass(frozen=True)
class PolicyRecommendation:
    family: str
    host: str
    model: str
    samples: int
    output_profile: str
    budget_bytes: int
    cache_policy: str
    route: str
    canary: bool
    confidence: float
    success_rate: float
    mean_quality: float
    p95_latency_ms: float
    mean_reduction_ratio: float
    cache_hit_rate: float
    reasons: tuple[str, ...]
    policy_hash: str


class AdaptivePolicyTuner:
    """Conservative local policy tuner with explicit quality and security gates."""

    schema_version = 1

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._db() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS policy_observations(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    family TEXT NOT NULL,
                    host TEXT NOT NULL,
                    model TEXT NOT NULL,
                    raw_bytes INTEGER NOT NULL,
                    visible_bytes INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    success INTEGER NOT NULL,
                    quality REAL NOT NULL,
                    cache_hit INTEGER NOT NULL,
                    security_regressions INTEGER NOT NULL,
                    recorded_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS policy_scope_idx
                    ON policy_observations(family,host,model,sequence DESC);
                CREATE TABLE IF NOT EXISTS policy_decisions(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    promoted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS policy_decision_scope_idx
                    ON policy_decisions(scope_key,sequence DESC);
                """
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def record(self, observation: PolicyObservation) -> int:
        timestamp = observation.recorded_at or time.time()
        with self._lock:
            with self._db() as db:
                cursor = db.execute(
                    """
                    INSERT INTO policy_observations(
                        family,host,model,raw_bytes,visible_bytes,latency_ms,success,
                        quality,cache_hit,security_regressions,recorded_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        observation.family.strip().casefold() or "generic",
                        observation.host.strip().casefold() or "unknown",
                        observation.model.strip() or "unknown",
                        observation.raw_bytes,
                        observation.visible_bytes,
                        observation.latency_ms,
                        int(observation.success),
                        observation.quality,
                        int(observation.cache_hit),
                        observation.security_regressions,
                        timestamp,
                    ),
                )
                db.commit()
                return int(cursor.lastrowid)

    def recommend(
        self,
        family: str,
        *,
        host: str = "unknown",
        model: str = "unknown",
        minimum_samples: int = 12,
        window: int = 200,
        quality_floor: float = 0.98,
        success_floor: float = 0.995,
        latency_ceiling_ms: float = 250.0,
    ) -> PolicyRecommendation:
        if minimum_samples < 3 or window < minimum_samples:
            raise ValueError("invalid sample window")
        scope = (
            family.strip().casefold() or "generic",
            host.strip().casefold() or "unknown",
            model.strip() or "unknown",
        )
        with self._db() as db:
            rows = db.execute(
                """
                SELECT * FROM policy_observations
                WHERE family=? AND host=? AND model=?
                ORDER BY sequence DESC LIMIT ?
                """,
                (*scope, window),
            ).fetchall()
        observations = list(reversed(rows))
        samples = len(observations)
        if not observations:
            success_rate, mean_quality, mean_reduction, cache_rate, p95 = 0.0, 1.0, 0.0, 0.0, 0.0
        else:
            success_rate = fmean(float(row["success"]) for row in observations)
            mean_quality = fmean(float(row["quality"]) for row in observations)
            reductions = [
                max(0.0, 1.0 - (int(row["visible_bytes"]) / max(1, int(row["raw_bytes"]))))
                for row in observations
            ]
            mean_reduction = fmean(reductions)
            cache_rate = fmean(float(row["cache_hit"]) for row in observations)
            latencies = sorted(float(row["latency_ms"]) for row in observations)
            p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)]
        regressions = sum(int(row["security_regressions"]) for row in observations)

        reasons: list[str] = []
        output_profile = "balanced"
        budget_bytes = 12_000
        cache_policy = "auto"
        route = "exact-capture"
        canary = False

        if samples < minimum_samples:
            reasons.append(f"insufficient-samples:{samples}/{minimum_samples}")
        elif regressions:
            reasons.append(f"security-regression:{regressions}")
            cache_policy = "off"
            route = "sandbox-exact-capture"
        elif success_rate < success_floor:
            reasons.append(f"success-below-floor:{success_rate:.4f}")
            route = "sandbox-exact-capture"
        elif mean_quality < quality_floor:
            reasons.append(f"quality-below-floor:{mean_quality:.4f}")
        elif p95 > latency_ceiling_ms:
            reasons.append(f"latency-above-ceiling:{p95:.2f}")
            budget_bytes = 8192
        else:
            canary = True
            if mean_reduction >= 0.85 and mean_quality >= 0.995:
                output_profile = "terse" if scope[0] in {"logs", "test", "build", "rag"} else "compact"
                budget_bytes = 4096 if output_profile == "compact" else 2048
                reasons.append("high-reduction-with-quality-headroom")
            elif mean_reduction >= 0.60:
                output_profile = "compact"
                budget_bytes = 6144
                reasons.append("stable-compaction-benefit")
            else:
                reasons.append("limited-compaction-benefit")
            if cache_rate >= 0.35:
                cache_policy = "read-write"
                reasons.append("sustained-cache-hits")
            route = "specialized-router"

        confidence = min(1.0, samples / max(minimum_samples * 3, 1))
        if regressions or success_rate < success_floor or mean_quality < quality_floor:
            confidence *= 0.5
        payload = {
            "schema_version": self.schema_version,
            "scope": scope,
            "samples": samples,
            "output_profile": output_profile,
            "budget_bytes": budget_bytes,
            "cache_policy": cache_policy,
            "route": route,
            "canary": canary,
            "confidence": round(confidence, 8),
            "metrics": {
                "success_rate": success_rate,
                "mean_quality": mean_quality,
                "p95_latency_ms": p95,
                "mean_reduction_ratio": mean_reduction,
                "cache_hit_rate": cache_rate,
            },
            "reasons": reasons,
        }
        policy_hash = sha256_bytes(canonical_json(payload))
        return PolicyRecommendation(
            family=scope[0], host=scope[1], model=scope[2], samples=samples,
            output_profile=output_profile, budget_bytes=budget_bytes,
            cache_policy=cache_policy, route=route, canary=canary,
            confidence=confidence, success_rate=success_rate, mean_quality=mean_quality,
            p95_latency_ms=p95, mean_reduction_ratio=mean_reduction,
            cache_hit_rate=cache_rate, reasons=tuple(reasons), policy_hash=policy_hash,
        )

    def stage(self, recommendation: PolicyRecommendation, *, promote: bool = False) -> int:
        payload = asdict(recommendation)
        scope_key = "|".join((recommendation.family, recommendation.host, recommendation.model))
        with self._lock:
            with self._db() as db:
                cursor = db.execute(
                    "INSERT INTO policy_decisions(scope_key,policy_hash,payload_json,promoted,created_at) VALUES(?,?,?,?,?)",
                    (scope_key, recommendation.policy_hash, json.dumps(payload, sort_keys=True), int(promote), time.time()),
                )
                db.commit()
                return int(cursor.lastrowid)

    def active(self, family: str, *, host: str = "unknown", model: str = "unknown") -> dict[str, Any] | None:
        scope_key = "|".join((family.strip().casefold() or "generic", host.strip().casefold() or "unknown", model.strip() or "unknown"))
        with self._db() as db:
            row = db.execute(
                "SELECT payload_json FROM policy_decisions WHERE scope_key=? AND promoted=1 ORDER BY sequence DESC LIMIT 1",
                (scope_key,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def rollback(self, family: str, *, host: str = "unknown", model: str = "unknown") -> dict[str, Any] | None:
        scope_key = "|".join((family.strip().casefold() or "generic", host.strip().casefold() or "unknown", model.strip() or "unknown"))
        with self._lock:
            with self._db() as db:
                rows = db.execute(
                    "SELECT sequence,payload_json FROM policy_decisions WHERE scope_key=? AND promoted=1 ORDER BY sequence DESC LIMIT 2",
                    (scope_key,),
                ).fetchall()
                if not rows:
                    return None
                db.execute("UPDATE policy_decisions SET promoted=0 WHERE sequence=?", (int(rows[0]["sequence"]),))
                db.commit()
        return json.loads(rows[1]["payload_json"]) if len(rows) > 1 else None

    def integrity_check(self) -> bool:
        with self._db() as db:
            row = db.execute("PRAGMA integrity_check").fetchone()
        return bool(row and str(row[0]).casefold() == "ok")


def observations_from_dicts(values: Iterable[dict[str, Any]]) -> list[PolicyObservation]:
    return [PolicyObservation(**value) for value in values]
