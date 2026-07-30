#![forbid(unsafe_code)]

mod broker_live_snapshot_contract;
mod broker_snapshot_contract;
mod config_contract;
mod read_only_cli_contract;
mod scheduler_read_only_contract;
mod state_layout_contract;
mod state_receipt_contract;
mod state_snapshot_contract;

use std::env;
use std::fmt::Write as _;
use std::process::ExitCode;

use broker_live_snapshot_contract::snapshot_live_broker_database_json;
use broker_snapshot_contract::snapshot_broker_database_json;
use config_contract::{
    default_config_wire, explain_config_wire_json, resolve_config_wire, snapshot_json, status_json,
};
use read_only_cli_contract::result_json as static_cli_result_json;
use scheduler_read_only_contract::{scheduler_list_json, scheduler_stats_json};
use state_layout_contract::state_layout_json;
use state_receipt_contract::inspect_receipt_json;
use state_snapshot_contract::inspect_state_root_json;
use syntavra_contracts::{
    capabilities_json, CONTRACT_DESCRIPTOR, CONTRACT_VERSION, ENGINE_NAME, ENGINE_STABILITY,
    PRODUCT_NAME, PRODUCT_VERSION, RELEASE_CHANNEL,
};
use syntavra_core::{
    bytes_to_hex, canonical_manifest_bytes, manifest_digest_hex, normalize_repository_path,
    sha256_hex,
};

const USAGE: &str = concat!(
    "Syntavra native Rust engine (experimental)\n\n",
    "USAGE:\n",
    "  syntavra-rs version\n",
    "  syntavra-rs status [config-wire-hex]\n",
    "  syntavra-rs config explain <config-wire-hex> <path-utf8-hex>\n",
    "  syntavra-rs config resolve <config-wire-hex>\n",
    "  syntavra-rs config show <config-wire-hex>\n",
    "  syntavra-rs pipeline describe\n",
    "  syntavra-rs plugins list\n",
    "  syntavra-rs scheduler stats <state-root>\n",
    "  syntavra-rs scheduler list <state-root> <limit> <states-json-hex>\n",
    "  syntavra-rs state layout\n",
    "  syntavra-rs state inspect <expected-project-id> <project-root>\n",
    "  syntavra-rs state broker-live-snapshot <expected-project-id> <project-root> <database-path>\n",
    "  syntavra-rs state broker-snapshot <expected-project-id> <project-root> <database-path>\n",
    "  syntavra-rs receipt inspect <expected-project-id> <receipt-wire-hex>\n",
    "  syntavra-rs engine capabilities\n",
    "  syntavra-rs engine contract-hash\n",
    "  syntavra-rs primitive sha256 <input-hex>\n",
    "  syntavra-rs primitive canonicalize <repository-path> <input-hex>\n",
    "  syntavra-rs primitive manifest-digest <repository-path> <input-hex>\n",
    "  syntavra-rs primitive normalize-path <repository-path>\n",
);

#[derive(Debug, Clone, PartialEq, Eq)]
enum Command {
    Version,
    Status(Option<String>),
    ConfigExplain {
        wire_hex: String,
        path_hex: String,
    },
    ConfigResolve(String),
    ConfigShow(String),
    PipelineDescribe,
    PluginsList,
    SchedulerStats {
        state_root: String,
    },
    SchedulerList {
        state_root: String,
        limit: usize,
        states_hex: String,
    },
    StateLayout,
    BrokerLiveSnapshot {
        expected_project_id: String,
        project_root: String,
        database_path: String,
    },
    BrokerSnapshot {
        expected_project_id: String,
        project_root: String,
        database_path: String,
    },
    StateInspect {
        expected_project_id: String,
        project_root: String,
    },
    ReceiptInspect {
        expected_project_id: String,
        wire_hex: String,
    },
    Capabilities,
    ContractHash,
    PrimitiveSha256(String),
    PrimitiveCanonicalize {
        path: String,
        input_hex: String,
    },
    PrimitiveManifestDigest {
        path: String,
        input_hex: String,
    },
    PrimitiveNormalizePath(String),
    Help,
}

