from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .inference_skip_cache import InferenceSkipIdentity
from .session_memory import SessionMemory


SCHEMA_VERSION = 1
MIN_PROMOTION_EXPERIENCES = 2

READ_ONLY_WORKFLOW_ACTIONS = frozenset(
    {
        "search",
        "search_reduce",
        "search_inspect",
        "inspect",
        "diff",
        "impact",
        "verifiers",
        "run_verifier",
    }
)
MUTATING_WORKFLOW_ACTIONS = frozenset({"edit", "patch"})

_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "search": frozenset({"action", "query", "limit", "fields", "filters"}),
    "search_reduce": frozenset(
        {
            "action",
            "query",
            "operator",
            "field",
            "group_by",
            "limit",
            "fields",
            "filters",
            "descending",
            "sample_seed",
        }
    ),
    "search_inspect": frozenset({"action", "query", "fields", "context_lines"}),
    "inspect": frozenset({"action", "path", "paths", "start_line", "end_line"}),
    "diff": frozenset({"action"}),
    "impact": frozenset({"action", "node_id"}),
    "verifiers": frozenset({"action"}),
    "run_verifier": frozenset({"action", "name"}),
}
_FORBIDDEN_REFERENCE_KEYS = frozenset({"content", "payload", "body", "text", "secret", "raw_text"})
_PARAM_KEY = "$param"


class WorkflowMacroError(RuntimeError):
    pass


class WorkflowMacroAmbiguityError(WorkflowMacroError):
    pass


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("workflow macro values must be canonical JSON values") from exc


def _sha(value: Any) -> str:
    data = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(data).hexdigest()


def _require_sha256(value: str, *, name: str) -> str:
    normalized = str(value or "").strip().casefold()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{name} must be a lowercase sha256")
    return normalized


