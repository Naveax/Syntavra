#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"{relative}: expected exactly one guarded anchor")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


# Native Rust CLI wiring.
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "mod state_snapshot_contract;\n",
    "mod state_snapshot_contract;\nmod telemetry_metrics_contract;\n",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "use state_snapshot_contract::inspect_state_root_json;\n",
    "use state_snapshot_contract::inspect_state_root_json;\n"
    "use telemetry_metrics_contract::telemetry_metrics_json;\n",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '    "  syntavra-rs scheduler list <state-root> <limit> <states-json-hex>\\n",\n',
    '    "  syntavra-rs scheduler list <state-root> <limit> <states-json-hex>\\n",\n'
    '    "  syntavra-rs telemetry metrics <json|prometheus>\\n",\n',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "    SchedulerList {\n"
    "        state_root: String,\n"
    "        limit: usize,\n"
    "        states_hex: String,\n"
    "    },\n"
    "    StateLayout,\n",
    "    SchedulerList {\n"
    "        state_root: String,\n"
    "        limit: usize,\n"
    "        states_hex: String,\n"
    "    },\n"
    "    TelemetryMetrics {\n"
    "        output_format: String,\n"
    "    },\n"
    "    StateLayout,\n",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '        [plugins, action] if plugins == "plugins" && action == "list" => Ok(Command::PluginsList),\n',
    '        [plugins, action] if plugins == "plugins" && action == "list" => Ok(Command::PluginsList),\n'
    '        [telemetry, action, output_format]\n'
    '            if telemetry == "telemetry" && action == "metrics" =>\n'
    '        {\n'
    '            Ok(Command::TelemetryMetrics {\n'
    '                output_format: output_format.clone(),\n'
    '            })\n'
    '        }\n',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '        Command::PluginsList => println!("{}", static_cli_result_json("plugins.list")?),\n'
    '        Command::StateLayout => println!("{}", state_layout_json()),\n',
    '        Command::PluginsList => println!("{}", static_cli_result_json("plugins.list")?),\n'
    '        Command::TelemetryMetrics { output_format } => {\n'
    '            println!("{}", telemetry_metrics_json(&output_format)?);\n'
    '        }\n'
    '        Command::StateLayout => println!("{}", state_layout_json()),\n',
)

# Python/Rust capability contract sync.
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "status",\n    "version",\n',
    '    "status",\n    "telemetry.metrics",\n    "version",\n',
)
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "capability=status|preview|read-only\\n"\n'
    '    "capability=version|preview|read-only\\n"\n',
    '    "capability=status|preview|read-only\\n"\n'
    '    "capability=telemetry.metrics|preview|read-only\\n"\n'
    '    "capability=version|preview|read-only\\n"\n',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '    Capability {\n'
    '        name: "status",\n'
    '        maturity: "preview",\n'
    '        mutation: "read-only",\n'
    '    },\n'
    '    Capability {\n'
    '        name: "version",\n',
    '    Capability {\n'
    '        name: "status",\n'
    '        maturity: "preview",\n'
    '        mutation: "read-only",\n'
    '    },\n'
    '    Capability {\n'
    '        name: "telemetry.metrics",\n'
    '        maturity: "preview",\n'
    '        mutation: "read-only",\n'
    '    },\n'
    '    Capability {\n'
    '        name: "version",\n',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '    "capability=status|preview|read-only\\n",\n'
    '    "capability=version|preview|read-only\\n",\n',
    '    "capability=status|preview|read-only\\n",\n'
    '    "capability=telemetry.metrics|preview|read-only\\n",\n'
    '    "capability=version|preview|read-only\\n",\n',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '        assert!(capabilities_json().contains("\\\"name\\\":\\\"status\\\""));\n',
    '        assert!(capabilities_json().contains("\\\"name\\\":\\\"status\\\""));\n'
    '        assert!(capabilities_json().contains("\\\"name\\\":\\\"telemetry.metrics\\\""));\n',
)
replace_once(
    "contracts/engine/descriptor.txt",
    "capability=status|preview|read-only\ncapability=version|preview|read-only\n",
    "capability=status|preview|read-only\n"
    "capability=telemetry.metrics|preview|read-only\n"
    "capability=version|preview|read-only\n",
)

