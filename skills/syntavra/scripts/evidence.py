#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from common import canonical_hash, contains_secret, normalize
from store import project_id, put_object, transaction

INTEGRITY_RANK = {"T2": 1, "T1": 2, "T0": 3}


@dataclass(frozen=True)
class EvidenceItem:
    claim_id: str
    proposition: str
    commit_sha: str
    path: str
    start_line: int
    end_line: int
    source_engine: str
    integrity: str
    confidence: float
    content: str
    metadata: dict[str, Any]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def fingerprint(self) -> str:
        return canonical_hash({
            "claim_id": self.claim_id,
            "commit": self.commit_sha,
            "path": self.path.replace("\\", "/").casefold(),
            "range": [self.start_line, self.end_line],
            "content_hash": self.content_hash,
        })


def claim_id(proposition: str) -> str:
    # Stable proposition identity: normalized text with values preserved.
    canonical = normalize(re.sub(r"\s+", " ", proposition)).strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def overlap_ratio(a: EvidenceItem, b: EvidenceItem) -> float:
    if a.path.replace("\\", "/").casefold() != b.path.replace("\\", "/").casefold():
        return 0.0
    left = max(a.start_line, b.start_line)
    right = min(a.end_line, b.end_line)
    if right < left:
        return 0.0
    intersection = right - left + 1
    union = max(a.end_line, b.end_line) - min(a.start_line, b.start_line) + 1
    return intersection / max(1, union)


def novelty(item: EvidenceItem, existing: Iterable[EvidenceItem]) -> float:
    best = 0.0
    for other in existing:
        if item.fingerprint == other.fingerprint:
            return 0.0
        if item.claim_id == other.claim_id:
            best = max(best, 0.9)
        best = max(best, overlap_ratio(item, other))
        if item.content_hash == other.content_hash:
            best = max(best, 0.98)
    return max(0.0, 1.0 - best)


def authority_key(item: EvidenceItem) -> tuple[int, int, float, int]:
    exact_source = 1 if item.path and item.start_line > 0 else 0
    return (INTEGRITY_RANK.get(item.integrity, 0), exact_source, item.confidence, len(item.content))


def resolve_claim(items: list[EvidenceItem]) -> dict[str, Any]:
    if not items:
        return {"status": "missing", "winner": None, "alternatives": []}
    grouped: dict[str, list[EvidenceItem]] = {}
    for item in items:
        grouped.setdefault(item.content_hash, []).append(item)
    if len(grouped) == 1:
        winner = max(items, key=authority_key)
        return {"status": "corroborated" if len(items) > 1 else "single", "winner": asdict(winner), "alternatives": [], "engines": sorted({item.source_engine for item in items})}
    winner = max(items, key=authority_key)
    alternatives = [asdict(item) for item in sorted(items, key=authority_key, reverse=True) if item.fingerprint != winner.fingerprint]
    return {"status": "conflict-resolved-by-integrity-source-confidence", "winner": asdict(winner), "alternatives": alternatives}


def insert(project: str | Path, item: EvidenceItem, *, allow_secret: bool = False) -> dict[str, Any]:
    if item.integrity not in INTEGRITY_RANK:
        raise ValueError("integrity must be T0/T1/T2")
    if not 0.0 <= item.confidence <= 1.0:
        raise ValueError("confidence out of range")
    if contains_secret(item.content) and not allow_secret:
        raise ValueError("evidence contains likely secret")
    stored = put_object(project, item.content.encode("utf-8"), compress=True)
    pid = project_id(project)
    now = time.time()
    with transaction(project) as con:
        existing = con.execute("SELECT id,source_engine,content_hash FROM evidence WHERE project_id=? AND fingerprint=? AND status='active'", (pid, item.fingerprint)).fetchone()
        if existing:
            if existing["source_engine"] != item.source_engine:
                con.execute("UPDATE evidence SET confidence=MAX(confidence,?), metadata_json=? WHERE id=?", (item.confidence, json.dumps({**item.metadata, "corroborated_by": item.source_engine}, sort_keys=True), existing["id"]))
            return {"created": False, "id": existing["id"], "deduplicated": True, "handle": stored["handle"]}
        claim_rows = con.execute("SELECT * FROM evidence WHERE project_id=? AND claim_id=? AND status='active' ORDER BY confidence DESC", (pid, item.claim_id)).fetchall()
        supersedes = None
        corroborates = None
        status = "active"
        if claim_rows:
            same = next((row for row in claim_rows if row["content_hash"] == item.content_hash), None)
            if same:
                corroborates = same["id"]
            else:
                current_best = max(claim_rows, key=lambda row: (INTEGRITY_RANK.get(row["integrity"], 0), bool(row["path"]), row["confidence"]))
                current_key = (INTEGRITY_RANK.get(current_best["integrity"], 0), bool(current_best["path"]), current_best["confidence"])
                if authority_key(item)[:3] > current_key:
                    supersedes = current_best["id"]
                    con.execute("UPDATE evidence SET status='superseded' WHERE id=?", (supersedes,))
                else:
                    status = "alternative"
        cursor = con.execute(
            """
            INSERT INTO evidence(project_id,claim_id,fingerprint,commit_sha,path,start_line,end_line,source_engine,integrity,confidence,content_hash,recovery_handle,proposition,status,supersedes,corroborates,created,metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (pid, item.claim_id, item.fingerprint, item.commit_sha, item.path, item.start_line, item.end_line, item.source_engine, item.integrity, item.confidence, item.content_hash, stored["handle"], item.proposition, status, supersedes, corroborates, now, json.dumps(item.metadata, ensure_ascii=False, sort_keys=True)),
        )
        return {"created": True, "id": cursor.lastrowid, "status": status, "supersedes": supersedes, "corroborates": corroborates, "handle": stored["handle"]}


def context_delta(new_item: EvidenceItem, existing: Iterable[EvidenceItem]) -> dict[str, Any]:
    existing = list(existing)
    ratio = novelty(new_item, existing)
    if ratio == 0.0:
        return {"emit": False, "reason": "exact-duplicate", "reference": f"evidence:{new_item.fingerprint[:16]}"}
    overlaps = [item for item in existing if overlap_ratio(new_item, item) > 0]
    if overlaps:
        covered_lines: set[int] = set()
        for item in overlaps:
            covered_lines.update(range(max(new_item.start_line, item.start_line), min(new_item.end_line, item.end_line) + 1))
        novel_ranges: list[tuple[int, int]] = []
        start = None
        for line in range(new_item.start_line, new_item.end_line + 1):
            if line not in covered_lines and start is None:
                start = line
            if line in covered_lines and start is not None:
                novel_ranges.append((start, line - 1)); start = None
        if start is not None:
            novel_ranges.append((start, new_item.end_line))
        return {"emit": bool(novel_ranges), "reason": "overlap-delta", "novel_ranges": novel_ranges, "novelty": ratio}
    return {"emit": True, "reason": "novel", "novelty": ratio, "full_range": [new_item.start_line, new_item.end_line]}
