from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import pytest

from syntavra_runtime.config_contract import resolve_config_wire
from syntavra_runtime.config_explain_contract import explain_result
from syntavra_runtime.config_explain_router_r24 import ConfigExplainRouterR24
from syntavra_runtime.engine_entry import main as engine_main
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
    assert arguments[:2] == ("config", "explain")
    wire = bytes.fromhex(arguments[2])
    path = bytes.fromhex(arguments[3]).decode("utf-8")
    return explain_result(resolve_config_wire(wire), path)


def _write_project_config(project: Path, text: str) -> None:
    path = project / ".syntavra" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_public_python_config_explain_is_state_free(
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
            "explain",
            "runtime.profile",
        ]
    ) == 0

    assert json.loads(capsys.readouterr().out) == {
        "path": "runtime.profile",
        "value": "compact",
        "source": "project-config",
        "scope": "project",
    }
    assert not state.exists()
    assert not (project / ".syntavra" / "config-last-good.json").exists()


def test_missing_path_preserves_legacy_result_shape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    result = ConfigExplainRouterR24(
        _selector(project),
        project_input_root=project,
    ).route(
        "config.explain",
        cli_override="python",
        explain_path="missing.value",
    )
    assert result["result"] == {"found": False, "path": "missing.value"}


def test_auto_runs_native_rust_config_explain(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(project, '[routing]\nbudget_bytes = 4096\n')
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        return _candidate_runner(binary, arguments)

    result = ConfigExplainRouterR24(
        _selector(project),
        runner=runner,
        project_input_root=project,
        platform_probe=lambda: ("linux", "x86_64"),
    ).route(
        "config.explain",
        cli_override="auto",
        explain_path="routing.budget_bytes",
    )

    assert result["selection"]["resolved"] == "rust"
    assert result["capability"] == "config.explain"
    assert result["result"] == {
        "path": "routing.budget_bytes",
        "value": 4096,
        "source": "project-config",
        "scope": "project",
    }
    assert calls and calls[0][:2] == ("config", "explain")
    assert bytes.fromhex(calls[0][3]).decode("utf-8") == "routing.budget_bytes"
    assert not (project / ".syntavra" / "pre-release").exists()


def test_environment_credential_reference_is_redacted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    selector = _selector(project)
    selector.env["SYNTAVRA_CFG__PROVIDER__CREDENTIAL_REF"] = "secret://provider/key"
    result = ConfigExplainRouterR24(
        selector,
        project_input_root=project,
    ).route(
        "config.explain",
        cli_override="python",
        explain_path="provider.credential_ref",
    )
    assert result["result"]["value"] == "[secret-ref]"
    assert result["result"]["source"] == "SYNTAVRA_CFG__PROVIDER__CREDENTIAL_REF"


@pytest.mark.parametrize(
    "path",
    ["", ".runtime.profile", "runtime..profile", "runtime.profile.", "a\nvalue", "x" * 513],
)
def test_invalid_path_fails_before_rust_verification(tmp_path: Path, path: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    verification_calls: list[tuple[str, ...]] = []

    def verifier(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        verification_calls.append(arguments)
        return _verification_runner(binary, arguments)

    router = ConfigExplainRouterR24(
        _selector(project, runner=verifier),
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("config.explain", cli_override="auto", explain_path=path)

    assert caught.value.code == "ENGINE_ROUTE_CONFIG_EXPLAIN_PATH_INVALID_R24"
    assert verification_calls == []


def test_rust_result_drift_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def drifting_runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        value = dict(_candidate_runner(binary, arguments))
        value["scope"] = "wrong"
        return value

    router = ConfigExplainRouterR24(
        _selector(project),
        runner=drifting_runner,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route(
            "config.explain",
            cli_override="rust",
            explain_path="runtime.profile",
        )

    assert caught.value.code == "RUST_CONFIG_EXPLAIN_PARITY_INVALID_R24"
    assert caught.value.details["fallback_attempted"] is False


def test_rust_failure_never_reexecutes_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, ...]] = []

    def failing_runner(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        raise RuntimeError("candidate failure")

    router = ConfigExplainRouterR24(
        _selector(project),
        runner=failing_runner,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route(
            "config.explain",
            cli_override="rust",
            explain_path="runtime.profile",
        )

    assert caught.value.code == "RUST_ROUTE_EXECUTION_FAILED_R24"
    assert caught.value.details["fallback_attempted"] is False
    assert len(calls) == 1
