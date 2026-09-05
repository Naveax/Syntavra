from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from .post_completion_common import content_receipt, sha256_digest, sorted_unique


class CrossHostAdapterConformanceSuite:
    @staticmethod
    def certify(results: Mapping[str, Mapping[str, Any]], *, required_hosts: Iterable[str] = ()) -> dict[str, Any]:
        required = sorted_unique(tuple(required_hosts))
        missing = [host for host in required if host not in results]
        normalized: dict[str, dict[str, Any]] = {}
        semantic_hashes: set[str] = set()
        for host, raw in sorted(results.items()):
            row = dict(raw)
            projection = {
                "decision": row.get("decision"),
                "selected_capabilities": sorted(row.get("selected_capabilities") or []),
                "risk": row.get("risk"),
                "exact_recovery": bool(row.get("exact_recovery")),
                "fallback": row.get("fallback"),
            }
            normalized[host] = projection
            semantic_hashes.add(sha256_digest(projection))
        equivalent = len(semantic_hashes) <= 1 and not missing and bool(normalized)
        return content_receipt("CROSS_HOST_ADAPTER_CONFORMANCE_V1", {"ok": equivalent, "missing_hosts": missing, "semantic_hashes": sorted(semantic_hashes), "hosts": sorted(normalized), "live_certification_claimed": False})


