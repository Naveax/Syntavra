from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .adapters.simulated import SimulatedStudioAdapter
from .budgets import BudgetLedger
from .capabilities import default_capabilities
from .capability_graph import CapabilityGraph
from .context_knapsack import ContextCandidate, ContextPackage, select_context
from .evidence_ledger import EvidenceLedger, EvidenceRecord
from .registries import default_engines
from .task_state import RobloxTaskState
from .telemetry import TelemetryRecorder
from .validators import ProofNode
from .workflow import CheckpointStore, WorkflowExecutor, WorkflowNode

VALIDATORS = {"capability", "script_syntax", "artifact_hash", "luau_diagnostics", "playtest", "log_expectations", "response_schema", "asset_integrity", "remote_validation", "migration", "rollback", "context", "budget", "workflow", "telemetry"}


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    task_hash: str
    capability_plan: tuple[str, ...]
    context: ContextPackage
    node_count: int
    verified: bool
    proof: tuple[ProofNode, ...]
    elapsed_ms: float


class RobloxStudioOrchestrator:
    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        specs = default_capabilities()
        engines = default_engines(specs)
        self.graph = CapabilityGraph(specs, engines=engines.engines, validators=VALIDATORS)
        self.ledger = EvidenceLedger(self.state_root / "evidence.db")
        self.telemetry = TelemetryRecorder(self.state_root / "telemetry.jsonl")
        self.executor = WorkflowExecutor({"studio_bridge": SimulatedStudioAdapter()}, CheckpointStore(self.state_root / "checkpoint.json"))

    def run(self, task: RobloxTaskState, candidates: Iterable[ContextCandidate], *, branch: str = "main", commit: str = "WORKTREE") -> OrchestrationResult:
        started = time.perf_counter()
        self.telemetry.record(task.task_id, "understand", "STARTED")
        plan = self.graph.plan(task.requested_capabilities, task.authorized_capabilities)
        required_roles = set(task.evidence_requirements)
        context = select_context(candidates, required_roles=required_roles, token_budget=task.token_budget)
        now = int(time.time())
        for candidate in context.selected:
            source_hash = hashlib.sha256(candidate.content.encode()).hexdigest()
            record = EvidenceRecord(
                evidence_id=f"{task.task_id}:{candidate.candidate_id}", task_id=task.task_id,
                source_type="context_candidate", source_uri=candidate.candidate_id, source_hash=source_hash,
                project_fingerprint=task.project_fingerprint, branch=branch, commit=commit,
                generation=1, timestamp=now, trust_class="PROJECT_ATTESTED", taint_class="CLEAN",
                exact_fragment=candidate.content if candidate.exact_required else "", summary=candidate.content[:240],
                recovery_handle=candidate.recovery_handle, valid_from=now-1, valid_until=now+3600,
                superseded_by=None, validator_links=("context",),
            )
            self.ledger.append(record)
        nodes = []
        previous = None
        executable = [capability for capability in plan if capability in {"inspect_project", "inspect_selection", "read_script", "write_script", "execute_luau", "start_playtest", "stop_playtest", "read_client_logs", "read_server_logs", "capture_viewport", "asset_import", "ui_edit", "datamodel_graph"}]
        for index, capability in enumerate(executable):
            node_id = f"node-{index:02d}-{capability}"
            nodes.append(WorkflowNode(node_id, capability, () if previous is None else (previous,), tuple(required_roles), "studio_bridge", 5, 1, ("response_schema", "capability"), "checkpoint" if "write" in capability or "edit" in capability or "import" in capability else None))
            previous = node_id
        budget = BudgetLedger(task.token_budget, task.request_budget, task.transfer_budget, task.gpu_budget, task.wall_time_budget)
        results = self.executor.execute(task_id=task.task_id, nodes=nodes, authorized=set(task.authorized_capabilities), budget=budget, payload={"task_hash": task.canonical_hash(), "context_ids": [item.candidate_id for item in context.selected]}) if nodes else ()
        proof = tuple(ProofNode(
            requirement=result.response.capability if result.response else result.node_id,
            evidence=tuple(item.candidate_id for item in context.selected), execution=result.node_id,
            artifact_hash=str(result.response.payload.get("artifact_hash", "")) if result.response else "",
            validator_results=result.validations, observed_result=result.state.value, residual_risk="SIMULATED_ENGINE",
        ) for result in results)
        elapsed = (time.perf_counter() - started) * 1000
        self.telemetry.record(task.task_id, "complete", "SUCCEEDED", elapsed_ms=elapsed, nodes=len(results), token_cost=context.token_cost)
        return OrchestrationResult(task.canonical_hash(), plan, context, len(results), all(all(v.passed for v in item.validations) for item in results), proof, elapsed)
