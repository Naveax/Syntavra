from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .config_contract import MAX_CONFIG_WIRE_BYTES, decode_config_wire, resolve_config_phases
from .config_last_good_plan import (
    CLAIM as PLAN_CLAIM,
    TARGET_RELATIVE_PATH,
    ConfigLastGoodPlanError,
    _canonical_payload,
    config_last_good_plan,
)

SCHEMA_VERSION = 1
CONTRACT_ID = "syntavra-config-last-good-atomic-apply-v1"
CLAIM = "RUST_CONFIG_LAST_GOOD_ATOMIC_APPLY_PARITY_PROVEN_R25_FIXTURES"
LOCK_RELATIVE_PATH = ".syntavra/pre-release/config-last-good.lock"
MAX_PAYLOAD_BYTES = MAX_CONFIG_WIRE_BYTES


class ConfigLastGoodApplyError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _canonical_snapshot_payload(config_wire: bytes) -> bytes:
    try:
        phases = decode_config_wire(bytes(config_wire))
        snapshot = resolve_config_phases(phases)
        payload = _canonical_payload(snapshot) + b"\n"
    except (ConfigLastGoodPlanError, Exception) as exc:
        if isinstance(exc, ConfigLastGoodApplyError):
            raise
        raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_PAYLOAD_INVALID") from exc
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_PAYLOAD_TOO_LARGE")
    return payload


def _mode_type(path: Path) -> int:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_PATH_INSPECTION_FAILED") from exc


def _ensure_secure_parent(project_root: Path) -> tuple[Path, list[Path]]:
    parent = project_root / ".syntavra" / "pre-release"
    created: list[Path] = []
    for path in (project_root / ".syntavra", parent):
        mode = _mode_type(path)
        if mode:
            if stat.S_ISLNK(mode):
                raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_PARENT_SYMLINK")
            if not stat.S_ISDIR(mode):
                raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_PARENT_TYPE_INVALID")
            continue
        try:
            path.mkdir(mode=0o700)
            created.append(path)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_PARENT_CREATE_FAILED") from exc
        mode = _mode_type(path)
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_PARENT_RACE")
    return parent, created


def _validate_target(path: Path, *, symlink_code: str, type_code: str) -> None:
    mode = _mode_type(path)
    if not mode:
        return
    if stat.S_ISLNK(mode):
        raise ConfigLastGoodApplyError(symlink_code)
    if not stat.S_ISREG(mode):
        raise ConfigLastGoodApplyError(type_code)


def _sync_directory(path: Path) -> bool:
    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _cleanup_created_directories(created: list[Path]) -> None:
    for path in reversed(created):
        try:
            path.rmdir()
        except OSError:
            pass


