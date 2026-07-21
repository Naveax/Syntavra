from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .host_adapters import KNOWN_HOSTS, detect_hosts
from .host_installation import HostInstallationManager
from .integration_matrix import HOSTS, IntegrationMatrix
from .product_surface import MCP_PROFILES, PlatformAdapterRegistry, ProductSurface, SessionAnalyticsStore
from .proxy_product import ProxyProductRegistry
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
    installable_hosts: tuple[str, ...]
    contract_only_hosts: tuple[str, ...]
    estimated_seconds: float
    one_command: bool = True
    mental_model: tuple[str, ...] = ("setup", "status", "run", "prove")


class ZeroFrictionManager:
    """One-command, backup-first, verified pre-release product installer."""

    def __init__(self, project_root: Path, state_root: Path | None = None):
        self.project_root = project_root.resolve(strict=False)
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.state_root = (state_root or self.project_root / ".signalcore" / "pre-release").resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)

    def _skill_root(self) -> Path:
        repository_skill = self.project_root / "skills" / "signal-core"
        if (repository_skill / "SKILL.md").is_file():
            return repository_skill
        bundled = Path(__file__).resolve().parent / "bundled_skill"
        if not (bundled / "SKILL.md").is_file():
            raise FileNotFoundError("SignalCore bundled skill is unavailable")
        return bundled

    def _host_manager(self) -> HostInstallationManager:
        return HostInstallationManager(
            self.state_root / "host-installations.sqlite3",
            project=self.project_root,
            skill_root=self._skill_root(),
        )

    def detected_hosts(self) -> tuple[str, ...]:
        return tuple(sorted({row["host"] for row in detect_hosts(self.project_root)}))

    @staticmethod
    def _matrix_hosts() -> tuple[str, ...]:
        return tuple(item.integration_id for item in HOSTS)

    def _targets(self, *, all_hosts: bool) -> tuple[str, ...]:
        candidates = self._matrix_hosts() if all_hosts else self.detected_hosts()
        return tuple(sorted({host for host in candidates if host in KNOWN_HOSTS and host != "generic-mcp"}))

    def install_plan(self, *, all_hosts: bool = False, profile: str = "minimal") -> InstallPlan:
        if profile not in MCP_PROFILES:
            raise ValueError(f"unknown MCP profile: {profile}")
        detected = self.detected_hosts()
        targets = self._targets(all_hosts=all_hosts)
        contract_only = tuple(sorted(set(self._matrix_hosts()) - set(KNOWN_HOSTS)))
        actions: list[InstallAction] = [
            InstallAction("backup", "existing-config", str(self.state_root / "host-installations"), True, "per-host backup-first transaction"),
            InstallAction("write", "runtime-config", str(self.state_root / "config.json"), True, "canonical pre-release config"),
            InstallAction("write", "product-surface", str(self.state_root / "product.json"), True, "four-command mental model"),
            InstallAction("write", f"mcp-profile:{profile}", str(self.state_root / "mcp-profile.json"), True, "bounded tool visibility"),
            InstallAction("write", "platform-adapters", str(self.state_root / "platform-adapters.json"), True, "concrete host config candidates"),
            InstallAction("install", "local-proxy", str(self.state_root / "proxy"), True, "credential-isolated provider gateway"),
        ]
        for host in targets:
            actions.append(InstallAction("configure-and-verify", host, str(self.project_root), True, "atomic native hook/MCP/skill integration"))
        actions.extend((
            InstallAction("verify", "doctor", str(self.project_root), False, "post-install verification"),
            InstallAction("record", "installation-receipt", str(self.state_root / "install-receipt.json"), False, "measured onboarding and rollback evidence"),
        ))
        return InstallPlan(
            VERSION,
            CHANNEL,
            str(self.project_root),
            tuple(actions),
            detected,
            targets,
            contract_only,
            min(59.0, 5.0 + len(actions) * 1.5),
        )

    def _host_installations(self, targets: tuple[str, ...], *, dry_run: bool) -> tuple[list[dict[str, Any]], list[str]]:
        if not targets:
            return [], []
        manager = self._host_manager()
        results: list[dict[str, Any]] = []
        applied: list[str] = []
        try:
            for host in targets:
                result = manager.apply(host, scope="project", dry_run=dry_run)
                results.append(asdict(result))
                if not dry_run:
                    applied.append(result.transaction_id)
        except Exception:
            for transaction_id in reversed(applied):
                try:
                    manager.rollback(transaction_id)
                except Exception:
                    pass
            raise
        return results, applied

    def install(self, *, all_hosts: bool = False, dry_run: bool = True, profile: str = "minimal") -> dict[str, Any]:
        started = time.perf_counter()
        started_at = time.time()
        plan = self.install_plan(all_hosts=all_hosts, profile=profile)
        host_results: list[dict[str, Any]] = []
        applied_transactions: list[str] = []
        setup_bundle: dict[str, Any] | None = None
        try:
            host_results, applied_transactions = self._host_installations(plan.installable_hosts, dry_run=dry_run)
            if not dry_run:
                config = {
                    "version": VERSION,
                    "channel": CHANNEL,
                    "project_root": str(self.project_root),
                    "hosts": list(plan.installable_hosts),
                    "host_transactions": applied_transactions,
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
                    "host_results": host_results,
                    "host_transactions": applied_transactions,
                    "onboarding_claim": "MEASURED_LOCAL_INSTALL_AND_HOST_VERIFICATION",
                }
                atomic_write_json(self.state_root / "install-receipt.json", receipt, mode=0o600)
        except Exception as error:
            if applied_transactions:
                manager = self._host_manager()
                for transaction_id in reversed(applied_transactions):
                    try:
                        manager.rollback(transaction_id)
                    except Exception:
                        pass
            return {
                "ok": False,
                "dry_run": dry_run,
                "profile": profile,
                "plan": asdict(plan),
                "host_results": host_results,
                "error": f"{type(error).__name__}: {error}",
                "rolled_back_transactions": applied_transactions,
                "wall_time_ms": (time.perf_counter() - started) * 1000.0,
            }
        return {
            "ok": True,
            "dry_run": dry_run,
            "profile": profile,
            "plan": asdict(plan),
            "setup_bundle": setup_bundle,
            "host_results": host_results,
            "host_transactions": applied_transactions,
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

    def _installed_config(self) -> dict[str, Any]:
        path = self.state_root / "config.json"
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"invalid": True}
        return value if isinstance(value, dict) else {"invalid": True}

    def _verify_hosts(self, hosts: list[str]) -> list[dict[str, Any]]:
        if not hosts:
            return []
        manager = self._host_manager()
        results: list[dict[str, Any]] = []
        for host in hosts:
            try:
                results.append(manager.verify(host, scope="project"))
            except Exception as error:
                results.append({"ok": False, "host": host, "reasons": [f"{type(error).__name__}: {error}"]})
        return results

    def doctor(self) -> dict[str, Any]:
        matrix = IntegrationMatrix.validate()
        adapters = PlatformAdapterRegistry.validate()
        proxy = ProxyProductRegistry.validate()
        config = self._installed_config()
        installed = bool(config and not config.get("invalid"))
        configured_hosts = [str(item) for item in config.get("hosts", [])] if installed else []
        host_verification = self._verify_hosts(configured_hosts)
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
        failed_hosts = [row for row in host_verification if not row.get("ok")]
        if failed_hosts:
            blocking.append({"code": "host-integration-verification-failed", "repair": "signalcore repair --apply"})
        if not os.access(self.state_root, os.W_OK):
            blocking.append({"code": "state-root-not-writable", "repair": "choose a writable --state-root"})
        if not adapters["ok"]:
            blocking.append({"code": "platform-adapter-matrix-invalid", "repair": "restore packaged platform adapter registry"})
        if not proxy["ok"]:
            blocking.append({"code": "proxy-preset-matrix-invalid", "repair": "restore packaged proxy preset registry"})
        healthy = matrix["ok"] and adapters["ok"] and proxy["ok"] and not blocking
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
                "proxy_contracts": proxy,
            },
            "matrix": matrix,
            "configured_hosts": configured_hosts,
            "host_verification": host_verification,
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
                install_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                install_receipt = {"invalid": True}
        host_results = install_receipt.get("host_results", []) if isinstance(install_receipt, dict) else []
        return {
            "version": VERSION,
            "channel": CHANNEL,
            "installed": receipt_path.is_file(),
            "state_root": str(self.state_root),
            "detected_hosts": self.detected_hosts(),
            "onboarding": {
                "measured": bool(install_receipt.get("wall_time_ms") is not None),
                "wall_time_ms": install_receipt.get("wall_time_ms"),
                "host_installations": len(host_results),
                "host_verification_passed": sum(bool(row.get("verification", {}).get("ok")) for row in host_results),
                "claim": "LOCAL_INSTALL_AND_HOST_RECEIPT" if install_receipt else "ONBOARDING_NOT_MEASURED",
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
            config = self._installed_config()
            profile = str(config.get("mcp_profile") or "minimal") if config else "minimal"
            if any(item["code"] == "not-installed" for item in all_findings):
                self.install(dry_run=False, profile=profile)
            elif any(item["code"] == "product-bundle-incomplete" for item in all_findings):
                ProductSurface.setup_bundle(self.project_root, self.state_root, profile)
            if any(item["code"] == "host-integration-verification-failed" for item in all_findings):
                manager = self._host_manager()
                for host in [str(item) for item in config.get("hosts", [])]:
                    manager.apply(host, scope="project", dry_run=False)
        final = self.doctor() if apply else diagnosis
        return {"ok": final["ok"], "apply": apply, "actions": actions, "remaining": [*final["issues"], *final["warnings"]]}

    def upgrade(self, target: str = VERSION) -> dict[str, Any]:
        ReleaseIdentity().require_version(target)
        return {
            "ok": True,
            "changed": False,
            "version": VERSION,
            "channel": CHANNEL,
            "reason": "version-locked-until-owner-authorization",
        }