class ActionDryRunSimulator:
    SUPPORTED = {"filesystem", "git", "network", "package", "deploy"}

    @classmethod
    def simulate(cls, action: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(action.get("kind", ""))
        if kind not in cls.SUPPORTED:
            return content_receipt("ACTION_DRY_RUN_SIMULATOR_V1", {"ok": False, "decision": "ABSTAIN", "reason": "UNSUPPORTED_ACTION", "kind": kind})
        side_effects = list(action.get("side_effects") or [])
        irreversible = bool(action.get("irreversible")) or any(str(item).casefold() in {"delete", "publish", "deploy-production", "charge"} for item in side_effects)
        preconditions = list(action.get("preconditions") or [])
        missing = [str(item) for item in preconditions if not action.get("satisfied", {}).get(str(item), False)]
        decision = "READY" if not missing and not irreversible else ("VERIFY" if not missing else "ABSTAIN")
        return content_receipt("ACTION_DRY_RUN_SIMULATOR_V1", {"ok": decision == "READY", "decision": decision, "kind": kind, "side_effects": side_effects, "irreversible": irreversible, "missing_preconditions": missing})


class FaultInjectionHarness:
    FAULTS = ("timeout", "malformed-json", "partial-output", "stale-file", "missing-dependency", "revoked-permission", "network-failure")

    @classmethod
    def apply(cls, fault: str, payload: Any) -> dict[str, Any]:
        if fault not in cls.FAULTS:
            raise ValueError("unknown fault")
        if fault == "timeout":
            result = {"error": "TimeoutError", "partial": None}
        elif fault == "malformed-json":
            result = {"value": "{malformed", "parseable": False}
        elif fault == "partial-output":
            result = {"partial": str(payload)[: max(1, len(str(payload)) // 2)], "complete": False}
        elif fault == "stale-file":
            result = {"value": payload, "freshness": "stale"}
        elif fault == "missing-dependency":
            result = {"error": "DependencyUnavailable", "dependency": str(payload)}
        elif fault == "revoked-permission":
            result = {"error": "PermissionRevoked", "authorized": False}
        else:
            result = {"error": "NetworkUnavailable", "retryable": True}
        return content_receipt("FAULT_INJECTION_HARNESS_V1", {"fault": fault, "result": result})


class GoldenCorpusGenerator:
    REDACT = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S+")

    @classmethod
    def generate(cls, tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        fixtures: list[dict[str, Any]] = []
        rejected: list[int] = []
        for index, raw in enumerate(tasks):
            row = dict(raw)
            if not row.get("permitted", False):
                rejected.append(index)
                continue
            text = cls.REDACT.sub(r"\1=<redacted>", str(row.get("content", "")))
            fixture = {"task_type": row.get("task_type", "unknown"), "content": text, "expected": row.get("expected"), "source_hash": sha256_digest(row)}
            fixture["fixture_id"] = sha256_digest(fixture)
            fixtures.append(fixture)
        fixtures.sort(key=lambda item: item["fixture_id"])
        return content_receipt("GOLDEN_CORPUS_GENERATOR_V1", {"ok": True, "fixtures": fixtures, "rejected_indices": rejected})


class LiveTaskReplayFixture:
    @staticmethod
    def from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(receipt)
        fixture = {
            "task_id": row.get("task_id"),
            "outcome": row.get("outcome"),
            "repository_commit": row.get("repository_commit"),
            "policy_hash": row.get("policy_hash"),
            "evidence_handles": sorted(row.get("evidence_handles") or []),
            "verifier": row.get("verifier"),
            "failure": row.get("failure"),
        }
        fixture["fixture_id"] = sha256_digest(fixture)
        return content_receipt("LIVE_TASK_REPLAY_FIXTURE_V1", {"ok": True, "fixture": fixture})


class ReproducibilityCapsule:
    REQUIRED = ("repository_commit", "policy_hash", "provider_profile", "tool_versions", "fixtures", "verifier", "environment")

    @classmethod
    def build(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        missing = [key for key in cls.REQUIRED if values.get(key) in (None, "", [], {})]
        payload = {key: values.get(key) for key in cls.REQUIRED}
        return content_receipt("REPRODUCIBILITY_CAPSULE_V1", {"ok": not missing, "missing": missing, "capsule": payload, "capsule_hash": sha256_digest(payload)})


class PerformanceBudgetGate:
    @staticmethod
    def evaluate(metrics: Mapping[str, float | int | bool], budgets: Mapping[str, float | int]) -> dict[str, Any]:
        failures: list[str] = []
        if not bool(metrics.get("correct", False)):
            failures.append("correctness")
        for key in ("cpu_ms", "ram_mb", "disk_mb", "latency_ms", "token_overhead"):
            if key in budgets and float(metrics.get(key, float("inf"))) > float(budgets[key]):
                failures.append(key)
        return content_receipt("PERFORMANCE_BUDGET_GATE_V1", {"ok": not failures, "decision": "PASS" if not failures else "FAIL", "failures": failures, "metrics": dict(metrics), "budgets": dict(budgets)})


class MemoryCorrectnessSuite:
    @staticmethod
    def evaluate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not rows:
            return content_receipt("MEMORY_CORRECTNESS_SUITE_V1", {"ok": False, "reason": "NO_FIXTURES"})
        tp = fp = fn = 0
        fresh_ok = supersession_ok = conflict_ok = poisoning_ok = wrong_project_ok = 0
        for row in rows:
            expected = bool(row.get("expected_relevant"))
            returned = bool(row.get("returned"))
            tp += int(expected and returned)
            fp += int(not expected and returned)
            fn += int(expected and not returned)
            fresh_ok += int(bool(row.get("freshness_ok", True)))
            supersession_ok += int(bool(row.get("supersession_ok", True)))
            conflict_ok += int(bool(row.get("conflict_ok", True)))
            poisoning_ok += int(bool(row.get("poisoning_rejected", True)))
            wrong_project_ok += int(bool(row.get("wrong_project_rejected", True)))
        n = len(rows)
        metrics = {
            "precision": tp / max(1, tp + fp),
            "recall": tp / max(1, tp + fn),
            "freshness": fresh_ok / n,
            "supersession": supersession_ok / n,
            "conflict": conflict_ok / n,
            "poisoning_rejection": poisoning_ok / n,
            "wrong_project_rejection": wrong_project_ok / n,
        }
        return content_receipt("MEMORY_CORRECTNESS_SUITE_V1", {"ok": True, "metrics": metrics, "fixtures": n})


class ToolSchemaCompatibilityFingerprint:
    @staticmethod
    def fingerprint(schema: Mapping[str, Any]) -> str:
        return sha256_digest(schema)

    @classmethod
    def compare(cls, previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
        old = dict(previous)
        new = dict(current)
        old_props = dict(old.get("properties") or {})
        new_props = dict(new.get("properties") or {})
        removed = sorted(old_props.keys() - new_props.keys())
        added = sorted(new_props.keys() - old_props.keys())
        changed = sorted(key for key in old_props.keys() & new_props.keys() if old_props[key] != new_props[key])
        required_changed = sorted(old.get("required") or []) != sorted(new.get("required") or [])
        breaking = bool(removed or changed or required_changed)
        return content_receipt("TOOL_SCHEMA_COMPATIBILITY_FINGERPRINT_V1", {"changed": cls.fingerprint(old) != cls.fingerprint(new), "breaking": breaking, "added": added, "removed": removed, "changed_fields": changed, "required_changed": required_changed, "previous": cls.fingerprint(old), "current": cls.fingerprint(new)})


class ToolDiscoveryDegradationMode:
    @staticmethod
    def discover(query: str, catalog: Sequence[Mapping[str, Any]], *, limit: int = 5) -> dict[str, Any]:
        tokens = {token for token in re.findall(r"[a-z0-9_.-]+", query.casefold()) if token}
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for raw in catalog:
            row = dict(raw)
            name = str(row.get("name", ""))
            haystack = " ".join([name, str(row.get("namespace", "")), " ".join(row.get("keywords") or [])]).casefold()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, name, row))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not scored:
            return content_receipt("TOOL_DISCOVERY_DEGRADATION_MODE_V1", {"ok": False, "decision": "ABSTAIN", "reason": "NO_MATCH", "tools": []})
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return content_receipt("TOOL_DISCOVERY_DEGRADATION_MODE_V1", {"ok": False, "decision": "ABSTAIN", "reason": "AMBIGUOUS_TOP_MATCH", "tools": []})
        selected = [item[2] for item in scored[: max(1, int(limit))]]
        return content_receipt("TOOL_DISCOVERY_DEGRADATION_MODE_V1", {"ok": True, "decision": "SELECT", "mode": "deterministic-keyword", "tools": selected})


class ProviderCapabilityNegotiator:
    @staticmethod
    def negotiate(requirements: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        required = dict(requirements)
        qualified: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for raw in candidates:
            row = dict(raw)
            reasons: list[str] = []
            if int(row.get("context_window", 0)) < int(required.get("context_window", 0)):
                reasons.append("context_window")
            for flag in ("tools", "streaming", "reasoning"):
                if required.get(flag) and not row.get(flag):
                    reasons.append(flag)
            if required.get("models") and row.get("model") not in set(required["models"]):
                reasons.append("model")
            if reasons:
                rejected.append({"id": row.get("id"), "reasons": reasons})
            else:
                qualified.append(row)
        qualified.sort(key=lambda row: (float(row.get("cost", 0.0)), float(row.get("latency_ms", 0.0)), str(row.get("id", ""))))
        selected = qualified[0] if qualified else None
        return content_receipt("PROVIDER_CAPABILITY_NEGOTIATION_V1", {"ok": selected is not None, "decision": "SELECT" if selected else "UNSUPPORTED", "selected": selected, "rejected": rejected, "fallback_explicit": True})


class PromptCacheStabilityGuard:
    @staticmethod
    def compare(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
        old = dict(previous)
        new = dict(current)
        reasons: list[str] = []
        if old.get("content_hash") != new.get("content_hash"):
            reasons.append("CONTENT_CHANGED")
        if old.get("order") != new.get("order"):
            reasons.append("ORDER_CHANGED")
        if old.get("metadata") != new.get("metadata"):
            reasons.append("METADATA_CHANGED")
        if old.get("provider") != new.get("provider"):
            reasons.append("PROVIDER_CHANGED")
        if old.get("model") != new.get("model"):
            reasons.append("MODEL_CHANGED")
        unnecessary = bool(reasons) and old.get("semantic_hash") == new.get("semantic_hash") and set(reasons) <= {"ORDER_CHANGED", "METADATA_CHANGED"}
        return content_receipt("PROMPT_CACHE_STABILITY_GUARD_V1", {"stable": not reasons, "cache_bust": bool(reasons), "reasons": reasons, "unnecessary_cache_bust": unnecessary})


class MultiAgentHandoffContractVerifier:
    REQUIRED = ("task_id", "repository_commit", "constraints", "completed_work", "evidence_handles")

    @classmethod
    def verify(cls, handoff: Mapping[str, Any], *, expected_repository_commit: str | None = None) -> dict[str, Any]:
        row = dict(handoff)
        failures = [f"MISSING:{key}" for key in cls.REQUIRED if row.get(key) in (None, "", [], {})]
        if expected_repository_commit is not None and row.get("repository_commit") != expected_repository_commit:
            failures.append("REPOSITORY_COMMIT_MISMATCH")
        for handle in row.get("evidence_handles") or []:
            if not isinstance(handle, Mapping) or not handle.get("integrity") or not handle.get("locator"):
                failures.append("INVALID_EVIDENCE_HANDLE")
        if row.get("security_state", {}).get("denied") and row.get("authorization", {}).get("allow"):
            failures.append("SECURITY_STATE_CONFLICT")
        return content_receipt("MULTI_AGENT_HANDOFF_CONTRACT_VERIFIER_V1", {"ok": not failures, "decision": "ACCEPT" if not failures else "REJECT", "failures": failures, "handoff_hash": sha256_digest(row)})


__all__ = [
    "ActionDryRunSimulator",
    "CrossHostAdapterConformanceSuite",
    "FaultInjectionHarness",
    "GoldenCorpusGenerator",
    "LiveTaskReplayFixture",
    "MemoryCorrectnessSuite",
    "MultiAgentHandoffContractVerifier",
    "PerformanceBudgetGate",
    "PromptCacheStabilityGuard",
    "ProviderCapabilityNegotiator",
    "ReproducibilityCapsule",
    "ToolDiscoveryDegradationMode",
    "ToolSchemaCompatibilityFingerprint",
]
