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

    try:
        expected_count = int(index["imported_count"])
        source_start, source_end = (int(value) for value in index["source_range"])
        canonical_start, canonical_end = (int(value) for value in index["canonical_range"])
        mapping_offset = int(index["mapping_offset"])
    except (KeyError, TypeError, ValueError):
        return ["index-range"]

    if _range_size(index.get("source_range")) != expected_count:
        errors.append("index-source-range-count")
    if _range_size(index.get("canonical_range")) != expected_count:
        errors.append("index-canonical-range-count")
    if canonical_start != source_start + mapping_offset or canonical_end != source_end + mapping_offset:
        errors.append("index-mapping-range")

    expected_next_source = source_start
    expected_next_canonical = canonical_start

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

        try:
            meta_source = [int(value) for value in meta["source_range"]]
            meta_canonical = [int(value) for value in meta["canonical_range"]]
        except (KeyError, TypeError, ValueError):
            errors.append(f"meta-range:{relative}")
            continue

        if meta_source[0] != expected_next_source:
            errors.append(f"source-shard-gap:{relative}:{meta_source[0]}!={expected_next_source}")
        if meta_canonical[0] != expected_next_canonical:
            errors.append(f"canonical-shard-gap:{relative}:{meta_canonical[0]}!={expected_next_canonical}")
        expected_next_source = meta_source[1] + 1
        expected_next_canonical = meta_canonical[1] + 1

        if shard_rows:
            try:
                first_source = int(shard_rows[0][0])
                last_source = int(shard_rows[-1][0])
                first_canonical = int(shard_rows[0][1])
                last_canonical = int(shard_rows[-1][1])
            except (IndexError, TypeError, ValueError):
                errors.append(f"row-boundary:{relative}")
            else:
                if [first_source, last_source] != meta_source:
                    errors.append(f"source-boundary:{relative}")
                if [first_canonical, last_canonical] != meta_canonical:
                    errors.append(f"canonical-boundary:{relative}")

        rows.extend(shard_rows)

    if expected_next_source != source_end + 1:
        errors.append("source-shard-coverage")
    if expected_next_canonical != canonical_end + 1:
        errors.append("canonical-shard-coverage")
    if len(rows) != expected_count:
        errors.append(f"count:{len(rows)}!={expected_count}")
        return errors

    source = [int(row[0]) for row in rows]
    canonical = [int(row[1]) for row in rows]
    if source != list(range(source_start, source_end + 1)):
        errors.append("source-sequence")
    if canonical != list(range(canonical_start, canonical_end + 1)):
        errors.append("canonical-sequence")
    if any(c != s + mapping_offset for s, c in zip(source, canonical)):
        errors.append("mapping-offset")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("HyperEfficiency roadmap invalid:", ", ".join(errors))
        return 1
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    print(
        "HyperEfficiency roadmap verified: "
        f"{index['imported_count']} items; "
        f"HE-{int(index['source_range'][0]):04d}..{int(index['source_range'][1]):04d} -> "
        f"CAP-{int(index['canonical_range'][0]):04d}..{int(index['canonical_range'][1]):04d}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
