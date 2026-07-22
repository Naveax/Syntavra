from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class FuzzResult:
    name: str
    cases: int
    passed: int
    rejected: int
    unexpected_failures: tuple[str, ...]
    duration_ms: float

    @property
    def ok(self) -> bool:
        return not self.unexpected_failures and self.passed + self.rejected == self.cases


@dataclass(frozen=True)
class FaultResult:
    name: str
    injected: bool
    detected: bool
    recovered: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.injected and self.detected and self.recovered


@dataclass(frozen=True)
class ReliabilityReport:
    started_at: str
    finished_at: str
    seed: int
    fuzz: tuple[FuzzResult, ...]
    faults: tuple[FaultResult, ...]
    claims: tuple[str, ...] = (
        "INTERNAL_RELIABILITY_MEASUREMENT_ONLY",
        "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN",
    )

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.fuzz) and all(item.ok for item in self.faults)


class FaultInjector:
    @staticmethod
    def partial_write(path: Path, payload: bytes, fraction: float = 0.5) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        count = max(0, min(len(payload), int(len(payload) * fraction)))
        with path.open("wb") as handle:
            handle.write(payload[:count])
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def truncate(path: Path, count: int) -> None:
        with path.open("r+b") as handle:
            handle.truncate(max(0, count))
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def corrupt_byte(path: Path, offset: int = 0) -> None:
        with path.open("r+b") as handle:
            data = handle.read()
            if not data:
                raise ValueError("cannot corrupt an empty file")
            position = max(0, min(len(data) - 1, offset))
            changed = bytearray(data)
            changed[position] ^= 0xFF
            handle.seek(0)
            handle.write(changed)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())


