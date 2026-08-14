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


def patch_ownership_probe() -> None:
    path = "crates/syntavra-cli/src/bin/syntavra-remaining71-ownership.rs"
    text = read(path)

    marker = "\nfn remaining71_owner_modules(command: &[String]) -> Vec<&'static str> {\n"
    helper = r'''
fn production_selector_owns(route: &str, command: &[String]) -> bool {
    // The top-level selector consumes engine management actions before
    // run_selected()/native_product dispatch. They are native selector-owned,
    // but intentionally are not native_product::supports() routes.
    if matches!(
        command,
        [engine, action]
            if engine == "engine"
                && matches!(action.as_str(), "list" | "status" | "use" | "verify")
    ) {
        return true;
    }

    // "engine route" is a canonical parser leaf with a required positional
    // route name. Production dispatch owns the family at three components via
    // native_engine_route_control::supports(). Probe that family with a
    // deliberately non-certified route name: ownership is what is being
    // audited here, not successful execution of a particular route payload.
    if route == "engine route" {
        let family_probe = vec![
            "engine".to_owned(),
            "route".to_owned(),
            "__ownership_probe__".to_owned(),
        ];
        return native_product::supports(&family_probe);
    }

    native_product::supports(command)
}
'''
    text = replace_once(
        text,
        marker,
        "\n" + helper + marker,
        "production selector ownership helper insertion",
    )
    text = replace_once(
        text,
        "        let owned = native_product::supports(&path);\n",
        "        let owned = production_selector_owns(route, &path);\n",
        "production selector ownership call",
    )

    old_import = (
        "    use super::{inventory_state, remaining71_owner_modules, selector_path, InventoryState};\n"
    )
    new_import = (
        "    use super::{\n"
        "        inventory_state, production_selector_owns, remaining71_owner_modules, selector_path,\n"
        "        InventoryState,\n"
        "    };\n"
    )
    text = replace_once(text, old_import, new_import, "ownership test import")

    anchor = "    #[test]\n    fn known_remaining_selectors_have_single_lower_module_owner() {\n"
    tests = r'''    #[test]
    fn production_selector_accounts_for_engine_management_and_route_family() {
        for action in ["list", "status", "use", "verify"] {
            let route = format!("engine {action}");
            let path = selector_path(&route);
            assert!(production_selector_owns(&route, &path), "route={route}");
        }

        let route = "engine route";
        let path = selector_path(route);
        assert_eq!(path, vec!["engine", "route"]);
        assert!(production_selector_owns(route, &path));
    }

'''
    text = replace_once(
        text,
        anchor,
        tests + anchor,
        "engine selector ownership tests",
    )

    authority_anchor = (
        '            "authority": "tools/report_missing_native_public_routes.py canonical report",\n'
    )
    authority_insert = authority_anchor + (
        '            "selector_authority": "syntavra.rs engine_command for engine management; '
        'native_product::supports for native product routes; native_engine_route_control family '
        'ownership for the engine route parser leaf",\n'
    )
    text = replace_once(
        text,
        authority_anchor,
        authority_insert,
        "selector authority output",
    )
    write(path, text)


def patch_transition_reporter() -> None:
    path = "tools/report_phase2_rust_migration_transition.py"
    text = read(path)
    replacements = (
        (
            '"rust_selector_resolution": "production-native-product-supports",',
            '"rust_selector_resolution": "production-selector-owned",',
            "transition selector resolution authority",
        ),
        (
            '"rust_owner_boundary": "native_product::supports",',
            '"rust_owner_boundary": "production-selector",',
            "transition owner boundary authority",
        ),
        (
            '"promoted_set_equality": "canonical inventory + production native_product::supports audit",',
            '"promoted_set_equality": "canonical inventory + production selector ownership audit",',
            "transition promoted authority",
        ),
    )
    for old, new, label in replacements:
        text = replace_once(text, old, new, label)
    write(path, text)


def main() -> int:
    patch_ownership_probe()
    patch_transition_reporter()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
