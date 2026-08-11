#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "crates/syntavra-cli/src/native_remaining71_memory.rs"
VALIDATOR = ROOT / "tools/validate_remaining71_session_memory_differential.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_rust() -> None:
    text = RUST.read_text(encoding="utf-8")

    canonical = '''fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&sorted(value)).map_err(|error| format!("MEMORY_JSON_FAILED:{error}"))
}
'''
    renderer = canonical + '''
fn python_json_dumps_sorted(value: &Value) -> Result<String, String> {
    fn render(value: &Value, output: &mut String) -> Result<(), String> {
        match value {
            Value::Null => output.push_str("null"),
            Value::Bool(true) => output.push_str("true"),
            Value::Bool(false) => output.push_str("false"),
            Value::Number(number) => output.push_str(&number.to_string()),
            Value::String(text) => output.push_str(
                &serde_json::to_string(text)
                    .map_err(|error| format!("MEMORY_PYTHON_JSON_STRING:{error}"))?,
            ),
            Value::Array(rows) => {
                output.push('[');
                for (index, row) in rows.iter().enumerate() {
                    if index != 0 {
                        output.push_str(", ");
                    }
                    render(row, output)?;
                }
                output.push(']');
            }
            Value::Object(map) => {
                output.push('{');
                for (index, (key, row)) in map.iter().enumerate() {
                    if index != 0 {
                        output.push_str(", ");
                    }
                    output.push_str(
                        &serde_json::to_string(key)
                            .map_err(|error| format!("MEMORY_PYTHON_JSON_KEY:{error}"))?,
                    );
                    output.push_str(": ");
                    render(row, output)?;
                }
                output.push('}');
            }
        }
        Ok(())
    }

    let value = sorted(value);
    let mut output = String::new();
    render(&value, &mut output)?;
    Ok(output)
}
'''
    text = replace_once(text, canonical, renderer, "python-json-renderer")

    text = replace_once(
        text,
        '''    let parents_json =
        serde_json::to_string(parents).map_err(|error| format!("SESSION_PARENTS_JSON:{error}"))?;
    let metadata_json = serde_json::to_string(&sorted(metadata))
        .map_err(|error| format!("SESSION_METADATA_JSON:{error}"))?;
''',
        '''    let parents_json = python_json_dumps_sorted(&json!(parents))?;
    let metadata_json = python_json_dumps_sorted(metadata)?;
''',
        "session-open-json-spacing",
    )

    text = replace_once(
        text,
        '''        let payload = serde_json::to_string(&sorted(&event["payload"]))
            .map_err(|error| format!("SESSION_SUMMARY_JSON:{error}"))?;
''',
        '''        let payload = python_json_dumps_sorted(&event["payload"])?;
''',
        "session-summary-json-spacing",
    )

    text = replace_once(
        text,
        '''        let payload_rendered = serde_json::to_string(&sorted(&event["payload"]))
            .map_err(|error| format!("SESSION_RETRIEVE_JSON:{error}"))?;
''',
        '''        let payload_rendered = python_json_dumps_sorted(&event["payload"])?;
''',
        "session-retrieve-json-spacing",
    )

    execute_anchor = '''pub(crate) fn execute(
    command: &[String],
'''
    tests = '''#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_json_dumps_sorted_matches_python_spacing() {
        let value = json!({
            "z": [1, {"β": "snow"}],
            "a": {"n": 1.0, "flag": true},
        });
        assert_eq!(
            python_json_dumps_sorted(&value).expect("render"),
            r#"{"a": {"flag": true, "n": 1.0}, "z": [1, {"β": "snow"}]}"#
        );
    }
}

''' + execute_anchor
    text = replace_once(text, execute_anchor, tests, "session-json-regression-test")
    RUST.write_text(text, encoding="utf-8")


def patch_validator() -> None:
    text = VALIDATOR.read_text(encoding="utf-8")

    merge_anchor = '''def normalize_merge(value: dict[str, Any]) -> dict[str, Any]:
    result = strip_times(value)
    merged = dict(result.get("merged") or {})
    merged["session_id"] = "<generated>"
    result["merged"] = merged
    return result
'''
    generated_helpers = merge_anchor + '''

def normalize_generated_event(value: dict[str, Any]) -> dict[str, Any]:
    result = strip_times(value)
    result["session_id"] = "<generated>"
    result["event_hash"] = "<generated-session-hash>"
    return result


def normalize_generated_verify(value: dict[str, Any]) -> dict[str, Any]:
    result = strip_times(value)
    result["session_id"] = "<generated>"
    result["last_hash"] = "<generated-session-hash>"
    return result
'''
    text = replace_once(text, merge_anchor, generated_helpers, "generated-session-normalizers")

    text = replace_once(
        text,
        '''        "child_append": {
            **strip_times(child_append),
            "session_id": "<generated>",
        },
        "child_verify": {
            **strip_times(child_verify),
            "session_id": "<generated>",
        },
''',
        '''        "child_append": normalize_generated_event(child_append),
        "child_verify": normalize_generated_verify(child_verify),
''',
        "child-generated-hash-normalization",
    )
    text = replace_once(
        text,
        '''        "merged_verify": {
            **strip_times(merged_verify),
            "session_id": "<generated>",
        },
''',
        '''        "merged_verify": normalize_generated_verify(merged_verify),
''',
        "merged-generated-hash-normalization",
    )

    old_invariants = '''    invariants: list[tuple[str, Any, Any]] = [
        ("verified_before.ok", python_result["verified_before"].get("ok"), True),
        ("verified_after.ok", python_result["verified_after"].get("ok"), True),
        ("restore.exact_recovery", python_result["restore"].get("exact_recovery"), True),
        ("child_verify.ok", python_result["child_verify"].get("ok"), True),
        ("merged_verify.ok", python_result["merged_verify"].get("ok"), True),
        ("compact.exact_history_preserved", python_result["compact"].get("exact_history_preserved"), True),
    ]
'''
    new_invariants = '''    invariants: list[tuple[str, Any, Any]] = [
        ("python.verified_before.ok", python_result["verified_before"].get("ok"), True),
        ("python.verified_after.ok", python_result["verified_after"].get("ok"), True),
        ("python.restore.exact_recovery", python_result["restore"].get("exact_recovery"), True),
        ("python.child_verify.ok", python_result["child_verify"].get("ok"), True),
        ("python.merged_verify.ok", python_result["merged_verify"].get("ok"), True),
        ("python.compact.exact_history_preserved", python_result["compact"].get("exact_history_preserved"), True),
        ("rust.verified_before.ok", rust_result["verified_before"].get("ok"), True),
        ("rust.verified_after.ok", rust_result["verified_after"].get("ok"), True),
        ("rust.restore.exact_recovery", rust_result["restore"].get("exact_recovery"), True),
        ("rust.child_verify.ok", rust_result["child_verify"].get("ok"), True),
        ("rust.merged_verify.ok", rust_result["merged_verify"].get("ok"), True),
        ("rust.compact.exact_history_preserved", rust_result["compact"].get("exact_history_preserved"), True),
    ]
'''
    text = replace_once(text, old_invariants, new_invariants, "two-engine-invariants")
    text = replace_once(
        text,
        '"claim_boundary": "deterministic local session-memory lifecycle and exact-hash parity only",',
        '"claim_boundary": "exact hashes for deterministic session IDs; generated fork/merge session hashes are normalized only after each engine verifies its own chain",',
        "claim-boundary",
    )
    VALIDATOR.write_text(text, encoding="utf-8")


def main() -> None:
    patch_rust()
    patch_validator()
    print("session-memory parity patch applied")


if __name__ == "__main__":
    main()
