from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .util import atomic_write_json, canonical_json, sha256_bytes


@dataclass(frozen=True)
class ArmExecutionPolicy:
    timeout_seconds: float = 1200.0
    max_artifact_bytes: int = 64 * 1024 * 1024
    max_visible_bytes: int = 16 * 1024
    require_result: bool = True
    require_receipt: bool = True
    allowed_environment_keys: tuple[str, ...] = (
        "PATH", "HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "SYSTEMROOT",
        "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "TZ",
    )

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.timeout_seconds > 14_400:
            raise ValueError("timeout_seconds out of bounds")
        if self.max_artifact_bytes < 4096 or self.max_visible_bytes < 256:
            raise ValueError("artifact limits are too small")
        if self.max_visible_bytes > self.max_artifact_bytes:
            raise ValueError("visible limit cannot exceed artifact limit")


@dataclass(frozen=True)
class ArmRunReceipt:
    schema_version: int
    run_id: str
    arm_id: str
    pair_key: str
    command_hash: str
    environment_hash: str
    workspace_hash: str
    started_at: float
    wall_seconds: float
    exit_code: int
    timed_out: bool
    result_valid: bool
    provider_receipt_valid: bool
    success: bool
    stdout_hash: str
    stderr_hash: str
    stdout_handle: str
    stderr_handle: str
    stdout_preview: str
    stderr_preview: str
    result: dict[str, Any]
    failure_reasons: tuple[str, ...]
    receipt_hash: str


