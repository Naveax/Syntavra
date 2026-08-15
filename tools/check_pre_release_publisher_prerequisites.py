#!/usr/bin/env python3
"""Build a machine-readable, zero-write pre-release publisher prerequisite audit.

This checker intentionally verifies only prerequisites that can be observed from the
GitHub workflow without exposing credential values. External trusted-publisher
bindings (PyPI and VS Code Marketplace) stay explicitly unverified until separate
provider-side evidence is admitted.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
VERSION = "0.0.1"
CHANNEL = "pre-release"
ENVIRONMENT = "pre-release"
ARMING_VALUE = "PUBLISH_0_0_1_PRE_RELEASE"


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _load_environment(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("environment JSON must be an object")
    return value


def _environment_state(value: dict[str, Any]) -> tuple[bool, bool, int]:
    if value.get("missing") is True:
        return False, False, 0

    name = value.get("name")
    exists = name == ENVIRONMENT
    rules = value.get("protection_rules") or []
    if not isinstance(rules, list):
        rules = []

    reviewer_rules = [
        rule
        for rule in rules
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
    ]
    return exists, bool(reviewer_rules), len(reviewer_rules)


def build_report(
    *,
    exact_head: str,
    environment: dict[str, Any],
    arming_secret_present: bool,
    arming_secret_matches: bool,
    npm_token_present: bool,
    crates_token_present: bool,
) -> dict[str, Any]:
    if not HEX_SHA_RE.fullmatch(exact_head):
        raise ValueError("exact_head must be exactly 40 hexadecimal characters")

    exact_head = exact_head.lower()
    environment_exists, required_reviewers, reviewer_rule_count = _environment_state(environment)

    credential_checks = {
        "syntavra_publish_armed": {
            "present": arming_secret_present,
            "matches_required_arming_value": arming_secret_matches,
            "required_value_name": ARMING_VALUE,
            "value_exposed": False,
        },
        "npm_token": {
            "present": npm_token_present,
            "secret_name": "NPM_TOKEN",
            "value_exposed": False,
        },
        "crates_io_token": {
            "present": crates_token_present,
            "secret_name": "CRATES_IO_TOKEN",
            "value_exposed": False,
        },
    }

    github_checkable_ready = all(
        (
            environment_exists,
            required_reviewers,
            arming_secret_present,
            arming_secret_matches,
            npm_token_present,
            crates_token_present,
        )
    )

    external_bindings = {
        "pypi_trusted_publisher": {
            "status": "external_unverified",
            "registry": "PyPI",
            "package": "syntavra-runtime",
            "expected_repository": "Naveax/Syntavra",
            "expected_workflow": ".github/workflows/publish-pre-release.yml",
            "expected_environment": ENVIRONMENT,
            "reason": "Provider-side trusted-publisher binding is not observable from this GitHub repository audit without performing publication.",
        },
        "vscode_marketplace_trusted_publisher": {
            "status": "external_unverified",
            "registry": "VS Code Marketplace",
            "extension_id": "naveax.syntavra-vscode",
            "expected_repository": "Naveax/Syntavra",
            "expected_workflow": ".github/workflows/publish-pre-release.yml",
            "expected_environment": ENVIRONMENT,
            "reason": "Marketplace OIDC trusted-publisher binding is not observable from this GitHub repository audit without an external provider-side assertion.",
        },
    }

    external_bindings_verified = all(
        item["status"] == "verified" for item in external_bindings.values()
    )
    publication_ready = github_checkable_ready and external_bindings_verified

    if not github_checkable_ready:
        claim = "PUBLISHER_GITHUB_PREREQUISITES_INCOMPLETE"
    elif not external_bindings_verified:
        claim = "PUBLISHER_EXTERNAL_BINDINGS_UNVERIFIED"
    else:
        claim = "PUBLISHER_PREREQUISITES_READY"

    return {
        "schema_version": 1,
        "product": "Syntavra",
        "version": VERSION,
        "channel": CHANNEL,
        "exact_head": exact_head,
        "audit_mode": "zero-write",
        "publication_performed": False,
        "secret_values_exposed": False,
        "claim": claim,
        "github_environment": {
            "name": ENVIRONMENT,
            "exists": environment_exists,
            "required_reviewers": required_reviewers,
            "required_reviewer_rule_count": reviewer_rule_count,
        },
        "credentials": credential_checks,
        "github_checkable_ready": github_checkable_ready,
        "external_bindings": external_bindings,
        "external_bindings_verified": external_bindings_verified,
        "publication_ready": publication_ready,
        "next_authority": (
            "Create and protect the pre-release GitHub environment, configure required repository secrets, "
            "then independently establish and verify provider-side OIDC/trusted-publisher bindings before requesting publish mode."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exact-head", required=True)
    parser.add_argument("--environment-json", type=Path, required=True)
    parser.add_argument("--arming-secret-present", type=_bool, required=True)
    parser.add_argument("--arming-secret-matches", type=_bool, required=True)
    parser.add_argument("--npm-token-present", type=_bool, required=True)
    parser.add_argument("--crates-token-present", type=_bool, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-github-ready", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(
        exact_head=args.exact_head,
        environment=_load_environment(args.environment_json),
        arming_secret_present=args.arming_secret_present,
        arming_secret_matches=args.arming_secret_matches,
        npm_token_present=args.npm_token_present,
        crates_token_present=args.crates_token_present,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))

    if args.require_github_ready and not report["github_checkable_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
