from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _environment(home: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SYNTAVRA_CFG__")
    }
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["SYNTAVRA_CFG__PROVIDER__CREDENTIAL_REF"] = "secret://provider/key"
    return environment


def _run(
    engine: str,
    project: Path,
    home: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    if engine == "rust" and shutil.which("cargo") is None:
        pytest.skip("Cargo is required for the real Rust binary differential")
    argv = (
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "--bin",
            "syntavra",
            "--",
            "--engine",
            "rust",
        ]
        if engine == "rust"
        else [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
        ]
    )
    return subprocess.run(
        [*argv, "--project", str(project), *arguments],
        cwd=ROOT,
        env=_environment(home),
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    config = project / ".syntavra" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[runtime]\nprofile = "compact"\n\n[routing]\nbudget_bytes = 4_096\n',
        encoding="utf-8",
    )
    return project, home


def _assert_exact(
    project: Path,
    home: Path,
    *arguments: str,
) -> dict[str, object]:
    python_result = _run("python", project, home, *arguments)
    rust_result = _run("rust", project, home, *arguments)
    assert python_result.returncode == rust_result.returncode == 0, (
        python_result.stdout,
        python_result.stderr,
        rust_result.stdout,
        rust_result.stderr,
    )
    python_value = json.loads(python_result.stdout)
    rust_value = json.loads(rust_result.stdout)
    assert rust_value == python_value
    assert not (project / ".syntavra" / "pre-release").exists()
    return rust_value


def test_native_config_show_matches_live_python_snapshot(tmp_path: Path) -> None:
    project, home = _fixture(tmp_path)
    value = _assert_exact(project, home, "config", "show")
    assert "loaded_at" not in value
    assert value["values"]["runtime"]["profile"] == "compact"
    credential_rows = [
        row
        for row in value["provenance"]
        if row.get("path") == "provider.credential_ref"
    ]
    assert credential_rows[-1]["value"] == "[secret-ref]"


def test_native_config_explain_matches_found_and_missing_paths(tmp_path: Path) -> None:
    project, home = _fixture(tmp_path)
    found = _assert_exact(project, home, "config", "explain", "provider.credential_ref")
    assert found["value"] == "[secret-ref]"
    assert found["scope"] == "environment"
    missing = _assert_exact(project, home, "config", "explain", "missing.value")
    assert missing == {"found": False, "path": "missing.value"}


def test_native_config_validate_matches_python_contract(tmp_path: Path) -> None:
    project, home = _fixture(tmp_path)
    value = _assert_exact(project, home, "config", "validate")
    assert set(value) == {"ok", "config_hash", "warnings"}
    assert value["ok"] is True


def test_native_config_invalid_profile_fails_closed_without_state(tmp_path: Path) -> None:
    project, home = _fixture(tmp_path)
    (project / ".syntavra" / "config.toml").write_text(
        '[runtime]\nprofile = "invalid"\n',
        encoding="utf-8",
    )
    rust_result = _run("rust", project, home, "config", "validate")
    assert rust_result.returncode != 0
    assert "CONFIG_RUNTIME_PROFILE_INVALID" in rust_result.stdout
    assert not (project / ".syntavra" / "pre-release").exists()
