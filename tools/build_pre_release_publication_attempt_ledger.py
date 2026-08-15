#!/usr/bin/env python3
"""Build a non-canonical ledger of one guarded pre-release publication attempt.

The ledger aggregates public-visibility evidence produced by the serialized
publication jobs. It never contacts a registry, uses no credentials, and never
mutates release/publish-readiness.json. Its purpose is forensic continuity when
an irreversible publication attempt succeeds only partially.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

VERSION = "0.0.1"
CHANNEL = "pre-release"
PRODUCTION_TARGETS = (
    "rust_contracts",
    "rust_core",
    "rust_cli",
    "npm",
    "npm_sdk",
    "python",
    "vscode",
)
LEGACY_TARGET = "legacy_native_companion"
ALL_TARGETS = (*PRODUCTION_TARGETS, LEGACY_TARGET)
TARGET_JOB = {
    "rust_contracts": "rust_production",
    "rust_core": "rust_production",
    "rust_cli": "rust_production",
    "npm": "npm_installer",
    "npm_sdk": "npm_sdk",
    "python": "pypi",
    "vscode": "vscode",
    "legacy_native_companion": "legacy_native_companion",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_visibility_evidence(root: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    evidence: dict[str, dict[str, Any]] = {}
    integrity_errors: list[str] = []
    if not root.exists():
        return evidence, integrity_errors

    for path in sorted(root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            integrity_errors.append(f"invalid-json:{path.name}:{type(exc).__name__}")
            continue
        if not isinstance(value, dict):
            integrity_errors.append(f"non-object-json:{path.name}")
            continue
        if value.get("product") != "Syntavra" or value.get("version") != VERSION:
            continue
        if value.get("channel") != CHANNEL:
            continue
        target = value.get("target")
        if target not in ALL_TARGETS:
            continue
        if target in evidence:
            integrity_errors.append(f"duplicate-target-evidence:{target}")
            continue
        evidence[target] = {
            "file": str(path.relative_to(root)).replace("\\", "/"),
            "sha256": _sha256(path),
            "visibility_verified": value.get("visibility_verified") is True,
            "claim": value.get("claim"),
            "registry": value.get("registry"),
            "package": value.get("package"),
            "publication_performed_by_checker": value.get("publication_performed_by_checker"),
            "canonical_readiness_mutated": value.get("canonical_readiness_mutated"),
        }
        if value.get("publication_performed_by_checker") is not False:
            integrity_errors.append(f"checker-publication-boundary-invalid:{target}")
        if value.get("canonical_readiness_mutated") is not False:
            integrity_errors.append(f"checker-readiness-boundary-invalid:{target}")

    return evidence, integrity_errors


def build_ledger(
    *,
    exact_head: str,
    visibility_root: Path,
    job_results: dict[str, Any],
    legacy_requested: bool,
) -> dict[str, Any]:
    if len(exact_head) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in exact_head):
        raise ValueError("exact_head must be exactly 40 hexadecimal characters")
    if not isinstance(job_results, dict):
        raise ValueError("job_results must be an object")

    evidence, integrity_errors = collect_visibility_evidence(visibility_root)
    requested_targets = list(PRODUCTION_TARGETS)
    if legacy_requested:
        requested_targets.append(LEGACY_TARGET)

    targets: dict[str, Any] = {}
    visible_targets: list[str] = []
    unverified_targets: list[str] = []
    missing_targets: list[str] = []

    for target in ALL_TARGETS:
        requested = target in requested_targets
        item = evidence.get(target)
        if item is None:
            state = "missing"
            if requested:
                missing_targets.append(target)
        elif item["visibility_verified"]:
            state = "visible"
            if requested:
                visible_targets.append(target)
        else:
            state = "unverified"
            if requested:
                unverified_targets.append(target)
        targets[target] = {
            "requested": requested,
            "job": TARGET_JOB[target],
            "job_result": job_results.get(TARGET_JOB[target]),
            "state": state,
            "evidence": item,
        }

    production_visible = all(targets[target]["state"] == "visible" for target in PRODUCTION_TARGETS)
    all_requested_visible = all(targets[target]["state"] == "visible" for target in requested_targets)
    any_requested_visible = any(targets[target]["state"] == "visible" for target in requested_targets)
    partial_publication_observed = any_requested_visible and not all_requested_visible

    for target in requested_targets:
        job_result = targets[target]["job_result"]
        state = targets[target]["state"]
        if job_result == "success" and state != "visible":
            integrity_errors.append(f"successful-job-without-visible-evidence:{target}")

    if all_requested_visible:
        claim = "REQUESTED_PUBLICATION_VISIBILITY_COMPLETE"
    elif partial_publication_observed:
        claim = "PARTIAL_PUBLICATION_VISIBILITY_OBSERVED"
    else:
        claim = "NO_REQUESTED_PUBLICATION_VISIBILITY_CONFIRMED"

    return {
        "schema_version": 1,
        "product": "Syntavra",
        "version": VERSION,
        "channel": CHANNEL,
        "exact_head": exact_head.lower(),
        "authority": "pre-release-publication-attempt-ledger",
        "canonical_readiness_mutated": False,
        "registry_receipts_admitted": False,
        "claim_boundary": "PUBLIC_VISIBILITY_EVIDENCE_ONLY_NOT_CANONICAL_REGISTRY_RECEIPT_ADMISSION",
        "publication_order": [
            "rust_contracts",
            "rust_core",
            "rust_cli",
            "npm",
            "npm_sdk",
            "python",
            "vscode",
            "legacy_native_companion",
        ],
        "legacy_requested": legacy_requested,
        "requested_targets": requested_targets,
        "job_results": job_results,
        "targets": targets,
        "observed_visibility_evidence_count": len(evidence),
        "visible_requested_targets": visible_targets,
        "unverified_requested_targets": unverified_targets,
        "missing_requested_targets": missing_targets,
        "production_publication_fully_visible": production_visible,
        "all_requested_visibility_verified": all_requested_visible,
        "partial_publication_observed": partial_publication_observed,
        "integrity_errors": sorted(set(integrity_errors)),
        "claim": claim,
        "next_authority": "Independently verify public registry receipts and admit canonical published state through a separate exact-head reviewed change.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exact-head", required=True)
    parser.add_argument("--visibility-root", type=Path, required=True)
    parser.add_argument("--job-results-json", type=Path, required=True)
    parser.add_argument("--legacy-requested", choices=("true", "false"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    job_results = json.loads(args.job_results_json.read_text(encoding="utf-8"))
    ledger = build_ledger(
        exact_head=args.exact_head,
        visibility_root=args.visibility_root,
        job_results=job_results,
        legacy_requested=args.legacy_requested == "true",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
