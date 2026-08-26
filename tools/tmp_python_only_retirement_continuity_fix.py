#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts/python/capability-completeness-registry-v1.json"
COMPLETION = ROOT / "contracts/python/python-completion-certificate-v1.json"
CHECKLIST = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md"
LIVE = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md"
ROADMAP = ROOT / "docs/SYNTAVRA_PYTHON_FIRST_ROADMAP_APPENDIX.md"


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


registry = read_json(REGISTRY)
policy = registry["policy"]
policy["python_hardening_continues_while_rust_retired"] = True
policy["rust_transition_capabilities_deferred_while_retired"] = True
assert registry["python_complete"]["ready"] is True
assert registry["python_complete"]["rust_resume_allowed"] is False
assert registry["python_complete"]["rust_retired"] is True
write_json(REGISTRY, registry)

# The capability registry participates in the Python contract freeze. Re-pin after
# adding the durable continuation policy so the final certificate remains exact.
from tools.certify_python_completion_certificate_v1 import derive_contract_freeze

completion = read_json(COMPLETION)
freeze = derive_contract_freeze(ROOT, registry)
completion["contract_freeze"]["expected_contract_count"] = freeze["contract_count"]
completion["contract_freeze"]["expected_sha256"] = freeze["sha256"]
write_json(COMPLETION, completion)

checklist = CHECKLIST.read_text(encoding="utf-8")
marker = "## Rust resume: do not start before Python COMPLETE"
if marker not in checklist:
    raise AssertionError("legacy Rust-resume checklist marker missing")
prefix = checklist.split(marker, 1)[0].rstrip()
replacement_tail = r'''
## Rust retirement boundary after Python COMPLETE

- [x] `PYTHON_COMPLETE = true` is independent from Rust reactivation.
- [x] `rust_resume_allowed = false` while Rust is retired.
- [x] Rust feature/parity development remains frozen.
- [x] Rust production promotion remains **174/245** with **71** remaining.
- [x] Remaining owned stays **71** and unowned stays **0** unless a separately reviewed canonical inventory change says otherwise.
- [x] Python remains the active product/feature/hardening authority.
- [ ] Do not export or consume the Python→Rust migration corpus as an active port plan while Rust is retired.
- [ ] Do not start Remaining-71 differential/port work while Rust is retired.
- [ ] Reactivate Rust only through a separate explicit, reviewed and admitted reactivation authority.
- [ ] Even after a future reactivation, production promotion remains a separate gate from feature/parity work.

## Required end-of-session checkpoint

Update this block after every implementation session:

```text
CURRENT HEAD:
<sha>

ACTIVE BRANCH:
<branch>

PYTHON STATUS:
<active Python capability / hardening / certification item>

RUST STATUS:
RETIRED/FROZEN — rust_resume_allowed=false; production promotion 174/245; 71 remaining

COMPLETED:
- ...

VERIFIED:
- ...

BLOCKERS:
- ...

NEXT EXACT TASK:
- ...
```

## Copy/paste continuation message

```text
Continue Syntavra in Python-active / Rust-retired mode.

Read docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md, this checklist, the append-only Python-first roadmap, and contracts/python/capability-completeness-registry-v1.json before choosing work.

Hard rules:
- Python is the active product/feature/hardening authority.
- PYTHON_COMPLETE may be true while Rust remains retired.
- rust_resume_allowed=false until a separate explicit Rust-reactivation authority is admitted.
- Rust feature/parity work stays frozen at 174/245 production promotion with 71 remaining.
- Do not start Remaining-71 differential/port work and do not change Rust promotion counters.
- Continue Python roadmap capabilities in dependency order, reusing canonical owners instead of adding parallel stores/engines.
- Capabilities 271–275 are Rust-transition work and remain deferred while Rust is retired.
- Keep external superiority/adoption/maturity claims evidence-gated.
- Before any workflow dispatch/rerun, check queued/in_progress equivalent runs; never use rerun as polling.
```
'''.strip()
CHECKLIST.write_text(prefix + "\n\n" + replacement_tail + "\n", encoding="utf-8")

