from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Callable

from tests.runtime.test_native_backup_create_r38 import (
    FIXED_MASTER_KEY,
    _environment,
    _prepare_state,
    _run_create,
)
from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run_verify(
    engine: str,
    project: Path,
    state: Path,
    source: Path,
    *,
    plaintext: bool,
    master_key: str | None = FIXED_MASTER_KEY,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-verify-home"
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


def _create(
    engine: str,
    project: Path,
    state: Path,
    destination: Path,
    *,
    plaintext: bool,
    master_key: str | None = FIXED_MASTER_KEY,
) -> dict[str, Any]:
    _prepare_state(state)
    completed = _run_create(
        engine,
        project,
        state,
        destination,
        plaintext=plaintext,
        master_key=master_key,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def _assert_ok(completed: subprocess.CompletedProcess[str], files: int) -> None:
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "ok": True,
        "files": files,
        "failures": [],
    }


def _rewrite_plaintext_archive(
    source: Path,
    destination: Path,
    extraction: Path,
    *,
    mutate: Callable[[Path], None],
) -> None:
    extraction.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source, "r") as handle:
        handle.extractall(extraction, filter="data")
    mutate(extraction)
    with tarfile.open(destination, "w") as handle:
        for path in sorted(extraction.rglob("*")):
            handle.add(path, arcname=path.relative_to(extraction), recursive=False)


def test_native_backup_verify_plaintext_cross_engine(tmp_path: Path) -> None:
    project = tmp_path / "project"
    for creator in ("python", "rust"):
        state = tmp_path / f"{creator}-plaintext-state"
        archive = tmp_path / f"{creator}.tar"
        created = _create(creator, project, state, archive, plaintext=True)
        for verifier in ("python", "rust"):
            _assert_ok(
                _run_verify(
                    verifier,
                    project,
                    state,
                    archive,
                    plaintext=True,
                ),
                created["files"],
            )


def test_native_backup_verify_encrypted_cross_engine(tmp_path: Path) -> None:
    project = tmp_path / "project"
    for creator in ("python", "rust"):
        state = tmp_path / f"{creator}-encrypted-state"
        archive = tmp_path / f"{creator}.scbackup"
        created = _create(creator, project, state, archive, plaintext=False)
        for verifier in ("python", "rust"):
            _assert_ok(
                _run_verify(
                    verifier,
                    project,
                    state,
                    archive,
                    plaintext=False,
                ),
                created["files"],
            )


def test_native_backup_verify_python_local_key_archive(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "python-local-state"
    archive = tmp_path / "python-local.scbackup"
    created = _create(
        "python",
        project,
        state,
        archive,
        plaintext=False,
        master_key=None,
    )
    _assert_ok(
        _run_verify(
            "rust",
            project,
            state,
            archive,
            plaintext=False,
            master_key=None,
        ),
        created["files"],
    )


def test_native_backup_verify_hash_failure_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "tampered-state"
    original = tmp_path / "original.tar"
    created = _create("python", project, state, original, plaintext=True)
    tampered = tmp_path / "tampered.tar"

    def mutate(root: Path) -> None:
        (root / "config.json").write_text('{"enabled":false}\n', encoding="utf-8")

    _rewrite_plaintext_archive(
        original,
        tampered,
        tmp_path / "tampered-extract",
        mutate=mutate,
    )
    expected = {
        "ok": False,
        "files": created["files"],
        "failures": ["config.json"],
    }
    for verifier in ("python", "rust"):
        completed = _run_verify(
            verifier,
            project,
            state,
            tampered,
            plaintext=True,
        )
        assert completed.returncode == 3, (completed.stdout, completed.stderr)
        assert completed.stderr == ""
        assert json.loads(completed.stdout) == expected


def test_native_backup_verify_rejects_project_scope_mismatch(tmp_path: Path) -> None:
    source_project = tmp_path / "source-project"
    other_project = tmp_path / "other-project"
    state = tmp_path / "scope-state"
    archive = tmp_path / "scope.tar"
    _create("python", source_project, state, archive, plaintext=True)
    for verifier in ("python", "rust"):
        completed = _run_verify(
            verifier,
            other_project,
            state,
            archive,
            plaintext=True,
        )
        assert completed.returncode != 0
        assert not (tmp_path / "escaped.txt").exists()


def test_native_backup_verify_rejects_path_traversal(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "traversal-state"
    archive = tmp_path / "traversal.tar"
    manifest = {
        "schema_version": 1,
        "project_id": "unused-before-extraction",
        "created_at": 1.0,
        "files": {},
    }
    with tarfile.open(archive, "w") as handle:
        payload = b"escape\n"
        member = tarfile.TarInfo("../escaped.txt")
        member.size = len(payload)
        handle.addfile(member, io.BytesIO(payload))
        encoded = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
        manifest_member = tarfile.TarInfo("BACKUP_MANIFEST.json")
        manifest_member.size = len(encoded)
        handle.addfile(manifest_member, io.BytesIO(encoded))
    for verifier in ("python", "rust"):
        completed = _run_verify(
            verifier,
            project,
            state,
            archive,
            plaintext=True,
        )
        assert completed.returncode != 0
        assert not (tmp_path / "escaped.txt").exists()


def test_native_backup_verify_rejects_special_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "special-state"
    archive = tmp_path / "special.tar"
    with tarfile.open(archive, "w") as handle:
        member = tarfile.TarInfo("linked")
        member.type = tarfile.SYMTYPE
        member.linkname = "outside"
        handle.addfile(member)
    for verifier in ("python", "rust"):
        completed = _run_verify(
            verifier,
            project,
            state,
            archive,
            plaintext=True,
        )
        assert completed.returncode != 0


def test_native_backup_verify_rejects_encrypted_authentication_tamper(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state = tmp_path / "auth-state"
    archive = tmp_path / "auth.scbackup"
    _create("rust", project, state, archive, plaintext=False)
    payload = bytearray(archive.read_bytes())
    payload[-1] ^= 0x01
    archive.write_bytes(payload)
    for verifier in ("python", "rust"):
        completed = _run_verify(
            verifier,
            project,
            state,
            archive,
            plaintext=False,
        )
        assert completed.returncode != 0
