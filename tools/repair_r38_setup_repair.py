#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "crates" / "syntavra-cli" / "src" / "native_install.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"

MODULE_TOKEN = 'mod native_setup_repair;'
SUPPORT_TOKEN = 'native_setup_repair::supports(command)'
EXECUTE_TOKEN = 'if native_setup_repair::supports(command) {'
SETUP_TEST_TOKEN = 'vec!["setup"]'
REPAIR_TEST_TOKEN = 'vec!["repair"]'

MODULE_ANCHOR = '''#[path = "native_install.rs"]
mod native_install;
'''
MODULE_INSERT = '''#[path = "native_setup_repair.rs"]
mod native_setup_repair;
'''
SUPPORT_ANCHOR = '        || native_install::supports(command)\n'
SUPPORT_INSERT = '        || native_setup_repair::supports(command)\n'
EXECUTE_ANCHOR = '''    if native_install::supports(command) {
        return native_install::execute(arguments, project_root, state_root);
    }
'''
EXECUTE_INSERT = '''    if native_setup_repair::supports(command) {
        let decision = native_setup_repair::execute(
            command,
            arguments,
            project_root,
            state_root,
        )?;
        if decision.exit_code != 0 {
            emit_failed_value(&decision.value, decision.exit_code);
        }
        return Ok(decision.value);
    }
'''
TEST_ANCHOR = '            vec!["install"],\n'
TEST_INSERT = '            vec!["setup"],\n            vec!["repair"],\n'
INSTALL_EXECUTE_ANCHOR = 'pub fn execute(\n'

REPAIR_HELPERS = '''pub(super) fn repair_bundle(
    project_root: &Path,
    state_root: &Path,
    profile_name: &str,
) -> Result<Value, String> {
    fs::create_dir_all(project_root)
        .map_err(|error| format!("INSTALL_PROJECT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(state_root)
        .map_err(|error| format!("INSTALL_STATE_CREATE_FAILED:{error}"))?;
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("INSTALL_PROJECT_RESOLVE_FAILED:{error}"))?;
    let state = fs::canonicalize(state_root)
        .map_err(|error| format!("INSTALL_STATE_RESOLVE_FAILED:{error}"))?;
    profile(profile_name)?;
    write_bundle(&project, &state, profile_name)
}

pub(super) fn reapply_host(
    host: &str,
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    fs::create_dir_all(project_root)
        .map_err(|error| format!("INSTALL_PROJECT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(state_root)
        .map_err(|error| format!("INSTALL_STATE_CREATE_FAILED:{error}"))?;
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("INSTALL_PROJECT_RESOLVE_FAILED:{error}"))?;
    let state = fs::canonicalize(state_root)
        .map_err(|error| format!("INSTALL_STATE_RESOLVE_FAILED:{error}"))?;
    let spec = host_spec(host).ok_or_else(|| format!("unsupported concrete host: {host}"))?;
    let source = skill_root(&project);
    apply_host(spec, &project, &state, &source, false)
}

'''

INSTALL_INVARIANTS = (
    'pub(super) fn repair_bundle(',
    'pub(super) fn reapply_host(',
    '"mcp_capable": 14',
    '"primary-certification-target": 3',
)


def insert_after_once(source: str, *, token: str, anchor: str, addition: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label}: expected token count 0 or 1, got {count}")
    anchor_count = source.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(f"{label}: expected one anchor, got {anchor_count}")
    return source.replace(anchor, anchor + addition, 1), True


def repair_install_contract() -> bool:
    source = INSTALL.read_text(encoding="utf-8")
    rendered = source
    helper_counts = (
        rendered.count('pub(super) fn repair_bundle('),
        rendered.count('pub(super) fn reapply_host('),
    )
    changed = False
    if helper_counts == (0, 0):
        anchor_count = rendered.count(INSTALL_EXECUTE_ANCHOR)
        if anchor_count != 1:
            raise RuntimeError(f"native install execute anchor count must be 1, got {anchor_count}")
        rendered = rendered.replace(INSTALL_EXECUTE_ANCHOR, REPAIR_HELPERS + INSTALL_EXECUTE_ANCHOR, 1)
        changed = True
    elif helper_counts != (1, 1):
        raise RuntimeError(f"native install repair helper counts invalid: {helper_counts}")

    invalid = {token: rendered.count(token) for token in INSTALL_INVARIANTS if rendered.count(token) != 1}
    if invalid:
        raise RuntimeError(f"native install/setup contract incomplete: {invalid}")
    if changed:
        INSTALL.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_expansion() -> bool:
    source = EXPANSION.read_text(encoding="utf-8")
    rendered = source
    changed = False

    for token, anchor, addition, label in (
        (MODULE_TOKEN, MODULE_ANCHOR, MODULE_INSERT, "setup repair module"),
        (SUPPORT_TOKEN, SUPPORT_ANCHOR, SUPPORT_INSERT, "setup repair support"),
        (EXECUTE_TOKEN, EXECUTE_ANCHOR, EXECUTE_INSERT, "setup repair execute"),
    ):
        rendered, applied = insert_after_once(
            rendered,
            token=token,
            anchor=anchor,
            addition=addition,
            label=label,
        )
        changed = changed or applied

    setup_count = rendered.count(SETUP_TEST_TOKEN)
    repair_count = rendered.count(REPAIR_TEST_TOKEN)
    if setup_count == repair_count == 0:
        anchor_count = rendered.count(TEST_ANCHOR)
        if anchor_count != 1:
            raise RuntimeError(f"setup repair route test: expected one anchor, got {anchor_count}")
        rendered = rendered.replace(TEST_ANCHOR, TEST_ANCHOR + TEST_INSERT, 1)
        changed = True
    elif setup_count != 1 or repair_count != 1:
        raise RuntimeError(
            "setup repair route test: expected setup/repair token counts 0/0 or 1/1; "
            f"got {setup_count}/{repair_count}"
        )

    invariants = (
        MODULE_TOKEN,
        SUPPORT_TOKEN,
        EXECUTE_TOKEN,
        SETUP_TEST_TOKEN,
        REPAIR_TEST_TOKEN,
    )
    invalid = {token: rendered.count(token) for token in invariants if rendered.count(token) != 1}
    if invalid:
        raise RuntimeError(f"native expansion setup/repair invariant failed: {invalid}")

    if changed:
        EXPANSION.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair_install_contract() | repair_expansion()
    print(json.dumps({"changed": changed, "ok": True, "surface": "setup-repair"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
