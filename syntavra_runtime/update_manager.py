from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class UpdateArtifact:
    platform: str
    architecture: str
    filename: str
    sha256: str
    size: int
    url: str = ""
    delta_from: str = ""
    delta_sha256: str = ""


@dataclass(frozen=True)
class UpdateManifest:
    product: str
    version: str
    channel: str
    generated_at: str
    artifacts: tuple[UpdateArtifact, ...]
    minimum_installer: str = "0.0.1"
    metadata: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "version": self.version,
            "channel": self.channel,
            "generated_at": self.generated_at,
            "minimum_installer": self.minimum_installer,
            "artifacts": [asdict(item) for item in self.artifacts],
            "metadata": self.metadata,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "UpdateManifest":
        return cls(
            product=str(value["product"]),
            version=str(value["version"]),
            channel=str(value["channel"]),
            generated_at=str(value["generated_at"]),
            minimum_installer=str(value.get("minimum_installer", "0.0.1")),
            artifacts=tuple(UpdateArtifact(**item) for item in value.get("artifacts", [])),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class UpdateReceipt:
    receipt_id: str
    status: str
    target: str
    previous: str
    installed_sha256: str
    expected_sha256: str
    started_at: str
    finished_at: str
    rollback_performed: bool
    health: dict[str, Any]
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "installed"


class DistributionManager:
    """Signed-manifest, checksum-verified, atomic installation and rollback."""

    PRODUCT = "Syntavra"
    VERSION = "0.0.1"
    CHANNEL = "pre-release"

    def __init__(self, install_root: Path, state_root: Path):
        self.install_root = install_root.resolve(strict=False)
        self.state_root = state_root.resolve(strict=False)
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.backups = self.state_root / "update-backups"
        self.receipts = self.state_root / "update-receipts"
        self.backups.mkdir(parents=True, exist_ok=True)
        self.receipts.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def verify_manifest(envelope: Mapping[str, Any], public_key: bytes | str) -> UpdateManifest:
        # Keep the portable CLI startup independent of platform OpenSSL/cryptography
        # extensions. Signature verification loads the native dependency only when
        # an update manifest is actually verified.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        payload = envelope.get("payload")
        signature = envelope.get("signature")
        if not isinstance(payload, Mapping) or not isinstance(signature, str):
            raise ValueError("signed update envelope requires payload and signature")
        key_bytes = base64.b64decode(public_key) if isinstance(public_key, str) else bytes(public_key)
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(base64.b64decode(signature), _canonical(payload))
        manifest = UpdateManifest.from_mapping(payload)
        if manifest.product != DistributionManager.PRODUCT:
            raise ValueError(f"unexpected product: {manifest.product}")
        if manifest.version != DistributionManager.VERSION or manifest.channel != DistributionManager.CHANNEL:
            raise ValueError("update identity violates locked 0.0.1 pre-release line")
        return manifest

    @staticmethod
    def select(manifest: UpdateManifest, platform_name: str, architecture: str) -> UpdateArtifact:
        matches = [item for item in manifest.artifacts if item.platform == platform_name and item.architecture == architecture]
        if len(matches) != 1:
            raise LookupError(f"expected one update artifact for {platform_name}/{architecture}, found {len(matches)}")
        return matches[0]

    @staticmethod
    def verify_artifact(path: Path, artifact: UpdateArtifact) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        digest = _sha256(path)
        if size != artifact.size:
            raise ValueError(f"artifact size mismatch: expected {artifact.size}, got {size}")
        if digest != artifact.sha256:
            raise ValueError(f"artifact checksum mismatch: expected {artifact.sha256}, got {digest}")
        return {"ok": True, "size": size, "sha256": digest}

    def _receipt(self, value: UpdateReceipt) -> UpdateReceipt:
        destination = self.receipts / f"{value.receipt_id.split(':', 1)[1]}.json"
        destination.write_text(json.dumps(asdict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return value

    def install(
        self,
        source: Path,
        artifact: UpdateArtifact,
        *,
        executable_name: str = "syntavra",
        health_check: Callable[[Path], Mapping[str, Any] | bool] | None = None,
    ) -> UpdateReceipt:
        started_at = _now()
        self.verify_artifact(source, artifact)
        target = self.install_root / executable_name
        previous_hash = _sha256(target) if target.is_file() else ""
        backup = self.backups / f"{executable_name}.{previous_hash or 'none'}.bak"
        staged = self.install_root / f".{executable_name}.{artifact.sha256[:12]}.staged"
        rollback_performed = False
        health: dict[str, Any] = {}
        try:
            shutil.copyfile(source, staged)
            os.chmod(staged, source.stat().st_mode | 0o111)
            if _sha256(staged) != artifact.sha256:
                raise ValueError("staged artifact checksum mismatch")
            if target.exists():
                shutil.copy2(target, backup)
            os.replace(staged, target)
            if health_check:
                result = health_check(target)
                health = dict(result) if isinstance(result, Mapping) else {"ok": bool(result)}
                if not health.get("ok", False):
                    raise RuntimeError(f"post-install health check failed: {health}")
            else:
                health = {"ok": True, "mode": "checksum-only"}
            body = {
                "status": "installed",
                "target": str(target),
                "previous": previous_hash,
                "installed": artifact.sha256,
                "started_at": started_at,
                "finished_at": _now(),
            }
            receipt_id = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
            return self._receipt(
                UpdateReceipt(
                    receipt_id=receipt_id,
                    status="installed",
                    target=str(target),
                    previous=previous_hash,
                    installed_sha256=artifact.sha256,
                    expected_sha256=artifact.sha256,
                    started_at=started_at,
                    finished_at=body["finished_at"],
                    rollback_performed=False,
                    health=health,
                )
            )
        except Exception as error:
            staged.unlink(missing_ok=True)
            if backup.is_file():
                os.replace(backup, target)
                rollback_performed = True
            elif target.exists() and _sha256(target) == artifact.sha256:
                target.unlink()
                rollback_performed = True
            body = {
                "status": "rolled-back" if rollback_performed else "failed",
                "target": str(target),
                "previous": previous_hash,
                "installed": artifact.sha256,
                "started_at": started_at,
                "finished_at": _now(),
                "error": f"{type(error).__name__}: {error}",
            }
            receipt_id = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
            return self._receipt(
                UpdateReceipt(
                    receipt_id=receipt_id,
                    status=body["status"],
                    target=str(target),
                    previous=previous_hash,
                    installed_sha256=_sha256(target) if target.is_file() else "",
                    expected_sha256=artifact.sha256,
                    started_at=started_at,
                    finished_at=body["finished_at"],
                    rollback_performed=rollback_performed,
                    health=health,
                    detail=body["error"],
                )
            )

    def rollback(self, executable_name: str = "syntavra", *, expected_previous_sha256: str = "") -> dict[str, Any]:
        candidates = sorted(self.backups.glob(f"{executable_name}.*.bak"), key=lambda path: path.stat().st_mtime, reverse=True)
        if expected_previous_sha256:
            candidates = [path for path in candidates if expected_previous_sha256 in path.name and _sha256(path) == expected_previous_sha256]
        if not candidates:
            return {"ok": False, "reason": "no matching backup"}
        target = self.install_root / executable_name
        backup = candidates[0]
        os.replace(backup, target)
        return {"ok": True, "target": str(target), "sha256": _sha256(target), "restored_from": str(backup)}

    def create_offline_bundle(
        self,
        manifest_envelope: Mapping[str, Any],
        artifacts: Mapping[str, Path],
        destination: Path,
    ) -> dict[str, Any]:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "manifest.json").write_text(json.dumps(dict(manifest_envelope), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        copied = []
        for filename, source in artifacts.items():
            target = destination / Path(filename).name
            shutil.copy2(source, target)
            copied.append({"filename": target.name, "sha256": _sha256(target), "size": target.stat().st_size})
        index = {"product": self.PRODUCT, "version": self.VERSION, "channel": self.CHANNEL, "created_at": _now(), "artifacts": copied}
        (destination / "bundle-index.json").write_text(json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return {"ok": True, "destination": str(destination), "artifacts": copied}


__all__ = ["DistributionManager", "UpdateArtifact", "UpdateManifest", "UpdateReceipt"]
