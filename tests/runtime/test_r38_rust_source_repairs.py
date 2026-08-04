from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "repair_r38_rust_sources.py"
SPEC = importlib.util.spec_from_file_location("repair_r38_rust_sources", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_r38_rust_source_repairs_are_complete_and_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fragments: dict[str, list[str]] = defaultdict(list)
    for replacement in MODULE.REPLACEMENTS:
        fragments[replacement.path].append(replacement.old)

    for relative, values in fragments.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n\n".join(values) + "\n", encoding="utf-8")

    monkeypatch.setattr(MODULE, "ROOT", tmp_path)

    assert MODULE.repair() == 0
    first = {
        relative: (tmp_path / relative).read_text(encoding="utf-8")
        for relative in fragments
    }
    for replacement in MODULE.REPLACEMENTS:
        rendered = first[replacement.path]
        assert rendered.count(replacement.new) == 1
        if replacement.old not in replacement.new:
            assert replacement.old not in rendered

    assert MODULE.repair() == 0
    second = {
        relative: (tmp_path / relative).read_text(encoding="utf-8")
        for relative in fragments
    }
    assert second == first
