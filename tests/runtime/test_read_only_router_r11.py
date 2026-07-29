from __future__ import annotations

import json
from pathlib import Path

import pytest

from syntavra_runtime.config_contract import (
    encode_config_wire,
    resolve_config_phases,
    resolve_config_wire,
    status_projection,
)
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


def _explicit_phases() -> list[dict[str, dict[str, object]]]:
    return [
        {
            "project": {
                "runtime": {"profile": "compact"},
                "routing": {"budget_bytes": 4096},
            },
            "environment": {
                "provider.timeout_seconds": 90.0,
            },
        }
    ]


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
    if len(arguments) == 2 and arguments[0] == "status":
        raw = bytes.fromhex(arguments[1])
        return status_projection(resolve_config_wire(raw))
    if len(arguments) == 3 and arguments[:2] == ("config", "resolve"):
        raw = bytes.fromhex(arguments[2])
        return resolve_config_wire(raw)
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


def test_supported_r14_routes_include_config_resolve(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner)
    assert router.supported_commands() == ("config.resolve", "status", "version")


def test_python_version_route_uses_v3_success_envelope(tmp_path: Path) -> None:
    result = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner).route(
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
        "input",
        "fallback",
        "result",
    }
    assert result["phase"] == "R14"
    assert result["schema_version"] == 3
    assert result["input"] == {
        "profile": "none",
        "format": None,
        "bytes": 0,
        "sha256": None,
    }
    assert result["fallback"] == {"policy": "none", "attempted": False}


def test_default_status_route_has_exact_cross_engine_parity(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner)
    python_result = router.route("status", cli_override="python")
    rust_result = router.route("status", cli_override="rust")
    assert python_result["result"] == rust_result["result"] == _default_status()
    assert python_result["input"] == rust_result["input"]
    assert python_result["input"]["profile"] == "default-config-only"
    assert python_result["input"]["format"] == "R6CFG1"
    assert python_result["input"]["bytes"] > 0
    assert len(python_result["input"]["sha256"]) == 64


def test_explicit_status_wire_has_exact_cross_engine_parity(tmp_path: Path) -> None:
    wire = encode_config_wire(_explicit_phases())
    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner)
    python_result = router.route(
        "status",
        cli_override="python",
        config_wire_hex=wire.hex(),
    )
    rust_result = router.route(
        "status",
        cli_override="rust",
        config_wire_hex=wire.hex(),
    )
    expected = status_projection(resolve_config_wire(wire))
    assert python_result["result"] == rust_result["result"] == expected
    assert python_result["input"] == rust_result["input"]
    assert python_result["input"]["profile"] == "explicit-config-wire-v1"
    assert python_result["input"]["bytes"] == len(wire)
    assert wire.hex() not in json.dumps(rust_result)


def test_explicit_config_resolve_has_exact_cross_engine_parity(tmp_path: Path) -> None:
    wire = encode_config_wire(_explicit_phases())
    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner)
    python_result = router.route(
        "config.resolve",
        cli_override="python",
        config_wire_hex=wire.hex(),
    )
    rust_result = router.route(
        "config.resolve",
        cli_override="rust",
        config_wire_hex=wire.hex(),
    )
    expected = resolve_config_wire(wire)
    assert python_result["result"] == rust_result["result"] == expected
    assert python_result["input"] == rust_result["input"]
    assert python_result["command"] == "config.resolve"
    assert python_result["capability"] == "config.resolve"
    assert python_result["mutation"] == "read-only"
    assert wire.hex() not in json.dumps(rust_result)


