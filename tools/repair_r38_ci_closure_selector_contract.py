#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "repair_r38_ci_closure.py"

IMPORT = (
    "from repair_r38_runtime_selector_contract import "
    "repair as repair_runtime_selector_contract\n"
)
FUNCTION_PATTERN = re.compile(
    r"def repair_init_selector_contract\(\) -> bool:\n.*?\n\ndef repair_context_contract\(\) -> bool:",
    re.DOTALL,
)
CANONICAL_FUNCTION = '''def repair_init_selector_contract() -> bool:
    changed = repair_runtime_selector_contract()
    legacy_single = 'Some("rollout-tail" | "context-stress" | "claim" | "context")'
    init_single = (
        'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init")'
    )
    canonical_single = (
        'Some("rollout-tail" | "context-stress" | "claim" | "context" | "init" | "hook" | "mcp")'
    )

    source = SELECTOR.read_text(encoding="utf-8")
    counts = {
        "legacy": source.count(legacy_single),
        "init": source.count(init_single),
        "canonical": source.count(canonical_single),
    }
    if counts == {"legacy": 0, "init": 0, "canonical": 1}:
        pass
    elif counts["canonical"] == 0 and counts["legacy"] + counts["init"] == 1:
        previous = legacy_single if counts["legacy"] == 1 else init_single
        SELECTOR.write_text(
            source.replace(previous, canonical_single, 1),
            encoding="utf-8",
            newline="\n",
        )
        changed = True
    else:
        raise RuntimeError(
            "single-segment selector invariant failed: "
            f"legacy={counts['legacy']}, init={counts['init']}, "
            f"canonical={counts['canonical']}"
        )

    source = SELECTOR.read_text(encoding="utf-8")
    required_tokens = (
        '"--skill-root"',
        '"--host"',
        '"--mcp-profile"',
        'value.starts_with("--skill-root=")',
        'value.starts_with("--host=")',
        'value.starts_with("--mcp-profile=")',
        'value.starts_with("--codex-home=")',
        'value.starts_with("--rollout=")',
        'value.starts_with("--state-file=")',
        'value.starts_with("--session-hint=")',
    )
    missing = [token for token in required_tokens if token not in source]
    if missing:
        raise RuntimeError(f"selector canonical option tokens missing: {missing}")
    return changed


def repair_context_contract() -> bool:'''


def render(source: str) -> tuple[str, bool]:
    rendered = source
    changed = False

    import_count = rendered.count(IMPORT)
    if import_count == 0:
        anchor = "from pathlib import Path\n"
        if rendered.count(anchor) != 1:
            raise RuntimeError("CI closure pathlib import anchor must be unique")
        rendered = rendered.replace(anchor, anchor + "\n" + IMPORT, 1)
        changed = True
    elif import_count != 1:
        raise RuntimeError(f"CI closure selector helper import count invalid: {import_count}")

    canonical_marker = "    changed = repair_runtime_selector_contract()\n"
    marker_count = rendered.count(canonical_marker)
    if marker_count == 0:
        rendered, count = FUNCTION_PATTERN.subn(CANONICAL_FUNCTION, rendered, count=1)
        if count != 1:
            raise RuntimeError(f"CI closure selector function boundary count invalid: {count}")
        changed = True
    elif marker_count != 1:
        raise RuntimeError(f"CI closure canonical selector function count invalid: {marker_count}")

    if rendered.count(IMPORT) != 1:
        raise RuntimeError("CI closure selector helper import invariant failed")
    if rendered.count(canonical_marker) != 1:
        raise RuntimeError("CI closure canonical selector function invariant failed")
    if '| "hook" | "mcp")' not in rendered:
        raise RuntimeError("CI closure canonical hook/mcp selector surface missing")
    return rendered, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    source = TARGET.read_text(encoding="utf-8")
    rendered, changed = render(source)
    if arguments.check and changed:
        raise RuntimeError("CI closure selector contract requires repair")
    if changed:
        TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "changed": changed,
                "mode": "check" if arguments.check else "repair",
                "ok": True,
                "surface": "ci-closure-selector-contract",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
