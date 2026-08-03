from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
MODULE_PATH = TOOLS / "sync_r38_generated_metadata.py"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("sync_r38_generated_metadata", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_native_expansion_normalization_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "native_product.rs"
    declaration = MODULE.EXPANSION_DECLARATION
    anchor = MODULE.EXPANSION_ANCHOR
    source.write_text(
        "#![forbid(unsafe_code)]\n\n" + declaration * 11 + anchor,
        encoding="utf-8",
    )

    assert MODULE.normalize_native_expansion(source) is True
    first = source.read_text(encoding="utf-8")
    assert first.count(declaration) == 1
    assert first.count(anchor) == 1

    assert MODULE.normalize_native_expansion(source) is False
    assert source.read_text(encoding="utf-8") == first


def test_required_native_commands_are_added_once(tmp_path: Path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            {
                "rust_surface": {
                    "native_public_commands": ["output profiles", "version"]
                }
            }
        ),
        encoding="utf-8",
    )

    assert MODULE.ensure_required_native_commands(contract) is True
    first = json.loads(contract.read_text(encoding="utf-8"))
    assert first["rust_surface"]["native_public_commands"] == [
        "host",
        "host capabilities",
        "host detect",
        "host negotiate",
        "output compact",
        "output govern",
        "output profiles",
        "version",
    ]

    assert MODULE.ensure_required_native_commands(contract) is False
    second = json.loads(contract.read_text(encoding="utf-8"))
    assert second == first
