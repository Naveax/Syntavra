from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "repair_r38_selector_option_values.py"
SPEC = importlib.util.spec_from_file_location(
    "repair_r38_selector_option_values",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _source(option_block: str, truncation_block: str) -> str:
    return (
        "prefix\n"
        "fn command_path(arguments: &[String]) -> Vec<String> {\n"
        "    let mut positional = Vec::new();\n"
        "    let mut index = 0usize;\n"
        "    while index < arguments.len() {\n"
        "        let value = &arguments[index];\n"
        + option_block
        + "            index += 2;\n"
        "            continue;\n"
        "        }\n"
        "        positional.push(value.clone());\n"
        "        index += 1;\n"
        "    }\n"
        + truncation_block
        + "    } else {\n"
        "        positional.truncate(2);\n"
        "    }\n"
        "    positional\n"
        "}\n"
        "\nfn executable_exists(path: &Path) -> bool {\n"
        "    path.is_file()\n"
        "}\n"
        "suffix\n"
    )


def test_selector_option_value_repair_migrates_legacy_source(
    tmp_path: Path,
) -> None:
    target = tmp_path / "syntavra.rs"
    target.write_text(
        _source(MODULE.LEGACY_OPTION_BLOCK, MODULE.LEGACY_CONTEXT_TRUNCATION),
        encoding="utf-8",
    )

    assert MODULE.repair(target) is True
    first = target.read_text(encoding="utf-8")
    assert MODULE.LEGACY_OPTION_BLOCK not in first
    assert MODULE.LEGACY_CONTEXT_TRUNCATION not in first
    assert first.count(MODULE.CANONICAL_OPTION_BLOCK) == 1
    assert first.count(MODULE.CANONICAL_TRUNCATION) == 1

    assert MODULE.repair(target) is False
    assert target.read_text(encoding="utf-8") == first


def test_selector_option_value_repair_adds_session_hint(
    tmp_path: Path,
) -> None:
    target = tmp_path / "syntavra.rs"
    target.write_text(
        _source(MODULE.PRE_SESSION_OPTION_BLOCK, MODULE.CANONICAL_TRUNCATION),
        encoding="utf-8",
    )

    assert MODULE.repair(target) is True
    rendered = target.read_text(encoding="utf-8")
    assert MODULE.PRE_SESSION_OPTION_BLOCK not in rendered
    assert rendered.count(MODULE.CANONICAL_OPTION_BLOCK) == 1
    assert rendered.count('"--session-hint"') == 1
    assert MODULE.repair(target) is False


def test_selector_option_value_repair_collapses_duplicate_branches(
    tmp_path: Path,
) -> None:
    target = tmp_path / "syntavra.rs"
    target.write_text(
        _source(MODULE.CANONICAL_OPTION_BLOCK, MODULE.DUPLICATE_TRUNCATION),
        encoding="utf-8",
    )

    assert MODULE.repair(target) is True
    rendered = target.read_text(encoding="utf-8")
    assert MODULE.DUPLICATE_TRUNCATION not in rendered
    assert rendered.count("positional.truncate(1);") == 1
    assert MODULE.repair(target) is False


def test_selector_option_value_repair_accepts_canonical_source(
    tmp_path: Path,
) -> None:
    target = tmp_path / "syntavra.rs"
    canonical = _source(MODULE.CANONICAL_OPTION_BLOCK, MODULE.CANONICAL_TRUNCATION)
    target.write_text(canonical, encoding="utf-8")

    assert MODULE.repair(target) is False
    assert target.read_text(encoding="utf-8") == canonical


def test_selector_option_value_repair_rejects_missing_value_option(
    tmp_path: Path,
) -> None:
    target = tmp_path / "syntavra.rs"
    broken_options = MODULE.CANONICAL_OPTION_BLOCK.replace(
        '                | "--session-hint"\n',
        "",
    )
    target.write_text(
        _source(broken_options, MODULE.CANONICAL_TRUNCATION),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="value options missing"):
        MODULE.repair(target)