def test_config_resolve_requires_input_before_engine_execution(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(_binary, arguments)

    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("config.resolve", cli_override="rust")
    assert error.value.code == "ENGINE_ROUTE_INPUT_REQUIRED_R14"
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


def test_version_rejects_config_input_before_engine_execution(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(_binary, arguments)

    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("version", cli_override="rust", config_wire_hex="00")
    assert error.value.code == "ENGINE_ROUTE_INPUT_UNSUPPORTED_R14"
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


@pytest.mark.parametrize("command", ["status", "config.resolve"])
def test_invalid_config_wire_fails_before_engine_execution(
    tmp_path: Path,
    command: str,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(_binary, arguments)

    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route(command, cli_override="rust", config_wire_hex="abc")
    assert error.value.code == "ENGINE_ROUTE_INPUT_INVALID_R14"
    assert error.value.details["provided_hex_characters"] == 3
    assert "abc" not in json.dumps(error.value.to_dict())
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


def test_unsupported_route_fails_closed_for_both_engines(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=_rust_runner)
    for engine in ("python", "rust"):
        with pytest.raises(EngineSelectionError) as error:
            router.route("state.layout", cli_override=engine)
        assert error.value.code == "ENGINE_ROUTE_UNSUPPORTED_R14"
        assert error.value.details["supported"] == [
            "config.resolve",
            "status",
            "version",
        ]
        assert error.value.details["fallback_attempted"] is False


def test_rust_status_drift_fails_closed_without_python_reexecution(tmp_path: Path) -> None:
    wire = encode_config_wire(_explicit_phases())
    calls: list[tuple[str, ...]] = []

    def drift_runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        result = status_projection(resolve_config_wire(wire))
        result["config_hash"] = "0" * 64
        return result

    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=drift_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "status",
            cli_override="rust",
            config_wire_hex=wire.hex(),
        )
    assert error.value.code == "RUST_STATUS_ROUTE_PARITY_INVALID"
    assert error.value.details["input_profile"] == "explicit-config-wire-v1"
    assert error.value.details["mismatched_keys"] == ["config_hash"]
    assert error.value.details["fallback_attempted"] is False
    assert calls == [("status", wire.hex())]


def test_rust_config_resolve_drift_is_digest_only_and_has_no_fallback(
    tmp_path: Path,
) -> None:
    wire = encode_config_wire(_explicit_phases())
    calls: list[tuple[str, ...]] = []

    def drift_runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        result = resolve_config_wire(wire)
        result["values"]["runtime"]["profile"] = "detailed"
        return result

    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=drift_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "config.resolve",
            cli_override="rust",
            config_wire_hex=wire.hex(),
        )
    value = error.value.to_dict()
    assert error.value.code == "RUST_CONFIG_RESOLVE_ROUTE_PARITY_INVALID"
    assert error.value.details["mismatched_keys"] == ["values"]
    assert "expected_sha256" in error.value.details
    assert "actual_sha256" in error.value.details
    assert "compact" not in json.dumps(value)
    assert wire.hex() not in json.dumps(value)
    assert error.value.details["fallback_attempted"] is False
    assert calls == [("config", "resolve", wire.hex())]


@pytest.mark.parametrize(
    ("command", "wire", "expected_arguments"),
    [
        ("status", None, ("status",)),
        ("version", None, ("version",)),
        (
            "config.resolve",
            encode_config_wire(_explicit_phases()),
            None,
        ),
    ],
)
def test_rust_execution_failure_never_reexecutes_in_python(
    tmp_path: Path,
    command: str,
    wire: bytes | None,
    expected_arguments: tuple[str, ...] | None,
) -> None:
    calls: list[tuple[str, ...]] = []

    def failing_runner(_binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        raise TimeoutError("bounded route timeout")

    router = ReadOnlyCommandRouter(_selector(tmp_path), runner=failing_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            command,
            cli_override="rust",
            config_wire_hex=wire.hex() if wire is not None else None,
        )
    assert error.value.code == "RUST_ROUTE_EXECUTION_FAILED_R14"
    assert error.value.details["command"] == command
    assert error.value.details["exception_message"] == "redacted"
    assert error.value.details["fallback_policy"] == "none"
    assert error.value.details["fallback_attempted"] is False
    if expected_arguments is None:
        assert wire is not None
        expected_arguments = ("config", "resolve", wire.hex())
    assert calls == [expected_arguments]


def test_engine_cli_routes_explicit_config_resolve(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wire = encode_config_wire(_explicit_phases())
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouter(selector, runner=_rust_runner)
    code = engine_main(
        [
            "--project",
            str(tmp_path),
            "engine",
            "route",
            "config.resolve",
            "--config-wire-hex",
            wire.hex(),
        ],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["phase"] == "R14"
    assert value["schema_version"] == 3
    assert value["selection"]["resolved"] == "rust"
    assert value["input"]["profile"] == "explicit-config-wire-v1"
    assert value["result"] == resolve_config_wire(wire)
