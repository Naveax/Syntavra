from __future__ import annotations

import copy
import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from .inference_skip_cache import InferenceSkipIdentity
from .verified_workflow import (
    BoundVerifiedWorkflow,
    READ_ONLY_WORKFLOW_ACTIONS,
    VerifiedWorkflowCompiler,
    WorkflowCompatibilityIdentity,
    WorkflowMacroAmbiguityError,
)


_RUNTIME_SOURCES = (
    "verified_workflow.py",
    "verified_workflow_extension.py",
    "agent_runtime.py",
    "autonomous_agent.py",
    "agent_retrieval.py",
)
_COMPILER: ContextVar[VerifiedWorkflowCompiler | None] = ContextVar(
    "syntavra_verified_workflow_compiler",
    default=None,
)
_RUN_STATE: ContextVar["_WorkflowRunState | None"] = ContextVar(
    "syntavra_verified_workflow_run_state",
    default=None,
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    raw = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def workflow_runtime_hash() -> str:
    """Fingerprint every runtime surface that can change macro replay semantics."""
    root = Path(__file__).resolve().parent
    rows: list[tuple[str, int, str]] = []
    for name in _RUNTIME_SOURCES:
        path = root / name
        data = path.read_bytes()
        rows.append((name, len(data), hashlib.sha256(data).hexdigest()))
    return _sha(rows)


def _compatibility(identity: InferenceSkipIdentity) -> WorkflowCompatibilityIdentity:
    base = WorkflowCompatibilityIdentity.from_inference_identity(identity)
    runtime_hash = workflow_runtime_hash()
    return replace(
        base,
        policy_hash=_sha(
            {
                "base_policy_hash": base.policy_hash,
                "workflow_runtime_hash": runtime_hash,
            }
        ),
    )


@dataclass
class _WorkflowRunState:
    compiler: VerifiedWorkflowCompiler
    compatibility: WorkflowCompatibilityIdentity | None = None
    bound: BoundVerifiedWorkflow | None = None
    replayed_ever: bool = False
    provider_calls_avoided: int = 0
    executed_actions: int = 0
    observed_actions: list[dict[str, Any]] = field(default_factory=list)
    replay_failure: str = ""


def _session_id(compatibility: WorkflowCompatibilityIdentity) -> str:
    return "verified-workflow-" + compatibility.compatibility_hash[:40]


def _active_bound(
    state: _WorkflowRunState,
    *,
    instruction: str,
) -> BoundVerifiedWorkflow | None:
    compatibility = state.compatibility
    if compatibility is None:
        return None
    macros = list(state.compiler.promoted_macros(compatibility))
    if not macros:
        return None

    events = state.compiler.memory.events(_session_id(compatibility))
    latest_promotion: dict[str, int] = {}
    latest_quarantine: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") or {}
        macro_id = str(payload.get("macro_id") or "")
        if not macro_id:
            continue
        sequence = int(event.get("sequence") or 0)
        if event_type == "verified-workflow-promotion":
            latest_promotion[macro_id] = sequence
        elif event_type == "verified-workflow-quarantine":
            latest_quarantine[macro_id] = sequence

    active = [
        macro
        for macro in macros
        if latest_promotion.get(macro.macro_id, 0)
        > latest_quarantine.get(macro.macro_id, 0)
    ]
    if not active:
        return None
    if len(active) > 1:
        raise WorkflowMacroAmbiguityError(
            "multiple active verified workflow macros are compatible"
        )
    macro = active[0]
    parameters = {"instruction": instruction}
    actions = state.compiler.bind(macro.template, parameters)
    return BoundVerifiedWorkflow(
        macro=macro,
        parameters=parameters,
        actions=actions,
    )


def _quarantine(
    state: _WorkflowRunState,
    *,
    bound: BoundVerifiedWorkflow,
    reason: str,
    verifier_receipt_hash: str = "",
) -> None:
    compatibility = state.compatibility
    if compatibility is None:
        return
    session_id = _session_id(compatibility)
    state.compiler.memory.open(
        session_id,
        metadata={
            "owner": "verified-workflow-macro-v1",
            "compatibility_hash": compatibility.compatibility_hash,
            "project_id": compatibility.project_id,
        },
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "macro_id": bound.macro.macro_id,
        "compatibility_hash": compatibility.compatibility_hash,
        "reason": str(reason)[:160],
    }
    if verifier_receipt_hash:
        payload["verifier_receipt_hash"] = verifier_receipt_hash
    state.compiler.memory.append(
        session_id,
        "verified-workflow-quarantine",
        payload,
    )


def _verification_passed(product: Any) -> bool:
    return bool(
        product.run.ok
        and product.verification_complete
        and all(bool(item.get("ok")) for item in product.post_verifiers)
    )


def _verifier_receipt_hash(product: Any) -> str:
    receipt = None
    for attempt in reversed(product.run.attempts):
        if attempt.verifier is not None:
            receipt = attempt.verifier
            break
    if receipt is None:
        return ""
    primary = {
        "receipt_id": str(receipt.receipt_id),
        "command": list(receipt.command),
        "started_at": str(receipt.started_at),
        "duration_ms": float(receipt.duration_ms),
        "exit_code": int(receipt.exit_code),
        "timed_out": bool(receipt.timed_out),
        "output_limit_exceeded": bool(receipt.output_limit_exceeded),
        "stdout_sha256": str(receipt.stdout_sha256),
        "stderr_sha256": str(receipt.stderr_sha256),
    }
    return _sha(
        {
            "schema_version": 1,
            "primary": primary,
            "post_verifiers": [dict(item) for item in product.post_verifiers],
            "verification_complete": bool(product.verification_complete),
        }
    )


def _task_reference(product: Any, instruction: str) -> dict[str, Any]:
    return {
        "run_id": str(product.run.run_id),
        "instruction_sha256": _sha(instruction.encode("utf-8")),
        "final_diff_sha256": _sha(product.run.final_diff.encode("utf-8")),
        "changed_files": list(product.run.changed_files),
    }


def install() -> None:
    """Install fail-closed verified workflow replay into the existing agent product path."""
    from .agent_runtime import AgentRuntime, GatewayPatchProvider
    from .autonomous_agent import AutonomousCodingAgent

    if getattr(AgentRuntime, "_syntavra_verified_workflow_gateway_v1", False):
        return

    original_runtime_run = AgentRuntime.run
    original_gateway_init = GatewayPatchProvider.__init__
    original_gateway_call_model = GatewayPatchProvider._call_model
    original_gateway_propose = GatewayPatchProvider.propose
    original_inference_identity = AutonomousCodingAgent._inference_identity

    @wraps(original_gateway_init)
    def gateway_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_gateway_init(self, *args, **kwargs)
        self._verified_workflow_run_state = _RUN_STATE.get()
        self._verified_workflow_action_queue = []
        self._verified_workflow_local_action_inflight = False

    @wraps(original_inference_identity)
    def inference_identity(self: Any, task: Any, repository_fingerprint: Any) -> Any:
        identity = original_inference_identity(self, task, repository_fingerprint)
        state = _RUN_STATE.get()
        if state is not None and identity is not None:
            try:
                state.compatibility = _compatibility(identity)
            except (OSError, TypeError, ValueError):
                state.compatibility = None
        return identity

    @wraps(original_gateway_call_model)
    def gateway_call_model(self: Any, base: Mapping[str, Any], *, round_number: int) -> Any:
        state = getattr(self, "_verified_workflow_run_state", None)
        self._verified_workflow_local_action_inflight = False
        queue = getattr(self, "_verified_workflow_action_queue", None)
        if state is not None and queue:
            action = copy.deepcopy(queue.pop(0))
            self._verified_workflow_local_action_inflight = True
            state.replayed_ever = True
            state.provider_calls_avoided += 1
            state.executed_actions += 1
            try:
                self.journal.emit(
                    "verified-workflow-action-replayed",
                    round=round_number,
                    macro_id=state.bound.macro.macro_id if state.bound else "",
                    action=str(action.get("action") or ""),
                )
            except Exception:
                pass
            return SimpleNamespace(
                text=json.dumps(action, ensure_ascii=False, sort_keys=True),
                provider="verified-workflow-macro",
                model="local-replay",
                usage={},
                raw={},
                response_id="",
                finish_reason="tool_action",
            )

        result = original_gateway_call_model(self, base, round_number=round_number)
        if state is not None and not state.replayed_ever:
            try:
                action = self._action(result.text)
            except (TypeError, ValueError, json.JSONDecodeError):
                action = {}
            name = str(action.get("action") or "").casefold()
            if name in READ_ONLY_WORKFLOW_ACTIONS:
                state.observed_actions.append(copy.deepcopy(action))
        return result

    @wraps(original_gateway_propose)
    def gateway_propose(
        self: Any,
        task: Any,
        context: Mapping[str, Any],
        previous_failure: Mapping[str, Any] | None,
    ) -> Any:
        state = getattr(self, "_verified_workflow_run_state", None)
        eligible = bool(
            state is not None
            and state.compatibility is not None
            and int(context.get("attempt") or 0) == 1
            and previous_failure is None
            and not str(context.get("current_diff") or "")
        )
        if not eligible:
            return original_gateway_propose(self, task, context, previous_failure)

        try:
            bound = _active_bound(state, instruction=str(task.instruction))
        except (WorkflowMacroAmbiguityError, KeyError, TypeError, ValueError):
            return original_gateway_propose(self, task, context, previous_failure)
        if bound is None:
            return original_gateway_propose(self, task, context, previous_failure)

        state.bound = bound
        self._verified_workflow_action_queue = [
            copy.deepcopy(action) for action in bound.actions
        ]
        self._verified_workflow_local_action_inflight = False
        try:
            proposal = original_gateway_propose(self, task, context, previous_failure)
        except Exception as exc:
            if self._verified_workflow_local_action_inflight:
                reason = f"replay-action-failed:{type(exc).__name__}"
                state.replay_failure = reason
                state.compiler.record_execution(
                    state.compatibility,
                    bound,
                    ok=False,
                    reverified=False,
                    provider_calls_avoided=state.provider_calls_avoided,
                    executed_actions=state.executed_actions,
                )
                _quarantine(state, bound=bound, reason=reason)
                self._verified_workflow_action_queue = []
                self._verified_workflow_local_action_inflight = False
                state.bound = None
                try:
                    self.journal.emit(
                        "verified-workflow-quarantined",
                        macro_id=bound.macro.macro_id,
                        reason=reason,
                    )
                except Exception:
                    pass
                return original_gateway_propose(
                    self,
                    task,
                    context,
                    previous_failure,
                )
            raise

        self.trace.insert(
            0,
            {
                "round": 0,
                "action": "verified_workflow_replay",
                "macro_id": bound.macro.macro_id,
                "provider_calls_avoided": state.provider_calls_avoided,
                "executed_actions": state.executed_actions,
                "runtime_hash": workflow_runtime_hash(),
            },
        )
        return proposal

    @wraps(original_runtime_run)
    def runtime_run(self: Any, *args: Any, **kwargs: Any) -> Any:
        memory = getattr(self, "memory", None)
        if memory is None:
            return original_runtime_run(self, *args, **kwargs)

        instruction = str(args[0]) if args else str(kwargs.get("instruction") or "")
        compiler = VerifiedWorkflowCompiler(memory)
        state = _WorkflowRunState(compiler=compiler)
        compiler_token = _COMPILER.set(compiler)
        state_token = _RUN_STATE.set(state)
        try:
            try:
                product = original_runtime_run(self, *args, **kwargs)
            except Exception as exc:
                if state.bound is not None and state.replayed_ever:
                    reason = f"agent-runtime-failed:{type(exc).__name__}"
                    state.compiler.record_execution(
                        state.compatibility,
                        state.bound,
                        ok=False,
                        reverified=False,
                        provider_calls_avoided=state.provider_calls_avoided,
                        executed_actions=state.executed_actions,
                    )
                    _quarantine(state, bound=state.bound, reason=reason)
                raise

            passed = _verification_passed(product)
            receipt_hash = _verifier_receipt_hash(product)

            if state.bound is not None and state.replayed_ever:
                state.compiler.record_execution(
                    state.compatibility,
                    state.bound,
                    ok=passed,
                    reverified=passed,
                    verifier_receipt_hash=receipt_hash if passed else "",
                    provider_calls_avoided=state.provider_calls_avoided,
                    executed_actions=state.executed_actions,
                )
                if not passed:
                    _quarantine(
                        state,
                        bound=state.bound,
                        reason="fresh-verifier-failed",
                        verifier_receipt_hash=receipt_hash,
                    )
            elif (
                passed
                and state.compatibility is not None
                and state.observed_actions
                and instruction
            ):
                try:
                    state.compiler.record_verified_experience(
                        state.compatibility,
                        state.observed_actions,
                        parameters={"instruction": instruction},
                        task_reference=_task_reference(product, instruction),
                        verifier_receipt_hash=receipt_hash,
                    )
                except (KeyError, PermissionError, TypeError, ValueError):
                    pass
            return product
        finally:
            _RUN_STATE.reset(state_token)
            _COMPILER.reset(compiler_token)

    GatewayPatchProvider.__init__ = gateway_init
    GatewayPatchProvider._call_model = gateway_call_model
    GatewayPatchProvider.propose = gateway_propose
    AutonomousCodingAgent._inference_identity = inference_identity
    AgentRuntime.run = runtime_run
    AgentRuntime._syntavra_verified_workflow_gateway_v1 = True


__all__ = ["install", "workflow_runtime_hash"]
