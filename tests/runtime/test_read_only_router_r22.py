from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from syntavra_runtime.engine_cli import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.read_only_router_r22 import (
    AUTO_POLICY,
    AUTO_ROUTE_CAPABILITIES,
    ReadOnlyCommandRouterR22,
)


def _capability_rows() -> list[dict[str, str]]:
    return [
        {"name": name, "maturity": "preview", "mutation": "read-only"}
        for name in RUST_CAPABILITIES
    ]


def _verification_runner(
    _binary: Path,
    arguments: tuple[str, ...],
) -> Mapping[str, Any]:
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


def _route_runner(
    _binary: Path,
    arguments: tuple[str, ...],
) -> Mapping[str, Any]:
    if arguments == ("version",):
        return _verification_runner(_binary, arguments)
    raise AssertionError(arguments)


def _selector(
    project: Path,
    *,
    runner=_verification_runner,
    environment: Mapping[str, str] | None = None,
) -> EngineSelector:
    binary = project / "syntavra-rs"
    binary.write_bytes(b"test")
    return EngineSelector(
        project_root=project,
        env=dict(environment or {"HOME": str(project / "home")}),
        rust_binary=binary,
        runner=runner,
    )


def _router(
    project: Path,
    *,
    selector: EngineSelector | None = None,
    runner=_route_runner,
    platform_pair: tuple[str, str] = ("linux", "x86_64"),
) -> ReadOnlyCommandRouterR22:
    return ReadOnlyCommandRouterR22(
        selector or _selector(project),
        runner=runner,
        project_input_root=project,
        platform_probe=lambda: platform_pair,
    )


def test_r22_route_inventory_is_exactly_the_proven_read_only_surface() -> None:
    assert ReadOnlyCommandRouterR22.supported_commands() == tuple(
        sorted(AUTO_ROUTE_CAPABILITIES)
    )
    assert list(sorted(AUTO_ROUTE_CAPABILITIES)) == [
        "config.resolve",
        "receipt.inspect",
        "state.broker-live-snapshot",
        "state.broker-snapshot",
        "state.inspect",
        "state.layout",
        "status",
        "version",
    ]


