from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one patch anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "crates/syntavra-cli/src/main.rs",
    "mod config_contract;\nmod read_only_cli_contract;",
    "mod config_contract;\nmod migration_plan_read_only_contract;\nmod read_only_cli_contract;",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "use config_contract::{\n    default_config_wire, explain_config_wire_json, resolve_config_wire, snapshot_json, status_json,\n};\nuse read_only_cli_contract::result_json as static_cli_result_json;",
    "use config_contract::{\n    default_config_wire, explain_config_wire_json, resolve_config_wire, snapshot_json, status_json,\n};\nuse migration_plan_read_only_contract::migration_plan_json;\nuse read_only_cli_contract::result_json as static_cli_result_json;",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '    "  syntavra-rs config show <config-wire-hex>\\n",\n    "  syntavra-rs pipeline describe\\n",',
    '    "  syntavra-rs config show <config-wire-hex>\\n",\n    "  syntavra-rs migration plan <project-root> <database-path-utf8-hex>\\n",\n    "  syntavra-rs pipeline describe\\n",',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "    ConfigShow(String),\n    PipelineDescribe,",
    "    ConfigShow(String),\n    MigrationPlan {\n        project_root: String,\n        database_path_hex: String,\n    },\n    PipelineDescribe,",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "fn parse_command(arguments: &[String]) -> Result<Command, String> {\n    if let Some(command) = parse_scheduler_command(arguments)? {",
    '''fn parse_migration_command(arguments: &[String]) -> Option<Command> {
    match arguments {
        [migration, action, project_root, database_path_hex]
            if migration == "migration" && action == "plan" =>
        {
            Some(Command::MigrationPlan {
                project_root: project_root.clone(),
                database_path_hex: database_path_hex.clone(),
            })
        }
        _ => None,
    }
}

fn parse_command(arguments: &[String]) -> Result<Command, String> {
    if let Some(command) = parse_migration_command(arguments) {
        return Ok(command);
    }
    if let Some(command) = parse_scheduler_command(arguments)? {''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "fn run_scheduler(command: &Command) -> Result<bool, String> {",
    '''fn run_migration(command: &Command) -> Result<bool, String> {
    match command {
        Command::MigrationPlan {
            project_root,
            database_path_hex,
        } => {
            let database_path = String::from_utf8(decode_hex(database_path_hex)?)
                .map_err(|_| "MIGRATION_PLAN_DATABASE_PATH_UTF8_INVALID".to_owned())?;
            println!("{}", migration_plan_json(project_root, &database_path)?);
            Ok(true)
        }
        _ => Ok(false),
    }
}