LIVE.write_text(
    '''# Syntavra Python-First Live Checkpoint

Updated: **2026-08-26**

This file is the volatile continuation authority. Historical checkpoints remain historical; the capability registry and append-only roadmap are the machine-readable/long-form authorities.

## Current admitted base

- `main`: `e2ead74f70aef8cbf4da333bf19698231b45327b` before the final Python-only phase-exit seal.
- SignalBench Python product lifecycle: certified.
- Python Completion Certificate v1: implementation admitted and phase-exit lifecycle being sealed on `agent/python-completion-phase-exit-v1`.
- Python feature/hardening authority: active.
- Rust feature/parity development: retired/frozen.
- Rust production promotion: 174/245.
- Remaining Rust parity/promotion set: 71.

## Canonical post-completion state

The phase-exit seal deliberately separates Python completion from Rust reactivation:

```text
PYTHON_COMPLETE = true
rust_resume_allowed = false
rust_retired = true
Rust production promotion = 174/245
Remaining = 71
```

Python Completion is necessary evidence for any hypothetical future Rust reactivation, but it is not sufficient authority to reactivate Rust. A separate explicit reviewed/admitted decision is required.

## Active development direction

Syntavra continues as a Python-active product/runtime while Rust is retired:

- Python roadmap capabilities 240–270 remain active in dependency order.
- Roadmap capabilities 271–275 are Rust-transition work and remain deferred.
- Python product-quality/composition capabilities 276–280 remain active when their Python prerequisites are satisfied.
- Existing canonical owners must be reused instead of creating parallel databases, stores, engines or public surfaces without need.
- External superiority, adoption and maturity claims remain externally evidence-gated.
- Implementation admission and lifecycle certification remain separate boundaries where the repository already uses that discipline.

## Current exact task

1. Seal `agent/python-completion-phase-exit-v1` helper-free with `PYTHON_COMPLETE=true`, `rust_resume_allowed=false`, `rust_retired=true`.
2. Require Python Completion, Python Authority, Capability Completeness, Rust Freeze, repository validation, release smoke and exact-head PR gates to agree on that state.
3. Do not merge without explicit authorization.
4. After phase-exit admission, re-read fresh `main` and rebuild capability 240 `runtime_contract_version_graph_v1` on top of the admitted retirement authority.
5. Continue to capability 241 `context_decision_trace_v1` only after capability 240 implementation/lifecycle admission.

## CI discipline

Before dispatch/rerun, inspect queued/in-progress equivalent runs. Track an existing run by `run_id`; never rerun as polling. CI waiting is used for independent source/authority review instead of creating duplicate runs.
''',
    encoding="utf-8",
)

roadmap = ROADMAP.read_text(encoding="utf-8")
roadmap_marker = "## 2026-08-26 Python continuation while Rust is retired"
if roadmap_marker not in roadmap:
    roadmap += (
        "\n\n" + roadmap_marker + "\n\n"
        "The Rust retirement override does not end Python product development. While `rust_resume_allowed=false`, capabilities **240–270** continue as Python-side runtime/product hardening and additions in dependency order, capabilities **271–275** remain deferred because they are Rust-transition work, and capabilities **276–280** remain Python product-quality/composition work subject to their Python prerequisites. Python COMPLETE and Rust reactivation are separate state machines; no Python capability implicitly authorizes Rust feature/parity work or production promotion.\n"
    )
    ROADMAP.write_text(roadmap, encoding="utf-8")

print(json.dumps({
    "python_complete": True,
    "rust_resume_allowed": False,
    "rust_retired": True,
    "python_continuation": "240-270,276-280",
    "deferred_rust_transition": "271-275",
    "contract_freeze_sha256": freeze["sha256"],
}, indent=2))
