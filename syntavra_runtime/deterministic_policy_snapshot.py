from __future__ import annotations

import copy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .adaptive_context_policy import AdaptivePolicyConfig
from .context_decision_trace import ContextDecisionTrace
from .contract_version_graph import RuntimeContractVersionGraph
from .util import canonical_json, sha256_bytes, sha256_file


ROOT = Path(__file__).resolve().parents[1]
POLICY_CONTRACT_PATH = "contracts/python/adaptive-context-policy-v1.json"
POLICY_RUNTIME_PATH = "syntavra_runtime/adaptive_context_policy.py"
_FORBIDDEN_REFERENCE_KEYS = frozenset({"content", "payload", "raw_text", "body", "secret", "text"})
_SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "policy_family",
        "contract_graph_schema_version",
        "policy_contract",
        "policy_runtime",
        "config",
        "snapshot_hash",
    }
)
_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "policy_snapshot",
        "policy_snapshot_hash",
        "policy_receipt_hash",
        "context_decision_trace_hash",
        "task_reference",
        "binding_hash",
    }
)


def _require_sha256(value: str, *, name: str) -> str:
    normalized = str(value).casefold()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{name} must be a lowercase sha256")
    return normalized


def _reference_only(value: Any, *, path: str = "reference") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_REFERENCE_KEYS:
                raise ValueError(f"{path} cannot carry context payload authority: {key}")
            _reference_only(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reference_only(nested, path=f"{path}[{index}]")


def _verify_policy_receipt(result: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("deterministic policy snapshot requires a policy receipt")
    basis = copy.deepcopy(dict(receipt))
    observed = str(basis.pop("receipt_hash", ""))
    _require_sha256(observed, name="policy receipt hash")
    expected = sha256_bytes(canonical_json(basis))
    if observed != expected:
        raise ValueError("policy receipt hash mismatch")
    return basis, observed


class DeterministicPolicySnapshot:
    """Exact, deterministic identity for the Adaptive Context Policy used by a task.

    The snapshot binds policy config to the canonical contract-graph node hash and
    the runtime implementation hash. Replay bindings then reference the verified
    policy receipt and optional Context Decision Trace by hash. No context payload,
    persistent store, policy recomputation, or side-effect authority is introduced.
    """

    schema_version = 1
    policy_family = "adaptive-context-policy"

    @classmethod
    def _authority(cls, repo: Path) -> dict[str, Any]:
        root = repo.resolve()
        graph = RuntimeContractVersionGraph(root).build()
        nodes = {
            str(row.get("path")): row
            for row in graph.get("nodes", [])
            if isinstance(row, Mapping)
        }
        node = nodes.get(POLICY_CONTRACT_PATH)
        if not isinstance(node, Mapping):
            raise ValueError("Adaptive Context Policy contract is missing from Runtime Contract Version Graph")
        runtime = root / POLICY_RUNTIME_PATH
        if not runtime.is_file():
            raise FileNotFoundError(f"missing Adaptive Context Policy runtime: {POLICY_RUNTIME_PATH}")
        return {
            "contract_graph_schema_version": int(graph.get("schema_version", 0)),
            "policy_contract": {
                "path": POLICY_CONTRACT_PATH,
                "schema_version": node.get("schema_version"),
                "sha256": _require_sha256(str(node.get("sha256") or ""), name="policy contract sha256"),
            },
            "policy_runtime": {
                "path": POLICY_RUNTIME_PATH,
                "sha256": sha256_file(runtime),
            },
        }

    @classmethod
    def capture(
        cls,
        config: AdaptivePolicyConfig,
        *,
        repo: Path | None = None,
    ) -> dict[str, Any]:
        authority = cls._authority((repo or ROOT).resolve())
        basis = {
            "schema_version": cls.schema_version,
            "policy_family": cls.policy_family,
            "contract_graph_schema_version": authority["contract_graph_schema_version"],
            "policy_contract": authority["policy_contract"],
            "policy_runtime": authority["policy_runtime"],
            "config": asdict(config),
        }
        return {
            **basis,
            "snapshot_hash": sha256_bytes(canonical_json(basis)),
        }

    @classmethod
    def verify(
        cls,
        snapshot: Mapping[str, Any],
        *,
        repo: Path | None = None,
        require_current_authority: bool = True,
    ) -> bool:
        if set(snapshot) != _SNAPSHOT_KEYS:
            raise ValueError("deterministic policy snapshot shape drift")
        if snapshot.get("schema_version") != cls.schema_version:
            raise ValueError("deterministic policy snapshot schema drift")
        if snapshot.get("policy_family") != cls.policy_family:
            raise ValueError("deterministic policy snapshot family drift")

        contract = snapshot.get("policy_contract")
        runtime = snapshot.get("policy_runtime")
        config = snapshot.get("config")
        if not isinstance(contract, Mapping) or set(contract) != {"path", "schema_version", "sha256"}:
            raise ValueError("deterministic policy snapshot contract identity drift")
        if not isinstance(runtime, Mapping) or set(runtime) != {"path", "sha256"}:
            raise ValueError("deterministic policy snapshot runtime identity drift")
        if not isinstance(config, Mapping):
            raise ValueError("deterministic policy snapshot config must be an object")
        if contract.get("path") != POLICY_CONTRACT_PATH:
            raise ValueError("deterministic policy snapshot contract path drift")
        if runtime.get("path") != POLICY_RUNTIME_PATH:
            raise ValueError("deterministic policy snapshot runtime path drift")
        _require_sha256(str(contract.get("sha256") or ""), name="policy contract sha256")
        _require_sha256(str(runtime.get("sha256") or ""), name="policy runtime sha256")

        basis = {
            "schema_version": cls.schema_version,
            "policy_family": cls.policy_family,
            "contract_graph_schema_version": snapshot.get("contract_graph_schema_version"),
            "policy_contract": dict(contract),
            "policy_runtime": dict(runtime),
            "config": dict(config),
        }
        expected = sha256_bytes(canonical_json(basis))
        observed = _require_sha256(str(snapshot.get("snapshot_hash") or ""), name="policy snapshot hash")
        if observed != expected:
            raise ValueError("deterministic policy snapshot hash mismatch")

        if require_current_authority:
            current = cls._authority((repo or ROOT).resolve())
            if snapshot.get("contract_graph_schema_version") != current["contract_graph_schema_version"]:
                raise ValueError("policy snapshot contract graph schema drift")
            if dict(contract) != current["policy_contract"]:
                raise ValueError("policy snapshot contract authority drift")
            if dict(runtime) != current["policy_runtime"]:
                raise ValueError("policy snapshot runtime authority drift")
        return True

    @classmethod
    def bind(
        cls,
        snapshot: Mapping[str, Any],
        policy_result: Mapping[str, Any],
        *,
        trace: Mapping[str, Any] | None = None,
        task_reference: Mapping[str, Any] | None = None,
        repo: Path | None = None,
    ) -> dict[str, Any]:
        cls.verify(snapshot, repo=repo)
        receipt, receipt_hash = _verify_policy_receipt(policy_result)
        if receipt.get("config") != snapshot.get("config"):
            raise ValueError("policy receipt config does not match deterministic snapshot")

        trace_hash = ""
        if trace is not None:
            ContextDecisionTrace.verify(trace)
            if str(trace.get("policy_receipt_hash") or "") != receipt_hash:
                raise ValueError("Context Decision Trace does not bind the supplied policy receipt")
            trace_hash = _require_sha256(str(trace.get("trace_hash") or ""), name="context decision trace hash")

        reference = copy.deepcopy(dict(task_reference or {}))
        _reference_only(reference, path="task_reference")
        basis = {
            "schema_version": cls.schema_version,
            "policy_snapshot_hash": str(snapshot.get("snapshot_hash") or ""),
            "policy_receipt_hash": receipt_hash,
            "context_decision_trace_hash": trace_hash,
            "task_reference": reference,
        }
        _require_sha256(basis["policy_snapshot_hash"], name="policy snapshot hash")
        return {
            "schema_version": cls.schema_version,
            "policy_snapshot": copy.deepcopy(dict(snapshot)),
            "policy_snapshot_hash": basis["policy_snapshot_hash"],
            "policy_receipt_hash": receipt_hash,
            "context_decision_trace_hash": trace_hash,
            "task_reference": reference,
            "binding_hash": sha256_bytes(canonical_json(basis)),
        }

    @classmethod
    def verify_binding(
        cls,
        binding: Mapping[str, Any],
        policy_result: Mapping[str, Any],
        *,
        trace: Mapping[str, Any] | None = None,
        repo: Path | None = None,
    ) -> bool:
        if set(binding) != _BINDING_KEYS:
            raise ValueError("deterministic policy replay binding shape drift")
        if binding.get("schema_version") != cls.schema_version:
            raise ValueError("deterministic policy replay binding schema drift")
        snapshot = binding.get("policy_snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError("deterministic policy replay binding requires attached snapshot")
        cls.verify(snapshot, repo=repo)

        snapshot_hash = _require_sha256(str(binding.get("policy_snapshot_hash") or ""), name="policy snapshot hash")
        if snapshot_hash != str(snapshot.get("snapshot_hash") or ""):
            raise ValueError("replay binding snapshot hash mismatch")
        receipt, receipt_hash = _verify_policy_receipt(policy_result)
        if receipt_hash != str(binding.get("policy_receipt_hash") or ""):
            raise ValueError("replay binding policy receipt hash mismatch")
        if receipt.get("config") != snapshot.get("config"):
            raise ValueError("replay binding receipt config mismatch")

        observed_trace_hash = str(binding.get("context_decision_trace_hash") or "")
        if observed_trace_hash:
            _require_sha256(observed_trace_hash, name="context decision trace hash")
            if trace is None:
                raise ValueError("replay binding requires Context Decision Trace material for verification")
            ContextDecisionTrace.verify(trace)
            if str(trace.get("policy_receipt_hash") or "") != receipt_hash:
                raise ValueError("replay binding trace receipt mismatch")
            if str(trace.get("trace_hash") or "") != observed_trace_hash:
                raise ValueError("replay binding Context Decision Trace hash mismatch")
        elif trace is not None:
            raise ValueError("unexpected Context Decision Trace for binding without trace hash")

        reference = binding.get("task_reference")
        if not isinstance(reference, Mapping):
            raise ValueError("replay binding task reference must be an object")
        _reference_only(reference, path="task_reference")
        basis = {
            "schema_version": cls.schema_version,
            "policy_snapshot_hash": snapshot_hash,
            "policy_receipt_hash": receipt_hash,
            "context_decision_trace_hash": observed_trace_hash,
            "task_reference": dict(reference),
        }
        expected = sha256_bytes(canonical_json(basis))
        observed = _require_sha256(str(binding.get("binding_hash") or ""), name="replay binding hash")
        if observed != expected:
            raise ValueError("deterministic policy replay binding hash mismatch")
        return True


__all__ = ["DeterministicPolicySnapshot"]
