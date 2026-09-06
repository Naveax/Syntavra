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

from .codex_integration import (
    CODEX_CONFIG_PATH,
    CODEX_SKILL_PATH,
    mcp_entry as codex_mcp_entry,
    parse_config as parse_codex_config,
    render_config as render_codex_config,
    verify_entry as verify_codex_entry,
)
from .competitive_fabric import PlatformPlanBuilder
from .host_adapters import KNOWN_HOSTS, host_spec, negotiate
from .state import StateDB
from .util import canonical_json, sha256_bytes


_TEXT_BEGIN = "<!-- SYNTAVRA:BEGIN managed-host-integration -->"
_TEXT_END = "<!-- SYNTAVRA:END managed-host-integration -->"


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


class HostInstallationRollbackError(RuntimeError):
    """A host installation rollback failed after an apply or explicit rollback request."""

    def __init__(
        self,
        *,
        transaction_id: str,
        rollback_error: Exception,
        apply_error: Exception | None = None,
    ):
        self.transaction_id = transaction_id
        self.rollback_error = rollback_error
        self.apply_error = apply_error
        details = [
            f"transaction_id={transaction_id}",
            f"rollback_error={type(rollback_error).__name__}: {rollback_error}",
        ]
        if apply_error is not None:
            details.insert(1, f"apply_error={type(apply_error).__name__}: {apply_error}")
        super().__init__("host installation rollback failed: " + "; ".join(details))


