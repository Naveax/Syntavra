from __future__ import annotations

import json
from pathlib import Path

from syntavra_runtime.config_contract import (
    encode_config_wire,
    resolve_config_phases,
    status_projection,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "parity" / "fixtures" / "config-status-v1.json"


def _cases() -> dict[str, dict]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {str(case["name"]): case for case in value["cases"]}


def _winner(config: dict, path: str) -> dict:
    rows = [item for item in config["provenance"] if item["path"] == path]
    assert rows
    return rows[-1]


def test_r6_fixture_contract_is_deterministic() -> None:
    for case in _cases().values():
        first = resolve_config_phases(case["phases"])
        second = resolve_config_phases(case["phases"])
        assert first == second
        assert len(first["config_hash"]) == 64
        assert encode_config_wire(case["phases"]) == encode_config_wire(case["phases"])


def test_r6_scope_precedence_and_provenance() -> None:
    case = _cases()["scope-precedence"]
    config = resolve_config_phases(case["phases"])
    assert config["values"]["runtime"]["profile"] == "balanced"
    assert config["values"]["routing"]["budget_bytes"] == 16384
    assert _winner(config, "runtime.profile") == {
        "path": "runtime.profile",
        "value": "balanced",
        "source": "task-override",
        "scope": "task",
    }


def test_r6_last_good_fallback_is_fail_closed() -> None:
    case = _cases()["last-good-fallback"]
    config = resolve_config_phases(case["phases"])
    assert config["values"]["runtime"]["profile"] == "compact"
    assert config["values"]["security"]["dlp"] == "preferred"
    assert config["warnings"] == ["invalid-current-config-fell-back:ConfigError"]


def test_r6_environment_secret_reference_is_redacted_only_in_provenance() -> None:
    case = _cases()["environment-secret-reference"]
    config = resolve_config_phases(case["phases"])
    assert config["values"]["provider"]["credential_ref"] == "secret://provider/default"
    assert _winner(config, "provider.credential_ref")["value"] == "[secret-ref]"


def test_r6_status_preserves_locked_identity_and_read_only_boundary() -> None:
    config = resolve_config_phases([{}])
    status = status_projection(config)
    assert status["product"] == "Syntavra"
    assert status["product_version"] == "0.0.1"
    assert status["release_channel"] == "pre-release"
    assert status["version_locked"] is True
    assert status["candidate_engine"] == "rust"
    assert status["general_command_routing"] == "blocked"
    assert status["mutation"] == "read-only"
