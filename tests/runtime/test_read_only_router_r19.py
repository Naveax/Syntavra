from __future__ import annotations

import hashlib
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
from syntavra_runtime.read_only_router_r19 import (
    MAX_RECEIPT_WIRE_BYTES,
    ReadOnlyCommandRouterR19,
)
from syntavra_runtime.state_receipt_contract import inspect_receipt_wire
from syntavra_runtime.state_snapshot_contract import project_id_for_root


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
    if len(arguments) == 4 and arguments[:2] == ("receipt", "inspect"):
        return inspect_receipt_wire(
            bytes.fromhex(arguments[3]),
            expected_project_id=arguments[2],
        )
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


def _receipt_wire(
    project_id: str,
    *,
    operation: str = "state.inspect",
    receipt_id: str = "receipt-r19",
) -> bytes:
    lines = [
        "R7RCPT1",
        "schema_version=1",
        "product_version=0.0.1",
        "contract_version=1",
        "engine=python",
        f"operation_hex={operation.encode('utf-8').hex()}",
        "created_at_ms=1720000000000",
        f"project_id={project_id}",
        f"receipt_id_hex={receipt_id.encode('utf-8').hex()}",
        f"payload_hash={'1' * 64}",
        "previous_hash=-",
        "fallback_from=-",
        "fallback_to=-",
        "fallback_reason_hex=",
        "fallback_state_mutated=false",
    ]
    material = ("\n".join(lines) + "\n").encode("utf-8")
    receipt_hash = hashlib.sha256(material).hexdigest()
    return material + f"receipt_hash={receipt_hash}\n".encode("utf-8")


