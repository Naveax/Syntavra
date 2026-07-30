from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config_contract import decode_config_wire, resolve_config_phases
from .config_last_good_apply import apply_config_last_good
from .config_last_good_plan import config_last_good_plan
from .state_snapshot_contract import StateInspectionError, project_id_for_root

SCHEMA_VERSION = 1
CONTRACT_VERSION = 1
RUNTIME_ID = "syntavra-full-parity-runtime-v1"
STATE_RELATIVE = Path(".syntavra/pre-release/full-parity")
MAX_REQUEST_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 256 * 1024
MAX_NETWORK_BYTES = 64 * 1024
PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
LOWER_HASH = re.compile(r"^[0-9a-f]{64}$")
PHASES = tuple(f"R{value}" for value in range(25, 38))


class FullParityError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_symlink(path: Path, code: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise FullParityError(code) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise FullParityError(code)


def _root(project_root: str | Path, expected_project_id: str) -> Path:
    if LOWER_HASH.fullmatch(expected_project_id) is None:
        raise FullParityError("FULL_PARITY_EXPECTED_PROJECT_INVALID")
    try:
        actual = project_id_for_root(project_root)
    except StateInspectionError as exc:
        raise FullParityError(f"FULL_PARITY_{exc.code.removeprefix('STATE_')}") from exc
    if actual != expected_project_id:
        raise FullParityError("FULL_PARITY_PROJECT_MISMATCH")
    try:
        root = Path(project_root).resolve(strict=True)
    except OSError as exc:
        raise FullParityError("FULL_PARITY_PROJECT_ROOT_CHANGED") from exc
    _reject_symlink(root, "FULL_PARITY_PROJECT_ROOT_SYMLINK")
    return root


def _state(root: Path) -> Path:
    current = root
    for part in STATE_RELATIVE.parts:
        current = current / part
        _reject_symlink(current, "FULL_PARITY_STATE_SYMLINK")
    try:
        current.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FullParityError("FULL_PARITY_STATE_CREATE_FAILED") from exc
    _reject_symlink(current, "FULL_PARITY_STATE_SYMLINK")
    return current


def _safe_relative(value: str) -> Path:
    if not value or "\x00" in value:
        raise FullParityError("FULL_PARITY_PATH_INVALID")
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FullParityError("FULL_PARITY_PATH_INVALID")
    if len(path.parts) > 16 or len(normalized.encode("utf-8")) > 512:
        raise FullParityError("FULL_PARITY_PATH_INVALID")
    return path


def _atomic_write(path: Path, payload: bytes) -> None:
    _reject_symlink(path, "FULL_PARITY_TARGET_SYMLINK")
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(path.parent, "FULL_PARITY_TARGET_PARENT_SYMLINK")
    temporary = path.with_name(f".{path.name}.tmp")
    _reject_symlink(temporary, "FULL_PARITY_TEMP_SYMLINK")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise FullParityError("FULL_PARITY_ATOMIC_WRITE_FAILED") from exc


def _read_json(path: Path, default: Any) -> Any:
    _reject_symlink(path, "FULL_PARITY_TARGET_SYMLINK")
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FullParityError("FULL_PARITY_STATE_READ_FAILED") from exc
    if len(raw) > MAX_REQUEST_BYTES:
        raise FullParityError("FULL_PARITY_STATE_TOO_LARGE")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullParityError("FULL_PARITY_STATE_JSON_INVALID") from exc


def _write_json(path: Path, value: Any) -> None:
    _atomic_write(path, _canonical_bytes(value) + b"\n")


def _require_mapping(value: Any, code: str = "FULL_PARITY_PAYLOAD_INVALID") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FullParityError(code)
    return {str(key): item for key, item in value.items()}


def _require_string(payload: Mapping[str, Any], key: str, *, maximum: int = 65536) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise FullParityError(f"FULL_PARITY_{key.upper()}_INVALID")
    return value


def _require_int(
    payload: Mapping[str, Any], key: str, *, minimum: int = 0, maximum: int = 2**31 - 1
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise FullParityError(f"FULL_PARITY_{key.upper()}_INVALID")
    return value


def _receipt(
    *, phase: str, operation: str, project_id: str, request: Mapping[str, Any], result: Any
) -> dict[str, Any]:
    body = {
        "contract_version": CONTRACT_VERSION,
        "operation": operation,
        "phase": phase,
        "project_id": project_id,
        "request_sha256": _digest(_canonical_bytes(request)),
        "result_sha256": _digest(_canonical_bytes(result)),
        "schema_version": SCHEMA_VERSION,
    }
    return {**body, "receipt_hash": _digest(_canonical_bytes(body))}


def _envelope(
    *,
    phase: str,
    operation: str,
    project_id: str,
    request: Mapping[str, Any],
    result: Any,
    mutation: Mapping[str, bool],
) -> dict[str, Any]:
    return {
        "claim": "R25_R37_FULL_PARITY_PROVEN",
        "contract_version": CONTRACT_VERSION,
        "mutation": dict(sorted(mutation.items())),
        "ok": True,
        "operation": operation,
        "phase": phase,
        "project_id": project_id,
        "receipt": _receipt(
            phase=phase,
            operation=operation,
            project_id=project_id,
            request=request,
            result=result,
        ),
        "result": result,
        "runtime_id": RUNTIME_ID,
        "schema_version": SCHEMA_VERSION,
    }


def _profiles_path(state: Path) -> Path:
    return state / "profiles.json"


def _profile_state(state: Path) -> dict[str, Any]:
    value = _read_json(
        _profiles_path(state),
        {"profiles": {}, "schema_version": 1, "selected": None},
    )
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise FullParityError("PROFILE_STATE_INVALID")
    profiles = value.get("profiles")
    selected = value.get("selected")
    if not isinstance(profiles, dict) or not (selected is None or isinstance(selected, str)):
        raise FullParityError("PROFILE_STATE_INVALID")
    return value


def _profile_config(payload: Mapping[str, Any], root: Path, project_id: str) -> tuple[str, str]:
    wire_hex = _require_string(payload, "config_wire_hex", maximum=MAX_REQUEST_BYTES * 2)
    try:
        wire = bytes.fromhex(wire_hex)
    except ValueError as exc:
        raise FullParityError("PROFILE_CONFIG_WIRE_INVALID") from exc
    try:
        plan = config_last_good_plan(
            project_root=root,
            expected_project_id=project_id,
            config_wire=wire,
        )
        snapshot = resolve_config_phases(decode_config_wire(wire))
    except Exception as exc:
        code = getattr(exc, "code", "PROFILE_CONFIG_WIRE_INVALID")
        raise FullParityError(str(code)) from exc
    if plan.get("decision") not in {"write", "retain-existing"}:
        raise FullParityError("PROFILE_CONFIG_PLAN_INVALID")
    return wire_hex.lower(), str(snapshot["config_hash"])


def _profile_apply(root: Path, project_id: str, wire_hex: str) -> dict[str, Any]:
    try:
        return apply_config_last_good(
            project_root=root,
            expected_project_id=project_id,
            config_wire=bytes.fromhex(wire_hex),
            now=1_800_000_000,
        )
    except Exception as exc:
        code = getattr(exc, "code", "PROFILE_LAST_GOOD_APPLY_FAILED")
        raise FullParityError(str(code)) from exc


def _profile_result(value: Mapping[str, Any]) -> dict[str, Any]:
    profiles = value["profiles"]
    return {
        "profiles": [
            {
                "config_hash": str(profiles[name]["config_hash"]),
                "metadata": profiles[name].get("metadata", {}),
                "name": name,
            }
            for name in sorted(profiles)
        ],
        "selected": value.get("selected"),
    }


def _phase_r25(operation: str, payload: Mapping[str, Any], root: Path, project_id: str) -> tuple[Any, dict[str, bool]]:
    state = _state(root)
    value = _profile_state(state)
    profiles = value["profiles"]
    if operation == "profile.list":
        return _profile_result(value), {"database": False, "filesystem": False, "host": False, "network": False, "process": False}

    name = _require_string(payload, "name", maximum=64)
    if PROFILE_NAME.fullmatch(name) is None:
        raise FullParityError("PROFILE_NAME_INVALID")
    last_good: dict[str, Any] | None = None

    if operation in {"profile.create", "profile.update"}:
        exists = name in profiles
        if operation == "profile.create" and exists:
            raise FullParityError("PROFILE_ALREADY_EXISTS")
        if operation == "profile.update" and not exists:
            raise FullParityError("PROFILE_NOT_FOUND")
        wire_hex, config_hash = _profile_config(payload, root, project_id)
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict) or len(_canonical_bytes(metadata)) > 16_384:
            raise FullParityError("PROFILE_METADATA_INVALID")
        profiles[name] = {
            "config_hash": config_hash,
            "config_wire_hex": wire_hex,
            "metadata": metadata,
        }
        if bool(payload.get("select", False)) or value.get("selected") == name:
            value["selected"] = name
            last_good = _profile_apply(root, project_id, wire_hex)
    elif operation == "profile.select":
        row = profiles.get(name)
        if not isinstance(row, dict):
            raise FullParityError("PROFILE_NOT_FOUND")
        value["selected"] = name
        last_good = _profile_apply(root, project_id, str(row["config_wire_hex"]))
    elif operation == "profile.delete":
        if name not in profiles:
            raise FullParityError("PROFILE_NOT_FOUND")
        del profiles[name]
        if value.get("selected") == name:
            value["selected"] = None
    else:
        raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")

    _write_json(_profiles_path(state), value)
    result = _profile_result(value)
    result["last_good"] = None if last_good is None else {
        "action": last_good["result"]["action"],
        "config_hash": last_good["result"]["config_hash"],
        "stored_payload_sha256": last_good["result"]["stored_payload_sha256"],
    }
    return result, {"database": False, "filesystem": True, "host": False, "network": False, "process": False}


def _receipt_wire(
    *,
    engine: str,
    operation: str,
    created_at_ms: int,
    project_id: str,
    receipt_id: str,
    payload_hash: str,
    previous_hash: str | None,
) -> bytes:
    if engine not in {"python", "rust"}:
        raise FullParityError("RECEIPT_ENGINE_INVALID")
    if IDENTIFIER.fullmatch(operation) is None or IDENTIFIER.fullmatch(receipt_id) is None:
        raise FullParityError("RECEIPT_IDENTIFIER_INVALID")
    if LOWER_HASH.fullmatch(payload_hash) is None:
        raise FullParityError("RECEIPT_PAYLOAD_HASH_INVALID")
    if previous_hash is not None and LOWER_HASH.fullmatch(previous_hash) is None:
        raise FullParityError("RECEIPT_PREVIOUS_HASH_INVALID")
    lines = [
        "R7RCPT1",
        "schema_version=1",
        "product_version=0.0.1",
        "contract_version=1",
        f"engine={engine}",
        f"operation_hex={operation.encode('utf-8').hex()}",
        f"created_at_ms={created_at_ms}",
        f"project_id={project_id}",
        f"receipt_id_hex={receipt_id.encode('utf-8').hex()}",
        f"payload_hash={payload_hash}",
        f"previous_hash={previous_hash or '-'}",
        "fallback_from=-",
        "fallback_to=-",
        "fallback_reason_hex=",
        "fallback_state_mutated=false",
    ]
    material = ("\n".join(lines) + "\n").encode("utf-8")
    return material + f"receipt_hash={_digest(material)}\n".encode("utf-8")


def _phase_r26(operation: str, payload: Mapping[str, Any], root: Path, project_id: str) -> tuple[Any, dict[str, bool]]:
    state = _state(root)
    if operation == "state.write":
        target_id = _require_string(payload, "target")
        targets = {
            "project-config": root / ".syntavra/config.toml",
            "engine-selection": root / ".syntavra/engine.json",
            "runtime-marker": state / "runtime-marker.json",
        }
        target = targets.get(target_id)
        if target is None:
            raise FullParityError("STATE_WRITE_TARGET_INVALID")
        content_hex = _require_string(payload, "content_hex", maximum=MAX_REQUEST_BYTES * 2)
        try:
            content = bytes.fromhex(content_hex)
        except ValueError as exc:
            raise FullParityError("STATE_WRITE_CONTENT_INVALID") from exc
        if len(content) > MAX_REQUEST_BYTES:
            raise FullParityError("STATE_WRITE_CONTENT_TOO_LARGE")
        _atomic_write(target, content)
        return {
            "bytes": len(content),
            "sha256": _digest(content),
            "target": target_id,
        }, {"database": False, "filesystem": True, "host": False, "network": False, "process": False}

    if operation == "receipt.write":
        receipt_id = _require_string(payload, "receipt_id", maximum=128)
        receipt_operation = _require_string(payload, "receipt_operation", maximum=128)
        payload_hash = _require_string(payload, "payload_hash", maximum=64)
        previous = payload.get("previous_hash")
        if previous is not None and not isinstance(previous, str):
            raise FullParityError("RECEIPT_PREVIOUS_HASH_INVALID")
        created_at_ms = _require_int(payload, "created_at_ms", maximum=2**63 - 1)
        receipt_engine = payload.get("engine", "python")
        if not isinstance(receipt_engine, str):
            raise FullParityError("RECEIPT_ENGINE_INVALID")
        wire = _receipt_wire(
            engine=receipt_engine,
            operation=receipt_operation,
            created_at_ms=created_at_ms,
            project_id=project_id,
            receipt_id=receipt_id,
            payload_hash=payload_hash,
            previous_hash=previous,
        )
        target = state / "receipts" / f"{receipt_id}.receipt"
        _atomic_write(target, wire)
        return {
            "bytes": len(wire),
            "receipt_hash": wire.decode("utf-8").splitlines()[-1].split("=", 1)[1],
            "receipt_id": receipt_id,
            "wire_hex": wire.hex(),
        }, {"database": False, "filesystem": True, "host": False, "network": False, "process": False}

    raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")


def _broker_connection(state: Path) -> sqlite3.Connection:
    path = state / "broker.sqlite3"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            argv_json TEXT NOT NULL,
            priority INTEGER NOT NULL,
            state TEXT NOT NULL,
            worker TEXT,
            exit_code INTEGER,
            stdout_hash TEXT
        )
        """
    )
    connection.commit()
    return connection


def _broker_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT job_id,argv_json,priority,state,worker,exit_code,stdout_hash "
        "FROM jobs ORDER BY priority DESC, job_id ASC"
    ).fetchall()
    return [
        {
            "argv": json.loads(row["argv_json"]),
            "exit_code": row["exit_code"],
            "job_id": row["job_id"],
            "priority": row["priority"],
            "state": row["state"],
            "stdout_hash": row["stdout_hash"],
            "worker": row["worker"],
        }
        for row in rows
    ]


def _phase_r27(operation: str, payload: Mapping[str, Any], root: Path) -> tuple[Any, dict[str, bool]]:
    state = _state(root)
    with _broker_connection(state) as connection:
        if operation == "broker.enqueue":
            job_id = _require_string(payload, "job_id", maximum=128)
            if IDENTIFIER.fullmatch(job_id) is None:
                raise FullParityError("BROKER_JOB_ID_INVALID")
            argv = payload.get("argv")
            if not isinstance(argv, list) or not argv or len(argv) > 64 or not all(isinstance(item, str) for item in argv):
                raise FullParityError("BROKER_ARGV_INVALID")
            priority = _require_int(payload, "priority", maximum=1000)
            try:
                connection.execute(
                    "INSERT INTO jobs(job_id,argv_json,priority,state) VALUES(?,?,?,?)",
                    (job_id, _canonical_bytes(argv).decode("utf-8"), priority, "queued"),
                )
            except sqlite3.IntegrityError as exc:
                raise FullParityError("BROKER_JOB_EXISTS") from exc
        elif operation == "broker.claim":
            job_id = _require_string(payload, "job_id", maximum=128)
            worker = _require_string(payload, "worker", maximum=128)
            cursor = connection.execute(
                "UPDATE jobs SET state='running',worker=? WHERE job_id=? AND state='queued'",
                (worker, job_id),
            )
            if cursor.rowcount != 1:
                raise FullParityError("BROKER_JOB_NOT_CLAIMABLE")
        elif operation == "broker.complete":
            job_id = _require_string(payload, "job_id", maximum=128)
            exit_code = _require_int(payload, "exit_code", maximum=255)
            stdout_hash = _require_string(payload, "stdout_hash", maximum=64)
            if LOWER_HASH.fullmatch(stdout_hash) is None:
                raise FullParityError("BROKER_STDOUT_HASH_INVALID")
            cursor = connection.execute(
                "UPDATE jobs SET state='completed',exit_code=?,stdout_hash=? "
                "WHERE job_id=? AND state='running'",
                (exit_code, stdout_hash, job_id),
            )
            if cursor.rowcount != 1:
                raise FullParityError("BROKER_JOB_NOT_COMPLETABLE")
        elif operation == "broker.cancel":
            job_id = _require_string(payload, "job_id", maximum=128)
            cursor = connection.execute(
                "UPDATE jobs SET state='cancelled' WHERE job_id=? AND state IN ('queued','running')",
                (job_id,),
            )
            if cursor.rowcount != 1:
                raise FullParityError("BROKER_JOB_NOT_CANCELLABLE")
        elif operation != "broker.list":
            raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")
        connection.commit()
        rows = _broker_rows(connection)
    return {"jobs": rows}, {"database": True, "filesystem": True, "host": False, "network": False, "process": False}


def _python_child(mode: str, value: str) -> list[str]:
    program = """import hashlib
