#![forbid(unsafe_code)]

#[allow(dead_code)]
#[path = "../config_contract.rs"]
mod config_contract;
#[allow(dead_code)]
#[path = "../config_last_good_apply.rs"]
mod config_last_good_apply;
#[allow(dead_code)]
#[path = "../config_last_good_plan.rs"]
mod config_last_good_plan;
#[path = "../full_parity_runtime.rs"]
mod full_parity_runtime;
#[allow(dead_code)]
#[path = "../state_snapshot_contract.rs"]
mod state_snapshot_contract;

use std::env;
use std::process::ExitCode;

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if value.len() % 2 != 0 {
        return Err("FULL_PARITY_REQUEST_HEX_INVALID".to_owned());
    }
    let mut output = Vec::with_capacity(value.len() / 2);
    for pair in value.as_bytes().chunks_exact(2) {
        let high = nibble(pair[0]).ok_or_else(|| "FULL_PARITY_REQUEST_HEX_INVALID".to_owned())?;
        let low = nibble(pair[1]).ok_or_else(|| "FULL_PARITY_REQUEST_HEX_INVALID".to_owned())?;
        output.push((high << 4) | low);
    }
    Ok(output)
}

fn nibble(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn run(arguments: &[String]) -> Result<ExitCode, String> {
    match arguments {
        [flag, mode, value] if flag == "--child" => {
            let code = full_parity_runtime::child_mode(mode, value)?;
            Ok(ExitCode::from(u8::try_from(code).unwrap_or(255)))
        }
        [project_root, expected_project_id, request_hex] => {
            let request = decode_hex(request_hex)?;
            let output = full_parity_runtime::execute_json(project_root, expected_project_id, &request)
                .map_err(|error| error.code)?;
            println!("{output}");
            Ok(ExitCode::SUCCESS)
        }
        _ => Err("FULL_PARITY_ARGUMENTS_INVALID".to_owned()),
    }
}

fn main() -> ExitCode {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    match run(&arguments) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::decode_hex;

    #[test]
    fn request_hex_is_strict() {
        assert_eq!(decode_hex("7b7d"), Ok(b"{}".to_vec()));
        assert!(decode_hex("0").is_err());
        assert!(decode_hex("zz").is_err());
    }
}
