from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from syntavra_runtime.engine_entry import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.telemetry_metrics_contract import telemetry_metrics_result
from syntavra_runtime.telemetry_metrics_router_r24 import TelemetryMetricsRouterR24
from syntavra_runtime.unified_cli import main as unified_main


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


def _selector(project: Path) -> EngineSelector:
    binary = project / "syntavra-rs"
    binary.write_bytes(b"test")
    return EngineSelector(
        project_root=project,
        state_root=project / ".syntavra" / "pre-release",
        env={"HOME": str(project / "home")},
        rust_binary=binary,
        runner=_verification_runner,
    )


def test_python_and_unified_cli_do_not_create_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert engine_main(
        ["--engine", "python", "--project", str(project), "telemetry", "metrics"]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {
        "counters": [],
        "gauges": [],
        "histograms": [],
    }
    assert not (project / ".syntavra").exists()

    assert unified_main(["--project", str(project), "telemetry", "metrics"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "counters": [],
        "gauges": [],
        "histograms": [],
    }
    assert not (project / ".syntavra").exists()


def test_prometheus_output_is_one_empty_line_without_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert engine_main(
        [
            "--engine",
            "python",
            "--project",
            str(project),
            "telemetry",
            "metrics",
            "--prometheus",
        ]
    ) == 0
    assert capsys.readouterr().out == "\n"
    assert not (project / ".syntavra").exists()


def test_auto_selects_verified_rust_for_both_formats(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[str, ...]] = []

    def runner(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        return telemetry_metrics_result(arguments[2])

    router = TelemetryMetricsRouterR24(
        _selector(project),
        runner=runner,
        project_input_root=project,
        platform_probe=lambda: ("linux", "x86_64"),
    )
    json_result = router.route("telemetry.metrics", cli_override="auto")
    prometheus_result = router.route(
        "telemetry.metrics",
        cli_override="auto",
        telemetry_prometheus=True,
    )

    assert json_result["selection"]["resolved"] == "rust"
    assert json_result["result"] == telemetry_metrics_result("json")
    assert prometheus_result["result"] == telemetry_metrics_result("prometheus")
    assert calls == [
        ("telemetry", "metrics", "json"),
        ("telemetry", "metrics", "prometheus"),
    ]
    assert not (project / ".syntavra").exists()


def test_drift_and_execution_failure_never_fall_back(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def drifting(_binary: Path, _arguments: tuple[str, ...]) -> Mapping[str, Any]:
        return {"format": "json", "metrics": {"counters": [{"name": "drift"}]}}

    router = TelemetryMetricsRouterR24(
        _selector(project),
        runner=drifting,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as drift:
        router.route("telemetry.metrics", cli_override="rust")
    assert drift.value.code == "RUST_TELEMETRY_METRICS_PARITY_INVALID_R24"
    assert drift.value.details["fallback_attempted"] is False

    calls: list[tuple[str, ...]] = []

    def failing(_binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        calls.append(arguments)
        raise RuntimeError("candidate failure")

    router = TelemetryMetricsRouterR24(
        _selector(project),
        runner=failing,
        project_input_root=project,
    )
    with pytest.raises(EngineSelectionError) as failure:
        router.route("telemetry.metrics", cli_override="rust")
    assert failure.value.code == "RUST_ROUTE_EXECUTION_FAILED_R24"
    assert failure.value.details["fallback_attempted"] is False
    assert calls == [("telemetry", "metrics", "json")]
