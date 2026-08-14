#!/usr/bin/env python3
from __future__ import annotations

import re
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


def replace_n(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} preimages, got {count}")
    return text.replace(old, new)


def sub_n(text: str, pattern: str, replacement: str, expected: int, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, flags=re.MULTILINE | re.DOTALL)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} regex matches, got {count}")
    return updated


def patch_native_product() -> None:
    path = "crates/syntavra-cli/src/native_product.rs"
    text = read(path)
    helper = (
        'fn bulk_parity_probe_enabled() -> bool {\n'
        '    std::env::var_os("SYNTAVRA_BULK_PARITY_PROBE").is_some_and(|value| value == "1")\n'
        '}\n\n'
    )
    text = replace_once(text, helper, "", "native_product bulk probe helper")

    support_pattern = r"\(bulk_parity_probe_enabled\(\) && (native_remaining71_[a-z_]+::supports\(command\))\)"
    updated, count = re.subn(support_pattern, r"\1", text)
    if count != 12:
        raise SystemExit(f"native_product supports guards: expected 12, got {count}")
    text = updated

    text = replace_once(
        text,
        "if bulk_parity_probe_enabled() && cc_checkpoint_supports(command) {",
        "if cc_checkpoint_supports(command) {",
        "native_product checkpoint activation guard",
    )

    execute_pattern = r"if bulk_parity_probe_enabled\(\) && (native_remaining71_[a-z_]+::supports\(command\))"
    updated, count = re.subn(execute_pattern, r"if \1", text)
    if count != 12:
        raise SystemExit(f"native_product execute guards: expected 12, got {count}")
    text = updated

    if "bulk_parity_probe_enabled" in text or "SYNTAVRA_BULK_PARITY_PROBE" in text:
        raise SystemExit("native_product activation guard survived production promotion")
    write(path, text)


def patch_selector_status() -> None:
    path = "crates/syntavra-cli/src/bin/syntavra.rs"
    text = read(path)
    text = replace_once(
        text,
        "const NATIVE_COMMAND_COUNT: u64 = 170;",
        "const NATIVE_COMMAND_COUNT: u64 = 245;",
        "native selector status counter",
    )
    write(path, text)


def patch_ownership_probe() -> None:
    path = "crates/syntavra-cli/src/bin/syntavra-remaining71-ownership.rs"
    text = read(path)

    selector_pattern = r"fn selector_path\(route: &str\) -> Vec<String> \{.*?\n\}\n\n(?=fn remaining71_owner_modules)"
    selector_replacement = (
        "fn selector_path(route: &str) -> Vec<String> {\n"
        "    // Mirror syntavra.rs command_path() for canonical route identities: engine\n"
        "    // route dispatch keeps its third route key, while other families select at\n"
        "    // one or two components and consume deeper actions inside the native owner.\n"
        "    let mut components = route\n"
        "        .split_whitespace()\n"
        "        .map(str::to_owned)\n"
        "        .collect::<Vec<_>>();\n"
        "    let limit = if components.first().map(String::as_str) == Some(\"engine\")\n"
        "        && components.get(1).map(String::as_str) == Some(\"route\")\n"
        "    {\n"
        "        3\n"
        "    } else {\n"
        "        2\n"
        "    };\n"
        "    components.truncate(limit);\n"
        "    components\n"
        "}\n\n"
    )
    text = sub_n(text, selector_pattern, selector_replacement, 1, "ownership selector_path")

    text = replace_once(
        text,
        '    std::env::set_var("SYNTAVRA_BULK_PARITY_PROBE", "1");\n\n',
        "",
        "ownership probe environment setter",
    )

    loop_start_marker = "    let routes = &report.remaining_routes;\n"
    loop_end_marker = "    let promoted_set_equality ="
    if text.count(loop_start_marker) != 1 or text.count(loop_end_marker) != 1:
        raise SystemExit("ownership loop boundary drift")
    start = text.index(loop_start_marker)
    end = text.index(loop_end_marker, start)
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
    text = text[:start] + new_loops + text[end:]

    promoted_pattern = (
        r"    let promoted_set_equality = report\.state == InventoryState::Promoted\n"
        r"\s+&& report\.native_count == EXPECTED_PUBLIC_ROUTE_COUNT\n"
        r"\s+&& report\.public_routes\.len\(\) == EXPECTED_PUBLIC_ROUTE_COUNT as usize\n"
        r"\s+&& routes\.is_empty\(\);\n"
    )
    promoted_replacement = (
        "    let promoted_set_equality = report.state == InventoryState::Promoted\n"
        "        && report.native_count == EXPECTED_PUBLIC_ROUTE_COUNT\n"
        "        && report.public_routes.len() == EXPECTED_PUBLIC_ROUTE_COUNT as usize\n"
        "        && report.remaining_routes.is_empty()\n"
        "        && ownership.len() == EXPECTED_PUBLIC_ROUTE_COUNT as usize\n"
        "        && unowned.is_empty();\n"
    )
    text = sub_n(text, promoted_pattern, promoted_replacement, 1, "promoted ownership boundary")

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
        "ownership probe environment receipt",
    )
    old_boundary = (
        "selector/lower-module ownership in the frozen state and exact public/native set equality "
        "in the promoted state; behavioral parity still requires differential execution"
    )
    new_boundary = (
        "production selector ownership for the canonical route set, plus lower-module ownership "
        "for frozen remaining routes; behavioral parity still requires differential execution"
    )
    text = replace_n(text, old_boundary, new_boundary, 2, "ownership claim boundary")

    test_anchor = '        assert_eq!(selector_path("provider proxy"), vec!["provider", "proxy"]);\n'
    test_add = (
        test_anchor
        + "        assert_eq!(\n"
        + '            selector_path("engine route config.show"),\n'
        + '            vec!["engine", "route", "config.show"]\n'
        + "        );\n"
    )
    text = replace_once(text, test_anchor, test_add, "ownership selector path test anchor")
    write(path, text)


