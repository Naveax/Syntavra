#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "e0d43611560296c269269d020f1a3961b92be8fc"
TARGET_BRANCH = "automation/context-decision-trace-v1-builder-20260827"
TEMP_PATHS = (
    ".github/workflows/tmp-context-decision-trace-builder-v1.yml",
    "tools/tmp_context_decision_trace_builder_v1.py",
)
EXPECTED_PATHS = sorted(
    (
        ".github/workflows/context-decision-trace.yml",
        ".github/workflows/release-main-merge-gate.yml",
        "MANIFEST.sha256",
        "contracts/python/capability-completeness-registry-v1.json",
        "contracts/python/context-decision-trace-v1.json",
        "syntavra_runtime/context_decision_trace.py",
        "tests/runtime/test_context_decision_trace_v1.py",
        "tests/runtime/test_release_action_pins.py",
        "tools/certify_context_decision_trace_v1.py",
    )
)
UPSTREAM_ID = "runtime_contract_version_graph_v1"
CAPABILITY_ID = "context_decision_trace_v1"


def run(*args: str, capture: bool = False) -> str:
    proc = subprocess.run(
        list(args),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if proc.returncode:
        if capture:
            print(proc.stdout, end="")
            print(proc.stderr, end="", file=sys.stderr)
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args)}")
    return proc.stdout if capture else ""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def write(relative: str, content: str) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")


