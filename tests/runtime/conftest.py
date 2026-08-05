from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_CARGO_TARGET = ROOT / "target"


def pytest_configure(config: Any) -> None:
    """Keep real Rust selector builds and runtime lookups on one exact path.

    R38 differential tests build the selector through Cargo and then execute
    ``target/debug/syntavra``. CI may provide a repository-wide
    ``CARGO_TARGET_DIR`` for unrelated build caching; allowing that value to
    redirect test-local builds makes the binary lookup fail after a successful
    Cargo invocation. Runtime tests therefore pin their own build output to the
    canonical path they verify on every platform.
    """

    del config
    os.environ["CARGO_TARGET_DIR"] = str(CANONICAL_CARGO_TARGET)


def pytest_make_parametrize_id(config: Any, val: object, argname: str) -> str | None:
    """Keep runtime test node IDs bounded on Windows.

    Pytest mirrors the current node ID into ``PYTEST_CURRENT_TEST``. Large
    security-boundary fixtures, such as R19's 64 KiB receipt transport case,
    can otherwise exceed Windows' environment-variable length limit before
    the test body runs.
    """

    del config
    if isinstance(val, str) and len(val) > 256:
        digest = hashlib.sha256(val.encode("utf-8")).hexdigest()[:16]
        return f"{argname}-len{len(val)}-sha256-{digest}"
    return None
