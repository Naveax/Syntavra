#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

path = Path("tools/tmp_context_decision_trace_builder_v1.py")
text = path.read_text(encoding="utf-8")

old_temp = '''TEMP_PATHS = (\n    ".github/workflows/tmp-context-decision-trace-builder-v1.yml",\n    "tools/tmp_context_decision_trace_builder_v1.py",\n)'''
new_temp = '''TEMP_PATHS = (\n    ".github/workflows/tmp-context-decision-trace-builder-v1.yml",\n    ".github/workflows/tmp-context-decision-trace-builder-v2.yml",\n    "tools/tmp_context_decision_trace_builder_v1.py",\n    "tools/tmp_context_decision_trace_builder_patch_v2.py",\n)'''
assert old_temp in text, "TEMP_PATHS anchor missing"
text = text.replace(old_temp, new_temp, 1)

old_expected = '''        "tests/runtime/test_context_decision_trace_v1.py",\n        "tests/runtime/test_release_action_pins.py",'''
new_expected = '''        "tests/runtime/test_context_decision_trace_v1.py",\n        "tests/runtime/test_python_capability_completeness.py",\n        "tests/runtime/test_release_action_pins.py",'''
assert old_expected in text, "EXPECTED_PATHS anchor missing"
text = text.replace(old_expected, new_expected, 1)

anchor = '''def patch_release_gate() -> None:\n'''
addition = r'''def patch_completeness_tests() -> None:
    path = ROOT / "tests/runtime/test_python_capability_completeness.py"
    source = path.read_text(encoding="utf-8")
    constant_anchor = 'CONTRACT = ROOT / "contracts/python/capability-completeness-registry-v1.json"\n'
    constant = (
        constant_anchor
        + 'EXPECTED_POST_COMPLETION_PREFIX = [\n'
        + '    "runtime_contract_version_graph_v1",\n'
        + '    "context_decision_trace_v1",\n'
        + ']\n'
    )
    require(source.count(constant_anchor) == 1, "completeness test constant anchor drift")
    source = source.replace(constant_anchor, constant, 1)

    old_registry_assert = '        self.assertEqual(contract["post_completion_milestone_order"], ["runtime_contract_version_graph_v1"])\n'
    new_registry_assert = (
        '        post_completion_order = contract["post_completion_milestone_order"]\n'
        '        self.assertEqual(\n'
        '            post_completion_order[: len(EXPECTED_POST_COMPLETION_PREFIX)],\n'
        '            EXPECTED_POST_COMPLETION_PREFIX,\n'
        '        )\n'
        '        self.assertEqual(len(post_completion_order), len(set(post_completion_order)))\n'
    )
    require(source.count(old_registry_assert) == 1, "completeness registry order assertion drift")
    source = source.replace(old_registry_assert, new_registry_assert, 1)

    old_report_assert = '        self.assertEqual(report["post_completion_milestone_order"], ["runtime_contract_version_graph_v1"])\n'
    new_report_assert = (
        '        self.assertEqual(\n'
        '            report["post_completion_milestone_order"],\n'
        '            contract["post_completion_milestone_order"],\n'
        '        )\n'
        '        self.assertEqual(\n'
        '            report["post_completion_milestone_order"][: len(EXPECTED_POST_COMPLETION_PREFIX)],\n'
        '            EXPECTED_POST_COMPLETION_PREFIX,\n'
        '        )\n'
    )
    require(source.count(old_report_assert) == 1, "completeness report order assertion drift")
    source = source.replace(old_report_assert, new_report_assert, 1)
    path.write_text(source, encoding="utf-8")


'''
assert anchor in text, "patch_release_gate anchor missing"
text = text.replace(anchor, addition + anchor, 1)

old_call = '''    patch_registry()\n    patch_release_gate()'''
new_call = '''    patch_registry()\n    patch_completeness_tests()\n    patch_release_gate()'''
assert old_call in text, "patch call anchor missing"
text = text.replace(old_call, new_call, 1)

path.write_text(text, encoding="utf-8")
