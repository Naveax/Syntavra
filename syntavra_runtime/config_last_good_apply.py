from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, Mapping

from .config_contract import decode_config_wire, resolve_config_phases
from .config_last_good_plan import (
    TARGET_RELATIVE_PATH,
    ConfigLastGoodPlanError,
    config_last_good_plan,
)

SCHEMA_VERSION = 1
CONTRACT_VERSION = 1
APPLY_ID = "syntavra-config-last-good-apply-v1"
CLAIM = "RUST_CONFIG_LAST_GOOD_APPLY_PARITY_PROVEN_R25_FIXTURES"
LOCK_RELATIVE_PATH = ".syntavra/pre-release/config-last-good.lock"
TEMP_RELATIVE_PATH = ".syntavra/pre-release/.config-last-good.json.apply.tmp"
STALE_LOCK_SECONDS = 300
FAULT_POINTS = frozenset({"after-lock", "after-temp-sync", "after-replace"})


class ConfigLastGoodApplyError(RuntimeError):
    def __init__(self, code: str, *, crash_simulated: bool = False):
        super().__init__(code)
        self.code = code
        self.crash_simulated = crash_simulated


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _candidate_payload(config_wire: bytes) -> bytes:
    try:
        snapshot = resolve_config_phases(decode_config_wire(bytes(config_wire)))
    except Exception as exc:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_CONFIG_INVALID") from exc
    expected = {"schema_version", "values", "provenance", "config_hash", "warnings"}
    if {str(key) for key in snapshot} != expected:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_SNAPSHOT_KEYS_INVALID")
    return _canonical_json_bytes(snapshot)


def _normalized_json_payload(raw: bytes) -> bytes:
    payload = bytes(raw)
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if not payload or payload.endswith(b"\n"):
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TARGET_ENCODING_INVALID")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TARGET_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TARGET_JSON_INVALID")
    return _canonical_json_bytes(value)


def _validate_target_snapshot(raw: bytes, expected_config_hash: str | None) -> bytes:
    canonical = _normalized_json_payload(raw)
    value = json.loads(canonical)
    required = {"schema_version", "values", "provenance", "config_hash"}
    if not required.issubset(value):
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TARGET_SCHEMA_INVALID")
    if int(value["schema_version"]) != 1:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TARGET_SCHEMA_INVALID")
    if expected_config_hash is not None and str(value["config_hash"]) != expected_config_hash:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TARGET_HASH_MISMATCH")
    return canonical


def _reject_symlink(path: Path, code: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConfigLastGoodApplyError(code) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ConfigLastGoodApplyError(code)


def _prepare_state_directory(project_root: Path) -> Path:
    state = project_root / ".syntavra"
    release = state / "pre-release"
    _reject_symlink(state, "CONFIG_LIFECYCLE_STATE_ROOT_SYMLINK")
    _reject_symlink(release, "CONFIG_LIFECYCLE_RELEASE_ROOT_SYMLINK")
    try:
        release.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_STATE_ROOT_CREATE_FAILED") from exc
    _reject_symlink(state, "CONFIG_LIFECYCLE_STATE_ROOT_SYMLINK")
    _reject_symlink(release, "CONFIG_LIFECYCLE_RELEASE_ROOT_SYMLINK")
    return release


def _sync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _lock_payload(project_id: str) -> bytes:
    return _canonical_json_bytes(
        {
            "contract_version": CONTRACT_VERSION,
            "project_id": project_id,
            "target": TARGET_RELATIVE_PATH,
        }
    ) + b"\n"


def _lock_is_stale(path: Path, *, now: float) -> bool:
    try:
        age = now - path.stat().st_mtime
    except OSError as exc:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_LOCK_METADATA_FAILED") from exc
    return age >= STALE_LOCK_SECONDS


def _validate_lock_binding(path: Path, project_id: str) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_STALE_LOCK_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("contract_version") != CONTRACT_VERSION
        or value.get("project_id") != project_id
        or value.get("target") != TARGET_RELATIVE_PATH
    ):
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_STALE_LOCK_INVALID")


