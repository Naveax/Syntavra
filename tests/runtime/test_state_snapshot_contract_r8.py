from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from syntavra_runtime.state_snapshot_contract import (
    StateInspectionError,
    inspect_state_root,
    project_id_for_root,
)


def _populate(root: Path) -> None:
    state = root / ".syntavra"
    state.mkdir()
    (state / "config.toml").write_text("mode = \"safe\"\n", encoding="utf-8")
    (state / "engine.json").write_text('{"engine":"python"}\n', encoding="utf-8")
    (state / "pre-release").mkdir()
    (state / "runtime-v3").mkdir()


def _tree_snapshot(root: Path) -> list[tuple[str, int, int, bytes | None]]:
    rows: list[tuple[str, int, int, bytes | None]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        payload = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        rows.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_mode,
                metadata.st_mtime_ns,
                payload,
            )
        )
    return rows


def test_empty_project_reports_missing_state_without_mutation(tmp_path: Path) -> None:
    project_id = project_id_for_root(tmp_path)
    before = _tree_snapshot(tmp_path)

    value = inspect_state_root(tmp_path, expected_project_id=project_id)

    assert value["ok"] is True
    assert value["project_id"] == project_id
    assert [row["observed_kind"] for row in value["paths"]] == ["missing"] * 5
    assert value["mutation"] == {"filesystem": False, "database_opened": False}
    assert _tree_snapshot(tmp_path) == before


def test_populated_project_has_deterministic_inventory(tmp_path: Path) -> None:
    _populate(tmp_path)
    project_id = project_id_for_root(tmp_path)
    before = _tree_snapshot(tmp_path)

    value = inspect_state_root(tmp_path, expected_project_id=project_id)
    rows = {row["id"]: row for row in value["paths"]}

    assert rows["state-root"]["observed_kind"] == "directory"
    assert rows["pre-release-state"]["observed_kind"] == "directory"
    assert rows["runtime-v3-state"]["observed_kind"] == "directory"
    config = b'mode = "safe"\n'
    engine = b'{"engine":"python"}\n'
    assert rows["project-config"]["size_bytes"] == len(config)
    assert rows["project-config"]["sha256"] == hashlib.sha256(config).hexdigest()
    assert rows["engine-selection"]["size_bytes"] == len(engine)
    assert rows["engine-selection"]["sha256"] == hashlib.sha256(engine).hexdigest()
    assert _tree_snapshot(tmp_path) == before


def test_expected_project_binding_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(StateInspectionError, match="STATE_EXPECTED_PROJECT_INVALID"):
        inspect_state_root(tmp_path, expected_project_id="not-a-hash")

    wrong = "0" * 64
    if project_id_for_root(tmp_path) == wrong:
        wrong = "1" * 64
    with pytest.raises(StateInspectionError, match="STATE_PROJECT_MISMATCH"):
        inspect_state_root(tmp_path, expected_project_id=wrong)


def test_known_path_kind_mismatch_fails_closed(tmp_path: Path) -> None:
    state = tmp_path / ".syntavra"
    state.mkdir()
    (state / "config.toml").mkdir()

    with pytest.raises(StateInspectionError, match="STATE_PATH_KIND_MISMATCH"):
        inspect_state_root(tmp_path, expected_project_id=project_id_for_root(tmp_path))


def test_bounded_file_read_fails_closed(tmp_path: Path) -> None:
    state = tmp_path / ".syntavra"
    state.mkdir()
    (state / "config.toml").write_bytes(b"x" * (1024 * 1024 + 1))

    with pytest.raises(StateInspectionError, match="STATE_FILE_SIZE_LIMIT"):
        inspect_state_root(tmp_path, expected_project_id=project_id_for_root(tmp_path))


def test_symlinked_state_path_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / ".syntavra"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(StateInspectionError, match="STATE_PATH_SYMLINK"):
        inspect_state_root(tmp_path, expected_project_id=project_id_for_root(tmp_path))
