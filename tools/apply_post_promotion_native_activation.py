#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one preimage, got {count}")
    return text.replace(old, new, 1)


def patch_native_product() -> None:
    path = "crates/syntavra-cli/src/native_product.rs"
    text = read(path)
    helper = (
        'fn bulk_parity_probe_enabled() -> bool {\n'
        '    std::env::var_os("SYNTAVRA_BULK_PARITY_PROBE").is_some_and(|value| value == "1")\n'
        '}\n\n'
    )
    text = replace_once(text, helper, "", "native_product bulk probe helper")

    guard = "bulk_parity_probe_enabled() && "
    guard_count = text.count(guard)
    if guard_count != 25:
        raise SystemExit(f"native_product activation guards: expected 25, got {guard_count}")
    text = text.replace(guard, "")
    if "bulk_parity_probe_enabled" in text or "SYNTAVRA_BULK_PARITY_PROBE" in text:
        raise SystemExit("probe-only production activation survived repair")
    write(path, text)


def patch_selector_status() -> None:
    path = "crates/syntavra-cli/src/bin/syntavra.rs"
    text = read(path)
    text = replace_once(
        text,
        "const NATIVE_COMMAND_COUNT: u64 = 170;",
        "const NATIVE_COMMAND_COUNT: u64 = 245;",
        "native engine status counter",
    )
    write(path, text)


def patch_ownership_probe() -> None:
    path = "crates/syntavra-cli/src/bin/syntavra-remaining71-ownership.rs"
    text = read(path)

    selector_start = text.index("fn selector_path(route: &str) -> Vec<String> {")
    selector_end = text.index("\nfn remaining71_owner_modules", selector_start)
    new_selector = (
        "fn selector_path(route: &str) -> Vec<String> {\n"
        "    let mut positional = route\n"
        "        .split_whitespace()\n"
        "        .map(str::to_owned)\n"
        "        .collect::<Vec<_>>();\n"
        "    if matches!(\n"
        "        positional.first().map(String::as_str),\n"
        "        Some(\"rollout-tail\" | \"context-stress\" | \"claim\" | \"context\" | \"init\" | \"hook\" | \"mcp\")\n"
        "    ) {\n"
        "        positional.truncate(1);\n"
        "    } else if positional.first().map(String::as_str) == Some(\"engine\")\n"
        "        && positional.get(1).map(String::as_str) == Some(\"route\")\n"
        "    {\n"
        "        positional.truncate(3);\n"
        "    } else {\n"
        "        positional.truncate(2);\n"
        "    }\n"
        "    positional\n"
        "}\n"
    )
    text = text[:selector_start] + new_selector + text[selector_end:]

    text = replace_once(
        text,
        '    std::env::set_var("SYNTAVRA_BULK_PARITY_PROBE", "1");\n\n',
        "",
        "ownership probe environment setter",
    )

    loop_start_marker = "    let routes = &report.remaining_routes;\n"
    promoted_marker = "    let promoted_set_equality ="
    if text.count(loop_start_marker) != 1 or text.count(promoted_marker) != 1:
        raise SystemExit("ownership loop boundary drift")
    start = text.index(loop_start_marker)
    promoted_start = text.index(promoted_marker, start)
    new_loops = (
        "    let selector_routes = match report.state {\n"
        "        InventoryState::Frozen => &report.remaining_routes,\n"
        "        InventoryState::Promoted => &report.public_routes,\n"
        "    };\n"
        "    let lower_module_routes = &report.remaining_routes;\n\n"
        "    let mut unowned = Vec::<String>::new();\n"
        "    let mut ownership = BTreeMap::<String, bool>::new();\n"
        "    let mut selector_paths = BTreeMap::<String, Vec<String>>::new();\n"
        "    let mut owner_modules = BTreeMap::<String, String>::new();\n"
        "    let mut owner_candidates = BTreeMap::<String, Vec<&'static str>>::new();\n"
        "    let mut duplicate_owner_routes = Vec::<String>::new();\n"
        "    let mut module_unowned_routes = Vec::<String>::new();\n\n"
        "    for route in selector_routes {\n"
        "        let path = selector_path(route);\n"
        "        let owned = native_product::supports(&path);\n"
        "        ownership.insert(route.clone(), owned);\n"
        "        selector_paths.insert(route.clone(), path);\n"
        "        if !owned {\n"
        "            unowned.push(route.clone());\n"
        "        }\n"
        "    }\n\n"
        "    for route in lower_module_routes {\n"
        "        let path = selector_path(route);\n"
        "        let candidates = remaining71_owner_modules(&path);\n"
        "        owner_candidates.insert(route.clone(), candidates.clone());\n"
        "        match candidates.as_slice() {\n"
        "            [owner] => {\n"
        "                owner_modules.insert(route.clone(), (*owner).to_owned());\n"
        "            }\n"
        "            [] => module_unowned_routes.push(route.clone()),\n"
        "            _ => duplicate_owner_routes.push(route.clone()),\n"
        "        }\n"
        "    }\n\n"
    )
    text = text[:start] + new_loops + text[promoted_start:]

    promoted_start = text.index(promoted_marker)
    ok_start = text.index("    let ok = match report.state", promoted_start)
    new_promoted = (
        "    let promoted_set_equality = report.state == InventoryState::Promoted\n"
        "        && report.native_count == EXPECTED_PUBLIC_ROUTE_COUNT\n"
        "        && report.public_routes.len() == EXPECTED_PUBLIC_ROUTE_COUNT as usize\n"
        "        && report.remaining_routes.is_empty()\n"
        "        && ownership.len() == EXPECTED_PUBLIC_ROUTE_COUNT as usize\n"
        "        && unowned.is_empty();\n"
    )
    text = text[:promoted_start] + new_promoted + text[ok_start:]

    text = replace_once(
        text,
        '"report_derived_remaining_count": routes.len(),',
        '"report_derived_remaining_count": report.remaining_routes.len(),',
        "ownership remaining count output",
    )
    text = replace_once(
        text,
        '"probe_environment": "SYNTAVRA_BULK_PARITY_PROBE=1",',
        '"probe_environment": "production selector; no activation probe flag",',
        "ownership probe environment output",
    )

    old_boundary = (
        "selector/lower-module ownership in the frozen state and exact public/native set equality "
        "in the promoted state; behavioral parity still requires differential execution"
    )
    new_boundary = (
        "production selector ownership for the canonical route set, plus lower-module ownership "
        "for frozen remaining routes; behavioral parity still requires differential execution"
    )
    boundary_count = text.count(old_boundary)
    if boundary_count != 2:
        raise SystemExit(f"ownership claim boundary: expected 2 preimages, got {boundary_count}")
    text = text.replace(old_boundary, new_boundary)

    provider_test = '        assert_eq!(selector_path("provider proxy"), vec!["provider", "proxy"]);\n'
    extra_tests = (
        provider_test
        + "        assert_eq!(\n"
        + '            selector_path("engine route config.show"),\n'
        + '            vec!["engine", "route", "config.show"]\n'
        + "        );\n"
        + '        assert_eq!(selector_path("context evaluate"), vec!["context"]);\n'
    )
    text = replace_once(text, provider_test, extra_tests, "ownership selector path tests")
    write(path, text)


