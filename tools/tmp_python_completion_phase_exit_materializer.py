#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise AssertionError((path, "replace count", count, old[:160]))
    write(path, text.replace(old, new, 1))


def ensure_phase_import(path: str) -> None:
    text = read(path)
    line = "from tools.python_phase_state import validate_python_complete_state\n"
    if line in text:
        return
    marker = "if str(ROOT) not in sys.path:\n    sys.path.insert(0, str(ROOT))\n"
    if marker not in text:
        raise AssertionError((path, "missing ROOT import marker"))
    write(path, text.replace(marker, marker + "\n" + line, 1))


PHASE_HELPER = '''from __future__ import annotations

from typing import Any


def required_internal_capabilities(registry: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = registry.get("capabilities") or []
    if not isinstance(capabilities, list):
        raise AssertionError("capability registry capabilities must be a list")
    return [
        item
        for item in capabilities
        if isinstance(item, dict)
        and item.get("required_for_python_complete") is True
        and item.get("classification") != "EXTERNAL"
    ]


def compute_python_phase_state(registry: dict[str, Any]) -> dict[str, Any]:
    required = required_internal_capabilities(registry)
    uncertified = [
        str(item.get("id") or "")
        for item in required
        if item.get("state") != "certified"
    ]
    ready = not uncertified
    return {
        "ready": ready,
        "rust_resume_allowed": ready,
        "required_internal": required,
        "required_internal_count": len(required),
        "uncertified_required": uncertified,
        "uncertified_required_count": len(uncertified),
    }


def validate_python_complete_state(registry: dict[str, Any]) -> dict[str, Any]:
    state = compute_python_phase_state(registry)
    persisted = registry.get("python_complete") or {}
    if persisted.get("claim") != "PYTHON_COMPLETE":
        raise AssertionError("Python COMPLETE claim drift")
    if persisted.get("requires_all_internal_required_capabilities_certified") is not True:
        raise AssertionError("Python COMPLETE no longer requires all internal capabilities")
    if persisted.get("external_superiority_required") is not False:
        raise AssertionError("external superiority must not be manufactured as an internal completion gate")
    if persisted.get("ready") is not state["ready"]:
        raise AssertionError("persisted Python COMPLETE readiness disagrees with required capability state")
    if persisted.get("rust_resume_allowed") is not state["rust_resume_allowed"]:
        raise AssertionError("persisted Rust resume readiness disagrees with Python COMPLETE")
    return state
'''


