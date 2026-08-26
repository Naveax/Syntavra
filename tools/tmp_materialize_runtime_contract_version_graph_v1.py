#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts/python/capability-completeness-registry-v1.json"
COMPLETION = ROOT / "contracts/python/python-completion-certificate-v1.json"
ROADMAP = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md"
CHECKLIST = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md"
LIVE = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md"

BASE_MAIN = "e2ead74f70aef8cbf4da333bf19698231b45327b"
CAPABILITY_ID = "runtime_contract_version_graph_v1"


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object: {path}")
    return value


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


registry = read_json(REGISTRY)
policy = registry["policy"]
policy["python_hardening_may_continue_after_completion_certificate_implementation"] = True
policy["rust_reactivation_requires_explicit_owner_decision"] = True
policy["rust_transition_capabilities_deferred_while_rust_retired"] = True

if CAPABILITY_ID in registry["milestone_order"]:
    raise AssertionError(f"{CAPABILITY_ID} already present in milestone order")
if any(item.get("id") == CAPABILITY_ID for item in registry["capabilities"]):
    raise AssertionError(f"{CAPABILITY_ID} already present in capability rows")

registry["milestone_order"].append(CAPABILITY_ID)
entry = {
    "id": CAPABILITY_ID,
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
        "tests/runtime/test_release_action_pins.py",
        "docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md",
        "docs/SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md",
        "docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md",
    ],
    "certification_evidence": [],
    "acceptance": (
        "Deterministic Python contract/schema dependency graph, fail-closed missing/unsafe references, metadata-only external leaves, "
        "content-addressed graph identity, explicit transitive downstream invalidation, exact-head CI and Release Main enforcement. "
        "No public CLI or parallel store is added and Rust remains retired/frozen."
    ),
}
external_index = next(i for i, item in enumerate(registry["capabilities"]) if item.get("classification") == "EXTERNAL")
registry["capabilities"].insert(external_index, entry)

# Python Completion Certificate is intentionally not finalized by this milestone.
assert registry["python_complete"]["ready"] is False
assert registry["python_complete"]["rust_resume_allowed"] is False
completion_row = next(item for item in registry["capabilities"] if item["id"] == "python_completion_certificate_v1")
assert completion_row["state"] == "partial"
write_json(REGISTRY, registry)

# Registry content participates in the frozen Python contract authority. Recompute
# the implementation-only completion contract pin without changing its lifecycle.
from tools.certify_python_completion_certificate_v1 import derive_contract_freeze

completion = read_json(COMPLETION)
freeze = derive_contract_freeze(ROOT, registry)
completion["contract_freeze"]["expected_contract_count"] = freeze["contract_count"]
completion["contract_freeze"]["expected_sha256"] = freeze["sha256"]
write_json(COMPLETION, completion)

roadmap = ROADMAP.read_text(encoding="utf-8")
roadmap_marker = "## 2026-08-26 Python-only continuation amendment after capability 239 implementation"
if roadmap_marker not in roadmap:
    roadmap += (
        "\n\n" + roadmap_marker + "\n\n"
        "The repository direction continues in Python after the implementation-only Python Completion Certificate pass. "
        "`python_completion_certificate_v1` remains lifecycle-partial and its final phase-exit seal is intentionally parked while Python product hardening/additions continue.\n\n"
        "Active Python-only continuation:\n\n"
        "- capabilities **240–270** continue in roadmap order where they add or harden Python product/runtime authorities;\n"
        "- capabilities **276–280** remain Python product-quality/composition work and may follow their prerequisite Python capabilities;\n"
        "- capabilities **271–275** are Rust-transition work and are deferred while Rust is retired/frozen;\n"
        "- no Python-only capability grants Rust feature, parity, counter or production-promotion authority;\n"
        "- existing canonical owners must be reused instead of creating parallel stores/engines;\n"
        "- each capability keeps implementation admission and lifecycle certification as separate evidence boundaries when applicable.\n\n"
        "### Capability 240 — Runtime Contract Version Graph\n\n"
        "`runtime_contract_version_graph_v1` implements roadmap capability 240 as an internal metadata-only Python authority. "
        "It discovers Python contract/schema dependencies deterministically, treats referenced non-Python contracts as metadata-only leaves, "
        "and computes content-addressed transitive invalidation plans when contract versions or contents change. "
        "It adds no public CLI route, owns no product contract contents, creates no persistent store, and does not resume Rust.\n"
    )
    ROADMAP.write_text(roadmap, encoding="utf-8")

checklist = CHECKLIST.read_text(encoding="utf-8")
checklist_marker = "## 2026-08-26 Current Python-only continuation authority"
if checklist_marker not in checklist:
    checklist += (
        "\n\n" + checklist_marker + "\n\n"
        "- [x] SignalBench Python product lifecycle is certified.\n"
        "- [x] Python Completion Certificate v1 implementation is admitted; its final lifecycle/phase-exit seal remains intentionally parked.\n"
        "- [x] Rust feature/parity development remains retired/frozen at 174/245 production promotion with 71 remaining.\n"
        "- [x] Roadmap 240+ is treated as continued Python hardening/addition work, not as authorization to resume Rust.\n"
        "- [x] Start capability 240 `runtime_contract_version_graph_v1` without a parallel store or public CLI route.\n"
        "- [x] Enforce deterministic graph identity, external metadata-leaf boundaries and transitive invalidation receipts.\n"
        "- [x] Bind capability 240 to regression tests, exact-head workflow, Release Main and immutable action-pin enforcement.\n"
        "- [ ] Admit the capability 240 implementation PR only after permanent helper-free exact-head gates pass.\n"
        "- [ ] Certify capability 240 lifecycle in a separate minimal seal after implementation admission.\n"
        "- [ ] Re-read fresh `main`, then advance to capability 241 `context_decision_trace_v1`.\n"
        "- [ ] Keep capabilities 271–275 deferred while Rust remains retired unless the owner explicitly changes that direction.\n"
    )
    CHECKLIST.write_text(checklist, encoding="utf-8")