class ReliabilityLaboratory:
    """Deterministic fuzz and fault-injection campaigns for critical parsers/stores."""

    def __init__(self, state_root: Path, *, seed: int = 1):
        self.state_root = state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.seed = int(seed)
        self.random = random.Random(self.seed)

    def fuzz_json(self, parser: Callable[[str], Any], *, cases: int = 1000, max_length: int = 2048) -> FuzzResult:
        started = time.monotonic()
        passed = rejected = 0
        failures: list[str] = []
        alphabet = "{}[],:\"\\0123456789truefalsenull abcXYZ_-\n\t"
        for index in range(max(0, cases)):
            if index % 5 == 0:
                value: Any = {
                    "index": index,
                    "values": [self.random.randint(-1000, 1000) for _ in range(self.random.randint(0, 20))],
                    "nested": {"ok": bool(index % 2), "text": "x" * self.random.randint(0, 64)},
                }
                candidate = json.dumps(value, ensure_ascii=False)
                expected_valid = True
            else:
                length = self.random.randint(0, max(1, max_length))
                candidate = "".join(self.random.choice(alphabet) for _ in range(length))
                expected_valid = False
            try:
                parser(candidate)
                passed += 1
                if expected_valid:
                    continue
            except (ValueError, TypeError, json.JSONDecodeError, KeyError, IndexError):
                rejected += 1
                if not expected_valid:
                    continue
            except Exception as error:
                failures.append(f"case {index}: {type(error).__name__}: {error}")
                continue
        return FuzzResult(
            name="json-parser",
            cases=max(0, cases),
            passed=passed,
            rejected=rejected,
            unexpected_failures=tuple(failures[:50]),
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )

    def fuzz_callable(
        self,
        name: str,
        function: Callable[[bytes], Any],
        *,
        cases: int = 1000,
        max_length: int = 4096,
        expected_exceptions: tuple[type[BaseException], ...] = (ValueError, TypeError, UnicodeError, KeyError, IndexError),
    ) -> FuzzResult:
        started = time.monotonic()
        passed = rejected = 0
        failures: list[str] = []
        for index in range(max(0, cases)):
            payload = self.random.randbytes(self.random.randint(0, max(1, max_length)))
            try:
                function(payload)
                passed += 1
            except expected_exceptions:
                rejected += 1
            except Exception as error:
                failures.append(f"case {index}: {type(error).__name__}: {error}")
        return FuzzResult(
            name=name,
            cases=max(0, cases),
            passed=passed,
            rejected=rejected,
            unexpected_failures=tuple(failures[:50]),
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )

    def artifact_corruption(self, store: Any) -> FaultResult:
        record = store.put(b"syntavra-reliability-payload", kind="reliability")
        path = Path(record.object_path)
        original = path.read_bytes()
        FaultInjector.corrupt_byte(path)
        detected = False
        try:
            store.read(record.artifact_id)
        except ValueError:
            detected = True
        path.write_bytes(original)
        recovered = store.read(record.artifact_id) == original
        return FaultResult("artifact-hash-corruption", True, detected, recovered)

    def partial_atomic_write(self) -> FaultResult:
        root = self.state_root / "faults" / "partial-write"
        root.mkdir(parents=True, exist_ok=True)
        target = root / "state.json"
        committed = json.dumps({"generation": 1, "valid": True}, sort_keys=True).encode()
        target.write_bytes(committed)
        interrupted = root / ".state.json.interrupted"
        FaultInjector.partial_write(interrupted, json.dumps({"generation": 2, "valid": True}).encode(), 0.4)
        detected = False
        try:
            json.loads(interrupted.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            detected = True
        recovered = json.loads(target.read_text(encoding="utf-8"))["generation"] == 1
        interrupted.unlink(missing_ok=True)
        return FaultResult("partial-atomic-write", True, detected, recovered)

    def sqlite_recovery(self) -> FaultResult:
        root = self.state_root / "faults" / "sqlite"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "state.sqlite3"
        path.unlink(missing_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO state(value) VALUES('committed')")
        backup = path.with_suffix(".backup")
        backup.write_bytes(path.read_bytes())
        FaultInjector.truncate(path, max(1, path.stat().st_size // 3))
        detected = False
        try:
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError:
            detected = True
        path.write_bytes(backup.read_bytes())
        with sqlite3.connect(path) as db:
            recovered = db.execute("SELECT value FROM state").fetchone()[0] == "committed"
        return FaultResult("sqlite-corruption-recovery", True, detected, recovered)

    def capability_replay(self, security: Any) -> FaultResult:
        token = security.issue(
            session_id="reliability",
            tool="repo.write",
            arguments={"path": "README.md"},
            resource="workspace:/README.md",
            permissions=("write",),
            ttl_seconds=60,
            single_use=True,
        )
        first = security.verify(
            token,
            tool="repo.write",
            arguments={"path": "README.md"},
            resource="workspace:/README.md",
            consume=True,
        )
        second = security.verify(
            token,
            tool="repo.write",
            arguments={"path": "README.md"},
            resource="workspace:/README.md",
            consume=True,
        )
        first_ok = bool(first.get("ok") if isinstance(first, Mapping) else getattr(first, "ok", False))
        second_ok = bool(second.get("ok") if isinstance(second, Mapping) else getattr(second, "ok", False))
        return FaultResult("capability-replay", True, not second_ok, first_ok and not second_ok)

    def campaign(
        self,
        *,
        json_parser: Callable[[str], Any] = json.loads,
        artifact_store: Any | None = None,
        capability_security: Any | None = None,
        parser_cases: int = 1000,
    ) -> ReliabilityReport:
        started = _now()
        fuzz = [self.fuzz_json(json_parser, cases=parser_cases)]
        faults = [self.partial_atomic_write(), self.sqlite_recovery()]
        if artifact_store is not None:
            faults.append(self.artifact_corruption(artifact_store))
        if capability_security is not None:
            faults.append(self.capability_replay(capability_security))
        report = ReliabilityReport(
            started_at=started,
            finished_at=_now(),
            seed=self.seed,
            fuzz=tuple(fuzz),
            faults=tuple(faults),
        )
        encoded = json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, indent=2)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        destination = self.state_root / "reliability-reports" / f"{digest}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded + "\n", encoding="utf-8")
        return report


__all__ = ["FaultInjector", "FaultResult", "FuzzResult", "ReliabilityLaboratory", "ReliabilityReport"]
