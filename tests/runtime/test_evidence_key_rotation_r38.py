from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.evidence_rotation import rotate_evidence_key

ROOT = Path(__file__).resolve().parents[2]


def test_evidence_key_rotation_reencrypts_existing_objects(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence", project_id="rotation-test")
    first = store.put(b"alpha", reference="first")
    second_payload = b"beta" * 1000
    second = store.put(second_payload, reference="second")

    result = rotate_evidence_key(store, reencrypt=True)

    assert result["ok"] is True
    assert result["previous_key_version"] == 1
    assert result["active_key_version"] == 2
    assert result["objects"] == 2
    assert result["reencrypted"] == 2
    assert store.get(first) == b"alpha"
    assert store.get(second) == second_payload
    assert store.describe(first)["encryption"]["key_version"] == 2
    assert store.describe(second)["encryption"]["key_version"] == 2
    assert store.stats()["active_key_version"] == 2


def test_evidence_key_rotation_without_reencrypt_preserves_old_objects(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence", project_id="rotation-test")
    handle = store.put(b"legacy")

    result = rotate_evidence_key(store, reencrypt=False)

    assert result["active_key_version"] == 2
    assert result["reencrypted"] == 0
    assert result["skipped"] == 1
    assert store.get(handle) == b"legacy"
    assert store.describe(handle)["encryption"]["key_version"] == 1
    new_handle = store.put(b"current")
    assert store.describe(new_handle)["encryption"]["key_version"] == 2


def test_python_engine_evidence_rotate_key_is_runnable(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--project",
            str(tmp_path / "project"),
            "--state-root",
            str(tmp_path / "state"),
            "evidence",
            "rotate-key",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["active_key_version"] == 2
