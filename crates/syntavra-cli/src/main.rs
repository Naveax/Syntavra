#![forbid(unsafe_code)]

use std::env;
use std::process::ExitCode;
use syntavra_contracts::{
    capabilities_json, CONTRACT_DESCRIPTOR, CONTRACT_VERSION, ENGINE_NAME, ENGINE_STABILITY,
    PRODUCT_NAME, PRODUCT_VERSION, RELEASE_CHANNEL,
};
use syntavra_core::sha256_hex;

const USAGE: &str = concat!(
    "Syntavra native Rust engine (experimental)\n\n",
    "USAGE:\n",
    "  syntavra-rs version\n",
    "  syntavra-rs engine capabilities\n",
    "  syntavra-rs engine contract-hash\n",
);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Command {
    Version,
    Capabilities,
    ContractHash,
    Help,
}

fn parse_command(arguments: &[String]) -> Result<Command, String> {
    match arguments {
        [] => Ok(Command::Help),
        [value] if value == "version" || value == "--version" => Ok(Command::Version),
        [value] if value == "help" || value == "--help" || value == "-h" => Ok(Command::Help),
        [engine, action] if engine == "engine" && action == "capabilities" => {
            Ok(Command::Capabilities)
        }
        [engine, action] if engine == "engine" && action == "contract-hash" => {
            Ok(Command::ContractHash)
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

fn run(command: Command) -> ExitCode {
    match command {
        Command::Version => println!("{}", version_json()),
        Command::Capabilities => println!("{}", capabilities_json()),
        Command::ContractHash => println!("{}", contract_hash_json()),
        Command::Help => print!("{USAGE}"),
    }
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    match parse_command(&arguments) {
        Ok(command) => run(command),
        Err(error) => {
            eprintln!("{error}\n\n{USAGE}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{contract_hash_json, parse_command, version_json, Command};

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
    fn rejects_unknown_commands() {
        assert!(parse_command(&args(&["status"])).is_err());
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
}
