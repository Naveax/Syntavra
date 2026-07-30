from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from syntavra_runtime.config_contract import (
    decode_config_wire,
    encode_config_wire,
    resolve_config_phases,
)
from syntavra_runtime.config_last_good_apply import (
    CLAIM,
    LOCK_RELATIVE_PATH,
    TEMP_RELATIVE_PATH,
    ConfigLastGoodApplyError,
    apply_config_last_good,
    canonical_apply_json,
)
from syntavra_runtime.state_snapshot_contract import project_id_for_root


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    return project, project_id_for_root(project)


def _write_wire(profile: str = "compact") -> bytes:
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


def test_writes_canonical_last_good_and_is_idempotent(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = _write_wire()

    first = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )
    second = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )

    target = _target(project)
    assert first["ok"] is True
    assert first["claim"] == CLAIM
    assert first["result"]["action"] == "write"
    assert first["mutation"]["filesystem"] is True
    assert second["result"]["action"] == "already-current"
    assert second["mutation"]["filesystem"] is False
    assert target.read_bytes().endswith(b"\n")
    assert json.loads(target.read_text(encoding="utf-8"))["config_hash"] == first["result"]["config_hash"]
    assert not (project / LOCK_RELATIVE_PATH).exists()
    assert not (project / TEMP_RELATIVE_PATH).exists()
    assert json.loads(canonical_apply_json(first)) == first


def test_replaces_an_older_valid_last_good(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    compact = _write_wire("compact")
    detailed = _write_wire("detailed")

    first = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=compact,
    )
    second = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=detailed,
    )

    assert first["result"]["config_hash"] != second["result"]["config_hash"]
    assert second["result"]["action"] == "write"
    assert json.loads(_target(project).read_text(encoding="utf-8"))["values"]["runtime"]["profile"] == "detailed"


def test_retain_existing_accepts_legacy_loaded_at_payload(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire(
        [
            {"project": {"runtime": {"profile": "compact"}}},
            {"project": {"runtime": {"profile": "invalid-profile"}}},
        ]
    )
    snapshot = resolve_config_phases(decode_config_wire(wire))
    legacy = dict(snapshot)
    legacy["loaded_at"] = 123.5
    target = _target(project)
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    receipt = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )

    assert receipt["decision"] == "retain-existing"
    assert receipt["result"]["action"] == "retain-existing"
    assert receipt["mutation"]["filesystem"] is False
    assert "loaded_at" in json.loads(target.read_text(encoding="utf-8"))


def test_retain_missing_target_fails_without_creating_state(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire(
        [
            {"project": {"runtime": {"profile": "compact"}}},
            {"project": {"runtime": {"profile": "invalid-profile"}}},
        ]
    )

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
        )

    assert captured.value.code == "CONFIG_LIFECYCLE_RETAIN_TARGET_MISSING"
    assert not (project / ".syntavra").exists()


@pytest.mark.parametrize("scope", ["session", "task"])
def test_ephemeral_scope_is_rejected_before_state_creation(
    tmp_path: Path,
    scope: str,
) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire([{scope: {"runtime": {"profile": "terse"}}}])

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
        )

    assert captured.value.code == "CONFIG_LIFECYCLE_EPHEMERAL_SCOPE_FORBIDDEN"
    assert not (project / ".syntavra").exists()


def test_crash_after_temp_sync_recovers_stale_lock_and_promotes_temp(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = _write_wire("audit")

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
            fault="after-temp-sync",
        )

    assert captured.value.code == "CONFIG_LIFECYCLE_FAULT_INJECTED_AFTER_TEMP_SYNC"
    lock = project / LOCK_RELATIVE_PATH
    temp = project / TEMP_RELATIVE_PATH
    assert lock.is_file()
    assert temp.is_file()
    os.utime(lock, (0, 0))

    receipt = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )

    assert receipt["result"]["action"] == "recover-temp"
    assert receipt["lock"]["stale_recovered"] is True
    assert _target(project).is_file()
    assert not lock.exists()
    assert not temp.exists()


def test_crash_after_replace_recovers_as_already_current(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = _write_wire("terse")

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
            fault="after-replace",
        )

    assert captured.value.code == "CONFIG_LIFECYCLE_FAULT_INJECTED_AFTER_REPLACE"
    lock = project / LOCK_RELATIVE_PATH
    assert lock.is_file()
    assert _target(project).is_file()
    os.utime(lock, (0, 0))

    receipt = apply_config_last_good(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )

    assert receipt["result"]["action"] == "already-current"
    assert receipt["lock"]["stale_recovered"] is True


def test_live_lock_is_fail_closed(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = _write_wire()
    lock = project / LOCK_RELATIVE_PATH
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "project_id": project_id,
                "target": ".syntavra/pre-release/config-last-good.json",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigLastGoodApplyError) as captured:
        apply_config_last_good(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
        )

    assert captured.value.code == "CONFIG_LIFECYCLE_LOCK_HELD"
