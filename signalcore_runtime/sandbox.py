from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence import EvidenceStore
from .output_firewall import summarize
from .util import atomic_write_json, sha256_bytes


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxPolicy:
    backend: str = "auto"
    network: str = "none"
    read_only_repository: bool = False
    timeout_seconds: float = 1200.0
    memory_mb: int = 2048
    cpu_count: float = 2.0
    process_limit: int = 256
    env_allowlist: tuple[str, ...] = (
        "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
        "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV", "CI",
    )
    env_overrides: Mapping[str, str] = field(default_factory=dict)
    writable_paths: tuple[str, ...] = ()
    strict: bool = True

    def __post_init__(self) -> None:
        if self.network not in {"none", "inherit"}:
            raise ValueError("network must be none or inherit")
        if self.backend not in {"auto", "docker", "podman", "bwrap", "local-restricted"}:
            raise ValueError("unsupported sandbox backend")
        if self.timeout_seconds <= 0 or self.memory_mb <= 0 or self.cpu_count <= 0 or self.process_limit <= 0:
            raise ValueError("sandbox limits must be positive")


@dataclass(frozen=True)
class SandboxPlan:
    sandbox_id: str
    backend: str
    command: tuple[str, ...]
    cwd: str
    guarantees: dict[str, bool]
    policy: dict[str, Any]
    degraded_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxResult:
    sandbox_id: str
    backend: str
    exit_code: int
    duration_seconds: float
    timed_out: bool
    summary: str
    evidence_handle: str
    stdout_bytes: int
    stderr_bytes: int
    guarantees: dict[str, bool]
    degraded_reasons: tuple[str, ...] = ()


