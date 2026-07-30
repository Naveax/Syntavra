from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected one patch anchor, found {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "crates/syntavra-cli/src/main.rs",
    "mod read_only_cli_contract;\nmod state_layout_contract;",
    "mod read_only_cli_contract;\nmod scheduler_read_only_contract;\nmod state_layout_contract;",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "use read_only_cli_contract::result_json as static_cli_result_json;\nuse state_layout_contract::state_layout_json;",
    "use read_only_cli_contract::result_json as static_cli_result_json;\nuse scheduler_read_only_contract::{scheduler_list_json, scheduler_stats_json};\nuse state_layout_contract::state_layout_json;",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '    "  syntavra-rs plugins list\\n",\n    "  syntavra-rs state layout\\n",',
    '    "  syntavra-rs plugins list\\n",\n    "  syntavra-rs scheduler stats <state-root>\\n",\n    "  syntavra-rs scheduler list <state-root> <limit> <states-json-hex>\\n",\n    "  syntavra-rs state layout\\n",',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "    PipelineDescribe,\n    PluginsList,\n    StateLayout,",
    "    PipelineDescribe,\n    PluginsList,\n    SchedulerStats {\n        state_root: String,\n    },\n    SchedulerList {\n        state_root: String,\n        limit: usize,\n        states_hex: String,\n    },\n    StateLayout,",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '        [plugins, action] if plugins == "plugins" && action == "list" => Ok(Command::PluginsList),\n        [state, action] if state == "state" && action == "layout" => Ok(Command::StateLayout),',
    '''        [plugins, action] if plugins == "plugins" && action == "list" => Ok(Command::PluginsList),
        [scheduler, action, state_root] if scheduler == "scheduler" && action == "stats" => {
            Ok(Command::SchedulerStats {
                state_root: state_root.clone(),
            })
        }
        [scheduler, action, state_root, limit, states_hex]
            if scheduler == "scheduler" && action == "list" =>
        {
            let limit = limit
                .parse::<usize>()
                .map_err(|_| "SCHEDULER_READ_ONLY_LIMIT_INVALID".to_owned())?;
            Ok(Command::SchedulerList {
                state_root: state_root.clone(),
                limit,
                states_hex: states_hex.clone(),
            })
        }
        [state, action] if state == "state" && action == "layout" => Ok(Command::StateLayout),''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '        Command::PipelineDescribe => println!("{}", static_cli_result_json("pipeline.describe")?),\n        Command::PluginsList => println!("{}", static_cli_result_json("plugins.list")?),\n        Command::StateLayout => println!("{}", state_layout_json()),',
    '''        Command::PipelineDescribe => println!("{}", static_cli_result_json("pipeline.describe")?),
        Command::PluginsList => println!("{}", static_cli_result_json("plugins.list")?),
        Command::SchedulerStats { state_root } => {
            println!("{}", scheduler_stats_json(&state_root)?);
        }
        Command::SchedulerList {
            state_root,
            limit,
            states_hex,
        } => {
            let states_json = decode_hex(&states_hex)?;
            println!("{}", scheduler_list_json(&state_root, limit, &states_json)?);
        }
        Command::StateLayout => println!("{}", state_layout_json()),''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''    #[test]
    fn rejects_unknown_commands() {''',
    '''    #[test]
    fn parses_r24_scheduler_read_only_commands() {
        assert_eq!(
            parse_command(&args(&["scheduler", "stats", ".state"])),
            Ok(Command::SchedulerStats {
                state_root: ".state".to_owned(),
            })
        );
        assert_eq!(
            parse_command(&args(&[
                "scheduler",
                "list",
                ".state",
                "25",
                "5b22717565756564225d",
            ])),
            Ok(Command::SchedulerList {
                state_root: ".state".to_owned(),
                limit: 25,
                states_hex: "5b22717565756564225d".to_owned(),
            })
        );
    }

    #[test]
    fn rejects_unknown_commands() {''',
)

replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '''    Capability {
        name: "receipt.inspect",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "state.broker-live-snapshot",''',
    '''    Capability {
        name: "receipt.inspect",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "scheduler.list",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "scheduler.stats",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "state.broker-live-snapshot",''',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '    "capability=receipt.inspect|preview|read-only\\n",\n    "capability=state.broker-live-snapshot|preview|read-only\\n",',
    '    "capability=receipt.inspect|preview|read-only\\n",\n    "capability=scheduler.list|preview|read-only\\n",\n    "capability=scheduler.stats|preview|read-only\\n",\n    "capability=state.broker-live-snapshot|preview|read-only\\n",',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '        assert!(capabilities_json().contains("\\"name\\":\\"receipt.inspect\\""));\n        assert!(capabilities_json().contains("\\"name\\":\\"state.broker-live-snapshot\\""));',
    '        assert!(capabilities_json().contains("\\"name\\":\\"receipt.inspect\\""));\n        assert!(capabilities_json().contains("\\"name\\":\\"scheduler.list\\""));\n        assert!(capabilities_json().contains("\\"name\\":\\"scheduler.stats\\""));\n        assert!(capabilities_json().contains("\\"name\\":\\"state.broker-live-snapshot\\""));',
)

replace_once(
    "contracts/engine/descriptor.txt",
    "capability=receipt.inspect|preview|read-only\ncapability=state.broker-live-snapshot|preview|read-only\n",
    "capability=receipt.inspect|preview|read-only\ncapability=scheduler.list|preview|read-only\ncapability=scheduler.stats|preview|read-only\ncapability=state.broker-live-snapshot|preview|read-only\n",
)

replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "receipt.inspect",\n    "state.broker-live-snapshot",',
    '    "receipt.inspect",\n    "scheduler.list",\n    "scheduler.stats",\n    "state.broker-live-snapshot",',
)
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "capability=receipt.inspect|preview|read-only\\n"\n    "capability=state.broker-live-snapshot|preview|read-only\\n"',
    '    "capability=receipt.inspect|preview|read-only\\n"\n    "capability=scheduler.list|preview|read-only\\n"\n    "capability=scheduler.stats|preview|read-only\\n"\n    "capability=state.broker-live-snapshot|preview|read-only\\n"',
)

replace_once(
    "syntavra_runtime/engine_entry.py",
    "from .config_show_router_r24 import ConfigShowRouterR24",
    "from .scheduler_read_only_router_r24 import SchedulerReadOnlyRouterR24",
)
replace_once(
    "syntavra_runtime/engine_entry.py",
    '''def _read_only_request(rest: list[str]) -> tuple[str, str | None] | None:
    route = READ_ONLY_COMMANDS.get(tuple(rest))
    if route is not None:
        return route, None
    if len(rest) == 3 and rest[0] == "config" and rest[1] == "explain":
        return "config.explain", rest[2]
    return None
''',
    '''def _read_only_request(rest: list[str]) -> tuple[str, dict[str, Any]] | None:
    route = READ_ONLY_COMMANDS.get(tuple(rest))
    if route is not None:
        return route, {}
    if len(rest) == 3 and rest[0] == "config" and rest[1] == "explain":
        return "config.explain", {"explain_path": rest[2]}
    if rest == ["scheduler", "stats"]:
        return "scheduler.stats", {}
    if len(rest) >= 2 and rest[:2] == ["scheduler", "list"]:
        states: list[str] = []
        limit = 100
        index = 2
        while index < len(rest):
            value = rest[index]
            if value == "--state":
                if index + 1 >= len(rest):
                    raise EngineSelectionError(
                        "SCHEDULER_READ_ONLY_STATE_MISSING_R24",
                        "--state requires a scheduler state",
                    )
                states.append(rest[index + 1])
                index += 2
                continue
            if value.startswith("--state="):
                states.append(value.split("=", 1)[1])
                index += 1
                continue
            if value == "--limit":
                if index + 1 >= len(rest):
                    raise EngineSelectionError(
                        "SCHEDULER_READ_ONLY_LIMIT_MISSING_R24",
                        "--limit requires an integer",
                    )
                raw_limit = rest[index + 1]
                index += 2
            elif value.startswith("--limit="):
                raw_limit = value.split("=", 1)[1]
                index += 1
            else:
                return None
            try:
                limit = int(raw_limit)
            except ValueError as exc:
                raise EngineSelectionError(
                    "SCHEDULER_READ_ONLY_LIMIT_INVALID_R24",
                    "--limit requires an integer",
                ) from exc
        return "scheduler.list", {
            "scheduler_states": tuple(states),
            "scheduler_limit": limit,
        }
    return None
''',
)
replace_once(
    "syntavra_runtime/engine_entry.py",
    '''        if request is not None:
            route, explain_path = request
            router = ConfigShowRouterR24(selector, project_input_root=project)
            routed = router.route(
                route,
                cli_override=override,
                explain_path=explain_path,
            )''',
    '''        if request is not None:
            route, route_kwargs = request
            router = SchedulerReadOnlyRouterR24(selector, project_input_root=project)
            routed = router.route(
                route,
                cli_override=override,
                **route_kwargs,
            )''',
)

replace_once(
    "syntavra_runtime/engine_cli.py",
    "from .read_only_router_r24 import ReadOnlyCommandRouterR24",
    "from .scheduler_read_only_router_r24 import SchedulerReadOnlyRouterR24",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    '''    route.add_argument("--database-path")
    return parser''',
    '''    route.add_argument("--database-path")
    route.add_argument("--scheduler-state", action="append")
    route.add_argument("--scheduler-limit", type=int)
    return parser''',
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    "    router: ReadOnlyCommandRouterR24 | None = None,",
    "    router: SchedulerReadOnlyRouterR24 | None = None,",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    "    active_router = router or ReadOnlyCommandRouterR24(",
    "    active_router = router or SchedulerReadOnlyRouterR24(",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    '''            if args.database_path is not None:
                route_kwargs["database_path"] = args.database_path
            result = active_router.route(args.route_command, **route_kwargs)''',
    '''            if args.database_path is not None:
                route_kwargs["database_path"] = args.database_path
            if args.scheduler_state is not None:
                route_kwargs["scheduler_states"] = tuple(args.scheduler_state)
            if args.scheduler_limit is not None:
                route_kwargs["scheduler_limit"] = args.scheduler_limit
            result = active_router.route(args.route_command, **route_kwargs)''',
)

replace_once(
    "syntavra_runtime/unified_cli.py",
    "from .read_only_cli_contract import pipeline_description, plugin_inventory",
    "from .read_only_cli_contract import pipeline_description, plugin_inventory\nfrom .scheduler_read_only_contract import scheduler_read_only_result",
)
replace_once(
    "syntavra_runtime/unified_cli.py",
    '''    if args.command == "config" and args.action == "show":
        wire = discover_live_config_wire(project_root=project)
        _emit(show_result(resolve_config_wire(wire)))
        return 0

    evidence = EvidenceStore(state / "evidence", project_id=project_id)''',
    '''    if args.command == "config" and args.action == "show":
        wire = discover_live_config_wire(project_root=project)
        _emit(show_result(resolve_config_wire(wire)))
        return 0
    if args.command == "scheduler" and args.action in {"stats", "list"}:
        _emit(
            scheduler_read_only_result(
                state,
                f"scheduler.{args.action}",
                states=tuple(args.state) if args.action == "list" else (),
                limit=args.limit if args.action == "list" else 100,
            )
        )
        return 0

    evidence = EvidenceStore(state / "evidence", project_id=project_id)''',
)

print("R24 scheduler read-only source patch applied")
