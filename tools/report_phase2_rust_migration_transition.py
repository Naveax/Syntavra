#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import traceback
from pathlib import Path
from typing import Any

from tools import report_phase2_rust_migration_matrix as baseline

FROZEN_STATE = (245, 174, 71)
PROMOTED_STATE = (245, 245, 0)
COMPLETE_CLAIM = "PHASE2_RUST_MIGRATION_COMPLETE"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _inventory_state(inventory: dict[str, Any]) -> str:
    if inventory.get("ok") is not True:
        raise AssertionError("canonical public/native inventory report is red")
    python = inventory.get("python") or {}
    rust = inventory.get("rust") or {}
    state = (
        int(python.get("derived_count", -1)),
        int(rust.get("native_count", -1)),
        int(rust.get("missing_count", -1)),
    )
    if state == FROZEN_STATE:
        return "frozen-174-71"
    if state == PROMOTED_STATE:
        return "promoted-245-0"
    raise AssertionError(
        "Phase 2 migration inventory must remain atomic: "
        f"public/native/remaining={state}; accepted={FROZEN_STATE} or {PROMOTED_STATE}"
    )


def _validate_promoted_ownership(ownership: dict[str, Any]) -> None:
    if ownership.get("ok") is not True:
        raise AssertionError("promoted ownership probe is red")
    expected = {
        "inventory_state": "promoted-245-0",
        "public_route_count": 245,
        "native_route_count": 245,
        "report_derived_remaining_count": 0,
        "owned_count": 245,
        "unowned_count": 0,
        "owner_module_count": 0,
        "module_unowned_count": 0,
        "duplicate_owner_count": 0,
        "promoted_public_native_set_equality": True,
    }
    for key, value in expected.items():
        if ownership.get(key) != value:
            raise AssertionError(
                f"promoted ownership drift for {key}: expected {value!r}, got {ownership.get(key)!r}"
            )
    for key in (
        "unowned_routes",
        "module_unowned_routes",
        "duplicate_owner_routes",
    ):
        if ownership.get(key) != []:
            raise AssertionError(f"promoted ownership {key} must be empty")
    selector_paths = ownership.get("selector_paths")
    if not isinstance(selector_paths, dict) or len(selector_paths) != 245:
        raise AssertionError("promoted ownership selector_paths must prove all 245 routes")
    for key in ("owner_modules", "owner_candidates"):
        if ownership.get(key) != {}:
            raise AssertionError(f"promoted ownership {key} must be empty")


