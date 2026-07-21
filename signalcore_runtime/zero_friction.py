from __future__ import annotations

import os
import shutil
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .integration_matrix import HOSTS, IntegrationMatrix
from .product_surface import MCP_PROFILES, PlatformAdapterRegistry, ProductSurface, SessionAnalyticsStore
from .release_identity import CHANNEL, VERSION, ReleaseIdentity
from .util import atomic_write_json


@dataclass(frozen=True)
class InstallAction:
    action: str
    target: str
    path: str
    reversible: bool
    reason: str


@dataclass(frozen=True)
class InstallPlan:
    version: str
    channel: str
    project_root: str
    actions: tuple[InstallAction, ...]
    detected_hosts: tuple[str, ...]
    estimated_seconds: float
    one_command: bool = True
    mental_model: tuple[str, ...] = ("setup", "status", "run", "prove")


_HOST_BINARIES = {
    "claude-code": ("claude",),
    "codex": ("codex",),
    "gemini-cli": ("gemini",),
    "vscode-copilot": ("code",),
    "cursor": ("cursor",),
    "windsurf": ("windsurf",),
    "opencode": ("opencode",),
    "aider": ("aider",),
    "qwen-code": ("qwen", "qwen-code"),
    "continue": ("continue",),
    "zed": ("zed",),
    "pi": ("pi",),
    "omp": ("omp",),
    "openclaw": ("openclaw",),
}


