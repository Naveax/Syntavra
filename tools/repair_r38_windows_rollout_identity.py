#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates/syntavra-cli/src/native_rollout_tail.rs"

LEGACY_FILE_NUMBERS = """#[cfg(windows)]
fn file_numbers(metadata: &fs::Metadata) -> (u64, u64) {
    use std::os::windows::fs::MetadataExt;
    (
        metadata.file_index().unwrap_or(0),
        u64::from(metadata.volume_serial_number().unwrap_or(0)),
    )
}
"""

CANONICAL_FILE_NUMBERS = """#[cfg(windows)]
fn file_numbers(metadata: &fs::Metadata) -> (u64, u64) {
    use std::os::windows::fs::MetadataExt;
    (metadata.creation_time(), 0)
}
"""

LEGACY_FILE_IDENTITY = """fn file_identity(path: &Path) -> Result<String, String> {
    let resolved =
        fs::canonicalize(path).map_err(|error| format!("ROLLOUT_PATH_RESOLVE_FAILED:{error}"))?;
    let metadata =
        fs::metadata(&resolved).map_err(|error| format!("ROLLOUT_METADATA_FAILED:{error}"))?;
    let (inode, device) = file_numbers(&metadata);
    let material = format!("{}|{inode}|{device}", resolved.display());
    Ok(sha256_hex(material.as_bytes()))
}
"""

CANONICAL_FILE_IDENTITY = r'''#[cfg(windows)]
fn identity_path(path: &Path) -> String {
    let value = path.to_string_lossy().into_owned();
    if let Some(unc) = value.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else {
        value.strip_prefix(r"\\?\").unwrap_or(&value).to_owned()
    }
}

#[cfg(not(windows))]
fn identity_path(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn file_identity(path: &Path) -> Result<String, String> {
    let resolved =
        fs::canonicalize(path).map_err(|error| format!("ROLLOUT_PATH_RESOLVE_FAILED:{error}"))?;
    let metadata =
        fs::metadata(&resolved).map_err(|error| format!("ROLLOUT_METADATA_FAILED:{error}"))?;
    let (inode, device) = file_numbers(&metadata);
    let material = format!("{}|{inode}|{device}", identity_path(&resolved));
    Ok(sha256_hex(material.as_bytes()))
}
'''

LEGACY_OUTPUT_VALUE = "Value::String(selected.to_string_lossy().into_owned())"
CANONICAL_OUTPUT_VALUE = "Value::String(identity_path(&selected))"


def _replace_optional_once(source: str, legacy: str, canonical: str, label: str) -> tuple[str, bool]:
    legacy_count = source.count(legacy)
    if legacy_count > 1:
        raise RuntimeError(f"multiple legacy {label} fragments found: {legacy_count}")
    if legacy_count == 1:
        return source.replace(legacy, canonical, 1), True
    return source, False


def _validate_canonical(source: str, path: Path) -> None:
    invariants = {
        "stable Windows creation-time identity": source.count("(metadata.creation_time(), 0)"),
        "Windows and non-Windows identity_path functions": source.count(
            "fn identity_path(path: &Path) -> String"
        ),
        "identity hash path normalization": source.count("identity_path(&resolved)"),
        "rollout output path normalization": source.count("identity_path(&selected)"),
    }
    expected = {
        "stable Windows creation-time identity": 1,
        "Windows and non-Windows identity_path functions": 2,
        "identity hash path normalization": 1,
        "rollout output path normalization": 1,
    }
    failures = {
        name: (count, expected[name])
        for name, count in invariants.items()
        if count != expected[name]
    }
    forbidden = {
        "unstable file_index": source.count("metadata.file_index()"),
        "unstable volume_serial_number": source.count("metadata.volume_serial_number()"),
        "unnormalized identity material": source.count(
            'format!("{}|{inode}|{device}", resolved.display())'
        ),
        "unnormalized rollout output": source.count(LEGACY_OUTPUT_VALUE),
    }
    forbidden = {name: count for name, count in forbidden.items() if count != 0}
    if failures or forbidden:
        raise RuntimeError(
            f"Windows rollout identity invariants failed in {path}; "
            f"expected-count failures={failures}; forbidden fragments={forbidden}"
        )


def repair(path: Path | None = None) -> bool:
    path = path or TARGET
    source = path.read_text(encoding="utf-8")
    rendered = source
    changed = False

    rendered, applied = _replace_optional_once(
        rendered,
        LEGACY_FILE_NUMBERS,
        CANONICAL_FILE_NUMBERS,
        "Windows file-number",
    )
    changed = changed or applied

    rendered, applied = _replace_optional_once(
        rendered,
        LEGACY_FILE_IDENTITY,
        CANONICAL_FILE_IDENTITY,
        "rollout identity",
    )
    changed = changed or applied

    rendered, applied = _replace_optional_once(
        rendered,
        LEGACY_OUTPUT_VALUE,
        CANONICAL_OUTPUT_VALUE,
        "rollout output",
    )
    changed = changed or applied

    _validate_canonical(rendered, path)
    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print(
        "repaired: native_rollout_tail.rs"
        if changed
        else "Windows rollout identity already canonical"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
