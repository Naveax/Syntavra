#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "crates" / "syntavra-cli" / "src" / "native_product.rs"
EVIDENCE = ROOT / "crates" / "syntavra-cli" / "src" / "native_evidence_store.rs"
GET = ROOT / "crates" / "syntavra-cli" / "src" / "native_evidence_get.rs"
VALIDATOR = ROOT / "tools" / "validate_r38_regression_closure.py"

MODULE = '''#[path = "native_evidence_get.rs"]
mod native_evidence_get;
'''
MODULE_ANCHOR = '''#[path = "native_compress_verify.rs"]
mod native_compress_verify;
'''
SUPPORT = "        || native_evidence_get::supports(command)\n"
SUPPORT_ANCHOR = "        || native_compress_verify::supports(command)\n"
EXECUTE = '''    if native_evidence_get::supports(command) {
        return native_evidence_get::execute(&arguments, project_root, state_root).map(Some);
    }
'''
EXECUTE_ANCHOR = '''    if native_compress_verify::supports(command) {
        let value = native_compress_verify::execute(&arguments, project_root, state_root)?;
        if value["ok"].as_bool() == Some(false) {
            emit_failed_decision(&value, 3);
        }
        return Ok(Some(value));
    }
'''
TARGET = '    "tests/runtime/test_native_evidence_get_r38.py",\n'
TARGET_ANCHOR = '    "tests/runtime/test_native_compress_verify_r38.py",\n'

OLD_GET = '''    pub(crate) fn get(&self, handle: &str) -> Result<Vec<u8>, String> {
        let digest = parse_handle(handle)?;
        let metadata = serde_json::from_slice::<Value>(
            &fs::read(self.metadata_path(&digest))
                .map_err(|error| format!("EVIDENCE_METADATA_READ_FAILED:{error}"))?,
        )
        .map_err(|error| format!("EVIDENCE_METADATA_INVALID:{error}"))?;
        if metadata["project_id"].as_str() != Some(self.project_id.as_str()) {
            return Err("EVIDENCE_SCOPE_MISMATCH".to_owned());
        }
        let output = self.decrypt_digest(&digest)?;
        let connection = Connection::open(self.root.join("evidence.sqlite3"))
            .map_err(|error| format!("EVIDENCE_INDEX_OPEN_FAILED:{error}"))?;
        connection
            .execute(
                "UPDATE evidence_objects SET last_accessed_at=?1 WHERE digest=?2",
                params![now_seconds()?, digest],
            )
            .map_err(|error| format!("EVIDENCE_ACCESS_UPDATE_FAILED:{error}"))?;
        Ok(output)
    }
'''

NEW_GET = '''    pub(crate) fn get_with_max_bytes(
        &self,
        handle: &str,
        max_bytes: Option<i128>,
    ) -> Result<Vec<u8>, String> {
        let digest = parse_handle(handle)?;
        let metadata = serde_json::from_slice::<Value>(
            &fs::read(self.metadata_path(&digest))
                .map_err(|error| format!("EVIDENCE_METADATA_READ_FAILED:{error}"))?,
        )
        .map_err(|error| format!("EVIDENCE_METADATA_INVALID:{error}"))?;
        if metadata["project_id"].as_str() != Some(self.project_id.as_str()) {
            return Err("EVIDENCE_SCOPE_MISMATCH".to_owned());
        }
        let integer = |value: &Value, field: &str| -> Result<i128, String> {
            match value {
                Value::Bool(flag) => Ok(i128::from(*flag)),
                Value::Number(number) => number
                    .as_i64()
                    .map(i128::from)
                    .or_else(|| number.as_u64().map(i128::from))
                    .or_else(|| {
                        number
                            .as_f64()
                            .filter(|item| item.is_finite())
                            .map(|item| item.trunc() as i128)
                    })
                    .ok_or_else(|| format!("EVIDENCE_METADATA_INTEGER_INVALID:{field}")),
                Value::String(text) => text
                    .trim()
                    .parse::<i128>()
                    .map_err(|_| format!("EVIDENCE_METADATA_INTEGER_INVALID:{field}")),
                _ => Err(format!("EVIDENCE_METADATA_INTEGER_INVALID:{field}")),
            }
        };
        let schema_version = integer(&metadata["schema_version"], "schema_version")?;
        if schema_version != i128::from(SCHEMA_VERSION) {
            return Err("EVIDENCE_METADATA_SCHEMA_INVALID".to_owned());
        }
        let described_size = integer(&metadata["bytes"], "bytes")?;
        if let Some(limit) = max_bytes {
            if described_size > limit {
                return Err(format!(
                    "EVIDENCE_EXCEEDS_MAX_BYTES:{described_size}>{limit}"
                ));
            }
        }
        let mut output = self.decrypt_digest(&digest)?;
        let actual_size = i128::try_from(output.len())
            .map_err(|_| "EVIDENCE_PLAINTEXT_SIZE_INVALID".to_owned())?;
        if let Some(limit) = max_bytes {
            if actual_size > limit {
                output.zeroize();
                return Err(format!("EVIDENCE_EXCEEDS_MAX_BYTES:{actual_size}>{limit}"));
            }
        }
        let connection = Connection::open(self.root.join("evidence.sqlite3"))
            .map_err(|error| format!("EVIDENCE_INDEX_OPEN_FAILED:{error}"))?;
        connection
            .execute(
                "UPDATE evidence_objects SET last_accessed_at=?1 WHERE digest=?2",
                params![now_seconds()?, digest],
            )
            .map_err(|error| format!("EVIDENCE_ACCESS_UPDATE_FAILED:{error}"))?;
        Ok(output)
    }

    pub(crate) fn get(&self, handle: &str) -> Result<Vec<u8>, String> {
        self.get_with_max_bytes(handle, None)
    }
'''