fn run_scheduler(command: &Command) -> Result<bool, String> {''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "    if run_scheduler(&command)? || run_primitive(&command)? {",
    "    if run_migration(&command)? || run_scheduler(&command)? || run_primitive(&command)? {",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        Command::SchedulerStats { .. }
        | Command::SchedulerList { .. }
        | Command::PrimitiveSha256(_)''',
    '''        Command::MigrationPlan { .. }
        | Command::SchedulerStats { .. }
        | Command::SchedulerList { .. }
        | Command::PrimitiveSha256(_)''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''    #[test]
    fn parses_r5_primitive_commands() {''',
    '''    #[test]
    fn parses_r24_migration_plan_command() {
        assert_eq!(
            parse_command(&args(&["migration", "plan", ".", "64622e73716c69746533"])),
            Ok(Command::MigrationPlan {
                project_root: ".".to_owned(),
                database_path_hex: "64622e73716c69746533".to_owned(),
            })
        );
    }

    #[test]
    fn parses_r5_primitive_commands() {''',
)

replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '''    Capability {
        name: "pipeline.describe",
        maturity: "preview",
        mutation: "read-only",
    },''',
    '''    Capability {
        name: "migration.plan",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "pipeline.describe",
        maturity: "preview",
        mutation: "read-only",
    },''',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '    "capability=pipeline.describe|preview|read-only\\n",',
    '    "capability=migration.plan|preview|read-only\\n",\n    "capability=pipeline.describe|preview|read-only\\n",',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '        assert!(capabilities_json().contains("\\"name\\":\\"pipeline.describe\\""));',
    '        assert!(capabilities_json().contains("\\"name\\":\\"migration.plan\\""));\n        assert!(capabilities_json().contains("\\"name\\":\\"pipeline.describe\\""));',
)
replace_once(
    "contracts/engine/descriptor.txt",
    "capability=pipeline.describe|preview|read-only\n",
    "capability=migration.plan|preview|read-only\ncapability=pipeline.describe|preview|read-only\n",
)
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "pipeline.describe",\n    "plugins.list",',
    '    "migration.plan",\n    "pipeline.describe",\n    "plugins.list",',
)
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "capability=pipeline.describe|preview|read-only\\n"',
    '    "capability=migration.plan|preview|read-only\\n"\n    "capability=pipeline.describe|preview|read-only\\n"',
)

replace_once(
    "syntavra_runtime/engine_entry.py",
    "from .scheduler_read_only_router_r24 import SchedulerReadOnlyRouterR24",
    "from .migration_plan_router_r24 import MigrationPlanRouterR24",
)
replace_once(
    "syntavra_runtime/engine_entry.py",
    '''    if rest == ["scheduler", "stats"]:
        return "scheduler.stats", {}''',
    '''    if len(rest) == 3 and rest[:2] == ["migrate", "plan"]:
        return "migration.plan", {"migration_database": rest[2]}
    if rest == ["scheduler", "stats"]:
        return "scheduler.stats", {}''',
)
replace_once(
    "syntavra_runtime/engine_entry.py",
    "            router = SchedulerReadOnlyRouterR24(selector, project_input_root=project)",
    "            router = MigrationPlanRouterR24(selector, project_input_root=project)",
)

replace_once(
    "syntavra_runtime/engine_cli.py",
    "from .scheduler_read_only_router_r24 import SchedulerReadOnlyRouterR24",
    "from .migration_plan_router_r24 import MigrationPlanRouterR24",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    '''    route.add_argument("--scheduler-limit", type=int)
    return parser''',
    '''    route.add_argument("--scheduler-limit", type=int)
    route.add_argument("--migration-database")
    return parser''',
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    "    router: SchedulerReadOnlyRouterR24 | None = None,",
    "    router: MigrationPlanRouterR24 | None = None,",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    "    active_router = router or SchedulerReadOnlyRouterR24(",
    "    active_router = router or MigrationPlanRouterR24(",
)
replace_once(
    "syntavra_runtime/engine_cli.py",
    '''            if args.scheduler_limit is not None:
                route_kwargs["scheduler_limit"] = args.scheduler_limit
            result = active_router.route(args.route_command, **route_kwargs)''',
    '''            if args.scheduler_limit is not None:
                route_kwargs["scheduler_limit"] = args.scheduler_limit
            if args.migration_database is not None:
                route_kwargs["migration_database"] = args.migration_database
            result = active_router.route(args.route_command, **route_kwargs)''',
)

replace_once(
    "syntavra_runtime/unified_cli.py",
    "from .migrations import MigrationManager",
    "from .migration_plan_read_only_contract import migration_plan_read_only_result\nfrom .migrations import MigrationManager",
)
replace_once(
    "syntavra_runtime/unified_cli.py",
    '''    if args.command == "scheduler" and args.action in {"stats", "list"}:
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
    '''    if args.command == "scheduler" and args.action in {"stats", "list"}:
        _emit(
            scheduler_read_only_result(
                state,
                f"scheduler.{args.action}",
                states=tuple(args.state) if args.action == "list" else (),
                limit=args.limit if args.action == "list" else 100,
            )
        )
        return 0
    if args.command == "migrate" and args.action == "plan":
        _emit(migration_plan_read_only_result(project, args.database))
        return 0

    evidence = EvidenceStore(state / "evidence", project_id=project_id)''',
)

print("R24 migration plan source patch applied")
