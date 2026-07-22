from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping

from .adapters.base import EngineAdapter, EngineRequest, EngineResponse
from .budgets import BudgetLedger
from .errors import WorkflowError
from .validators import ValidationResult, require_all, validate_capability, validate_response_schema


class NodeState(StrEnum):
    PENDING="PENDING"; READY="READY"; RUNNING="RUNNING"; BLOCKED="BLOCKED"; VALIDATING="VALIDATING"; SUCCEEDED="SUCCEEDED"; FAILED="FAILED"; ROLLED_BACK="ROLLED_BACK"; CANCELLED="CANCELLED"


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    node_id: str
    capability: str
    dependencies: tuple[str, ...]
    required_evidence: tuple[str, ...]
    engine_id: str
    timeout: int
    retry_attempts: int
    validator_names: tuple[str, ...]
    rollback_action: str | None


@dataclass(frozen=True, slots=True)
class NodeResult:
    node_id: str
    state: NodeState
    response: EngineResponse | None
    validations: tuple[ValidationResult, ...]
    attempts: int


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, task_id: str, results: Iterable[NodeResult]) -> None:
        payload = {"task_id": task_id, "results": [asdict(result) for result in results], "saved_at": time.time_ns()}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, default=str, sort_keys=True), encoding="utf-8")
        temp.replace(self.path)

    def exists(self) -> bool:
        return self.path.exists()


class WorkflowExecutor:
    def __init__(self, adapters: Mapping[str, EngineAdapter], checkpoint: CheckpointStore) -> None:
        self.adapters = dict(adapters)
        self.checkpoint = checkpoint

    def execute(self, *, task_id: str, nodes: Iterable[WorkflowNode], authorized: set[str], budget: BudgetLedger, payload: Mapping[str, object]) -> tuple[NodeResult, ...]:
        node_list = tuple(nodes)
        completed: set[str] = set()
        results: list[NodeResult] = []
        for node in node_list:
            if not set(node.dependencies).issubset(completed):
                raise WorkflowError(f"node {node.node_id} dependencies are incomplete")
            adapter = self.adapters.get(node.engine_id)
            if adapter is None:
                raise WorkflowError(f"engine adapter missing: {node.engine_id}")
            budget.reserve(requests=1, wall_time_s=min(node.timeout, 1))
            attempts = 0
            response = None
            while attempts < node.retry_attempts:
                attempts += 1
                response = adapter.execute(EngineRequest(node.capability, task_id, payload))
                if response.status == "SUCCEEDED":
                    break
            if response is None or response.status != "SUCCEEDED":
                result = NodeResult(node.node_id, NodeState.FAILED, response, (), attempts)
                results.append(result)
                self.checkpoint.save(task_id, results)
                raise WorkflowError(f"node failed: {node.node_id}")
            validations = (
                validate_response_schema(response.payload, {"task_id", "capability", "artifact_hash"}),
                validate_capability({node.capability}, authorized),
            )
            require_all(validations)
            result = NodeResult(node.node_id, NodeState.SUCCEEDED, response, validations, attempts)
            results.append(result)
            completed.add(node.node_id)
            self.checkpoint.save(task_id, results)
        return tuple(results)
