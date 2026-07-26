#![forbid(unsafe_code)]

pub const STATE_LAYOUT_JSON: &str = include_str!("../../../contracts/state/layout.json");

#[must_use]
pub const fn state_layout_json() -> &'static str {
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