class HostInstallationManager:
    """Atomic, reversible installer for Syntavra host integrations.

    Every write is staged, backed up, recorded in SQLite and rolled back on partial
    failure. Codex is handled through its current TOML/Agent-Skill contract; older
    JSON hosts continue to use recursive merge semantics.
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
    def _path_exists(path: Path) -> bool:
        return path.exists() or path.is_symlink()

    @classmethod
    def _remove_path(cls, path: Path) -> None:
        if not cls._path_exists(path):
            return
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    @classmethod
    def _replace_staged_path(cls, staged: Path, target: Path) -> None:
        """Install a fully staged path without discarding the last live directory."""
        target.parent.mkdir(parents=True, exist_ok=True)
        if not cls._path_exists(target):
            os.replace(staged, target)
            return

        target_is_dir = target.is_dir() and not target.is_symlink()
        staged_is_dir = staged.is_dir() and not staged.is_symlink()
        if not target_is_dir and not staged_is_dir:
            os.replace(staged, target)
            return

        safety = target.parent / f".{target.name}.syntavra-safety-{uuid.uuid4().hex}"
        os.replace(target, safety)
        try:
            os.replace(staged, target)
        except Exception as install_exc:
            try:
                os.replace(safety, target)
            except Exception as restore_exc:
                raise RuntimeError(
                    "host target replacement and live-target recovery both failed: "
                    f"target={target}; safety_path={safety}; "
                    f"install_error={type(install_exc).__name__}: {install_exc}; "
                    f"restore_error={type(restore_exc).__name__}: {restore_exc}"
                ) from restore_exc
            raise
        else:
            cls._remove_path(safety)

    @classmethod
    def _stage_path_copy(cls, source: Path, target: Path, *, symlinks: bool) -> Path:
        """Copy a source completely beside target before any live-target mutation."""
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.parent / f".{target.name}.syntavra-stage-{uuid.uuid4().hex}"
        try:
            if source.is_dir() and not source.is_symlink():
                shutil.copytree(source, staged, symlinks=symlinks)
            elif source.is_symlink():
                staged.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
            else:
                shutil.copy2(source, staged)
        except Exception:
            cls._remove_path(staged)
            raise
        return staged

    @classmethod
    def _copy_tree_atomic(cls, source: Path, target: Path) -> None:
        staged = cls._stage_path_copy(source, target, symlinks=False)
        try:
            cls._replace_staged_path(staged, target)
        finally:
            cls._remove_path(staged)

    @classmethod
    def _restore_previous(cls, target: Path, *, existed: bool, backup_path: str) -> None:
        if not existed:
            cls._remove_path(target)
            return
        if not backup_path:
            raise FileNotFoundError(f"missing host installation backup for existing target: {target}")
        backup = Path(backup_path)
        if not cls._path_exists(backup):
            raise FileNotFoundError(f"host installation backup missing: {backup}")
        staged = cls._stage_path_copy(backup, target, symlinks=True)
        try:
            cls._replace_staged_path(staged, target)
        finally:
            cls._remove_path(staged)

    @classmethod
    def _rollback_applied(cls, applied: list[tuple[Path, bool, str]]) -> None:
        for target, existed, backup_path in reversed(applied):
            cls._restore_previous(target, existed=existed, backup_path=backup_path)

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

    @staticmethod
    def _paths(host: str) -> tuple[str, str]:
        spec = host_spec(host)
        if host == "codex":
            return CODEX_CONFIG_PATH, CODEX_SKILL_PATH
        return spec.config_path, spec.skill_path

    def plan(self, host: str, *, scope: str = "project") -> dict[str, Any]:
        normalized = host.casefold()
        if normalized != "codex":
            return PlatformPlanBuilder().plan(normalized, project=self.project, scope=scope)
        spec = host_spec(normalized)
        entry = codex_mcp_entry(("syntavra",), project=self.project, scope=scope)
        return {
            "host": "codex",
            "display_name": spec.display_name,
            "scope": scope,
            "project": str(self.project),
            "mode": negotiate("codex")["mode"],
            "enforced": negotiate("codex")["enforced"],
            "verified_adapter": spec.verified,
            "files": [
                {"path": CODEX_CONFIG_PATH, "format": "toml", "entry": entry},
                {"path": f"{CODEX_SKILL_PATH}/SKILL.md", "source": "bundled syntavra skill"},
            ],
            "capabilities": {**asdict(spec), "config_path": CODEX_CONFIG_PATH, "skill_path": CODEX_SKILL_PATH},
            "validation": ["codex mcp list", "syntavra status --doctor", "syntavra status"],
        }

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
        config_path, skill_path = self._paths(normalized)

        if config_path:
            target = self._safe_target(root, config_path)
            if target.exists() and not target.is_file():
                raise IsADirectoryError(target)
            if normalized == "codex":
                existing_text = target.read_text(encoding="utf-8", errors="strict") if target.is_file() else ""
                entry = codex_mcp_entry(("syntavra",), project=self.project, scope=scope)
                rendered = render_codex_config(existing_text, entry).encode("utf-8")
                staged.append((target, "toml-config", rendered, target.exists(), self._digest(target), config_path))
            else:
                existing: dict[str, Any] = {}
                if target.is_file():
                    try:
                        loaded = json.loads(target.read_text(encoding="utf-8"))
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"host config is not valid JSON: {target}: {exc}") from exc
                    if not isinstance(loaded, Mapping):
                        raise TypeError(f"host config root must be an object: {target}")
                    existing = dict(loaded)
                overlay = next((row["merge"] for row in plan["files"] if row.get("path") == config_path), {})
                merged = self._merge(existing, overlay)
                staged.append((target, "json-config", self._json_bytes(merged), target.exists(), self._digest(target), config_path))

        if skill_path:
            target = self._safe_target(root, skill_path)
            kind, payload = self._skill_payload(target)
            staged.append((target, kind, payload, target.exists(), self._digest(target), skill_path))

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
                applied.append((target, existed, backup_path))
                after_hash = self._digest(target)
                action = "updated" if existed else "created"
                changes.append(InstallationChange(relative, kind, action, existed, before_hash, after_hash, backup_path))
            verification = self.verify(normalized, scope=scope)
            if not verification["ok"]:
                raise RuntimeError(f"installation verification failed: {verification['reasons']}")
            status = "applied"
        except Exception as apply_exc:
            try:
                self._rollback_applied(applied)
            except Exception as rollback_exc:
                raise HostInstallationRollbackError(
                    transaction_id=transaction_id,
                    apply_error=apply_exc,
                    rollback_error=rollback_exc,
                ) from rollback_exc
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
        config_path, skill_path = self._paths(normalized)
        if config_path:
            target = self._safe_target(root, config_path)
            if not target.is_file():
                reasons.append("missing-config")
            elif normalized == "codex":
                try:
                    entry = parse_codex_config(target.read_text(encoding="utf-8", errors="strict"))
                except (ValueError, UnicodeError):
                    reasons.append("invalid-config-toml")
                else:
                    if not entry:
                        reasons.append("missing-syntavra-mcp")
                    else:
                        reasons.extend(verify_codex_entry(entry, project=self.project, scope=scope))
                details["config"] = {"path": config_path, "hash": self._digest(target), "format": "toml"}
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
                details["config"] = {"path": config_path, "hash": self._digest(target), "format": "json"}
        if skill_path:
            target = self._safe_target(root, skill_path)
            if not target.exists():
                reasons.append("missing-skill")
            elif target.is_file():
                text = target.read_text(encoding="utf-8", errors="replace")
                if _TEXT_BEGIN not in text or _TEXT_END not in text:
                    reasons.append("unmanaged-skill-file")
            elif not (target / "SKILL.md").is_file():
                reasons.append("missing-skill-entrypoint")
            details["skill"] = {"path": skill_path, "hash": self._digest(target)}
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
        try:
            for raw in reversed(original["changes"]):
                change = InstallationChange(**raw)
                target = self._safe_target(root, change.path)
                self._restore_previous(target, existed=change.existed, backup_path=change.backup_path)
                rolled.append(InstallationChange(
                    path=change.path,
                    kind=change.kind,
                    action="restored" if change.existed else "removed",
                    existed=change.existed,
                    before_hash=change.after_hash,
                    after_hash=self._digest(target),
                    backup_path=change.backup_path,
                ))
        except Exception as rollback_exc:
            raise HostInstallationRollbackError(
                transaction_id=transaction_id,
                rollback_error=rollback_exc,
            ) from rollback_exc
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