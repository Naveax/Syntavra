from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


_SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY", "CREDENTIAL")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class SandboxPolicy:
    workspace: Path
    writable_paths: tuple[Path, ...] = ()
    network_hosts: tuple[str, ...] = ()
    timeout_seconds: float = 300.0
    memory_bytes: int | None = None
    cpu_seconds: int | None = None
    allow_child_processes: bool = True
    strict_native: bool = False
    environment_allowlist: tuple[str, ...] = ("PATH", "HOME", "USER", "USERNAME", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "COMSPEC")

    def normalized(self) -> "SandboxPolicy":
        workspace = self.workspace.resolve(strict=True)
        writable = tuple(path.resolve(strict=False) for path in (self.writable_paths or (workspace,)))
        for path in writable:
            try:
                path.relative_to(workspace)
            except ValueError as error:
                raise ValueError(f"writable path escapes workspace: {path}") from error
        return SandboxPolicy(
            workspace=workspace,
            writable_paths=writable,
            network_hosts=tuple(sorted(set(self.network_hosts))),
            timeout_seconds=max(0.1, float(self.timeout_seconds)),
            memory_bytes=self.memory_bytes,
            cpu_seconds=self.cpu_seconds,
            allow_child_processes=self.allow_child_processes,
            strict_native=self.strict_native,
            environment_allowlist=self.environment_allowlist,
        )