def patch_transition_reporter() -> None:
    path = "tools/report_phase2_rust_migration_transition.py"
    text = read(path)
    text = replace_once(
        text,
        '        "owned_count": 0,',
        '        "owned_count": 245,',
        "transition promoted owned_count",
    )

    maps_start_marker = '    for key in ("selector_paths", "owner_modules", "owner_candidates"):\n'
    if text.count(maps_start_marker) != 1:
        raise SystemExit("transition promoted map validation drift")
    maps_start = text.index(maps_start_marker)
    build_start = text.index("\n\ndef _build_promoted", maps_start)
    new_map_validation = (
        '    selector_paths = ownership.get("selector_paths")\n'
        '    if not isinstance(selector_paths, dict) or len(selector_paths) != 245:\n'
        '        raise AssertionError("promoted ownership selector_paths must prove all 245 routes")\n'
        '    for key in ("owner_modules", "owner_candidates"):\n'
        '        if ownership.get(key) != {}:\n'
        '            raise AssertionError(f"promoted ownership {key} must be empty")\n'
    )
    text = text[:maps_start] + new_map_validation + text[build_start:]

    validation_anchor = "    _validate_promoted_ownership(ownership)\n\n"
    validation_insert = (
        validation_anchor
        + '    selector_paths = ownership["selector_paths"]\n'
        + '    if set(selector_paths) != canonical_routes:\n'
        + '        raise AssertionError("promoted production selector proof must cover the exact canonical route set")\n\n'
    )
    text = replace_once(
        text,
        validation_anchor,
        validation_insert,
        "transition selector ownership insertion",
    )

    replacements = [
        ('"rust_selector_components": route.split(),', '"rust_selector_components": selector_paths[route],', "transition selector components"),
        ('"rust_selector_resolution": "promoted-contract-route-identity",', '"rust_selector_resolution": "production-native-product-supports",', "transition selector resolution"),
        ('"rust_owner_boundary": "dual-engine-public-surface-v2.native_public_commands",', '"rust_owner_boundary": "native_product::supports",', "transition owner boundary"),
        ('"rust_owner_module": "promoted-native-contract",', '"rust_owner_module": "production-selector",', "transition owner module"),
        ('"promoted_set_equality": "tools/report_missing_native_public_routes.py + syntavra-remaining71-ownership",', '"promoted_set_equality": "canonical inventory + production native_product::supports audit",', "transition authority"),
    ]
    for old, new, label in replacements:
        text = replace_once(text, old, new, label)

    text = replace_once(
        text,
        '            "atomic_promotion_target": 245,\n',
        '            "atomic_promotion_target": 245,\n'
        '            "public_selector_owned": 245,\n'
        '            "public_selector_unowned": 0,\n',
        "transition promoted selector summary",
    )

    old_claim = (
        '            "This transition receipt proves the canonical 245-route Python identity is exactly the "\n'
        '            "promoted native set with zero bridge/missing/extra routes. Behavioral parity still "\n'
        '            "requires the final exact-head family differential gates."\n'
    )
    new_claim = (
        '            "This transition receipt proves the canonical 245-route Python identity is exactly the "\n'
        '            "promoted native set with zero bridge/missing/extra routes and that all 245 canonical "\n'
        '            "routes are owned by the production Rust selector without an activation probe flag. "\n'
        '            "Behavioral parity still requires the final exact-head family differential gates."\n'
    )
    text = replace_once(text, old_claim, new_claim, "transition claim boundary")
    write(path, text)