def validate_sources() -> None:
    if not GET.is_file():
        raise RuntimeError("native evidence get source is missing")
    if not EVIDENCE.is_file():
        raise RuntimeError("native evidence store source is missing")


def repair_evidence_store() -> bool:
    source = EVIDENCE.read_text(encoding="utf-8")
    if "pub(crate) fn get_with_max_bytes" in source:
        return False
    if source.count(OLD_GET) != 1:
        raise RuntimeError("evidence get method anchor is ambiguous")
    EVIDENCE.write_text(
        source.replace(OLD_GET, NEW_GET, 1), encoding="utf-8", newline="\n"
    )
    return True


def repair_product() -> bool:
    source = PRODUCT.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if "mod native_evidence_get;" not in rendered:
        if rendered.count(MODULE_ANCHOR) != 1:
            raise RuntimeError("evidence get module anchor is ambiguous")
        rendered = rendered.replace(MODULE_ANCHOR, MODULE_ANCHOR + MODULE, 1)
        changed = True
    if "|| native_evidence_get::supports(command)" not in rendered:
        if rendered.count(SUPPORT_ANCHOR) != 1:
            raise RuntimeError("evidence get support anchor is ambiguous")
        rendered = rendered.replace(SUPPORT_ANCHOR, SUPPORT_ANCHOR + SUPPORT, 1)
        changed = True
    execute_support = "if native_evidence_get::supports(command) {"
    execute_call = "native_evidence_get::execute("
    presence = (execute_support in rendered, execute_call in rendered)
    if presence == (False, False):
        if rendered.count(EXECUTE_ANCHOR) != 1:
            raise RuntimeError("evidence get execute anchor is ambiguous")
        rendered = rendered.replace(EXECUTE_ANCHOR, EXECUTE_ANCHOR + EXECUTE, 1)
        changed = True
    elif presence != (True, True):
        raise RuntimeError("evidence get execute wiring is partial")
    if changed:
        PRODUCT.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_validator() -> bool:
    source = VALIDATOR.read_text(encoding="utf-8")
    if source.count(TARGET) == 1:
        return False
    if source.count(TARGET) != 0 or source.count(TARGET_ANCHOR) != 1:
        raise RuntimeError("evidence get validator contract is ambiguous")
    VALIDATOR.write_text(
        source.replace(TARGET_ANCHOR, TARGET_ANCHOR + TARGET, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def main() -> int:
    validate_sources()
    evidence_changed = repair_evidence_store()
    product_changed = repair_product()
    validator_changed = repair_validator()
    print(
        json.dumps(
            {
                "changed": evidence_changed or product_changed or validator_changed,
                "evidence_changed": evidence_changed,
                "ok": True,
                "product_changed": product_changed,
                "surface": "native-evidence-get",
                "validator_changed": validator_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
