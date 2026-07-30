from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from syntavra_runtime.config_contract import encode_config_wire
from syntavra_runtime.config_last_good_apply import (
    CLAIM,
    ConfigLastGoodApplyError,
    apply_config_last_good,
)
from syntavra_runtime.state_snapshot_contract import project_id_for_root


def _project(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "project"
    root.mkdir()
    return root, project_id_for_root(root)


def _wire(profile: str = "compact") -> bytes:
    return encode_config_wire(
        [
            {
                "project": {
                    "runtime": {"profile": profile},
                    "routing": {"budget_bytes": 4096},
                }
            }
        ]
    )


def _target(project: Path) -> Path:
    return project / ".syntavra" / "pre-release" / "config-last-good.json"


def test_write_creates_canonical_private_last_good_file(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)

    result = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=_wire(),
    )

    target = _target(project)
    payload = target.read_bytes()
    assert result["ok"] is True
    assert result["claim"] == CLAIM
    assert result["decision"] == "write"
    assert result["action"] == "written"
    assert result["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["target_sha256"] == result["payload_sha256"]
    assert result["mutation"]["target_replaced"] is True
    assert result["mutation"]["temporary_created"] is True
    assert not (target.parent / "config-last-good.lock").exists()
    assert not tuple(target.parent.glob(".config-last-good.*.tmp"))
    decoded = json.loads(payload)
    assert decoded["values"]["runtime"]["profile"] == "compact"
    assert "loaded_at" not in decoded
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def test_second_equal_apply_is_unchanged(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    first = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=_wire("balanced"),
    )
    target = _target(project)
    before = (target.read_bytes(), target.stat().st_mtime_ns)

    second = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=_wire("balanced"),
    )

    assert first["action"] == "written"
    assert second["action"] == "unchanged"
    assert second["mutation"]["target_replaced"] is False
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert not (target.parent / "config-last-good.lock").exists()


def test_retain_existing_decision_creates_no_state(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire(
        [
            {"project": {"runtime": {"profile": "compact"}}},
            {"project": {"runtime": {"profile": "invalid"}}},
        ]
    )

    result = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )

    assert result["decision"] == "retain-existing"
    assert result["action"] == "retained"
    assert result["mutation"] == {
        "directory_created": False,
        "directory_synced": False,
        "lock_created": False,
        "target_replaced": False,
        "temporary_created": False,
    }
    assert not (project / ".syntavra").exists()


def test_existing_lock_fails_without_touching_target(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    parent = project / ".syntavra" / "pre-release"
    parent.mkdir(parents=True)
    lock = parent / "config-last-good.lock"
    lock.write_text("owned\n", encoding="utf-8")

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=_wire(),
        )

    assert captured.value.code == "CONFIG_LAST_GOOD_APPLY_LOCK_BUSY"
    assert lock.read_text(encoding="utf-8") == "owned\n"
    assert not _target(project).exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privileges are environment-dependent")
def test_target_symlink_fails_closed(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    parent = project / ".syntavra" / "pre-release"
    parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    _target(project).symlink_to(outside)

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=_wire(),
        )

    assert captured.value.code == "CONFIG_LAST_GOOD_APPLY_TARGET_SYMLINK"
    assert outside.read_text(encoding="utf-8") == "outside"
    assert not (parent / "config-last-good.lock").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privileges are environment-dependent")
def test_parent_symlink_fails_closed(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".syntavra").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=_wire(),
        )

    assert captured.value.code == "CONFIG_LAST_GOOD_APPLY_PARENT_SYMLINK"
    assert tuple(outside.iterdir()) == ()


def test_project_mismatch_fails_before_state_creation(tmp_path: Path) -> None:
    project, _ = _project(tmp_path)

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id="0" * 64,
            config_wire=_wire(),
        )

    assert captured.value.code == "CONFIG_LAST_GOOD_APPLY_PROJECT_MISMATCH"
    assert not (project / ".syntavra").exists()
