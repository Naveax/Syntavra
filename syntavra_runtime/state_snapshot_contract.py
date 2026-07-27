from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CONTRACT_VERSION = 1
INSPECTION_ID = "syntavra-state-inspection-v1"
MAX_FILE_BYTES = 1024 * 1024

_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")

KNOWN_PATHS: tuple[dict[str, str], ...] = (
    {
        "id": "state-root",
        "path": ".syntavra",
        "expected_kind": "directory",
    },
    {
        "id": "project-config",
        "path": ".syntavra/config.toml",
        "expected_kind": "file",
    },
    {
        "id": "engine-selection",
        "path": ".syntavra/engine.json",
        "expected_kind": "file",
    },
    {
        "id": "pre-release-state",
        "path": ".syntavra/pre-release",
        "expected_kind": "directory",
    },
    {
        "id": "runtime-v3-state",
        "path": ".syntavra/runtime-v3",
        "expected_kind": "directory",
    },
)


class StateInspectionError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _normalized_absolute_path(path: Path) -> tuple[Path, str]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise StateInspectionError("STATE_PROJECT_ROOT_MISSING") from exc
    except OSError as exc:
        raise StateInspectionError("STATE_PROJECT_ROOT_METADATA_FAILED") from exc

    if stat.S_ISLNK(metadata.st_mode):
        raise StateInspectionError("STATE_PROJECT_ROOT_SYMLINK")
    if not stat.S_ISDIR(metadata.st_mode):
        raise StateInspectionError("STATE_PROJECT_ROOT_NOT_DIRECTORY")

    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise StateInspectionError("STATE_PROJECT_ROOT_RESOLVE_FAILED") from exc

    normalized = os.fspath(canonical).replace("\\", "/")
    if normalized.startswith("//?/"):
        normalized = normalized[4:]
    if len(normalized) >= 3 and normalized[1:3] == ":/":
        normalized = normalized[0].lower() + normalized[1:]
    return canonical, normalized


def project_id_for_root(project_root: str | os.PathLike[str]) -> str:
    _, normalized = _normalized_absolute_path(Path(project_root))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _missing_row(spec: dict[str, str]) -> dict[str, Any]:
    return {
        "id": spec["id"],
        "path": spec["path"],
        "expected_kind": spec["expected_kind"],
        "observed_kind": "missing",
        "exists": False,
        "size_bytes": None,
        "sha256": None,
    }


def _inspect_path(root: Path, spec: dict[str, str]) -> dict[str, Any]:
    current = root
    for part in Path(spec["path"]).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return _missing_row(spec)
        except OSError as exc:
            raise StateInspectionError("STATE_PATH_METADATA_FAILED") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise StateInspectionError("STATE_PATH_SYMLINK")

    expected = spec["expected_kind"]
    if stat.S_ISDIR(metadata.st_mode):
        observed = "directory"
    elif stat.S_ISREG(metadata.st_mode):
        observed = "file"
    else:
        raise StateInspectionError("STATE_PATH_TYPE_UNSUPPORTED")

    if observed != expected:
        raise StateInspectionError("STATE_PATH_KIND_MISMATCH")

    size_bytes: int | None = None
    digest: str | None = None
    if observed == "file":
        if metadata.st_size < 0 or metadata.st_size > MAX_FILE_BYTES:
            raise StateInspectionError("STATE_FILE_SIZE_LIMIT")
        try:
            payload = current.read_bytes()
            after = current.lstat()
        except OSError as exc:
            raise StateInspectionError("STATE_FILE_READ_FAILED") from exc
        if stat.S_ISLNK(after.st_mode):
            raise StateInspectionError("STATE_PATH_SYMLINK")
        if (
            after.st_mode != metadata.st_mode
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
        ):
            raise StateInspectionError("STATE_PATH_CHANGED_DURING_READ")
        if len(payload) != metadata.st_size:
            raise StateInspectionError("STATE_PATH_CHANGED_DURING_READ")
        size_bytes = len(payload)
        digest = hashlib.sha256(payload).hexdigest()

    return {
        "id": spec["id"],
        "path": spec["path"],
        "expected_kind": expected,
        "observed_kind": observed,
        "exists": True,
        "size_bytes": size_bytes,
        "sha256": digest,
    }


def inspect_state_root(
    project_root: str | os.PathLike[str],
    *,
    expected_project_id: str,
) -> dict[str, Any]:
    if not _LOWER_HEX_64.fullmatch(expected_project_id):
        raise StateInspectionError("STATE_EXPECTED_PROJECT_INVALID")

    canonical_root, normalized = _normalized_absolute_path(Path(project_root))
    actual_project_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if actual_project_id != expected_project_id:
        raise StateInspectionError("STATE_PROJECT_MISMATCH")

    paths = [_inspect_path(canonical_root, spec) for spec in KNOWN_PATHS]
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "inspection_id": INSPECTION_ID,
        "project_id": actual_project_id,
        "project_binding": {
            "expected": expected_project_id,
            "actual": actual_project_id,
            "matched": True,
        },
        "paths": paths,
        "mutation": {
            "filesystem": False,
            "database_opened": False,
        },
        "claim": "RUST_STATE_ROOT_READ_PARITY_PROVEN_R8_FIXTURES",
    }
