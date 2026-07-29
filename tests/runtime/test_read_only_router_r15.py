from __future__ import annotations

import json
import os
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
from syntavra_runtime.read_only_router_r15 import ReadOnlyCommandRouterR15


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
        raise AssertionError("live status must use an explicit discovered wire")
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


def _identity(path: Path) -> tuple[bytes, int, int]:
    metadata = path.stat()
    return path.read_bytes(), metadata.st_size, metadata.st_mtime_ns


def test_live_config_resolve_has_exact_cross_engine_parity_without_mutation(
    tmp_path: Path,
) -> None:
    user, project = _write_live_config(tmp_path)
    selector = _selector(
        tmp_path,
        extra_env={
            "SYNTAVRA_CFG__PROVIDER__TIMEOUT_SECONDS": "90.0",
            "SYNTAVRA_CFG__PROVIDER__CREDENTIAL_REF": "secret://ci/provider",
        },
    )
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR15(selector, runner=runner)
    before = (_identity(user), _identity(project))
    python_result = router.route(
        "config.resolve",
        cli_override="python",
        live_config=True,
    )
    rust_result = router.route(
        "config.resolve",
        cli_override="rust",
        live_config=True,
    )
    assert python_result["result"] == rust_result["result"]
    assert python_result["phase"] == rust_result["phase"] == "R15"
    assert python_result["schema_version"] == rust_result["schema_version"] == 4
    assert python_result["input"]["profile"] == "live-config-discovery-v1"
    assert rust_result["result"]["values"]["runtime"]["profile"] == "detailed"
    assert rust_result["result"]["values"]["provider"]["timeout_seconds"] == 90.0
    credential_rows = [
        row
        for row in rust_result["result"]["provenance"]
        if row["path"] == "provider.credential_ref"
    ]
    assert credential_rows[-1]["value"] == "[secret-ref]"
    assert before == (_identity(user), _identity(project))
    assert not (tmp_path / ".syntavra" / "pre-release" / "config-last-good.json").exists()
    assert len(calls) == 1
    discovered_wire_hex = calls[0][2]
    assert calls[0][:2] == ("config", "resolve")
    assert discovered_wire_hex not in json.dumps(rust_result)


def test_live_status_has_exact_cross_engine_parity(tmp_path: Path) -> None:
    _write_live_config(tmp_path)
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouterR15(selector, runner=_rust_runner)
    python_result = router.route("status", cli_override="python", live_config=True)
    rust_result = router.route("status", cli_override="rust", live_config=True)
    assert python_result["result"] == rust_result["result"]
    assert python_result["input"] == rust_result["input"]
    assert python_result["input"]["profile"] == "live-config-discovery-v1"
    assert python_result["input"]["format"] == "R6CFG1"
    assert python_result["input"]["bytes"] > 0
    assert len(python_result["input"]["sha256"]) == 64


def test_invalid_live_config_fails_before_engine_execution(tmp_path: Path) -> None:
    _user, project = _write_live_config(tmp_path)
    project.write_bytes(b"[runtime\nprofile = 'broken'\n")
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR15(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("status", cli_override="rust", live_config=True)
    assert error.value.code == "ENGINE_ROUTE_LIVE_CONFIG_INVALID_R15"
    assert error.value.details["phase"] == "R15"
    assert error.value.details["fallback_attempted"] is False
    assert "broken" not in json.dumps(error.value.to_dict())
    assert calls == []


def test_live_and_explicit_inputs_are_mutually_exclusive(tmp_path: Path) -> None:
    _write_live_config(tmp_path)
    router = ReadOnlyCommandRouterR15(_selector(tmp_path), runner=_rust_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route(
            "status",
            cli_override="rust",
            config_wire_hex="00",
            live_config=True,
        )
    assert error.value.code == "ENGINE_ROUTE_INPUT_CONFLICT_R15"
    assert error.value.details["fallback_attempted"] is False


def test_version_rejects_live_config_before_engine_execution(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(binary: Path, arguments: tuple[str, ...]):
        calls.append(arguments)
        return _rust_runner(binary, arguments)

    router = ReadOnlyCommandRouterR15(_selector(tmp_path), runner=runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("version", cli_override="rust", live_config=True)
    assert error.value.code == "ENGINE_ROUTE_LIVE_CONFIG_UNSUPPORTED_R15"
    assert calls == []


def test_symlinked_project_config_is_rejected(tmp_path: Path) -> None:
    _user, project = _write_live_config(tmp_path)
    target = tmp_path / "actual-config.toml"
    target.write_bytes(project.read_bytes())
    project.unlink()
    try:
        os.symlink(target, project)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    router = ReadOnlyCommandRouterR15(_selector(tmp_path), runner=_rust_runner)
    with pytest.raises(EngineSelectionError) as error:
        router.route("config.resolve", cli_override="rust", live_config=True)
    assert error.value.code == "ENGINE_ROUTE_LIVE_CONFIG_INVALID_R15"
    assert "symlink" in error.value.details["reason"]


def test_engine_cli_routes_live_config_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_live_config(tmp_path)
    selector = _selector(tmp_path)
    router = ReadOnlyCommandRouterR15(selector, runner=_rust_runner)
    code = engine_main(
        [
            "--project",
            str(tmp_path),
            "engine",
            "route",
            "status",
            "--live-config",
        ],
        selector=selector,
        cli_override="rust",
        router=router,
    )
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["phase"] == "R15"
    assert value["schema_version"] == 4
    assert value["input"]["profile"] == "live-config-discovery-v1"
    assert value["selection"]["resolved"] == "rust"
