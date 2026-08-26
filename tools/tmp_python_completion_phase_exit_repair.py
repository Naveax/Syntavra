#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEGACY_INLINE_CERTIFIERS = [
    "tools/certify_universal_context_item.py",
    "tools/certify_evidence_store_v2.py",
    "tools/certify_typed_context_object_store.py",
    "tools/certify_programmatic_execution.py",
    "tools/certify_deferred_tool_discovery.py",
    "tools/certify_context_namespace.py",
    "tools/certify_adaptive_context_policy.py",
]

OLD_GLOBAL_STATE = (
    '    _require(completeness.get("python_complete_ready") is False, "Python COMPLETE unexpectedly true")\n'
    '    _require(completeness.get("rust_resume_allowed") is False, "Rust resume unexpectedly true")\n'
)
NEW_GLOBAL_STATE = (
    '    _require(isinstance(completeness.get("python_complete_ready"), bool), "Python COMPLETE state must be boolean")\n'
    '    _require(\n'
    '        completeness.get("python_complete_ready") is completeness.get("rust_resume_allowed"),\n'
    '        "Python COMPLETE/Rust resume state disagreement",\n'
    '    )\n'
)

MULTI_GRAPH_OLD = (
    '    _require(\n'
    '        completeness.get("python_complete_ready") is False,\n'
    '        "Python COMPLETE unexpectedly true",\n'
    '    )\n'
    '    _require(\n'
    '        completeness.get("rust_resume_allowed") is False,\n'
    '        "Rust resume unexpectedly true",\n'
    '    )\n'
)
MULTI_GRAPH_NEW = (
    '    _require(\n'
    '        isinstance(completeness.get("python_complete_ready"), bool),\n'
    '        "Python COMPLETE state must be boolean",\n'
    '    )\n'
    '    _require(\n'
    '        completeness.get("python_complete_ready") is completeness.get("rust_resume_allowed"),\n'
    '        "Python COMPLETE/Rust resume state disagreement",\n'
    '    )\n'
)


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise AssertionError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


for relative in LEGACY_INLINE_CERTIFIERS:
    replace_once(ROOT / relative, OLD_GLOBAL_STATE, NEW_GLOBAL_STATE, "legacy global lifecycle assertion")

replace_once(
    ROOT / "tools/certify_multi_graph_retrieval.py",
    MULTI_GRAPH_OLD,
    MULTI_GRAPH_NEW,
    "multi-graph global lifecycle assertion",
)

replace_once(
    ROOT / "tools/certify_universal_context_item.py",
    '    _require(rust_freeze.get("rust_resume_allowed") is False, "Rust resume unexpectedly enabled")\n',
    '    _require(\n'
    '        rust_freeze.get("rust_resume_allowed") is completeness.get("rust_resume_allowed"),\n'
    '        "Rust freeze/current resume state disagreement",\n'
    '    )\n',
    "universal rust resume assertion",
)

checklist = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md"
text = checklist.read_text(encoding="utf-8")

hard_rules = """## Hard rules

- [x] The Python-first implementation/certification phase is complete; Python remains the frozen reference/oracle for Rust differential work.
- [x] Rust code, tests, ownership records and contracts are retained.
- [x] `PYTHON_COMPLETE = true` and Rust feature/parity development resume is allowed only through the canonical completion state.
- [x] Rust production promotion baseline stays **174/245** with **71** remaining until the separate promotion authority passes.
- [x] Remaining owned stays **71**, unowned stays **0** unless the canonical inventory changes for a separately reviewed reason.
- [x] Rust feature/parity work may resume after Python COMPLETE, but this does not grant production promotion credit.
- [x] Do not change the native production counter or perform 174→245 promotion before differential and promotion gates pass.
- [x] The master roadmap is append-only. Existing roadmap material must not be deleted, shortened or rewritten.
- [x] Reuse the frozen Python contracts, behavior vectors and receipts as the Rust migration oracle instead of creating parallel authorities.

Maintenance exceptions remain narrow and must never bypass the separate production-promotion authority.
"""