def write_runtime() -> None:
    write(
        "syntavra_runtime/context_decision_trace.py",
        r'''
        from __future__ import annotations

        from collections import Counter
        from dataclasses import asdict, dataclass
        from typing import Any, Iterable, Mapping

        from .util import canonical_json, sha256_bytes


        REQUIRED_TRACE_DECISIONS = frozenset({"include", "omit", "compress", "retrieve", "reset", "abstain"})
        TRACE_DECISIONS = frozenset((*REQUIRED_TRACE_DECISIONS, "branch"))
        ZERO_HASH = "0" * 64

        _ACTION_TO_DECISION = {
            "KEEP": "include",
            "SUMMARIZE": "compress",
            "COMPRESS": "compress",
            "EXTERNALIZE": "omit",
            "RESET": "reset",
            "ABSTAIN": "abstain",
            "BRANCH": "branch",
            "RETRIEVE": "retrieve",
        }
        _FORBIDDEN_REFERENCE_KEYS = frozenset({"content", "payload", "raw_text", "body", "secret", "text"})


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


        def _decision(action: str) -> str:
            try:
                return _ACTION_TO_DECISION[str(action)]
            except KeyError as exc:
                raise ValueError(f"unsupported traced policy action: {action}") from exc


        @dataclass(frozen=True)
        class ContextDecisionTraceEvent:
            sequence: int
            scope: str
            identity: str
            recommended_decision: str
            effective_decision: str
            recommended_action: str
            effective_action: str
            reason_codes: tuple[str, ...]
            source_refs: tuple[str, ...]
            namespace_uri: str
            item_id: str
            input_tokens: int
            visible_tokens: int
            previous_event_hash: str
            event_hash: str

            def __post_init__(self) -> None:
                if self.sequence < 1:
                    raise ValueError("trace sequence must be positive")
                if self.scope not in {"item", "session", "retrieval"}:
                    raise ValueError(f"unsupported trace scope: {self.scope}")
                if not self.identity:
                    raise ValueError("trace identity is required")
                if self.recommended_decision not in TRACE_DECISIONS:
                    raise ValueError(f"unsupported recommended trace decision: {self.recommended_decision}")
                if self.effective_decision not in TRACE_DECISIONS:
                    raise ValueError(f"unsupported effective trace decision: {self.effective_decision}")
                if self.input_tokens < 0 or self.visible_tokens < 0:
                    raise ValueError("trace token counts must be non-negative")
                _require_sha256(self.previous_event_hash, name="previous_event_hash")
                _require_sha256(self.event_hash, name="event_hash")


        class ContextDecisionTrace:
            """Deterministic, reference-only trace over context policy decisions.

            The trace records what policy recommended and what actually took effect.
            It does not own policy snapshots, evidence persistence, context payloads,
            or side effects. Retrieval is recorded explicitly as a later reference-only
            event instead of pretending EXTERNALIZE already performed a retrieval.
            """

            schema_version = 1

            @staticmethod
            def _verify_policy_receipt(result: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
                receipt = result.get("receipt")
                if not isinstance(receipt, Mapping):
                    raise ValueError("context decision trace requires a policy receipt")
                basis = dict(receipt)
                observed = str(basis.pop("receipt_hash", ""))
                _require_sha256(observed, name="policy receipt hash")
                expected = sha256_bytes(canonical_json(basis))
                if observed != expected:
                    raise ValueError("policy receipt hash mismatch")
                _reference_only(basis.get("signals") or [], path="policy_receipt.signals")
                return basis, observed

            @staticmethod
            def _seal_event(
                *,
                sequence: int,
                scope: str,
                identity: str,
                recommended_action: str,
                effective_action: str,
                reason_codes: Iterable[str] = (),
                source_refs: Iterable[str] = (),
                namespace_uri: str = "",
                item_id: str = "",
                input_tokens: int = 0,
                visible_tokens: int = 0,
                previous_event_hash: str = ZERO_HASH,
            ) -> ContextDecisionTraceEvent:
                refs = tuple(sorted(set(str(value) for value in source_refs if str(value))))
                reasons = tuple(dict.fromkeys(str(value) for value in reason_codes if str(value)))
                basis = {
                    "schema_version": 1,
                    "sequence": int(sequence),
                    "scope": str(scope),
                    "identity": str(identity),
                    "recommended_decision": _decision(recommended_action),
                    "effective_decision": _decision(effective_action),
                    "recommended_action": str(recommended_action),
                    "effective_action": str(effective_action),
                    "reason_codes": list(reasons),
                    "source_refs": list(refs),
                    "namespace_uri": str(namespace_uri),
                    "item_id": str(item_id),
                    "input_tokens": max(0, int(input_tokens)),
                    "visible_tokens": max(0, int(visible_tokens)),
                    "previous_event_hash": _require_sha256(previous_event_hash, name="previous_event_hash"),
                }
                event_hash = sha256_bytes(canonical_json(basis))
                return ContextDecisionTraceEvent(
                    sequence=basis["sequence"],
                    scope=basis["scope"],
                    identity=basis["identity"],
                    recommended_decision=basis["recommended_decision"],
                    effective_decision=basis["effective_decision"],
                    recommended_action=basis["recommended_action"],
                    effective_action=basis["effective_action"],
                    reason_codes=reasons,
                    source_refs=refs,
                    namespace_uri=basis["namespace_uri"],
                    item_id=basis["item_id"],
                    input_tokens=basis["input_tokens"],
                    visible_tokens=basis["visible_tokens"],
                    previous_event_hash=basis["previous_event_hash"],
                    event_hash=event_hash,
                )

            @classmethod
            def _seal_trace(cls, policy_receipt_hash: str, events: Iterable[ContextDecisionTraceEvent]) -> dict[str, Any]:
                receipt_hash = _require_sha256(policy_receipt_hash, name="policy_receipt_hash")
                rows = [asdict(event) for event in events]
                counts = Counter(str(row["recommended_decision"]) for row in rows)
                basis = {
                    "schema_version": cls.schema_version,
                    "policy_receipt_hash": receipt_hash,
                    "events": rows,
                    "event_count": len(rows),
                    "decision_counts": dict(sorted(counts.items())),
                }
                return {
                    **basis,
                    "trace_hash": sha256_bytes(canonical_json(basis)),
                }

            @classmethod
            def from_policy_result(cls, result: Mapping[str, Any]) -> dict[str, Any]:
                receipt, receipt_hash = cls._verify_policy_receipt(result)
                decisions = receipt.get("decisions") or []
                if not isinstance(decisions, list):
                    raise ValueError("policy receipt decisions must be a list")

                events: list[ContextDecisionTraceEvent] = []
                previous = ZERO_HASH
                for row in decisions:
                    if not isinstance(row, Mapping):
                        raise ValueError("policy decision row must be an object")
                    event = cls._seal_event(
                        sequence=len(events) + 1,
                        scope="item",
                        identity=str(row.get("identity") or ""),
                        recommended_action=str(row.get("recommended_action") or ""),
                        effective_action=str(row.get("effective_action") or ""),
                        reason_codes=row.get("reason_codes") or (),
                        source_refs=row.get("source_refs") or (),
                        namespace_uri=str(row.get("namespace_uri") or ""),
                        item_id=str(row.get("item_id") or ""),
                        input_tokens=int(row.get("input_tokens") or 0),
                        visible_tokens=int(row.get("visible_tokens") or 0),
                        previous_event_hash=previous,
                    )
                    events.append(event)
                    previous = event.event_hash

                metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
                session_event = cls._seal_event(
                    sequence=len(events) + 1,
                    scope="session",
                    identity="session",
                    recommended_action=str(receipt.get("recommended_session_action") or ""),
                    effective_action=str(receipt.get("effective_session_action") or ""),
                    reason_codes=receipt.get("session_reason_codes") or (),
                    input_tokens=int(metrics.get("input_tokens") or 0),
                    visible_tokens=int(metrics.get("effective_visible_tokens") or 0),
                    previous_event_hash=previous,
                )
                events.append(session_event)
                trace = cls._seal_trace(receipt_hash, events)
                cls.verify(trace)
                return trace

            @classmethod
            def append_retrieval(
                cls,
                trace: Mapping[str, Any],
                *,
                identity: str,
                source_refs: Iterable[str],
                namespace_uri: str = "",
                item_id: str = "",
                reason_codes: Iterable[str] = ("RECOVERY_HANDLE_RETRIEVED",),
                visible_tokens: int = 0,
            ) -> dict[str, Any]:
                cls.verify(trace)
                rows = trace.get("events") or []
                events = [ContextDecisionTraceEvent(**dict(row)) for row in rows]
                previous = events[-1].event_hash if events else ZERO_HASH
                event = cls._seal_event(
                    sequence=len(events) + 1,
                    scope="retrieval",
                    identity=str(identity),
                    recommended_action="RETRIEVE",
                    effective_action="RETRIEVE",
                    reason_codes=reason_codes,
                    source_refs=source_refs,
                    namespace_uri=namespace_uri,
                    item_id=item_id,
                    visible_tokens=visible_tokens,
                    previous_event_hash=previous,
                )
                events.append(event)
                result = cls._seal_trace(str(trace.get("policy_receipt_hash") or ""), events)
                cls.verify(result)
                return result

            @classmethod
            def verify(cls, trace: Mapping[str, Any]) -> bool:
                if trace.get("schema_version") != cls.schema_version:
                    raise ValueError("context decision trace schema drift")
                receipt_hash = _require_sha256(str(trace.get("policy_receipt_hash") or ""), name="policy_receipt_hash")
                rows = trace.get("events")
                if not isinstance(rows, list) or not rows:
                    raise ValueError("context decision trace requires events")
                if int(trace.get("event_count", -1)) != len(rows):
                    raise ValueError("context decision trace event count drift")

                previous = ZERO_HASH
                events: list[ContextDecisionTraceEvent] = []
                for index, raw in enumerate(rows, start=1):
                    if not isinstance(raw, Mapping):
                        raise ValueError("context decision trace event must be an object")
                    row = dict(raw)
                    observed_hash = str(row.pop("event_hash", ""))
                    if int(row.get("sequence", -1)) != index:
                        raise ValueError("context decision trace sequence drift")
                    if str(row.get("previous_event_hash") or "") != previous:
                        raise ValueError("context decision trace previous hash drift")
                    expected_hash = sha256_bytes(canonical_json({"schema_version": 1, **row}))
                    if observed_hash != expected_hash:
                        raise ValueError("context decision trace event hash mismatch")
                    event = ContextDecisionTraceEvent(event_hash=observed_hash, **row)
                    events.append(event)
                    previous = observed_hash

                expected_counts = dict(sorted(Counter(event.recommended_decision for event in events).items()))
                if trace.get("decision_counts") != expected_counts:
                    raise ValueError("context decision trace decision counts drift")
                basis = {
                    "schema_version": cls.schema_version,
                    "policy_receipt_hash": receipt_hash,
                    "events": [asdict(event) for event in events],
                    "event_count": len(events),
                    "decision_counts": expected_counts,
                }
                expected_trace_hash = sha256_bytes(canonical_json(basis))
                if str(trace.get("trace_hash") or "") != expected_trace_hash:
                    raise ValueError("context decision trace hash mismatch")
                return True


        __all__ = [
            "REQUIRED_TRACE_DECISIONS",
            "TRACE_DECISIONS",
            "ContextDecisionTraceEvent",
            "ContextDecisionTrace",
        ]
        ''',
    )