def patch_phase2_workflow() -> None:
    path = ".github/workflows/phase2-rust-migration-matrix.yml"
    text = read(path)
    old = "\n".join(
        [
            "              assert value['owned_count'] == 0",
            "              assert value['unowned_count'] == 0",
            "              assert value['selector_paths'] == {}",
            "              assert value['owner_module_count'] == 0",
        ]
    )
    new = "\n".join(
        [
            "              assert value['owned_count'] == 245",
            "              assert value['unowned_count'] == 0",
            "              assert len(value['selector_paths']) == 245",
            "              manifest_routes = {row['route'] for row in inventory['python']['manifest']}",
            "              assert set(value['selector_paths']) == manifest_routes",
            "              assert value['owner_module_count'] == 0",
        ]
    )
    text = replace_once(text, old, new, "Phase 2 promoted ownership assertions")
    write(path, text)


def patch_capability_workflow() -> None:
    path = ".github/workflows/remaining71-capability-differential.yml"
    text = read(path)
    old = "\n".join(
        [
            "          elif state == 'promoted-245-0':",
            "              assert ownership.get('native_route_count') == 245, ownership",
            "              assert ownership.get('report_derived_remaining_count') == 0, ownership",
            "              assert ownership.get('promoted_public_native_set_equality') is True, ownership",
            "              boundary = 'promoted-public-native-set-equality'",
        ]
    )
    new = "\n".join(
        [
            "          elif state == 'promoted-245-0':",
            "              assert ownership.get('native_route_count') == 245, ownership",
            "              assert ownership.get('report_derived_remaining_count') == 0, ownership",
            "              assert ownership.get('promoted_public_native_set_equality') is True, ownership",
            "              assert ownership.get('owned_count') == 245, ownership",
            "              assert len(ownership.get('selector_paths') or {}) == 245, ownership",
            "              boundary = 'promoted-production-selector-ownership'",
        ]
    )
    text = replace_once(text, old, new, "capability promoted ownership assertions")
    write(path, text)


def patch_transition_tests() -> None:
    path = "tests/runtime/test_phase2_rust_migration_transition.py"
    text = read(path)
    text = replace_once(
        text,
        '            "owned_count": 0,',
        '            "owned_count": 245,',
        "transition test owned count",
    )
    text = replace_once(
        text,
        '            "selector_paths": {},',
        '            "selector_paths": {f"run route-{index}": ["run", f"route-{index}"] for index in range(245)},',
        "transition test selector paths",
    )
    bad_anchor = '            ("native_route_count", 244),\n'
    text = replace_once(
        text,
        bad_anchor,
        bad_anchor + '            ("owned_count", 244),\n',
        "transition test bad owned count",
    )
    write(path, text)


def main() -> int:
    patch_native_product()
    patch_selector_status()
    patch_ownership_probe()
    patch_transition_reporter()
    patch_phase2_workflow()
    patch_capability_workflow()
    patch_transition_tests()
    print("post-promotion native activation patch applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