repo_state = """## Repository state at checkpoint

- PR #174 (`Implement Python Completion Certificate v1`) merged to `main` as `e2ead74f70aef8cbf4da333bf19698231b45327b`.
- Merge-push Python Completion Certificate run `32979620715` passed Linux, Windows, aggregate validation and exact-head certificate generation.
- Active phase-exit branch: `agent/python-completion-phase-exit-v1`.
- Python Completion Certificate lifecycle is `certified`; the canonical registry records `PYTHON_COMPLETE = true` and `rust_resume_allowed = true`.
- Rust feature/parity work may resume, while production promotion remains frozen at **174/245** with **71** remaining.
- External superiority, adoption, marketplace maturity and long-lived real-world claims remain external-evidence claims and are not manufactured by repository self-certification.
"""

immediate = """## Immediate exact task

- [x] Merge PR #174 after exact-head completion, Release Main, Rust Freeze, Capability Completeness and SignalBench gates passed.
- [x] Verify merge-push Completion Certificate run `32979620715` on `main`.
- [x] Materialize the final Python phase-exit registry, authority and Rust-resume transition without changing production promotion.
- [ ] Reconcile all historical milestone certifiers with the canonical post-completion global lifecycle state.
- [ ] Remove all temporary phase-exit helper files and verify a helper-free manifest/diff.
- [ ] Open the final phase-exit PR from `agent/python-completion-phase-exit-v1`.
- [ ] Require exact-head Linux + Windows Completion Certificate `PASS` plus all load-bearing CI on the final PR head.
- [ ] Merge the final phase-exit seal only after every exact-head gate is green.
- [ ] Re-read fresh `main`, export the frozen Python→Rust contract/behavior corpus, then continue Remaining-71 differential work. Production promotion remains a later separate gate.
"""

patterns = [
    (r"## Hard rules\n.*?\n## Repository state at checkpoint", hard_rules + "\n## Repository state at checkpoint"),
    (r"## Repository state at checkpoint\n.*?\n## Immediate exact task", repo_state + "\n## Immediate exact task"),
    (r"## Immediate exact task\n.*?\n## Existing Python baseline", immediate + "\n## Existing Python baseline"),
]
for pattern, replacement in patterns:
    text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise AssertionError(f"continuity section replacement failed: {pattern}")

text = text.replace("- [x] `memory_retrieval_v1` — current admission candidate", "- [x] `memory_retrieval_v1`", 1)

copy_message = """## Copy/paste continuation message

```text
Continue Syntavra from the post-Python-COMPLETE Rust-resume boundary.

Read this file and the Python-first roadmap appendix first, then resolve the current GitHub PR/CI state.

Hard rules:
- Master roadmap is append-only. Delete/rewrite nothing from it.
- Python Completion Certificate is PASS/certified and Python stays the frozen reference/oracle.
- Rust feature/parity development may resume.
- Rust production promotion remains 174/245 with 71 remaining.
- Do not perform the atomic 174→245 production promotion until the separate differential and promotion authorities pass.
- Do not change the native production counter merely because Rust development resumed.
- Reuse frozen Python contracts, golden behavior vectors and receipts as migration authority.
- Keep public API surface small and avoid duplicate engines/authorities.
- Every Rust differential/port step needs tests, exact-head evidence and acceptance criteria.
- Work on the first unchecked Rust-resume/differential task only.
- Do not mark a task complete without verification.

Before new Rust feature/parity work:
1. Resolve fresh `main`, current branch/head and the final phase-exit PR/CI state.
2. If the phase-exit PR is still open, finish exact-head Linux/Windows Completion Certificate and all load-bearing CI, then merge it.
3. Re-read `main` after merge.
4. Export the frozen Python→Rust contract corpus and golden behavior vectors/receipts.
5. Rebase Remaining-71 differential families on the frozen Python product behavior.

Then continue in this order:
Frozen Python contract corpus
→ Golden behavior vectors/receipts
→ Remaining-71 differential rebase
→ Rust capability ports
→ Required differential certification
→ Atomic 174→245 promotion
→ 245/245 post-promotion certification
→ Python-oracle certification window
→ Rust authority decision

At the end update:
CURRENT HEAD
COMPLETED
VERIFIED
BLOCKERS
NEXT EXACT TASK
```
"""
text, count = re.subn(r"## Copy/paste continuation message\n.*\Z", copy_message, text, count=1, flags=re.S)
if count != 1:
    raise AssertionError("copy/paste continuation block replacement failed")

checklist.write_text(text, encoding="utf-8")
print("phase-exit legacy repair staged")
