from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable, Mapping

from .post_completion_common import content_receipt, sha256_digest, sorted_unique


class FeatureSurfaceBudget:
    @staticmethod
    def evaluate(*, before_commands: Iterable[str], after_commands: Iterable[str], before_tools: Iterable[str] = (), after_tools: Iterable[str] = (), max_new_commands: int = 0, max_new_tools: int = 0) -> dict[str, Any]:
        bc, ac = set(before_commands), set(after_commands)
        bt, at = set(before_tools), set(after_tools)
        new_commands = sorted(ac - bc)
        new_tools = sorted(at - bt)
        ok = len(new_commands) <= max_new_commands and len(new_tools) <= max_new_tools
        return content_receipt("FEATURE_SURFACE_BUDGET_V1", {"ok": ok, "new_commands": new_commands, "new_tools": new_tools, "max_new_commands": max_new_commands, "max_new_tools": max_new_tools, "default_internal": True})


class InternalCapabilityComposition:
    STABLE_PRIMITIVES = ("context", "evidence", "search", "execute", "verify", "memory")

    @classmethod
    def compose(cls, capabilities: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        rows = {name: dict(value) for name, value in capabilities.items()}
        failures: list[str] = []
        indegree = {name: 0 for name in rows}
        graph: dict[str, list[str]] = defaultdict(list)
        for name, row in rows.items():
            primitive = row.get("primitive")
            if primitive not in cls.STABLE_PRIMITIVES:
                failures.append(f"INVALID_PRIMITIVE:{name}")
            for dependency in row.get("depends_on") or []:
                if dependency not in rows:
                    failures.append(f"MISSING_DEPENDENCY:{name}:{dependency}")
                    continue
                graph[str(dependency)].append(name)
                indegree[name] += 1
        queue = deque(sorted(name for name, degree in indegree.items() if degree == 0))
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for child in sorted(graph.get(node, [])):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(rows):
            failures.append("CAPABILITY_GRAPH_CYCLE")
        return content_receipt("INTERNAL_CAPABILITY_COMPOSITION_V1", {"ok": not failures, "order": order, "failures": failures, "public_primitives": list(cls.STABLE_PRIMITIVES)})


class ProductProfileCertification:
    REQUIRED = ("minimal", "balanced", "audit")

    @classmethod
    def certify(cls, profiles: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        failures: list[str] = []
        receipts: dict[str, Any] = {}
        for name in cls.REQUIRED:
            row = dict(profiles.get(name) or {})
            if not row:
                failures.append(f"MISSING_PROFILE:{name}")
                continue
            tools = list(row.get("tools") or [])
            max_tools = row.get("max_tools")
            if max_tools is not None and len(tools) > int(max_tools):
                failures.append(f"TOOL_BUDGET:{name}")
            if row.get("silent_fallback"):
                failures.append(f"SILENT_FALLBACK:{name}")
            receipts[name] = {"tool_count": len(tools), "profile_hash": sha256_digest(row)}
        if "adaptive" in profiles:
            receipts["adaptive"] = {"experimental": True, "profile_hash": sha256_digest(dict(profiles["adaptive"]))}
        return content_receipt("PRODUCT_PROFILE_CERTIFICATION_V1", {"ok": not failures, "profiles": receipts, "failures": failures, "live_external_certification_claimed": False})


class NoSilentFallbackReceipt:
    @staticmethod
    def build(*, cause: str, selected_path: str, risk_before: str, risk_after: str, evidence: Iterable[str] = (), allowed: bool = True) -> dict[str, Any]:
        if not str(cause).strip() or not str(selected_path).strip():
            raise ValueError("fallback cause and selected path are required")
        payload = {"cause": str(cause), "selected_path": str(selected_path), "risk_before": str(risk_before), "risk_after": str(risk_after), "evidence": list(sorted_unique(tuple(evidence))), "allowed": bool(allowed)}
        return content_receipt("NO_SILENT_FALLBACK_RECEIPT_V1", payload)


class ContextQualitySLOGate:
    DEFAULT_THRESHOLDS = {"task_success_rate": 0.95, "critical_evidence_recall": 0.99, "context_precision": 0.80, "verifier_success_rate": 0.99, "unsafe_action_rate_max": 0.0}

    @classmethod
    def evaluate(cls, metrics: Mapping[str, float], thresholds: Mapping[str, float] | None = None) -> dict[str, Any]:
        limits = {**cls.DEFAULT_THRESHOLDS, **dict(thresholds or {})}
        failures: list[str] = []
        for key in ("task_success_rate", "critical_evidence_recall", "context_precision", "verifier_success_rate"):
            if float(metrics.get(key, 0.0)) < float(limits[key]):
                failures.append(key)
        if float(metrics.get("unsafe_action_rate", 1.0)) > float(limits["unsafe_action_rate_max"]):
            failures.append("unsafe_action_rate")
        return content_receipt("CONTEXT_QUALITY_SLO_GATE_V1", {"ok": not failures, "decision": "RELEASE" if not failures else "BLOCK_RELEASE", "failures": failures, "metrics": dict(metrics), "thresholds": limits, "token_savings_is_secondary": True})


__all__ = ["ContextQualitySLOGate", "FeatureSurfaceBudget", "InternalCapabilityComposition", "NoSilentFallbackReceipt", "ProductProfileCertification"]
