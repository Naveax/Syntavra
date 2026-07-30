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
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "crates/syntavra-cli/src/scheduler_read_only_contract.rs",
    '''fn inspect(
    state_root: &Path,
    stats: bool,
    states: &[String],
    limit: usize,
) -> Result<Value, String> {''',
    '''fn inspect(
    state_root: &Path,
    stats_mode: bool,
    state_filters: &[String],
    limit: usize,
) -> Result<Value, String> {''',
)
replace_once(
    "crates/syntavra-cli/src/scheduler_read_only_contract.rs",
    '''        return Ok(empty_result(stats));
    }
    let identity_before''',
    '''        return Ok(empty_result(stats_mode));
    }
    let identity_before''',
)
replace_once(
    "crates/syntavra-cli/src/scheduler_read_only_contract.rs",
    '''    let result = if stats {''',
    '''    let result = if stats_mode {''',
)
replace_once(
    "crates/syntavra-cli/src/scheduler_read_only_contract.rs",
    '''            let (state, count) = row.map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
            counts.insert(state, json!(count));''',
    '''            let (state_name, count) =
                row.map_err(|_| "SCHEDULER_READ_ONLY_QUERY_FAILED".to_owned())?;
            counts.insert(state_name, json!(count));''',
)
replace_once(
    "crates/syntavra-cli/src/scheduler_read_only_contract.rs",
    '''        if !states.is_empty() {
            query.push_str(" WHERE state IN (");
            query.push_str(&vec!["?"; states.len()].join(","));''',
    '''        if !state_filters.is_empty() {
            query.push_str(" WHERE state IN (");
            query.push_str(&vec!["?"; state_filters.len()].join(","));''',
)
replace_once(
    "crates/syntavra-cli/src/scheduler_read_only_contract.rs",
    '''        let mut parameters = states
            .iter()
            .map(|value| rusqlite::types::Value::Text(value.clone()))
            .collect::<Vec<_>>();
        parameters.push(rusqlite::types::Value::Integer(
            limit.clamp(1, MAXIMUM_LIMIT) as i64,
        ));''',
    '''        let mut parameters = state_filters
            .iter()
            .map(|value| rusqlite::types::Value::Text(value.clone()))
            .collect::<Vec<_>>();
        let bounded_limit = i64::try_from(limit.clamp(1, MAXIMUM_LIMIT))
            .map_err(|_| "SCHEDULER_READ_ONLY_LIMIT_INVALID".to_owned())?;
        parameters.push(rusqlite::types::Value::Integer(bounded_limit));''',
)

replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''fn parse_command(arguments: &[String]) -> Result<Command, String> {
    match arguments {''',
    '''fn parse_scheduler_command(arguments: &[String]) -> Result<Option<Command>, String> {
    match arguments {
        [scheduler, action, state_root] if scheduler == "scheduler" && action == "stats" => {
            Ok(Some(Command::SchedulerStats {
                state_root: state_root.clone(),
            }))
        }
        [scheduler, action, state_root, limit, states_hex]
            if scheduler == "scheduler" && action == "list" =>
        {
            let limit = limit
                .parse::<usize>()
                .map_err(|_| "SCHEDULER_READ_ONLY_LIMIT_INVALID".to_owned())?;
            Ok(Some(Command::SchedulerList {
                state_root: state_root.clone(),
                limit,
                states_hex: states_hex.clone(),
            }))
        }
        _ => Ok(None),
    }
}

fn parse_command(arguments: &[String]) -> Result<Command, String> {
    if let Some(command) = parse_scheduler_command(arguments)? {
        return Ok(command);
    }
    match arguments {''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        [scheduler, action, state_root] if scheduler == "scheduler" && action == "stats" => {
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
''',
    "",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''fn run(command: Command) -> Result<(), String> {
    match command {''',
    '''fn run_scheduler(command: &Command) -> Result<bool, String> {
    match command {
        Command::SchedulerStats { state_root } => {
            println!("{}", scheduler_stats_json(state_root)?);
            Ok(true)
        }
        Command::SchedulerList {
            state_root,
            limit,
            states_hex,
        } => {
            let states_json = decode_hex(states_hex)?;
            println!("{}", scheduler_list_json(state_root, *limit, &states_json)?);
            Ok(true)
        }
        _ => Ok(false),
    }
}

fn run(command: Command) -> Result<(), String> {
    if run_scheduler(&command)? {
        return Ok(());
    }
    match command {''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        Command::SchedulerStats { state_root } => {
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
''',
    "",
)

print("R24 scheduler Clippy repair applied")