fn parse_scheduler_command(arguments: &[String]) -> Result<Option<Command>, String> {
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
    match arguments {
        [] => Ok(Command::Help),
        [value] if value == "version" || value == "--version" => Ok(Command::Version),
        [value] if value == "status" => Ok(Command::Status(None)),
        [value, wire] if value == "status" => Ok(Command::Status(Some(wire.clone()))),
        [config, action, wire_hex, path_hex] if config == "config" && action == "explain" => {
            Ok(Command::ConfigExplain {
                wire_hex: wire_hex.clone(),
                path_hex: path_hex.clone(),
            })
        }
        [config, action, wire] if config == "config" && action == "resolve" => {
            Ok(Command::ConfigResolve(wire.clone()))
        }
        [config, action, wire] if config == "config" && action == "show" => {
            Ok(Command::ConfigShow(wire.clone()))
        }
        [pipeline, action] if pipeline == "pipeline" && action == "describe" => {
            Ok(Command::PipelineDescribe)
        }
        [plugins, action] if plugins == "plugins" && action == "list" => Ok(Command::PluginsList),
        [state, action] if state == "state" && action == "layout" => Ok(Command::StateLayout),
        [state, action, expected_project_id, project_root, database_path]
            if state == "state" && action == "broker-live-snapshot" =>
        {
            Ok(Command::BrokerLiveSnapshot {
                expected_project_id: expected_project_id.clone(),
                project_root: project_root.clone(),
                database_path: database_path.clone(),
            })
        }
        [state, action, expected_project_id, project_root, database_path]
            if state == "state" && action == "broker-snapshot" =>
        {
            Ok(Command::BrokerSnapshot {
                expected_project_id: expected_project_id.clone(),
                project_root: project_root.clone(),
                database_path: database_path.clone(),
            })
        }
        [state, action, expected_project_id, project_root]
            if state == "state" && action == "inspect" =>
        {
            Ok(Command::StateInspect {
                expected_project_id: expected_project_id.clone(),
                project_root: project_root.clone(),
            })
        }
        [receipt, action, expected_project_id, wire_hex]
            if receipt == "receipt" && action == "inspect" =>
        {
            Ok(Command::ReceiptInspect {
                expected_project_id: expected_project_id.clone(),
                wire_hex: wire_hex.clone(),
            })
        }
        [value] if value == "help" || value == "--help" || value == "-h" => Ok(Command::Help),
        [engine, action] if engine == "engine" && action == "capabilities" => {
            Ok(Command::Capabilities)
        }
        [engine, action] if engine == "engine" && action == "contract-hash" => {
            Ok(Command::ContractHash)
        }
        [primitive, action, input_hex] if primitive == "primitive" && action == "sha256" => {
            Ok(Command::PrimitiveSha256(input_hex.clone()))
        }
        [primitive, action, path] if primitive == "primitive" && action == "normalize-path" => {
            Ok(Command::PrimitiveNormalizePath(path.clone()))
        }
        [primitive, action, path, input_hex]
            if primitive == "primitive" && action == "canonicalize" =>
        {
            Ok(Command::PrimitiveCanonicalize {
                path: path.clone(),
                input_hex: input_hex.clone(),
            })
        }
        [primitive, action, path, input_hex]
            if primitive == "primitive" && action == "manifest-digest" =>
        {
            Ok(Command::PrimitiveManifestDigest {
                path: path.clone(),
                input_hex: input_hex.clone(),
            })
        }
        _ => Err(format!("unsupported arguments: {}", arguments.join(" "))),
    }
}

fn version_json() -> String {
    format!(
        concat!(
            "{{\"product\":\"{}\",",
            "\"product_version\":\"{}\",",
            "\"release_channel\":\"{}\",",
            "\"engine\":\"{}\",",
            "\"engine_stability\":\"{}\",",
            "\"contract_version\":{}}}"
        ),
        PRODUCT_NAME,
        PRODUCT_VERSION,
        RELEASE_CHANNEL,
        ENGINE_NAME,
        ENGINE_STABILITY,
        CONTRACT_VERSION
    )
}

fn contract_hash_json() -> String {
    format!(
        concat!(
            "{{\"engine\":\"{}\",",
            "\"contract_version\":{},",
            "\"algorithm\":\"sha256\",",
            "\"contract_hash\":\"{}\"}}"
        ),
        ENGINE_NAME,
        CONTRACT_VERSION,
        sha256_hex(CONTRACT_DESCRIPTOR.as_bytes())
    )
}

fn json_string(value: &str) -> String {
    let mut output = String::with_capacity(value.len() + 2);
    output.push('"');
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            value if value.is_control() => {
                write!(&mut output, "\\u{:04x}", u32::from(value))
                    .expect("writing to a String cannot fail");
            }
            value => output.push(value),
        }
    }
    output.push('"');
    output
}

fn hex_nibble(value: u8) -> Result<u8, String> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        b'A'..=b'F' => Ok(value - b'A' + 10),
        _ => Err("HEX_INVALID".to_owned()),
    }
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("HEX_ODD_LENGTH".to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        output.push((hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?);
    }
    Ok(output)
}

fn config_wire(encoded: Option<String>) -> Result<Vec<u8>, String> {
    encoded.map_or_else(
        || Ok(default_config_wire().to_vec()),
        |value| decode_hex(&value),
    )
}

