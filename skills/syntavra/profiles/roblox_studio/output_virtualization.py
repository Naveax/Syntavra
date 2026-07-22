from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class OutputCapsule:
    raw_hash: str
    raw_path: str
    events: tuple[str, ...]
    duplicate_count: int
    critical_markers: tuple[str, ...]


_CRITICAL = re.compile(r"(?i)(error|failed|traceback|security|unauthorized|rollback|assert|warn)")


def virtualize_output(raw: str, *, storage_root: Path, family: str = "generic") -> OutputCapsule:
    encoded = raw.encode("utf-8", errors="replace")
    raw_hash = hashlib.sha256(encoded).hexdigest()
    storage_root = Path(storage_root)
    storage_root.mkdir(parents=True, exist_ok=True)
    path = storage_root / f"{raw_hash}.log"
    path.write_bytes(encoded)
    seen: set[str] = set()
    events: list[str] = []
    duplicates = 0
    markers: list[str] = []
    for line in raw.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        if normalized in seen:
            duplicates += 1
            continue
        seen.add(normalized)
        if _CRITICAL.search(normalized):
            markers.append(normalized)
        if family == "roblox" and any(prefix in normalized for prefix in ("Script '://", "ServerScriptService", "ReplicatedStorage", "Players.")):
            markers.append(normalized)
        if len(events) < 64:
            events.append(normalized[:1000])
    return OutputCapsule(raw_hash, str(path), tuple(events), duplicates, tuple(dict.fromkeys(markers)))
