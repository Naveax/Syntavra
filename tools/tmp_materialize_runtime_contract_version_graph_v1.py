#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts/python/capability-completeness-registry-v1.json"
COMPLETION = ROOT / "contracts/python/python-completion-certificate-v1.json"
ROADMAP = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md"
CHECKLIST = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md"


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object: {path}")
    return value


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


registry = read_json(REGISTRY)
capability_id = "runtime_contract_version_graph_v1"
if capability_id in registry["milestone_order"]:
    raise AssertionError(f"{capability_id} already present in milestone order")
if any(item.get("id") == capability_id for item in registry["capabilities"]):
    raise AssertionError(f"{capability_id} already present in capability rows")

registry["milestone_order"].append(capability_id)
entry = {
    "id": capability_id,
    "group": "contract-authority",
    "state": "implemented",
    "classification": "NEW",
    "required_for_python_complete": False,
    "implementation_evidence": [
        "contracts/python/runtime-contract-version-graph-v1.json",
        "syntavra_runtime/contract_version_graph.py",
        "tools/certify_runtime_contract_version_graph_v1.py",
        "tests/runtime/test_runtime_contract_version_graph_v1.py",
        ".github/workflows/runtime-contract-version-graph.yml",
        ".github/workflows/release-main-merge-gate.yml",
        "tests/runtime/test_release_action_pins.py"
    ],
    "certification_evidence": [],
    "acceptance": "Deterministic Python contract/schema dependency graph, fail-closed missing references, content-addressed graph identity, explicit transitive downstream invalidation, exact-head CI and Release Main enforcement. Rust remains retired/frozen."
}
external_index = next(i for i, item in enumerate(registry["capabilities"]) if item.get("classification") == "EXTERNAL")
registry["capabilities"].insert(external_index, entry)
assert registry["python_complete"]["ready"] is False
assert registry["python_complete"]["rust_resume_allowed"] is False
write_json(REGISTRY, registry)

# Registry content participates in the frozen Python contract digest. Re-pin the
# implementation-only Completion Certificate contract without changing its lifecycle.
from tools.certify_python_completion_certificate_v1 import derive_contract_freeze

completion = read_json(COMPLETION)
freeze = derive_contract_freeze(ROOT, registry)
completion["contract_freeze"]["expected_contract_count"] = freeze["contract_count"]
completion["contract_freeze"]["expected_sha256"] = freeze["sha256"]
write_json(COMPLETION, completion)

roadmap = ROADMAP.read_text(encoding="utf-8")
roadmap_marker = "## 2026-08-26 Capability 240 implementation checkpoint"
if roadmap_marker not in roadmap:
    roadmap += (
        "\n\n" + roadmap_marker + "\n\n"
        "`runtime_contract_version_graph_v1` now implements roadmap capability 240 in Python as an internal metadata-only authority. "
        "It discovers Python contract/schema dependencies deterministically, represents referenced non-Python contracts only as metadata leaves, "
        "and computes content-addressed transitive invalidation plans when contract versions or contents change. "
        "This implementation adds no public CLI route, does not mutate contract owners, and does not resume Rust feature/parity work.\n"
    )
    ROADMAP.write_text(roadmap, encoding="utf-8")

checklist = CHECKLIST.read_text(encoding="utf-8")
checklist_marker = "## 2026-08-26 Python hardening continuation"
if checklist_marker not in checklist:
    checklist += (
        "\n\n" + checklist_marker + "\n\n"
        "- [x] SignalBench lifecycle is certified and Python Completion Certificate implementation is admitted but its lifecycle remains partial.\n"
        "- [x] Rust remains retired/frozen at 174/245 with 71 remaining; no Rust feature/parity continuation is authorized.\n"
        "- [x] Start roadmap capability 240 `runtime_contract_version_graph_v1` as Python-only hardening.\n"
        "- [x] Add deterministic contract/schema graph identity and transitive invalidation runtime.\n"
        "- [x] Bind capability 240 to tests, exact-head workflow, Release Main and immutable action-pin enforcement.\n"
        "- [ ] Admit the capability 240 implementation PR after exact-head gates pass.\n"
        "- [ ] Continue to the next Python-only hardening/addition after fresh `main`; do not resume Rust unless explicitly reactivated.\n"
    )
    CHECKLIST.write_text(checklist, encoding="utf-8")

print(json.dumps({
    "capability": capability_id,
    "state": "implemented",
    "required_for_python_complete": False,
    "python_complete": False,
    "rust_resume_allowed": False,
    "contract_freeze_sha256": freeze["sha256"],
}, indent=2))
