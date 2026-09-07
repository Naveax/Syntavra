#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "contracts/python/hyperefficiency-roadmap-v1.json"
EXPECTED_CAPABILITY_FIELDS = [
    "he_number",
    "canonical_number",
    "title",
    "generation",
    "source_section",
]


def _range_size(value: Any) -> int | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    try:
        start, end = (int(value[0]), int(value[1]))
    except (TypeError, ValueError):
        return None
    return end - start + 1 if end >= start else None


def validate() -> list[str]:
    errors: list[str] = []
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    rows: list[list[object]] = []

    for meta in index["shards"]:
        relative = str(meta["path"])
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing:{relative}")
            continue

        payload = path.read_bytes()
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        expected_sha256 = str(meta.get("sha256") or "")
        if actual_sha256 != expected_sha256:
            errors.append(f"sha256:{relative}:expected={expected_sha256}:actual={actual_sha256}")
            continue

        try:
            shard = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"json:{relative}:{type(exc).__name__}")
            continue

        if shard.get("schema_version") != 1:
            errors.append(f"schema:{relative}")
        if shard.get("family") != "syntavra-hyperefficiency-roadmap-shard":
            errors.append(f"family:{relative}")
        if shard.get("default_state") != "admitted_reconciliation":
            errors.append(f"state:{relative}")
        if shard.get("capability_fields") != EXPECTED_CAPABILITY_FIELDS:
            errors.append(f"fields:{relative}")
        if shard.get("shard") != meta.get("shard"):
            errors.append(f"shard-id:{relative}")
        if shard.get("source_range") != meta.get("source_range"):
            errors.append(f"source-range:{relative}")
        if shard.get("canonical_range") != meta.get("canonical_range"):
            errors.append(f"canonical-range:{relative}")

        shard_rows = shard.get("capabilities")
        if not isinstance(shard_rows, list):
            errors.append(f"capabilities:{relative}")
            continue

        declared_count = int(meta.get("count", -1))
        if len(shard_rows) != declared_count:
            errors.append(f"declared-count:{relative}:{len(shard_rows)}!={declared_count}")
        if _range_size(meta.get("source_range")) != declared_count:
            errors.append(f"source-range-count:{relative}")
        if _range_size(meta.get("canonical_range")) != declared_count:
            errors.append(f"canonical-range-count:{relative}")

        if shard_rows:
            try:
                first_source = int(shard_rows[0][0])
                last_source = int(shard_rows[-1][0])
                first_canonical = int(shard_rows[0][1])
                last_canonical = int(shard_rows[-1][1])
                source_range = [int(value) for value in meta["source_range"]]
                canonical_range = [int(value) for value in meta["canonical_range"]]
            except (IndexError, KeyError, TypeError, ValueError):
                errors.append(f"row-boundary:{relative}")
            else:
                if [first_source, last_source] != source_range:
                    errors.append(f"source-boundary:{relative}")
                if [first_canonical, last_canonical] != canonical_range:
                    errors.append(f"canonical-boundary:{relative}")

        rows.extend(shard_rows)

    expected_count = int(index.get("imported_count", 1284))
    if expected_count != 1284:
        errors.append(f"index-count:{expected_count}")
    if len(rows) != expected_count:
        errors.append(f"count:{len(rows)}")
        return errors

    source = [int(row[0]) for row in rows]
    canonical = [int(row[1]) for row in rows]
    if source != list(range(1, 1285)):
        errors.append("source-sequence")
    if canonical != list(range(281, 1565)):
        errors.append("canonical-sequence")
    if any(c != s + 280 for s, c in zip(source, canonical)):
        errors.append("mapping-offset")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("HyperEfficiency roadmap invalid:", ", ".join(errors))
        return 1
    print("HyperEfficiency roadmap verified: 1284 items; HE-0001..1284 -> CAP-0281..1564")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
