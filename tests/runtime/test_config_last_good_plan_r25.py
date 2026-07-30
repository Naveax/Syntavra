from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from syntavra_runtime.config_contract import encode_config_wire
from syntavra_runtime.config_last_good_plan import (
    CLAIM,
    ConfigLastGoodPlanError,
    canonical_plan_json,
    config_last_good_plan,
)
from syntavra_runtime.state_snapshot_contract import project_id_for_root


def _tree(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
        )
    )


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    return project, project_id_for_root(project)


def test_valid_persistent_config_produces_write_plan_without_state(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire(
        [
            {
                "project": {
                    "runtime": {"profile": "compact"},
                    "routing": {"budget_bytes": 4096},
                }
            }
        ]
    )
    before = _tree(project)

    plan = config_last_good_plan(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )

    assert plan["ok"] is True
    assert plan["decision"] == "write"
    assert plan["fallback_used"] is False
    assert plan["claim"] == CLAIM
    assert plan["apply_authority"] == "blocked"
    assert plan["target"]["relative_path"] == ".syntavra/pre-release/config-last-good.json"
    assert plan["mutation"] == {"database_opened": False, "filesystem": False}
    assert plan["candidate"]["payload_bytes"] > 0
    assert len(plan["candidate"]["payload_sha256"]) == 64
    assert _tree(project) == before
    rendered = canonical_plan_json(plan)
    assert str(project) not in rendered
    assert wire.hex() not in rendered
    assert "loaded_at" not in rendered


def test_invalid_current_phase_retains_prior_last_good(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire(
        [
            {"project": {"runtime": {"profile": "compact"}}},
            {"project": {"runtime": {"profile": "not-a-profile"}}},
        ]
    )

    plan = config_last_good_plan(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )

    assert plan["decision"] == "retain-existing"
    assert plan["fallback_used"] is True
    assert plan["candidate"]["warnings"] == [
        "invalid-current-config-fell-back:ConfigError"
    ]
    assert not (project / ".syntavra").exists()


@pytest.mark.parametrize("scope", ["session", "task"])
def test_ephemeral_overrides_are_forbidden_from_persistent_plan(
    tmp_path: Path,
    scope: str,
) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire([{scope: {"runtime": {"profile": "terse"}}}])

    with pytest.raises(ConfigLastGoodPlanError) as captured:
        config_last_good_plan(
            project_root=project,
            expected_project_id=project_id,
            config_wire=wire,
        )

    assert captured.value.code == "CONFIG_LIFECYCLE_EPHEMERAL_SCOPE_FORBIDDEN"
    assert not (project / ".syntavra").exists()


def test_project_mismatch_is_fail_closed(tmp_path: Path) -> None:
    project, _ = _project(tmp_path)
    wire = encode_config_wire([{}])

    with pytest.raises(ConfigLastGoodPlanError) as captured:
        config_last_good_plan(
            project_root=project,
            expected_project_id="0" * 64,
            config_wire=wire,
        )

    assert captured.value.code == "CONFIG_LIFECYCLE_PROJECT_MISMATCH"


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privileges are environment-dependent")
def test_project_root_symlink_is_rejected(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    alias = tmp_path / "project-link"
    alias.symlink_to(project, target_is_directory=True)
    wire = encode_config_wire([{}])

    with pytest.raises(ConfigLastGoodPlanError) as captured:
        config_last_good_plan(
            project_root=alias,
            expected_project_id=project_id,
            config_wire=wire,
        )

    assert captured.value.code == "CONFIG_LIFECYCLE_PROJECT_ROOT_SYMLINK"


def test_plan_json_is_deterministic(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire([{"project": {"runtime": {"profile": "audit"}}}])

    first = config_last_good_plan(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )
    second = config_last_good_plan(
        project_root=project,
        expected_project_id=project_id,
        config_wire=wire,
    )

    assert first == second
    assert json.loads(canonical_plan_json(first)) == first