def apply_config_last_good(
    *,
    project_root: str | Path,
    expected_project_id: str,
    config_wire: bytes,
) -> dict[str, Any]:
    raw_wire = bytes(config_wire)
    try:
        plan = config_last_good_plan(
            project_root=project_root,
            expected_project_id=expected_project_id,
            config_wire=raw_wire,
        )
    except ConfigLastGoodPlanError as exc:
        raise ConfigLastGoodApplyError(exc.code.replace("CONFIG_LIFECYCLE_", "CONFIG_LAST_GOOD_APPLY_")) from exc

    if plan["claim"] != PLAN_CLAIM:
        raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_PLAN_CLAIM_INVALID")

    project_id = str(plan["project_id"])
    decision = str(plan["decision"])
    payload = _canonical_snapshot_payload(raw_wire)
    payload_sha256 = hashlib.sha256(payload).hexdigest()

    if decision == "retain-existing":
        return {
            "action": "retained",
            "claim": CLAIM,
            "contract_id": CONTRACT_ID,
            "decision": decision,
            "full_product_parity": "FULL_PARITY_NOT_PROVEN",
            "mutation": {
                "directory_created": False,
                "directory_synced": False,
                "lock_created": False,
                "target_replaced": False,
                "temporary_created": False,
            },
            "ok": True,
            "payload_bytes": len(payload),
            "payload_sha256": payload_sha256,
            "project_id": project_id,
            "schema_version": SCHEMA_VERSION,
            "target_sha256": None,
        }
    if decision != "write":
        raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_DECISION_INVALID")

    root = Path(project_root)
    target = root / TARGET_RELATIVE_PATH
    lock = root / LOCK_RELATIVE_PATH
    parent: Path | None = None
    created_dirs: list[Path] = []
    lock_fd: int | None = None
    temp_path: Path | None = None
    completed = False
    mutation = {
        "directory_created": False,
        "directory_synced": False,
        "lock_created": False,
        "target_replaced": False,
        "temporary_created": False,
    }

    try:
        parent, created_dirs = _ensure_secure_parent(root)
        mutation["directory_created"] = bool(created_dirs)
        _validate_target(
            target,
            symlink_code="CONFIG_LAST_GOOD_APPLY_TARGET_SYMLINK",
            type_code="CONFIG_LAST_GOOD_APPLY_TARGET_TYPE_INVALID",
        )
        _validate_target(
            lock,
            symlink_code="CONFIG_LAST_GOOD_APPLY_LOCK_SYMLINK",
            type_code="CONFIG_LAST_GOOD_APPLY_LOCK_BUSY",
        )
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_LOCK_BUSY") from exc
        except OSError as exc:
            raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_LOCK_CREATE_FAILED") from exc
        mutation["lock_created"] = True
        os.write(lock_fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(lock_fd)

        if target.exists():
            try:
                existing = target.read_bytes()
            except OSError as exc:
                raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_TARGET_READ_FAILED") from exc
            if len(existing) > MAX_PAYLOAD_BYTES:
                raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_EXISTING_TOO_LARGE")
            if existing == payload:
                completed = True
                return {
                    "action": "unchanged",
                    "claim": CLAIM,
                    "contract_id": CONTRACT_ID,
                    "decision": decision,
                    "full_product_parity": "FULL_PARITY_NOT_PROVEN",
                    "mutation": mutation,
                    "ok": True,
                    "payload_bytes": len(payload),
                    "payload_sha256": payload_sha256,
                    "project_id": project_id,
                    "schema_version": SCHEMA_VERSION,
                    "target_sha256": payload_sha256,
                }

        fd, temp_name = tempfile.mkstemp(prefix=".config-last-good.", suffix=".tmp", dir=parent)
        temp_path = Path(temp_name)
        mutation["temporary_created"] = True
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            if os.name != "nt":
                raise
        _validate_target(
            target,
            symlink_code="CONFIG_LAST_GOOD_APPLY_TARGET_SYMLINK",
            type_code="CONFIG_LAST_GOOD_APPLY_TARGET_TYPE_INVALID",
        )
        os.replace(temp_path, target)
        temp_path = None
        mutation["target_replaced"] = True
        mutation["directory_synced"] = _sync_directory(parent)
        final = target.read_bytes()
        if final != payload:
            raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_POST_WRITE_MISMATCH")
        completed = True
        return {
            "action": "written",
            "claim": CLAIM,
            "contract_id": CONTRACT_ID,
            "decision": decision,
            "full_product_parity": "FULL_PARITY_NOT_PROVEN",
            "mutation": mutation,
            "ok": True,
            "payload_bytes": len(payload),
            "payload_sha256": payload_sha256,
            "project_id": project_id,
            "schema_version": SCHEMA_VERSION,
            "target_sha256": hashlib.sha256(final).hexdigest(),
        }
    except ConfigLastGoodApplyError:
        raise
    except OSError as exc:
        raise ConfigLastGoodApplyError("CONFIG_LAST_GOOD_APPLY_IO_FAILED") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        if mutation["lock_created"]:
            try:
                lock.unlink()
            except OSError:
                pass
        if not completed:
            _cleanup_created_directories(created_dirs)


def canonical_apply_json(result: Mapping[str, Any]) -> str:
    return json.dumps(dict(result), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