fn run_config_explain(wire_hex: &str, path_hex: &str) -> Result<(), String> {
    let wire = decode_hex(wire_hex)?;
    let path = decode_hex(path_hex)?;
    println!("{}", explain_config_wire_json(&wire, &path)?);
    Ok(())
}

fn run_config_snapshot(encoded: &str) -> Result<(), String> {
    let wire = decode_hex(encoded)?;
    let snapshot = resolve_config_wire(&wire)?;
    println!("{}", snapshot_json(&snapshot)?);
    Ok(())
}

fn run_scheduler(command: &Command) -> Result<bool, String> {
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
    match command {
        Command::Version => println!("{}", version_json()),
        Command::Status(encoded) => {
            let wire = config_wire(encoded)?;
            let snapshot = resolve_config_wire(&wire)?;
            println!("{}", status_json(&snapshot));
        }
        Command::ConfigExplain { wire_hex, path_hex } => {
            run_config_explain(&wire_hex, &path_hex)?;
        }
        Command::ConfigResolve(encoded) | Command::ConfigShow(encoded) => {
            run_config_snapshot(&encoded)?;
        }
        Command::PipelineDescribe => println!("{}", static_cli_result_json("pipeline.describe")?),
        Command::PluginsList => println!("{}", static_cli_result_json("plugins.list")?),
        Command::StateLayout => println!("{}", state_layout_json()),
        Command::BrokerLiveSnapshot {
            expected_project_id,
            project_root,
            database_path,
        } => println!(
            "{}",
            snapshot_live_broker_database_json(
                &project_root,
                &database_path,
                &expected_project_id,
            )?
        ),
        Command::BrokerSnapshot {
            expected_project_id,
            project_root,
            database_path,
        } => println!(
            "{}",
            snapshot_broker_database_json(&project_root, &database_path, &expected_project_id,)?
        ),
        Command::StateInspect {
            expected_project_id,
            project_root,
        } => println!(
            "{}",
            inspect_state_root_json(&project_root, &expected_project_id)?
        ),
        Command::ReceiptInspect {
            expected_project_id,
            wire_hex,
        } => {
            let wire = decode_hex(&wire_hex)?;
            println!("{}", inspect_receipt_json(&wire, &expected_project_id)?);
        }
        Command::Capabilities => println!("{}", capabilities_json()),
        Command::ContractHash => println!("{}", contract_hash_json()),
        Command::PrimitiveSha256(input_hex) => {
            let input = decode_hex(&input_hex)?;
            println!(
                "{{\"algorithm\":\"sha256\",\"digest\":\"{}\",\"input_hex\":\"{}\"}}",
                sha256_hex(&input),
                bytes_to_hex(&input)
            );
        }
        Command::PrimitiveCanonicalize { path, input_hex } => {
            let input = decode_hex(&input_hex)?;
            let normalized =
                normalize_repository_path(&path).map_err(|error| error.code().to_owned())?;
            let canonical = canonical_manifest_bytes(&normalized, &input)
                .map_err(|error| error.code().to_owned())?;
            println!(
                concat!(
                    "{{\"path\":{},",
                    "\"canonical_hex\":\"{}\",",
                    "\"digest\":\"{}\"}}"
                ),
                json_string(&normalized),
                bytes_to_hex(&canonical),
                sha256_hex(&canonical)
            );
        }
        Command::PrimitiveManifestDigest { path, input_hex } => {
            let input = decode_hex(&input_hex)?;
            let normalized =
                normalize_repository_path(&path).map_err(|error| error.code().to_owned())?;
            let digest = manifest_digest_hex(&normalized, &input)
                .map_err(|error| error.code().to_owned())?;
            println!(
                "{{\"path\":{},\"algorithm\":\"sha256\",\"digest\":\"{}\"}}",
                json_string(&normalized),
                digest
            );
        }
        Command::PrimitiveNormalizePath(path) => {
            let normalized =
                normalize_repository_path(&path).map_err(|error| error.code().to_owned())?;
            println!("{{\"path\":{}}}", json_string(&normalized));
        }
        Command::SchedulerStats { .. } | Command::SchedulerList { .. } => {
            unreachable!("scheduler commands are handled before the main match")
        }
        Command::Help => print!("{USAGE}"),
    }
    Ok(())
}

