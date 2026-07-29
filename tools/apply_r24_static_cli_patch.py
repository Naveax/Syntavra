#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


def patch_unified_cli() -> None:
    path = ROOT / "syntavra_runtime" / "unified_cli.py"
    replace_once(
        path,
        "from .plugin_sdk import PluginRegistry\n",
        "from .read_only_cli_contract import pipeline_description, plugin_inventory\n",
    )
    replace_once(path, "from .runtime_pipeline import UnifiedRuntimePipeline\n", "")
    replace_once(
        path,
        "    project_id = stable_project_id(project)\n    evidence = EvidenceStore(state / \"evidence\", project_id=project_id)\n",
        "    project_id = stable_project_id(project)\n\n"
        "    if args.command == \"pipeline\":\n"
        "        _emit(pipeline_description())\n"
        "        return 0\n"
        "    if args.command == \"plugins\":\n"
        "        _emit(plugin_inventory())\n"
        "        return 0\n\n"
        "    evidence = EvidenceStore(state / \"evidence\", project_id=project_id)\n",
    )
    replace_once(
        path,
        "    if args.command == \"pipeline\":\n"
        "        config = ConfigManager(project_root=project, state_root=state)\n"
        "        observability = Observability(state / \"observability\")\n"
        "        pipeline = UnifiedRuntimePipeline(evidence=evidence, config=config, observability=observability)\n"
        "        _emit(pipeline.describe())\n"
        "        return 0\n\n"
        "    if args.command == \"plugins\":\n"
        "        _emit({\"plugins\": PluginRegistry().records(), \"discovery\": \"explicit-only\"})\n"
        "        return 0\n\n",
        "",
    )


def patch_engine_cli() -> None:
    path = ROOT / "syntavra_runtime" / "engine_cli.py"
    replace_once(
        path,
        "from .read_only_router_r22 import ReadOnlyCommandRouterR22\n",
        "from .read_only_router_r24 import ReadOnlyCommandRouterR24\n",
    )
    replace_once(
        path,
        "description=\"Syntavra R22 capability-aware auto read-only router\"",
        "description=\"Syntavra R24 full read-only CLI parity router\"",
    )
    replace_once(path, "router: ReadOnlyCommandRouterR22 | None = None", "router: ReadOnlyCommandRouterR24 | None = None")
    replace_once(path, "active_router = router or ReadOnlyCommandRouterR22(", "active_router = router or ReadOnlyCommandRouterR24(")


def patch_engine_entry() -> None:
    path = ROOT / "syntavra_runtime" / "engine_entry.py"
    replace_once(
        path,
        "from .engine_selector import ENGINE_MODES, EngineSelectionError, EngineSelector\n",
        "from .engine_selector import ENGINE_MODES, EngineSelectionError, EngineSelector\n"
        "from .read_only_router_r24 import ReadOnlyCommandRouterR24\n",
    )
    replace_once(
        path,
        "SELECTOR_COMMANDS = frozenset({\"engine\"})\n",
        "SELECTOR_COMMANDS = frozenset({\"engine\"})\n"
        "STATIC_READ_ONLY_COMMANDS = {\n"
        "    (\"pipeline\", \"describe\"): \"pipeline.describe\",\n"
        "    (\"plugins\", \"list\"): \"plugins.list\",\n"
        "}\n",
    )
    replace_once(
        path,
        "def _find_command(rest: list[str]) -> str:\n"
        "    for value in rest:\n"
        "        if not value.startswith(\"-\"):\n"
        "            return value\n"
        "    return \"\"\n",
        "def _find_command(rest: list[str]) -> str:\n"
        "    for value in rest:\n"
        "        if not value.startswith(\"-\"):\n"
        "            return value\n"
        "    return \"\"\n\n\n"
        "def _static_read_only_route(rest: list[str]) -> str | None:\n"
        "    return STATIC_READ_ONLY_COMMANDS.get(tuple(rest))\n",
    )
    replace_once(
        path,
        "        selector = EngineSelector(project_root=project, state_root=state)\n"
        "        if command in SELECTOR_COMMANDS:\n",
        "        selector = EngineSelector(project_root=project, state_root=state)\n"
        "        static_route = _static_read_only_route(rest)\n"
        "        if static_route is not None:\n"
        "            router = ReadOnlyCommandRouterR24(selector, project_input_root=project)\n"
        "            routed = router.route(static_route, cli_override=override)\n"
        "            _emit(routed[\"result\"])\n"
        "            return 0\n"
        "        if command in SELECTOR_COMMANDS:\n",
    )


