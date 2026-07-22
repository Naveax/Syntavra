from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VerifiedRealTask:
    identity: str
    repository: str
    issue_number: int
    receipt_path: str
    patch_sha256: str
    evidence_objects: int
    patched_tests: int


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _integer(value: Any, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def verify_real_task_receipt(path: Path) -> tuple[VerifiedRealTask | None, list[str]]:
    path = Path(path)
    reasons: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"receipt-unreadable:{type(exc).__name__}"]

    if payload.get("schema_version") != 1:
        reasons.append("schema-version")
    if payload.get("receipt_type") != "syntavra-real-repository-task":
        reasons.append("receipt-type")

    task = payload.get("task")
    result = payload.get("result")
    artifacts = payload.get("artifacts")
    execution = payload.get("execution")
    boundary = payload.get("claim_boundary")
    if not isinstance(task, dict):
        reasons.append("task-object")
        task = {}
    if not isinstance(result, dict):
        reasons.append("result-object")
        result = {}
    if not isinstance(artifacts, dict):
        reasons.append("artifacts-object")
        artifacts = {}
    if not isinstance(execution, dict):
        reasons.append("execution-object")
        execution = {}
    if not isinstance(boundary, dict):
        reasons.append("claim-boundary-object")
        boundary = {}

    identity = task.get("identity")
    repository = task.get("repository")
    issue_number = _integer(task.get("issue_number"), minimum=1)
    if not isinstance(identity, str) or not identity.strip():
        reasons.append("task-identity")
    if not isinstance(repository, str) or "/" not in repository:
        reasons.append("repository")
    if issue_number is None:
        reasons.append("issue-number")
    if task.get("issue_state_at_selection") != "open":
        reasons.append("issue-not-open-at-selection")
    if task.get("baseline_matches_source") is not True:
        reasons.append("baseline-source-identity")

    if result.get("status") != "FIXED_LOCALLY_VERIFIED":
        reasons.append("result-status")
    if result.get("working_tree_clean") is not True:
        reasons.append("working-tree-dirty")

    baseline = result.get("baseline_tests")
    patched = result.get("patched_tests")
    if not isinstance(baseline, dict):
        reasons.append("baseline-tests-object")
        baseline = {}
    if not isinstance(patched, dict):
        reasons.append("patched-tests-object")
        patched = {}
    baseline_failed = _integer(baseline.get("failed"))
    patched_failed = _integer(patched.get("failed"))
    patched_passed = _integer(patched.get("passed"), minimum=1)
    if baseline_failed is None or baseline_failed < 1:
        reasons.append("baseline-did-not-fail")
    if patched_failed != 0:
        reasons.append("patched-tests-failed")
    if patched_passed is None:
        reasons.append("patched-tests-empty")

    patch_info = artifacts.get("patch")
    patch_sha = ""
    if not isinstance(patch_info, dict):
        reasons.append("patch-object")
    else:
        relative = patch_info.get("path")
        expected_sha = patch_info.get("sha256")
        if not isinstance(relative, str) or not relative:
            reasons.append("patch-path")
        else:
            patch_path = path.parent / relative
            try:
                patch_sha = _sha256_file(patch_path)
            except OSError:
                reasons.append("patch-missing")
            else:
                if patch_sha != expected_sha:
                    reasons.append("patch-hash")
                declared_bytes = _integer(patch_info.get("bytes"))
                if declared_bytes is None or declared_bytes != patch_path.stat().st_size:
                    reasons.append("patch-size")

    evidence_rows = artifacts.get("evidence")
    evidence_count = 0
    if not isinstance(evidence_rows, list) or not evidence_rows:
        reasons.append("evidence-empty")
        evidence_rows = []
    for index, row in enumerate(evidence_rows):
        if not isinstance(row, dict):
            reasons.append(f"evidence-{index}-object")
            continue
        relative = row.get("path")
        expected_sha = row.get("sha256")
        handle = row.get("handle")
        if not isinstance(relative, str) or not relative:
            reasons.append(f"evidence-{index}-path")
            continue
        evidence_path = path.parent / relative
        try:
            actual_sha = _sha256_file(evidence_path)
        except OSError:
            reasons.append(f"evidence-{index}-missing")
            continue
        if actual_sha != expected_sha:
            reasons.append(f"evidence-{index}-hash")
        if handle != f"sc://sha256/{actual_sha}":
            reasons.append(f"evidence-{index}-handle")
        declared_bytes = _integer(row.get("bytes"))
        if declared_bytes is None or declared_bytes != evidence_path.stat().st_size:
            reasons.append(f"evidence-{index}-size")
        evidence_count += 1

    if execution.get("all_evidence_verified") is not True:
        reasons.append("execution-evidence-unverified")
    if boundary.get("counts_as_real_repository_task") is not True:
        reasons.append("real-task-boundary")
    if boundary.get("counts_as_competitor_arm") is not False:
        reasons.append("competitor-arm-boundary")
    if boundary.get("public_superiority_proven") is not False:
        reasons.append("superiority-boundary")

    if reasons:
        return None, reasons
    return (
        VerifiedRealTask(
            identity=str(identity),
            repository=str(repository),
            issue_number=int(issue_number),
            receipt_path=path.as_posix(),
            patch_sha256=patch_sha,
            evidence_objects=evidence_count,
            patched_tests=int(patched_passed),
        ),
        [],
    )


def load_verified_real_tasks(root: Path) -> dict[str, Any]:
    root = Path(root)
    verified: list[VerifiedRealTask] = []
    rejected: list[dict[str, Any]] = []
    identities: set[str] = set()
    receipt_paths = sorted(root.glob("*/receipt.json")) if root.is_dir() else []
    for receipt_path in receipt_paths:
        task, reasons = verify_real_task_receipt(receipt_path)
        if task is None:
            rejected.append(
                {"receipt_path": receipt_path.as_posix(), "reasons": reasons}
            )
            continue
        if task.identity in identities:
            rejected.append(
                {
                    "receipt_path": receipt_path.as_posix(),
                    "reasons": ["duplicate-task-identity"],
                }
            )
            continue
        identities.add(task.identity)
        verified.append(task)
    return {
        "verified_count": len(verified),
        "verified": [asdict(task) for task in verified],
        "rejected_count": len(rejected),
        "rejected": rejected,
    }