def _reference_only(value: Any, *, path: str = "reference") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_REFERENCE_KEYS:
                raise ValueError(f"{path} cannot carry raw authority: {key}")
            _reference_only(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reference_only(nested, path=f"{path}[{index}]")


def _parameter_node(name: str) -> dict[str, str]:
    return {_PARAM_KEY: name}


def _is_parameter_node(value: Any) -> bool:
    return isinstance(value, Mapping) and set(value) == {_PARAM_KEY}


def _reject_reserved_parameter_nodes(value: Any, *, path: str = "action") -> None:
    if _is_parameter_node(value):
        raise ValueError(f"{path} contains a reserved parameter node")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_reserved_parameter_nodes(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_reserved_parameter_nodes(nested, path=f"{path}[{index}]")


def _parameterize(value: Any, parameters: Mapping[str, Any], usage: dict[str, int]) -> Any:
    matches = [name for name, parameter in parameters.items() if value == parameter]
    if len(matches) > 1:
        raise ValueError("ambiguous workflow parameter values must be distinct")
    if len(matches) == 1:
        name = matches[0]
        usage[name] += 1
        return _parameter_node(name)
    if isinstance(value, Mapping):
        return {str(key): _parameterize(nested, parameters, usage) for key, nested in value.items()}
    if isinstance(value, list):
        return [_parameterize(nested, parameters, usage) for nested in value]
    if isinstance(value, tuple):
        return [_parameterize(nested, parameters, usage) for nested in value]
    return copy.deepcopy(value)


def _bind(value: Any, parameters: Mapping[str, Any]) -> Any:
    if _is_parameter_node(value):
        name = str(value[_PARAM_KEY])
        if name not in parameters:
            raise KeyError(f"missing workflow parameter: {name}")
        return copy.deepcopy(parameters[name])
    if isinstance(value, Mapping):
        return {str(key): _bind(nested, parameters) for key, nested in value.items()}
    if isinstance(value, list):
        return [_bind(nested, parameters) for nested in value]
    return copy.deepcopy(value)


def _validate_action(action: Mapping[str, Any], *, allow_parameter_nodes: bool = False) -> dict[str, Any]:
    if not isinstance(action, Mapping):
        raise TypeError("workflow action must be an object")
    normalized = copy.deepcopy(dict(action))
    name = str(normalized.get("action") or "").strip().casefold()
    if not name:
        raise ValueError("workflow action name is required")
    if name in MUTATING_WORKFLOW_ACTIONS:
        raise PermissionError(f"mutating workflow action is not macro-eligible: {name}")
    if name not in READ_ONLY_WORKFLOW_ACTIONS:
        raise ValueError(f"unsupported workflow macro action: {name}")
    normalized["action"] = name
    unknown = sorted(set(normalized) - set(_ALLOWED_FIELDS[name]))
    if unknown:
        raise ValueError(f"unknown fields for workflow action {name}: {','.join(unknown)}")
    if not allow_parameter_nodes:
        _reject_reserved_parameter_nodes(normalized)
    _canonical(normalized)
    return normalized


@dataclass(frozen=True)
class WorkflowCompatibilityIdentity:
    project_id: str
    repository_fingerprint: str
    verifier_hash: str
    policy_hash: str
    task_family_hash: str
    dependency_hash: str
    toolchain_hash: str
    environment_hash: str
    tool_schema_hash: str
    security_hash: str
    verifier_contract_hash: str

    def __post_init__(self) -> None:
        if not str(self.project_id).strip():
            raise ValueError("workflow compatibility requires project_id")
        for field_name in (
            "repository_fingerprint",
            "verifier_hash",
            "policy_hash",
            "task_family_hash",
            "dependency_hash",
            "toolchain_hash",
            "environment_hash",
            "tool_schema_hash",
            "security_hash",
            "verifier_contract_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_sha256(str(getattr(self, field_name)), name=field_name),
            )

    @classmethod
    def from_inference_identity(cls, identity: InferenceSkipIdentity) -> "WorkflowCompatibilityIdentity":
        return cls(
            project_id=identity.project_id,
            repository_fingerprint=identity.repository_fingerprint,
            verifier_hash=identity.verifier_hash,
            policy_hash=identity.policy_hash,
            task_family_hash=identity.task_family_hash,
            dependency_hash=identity.dependency_hash,
            toolchain_hash=identity.toolchain_hash,
            environment_hash=identity.environment_hash,
            tool_schema_hash=identity.tool_schema_hash,
            security_hash=identity.security_hash,
            verifier_contract_hash=identity.verifier_contract_hash,
        )

    @property
    def compatibility_hash(self) -> str:
        return _sha({"schema_version": SCHEMA_VERSION, "compatibility": asdict(self)})


@dataclass(frozen=True)
class WorkflowTemplate:
    parameter_names: tuple[str, ...]
    actions: tuple[dict[str, Any], ...]
    template_hash: str

    def __post_init__(self) -> None:
        names = tuple(str(name).strip() for name in self.parameter_names)
        if not names or len(names) != len(set(names)) or tuple(sorted(names)) != names:
            raise ValueError("workflow parameter names must be unique, sorted and non-empty")
        if any(not name or name.startswith("$") for name in names):
            raise ValueError("invalid workflow parameter name")
        if not self.actions:
            raise ValueError("workflow template requires at least one action")
        normalized = tuple(_validate_action(action, allow_parameter_nodes=True) for action in self.actions)
        expected = _sha(
            {
                "schema_version": SCHEMA_VERSION,
                "parameter_names": list(names),
                "actions": list(normalized),
            }
        )
        observed = _require_sha256(self.template_hash, name="template_hash")
        if observed != expected:
            raise ValueError("workflow template hash mismatch")


@dataclass(frozen=True)
class VerifiedWorkflowMacro:
    macro_id: str
    compatibility_hash: str
    template: WorkflowTemplate
    experience_ids: tuple[str, ...]
    verifier_receipt_hashes: tuple[str, ...]
    task_reference_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.macro_id, name="macro_id")
        _require_sha256(self.compatibility_hash, name="compatibility_hash")
        if len(self.experience_ids) < MIN_PROMOTION_EXPERIENCES:
            raise ValueError("verified workflow macro requires repeated experiences")
        if len(self.experience_ids) != len(set(self.experience_ids)):
            raise ValueError("workflow experience identities must be unique")
        if len(set(self.verifier_receipt_hashes)) < MIN_PROMOTION_EXPERIENCES:
            raise ValueError("workflow macro requires independent verifier receipts")
        if len(set(self.task_reference_hashes)) < MIN_PROMOTION_EXPERIENCES:
            raise ValueError("workflow macro requires independent task references")


@dataclass(frozen=True)
class BoundVerifiedWorkflow:
    macro: VerifiedWorkflowMacro
    parameters: dict[str, Any]
    actions: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class VerifiedWorkflowExecutionReceipt:
    schema_version: int
    macro_id: str
    compatibility_hash: str
    execution_id: str
    ok: bool
    reverified: bool
    verifier_receipt_hash: str
    provider_calls_avoided: int
    executed_actions: int


class VerifiedWorkflowCompiler:
    """Promote repeated verified read-only tool graphs into deterministic reusable workflows.

    SessionMemory remains the exact experience/promotion/execution authority. No
    parallel workflow database is introduced. Compatibility deliberately reuses
    TE-P0-10 identity components while excluding instruction identity so a
    parameterized workflow can serve multiple task instances under the same
    repository/toolchain/policy/security/verifier authority.
    """

    def __init__(self, memory: SessionMemory, *, min_experiences: int = MIN_PROMOTION_EXPERIENCES) -> None:
        self.memory = memory
        self.min_experiences = max(MIN_PROMOTION_EXPERIENCES, int(min_experiences))

    @staticmethod
    def compile_template(
        actions: Sequence[Mapping[str, Any]],
        *,
        parameters: Mapping[str, Any],
    ) -> WorkflowTemplate:
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, bytearray)):
            raise TypeError("workflow actions must be a sequence")
        if not actions:
            raise ValueError("workflow actions must not be empty")
        clean_parameters = {str(name).strip(): copy.deepcopy(value) for name, value in parameters.items()}
        if not clean_parameters or any(not name or name.startswith("$") for name in clean_parameters):
            raise ValueError("workflow parameters must be non-empty named values")
        _canonical(clean_parameters)
        if len({_canonical(value) for value in clean_parameters.values()}) != len(clean_parameters):
            raise ValueError("workflow parameter values must be distinct")

        usage = {name: 0 for name in clean_parameters}
        normalized: list[dict[str, Any]] = []
        for raw in actions:
            action = _validate_action(raw)
            normalized.append(_parameterize(action, clean_parameters, usage))
        unused = sorted(name for name, count in usage.items() if count == 0)
        if unused:
            raise ValueError("workflow parameters were not observed in the tool graph: " + ",".join(unused))
        names = tuple(sorted(clean_parameters))
        template_material = {
            "schema_version": SCHEMA_VERSION,
            "parameter_names": list(names),
            "actions": normalized,
        }
        return WorkflowTemplate(names, tuple(normalized), _sha(template_material))

    @staticmethod
    def bind(template: WorkflowTemplate, parameters: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        supplied = {str(name).strip(): copy.deepcopy(value) for name, value in parameters.items()}
        required = set(template.parameter_names)
        if set(supplied) != required:
            missing = sorted(required - set(supplied))
            extra = sorted(set(supplied) - required)
            raise ValueError(f"workflow parameter mismatch missing={missing} extra={extra}")
        _canonical(supplied)
        bound = tuple(_validate_action(_bind(action, supplied)) for action in template.actions)
        return bound

    @staticmethod
    def _session_id(compatibility: WorkflowCompatibilityIdentity) -> str:
        return "verified-workflow-" + compatibility.compatibility_hash[:40]

    @staticmethod
    def _macro_id(compatibility_hash: str, template_hash: str) -> str:
        return _sha(
            {
                "schema_version": SCHEMA_VERSION,
                "compatibility_hash": compatibility_hash,
                "template_hash": template_hash,
            }
        )

    @staticmethod
    def _template_payload(template: WorkflowTemplate) -> dict[str, Any]:
        return {
            "parameter_names": list(template.parameter_names),
            "actions": list(template.actions),
            "template_hash": template.template_hash,
        }

    @staticmethod
    def _template_from_payload(payload: Mapping[str, Any]) -> WorkflowTemplate:
        return WorkflowTemplate(
            parameter_names=tuple(payload.get("parameter_names") or ()),
            actions=tuple(copy.deepcopy(payload.get("actions") or ())),
            template_hash=str(payload.get("template_hash") or ""),
        )

    def _ensure_session(self, compatibility: WorkflowCompatibilityIdentity) -> str:
        session_id = self._session_id(compatibility)
        session = self.memory.open(
            session_id,
            metadata={
                "owner": "verified-workflow-macro-v1",
                "compatibility_hash": compatibility.compatibility_hash,
                "project_id": compatibility.project_id,
            },
        )
        metadata = session.get("metadata") or {}
        if str(metadata.get("compatibility_hash") or "") != compatibility.compatibility_hash:
            raise WorkflowMacroError("workflow session compatibility drift")
        return session_id

    def _events(self, compatibility: WorkflowCompatibilityIdentity) -> list[dict[str, Any]]:
        session_id = self._session_id(compatibility)
        try:
            description = self.memory.describe(session_id)
        except KeyError:
            return []
        metadata = description.get("metadata") or {}
        if str(metadata.get("compatibility_hash") or "") != compatibility.compatibility_hash:
            raise WorkflowMacroError("workflow session compatibility drift")
        if not self.memory.verify(session_id)["ok"]:
            raise WorkflowMacroError("workflow experience chain failed integrity verification")
        return self.memory.events(session_id)

    def record_verified_experience(
        self,
        compatibility: WorkflowCompatibilityIdentity,
        actions: Sequence[Mapping[str, Any]],
        *,
        parameters: Mapping[str, Any],
        task_reference: Mapping[str, Any],
        verifier_receipt_hash: str,
    ) -> VerifiedWorkflowMacro | None:
        verifier_hash = _require_sha256(verifier_receipt_hash, name="verifier_receipt_hash")
        reference = copy.deepcopy(dict(task_reference))
        _reference_only(reference, path="task_reference")
        reference_hash = _sha(reference)
        template = self.compile_template(actions, parameters=parameters)
        compatibility_hash = compatibility.compatibility_hash
        experience_id = _sha(
            {
                "schema_version": SCHEMA_VERSION,
                "compatibility_hash": compatibility_hash,
                "template_hash": template.template_hash,
                "task_reference_hash": reference_hash,
                "verifier_receipt_hash": verifier_hash,
            }
        )
        session_id = self._ensure_session(compatibility)
        existing = {
            str(event["payload"].get("experience_id") or "")
            for event in self.memory.events(session_id)
            if event["event_type"] == "verified-workflow-experience"
        }
        if experience_id not in existing:
            self.memory.append(
                session_id,
                "verified-workflow-experience",
                {
                    "schema_version": SCHEMA_VERSION,
                    "experience_id": experience_id,
                    "compatibility_hash": compatibility_hash,
                    "template": self._template_payload(template),
                    "task_reference": reference,
                    "task_reference_hash": reference_hash,
                    "verifier_receipt_hash": verifier_hash,
                },
            )
        return self.promote(compatibility, template.template_hash)

    def promote(
        self,
        compatibility: WorkflowCompatibilityIdentity,
        template_hash: str,
    ) -> VerifiedWorkflowMacro | None:
        expected_template_hash = _require_sha256(template_hash, name="template_hash")
        events = self._events(compatibility)
        experiences = [
            event["payload"]
            for event in events
            if event["event_type"] == "verified-workflow-experience"
            and str(event["payload"].get("template", {}).get("template_hash") or "") == expected_template_hash
        ]
        unique: dict[str, Mapping[str, Any]] = {}
        for payload in experiences:
            unique[str(payload.get("experience_id") or "")] = payload
        experiences = list(unique.values())
        task_hashes = {str(payload.get("task_reference_hash") or "") for payload in experiences}
        verifier_hashes = {str(payload.get("verifier_receipt_hash") or "") for payload in experiences}
        if (
            len(experiences) < self.min_experiences
            or len(task_hashes) < self.min_experiences
            or len(verifier_hashes) < self.min_experiences
        ):
            return None

        first_template = self._template_from_payload(experiences[0]["template"])
        if first_template.template_hash != expected_template_hash:
            raise WorkflowMacroError("workflow experience template identity drift")
        for payload in experiences[1:]:
            candidate = self._template_from_payload(payload["template"])
            if candidate != first_template:
                raise WorkflowMacroError("workflow experiences disagree on template material")

        compatibility_hash = compatibility.compatibility_hash
        macro_id = self._macro_id(compatibility_hash, expected_template_hash)
        macro = VerifiedWorkflowMacro(
            macro_id=macro_id,
            compatibility_hash=compatibility_hash,
            template=first_template,
            experience_ids=tuple(sorted(str(payload["experience_id"]) for payload in experiences)),
            verifier_receipt_hashes=tuple(sorted(verifier_hashes)),
            task_reference_hashes=tuple(sorted(task_hashes)),
        )
        session_id = self._ensure_session(compatibility)
        prior_promotions = [
            event["payload"]
            for event in self.memory.events(session_id)
            if event["event_type"] == "verified-workflow-promotion"
            and str(event["payload"].get("macro_id") or "") == macro_id
        ]
        latest_ids = tuple(sorted(str(item) for item in macro.experience_ids))
        if not prior_promotions or tuple(sorted(prior_promotions[-1].get("experience_ids") or ())) != latest_ids:
            self.memory.append(
                session_id,
                "verified-workflow-promotion",
                {
                    "schema_version": SCHEMA_VERSION,
                    "macro_id": macro.macro_id,
                    "compatibility_hash": macro.compatibility_hash,
                    "template": self._template_payload(macro.template),
                    "experience_ids": list(macro.experience_ids),
                    "verifier_receipt_hashes": list(macro.verifier_receipt_hashes),
                    "task_reference_hashes": list(macro.task_reference_hashes),
                    "min_experiences": self.min_experiences,
                },
            )
        return macro

    def promoted_macros(
        self,
        compatibility: WorkflowCompatibilityIdentity,
    ) -> tuple[VerifiedWorkflowMacro, ...]:
        events = self._events(compatibility)
        latest: dict[str, Mapping[str, Any]] = {}
        for event in events:
            if event["event_type"] == "verified-workflow-promotion":
                payload = event["payload"]
                macro_id = str(payload.get("macro_id") or "")
                latest[macro_id] = payload
        macros: list[VerifiedWorkflowMacro] = []
        for macro_id, payload in latest.items():
            compatibility_hash = _require_sha256(
                str(payload.get("compatibility_hash") or ""),
                name="compatibility_hash",
            )
            if compatibility_hash != compatibility.compatibility_hash:
                continue
            template = self._template_from_payload(payload.get("template") or {})
            expected_macro_id = self._macro_id(compatibility_hash, template.template_hash)
            if _require_sha256(macro_id, name="macro_id") != expected_macro_id:
                raise WorkflowMacroError("workflow macro identity drift")
            macros.append(
                VerifiedWorkflowMacro(
                    macro_id=macro_id,
                    compatibility_hash=compatibility_hash,
                    template=template,
                    experience_ids=tuple(payload.get("experience_ids") or ()),
                    verifier_receipt_hashes=tuple(payload.get("verifier_receipt_hashes") or ()),
                    task_reference_hashes=tuple(payload.get("task_reference_hashes") or ()),
                )
            )
        return tuple(sorted(macros, key=lambda item: item.macro_id))

    def resolve(
        self,
        compatibility: WorkflowCompatibilityIdentity,
        *,
        parameters: Mapping[str, Any],
        macro_id: str = "",
    ) -> BoundVerifiedWorkflow | None:
        macros = list(self.promoted_macros(compatibility))
        if macro_id:
            target = _require_sha256(macro_id, name="macro_id")
            macros = [macro for macro in macros if macro.macro_id == target]
        if not macros:
            return None
        if len(macros) > 1:
            raise WorkflowMacroAmbiguityError(
                "multiple verified workflow macros are compatible; explicit macro_id is required"
            )
        macro = macros[0]
        actions = self.bind(macro.template, parameters)
        return BoundVerifiedWorkflow(macro=macro, parameters=dict(parameters), actions=actions)

    def record_execution(
        self,
        compatibility: WorkflowCompatibilityIdentity,
        bound: BoundVerifiedWorkflow,
        *,
        ok: bool,
        reverified: bool,
        verifier_receipt_hash: str = "",
        provider_calls_avoided: int = 0,
        executed_actions: int | None = None,
    ) -> VerifiedWorkflowExecutionReceipt:
        if bound.macro.compatibility_hash != compatibility.compatibility_hash:
            raise WorkflowMacroError("workflow execution compatibility drift")
        calls_avoided = int(provider_calls_avoided)
        action_count = len(bound.actions) if executed_actions is None else int(executed_actions)
        if calls_avoided < 0 or action_count < 0:
            raise ValueError("workflow execution counters must be non-negative")
        verifier_hash = ""
        if ok:
            if not reverified:
                raise WorkflowMacroError("successful workflow macro execution requires fresh re-verification")
            verifier_hash = _require_sha256(verifier_receipt_hash, name="verifier_receipt_hash")
        elif verifier_receipt_hash:
            verifier_hash = _require_sha256(verifier_receipt_hash, name="verifier_receipt_hash")

        execution_material = {
            "schema_version": SCHEMA_VERSION,
            "macro_id": bound.macro.macro_id,
            "compatibility_hash": compatibility.compatibility_hash,
            "parameters_hash": _sha(bound.parameters),
            "actions_hash": _sha(list(bound.actions)),
            "ok": bool(ok),
            "reverified": bool(reverified),
            "verifier_receipt_hash": verifier_hash,
            "provider_calls_avoided": calls_avoided,
            "executed_actions": action_count,
        }
        execution_id = _sha(execution_material)
        receipt = VerifiedWorkflowExecutionReceipt(
            schema_version=SCHEMA_VERSION,
            macro_id=bound.macro.macro_id,
            compatibility_hash=compatibility.compatibility_hash,
            execution_id=execution_id,
            ok=bool(ok),
            reverified=bool(reverified),
            verifier_receipt_hash=verifier_hash,
            provider_calls_avoided=calls_avoided,
            executed_actions=action_count,
        )
        session_id = self._ensure_session(compatibility)
        existing = {
            str(event["payload"].get("execution_id") or "")
            for event in self.memory.events(session_id)
            if event["event_type"] == "verified-workflow-execution"
        }
        if execution_id not in existing:
            self.memory.append(session_id, "verified-workflow-execution", asdict(receipt))
        return receipt


__all__ = [
    "MIN_PROMOTION_EXPERIENCES",
    "MUTATING_WORKFLOW_ACTIONS",
    "READ_ONLY_WORKFLOW_ACTIONS",
    "BoundVerifiedWorkflow",
    "VerifiedWorkflowCompiler",
    "VerifiedWorkflowExecutionReceipt",
    "VerifiedWorkflowMacro",
    "WorkflowCompatibilityIdentity",
    "WorkflowMacroAmbiguityError",
    "WorkflowMacroError",
    "WorkflowTemplate",
]
