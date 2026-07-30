#![forbid(unsafe_code)]

#[allow(dead_code)]
#[path = "../config_contract.rs"]
mod config_contract;
#[path = "../config_last_good_apply.rs"]
mod config_last_good_apply;
#[path = "../config_last_good_plan.rs"]
mod config_last_good_plan;
#[allow(dead_code)]
#[path = "../state_snapshot_contract.rs"]
mod state_snapshot_contract;

use std::env;
use std::process::ExitCode;

use config_last_good_apply::apply_json;

const USAGE: &str = concat!(
    "USAGE: syntavra-config-lifecycle-apply ",
    "<expected-project-id> <project-root> <config-wire-hex> [--fault <point>]"
);

fn hex_nibble(value: u8) -> Result<u8, String> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        b'A'..=b'F' => Ok(value - b'A' + 10),
        _ => Err("CONFIG_LIFECYCLE_WIRE_HEX_INVALID".to_owned()),
    }
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("CONFIG_LIFECYCLE_WIRE_HEX_INVALID".to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        output.push((hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?);
    }
    Ok(output)
}

fn parse_arguments(arguments: &[String]) -> Result<(&str, &str, Vec<u8>, Option<&str>), String> {
    match arguments {
        [expected_project_id, project_root, wire_hex] => Ok((
            expected_project_id,
            project_root,
            decode_hex(wire_hex)?,
            None,
        )),
        [expected_project_id, project_root, wire_hex, flag, point] if flag == "--fault" => Ok((
            expected_project_id,
            project_root,
            decode_hex(wire_hex)?,
            Some(point.as_str()),
        )),
        _ => Err("CONFIG_LIFECYCLE_ARGUMENTS_INVALID".to_owned()),
    }
}

fn run(arguments: &[String]) -> Result<(), String> {
    let (expected_project_id, project_root, wire, fault) = parse_arguments(arguments)?;
    let receipt = apply_json(project_root, expected_project_id, &wire, fault, None)
        .map_err(|error| error.code)?;
    println!("{receipt}");
    Ok(())
}

fn main() -> ExitCode {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    match run(&arguments) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}\n\n{USAGE}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{decode_hex, parse_arguments};

    #[test]
    fn decodes_hex() {
        assert_eq!(decode_hex("5236434647310a"), Ok(b"R6CFG1\n".to_vec()));
        assert!(decode_hex("0").is_err());
        assert!(decode_hex("zz").is_err());
    }

    #[test]
    fn parses_optional_fault() {
        let arguments = vec![
            "a".repeat(64),
            ".".to_owned(),
            "00".to_owned(),
            "--fault".to_owned(),
            "after-lock".to_owned(),
        ];
        let parsed = parse_arguments(&arguments).expect("arguments");
        assert_eq!(parsed.3, Some("after-lock"));
    }
}
