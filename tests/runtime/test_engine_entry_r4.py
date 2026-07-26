from __future__ import annotations

import json
from pathlib import Path

from syntavra_runtime.engine_entry import main


def _payload(capsys):
    return json.loads(capsys.readouterr().out)


def test_engine_status_defaults_to_python(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("SYNTAVRA_ENGINE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    code = main(["--project", str(tmp_path), "engine", "status"])
    value = _payload(capsys)
    assert code == 0
    assert value["selection"]["resolved"] == "python"
    assert value["routing"]["rust"] == "general-command-routing-blocked-until-R5+"


def test_engine_use_auto_writes_project_scope(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("SYNTAVRA_ENGINE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    code = main(["--project", str(tmp_path), "engine", "use", "auto"])
    value = _payload(capsys)
    assert code == 0
    assert value["persisted"]["engine"] == "auto"
    assert json.loads((tmp_path / ".syntavra" / "engine.json").read_text(encoding="utf-8"))["engine"] == "auto"


def test_invalid_environment_selection_stops_before_python_dispatch(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SYNTAVRA_ENGINE", "unknown")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    code = main(["--project", str(tmp_path), "status"])
    value = _payload(capsys)
    assert code == 4
    assert value["error"]["code"] == "ENGINE_SELECTION_INVALID"


def test_explicit_missing_rust_binary_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SYNTAVRA_RUST_BIN", str(tmp_path / "missing-rust"))
    code = main(["--project", str(tmp_path), "--engine", "rust", "status"])
    value = _payload(capsys)
    assert code == 4
    assert value["error"]["code"] == "RUST_ENGINE_UNAVAILABLE"


def test_duplicate_engine_override_is_rejected(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    code = main([
        "--project", str(tmp_path),
        "--engine", "python",
        "status",
        "--engine=auto",
    ])
    value = _payload(capsys)
    assert code == 4
    assert value["error"]["code"] == "ENGINE_OVERRIDE_DUPLICATE"
