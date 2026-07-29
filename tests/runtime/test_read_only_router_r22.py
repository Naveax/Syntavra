from __future__ import annotations

from pathlib import Path

import pytest

from syntavra_runtime.engine_selector import (
    EngineSelection,
    EngineSelectionError,
    EngineVerification,
)
from syntavra_runtime.read_only_router_r22 import (
    AUTO_POLICY,
    AUTO_RUST_COMMANDS,
    ReadOnlyCommandRouterR22,
)


class FakeSelector:
    def __init__(
        self,
        *,
        requested: str = "auto",
        available: bool = True,
        compatible: bool = True,
        capabilities: tuple[str, ...] = ("version",),
    ) -> None:
        self.requested = requested
        self.available = available
        self.compatible = compatible
        self.capabilities = capabilities
        self.binary = Path("syntavra-rs")

    def resolve(self, *, cli_override: str | None = None) -> EngineSelection:
        requested = self.requested if cli_override is None else cli_override
        resolved = "python" if requested == "auto" else requested
        return EngineSelection(
            requested=requested,
            resolved=resolved,
            source="test",
            scope="command",
            source_path="",
            reason="TEST_SELECTION",
        )

    def verify_rust(self) -> EngineVerification:
        return EngineVerification(
            engine="rust",
            available=self.available,
            compatible=self.compatible,
            stability="experimental",
            executable=str(self.binary) if self.available else "",
            capabilities=self.capabilities,
            checks={"test": self.compatible},
            errors=() if self.compatible else ("TEST_INCOMPATIBLE",),
            claim="RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT",
        )

    def discover_rust_binary(self) -> Path | None:
        return self.binary if self.available else None


def _rust_version() -> dict[str, object]:
    return {
        "product": "Syntavra",
        "product_version": "0.0.1",
        "release_channel": "pre-release",
        "engine": "rust",
        "engine_stability": "experimental",
        "contract_version": 1,
    }


def test_auto_selects_rust_only_when_route_is_fully_eligible(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(_binary: Path, arguments: tuple[str, ...]) -> dict[str, object]:
        calls.append(arguments)
        return _rust_version()

    router = ReadOnlyCommandRouterR22(
        FakeSelector(capabilities=("version",)),
        runner=runner,
        project_input_root=tmp_path,
        platform_name="linux",
    )

    result = router.route("version", cli_override="auto")

    assert calls == [("version",)]
    assert result["phase"] == "R22"
    assert result["schema_version"] == 11
    assert result["selection"]["requested"] == "auto"
    assert result["selection"]["resolved"] == "rust"
    assert result["selection"]["auto_policy"] == AUTO_POLICY
    assert result["auto_decision"]["reason"] == "AUTO_ROUTE_RUST_ELIGIBLE_R22"
    assert result["auto_decision"]["rust_started"] is False
    assert result["result"]["engine"] == "rust"


def test_auto_selects_python_before_start_when_rust_is_unavailable(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    router = ReadOnlyCommandRouterR22(
        FakeSelector(available=False, compatible=False, capabilities=()),
        runner=lambda _binary, arguments: calls.append(arguments) or _rust_version(),
        project_input_root=tmp_path,
        platform_name="linux",
    )

    result = router.route("version", cli_override="auto")

    assert calls == []
    assert result["selection"]["requested"] == "auto"
    assert result["selection"]["resolved"] == "python"
    assert result["auto_decision"]["reason"] == "AUTO_ROUTE_PYTHON_RUST_UNAVAILABLE_R22"
    assert result["auto_decision"]["fallback_attempted"] is False
    assert result["result"]["engine"] == "python"


def test_auto_selects_python_when_route_capability_is_missing(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouterR22(
        FakeSelector(capabilities=("status",)),
        runner=lambda _binary, _arguments: pytest.fail("Rust must not start"),
        project_input_root=tmp_path,
        platform_name="linux",
    )

    result = router.route("version", cli_override="auto")

    assert result["selection"]["resolved"] == "python"
    assert result["auto_decision"]["reason"] == "AUTO_ROUTE_PYTHON_CAPABILITY_MISSING_R22"
    assert result["auto_decision"]["capability_present"] is False


def test_auto_selects_python_on_unsupported_platform(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouterR22(
        FakeSelector(capabilities=("version",)),
        runner=lambda _binary, _arguments: pytest.fail("Rust must not start"),
        project_input_root=tmp_path,
        platform_name="plan9",
    )

    result = router.route("version", cli_override="auto")

    assert result["selection"]["resolved"] == "python"
    assert result["auto_decision"]["reason"] == "AUTO_ROUTE_PYTHON_UNSUPPORTED_PLATFORM_R22"


def test_explicit_rust_failure_never_reexecutes_python(tmp_path: Path) -> None:
    calls = 0

    def failing_runner(_binary: Path, _arguments: tuple[str, ...]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise RuntimeError("candidate failed")

    router = ReadOnlyCommandRouterR22(
        FakeSelector(requested="rust", capabilities=("version",)),
        runner=failing_runner,
        project_input_root=tmp_path,
        platform_name="linux",
    )

    with pytest.raises(EngineSelectionError) as captured:
        router.route("version", cli_override="rust")

    assert calls == 1
    assert captured.value.code == "RUST_ROUTE_EXECUTION_FAILED_R14"
    assert captured.value.details["phase"] == "R22"
    assert captured.value.details["fallback_attempted"] is False


def test_auto_rust_failure_never_retries_python(tmp_path: Path) -> None:
    calls = 0

    def failing_runner(_binary: Path, _arguments: tuple[str, ...]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise RuntimeError("candidate failed")

    router = ReadOnlyCommandRouterR22(
        FakeSelector(capabilities=("version",)),
        runner=failing_runner,
        project_input_root=tmp_path,
        platform_name="linux",
    )

    with pytest.raises(EngineSelectionError) as captured:
        router.route("version", cli_override="auto")

    assert calls == 1
    assert captured.value.details["auto_decision"]["selected_engine"] == "rust"
    assert captured.value.details["fallback_attempted"] is False


def test_r22_whitelist_matches_all_proven_installed_read_only_routes() -> None:
    assert AUTO_RUST_COMMANDS == {
        "config.resolve",
        "receipt.inspect",
        "state.broker-live-snapshot",
        "state.broker-snapshot",
        "state.inspect",
        "state.layout",
        "status",
        "version",
    }