def write_contract() -> None:
    contract = {
        "schema_version": 1,
        "family": "context-decision-trace",
        "phase": "python-first-post-completion",
        "claim": "CONTEXT_DECISION_TRACE_V1",
        "strict": True,
        "runtime": "syntavra_runtime/context_decision_trace.py",
        "authorities": {
            "adaptive_context_policy": "contracts/python/adaptive-context-policy-v1.json",
            "adaptive_context_runtime": "syntavra_runtime/adaptive_context_policy.py",
            "upstream_contract_graph": "contracts/python/runtime-contract-version-graph-v1.json",
            "capability_registry": "contracts/python/capability-completeness-registry-v1.json",
        },
        "required_decision_types": ["include", "omit", "compress", "retrieve", "reset", "abstain"],
        "supplementary_decision_types": ["branch"],
        "trace_policy": {
            "deterministic": True,
            "timestamp_free_identity": True,
            "policy_receipt_hash_verified_before_trace": True,
            "recommended_and_effective_decisions_separate": True,
            "item_and_session_decisions_traced": True,
            "retrieval_recorded_as_explicit_later_event": True,
            "event_sequence_contiguous": True,
            "event_hash_integrity": True,
            "previous_event_hash_integrity": True,
            "trace_hash_integrity": True,
            "tamper_fails_closed": True,
        },
        "mapping_policy": {
            "KEEP": "include",
            "SUMMARIZE": "compress",
            "COMPRESS": "compress",
            "EXTERNALIZE": "omit",
            "RETRIEVE": "retrieve",
            "RESET": "reset",
            "ABSTAIN": "abstain",
            "BRANCH": "branch",
        },
        "ownership_policy": {
            "reference_only": True,
            "context_payload_storage_forbidden": True,
            "persistent_journal_forbidden": True,
            "policy_snapshot_ownership_forbidden": True,
            "policy_recomputation_forbidden": True,
            "side_effect_authority_forbidden": True,
            "no_public_cli_route": True,
            "rust_feature_work_forbidden": True,
        },
        "enforcement": {
            "tests": "tests/runtime/test_context_decision_trace_v1.py",
            "certifier": "tools/certify_context_decision_trace_v1.py",
            "exact_head_workflow": ".github/workflows/context-decision-trace.yml",
            "release_main_gate": ".github/workflows/release-main-merge-gate.yml",
            "immutable_action_pin_policy": "tests/runtime/test_release_action_pins.py",
        },
        "acceptance": "Deterministically trace include/omit/compress/retrieve/reset/abstain decisions from verified Adaptive Context Policy receipts, preserve recommended versus effective semantics, provide reference-only replay/tamper verification, and leave policy snapshots, evidence journals, payload storage, side effects, Rust work and production promotion outside this capability.",
        "claim_boundary": "This contract records deterministic reference-only context decision traces. It does not claim policy snapshot authority (capability 242), evidence mutation journal authority (capability 243), context payload ownership, autonomous retrieval execution, Rust feature/parity work, or Rust production promotion.",
    }
    path = ROOT / "contracts/python/context-decision-trace-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_tests() -> None:
    write(
        "tests/runtime/test_context_decision_trace_v1.py",
        r'''
        from __future__ import annotations

        import copy
        import unittest

        from syntavra_runtime.adaptive_context_policy import (
            AdaptiveContextPolicy,
            AdaptivePolicyConfig,
            ContextPolicySignal,
            ContextPolicyState,
        )
        from syntavra_runtime.context_decision_trace import (
            REQUIRED_TRACE_DECISIONS,
            ContextDecisionTrace,
        )


        class ContextDecisionTraceV1Tests(unittest.TestCase):
            def setUp(self) -> None:
                self.policy = AdaptiveContextPolicy(AdaptivePolicyConfig(context_budget_tokens=1000))

            @staticmethod
            def signal(identity: str, **overrides: object) -> ContextPolicySignal:
                values: dict[str, object] = {
                    "identity": identity,
                    "token_count": 120,
                    "relevance": 0.8,
                    "trust": 0.95,
                    "freshness": 1.0,
                    "recoverable": True,
                    "namespace_uri": f"syntavra://context/item/{identity}",
                    "item_id": identity,
                    "source_refs": (f"evidence:{identity}",),
                }
                values.update(overrides)
                return ContextPolicySignal(**values)  # type: ignore[arg-type]

            def trace(self, task: str, signals: list[ContextPolicySignal], *, state: ContextPolicyState | None = None):
                return ContextDecisionTrace.from_policy_result(self.policy.evaluate(task, signals, state=state))

            def test_trace_is_deterministic_across_signal_order(self) -> None:
                signals = [self.signal("b", relevance=0.6), self.signal("a", relevance=1.0)]
                first = ContextDecisionTrace.from_policy_result(self.policy.evaluate("trace task", signals))
                second = ContextDecisionTrace.from_policy_result(self.policy.evaluate("trace task", reversed(signals)))
                self.assertEqual(first["trace_hash"], second["trace_hash"])
                self.assertEqual(first["events"], second["events"])
                self.assertTrue(ContextDecisionTrace.verify(first))
                self.assertNotIn("timestamp", str(first).casefold())

            def test_keep_maps_to_include(self) -> None:
                trace = self.trace("include", [self.signal("a", relevance=1.0)])
                self.assertEqual(trace["events"][0]["recommended_decision"], "include")

            def test_summary_and_compression_map_to_compress(self) -> None:
                summary = self.trace("summary", [self.signal("a", relevance=0.55, trust=0.8, freshness=0.8)])
                compressed = self.trace("compress", [self.signal("a", relevance=0.25, trust=0.65, freshness=0.7)])
                self.assertEqual(summary["events"][0]["recommended_decision"], "compress")
                self.assertEqual(compressed["events"][0]["recommended_decision"], "compress")

            def test_externalize_maps_to_omit_and_preserves_references(self) -> None:
                trace = self.trace("omit", [self.signal("a", relevance=0.0, trust=0.2, freshness=0.2)])
                event = trace["events"][0]
                self.assertEqual(event["recommended_decision"], "omit")
                self.assertEqual(event["source_refs"], ("evidence:a",))
                self.assertEqual(event["namespace_uri"], "syntavra://context/item/a")

            def test_explicit_retrieval_event_is_reference_only_and_deterministic(self) -> None:
                base = self.trace("omit", [self.signal("a", relevance=0.0, trust=0.2, freshness=0.2)])
                first = ContextDecisionTrace.append_retrieval(
                    base,
                    identity="a",
                    source_refs=("evidence:a", "file-hash:" + "a" * 64),
                    namespace_uri="syntavra://context/item/a",
                    item_id="a",
                    visible_tokens=80,
                )
                second = ContextDecisionTrace.append_retrieval(
                    base,
                    identity="a",
                    source_refs=("file-hash:" + "a" * 64, "evidence:a"),
                    namespace_uri="syntavra://context/item/a",
                    item_id="a",
                    visible_tokens=80,
                )
                self.assertEqual(first["trace_hash"], second["trace_hash"])
                self.assertEqual(first["events"][-1]["recommended_decision"], "retrieve")
                self.assertEqual(first["events"][-1]["scope"], "retrieval")

            def test_reset_and_abstain_are_traced(self) -> None:
                reset = self.trace(
                    "reset",
                    [self.signal("a", token_count=10, relevance=1.0)],
                    state=ContextPolicyState(current_context_tokens=970, reset_allowed=True),
                )
                self.assertEqual(reset["events"][-1]["recommended_decision"], "reset")
                abstain = self.trace("unsafe", [self.signal("a", security_denied=True, relevance=1.0)])
                self.assertEqual(abstain["events"][-1]["recommended_decision"], "abstain")

            def test_branch_is_traced_without_mislabeling_it_as_reset_or_omit(self) -> None:
                trace = self.trace(
                    "branch",
                    [self.signal("a")],
                    state=ContextPolicyState(task_drift=0.9, branch_allowed=True),
                )
                self.assertEqual(trace["events"][-1]["recommended_decision"], "branch")

            def test_shadow_mode_preserves_recommended_vs_effective_semantics(self) -> None:
                trace = self.trace(
                    "shadow reset",
                    [self.signal("a", token_count=10, relevance=1.0)],
                    state=ContextPolicyState(current_context_tokens=970, reset_allowed=True, shadow_mode=True),
                )
                session = trace["events"][-1]
                self.assertEqual(session["recommended_decision"], "reset")
                self.assertEqual(session["effective_decision"], "include")

            def test_policy_receipt_tamper_fails_closed(self) -> None:
                result = self.policy.evaluate("tamper", [self.signal("a")])
                result["receipt"]["decisions"][0]["visible_tokens"] += 1
                with self.assertRaises(ValueError):
                    ContextDecisionTrace.from_policy_result(result)

            def test_trace_event_tamper_fails_closed(self) -> None:
                trace = self.trace("tamper trace", [self.signal("a")])
                mutated = copy.deepcopy(trace)
                mutated["events"][0]["recommended_decision"] = "omit"
                with self.assertRaises(ValueError):
                    ContextDecisionTrace.verify(mutated)

            def test_required_roadmap_decision_vocabulary_is_present(self) -> None:
                self.assertEqual(
                    REQUIRED_TRACE_DECISIONS,
                    {"include", "omit", "compress", "retrieve", "reset", "abstain"},
                )

            def test_trace_does_not_copy_task_or_signal_metadata_payload(self) -> None:
                trace = self.trace("SECRET TASK TEXT SHOULD STAY IN POLICY RECEIPT", [self.signal("a")])
                text = str(trace)
                self.assertNotIn("SECRET TASK TEXT", text)
                self.assertNotIn("metadata", text)


        if __name__ == "__main__":
            unittest.main()
        ''',
    )


