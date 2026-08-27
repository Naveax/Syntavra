#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts/python/capability-completeness-registry-v1.json"
COMPLETENESS = ROOT / "tools/certify_python_capability_completeness.py"
COMPLETENESS_TEST = ROOT / "tests/runtime/test_python_capability_completeness.py"


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object: {path}")
    return value


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(path: Path, anchor: str, replacement: str, *, marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    if text.count(anchor) != 1:
        raise AssertionError(f"expected exactly one anchor in {path}: {anchor!r}")
    write_text(path, text.replace(anchor, replacement, 1))


registry = read_json(REGISTRY)
phase = registry.get("python_complete") or {}
assert phase.get("ready") is True
assert phase.get("rust_resume_allowed") is False
assert phase.get("rust_retired") is True

# Reconcile exact-head workflow assertions. Historical capability contracts are
# intentionally untouched; only current repository-state assertions change.
workflow_changes: list[str] = []
for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    changed = False
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if (
            stripped.startswith("assert ")
            and "python_complete_ready" in line
            and re.search(r"\bis\s+False\b", line)
        ):
            lines[index] = re.sub(r"\bis\s+False\b", "is True", line, count=1)
            changed = True
    if changed:
        write_text(path, "".join(lines))
        workflow_changes.append(path.relative_to(ROOT).as_posix())

# Reconcile certifier report dictionaries. AST matching avoids touching historic
# contract assertions such as python_complete_must_remain_false.
certifier_changes: list[str] = []
for path in sorted((ROOT / "tools").glob("certify*.py")):
    if path.name.startswith("tmp_"):
        continue
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    targets: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "python_complete_ready"
                and isinstance(value, ast.Constant)
                and value.value is False
            ):
                targets.append((value.lineno, value.col_offset))
    if not targets:
        continue
    lines = source.splitlines(keepends=True)
    for lineno, col in sorted(targets, reverse=True):
        line = lines[lineno - 1]
        position = line.find("False", col)
        if position < 0:
            raise AssertionError(f"unable to locate False literal in {path}:{lineno}")
        lines[lineno - 1] = line[:position] + "True" + line[position + 5 :]
    write_text(path, "".join(lines))
    certifier_changes.append(path.relative_to(ROOT).as_posix())

# The phase-exit tree currently has 17 legacy exact-head workflow/certifier
# pairs carrying the pre-completion boolean. Fail closed if that inventory drifts.
assert len(workflow_changes) == 17, workflow_changes
assert len(certifier_changes) == 17, certifier_changes

