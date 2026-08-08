#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "crates" / "syntavra-cli" / "src" / "native_host.rs"
EXPANSION = ROOT / "crates" / "syntavra-cli" / "src" / "native_expansion.rs"
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"

HOST_CONTRACT = '''pub(crate) fn fabric_install_contract(
    host: &str,
    project: &Path,
    scope: &str,
) -> Result<Value, String> {
    let normalized = host.to_lowercase();
    let specs = host_specs();
    let active = specs
        .into_iter()
        .find(|spec| spec.host == normalized)
        .filter(|spec| spec.host != "generic-mcp")
        .ok_or_else(|| format!("unsupported concrete host: {host}"))?;

    let mut overlay = Map::new();
    overlay.insert(
        "mcpServers".to_owned(),
        json!({"syntavra": {"command": "syntavra", "args": ["mcp"]}}),
    );
    if active.host == "claude-code" {
        overlay.insert(
            "statusLine".to_owned(),
            json!({"type": "command", "command": "syntavra run statusline"}),
        );
    }
    if active.supports_pre_tool_hook || active.supports_post_tool_hook {
        overlay.insert(
            "hooks".to_owned(),
            json!({
                "PreToolUse": [{"type": "command", "command": "syntavra hook pre"}],
                "PostToolUse": [{"type": "command", "command": "syntavra hook post"}],
                "UserPromptSubmit": [{"type": "command", "command": "syntavra hook prompt"}],
                "PreCompact": [{"type": "command", "command": "syntavra hook pre-compact"}],
                "SessionStart": [{"type": "command", "command": "syntavra hook session-start"}],
                "Stop": [{"type": "command", "command": "syntavra hook stop"}],
                "SessionEnd": [{"type": "command", "command": "syntavra hook session-end"}],
            }),
        );
    }

    let negotiation = negotiate_value(&normalized, true, None);
    let mut files = Vec::<Value>::new();
    if !active.config_path.is_empty() {
        files.push(json!({"path": active.config_path, "merge": Value::Object(overlay.clone())}));
    }
    if !active.skill_path.is_empty() {
        let skill_plan_path = if active.skill_path.ends_with(".md") {
            active.skill_path.clone()
        } else {
            format!("{}/SKILL.md", active.skill_path.trim_end_matches('/'))
        };
        files.push(json!({"path": skill_plan_path, "source": "bundled syntavra skill"}));
    }
    let plan = json!({
        "host": active.host,
        "display_name": active.display_name,
        "scope": scope,
        "project": project.to_string_lossy(),
        "mode": negotiation["mode"],
        "enforced": negotiation["enforced"],
        "verified_adapter": active.verified,
        "files": files,
        "capabilities": capabilities(&active),
        "validation": [
            "syntavra doctor",
            format!("syntavra host negotiate --host-name {}", active.host),
            "syntavra status",
        ],
    });
    Ok(json!({
        "host": active.host,
        "config_path": active.config_path,
        "skill_path": active.skill_path,
        "hooks_required": active.supports_pre_tool_hook || active.supports_post_tool_hook,
        "overlay": Value::Object(overlay),
        "negotiation_installed_true": negotiate_value(&normalized, true, Some(true)),
        "negotiation_installed_false": negotiate_value(&normalized, true, Some(false)),
        "plan": plan,
    }))
}

'''
HOST_ANCHOR = "pub(crate) fn doctor_contract(host: &str) -> Value {\n"

EXPANSION_BRIDGE = '''pub(crate) fn fabric_install_contract(
    host: &str,
    project: &Path,
    scope: &str,
) -> Result<Value, String> {
    native_host::fabric_install_contract(host, project, scope)
}

'''
EXPANSION_ANCHOR = "pub(crate) fn doctor_host_contract(host: &str) -> Value {\n"

INSTALL_MODULE = '''#[path = "native_fabric_install.rs"]
mod native_fabric_install;
'''
MODULE_ANCHOR = '''#[path = "native_fabric_insights.rs"]
mod native_fabric_insights;
'''
INSTALL_SUPPORT = "        || native_fabric_install::supports(command)\n"
SUPPORT_ANCHOR = "        || native_fabric_insights::supports(command)\n"
INSTALL_EXECUTE = '''    if native_fabric_install::supports(command) {
        return native_fabric_install::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_fabric_insights::supports(command) {
        return native_fabric_insights::execute(&arguments, state_root).map(Some);
    }
'''


def insert_once(source: str, token: str, anchor: str, label: str) -> tuple[str, bool]:
    count = source.count(token)
    if count == 1:
        return source, False
    if count != 0:
        raise RuntimeError(f"{label} count invalid: {count}")
    if source.count(anchor) != 1:
        raise RuntimeError(f"{label} anchor must be unique")
    return source.replace(anchor, token + anchor, 1), True


def repair_host() -> bool:
    source = HOST.read_text(encoding="utf-8")
    rendered, changed = insert_once(
        source,
        HOST_CONTRACT,
        HOST_ANCHOR,
        "native host fabric install contract",
    )
    if rendered.count("pub(crate) fn fabric_install_contract") != 1:
        raise RuntimeError("native host fabric install contract invariant failed")
    if changed:
        HOST.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_expansion() -> bool:
    source = EXPANSION.read_text(encoding="utf-8")
    rendered, changed = insert_once(
        source,
        EXPANSION_BRIDGE,
        EXPANSION_ANCHOR,
        "native expansion fabric install bridge",
    )
    if rendered.count("pub(crate) fn fabric_install_contract") != 1:
        raise RuntimeError("native expansion fabric install bridge invariant failed")
    if changed:
        EXPANSION.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    for token, anchor, label in (
        (INSTALL_MODULE, MODULE_ANCHOR, "fabric install module"),
        (INSTALL_SUPPORT, SUPPORT_ANCHOR, "fabric install support"),
        (INSTALL_EXECUTE, EXECUTE_ANCHOR, "fabric install execute"),
    ):
        rendered, applied = insert_once(rendered, token, anchor, label)
        changed = changed or applied
    if any(
        rendered.count(token) != 1
        for token in (INSTALL_MODULE, INSTALL_SUPPORT, INSTALL_EXECUTE)
    ):
        raise RuntimeError("fabric install product wiring invariant failed")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair() -> bool:
    host_changed = repair_host()
    expansion_changed = repair_expansion()
    product_changed = repair_product()
    return host_changed or expansion_changed or product_changed


def main() -> int:
    changed = repair()
    print(
        json.dumps(
            {
                "changed": changed,
                "ok": True,
                "surface": "native-fabric-install-contract",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
