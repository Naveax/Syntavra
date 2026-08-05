from __future__ import annotations

import json
from pathlib import Path

from tests.runtime.test_native_status_r38 import _run


def test_python_default_status_language_inventory_diagnostic(tmp_path: Path) -> None:
    project = tmp_path / "python-project"
    project.mkdir()
    code, value, stderr = _run("python", project)
    assert code == 0
    assert stderr == ""
    language = value["platform"]["language_platform"]
    diagnostic = {
        "languages": language["language_registry"]["languages"],
        "adapters": language["language_registry"]["adapters"],
        "tree_sitter_available": language["tree_sitter"]["available_languages"],
    }
    raise AssertionError("R38_STATUS_LANGUAGE_INVENTORY=" + json.dumps(diagnostic, sort_keys=True))