def patch_transition_reporter() -> None:
    path = "tools/report_phase2_rust_migration_transition.py"
    text = read(path)
    text = replace_once(text, '        "owned_count": 0,', '        "owned_count": 245,', "transition owned count")

    old_empty = (
        '    for key in ("selector_paths", "owner_modules", "owner_candidates"):\n'
        '        if ownership.get(key) != {}:\n'
        '            raise AssertionError(f"promoted ownership {key} must be empty")\n'
    )
    new_empty = (
        '    selector_paths = ownership.get("selector_paths")\n'
        '    if not isinstance(selector_paths, dict) or len(selector_paths) != 245:\n'
        '        raise AssertionError("promoted ownership selector_paths must prove all 245 routes")\n'
        '    for key in ("owner_modules", "owner_candidates"):\n'
        '        if ownership.get(key) != {}:\n'
        '            raise AssertionError(f"promoted ownership {key} must be empty")\n'
    )
    text = replace_once(text, old_empty, new_empty, "transition promoted map validation")

    call_anchor = "    _validate_promoted_ownership(ownership)\n\n    manifest = list(python.get(\"manifest\") or [])\n"
    call_replacement = (
        "    _validate_promoted_ownership(ownership)\n\n"
        '    selector_paths = ownership["selector_paths"]\n'
        '    if set(selector_paths) != canonical_routes:\n'
        '        raise AssertionError("promoted production selector proof must cover the exact canonical route set")\n\n'
        '    manifest = list(python.get("manifest") or [])\n'
    )
    text = replace_once(text, call_anchor, call_replacement, "transition promoted ownership call")

    for old, new, label in [
        ('"rust_selector_components": route.split(),', '"rust_selector_components": selector_paths[route],', "transition selector components"),
        ('"rust_selector_resolution": "promoted-contract-route-identity",', '"rust_selector_resolution": "production-native-product-supports",', "transition selector resolution"),
        ('"rust_owner_boundary": "dual-engine-public-surface-v2.native_public_commands",', '"rust_owner_boundary": "native_product::supports",', "transition owner boundary"),
        ('"rust_owner_module": "promoted-native-contract",', '"rust_owner_module": "production-selector",', "transition owner module"),
        ('"promoted_set_equality": "tools/report_missing_native_public_routes.py + syntavra-remaining71-ownership",', '"promoted_set_equality": "canonical inventory + production native_product::supports audit",', "transition promoted authority"),
    ]:
        text = replace_once(text, old, new, label)

    text = replace_once(
        text,
        '            "atomic_promotion_target": 245,',
        '            "atomic_promotion_target": 245,\n            "public_selector_owned": 245,\n            "public_selector_unowned": 0,',
        "transition summary selector proof",
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
    old = (
        "              assert value['owned_count'] == 0\n"
        "              assert value['unowned_count'] == 0\n"
        "              assert value['selector_paths'] == {}\n"
        "              assert value['owner_module_count'] == 0\n"
    )
    new = (
        "              assert value['owned_count'] == 245\n"
        "              assert value['unowned_count'] == 0\n"
        "              assert len(value['selector_paths']) == 245\n"
        "              manifest_routes = {row['route'] for row in inventory['python']['manifest']}\n"
        "              assert set(value['selector_paths']) == manifest_routes\n"
        "              assert value['owner_module_count'] == 0\n"
    )
    text = replace_once(text, old, new, "phase2 promoted ownership assertions")
    write(path, text)


def patch_capability_workflow() -> None:
    path = ".github/workflows/remaining71-capability-differential.yml"
    text = read(path)
    old = (
        "          elif state == 'promoted-245-0':\n"
        "              assert ownership.get('native_route_count') == 245, ownership\n"
        "              assert ownership.get('report_derived_remaining_count') == 0, ownership\n"
        "              assert ownership.get('promoted_public_native_set_equality') is True, ownership\n"
        "              boundary = 'promoted-public-native-set-equality'\n"
    )
    new = (
        "          elif state == 'promoted-245-0':\n"
        "              assert ownership.get('native_route_count') == 245, ownership\n"
        "              assert ownership.get('report_derived_remaining_count') == 0, ownership\n"
        "              assert ownership.get('promoted_public_native_set_equality') is True, ownership\n"
        "              assert ownership.get('owned_count') == 245, ownership\n"
        "              assert len(ownership.get('selector_paths') or {}) == 245, ownership\n"
        "              boundary = 'promoted-production-selector-ownership'\n"
    )
    text = replace_once(text, old, new, "capability promoted ownership assertions")
    write(path, text)


def patch_transition_tests() -> None:
    path = "tests/runtime/test_phase2_rust_migration_transition.py"
    text = read(path)
    text = replace_once(text, '            "owned_count": 0,', '            "owned_count": 245,', "transition test owned count")
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
        "transition test bad ownership count",
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
