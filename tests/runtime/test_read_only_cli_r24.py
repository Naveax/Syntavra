from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.read_only_cli_contract import (
    pipeline_description,
    plugin_inventory,
    static_route_result,
)
from syntavra_runtime.read_only_router_r24 import ReadOnlyCommandRouterR24
from syntavra_runtime.unified_cli import main as unified_main


def verification_runner(_binary: Path, args: tuple[str, ...]) -> Mapping[str, Any]:
    if args == ("version",):
        return {"product":"Syntavra","product_version":"0.0.1","release_channel":"pre-release","engine":"rust","engine_stability":"experimental","contract_version":1}
    if args == ("engine", "capabilities"):
        return {"contract_version":1,"capabilities":[{"name":name,"maturity":"preview","mutation":"read-only"} for name in RUST_CAPABILITIES]}
    if args == ("engine", "contract-hash"):
        return {"engine":"rust","contract_version":1,"algorithm":"sha256","contract_hash":ENGINE_CONTRACT_SHA256}
    raise AssertionError(args)


def selector(root: Path) -> EngineSelector:
    binary = root / "syntavra-rs"
    binary.write_bytes(b"test")
    return EngineSelector(project_root=root, env={"HOME":str(root / "home")}, rust_binary=binary, runner=verification_runner)


def route_runner(_binary: Path, args: tuple[str, ...]) -> Mapping[str, Any]:
    if args == ("pipeline", "describe"):
        return pipeline_description()
    if args == ("plugins", "list"):
        return plugin_inventory()
    raise AssertionError(args)


@pytest.mark.parametrize("argv,expected", [
    (("pipeline", "describe"), pipeline_description()),
    (("plugins", "list"), plugin_inventory()),
])
def test_python_commands_are_state_free(tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...], expected: dict[str, Any]) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / ".syntavra" / "pre-release"
    assert unified_main(["--project", str(project), "--state-root", str(state), *argv]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert not state.exists()
    assert list(project.iterdir()) == []


@pytest.mark.parametrize("route,args", [
    ("pipeline.describe", ("pipeline", "describe")),
    ("plugins.list", ("plugins", "list")),
])
def test_auto_uses_verified_rust(tmp_path: Path, route: str, args: tuple[str, ...]) -> None:
    calls: list[tuple[str, ...]] = []
    def runner(binary: Path, actual: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(actual)
        return route_runner(binary, actual)
    router = ReadOnlyCommandRouterR24(selector(tmp_path), runner=runner, project_input_root=tmp_path, platform_probe=lambda:("linux","x86_64"))
    result = router.route(route, cli_override="auto")
    assert result["phase"] == "R24"
    assert result["schema_version"] == 12
    assert result["selection"]["resolved"] == "rust"
    assert result["result"] == static_route_result(route)
    assert calls == [args]


def test_explicit_python_does_not_run_rust(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    def runner(_binary: Path, args: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(args)
        return {}
    result = ReadOnlyCommandRouterR24(selector(tmp_path), runner=runner, project_input_root=tmp_path).route("plugins.list", cli_override="python")
    assert result["result"] == plugin_inventory()
    assert calls == []


def test_rust_result_drift_is_rejected(tmp_path: Path) -> None:
    def runner(binary: Path, args: tuple[str, ...]) -> Mapping[str, Any]:
        value = dict(route_runner(binary, args))
        value["extra"] = True
        return value
    router = ReadOnlyCommandRouterR24(selector(tmp_path), runner=runner, project_input_root=tmp_path)
    with pytest.raises(EngineSelectionError) as caught:
        router.route("pipeline.describe", cli_override="rust")
    assert caught.value.code == "RUST_STATIC_CLI_ROUTE_PARITY_INVALID_R24"
    assert caught.value.details["fallback_attempted"] is False


def test_rust_error_never_reexecutes_python(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    def runner(_binary: Path, args: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(args)
        raise RuntimeError("candidate-error")
    router = ReadOnlyCommandRouterR24(selector(tmp_path), runner=runner, project_input_root=tmp_path)
    with pytest.raises(EngineSelectionError) as caught:
        router.route("plugins.list", cli_override="rust")
    assert caught.value.code == "RUST_ROUTE_EXECUTION_FAILED_R24"
    assert caught.value.details["fallback_attempted"] is False
    assert calls == [("plugins", "list")]
