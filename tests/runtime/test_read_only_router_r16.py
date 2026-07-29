from __future__ import annotations

import json
from pathlib import Path

import pytest

from syntavra_runtime.config_contract import resolve_config_wire, status_projection
from syntavra_runtime.engine_cli import main as engine_main
from syntavra_runtime.engine_selector import (
    ENGINE_CONTRACT_SHA256,
    RUST_CAPABILITIES,
    EngineSelectionError,
    EngineSelector,
)
from syntavra_runtime.read_only_router_r16 import ReadOnlyCommandRouterR16
from syntavra_runtime.util import canonical_json


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
        raise AssertionError("R16 live status must use a discovered wire")
    if len(arguments) == 2 and arguments[0] == "status":
        return status_projection(resolve_config_wire(bytes.fromhex(arguments[1])))
    if len(arguments) == 3 and arguments[:2] == ("config", "resolve"):
        return resolve_config_wire(bytes.fromhex(arguments[2]))
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


def _selector(tmp_path: Path, *, extra_env: dict[str, str] | None = None) -> EngineSelector:
    binary = tmp_path / "syntavra-rs"
    binary.write_bytes(b"test")
    env = {"HOME": str(tmp_path / "home")}
    env.update(extra_env or {})
    return EngineSelector(
        project_root=tmp_path,
        env=env,
        rust_binary=binary,
        runner=_rust_runner,
    )


def _write_live_config(tmp_path: Path) -> tuple[Path, Path]:
    user = tmp_path / "home" / ".config" / "syntavra" / "config.toml"
    project = tmp_path / ".syntavra" / "config.toml"
    user.parent.mkdir(parents=True)
    project.parent.mkdir(parents=True)
    user.write_bytes(b'[runtime]\nprofile = "compact"\n')
    project.write_bytes(
        b'[runtime]\nprofile = "detailed"\n[routing]\nbudget_bytes = 4096\n'
    )
    return user, project


def _override_hex(value: dict[str, object]) -> str:
    return canonical_json(value).hex()


def _identity(path: Path) -> tuple[bytes, int, int]:
    metadata = path.stat()
    return path.read_bytes(), metadata.st_size, metadata.st_mtime_ns


def test_session_task_overrides_have_exact_cross_engine_parity_and_precedence(
    tmp_path: Path,
) -> None:
    user, project = _write_live_config(tmp_path)
    selector = _selector(
        tmp_path,
        extra_env={"SYNTAVRA_CFG__PROVIDER__TIMEOUT_SECONDS": "90.0"},
    )
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR16(selector, runner=runner)
    session_hex = _override_hex(
        {
            "provider": {"timeout_seconds": 45.0},
            "runtime": {"profile": "audit"},
        }
    )
    task_hex = _override_hex(
        {
            "routing": {"budget_bytes": 16384},
            "runtime": {"profile": "terse"},
        }
    )
    before = (_identity(user), _identity(project))
    python_result = router.route(
        "config.resolve",
        cli_override="python",
        live_config=True,
        session_override_json_hex=session_hex,
        task_override_json_hex=task_hex,
    )
    rust_result = router.route(
        "config.resolve",
        cli_override="rust",
        live_config=True,
        session_override_json_hex=session_hex,
        task_override_json_hex=task_hex,
    )

    assert python_result["result"] == rust_result["result"]
    assert rust_result["phase"] == "R16"
    assert rust_result["schema_version"] == 5
    assert rust_result["input"]["profile"] == "live-config-session-task-v1"
    assert rust_result["result"]["values"]["runtime"]["profile"] == "terse"
    assert rust_result["result"]["values"]["provider"]["timeout_seconds"] == 45.0
    assert rust_result["result"]["values"]["routing"]["budget_bytes"] == 16384
    profile_rows = [
        row
        for row in rust_result["result"]["provenance"]
        if row["path"] == "runtime.profile"
    ]
    assert [row["scope"] for row in profile_rows[-4:]] == [
        "user",
        "project",
        "session",
        "task",
    ]
    assert before == (_identity(user), _identity(project))
    assert not (tmp_path / ".syntavra" / "pre-release" / "config-last-good.json").exists()
    assert len(calls) == 1
    encoded_result = json.dumps(rust_result, sort_keys=True)
    assert session_hex not in encoded_result
    assert task_hex not in encoded_result


