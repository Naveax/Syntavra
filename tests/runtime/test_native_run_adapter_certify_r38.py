from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.runtime.test_native_install_r38 import _selector_binary
from tests.runtime.test_native_run_adapter_conformance_r38 import (
    _assert_hash,
    _receipt_files,
    _state_shape,
)

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_ID = "codex-cli"
VALID_RECEIPT: dict[str, Any] = {
    "host": "codex",
    "host_version": "1.0.0",
    "clean_install": True,
    "tool_interception": True,
    "context_interception": True,
    "security_denial": True,
    "session_restore": True,
    "artifact_hash": "sha256:" + ("a" * 64),
}


def _run(
    engine: str,
    project: Path,
    home: Path,
    receipt: Any,
    *,
    adapter_id: str = ADAPTER_ID,
    from_file: bool = False,
) -> subprocess.CompletedProcess[str]:
    project.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    prefix = (
        [sys.executable, "-m", "syntavra_runtime.engine_entry"]
        if engine == "python"
        else [str(_selector_binary())]
    )
    rendered = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    if from_file:
        source = project / "external-receipt.json"
        source.write_text(rendered + "\n", encoding="utf-8", newline="\n")
        rendered = str(source)
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["PATH"] = ""
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            *prefix,
            "--engine",
            engine,
            "--project",
            str(project),
            "--state-root",
            str(project / "state"),
            "run",
            "adapter-certify",
            adapter_id,
            rendered,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        timeout=600,
        env=environment,
    )


def _normalize(value: Any, *, project: Path, home: Path) -> Any:
    if isinstance(value, dict):
        output = {
            key: _normalize(item, project=project, home=home)
            for key, item in value.items()
        }
        if "created_at" in output:
            output["created_at"] = "<created-at>"
        if "receipt_id" in output:
            output["receipt_id"] = "<receipt-id>"
        return output
    if isinstance(value, list):
        return [_normalize(item, project=project, home=home) for item in value]
    if isinstance(value, str):
        return value.replace(str(project), "<project>").replace(str(home), "<home>")
    return value


def _pair(
    tmp_path: Path,
    receipt: Any,
    *,
    from_file: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, Path, Path]:
    python_project = tmp_path / "python-project"
    rust_project = tmp_path / "rust-project"
    python_home = tmp_path / "python-home"
    rust_home = tmp_path / "rust-home"
    python = _run(
        "python",
        python_project,
        python_home,
        receipt,
        from_file=from_file,
    )
    rust = _run(
        "rust",
        rust_project,
        rust_home,
        receipt,
        from_file=from_file,
    )
    assert rust.returncode == python.returncode, {
        "python": (python.stdout, python.stderr),
        "rust": (rust.stdout, rust.stderr),
    }
    assert rust.stderr == python.stderr == ""
    python_value = json.loads(python.stdout)
    rust_value = json.loads(rust.stdout)
    _assert_hash(python_value)
    _assert_hash(rust_value)
    assert _normalize(
        rust_value, project=rust_project, home=rust_home
    ) == _normalize(
        python_value, project=python_project, home=python_home
    )
    return rust_value, python_value, rust_project, python_project, rust_home, python_home


def _assert_persisted(project: Path, value: dict[str, Any]) -> None:
    receipts = _receipt_files(project)
    assert len(receipts) == 1
    assert receipts[0].stem == value["receipt_id"].split(":", 1)[1]
    assert json.loads(receipts[0].read_text(encoding="utf-8")) == value


def test_native_adapter_certify_valid_receipt_matches_python(tmp_path: Path) -> None:
    rust, python, rust_project, python_project, *_ = _pair(tmp_path, VALID_RECEIPT)
    assert rust["ok"] is python["ok"] is True
    assert rust["maturity"] == python["maturity"] == "Certified"
    assert rust["detected"] is python["detected"] is True
    assert rust["checks"]["missing"] == []
    assert rust["checks"]["external_receipt"] == VALID_RECEIPT
    _assert_persisted(rust_project, rust)
    _assert_persisted(python_project, python)
    assert _state_shape(rust_project) == _state_shape(python_project)


def test_native_adapter_certify_false_check_matches_python(tmp_path: Path) -> None:
    receipt = {**VALID_RECEIPT, "security_denial": False}
    rust, python, rust_project, python_project, *_ = _pair(tmp_path, receipt)
    assert rust["ok"] is python["ok"] is False
    assert rust["maturity"] == python["maturity"] == "Enforced"
    assert rust["checks"]["missing"] == []
    _assert_persisted(rust_project, rust)
    _assert_persisted(python_project, python)
    assert _state_shape(rust_project) == _state_shape(python_project)


def test_native_adapter_certify_missing_fields_are_sorted(tmp_path: Path) -> None:
    receipt = {"host": "codex", "host_version": "1.0.0"}
    rust, python, rust_project, python_project, *_ = _pair(tmp_path, receipt)
    expected = [
        "artifact_hash",
        "clean_install",
        "context_interception",
        "security_denial",
        "session_restore",
        "tool_interception",
    ]
    assert rust["checks"]["missing"] == python["checks"]["missing"] == expected
    assert rust["ok"] is python["ok"] is False
    assert rust["maturity"] == python["maturity"] == "Enforced"
    assert _state_shape(rust_project) == _state_shape(python_project)


def test_native_adapter_certify_file_input_matches_python(tmp_path: Path) -> None:
    rust, python, rust_project, python_project, *_ = _pair(
        tmp_path,
        VALID_RECEIPT,
        from_file=True,
    )
    assert rust["ok"] is python["ok"] is True
    assert rust["checks"]["external_receipt"] == VALID_RECEIPT
    assert _state_shape(rust_project) == _state_shape(python_project)


def test_native_adapter_certify_unknown_adapter_fails_without_receipt(tmp_path: Path) -> None:
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        home = tmp_path / f"{engine}-home"
        completed = _run(
            engine,
            project,
            home,
            VALID_RECEIPT,
            adapter_id="missing-adapter",
        )
        assert completed.returncode != 0
        assert _receipt_files(project) == []
        assert (project / "state" / "unified" / "adapter-receipts").is_dir()
        assert (project / "state" / "unified" / "adapter-backups").is_dir()


def test_native_adapter_certify_rejects_non_object_without_receipt(tmp_path: Path) -> None:
    for engine in ("python", "rust"):
        project = tmp_path / f"{engine}-project"
        home = tmp_path / f"{engine}-home"
        completed = _run(engine, project, home, ["not", "an", "object"])
        assert completed.returncode != 0
        assert _receipt_files(project) == []
