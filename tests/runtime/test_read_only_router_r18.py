from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from syntavra_runtime.engine_cli import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.read_only_router_r18 import ReadOnlyCommandRouterR18
from syntavra_runtime.state_snapshot_contract import (
    MAX_FILE_BYTES,
    inspect_state_root,
    project_id_for_root,
)


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
    if len(arguments) == 4 and arguments[:2] == ("state", "inspect"):
        return inspect_state_root(
            Path(arguments[3]),
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


def _populate_state(tmp_path: Path) -> None:
    state = tmp_path / ".syntavra"
    (state / "pre-release").mkdir(parents=True)
    (state / "runtime-v3").mkdir()
    (state / "config.toml").write_bytes(b'[runtime]\nprofile = "audit"\n')
    (state / "engine.json").write_bytes(b'{"schema_version":1,"engine":"python"}\n')


def _inventory(root: Path) -> dict[str, tuple[str, int | None, str | None]]:
    rows: dict[str, tuple[str, int | None, str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows[relative] = ("symlink", None, None)
        elif path.is_dir():
            rows[relative] = ("directory", None, None)
        elif path.is_file():
            payload = path.read_bytes()
            rows[relative] = ("file", len(payload), hashlib.sha256(payload).hexdigest())
    return rows


def test_state_inspect_has_exact_cross_engine_parity_without_mutation(
    tmp_path: Path,
) -> None:
    _populate_state(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR18(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    before = _inventory(tmp_path)
    python_result = router.route("state.inspect", cli_override="python")
    rust_result = router.route("state.inspect", cli_override="rust")
    after = _inventory(tmp_path)
    project_id = project_id_for_root(tmp_path)

    assert python_result["result"] == rust_result["result"]
    assert rust_result["result"] == inspect_state_root(
        tmp_path,
        expected_project_id=project_id,
    )
    assert rust_result["phase"] == "R18"
    assert rust_result["schema_version"] == 7
    assert rust_result["command"] == "state.inspect"
    assert rust_result["capability"] == "state.inspect"
    assert rust_result["mutation"] == "read-only"
    assert rust_result["input"] == {
        "profile": "project-bound-state-root-v1",
        "format": "sha256-normalized-absolute-path-v1",
        "bytes": 32,
        "sha256": project_id,
    }
    assert rust_result["limits"] == {"maximum_file_bytes": MAX_FILE_BYTES}
    assert rust_result["fallback"] == {"policy": "none", "attempted": False}
    assert len(calls) == 1
    assert calls[0][:3] == ("state", "inspect", project_id)
    assert before == after
    rendered = json.dumps(rust_result, sort_keys=True)
    assert str(tmp_path) not in rendered


@pytest.mark.parametrize(
    "kwargs",
    [
        {"config_wire_hex": "00"},
        {"live_config": True},
        {"session_override_json_hex": "7b7d"},
        {"task_override_json_hex": "7b7d"},
    ],
)
def test_state_inspect_rejects_configuration_inputs_before_filesystem_read(
    tmp_path: Path,
    kwargs: dict[str, object],
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR18(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.inspect", cli_override="rust", **kwargs)
    assert error.value.code == "ENGINE_ROUTE_STATE_INSPECT_INPUT_UNSUPPORTED_R18"
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


def test_state_inspect_rejects_project_root_symlink_before_engine_selection(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "project-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR18(
        _selector(target),
        runner=runner,
        project_input_root=link,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.inspect", cli_override="rust")
    assert error.value.code == "ENGINE_ROUTE_STATE_INSPECT_PREFLIGHT_FAILED_R18"
    assert error.value.details["state_error"] == "STATE_PROJECT_ROOT_SYMLINK"
    assert str(link) not in json.dumps(error.value.to_dict(), sort_keys=True)
    assert calls == []


def test_state_inspect_rejects_oversized_file_before_engine_selection(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".syntavra"
    state.mkdir()
    with (state / "config.toml").open("wb") as handle:
        handle.truncate(MAX_FILE_BYTES + 1)
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR18(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.inspect", cli_override="rust")
    assert error.value.code == "ENGINE_ROUTE_STATE_INSPECT_PREFLIGHT_FAILED_R18"
    assert error.value.details["state_error"] == "STATE_FILE_SIZE_LIMIT"
    assert calls == []


def test_state_inspect_drift_fails_closed_with_digest_only_diagnostics(
    tmp_path: Path,
) -> None:
    _populate_state(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        if len(arguments) == 4 and arguments[:2] == ("state", "inspect"):
            value = inspect_state_root(
                Path(arguments[3]),
                expected_project_id=arguments[2],
            )
            value["inspection_id"] = "sensitive-drifted-inspection"
            return value
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR18(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.inspect", cli_override="rust")
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "RUST_STATE_INSPECT_ROUTE_PARITY_INVALID_R18"
    assert error.value.details["mismatched_keys"] == ["inspection_id"]
    assert len(error.value.details["expected_sha256"]) == 64
    assert len(error.value.details["actual_sha256"]) == 64
    assert "sensitive-drifted-inspection" not in rendered
    assert str(tmp_path) not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert len(calls) == 1


def test_state_inspect_execution_failure_never_falls_back_to_python(
    tmp_path: Path,
) -> None:
    _populate_state(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        if len(arguments) == 4 and arguments[:2] == ("state", "inspect"):
            raise RuntimeError("sensitive project path failure")
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR18(
        _selector(tmp_path),
        runner=runner,
        project_input_root=tmp_path,
    )
    with pytest.raises(EngineSelectionError) as error:
        router.route("state.inspect", cli_override="rust")
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "RUST_ROUTE_EXECUTION_FAILED_R18"
    assert error.value.details["exception_message"] == "redacted"
    assert "sensitive project path failure" not in rendered
    assert str(tmp_path) not in rendered
    assert error.value.details["fallback_attempted"] is False
    assert len(calls) == 1


def test_engine_cli_routes_state_inspection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _populate_state(tmp_path)
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouterR18(
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
            "state.inspect",
        ],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["phase"] == "R18"
    assert value["schema_version"] == 7
    assert value["command"] == "state.inspect"
    assert value["selection"]["resolved"] == "rust"
    assert value["result"]["project_binding"]["matched"] is True
    assert str(tmp_path) not in json.dumps(value, sort_keys=True)