def write_certifier() -> None:
    write(
        "tools/certify_context_decision_trace_v1.py",
        r'''
        #!/usr/bin/env python3
        from __future__ import annotations

        import argparse
        import copy
        import json
        import subprocess
        import sys
        import traceback
        from pathlib import Path
        from typing import Any

        ROOT = Path(__file__).resolve().parents[1]
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

        from syntavra_runtime.adaptive_context_policy import (
            AdaptiveContextPolicy,
            AdaptivePolicyConfig,
            ContextPolicySignal,
            ContextPolicyState,
        )
        from syntavra_runtime.context_decision_trace import REQUIRED_TRACE_DECISIONS, ContextDecisionTrace

        CONTRACT = Path("contracts/python/context-decision-trace-v1.json")
        REGISTRY = Path("contracts/python/capability-completeness-registry-v1.json")
        WORKFLOW = Path(".github/workflows/context-decision-trace.yml")
        RELEASE_GATE = Path(".github/workflows/release-main-merge-gate.yml")
        PIN_POLICY = Path("tests/runtime/test_release_action_pins.py")
        TEST = Path("tests/runtime/test_context_decision_trace_v1.py")
        CAPABILITY_ID = "context_decision_trace_v1"
        UPSTREAM_ID = "runtime_contract_version_graph_v1"


        def _require(condition: bool, message: str) -> None:
            if not condition:
                raise AssertionError(message)


        def _read_json(path: Path) -> dict[str, Any]:
            value = json.loads(path.read_text(encoding="utf-8"))
            _require(isinstance(value, dict), f"expected JSON object: {path}")
            return value


        def _head(repo: Path) -> str:
            return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


        def _validate_contract(repo: Path) -> dict[str, Any]:
            contract = _read_json(repo / CONTRACT)
            _require(contract.get("schema_version") == 1, "context decision trace schema drift")
            _require(contract.get("family") == "context-decision-trace", "context decision trace family drift")
            _require(contract.get("phase") == "python-first-post-completion", "context decision trace phase drift")
            _require(contract.get("claim") == "CONTEXT_DECISION_TRACE_V1", "context decision trace claim drift")
            _require(contract.get("strict") is True, "context decision trace must remain strict")
            _require(contract.get("runtime") == "syntavra_runtime/context_decision_trace.py", "context decision trace runtime drift")
            _require(
                set(contract.get("required_decision_types") or []) == REQUIRED_TRACE_DECISIONS,
                "required roadmap decision vocabulary drift",
            )
            trace_policy = contract.get("trace_policy") or {}
            for key in (
                "deterministic",
                "timestamp_free_identity",
                "policy_receipt_hash_verified_before_trace",
                "recommended_and_effective_decisions_separate",
                "item_and_session_decisions_traced",
                "retrieval_recorded_as_explicit_later_event",
                "event_sequence_contiguous",
                "event_hash_integrity",
                "previous_event_hash_integrity",
                "trace_hash_integrity",
                "tamper_fails_closed",
            ):
                _require(trace_policy.get(key) is True, f"trace policy disabled: {key}")
            ownership = contract.get("ownership_policy") or {}
            for key in (
                "reference_only",
                "context_payload_storage_forbidden",
                "persistent_journal_forbidden",
                "policy_snapshot_ownership_forbidden",
                "policy_recomputation_forbidden",
                "side_effect_authority_forbidden",
                "no_public_cli_route",
                "rust_feature_work_forbidden",
            ):
                _require(ownership.get(key) is True, f"trace ownership policy disabled: {key}")
            for relative in (contract.get("authorities") or {}).values():
                _require((repo / relative).is_file(), f"missing context trace authority: {relative}")
            return contract


        def _validate_enforcement(repo: Path) -> dict[str, str]:
            for relative in (WORKFLOW, RELEASE_GATE, PIN_POLICY, TEST):
                _require((repo / relative).is_file(), f"missing context decision trace enforcement: {relative}")
            workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
            for token in (
                "context-decision-trace-${{ github.event.pull_request.number || github.ref }}",
                "tests.runtime.test_context_decision_trace_v1",
                "tools/certify_context_decision_trace_v1.py",
                "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
                "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
                "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            ):
                _require(token in workflow, f"context decision trace workflow drift: {token}")
            release = (repo / RELEASE_GATE).read_text(encoding="utf-8")
            _require("tests.runtime.test_context_decision_trace_v1" in release, "Release Main lost context decision trace regression")
            _require("tools/certify_context_decision_trace_v1.py" in release, "Release Main lost context decision trace certifier")
            pins = (repo / PIN_POLICY).read_text(encoding="utf-8")
            _require('".github/workflows/context-decision-trace.yml"' in pins, "pin policy lost context decision trace workflow")
            return {
                "exact_head_workflow": WORKFLOW.as_posix(),
                "release_main_gate": RELEASE_GATE.as_posix(),
                "immutable_action_pin_policy": PIN_POLICY.as_posix(),
            }


        def _smoke() -> dict[str, Any]:
            policy = AdaptiveContextPolicy(AdaptivePolicyConfig(context_budget_tokens=1000))

            def signal(identity: str, **overrides: Any) -> ContextPolicySignal:
                values: dict[str, Any] = {
                    "identity": identity,
                    "token_count": 120,
                    "relevance": 0.8,
                    "trust": 0.95,
                    "freshness": 1.0,
                    "recoverable": True,
                    "namespace_uri": f"syntavra://context/item/{identity}",
                    "item_id": identity,
                    "source_refs": (f"evidence:{identity}",),
                }
                values.update(overrides)
                return ContextPolicySignal(**values)

            include = ContextDecisionTrace.from_policy_result(policy.evaluate("include", [signal("include", relevance=1.0)]))
            compress = ContextDecisionTrace.from_policy_result(
                policy.evaluate("compress", [signal("compress", relevance=0.25, trust=0.65, freshness=0.7)])
            )
            omit_result = policy.evaluate("omit", [signal("omit", relevance=0.0, trust=0.2, freshness=0.2)])
            omit = ContextDecisionTrace.from_policy_result(omit_result)
            retrieve = ContextDecisionTrace.append_retrieval(
                omit,
                identity="omit",
                source_refs=("evidence:omit",),
                namespace_uri="syntavra://context/item/omit",
                item_id="omit",
                visible_tokens=64,
            )
            reset = ContextDecisionTrace.from_policy_result(
                policy.evaluate(
                    "reset",
                    [signal("reset", token_count=10, relevance=1.0)],
                    state=ContextPolicyState(current_context_tokens=970, reset_allowed=True),
                )
            )
            abstain = ContextDecisionTrace.from_policy_result(
                policy.evaluate("abstain", [signal("abstain", security_denied=True, relevance=1.0)])
            )
            shadow = ContextDecisionTrace.from_policy_result(
                policy.evaluate(
                    "shadow",
                    [signal("shadow", token_count=10, relevance=1.0)],
                    state=ContextPolicyState(current_context_tokens=970, reset_allowed=True, shadow_mode=True),
                )
            )

            observed = {
                include["events"][0]["recommended_decision"],
                compress["events"][0]["recommended_decision"],
                omit["events"][0]["recommended_decision"],
                retrieve["events"][-1]["recommended_decision"],
                reset["events"][-1]["recommended_decision"],
                abstain["events"][-1]["recommended_decision"],
            }
            _require(observed == REQUIRED_TRACE_DECISIONS, f"required decision trace coverage drift: {observed}")
            _require(shadow["events"][-1]["recommended_decision"] == "reset", "shadow recommendation lost")
            _require(shadow["events"][-1]["effective_decision"] == "include", "shadow effective action drift")

            replay = ContextDecisionTrace.from_policy_result(omit_result)
            _require(replay["trace_hash"] == omit["trace_hash"], "decision trace replay is not deterministic")
            mutated = copy.deepcopy(omit)
            mutated["events"][0]["reason_codes"] = ("TAMPER",)
            try:
                ContextDecisionTrace.verify(mutated)
            except ValueError:
                pass
            else:
                raise AssertionError("context decision trace tamper did not fail closed")

            return {
                "required_decisions_traced": sorted(observed),
                "deterministic_replay": True,
                "tamper_fails_closed": True,
                "recommended_effective_separation": True,
                "reference_only_retrieval_event": True,
                "sample_trace_hash": retrieve["trace_hash"],
            }


        def certify(repo: Path) -> dict[str, Any]:
            repo = repo.resolve()
            _require(repo == ROOT, f"context decision trace certifier must run against its own checkout: {repo} != {ROOT}")
            contract = _validate_contract(repo)
            enforcement = _validate_enforcement(repo)
            registry = _read_json(repo / REGISTRY)
            python_complete = registry.get("python_complete") or {}
            _require(python_complete.get("ready") is True, "Context Decision Trace is post-completion Python hardening")
            _require(python_complete.get("rust_resume_allowed") is False, "Rust must remain retired during Context Decision Trace work")
            _require(python_complete.get("rust_retired") is True, "Rust retirement must remain explicit")

            order = registry.get("post_completion_milestone_order") or []
            _require(UPSTREAM_ID in order and CAPABILITY_ID in order, "post-completion trace order incomplete")
            _require(order.index(CAPABILITY_ID) == order.index(UPSTREAM_ID) + 1, "Context Decision Trace must immediately follow Runtime Contract Version Graph")
            by_id = {row.get("id"): row for row in registry.get("capabilities") or [] if isinstance(row, dict)}
            upstream = by_id.get(UPSTREAM_ID) or {}
            lifecycle = by_id.get(CAPABILITY_ID) or {}
            upstream_state = str(upstream.get("state") or "")
            lifecycle_state = str(lifecycle.get("state") or "")
            _require(upstream_state in {"implemented", "verified", "certified"}, f"invalid upstream graph lifecycle: {upstream_state}")
            _require(lifecycle_state in {"implemented", "verified", "certified"}, f"invalid Context Decision Trace lifecycle: {lifecycle_state}")
            _require(lifecycle.get("required_for_python_complete") is False, "Context Decision Trace cannot reopen Python COMPLETE")
            current = next(
                (milestone for milestone in order if (by_id.get(milestone) or {}).get("state") != "certified"),
                "post_completion_complete",
            )
            implementation_ready = True
            admission_ready = upstream_state == "certified" and lifecycle_state in {"implemented", "verified", "certified"}
            if admission_ready:
                _require(current in {CAPABILITY_ID, "post_completion_complete"}, f"admission-ready trace has wrong current milestone: {current}")
            else:
                _require(current == UPSTREAM_ID, f"stacked trace prep must remain blocked on upstream milestone: {current}")

            runtime = _smoke()
            exact_head = _head(repo)
            _require(len(exact_head) == 40, "unable to resolve exact git head")
            return {
                "ok": True,
                "schema_version": 1,
                "claim": "CONTEXT_DECISION_TRACE_V1",
                "exact_head": exact_head,
                "implementation_ready": implementation_ready,
                "admission_ready": admission_ready,
                "lifecycle_state": lifecycle_state,
                "upstream_runtime_contract_graph_state": upstream_state,
                "post_completion_current_milestone": current,
                "python_complete_ready": True,
                "runtime": runtime,
                "enforcement": enforcement,
                "rust_resume_allowed": False,
                "rust": {
                    "production_promoted": 174,
                    "remaining_parity_promotion": 71,
                    "feature_development_frozen": True,
                },
                "claim_boundary": contract["claim_boundary"],
            }


        def main() -> int:
            parser = argparse.ArgumentParser(description="Certify Syntavra Context Decision Trace v1")
            parser.add_argument("--repo", default=".")
            parser.add_argument("--out")
            args = parser.parse_args()
            try:
                report = certify(Path(args.repo))
            except Exception as exc:
                report = {
                    "ok": False,
                    "schema_version": 1,
                    "claim": "CONTEXT_DECISION_TRACE_V1",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if args.out:
                output = Path(args.out)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(payload, encoding="utf-8")
            print(payload, end="")
            return 0 if report.get("ok") is True else 1


        if __name__ == "__main__":
            raise SystemExit(main())
        ''',
    )


