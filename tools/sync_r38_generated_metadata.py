#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import sync_r38_native_command_count as legacy

ROOT = Path(__file__).resolve().parents[1]
STATIC_SURFACES = ROOT / "crates/syntavra-cli/src/native_static_surfaces.rs"
EXPANSION_DECLARATION = '#[path = "native_expansion.rs"]\nmod native_expansion;\n'
EXPANSION_ANCHOR = '#[path = "native_external_suite_gate.rs"]\nmod native_external_suite_gate;\n'

PLATFORM_COMPONENTS = (
    "context-compiler",
    "terminal-output-engine",
    "output-firewall",
    "artifact-store",
    "canonical-repository-graph",
    "indexed-repository-query",
    "tree-sitter-syntax-adapter",
    "semantic-intelligence",
    "runtime-evidence",
    "universal-language-platform",
    "sandboxed-language-services",
    "generic-lsp-bridge",
    "semantic-index-import",
    "session-memory",
    "capability-security",
    "execution-sandbox",
    "provider-gateway",
    "adapter-platform",
    "model-gateway",
    "agent-runtime",
    "coding-agent",
    "headless-runtime",
    "interactive-console",
    "reliability-laboratory",
    "distribution-manager",
    "signalbench",
)
PLATFORM_ROUTES = (
    "run competitive-manifest",
    "run platform-manifest",
)

LEGACY_STATIC_SUPPORT = """pub fn supports(command: &[String]) -> bool {
    command.len() == 2
        && matches!(
            (command[0].as_str(), command[1].as_str()),
            (\"provider\", \"capabilities\")
                | (\"output\", \"profiles\")
                | (\"benchmark\", \"generate-config\")
        )
}
"""
CANONICAL_STATIC_SUPPORT = """pub fn supports(command: &[String]) -> bool {
    command.len() == 2
        && matches!(
            (command[0].as_str(), command[1].as_str()),
            (\"provider\", \"capabilities\")
                | (\"output\", \"profiles\")
                | (\"benchmark\", \"generate-config\")
                | (\"run\", \"platform-manifest\" | \"competitive-manifest\")
        )
}
"""
LEGACY_STATIC_EXECUTE_ARM = (
    '        ("benchmark", "generate-config") => benchmark_generate_config(arguments),\n'
)
CANONICAL_STATIC_EXECUTE_ARMS = """        (\"benchmark\", \"generate-config\") => benchmark_generate_config(arguments),
        (\"run\", \"platform-manifest\" | \"competitive-manifest\") => Ok(platform_manifest()),
"""
STATIC_EXECUTE_SIGNATURE = (
    "pub fn execute(command: &[String], arguments: &[String]) -> Result<Value, String> {"
)

PLATFORM_MANIFEST_FUNCTION = """fn platform_manifest() -> Value {
    json!({
        \"product\": \"Syntavra\",
        \"version\": \"0.0.1\",
        \"channel\": \"pre-release\",
        \"runtime\": \"unified\",
        \"components\": [
            \"context-compiler\",
            \"terminal-output-engine\",
            \"output-firewall\",
            \"artifact-store\",
            \"canonical-repository-graph\",
            \"indexed-repository-query\",
            \"tree-sitter-syntax-adapter\",
            \"semantic-intelligence\",
            \"runtime-evidence\",
            \"universal-language-platform\",
            \"sandboxed-language-services\",
            \"generic-lsp-bridge\",
            \"semantic-index-import\",
            \"session-memory\",
            \"capability-security\",
            \"execution-sandbox\",
            \"provider-gateway\",
            \"adapter-platform\",
            \"model-gateway\",
            \"agent-runtime\",
            \"coding-agent\",
            \"headless-runtime\",
            \"interactive-console\",
            \"reliability-laboratory\",
            \"distribution-manager\",
            \"signalbench\"
        ],
        \"adapter_contract\": {
            \"ok\": true,
            \"inventory_gate\": true,
            \"adapters\": 20,
            \"levels\": {\"A\": 4, \"B\": 10, \"C\": 5, \"D\": 1},
            \"surfaces\": {\"cli\": 8, \"ide\": 7, \"ide-extension\": 3, \"platform\": 2},
            \"non_cli_adapters\": 12,
            \"invalid\": [],
            \"live_certified\": 0,
            \"live_boundary\": \"live certification requires external execution receipts\"
        },
        \"external_claims\": \"NOT_PROVEN_WITHOUT_EXTERNAL_RECEIPTS\"
    })
}

"""

REQUIRED_NATIVE_COMMANDS = {
    "host",
    "host capabilities",
    "host detect",
    "host negotiate",
    "inspect impact",
    "inspect map",
    "inspect paths",
    "inspect stats",
    "inspect symbol",
    "output compact",
    "output govern",
    *PLATFORM_ROUTES,
}