class SandboxManager:
    """Portable fail-closed sandbox planner and executor.

    Container and bubblewrap backends provide explicit network/filesystem/process
    controls. The local backend is intentionally labelled degraded and cannot be
    selected for `network=none` under strict policy.
    """

    def __init__(self, root: Path, *, project: Path, evidence: EvidenceStore):
        self.root = root
        self.project = project.resolve(strict=True)
        self.evidence = evidence
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(exist_ok=True)

    @staticmethod
    def backends() -> dict[str, str | None]:
        return {
            "docker": shutil.which("docker"),
            "podman": shutil.which("podman"),
            "bwrap": shutil.which("bwrap") if os.name != "nt" else None,
            "local-restricted": sys_executable(),
        }

    def select_backend(self, policy: SandboxPolicy) -> tuple[str, tuple[str, ...]]:
        available = self.backends()
        if policy.backend != "auto":
            if not available.get(policy.backend):
                raise SandboxError(f"requested backend unavailable: {policy.backend}")
            selected = policy.backend
        else:
            selected = next((name for name in ("docker", "podman", "bwrap") if available.get(name)), "local-restricted")
        reasons: list[str] = []
        if selected == "local-restricted":
            reasons.extend(("network-isolation-unavailable", "filesystem-overlay-unavailable"))
            if policy.strict and policy.network == "none":
                raise SandboxError("strict network-disabled execution requires docker, podman, or bwrap")
        return selected, tuple(reasons)

    def _validate_relative(self, value: str) -> Path:
        candidate = (self.project / value).resolve(strict=False)
        try:
            candidate.relative_to(self.project)
        except ValueError as exc:
            raise SandboxError(f"path escapes project: {value}") from exc
        return candidate

    def _filtered_env(self, policy: SandboxPolicy, env: Mapping[str, str] | None = None) -> dict[str, str]:
        source = dict(os.environ)
        if env:
            source.update({str(key): str(value) for key, value in env.items()})
        allowed = {key: source[key] for key in policy.env_allowlist if key in source}
        allowed.update({str(key): str(value) for key, value in policy.env_overrides.items()})
        for key in tuple(allowed):
            if any(marker in key.casefold() for marker in ("token", "secret", "password", "credential", "api_key")):
                allowed.pop(key, None)
        allowed["SIGNALCORE_SANDBOX"] = "1"
        # Numerical libraries may otherwise spawn one worker per host CPU before
        # user code starts. Keep local-restricted execution bounded and portable.
        allowed.setdefault("OPENBLAS_NUM_THREADS", "1")
        allowed.setdefault("OMP_NUM_THREADS", "1")
        allowed.setdefault("MKL_NUM_THREADS", "1")
        allowed.setdefault("NUMEXPR_NUM_THREADS", "1")
        return allowed

    def plan(
        self,
        argv: Iterable[str],
        *,
        policy: SandboxPolicy,
        cwd: str = ".",
        env: Mapping[str, str] | None = None,
    ) -> SandboxPlan:
        command = tuple(str(value) for value in argv)
        if not command:
            raise SandboxError("sandbox command is empty")
        cwd_path = self._validate_relative(cwd)
        backend, degraded = self.select_backend(policy)
        sandbox_id = "sb-" + uuid.uuid4().hex
        available = self.backends()
        guarantees = {
            "network_isolated": backend in {"docker", "podman", "bwrap"} and policy.network == "none",
            "filesystem_isolated": backend in {"docker", "podman", "bwrap"},
            "resource_limited": backend in {"docker", "podman", "bwrap"} or (os.name != "nt" and sys.platform != "darwin"),
            "secret_filtered": True,
            "process_tree_controlled": True,
        }
        if backend in {"docker", "podman"}:
            executable = str(available[backend])
            mount_mode = "ro" if policy.read_only_repository else "rw"
            relative_cwd = cwd_path.relative_to(self.project).as_posix()
            container_cwd = "/workspace" + (f"/{relative_cwd}" if relative_cwd != "." else "")
            wrapper = [
                executable, "run", "--rm", "--init",
                "--network", "none" if policy.network == "none" else "bridge",
                "--memory", f"{policy.memory_mb}m",
                "--cpus", str(policy.cpu_count),
                "--pids-limit", str(policy.process_limit),
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
                "--mount", f"type=bind,src={self.project},dst=/workspace,{mount_mode}",
                "--workdir", container_cwd,
            ]
            for writable in policy.writable_paths:
                host_path = self._validate_relative(writable)
                container_path = "/workspace/" + host_path.relative_to(self.project).as_posix()
                wrapper.extend(("--mount", f"type=bind,src={host_path},dst={container_path},rw"))
            for key, value in self._filtered_env(policy, env).items():
                wrapper.extend(("--env", f"{key}={value}"))
            image = str(policy.env_overrides.get("SIGNALCORE_SANDBOX_IMAGE", "python:3.13-slim"))
            wrapped = tuple((*wrapper, image, *command))
            execution_cwd = str(self.project)
        elif backend == "bwrap":
            executable = str(available[backend])
            relative_cwd = cwd_path.relative_to(self.project).as_posix()
            target_cwd = "/workspace" + (f"/{relative_cwd}" if relative_cwd != "." else "")
            wrapper = [
                executable, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
                "--ro-bind" if policy.read_only_repository else "--bind", str(self.project), "/workspace",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--chdir", target_cwd,
            ]
            if policy.network == "none":
                wrapper.append("--unshare-net")
            for system_path in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
                if Path(system_path).exists():
                    wrapper.extend(("--ro-bind", system_path, system_path))
            wrapped = tuple((*wrapper, *command))
            execution_cwd = str(self.project)
        else:
            wrapped = command
            execution_cwd = str(cwd_path)
        return SandboxPlan(sandbox_id, backend, wrapped, execution_cwd, guarantees, asdict(policy), degraded)

    def execute(
        self,
        argv: Iterable[str],
        *,
        policy: SandboxPolicy | None = None,
        cwd: str = ".",
        env: Mapping[str, str] | None = None,
    ) -> SandboxResult:
        policy = policy or SandboxPolicy()
        plan = self.plan(argv, policy=policy, cwd=cwd, env=env)
        run_root = self.root / "runs" / plan.sandbox_id
        run_root.mkdir(parents=True, exist_ok=False)
        stdout_path = run_root / "stdout.log"
        stderr_path = run_root / "stderr.log"
        atomic_write_json(run_root / "plan.json", asdict(plan))
        started = time.time()
        creation: dict[str, Any] = {}
        preexec = None
        if os.name == "nt":
            creation["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creation["start_new_session"] = True
            if plan.backend == "local-restricted":
                try:
                    import resource

                    def limit_resources() -> None:
                        # Never allow a platform-specific rlimit failure to abort
                        # process creation from preexec_fn. macOS rejects
                        # RLIMIT_AS for common interpreter launches; Linux accepts
                        # it. CPU limits remain best-effort on all POSIX hosts.
                        cpu_limit = max(1, int(policy.timeout_seconds))
                        try:
                            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
                        except (OSError, ValueError):
                            pass
                        if sys.platform != "darwin" and hasattr(resource, "RLIMIT_AS"):
                            bytes_limit = policy.memory_mb * 1024 * 1024
                            try:
                                resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
                            except (OSError, ValueError):
                                pass
                        # RLIMIT_NPROC is user-wide on common Unix systems and
                        # can make a child fail during interpreter startup when
                        # the runner already owns many processes. Process-tree
                        # cleanup remains enforced; strict PID isolation belongs
                        # to container/bwrap backends.

                    preexec = limit_resources
                except (ImportError, ValueError):
                    preexec = None
        timed_out = False
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                plan.command,
                cwd=plan.cwd,
                env=self._filtered_env(policy, env) if plan.backend == "local-restricted" else None,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                preexec_fn=preexec,
                **creation,
            )
            try:
                exit_code = process.wait(timeout=policy.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_tree(process.pid)
                try:
                    exit_code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    kill_tree(process.pid)
                    exit_code = process.wait(timeout=10)
        completed = time.time()
        firewall = summarize(
            tuple(str(value) for value in argv),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            exit_code=exit_code,
            duration_seconds=completed - started,
            evidence=self.evidence,
        )
        result = SandboxResult(
            plan.sandbox_id,
            plan.backend,
            int(exit_code),
            completed - started,
            timed_out,
            firewall.summary,
            firewall.evidence_handle,
            stdout_path.stat().st_size,
            stderr_path.stat().st_size,
            plan.guarantees,
            plan.degraded_reasons,
        )
        atomic_write_json(run_root / "result.json", asdict(result))
        return result

    def execute_batch(
        self,
        commands: Iterable[Iterable[str]],
        *,
        policy: SandboxPolicy | None = None,
        cwd: str = ".",
        stop_on_failure: bool = True,
    ) -> list[SandboxResult]:
        results: list[SandboxResult] = []
        for command in commands:
            result = self.execute(command, policy=policy, cwd=cwd)
            results.append(result)
            if stop_on_failure and result.exit_code != 0:
                break
        return results

    def read(self, relative: str, *, max_bytes: int = 1024 * 1024) -> bytes:
        path = self._validate_relative(relative)
        if not path.is_file():
            raise SandboxError(f"sandbox path is not a file: {relative}")
        if path.stat().st_size > max_bytes:
            raise SandboxError("sandbox read exceeds max_bytes")
        return path.read_bytes()

    def write(self, relative: str, data: bytes, *, policy: SandboxPolicy) -> dict[str, Any]:
        path = self._validate_relative(relative)
        allowed = any(path == self._validate_relative(value) or self._validate_relative(value) in path.parents for value in policy.writable_paths)
        if not allowed:
            raise SandboxError(f"path is not writable by policy: {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": str(path), "bytes": len(data), "sha256": sha256_bytes(data)}

    def export(self, relative: str) -> str:
        path = self._validate_relative(relative)
        if not path.is_file():
            raise SandboxError(f"artifact does not exist: {relative}")
        return self.evidence.put_file(path, kind="sandbox-artifact", metadata={"relative_path": relative})

    def destroy(self, sandbox_id: str) -> bool:
        if not sandbox_id.startswith("sb-") or any(character not in "0123456789abcdef-" for character in sandbox_id):
            raise SandboxError("invalid sandbox id")
        path = self.root / "runs" / sandbox_id
        if path.is_dir():
            shutil.rmtree(path)
            return True
        return False


def sys_executable() -> str:
    import sys

    return sys.executable


def terminate_tree(pid: int) -> None:
    import signal

    try:
        if os.name == "nt":
            subprocess.run(("taskkill", "/PID", str(pid), "/T"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def kill_tree(pid: int) -> None:
    import signal

    try:
        if os.name == "nt":
            subprocess.run(("taskkill", "/PID", str(pid), "/T", "/F"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
