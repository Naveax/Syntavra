from __future__ import annotations

import json
from pathlib import Path

import pytest

from syntavra_runtime.canonical_primitives import (
    CanonicalPathError,
    canonical_manifest_bytes,
    canonical_text_bytes,
    manifest_digest_hex,
    normalize_repository_path,
    sha256_hex,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "parity" / "fixtures" / "primitives-v1.json"


def test_repository_path_normalization_is_lexical_and_platform_independent() -> None:
    assert normalize_repository_path(r".\src//./nested\main.py") == "src/nested/main.py"
    assert normalize_repository_path("Müşteri/Özet.md") == "Müşteri/Özet.md"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("src/../secret", "PATH_PARENT_TRAVERSAL"),
        ("/etc/passwd", "PATH_ABSOLUTE"),
        (r"C:\repo\file", "PATH_DRIVE_PREFIX"),
        ("./", "PATH_EMPTY"),
        ("bad\x00path", "PATH_NUL"),
    ],
)
def test_unsafe_repository_paths_fail_closed(value: str, code: str) -> None:
    with pytest.raises(CanonicalPathError) as error:
        normalize_repository_path(value)
    assert error.value.code == code


def test_text_canonicalization_preserves_opaque_payloads() -> None:
    assert canonical_text_bytes(b"first\r\nsecond\r") == b"first\nsecond\n"
    binary = b"alpha\r\n\x00omega\r\n"
    assert canonical_text_bytes(binary) == binary
    invalid_utf8 = bytes.fromhex("ff0d0afe")
    assert canonical_text_bytes(invalid_utf8) == invalid_utf8


def test_real_task_receipts_remain_byte_exact() -> None:
    payload = b"receipt\r\npayload\r\n"
    assert canonical_manifest_bytes(
        "benchmarks/results/real-tasks/raw-receipt.txt",
        payload,
    ) == payload


def test_shared_r5_fixture_matches_python_reference() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == 1

    for row in fixture["sha256"]:
        assert sha256_hex(bytes.fromhex(row["input_hex"])) == row["digest"]

    for row in fixture["canonical_manifest"]:
        payload = bytes.fromhex(row["input_hex"])
        assert canonical_manifest_bytes(row["path"], payload).hex() == row["canonical_hex"]
        assert manifest_digest_hex(row["path"], payload) == row["digest"]

    for row in fixture["paths"]:
        if "error" in row:
            with pytest.raises(CanonicalPathError) as error:
                normalize_repository_path(row["input"])
            assert error.value.code == row["error"]
        else:
            assert normalize_repository_path(row["input"]) == row["normalized"]
