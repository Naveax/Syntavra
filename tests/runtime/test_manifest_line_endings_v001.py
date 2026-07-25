import hashlib
from pathlib import Path

from tools.refresh_manifest import canonical_manifest_bytes


def _digest(relative: str, data: bytes) -> str:
    canonical = canonical_manifest_bytes(Path(relative), data)
    return hashlib.sha256(canonical).hexdigest()


def test_text_hash_is_independent_of_crlf_or_lf() -> None:
    lf = b"first line\nsecond line\n"
    crlf = b"first line\r\nsecond line\r\n"

    assert _digest("syntavra_runtime/example.py", lf) == _digest(
        "syntavra_runtime/example.py",
        crlf,
    )


def test_lone_carriage_returns_are_normalized_for_text() -> None:
    assert canonical_manifest_bytes(
        Path("docs/example.md"),
        b"first\rsecond\r",
    ) == b"first\nsecond\n"


def test_binary_payloads_are_not_normalized() -> None:
    payload = b"\x89PNG\r\n\x00binary\r\n"

    assert canonical_manifest_bytes(
        Path("assets/example.png"),
        payload,
    ) == payload


def test_real_task_receipts_preserve_exact_bytes() -> None:
    payload = b"receipt\r\npayload\r\n"

    assert canonical_manifest_bytes(
        Path("benchmarks/results/real-tasks/raw-receipt.txt"),
        payload,
    ) == payload