# Installed engine router and public CLI wiring.
replace_once(
    "syntavra_runtime/engine_cli.py",
    "from .migration_plan_router_r24 import MigrationPlanRouterR24\n",
    "from .telemetry_metrics_router_r24 import TelemetryMetricsRouterR24\n",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    '    route.add_argument("--migration-database")\n',
    '    route.add_argument("--migration-database")\n'
    '    route.add_argument("--telemetry-prometheus", action="store_true")\n',
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    "    router: MigrationPlanRouterR24 | None = None,\n",
    "    router: TelemetryMetricsRouterR24 | None = None,\n",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    "    active_router = router or MigrationPlanRouterR24(\n",
    "    active_router = router or TelemetryMetricsRouterR24(\n",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    "            if args.migration_database is not None:\n"
    "                route_kwargs[\"migration_database\"] = args.migration_database\n"
    "            result = active_router.route(args.route_command, **route_kwargs)\n",
    "            if args.migration_database is not None:\n"
    "                route_kwargs[\"migration_database\"] = args.migration_database\n"
    "            if args.telemetry_prometheus:\n"
    "                route_kwargs[\"telemetry_prometheus\"] = True\n"
    "            result = active_router.route(args.route_command, **route_kwargs)\n",
)
replace_once(
    "syntavra_runtime/engine_entry.py",
    "from .migration_plan_router_r24 import MigrationPlanRouterR24\n",
    "from .telemetry_metrics_router_r24 import TelemetryMetricsRouterR24\n",
)
replace_once(
    "syntavra_runtime/engine_entry.py",
    '    if rest == ["scheduler", "stats"]:\n',
    '    if rest == ["telemetry", "metrics"]:\n'
    '        return "telemetry.metrics", {}\n'
    '    if rest == ["telemetry", "metrics", "--prometheus"]:\n'
    '        return "telemetry.metrics", {"telemetry_prometheus": True}\n'
    '    if rest == ["scheduler", "stats"]:\n',
)
replace_once(
    "syntavra_runtime/engine_entry.py",
    "            router = MigrationPlanRouterR24(selector, project_input_root=project)\n",
    "            router = TelemetryMetricsRouterR24(selector, project_input_root=project)\n",
)
replace_once(
    "syntavra_runtime/engine_entry.py",
    '            _emit(routed["result"])\n'
    '            return 0\n',
    '            result = routed["result"]\n'
    '            if route == "telemetry.metrics":\n'
    '                if result["format"] == "prometheus":\n'
    '                    print(result["text"])\n'
    '                else:\n'
    '                    _emit(result["metrics"])\n'
    '            else:\n'
    '                _emit(result)\n'
    '            return 0\n',
)
replace_once(
    "syntavra_runtime/unified_cli.py",
    "from .scheduler_read_only_contract import scheduler_read_only_result\n",
    "from .scheduler_read_only_contract import scheduler_read_only_result\n"
    "from .telemetry_metrics_contract import telemetry_metrics_result\n",
)
replace_once(
    "syntavra_runtime/unified_cli.py",
    '    if args.command == "migrate" and args.action == "plan":\n'
    '        _emit(migration_plan_read_only_result(project, args.database))\n'
    '        return 0\n\n'
    '    evidence = EvidenceStore(state / "evidence", project_id=project_id)\n',
    '    if args.command == "migrate" and args.action == "plan":\n'
    '        _emit(migration_plan_read_only_result(project, args.database))\n'
    '        return 0\n'
    '    if args.command == "telemetry" and args.action == "metrics":\n'
    '        result = telemetry_metrics_result("prometheus" if args.prometheus else "json")\n'
    '        if result["format"] == "prometheus":\n'
    '            print(result["text"])\n'
    '        else:\n'
    '            _emit(result["metrics"])\n'
    '        return 0\n\n'
    '    evidence = EvidenceStore(state / "evidence", project_id=project_id)\n',
)

# Aggregate capability and parity surfaces.
replace_once(
    "tools/run_engine_parity.py",
    '        "status",\n        "version",\n',
    '        "status",\n        "telemetry.metrics",\n        "version",\n',
)
replace_once(
    "tools/run_engine_parity_r24.py",
    "from verify_r24_scheduler_read_only import verify as verify_r24_scheduler_read_only\n",
    "from verify_r24_scheduler_read_only import verify as verify_r24_scheduler_read_only\n"
    "from verify_r24_telemetry_metrics import verify as verify_r24_telemetry_metrics\n",
)
replace_once(
    "tools/run_engine_parity_r24.py",
    "    scheduler_read_only = verify_r24_scheduler_read_only()\n",
    "    scheduler_read_only = verify_r24_scheduler_read_only()\n"
    "    telemetry_metrics = verify_r24_telemetry_metrics()\n",
)
replace_once(
    "tools/run_engine_parity_r24.py",
    '        raise RuntimeError("R24 scheduler read-only parity regression")\n'
    '    return {\n',
    '        raise RuntimeError("R24 scheduler read-only parity regression")\n'
    '    if (\n'
    '        telemetry_metrics.get("ok") is not True\n'
    '        or telemetry_metrics.get("phase") != "R24"\n'
    '        or telemetry_metrics.get("command") != "telemetry.metrics"\n'
    '        or telemetry_metrics.get("capability") != "telemetry.metrics"\n'
    '    ):\n'
    '        raise RuntimeError("R24 telemetry.metrics parity regression")\n'
    '    return {\n',
)
replace_once(
    "tools/run_engine_parity_r24.py",
    '        "scheduler_read_only": scheduler_read_only,\n'
    '        "claim": "RUST_READ_ONLY_CLI_PARITY_EXPANDED_R24",\n',
    '        "scheduler_read_only": scheduler_read_only,\n'
    '        "telemetry_metrics": telemetry_metrics,\n'
    '        "claim": "RUST_READ_ONLY_CLI_PARITY_EXPANDED_R24",\n',
)

# Full parity catalog and regression expectation.
catalog_path = ROOT / "contracts/parity/python-rust-full-parity-v1.json"
catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
feature_id = "route.telemetry.metrics"
if not any(row.get("id") == feature_id for row in catalog["features"]):
    marker = next(
        index
        for index, row in enumerate(catalog["features"])
        if row.get("id") == "cli.read-only.complete"
    )
    catalog["features"].insert(
        marker,
        {
            "id": feature_id,
            "target_phase": "R24",
            "category": "cli",
            "python_owner": "syntavra_runtime/telemetry_metrics_contract.py",
            "rust_owner": "crates/syntavra-cli/src/telemetry_metrics_contract.rs",
            "status": "PARITY_PROVEN",
            "mutation": "read-only",
            "contract": "contracts/cli/telemetry-metrics-read-only-v1.json",
            "parity_tests": ["tools/verify_r24_telemetry_metrics.py"],
        },
    )
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
replace_once(
    "tests/runtime/test_full_parity_catalog_v1.py",
    '        "route.scheduler.stats",\n'
    '    }\n',
    '        "route.scheduler.stats",\n'
    '        "route.telemetry.metrics",\n'
    '    }\n',
)

print("R24 telemetry metrics guarded assembly applied")
