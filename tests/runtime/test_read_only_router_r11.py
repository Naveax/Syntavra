from __future__ import annotations

import json
from pathlib import Path

import pytest

from syntavra_runtime.config_contract import resolve_config_phases, status_projection
from syntavra_runtime.engine_cli import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.read_only_router import ReadOnlyCommandRouter


def _default_status() -> dict[str, object]:
    return status_projection(resolve_config_phases([{}]))


def _capability_rows() -> list[dict[str, str]]:
    return [
        {"name": name, "maturity": "preview", "mutation": "read-only"}
        for name in RUST_CAPABILITIES
    ]


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
    if arguments == ("status",):
        return _default_status()
    if arguments == ("engine", "capabilities"):
        return {"contract_version": 1, "capabilities": _capability_rows()}
    if arguments == ("engine", "contract-hash"):
        return {
            "engine": "rust",
            "contract_version": 1,
            "algorithm": "sha256",
            "contract_hash": ENGINE_CONTRACT_SHA256,
        }
    raise AssertionError(arguments)


def _selector(tmp_path: Path) -> EngineSelector:
    binary = tmp_path / "syntavra-rs"
    binary.write_bytes(b"test")
    return EngineSelector(
        project_root=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        rust_binary=binary,
        runner=_rust_runner,
    )


def test_supported_r12_routes_are_status_and_version(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner)
    assert router.supported_commands() == ("status", "version")


def test_python_version_route_uses_exact_success_envelope(tmp_path: Path) -> None:
    selector = _selector(tmp_path)
    result = ReadOnlyCommandRouter(selector, runner=_rust_runner).route(
        "version",
        cli_override="python",
    )
    assert set(result) == {
        "ok",
        "phase",
        "schema_version",
        "command",
        "capability",
        "mutation",
        "selection",
        "fallback",
        "result",
    }
    assert result["ok"] is True
    assert result["phase"] == "R12"
    assert result["fallback"] == {"policy": "none", "attempted": False}
    assert result["result"] == {
        "product": "Syntavra",
        "product_version": "0.0.1",
        "release_channel": "pre-release",
        "engine": "python",
        "engine_stability": "reference",
        "contract_version": 1,
    }


def test_rust_version_route_executes_verified_binary_without_fallback(tmp_path: Path) -> None:
    selector = _selector(tmp_path)
    result = ReadOnlyCommandRouter(selector, runner=_rust_runner).route(
        "version",
        cli_override="rust",
    )
    assert result["selection"]["resolved"] == "rust"
    assert result["fallback"] == {"policy": "none", "attempted": False}
    assert result["result"]["engine"] == "rust"
    assert result["result"]["engine_stability"] == "experimental"


@pytest.mark.parametrize("engine", ["python", "rust"])
def test_default_status_route_has_exact_cross_engine_parity(
    tmp_path: Path,
    engine: str,
) -> None:
    selector = _selector(tmp_path)
    result = ReadOnlyCommandRouter(selector, runner=_rust_runner).route(
        "status",
        cli_override=engine,
    )
    assert result["phase"] == "R12"
    assert result["command"] == "status"
    assert result["capability"] == "status"
    assert result["mutation"] == "read-only"
    assert result["selection"]["resolved"] == engine
    assert result["fallback"] == {"policy": "none", "attempted": False}
    assert result["result"] == _default_status()
    assert result["result"]["general_command_routing"] == "blocked"


def test_unsupported_route_fails_closed_for_both_engines(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner)
    for engine in ("python", "rust"):
        with pytest.raises(EngineSelectionError) as error:
            router.route("config.resolve", cli_override=engine)
        assert error.value.code == "ENGINE_ROUTE_UNSUPPORTED_R12"
        assert error.value.details["supported"] == ["status", "version"]
        assert error.value.details["fallback_attempted"] is False
        assert error.value.details["fallback_policy"] == "none"


def test_invalid_rust_version_result_never_reexecutes_in_python(tmp_path: Path) -> None:
    selector = _selector(tmp_path)
    calls: list[tuple[str, ...]] = []

    def invalid_runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return {"product": "Syntavra"}

    router = ReadOnlyCommandRouter(selector, runner=invalid_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("version", cli_override="rust")
    assert error.value.code == "RUST_ROUTE_RESULT_SCHEMA_INVALID"
    assert error.value.details["fallback_attempted"] is False
    assert calls == [("version",)]


def test_rust_status_drift_fails_closed_without_python_reexecution(tmp_path: Path) -> None:
    selector = _selector(tmp_path)
    calls: list[tuple[str, ...]] = []

    def drift_runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        result = _default_status()
        result["config_hash"] = "0" * 64
        return result

    router = ReadOnlyCommandRouter(selector, runner=drift_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("status", cli_override="rust")
    assert error.value.code == "RUST_STATUS_ROUTE_PARITY_INVALID"
    assert error.value.details["input_profile"] == "default-config-only"
    assert error.value.details["fallback_attempted"] is False
    assert calls == [("status",)]


@pytest.mark.parametrize("command", ["status", "version"])
def test_rust_execution_failure_never_reexecutes_in_python(
    tmp_path: Path,
    command: str,
) -> None:
    selector = _selector(tmp_path)
    calls: list[tuple[str, ...]] = []

    def failing_runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        raise TimeoutError("bounded route timeout")

    router = ReadOnlyCommandRouter(selector, runner=failing_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route(command, cli_override="rust")
    assert error.value.code == "RUST_ROUTE_EXECUTION_FAILED_R12"
    assert error.value.details["command"] == command
    assert error.value.details["exception"] == "TimeoutError"
    assert error.value.details["exception_message"] == "bounded route timeout"
    assert error.value.details["fallback_policy"] == "none"
    assert error.value.details["fallback_attempted"] is False
    assert calls == [(command,)]


def test_engine_cli_emits_structured_r12_status_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouter(selector, runner=_rust_runner)
    code = engine_main(
        ["--project", str(tmp_path), "engine", "route", "status"],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["phase"] == "R12"
    assert value["selection"]["resolved"] == "rust"
    assert value["command"] == "status"
    assert value["result"] == _default_status()


def test_engine_cli_emits_structured_unsupported_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouter(selector, runner=_rust_runner)
    code = engine_main(
        ["--project", str(tmp_path), "engine", "route", "config.resolve"],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 4
    value = json.loads(capsys.readouterr().out)
    assert value["ok"] is False
    assert value["error"]["code"] == "ENGINE_ROUTE_UNSUPPORTED_R12"
    assert value["error"]["details"]["fallback_attempted"] is False