def test_receipt_inspect_has_exact_cross_engine_parity_without_state_access(
    tmp_path: Path,
) -> None:
    project_id = project_id_for_root(tmp_path)
    wire = _receipt_wire(project_id)
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR19(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    python_result = router.route(
        "receipt.inspect",
        cli_override="python",
        receipt_wire_hex=wire.hex(),
    )
    rust_result = router.route(
        "receipt.inspect",
        cli_override="rust",
        receipt_wire_hex=wire.hex(),
    )
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    assert python_result["result"] == rust_result["result"]
    assert rust_result["result"] == inspect_receipt_wire(
        wire,
        expected_project_id=project_id,
    )
    assert rust_result["phase"] == "R19"
    assert rust_result["schema_version"] == 8
    assert rust_result["command"] == "receipt.inspect"
    assert rust_result["capability"] == "receipt.inspect"
    assert rust_result["mutation"] == "read-only"
    assert rust_result["input"] == {
        "profile": "project-bound-receipt-wire-v1",
        "format": "R7RCPT1-lowercase-hex-v1",
        "bytes": len(wire),
        "sha256": hashlib.sha256(wire).hexdigest(),
    }
    assert rust_result["fallback"] == {"policy": "none", "attempted": False}
    assert calls == [("receipt", "inspect", project_id, wire.hex())]
    assert before == after
    assert not (tmp_path / ".syntavra").exists()
    rendered = json.dumps(rust_result, sort_keys=True)
    assert wire.hex() not in rendered
    assert str(tmp_path) not in rendered


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"config_wire_hex": "00", "receipt_wire_hex": "00"},
        {"live_config": True, "receipt_wire_hex": "00"},
        {"session_override_json_hex": "7b7d", "receipt_wire_hex": "00"},
        {"task_override_json_hex": "7b7d", "receipt_wire_hex": "00"},
    ],
)
def test_receipt_route_rejects_missing_or_conflicting_inputs_before_engine(
    tmp_path: Path,
    kwargs: dict[str, object],
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR19(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route("receipt.inspect", cli_override="rust", **kwargs)
    assert error.value.code in {
        "ENGINE_ROUTE_RECEIPT_INPUT_REQUIRED_R19",
        "ENGINE_ROUTE_RECEIPT_INPUT_CONFLICT_R19",
    }
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


@pytest.mark.parametrize(
    "encoded,error_code",
    [
        ("AA", "RECEIPT_ROUTE_HEX_NONCANONICAL"),
        ("0", "RECEIPT_ROUTE_HEX_NONCANONICAL"),
        ("zz", "RECEIPT_ROUTE_HEX_NONCANONICAL"),
        ("00" * (MAX_RECEIPT_WIRE_BYTES + 1), "RECEIPT_ROUTE_SIZE_LIMIT"),
    ],
)
def test_receipt_transport_fails_closed_and_redacts_raw_input(
    tmp_path: Path,
    encoded: str,
    error_code: str,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR19(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "receipt.inspect",
            cli_override="rust",
            receipt_wire_hex=encoded,
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "ENGINE_ROUTE_RECEIPT_PREFLIGHT_FAILED_R19"
    assert error.value.details["receipt_error"] == error_code
    if len(encoded) < 256:
        assert encoded not in rendered
    assert str(tmp_path) not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


def test_receipt_cross_project_replay_fails_before_engine_selection(
    tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    wire = _receipt_wire(project_id_for_root(other))
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR19(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "receipt.inspect",
            cli_override="rust",
            receipt_wire_hex=wire.hex(),
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "ENGINE_ROUTE_RECEIPT_PREFLIGHT_FAILED_R19"
    assert error.value.details["receipt_error"] == "RECEIPT_PROJECT_MISMATCH"
    assert wire.hex() not in rendered
    assert str(tmp_path) not in rendered
    assert calls == []


def test_receipt_project_root_symlink_fails_before_engine_selection(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "project-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    wire = _receipt_wire(project_id_for_root(target))
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR19(
        _selector(target),
        runner=runner,
        project_input_root=link,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "receipt.inspect",
            cli_override="rust",
            receipt_wire_hex=wire.hex(),
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "ENGINE_ROUTE_RECEIPT_PREFLIGHT_FAILED_R19"
    assert error.value.details["receipt_error"] == "STATE_PROJECT_ROOT_SYMLINK"
    assert str(link) not in rendered
    assert calls == []


def test_receipt_drift_uses_digest_only_diagnostics(tmp_path: Path) -> None:
    project_id = project_id_for_root(tmp_path)
    wire = _receipt_wire(project_id)
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        if len(arguments) == 4 and arguments[:2] == ("receipt", "inspect"):
            value = inspect_receipt_wire(
                bytes.fromhex(arguments[3]),
                expected_project_id=arguments[2],
            )
            value["receipt_id"] = "sensitive-drifted-receipt"
            return value
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR19(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "receipt.inspect",
            cli_override="rust",
            receipt_wire_hex=wire.hex(),
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "RUST_RECEIPT_ROUTE_PARITY_INVALID_R19"
    assert error.value.details["mismatched_keys"] == ["receipt_id"]
    assert len(error.value.details["expected_sha256"]) == 64
    assert len(error.value.details["actual_sha256"]) == 64
    assert "sensitive-drifted-receipt" not in rendered
    assert wire.hex() not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert len(calls) == 1


def test_receipt_execution_failure_never_falls_back_to_python(tmp_path: Path) -> None:
    project_id = project_id_for_root(tmp_path)
    wire = _receipt_wire(project_id)
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        if len(arguments) == 4 and arguments[:2] == ("receipt", "inspect"):
            raise RuntimeError("sensitive receipt failure")
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR19(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "receipt.inspect",
            cli_override="rust",
            receipt_wire_hex=wire.hex(),
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "RUST_ROUTE_EXECUTION_FAILED_R19"
    assert error.value.details["exception_message"] == "redacted"
    assert "sensitive receipt failure" not in rendered
    assert wire.hex() not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert len(calls) == 1


def test_engine_cli_routes_receipt_inspection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wire = _receipt_wire(project_id_for_root(tmp_path))
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouterR19(
        selector,
        runner=_rust_runner,
        project_input_root=tmp_path,
    )
    code = engine_main(
        [
            "--project",
            str(tmp_path),
            "engine",
            "route",
            "receipt.inspect",
            "--receipt-wire-hex",
            wire.hex(),
        ],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["phase"] == "R19"
    assert value["schema_version"] == 8
    assert value["command"] == "receipt.inspect"
    assert value["selection"]["resolved"] == "rust"
    assert value["result"]["project_binding"]["matched"] is True
    assert wire.hex() not in json.dumps(value, sort_keys=True)