def normalize_native_expansion(path: Path = legacy.NATIVE_PRODUCT) -> bool:
    source = path.read_text(encoding="utf-8")
    without_expansion = source.replace(EXPANSION_DECLARATION, "")
    anchor_count = without_expansion.count(EXPANSION_ANCHOR)
    if anchor_count != 1:
        raise RuntimeError(
            f"expected one native expansion anchor, found {anchor_count}"
        )
    rendered = without_expansion.replace(
        EXPANSION_ANCHOR,
        EXPANSION_DECLARATION + EXPANSION_ANCHOR,
        1,
    )
    changed = rendered != source
    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def _validate_platform_manifest_source(source: str, path: Path) -> None:
    for route in ("platform-manifest", "competitive-manifest"):
        count = source.count(f'"{route}"')
        if count != 2:
            raise RuntimeError(
                f"platform manifest route invariant failed in {path}: {route}={count}"
            )
    if source.count("fn platform_manifest() -> Value {") != 1:
        raise RuntimeError(f"platform manifest function invariant failed in {path}")
    start = source.find("fn platform_manifest() -> Value {")
    end = source.find(STATIC_EXECUTE_SIGNATURE, start)
    if start < 0 or end < 0:
        raise RuntimeError(f"platform manifest function boundary missing in {path}")
    function = source[start:end]
    for component in PLATFORM_COMPONENTS:
        if function.count(f'"{component}"') != 1:
            raise RuntimeError(
                f"platform manifest component invariant failed in {path}: {component}"
            )
    required_fragments = (
        '"product": "Syntavra"',
        '"version": "0.0.1"',
        '"channel": "pre-release"',
        '"runtime": "unified"',
        '"adapters": 20',
        '"non_cli_adapters": 12',
        '"live_certified": 0',
        '"external_claims": "NOT_PROVEN_WITHOUT_EXTERNAL_RECEIPTS"',
    )
    missing = [fragment for fragment in required_fragments if fragment not in function]
    if missing:
        raise RuntimeError(
            f"platform manifest payload invariant failed in {path}: {missing}"
        )


def repair_platform_manifest(path: Path = STATIC_SURFACES) -> bool:
    source = path.read_text(encoding="utf-8")
    route_markers = sum(
        source.count(f'"{route}"')
        for route in ("platform-manifest", "competitive-manifest")
    )
    function_count = source.count("fn platform_manifest() -> Value {")
    if route_markers == 4 and function_count == 1:
        _validate_platform_manifest_source(source, path)
        return False
    if route_markers != 0 or function_count != 0:
        raise RuntimeError(
            f"partial platform manifest implementation in {path}: "
            f"route_markers={route_markers}, functions={function_count}"
        )
    if source.count(LEGACY_STATIC_SUPPORT) != 1:
        raise RuntimeError(f"legacy static support anchor missing in {path}")
    if source.count(LEGACY_STATIC_EXECUTE_ARM) != 1:
        raise RuntimeError(f"legacy static execute anchor missing in {path}")
    if source.count(STATIC_EXECUTE_SIGNATURE) != 1:
        raise RuntimeError(f"static execute signature invariant failed in {path}")

    rendered = source.replace(
        LEGACY_STATIC_SUPPORT,
        CANONICAL_STATIC_SUPPORT,
        1,
    )
    rendered = rendered.replace(
        LEGACY_STATIC_EXECUTE_ARM,
        CANONICAL_STATIC_EXECUTE_ARMS,
        1,
    )
    rendered = rendered.replace(
        STATIC_EXECUTE_SIGNATURE,
        PLATFORM_MANIFEST_FUNCTION + STATIC_EXECUTE_SIGNATURE,
        1,
    )
    _validate_platform_manifest_source(rendered, path)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def ensure_required_native_commands(path: Path = legacy.CONTRACT) -> bool:
    contract = json.loads(path.read_text(encoding="utf-8"))
    rust = contract["rust_surface"]
    commands = set(rust["native_public_commands"])
    missing = REQUIRED_NATIVE_COMMANDS - commands
    if not missing:
        return False
    commands.update(REQUIRED_NATIVE_COMMANDS)
    rust["native_public_commands"] = sorted(commands)
    path.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return True


def synchronize() -> int:
    repair_platform_manifest()
    ensure_required_native_commands()
    normalize_native_expansion()
    status = legacy.sync()
    normalize_native_expansion()
    repair_platform_manifest()
    return status


if __name__ == "__main__":
    raise SystemExit(synchronize())