def patch_contracts() -> None:
    path = ROOT / "crates" / "syntavra-contracts" / "src" / "lib.rs"
    replace_once(
        path,
        "    Capability {\n"
        "        name: \"engine.contract-hash\",\n"
        "        maturity: \"preview\",\n"
        "        mutation: \"read-only\",\n"
        "    },\n"
        "    Capability {\n"
        "        name: \"receipt.inspect\",\n",
        "    Capability {\n"
        "        name: \"engine.contract-hash\",\n"
        "        maturity: \"preview\",\n"
        "        mutation: \"read-only\",\n"
        "    },\n"
        "    Capability {\n"
        "        name: \"pipeline.describe\",\n"
        "        maturity: \"preview\",\n"
        "        mutation: \"read-only\",\n"
        "    },\n"
        "    Capability {\n"
        "        name: \"plugins.list\",\n"
        "        maturity: \"preview\",\n"
        "        mutation: \"read-only\",\n"
        "    },\n"
        "    Capability {\n"
        "        name: \"receipt.inspect\",\n",
    )
    replace_once(
        path,
        "    \"capability=engine.contract-hash|preview|read-only\\n\",\n"
        "    \"capability=receipt.inspect|preview|read-only\\n\",\n",
        "    \"capability=engine.contract-hash|preview|read-only\\n\",\n"
        "    \"capability=pipeline.describe|preview|read-only\\n\",\n"
        "    \"capability=plugins.list|preview|read-only\\n\",\n"
        "    \"capability=receipt.inspect|preview|read-only\\n\",\n",
    )
    replace_once(
        path,
        "        assert!(capabilities_json().contains(\"\\\"name\\\":\\\"receipt.inspect\\\"\"));\n",
        "        assert!(capabilities_json().contains(\"\\\"name\\\":\\\"pipeline.describe\\\"\"));\n"
        "        assert!(capabilities_json().contains(\"\\\"name\\\":\\\"plugins.list\\\"\"));\n"
        "        assert!(capabilities_json().contains(\"\\\"name\\\":\\\"receipt.inspect\\\"\"));\n",
    )


def patch_rust_cli() -> None:
    path = ROOT / "crates" / "syntavra-cli" / "src" / "main.rs"
    replace_once(path, "mod config_contract;\n", "mod config_contract;\nmod read_only_cli_contract;\n")
    replace_once(
        path,
        "use config_contract::{default_config_wire, resolve_config_wire, snapshot_json, status_json};\n",
        "use config_contract::{default_config_wire, resolve_config_wire, snapshot_json, status_json};\n"
        "use read_only_cli_contract::result_json as static_cli_result_json;\n",
    )
    replace_once(
        path,
        "    \"  syntavra-rs config resolve <config-wire-hex>\\n\",\n",
        "    \"  syntavra-rs config resolve <config-wire-hex>\\n\",\n"
        "    \"  syntavra-rs pipeline describe\\n\",\n"
        "    \"  syntavra-rs plugins list\\n\",\n",
    )
    replace_once(
        path,
        "    ConfigResolve(String),\n    StateLayout,\n",
        "    ConfigResolve(String),\n    PipelineDescribe,\n    PluginsList,\n    StateLayout,\n",
    )
    replace_once(
        path,
        "        [config, action, wire] if config == \"config\" && action == \"resolve\" => {\n"
        "            Ok(Command::ConfigResolve(wire.clone()))\n"
        "        }\n"
        "        [state, action] if state == \"state\" && action == \"layout\" => Ok(Command::StateLayout),\n",
        "        [config, action, wire] if config == \"config\" && action == \"resolve\" => {\n"
        "            Ok(Command::ConfigResolve(wire.clone()))\n"
        "        }\n"
        "        [pipeline, action] if pipeline == \"pipeline\" && action == \"describe\" => {\n"
        "            Ok(Command::PipelineDescribe)\n"
        "        }\n"
        "        [plugins, action] if plugins == \"plugins\" && action == \"list\" => {\n"
        "            Ok(Command::PluginsList)\n"
        "        }\n"
        "        [state, action] if state == \"state\" && action == \"layout\" => Ok(Command::StateLayout),\n",
    )
    replace_once(
        path,
        "        Command::ConfigResolve(encoded) => {\n"
        "            let wire = decode_hex(&encoded)?;\n"
        "            let snapshot = resolve_config_wire(&wire)?;\n"
        "            println!(\"{}\", snapshot_json(&snapshot)?);\n"
        "        }\n"
        "        Command::StateLayout => println!(\"{}\", state_layout_json()),\n",
        "        Command::ConfigResolve(encoded) => {\n"
        "            let wire = decode_hex(&encoded)?;\n"
        "            let snapshot = resolve_config_wire(&wire)?;\n"
        "            println!(\"{}\", snapshot_json(&snapshot)?);\n"
        "        }\n"
        "        Command::PipelineDescribe => println!(\"{}\", static_cli_result_json(\"pipeline.describe\")?),\n"
        "        Command::PluginsList => println!(\"{}\", static_cli_result_json(\"plugins.list\")?),\n"
        "        Command::StateLayout => println!(\"{}\", state_layout_json()),\n",
    )
    replace_once(
        path,
        "    #[test]\n    fn rejects_unknown_commands() {\n",
        "    #[test]\n"
        "    fn parses_r24_static_read_only_cli_commands() {\n"
        "        assert_eq!(\n"
        "            parse_command(&args(&[\"pipeline\", \"describe\"])),\n"
        "            Ok(Command::PipelineDescribe)\n"
        "        );\n"
        "        assert_eq!(\n"
        "            parse_command(&args(&[\"plugins\", \"list\"])),\n"
        "            Ok(Command::PluginsList)\n"
        "        );\n"
        "    }\n\n"
        "    #[test]\n    fn rejects_unknown_commands() {\n",
    )


def main() -> int:
    patch_unified_cli()
    patch_engine_cli()
    patch_engine_entry()
    patch_contracts()
    patch_rust_cli()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
