#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def write_json(relative: str, value: dict) -> None:
    (ROOT / relative).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise AssertionError(f"{relative}: expected one match, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Canonical registry: Python COMPLETE is independent from Rust reactivation.
registry_path = "contracts/python/capability-completeness-registry-v1.json"
registry = read_json(registry_path)
policy = registry["policy"]
policy["rust_resume_requires_python_complete"] = True
policy["python_complete_does_not_auto_resume_rust"] = True
policy["rust_retirement_is_independent_of_python_complete"] = True
python_complete = registry["python_complete"]
python_complete["ready"] = True
python_complete["rust_resume_allowed"] = False
python_complete["rust_retired"] = True
completion_row = next(item for item in registry["capabilities"] if item["id"] == "python_completion_certificate_v1")
completion_row["acceptance"] = (
    "Exact-head Python Completion Certificate must pass all required internal gates plus Linux/Windows clean-install platform receipts "
    "before PYTHON_COMPLETE becomes true. Rust remains retired/frozen until a separate explicit reactivation decision; Python completion does not auto-resume Rust."
)
write_json(registry_path, registry)

# Python authority keeps Python active and Rust retired.
authority_path = "contracts/python/python-authority-v1.json"
authority = read_json(authority_path)
freeze = authority["rust_freeze"]
freeze["active"] = True
freeze["retired"] = True
freeze["feature_development_allowed"] = False
freeze["production_promotion_allowed"] = False
freeze["native_counter_change_allowed"] = False
freeze["resume_requires"] = "contracts/python/python-completion-certificate-v1.json"
freeze["resume_claim"] = "PYTHON_COMPLETE"
freeze["resume_transition"] = {
    "python_complete_opens_rust_feature_development": False,
    "python_complete_opens_remaining71_parity_work": False,
    "python_complete_does_not_grant_production_promotion": True,
    "production_promotion_remains_separate": True,
    "explicit_reactivation_required": True,
    "rust_retired_until_explicit_reactivation": True,
}
write_json(authority_path, authority)

# Rust freeze remains active even after Python completion.
rust_path = "contracts/python/rust-feature-freeze-guard-v1.json"
rust = read_json(rust_path)
rust["expected"]["python_complete"] = True
rust["expected"]["rust_resume_allowed"] = False
rust["policy"]["rust_retired"] = True
rust["policy"]["explicit_reactivation_required"] = True
rust["post_python_complete"] = {
    "rust_feature_development_allowed": False,
    "remaining71_parity_work_allowed": False,
    "rust_production_promotion_allowed": False,
    "native_counter_change_allowed": False,
    "promotion_authority_change_allowed": False,
    "rust_retired": True,
    "explicit_reactivation_required": True,
}
write_json(rust_path, rust)

# Completion contract explicitly decouples completion from Rust resume.
completion_contract_path = "contracts/python/python-completion-certificate-v1.json"
completion_contract = read_json(completion_contract_path)
completion_contract["lifecycle"].pop("rust_resume_requires_certificate_pass", None)
completion_contract["lifecycle"]["rust_resume_is_separate_explicit_decision"] = True
completion_contract["lifecycle"]["python_complete_does_not_auto_resume_rust"] = True
completion_contract["claim_boundary"] = (
    "This certificate proves repository-internal Python product completion gates only. It does not manufacture external superiority, adoption, marketplace maturity, "
    "or Rust Remaining-71 parity/promotion. Python COMPLETE does not auto-resume Rust; Rust remains retired/frozen until a separate explicit reactivation decision."
)
write_json(completion_contract_path, completion_contract)

# Canonical Python phase state: completion readiness and Rust resume are separate state machines.
(ROOT / "tools/python_phase_state.py").write_text(
    '''from __future__ import annotations\n\nfrom typing import Any\n\n\ndef required_internal_capabilities(registry: dict[str, Any]) -> list[dict[str, Any]]:\n    capabilities = registry.get("capabilities") or []\n    if not isinstance(capabilities, list):\n        raise AssertionError("capability registry capabilities must be a list")\n    return [\n        item\n        for item in capabilities\n        if isinstance(item, dict)\n        and item.get("required_for_python_complete") is True\n        and item.get("classification") != "EXTERNAL"\n    ]\n\n\ndef compute_python_phase_state(registry: dict[str, Any]) -> dict[str, Any]:\n    required = required_internal_capabilities(registry)\n    uncertified = [\n        str(item.get("id") or "")\n        for item in required\n        if item.get("state") != "certified"\n    ]\n    ready = not uncertified\n    persisted = registry.get("python_complete") or {}\n    rust_resume_allowed = persisted.get("rust_resume_allowed")\n    rust_retired = persisted.get("rust_retired")\n    if not isinstance(rust_resume_allowed, bool):\n        raise AssertionError("persisted Rust resume readiness must be boolean")\n    if not isinstance(rust_retired, bool):\n        raise AssertionError("persisted Rust retirement state must be boolean")\n    if rust_resume_allowed and not ready:\n        raise AssertionError("Rust resume cannot precede Python COMPLETE")\n    if rust_retired and rust_resume_allowed:\n        raise AssertionError("retired Rust cannot be resume-allowed")\n    return {\n        "ready": ready,\n        "rust_resume_allowed": rust_resume_allowed,\n        "rust_retired": rust_retired,\n        "required_internal": required,\n        "required_internal_count": len(required),\n        "uncertified_required": uncertified,\n        "uncertified_required_count": len(uncertified),\n    }\n\n\ndef validate_python_complete_state(registry: dict[str, Any]) -> dict[str, Any]:\n    state = compute_python_phase_state(registry)\n    persisted = registry.get("python_complete") or {}\n    if persisted.get("claim") != "PYTHON_COMPLETE":\n        raise AssertionError("Python COMPLETE claim drift")\n    if persisted.get("requires_all_internal_required_capabilities_certified") is not True:\n        raise AssertionError("Python COMPLETE no longer requires all internal capabilities")\n    if persisted.get("external_superiority_required") is not False:\n        raise AssertionError("external superiority must not be manufactured as an internal completion gate")\n    if persisted.get("ready") is not state["ready"]:\n        raise AssertionError("persisted Python COMPLETE readiness disagrees with required capability state")\n    if persisted.get("rust_resume_allowed") is not state["rust_resume_allowed"]:\n        raise AssertionError("persisted Rust resume readiness drift")\n    if persisted.get("rust_retired") is not state["rust_retired"]:\n        raise AssertionError("persisted Rust retirement state drift")\n    return state\n''',
    encoding="utf-8",
)

# Completeness report: Python can be complete while Rust remains retired.
replace_once(
    "tools/certify_python_capability_completeness.py",
    '    _require(python_complete.get("rust_resume_allowed") is computed_python_complete, "Rust resume readiness disagrees with Python COMPLETE")\n',
    '    _require(python_complete.get("rust_resume_allowed") is phase_state["rust_resume_allowed"], "Rust resume readiness disagrees with canonical phase state")\n'
    '    _require(python_complete.get("rust_retired") is phase_state["rust_retired"], "Rust retirement readiness disagrees with canonical phase state")\n',
)
replace_once(
    "tools/certify_python_capability_completeness.py",
    '        "rust_resume_allowed": computed_python_complete,\n',
    '        "rust_resume_allowed": phase_state["rust_resume_allowed"],\n',
)
replace_once(
    "tools/certify_python_capability_completeness.py",
    '            "feature_development_frozen": not computed_python_complete,\n',
    '            "feature_development_frozen": not phase_state["rust_resume_allowed"],\n',
)

# Legacy certifiers only require Rust resume to imply Python COMPLETE, not equality.
legacy = [
    "tools/certify_universal_context_item.py",
    "tools/certify_evidence_store_v2.py",
    "tools/certify_typed_context_object_store.py",
    "tools/certify_programmatic_execution.py",
    "tools/certify_deferred_tool_discovery.py",
    "tools/certify_context_namespace.py",
    "tools/certify_multi_graph_retrieval.py",
    "tools/certify_adaptive_context_policy.py",
]
old = (
    '    _require(\n'
    '        completeness.get("python_complete_ready") is completeness.get("rust_resume_allowed"),\n'
    '        "Python COMPLETE/Rust resume state disagreement",\n'
    '    )\n'
)
new = (
    '    _require(isinstance(completeness.get("rust_resume_allowed"), bool), "Rust resume state must be boolean")\n'
    '    _require(\n'
    '        not completeness.get("rust_resume_allowed") or completeness.get("python_complete_ready") is True,\n'
    '        "Rust resume cannot precede Python COMPLETE",\n'
    '    )\n'
)
for relative in legacy:
    replace_once(relative, old, new)

# Python authority accepts complete Python while explicitly keeping Rust retired.
replace_once(
    "tools/certify_python_authority.py",
    '    for name in (\n'
    '        "python_complete_opens_rust_feature_development",\n'
    '        "python_complete_opens_remaining71_parity_work",\n'
    '        "python_complete_does_not_grant_production_promotion",\n'
    '        "production_promotion_remains_separate",\n'
    '    ):\n'
    '        _require(transition.get(name) is True, f"Rust resume transition disabled: {name}")\n',
    '    for name in ("python_complete_opens_rust_feature_development", "python_complete_opens_remaining71_parity_work"):\n'
    '        _require(transition.get(name) is False, f"Rust retirement transition unexpectedly enabled: {name}")\n'
    '    for name in ("python_complete_does_not_grant_production_promotion", "production_promotion_remains_separate", "explicit_reactivation_required", "rust_retired_until_explicit_reactivation"):\n'
    '        _require(transition.get(name) is True, f"Rust retirement transition disabled: {name}")\n',
)
replace_once(
    "tools/certify_python_authority.py",
    '            "After Python COMPLETE, Rust feature/parity work may resume while production promotion remains frozen at 174/245; this certificate grants no promotion credit."\n',
    '            "Python COMPLETE does not auto-resume Rust. Rust remains retired/frozen at 174/245 with 71 remaining until a separate explicit reactivation decision."\n',
)

# Rust freeze remains frozen after completion.
replace_once(
    "tools/certify_rust_feature_freeze_guard.py",
    '    _require(post_complete.get("rust_feature_development_allowed") is True, "post-completion Rust feature development must be enabled")\n'
    '    _require(post_complete.get("remaining71_parity_work_allowed") is True, "post-completion Remaining-71 work must be enabled")\n',
    '    _require(post_complete.get("rust_feature_development_allowed") is False, "retired Rust feature development unexpectedly enabled")\n'
    '    _require(post_complete.get("remaining71_parity_work_allowed") is False, "retired Remaining-71 work unexpectedly enabled")\n'
    '    _require(post_complete.get("rust_retired") is True, "Rust retirement state drift")\n'
    '    _require(post_complete.get("explicit_reactivation_required") is True, "Rust explicit-reactivation gate drift")\n',
)
replace_once(
    "tools/certify_rust_feature_freeze_guard.py",
    '    _require(baseline["rust_resume_allowed"] is True, "Rust feature-development resume is not admitted")\n',
    '    _require(baseline["rust_resume_allowed"] is False, "Rust must remain retired/frozen")\n',
)
replace_once(
    "tools/certify_rust_feature_freeze_guard.py",
    '        "claim_boundary": "This certificate enforces the phase boundary: Python COMPLETE may reopen Rust feature/parity work, while production promotion remains separately frozen at 174/245 until its own authority passes.",\n',
    '        "claim_boundary": "This certificate enforces Rust retirement/freeze independently of Python COMPLETE. Rust feature/parity work and production promotion remain closed at 174/245 with 71 remaining until an explicit future reactivation authority changes that state.",\n',
)
replace_once(
    "tools/check_rust_feature_freeze.py",
    '            "After Python COMPLETE, native feature work and Remaining-71 parity work may resume, while production-promotion authority and native promotion counters remain frozen. "\n',
    '            "Python COMPLETE does not auto-resume Rust; native feature work and Remaining-71 parity work remain frozen while Rust is retired. "\n',
)

# Completion PASS no longer depends on Rust resume.
replace_once(
    "tools/certify_python_completion_certificate_v1.py",
    '    phase_exit_ready = certificate_candidate_ready and persisted_ready and persisted_rust_resume\n',
    '    phase_exit_ready = certificate_candidate_ready and persisted_ready\n',
)
replace_once(
    "tools/certify_python_completion_certificate_v1.py",
    '            "feature_development_frozen": not phase_exit_ready,\n',
    '            "feature_development_frozen": not persisted_rust_resume,\n',
)

# Regression expectations.
replace_once(
    "tests/runtime/test_python_capability_completeness.py",
    '        self.assertTrue(report["rust_resume_allowed"])\n',
    '        self.assertFalse(report["rust_resume_allowed"])\n',
)
replace_once(
    "tests/runtime/test_python_capability_completeness.py",
    '        self.assertFalse(report["rust"]["feature_development_frozen"])\n',
    '        self.assertTrue(report["rust"]["feature_development_frozen"])\n',
)
replace_once(
    "tests/runtime/test_python_authority.py",
    '        self.assertTrue(all(contract["rust_freeze"]["resume_transition"].values()))\n',
    '        self.assertFalse(contract["rust_freeze"]["resume_transition"]["python_complete_opens_rust_feature_development"])\n'
    '        self.assertFalse(contract["rust_freeze"]["resume_transition"]["python_complete_opens_remaining71_parity_work"])\n'
    '        self.assertTrue(contract["rust_freeze"]["resume_transition"]["explicit_reactivation_required"])\n'
    '        self.assertTrue(contract["rust_freeze"]["resume_transition"]["rust_retired_until_explicit_reactivation"])\n',
)
replace_once("tests/runtime/test_python_authority.py", '        self.assertFalse(report["rust"]["feature_development_frozen"])\n', '        self.assertTrue(report["rust"]["feature_development_frozen"])\n')
replace_once("tests/runtime/test_python_authority.py", '        self.assertTrue(report["rust"]["resume_allowed"])\n', '        self.assertFalse(report["rust"]["resume_allowed"])\n')
replace_once("tests/runtime/test_python_authority.py", '        self.assertTrue(observed["rust_resume_allowed"])\n', '        self.assertFalse(observed["rust_resume_allowed"])\n')
replace_once("tests/runtime/test_python_completion_certificate_v1.py", '        self.assertTrue(registry["python_complete"]["rust_resume_allowed"])\n', '        self.assertFalse(registry["python_complete"]["rust_resume_allowed"])\n        self.assertTrue(registry["python_complete"]["rust_retired"])\n')

rust_test = ROOT / "tests/runtime/test_rust_feature_freeze_guard.py"
text = rust_test.read_text(encoding="utf-8")
text = text.replace('        self.assertTrue(contract["post_python_complete"]["rust_feature_development_allowed"])\n', '        self.assertFalse(contract["post_python_complete"]["rust_feature_development_allowed"])\n')
text = text.replace('        self.assertTrue(contract["post_python_complete"]["remaining71_parity_work_allowed"])\n', '        self.assertFalse(contract["post_python_complete"]["remaining71_parity_work_allowed"])\n')
text = text.replace('        self.assertTrue(contract["expected"]["rust_resume_allowed"])\n', '        self.assertFalse(contract["expected"]["rust_resume_allowed"])\n')
text = text.replace('        self.assertTrue(baseline["rust_resume_allowed"])\n', '        self.assertFalse(baseline["rust_resume_allowed"])\n')
text = text.replace('    def test_python_complete_allows_native_feature_change_without_promotion(self) -> None:\n', '    def test_python_complete_does_not_auto_resume_native_feature_change(self) -> None:\n')
text = text.replace('        self.assertTrue(report["ok"])\n        self.assertEqual(report["allowed_resumed_change_count"], 1)\n        self.assertEqual(report["allowed_resumed_changes"][0]["class"], "native")\n        self.assertEqual(report["denied_change_count"], 0)\n', '        self.assertFalse(report["ok"])\n        self.assertEqual(report["allowed_resumed_change_count"], 0)\n        self.assertEqual(report["denied_change_count"], 1)\n        self.assertEqual(report["denied_changes"][0]["class"], "native")\n', 1)
text = text.replace('    def test_python_complete_allows_remaining71_parity_change(self) -> None:\n', '    def test_python_complete_does_not_auto_resume_remaining71_parity_change(self) -> None:\n')
text = text.replace('        self.assertTrue(report["ok"])\n        self.assertEqual(report["allowed_resumed_change_count"], 1)\n        self.assertEqual(report["allowed_resumed_changes"][0]["class"], "remaining71")\n        self.assertEqual(report["denied_change_count"], 0)\n', '        self.assertFalse(report["ok"])\n        self.assertEqual(report["allowed_resumed_change_count"], 0)\n        self.assertEqual(report["denied_change_count"], 1)\n        self.assertEqual(report["denied_changes"][0]["class"], "remaining71")\n', 1)
text = text.replace('        self.assertTrue(report["rust_resume_allowed"])\n', '        self.assertFalse(report["rust_resume_allowed"])\n')
text = text.replace('        self.assertFalse(report["rust"]["feature_development_frozen"])\n', '        self.assertTrue(report["rust"]["feature_development_frozen"])\n')
rust_test.write_text(text, encoding="utf-8")

# Continuity: Python work continues; Rust is retired until explicitly revisited.
checklist_path = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md"
checklist = checklist_path.read_text(encoding="utf-8")
checklist = checklist.replace(
    "- [x] `PYTHON_COMPLETE = true` and Rust feature/parity development resume is allowed only through the canonical completion state.\n",
    "- [x] `PYTHON_COMPLETE = true`; Rust remains retired/frozen and does not auto-resume from Python completion.\n",
)
checklist = checklist.replace(
    "- [x] Rust feature/parity work may resume after Python COMPLETE, but this does not grant production promotion credit.\n",
    "- [x] Rust feature/parity work remains retired for now; only narrow maintenance exceptions are allowed.\n",
)
checklist = checklist.replace(
    "- Python Completion Certificate lifecycle is `certified`; the canonical registry records `PYTHON_COMPLETE = true` and `rust_resume_allowed = true`.\n",
    "- Python Completion Certificate lifecycle is `certified`; the canonical registry records `PYTHON_COMPLETE = true`, `rust_resume_allowed = false`, and Rust retired/frozen.\n",
)
checklist = checklist.replace(
    "- Rust feature/parity work may resume, while production promotion remains frozen at **174/245** with **71** remaining.\n",
    "- Rust feature/parity work remains retired/frozen at **174/245** with **71** remaining while Python additions, hardening, fixes and certification continue.\n",
)
checklist = checklist.replace(
    "- [ ] Re-read fresh `main`, export the frozen Python→Rust contract/behavior corpus, then continue Remaining-71 differential work. Production promotion remains a later separate gate.\n",
    "- [ ] Re-read fresh `main`, then continue Python additions, hardening, fixes and certification. Do not resume Rust/Remaining-71 work unless explicitly reactivated later.\n",
)
checklist = checklist.replace(
    "Continue Syntavra from the post-Python-COMPLETE Rust-resume boundary.\n",
    "Continue Syntavra in Python-active / Rust-retired mode.\n",
)
checklist = checklist.replace(
    "- Rust feature/parity development may resume.\n",
    "- Rust feature/parity development remains retired/frozen.\n",
)
checklist = checklist.replace(
    "- Reuse frozen Python contracts, golden behavior vectors and receipts as migration authority.\n",
    "- Keep Python as the active product/feature authority; do not start Rust migration work yet.\n",
)
checklist_path.write_text(checklist, encoding="utf-8")

roadmap_path = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md"
roadmap = roadmap_path.read_text(encoding="utf-8")
marker = "\n## 2026-08-26 Rust retirement override\n"
if marker not in roadmap:
    roadmap += (
        marker
        + "\nPython Completion Certificate may be PASS while Rust remains explicitly retired/frozen. "
        + "`PYTHON_COMPLETE` is necessary but not sufficient for Rust reactivation. Until a separately admitted reactivation decision exists, "
        + "`rust_resume_allowed=false`, native/Remaining-71 feature work stays closed, and the production baseline remains 174/245 with 71 remaining. "
        + "Current active development continues on Python additions, hardening, fixes and certification.\n"
    )
roadmap_path.write_text(roadmap, encoding="utf-8")

# Re-pin the registry-derived Python contract freeze after authority contract changes.
from tools.certify_python_completion_certificate_v1 import derive_contract_freeze
registry = read_json(registry_path)
completion_contract = read_json(completion_contract_path)
freeze = derive_contract_freeze(ROOT, registry)
completion_contract["contract_freeze"]["expected_contract_count"] = freeze["contract_count"]
completion_contract["contract_freeze"]["expected_sha256"] = freeze["sha256"]
write_json(completion_contract_path, completion_contract)
print(json.dumps({"python_complete": True, "rust_resume_allowed": False, "rust_retired": True, "contract_freeze": freeze["sha256"]}, indent=2))
