#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

CONTRACT_RELATIVE = Path("contracts/engine/phase2-rust-migration-matrix-v1.json")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


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


def _route_digest(routes: list[str]) -> str:
    payload = json.dumps(
        sorted(set(routes)),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _assert_no_route_identity_copy(value: Any) -> None:
    if isinstance(value, list):
        if len(value) in {71, 245} and all(isinstance(item, str) for item in value):
            raise AssertionError(
                f"Phase 2 matrix contract must not duplicate a {len(value)}-route identity list"
            )
        for item in value:
            _assert_no_route_identity_copy(item)
    elif isinstance(value, dict):
        for item in value.values():
            _assert_no_route_identity_copy(item)


def _program_index(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = list(contract.get("family_programs") or [])
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AssertionError(f"invalid family program row: {row!r}")
        family = str(row.get("family") or "")
        if not family or family in result:
            raise AssertionError(f"invalid/duplicate family program: {family!r}")
        result[family] = row
    return result


def _load_contract(repo: Path) -> tuple[dict[str, Any], str]:
    path = repo / CONTRACT_RELATIVE
    raw = path.read_bytes()
    contract = json.loads(raw)
    if not isinstance(contract, dict):
        raise AssertionError("Phase 2 migration matrix contract must be a JSON object")
    if contract.get("schema_version") != 1:
        raise AssertionError("Phase 2 migration matrix schema drift")
    if contract.get("family") != "phase2-rust-migration-matrix":
        raise AssertionError("Phase 2 migration matrix family drift")
    if contract.get("phase") != "A-B":
        raise AssertionError("Phase 2 migration matrix phase drift")
    if contract.get("strict") is not True:
        raise AssertionError("Phase 2 migration matrix contract must be strict")
    _assert_no_route_identity_copy(contract)
    programs = _program_index(contract)
    if len(programs) != 14:
        raise AssertionError(f"expected 14 Python behavior family programs, got {len(programs)}")
    return contract, hashlib.sha256(raw).hexdigest()


def _validate_phase1(
    phase1: dict[str, Any],
    contract: dict[str, Any],
    exact_head: str,
) -> dict[str, dict[str, Any]]:
    cfg = contract["python_reference"]
    if phase1.get("ok") is not True:
        raise AssertionError("Python Phase 1 acceptance is not green")
    if phase1.get("claim") != cfg["expected_claim"]:
        raise AssertionError("Python Phase 1 acceptance claim drift")
    if phase1.get("exact_head") != exact_head:
        raise AssertionError(
            f"Python Phase 1 acceptance exact-head drift: {phase1.get('exact_head')} != {exact_head}"
        )
    rows = list(phase1.get("route_semantics") or [])
    expected = int(cfg["expected_public_route_count"])
    if len(rows) != expected:
        raise AssertionError(f"Python Phase 1 route semantics count drift: {len(rows)} != {expected}")
    by_route: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AssertionError("Python Phase 1 route semantics contains non-object row")
        route = str(row.get("route") or "")
        if not route or route in by_route:
            raise AssertionError(f"invalid/duplicate Phase 1 route row: {route!r}")
        by_route[route] = row
    digest = _route_digest(list(by_route))
    if digest != cfg["expected_route_sha256"]:
        raise AssertionError(f"Python Phase 1 route digest drift: {digest}")
    return by_route


def _validate_inventory(
    inventory: dict[str, Any],
    contract: dict[str, Any],
    canonical_routes: set[str],
) -> tuple[set[str], set[str]]:
    cfg = contract["rust_baseline"]
    if inventory.get("ok") is not True:
        raise AssertionError("canonical public/native inventory report is red")
    python = inventory.get("python") or {}
    rust = inventory.get("rust") or {}
    if int(python.get("derived_count", -1)) != len(canonical_routes):
        raise AssertionError("inventory Python route count drift")
    if str(python.get("derived_sha256")) != contract["python_reference"]["expected_route_sha256"]:
        raise AssertionError("inventory Python route digest drift")
    if int(rust.get("native_count", -1)) != int(cfg["expected_promoted_native"]):
        raise AssertionError("inventory promoted native count drift")
    if int(rust.get("missing_count", -1)) != int(cfg["expected_remaining"]):
        raise AssertionError("inventory remaining route count drift")
    missing_rows = list(inventory.get("missing_routes") or [])
    if any(not isinstance(route, str) or not route for route in missing_rows):
        raise AssertionError("inventory missing_routes contains invalid route")
    missing = set(missing_rows)
    if len(missing) != len(missing_rows):
        raise AssertionError("inventory missing_routes contains duplicates")
    promoted = set(canonical_routes) - missing
    if len(promoted) != int(cfg["expected_promoted_native"]):
        raise AssertionError("derived promoted route count drift")
    if missing - canonical_routes:
        raise AssertionError("inventory contains non-canonical missing routes")
    return promoted, missing


def _validate_ownership(
    ownership: dict[str, Any],
    contract: dict[str, Any],
    missing: set[str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    cfg = contract["rust_baseline"]
    if ownership.get("ok") is not True:
        raise AssertionError("Remaining-71 Rust ownership probe is red")
    if int(ownership.get("public_route_count", -1)) != int(
        contract["python_reference"]["expected_public_route_count"]
    ):
        raise AssertionError("ownership public route count drift")
    if int(ownership.get("frozen_native_route_count", -1)) != int(cfg["expected_promoted_native"]):
        raise AssertionError("ownership frozen native count drift")
    if int(ownership.get("report_derived_remaining_count", -1)) != int(cfg["expected_remaining"]):
        raise AssertionError("ownership remaining count drift")
    if int(ownership.get("owned_count", -1)) != int(cfg["expected_remaining_owned"]):
        raise AssertionError("ownership selector-owned count drift")
    if int(ownership.get("unowned_count", -1)) != int(cfg["expected_unowned"]):
        raise AssertionError("ownership selector-unowned count drift")
    if int(ownership.get("owner_module_count", -1)) != int(cfg["expected_remaining_owned"]):
        raise AssertionError("ownership lower-module owner count drift")
    if int(ownership.get("module_unowned_count", -1)) != 0:
        raise AssertionError(f"Remaining-71 lower-module unowned routes: {ownership.get('module_unowned_routes')}")
    if int(ownership.get("duplicate_owner_count", -1)) != 0:
        raise AssertionError(f"Remaining-71 duplicate lower-module owners: {ownership.get('duplicate_owner_routes')}")

    selectors_raw = ownership.get("selector_paths") or {}
    modules_raw = ownership.get("owner_modules") or {}
    candidates_raw = ownership.get("owner_candidates") or {}
    if not isinstance(selectors_raw, dict):
        raise AssertionError("ownership selector_paths is not an object")
    if not isinstance(modules_raw, dict):
        raise AssertionError("ownership owner_modules is not an object")
    if not isinstance(candidates_raw, dict):
        raise AssertionError("ownership owner_candidates is not an object")

    expected_keys = set(missing)
    for name, mapping in [
        ("selector_paths", selectors_raw),
        ("owner_modules", modules_raw),
        ("owner_candidates", candidates_raw),
    ]:
        keys = set(mapping)
        missing_keys = sorted(expected_keys - keys)
        stale_keys = sorted(keys - expected_keys)
        if missing_keys:
            raise AssertionError(f"Remaining-71 {name} missing routes: {missing_keys}")
        if stale_keys:
            raise AssertionError(f"stale Remaining-71 {name} routes: {stale_keys}")

    selectors: dict[str, list[str]] = {}
    modules: dict[str, str] = {}
    for route in sorted(missing):
        path = selectors_raw.get(route)
        if not isinstance(path, list) or not path or any(not isinstance(item, str) or not item for item in path):
            raise AssertionError(f"invalid Rust selector path for {route}: {path!r}")
        module = modules_raw.get(route)
        if not isinstance(module, str) or not module.startswith("native_remaining71_"):
            raise AssertionError(f"invalid Rust lower-module owner for {route}: {module!r}")
        candidates = candidates_raw.get(route)
        if candidates != [module]:
            raise AssertionError(
                f"Rust lower-module owner/candidate mismatch for {route}: owner={module!r} candidates={candidates!r}"
            )
        selectors[route] = list(path)
        modules[route] = module
    return selectors, modules


def build_matrix(
    repo: Path,
    *,
    phase1: dict[str, Any],
    inventory: dict[str, Any],
    ownership: dict[str, Any],
) -> dict[str, Any]:
    contract, contract_sha = _load_contract(repo)
    exact_head = _head(repo)
    if not exact_head:
        raise AssertionError("unable to resolve exact git HEAD")

    phase1_rows = _validate_phase1(phase1, contract, exact_head)
    canonical_routes = set(phase1_rows)
    promoted, missing = _validate_inventory(inventory, contract, canonical_routes)
    selectors, modules = _validate_ownership(ownership, contract, missing)
    programs = _program_index(contract)

    family_names = {str(row.get("family") or "") for row in phase1_rows.values()}
    unknown_program_families = sorted(family_names - set(programs))
    if unknown_program_families:
        raise AssertionError(f"Phase 1 route families lack Phase 2 program mapping: {unknown_program_families}")

    rows: list[dict[str, Any]] = []
    remaining_by_family: Counter[str] = Counter()
    remaining_by_rust_module: Counter[str] = Counter()
    for route in sorted(canonical_routes):
        python_row = phase1_rows[route]
        family = str(python_row["family"])
        program = programs[family]
        is_promoted = route in promoted
        if is_promoted:
            selector = route
            selector_components = route.split()
            selector_state = "promoted-contract-route-identity"
            owner_boundary = "dual-engine-public-surface-v2.native_public_commands"
            rust_owner_module = "frozen-native-contract"
            promotion_state = "promoted-baseline"
            certification_state = "baseline-promoted-not-part-of-remaining71-recertification"
        else:
            selector_components = selectors[route]
            selector = " ".join(selector_components)
            selector_state = "remaining71-selector-and-lower-module-owned"
            rust_owner_module = modules[route]
            owner_boundary = f"native_product::bulk-parity-probe::{rust_owner_module}"
            promotion_state = "blocked-until-atomic-71-route-promotion"
            certification_state = str(program["state"])
            remaining_by_family[family] += 1
            remaining_by_rust_module[rust_owner_module] += 1

        rows.append(
            {
                "route": route,
                "python_family": family,
                "python_entrypoint": python_row.get("entrypoint"),
                "python_sources": python_row.get("sources"),
                "rust_selector": selector,
                "rust_selector_components": selector_components,
                "rust_selector_resolution": selector_state,
                "rust_owner_boundary": owner_boundary,
                "rust_owner_module": rust_owner_module,
                "rust_selector_owned": True,
                "rust_owner_module_owned": True,
                "production_native": is_promoted,
                "promotion_state": promotion_state,
                "differential_validator": program.get("validator"),
                "differential_workflow": program.get("workflow"),
                "certification_state": certification_state,
            }
        )

    duplicate_routes = len(rows) - len({row["route"] for row in rows})
    if duplicate_routes:
        raise AssertionError("Phase 2 migration matrix contains duplicate routes")

    pending_remaining = sum(
        1
        for row in rows
        if not row["production_native"]
        and row["certification_state"] in {"pending-family-differential", "partial-existing-validator"}
    )
    existing_recertification_remaining = sum(
        1
        for row in rows
        if not row["production_native"]
        and row["certification_state"] == "existing-needs-final-head-recertification"
    )

    summary = {
        "public_routes": len(rows),
        "promoted_native": len(promoted),
        "remaining": len(missing),
        "remaining_selector_owned": len(selectors),
        "remaining_owner_module_owned": len(modules),
        "remaining_unowned": 0,
        "duplicate_routes": duplicate_routes,
        "duplicate_lower_owner_routes": int(ownership.get("duplicate_owner_count", -1)),
        "stale_remaining_selector_routes": 0,
        "missing_remaining_selector_routes": 0,
        "remaining_owner_module_unresolved": len(missing) - len(modules),
        "remaining_by_python_family": dict(sorted(remaining_by_family.items())),
        "remaining_by_rust_module": dict(sorted(remaining_by_rust_module.items())),
        "existing_family_gate_recertification_routes": existing_recertification_remaining,
        "pending_or_partial_family_gate_routes": pending_remaining,
        "atomic_promotion_target": int(contract["rust_baseline"]["atomic_promotion_target"]),
    }

    return {
        "ok": True,
        "schema_version": 1,
        "family": "phase2-rust-migration-matrix",
        "phase": "A-B",
        "claim": contract["claim"],
        "exact_head": exact_head,
        "contract": str(CONTRACT_RELATIVE).replace("\\", "/"),
        "contract_sha256": contract_sha,
        "python_reference": {
            "claim": phase1["claim"],
            "route_count": len(phase1_rows),
            "route_sha256": _route_digest(list(phase1_rows)),
            "accepted_phase1_head": contract["python_reference"]["phase1_accepted_head"],
            "current_exact_head_recertified": phase1["exact_head"],
        },
        "authority": {
            "route_identity": contract["rust_baseline"]["remaining_authority"],
            "remaining_selector_ownership": contract["rust_baseline"]["ownership_probe_binary"],
            "lower_rust_module_ownership": "Remaining-71 Rust modules' supports() predicates",
            "hardcoded_remaining_route_list": False,
        },
        "family_programs": [programs[key] for key in sorted(programs)],
        "summary": summary,
        "matrix": rows,
        "claim_boundary": (
            "This gate establishes frozen Python route/family metadata plus exact Remaining-71 "
            "selector and lower Rust module ownership. Behavioral parity and final exact-head "
            "family certification remain mandatory before atomic promotion."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Phase 2 Rust migration matrix from frozen authorities")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--phase1-acceptance", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ownership", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)

    try:
        result = build_matrix(
            repo,
            phase1=_read_json(Path(args.phase1_acceptance)),
            inventory=_read_json(Path(args.inventory)),
            ownership=_read_json(Path(args.ownership)),
        )
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "phase2-rust-migration-matrix",
            "phase": "A-B",
            "exact_head": _head(repo),
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