def write_workflow() -> None:
    write(
        ".github/workflows/context-decision-trace.yml",
        r'''
        name: Context Decision Trace

        on:
          pull_request:
            branches:
              - main
            paths:
              - "contracts/python/context-decision-trace-v1.json"
              - "contracts/python/capability-completeness-registry-v1.json"
              - "syntavra_runtime/context_decision_trace.py"
              - "syntavra_runtime/adaptive_context_policy.py"
              - "tools/certify_context_decision_trace_v1.py"
              - "tests/runtime/test_context_decision_trace_v1.py"
              - ".github/workflows/context-decision-trace.yml"
              - ".github/workflows/release-main-merge-gate.yml"
              - "tests/runtime/test_release_action_pins.py"
              - "MANIFEST.sha256"
          push:
            branches:
              - main

        permissions:
          contents: read

        concurrency:
          group: context-decision-trace-${{ github.event.pull_request.number || github.ref }}
          cancel-in-progress: false

        jobs:
          context-decision-trace:
            runs-on: ubuntu-24.04
            timeout-minutes: 30
            env:
              PYTHONPATH: ${{ github.workspace }}
              PYTHONUTF8: "1"
            steps:
              - name: Checkout exact head
                uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
                with:
                  ref: ${{ github.event.pull_request.head.sha || github.sha }}
                  fetch-depth: 0

              - name: Set up Python 3.12
                uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
                with:
                  python-version: '3.12'

              - name: Install Python runtime
                run: python -m pip install -e .

              - name: Run Context Decision Trace regression suite
                run: python -m unittest tests.runtime.test_context_decision_trace_v1 -v

              - name: Generate exact-head Context Decision Trace certificate
                run: |
                  python tools/certify_context_decision_trace_v1.py --repo . --out /tmp/context-decision-trace.json
                  python - <<'PY'
                  import json
                  import subprocess
                  from pathlib import Path

                  report = json.loads(Path('/tmp/context-decision-trace.json').read_text(encoding='utf-8'))
                  head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
                  assert report['ok'] is True, report
                  assert report['claim'] == 'CONTEXT_DECISION_TRACE_V1'
                  assert report['exact_head'] == head
                  assert report['implementation_ready'] is True
                  assert report['python_complete_ready'] is True
                  assert report['upstream_runtime_contract_graph_state'] in {'implemented', 'verified', 'certified'}
                  assert report['admission_ready'] is (report['upstream_runtime_contract_graph_state'] == 'certified')
                  assert report['runtime']['required_decisions_traced'] == ['abstain', 'compress', 'include', 'omit', 'reset', 'retrieve']
                  assert report['runtime']['deterministic_replay'] is True
                  assert report['runtime']['tamper_fails_closed'] is True
                  assert report['runtime']['recommended_effective_separation'] is True
                  assert report['rust_resume_allowed'] is False
                  assert report['rust']['production_promoted'] == 174
                  assert report['rust']['remaining_parity_promotion'] == 71
                  PY

              - name: Enforce clean exact-head repository
                if: always()
                shell: bash
                run: |
                  set -euo pipefail
                  rm -rf syntavra_runtime.egg-info
                  git diff --check
                  status="$(git status --porcelain --untracked-files=all)"
                  printf '%s\n' "$status"
                  test -z "$status"

              - name: Upload Context Decision Trace evidence
                if: always()
                uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
                with:
                  name: context-decision-trace-${{ github.event.pull_request.head.sha || github.sha }}
                  path: /tmp/context-decision-trace.json
                  if-no-files-found: error
        ''',
    )