def _build_promoted(
    repo: Path,
    *,
    phase1: dict[str, Any],
    inventory: dict[str, Any],
    ownership: dict[str, Any],
) -> dict[str, Any]:
    contract, contract_sha = baseline._load_contract(repo)
    exact_head = baseline._head(repo)
    if not exact_head:
        raise AssertionError("unable to resolve exact git HEAD")
    phase1_rows = baseline._validate_phase1(phase1, contract, exact_head)
    canonical_routes = set(phase1_rows)
    if len(canonical_routes) != 245:
        raise AssertionError("promoted Phase 2 canonical route count drift")

    python = inventory.get("python") or {}
    rust = inventory.get("rust") or {}
    if str(python.get("derived_sha256")) != contract["python_reference"]["expected_route_sha256"]:
        raise AssertionError("promoted inventory Python route digest drift")
    if inventory.get("missing_routes") != []:
        raise AssertionError("promoted inventory must have zero missing routes")
    if rust.get("extra_native_routes") != []:
        raise AssertionError("promoted inventory must have zero extra native routes")
    _validate_promoted_ownership(ownership)

    selector_paths = ownership["selector_paths"]
    if set(selector_paths) != canonical_routes:
        raise AssertionError("promoted production selector proof must cover the exact canonical route set")

    manifest = list(python.get("manifest") or [])
    manifest_routes = [row.get("route") for row in manifest if isinstance(row, dict)]
    if len(manifest_routes) != 245 or set(manifest_routes) != canonical_routes:
        raise AssertionError("promoted inventory manifest must exactly equal Phase 1 route identity")

    rows: list[dict[str, Any]] = []
    for route in sorted(canonical_routes):
        python_row = phase1_rows[route]
        rows.append(
            {
                "route": route,
                "python_family": python_row.get("family"),
                "python_entrypoint": python_row.get("entrypoint"),
                "python_sources": python_row.get("sources"),
                "rust_selector": route,
                "rust_selector_components": selector_paths[route],
                "rust_selector_resolution": "production-selector-owned",
                "rust_owner_boundary": "production-selector",
                "rust_owner_module": "production-selector",
                "rust_selector_owned": True,
                "rust_owner_module_owned": True,
                "production_native": True,
                "promotion_state": "atomic-245-route-promotion-complete",
                "certification_state": "final-exact-head-family-gates-required",
            }
        )

    return {
        "ok": True,
        "schema_version": 2,
        "family": "phase2-rust-migration-transition",
        "phase": "complete",
        "claim": COMPLETE_CLAIM,
        "inventory_state": "promoted-245-0",
        "exact_head": exact_head,
        "baseline_contract": str(baseline.CONTRACT_RELATIVE).replace("\\", "/"),
        "baseline_contract_sha256": contract_sha,
        "python_reference": {
            "claim": phase1["claim"],
            "route_count": len(phase1_rows),
            "route_sha256": baseline._route_digest(list(phase1_rows)),
            "current_exact_head_recertified": phase1["exact_head"],
        },
        "authority": {
            "route_identity": contract["rust_baseline"]["remaining_authority"],
            "promoted_set_equality": "canonical inventory + production selector ownership audit",
            "hardcoded_remaining_route_list": False,
        },
        "summary": {
            "public_routes": 245,
            "promoted_native": 245,
            "remaining": 0,
            "remaining_selector_owned": 0,
            "remaining_owner_module_owned": 0,
            "remaining_unowned": 0,
            "duplicate_routes": 0,
            "duplicate_lower_owner_routes": 0,
            "stale_remaining_selector_routes": 0,
            "missing_remaining_selector_routes": 0,
            "remaining_owner_module_unresolved": 0,
            "atomic_promotion_target": 245,
            "public_selector_owned": 245,
            "public_selector_unowned": 0,
        },
        "matrix": rows,
        "claim_boundary": (
            "This transition receipt proves the canonical 245-route Python identity is exactly the "
            "promoted native set with zero bridge/missing/extra routes and that all 245 canonical "
            "routes are owned by the production Rust selector without an activation probe flag. "
            "Behavioral parity still requires the final exact-head family differential gates."
        ),
    }


def build_transition(
    repo: Path,
    *,
    phase1: dict[str, Any],
    inventory: dict[str, Any],
    ownership: dict[str, Any],
) -> dict[str, Any]:
    state = _inventory_state(inventory)
    if state == "frozen-174-71":
        compatibility_ownership = copy.deepcopy(ownership)
        compatibility_ownership.setdefault(
            "frozen_native_route_count",
            compatibility_ownership.get("native_route_count"),
        )
        result = baseline.build_matrix(
            repo,
            phase1=phase1,
            inventory=inventory,
            ownership=compatibility_ownership,
        )
        result["inventory_state"] = state
        result["transition_reporter"] = True
        return result
    return _build_promoted(
        repo,
        phase1=phase1,
        inventory=inventory,
        ownership=ownership,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a Phase 2 migration receipt for either frozen 174/71 or promoted 245/0 state."
    )
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--phase1-acceptance", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ownership", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)

    try:
        result = build_transition(
            repo,
            phase1=_read_json(Path(args.phase1_acceptance)),
            inventory=_read_json(Path(args.inventory)),
            ownership=_read_json(Path(args.ownership)),
        )
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 2,
            "family": "phase2-rust-migration-transition",
            "exact_head": baseline._head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