# Make the central completeness certifier enforce this invariant permanently.
replace_once(
    COMPLETENESS,
    "import argparse\nimport json\n",
    "import argparse\nimport ast\nimport json\nimport re\n",
    marker="import ast\nimport json\nimport re\n",
)
consistency_function = '''\n\ndef _validate_current_state_report_surfaces(\n    repo: Path,\n    *,\n    python_complete_ready: bool,\n) -> dict[str, Any]:\n    workflow_files = sorted((repo / ".github/workflows").glob("*.y*ml"))\n    certifier_files = sorted((repo / "tools").glob("certify*.py"))\n    stale: list[str] = []\n\n    if python_complete_ready:\n        for path in workflow_files:\n            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):\n                if (\n                    line.lstrip().startswith("assert ")\n                    and "python_complete_ready" in line\n                    and re.search(r"\\bis\\s+False\\b", line)\n                ):\n                    stale.append(f"{path.relative_to(repo).as_posix()}:{lineno}:workflow")\n\n        for path in certifier_files:\n            source = path.read_text(encoding="utf-8")\n            try:\n                tree = ast.parse(source, filename=str(path))\n            except SyntaxError as exc:\n                raise AssertionError(f"unable to parse certifier for current-state consistency: {path}: {exc}") from exc\n            for node in ast.walk(tree):\n                if not isinstance(node, ast.Dict):\n                    continue\n                for key, value in zip(node.keys, node.values):\n                    if (\n                        isinstance(key, ast.Constant)\n                        and key.value == "python_complete_ready"\n                        and isinstance(value, ast.Constant)\n                        and value.value is False\n                    ):\n                        stale.append(f"{path.relative_to(repo).as_posix()}:{value.lineno}:certifier")\n\n    _require(not stale, f"stale Python COMPLETE current-state reports remain: {stale}")\n    return {\n        "checked": bool(python_complete_ready),\n        "workflow_files_scanned": len(workflow_files),\n        "certifier_files_scanned": len(certifier_files),\n        "stale_surfaces": stale,\n    }\n'''
replace_once(
    COMPLETENESS,
    "\ndef _validate_enforcement(repo: Path) -> dict[str, str]:\n",
    consistency_function + "\ndef _validate_enforcement(repo: Path) -> dict[str, str]:\n",
    marker="def _validate_current_state_report_surfaces(",
)
replace_once(
    COMPLETENESS,
    "    computed_python_complete = not uncertified_required\n\n    python_complete = contract.get(\"python_complete\") or {}\n",
    "    computed_python_complete = not uncertified_required\n"
    "    current_state_report_consistency = _validate_current_state_report_surfaces(\n"
    "        repo, python_complete_ready=computed_python_complete\n"
    "    )\n\n"
    "    python_complete = contract.get(\"python_complete\") or {}\n",
    marker="current_state_report_consistency = _validate_current_state_report_surfaces(",
)
replace_once(
    COMPLETENESS,
    '        "python_complete_ready": computed_python_complete,\n',
    '        "python_complete_ready": computed_python_complete,\n'
    '        "current_state_report_consistency": current_state_report_consistency,\n',
    marker='"current_state_report_consistency": current_state_report_consistency,',
)

# Regression coverage binds the source-level invariant to the central authority.
replace_once(
    COMPLETENESS_TEST,
    "    _validate_capabilities,\n    certify,\n",
    "    _validate_capabilities,\n"
    "    _validate_current_state_report_surfaces,\n"
    "    certify,\n",
    marker="_validate_current_state_report_surfaces,",
)
method = '''\n    def test_completed_phase_has_no_stale_current_state_reports(self) -> None:\n        report = _validate_current_state_report_surfaces(\n            ROOT, python_complete_ready=True\n        )\n        self.assertTrue(report["checked"])\n        self.assertEqual(report["stale_surfaces"], [])\n        self.assertGreater(report["workflow_files_scanned"], 0)\n        self.assertGreater(report["certifier_files_scanned"], 0)\n\n'''
replace_once(
    COMPLETENESS_TEST,
    "    def test_registry_does_not_duplicate_route_identity_lists(self) -> None:\n",
    method + "    def test_registry_does_not_duplicate_route_identity_lists(self) -> None:\n",
    marker="def test_completed_phase_has_no_stale_current_state_reports",
)

# Final source-level audit. Historical contract fields are allowed; current exact-
# head report/workflow surfaces are not.
remaining_workflows: list[str] = []
for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if (
            line.lstrip().startswith("assert ")
            and "python_complete_ready" in line
            and re.search(r"\bis\s+False\b", line)
        ):
            remaining_workflows.append(f"{path.relative_to(ROOT).as_posix()}:{lineno}")
remaining_certifiers: list[str] = []
for path in sorted((ROOT / "tools").glob("certify*.py")):
    if path.name.startswith("tmp_"):
        continue
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "python_complete_ready"
                and isinstance(value, ast.Constant)
                and value.value is False
            ):
                remaining_certifiers.append(f"{path.relative_to(ROOT).as_posix()}:{value.lineno}")
assert not remaining_workflows, remaining_workflows
assert not remaining_certifiers, remaining_certifiers

print(json.dumps({
    "claim": "PYTHON_COMPLETION_CURRENT_STATE_REPORT_RECONCILIATION_V1",
    "python_complete_ready": True,
    "rust_resume_allowed": False,
    "rust_retired": True,
    "workflow_changes": workflow_changes,
    "certifier_changes": certifier_changes,
    "workflow_change_count": len(workflow_changes),
    "certifier_change_count": len(certifier_changes),
}, indent=2))
