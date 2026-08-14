from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_native_publish_identity_targets_production_selector() -> None:
    readiness = json.loads((ROOT / "release" / "publish-readiness.json").read_text(encoding="utf-8"))
    native = readiness["native"]
    legacy = readiness["legacy_native_companion"]

    assert native["package"] == "syntavra-cli"
    assert native["binary"] == "syntavra"
    assert native["publish_order"] == ["syntavra-contracts", "syntavra-core", "syntavra-cli"]
    assert native["published"] is False

    assert legacy["package"] == "syntavra-native"
    assert legacy["workspace_member"] is False
    assert legacy["production_selector"] is False
    assert legacy["published"] is False


def test_rust_publish_graph_has_versioned_path_dependencies() -> None:
    cli = _toml(ROOT / "crates" / "syntavra-cli" / "Cargo.toml")
    contracts = _toml(ROOT / "crates" / "syntavra-contracts" / "Cargo.toml")
    core = _toml(ROOT / "crates" / "syntavra-core" / "Cargo.toml")
    workspace = _toml(ROOT / "Cargo.toml")

    assert cli["package"]["name"] == "syntavra-cli"
    assert any(row["name"] == "syntavra" for row in cli["bin"])
    assert cli["dependencies"]["syntavra-contracts"] == {
        "version": "0.0.1",
        "path": "../syntavra-contracts",
    }
    assert cli["dependencies"]["syntavra-core"] == {
        "version": "0.0.1",
        "path": "../syntavra-core",
    }
    assert contracts["package"]["name"] == "syntavra-contracts"
    assert core["package"]["name"] == "syntavra-core"
    assert "native/syntavra-native" in workspace["workspace"]["exclude"]
