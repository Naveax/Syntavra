from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .competitive_fabric import PlatformPlanBuilder
from .host_adapters import KNOWN_HOSTS, host_spec, negotiate
from .state import StateDB
from .util import canonical_json, sha256_bytes


_TEXT_BEGIN = "<!-- SIGNALCORE:BEGIN managed-host-integration -->"
_TEXT_END = "<!-- SIGNALCORE:END managed-host-integration -->"


@dataclass(frozen=True)
class InstallationChange:
    path: str
    kind: str
    action: str
    existed: bool
    before_hash: str
    after_hash: str
    backup_path: str


@dataclass(frozen=True)
class InstallationResult:
    transaction_id: str
    host: str
    scope: str
    root: str
    status: str
    changes: tuple[InstallationChange, ...]
    verification: dict[str, Any]
    created_at: float


class HostInstallationManager:
    """Atomic, reversible installer for Syntavra host integrations.

    The manager only writes paths declared by HostCapabilities. Every write is staged,
    backed up, recorded in SQLite, and rolled back on partial failure. Existing JSON
    configuration is recursively merged; unrelated user keys are never discarded.
    """

    def __init__(
        self,
        path: Path,
        *,
        project: Path,
        skill_root: Path,
        home: Path | None = None,
    ):
        self.project = project.resolve(strict=True)
        self.skill_root = skill_root.resolve(strict=True)
        self.home = (home or Path.home()).resolve(strict=False)
        self.state = StateDB(path)
        self.storage = path.resolve(strict=False).parent / "host-installations"
        self.storage.mkdir(parents=True, exist_ok=True)
        if not (self.skill_root / "SKILL.md").is_file():
            raise FileNotFoundError(f"Syntavra skill source is incomplete: {self.skill_root}")
        with self.state.transaction(immediate=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS host_install_transactions(
                    transaction_id TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    root TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS host_install_host_idx
                    ON host_install_transactions(host,scope,created_at);
                """
            )

    def _root(self, scope: str) -> Path:
        if scope == "project":
            return self.project
        if scope == "user":
            return self.home
        raise ValueError("scope must be project or user")

    @staticmethod
    def _digest(path: Path) -> str:
        if not path.exists() and not path.is_symlink():
            return ""
        if path.is_symlink():
            return sha256_bytes(f"symlink:{os.readlink(path)}".encode("utf-8"))
        if path.is_file():
            return sha256_bytes(path.read_bytes())
        rows: list[tuple[str, str]] = []
        for child in sorted(path.rglob("*")):
            if child.is_symlink():
                rows.append((child.relative_to(path).as_posix(), f"symlink:{os.readlink(child)}"))
            elif child.is_file():
                rows.append((child.relative_to(path).as_posix(), sha256_bytes(child.read_bytes())))
        return sha256_bytes(canonical_json(rows))

    @staticmethod
    def _merge(base: Any, overlay: Any) -> Any:
        if isinstance(base, Mapping) and isinstance(overlay, Mapping):
            result = {str(key): value for key, value in base.items()}
            for key, value in overlay.items():
                name = str(key)
                result[name] = HostInstallationManager._merge(result[name], value) if name in result else value
            return result
        return overlay

    @staticmethod
    def _json_bytes(value: Mapping[str, Any]) -> bytes:
        return (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    def _safe_target(self, root: Path, relative: str) -> Path:
        candidate = root / relative
        cursor = root
        for part in Path(relative).parts:
            if part in {"", "."}:
                continue
            if part == "..":
                raise PermissionError(f"host path traversal rejected: {relative}")
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                raise PermissionError(f"host path symlink rejected: {relative}")
        resolved_parent = candidate.parent.resolve(strict=False)
        try:
            resolved_parent.relative_to(root.resolve(strict=False))
        except ValueError as exc:
            raise PermissionError(f"host path escapes installation root: {relative}") from exc
        return candidate

    @staticmethod
    def _atomic_file(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _copy_tree_atomic(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.syntavra-{uuid.uuid4().hex}"
        shutil.copytree(source, temporary, symlinks=False)
        try:
            if target.exists():
                shutil.rmtree(target)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)

    @staticmethod
    def _managed_text(existing: str, block: str) -> str:
        managed = f"{_TEXT_BEGIN}\n{block.rstrip()}\n{_TEXT_END}"
        if _TEXT_BEGIN in existing and _TEXT_END in existing:
            prefix, remainder = existing.split(_TEXT_BEGIN, 1)
            _, suffix = remainder.split(_TEXT_END, 1)
            return prefix.rstrip() + "\n\n" + managed + suffix
        if not existing.strip():
            return managed + "\n"
        return existing.rstrip() + "\n\n" + managed + "\n"

    def _backup(self, transaction: Path, root: Path, target: Path) -> str:
        if not target.exists() and not target.is_symlink():
            return ""
        relative = target.relative_to(root)
        backup = transaction / "backup" / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir() and not target.is_symlink():
            shutil.copytree(target, backup, symlinks=True)
        elif target.is_symlink():
            backup.symlink_to(os.readlink(target))
        else:
            shutil.copy2(target, backup)
        return str(backup)

    def _skill_payload(self, target: Path) -> tuple[str, bytes | Path]:
        if target.suffix.casefold() in {".md", ".mdc", ".txt"} or target.name == "AGENTS.md":
            source = (self.skill_root / "SKILL.md").read_text(encoding="utf-8")
            existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
            return "managed-text", self._managed_text(existing, source).encode("utf-8")
        return "skill-directory", self.skill_root

    def plan(self, host: str, *, scope: str = "project") -> dict[str, Any]:
        return PlatformPlanBuilder().plan(host, project=self.project, scope=scope)

    def apply(self, host: str, *, scope: str = "project", dry_run: bool = False) -> InstallationResult:
        normalized = host.casefold()
        if normalized not in KNOWN_HOSTS or normalized == "generic-mcp":
            raise ValueError(f"unsupported concrete host: {host}")
        spec = host_spec(normalized)
        root = self._root(scope)
        plan = self.plan(normalized, scope=scope)
        transaction_id = f"host-{int(time.time())}-{uuid.uuid4().hex[:12]}"
        transaction = self.storage / transaction_id
        changes: list[InstallationChange] = []
        staged: list[tuple[Path, str, bytes | Path, bool, str, str]] = []

        if spec.config_path:
            target = self._safe_target(root, spec.config_path)
            if target.exists() and not target.is_file():
                raise IsADirectoryError(target)
            existing: dict[str, Any] = {}
            if target.is_file():
                try:
                    loaded = json.loads(target.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"host config is not valid JSON: {target}: {exc}") from exc
                if not isinstance(loaded, Mapping):
                    raise TypeError(f"host config root must be an object: {target}")
                existing = dict(loaded)
            overlay = next((row["merge"] for row in plan["files"] if row.get("path") == spec.config_path), {})
            merged = self._merge(existing, overlay)
            staged.append((target, "json-config", self._json_bytes(merged), target.exists(), self._digest(target), spec.config_path))

        if spec.skill_path:
            target = self._safe_target(root, spec.skill_path)
            kind, payload = self._skill_payload(target)
            staged.append((target, kind, payload, target.exists(), self._digest(target), spec.skill_path))

        if dry_run:
            for target, kind, payload, existed, before_hash, relative in staged:
                if isinstance(payload, bytes):
                    after_hash = sha256_bytes(payload)
                else:
                    after_hash = self._digest(payload)
                changes.append(InstallationChange(relative, kind, "would-update" if existed else "would-create", existed, before_hash, after_hash, ""))
            return InstallationResult(
                transaction_id=transaction_id,
                host=normalized,
                scope=scope,
                root=str(root),
                status="dry-run",
                changes=tuple(changes),
                verification={"ok": True, "dry_run": True, "plan": plan},
                created_at=time.time(),
            )

        transaction.mkdir(parents=True, exist_ok=False)
        applied: list[tuple[Path, bool, str]] = []
        created_at = time.time()
        try:
            for target, kind, payload, existed, before_hash, relative in staged:
                backup_path = self._backup(transaction, root, target)
                if isinstance(payload, bytes):
                    self._atomic_file(target, payload)
                else:
                    self._copy_tree_atomic(payload, target)
                after_hash = self._digest(target)
                action = "updated" if existed else "created"
                changes.append(InstallationChange(relative, kind, action, existed, before_hash, after_hash, backup_path))
                applied.append((target, existed, backup_path))
            verification = self.verify(normalized, scope=scope)
            if not verification["ok"]:
                raise RuntimeError(f"installation verification failed: {verification['reasons']}")
            status = "applied"
        except Exception:
            for target, existed, backup_path in reversed(applied):
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
                if existed and backup_path:
                    backup = Path(backup_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if backup.is_dir() and not backup.is_symlink():
                        shutil.copytree(backup, target, symlinks=True)
                    elif backup.is_symlink():
                        target.symlink_to(os.readlink(backup))
                    else:
                        shutil.copy2(backup, target)
            shutil.rmtree(transaction, ignore_errors=True)
            raise

        result = InstallationResult(
            transaction_id=transaction_id,
            host=normalized,
            scope=scope,
            root=str(root),
            status=status,
            changes=tuple(changes),
            verification=verification,
            created_at=created_at,
        )
        manifest = transaction / "manifest.json"
        manifest.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.state.transaction(immediate=True) as db:
            db.execute(
                "INSERT INTO host_install_transactions(transaction_id,host,scope,root,status,manifest_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (transaction_id, normalized, scope, str(root), status, json.dumps(asdict(result), ensure_ascii=False, sort_keys=True), created_at, time.time()),
            )
        return result

    def verify(self, host: str, *, scope: str = "project") -> dict[str, Any]:
        normalized = host.casefold()
        spec = host_spec(normalized)
        root = self._root(scope)
        reasons: list[str] = []
        details: dict[str, Any] = {}
        if spec.config_path:
            target = self._safe_target(root, spec.config_path)
            if not target.is_file():
                reasons.append("missing-config")
            else:
                try:
                    config = json.loads(target.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    reasons.append("invalid-config-json")
                else:
                    syntavra = (config.get("mcpServers") or {}).get("syntavra") if isinstance(config, Mapping) else None
                    if not isinstance(syntavra, Mapping) or syntavra.get("command") != "syntavra":
                        reasons.append("missing-syntavra-mcp")
                    if spec.supports_pre_tool_hook or spec.supports_post_tool_hook:
                        hooks = config.get("hooks") if isinstance(config, Mapping) else None
                        if not isinstance(hooks, Mapping):
                            reasons.append("missing-hooks")
                details["config"] = {"path": spec.config_path, "hash": self._digest(target)}
        if spec.skill_path:
            target = self._safe_target(root, spec.skill_path)
            if not target.exists():
                reasons.append("missing-skill")
            elif target.is_file():
                text = target.read_text(encoding="utf-8", errors="replace")
                if _TEXT_BEGIN not in text or _TEXT_END not in text:
                    reasons.append("unmanaged-skill-file")
            elif not (target / "SKILL.md").is_file():
                reasons.append("missing-skill-entrypoint")
            details["skill"] = {"path": spec.skill_path, "hash": self._digest(target)}
        return {
            "ok": not reasons,
            "host": normalized,
            "scope": scope,
            "root": str(root),
            "mode": negotiate(normalized, installed=not reasons)["mode"],
            "reasons": reasons,
            "details": details,
        }

    def rollback(self, transaction_id: str) -> InstallationResult:
        with self.state.read() as db:
            row = db.execute(
                "SELECT * FROM host_install_transactions WHERE transaction_id=?",
                (transaction_id,),
            ).fetchone()
        if row is None:
            raise KeyError(transaction_id)
        if str(row["status"]) == "rolled-back":
            return InstallationResult(**json.loads(str(row["manifest_json"])))
        original = json.loads(str(row["manifest_json"]))
        root = Path(str(row["root"])).resolve(strict=False)
        rolled: list[InstallationChange] = []
        for raw in reversed(original["changes"]):
            change = InstallationChange(**raw)
            target = self._safe_target(root, change.path)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            if change.existed and change.backup_path:
                backup = Path(change.backup_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                if backup.is_dir() and not backup.is_symlink():
                    shutil.copytree(backup, target, symlinks=True)
                elif backup.is_symlink():
                    target.symlink_to(os.readlink(backup))
                else:
                    shutil.copy2(backup, target)
            rolled.append(InstallationChange(
                path=change.path,
                kind=change.kind,
                action="restored" if change.existed else "removed",
                existed=change.existed,
                before_hash=change.after_hash,
                after_hash=self._digest(target),
                backup_path=change.backup_path,
            ))
        verification = {"ok": True, "rolled_back": True}
        result = InstallationResult(
            transaction_id=transaction_id,
            host=str(row["host"]),
            scope=str(row["scope"]),
            root=str(root),
            status="rolled-back",
            changes=tuple(reversed(rolled)),
            verification=verification,
            created_at=float(row["created_at"]),
        )
        with self.state.transaction(immediate=True) as db:
            db.execute(
                "UPDATE host_install_transactions SET status=?,manifest_json=?,updated_at=? WHERE transaction_id=?",
                ("rolled-back", json.dumps(asdict(result), ensure_ascii=False, sort_keys=True), time.time(), transaction_id),
            )
        return result

    def transactions(self, *, host: str = "", limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT transaction_id,host,scope,root,status,created_at,updated_at FROM host_install_transactions"
        params: list[Any] = []
        if host:
            sql += " WHERE host=?"
            params.append(host.casefold())
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self.state.read() as db:
            return [dict(row) for row in db.execute(sql, params)]
