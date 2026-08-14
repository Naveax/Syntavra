#![forbid(unsafe_code)]

use serde_json::{json, Value};

const VERSION: &str = "0.0.1";
const CHANNEL: &str = "pre-release";

fn target(arguments: &[String]) -> Result<&str, String> {
    let mut values = arguments.iter();
    while let Some(argument) = values.next() {
        if argument == "--target" {
            return values
                .next()
                .map(String::as_str)
                .ok_or_else(|| "UPGRADE_TARGET_MISSING".to_owned());
        }
        if let Some(value) = argument.strip_prefix("--target=") {
            return Ok(value);
        }
    }
    Ok(VERSION)
}

fn require_version(requested: &str) -> Result<(), String> {
    let normalized = requested.trim().trim_start_matches('v');
    if normalized == VERSION {
        Ok(())
    } else {
        Err(format!(
            "Syntavra version is locked to {VERSION}; explicit owner approval is required to change it"
        ))
    }
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    require_version(target(arguments)?)?;
    Ok(json!({
        "ok": true,
        "changed": false,
        "version": VERSION,
        "channel": CHANNEL,
        "reason": "version-locked-until-owner-authorization",
    }))
}

#[cfg(test)]
mod tests {
    use super::execute;

    #[test]
    fn accepts_locked_version_with_or_without_prefix() {
        for requested in ["0.0.1", "v0.0.1"] {
            let arguments = vec![
                "upgrade".to_owned(),
                "--target".to_owned(),
                requested.to_owned(),
            ];
            let value = execute(&arguments).expect("locked version");
            assert_eq!(value["ok"], true);
            assert_eq!(value["changed"], false);
        }
    }

    #[test]
    fn rejects_other_versions() {
        let arguments = vec![
            "upgrade".to_owned(),
            "--target".to_owned(),
            "0.0.2".to_owned(),
        ];
        assert!(execute(&arguments)
            .expect_err("version lock")
            .contains("explicit owner approval"));
    }
}
