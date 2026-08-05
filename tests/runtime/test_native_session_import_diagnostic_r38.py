from __future__ import annotations

from pathlib import Path

from tests.runtime.test_native_session_public_r38 import _run, _write_export


def test_native_session_import_reports_full_failure_details(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    export_path = tmp_path / "session.json"
    _write_export(project, export_path)
    state = tmp_path / "rust-state"
    code, result, stderr = _run(
        "rust",
        project,
        state,
        "session",
        "import",
        "--input",
        str(export_path),
        "--session-id=imported-session",
    )
    assert code == 0, {
        "code": code,
        "result": result,
        "stderr": stderr,
    }