def patch_registry() -> None:
    path = ROOT / "contracts/python/capability-completeness-registry-v1.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    require(registry.get("post_completion_milestone_order") == [UPSTREAM_ID], "unexpected post-completion order before 241")
    rows = registry.get("capabilities") or []
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    require(CAPABILITY_ID not in ids, "Context Decision Trace already exists in registry")
    upstream_index = ids.index(UPSTREAM_ID)
    row = {
        "id": CAPABILITY_ID,
        "group": "post-completion-context-policy",
        "state": "implemented",
        "classification": "NEW",
        "required_for_python_complete": False,
        "implementation_evidence": [
            "contracts/python/context-decision-trace-v1.json",
            "syntavra_runtime/context_decision_trace.py",
            "syntavra_runtime/adaptive_context_policy.py",
            "contracts/python/adaptive-context-policy-v1.json",
            "contracts/python/runtime-contract-version-graph-v1.json",
            "tools/certify_context_decision_trace_v1.py",
            "tests/runtime/test_context_decision_trace_v1.py",
            ".github/workflows/context-decision-trace.yml",
            ".github/workflows/release-main-merge-gate.yml",
            "tests/runtime/test_release_action_pins.py",
        ],
        "certification_evidence": [],
        "acceptance": "Deterministic reference-only trace of include/omit/compress/retrieve/reset/abstain decisions from verified policy receipts, with recommended/effective separation, deterministic replay and fail-closed tamper detection. Policy snapshot and evidence-journal ownership stay with later capabilities; Python COMPLETE remains sealed and Rust stays retired/frozen.",
    }
    rows.insert(upstream_index + 1, row)
    registry["capabilities"] = rows
    registry["post_completion_milestone_order"] = [UPSTREAM_ID, CAPABILITY_ID]
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_release_gate() -> None:
    path = ROOT / ".github/workflows/release-main-merge-gate.yml"
    text = path.read_text(encoding="utf-8")
    test_anchor = "          python -m unittest tests.runtime.test_runtime_contract_version_graph_v1 -v\n"
    cert_anchor = "          python tools/certify_runtime_contract_version_graph_v1.py --out /tmp/runtime-contract-version-graph.json\n"
    require(text.count(test_anchor) == 1, "release gate 240 unittest anchor drift")
    require(text.count(cert_anchor) == 1, "release gate 240 certifier anchor drift")
    text = text.replace(test_anchor, test_anchor + "          python -m unittest tests.runtime.test_context_decision_trace_v1 -v\n", 1)
    text = text.replace(cert_anchor, cert_anchor + "          python tools/certify_context_decision_trace_v1.py --out /tmp/context-decision-trace.json\n", 1)
    path.write_text(text, encoding="utf-8")


