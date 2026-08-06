from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

from syntavra_runtime.crypto import open_sealed_file
from syntavra_runtime.util import stable_project_id
from tests.runtime.test_native_backup_create_r38 import (
    FIXED_MASTER_KEY,
    _environment,
)
from tests.runtime.test_native_backup_verify_r38 import (
    _create,
    _rewrite_plaintext_archive,
    _run_verify,
)
from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run_restore(
    engine: str,
    project: Path,
    state: Path,
    source: Path,
    *,
    plaintext: bool,
    apply: bool,
    master_key: str | None = FIXED_MASTER_KEY,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-restore-home"
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
        "restore",
        str(source),
    ]
    if plaintext:
        arguments.append("--plaintext")
    if apply:
        arguments.append("--apply")
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


def _prepare_target(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "config.json").write_text('{"enabled":false}\n', encoding="utf-8")
    nested = state / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "data.txt").write_text("stale target payload\n", encoding="utf-8")
    (state / "extra.txt").write_text("must survive restore\n", encoding="utf-8")
    with sqlite3.connect(state / "runtime.sqlite3") as connection:
        connection.execute("CREATE TABLE old_records(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO old_records(value) VALUES('before-restore')")


def _explicit_snapshot(state: Path) -> dict[str, str]:
    paths = (
        "config.json",
        "nested/data.txt",
        "extra.txt",
        "runtime.sqlite3",
    )
    return {
        relative: hashlib.sha256((state / relative).read_bytes()).hexdigest()
        for relative in paths
    }


def _extract_plaintext(archive: Path, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r") as handle:
        handle.extractall(destination, filter="data")
    return json.loads((destination / "BACKUP_MANIFEST.json").read_text(encoding="utf-8"))


def _assert_state_matches_manifest(state: Path, extracted: Path, manifest: dict[str, Any]) -> None:
    for relative, expected in manifest["files"].items():
        restored = state / relative
        assert restored.is_file(), relative
        assert restored.read_bytes() == (extracted / relative).read_bytes(), relative
        assert hashlib.sha256(restored.read_bytes()).hexdigest() == expected["sha256"]
    assert (state / "extra.txt").read_text(encoding="utf-8") == "must survive restore\n"
    assert not list(state.rglob("*.restore-tmp"))
    with sqlite3.connect(state / "runtime.sqlite3") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM records").fetchall() == [
            ("sqlite-backup-ok",)
        ]


def _inspect_rollback(rollback: Path, project: Path, destination: Path) -> None:
    archive = destination / "rollback.tar"
    open_sealed_file(
        rollback,
        archive,
        master_key=base64.b64decode(FIXED_MASTER_KEY),
        project_id=stable_project_id(project),
    )
    extracted = destination / "rollback"
    extracted.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r") as handle:
        handle.extractall(extracted, filter="data")
    assert (extracted / "config.json").read_text(encoding="utf-8") == '{"enabled":false}\n'
    assert (extracted / "nested/data.txt").read_text(encoding="utf-8") == "stale target payload\n"
    assert (extracted / "extra.txt").read_text(encoding="utf-8") == "must survive restore\n"
    with sqlite3.connect(extracted / "runtime.sqlite3") as connection:
        assert connection.execute("SELECT value FROM old_records").fetchall() == [
            ("before-restore",)
        ]


