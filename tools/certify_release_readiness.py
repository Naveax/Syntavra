#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.provider_e5 import certify_provider_e5
from syntavra_runtime.util import atomic_write_json, canonical_json

DEFAULT_CONTRACT = ROOT / "contracts/python/token-economy-frozen-workloads-v1.json"

_PRIORITY_LABELS = frozenset(
    {
        "p0",
        "p1",
        "priority:p0",
        "priority:p1",
        "priority/p0",
        "priority/p1",
        "severity:p0",
        "severity:p1",
        "severity/p0",
        "severity/p1",
        "blocker:p0",
        "blocker:p1",
    }
)
_PRIORITY_TITLE = re.compile(r"^\s*(?:\[p[01]\]|p[01]\s*[:\-])", re.IGNORECASE)
_RECEIPT_BINDING_FIELDS = (
    "run_id",
    "task_id",
    "arm_id",
    "repetition",
    "cache_mode",
    "provider_response_hash",
    "usage_receipt_hash",
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _hex64(value: Any) -> bool:
    text = str(value or "").casefold()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def _receipt_hash(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("receipt_sha256", None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _label_names(issue: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    labels = issue.get("labels", [])
    if not isinstance(labels, list):
        return result
    for raw in labels:
        if isinstance(raw, Mapping):
            name = str(raw.get("name") or "")
        else:
            name = str(raw or "")
        if name:
            result.add(name.strip().casefold())
    return result


def _priority_marker(issue: Mapping[str, Any]) -> str:
    labels = _label_names(issue)
    matched = sorted(labels & _PRIORITY_LABELS)
    if matched:
        return f"label:{matched[0]}"
    title = str(issue.get("title") or "")
    title_match = _PRIORITY_TITLE.search(title)
    if title_match:
        return f"title:{title_match.group(0).strip()}"
    return ""


def fetch_open_issue_snapshot(
    repository: str,
    token: str,
    *,
    exact_head: str = "",
    max_pages: int = 100,
) -> dict[str, Any]:
    if "/" not in repository or repository.startswith("/") or repository.endswith("/"):
        raise ValueError("repository must be owner/name")
    if not token:
        raise ValueError("GitHub token is required for a complete issue snapshot")
    issues: list[dict[str, Any]] = []
    page = 1
    while True:
        if page > max_pages:
            raise RuntimeError("open issue snapshot exceeded pagination safety limit")
        query = urllib.parse.urlencode({"state": "open", "per_page": 100, "page": page})
        url = f"https://api.github.com/repos/{repository}/issues?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "syntavra-release-readiness/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"GitHub open issue snapshot failed on page {page}: {type(exc).__name__}"
            ) from exc
        if not isinstance(payload, list):
            raise RuntimeError("GitHub open issue response was not a list")
        for raw in payload:
            if isinstance(raw, Mapping):
                issues.append(dict(raw))
        if len(payload) < 100:
            break
        page += 1

    return {
        "schema_version": 1,
        "family": "syntavra-open-github-issue-snapshot",
        "repository": repository,
        "exact_head": exact_head,
        "complete": True,
        "pages_fetched": page,
        "open_issue_or_pr_count": len(issues),
        "issues": issues,
    }


def certify_known_p0_p1_blockers(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if snapshot.get("complete") is not True:
        failures.append("issue-snapshot-not-complete")
    repository = str(snapshot.get("repository") or "")
    if not repository:
        failures.append("repository-identity-missing")
    raw_issues = snapshot.get("issues", [])
    if not isinstance(raw_issues, list):
        failures.append("issues-not-list")
        raw_issues = []

    blockers: list[dict[str, Any]] = []
    issue_count = 0
    for raw in raw_issues:
        if not isinstance(raw, Mapping):
            failures.append("issue-entry-not-object")
            continue
        if raw.get("pull_request") is not None:
            continue
        state = str(raw.get("state") or "open").casefold()
        if state != "open":
            continue
        issue_count += 1
        marker = _priority_marker(raw)
        if not marker:
            continue
        blockers.append(
            {
                "number": int(raw.get("number") or 0),
                "title": str(raw.get("title") or ""),
                "marker": marker,
                "html_url": str(raw.get("html_url") or ""),
            }
        )

    if blockers:
        failures.append(f"known-p0-p1-blockers:{len(blockers)}")
    ok = not failures
    return {
        "schema_version": 1,
        "claim": (
            "ZERO_KNOWN_P0_P1_BLOCKERS_PROVEN"
            if ok
            else "ZERO_KNOWN_P0_P1_BLOCKERS_NOT_PROVEN"
        ),
        "claim_boundary": (
            "This certifies only the complete open GitHub issue snapshot under the repository's explicit P0/P1 "
            "label/title marker policy at certification time; it does not claim that undiscovered defects do not exist."
        ),
        "ok": ok,
        "score": 10.0 if ok else 5.0,
        "repository": repository,
        "exact_head": str(snapshot.get("exact_head") or ""),
        "open_issue_count": issue_count,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "failures": failures,
    }


def _readiness_receipt_for(row: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    artifact_dir = Path(str(row.get("artifact_dir") or ""))
    arm_result_path = artifact_dir / "arm-result.json"
    if not arm_result_path.is_file():
        return None, "arm-result-missing"
    try:
        arm_result = json.loads(arm_result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "arm-result-invalid-json"
    if not isinstance(arm_result, Mapping):
        return None, "arm-result-not-object"
    receipt = arm_result.get("readiness_receipt")
    if not isinstance(receipt, Mapping):
        return None, "readiness-receipt-missing"
    value = dict(receipt)
    if value.get("schema_version") != 1:
        return None, "readiness-receipt-schema"
    stored_hash = str(value.get("receipt_sha256") or "").casefold()
    if not _hex64(stored_hash) or stored_hash != _receipt_hash(value):
        return None, "readiness-receipt-hash-invalid"

    for field in _RECEIPT_BINDING_FIELDS:
        expected = row.get(field)
        observed = value.get(field)
        if field == "repetition":
            try:
                expected = int(expected or 0)
                observed = int(observed or 0)
            except (TypeError, ValueError):
                return None, f"readiness-receipt-binding:{field}"
        else:
            expected = str(expected or "")
            observed = str(observed or "")
        if observed != expected:
            return None, f"readiness-receipt-binding:{field}"

    try:
        overflow = int(value.get("provider_overflow_count") or 0)
        explained = int(value.get("explained_provider_overflow_count") or 0)
    except (TypeError, ValueError):
        return None, "readiness-receipt-overflow-count-invalid"
    if overflow < 0 or explained < 0 or explained > overflow:
        return None, "readiness-receipt-overflow-count-invalid"

    reason_hashes = value.get("overflow_reason_hashes", [])
    if not isinstance(reason_hashes, list):
        return None, "readiness-receipt-overflow-reasons-not-list"
    if len(reason_hashes) != explained or any(not _hex64(item) for item in reason_hashes):
        return None, "readiness-receipt-overflow-reasons-invalid"

    rollback_verified = value.get("rollback_verified") is True
    failure_receipt_verified = value.get("failure_receipt_verified") is True
    rollback_hash = str(value.get("rollback_receipt_hash") or "")
    failure_hash = str(value.get("failure_receipt_hash") or "")
    if rollback_verified != _hex64(rollback_hash):
        return None, "readiness-receipt-rollback-proof-invalid"
    if failure_receipt_verified != _hex64(failure_hash):
        return None, "readiness-receipt-failure-proof-invalid"

    value["provider_overflow_count"] = overflow
    value["explained_provider_overflow_count"] = explained
    value["rollback_verified"] = rollback_verified
    value["failure_receipt_verified"] = failure_receipt_verified
    return value, ""


def certify_live_dogfood(
    replay: Mapping[str, Any],
    workload_contract: Mapping[str, Any],
) -> dict[str, Any]:
    e5 = certify_provider_e5(replay, workload_contract)
    failures: list[str] = []
    if e5.get("provider_evidence_complete") is not True:
        failures.append("provider-e5-not-valid")

    candidate = str(replay.get("candidate_arm") or "")
    rows = replay.get("results", [])
    if not isinstance(rows, list):
        rows = []
        failures.append("results-not-list")
    candidate_rows = [
        row
        for row in rows
        if isinstance(row, Mapping) and str(row.get("arm_id") or "") == candidate
    ]
    if not candidate:
        failures.append("candidate-arm-missing")
    if not candidate_rows:
        failures.append("candidate-results-missing")

    expected_tasks = {
        str(row.get("id") or "")
        for row in workload_contract.get("workloads", [])
        if isinstance(row, Mapping) and row.get("id")
    }
    observed_tasks = {str(row.get("task_id") or "") for row in candidate_rows}
    if expected_tasks != observed_tasks:
        failures.append("candidate-workload-coverage-mismatch")

    all_success = all(
        row.get("success") is True and row.get("verifier_success") is True
        for row in candidate_rows
    )
    if not all_success:
        failures.append("candidate-dogfood-failure")

    security_regressions = 0
    verifier_skips = 0
    for row in candidate_rows:
        try:
            security_regressions += int(row.get("security_regressions") or 0)
            verifier_skips += int(row.get("verifier_skips") or 0)
        except (TypeError, ValueError):
            failures.append("candidate-readiness-metric-invalid")
            break
    if security_regressions != 0:
        failures.append("candidate-security-regression")
    if verifier_skips != 0:
        failures.append("candidate-verifier-skip")

    receipts: list[dict[str, Any]] = []
    receipt_failures: list[str] = []
    total_overflow = 0
    explained_overflow = 0
    for row in candidate_rows:
        receipt, error = _readiness_receipt_for(row)
        if receipt is None:
            receipt_failures.append(
                f"{row.get('task_id')}:{row.get('cache_mode')}:r{row.get('repetition')}:{error}"
            )
            continue
        receipts.append(receipt)
        total_overflow += int(receipt["provider_overflow_count"])
        explained_overflow += int(receipt["explained_provider_overflow_count"])

    if len(receipts) != len(candidate_rows):
        failures.append(
            f"candidate-readiness-receipt-incomplete:{len(receipts)}!={len(candidate_rows)}"
        )
    unexplained_overflow = max(0, total_overflow - explained_overflow)
    if unexplained_overflow:
        failures.append(f"unexplained-provider-overflow:{unexplained_overflow}")

    b5_receipts = [
        receipt
        for receipt in receipts
        if str(receipt.get("task_id") or "").startswith("B5-")
    ]
    rollback_receipt_count = sum(
        1
        for receipt in b5_receipts
        if receipt.get("rollback_verified") is True
        and _hex64(receipt.get("rollback_receipt_hash"))
    )
    failure_receipt_count = sum(
        1
        for receipt in b5_receipts
        if receipt.get("failure_receipt_verified") is True
        and _hex64(receipt.get("failure_receipt_hash"))
    )
    if not b5_receipts:
        failures.append("b5-failure-repair-dogfood-missing")
    if rollback_receipt_count < 1:
        failures.append("rollback-receipt-not-verified")
    if failure_receipt_count < 1:
        failures.append("failure-receipt-not-verified")

    unique_failures = list(dict.fromkeys(failures))
    ok = not unique_failures
    candidate_scope = e5.get("scope", {}) if isinstance(e5.get("scope"), Mapping) else {}
    return {
        "schema_version": 1,
        "claim": (
            "LIVE_DOGFOOD_READINESS_PROVEN"
            if ok
            else "LIVE_DOGFOOD_READINESS_NOT_PROVEN"
        ),
        "claim_boundary": (
            "Dogfood readiness is bounded to the exact E5 candidate arm/version and frozen B0-B9 provider run. "
            "Every candidate run must carry a self-hashed readiness receipt bound to the provider result; "
            "unexplained provider overflow, security regression, verifier bypass, or missing B5 rollback/failure proof blocks promotion."
        ),
        "ok": ok,
        "score": 10.0 if ok else min(float(e5.get("score") or 0.0), 8.0),
        "provider_e5": e5,
        "candidate_arm": candidate,
        "candidate_version": str(candidate_scope.get("candidate_version") or ""),
        "candidate_result_count": len(candidate_rows),
        "candidate_workload_count": len(observed_tasks),
        "candidate_all_success": all_success,
        "readiness_receipt_count": len(receipts),
        "total_provider_overflow_count": total_overflow,
        "explained_provider_overflow_count": explained_overflow,
        "unexplained_provider_overflow_count": unexplained_overflow,
        "rollback_receipt_count": rollback_receipt_count,
        "failure_receipt_count": failure_receipt_count,
        "readiness_receipt_hashes": sorted(
            str(receipt.get("receipt_sha256") or "") for receipt in receipts
        ),
        "receipt_failures": receipt_failures,
        "failures": unique_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Certify live B0-B9 dogfood readiness and/or zero known P0/P1 GitHub blockers."
        )
    )
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--issues-json", type=Path)
    parser.add_argument("--github-repository")
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--exact-head", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-dogfood", action="store_true")
    parser.add_argument("--require-blocker-free", action="store_true")
    args = parser.parse_args()

    result: dict[str, Any] = {
        "schema_version": 1,
        "family": "syntavra-release-readiness-certification",
        "exact_head": args.exact_head,
    }
    requested = False
    observed_ok = True
    required_ok = True

    if args.replay is not None:
        requested = True
        replay = _json(args.replay)
        contract = _json(args.contract)
        dogfood = certify_live_dogfood(replay, contract)
        result["dogfood"] = dogfood
        observed_ok = observed_ok and bool(dogfood["ok"])
        if args.require_dogfood:
            required_ok = required_ok and bool(dogfood["ok"])
    elif args.require_dogfood:
        raise SystemExit("--require-dogfood requires --replay")

    snapshot: dict[str, Any] | None = None
    if args.issues_json is not None:
        snapshot = _json(args.issues_json)
    elif args.github_repository:
        token = os.environ.get(args.github_token_env, "")
        snapshot = fetch_open_issue_snapshot(
            args.github_repository,
            token,
            exact_head=args.exact_head,
        )
    if snapshot is not None:
        requested = True
        blockers = certify_known_p0_p1_blockers(snapshot)
        result["known_blockers"] = blockers
        observed_ok = observed_ok and bool(blockers["ok"])
        if args.require_blocker_free:
            required_ok = required_ok and bool(blockers["ok"])
    elif args.require_blocker_free:
        raise SystemExit(
            "--require-blocker-free requires --issues-json or --github-repository"
        )

    if not requested:
        raise SystemExit("at least one readiness evidence source is required")

    result["ok"] = observed_ok
    result["claim"] = (
        "REQUESTED_RELEASE_READINESS_GATES_PROVEN"
        if observed_ok
        else "REQUESTED_RELEASE_READINESS_GATES_NOT_PROVEN"
    )
    if args.output:
        atomic_write_json(args.output, result, mode=0o600)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if required_ok else 8


if __name__ == "__main__":
    raise SystemExit(main())