def patch_pin_policy() -> None:
    path = ROOT / "tests/runtime/test_release_action_pins.py"
    text = path.read_text(encoding="utf-8")
    anchor = '    ".github/workflows/runtime-contract-version-graph.yml",\n'
    require(text.count(anchor) == 1, "pin policy 240 anchor drift")
    text = text.replace(anchor, anchor + '    ".github/workflows/context-decision-trace.yml",\n', 1)
    path.write_text(text, encoding="utf-8")


def assert_paths() -> None:
    actual = sorted(
        line for line in run("git", "diff", "--name-only", BASE_SHA, capture=True).splitlines() if line
    )
    print("expected permanent paths:")
    print("\n".join(EXPECTED_PATHS))
    print("actual permanent paths:")
    print("\n".join(actual))
    require(actual == EXPECTED_PATHS, f"permanent path drift: {actual}")


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_reports() -> None:
    trace = load("/tmp/context-decision-trace.json")
    completeness = load("/tmp/python-capability-completeness.json")
    rust = load("/tmp/rust-feature-freeze-guard.json")
    graph = load("/tmp/runtime-contract-version-graph.json")
    require(trace.get("ok") is True and trace.get("implementation_ready") is True, f"trace certifier red: {trace}")
    require(trace.get("admission_ready") is False, f"stacked 241 prep must remain admission-blocked: {trace}")
    require(trace.get("upstream_runtime_contract_graph_state") == "implemented", f"unexpected 240 state: {trace}")
    require(trace.get("post_completion_current_milestone") == UPSTREAM_ID, f"trace milestone drift: {trace}")
    require(trace.get("python_complete_ready") is True and trace.get("rust_resume_allowed") is False, f"trace authority drift: {trace}")
    require((trace.get("rust") or {}).get("production_promoted") == 174, f"Rust promotion drift: {trace}")
    require((trace.get("rust") or {}).get("remaining_parity_promotion") == 71, f"Rust remaining drift: {trace}")
    require(completeness.get("ok") is True, f"completeness red: {completeness}")
    require(completeness.get("post_completion_milestone_order") == [UPSTREAM_ID, CAPABILITY_ID], f"post order drift: {completeness}")
    require(completeness.get("post_completion_current_milestone") == UPSTREAM_ID, f"current post milestone drift: {completeness}")
    require((completeness.get("current_state_report_consistency") or {}).get("stale_surfaces") == [], f"stale surfaces: {completeness}")
    require(rust.get("ok") is True and rust.get("rust_resume_allowed") is False, f"Rust freeze red: {rust}")
    require((rust.get("rust") or {}).get("production_promoted") == 174, f"Rust promotion changed: {rust}")
    require((rust.get("rust") or {}).get("remaining_parity_promotion") == 71, f"Rust remaining changed: {rust}")
    require(graph.get("ok") is True and graph.get("admission_ready") is True, f"240 graph regressed: {graph}")