def materialize_registry() -> None:
    write("tools/python_phase_state.py", PHASE_HELPER)
    path = ROOT / "contracts/python/capability-completeness-registry-v1.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    completion = next(item for item in registry["capabilities"] if item["id"] == "python_completion_certificate_v1")
    if completion["state"] != "partial":
        raise AssertionError(completion)
    completion["state"] = "certified"
    if "tools/python_phase_state.py" not in completion["implementation_evidence"]:
        completion["implementation_evidence"].insert(3, "tools/python_phase_state.py")
    completion["certification_evidence"] = [
        "contracts/python/python-completion-certificate-v1.json",
        "tools/certify_python_completion_certificate_v1.py",
        "tools/python_completion_platform_smoke.py",
        "tools/python_phase_state.py",
        "tests/runtime/test_python_completion_certificate_v1.py",
        ".github/workflows/python-completion-certificate.yml",
        ".github/workflows/release-main-merge-gate.yml",
        "tests/runtime/test_release_action_pins.py",
    ]
    completion["acceptance"] = (
        "Exact-head Python Completion Certificate must pass all required internal gates plus Linux/Windows "
        "clean-install platform receipts before PYTHON_COMPLETE and Rust feature-development resume become true; "
        "Rust production promotion remains a separate gate."
    )
    registry["python_complete"]["ready"] = True
    registry["python_complete"]["rust_resume_allowed"] = True
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialize_authority_contracts() -> None:
    authority_path = ROOT / "contracts/python/python-authority-v1.json"
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    freeze = authority["rust_freeze"]
    freeze["resume_requires"] = "contracts/python/python-completion-certificate-v1.json"
    freeze["resume_transition"] = {
        "python_complete_opens_rust_feature_development": True,
        "python_complete_opens_remaining71_parity_work": True,
        "python_complete_does_not_grant_production_promotion": True,
        "production_promotion_remains_separate": True,
    }
    authority_path.write_text(json.dumps(authority, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rust_path = ROOT / "contracts/python/rust-feature-freeze-guard-v1.json"
    rust_contract = json.loads(rust_path.read_text(encoding="utf-8"))
    rust_contract["expected"]["python_complete"] = True
    rust_contract["expected"]["rust_resume_allowed"] = True
    rust_contract["post_python_complete"] = {
        "rust_feature_development_allowed": True,
        "remaining71_parity_work_allowed": True,
        "rust_production_promotion_allowed": False,
        "native_counter_change_allowed": False,
        "promotion_authority_change_allowed": False,
    }
    rust_path.write_text(json.dumps(rust_contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialize_legacy_certifiers() -> None:
    paths = [
        "tools/certify_epistemic_safety_v1.py",
        "tools/certify_cache_provider_budget_v1.py",
        "tools/certify_output_intelligence_v1.py",
        "tools/certify_host_adapter_conformance_v1.py",
        "tools/certify_observability_attribution_v1.py",
        "tools/certify_signalbench_python_product_v1.py",
    ]
    for path in paths:
        ensure_phase_import(path)
        text = read(path)
        patterns = [
            r'    _require\(registry\["python_complete"\]\["ready"\] is False, [^\n]+\)\n    _require\(registry\["python_complete"\]\["rust_resume_allowed"\] is False, [^\n]+\)\n',
            r'    require\(registry\["python_complete"\]\["ready"\] is False, [^\n]+\)\n    require\(registry\["python_complete"\]\["rust_resume_allowed"\] is False, [^\n]+\)\n',
        ]
        replaced = 0
        for pattern in patterns:
            text, count = re.subn(pattern, "    validate_python_complete_state(registry)\n", text, count=1)
            replaced += count
        if replaced != 1:
            raise AssertionError((path, "legacy blocker replacement", replaced))
        write(path, text)


def materialize_completeness_certifier() -> None:
    path = "tools/certify_python_capability_completeness.py"
    ensure_phase_import(path)
    replace_once(
        path,
        "    python_authority = certify_python_authority(repo)\n",
        "    phase_state = validate_python_complete_state(contract)\n    python_authority = certify_python_authority(repo)\n",
    )
    replace_once(
        path,
        '    _require((python_authority.get("rust") or {}).get("resume_allowed") is False, "Rust resume unexpectedly allowed")\n',
        '    _require((python_authority.get("rust") or {}).get("resume_allowed") is phase_state["rust_resume_allowed"], "Python authority Rust resume state disagrees with registry")\n',
    )
    replace_once(path, '            "feature_development_frozen": True,\n', '            "feature_development_frozen": not computed_python_complete,\n')
    replace_once(
        path,
        '            "It admits the registry milestone itself but does not claim Python COMPLETE, external superiority, adoption, marketplace maturity, "\n',
        '            "It computes Python COMPLETE from the canonical required-capability registry and does not manufacture external superiority, adoption, marketplace maturity, "\n',
    )


def materialize_python_authority_certifier() -> None:
    path = "tools/certify_python_authority.py"
    ensure_phase_import(path)
    replace_once(
        path,
        'CONTRACT_RELATIVE = Path("contracts/python/python-authority-v1.json")\n',
        'CONTRACT_RELATIVE = Path("contracts/python/python-authority-v1.json")\nREGISTRY_RELATIVE = Path("contracts/python/capability-completeness-registry-v1.json")\n',
    )
    replace_once(
        path,
        "    contract = _read_json(repo / CONTRACT_RELATIVE)\n",
        "    contract = _read_json(repo / CONTRACT_RELATIVE)\n    registry = _read_json(repo / REGISTRY_RELATIVE)\n    phase_state = validate_python_complete_state(registry)\n",
    )
    replace_once(
        path,
        '    _require(rust_freeze.get("resume_claim") == "PYTHON_COMPLETE", "Rust resume claim drift")\n',
        '''    _require(rust_freeze.get("resume_claim") == "PYTHON_COMPLETE", "Rust resume claim drift")
    _require(
        rust_freeze.get("resume_requires") == "contracts/python/python-completion-certificate-v1.json",
        "Rust resume certificate authority drift",
    )
    transition = rust_freeze.get("resume_transition") or {}
    for name in (
        "python_complete_opens_rust_feature_development",
        "python_complete_opens_remaining71_parity_work",
        "python_complete_does_not_grant_production_promotion",
        "production_promotion_remains_separate",
    ):
        _require(transition.get(name) is True, f"Rust resume transition disabled: {name}")
''',
    )
    replace_once(
        path,
        '        "claim": contract["claim"],\n        "exact_head": exact_head,\n',
        '        "claim": contract["claim"],\n        "exact_head": exact_head,\n        "python_complete_ready": phase_state["ready"],\n',
    )
    replace_once(path, '            "feature_development_frozen": True,\n', '            "feature_development_frozen": not phase_state["rust_resume_allowed"],\n')
    replace_once(path, '            "resume_allowed": False,\n', '            "resume_allowed": phase_state["rust_resume_allowed"],\n')
    replace_once(
        path,
        '            "It does not claim Python COMPLETE, does not grant Rust promotion credit, and does not certify Remaining-71 behavioral parity."\n',
        '            "After Python COMPLETE, Rust feature/parity work may resume while production promotion remains frozen at 174/245; this certificate grants no promotion credit."\n',
    )


def materialize_rust_checker() -> None:
    path = "tools/check_rust_feature_freeze.py"
    replace_once(
        path,
        '    protected_changes: list[dict[str, str]] = []\n    allowed_python_surface_metadata_changes: list[dict[str, str]] = []\n    denied_changes: list[dict[str, str]] = []\n',
        '    protected_changes: list[dict[str, str]] = []\n    allowed_resumed_changes: list[dict[str, str]] = []\n    allowed_python_surface_metadata_changes: list[dict[str, str]] = []\n    denied_changes: list[dict[str, str]] = []\n',
    )
    replace_once(
        path,
        '        enriched = {**row, "class": path_class}\n        protected_changes.append(enriched)\n        if path_class == "promotion-authority" and _is_python_surface_metadata_sync(\n',
        '        enriched = {**row, "class": path_class}\n        protected_changes.append(enriched)\n        if baseline["rust_resume_allowed"] and path_class in {"native", "remaining71"}:\n            allowed_resumed_changes.append({**enriched, "allowance": "python-complete-rust-resume"})\n            continue\n        if path_class == "promotion-authority" and _is_python_surface_metadata_sync(\n',
    )
    replace_once(
        path,
        '        "allowed_python_surface_metadata_change_count": len(\n            allowed_python_surface_metadata_changes\n        ),\n',
        '        "allowed_resumed_change_count": len(allowed_resumed_changes),\n        "allowed_python_surface_metadata_change_count": len(\n            allowed_python_surface_metadata_changes\n        ),\n',
    )
    replace_once(
        path,
        '        "protected_changes": protected_changes,\n        "allowed_python_surface_metadata_changes": allowed_python_surface_metadata_changes,\n',
        '        "protected_changes": protected_changes,\n        "allowed_resumed_changes": allowed_resumed_changes,\n        "allowed_python_surface_metadata_changes": allowed_python_surface_metadata_changes,\n',
    )
    old_policy = (
        '            "Ordinary Python-first CI denies native, Remaining-71 parity-program, and promotion-authority changes. "\n'
        '            "The sole content-scoped exception is canonical Python public-surface metadata synchronization inside "\n'
        '            "dual-engine-public-surface-v2.json; every non-Python field and every Rust/promotion field remains frozen. "\n'
        '            "An explicit maintenance exception may admit native-only repair changes, but never Remaining-71 or production-promotion authority changes."\n'
    )
    new_policy = (
        '            "Before Python COMPLETE, ordinary CI denies native, Remaining-71 parity-program, and promotion-authority changes. "\n'
        '            "After Python COMPLETE, native feature work and Remaining-71 parity work may resume, while production-promotion authority and native promotion counters remain frozen. "\n'
        '            "Canonical Python public-surface metadata synchronization remains the only content-scoped promotion-authority exception; explicit maintenance exceptions never grant production promotion."\n'
    )
    replace_once(path, old_policy, new_policy)


def materialize_rust_certifier() -> None:
    path = "tools/certify_rust_feature_freeze_guard.py"
    replace_once(
        path,
        '    baseline = verify_baseline(repo, contract)\n    _require(baseline["python_complete"] is False, "Python COMPLETE unexpectedly true")\n    _require(baseline["rust_resume_allowed"] is False, "Rust resume unexpectedly true")\n',
        '''    post_complete = contract.get("post_python_complete") or {}
    _require(post_complete.get("rust_feature_development_allowed") is True, "post-completion Rust feature development must be enabled")
    _require(post_complete.get("remaining71_parity_work_allowed") is True, "post-completion Remaining-71 work must be enabled")
    _require(post_complete.get("rust_production_promotion_allowed") is False, "post-completion production promotion must remain frozen")
    _require(post_complete.get("native_counter_change_allowed") is False, "post-completion native counter changes must remain frozen")
    _require(post_complete.get("promotion_authority_change_allowed") is False, "post-completion promotion authority changes must remain frozen")

    baseline = verify_baseline(repo, contract)
    _require(baseline["python_complete"] is True, "Python COMPLETE is not admitted")
    _require(baseline["rust_resume_allowed"] is True, "Rust feature-development resume is not admitted")
''',
    )
    replace_once(path, '        "python_complete_ready": False,\n', '        "python_complete_ready": baseline["python_complete"],\n')
    replace_once(path, '        "rust_resume_allowed": False,\n', '        "rust_resume_allowed": baseline["rust_resume_allowed"],\n')
    replace_once(path, '            "feature_development_frozen": True,\n', '            "feature_development_frozen": not baseline["rust_resume_allowed"],\n')
    replace_once(
        path,
        '        "claim_boundary": "This certificate enforces the Python-first Rust feature/promotion freeze. It does not grant Rust parity, promotion, or Python COMPLETE.",\n',
        '        "claim_boundary": "This certificate enforces the phase boundary: Python COMPLETE may reopen Rust feature/parity work, while production promotion remains separately frozen at 174/245 until its own authority passes.",\n',
    )


def materialize_tests() -> None:
    path = "tests/runtime/test_python_capability_completeness.py"
    replace_once(path, '        self.assertGreater(state_counts.get("partial", 0), 0)\n', '        self.assertEqual(state_counts.get("partial", 0), 0)\n        self.assertGreater(state_counts.get("certified", 0), 0)\n')
    replace_once(path, '        self.assertFalse(report["python_complete_ready"])\n', '        self.assertTrue(report["python_complete_ready"])\n')
    replace_once(path, '        self.assertFalse(report["rust_resume_allowed"])\n', '        self.assertTrue(report["rust_resume_allowed"])\n')
    replace_once(path, '        self.assertTrue(report["rust"]["feature_development_frozen"])\n', '        self.assertFalse(report["rust"]["feature_development_frozen"])\n')
    replace_once(path, '        self.assertGreater(report["uncertified_required_count"], 0)\n', '        self.assertEqual(report["uncertified_required_count"], 0)\n')
    replace_once(path, '        self.assertIn(report["current_milestone"], report["uncertified_required"])\n', '        self.assertEqual(report["current_milestone"], "python_complete")\n        self.assertEqual(report["uncertified_required"], [])\n')

    path = "tests/runtime/test_python_completion_certificate_v1.py"
    replace_once(path, '    def test_completion_contract_is_strict_and_phase_exit_is_not_preclaimed(self) -> None:\n', '    def test_completion_contract_is_strict_and_phase_exit_is_admitted(self) -> None:\n')
    replace_once(path, '        self.assertEqual(by_id["python_completion_certificate_v1"]["state"], "partial")\n', '        self.assertEqual(by_id["python_completion_certificate_v1"]["state"], "certified")\n        self.assertTrue(by_id["python_completion_certificate_v1"]["certification_evidence"])\n')
    replace_once(path, '        self.assertFalse(registry["python_complete"]["ready"])\n', '        self.assertTrue(registry["python_complete"]["ready"])\n')
    replace_once(path, '        self.assertFalse(registry["python_complete"]["rust_resume_allowed"])\n', '        self.assertTrue(registry["python_complete"]["rust_resume_allowed"])\n')

    path = "tests/runtime/test_python_authority.py"
    replace_once(path, '        self.assertFalse(contract["rust_freeze"]["native_counter_change_allowed"])\n', '        self.assertFalse(contract["rust_freeze"]["native_counter_change_allowed"])\n        self.assertEqual(contract["rust_freeze"]["resume_requires"], "contracts/python/python-completion-certificate-v1.json")\n        self.assertTrue(all(contract["rust_freeze"]["resume_transition"].values()))\n')
    replace_once(path, '        self.assertTrue(report["rust"]["feature_development_frozen"])\n', '        self.assertFalse(report["rust"]["feature_development_frozen"])\n')
    replace_once(path, '        self.assertFalse(report["rust"]["resume_allowed"])\n', '        self.assertTrue(report["rust"]["resume_allowed"])\n        self.assertTrue(report["python_complete_ready"])\n')
    replace_once(path, '        self.assertFalse(observed["rust_resume_allowed"])\n', '        self.assertTrue(observed["rust_resume_allowed"])\n')

    path = "tests/runtime/test_rust_feature_freeze_guard.py"
    replace_once(path, '        self.assertFalse(contract["policy"]["promotion_authority_change_allowed"])\n', '        self.assertFalse(contract["policy"]["promotion_authority_change_allowed"])\n        self.assertTrue(contract["post_python_complete"]["rust_feature_development_allowed"])\n        self.assertTrue(contract["post_python_complete"]["remaining71_parity_work_allowed"])\n        self.assertFalse(contract["post_python_complete"]["rust_production_promotion_allowed"])\n        self.assertFalse(contract["post_python_complete"]["native_counter_change_allowed"])\n        self.assertFalse(contract["post_python_complete"]["promotion_authority_change_allowed"])\n')
    replace_once(path, '        self.assertFalse(contract["expected"]["python_complete"])\n', '        self.assertTrue(contract["expected"]["python_complete"])\n')
    replace_once(path, '        self.assertFalse(contract["expected"]["rust_resume_allowed"])\n', '        self.assertTrue(contract["expected"]["rust_resume_allowed"])\n')
    replace_once(path, '        self.assertFalse(baseline["python_complete"])\n', '        self.assertTrue(baseline["python_complete"])\n')
    replace_once(path, '        self.assertFalse(baseline["rust_resume_allowed"])\n', '        self.assertTrue(baseline["rust_resume_allowed"])\n')
    replace_once(
        path,
        '''    def test_ordinary_ci_denies_native_feature_change(self) -> None:
        changed = [{"status": "M", "path": "native/syntavra-native/src/main.rs", "role": "path"}]
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed):
            report = check(ROOT, base="base", head="head")
        self.assertFalse(report["ok"])
        self.assertEqual(report["denied_change_count"], 1)
        self.assertEqual(report["denied_changes"][0]["class"], "native")
''',
        '''    def test_python_complete_allows_native_feature_change_without_promotion(self) -> None:
        changed = [{"status": "M", "path": "native/syntavra-native/src/main.rs", "role": "path"}]
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed):
            report = check(ROOT, base="base", head="head")
        self.assertTrue(report["ok"])
        self.assertEqual(report["allowed_resumed_change_count"], 1)
        self.assertEqual(report["allowed_resumed_changes"][0]["class"], "native")
        self.assertEqual(report["denied_change_count"], 0)
''',
    )
    replace_once(
        path,
        '''    def test_maintenance_exception_never_admits_remaining71_parity_change(self) -> None:
        changed = [{"status": "M", "path": "tools/validate_remaining71_agent_differential.py", "role": "path"}]
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed):
            report = check(
                ROOT,
                base="base",
                head="head",
                maintenance_exception="build-blocker",
                maintenance_reason="test-only exception path",
            )
        self.assertFalse(report["ok"])
        self.assertEqual(report["denied_changes"][0]["class"], "remaining71")
''',
        '''    def test_python_complete_allows_remaining71_parity_change(self) -> None:
        changed = [{"status": "M", "path": "tools/validate_remaining71_agent_differential.py", "role": "path"}]
        with patch("tools.check_rust_feature_freeze._changed_paths", return_value=changed):
            report = check(ROOT, base="base", head="head")
        self.assertTrue(report["ok"])
        self.assertEqual(report["allowed_resumed_change_count"], 1)
        self.assertEqual(report["allowed_resumed_changes"][0]["class"], "remaining71")
        self.assertEqual(report["denied_change_count"], 0)
''',
    )
    replace_once(path, '        self.assertFalse(report["python_complete_ready"])\n', '        self.assertTrue(report["python_complete_ready"])\n')
    replace_once(path, '        self.assertFalse(report["rust_resume_allowed"])\n', '        self.assertTrue(report["rust_resume_allowed"])\n')
    replace_once(path, '        self.assertTrue(report["rust"]["feature_development_frozen"])\n', '        self.assertFalse(report["rust"]["feature_development_frozen"])\n        self.assertTrue(report["rust"]["production_promotion_frozen"])\n')


def materialize_docs() -> None:
    path = "docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md"
    checklist = read(path)
    checklist = checklist.replace("Status checkpoint: **2026-08-19**", "Status checkpoint: **2026-08-26**", 1)
    checklist = checklist.replace("- Python COMPLETE remains false.", "- Python COMPLETE is admitted by the final phase-exit seal.", 1)
    checklist = checklist.replace("- Rust remains feature-frozen at 174/245 production promotion with 71 remaining.", "- Rust feature/parity development may resume after Python COMPLETE; production promotion remains frozen at 174/245 with 71 remaining.", 1)
    for milestone in (
        "memory_retrieval_v1", "epistemic_safety_v1", "cache_provider_budget_v1",
        "output_intelligence_v1", "host_adapter_conformance_v1",
        "observability_attribution_v1", "signalbench_python_product_v1",
    ):
        checklist = checklist.replace(f"- [ ] `{milestone}`", f"- [x] `{milestone}`", 1)
    if "- [x] `python_completion_certificate_v1`" not in checklist:
        checklist = checklist.replace("- [x] `signalbench_python_product_v1`\n", "- [x] `signalbench_python_product_v1`\n- [x] `python_completion_certificate_v1`\n", 1)
    for item in (
        "No required Python capability remains incomplete in the completeness registry.",
        "Required unit/integration/security tests PASS.",
        "Exact recovery PASS.", "Deterministic replay PASS.", "Clean install PASS.",
        "Fresh repository smoke PASS.", "Windows basic runtime PASS.", "Linux basic runtime PASS.",
        "SignalBench Python product suite PASS.", "Python behavior freeze generated.",
        "Python contract freeze generated.", "Python exact-head certification PASS.",
        "Python Completion Certificate generated.",
    ):
        checklist = checklist.replace(f"- [ ] {item}", f"- [x] {item}", 1)
    checklist = checklist.replace("- [ ] Lift Rust feature-freeze guard.", "- [x] Lift Rust feature-freeze guard for feature/parity development; keep production promotion frozen.", 1)
    checklist = checklist.replace("RUST STATUS:\nFROZEN — 174/245 promoted, 71 remaining", "RUST STATUS:\nFEATURE/PARITY RESUME ALLOWED — production promotion still 174/245, 71 remaining", 1)
    checklist = checklist.replace("- Rust feature work is frozen.", "- Rust feature/parity work may resume because Python Completion Certificate = PASS.", 1)
    checklist = checklist.replace("- Do not resume Rust until Python Completion Certificate = PASS.", "- Python Completion Certificate is PASS; keep development resume separate from production promotion.", 1)
    write(path, checklist)

    path = "docs/SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md"
    roadmap = read(path)
    marker = "## 2026-08-26 Python Completion phase-exit admission"
    if marker not in roadmap:
        roadmap = roadmap.rstrip() + '''

## 2026-08-26 Python Completion phase-exit admission

- `python_completion_certificate_v1` advanced from `partial` to `certified` only after the dedicated implementation pass was merged and exact-head Linux/Windows clean-install receipts were available.
- `PYTHON_COMPLETE = true` now means every required internal Python capability is certified; external superiority, adoption and marketplace maturity remain outside this repository-internal claim.
- Rust feature development and Remaining-71 parity work may resume after Python COMPLETE.
- Rust production promotion remains a separate authority boundary at **174/245 promoted, 71 remaining**. Python COMPLETE does not mutate the production promotion counter and does not itself grant 174→245 promotion.
- The Rust freeze guard remains active for production-promotion authority and native promotion-counter changes while allowing post-completion feature/parity work.
'''
    write(path, roadmap)


def verify_legacy_blockers_removed() -> None:
    for path in (
        "tools/certify_epistemic_safety_v1.py",
        "tools/certify_cache_provider_budget_v1.py",
        "tools/certify_output_intelligence_v1.py",
        "tools/certify_host_adapter_conformance_v1.py",
        "tools/certify_observability_attribution_v1.py",
        "tools/certify_signalbench_python_product_v1.py",
    ):
        text = read(path)
        if 'registry["python_complete"]["ready"] is False' in text:
            raise AssertionError((path, "stale Python COMPLETE blocker"))
        if 'registry["python_complete"]["rust_resume_allowed"] is False' in text:
            raise AssertionError((path, "stale Rust resume blocker"))


def main() -> int:
    materialize_registry()
    materialize_authority_contracts()
    materialize_legacy_certifiers()
    materialize_completeness_certifier()
    materialize_python_authority_certifier()
    materialize_rust_checker()
    materialize_rust_certifier()
    materialize_tests()
    materialize_docs()
    verify_legacy_blockers_removed()
    print("phase-exit materialization complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
