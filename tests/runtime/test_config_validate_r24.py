from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import pytest

from syntavra_runtime.config_contract import resolve_config_wire
from syntavra_runtime.config_validate_contract import validation_result
from syntavra_runtime.engine_entry import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.live_config_discovery import discover_live_config_wire
from syntavra_runtime.read_only_router_r24 import ReadOnlyCommandRouterR24


def _verification_runner(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
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
                for name in RUST_CAPABILITIES
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


def _selector(project: Path, *, runner=_verification_runner) -> EngineSelector:
    binary = project / "syntavra-rs"
    binary.write_bytes(b"test")
    return EngineSelector(
        project_root=project,
        env={"HOME": str(project / "home")},
        rust_binary=binary,
        runner=runner,
    )


def _candidate_runner(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
    assert arguments[:2] == ("config", "resolve")
    return resolve_config_wire(bytes.fromhex(arguments[2]))


def _write_project_config(project: Path, text: str) -> None:
    path = project / ".syntavra" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_public_python_config_validate_is_state_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(project, '[runtime]\nprofile = "compact"\n')
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in tuple(os.environ):
        if name.startswith("SYNTAVRA_CFG__"):
            monkeypatch.delenv(name, raising=False)

    state = project / ".syntavra" / "pre-release"
    assert engine_main(
        [
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            "config",
            "validate",
        ]
    ) == 0

    wire = discover_live_config_wire(project_root=project, env=dict(os.environ))
    expected = validation_result(resolve_config_wire(wire))
    assert json.loads(capsys.readouterr().out) == expected
    assert not state.exists()
    assert not (project / ".syntavra" / "config-last-good.json").exists()


def test_auto_uses_rust_config_resolve_after_live_preflight(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(project, '[routing]\nbudget_bytes = 4096\n')
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        return _candidate_runner(binary, arguments)

    result = ReadOnlyCommandRouterR24(
        _selector(project),
        runner=runner,
        project_input_root=project,
        platform_probe=lambda: ("linux", "x86_64"),
    ).route("config.validate", cli_override="auto")

    assert result["phase"] == "R24"
    assert result["selection"]["resolved"] == "rust"
    assert result["capability"] == "config.resolve"
    assert result["input"]["profile"] == "live-config-discovery-v1"
    assert result["input"]["format"] == "R6CFG1"
    assert result["result"]["ok"] is True
    assert len(result["result"]["config_hash"]) == 64
    assert calls and calls[0][:2] == ("config", "resolve")
    assert not (project / ".syntavra" / "pre-release").exists()


def test_explicit_python_never_runs_rust(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, ...]] = []

    def runner(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        return {}

    result = ReadOnlyCommandRouterR24(
        _selector(project),
        runner=runner,
        project_input_root=project,
    ).route("config.validate", cli_override="python")

    assert result["result"]["ok"] is True
    assert calls == []


def test_invalid_live_config_fails_before_engine_verification(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(project, '[runtime]\nprofile = "invalid"\n')
    verification_calls: list[tuple[str, ...]] = []

    def verifier(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        verification_calls.append(arguments)
        return _verification_runner(binary, arguments)

    router = ReadOnlyCommandRouterR24(
        _selector(project, runner=verifier),
        project_input_root=project,
        platform_probe=lambda: ("linux", "x86_64"),
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("config.validate", cli_override="auto")

    assert caught.value.code == "ENGINE_ROUTE_LIVE_CONFIG_INVALID_R24"
    assert caught.value.details["fallback_attempted"] is False
    assert verification_calls == []
    assert not (project / ".syntavra" / "pre-release").exists()


def test_rust_snapshot_drift_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def drifting_runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        value = dict(_candidate_runner(binary, arguments))
        value["config_hash"] = "0" * 64
        return value

    router = ReadOnlyCommandRouterR24(
        _selector(project),
        runner=drifting_runner,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("config.validate", cli_override="rust")

    assert caught.value.code == "RUST_CONFIG_VALIDATE_SOURCE_PARITY_INVALID_R24"
    assert caught.value.details["fallback_attempted"] is False


def test_rust_failure_never_reexecutes_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, ...]] = []

    def failing_runner(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        raise RuntimeError("candidate failure")

    router = ReadOnlyCommandRouterR24(
        _selector(project),
        runner=failing_runner,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("config.validate", cli_override="rust")

    assert caught.value.code == "RUST_ROUTE_EXECUTION_FAILED_R24"
    assert caught.value.details["fallback_attempted"] is False
    assert len(calls) == 1