LIVE.write_text(
    f"""# Syntavra Python-First Live Checkpoint

Updated: **2026-08-26**

This file is the volatile continuation authority. Historical milestones remain in `SYNTAVRA_PYTHON_FIRST_CHECKPOINT.txt`; `contracts/python/capability-completeness-registry-v1.json` is the machine-readable lifecycle authority; `SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md` remains append-only.

## Current admitted base

- Fresh admitted `main` before capability 240: `{BASE_MAIN}`.
- SignalBench Python product lifecycle: **certified** through PR #173.
- Python Completion Certificate v1 implementation: admitted through PR #174.
- `python_completion_certificate_v1` lifecycle: **partial**, deliberately not phase-exit certified yet.
- `PYTHON_COMPLETE`: false.
- Python feature/hardening authority: active.
- Rust feature/parity development: **retired/frozen**.
- Rust production promotion: **174/245**.
- Remaining Rust parity/promotion set: **71**.
- `rust_resume_allowed`: false.

## Current direction

Syntavra continues Python-side product/runtime hardening after the implementation-only Completion Certificate pass. The old roadmap transition from Python completion directly into Rust work is not the active execution path while Rust is retired.

- Continue Python roadmap capabilities 240–270 in dependency order.
- Continue Python product-quality/composition capabilities 276–280 when their prerequisites are satisfied.
- Keep Rust-transition capabilities 271–275 deferred.
- Do not finalize the parked Python Completion Certificate lifecycle merely to unlock Rust.
- Do not change Rust feature/parity code, promotion counters, Remaining-71 ownership or production authority as part of Python work.
- Reuse canonical runtime owners and extend contracts/composition layers instead of creating duplicate engines, databases or stores.
- Preserve implementation → exact-head admission → separate lifecycle certification discipline.

## Current implementation — capability 240

### `runtime_contract_version_graph_v1`

- Branch: `agent/runtime-contract-version-graph-v1`.
- Roadmap capability: **240 — Runtime Contract Version Graph**.
- Classification: `NEW` internal Python metadata authority.
- Lifecycle target for this implementation pass: `implemented`.
- `required_for_python_complete`: false; this capability does not force the parked phase-exit gate.
- Builds deterministic version/content identities for Python capability contracts.
- Discovers contract references recursively only inside configured Python roots.
- Referenced non-Python contracts are metadata-only leaf authorities; their dependency trees are not recursively imported.
- Missing references, repository-path escapes, invalid schema versions and forbidden external references fail closed.
- Emits deterministic dependent→dependency edges and a content-addressed graph digest.
- Computes transitive reverse-dependency invalidation for added, removed or changed contracts.
- Adds no persistent store, no public CLI route and no contract mutation authority.
- Dedicated regression suite, certifier, exact-head workflow, Release Main binding and immutable action-pin coverage are present.

## Verification boundary

The implementation is not admitted merely because source files exist. Before merge the permanent helper-free tree must pass:

1. Runtime Contract Version Graph regression suite and exact-head certificate.
2. Python Capability Completeness.
3. Python Completion Certificate implementation boundary with lifecycle still partial.
4. Rust Feature Freeze Guard with 174/245 and 71 unchanged.
5. Full repository validation and release smoke.
6. Canonical `MANIFEST.sha256` refresh/check on the permanent tree.
7. Release Main and package/provenance gates created naturally by the final PR head.

## Next exact task

1. Materialize capability 240 registry/continuity state and canonical manifest on the permanent branch.
2. Remove every temporary materializer/helper from the final tree.
3. Run exact-head admission CI without rerun/redispatch polling.
4. Open/finish the capability 240 implementation PR only on the validated helper-free tree.
5. After implementation admission, create a separate minimal lifecycle certification seal for capability 240.
6. Re-read fresh `main` and begin capability 241 `context_decision_trace_v1` by composing existing Adaptive Context Policy, Runtime Evidence, receipt and handoff authorities.

## Required continuation instruction

```text
Continue Syntavra from docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md and cross-check the capability registry plus append-only roadmap before choosing work.
Python is the active feature/hardening authority. Rust remains retired/frozen at 174/245 with 71 remaining and rust_resume_allowed=false.
Do not use Python Completion Certificate lifecycle certification as an implicit Rust-resume action.
Continue Python-only capabilities one admitted milestone at a time, reusing canonical owners and preserving exact evidence/recovery boundaries.
When CI is active, track the existing equivalent run by run_id instead of creating a duplicate; continue independent work while it runs.
```
""",
    encoding="utf-8",
)

print(json.dumps({
    "capability": CAPABILITY_ID,
    "state": "implemented",
    "required_for_python_complete": False,
    "python_complete": False,
    "python_completion_certificate_lifecycle": "partial",
    "rust_resume_allowed": False,
    "rust_retired_frozen": True,
    "rust_production_promoted": 174,
    "rust_remaining": 71,
    "next_python_capability": "context_decision_trace_v1",
    "contract_freeze_sha256": freeze["sha256"],
}, indent=2))