fn main() -> ExitCode {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    match parse_command(&arguments).and_then(run) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}\n\n{USAGE}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{contract_hash_json, decode_hex, parse_command, version_json, Command};

    fn args(values: &[&str]) -> Vec<String> {
        values.iter().map(ToString::to_string).collect()
    }

    #[test]
    fn parses_initial_commands() {
        assert_eq!(parse_command(&args(&["version"])), Ok(Command::Version));
        assert_eq!(
            parse_command(&args(&["engine", "capabilities"])),
            Ok(Command::Capabilities)
        );
        assert_eq!(
            parse_command(&args(&["engine", "contract-hash"])),
            Ok(Command::ContractHash)
        );
    }

    #[test]
    fn parses_r5_primitive_commands() {
        assert_eq!(
            parse_command(&args(&["primitive", "sha256", "616263"])),
            Ok(Command::PrimitiveSha256("616263".to_owned()))
        );
        assert_eq!(
            parse_command(&args(&["primitive", "normalize-path", r"src\main.py"])),
            Ok(Command::PrimitiveNormalizePath(r"src\main.py".to_owned()))
        );
        assert!(matches!(
            parse_command(&args(&[
                "primitive",
                "canonicalize",
                "docs/readme.md",
                "610d0a"
            ])),
            Ok(Command::PrimitiveCanonicalize { .. })
        ));
    }

    #[test]
    fn parses_r6_config_and_status_commands() {
        assert_eq!(parse_command(&args(&["status"])), Ok(Command::Status(None)));
        assert_eq!(
            parse_command(&args(&["status", "00"])),
            Ok(Command::Status(Some("00".to_owned())))
        );
        assert_eq!(
            parse_command(&args(&["config", "resolve", "00"])),
            Ok(Command::ConfigResolve("00".to_owned()))
        );
        assert_eq!(
            parse_command(&args(&["config", "show", "00"])),
            Ok(Command::ConfigShow("00".to_owned()))
        );
        assert_eq!(
            parse_command(&args(&[
                "config",
                "explain",
                "00",
                "72756e74696d652e70726f66696c65"
            ])),
            Ok(Command::ConfigExplain {
                wire_hex: "00".to_owned(),
                path_hex: "72756e74696d652e70726f66696c65".to_owned(),
            })
        );
    }

    #[test]
    fn parses_r7_state_and_receipt_commands() {
        assert_eq!(
            parse_command(&args(&["state", "layout"])),
            Ok(Command::StateLayout)
        );
        assert_eq!(
            parse_command(&args(&["state", "inspect", "aa", "."])),
            Ok(Command::StateInspect {
                expected_project_id: "aa".to_owned(),
                project_root: ".".to_owned(),
            })
        );
        assert_eq!(
            parse_command(&args(&[
                "state",
                "broker-live-snapshot",
                "aa",
                ".",
                ".syntavra/runtime-v3/broker.sqlite3",
            ])),
            Ok(Command::BrokerLiveSnapshot {
                expected_project_id: "aa".to_owned(),
                project_root: ".".to_owned(),
                database_path: ".syntavra/runtime-v3/broker.sqlite3".to_owned(),
            })
        );
        assert_eq!(
            parse_command(&args(&[
                "state",
                "broker-snapshot",
                "aa",
                ".",
                ".syntavra/runtime-v3/broker.sqlite3",
            ])),
            Ok(Command::BrokerSnapshot {
                expected_project_id: "aa".to_owned(),
                project_root: ".".to_owned(),
                database_path: ".syntavra/runtime-v3/broker.sqlite3".to_owned(),
            })
        );
        assert_eq!(
            parse_command(&args(&["receipt", "inspect", "aa", "00"])),
            Ok(Command::ReceiptInspect {
                expected_project_id: "aa".to_owned(),
                wire_hex: "00".to_owned(),
            })
        );
    }

    #[test]
    fn parses_r24_static_read_only_cli_commands() {
        assert_eq!(
            parse_command(&args(&["pipeline", "describe"])),
            Ok(Command::PipelineDescribe)
        );
        assert_eq!(
            parse_command(&args(&["plugins", "list"])),
            Ok(Command::PluginsList)
        );
    }

    #[test]
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
    fn rejects_unknown_commands() {
        assert!(parse_command(&args(&["unknown"])).is_err());
    }

    #[test]
    fn version_output_preserves_locked_identity() {
        let value = version_json();
        assert!(value.contains("\"product_version\":\"0.0.1\""));
        assert!(value.contains("\"release_channel\":\"pre-release\""));
    }

    #[test]
    fn contract_hash_is_sha256() {
        let value = contract_hash_json();
        let marker = "\"contract_hash\":\"";
        let start = value.find(marker).expect("hash marker") + marker.len();
        let end = value[start..].find('"').expect("hash terminator") + start;
        assert_eq!(value[start..end].len(), 64);
    }

    #[test]
    fn decodes_hex_case_insensitively() {
        assert_eq!(decode_hex("00aF"), Ok(vec![0x00, 0xaf]));
        assert_eq!(decode_hex("0"), Err("HEX_ODD_LENGTH".to_owned()));
        assert_eq!(decode_hex("zz"), Err("HEX_INVALID".to_owned()));
    }
}