def test_native_backup_restore_plaintext_dry_run_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source_state = tmp_path / "source-state"
    archive = tmp_path / "source.tar"
    created = _create("python", project, source_state, archive, plaintext=True)
    expected = {
        "ok": True,
        "dry_run": True,
        "files": created["files"],
        "failures": [],
    }
    for engine in ("python", "rust"):
        target = tmp_path / f"{engine}-dry-target"
        _prepare_target(target)
        initialized = _run_verify(
            engine,
            project,
            target,
            archive,
            plaintext=True,
        )
        assert initialized.returncode == 0, (initialized.stdout, initialized.stderr)
        before = _explicit_snapshot(target)
        completed = _run_restore(
            engine,
            project,
            target,
            archive,
            plaintext=True,
            apply=False,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert completed.stderr == ""
        assert json.loads(completed.stdout) == expected
        assert _explicit_snapshot(target) == before
        assert not list((target / "backups").glob("pre-restore-*.scbackup"))


def test_native_backup_restore_plaintext_apply_and_rollback_match_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    source_state = tmp_path / "source-state"
    archive = tmp_path / "source.tar"
    created = _create("python", project, source_state, archive, plaintext=True)
    expected_root = tmp_path / "expected"
    manifest = _extract_plaintext(archive, expected_root)

    results: dict[str, dict[str, Any]] = {}
    for engine in ("python", "rust"):
        target = tmp_path / f"{engine}-apply-target"
        _prepare_target(target)
        (target / "config.json.restore-tmp").write_text("stale temp\n", encoding="utf-8")
        completed = _run_restore(
            engine,
            project,
            target,
            archive,
            plaintext=True,
            apply=True,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert completed.stderr == ""
        value = json.loads(completed.stdout)
        assert value["ok"] is True
        assert value["dry_run"] is False
        assert value["restored"] == created["files"]
        rollback = Path(value["rollback"])
        assert rollback.parent == target / "backups"
        assert rollback.name.startswith("pre-restore-")
        assert rollback.suffix == ".scbackup"
        assert rollback.is_file()
        verified = _run_verify(
            engine,
            project,
            target,
            rollback,
            plaintext=False,
        )
        assert verified.returncode == 0, (verified.stdout, verified.stderr)
        assert json.loads(verified.stdout)["ok"] is True
        _inspect_rollback(rollback, project, tmp_path / f"{engine}-rollback-inspect")
        _assert_state_matches_manifest(target, expected_root, manifest)
        results[engine] = {key: value[key] for key in ("ok", "dry_run", "restored")}
    assert results["rust"] == results["python"]


def test_native_backup_restore_encrypted_cross_engine_apply(tmp_path: Path) -> None:
    project = tmp_path / "project"
    for creator, restorer in (("python", "rust"), ("rust", "python")):
        source_state = tmp_path / f"{creator}-encrypted-source"
        archive = tmp_path / f"{creator}.scbackup"
        created = _create(creator, project, source_state, archive, plaintext=False)
        target = tmp_path / f"{restorer}-encrypted-target"
        _prepare_target(target)
        completed = _run_restore(
            restorer,
            project,
            target,
            archive,
            plaintext=False,
            apply=True,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        value = json.loads(completed.stdout)
        assert value["ok"] is True
        assert value["dry_run"] is False
        assert value["restored"] == created["files"]
        assert (target / "config.json").read_text(encoding="utf-8") == '{"enabled": true}\n'
        assert (target / "nested/data.txt").read_text(encoding="utf-8") == "exact backup payload\n"
        with sqlite3.connect(target / "runtime.sqlite3") as connection:
            assert connection.execute("SELECT value FROM records").fetchall() == [
                ("sqlite-backup-ok",)
            ]


def test_native_backup_restore_python_local_key_dry_run(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "local-key-state"
    archive = tmp_path / "local-key.scbackup"
    created = _create(
        "python",
        project,
        state,
        archive,
        plaintext=False,
        master_key=None,
    )
    completed = _run_restore(
        "rust",
        project,
        state,
        archive,
        plaintext=False,
        apply=False,
        master_key=None,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert json.loads(completed.stdout) == {
        "ok": True,
        "dry_run": True,
        "files": created["files"],
        "failures": [],
    }


def test_native_backup_restore_rejects_failed_verification_before_mutation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    source_state = tmp_path / "source-state"
    original = tmp_path / "original.tar"
    _create("python", project, source_state, original, plaintext=True)
    tampered = tmp_path / "tampered.tar"

    def mutate(root: Path) -> None:
        (root / "config.json").write_text('{"enabled":"tampered"}\n', encoding="utf-8")

    _rewrite_plaintext_archive(
        original,
        tampered,
        tmp_path / "tampered-extract",
        mutate=mutate,
    )
    for engine in ("python", "rust"):
        target = tmp_path / f"{engine}-failure-target"
        _prepare_target(target)
        initialized = _run_verify(
            engine,
            project,
            target,
            original,
            plaintext=True,
        )
        assert initialized.returncode == 0, (initialized.stdout, initialized.stderr)
        before = _explicit_snapshot(target)
        completed = _run_restore(
            engine,
            project,
            target,
            tampered,
            plaintext=True,
            apply=True,
        )
        assert completed.returncode != 0
        assert _explicit_snapshot(target) == before
        assert not list((target / "backups").glob("pre-restore-*.scbackup"))
        assert not list(target.rglob("*.restore-tmp"))
