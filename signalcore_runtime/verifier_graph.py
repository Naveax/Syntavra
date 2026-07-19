from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .state import StateDB
from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class VerifierResult:
    cache_key: str
    command: tuple[str, ...]
    tree_hash: str
    environment_hash: str
    dependency_hash: str
    toolchain_hash: str
    success: bool
    exit_code: int
    evidence_handle: str
    affected_paths: tuple[str, ...]
    created_at: float


class VerifierGraph:
    def __init__(self, path: Path):
        self.state = StateDB(path)

    @staticmethod
    def cache_key(command: Iterable[str], *, tree_hash: str, environment_hash: str, dependency_hash: str, toolchain_hash: str) -> str:
        payload = {"command": tuple(command), "tree_hash": tree_hash, "environment_hash": environment_hash, "dependency_hash": dependency_hash, "toolchain_hash": toolchain_hash}
        return sha256_bytes(canonical_json(payload))

    def record(
        self,
        command: Iterable[str],
        *,
        tree_hash: str,
        environment_hash: str,
        dependency_hash: str,
        toolchain_hash: str,
        success: bool,
        exit_code: int,
        evidence_handle: str,
        affected_paths: Iterable[str],
    ) -> VerifierResult:
        command_tuple = tuple(command)
        affected_tuple = tuple(sorted(set(affected_paths)))
        key = self.cache_key(command_tuple, tree_hash=tree_hash, environment_hash=environment_hash, dependency_hash=dependency_hash, toolchain_hash=toolchain_hash)
        created = time.time()
        with self.state.transaction(immediate=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO verifier_results VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (key, json.dumps(command_tuple), tree_hash, environment_hash, dependency_hash, toolchain_hash, int(success), exit_code, evidence_handle, json.dumps(affected_tuple), created),
            )
        return VerifierResult(key, command_tuple, tree_hash, environment_hash, dependency_hash, toolchain_hash, success, exit_code, evidence_handle, affected_tuple, created)

    def lookup(self, command: Iterable[str], *, tree_hash: str, environment_hash: str, dependency_hash: str, toolchain_hash: str) -> VerifierResult | None:
        key = self.cache_key(command, tree_hash=tree_hash, environment_hash=environment_hash, dependency_hash=dependency_hash, toolchain_hash=toolchain_hash)
        with self.state.read() as db:
            row = db.execute("SELECT * FROM verifier_results WHERE cache_key=?", (key,)).fetchone()
        if not row:
            return None
        return VerifierResult(
            row["cache_key"], tuple(json.loads(row["command_json"])), row["tree_hash"], row["environment_hash"], row["dependency_hash"], row["toolchain_hash"], bool(row["success"]), row["exit_code"], row["evidence_handle"], tuple(json.loads(row["affected_paths_json"])), row["created_at"],
        )

    def invalidated_by(self, changed_paths: Iterable[str]) -> list[dict]:
        changed = set(changed_paths)
        invalid: list[dict] = []
        with self.state.read() as db:
            rows = db.execute("SELECT * FROM verifier_results ORDER BY created_at DESC").fetchall()
        for row in rows:
            affected = set(json.loads(row["affected_paths_json"]))
            overlap = sorted(changed & affected)
            if overlap:
                invalid.append({"cache_key": row["cache_key"], "overlap": overlap, "command": json.loads(row["command_json"])})
        return invalid
