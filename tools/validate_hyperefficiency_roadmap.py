#!/usr/bin/env python3
from __future__ import annotations
import gzip, hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "contracts/python/hyperefficiency-roadmap-v1.json"

def validate() -> list[str]:
    errors: list[str] = []
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    rows: list[list[object]] = []
    for meta in index["shards"]:
        path = ROOT / meta["path"]
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != meta["sha256"]:
            errors.append(f"sha256:{meta['path']}")
            continue
        shard = json.loads(payload)
        rows.extend(shard["capabilities"])
    if len(rows) != 1284:
        errors.append(f"count:{len(rows)}")
    else:
        source = [int(row[0]) for row in rows]
        canonical = [int(row[1]) for row in rows]
        if source != list(range(1, 1285)): errors.append("source-sequence")
        if canonical != list(range(281, 1565)): errors.append("canonical-sequence")
        if any(c != s + 280 for s, c in zip(source, canonical)): errors.append("mapping-offset")
    for meta in index.get("source_provenance", []):
        path = ROOT / meta["path"]
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != meta["gzip_sha256"]:
            errors.append(f"provenance-gzip:{meta['path']}")
            continue
        if hashlib.sha256(gzip.decompress(payload)).hexdigest() != meta["source_sha256"]:
            errors.append(f"provenance-source:{meta['path']}")
    return errors

def main() -> int:
    errors = validate()
    if errors:
        print("HyperEfficiency roadmap invalid:", ", ".join(errors)); return 1
    print("HyperEfficiency roadmap verified: 1284 items; HE-0001..1284 -> CAP-0281..1564; provenance exact")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