def _acquire_lock(path: Path, project_id: str, *, now: float) -> bool:
    _reject_symlink(path, "CONFIG_LIFECYCLE_LOCK_SYMLINK")
    stale_recovered = False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _reject_symlink(path, "CONFIG_LIFECYCLE_LOCK_SYMLINK")
        if not _lock_is_stale(path, now=now):
            raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_LOCK_HELD")
        _validate_lock_binding(path, project_id)
        try:
            path.unlink()
        except OSError as exc:
            raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_STALE_LOCK_REMOVE_FAILED") from exc
        stale_recovered = True
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_LOCK_ACQUIRE_FAILED") from exc
    except OSError as exc:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_LOCK_ACQUIRE_FAILED") from exc

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_lock_payload(project_id))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_LOCK_WRITE_FAILED") from exc
    return stale_recovered


def _fault(point: str | None, expected: str) -> None:
    if point == expected:
        raise ConfigLastGoodApplyError(
            f"CONFIG_LIFECYCLE_FAULT_INJECTED_{expected.replace('-', '_').upper()}",
            crash_simulated=True,
        )


def _set_private_mode(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_temp(path: Path, payload: bytes) -> None:
    _reject_symlink(path, "CONFIG_LIFECYCLE_TEMP_SYMLINK")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        _set_private_mode(path)
    except OSError as exc:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TEMP_WRITE_FAILED") from exc


def _receipt(
    *,
    action: str,
    decision: str,
    project_id: str,
    candidate: Mapping[str, Any],
    stored_payload: bytes,
    stale_lock_recovered: bool,
    filesystem_mutated: bool,
) -> dict[str, Any]:
    return {
        "apply_authority": "bounded-shadow",
        "apply_id": APPLY_ID,
        "claim": CLAIM,
        "contract_version": CONTRACT_VERSION,
        "decision": decision,
        "full_product_parity": "FULL_PARITY_NOT_PROVEN",
        "lock": {
            "relative_path": LOCK_RELATIVE_PATH,
            "stale_recovered": stale_lock_recovered,
        },
        "mutation": {
            "database_opened": False,
            "filesystem": filesystem_mutated,
        },
        "ok": True,
        "project_id": project_id,
        "public_routing": "blocked",
        "result": {
            "action": action,
            "config_hash": str(candidate["config_hash"]),
            "payload_bytes": int(candidate["payload_bytes"]),
            "payload_sha256": str(candidate["payload_sha256"]),
            "stored_payload_bytes": len(stored_payload),
            "stored_payload_sha256": hashlib.sha256(stored_payload).hexdigest(),
        },
        "schema_version": SCHEMA_VERSION,
        "target": {
            "file_mode": "0600",
            "relative_path": TARGET_RELATIVE_PATH,
        },
    }


def apply_config_last_good(
    *,
    project_root: str | Path,
    expected_project_id: str,
    config_wire: bytes,
    fault: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if fault is not None and fault not in FAULT_POINTS:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_FAULT_POINT_INVALID")

    try:
        plan = config_last_good_plan(
            project_root=project_root,
            expected_project_id=expected_project_id,
            config_wire=config_wire,
        )
    except ConfigLastGoodPlanError as exc:
        raise ConfigLastGoodApplyError(exc.code) from exc

    try:
        root = Path(project_root).resolve(strict=True)
    except OSError as exc:
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_PROJECT_ROOT_CHANGED") from exc
    target = root / TARGET_RELATIVE_PATH
    lock = root / LOCK_RELATIVE_PATH
    temp = root / TEMP_RELATIVE_PATH
    candidate = plan["candidate"]
    decision = str(plan["decision"])
    if decision == "retain-existing":
        _reject_symlink(root / ".syntavra", "CONFIG_LIFECYCLE_STATE_ROOT_SYMLINK")
        _reject_symlink(
            root / ".syntavra" / "pre-release",
            "CONFIG_LIFECYCLE_RELEASE_ROOT_SYMLINK",
        )
        _reject_symlink(target, "CONFIG_LIFECYCLE_TARGET_SYMLINK")
        if not target.is_file():
            raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_RETAIN_TARGET_MISSING")
    release = _prepare_state_directory(root)
    candidate_payload = _candidate_payload(config_wire)
    if (
        len(candidate_payload) != int(candidate["payload_bytes"])
        or hashlib.sha256(candidate_payload).hexdigest() != candidate["payload_sha256"]
    ):
        raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_PLAN_PAYLOAD_MISMATCH")

    stale_recovered = _acquire_lock(
        lock,
        expected_project_id,
        now=time.time() if now is None else float(now),
    )
    crash_simulated = False
    try:
        _fault(fault, "after-lock")

        _reject_symlink(target, "CONFIG_LIFECYCLE_TARGET_SYMLINK")
        _reject_symlink(temp, "CONFIG_LIFECYCLE_TEMP_SYMLINK")

        if temp.is_file():
            try:
                temp_raw = temp.read_bytes()
            except OSError as exc:
                raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TEMP_READ_FAILED") from exc
            temp_payload = _normalized_json_payload(temp_raw)
            temp_matches = hashlib.sha256(temp_payload).hexdigest() == candidate["payload_sha256"]
            if temp_matches and not target.exists() and decision == "write":
                try:
                    os.replace(temp, target)
                except OSError as exc:
                    raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_REPLACE_FAILED") from exc
                _set_private_mode(target)
                _sync_directory(release)
                stored = _validate_target_snapshot(target.read_bytes(), str(candidate["config_hash"]))
                return _receipt(
                    action="recover-temp",
                    decision=decision,
                    project_id=expected_project_id,
                    candidate=candidate,
                    stored_payload=stored,
                    stale_lock_recovered=stale_recovered,
                    filesystem_mutated=True,
                )
            try:
                temp.unlink()
            except OSError as exc:
                raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TEMP_REMOVE_FAILED") from exc

        if target.is_file():
            try:
                current_raw = target.read_bytes()
            except OSError as exc:
                raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TARGET_READ_FAILED") from exc
            current = _validate_target_snapshot(
                current_raw,
                str(candidate["config_hash"]) if decision == "retain-existing" else None,
            )
            if decision == "retain-existing":
                return _receipt(
                    action="retain-existing",
                    decision=decision,
                    project_id=expected_project_id,
                    candidate=candidate,
                    stored_payload=current,
                    stale_lock_recovered=stale_recovered,
                    filesystem_mutated=False,
                )
            if hashlib.sha256(current).hexdigest() == candidate["payload_sha256"]:
                return _receipt(
                    action="already-current",
                    decision=decision,
                    project_id=expected_project_id,
                    candidate=candidate,
                    stored_payload=current,
                    stale_lock_recovered=stale_recovered,
                    filesystem_mutated=False,
                )
        elif decision == "retain-existing":
            raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_RETAIN_TARGET_MISSING")

        _write_temp(temp, candidate_payload)
        _fault(fault, "after-temp-sync")
        try:
            os.replace(temp, target)
        except OSError as exc:
            raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_REPLACE_FAILED") from exc
        _set_private_mode(target)
        _fault(fault, "after-replace")
        _sync_directory(release)

        try:
            stored = _validate_target_snapshot(target.read_bytes(), str(candidate["config_hash"]))
        except OSError as exc:
            raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_TARGET_READ_FAILED") from exc
        if hashlib.sha256(stored).hexdigest() != candidate["payload_sha256"]:
            raise ConfigLastGoodApplyError("CONFIG_LIFECYCLE_POST_WRITE_VERIFY_FAILED")
        return _receipt(
            action="write",
            decision=decision,
            project_id=expected_project_id,
            candidate=candidate,
            stored_payload=stored,
            stale_lock_recovered=stale_recovered,
            filesystem_mutated=True,
        )
    except ConfigLastGoodApplyError as exc:
        crash_simulated = exc.crash_simulated
        raise
    finally:
        if not crash_simulated:
            temp.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
            _sync_directory(release)


def canonical_apply_json(receipt: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(receipt),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply the bounded R25 config last-good transaction."
    )
    parser.add_argument("expected_project_id")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("config_wire_hex")
    parser.add_argument("--fault", choices=sorted(FAULT_POINTS))
    args = parser.parse_args(argv)
    try:
        wire = bytes.fromhex(args.config_wire_hex)
        result = apply_config_last_good(
            project_root=args.project_root,
            expected_project_id=args.expected_project_id,
            config_wire=wire,
            fault=args.fault,
        )
    except ValueError:
        print("CONFIG_LIFECYCLE_WIRE_HEX_INVALID")
        return 2
    except ConfigLastGoodApplyError as exc:
        print(exc.code)
        return 2
    print(canonical_apply_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