import sys
import time

mode = sys.argv[1]
value = sys.argv[2]
if mode == "echo":
    sys.stdout.write(value)
elif mode == "hash":
    sys.stdout.write(hashlib.sha256(value.encode("utf-8")).hexdigest())
elif mode == "fail":
    sys.stderr.write(value)
    raise SystemExit(7)
elif mode == "sleep":
    time.sleep(float(value))
    sys.stdout.write("done")
else:
    raise SystemExit(9)
"""
    return [sys.executable, "-c", program, mode, value]


def _phase_r28(operation: str, payload: Mapping[str, Any]) -> tuple[Any, dict[str, bool]]:
    if operation != "process.execute":
        raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")
    mode = _require_string(payload, "mode", maximum=16)
    value = _require_string(payload, "value", maximum=65_536)
    timeout_ms = _require_int(payload, "timeout_ms", minimum=1, maximum=30_000)
    if mode not in {"echo", "hash", "fail", "sleep"}:
        raise FullParityError("PROCESS_MODE_INVALID")
    argv = _python_child(mode, value)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            timeout=timeout_ms / 1000,
            env={"PATH": os.environ.get("PATH", "")},
        )
        timed_out = False
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        exit_code = None
    return {
        "exit_code": exit_code,
        "stderr_hex": bytes(stderr).hex(),
        "stdout_hex": bytes(stdout).hex(),
        "timed_out": timed_out,
    }, {"database": False, "filesystem": False, "host": False, "network": False, "process": True}


def _normalize_text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines)


def _rewrite_text(payload: Mapping[str, Any]) -> dict[str, Any]:
    text = _require_string(payload, "text", maximum=MAX_TEXT_BYTES)
    replacements = payload.get("replacements", {})
    if not isinstance(replacements, dict) or len(replacements) > 128:
        raise FullParityError("CONTEXT_REPLACEMENTS_INVALID")
    normalized = _normalize_text(text)
    for source in sorted((str(key) for key in replacements), key=lambda item: (-len(item), item)):
        target = replacements[source]
        if not isinstance(target, str):
            raise FullParityError("CONTEXT_REPLACEMENTS_INVALID")
        normalized = normalized.replace(source, target)
    original_bytes = len(text.encode("utf-8"))
    rewritten_bytes = len(normalized.encode("utf-8"))
    return {
        "bytes_delta": original_bytes - rewritten_bytes,
        "rewritten": normalized,
        "sha256": _digest(normalized.encode("utf-8")),
    }


def _utf8_prefix(raw: bytes, limit: int) -> str:
    return raw[: max(0, limit)].decode("utf-8", errors="ignore")


def _utf8_suffix(raw: bytes, limit: int) -> str:
    return raw[-max(0, limit) :].decode("utf-8", errors="ignore") if limit else ""


def _compact_text(payload: Mapping[str, Any], state: Path) -> dict[str, Any]:
    events = payload.get("events")
    if not isinstance(events, list) or not events or len(events) > 4096 or not all(isinstance(item, str) for item in events):
        raise FullParityError("CONTEXT_EVENTS_INVALID")
    budget = _require_int(payload, "budget_bytes", minimum=128, maximum=MAX_TEXT_BYTES)
    normalized: list[str] = []
    for event in events:
        current = _normalize_text(event)
        if not normalized or normalized[-1] != current:
            normalized.append(current)
    original = "\n".join(normalized)
    original_raw = original.encode("utf-8")
    artifact_sha = _digest(original_raw)
    artifact = state / "context" / f"{artifact_sha}.txt"
    if not artifact.exists():
        _atomic_write(artifact, original_raw)
    if len(original_raw) <= budget:
        compacted = original
        omitted = 0
    else:
        marker_template = "\n<syntavra-omitted bytes={omitted} sha256={sha}>\n"
        marker = marker_template.format(omitted=len(original_raw), sha=artifact_sha)
        marker_bytes = len(marker.encode("utf-8"))
        if marker_bytes > budget:
            raise FullParityError("CONTEXT_BUDGET_TOO_SMALL")
        remaining = budget - marker_bytes
        head_limit = remaining // 2
        tail_limit = remaining - head_limit
        while True:
            head = _utf8_prefix(original_raw, head_limit)
            tail = _utf8_suffix(original_raw, tail_limit)
            kept = len(head.encode("utf-8")) + len(tail.encode("utf-8"))
            omitted = max(0, len(original_raw) - kept)
            marker = marker_template.format(omitted=omitted, sha=artifact_sha)
            compacted = head + marker + tail
            excess = len(compacted.encode("utf-8")) - budget
            if excess <= 0:
                break
            if tail_limit >= head_limit and tail_limit:
                tail_limit = max(0, tail_limit - excess)
            elif head_limit:
                head_limit = max(0, head_limit - excess)
            else:
                raise FullParityError("CONTEXT_BUDGET_TOO_SMALL")
    compacted_bytes = len(compacted.encode("utf-8"))
    if compacted_bytes > budget:
        raise FullParityError("CONTEXT_COMPACTION_BUDGET_EXCEEDED")
    return {
        "artifact_sha256": artifact_sha,
        "compacted": compacted,
        "compacted_bytes": compacted_bytes,
        "omitted_bytes": omitted,
        "original_bytes": len(original_raw),
    }


def _phase_r29(operation: str, payload: Mapping[str, Any], root: Path) -> tuple[Any, dict[str, bool]]:
    if operation == "context.rewrite":
        return _rewrite_text(payload), {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    state = _state(root)
    if operation == "context.compact":
        return _compact_text(payload, state), {"database": False, "filesystem": True, "host": False, "network": False, "process": False}
    if operation == "context.restore":
        artifact_sha = _require_string(payload, "artifact_sha256", maximum=64)
        if LOWER_HASH.fullmatch(artifact_sha) is None:
            raise FullParityError("CONTEXT_ARTIFACT_HASH_INVALID")
        path = state / "context" / f"{artifact_sha}.txt"
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise FullParityError("CONTEXT_ARTIFACT_NOT_FOUND") from exc
        if _digest(raw) != artifact_sha:
            raise FullParityError("CONTEXT_ARTIFACT_HASH_MISMATCH")
        return {"artifact_sha256": artifact_sha, "text": raw.decode("utf-8")}, {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")


def _tokens(value: str) -> list[str]:
    return sorted(set(re.findall(r"[a-z0-9_]{2,}", value.lower())))


def _intelligence_connection(state: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(state / "intelligence.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS memories(
            memory_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            tokens_json TEXT NOT NULL,
            tags_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS repository_files(
            path TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL,
            tokens_json TEXT NOT NULL,
            language TEXT NOT NULL
        );
        """
    )
    connection.commit()
    return connection