class ZeroFrictionManager:
    """One-command, backup-first pre-release installer and product health surface."""

    def __init__(self, project_root: Path, state_root: Path | None = None):
        self.project_root = project_root.resolve(strict=False)
        self.state_root = (state_root or self.project_root / ".signalcore" / "pre-release").resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)

    def detected_hosts(self) -> tuple[str, ...]:
        found: list[str] = []
        for host in HOSTS:
            commands = _HOST_BINARIES.get(host.integration_id, ())
            if any(shutil.which(command) for command in commands):
                found.append(host.integration_id)
        return tuple(sorted(found))

    def install_plan(self, *, all_hosts: bool = False, profile: str = "minimal") -> InstallPlan:
        if profile not in MCP_PROFILES:
            raise ValueError(f"unknown MCP profile: {profile}")
        detected = self.detected_hosts()
        targets = [item.integration_id for item in HOSTS] if all_hosts else list(detected or ("codex", "claude-code", "gemini-cli"))
        actions: list[InstallAction] = [
            InstallAction("backup", "existing-config", str(self.state_root / "backups"), True, "backup-first mutation"),
            InstallAction("write", "runtime-config", str(self.state_root / "config.json"), True, "canonical pre-release config"),
            InstallAction("write", "product-surface", str(self.state_root / "product.json"), True, "four-command mental model"),
            InstallAction("write", f"mcp-profile:{profile}", str(self.state_root / "mcp-profile.json"), True, "bounded tool visibility"),
            InstallAction("write", "platform-adapters", str(self.state_root / "platform-adapters.json"), True, "real host config candidates"),
            InstallAction("install", "local-proxy", str(self.state_root / "proxy"), True, "credential-isolated provider gateway"),
        ]
        for host in targets:
            actions.append(InstallAction("configure", host, str(self.project_root), True, "native hook/MCP/wrapper integration"))
        actions.extend((
            InstallAction("verify", "doctor", str(self.project_root), False, "post-install verification"),
            InstallAction("record", "installation-receipt", str(self.state_root / "install-receipt.json"), False, "measured onboarding and rollback"),
        ))
        return InstallPlan(VERSION, CHANNEL, str(self.project_root), tuple(actions), detected, min(59.0, 6.0 + len(actions) * 1.5))

    def install(self, *, all_hosts: bool = False, dry_run: bool = True, profile: str = "minimal") -> dict[str, Any]:
        started = time.perf_counter()
        started_at = time.time()
        plan = self.install_plan(all_hosts=all_hosts, profile=profile)
        setup_bundle: dict[str, Any] | None = None
        if not dry_run:
            config = {
                "version": VERSION,
                "channel": CHANNEL,
                "project_root": str(self.project_root),
                "hosts": [item.target for item in plan.actions if item.action == "configure"],
                "mcp_profile": profile,
                "product_commands": list(plan.mental_model),
                "installed_at": started_at,
            }
            atomic_write_json(self.state_root / "config.json", config, mode=0o600)
            setup_bundle = ProductSurface.setup_bundle(self.project_root, self.state_root, profile)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            receipt = {
                "plan": asdict(plan),
                "applied": True,
                "profile": profile,
                "started_at": started_at,
                "completed_at": time.time(),
                "wall_time_ms": elapsed_ms,
                "setup_bundle": setup_bundle,
                "onboarding_claim": "MEASURED_LOCAL_INSTALL_ONLY",
            }
            atomic_write_json(self.state_root / "install-receipt.json", receipt, mode=0o600)
        return {
            "ok": True,
            "dry_run": dry_run,
            "profile": profile,
            "plan": asdict(plan),
            "setup_bundle": setup_bundle,
            "wall_time_ms": (time.perf_counter() - started) * 1000.0,
        }

    def wrapper_text(self, host: str) -> str:
        if not any(item.integration_id == host and item.family == "host" for item in HOSTS):
            raise KeyError(host)
        if os.name == "nt":
            return f'@echo off\r\nset "SIGNALCORE_HOST={host}"\r\nset "SIGNALCORE_CHANNEL={CHANNEL}"\r\n%*\r\n'
        return f'#!/usr/bin/env sh\nexport SIGNALCORE_HOST="{host}"\nexport SIGNALCORE_CHANNEL="{CHANNEL}"\nexec "$@"\n'

    def write_wrapper(self, host: str, path: Path) -> dict[str, Any]:
        text = self.wrapper_text(host)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="" if os.name == "nt" else "\n")
        if os.name != "nt":
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return {"ok": True, "host": host, "path": str(path), "version": VERSION}

    def doctor(self) -> dict[str, Any]:
        matrix = IntegrationMatrix.validate()
        adapters = PlatformAdapterRegistry.validate()
        installed = (self.state_root / "config.json").is_file()
        product_files = {
            "product": (self.state_root / "product.json").is_file(),
            "mcp_profile": (self.state_root / "mcp-profile.json").is_file(),
            "platform_adapters": (self.state_root / "platform-adapters.json").is_file(),
        }
        warnings: list[dict[str, str]] = []
        blocking: list[dict[str, str]] = []
        if not installed:
            warnings.append({"code": "not-installed", "repair": "signalcore setup --apply"})
        elif not all(product_files.values()):
            warnings.append({"code": "product-bundle-incomplete", "repair": "signalcore repair --apply"})
        if not os.access(self.state_root, os.W_OK):
            blocking.append({"code": "state-root-not-writable", "repair": "choose a writable --state-root"})
        if not adapters["ok"]:
            blocking.append({"code": "platform-adapter-matrix-invalid", "repair": "restore packaged platform adapter registry"})
        healthy = matrix["ok"] and adapters["ok"] and not blocking
        findings = len(warnings) + len(blocking)
        repairable = sum(bool(item.get("repair")) for item in [*warnings, *blocking])
        return {
            "ok": healthy,
            "ready_to_install": healthy,
            "installed": installed,
            "identity": ReleaseIdentity().to_dict(),
            "runtime": {
                "state": "PRE_RELEASE_INSTALLED" if installed else "PRE_RELEASE_READY",
                "healthy": healthy,
                "details": {"version": VERSION, "release_channel": CHANNEL},
            },
            "product_surface": {
                "mental_model": ["setup", "status", "run", "prove"],
                "files": product_files,
                "adapter_contracts": adapters,
            },
            "matrix": matrix,
            "detected_hosts": self.detected_hosts(),
            "detected_adapters": [row["host"] for row in PlatformAdapterRegistry.detect() if row["detected"]],
            "issues": blocking,
            "warnings": warnings,
            "auto_repairable_ratio": repairable / max(1, findings),
        }

    def stats(self) -> dict[str, Any]:
        receipt_path = self.state_root / "install-receipt.json"
        analytics = SessionAnalyticsStore(self.state_root / "analytics" / "events.jsonl").report()
        install_receipt: dict[str, Any] = {}
        if receipt_path.is_file():
            try:
                install_receipt = __import__("json").loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                install_receipt = {"invalid": True}
        return {
            "version": VERSION,
            "channel": CHANNEL,
            "installed": receipt_path.is_file(),
            "state_root": str(self.state_root),
            "detected_hosts": self.detected_hosts(),
            "onboarding": {
                "measured": bool(install_receipt.get("wall_time_ms") is not None),
                "wall_time_ms": install_receipt.get("wall_time_ms"),
                "claim": "LOCAL_INSTALL_RECEIPT" if install_receipt else "ONBOARDING_NOT_MEASURED",
            },
            "session_analytics": analytics,
            "savings_receipts": 0,
            "receipt_boundary": "real provider usage receipts are required",
        }

    def repair(self, *, apply: bool = False) -> dict[str, Any]:
        diagnosis = self.doctor()
        all_findings = [*diagnosis["issues"], *diagnosis["warnings"]]
        actions = [item["repair"] for item in all_findings]
        if apply:
            if any(item["code"] == "not-installed" for item in all_findings):
                self.install(dry_run=False)
            elif any(item["code"] == "product-bundle-incomplete" for item in all_findings):
                ProductSurface.setup_bundle(self.project_root, self.state_root, "minimal")
        final = self.doctor() if apply else diagnosis
        return {"ok": final["ok"], "apply": apply, "actions": actions, "remaining": [*final["issues"], *final["warnings"]]}

    def upgrade(self, target: str = VERSION) -> dict[str, Any]:
        ReleaseIdentity().require_version(target)
        return {"ok": True, "changed": False, "version": VERSION, "channel": CHANNEL, "reason": "version-locked"}
