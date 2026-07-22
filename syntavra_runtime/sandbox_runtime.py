from __future__ import annotations

import os
import platform
import subprocess
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .execution_sandbox import NativeSandboxBroker, SandboxBackend, SandboxPolicy


class HardenedSandboxBroker(NativeSandboxBroker):
    """Native sandbox broker with executable enforcement probes.

    Merely finding ``bwrap``, ``unshare`` or ``sandbox-exec`` is not enough: hosted
    runners and locked-down machines frequently ship a binary while denying the
    kernel capability it needs. The probe result is cached per backend and a failed
    probe falls back to the honest portable process boundary. ``strict_native``
    therefore remains fail-closed.
    """

    _probe_lock = threading.Lock()
    _probe_cache: dict[tuple[str, ...], tuple[bool, str]] = {}

    @classmethod
    def _probe(cls, command: tuple[str, ...], *, timeout: float = 5.0) -> tuple[bool, str]:
        with cls._probe_lock:
            cached = cls._probe_cache.get(command)
        if cached is not None:
            return cached
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
            detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()[-1000:]
            value = (result.returncode == 0, detail)
        except (OSError, subprocess.SubprocessError) as error:
            value = (False, f"{type(error).__name__}: {error}")
        with cls._probe_lock:
            cls._probe_cache[command] = value
        return value

    @staticmethod
    def _portable(system: str, detail: str, unsupported: tuple[str, ...]) -> SandboxBackend:
        return SandboxBackend(
            name="portable-process-boundary",
            platform=system,
            available=False,
            enforced=("cwd-boundary", "environment-filter", "timeout", "process-group"),
            unsupported=unsupported,
            detail=detail,
        )

    def backend(self, policy: SandboxPolicy) -> SandboxBackend:
        system = platform.system().casefold()
        selected = super().backend(policy)
        if selected.name == "bubblewrap":
            probe = (
                "bwrap",
                "--die-with-parent",
                "--new-session",
                "--unshare-user",
                "--unshare-pid",
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "/bin/true",
            )
            ok, detail = self._probe(probe)
            if not ok:
                return self._portable(
                    "linux",
                    f"bubblewrap probe failed: {detail or 'kernel rejected namespace setup'}",
                    ("mount-namespace", "network-namespace", "seccomp", "cgroup"),
                )
        elif selected.name == "unshare":
            probe = ("unshare", "--fork", "--pid", "--mount-proc", "/bin/true")
            ok, detail = self._probe(probe)
            if not ok:
                return self._portable(
                    "linux",
                    f"unshare probe failed: {detail or 'kernel rejected namespace setup'}",
                    ("mount-namespace", "network-namespace", "seccomp", "cgroup"),
                )
        elif selected.name == "sandbox-exec":
            probe = ("sandbox-exec", "-p", "(version 1) (allow default)", "/usr/bin/true")
            ok, detail = self._probe(probe)
            if not ok:
                return self._portable(
                    "darwin",
                    f"sandbox-exec probe failed: {detail or 'sandbox profile execution failed'}",
                    ("sandbox-profile", "network-policy", "keychain-policy"),
                )
        elif system == "windows":
            # The stdlib implementation currently enforces process-group termination,
            # environment filtering and timeouts. Job Object / restricted-token support
            # is intentionally not claimed until the native helper is present.
            return SandboxBackend(
                name="windows-process-boundary",
                platform="windows",
                available=True,
                enforced=("cwd-boundary", "environment-filter", "timeout", "process-tree"),
                unsupported=("job-object", "restricted-token", "appcontainer", "network-policy", "registry-boundary"),
                detail="native helper required for Job Object and restricted-token enforcement",
            )
        return selected

    def health(self, workspace: Path) -> dict[str, Any]:
        normalized = SandboxPolicy(workspace=workspace).normalized()
        backend = self.backend(normalized)
        return {
            "ok": True,
            "backend": asdict(backend),
            "strict_ready": backend.available and not backend.unsupported,
            "fail_closed": True,
            "probe_cached": backend.name in {"bubblewrap", "unshare", "sandbox-exec"},
        }


__all__ = ["HardenedSandboxBroker"]
