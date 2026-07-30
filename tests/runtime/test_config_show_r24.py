from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import pytest

from syntavra_runtime.config_contract import resolve_config_wire
from syntavra_runtime.config_show_contract import show_result
from syntavra_runtime.config_show_router_r24 import ConfigShowRouterR24
from syntavra_runtime.engine_entry import main as engine_main
from syntavra_runtime.unified_cli import main as unified_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)


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
    assert arguments[:2] == ("config", "show")
    return show_result(resolve_config_wire(bytes.fromhex(arguments[2])))


def _write_project_config(project: Path, text: str) -> None:
    path = project / ".syntavra" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_public_python_config_show_is_deterministic_and_state_free(
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
            "show",
        ]
    ) == 0
    first = json.loads(capsys.readouterr().out)
    assert engine_main(
        [
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            "config",
            "show",
        ]
    ) == 0
    second = json.loads(capsys.readouterr().out)

    assert first == second
    assert set(first) == {
        "schema_version",
        "values",
        "provenance",
        "config_hash",
        "warnings",
    }
    assert "loaded_at" not in first
    assert first["values"]["runtime"]["profile"] == "compact"
    assert not state.exists()
    assert not (project / ".syntavra" / "config-last-good.json").exists()


def test_direct_python_core_config_show_is_state_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(project, '[runtime]\nprofile = "audit"\n')
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in tuple(os.environ):
        if name.startswith("SYNTAVRA_CFG__"):
            monkeypatch.delenv(name, raising=False)

    state = project / ".syntavra" / "pre-release"
    assert unified_main(
        [
            "--project",
            str(project),
            "--state-root",
            str(state),
            "config",
            "show",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["runtime"]["profile"] == "audit"
    assert "loaded_at" not in result
    assert not state.exists()
    assert not (project / ".syntavra" / "config-last-good.json").exists()


def test_auto_runs_native_rust_config_show(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(project, '[routing]\nbudget_bytes = 4096\n')
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        return _candidate_runner(binary, arguments)

    result = ConfigShowRouterR24(
        _selector(project),
        runner=runner,
        project_input_root=project,
        platform_probe=lambda: ("linux", "x86_64"),
    ).route("config.show", cli_override="auto")

    assert result["selection"]["resolved"] == "rust"
    assert result["capability"] == "config.show"
    assert result["result"]["values"]["routing"]["budget_bytes"] == 4096
    assert "loaded_at" not in result["result"]
    assert calls and calls[0][:2] == ("config", "show")


def test_invalid_live_config_fails_before_engine_verification(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(project, '[runtime]\nprofile = "invalid"\n')
    verification_calls: list[tuple[str, ...]] = []

    def verifier(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        verification_calls.append(arguments)
        return _verification_runner(binary, arguments)

    router = ConfigShowRouterR24(
        _selector(project, runner=verifier),
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("config.show", cli_override="auto")

    assert caught.value.code == "ENGINE_ROUTE_LIVE_CONFIG_INVALID_R24"
    assert verification_calls == []


def test_rust_show_drift_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def drifting_runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        value = dict(_candidate_runner(binary, arguments))
        value["loaded_at"] = 1.0
        return value

    router = ConfigShowRouterR24(
        _selector(project),
        runner=drifting_runner,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("config.show", cli_override="rust")

    assert caught.value.code == "RUST_CONFIG_SHOW_PARITY_INVALID_R24"
    assert caught.value.details["fallback_attempted"] is False


def test_rust_failure_never_reexecutes_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, ...]] = []

    def failing_runner(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        raise RuntimeError("candidate failure")

    router = ConfigShowRouterR24(
        _selector(project),
        runner=failing_runner,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("config.show", cli_override="rust")

    assert caught.value.code == "RUST_ROUTE_EXECUTION_FAILED_R24"
    assert caught.value.details["fallback_attempted"] is False
    assert len(calls) == 1
