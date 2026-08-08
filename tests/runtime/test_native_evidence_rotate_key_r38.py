from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.util import stable_project_id
from tests.runtime.test_native_backup_create_r38 import _environment
from tests.runtime.test_native_install_r38 import _selector_binary

ROOT = Path(__file__).resolve().parents[2]


def _run(
    engine: str,
    project: Path,
    state: Path,
    command: list[str],
    *,
    key: str | None = None,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home = state.parent / f"{state.name}-{engine}-evidence-rotate-home"
    home.mkdir(parents=True, exist_ok=True)
    env = _environment(home, None)
    if key is not None:
        env["SYNTAVRA_EVIDENCE_KEY"] = key
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    return subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(state),
            *command,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=env,
    )


def _json(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    return json.loads(result.stdout)


def _seed(
    project: Path,
    state: Path,
    payloads: list[bytes],
    *,
    key: str | None = None,
) -> list[str]:
    project.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(
        state / "evidence",
        project_id=stable_project_id(project),
        master_key=bytes.fromhex(key) if key is not None else None,
    )
    return [
        store.put(payload, kind="rotation-test", metadata={"index": index})
        for index, payload in enumerate(payloads)
    ]


def _digest(handle: str) -> str:
    return handle.removeprefix("sc://sha256/")


def _object(state: Path, handle: str) -> Path:
    digest = _digest(handle)
    return state / "evidence" / "objects" / digest[:2] / digest[2:]


def _metadata(state: Path, handle: str) -> dict[str, Any]:
    path = state / "evidence" / "metadata" / f"{_digest(handle)}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _active(state: Path) -> dict[str, Any]:
    return json.loads(
        (state / "evidence" / "keys" / "active.json").read_text(encoding="utf-8")
    )


def _rows(state: Path) -> list[tuple[str, int, int]]:
    with sqlite3.connect(state / "evidence" / "evidence.sqlite3") as db:
        return [
            (str(digest), int(version), int(size))
            for digest, version, size in db.execute(
                "SELECT digest,key_version,stored_bytes FROM evidence_objects ORDER BY digest"
            ).fetchall()
        ]


def _assert_version(state: Path, handles: list[str], version: int) -> None:
    assert _active(state)["active_version"] == version
    row_versions = {digest: key_version for digest, key_version, _ in _rows(state)}
    for handle in handles:
        digest = _digest(handle)
        raw = _object(state, handle).read_bytes()
        assert raw[:6] == b"SCEV1\0"
        assert int.from_bytes(raw[6:10], "big") == version
        assert _metadata(state, handle)["encryption"]["key_version"] == version
        assert row_versions[digest] == version
    assert not list((state / "evidence").rglob("*.rotate-*"))
    assert not list((state / "evidence").rglob("*.rotate-backup"))


def _read(
    engine: str,
    project: Path,
    state: Path,
    handle: str,
    destination: Path,
    *,
    key: str | None = None,
) -> bytes:
    value = _json(
        _run(
            engine,
            project,
            state,
            ["evidence", "get", handle, "--output", str(destination)],
            key=key,
        )
    )
    assert value["handle"] == handle
    assert value["bytes"] == destination.stat().st_size
    return destination.read_bytes()


def test_native_evidence_rotate_key_empty_store_matches_python(tmp_path: Path) -> None:
    project = tmp_path / "project"
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    python_value = _json(
        _run("python", project, python_state, ["evidence", "rotate-key"])
    )
    rust_value = _json(_run("rust", project, rust_state, ["evidence", "rotate-key"]))
    assert rust_value == python_value == {
        "ok": True,
        "previous_key_version": 1,
        "active_key_version": 2,
        "reencrypt": True,
        "objects": 0,
        "reencrypted": 0,
        "skipped": 0,
        "stored_bytes": 0,
    }
    for state in (python_state, rust_state):
        assert _active(state)["active_version"] == 2
        assert (state / "evidence" / "keys" / "master-v1.key").stat().st_size == 32
        assert (state / "evidence" / "keys" / "master-v2.key").stat().st_size == 32


def test_native_evidence_rotate_key_exact_output_and_cross_engine_reads(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    payloads = [b"alpha", bytes(range(256)) * 32, "unicode β evidence".encode()]
    python_state = tmp_path / "python-state"
    rust_state = tmp_path / "rust-state"
    python_handles = _seed(project, python_state, payloads)
    rust_handles = _seed(project, rust_state, payloads)
    python_before = [_object(python_state, handle).read_bytes() for handle in python_handles]
    rust_before = [_object(rust_state, handle).read_bytes() for handle in rust_handles]

    python_value = _json(
        _run("python", project, python_state, ["evidence", "rotate-key"])
    )
    rust_value = _json(_run("rust", project, rust_state, ["evidence", "rotate-key"]))
    assert rust_value == python_value
    assert rust_value["objects"] == 3
    assert rust_value["reencrypted"] == 3
    assert rust_value["skipped"] == 0
    assert rust_value["stored_bytes"] == sum(
        _object(rust_state, handle).stat().st_size for handle in rust_handles
    )
    _assert_version(python_state, python_handles, 2)
    _assert_version(rust_state, rust_handles, 2)
    assert python_before != [_object(python_state, handle).read_bytes() for handle in python_handles]
    assert rust_before != [_object(rust_state, handle).read_bytes() for handle in rust_handles]

    for index, payload in enumerate(payloads):
        assert _read(
            "rust",
            project,
            python_state,
            python_handles[index],
            tmp_path / f"python-to-rust-{index}.bin",
        ) == payload
        assert _read(
            "python",
            project,
            rust_state,
            rust_handles[index],
            tmp_path / f"rust-to-python-{index}.bin",
        ) == payload


def test_native_evidence_rotate_key_repeated_cross_engine_rotation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    payloads = [b"first", b"second"]
    handles = _seed(project, state, payloads)
    first = _json(_run("rust", project, state, ["evidence", "rotate-key"]))
    second = _json(_run("python", project, state, ["evidence", "rotate-key"]))
    assert first["previous_key_version"] == 1
    assert first["active_key_version"] == 2
    assert second["previous_key_version"] == 2
    assert second["active_key_version"] == 3
    assert second["objects"] == second["reencrypted"] == 2
    _assert_version(state, handles, 3)
    for version in (1, 2, 3):
        assert (state / "evidence" / "keys" / f"master-v{version}.key").stat().st_size == 32
    for index, payload in enumerate(payloads):
        assert _read(
            "rust",
            project,
            state,
            handles[index],
            tmp_path / f"repeated-{index}.bin",
        ) == payload


def test_native_evidence_rotate_key_managed_mode_fails_before_mutation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    key = "11" * 32
    for engine in ("python", "rust"):
        state = tmp_path / f"{engine}-state"
        handle = _seed(project, state, [b"managed evidence"], key=key)[0]
        object_before = _object(state, handle).read_bytes()
        metadata_before = _metadata(state, handle)
        rows_before = _rows(state)
        result = _run(
            engine,
            project,
            state,
            ["evidence", "rotate-key"],
            key=key,
        )
        assert result.returncode != 0
        assert _active(state)["active_version"] == 1
        assert not (state / "evidence" / "keys" / "master-v2.key").exists()
        assert _object(state, handle).read_bytes() == object_before
        assert _metadata(state, handle) == metadata_before
        assert _rows(state) == rows_before
        assert _read(
            engine,
            project,
            state,
            handle,
            tmp_path / f"{engine}-managed.bin",
            key=key,
        ) == b"managed evidence"


def test_native_evidence_rotate_key_corruption_advances_key_but_preserves_object_state(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    for engine in ("python", "rust"):
        state = tmp_path / f"{engine}-state"
        handle = _seed(project, state, [b"authenticated evidence"])[0]
        path = _object(state, handle)
        corrupted = bytearray(path.read_bytes())
        corrupted[-1] ^= 1
        path.write_bytes(corrupted)
        metadata_before = _metadata(state, handle)
        rows_before = _rows(state)
        result = _run(engine, project, state, ["evidence", "rotate-key"])
        assert result.returncode != 0
        assert _active(state)["active_version"] == 2
        assert (state / "evidence" / "keys" / "master-v2.key").stat().st_size == 32
        assert path.read_bytes() == bytes(corrupted)
        assert _metadata(state, handle) == metadata_before
        assert _rows(state) == rows_before
        assert not list((state / "evidence").rglob("*.rotate-*"))
        assert not list((state / "evidence").rglob("*.rotate-backup"))


def test_native_evidence_rotate_key_replaces_stale_rotation_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "state"
    handle = _seed(project, state, [b"stale rotation cleanup"])[0]
    object_path = _object(state, handle)
    name = object_path.name
    stale_stage = object_path.with_name(f".{name}.rotate-2")
    stale_backup = object_path.with_name(f".{name}.rotate-backup")
    stale_stage.write_bytes(b"stale-stage")
    stale_backup.write_bytes(b"stale-backup")
    value = _json(_run("rust", project, state, ["evidence", "rotate-key"]))
    assert value["reencrypted"] == 1
    assert not stale_stage.exists()
    assert not stale_backup.exists()
    _assert_version(state, [handle], 2)
    assert _read(
        "python",
        project,
        state,
        handle,
        tmp_path / "stale-cleanup.bin",
    ) == b"stale rotation cleanup"
