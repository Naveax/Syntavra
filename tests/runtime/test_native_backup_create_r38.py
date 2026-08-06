from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

from syntavra_runtime.util import stable_project_id
from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]
FIXED_MASTER_KEY = base64.b64encode(bytes(range(32))).decode("ascii")


def _environment(home: Path, master_key: str | None) -> dict[str, str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["PATH"] = ""
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    if master_key is None:
        environment.pop("SYNTAVRA_EVIDENCE_MASTER_KEY_B64", None)
    else:
        environment["SYNTAVRA_EVIDENCE_MASTER_KEY_B64"] = master_key
    return environment


def _run_create(
    engine: str,
    project: Path,
    state: Path,
    destination: Path,
    *,
    plaintext: bool,
    master_key: str | None = FIXED_MASTER_KEY,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-home"
    home.mkdir(parents=True, exist_ok=True)
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    arguments = [
        *prefix,
        "--engine",
        engine,
        "--project",
        str(project),
        "--state-root",
        str(state),
        "backup",
        "create",
        str(destination),
    ]
    if plaintext:
        arguments.append("--plaintext")
    return subprocess.run(
        arguments,
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=_environment(home, master_key),
    )


def _run_python_verify(
    project: Path,
    state: Path,
    source: Path,
    *,
    plaintext: bool,
    master_key: str | None,
) -> subprocess.CompletedProcess[str]:
    home = state.parent / f"{state.name}-verify-home"
    home.mkdir(parents=True, exist_ok=True)
    arguments = [
        sys.executable,
        "-m",
        "syntavra_runtime.engine_entry",
        "--engine",
        "python",
        "--project",
        str(project),
        "--state-root",
        str(state),
        "backup",
        "verify",
        str(source),
    ]
    if plaintext:
        arguments.append("--plaintext")
    return subprocess.run(
        arguments,
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=_environment(home, master_key),
    )


def _read_manifest(archive: Path) -> tuple[dict[str, Any], bytes, list[str]]:
    with tarfile.open(archive, "r") as handle:
        names = sorted(member.name.removeprefix("./") for member in handle.getmembers())
        member = handle.getmember("BACKUP_MANIFEST.json")
        source = handle.extractfile(member)
        assert source is not None
        payload = source.read()
    return json.loads(payload), payload, names


def _prepare_state(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "config.json").write_text(
        json.dumps({"enabled": True}, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    nested = state / "nested"
    nested.mkdir()
    (nested / "data.txt").write_text("exact backup payload\n", encoding="utf-8")
    with sqlite3.connect(state / "runtime.sqlite3") as connection:
        connection.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO records(value) VALUES('sqlite-backup-ok')")
    for excluded in ("backups", "backup-keys", "tmp"):
        directory = state / excluded
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "excluded.txt").write_text("must not enter archive\n", encoding="utf-8")


def _assert_result(value: dict[str, Any], destination: Path, *, encrypted: bool) -> None:
    assert value["path"] == str(destination.resolve(strict=False))
    assert value["encrypted"] is encrypted
    assert isinstance(value["created_at"], float)
    assert value["created_at"] > 0
    assert isinstance(value["plaintext_bytes"], int)
    assert value["plaintext_bytes"] > 0
    assert len(value["manifest_hash"]) == 64
    int(value["manifest_hash"], 16)


def _assert_manifest_contract(
    manifest: dict[str, Any],
    payload: bytes,
    names: list[str],
    value: dict[str, Any],
    project: Path,
) -> None:
    assert manifest["schema_version"] == 1
    assert manifest["project_id"] == stable_project_id(project)
    assert manifest["created_at"] == value["created_at"]
    assert value["files"] == len(manifest["files"])
    assert "BACKUP_MANIFEST.json" in names
    assert set(manifest["files"]).issubset(names)
    assert all(
        not relative.startswith(("backups/", "backup-keys/", "tmp/"))
        for relative in manifest["files"]
    )
    assert hashlib.sha256(payload).hexdigest() == value["manifest_hash"]


def _comparable_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    rendered = json.loads(json.dumps(manifest, sort_keys=True))
    rendered.pop("created_at", None)
    return rendered


def test_native_backup_create_plaintext_empty_state_matches_contract(tmp_path: Path) -> None:
    project = tmp_path / "project"
    values: dict[str, dict[str, Any]] = {}
    manifests: dict[str, dict[str, Any]] = {}
    for engine in ("python", "rust"):
        state = tmp_path / f"{engine}-state"
        destination = tmp_path / f"{engine}-empty.tar"
        completed = _run_create(
            engine,
            project,
            state,
            destination,
            plaintext=True,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert completed.stderr == ""
        value = json.loads(completed.stdout)
        _assert_result(value, destination, encrypted=False)
        manifest, payload, names = _read_manifest(destination)
        _assert_manifest_contract(manifest, payload, names, value, project)
        assert (state / "backups").is_dir()
        assert (state / "backup-keys" / "keys").is_dir()
        values[engine] = value
        manifests[engine] = _comparable_manifest(manifest)
    assert values["rust"]["files"] == values["python"]["files"]
    assert values["rust"]["encrypted"] == values["python"]["encrypted"]
    assert manifests["rust"] == manifests["python"]


def test_native_backup_create_plaintext_captures_files_and_sqlite(tmp_path: Path) -> None:
    project = tmp_path / "project"
    manifests: dict[str, dict[str, Any]] = {}
    for engine in ("python", "rust"):
        state = tmp_path / f"{engine}-state"
        _prepare_state(state)
        destination = tmp_path / f"{engine}-state.tar"
        completed = _run_create(
            engine,
            project,
            state,
            destination,
            plaintext=True,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert completed.stderr == ""
        value = json.loads(completed.stdout)
        _assert_result(value, destination, encrypted=False)
        manifest, payload, names = _read_manifest(destination)
        _assert_manifest_contract(manifest, payload, names, value, project)
        assert {
            "config.json",
            "nested/data.txt",
            "runtime.sqlite3",
        }.issubset(manifest["files"])
        extract = tmp_path / f"{engine}-extract"
        with tarfile.open(destination, "r") as handle:
            handle.extractall(extract, filter="data")
        with sqlite3.connect(extract / "runtime.sqlite3") as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT value FROM records").fetchone()[0] == "sqlite-backup-ok"
        manifests[engine] = _comparable_manifest(manifest)
    assert manifests["rust"] == manifests["python"]


def test_native_backup_create_encrypted_is_python_verifiable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    for engine in ("python", "rust"):
        state = tmp_path / f"{engine}-encrypted-state"
        _prepare_state(state)
        destination = tmp_path / f"{engine}.scbackup"
        completed = _run_create(
            engine,
            project,
            state,
            destination,
            plaintext=False,
            master_key=FIXED_MASTER_KEY,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert completed.stderr == ""
        value = json.loads(completed.stdout)
        _assert_result(value, destination, encrypted=True)
        assert value["files"] >= 3
        assert destination.read_bytes()[:8] == b"SCCHNK2\x00"
        verification = _run_python_verify(
            project,
            state,
            destination,
            plaintext=False,
            master_key=FIXED_MASTER_KEY,
        )
        assert verification.returncode == 0, (verification.stdout, verification.stderr)
        assert verification.stderr == ""
        assert json.loads(verification.stdout) == {
            "ok": True,
            "files": value["files"],
            "failures": [],
        }


def test_native_backup_create_local_key_is_python_verifiable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "rust-local-state"
    _prepare_state(state)
    destination = tmp_path / "rust-local.scbackup"
    completed = _run_create(
        "rust",
        project,
        state,
        destination,
        plaintext=False,
        master_key=None,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr == ""
    value = json.loads(completed.stdout)
    key = state / "backup-keys" / "keys" / "local-v1.key"
    registry = state / "backup-keys" / "keys" / "registry.json"
    assert key.stat().st_size == 32
    assert json.loads(registry.read_text(encoding="utf-8"))["active"] == "local-v1"
    verification = _run_python_verify(
        project,
        state,
        destination,
        plaintext=False,
        master_key=None,
    )
    assert verification.returncode == 0, (verification.stdout, verification.stderr)
    assert verification.stderr == ""
    assert json.loads(verification.stdout) == {
        "ok": True,
        "files": value["files"],
        "failures": [],
    }


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_native_backup_create_skips_symlink_sources(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "rust-symlink-state"
    state.mkdir(parents=True)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        os.symlink(outside, state / "linked-secret.txt")
    except OSError:
        pytest.skip("symlink creation unavailable")
    destination = tmp_path / "rust-symlink.tar"
    completed = _run_create(
        "rust",
        project,
        state,
        destination,
        plaintext=True,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    manifest, _, _ = _read_manifest(destination)
    assert "linked-secret.txt" not in manifest["files"]
