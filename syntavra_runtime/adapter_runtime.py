from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping

from .adapter_platform import ADAPTERS, AdapterContract


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class AdapterMaturity(StrEnum):
    CONTRACT = "Contract"
    CONFIGURED = "Configured"
    CONNECTED = "Connected"
    INTERCEPTING = "Intercepting"
    ENFORCED = "Enforced"
    CERTIFIED = "Certified"


@dataclass(frozen=True)
class AdapterReceipt:
    receipt_id: str
    adapter_id: str
    maturity: AdapterMaturity
    operation: str
    ok: bool
    created_at: str
    detected: bool
    changed_paths: tuple[str, ...]
    capabilities: dict[str, bool]
    checks: dict[str, Any]
    rollback: dict[str, Any] = field(default_factory=dict)
    claim_boundary: str = "Certified requires a live-host external execution receipt"


class AdapterPlatformRuntime:
    """Executable adapter lifecycle for CLI and non-CLI integration surfaces."""

    def __init__(self, project: Path, state_root: Path, *, home: Path | None = None):
        self.project = project.resolve(strict=True)
        self.state_root = state_root.resolve(strict=False)
        self.home = (home or Path.home()).resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.receipts = self.state_root / "adapter-receipts"
        self.backups = self.state_root / "adapter-backups"
        self.receipts.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def contract(adapter_id: str) -> AdapterContract:
        matches = [item for item in ADAPTERS if item.adapter_id == adapter_id]
        if len(matches) != 1:
            raise KeyError(adapter_id)
        return matches[0]

    def _path(self, value: str) -> Path:
        expanded = Path(os.path.expanduser(value.replace("~", str(self.home), 1) if value.startswith("~") else value))
        return expanded if expanded.is_absolute() else self.project / expanded

    def detect(self, adapter_id: str) -> dict[str, Any]:
        contract = self.contract(adapter_id)
        commands = [command for command in contract.detection_commands if shutil.which(command)]
        paths = [str(path) for path in (self._path(value) for value in contract.config_paths) if path.exists()]
        return {
            "adapter_id": adapter_id,
            "detected": bool(commands or paths),
            "commands": commands,
            "paths": paths,
            "surface": contract.surface,
            "integration_modes": list(contract.integration_modes),
        }

    @staticmethod
    def _merge_json(current: Any, desired: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(current) if isinstance(current, Mapping) else {}
        for key, value in desired.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key] = AdapterPlatformRuntime._merge_json(result[key], value)
            else:
                result[key] = value
        return result

    def configure_json(
        self,
        adapter_id: str,
        relative_or_home_path: str,
        desired: Mapping[str, Any],
        *,
        apply: bool = False,
    ) -> AdapterReceipt:
        contract = self.contract(adapter_id)
        allowed = {str(self._path(value)) for value in contract.config_paths}
        target = self._path(relative_or_home_path)
        if str(target) not in allowed:
            raise PermissionError(f"path is not declared by adapter contract: {target}")
        current: Any = {}
        if target.is_file():
            current = json.loads(target.read_text(encoding="utf-8"))
        merged = self._merge_json(current, desired)
        changed = merged != current
        backup_path = ""
        if apply and changed:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                backup = self.backups / adapter_id / f"{target.name}.{digest}.bak"
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
                backup_path = str(backup)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
                json.dump(merged, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                temporary = Path(handle.name)
            os.replace(temporary, target)
        detection = self.detect(adapter_id)
        checks = {
            "declared_path": True,
            "valid_json": True,
            "changed": changed,
            "applied": apply and changed,
            "content_hash": hashlib.sha256(json.dumps(merged, sort_keys=True).encode()).hexdigest(),
        }
        maturity = AdapterMaturity.CONFIGURED if apply else AdapterMaturity.CONTRACT
        return self._write_receipt(
            adapter_id,
            maturity,
            "configure-json",
            ok=True,
            detected=bool(detection["detected"] or (apply and target.exists())),
            changed_paths=(str(target),) if changed else (),
            capabilities=contract.capabilities,
            checks=checks,
            rollback={"backup": backup_path, "target": str(target)} if backup_path else {},
        )

    def rollback(self, receipt: AdapterReceipt) -> dict[str, Any]:
        target = Path(str(receipt.rollback.get("target", "")))
        backup = Path(str(receipt.rollback.get("backup", "")))
        if not target or not backup.is_file():
            return {"ok": False, "reason": "receipt has no available backup"}
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(backup, target)
        return {"ok": True, "target": str(target)}

    def conformance(
        self,
        adapter_id: str,
        *,
        connector: Callable[[AdapterContract], Mapping[str, Any]] | None = None,
        interceptor: Callable[[AdapterContract], Mapping[str, Any]] | None = None,
        enforcer: Callable[[AdapterContract], Mapping[str, Any]] | None = None,
    ) -> AdapterReceipt:
        contract = self.contract(adapter_id)
        detection = self.detect(adapter_id)
        checks: dict[str, Any] = {"detection": detection}
        maturity = AdapterMaturity.CONTRACT
        ok = True
        if detection["detected"]:
            maturity = AdapterMaturity.CONFIGURED
        if connector:
            value = dict(connector(contract))
            checks["connect"] = value
            ok = ok and bool(value.get("ok"))
            if value.get("ok"):
                maturity = AdapterMaturity.CONNECTED
        if interceptor:
            value = dict(interceptor(contract))
            checks["intercept"] = value
            ok = ok and bool(value.get("ok"))
            if value.get("ok"):
                maturity = AdapterMaturity.INTERCEPTING
        if enforcer:
            value = dict(enforcer(contract))
            checks["enforce"] = value
            ok = ok and bool(value.get("ok"))
            if value.get("ok"):
                maturity = AdapterMaturity.ENFORCED
        return self._write_receipt(
            adapter_id,
            maturity,
            "conformance",
            ok=ok,
            detected=detection["detected"],
            changed_paths=(),
            capabilities=contract.capabilities,
            checks=checks,
        )

    def certify(self, adapter_id: str, external_receipt: Mapping[str, Any]) -> AdapterReceipt:
        required = {"host", "host_version", "clean_install", "tool_interception", "context_interception", "security_denial", "session_restore", "artifact_hash"}
        missing = sorted(required - set(external_receipt))
        valid = not missing and all(bool(external_receipt[key]) for key in required - {"host", "host_version", "artifact_hash"})
        artifact_hash = str(external_receipt.get("artifact_hash", ""))
        valid = valid and artifact_hash.startswith("sha256:") and len(artifact_hash) == 71
        contract = self.contract(adapter_id)
        return self._write_receipt(
            adapter_id,
            AdapterMaturity.CERTIFIED if valid else AdapterMaturity.ENFORCED,
            "certify",
            ok=valid,
            detected=True,
            changed_paths=(),
            capabilities=contract.capabilities,
            checks={"missing": missing, "external_receipt": dict(external_receipt)},
        )

    def _write_receipt(
        self,
        adapter_id: str,
        maturity: AdapterMaturity,
        operation: str,
        *,
        ok: bool,
        detected: bool,
        changed_paths: tuple[str, ...],
        capabilities: Mapping[str, bool],
        checks: Mapping[str, Any],
        rollback: Mapping[str, Any] | None = None,
    ) -> AdapterReceipt:
        body = {
            "adapter_id": adapter_id,
            "maturity": maturity.value,
            "operation": operation,
            "ok": ok,
            "detected": detected,
            "changed_paths": list(changed_paths),
            "capabilities": dict(capabilities),
            "checks": dict(checks),
            "created_at": _now(),
        }
        receipt_id = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
        receipt = AdapterReceipt(
            receipt_id=receipt_id,
            adapter_id=adapter_id,
            maturity=maturity,
            operation=operation,
            ok=ok,
            created_at=body["created_at"],
            detected=detected,
            changed_paths=changed_paths,
            capabilities=dict(capabilities),
            checks=dict(checks),
            rollback=dict(rollback or {}),
        )
        destination = self.receipts / f"{receipt_id.split(':', 1)[1]}.json"
        destination.write_text(json.dumps(asdict(receipt), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return receipt


__all__ = ["AdapterMaturity", "AdapterPlatformRuntime", "AdapterReceipt"]
