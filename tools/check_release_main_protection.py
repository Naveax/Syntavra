#!/usr/bin/env python3
"""Validate the effective protection contract for Syntavra's canonical main branch.

The checker consumes GitHub's `Get a branch` response plus `Get rules for a
branch` response. It performs no mutation and is suitable for publication
authority gates and audit artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
REQUIRED_RULE_TYPES = frozenset(
    {
        "pull_request",
        "required_status_checks",
        "non_fast_forward",
        "deletion",
    }
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rule_types(rules: Any) -> set[str]:
    if not isinstance(rules, list):
        raise ValueError("rules JSON must be an array")
    result: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("every branch rule must be an object")
        rule_type = rule.get("type")
        if isinstance(rule_type, str) and rule_type:
            result.add(rule_type)
    return result


def _status_contexts(rules: Any) -> list[str]:
    contexts: set[str] = set()
    if not isinstance(rules, list):
        return []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters") or {}
        checks = parameters.get("required_status_checks") or []
        if not isinstance(checks, list):
            continue
        for item in checks:
            if isinstance(item, dict):
                context = item.get("context")
                if isinstance(context, str) and context:
                    contexts.add(context)
    return sorted(contexts)


def build_report(*, exact_head: str, branch: dict[str, Any], rules: Any) -> dict[str, Any]:
    if not HEX_SHA_RE.fullmatch(exact_head):
        raise ValueError("exact_head must be exactly 40 hexadecimal characters")
    if not isinstance(branch, dict):
        raise ValueError("branch JSON must be an object")

    expected_head = exact_head.lower()
    branch_name = branch.get("name")
    observed_head = ((branch.get("commit") or {}).get("sha") or "").lower()
    protected = branch.get("protected") is True
    active_types = _rule_types(rules)
    missing_types = sorted(REQUIRED_RULE_TYPES - active_types)
    status_contexts = _status_contexts(rules)

    exact_head_matches = branch_name == "main" and observed_head == expected_head
    required_status_checks_present = "required_status_checks" in active_types and bool(status_contexts)
    effective_rules_ready = not missing_types and required_status_checks_present
    release_main_ready = exact_head_matches and protected and effective_rules_ready

    return {
        "schema_version": 1,
        "product": "Syntavra",
        "authority": "release-main-protection",
        "exact_head": expected_head,
        "publication_performed": False,
        "repository_mutated": False,
        "branch": {
            "name": branch_name,
            "observed_head": observed_head,
            "exact_head_matches": exact_head_matches,
            "protected": protected,
        },
        "required_rule_types": sorted(REQUIRED_RULE_TYPES),
        "active_rule_types": sorted(active_types),
        "missing_rule_types": missing_types,
        "required_status_check_contexts": status_contexts,
        "required_status_checks_present": required_status_checks_present,
        "release_main_ready": release_main_ready,
        "claim": (
            "RELEASE_MAIN_PROTECTION_READY"
            if release_main_ready
            else "RELEASE_MAIN_PROTECTION_INCOMPLETE"
        ),
        "next_authority": (
            "Configure active main-branch rules that require pull requests, required status checks, "
            "prevent force pushes, and prevent deletion; then rerun the zero-write authority check."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exact-head", required=True)
    parser.add_argument("--branch-json", type=Path, required=True)
    parser.add_argument("--rules-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(
        exact_head=args.exact_head,
        branch=_load_json(args.branch_json),
        rules=_load_json(args.rules_json),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.require_ready and not report["release_main_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
