from __future__ import annotations

import hashlib
from typing import Any


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
