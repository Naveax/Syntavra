#![forbid(unsafe_code)]

pub const STATE_LAYOUT_JSON: &str = include_str!("../../../contracts/state/layout.json");

#[must_use]
pub fn state_layout_json() -> &'static str {
    // Keep the historical R7 symbols live until the receipt module is split.
    // The command output itself always comes from the canonical R8 contract.
    let _legacy_layout = crate::state_receipt_contract::STATE_LAYOUT_JSON;
    let _legacy_layout_fn: fn() -> &'static str = crate::state_receipt_contract::state_layout_json;
    STATE_LAYOUT_JSON
}

#[cfg(test)]
mod tests {
    use super::state_layout_json;

    #[test]
    fn embeds_r8_read_only_layout_contract() {
        let value = state_layout_json();
        assert!(value.contains("\"command\": \"state.inspect\""));
        assert!(value.contains("\"filesystem_mutation\": false"));
        assert!(value.contains("\"database_access\": false"));
        assert!(value.contains("\"recursive_directory_read\": false"));
    }
}
