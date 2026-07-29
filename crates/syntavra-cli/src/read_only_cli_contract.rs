#![forbid(unsafe_code)]

use serde_json::Value;

pub const STATIC_READ_ONLY_CLI_CONTRACT: &str =
    include_str!("../../../contracts/cli/read-only-static-v1.json");

pub fn result_json(route: &str) -> Result<String, String> {
    let contract: Value = serde_json::from_str(STATIC_READ_ONLY_CLI_CONTRACT)
        .map_err(|_| "R24_STATIC_CLI_CONTRACT_INVALID".to_owned())?;
    let commands = contract
        .get("commands")
        .and_then(Value::as_array)
        .ok_or_else(|| "R24_STATIC_CLI_COMMANDS_INVALID".to_owned())?;
    let row = commands
        .iter()
        .find(|item| item.get("route").and_then(Value::as_str) == Some(route))
        .ok_or_else(|| "R24_STATIC_CLI_ROUTE_UNSUPPORTED".to_owned())?;
    let result = row
        .get("result")
        .ok_or_else(|| "R24_STATIC_CLI_RESULT_MISSING".to_owned())?;
    serde_json::to_string(result).map_err(|_| "R24_STATIC_CLI_RESULT_INVALID".to_owned())
}

#[cfg(test)]
mod tests {
    use super::result_json;

    #[test]
    fn emits_pipeline_description_from_shared_contract() {
        let value = result_json("pipeline.describe").expect("pipeline result");
        assert!(value.contains("\"schema_version\":1"));
        assert!(value.contains("\"request-security\""));
        assert!(value.contains("\"typed_delivery\":true"));
    }

    #[test]
    fn emits_empty_explicit_plugin_inventory() {
        let value = result_json("plugins.list").expect("plugin result");
        assert_eq!(value, r#"{"discovery":"explicit-only","plugins":[]}"#);
    }

    #[test]
    fn rejects_unknown_static_route() {
        assert_eq!(
            result_json("unknown").expect_err("unknown route must fail"),
            "R24_STATIC_CLI_ROUTE_UNSUPPORTED"
        );
    }
}
