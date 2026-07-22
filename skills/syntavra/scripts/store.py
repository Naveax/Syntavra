#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

from common import atomic_write, canonical_hash, contains_secret, git_branch, git_head, git_root, sha256_bytes

SCHEMA_VERSION = 4


def state_root(project: str | Path, scope: str = "project") -> Path:
    if scope == "user":
        home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
        return home / ".syntavra"
    return git_root(Path(project)) / ".syntavra"


def connect(project: str | Path, scope: str = "project") -> sqlite3.Connection:
    root = state_root(project, scope)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "state.sqlite3"
    con = sqlite3.connect(path, timeout=15.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA wal_autocheckpoint=1000")
    version = int(con.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        con.close()
        raise RuntimeError(f"state schema {version} is newer than supported {SCHEMA_VERSION}")
    if version < SCHEMA_VERSION:
        _migrate(con, version)
    return con


def _migrate(con: sqlite3.Connection, version: int) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memory(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              memory_key TEXT,
              category TEXT NOT NULL,
              content TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              confidence REAL NOT NULL DEFAULT 1.0,
              source TEXT,
              branch TEXT,
              valid_from_commit TEXT,
              valid_until_commit TEXT,
              supersedes INTEGER,
              contradicts INTEGER,
              ttl_seconds INTEGER,
              created REAL NOT NULL,
              updated REAL NOT NULL,
              access_count INTEGER NOT NULL DEFAULT 0,
              successful_reuse INTEGER NOT NULL DEFAULT 0,
              harmful_reuse INTEGER NOT NULL DEFAULT 0,
              token_roi REAL NOT NULL DEFAULT 0,
              latency_roi_ms REAL NOT NULL DEFAULT 0,
              sensitivity TEXT NOT NULL DEFAULT 'normal',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              UNIQUE(project_id,content_hash,status),
              FOREIGN KEY(supersedes) REFERENCES memory(id),
              FOREIGN KEY(contradicts) REFERENCES memory(id)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_lookup ON memory(project_id,status,category,updated DESC);
            CREATE INDEX IF NOT EXISTS idx_memory_key ON memory(project_id,memory_key,status);
            CREATE TABLE IF NOT EXISTS checkpoints(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              task_key TEXT NOT NULL,
              phase TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              payload_hash TEXT NOT NULL,
              commit_sha TEXT,
              branch TEXT,
              created REAL NOT NULL,
              UNIQUE(project_id,task_key,payload_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_checkpoint_task ON checkpoints(project_id,task_key,created DESC);
            CREATE TABLE IF NOT EXISTS evidence(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              claim_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL,
              commit_sha TEXT,
              path TEXT,
              start_line INTEGER,
              end_line INTEGER,
              source_engine TEXT NOT NULL,
              integrity TEXT NOT NULL,
              confidence REAL NOT NULL,
              content_hash TEXT NOT NULL,
              recovery_handle TEXT,
              proposition TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              supersedes INTEGER,
              corroborates INTEGER,
              created REAL NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              UNIQUE(project_id,fingerprint,status),
              FOREIGN KEY(supersedes) REFERENCES evidence(id),
              FOREIGN KEY(corroborates) REFERENCES evidence(id)
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(project_id,claim_id,status,confidence DESC);
            CREATE TABLE IF NOT EXISTS tool_events(
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              task_family TEXT NOT NULL,
              profile TEXT NOT NULL,
              engine TEXT NOT NULL,
              tool TEXT NOT NULL,
              success INTEGER NOT NULL,
              input_tokens INTEGER NOT NULL,
              cached_tokens INTEGER NOT NULL,
              output_tokens INTEGER NOT NULL,
              reasoning_tokens INTEGER NOT NULL,
              latency_ms REAL NOT NULL,
              money_cost REAL NOT NULL,
              useful_evidence INTEGER NOT NULL,
              duplicate_evidence INTEGER NOT NULL,
              retry_generated INTEGER NOT NULL,
              credit REAL NOT NULL DEFAULT 0,
              created REAL NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_tool_stats ON tool_events(task_family,profile,engine,tool,created DESC);
            CREATE TABLE IF NOT EXISTS task_events(
              id TEXT PRIMARY KEY,
              task_hash TEXT NOT NULL,
              task_family TEXT NOT NULL,
              model TEXT NOT NULL,
              platform TEXT NOT NULL,
              repository_fingerprint TEXT NOT NULL,
              profile TEXT NOT NULL,
              success INTEGER NOT NULL,
              active_tokens INTEGER NOT NULL,
              latency_ms REAL NOT NULL,
              money_cost REAL NOT NULL,
              evidence_coverage REAL NOT NULL,
              duplicate_ratio REAL NOT NULL,
              retries INTEGER NOT NULL,
              created REAL NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_task_stats ON task_events(task_family,profile,model,created DESC);
            CREATE TABLE IF NOT EXISTS receipts(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              previous_hash TEXT NOT NULL,
              event_hash TEXT NOT NULL,
              created REAL NOT NULL,
              UNIQUE(session_id,sequence)
            );
            CREATE TABLE IF NOT EXISTS budget_leases(
              lease_id TEXT PRIMARY KEY,
              scope TEXT NOT NULL,
              owner TEXT NOT NULL,
              reserved_tokens INTEGER NOT NULL,
              reserved_money REAL NOT NULL,
              expires REAL NOT NULL,
              state TEXT NOT NULL,
              created REAL NOT NULL
            );
            """
        )
        try:
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content, content='memory', content_rowid='id', tokenize='unicode61')")
            con.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN INSERT INTO memory_fts(rowid,content) VALUES(new.id,new.content); END;
                CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN INSERT INTO memory_fts(memory_fts,rowid,content) VALUES('delete',old.id,old.content); END;
                CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE OF content ON memory BEGIN
                  INSERT INTO memory_fts(memory_fts,rowid,content) VALUES('delete',old.id,old.content);
                  INSERT INTO memory_fts(rowid,content) VALUES(new.id,new.content);
                END;
                """
            )
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('fts5','1')")
        except sqlite3.OperationalError:
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('fts5','0')")
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        con.commit()
    except Exception:
        con.rollback()
        raise


@contextlib.contextmanager
def transaction(project: str | Path, scope: str = "project") -> Iterator[sqlite3.Connection]:
    con = connect(project, scope)
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def project_id(project: str | Path) -> str:
    return hashlib.sha256(str(git_root(Path(project))).casefold().encode("utf-8")).hexdigest()


def object_root(project: str | Path, scope: str = "project") -> Path:
    root = state_root(project, scope) / "objects" / "sha256"
    root.mkdir(parents=True, exist_ok=True)
    return root


def object_paths(project: str | Path, digest: str, scope: str = "project") -> tuple[Path, Path]:
    if not re_full_hash(digest):
        raise ValueError("invalid sha256 digest")
    base = object_root(project, scope) / digest[:2] / digest[2:4]
    return base / f"{digest}.raw", base / f"{digest}.gz"


def re_full_hash(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.casefold())


def put_object(project: str | Path, data: bytes, *, scope: str = "project", compress: bool = True) -> dict[str, Any]:
    digest = sha256_bytes(data)
    raw_path, gz_path = object_paths(project, digest, scope)
    existing = gz_path if gz_path.exists() else raw_path if raw_path.exists() else None
    if existing:
        return {"hash": digest, "handle": f"sha256:{digest}", "bytes": len(data), "stored_bytes": existing.stat().st_size, "encoding": "gzip" if existing.suffix == ".gz" else "raw", "deduplicated": True}
    encoded = data
    target = raw_path
    encoding = "raw"
    if compress and len(data) >= 4096:
        candidate = gzip.compress(data, compresslevel=6, mtime=0)
        if len(candidate) + 64 < len(data) * 0.92:
            encoded, target, encoding = candidate, gz_path, "gzip"
    atomic_write(target, encoded)
    return {"hash": digest, "handle": f"sha256:{digest}", "bytes": len(data), "stored_bytes": len(encoded), "encoding": encoding, "deduplicated": False}


def read_object(project: str | Path, handle: str, *, scope: str = "project", max_bytes: int = 128 * 1024 * 1024) -> bytes:
    if not handle.startswith("sha256:"):
        raise ValueError("unsupported recovery handle")
    digest = handle.split(":", 1)[1]
    raw_path, gz_path = object_paths(project, digest, scope)
    if raw_path.exists():
        data = raw_path.read_bytes()
        if len(data) > max_bytes:
            raise RuntimeError("object exceeds max_bytes")
    elif gz_path.exists():
        chunks: list[bytes] = []
        total = 0
        with gzip.open(gz_path, "rb") as handle_file:
            for chunk in iter(lambda: handle_file.read(1024 * 1024), b""):
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("decoded object exceeds max_bytes")
                chunks.append(chunk)
        data = b"".join(chunks)
    else:
        raise FileNotFoundError(digest)
    if sha256_bytes(data) != digest:
        raise RuntimeError("object hash mismatch")
    return data
