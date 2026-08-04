#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_RECEIPT_DECLARATION = '#[path = "state_receipt_contract.rs"]\nmod state_receipt_contract;\n'
STATE_LAYOUT_DECLARATION = '#[path = "state_layout_contract.rs"]\nmod state_layout_contract;\n'


@dataclass(frozen=True)
class Replacement:
    path: str
    old: str
    new: str
    count: int = 1


REPLACEMENTS = (
    Replacement(
        "crates/syntavra-cli/src/native_job_mutations.rs",
        "fn positional_job_id(arguments: &[String], action: &str) -> Result<&str, String> {",
        "fn positional_job_id<'a>(arguments: &'a [String], action: &str) -> Result<&'a str, String> {",
    ),
    Replacement(
        "crates/syntavra-cli/src/state_layout_contract.rs",
        "crate::state_receipt_contract::STATE_LAYOUT_JSON",
        "super::state_receipt_contract::STATE_LAYOUT_JSON",
    ),
    Replacement(
        "crates/syntavra-cli/src/state_layout_contract.rs",
        "crate::state_receipt_contract::state_layout_json",
        "super::state_receipt_contract::state_layout_json",
    ),
    Replacement(
        "crates/syntavra-cli/src/state_layout_contract.rs",
        "crate::state_snapshot_contract::project_id_for_root",
        "super::state_snapshot_contract::project_id_for_root",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_benchmark_tools.rs",
        """safe.get(*axis)
                        .is_some_and(|value| *value >= rule.critical_high)""",
        """safe.get(**axis)
                        .is_some_and(|value| *value >= rule.critical_high)""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_engine_routes.rs",
        """    let candidate = Path::new(value);
    let selected = normalize_lexical(if candidate.is_absolute() {
        candidate
    } else {
        &root.join(candidate)
    })?;""",
        """    let candidate = Path::new(value);
    let selected_path = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        root.join(candidate)
    };
    let selected = normalize_lexical(&selected_path)?;""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_verifier.rs",
        "use rusqlite::{params, Connection, OptionalExtension};",
        "use rusqlite::{Connection, OptionalExtension};",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_structural.rs",
        ".map(|node| (node.clone(), 0.15 * teleport[node]))",
        ".map(|node| (node.clone(), (1.0 - 0.85) * teleport[node]))",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_structural.rs",
        """            let total = callers
                .iter()
                .map(|(_, weight)| weight.max(0.01))
                .sum::<f64>()
                .max(1.0);""",
        """            let total = callers
                .iter()
                .map(|(_, weight)| weight.max(0.01))
                .sum::<f64>();""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_structural.rs",
        """fn stable_project_id(project: &Path) -> String {
    let mut normalized = project.to_string_lossy().into_owned();
    #[cfg(windows)]
    {
        normalized = normalized
            .strip_prefix(r"\\\\?\\")
            .unwrap_or(&normalized)
            .to_lowercase();
    }
    sha256_hex(normalized.as_bytes())
}""",
        """fn stable_project_id(project: &Path) -> String {
    let normalized = project.to_string_lossy().into_owned();
    #[cfg(windows)]
    let normalized = normalized
        .strip_prefix(r"\\\\?\\")
        .unwrap_or(&normalized)
        .to_lowercase();
    sha256_hex(normalized.as_bytes())
}""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_analytics.rs",
        """    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    (year, month as u32, day as u32)""",
        """    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    let month = u32::try_from(month).expect("civil month is within 1..=12");
    let day = u32::try_from(day).expect("civil day is within 1..=31");
    (year, month, day)""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_analytics.rs",
        '.and_then(|_| handle.write_all(b"\\n"))',
        '.and_then(|()| handle.write_all(b"\\n"))',
    ),
    Replacement(
        "crates/syntavra-cli/src/native_engine_routes.rs",
        "fn envelope(command: &str, capability: &str, input: Value, result: Value) -> Value {",
        """#[allow(clippy::needless_pass_by_value)]
fn envelope(command: &str, capability: &str, input: Value, result: Value) -> Value {""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_engine_routes.rs",
        "    Ok(parsed.clamp(1, 1000) as usize)",
        """    usize::try_from(parsed.clamp(1, 1000))
        .map_err(|_| "SCHEDULER_READ_ONLY_LIMIT_INVALID".to_owned())""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_engine_state_routes.rs",
        "fn envelope(command: &str, input: Value, result: Value) -> Value {",
        """#[allow(clippy::needless_pass_by_value)]
fn envelope(command: &str, input: Value, result: Value) -> Value {""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_evidence_gc.rs",
        '.and_then(|_| fs::create_dir_all(root.join("metadata")))',
        '.and_then(|()| fs::create_dir_all(root.join("metadata")))',
    ),
    Replacement(
        "crates/syntavra-cli/src/native_evidence_stats.rs",
        '.and_then(|_| fs::create_dir_all(root.join("metadata")))',
        '.and_then(|()| fs::create_dir_all(root.join("metadata")))',
    ),
    Replacement(
        "crates/syntavra-cli/src/native_evidence_stats.rs",
        "    row: EdgeRow,",
        "    row: &EdgeRow,",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_evidence_stats.rs",
        "neighbor_value(&connection, row, &linked)?",
        "neighbor_value(&connection, &row, &linked)?",
        count=2,
    ),
    Replacement(
        "crates/syntavra-cli/src/native_memory.rs",
        "fn search_rows(",
        """#[allow(clippy::too_many_arguments)]
fn search_rows(""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_rollout_tail.rs",
        """fn encode_hex(value: &[u8]) -> String {
    value.iter().map(|byte| format!("{byte:02x}")).collect()
}""",
        """fn encode_hex(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len().saturating_mul(2));
    for byte in value {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_session_context.rs",
        """#[derive(Debug, Clone)]
struct Event {""",
        """#[derive(Debug, Clone)]
#[allow(clippy::struct_field_names)]
struct Event {""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_session_context.rs",
        "fn compact(",
        """#[allow(clippy::too_many_lines)]
fn compact(""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_session_context.rs",
        """fn selected_start(length: usize, recent_events: i64) -> usize {
    if recent_events > 0 {
        length.saturating_sub(usize::try_from(recent_events).unwrap_or(usize::MAX))
    } else if recent_events == 0 {
        0
    } else {
        length.min(usize::try_from(recent_events.unsigned_abs()).unwrap_or(usize::MAX))
    }
}""",
        """fn selected_start(length: usize, recent_events: i64) -> usize {
    match recent_events.cmp(&0) {
        std::cmp::Ordering::Greater => {
            length.saturating_sub(usize::try_from(recent_events).unwrap_or(usize::MAX))
        }
        std::cmp::Ordering::Equal => 0,
        std::cmp::Ordering::Less => {
            length.min(usize::try_from(recent_events.unsigned_abs()).unwrap_or(usize::MAX))
        }
    }
}""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_session_list.rs",
        """fn session_json(row: (String, String, String, String, f64, f64, String)) -> Result<Value, String> {
    let metadata: Value =
        serde_json::from_str(&row.6).map_err(|_| "SESSION_LIST_METADATA_INVALID".to_owned())?;
    Ok(json!({
        "session_id": row.0,
        "project_id": row.1,
        "parent_ids": decode_array(&row.2, "SESSION_LIST_PARENT_IDS_INVALID")?,
        "state": row.3,
        "created_at": row.4,
        "updated_at": row.5,
        "metadata": metadata,
    }))
}""",
        """fn session_json(row: (String, String, String, String, f64, f64, String)) -> Result<Value, String> {
    let (session_id, project_id, parent_ids_json, state, created_at, updated_at, metadata_json) = row;
    let metadata: Value = serde_json::from_str(&metadata_json)
        .map_err(|_| "SESSION_LIST_METADATA_INVALID".to_owned())?;
    Ok(json!({
        "session_id": session_id,
        "project_id": project_id,
        "parent_ids": decode_array(&parent_ids_json, "SESSION_LIST_PARENT_IDS_INVALID")?,
        "state": state,
        "created_at": created_at,
        "updated_at": updated_at,
        "metadata": metadata,
    }))
}""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_stats.rs",
        "fn python_int(value: Option<&Value>) -> Result<i64, String> {",
        """#[allow(clippy::cast_possible_truncation)]
fn truncated_i64(number: f64) -> Option<i64> {
    let truncated = number.trunc();
    if !truncated.is_finite()
        || !(-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0)
            .contains(&truncated)
    {
        return None;
    }
    Some(truncated as i64)
}

fn python_int(value: Option<&Value>) -> Result<i64, String> {""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_stats.rs",
        ".or_else(|| value.as_f64().map(|number| number.trunc() as i64))",
        ".or_else(|| value.as_f64().and_then(truncated_i64))",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_structural.rs",
        r"""line.find(|character| character == '\'' || character == '"')?""",
        r"""line.find(['\'', '"'])?""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_session_continuity.rs",
        """#[derive(Debug, Clone)]
struct Event {""",
        """#[derive(Debug, Clone)]
#[allow(clippy::struct_field_names)]
struct Event {""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_session_continuity.rs",
        "fn compact(",
        """#[allow(clippy::too_many_lines)]
fn compact(""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_session_status.rs",
        "fn python_int(value: Option<&Value>) -> Result<i64, String> {",
        """#[allow(clippy::cast_possible_truncation)]
fn truncated_i64(number: f64) -> Option<i64> {
    let truncated = number.trunc();
    if !truncated.is_finite()
        || !(-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0)
            .contains(&truncated)
    {
        return None;
    }
    Some(truncated as i64)
}

fn python_int(value: Option<&Value>) -> Result<i64, String> {""",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_session_status.rs",
        ".or_else(|| value.as_f64().map(|number| number.trunc() as i64))",
        ".or_else(|| value.as_f64().and_then(truncated_i64))",
    ),
    Replacement(
        "crates/syntavra-cli/src/native_product.rs",
        """pub fn execute(
    command: &[String],""",
        """#[allow(clippy::too_many_lines)]
pub fn execute(
    command: &[String],""",
    ),
)


def replace_expected(replacement: Replacement) -> bool:
    path = ROOT / replacement.path
    source = path.read_text(encoding="utf-8")
    old_count = source.count(replacement.old)
    new_count = source.count(replacement.new)

    if new_count == replacement.count:
        if replacement.old in replacement.new:
            if old_count == replacement.count:
                return False
        elif old_count == 0:
            return False

    if old_count != replacement.count:
        raise RuntimeError(
            f"expected {replacement.count} legacy fragment(s) in "
            f"{replacement.path}, found {old_count}; canonical fragments={new_count}"
        )
    if replacement.old not in replacement.new and new_count != 0:
        raise RuntimeError(
            f"found both legacy and canonical fragments in {replacement.path}"
        )

    rendered = source.replace(
        replacement.old,
        replacement.new,
        replacement.count,
    )
    if rendered.count(replacement.new) != replacement.count:
        raise RuntimeError(
            f"canonical fragment count mismatch in {replacement.path}"
        )
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def remove_native_product_state_modules(path: Path | None = None) -> bool:
    path = path or ROOT / "crates/syntavra-cli/src/native_product.rs"
    source = path.read_text(encoding="utf-8")
    receipt_count = source.count(STATE_RECEIPT_DECLARATION)
    layout_count = source.count(STATE_LAYOUT_DECLARATION)
    if receipt_count == 0 and layout_count == 0:
        return False
    if receipt_count != 1 or layout_count != 1:
        raise RuntimeError(
            "expected zero or one declaration for each unused state contract "
            f"in {path}; receipt={receipt_count}, layout={layout_count}"
        )
    rendered = source.replace(STATE_RECEIPT_DECLARATION, "")
    rendered = rendered.replace(STATE_LAYOUT_DECLARATION, "")
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def repair() -> int:
    changed = []
    for replacement in REPLACEMENTS:
        if replace_expected(replacement):
            changed.append(replacement.path)
    if remove_native_product_state_modules():
        changed.append("crates/syntavra-cli/src/native_product.rs")
    for path in sorted(set(changed)):
        print(f"repaired: {path}")
    if not changed:
        print("R38 Rust sources already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(repair())
