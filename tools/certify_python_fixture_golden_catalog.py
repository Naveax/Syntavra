#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import traceback
from pathlib import Path
from typing import Any

from syntavra_runtime.util import canonical_json
from tools import report_missing_native_public_routes as public_surface

CATALOG_RELATIVE = Path("contracts/python/fixture-golden-catalog-v1.json")
EXPECTED_FAMILIES = [
    ("D", "agent"),
    ("E", "headless"),
    ("F", "graph-language-semantic"),
    ("G", "memory-intelligence"),
    ("H", "capability-inventory"),
    ("I", "provider-proxy"),
    ("J", "sandbox-security"),
    ("K", "platform-helper-evidence"),
    ("L", "benchmark-proof"),
    ("M", "context-compaction"),
    ("N", "setup-host"),
    ("O", "mcp-integration"),
    ("P", "publication-registry"),
    ("T", "core-legacy-route-reference"),
]


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _assert_no_full_route_copy(value: Any) -> None:
    if isinstance(value, list):
        if len(value) == 245 and all(isinstance(item, str) for item in value):
            raise AssertionError("fixture catalog must not duplicate the canonical 245-route list")
        for item in value:
            _assert_no_full_route_copy(item)
    elif isinstance(value, dict):
        for item in value.values():
            _assert_no_full_route_copy(item)