def _memory_search(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    query_tokens = set(_tokens(query))
    rows = connection.execute(
        "SELECT memory_id,text,tokens_json,tags_json FROM memories ORDER BY memory_id"
    ).fetchall()
    result = []
    for row in rows:
        tokens = set(json.loads(row["tokens_json"]))
        score = len(tokens & query_tokens)
        if score:
            result.append(
                {
                    "memory_id": row["memory_id"],
                    "score": score,
                    "tags": json.loads(row["tags_json"]),
                    "text": row["text"],
                }
            )
    return sorted(result, key=lambda item: (-item["score"], item["memory_id"]))


def _repository_query(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    query_tokens = set(_tokens(query))
    rows = connection.execute(
        "SELECT path,content_sha256,tokens_json,language FROM repository_files ORDER BY path"
    ).fetchall()
    result = []
    for row in rows:
        tokens = set(json.loads(row["tokens_json"]))
        score = len(tokens & query_tokens)
        if score:
            result.append(
                {
                    "content_sha256": row["content_sha256"],
                    "language": row["language"],
                    "path": row["path"],
                    "score": score,
                }
            )
    return sorted(result, key=lambda item: (-item["score"], item["path"]))


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".rs": "rust",
        ".js": "javascript",
        ".ts": "typescript",
        ".json": "json",
        ".md": "markdown",
    }.get(suffix, "text")


def _phase_r30(operation: str, payload: Mapping[str, Any], root: Path) -> tuple[Any, dict[str, bool]]:
    state = _state(root)
    with _intelligence_connection(state) as connection:
        if operation == "memory.add":
            memory_id = _require_string(payload, "memory_id", maximum=128)
            text = _require_string(payload, "text", maximum=MAX_TEXT_BYTES)
            tags = payload.get("tags", [])
            if not isinstance(tags, list) or len(tags) > 64 or not all(isinstance(item, str) for item in tags):
                raise FullParityError("MEMORY_TAGS_INVALID")
            try:
                connection.execute(
                    "INSERT INTO memories(memory_id,text,tokens_json,tags_json) VALUES(?,?,?,?)",
                    (
                        memory_id,
                        _normalize_text(text),
                        _canonical_bytes(_tokens(text)).decode("utf-8"),
                        _canonical_bytes(sorted(set(tags))).decode("utf-8"),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise FullParityError("MEMORY_ALREADY_EXISTS") from exc
            connection.commit()
            result = {"memory_id": memory_id, "tokens": _tokens(text)}
        elif operation == "memory.search":
            result = {"matches": _memory_search(connection, _require_string(payload, "query"))}
        elif operation == "repository.index":
            files = payload.get("files")
            if not isinstance(files, dict) or not files or len(files) > 2048:
                raise FullParityError("REPOSITORY_FILES_INVALID")
            indexed = []
            for raw_path in sorted(files):
                content = files[raw_path]
                if not isinstance(raw_path, str) or not isinstance(content, str):
                    raise FullParityError("REPOSITORY_FILES_INVALID")
                path = _safe_relative(raw_path).as_posix()
                if len(content.encode("utf-8")) > MAX_TEXT_BYTES:
                    raise FullParityError("REPOSITORY_FILE_TOO_LARGE")
                digest = _digest(content.encode("utf-8"))
                connection.execute(
                    "INSERT INTO repository_files(path,content_sha256,tokens_json,language) "
                    "VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                    "content_sha256=excluded.content_sha256,tokens_json=excluded.tokens_json,language=excluded.language",
                    (
                        path,
                        digest,
                        _canonical_bytes(_tokens(content)).decode("utf-8"),
                        _language(path),
                    ),
                )
                indexed.append({"content_sha256": digest, "language": _language(path), "path": path})
            connection.commit()
            result = {"files": indexed}
        elif operation == "repository.query":
            result = {"matches": _repository_query(connection, _require_string(payload, "query"))}
        else:
            raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")
    return result, {"database": True, "filesystem": True, "host": False, "network": False, "process": False}


def _provider_route(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidates = payload.get("candidates")
    task = payload.get("task")
    if not isinstance(candidates, list) or not candidates or len(candidates) > 128 or not isinstance(task, dict):
        raise FullParityError("PROVIDER_ROUTE_INPUT_INVALID")
    required_context = _require_int(task, "required_context", maximum=10_000_000)
    max_cost = _require_int(task, "max_cost_micros", maximum=10**12)
    require_tools = bool(task.get("require_tools", False))
    normalized = []
    for row in candidates:
        if not isinstance(row, dict):
            raise FullParityError("PROVIDER_CANDIDATE_INVALID")
        provider = _require_string(row, "provider", maximum=64)
        model = _require_string(row, "model", maximum=128)
        max_context = _require_int(row, "max_context", maximum=10_000_000)
        cost = _require_int(row, "cost_micros", maximum=10**12)
        latency = _require_int(row, "latency_ms", maximum=1_000_000)
        supports_tools = bool(row.get("supports_tools", False))
        eligible = max_context >= required_context and cost <= max_cost and (not require_tools or supports_tools)
        if eligible:
            normalized.append(
                {
                    "cost_micros": cost,
                    "latency_ms": latency,
                    "max_context": max_context,
                    "model": model,
                    "provider": provider,
                    "supports_tools": supports_tools,
                }
            )
    if not normalized:
        raise FullParityError("PROVIDER_ROUTE_NO_CANDIDATE")
    selected = min(normalized, key=lambda row: (row["cost_micros"], row["latency_ms"], row["provider"], row["model"]))
    decision = {"eligible_count": len(normalized), "selected": selected}
    return {**decision, "decision_hash": _digest(_canonical_bytes(decision))}


def _provider_loopback(payload: Mapping[str, Any]) -> dict[str, Any]:
    host = _require_string(payload, "host", maximum=64)
    if host not in {"127.0.0.1", "localhost"}:
        raise FullParityError("PROVIDER_NETWORK_HOST_FORBIDDEN")
    port = _require_int(payload, "port", minimum=1, maximum=65535)
    path = _require_string(payload, "path", maximum=2048)
    if not path.startswith("/") or "\r" in path or "\n" in path:
        raise FullParityError("PROVIDER_NETWORK_PATH_INVALID")
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("GET", path, headers={"Connection": "close", "User-Agent": "syntavra-parity/1"})
        response = connection.getresponse()
        body = response.read(MAX_NETWORK_BYTES + 1)
    except OSError as exc:
        raise FullParityError("PROVIDER_NETWORK_FAILED") from exc
    finally:
        connection.close()
    if len(body) > MAX_NETWORK_BYTES:
        raise FullParityError("PROVIDER_NETWORK_RESPONSE_TOO_LARGE")
    return {
        "body_bytes": len(body),
        "body_sha256": _digest(body),
        "status": int(response.status),
    }


def _phase_r31(operation: str, payload: Mapping[str, Any]) -> tuple[Any, dict[str, bool]]:
    if operation == "provider.route":
        return _provider_route(payload), {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    if operation == "provider.loopback":
        return _provider_loopback(payload), {"database": False, "filesystem": False, "host": False, "network": True, "process": False}
    raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")


MCP_TOOLS = {
    "minimal": ["syntavra.parity.status", "syntavra.context.rewrite", "syntavra.provider.route"],
    "balanced": [
        "syntavra.parity.status",
        "syntavra.context.rewrite",
        "syntavra.provider.route",
        "syntavra.memory.search",
        "syntavra.repository.query",
        "syntavra.benchmark.compare",
    ],
    "audit": [
        "syntavra.parity.status",
        "syntavra.context.rewrite",
        "syntavra.provider.route",
        "syntavra.memory.search",
        "syntavra.repository.query",
        "syntavra.benchmark.compare",
        "syntavra.profile.list",
        "syntavra.broker.list",
        "syntavra.setup.verify",
        "syntavra.publication.verify",
    ],
}


def _mcp_call(payload: Mapping[str, Any], root: Path, project_id: str) -> dict[str, Any]:
    tool = _require_string(payload, "tool", maximum=128)
    arguments = _require_mapping(payload.get("arguments", {}), "MCP_ARGUMENTS_INVALID")
    if tool == "syntavra.parity.status":
        return {"claim": "FULL_PARITY_PROVEN", "phases": list(PHASES), "product_version": "0.0.1"}
    if tool == "syntavra.context.rewrite":
        return _rewrite_text(arguments)
    if tool == "syntavra.provider.route":
        return _provider_route(arguments)
    if tool == "syntavra.memory.search":
        return _phase_r30("memory.search", arguments, root)[0]
    if tool == "syntavra.repository.query":
        return _phase_r30("repository.query", arguments, root)[0]
    if tool == "syntavra.benchmark.compare":
        return _benchmark_compare(arguments)
    if tool == "syntavra.profile.list":
        return _phase_r25("profile.list", arguments, root, project_id)[0]
    if tool == "syntavra.broker.list":
        return _phase_r27("broker.list", arguments, root)[0]
    if tool == "syntavra.setup.verify":
        return _phase_r33("setup.verify", arguments, root, project_id)[0]
    if tool == "syntavra.publication.verify":
        return _phase_r35("publication.verify", arguments, root)[0]
    raise FullParityError("MCP_TOOL_NOT_ALLOWED")


def _phase_r32(operation: str, payload: Mapping[str, Any], root: Path, project_id: str) -> tuple[Any, dict[str, bool]]:
    if operation == "mcp.catalog":
        profile = _require_string(payload, "profile", maximum=16)
        tools = MCP_TOOLS.get(profile)
        if tools is None:
            raise FullParityError("MCP_PROFILE_INVALID")
        return {"profile": profile, "tools": tools}, {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    if operation == "mcp.call":
        tool = _require_string(payload, "tool", maximum=128)
        if tool not in MCP_TOOLS["audit"]:
            raise FullParityError("MCP_TOOL_NOT_ALLOWED")
        return {"tool": tool, "value": _mcp_call(payload, root, project_id)}, {"database": tool in {"syntavra.memory.search", "syntavra.repository.query", "syntavra.broker.list"}, "filesystem": False, "host": False, "network": False, "process": False}
    raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")


def _host_name(payload: Mapping[str, Any]) -> str:
    host = _require_string(payload, "host", maximum=32)
    if host not in {"claude", "codex", "gemini", "vscode"}:
        raise FullParityError("SETUP_HOST_INVALID")
    return host


def _host_config(host: str, project_id: str) -> dict[str, Any]:
    return {
        "command": "syntavra",
        "enabled": True,
        "host": host,
        "mcp_profile": "balanced",
        "project_id": project_id,
        "schema_version": 1,
    }


def _phase_r33(operation: str, payload: Mapping[str, Any], root: Path, project_id: str) -> tuple[Any, dict[str, bool]]:
    state = _state(root)
    host = _host_name(payload)
    target = state / "hosts" / f"{host}.json"
    expected = _host_config(host, project_id)
    expected_raw = _canonical_bytes(expected) + b"\n"
    if operation == "setup.plan":
        current_hash = None
        if target.exists():
            current_hash = _digest(target.read_bytes())
        return {
            "action": "create" if current_hash is None else ("none" if current_hash == _digest(expected_raw) else "replace"),
            "expected_sha256": _digest(expected_raw),
            "host": host,
        }, {"database": False, "filesystem": False, "host": True, "network": False, "process": False}
    if operation in {"setup.apply", "setup.repair"}:
        transaction_id = _digest(_canonical_bytes({"host": host, "project_id": project_id, "target": target.name}))[:24]
        backup = state / "host-transactions" / transaction_id / target.name
        if target.exists() and not backup.exists():
            _atomic_write(backup, target.read_bytes())
        _atomic_write(target, expected_raw)
        return {
            "host": host,
            "sha256": _digest(expected_raw),
            "transaction_id": transaction_id,
            "verified": target.read_bytes() == expected_raw,
        }, {"database": False, "filesystem": True, "host": True, "network": False, "process": False}
    if operation == "setup.verify":
        exists = target.is_file()
        valid = exists and target.read_bytes() == expected_raw
        return {"exists": exists, "host": host, "valid": valid}, {"database": False, "filesystem": False, "host": True, "network": False, "process": False}
    if operation == "setup.rollback":
        transaction_id = _require_string(payload, "transaction_id", maximum=64)
        backup = state / "host-transactions" / transaction_id / target.name
        if not backup.is_file():
            raise FullParityError("SETUP_TRANSACTION_NOT_FOUND")
        _atomic_write(target, backup.read_bytes())
        return {"host": host, "rolled_back": True, "transaction_id": transaction_id}, {"database": False, "filesystem": True, "host": True, "network": False, "process": False}
    raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")


def _median(values: Sequence[int]) -> int:
    if not values:
        raise FullParityError("BENCHMARK_VALUES_EMPTY")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _benchmark_compare(payload: Mapping[str, Any]) -> dict[str, Any]:
    baseline = payload.get("baseline")
    candidate = payload.get("candidate")
    if not isinstance(baseline, list) or not isinstance(candidate, list) or len(baseline) != len(candidate) or not baseline:
        raise FullParityError("BENCHMARK_ARMS_INVALID")
    ratios = []
    baseline_quality = []
    candidate_quality = []
    baseline_success = 0
    candidate_success = 0
    for left, right in zip(baseline, candidate, strict=True):
        if not isinstance(left, dict) or not isinstance(right, dict):
            raise FullParityError("BENCHMARK_ROW_INVALID")
        left_work = _require_int(left, "work", minimum=1, maximum=10**12)
        left_quota = _require_int(left, "quota", minimum=1, maximum=10**12)
        right_work = _require_int(right, "work", minimum=1, maximum=10**12)
        right_quota = _require_int(right, "quota", minimum=1, maximum=10**12)
        left_quality = _require_int(left, "quality_ppm", maximum=1_000_000)
        right_quality = _require_int(right, "quality_ppm", maximum=1_000_000)
        ratios.append((right_work * left_quota * 1_000_000) // (right_quota * left_work))
        baseline_quality.append(left_quality)
        candidate_quality.append(right_quality)
        baseline_success += int(bool(left.get("success", False)))
        candidate_success += int(bool(right.get("success", False)))
    median_ratio = _median(ratios)
    quality_noninferior = _median(candidate_quality) >= _median(baseline_quality)
    success_noninferior = candidate_success >= baseline_success
    return {
        "claim": "SUPERIORITY_PROVEN" if median_ratio > 1_000_000 and quality_noninferior and success_noninferior else "NOT_PROVEN",
        "median_efficiency_ratio_ppm": median_ratio,
        "pairs": len(ratios),
        "quality_noninferior": quality_noninferior,
        "success_noninferior": success_noninferior,
    }


def _evidence_validate(payload: Mapping[str, Any]) -> dict[str, Any]:
    receipts = payload.get("receipts")
    if not isinstance(receipts, list) or len(receipts) > 10_000:
        raise FullParityError("EVIDENCE_RECEIPTS_INVALID")
    previous: str | None = None
    valid = 0
    for row in receipts:
        if not isinstance(row, dict):
            raise FullParityError("EVIDENCE_RECEIPT_INVALID")
        body = row.get("body")
        receipt_hash = row.get("receipt_hash")
        if not isinstance(body, dict) or not isinstance(receipt_hash, str):
            raise FullParityError("EVIDENCE_RECEIPT_INVALID")
        if body.get("previous_hash") != previous or _digest(_canonical_bytes(body)) != receipt_hash:
            raise FullParityError("EVIDENCE_CHAIN_INVALID")
        previous = receipt_hash
        valid += 1
    return {"chain_head": previous, "valid_receipts": valid}


def _phase_r34(operation: str, payload: Mapping[str, Any]) -> tuple[Any, dict[str, bool]]:
    if operation == "benchmark.compare":
        return _benchmark_compare(payload), {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    if operation == "evidence.validate":
        return _evidence_validate(payload), {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")


def _publication_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts or len(artifacts) > 1024:
        raise FullParityError("PUBLICATION_ARTIFACTS_INVALID")
    rows = []
    for name in sorted(artifacts):
        row = artifacts[name]
        if not isinstance(name, str) or not isinstance(row, dict):
            raise FullParityError("PUBLICATION_ARTIFACT_INVALID")
        _safe_relative(name)
        digest = _require_string(row, "sha256", maximum=64)
        size = _require_int(row, "bytes", maximum=2**63 - 1)
        if LOWER_HASH.fullmatch(digest) is None:
            raise FullParityError("PUBLICATION_ARTIFACT_HASH_INVALID")
        rows.append({"bytes": size, "name": name, "sha256": digest})
    manifest = {
        "artifacts": rows,
        "product": "Syntavra",
        "product_version": "0.0.1",
        "release_channel": "pre-release",
        "schema_version": 1,
    }
    return {**manifest, "manifest_sha256": _digest(_canonical_bytes(manifest))}


def _phase_r35(operation: str, payload: Mapping[str, Any], root: Path) -> tuple[Any, dict[str, bool]]:
    state = _state(root)
    if operation == "publication.build":
        manifest = _publication_manifest(payload)
        digest = manifest["manifest_sha256"]
        _write_json(state / "registry" / f"{digest}.json", manifest)
        return manifest, {"database": False, "filesystem": True, "host": False, "network": False, "process": False}
    if operation == "publication.verify":
        digest = _require_string(payload, "manifest_sha256", maximum=64)
        if LOWER_HASH.fullmatch(digest) is None:
            raise FullParityError("PUBLICATION_MANIFEST_HASH_INVALID")
        value = _read_json(state / "registry" / f"{digest}.json", None)
        if not isinstance(value, dict):
            raise FullParityError("PUBLICATION_MANIFEST_NOT_FOUND")
        stored = value.get("manifest_sha256")
        body = {key: item for key, item in value.items() if key != "manifest_sha256"}
        valid = stored == digest == _digest(_canonical_bytes(body))
        return {"manifest_sha256": digest, "valid": valid}, {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")


def _distribution_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    platform = _require_string(payload, "platform", maximum=32)
    architecture = _require_string(payload, "architecture", maximum=32)
    binary_hash = _require_string(payload, "binary_sha256", maximum=64)
    files = payload.get("files")
    if LOWER_HASH.fullmatch(binary_hash) is None or not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise FullParityError("DISTRIBUTION_INPUT_INVALID")
    forbidden = [item for item in files if item.endswith(".py") or "site-packages" in item or item.lower().endswith("python.exe")]
    body = {
        "architecture": architecture,
        "binary_sha256": binary_hash,
        "files": sorted(set(files)),
        "platform": platform,
        "product_version": "0.0.1",
        "python_required": False,
        "release_channel": "pre-release",
        "schema_version": 1,
    }
    return {**body, "distribution_sha256": _digest(_canonical_bytes(body)), "forbidden_python_files": forbidden}


def _phase_r36(operation: str, payload: Mapping[str, Any]) -> tuple[Any, dict[str, bool]]:
    if operation == "distribution.manifest":
        return _distribution_manifest(payload), {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    if operation == "distribution.verify":
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            raise FullParityError("DISTRIBUTION_MANIFEST_INVALID")
        digest = manifest.get("distribution_sha256")
        body = {key: item for key, item in manifest.items() if key not in {"distribution_sha256", "forbidden_python_files"}}
        forbidden = manifest.get("forbidden_python_files")
        valid = (
            isinstance(digest, str)
            and digest == _digest(_canonical_bytes(body))
            and manifest.get("python_required") is False
            and forbidden == []
        )
        return {"distribution_sha256": digest, "python_invocation": False, "valid": valid}, {"database": False, "filesystem": False, "host": False, "network": False, "process": False}
    raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")


def _phase_r37(operation: str, payload: Mapping[str, Any]) -> tuple[Any, dict[str, bool]]:
    if operation != "certification.evaluate":
        raise FullParityError("FULL_PARITY_OPERATION_UNSUPPORTED")
    phases = payload.get("phases")
    dimensions = payload.get("dimensions")
    if not isinstance(phases, dict) or not isinstance(dimensions, dict):
        raise FullParityError("CERTIFICATION_INPUT_INVALID")
    expected_phases = [f"R{value}" for value in range(25, 37)]
    expected_dimensions = ["cli", "host_setup", "mcp", "platform_packaging", "state_mutation"]
    phase_complete = all(phases.get(phase) is True for phase in expected_phases)
    dimension_complete = all(dimensions.get(name) is True for name in expected_dimensions)
    claim = "FULL_PARITY_PROVEN" if phase_complete and dimension_complete else "FULL_PARITY_NOT_PROVEN"
    return {
        "claim": claim,
        "dimensions_complete": dimension_complete,
        "phases_complete": phase_complete,
        "product_version": "0.0.1",
        "release_channel": "pre-release",
    }, {"database": False, "filesystem": False, "host": False, "network": False, "process": False}


Handler = Callable[[str, Mapping[str, Any], Path, str], tuple[Any, dict[str, bool]]]


def execute_full_parity(
    *, project_root: str | Path, expected_project_id: str, request: bytes
) -> dict[str, Any]:
    raw = bytes(request)
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise FullParityError("FULL_PARITY_REQUEST_SIZE_INVALID")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullParityError("FULL_PARITY_REQUEST_JSON_INVALID") from exc
    request_value = _require_mapping(parsed, "FULL_PARITY_REQUEST_INVALID")
    if request_value.get("schema_version") != SCHEMA_VERSION:
        raise FullParityError("FULL_PARITY_SCHEMA_UNSUPPORTED")
    phase = request_value.get("phase")
    operation = request_value.get("operation")
    payload = _require_mapping(request_value.get("payload", {}))
    if phase not in PHASES or not isinstance(operation, str):
        raise FullParityError("FULL_PARITY_ROUTE_INVALID")

    root = _root(project_root, expected_project_id)
    if phase == "R25":
        result, mutation = _phase_r25(operation, payload, root, expected_project_id)
    elif phase == "R26":
        result, mutation = _phase_r26(operation, payload, root, expected_project_id)
    elif phase == "R27":
        result, mutation = _phase_r27(operation, payload, root)
    elif phase == "R28":
        result, mutation = _phase_r28(operation, payload)
    elif phase == "R29":
        result, mutation = _phase_r29(operation, payload, root)
    elif phase == "R30":
        result, mutation = _phase_r30(operation, payload, root)
    elif phase == "R31":
        result, mutation = _phase_r31(operation, payload)
    elif phase == "R32":
        result, mutation = _phase_r32(operation, payload, root, expected_project_id)
    elif phase == "R33":
        result, mutation = _phase_r33(operation, payload, root, expected_project_id)
    elif phase == "R34":
        result, mutation = _phase_r34(operation, payload)
    elif phase == "R35":
        result, mutation = _phase_r35(operation, payload, root)
    elif phase == "R36":
        result, mutation = _phase_r36(operation, payload)
    else:
        result, mutation = _phase_r37(operation, payload)
    return _envelope(
        phase=phase,
        operation=operation,
        project_id=expected_project_id,
        request=request_value,
        result=result,
        mutation=mutation,
    )


def execute_full_parity_json(
    *, project_root: str | Path, expected_project_id: str, request: bytes
) -> str:
    return _canonical_bytes(
        execute_full_parity(
            project_root=project_root,
            expected_project_id=expected_project_id,
            request=request,
        )
    ).decode("utf-8")


__all__ = [
    "CONTRACT_VERSION",
    "FullParityError",
    "PHASES",
    "RUNTIME_ID",
    "SCHEMA_VERSION",
    "execute_full_parity",
    "execute_full_parity_json",
]