class SecureArmRunner:
    """Fail-closed executable-arm runner for identical-arm benchmarks.

    Commands are argv-only, execute in a fixed workspace, receive an environment
    allowlist, and must emit a bound result document. No competitor source is imported.
    """

    schema_version = 1

    def __init__(self, root: Path | str, *, evidence: Any | None = None):
        self.root = Path(root).resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.evidence = evidence

    @staticmethod
    def validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
        if isinstance(argv, (str, bytes, bytearray)):
            raise ValueError("arm command must be an argv array; shell strings are forbidden")
        values = tuple(str(item) for item in argv)
        if not values or any(not item or "\x00" in item for item in values):
            raise ValueError("arm argv entries must be non-empty and NUL-free")
        return values

    @staticmethod
    def validate_result(value: Mapping[str, Any], *, pair_key: str, arm_id: str, require_receipt: bool) -> tuple[bool, bool, list[str]]:
        reasons: list[str] = []
        if int(value.get("schema_version", 0)) < 1:
            reasons.append("result-schema-invalid")
        if str(value.get("pair_key") or "") != pair_key:
            reasons.append("pair-key-mismatch")
        if str(value.get("arm_id") or "") != arm_id:
            reasons.append("arm-id-mismatch")
        metrics = value.get("metrics")
        if not isinstance(metrics, Mapping):
            reasons.append("metrics-missing")
        else:
            for key in ("fresh_input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"):
                try:
                    number = int(metrics.get(key, 0))
                except (TypeError, ValueError):
                    reasons.append(f"metric-invalid:{key}")
                    continue
                if number < 0:
                    reasons.append(f"metric-negative:{key}")
        receipt = value.get("provider_receipt")
        receipt_valid = isinstance(receipt, Mapping) and all(
            str(receipt.get(key) or "") for key in ("provider", "model", "request_id", "response_hash")
        )
        if require_receipt and not receipt_valid:
            reasons.append("provider-receipt-missing-or-invalid")
        return not reasons, receipt_valid, reasons

    def run(
        self,
        *,
        arm_id: str,
        pair_key: str,
        argv: Sequence[str],
        workspace: Path | str,
        request: Mapping[str, Any],
        environment: Mapping[str, str] | None = None,
        policy: ArmExecutionPolicy | None = None,
    ) -> ArmRunReceipt:
        policy = policy or ArmExecutionPolicy()
        command = self.validate_argv(argv)
        workspace_path = Path(workspace).resolve(strict=True)
        if not workspace_path.is_dir():
            raise ValueError("workspace must be a directory")
        run_id = f"arm-{arm_id}-{uuid.uuid4().hex[:12]}"
        run_root = self.root / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        request_path = run_root / "request.json"
        result_path = run_root / "result.json"
        stdout_path = run_root / "stdout.log"
        stderr_path = run_root / "stderr.log"
        request_payload = {
            "schema_version": self.schema_version,
            "run_id": run_id,
            "arm_id": arm_id,
            "pair_key": pair_key,
            "workspace": str(workspace_path),
            "result_path": str(result_path),
            "request": dict(request),
        }
        atomic_write_json(request_path, request_payload, mode=0o600)
        substituted = tuple(
            item.replace("{request}", str(request_path)).replace("{result}", str(result_path)).replace("{workspace}", str(workspace_path))
            for item in command
        )
        executable = substituted[0]
        if os.path.isabs(executable):
            executable_path = Path(executable)
            if not executable_path.is_file():
                raise FileNotFoundError(executable)
        elif shutil.which(executable) is None:
            raise FileNotFoundError(executable)

        base_environment = {
            key: value for key, value in os.environ.items()
            if key in policy.allowed_environment_keys
        }
        supplied = {str(key): str(value) for key, value in (environment or {}).items()}
        forbidden = sorted(set(supplied) - set(policy.allowed_environment_keys))
        if forbidden:
            raise ValueError("environment keys are not allowlisted: " + ", ".join(forbidden))
        base_environment.update(supplied)
        base_environment.update({
            "SIGNALBENCH_REQUEST": str(request_path),
            "SIGNALBENCH_OUTPUT": str(result_path),
            "SIGNALBENCH_WORKSPACE": str(workspace_path),
            "SIGNALBENCH_PAIR_KEY": pair_key,
            "SIGNALBENCH_ARM_ID": arm_id,
        })
        environment_hash = sha256_bytes(canonical_json({key: "<set>" for key in sorted(base_environment)}))
        command_hash = sha256_bytes(canonical_json(substituted))
        workspace_hash = sha256_bytes(str(workspace_path).encode("utf-8"))
        started = time.time()
        timed_out = False
        exit_code = 1
        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                substituted,
                cwd=workspace_path,
                env=base_environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                **popen_kwargs,
            )
            try:
                exit_code = process.wait(timeout=policy.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "nt":
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                exit_code = 124
                process.wait(timeout=10)
        wall = time.time() - started
        reasons: list[str] = []
        if timed_out:
            reasons.append("arm-timeout")
        for path, label in ((stdout_path, "stdout"), (stderr_path, "stderr")):
            if path.stat().st_size > policy.max_artifact_bytes:
                reasons.append(f"{label}-artifact-limit-exceeded")

        stdout_bytes = stdout_path.read_bytes()
        stderr_bytes = stderr_path.read_bytes()
        stdout_hash = sha256_bytes(stdout_bytes)
        stderr_hash = sha256_bytes(stderr_bytes)
        stdout_handle = ""
        stderr_handle = ""
        if self.evidence is not None:
            stdout_handle = str(self.evidence.put(stdout_bytes, kind="arm-stdout", metadata={"run_id": run_id, "arm_id": arm_id, "pair_key": pair_key}))
            stderr_handle = str(self.evidence.put(stderr_bytes, kind="arm-stderr", metadata={"run_id": run_id, "arm_id": arm_id, "pair_key": pair_key}))

        result: dict[str, Any] = {}
        if result_path.is_file():
            try:
                decoded = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(decoded, Mapping):
                    result = dict(decoded)
                else:
                    reasons.append("result-must-be-object")
            except (OSError, json.JSONDecodeError) as exc:
                reasons.append(f"result-invalid:{type(exc).__name__}")
        elif policy.require_result:
            reasons.append("result-missing")
        result_valid, receipt_valid, result_reasons = self.validate_result(
            result, pair_key=pair_key, arm_id=arm_id, require_receipt=policy.require_receipt
        ) if result else (False, False, ["result-missing"])
        reasons.extend(result_reasons)
        success = bool(exit_code == 0 and not timed_out and result_valid and result.get("success", True) and not reasons)
        payload = {
            "schema_version": self.schema_version,
            "run_id": run_id,
            "arm_id": arm_id,
            "pair_key": pair_key,
            "command_hash": command_hash,
            "environment_hash": environment_hash,
            "workspace_hash": workspace_hash,
            "started_at": started,
            "wall_seconds": wall,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "result_valid": result_valid,
            "provider_receipt_valid": receipt_valid,
            "success": success,
            "stdout_hash": stdout_hash,
            "stderr_hash": stderr_hash,
            "stdout_handle": stdout_handle,
            "stderr_handle": stderr_handle,
            "stdout_preview": self._preview(stdout_bytes, policy.max_visible_bytes),
            "stderr_preview": self._preview(stderr_bytes, policy.max_visible_bytes),
            "result": result,
            "failure_reasons": list(dict.fromkeys(reasons)),
        }
        receipt_hash = sha256_bytes(canonical_json(payload))
        receipt = ArmRunReceipt(**{**payload, "failure_reasons": tuple(payload["failure_reasons"]), "receipt_hash": receipt_hash})
        atomic_write_json(run_root / "receipt.json", asdict(receipt), mode=0o600)
        return receipt

    @staticmethod
    def _preview(value: bytes, limit: int) -> str:
        if len(value) <= limit:
            return value.decode("utf-8", errors="replace")
        suffix = "\n[… exact arm output stored …]"
        keep = max(0, limit - len(suffix.encode("utf-8")))
        return value[:keep].decode("utf-8", errors="ignore").rstrip() + suffix