def certify(repo: Path) -> dict[str, Any]:
    catalog_path = repo / CATALOG_RELATIVE
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    if catalog.get("schema_version") != 1 or catalog.get("family") != "fixture-golden-catalog":
        raise AssertionError("fixture catalog identity drift")
    if catalog.get("phase_scope") != "D-P,T":
        raise AssertionError("fixture catalog phase scope drift")
    if catalog.get("family_count") != len(EXPECTED_FAMILIES):
        raise AssertionError("fixture catalog family count drift")

    routes = sorted(public_surface.python_public_route_sources())
    if len(routes) != 245:
        raise AssertionError(f"canonical public route count drift: {len(routes)} != 245")
    _assert_no_full_route_copy(catalog)

    policy = catalog.get("policy") or {}
    if policy.get("missing_forbidden") is not True:
        raise AssertionError("fixture catalog must fail closed on missing fixture coverage")
    if policy.get("nondeterminism_explicit") is not True:
        raise AssertionError("fixture catalog must require explicit nondeterminism")
    if policy.get("rust_python_required") is not False:
        raise AssertionError("Rust fixture consumption must not require Python")
    if policy.get("route_authority") != "tools/report_missing_native_public_routes.py":
        raise AssertionError("fixture catalog route authority drift")
    if policy.get("duplicate_245_route_list") is not False:
        raise AssertionError("fixture catalog must not become a second 245-route authority")

    required = list(catalog.get("required_fixture_kinds") or [])
    if required != ["happy_path", "empty_state", "malformed_input", "domain_error", "side_effect"]:
        raise AssertionError(f"fixture kind vocabulary drift: {required}")

    families = list(catalog.get("families") or [])
    observed_families = [(str(row.get("section")), str(row.get("family"))) for row in families]
    if observed_families != EXPECTED_FAMILIES:
        raise AssertionError(f"fixture family ordering/identity drift: {observed_families}")

    counts = {"covered": 0, "not_applicable": 0, "missing": 0}
    contract_count = 0
    inline_only_count = 0
    family_summaries: list[dict[str, Any]] = []

    for index, row in enumerate(families):
        family = str(row["family"])
        certifier_path = repo / str(row.get("certifier") or "")
        if not certifier_path.is_file():
            raise AssertionError(f"missing family certifier for {family}: {certifier_path}")

        contract = row.get("contract")
        if contract is None:
            inline_only_count += 1
        else:
            contract_path = repo / str(contract)
            if not contract_path.is_file():
                raise AssertionError(f"missing family contract for {family}: {contract_path}")
            json.loads(contract_path.read_text(encoding="utf-8"))
            contract_count += 1

        fixtures = row.get("fixtures")
        if not isinstance(fixtures, dict) or list(fixtures) != required:
            raise AssertionError(f"fixture kind coverage drift for {family}: {list(fixtures or {})}")

        family_counts = {"covered": 0, "not_applicable": 0}
        for kind in required:
            fixture = fixtures[kind]
            if not isinstance(fixture, list) or not fixture:
                raise AssertionError(f"invalid fixture encoding for {family}/{kind}")
            status = fixture[0]
            if status == "covered":
                if len(fixture) != 3 or not isinstance(fixture[1], str) or not fixture[1].strip():
                    raise AssertionError(f"covered fixture requires scenario for {family}/{kind}")
                if not isinstance(fixture[2], dict):
                    raise AssertionError(f"covered fixture requires static expected projection for {family}/{kind}")
                counts["covered"] += 1
                family_counts["covered"] += 1
            elif status == "not-applicable":
                if len(fixture) != 2 or not isinstance(fixture[1], str) or not fixture[1].strip():
                    raise AssertionError(f"not-applicable fixture requires reason for {family}/{kind}")
                counts["not_applicable"] += 1
                family_counts["not_applicable"] += 1
            elif status == "missing":
                counts["missing"] += 1
                raise AssertionError(f"missing fixture coverage is forbidden: {family}/{kind}")
            else:
                raise AssertionError(f"unknown fixture status for {family}/{kind}: {status!r}")

        snapshot = row.get("snapshot") or {}
        projection = snapshot.get("projection")
        expected_sha = str(snapshot.get("sha256") or "")
        if not isinstance(projection, dict) or len(expected_sha) != 64:
            raise AssertionError(f"deterministic snapshot shape drift for {family}")
        observed_sha = hashlib.sha256(canonical_json(projection)).hexdigest()
        if observed_sha != expected_sha:
            raise AssertionError(
                f"deterministic snapshot drift for {family}: {observed_sha} != {expected_sha}"
            )

        nondeterministic = row.get("nondeterministic_fields")
        if not isinstance(nondeterministic, list):
            raise AssertionError(f"nondeterministic field list missing for {family}")
        if any(not isinstance(item, str) or not item.strip() for item in nondeterministic):
            raise AssertionError(f"invalid nondeterministic field entry for {family}")
        if len(nondeterministic) != len(set(nondeterministic)):
            raise AssertionError(f"duplicate nondeterministic field entry for {family}")

        family_summaries.append(
            {
                "section": row["section"],
                "family": family,
                "covered": family_counts["covered"],
                "not_applicable": family_counts["not_applicable"],
                "snapshot_sha256": observed_sha,
                "nondeterministic_field_count": len(nondeterministic),
                "static_contract": contract,
                "catalog_pointer": f"/families/{index}",
                "python_required_by_rust": False,
            }
        )

    if counts != {"covered": 64, "not_applicable": 6, "missing": 0}:
        raise AssertionError(f"fixture coverage count drift: {counts}")
    if contract_count != 10 or inline_only_count != 4:
        raise AssertionError(
            f"fixture source layout drift: contracts={contract_count}, inline={inline_only_count}"
        )

    return {
        "ok": True,
        "schema_version": 1,
        "family": "fixture-golden-catalog",
        "engine": "python-reference-metadata",
        "exact_head": _head(repo),
        "catalog": str(CATALOG_RELATIVE).replace("\\", "/"),
        "catalog_file_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "catalog_semantic_sha256": hashlib.sha256(canonical_json(catalog)).hexdigest(),
        "canonical_public_route_count": len(routes),
        "canonical_public_route_sha256": public_surface._digest(routes),
        "family_count": len(families),
        "fixture_case_count": sum(counts.values()),
        "coverage": counts,
        "deterministic_snapshot_count": len(families),
        "static_contract_count": contract_count,
        "inline_static_family_count": inline_only_count,
        "rust_python_required": False,
        "family_summaries": family_summaries,
        "nondeterministic_policy": "only fields explicitly listed per family may be normalized by later Rust differential tests",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python Phase 1 fixture/golden catalog integrity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "fixture-golden-catalog",
            "engine": "python-reference-metadata",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
