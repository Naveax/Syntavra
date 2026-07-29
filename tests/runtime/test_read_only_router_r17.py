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
from syntavra_runtime.read_only_router_r17 import ReadOnlyCommandRouterR17
from syntavra_runtime.state_receipt_contract import state_layout


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
    if arguments == ("state", "layout"):
        return state_layout()
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


def test_state_layout_has_exact_cross_engine_parity_without_state_access(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR17(_selector(tmp_path), runner=runner)
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    python_result = router.route("state.layout", cli_override="python")
    rust_result = router.route("state.layout", cli_override="rust")
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    assert python_result["result"] == rust_result["result"] == state_layout()
    assert python_result["phase"] == rust_result["phase"] == "R17"
    assert python_result["schema_version"] == rust_result["schema_version"] == 6
    assert python_result["input"] == {
        "profile": "none",
        "format": None,
        "bytes": 0,
        "sha256": None,
    }
    assert rust_result["command"] == "state.layout"
    assert rust_result["capability"] == "state.layout"
    assert rust_result["mutation"] == "read-only"
    assert rust_result["fallback"] == {"policy": "none", "attempted": False}
    assert calls == [("state", "layout")]
    assert before == after
    assert not (tmp_path / ".syntavra").exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"config_wire_hex": "00"},
        {"live_config": True},
        {"session_override_json_hex": "7b7d"},
        {"task_override_json_hex": "7b7d"},
    ],
)
def test_state_layout_rejects_all_input_before_engine_execution(
    tmp_path: Path,
    kwargs: dict[str, object],
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR17(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.layout", cli_override="rust", **kwargs)
    assert error.value.code == "ENGINE_ROUTE_STATE_LAYOUT_INPUT_UNSUPPORTED_R17"
    assert error.value.details["phase"] == "R17"
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


def test_state_layout_drift_fails_closed_with_digest_only_diagnostics(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        if arguments == ("state", "layout"):
            value = state_layout()
            value["layout_id"] = "drifted-layout"
            return value
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR17(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.layout", cli_override="rust")
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "RUST_STATE_LAYOUT_ROUTE_PARITY_INVALID_R17"
    assert error.value.details["mismatched_keys"] == ["layout_id"]
    assert len(error.value.details["expected_sha256"]) == 64
    assert len(error.value.details["actual_sha256"]) == 64
    assert "drifted-layout" not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert calls == [("state", "layout")]


def test_state_layout_execution_failure_never_falls_back_to_python(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        if arguments == ("state", "layout"):
            raise RuntimeError("sensitive candidate failure")
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR17(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.layout", cli_override="rust")
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "RUST_ROUTE_EXECUTION_FAILED_R17"
    assert error.value.details["exception_message"] == "redacted"
    assert "sensitive candidate failure" not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert calls == [("state", "layout")]


def test_engine_cli_routes_state_layout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouterR17(selector, runner=_rust_runner)
    code = engine_main(
        [
            "--project",
            str(tmp_path),
            "engine",
            "route",
            "state.layout",
        ],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["phase"] == "R17"
    assert value["schema_version"] == 6
    assert value["command"] == "state.layout"
    assert value["selection"]["resolved"] == "rust"
    assert value["result"] == state_layout()