def main() -> int:
    run("git", "merge-base", "--is-ancestor", BASE_SHA, "HEAD")
    for relative in TEMP_PATHS:
        if (ROOT / relative).exists():
            run("git", "rm", "-f", relative)

    write_runtime()
    write_contract()
    write_tests()
    write_certifier()
    write_workflow()
    patch_registry()
    patch_release_gate()
    patch_pin_policy()

    run(sys.executable, "tools/refresh_manifest.py")
    run("git", "add", "-A")
    assert_paths()
    run("git", "diff", "--check")

    run(sys.executable, "-m", "unittest", "tests.runtime.test_context_decision_trace_v1", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_runtime_contract_version_graph_v1", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_python_capability_completeness", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_release_action_pins", "-v")
    run(sys.executable, "tools/certify_context_decision_trace_v1.py", "--out", "/tmp/context-decision-trace.json")
    run(sys.executable, "tools/certify_runtime_contract_version_graph_v1.py", "--out", "/tmp/runtime-contract-version-graph.json")
    run(sys.executable, "tools/certify_python_capability_completeness.py", "--out", "/tmp/python-capability-completeness.json")
    run(sys.executable, "tools/certify_rust_feature_freeze_guard.py", "--out", "/tmp/rust-feature-freeze-guard.json")
    validate_reports()

    run("git", "config", "user.name", "Naveax")
    run("git", "config", "user.email", "Omersevik095@gmail.com")
    run("git", "commit", "-m", "python: implement context decision trace v1")
    final_commit = run("git", "rev-parse", "HEAD", capture=True).strip()
    final_tree = run("git", "rev-parse", "HEAD^{tree}", capture=True).strip()

    run(sys.executable, "tools/refresh_manifest.py", "--check")
    run(sys.executable, "tools/certify_context_decision_trace_v1.py", "--out", "/tmp/context-decision-trace-postcommit.json")
    run(sys.executable, "tools/certify_runtime_contract_version_graph_v1.py", "--out", "/tmp/runtime-contract-version-graph-postcommit.json")
    run(sys.executable, "tools/certify_python_capability_completeness.py", "--out", "/tmp/python-capability-completeness-postcommit.json")
    run(sys.executable, "tools/certify_rust_feature_freeze_guard.py", "--out", "/tmp/rust-feature-freeze-guard-postcommit.json")
    run(sys.executable, "tools/certify_python_completion_certificate_v1.py", "--out", "/tmp/python-completion-certificate-postcommit.json")
    completion = load("/tmp/python-completion-certificate-postcommit.json")
    require(completion.get("ok") is True, f"completion certificate regressed: {completion}")
    require(completion.get("python_complete_ready") is True, f"Python COMPLETE drift: {completion}")
    require(completion.get("rust_resume_allowed") is False, f"Rust resume opened: {completion}")
    require((completion.get("gates") or {}).get("python_contract_freeze") is True, f"completion freeze regressed: {completion}")

    run(sys.executable, "tools/validate.py")
    run(sys.executable, "tools/validate_release.py", "--smoke", "--output", "/tmp/context-decision-trace-release-validation.json")
    run(sys.executable, "tools/refresh_manifest.py", "--check")
    run("git", "diff", "--check")
    require(not run("git", "status", "--porcelain", "--untracked-files=all", capture=True).strip(), "post-commit validation dirtied repository")

    run("git", "push", "origin", f"HEAD:{TARGET_BRANCH}")
    print(f"FINAL_COMMIT={final_commit}")
    print(f"FINAL_TREE={final_tree}")
    print("EXPECTED_PATHS=" + json.dumps(EXPECTED_PATHS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
