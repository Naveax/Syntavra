from __future__ import annotations

import json
from pathlib import Path

import pytest

from syntavra_runtime.engine_cli import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.read_only_router import ReadOnlyCommandRouter


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
    assert result["phase"] == "R11"
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


def test_unsupported_route_fails_closed_for_both_engines(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner)
    for engine in ("python", "rust"):
        with pytest.raises(EngineSelectionError) as error:
            router.route("status", cli_override=engine)
        assert error.value.code == "ENGINE_ROUTE_UNSUPPORTED_R11"
        assert error.value.details["supported"] == ["version"]
        assert error.value.details["fallback_attempted"] is False
        assert error.value.details["fallback_policy"] == "none"


def test_invalid_rust_result_never_reexecutes_in_python(tmp_path: Path) -> None:
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


def test_rust_execution_failure_never_reexecutes_in_python(tmp_path: Path) -> None:
    selector = _selector(tmp_path)
    calls: list[tuple[str, ...]] = []

    def failing_runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        raise TimeoutError("bounded route timeout")

    router = ReadOnlyCommandRouter(selector, runner=failing_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("version", cli_override="rust")
    assert error.value.code == "RUST_ROUTE_EXECUTION_FAILED_R11"
    assert error.value.details["exception"] == "TimeoutError"
    assert error.value.details["exception_message"] == "bounded route timeout"
    assert error.value.details["fallback_policy"] == "none"
    assert error.value.details["fallback_attempted"] is False
    assert calls == [("version",)]


def test_engine_cli_emits_structured_r11_route_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouter(selector, runner=_rust_runner)
    code = engine_main(
        ["--project", str(tmp_path), "engine", "route", "version"],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["phase"] == "R11"
    assert value["selection"]["resolved"] == "rust"
    assert value["result"]["engine"] == "rust"


def test_engine_cli_emits_structured_unsupported_error(
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
    assert code == 4
    value = json.loads(capsys.readouterr().out)
    assert value["ok"] is False
    assert value["error"]["code"] == "ENGINE_ROUTE_UNSUPPORTED_R11"
    assert value["error"]["details"]["fallback_attempted"] is False