def test_auto_selects_rust_for_verified_supported_route(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        return _route_runner(binary, arguments)

    result = _router(tmp_path, runner=runner).route(
        "version",
        cli_override="auto",
    )

    assert result["phase"] == "R22"
    assert result["schema_version"] == 11
    assert result["selection"]["requested"] == "auto"
    assert result["selection"]["resolved"] == "rust"
    assert result["selection"]["reason"] == "AUTO_ROUTE_RUST_SELECTED_R22"
    assert result["selection"]["auto_policy"] == AUTO_POLICY
    assert result["selection"]["fallback_policy"] == "no-fallback-after-selection"
    assert result["result"]["engine"] == "rust"
    assert calls == [("version",)]


def test_auto_selects_python_before_candidate_on_unsupported_platform(
    tmp_path: Path,
) -> None:
    verification_calls: list[tuple[str, ...]] = []
    candidate_calls: list[tuple[str, ...]] = []

    def verification_runner(
        binary: Path,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        verification_calls.append(arguments)
        return _verification_runner(binary, arguments)

    def candidate_runner(
        binary: Path,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        candidate_calls.append(arguments)
        return _route_runner(binary, arguments)

    result = _router(
        tmp_path,
        selector=_selector(tmp_path, runner=verification_runner),
        runner=candidate_runner,
        platform_pair=("freebsd", "x86_64"),
    ).route("version", cli_override="auto")

    assert result["selection"]["requested"] == "auto"
    assert result["selection"]["resolved"] == "python"
    assert result["selection"]["reason"] == "AUTO_ROUTE_PLATFORM_UNSUPPORTED_R22"
    assert result["result"]["engine"] == "python"
    assert verification_calls == []
    assert candidate_calls == []


def test_auto_selects_python_before_candidate_on_contract_drift(
    tmp_path: Path,
) -> None:
    candidate_calls: list[tuple[str, ...]] = []

    def incompatible_runner(
        binary: Path,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        value = dict(_verification_runner(binary, arguments))
        if arguments == ("engine", "contract-hash"):
            value["contract_hash"] = "0" * 64
        return value

    def candidate_runner(
        binary: Path,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        candidate_calls.append(arguments)
        return _route_runner(binary, arguments)

    result = _router(
        tmp_path,
        selector=_selector(tmp_path, runner=incompatible_runner),
        runner=candidate_runner,
    ).route("version", cli_override="auto")

    assert result["selection"]["resolved"] == "python"
    assert result["selection"]["reason"] == "AUTO_ROUTE_CONTRACT_INCOMPATIBLE_R22"
    assert result["result"]["engine"] == "python"
    assert candidate_calls == []


def test_explicit_rust_contract_failure_never_selects_python(tmp_path: Path) -> None:
    def incompatible_runner(
        binary: Path,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        value = dict(_verification_runner(binary, arguments))
        if arguments == ("engine", "contract-hash"):
            value["contract_hash"] = "0" * 64
        return value

    router = _router(
        tmp_path,
        selector=_selector(tmp_path, runner=incompatible_runner),
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("version", cli_override="rust")

    assert caught.value.code == "RUST_ENGINE_UNAVAILABLE_R14"
    assert caught.value.details["phase"] == "R22"
    assert caught.value.details["fallback_attempted"] is False


def test_auto_candidate_execution_failure_never_reexecutes_python(
    tmp_path: Path,
) -> None:
    candidate_calls: list[tuple[str, ...]] = []

    def failing_runner(
        _binary: Path,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        candidate_calls.append(arguments)
        raise RuntimeError("sensitive candidate failure")

    router = _router(tmp_path, runner=failing_runner)
    with pytest.raises(EngineSelectionError) as caught:
        router.route("version", cli_override="auto")

    rendered = json.dumps(caught.value.to_dict(), sort_keys=True)
    assert caught.value.code == "RUST_ROUTE_EXECUTION_FAILED_R14"
    assert caught.value.details["phase"] == "R22"
    assert caught.value.details["fallback_attempted"] is False
    assert "sensitive candidate failure" not in rendered
    assert candidate_calls == [("version",)]


def test_invalid_route_input_fails_before_auto_verification(tmp_path: Path) -> None:
    verification_calls: list[tuple[str, ...]] = []

    def verification_runner(
        binary: Path,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        verification_calls.append(arguments)
        return _verification_runner(binary, arguments)

    router = _router(
        tmp_path,
        selector=_selector(tmp_path, runner=verification_runner),
    )
    with pytest.raises(EngineSelectionError) as caught:
        router.route("state.broker-snapshot", cli_override="auto")

    assert caught.value.code == "ENGINE_ROUTE_BROKER_DATABASE_INPUT_REQUIRED_R20"
    assert caught.value.details["phase"] == "R22"
    assert verification_calls == []


def test_project_auto_preference_uses_route_scoped_policy(tmp_path: Path) -> None:
    config = tmp_path / ".syntavra" / "engine.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"schema_version": 1, "engine": "auto"}),
        encoding="utf-8",
    )
    result = _router(tmp_path).route("version")
    assert result["selection"]["source"] == str(config)
    assert result["selection"]["scope"] == "project"
    assert result["selection"]["requested"] == "auto"
    assert result["selection"]["resolved"] == "rust"


def test_cli_routes_auto_through_r22(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selector = _selector(tmp_path)
    router = _router(tmp_path, selector=selector)
    code = engine_main(
        ["--project", str(tmp_path), "engine", "route", "version"],
        selector=selector,
        cli_override="auto",
        router=router,
    )
    value = json.loads(capsys.readouterr().out)
    assert code == 0
    assert value["phase"] == "R22"
    assert value["schema_version"] == 11
    assert value["selection"]["requested"] == "auto"
    assert value["selection"]["resolved"] == "rust"


def test_r22_contract_locks_route_scoped_auto_policy() -> None:
    root = Path(__file__).resolve().parents[2]
    contract = json.loads(
        (root / "contracts" / "engine" / "read-only-routing-v11.json").read_text(
            encoding="utf-8"
        )
    )
    policy = contract["auto_selection"]
    assert contract["schema_version"] == 11
    assert contract["phase"] == "R22"
    assert contract["default_engine"] == "python"
    assert contract["auto_engine"] == "route-scoped-capability-aware"
    assert policy["eligible_commands"] == list(sorted(AUTO_ROUTE_CAPABILITIES))
    assert policy["selection_boundary"] == (
        "after-python-route-preflight-before-rust-candidate-execution"
    )
    assert policy["candidate_execution_failure"] == (
        "fail-closed-without-python-reexecution"
    )
    assert policy["general_non-route_commands"] == "python"
