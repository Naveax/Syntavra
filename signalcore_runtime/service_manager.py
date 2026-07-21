from __future__ import annotations

import hashlib
import os
import plistlib
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: tuple[str, ...]
    environment_file: str = ""
    working_directory: str = ""
    description: str = "SignalCore provider proxy"
    restart_seconds: int = 3

    def __post_init__(self) -> None:
        if not self.name or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in self.name):
            raise ValueError("service name must be machine-safe")
        if not self.command or any(not str(item) for item in self.command):
            raise ValueError("service command must be a non-empty argv tuple")
        if self.restart_seconds < 1 or self.restart_seconds > 3600:
            raise ValueError("restart_seconds out of bounds")


@dataclass(frozen=True)
class ServicePlan:
    platform: str
    service_name: str
    descriptor_path: str
    descriptor_hash: str
    descriptor: str
    activation_argv: tuple[str, ...]
    deactivation_argv: tuple[str, ...]
    user_scoped: bool


class ProviderProxyServiceManager:
    """Cross-platform user-service renderer and installer.

    Installation is user-scoped and refuses symlink destinations. Activation is
    opt-in so configuration can be reviewed before a service manager is modified.
    """

    def __init__(self, home: Path | str | None = None):
        self.home = Path(home or Path.home()).expanduser().resolve(strict=False)

    @staticmethod
    def _platform(value: str | None = None) -> str:
        normalized = (value or sys.platform).casefold()
        if normalized.startswith("linux"):
            return "linux"
        if normalized.startswith("darwin") or normalized.startswith("mac"):
            return "darwin"
        if normalized.startswith("win"):
            return "windows"
        raise ValueError(f"unsupported service platform: {value or sys.platform}")

    def plan(self, spec: ServiceSpec, *, platform_name: str | None = None) -> ServicePlan:
        platform_name = self._platform(platform_name)
        if platform_name == "linux":
            path = self.home / ".config" / "systemd" / "user" / f"{spec.name}.service"
            descriptor = self._systemd(spec)
            activation = ("systemctl", "--user", "enable", "--now", f"{spec.name}.service")
            deactivation = ("systemctl", "--user", "disable", "--now", f"{spec.name}.service")
        elif platform_name == "darwin":
            label = f"dev.signalcore.{spec.name}"
            path = self.home / "Library" / "LaunchAgents" / f"{label}.plist"
            descriptor = self._launchd(spec, label)
            activation = ("launchctl", "bootstrap", f"gui/{os.getuid()}", str(path))
            deactivation = ("launchctl", "bootout", f"gui/{os.getuid()}", str(path))
        else:
            path = self.home / "AppData" / "Local" / "SignalCore" / "services" / f"{spec.name}.xml"
            descriptor = self._windows_task(spec)
            activation = ("schtasks", "/Create", "/TN", spec.name, "/XML", str(path), "/F")
            deactivation = ("schtasks", "/Delete", "/TN", spec.name, "/F")
        digest = hashlib.sha256(descriptor.encode("utf-8")).hexdigest()
        return ServicePlan(
            platform=platform_name,
            service_name=spec.name,
            descriptor_path=str(path),
            descriptor_hash=digest,
            descriptor=descriptor,
            activation_argv=activation,
            deactivation_argv=deactivation,
            user_scoped=True,
        )

    @staticmethod
    def _systemd(spec: ServiceSpec) -> str:
        command = " ".join(shlex.quote(str(item)) for item in spec.command)
        lines = [
            "[Unit]",
            f"Description={spec.description}",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={command}",
            "Restart=on-failure",
            f"RestartSec={spec.restart_seconds}",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
        ]
        if spec.working_directory:
            lines.append(f"WorkingDirectory={spec.working_directory}")
        if spec.environment_file:
            lines.append(f"EnvironmentFile={spec.environment_file}")
        lines.extend(["", "[Install]", "WantedBy=default.target", ""])
        return "\n".join(lines)

    @staticmethod
    def _launchd(spec: ServiceSpec, label: str) -> str:
        payload: dict[str, Any] = {
            "Label": label,
            "ProgramArguments": [str(item) for item in spec.command],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": spec.restart_seconds,
            "ProcessType": "Background",
        }
        if spec.working_directory:
            payload["WorkingDirectory"] = spec.working_directory
        if spec.environment_file:
            payload["EnvironmentVariables"] = {"SIGNALCORE_ENV_FILE": spec.environment_file}
        return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")

    @staticmethod
    def _windows_task(spec: ServiceSpec) -> str:
        executable = escape(str(spec.command[0]))
        arguments = escape(subprocess.list2cmdline([str(item) for item in spec.command[1:]]))
        working = escape(spec.working_directory)
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(spec.description)}</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><RestartOnFailure><Interval>PT{spec.restart_seconds}S</Interval><Count>999</Count></RestartOnFailure><ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>{executable}</Command><Arguments>{arguments}</Arguments>{f'<WorkingDirectory>{working}</WorkingDirectory>' if working else ''}</Exec></Actions>
</Task>
'''

    def install(
        self,
        spec: ServiceSpec,
        *,
        platform_name: str | None = None,
        activate: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan(spec, platform_name=platform_name)
        path = Path(plan.descriptor_path)
        self._validate_destination(path)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(plan.descriptor, encoding="utf-8", newline="\n")
            os.replace(temporary, path)
            if activate and plan.platform == "linux":
                subprocess.run(("systemctl", "--user", "daemon-reload"), check=True, stdin=subprocess.DEVNULL)
            if activate:
                subprocess.run(plan.activation_argv, check=True, stdin=subprocess.DEVNULL)
        return {"ok": True, "dry_run": dry_run, "activated": bool(activate and not dry_run), "plan": asdict(plan)}

    def verify(self, spec: ServiceSpec, *, platform_name: str | None = None) -> dict[str, Any]:
        plan = self.plan(spec, platform_name=platform_name)
        path = Path(plan.descriptor_path)
        exists = path.is_file() and not path.is_symlink()
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest() if exists else ""
        return {
            "ok": bool(exists and actual_hash == plan.descriptor_hash),
            "exists": exists,
            "expected_hash": plan.descriptor_hash,
            "actual_hash": actual_hash,
            "path": str(path),
        }

    def uninstall(
        self,
        spec: ServiceSpec,
        *,
        platform_name: str | None = None,
        deactivate: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan(spec, platform_name=platform_name)
        path = Path(plan.descriptor_path)
        self._validate_destination(path)
        if not dry_run:
            if deactivate and path.exists():
                subprocess.run(plan.deactivation_argv, check=False, stdin=subprocess.DEVNULL)
            if path.exists():
                path.unlink()
            if deactivate and plan.platform == "linux":
                subprocess.run(("systemctl", "--user", "daemon-reload"), check=False, stdin=subprocess.DEVNULL)
        return {"ok": True, "dry_run": dry_run, "removed": path.exists() is False, "path": str(path)}

    def _validate_destination(self, path: Path) -> None:
        try:
            path.relative_to(self.home)
        except ValueError as exc:
            raise ValueError("service descriptor must remain under the user home") from exc
        current = path
        while current != self.home and current != current.parent:
            if current.exists() and current.is_symlink():
                raise ValueError(f"symlink service path component is forbidden: {current}")
            current = current.parent
