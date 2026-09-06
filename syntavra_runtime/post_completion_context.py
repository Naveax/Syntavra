from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping, Sequence

from .post_completion_common import content_receipt, sha256_digest, sorted_unique


class TaskLocalContextTransaction:
    def __init__(self, base: Mapping[str, Any]):
        self._base = copy.deepcopy(dict(base))
        self._working = copy.deepcopy(dict(base))
        self._closed = False
        self._changes: list[dict[str, Any]] = []

    @property
    def working(self) -> dict[str, Any]:
        return copy.deepcopy(self._working)

    def set(self, key: str, value: Any) -> None:
        if self._closed:
            raise RuntimeError("transaction closed")
        previous = self._working.get(key)
        self._working[key] = copy.deepcopy(value)
        self._changes.append({"op": "set", "key": key, "before": previous, "after": value})

    def remove(self, key: str) -> None:
        if self._closed:
            raise RuntimeError("transaction closed")
        previous = self._working.pop(key, None)
        self._changes.append({"op": "remove", "key": key, "before": previous})

    def commit(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._closed:
            raise RuntimeError("transaction closed")
        self._closed = True
        result = copy.deepcopy(self._working)
        return result, content_receipt(
            "TASK_LOCAL_CONTEXT_TRANSACTION_V1",
            {"outcome": "COMMIT", "base_hash": sha256_digest(self._base), "result_hash": sha256_digest(result), "changes": self._changes},
        )

    def rollback(self, *, reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._closed:
            raise RuntimeError("transaction closed")
        self._closed = True
        result = copy.deepcopy(self._base)
        return result, content_receipt(
            "TASK_LOCAL_CONTEXT_TRANSACTION_V1",
            {"outcome": "ROLLBACK", "reason": str(reason), "base_hash": sha256_digest(self._base), "attempted_changes": self._changes},
        )


class ContextDeltaCompiler:
    @staticmethod
    def compile(previous: Mapping[str, Any], current: Mapping[str, Any], *, exact_keys: Iterable[str] = ()) -> dict[str, Any]:
        old = dict(previous)
        new = dict(current)
        exact = set(str(key) for key in exact_keys)
        added = {key: new[key] for key in sorted(new.keys() - old.keys())}
        removed = sorted(old.keys() - new.keys())
        changed: dict[str, Any] = {}
        for key in sorted(old.keys() & new.keys()):
            if old[key] == new[key]:
                continue
            changed[key] = {
                "mode": "exact" if key in exact else "semantic",
                "before_hash": sha256_digest(old[key]),
                "after": new[key],
                "after_hash": sha256_digest(new[key]),
            }
        return content_receipt(
            "CONTEXT_DELTA_COMPILER_V1",
            {"previous_hash": sha256_digest(old), "current_hash": sha256_digest(new), "added": added, "removed": removed, "changed": changed, "noop": not added and not removed and not changed},
        )


class ContextBudgetExplanationPlanner:
    CATEGORIES = ("system", "repository", "tool", "memory", "history", "evidence")

    @classmethod
    def plan(cls, total_tokens: int, *, minimums: Mapping[str, int] | None = None, weights: Mapping[str, float] | None = None) -> dict[str, Any]:
        total = int(total_tokens)
        if total <= 0:
            raise ValueError("total_tokens must be positive")
        mins = {key: max(0, int((minimums or {}).get(key, 0))) for key in cls.CATEGORIES}
        if sum(mins.values()) > total:
            return content_receipt("CONTEXT_BUDGET_EXPLANATION_PLAN_V1", {"ok": False, "decision": "ABSTAIN", "reason": "MINIMUMS_EXCEED_BUDGET", "total_tokens": total, "minimums": mins})
        remaining = total - sum(mins.values())
        raw_weights = {key: max(0.0, float((weights or {}).get(key, 1.0))) for key in cls.CATEGORIES}
        weight_sum = sum(raw_weights.values()) or 1.0
        allocation = dict(mins)
        assigned = 0
        for key in cls.CATEGORIES[:-1]:
            share = int(remaining * raw_weights[key] / weight_sum)
            allocation[key] += share
            assigned += share
        allocation[cls.CATEGORIES[-1]] += remaining - assigned
        return content_receipt("CONTEXT_BUDGET_EXPLANATION_PLAN_V1", {"ok": True, "decision": "ALLOCATE", "total_tokens": total, "allocation": allocation, "minimums": mins, "weights": raw_weights})


class PolicyConflictResolver:
    PRECEDENCE = ("security", "task", "repository", "global", "model")

    @classmethod
    def resolve(cls, policies: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        effective: dict[str, Any] = {}
        ownership: dict[str, str] = {}
        conflicts: list[dict[str, Any]] = []
        for scope in reversed(cls.PRECEDENCE):
            for key, value in sorted(dict(policies.get(scope) or {}).items()):
                if key in effective and effective[key] != value:
                    conflicts.append({"key": key, "loser": ownership[key], "winner": scope, "loser_value": effective[key], "winner_value": value})
                effective[key] = copy.deepcopy(value)
                ownership[key] = scope
        return content_receipt("POLICY_CONFLICT_RESOLVER_V1", {"ok": True, "precedence": list(cls.PRECEDENCE), "effective": effective, "ownership": ownership, "conflicts": conflicts})


class MinimumEvidenceSchema:
    DEFAULTS = {
        "read": ("target",),
        "edit": ("target", "current_content", "constraints"),
        "delete": ("target", "impact", "verification"),
        "execute": ("command", "authorization"),
        "publish": ("artifact", "verification", "authorization"),
        "security-sensitive": ("target", "authorization", "security_review", "verification"),
    }

    @classmethod
    def evaluate(cls, action_class: str, evidence: Mapping[str, Any], *, critical: Iterable[str] = ()) -> dict[str, Any]:
        required = sorted_unique(tuple(cls.DEFAULTS.get(action_class, ())) + tuple(critical))
        missing = [key for key in required if evidence.get(key) in (None, "", [], {})]
        if not missing:
            decision = "ALLOW"
        elif action_class in {"delete", "publish", "security-sensitive"}:
            decision = "ABSTAIN"
        else:
            decision = "VERIFY"
        return content_receipt("MINIMUM_EVIDENCE_SCHEMA_V1", {"action_class": action_class, "required": list(required), "missing": missing, "decision": decision})


class SourceSpecificTrustCalibrator:
    BASELINES = {"local-file": 0.85, "git": 0.95, "user": 0.60, "mcp": 0.65, "web": 0.50, "generated-summary": 0.40, "memory": 0.45, "remote-api": 0.60}

    @classmethod
    def calibrate(cls, source: str, *, verified: bool = False, stale: bool = False, tainted: bool = False, conflict: bool = False) -> dict[str, Any]:
        if source not in cls.BASELINES:
            return content_receipt("SOURCE_SPECIFIC_TRUST_CALIBRATION_V1", {"ok": False, "source": source, "decision": "VERIFY", "confidence": 0.0, "reasons": ["UNKNOWN_SOURCE"]})
        score = cls.BASELINES[source]
        reasons = [f"baseline:{source}"]
        if verified:
            score += 0.15
            reasons.append("verified")
        if stale:
            score -= 0.20
            reasons.append("stale")
        if tainted:
            score -= 0.45
            reasons.append("tainted")
        if conflict:
            score -= 0.25
            reasons.append("conflict")
        score = max(0.0, min(1.0, score))
        decision = "ALLOW" if score >= 0.70 and not tainted else ("VERIFY" if score >= 0.35 else "ABSTAIN")
        return content_receipt("SOURCE_SPECIFIC_TRUST_CALIBRATION_V1", {"ok": True, "source": source, "confidence": round(score, 4), "decision": decision, "reasons": reasons})


class CacheInvalidationProvenance:
    KEYS = ("source_hash", "repository_commit", "dependency_hash", "tool_version", "policy_version", "schema_fingerprint")

    @classmethod
    def compare(cls, previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
        reasons = [key for key in cls.KEYS if previous.get(key) != current.get(key)]
        return content_receipt("CACHE_INVALIDATION_PROVENANCE_V1", {"invalidate": bool(reasons), "reasons": reasons, "previous_fingerprint": sha256_digest({key: previous.get(key) for key in cls.KEYS}), "current_fingerprint": sha256_digest({key: current.get(key) for key in cls.KEYS})})


class ContextLeakDetector:
    SECRET_PATTERNS = (
        re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|authorization)\s*[:=]\s*\S+"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    )
    PII_PATTERNS = (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), re.compile(r"\b(?:\+?\d[\d ()-]{8,}\d)\b"))

    @classmethod
    def inspect(cls, items: Sequence[Mapping[str, Any]], *, project_id: str, task_id: str, agent_id: str) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            scope = dict(item.get("scope") or {})
            if scope.get("project_id") not in (None, project_id):
                findings.append({"index": index, "kind": "CROSS_PROJECT"})
            if scope.get("task_id") not in (None, task_id):
                findings.append({"index": index, "kind": "CROSS_TASK"})
            if scope.get("agent_id") not in (None, agent_id):
                findings.append({"index": index, "kind": "CROSS_AGENT"})
            text = str(item.get("content", ""))
            if any(pattern.search(text) for pattern in cls.SECRET_PATTERNS):
                findings.append({"index": index, "kind": "SECRET"})
            if item.get("pii_scan", True) and any(pattern.search(text) for pattern in cls.PII_PATTERNS):
                findings.append({"index": index, "kind": "PII"})
        return content_receipt("CONTEXT_LEAK_DETECTOR_V1", {"ok": not findings, "decision": "ALLOW" if not findings else "ABSTAIN", "findings": findings})


class CompressionSafetyClassifier:
    @staticmethod
    def classify(metadata: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(metadata)
        if row.get("exact_required") or row.get("security_critical") or row.get("authorization") or row.get("secret"):
            safety = "EXACT_ONLY"
        elif row.get("syntax_sensitive") or row.get("machine_consumed"):
            safety = "STRUCTURAL_SAFE"
        elif row.get("semantic_critical") or row.get("constraints"):
            safety = "SEMANTIC_SAFE"
        else:
            safety = "LOSSY_ALLOWED"
        return content_receipt("COMPRESSION_SAFETY_CLASSES_V1", {"class": safety, "metadata_hash": sha256_digest(row)})


class SemanticPreservationVerifier:
    NUMBER = re.compile(r"(?<!\w)-?\d+(?:\.\d+)?%?")
    PATH = re.compile(r"(?:[A-Za-z]:[\\/][^\s]+|(?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)+)")
    ERROR = re.compile(r"\b(?:[A-Z][A-Z0-9_]{2,}|(?:error|exception|failed|denied|forbidden)\b)", re.I)
    NEGATION = re.compile(r"\b(?:not|no|never|without|must not|cannot|can't|do not|don't)\b", re.I)
    PERMISSION = re.compile(r"\b(?:allow|deny|permission|authorize|authorized|required|forbidden)\b", re.I)

    @classmethod
    def critical_tokens(cls, text: str) -> set[str]:
        tokens: set[str] = set()
        for pattern in (cls.NUMBER, cls.PATH, cls.ERROR, cls.NEGATION, cls.PERMISSION):
            tokens.update(match.group(0).casefold() for match in pattern.finditer(text))
        return tokens

    @classmethod
    def verify(cls, source: str, candidate: str) -> dict[str, Any]:
        required = cls.critical_tokens(source)
        present = cls.critical_tokens(candidate)
        missing = sorted(required - present)
        return content_receipt("SEMANTIC_PRESERVATION_VERIFIER_V1", {"ok": not missing, "required": sorted(required), "missing": missing, "source_hash": sha256_digest(source), "candidate_hash": sha256_digest(candidate)})


__all__ = [
    "CacheInvalidationProvenance",
    "CompressionSafetyClassifier",
    "ContextBudgetExplanationPlanner",
    "ContextDeltaCompiler",
    "ContextLeakDetector",
    "MinimumEvidenceSchema",
    "PolicyConflictResolver",
    "SemanticPreservationVerifier",
    "SourceSpecificTrustCalibrator",
    "TaskLocalContextTransaction",
]