def test_session_task_status_has_exact_cross_engine_parity(tmp_path: Path) -> None:
    _write_live_config(tmp_path)
    router = ReadOnlyCommandRouterR16(_selector(tmp_path), runner=_rust_runner)
    session_hex = _override_hex({"runtime": {"profile": "audit"}})
    task_hex = _override_hex({"runtime": {"profile": "terse"}})
    python_result = router.route(
        "status",
        cli_override="python",
        live_config=True,
        session_override_json_hex=session_hex,
        task_override_json_hex=task_hex,
    )
    rust_result = router.route(
        "status",
        cli_override="rust",
        live_config=True,
        session_override_json_hex=session_hex,
        task_override_json_hex=task_hex,
    )
    assert python_result["result"] == rust_result["result"]
    assert python_result["input"] == rust_result["input"]
    assert python_result["input"]["profile"] == "live-config-session-task-v1"


def test_override_requires_live_config_before_engine_execution(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR16(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "status",
            cli_override="rust",
            session_override_json_hex=_override_hex({"runtime": {"profile": "audit"}}),
        )
    assert error.value.code == "ENGINE_ROUTE_OVERRIDE_REQUIRES_LIVE_CONFIG_R16"
    assert error.value.details["fallback_attempted"] is False
    assert calls == []


@pytest.mark.parametrize(
    "raw",
    [
        b'{"runtime": {"profile": "audit"}}',
        b'{"runtime":{"profile":"audit","profile":"terse"}}',
        b'["not-an-object"]',
    ],
)
def test_invalid_override_fails_closed_without_raw_value(
    tmp_path: Path,
    raw: bytes,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR16(_selector(tmp_path), runner=runner)
    raw_hex = raw.hex()
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "config.resolve",
            cli_override="rust",
            live_config=True,
            session_override_json_hex=raw_hex,
        )
    rendered = json.dumps(error.value.to_dict(), sort_keys=True)
    assert error.value.code == "ENGINE_ROUTE_OVERRIDE_INVALID_R16"
    assert error.value.details["fallback_attempted"] is False
    assert raw_hex not in rendered
    assert "audit" not in rendered
    assert calls == []


def test_explicit_wire_and_override_are_mutually_exclusive(tmp_path: Path) -> None:
    router = ReadOnlyCommandRouterR16(_selector(tmp_path), runner=_rust_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "status",
            cli_override="rust",
            config_wire_hex="00",
            live_config=True,
            task_override_json_hex=_override_hex({"runtime": {"profile": "terse"}}),
        )
    assert error.value.code == "ENGINE_ROUTE_INPUT_CONFLICT_R16"
    assert error.value.details["fallback_attempted"] is False


def test_engine_cli_routes_live_config_with_transient_overrides(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_live_config(tmp_path)
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouterR16(selector, runner=_rust_runner)
    code = engine_main(
        [
            "--project",
            str(tmp_path),
            "engine",
            "route",
            "config.resolve",
            "--live-config",
            "--session-override-json-hex",
            _override_hex({"runtime": {"profile": "audit"}}),
            "--task-override-json-hex",
            _override_hex({"runtime": {"profile": "terse"}}),
        ],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["phase"] == "R16"
    assert value["schema_version"] == 5
    assert value["input"]["profile"] == "live-config-session-task-v1"
    assert value["result"]["values"]["runtime"]["profile"] == "terse"
    assert value["selection"]["resolved"] == "rust"
