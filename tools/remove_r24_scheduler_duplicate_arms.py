from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "crates" / "syntavra-cli" / "src" / "main.rs"
text = path.read_text(encoding="utf-8")

parse_block = '''        [scheduler, action, state_root] if scheduler == "scheduler" && action == "stats" => {
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
'''
run_block = '''        Command::SchedulerStats { state_root } => {
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
'''

if text.count(parse_block) != 1:
    raise SystemExit(f"expected one duplicate parse block, found {text.count(parse_block)}")
if text.count(run_block) != 1:
    raise SystemExit(f"expected one duplicate run block, found {text.count(run_block)}")
text = text.replace(parse_block, "", 1).replace(run_block, "", 1)
path.write_text(text, encoding="utf-8", newline="\n")
print("duplicate scheduler match arms removed")
