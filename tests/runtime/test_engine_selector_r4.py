from __future__ import annotations

import json
from pathlib import Path

import pytest

from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    EngineSelectionError,
    EngineSelector,
)


def _write_config(path: Path, engine: str, *, schema_version: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": schema_version, "engine": engine}),
        encoding="utf-8",
    )


def _rust_runner(_binary: Path, arguments: tuple[str, ...]):
    if arguments == ("version",):
        return {
            "product": "Syntavra",
            "product_version": "0.0.1",
            "release_channel": "pre-release",
            "engine": "rust",
            "engine_stability": "experimental",
            "contract_version": 1,
        }
    if arguments == ("engine", "capabilities"):
        return {
            "contract_version": 1,
            "capabilities": [
                {"name": name, "maturity": "preview", "mutation": "read-only"}
                for name in (
                    "config.explain",
                    "config.resolve",
                    "config.show",
                    "engine.capabilities",
                    "engine.contract-hash",
                    "migration.plan",
                    "pipeline.describe",
                    "plugins.list",
                    "receipt.inspect",
                    "scheduler.list",
                    "scheduler.stats",
                    "state.broker-live-snapshot",
                    "state.broker-snapshot",
                    "state.inspect",
                    "state.layout",
                    "status",
                    "version",
                )
            ],
        }
    if arguments == ("engine", "contract-hash"):
        return {
            "engine": "rust",
            "contract_version": 1,
            "algorithm": "sha256",
            "contract_hash": ENGINE_CONTRACT_SHA256,
        }
    raise AssertionError(arguments)


def test_selection_precedence_is_command_environment_project_user_default(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project_config = project / ".syntavra" / "engine.json"
    user_config = tmp_path / "user" / "engine.json"
    _write_config(user_config, "rust")
    selector = EngineSelector(
        project_root=project,
        user_config=user_config,
        env={"HOME": str(tmp_path / "home")},
    )
    assert selector.resolve().requested == "rust"
    assert selector.resolve().scope == "user"

    _write_config(project_config, "auto")
    selection = selector.resolve()
    assert selection.requested == "auto"
    assert selection.resolved == "python"
    assert selection.scope == "project"

    selector = EngineSelector(
        project_root=project,
        user_config=user_config,
        env={"HOME": str(tmp_path / "home"), "SYNTAVRA_ENGINE": "rust"},
    )
    assert selector.resolve().source == "SYNTAVRA_ENGINE"
    assert selector.resolve(cli_override="python").source == "--engine"
    assert selector.resolve(cli_override="python").resolved == "python"


def test_builtin_default_and_auto_policy_are_python(tmp_path: Path) -> None:
    selector = EngineSelector(project_root=tmp_path, env={"HOME": str(tmp_path / "home")})
    selection = selector.resolve()
    assert selection.requested == "python"
    assert selection.resolved == "python"
    assert selection.reason == "BUILTIN_PYTHON_DEFAULT"

    _write_config(tmp_path / ".syntavra" / "engine.json", "auto")
    auto = selector.resolve()
    assert auto.requested == "auto"
    assert auto.resolved == "python"
    assert auto.reason == "AUTO_POLICY_PYTHON_R4"


def test_invalid_or_future_config_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / ".syntavra" / "engine.json"
    _write_config(config, "python", schema_version=2)
    selector = EngineSelector(project_root=tmp_path, env={"HOME": str(tmp_path / "home")})
    with pytest.raises(EngineSelectionError) as error:
        selector.resolve()
    assert error.value.code == "ENGINE_CONFIG_SCHEMA_UNSUPPORTED"

    config.write_text('{"schema_version":1,"engine":"python","extra":true}', encoding="utf-8")
    with pytest.raises(EngineSelectionError) as error:
        selector.resolve()
    assert error.value.code == "ENGINE_CONFIG_UNKNOWN_FIELDS"


def test_rust_verification_and_persisted_selection(tmp_path: Path) -> None:
    binary = tmp_path / "syntavra-rs"
    binary.write_bytes(b"test")
    selector = EngineSelector(
        project_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        rust_binary=binary,
        runner=_rust_runner,
    )
    verification = selector.verify_rust()
    assert verification.available
    assert verification.compatible
    assert verification.capabilities == (
        "config.explain",
        "config.resolve",
        "config.show",
        "engine.capabilities",
        "engine.contract-hash",
        "migration.plan",
        "pipeline.describe",
        "plugins.list",
        "receipt.inspect",
        "scheduler.list",
        "scheduler.stats",
        "state.broker-live-snapshot",
        "state.broker-snapshot",
        "state.inspect",
        "state.layout",
        "status",
        "version",
    )

    result = selector.use("rust")
    assert result["ok"]
    assert result["persisted"]["engine"] == "rust"
    assert selector.resolve().resolved == "rust"
    stored = json.loads((tmp_path / ".syntavra" / "engine.json").read_text(encoding="utf-8"))
    assert stored == {"engine": "rust", "schema_version": 1}


def test_rust_selection_requires_verified_binary(tmp_path: Path) -> None:
    selector = EngineSelector(
        project_root=tmp_path,
        env={
            "HOME": str(tmp_path / "home"),
            "SYNTAVRA_RUST_BIN": str(tmp_path / "missing-rust"),
        },
    )
    with pytest.raises(EngineSelectionError) as error:
        selector.use("rust")
    assert error.value.code == "RUST_ENGINE_NOT_VERIFIED"


def test_environment_override_remains_effective_after_use(tmp_path: Path) -> None:
    selector = EngineSelector(
        project_root=tmp_path,
        env={"HOME": str(tmp_path / "home"), "SYNTAVRA_ENGINE": "rust"},
    )
    result = selector.use("python")
    assert result["persisted"]["engine"] == "python"
    assert result["effective"]["resolved"] == "rust"
    assert result["warnings"] == ["higher-precedence-override-remains-active"]


def test_general_rust_command_gate_is_fail_closed(tmp_path: Path) -> None:
    binary = tmp_path / "syntavra-rs"
    binary.write_bytes(b"test")
    selector = EngineSelector(
        project_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        rust_binary=binary,
        runner=_rust_runner,
    )
    with pytest.raises(EngineSelectionError) as error:
        selector.gate_general_command("status", cli_override="rust")
    assert error.value.code == "RUST_COMMAND_ROUTING_NOT_AVAILABLE_R4"
