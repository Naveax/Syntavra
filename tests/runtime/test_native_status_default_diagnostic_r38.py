from __future__ import annotations

import json
from pathlib import Path

from tests.runtime.test_native_status_r38 import _run


def _shape(value: object) -> object:
    if isinstance(value, dict):
        return {key: _shape(row) for key, row in value.items()}
    if isinstance(value, list):
        if len(value) <= 20:
            return [_shape(row) for row in value]
        return {"length": len(value), "first": [_shape(row) for row in value[:3]]}
    return value


def test_python_default_status_contract_diagnostic(tmp_path: Path) -> None:
    project = tmp_path / "python-project"
    project.mkdir()
    code, value, stderr = _run("python", project)
    assert code == 0
    assert stderr == ""
    competitive = dict(value["competitive_features"])
    groups = competitive.pop("feature_groups")
    platform = value["platform"]
    diagnostic = {
        "competitive": competitive,
        "competitive_group_lengths": {key: len(rows) for key, rows in groups.items()},
        "proxy_presets": value["proxy_presets"],
        "platform": {
            "top_keys": sorted(platform),
            "artifacts": platform["artifacts"],
            "semantic_graph": platform["semantic_graph"],
            "runtime_evidence": platform["runtime_evidence"],
            "language_platform": _shape(platform["language_platform"]),
            "memory": platform["memory"],
            "headless": platform["headless"],
            "sandbox": _shape(platform["sandbox"]),
            "adapters": platform["adapters"],
            "providers": platform["providers"],
            "capabilities": platform["capabilities"],
            "claim_boundary": platform["claim_boundary"],
        },
    }
    raise AssertionError("R38_STATUS_DEFAULT_DIAGNOSTIC=" + json.dumps(diagnostic, sort_keys=True))