@dataclass(frozen=True)
class SandboxBackend:
    name: str
    platform: str
    available: bool
    enforced: tuple[str, ...]
    unsupported: tuple[str, ...]
    command_prefix: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class ExecutionReceipt:
    receipt_id: str
    command: tuple[str, ...]
    cwd: str
    backend: SandboxBackend
    started_at: str
    duration_ms: float
    exit_code: int
    timed_out: bool
    stdout: str
    stderr: str
    stdout_sha256: str
    stderr_sha256: str
    environment_keys: tuple[str, ...]
    policy: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class NativeSandboxBroker:
    """Cross-platform process broker with honest backend capability reporting.

    The broker prefers native isolation when available. `strict_native=True` makes
    missing enforcement a hard error instead of silently claiming a sandbox.
    """

    def __init__(self, state_root: Path | None = None):
        self.state_root = state_root.resolve(strict=False) if state_root else None
        if self.state_root:
            self.state_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _linux_backend(policy: SandboxPolicy) -> SandboxBackend:
        if shutil.which("bwrap"):
            prefix = [
                "bwrap",
                "--die-with-parent",
                "--new-session",
                "--unshare-user",
                "--unshare-pid",
                "--unshare-uts",
                "--unshare-ipc",
                "--ro-bind", "/", "/",
                "--proc", "/proc",
                "--dev", "/dev",
                "--chdir", str(policy.workspace),
            ]
            for path in policy.writable_paths:
                prefix.extend(("--bind", str(path), str(path)))
            if not policy.network_hosts:
                prefix.append("--unshare-net")
            return SandboxBackend(
                name="bubblewrap",
                platform="linux",
                available=True,
                enforced=("mount-namespace", "pid-namespace", "user-namespace", "process-tree", "filesystem-boundary") + (("network-namespace",) if not policy.network_hosts else ()),
                unsupported=("domain-level-egress",) if policy.network_hosts else (),
                command_prefix=tuple(prefix),
            )
        if shutil.which("unshare"):
            prefix = ["unshare", "--fork", "--pid", "--mount-proc"]
            if not policy.network_hosts:
                prefix.append("--net")
            return SandboxBackend(
                name="unshare",
                platform="linux",
                available=True,
                enforced=("pid-namespace", "process-tree") + (("network-namespace",) if not policy.network_hosts else ()),
                unsupported=("filesystem-boundary", "seccomp", "cgroup", "domain-level-egress"),
                command_prefix=tuple(prefix),
                detail="partial native backend; filesystem containment relies on workspace validation",
            )
        return SandboxBackend(
            name="portable-process-boundary",
            platform="linux",
            available=False,
            enforced=("cwd-boundary", "environment-filter", "timeout", "process-group"),
            unsupported=("mount-namespace", "network-namespace", "seccomp", "cgroup"),
            detail="install bubblewrap for full native isolation",
        )

    @staticmethod
    def _macos_backend(policy: SandboxPolicy) -> SandboxBackend:
        executable = shutil.which("sandbox-exec")
        if executable:
            network = "(allow network*)" if policy.network_hosts else "(deny network*)"
            writes = " ".join(f'(subpath "{path}")' for path in policy.writable_paths)
            profile = f"(version 1) (deny default) (import \"system.sb\") (allow file-read*) (allow file-write* {writes}) (allow process-exec) {network}"
            return SandboxBackend(
                name="sandbox-exec",
                platform="darwin",
                available=True,
                enforced=("filesystem-boundary", "process-policy", "network-policy"),
                unsupported=("domain-level-egress", "memory-limit"),
                command_prefix=(executable, "-p", profile),
            )
        return SandboxBackend(
            name="portable-process-boundary",
            platform="darwin",
            available=False,
            enforced=("cwd-boundary", "environment-filter", "timeout", "process-group"),
            unsupported=("sandbox-profile", "network-policy", "keychain-policy"),
            detail="sandbox-exec is unavailable on this host",
        )

    @staticmethod
    def _windows_backend(_: SandboxPolicy) -> SandboxBackend:
        return SandboxBackend(
            name="windows-process-group",
            platform="windows",
            available=True,
            enforced=("cwd-boundary", "environment-filter", "timeout", "process-tree"),
            unsupported=("restricted-token", "job-memory-limit", "appcontainer", "network-policy", "registry-boundary"),
            detail="portable implementation uses a new process group and taskkill; stronger backends can be supplied by a native plugin",
        )

    def backend(self, policy: SandboxPolicy) -> SandboxBackend:
        system = platform.system().casefold()
        if system == "linux":
            return self._linux_backend(policy)
        if system == "darwin":
            return self._macos_backend(policy)
        if system == "windows":
            return self._windows_backend(policy)
        return SandboxBackend(
            name="portable-process-boundary",
            platform=system or "unknown",
            available=False,
            enforced=("cwd-boundary", "environment-filter", "timeout"),
            unsupported=("native-isolation",),
        )

    @staticmethod
    def _cwd(workspace: Path, cwd: Path | None) -> Path:
        selected = (cwd or workspace).resolve(strict=True)
        try:
            selected.relative_to(workspace)
        except ValueError as error:
            raise PermissionError(f"working directory escapes workspace: {selected}") from error
        return selected

    @staticmethod
    def _environment(policy: SandboxPolicy, extra: Mapping[str, str] | None) -> dict[str, str]:
        allowed = set(policy.environment_allowlist)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in allowed and not any(marker in key.upper() for marker in _SECRET_MARKERS)
        }
        for key, value in dict(extra or {}).items():
            if any(marker in key.upper() for marker in _SECRET_MARKERS):
                raise PermissionError(f"secret-like environment key is not agent-visible: {key}")
            environment[str(key)] = str(value)
        environment["SYNTAVRA_SANDBOX"] = "1"
        environment["SYNTAVRA_WORKSPACE"] = str(policy.workspace)
        return environment

    @staticmethod
    def _limit_resources(policy: SandboxPolicy):
        def apply() -> None:
            if platform.system().casefold() == "windows":
                return
            try:
                import resource

                if policy.memory_bytes:
                    resource.setrlimit(resource.RLIMIT_AS, (policy.memory_bytes, policy.memory_bytes))
                if policy.cpu_seconds:
                    resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
            except (ImportError, OSError, ValueError):
                if policy.strict_native:
                    raise
        return apply

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def run(
        self,
        command: Sequence[str],
        *,
        policy: SandboxPolicy,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
    ) -> ExecutionReceipt:
        normalized = policy.normalized()
        if not command or any(not isinstance(item, str) or "\x00" in item for item in command):
            raise ValueError("command must be a non-empty argv sequence")
        selected_cwd = self._cwd(normalized.workspace, cwd)
        backend = self.backend(normalized)
        if normalized.strict_native and (not backend.available or backend.unsupported):
            raise RuntimeError(f"required native sandbox controls unavailable: {backend.unsupported}")
        argv = [*backend.command_prefix, *command]
        env = self._environment(normalized, environment)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        started_at = _now()
        started = time.monotonic()
        process = subprocess.Popen(
            argv,
            cwd=selected_cwd,
            env=env,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
            preexec_fn=self._limit_resources(normalized) if os.name != "nt" else None,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(input=input_bytes, timeout=normalized.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate(process)
            stdout, stderr = process.communicate()
        duration = (time.monotonic() - started) * 1000.0
        receipt_body = {
            "command": list(command),
            "cwd": str(selected_cwd),
            "backend": asdict(backend),
            "started_at": started_at,
            "duration_ms": round(duration, 3),
            "exit_code": int(process.returncode if process.returncode is not None else -1),
            "timed_out": timed_out,
            "stdout_sha256": _hash(stdout),
            "stderr_sha256": _hash(stderr),
        }
        receipt_id = "sha256:" + _hash(json.dumps(receipt_body, sort_keys=True, separators=(",", ":")).encode())
        receipt = ExecutionReceipt(
            receipt_id=receipt_id,
            command=tuple(command),
            cwd=str(selected_cwd),
            backend=backend,
            started_at=started_at,
            duration_ms=round(duration, 3),
            exit_code=receipt_body["exit_code"],
            timed_out=timed_out,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            stdout_sha256=receipt_body["stdout_sha256"],
            stderr_sha256=receipt_body["stderr_sha256"],
            environment_keys=tuple(sorted(env)),
            policy={
                "workspace": str(normalized.workspace),
                "writable_paths": [str(path) for path in normalized.writable_paths],
                "network_hosts": list(normalized.network_hosts),
                "timeout_seconds": normalized.timeout_seconds,
                "memory_bytes": normalized.memory_bytes,
                "cpu_seconds": normalized.cpu_seconds,
                "allow_child_processes": normalized.allow_child_processes,
                "strict_native": normalized.strict_native,
            },
        )
        if self.state_root:
            destination = self.state_root / "execution-receipts" / f"{receipt_id.split(':', 1)[1]}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
                json.dump(asdict(receipt), handle, ensure_ascii=False, sort_keys=True, indent=2)
                temporary = Path(handle.name)
            os.replace(temporary, destination)
        return receipt

    def describe(self, workspace: Path) -> dict[str, Any]:
        policy = SandboxPolicy(workspace=workspace)
        backend = self.backend(policy.normalized())
        return {"ok": True, "backend": asdict(backend), "strict_ready": backend.available and not backend.unsupported}


__all__ = ["ExecutionReceipt", "NativeSandboxBroker", "SandboxBackend", "SandboxPolicy"]
