from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .host_adapters import KNOWN_HOSTS, detect_hosts, host_spec, negotiate
from .util import atomic_write_json, sha256_bytes


@dataclass(frozen=True)
class InstallChange:
    host: str
    path: str
    action: str
    backup: str = ""
    mode: str = ""


class InstallerError(RuntimeError):
    pass


class HostInstaller:
    """Idempotent, backup-first installer for native skill, MCP and hook surfaces."""

    def __init__(
        self,
        *,
        project: Path,
        skill_root: Path,
        home: Path | None = None,
        executable: tuple[str, ...] | None = None,
    ):
        self.project = project.resolve(strict=True)
        self.skill_root = skill_root.resolve(strict=True)
        self.home = (home or Path.home()).resolve(strict=False)
        self.executable = executable or (sys.executable, "-m", "signalcore_runtime")
        self.state_root = self.project / ".signalcore" / "install"
        self.backup_root = self.state_root / "backups"
        self.manifest_path = self.state_root / "manifest.json"

    def detect(self) -> list[dict[str, Any]]:
        return detect_hosts(self.project, home=self.home)

    def _backup(self, path: Path) -> str:
        if not path.exists():
            return ""
        stamp = f"{int(time.time() * 1000)}-{sha256_bytes(str(path).encode())[:12]}"
        destination = self.backup_root / stamp / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, destination)
        else:
            shutil.copy2(path, destination)
        return str(destination)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _mcp_entry(self) -> dict[str, Any]:
        return {
            "command": self.executable[0],
            "args": [*self.executable[1:], "--project", str(self.project), "mcp", "serve"],
            "cwd": str(self.project),
            "env": {"SIGNALCORE_PROJECT": str(self.project)},
        }

    def _hook_entry(self, phase: str) -> dict[str, Any]:
        return {
            "matcher": "shell|bash|exec|command|terminal|*",
            "hooks": [{
                "type": "command",
                "command": " ".join((*self.executable, "--project", str(self.project), "hook", phase)),
                "timeout": 30,
            }],
        }

    def _render_config(self, host: str, existing: dict[str, Any]) -> dict[str, Any]:
        value = json.loads(json.dumps(existing))
        spec = host_spec(host)
        if spec.supports_mcp:
            key = "mcpServers"
            servers = value.setdefault(key, {})
            servers["signalcore"] = self._mcp_entry()
        if spec.supports_pre_tool_hook or spec.supports_post_tool_hook:
            hooks = value.setdefault("hooks", {})
            if spec.supports_pre_tool_hook:
                entries = [row for row in hooks.get("PreToolUse", []) if row.get("signalcoreManaged") is not True]
                entries.append({**self._hook_entry("pre"), "signalcoreManaged": True})
                hooks["PreToolUse"] = entries
            if spec.supports_post_tool_hook:
                entries = [row for row in hooks.get("PostToolUse", []) if row.get("signalcoreManaged") is not True]
                entries.append({**self._hook_entry("post"), "signalcoreManaged": True})
                hooks["PostToolUse"] = entries
        value.setdefault("signalcore", {}).update({
            "managed": True,
            "version": "0.6.0",
            "project": str(self.project),
            "mode": negotiate(host)["mode"],
        })
        return value

    def _install_skill(self, host: str, *, scope: str) -> InstallChange | None:
        spec = host_spec(host)
        if not spec.skill_path or spec.skill_path == "AGENTS.md" or spec.skill_path.endswith((".md", ".mdc")):
            return self._install_instruction(host, scope=scope)
        base = self.project if scope == "project" else self.home
        destination = base / spec.skill_path
        backup = self._backup(destination)
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.skill_root, destination)
        return InstallChange(host, str(destination), "copy-skill", backup, negotiate(host)["mode"])

    def _install_instruction(self, host: str, *, scope: str) -> InstallChange | None:
        spec = host_spec(host)
        if not spec.skill_path:
            return None
        base = self.project if scope == "project" else self.home
        destination = base / spec.skill_path
        backup = self._backup(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        marker_start = "<!-- SIGNALCORE-MANAGED-START -->"
        marker_end = "<!-- SIGNALCORE-MANAGED-END -->"
        block = (
            f"{marker_start}\n"
            "# SignalCore\n"
            "Use the local SignalCore runtime before broad repository reads, long-running commands, "
            "large tool outputs, context compaction, or verifier reuse. Run `signalcore doctor` when enforcement is uncertain.\n"
            f"{marker_end}\n"
        )
        existing = destination.read_text(encoding="utf-8", errors="replace") if destination.is_file() else ""
        if marker_start in existing and marker_end in existing:
            prefix, tail = existing.split(marker_start, 1)
            _, suffix = tail.split(marker_end, 1)
            text = prefix.rstrip() + "\n\n" + block + suffix.lstrip("\n")
        else:
            text = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
        destination.write_text(text, encoding="utf-8", newline="\n")
        return InstallChange(host, str(destination), "write-instruction", backup, negotiate(host)["mode"])

    def _install_config(self, host: str, *, scope: str) -> InstallChange | None:
        spec = host_spec(host)
        if not spec.config_path:
            return None
        base = self.project if scope == "project" else self.home
        destination = base / spec.config_path
        existing = self._load_json(destination)
        rendered = self._render_config(host, existing)
        canonical = json.dumps(rendered, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if destination.is_file() and destination.read_text(encoding="utf-8", errors="replace") == canonical:
            return InstallChange(host, str(destination), "unchanged", "", negotiate(host, installed=True)["mode"])
        backup = self._backup(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(canonical, encoding="utf-8", newline="\n")
        return InstallChange(host, str(destination), "merge-config", backup, negotiate(host, installed=True)["mode"])

    def plan(self, hosts: Iterable[str], *, scope: str = "project") -> dict[str, Any]:
        resolved = tuple(dict.fromkeys(host.casefold() for host in hosts))
        unknown = [host for host in resolved if host not in KNOWN_HOSTS]
        if unknown:
            raise InstallerError(f"unknown hosts: {', '.join(unknown)}")
        return {
            "scope": scope,
            "hosts": resolved,
            "changes": [
                {
                    "host": host,
                    "skill": str((self.project if scope == "project" else self.home) / host_spec(host).skill_path) if host_spec(host).skill_path else None,
                    "config": str((self.project if scope == "project" else self.home) / host_spec(host).config_path) if host_spec(host).config_path else None,
                    "mode": negotiate(host)["mode"],
                }
                for host in resolved
            ],
        }

    def install(self, hosts: Iterable[str], *, scope: str = "project", dry_run: bool = False) -> dict[str, Any]:
        plan = self.plan(hosts, scope=scope)
        if dry_run:
            return {"ok": True, "dry_run": True, **plan}
        previous = self._load_json(self.manifest_path) if self.manifest_path.is_file() else {}
        original_by_path = {
            str(row.get("path")): row
            for row in previous.get("changes", [])
            if row.get("path")
        }
        observed: list[InstallChange] = []
        for host in plan["hosts"]:
            if change := self._install_skill(host, scope=scope):
                observed.append(change)
            if change := self._install_config(host, scope=scope):
                observed.append(change)
        merged_by_path: dict[str, dict[str, Any]] = dict(original_by_path)
        for change in observed:
            row = asdict(change)
            prior = merged_by_path.get(change.path)
            if prior is not None:
                # Reinstallation must preserve the first pre-SignalCore backup so
                # uninstall remains a true rollback rather than restoring a
                # previously managed configuration.
                row["backup"] = prior.get("backup", "")
            merged_by_path[change.path] = row
        manifest = {
            "schema_version": 2,
            "version": "0.6.0",
            "project": str(self.project),
            "scope": scope,
            "changes": list(merged_by_path.values()),
            "created_at": previous.get("created_at", time.time()),
            "updated_at": time.time(),
        }
        atomic_write_json(self.manifest_path, manifest)
        return {"ok": True, "dry_run": False, "changes": list(merged_by_path.values()), "manifest": str(self.manifest_path)}

    def uninstall(self, *, dry_run: bool = False) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            return {"ok": True, "changes": [], "reason": "not-installed"}
        manifest = self._load_json(self.manifest_path)
        changes: list[dict[str, Any]] = []
        for row in reversed(manifest.get("changes", [])):
            path = Path(row["path"])
            backup = Path(row["backup"]) if row.get("backup") else None
            changes.append({"path": str(path), "restore": str(backup) if backup else None})
            if dry_run:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            if backup and backup.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                if backup.is_dir():
                    shutil.copytree(backup, path)
                else:
                    shutil.copy2(backup, path)
        if not dry_run:
            self.manifest_path.unlink(missing_ok=True)
        return {"ok": True, "dry_run": dry_run, "changes": changes}

    def doctor(self) -> dict[str, Any]:
        detected = self.detect()
        installed = self._load_json(self.manifest_path) if self.manifest_path.is_file() else None
        checks = {
            "skill_root": self.skill_root.is_dir() and (self.skill_root / "SKILL.md").is_file(),
            "project_writable": os.access(self.project, os.W_OK),
            "state_writable": self.state_root.exists() or os.access(self.state_root.parent, os.W_OK),
            "manifest_valid": installed is None or installed.get("version") == "0.6.0",
        }
        return {
            "ok": all(checks.values()),
            "checks": checks,
            "detected_hosts": detected,
            "installed": installed,
            "available_hosts": sorted(KNOWN_HOSTS),
        }
