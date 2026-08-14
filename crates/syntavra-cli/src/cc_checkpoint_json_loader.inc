fn cc_json_token_boundary(bytes: &[u8], end: usize) -> bool {
    bytes.get(end).map_or(true, |byte| !byte.is_ascii_alphanumeric() && *byte != b'_')
}
