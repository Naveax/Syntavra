#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates/syntavra-cli/src/native_rollout_tail.rs"

REPLACEMENTS = (
    (
        """#[cfg(windows)]
fn file_numbers(metadata: &fs::Metadata) -> (u64, u64) {
    use std::os::windows::fs::MetadataExt;
    (
        metadata.file_index().unwrap_or(0),
        u64::from(metadata.volume_serial_number().unwrap_or(0)),
    )
}
""",
        """#[cfg(windows)]
fn file_numbers(metadata: &fs::Metadata) -> (u64, u64) {
    use std::os::windows::fs::MetadataExt;
    (metadata.creation_time(), 0)
}
""",
    ),
    (
        """fn file_identity(path: &Path) -> Result<String, String> {
    let resolved =
        fs::canonicalize(path).map_err(|error| format!("ROLLOUT_PATH_RESOLVE_FAILED:{error}"))?;
    let metadata =
        fs::metadata(&resolved).map_err(|error| format!("ROLLOUT_METADATA_FAILED:{error}"))?;
    let (inode, device) = file_numbers(&metadata);
    let material = format!("{}|{inode}|{device}", resolved.display());
    Ok(sha256_hex(material.as_bytes()))
}
""",
        r'''#[cfg(windows)]
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
''',
    ),
    (
        """    object.insert(
        "rollout".to_owned(),
        Value::String(selected.to_string_lossy().into_owned()),
    );
""",
        """    object.insert("rollout".to_owned(), Value::String(identity_path(&selected)));
""",
    ),
)


def repair(path: Path | None = None) -> bool:
    path = path or TARGET
    source = path.read_text(encoding="utf-8")
    rendered = source
    changed = False

    for legacy, canonical in REPLACEMENTS:
        legacy_count = rendered.count(legacy)
        canonical_count = rendered.count(canonical)
        if legacy_count == 0 and canonical_count == 1:
            continue
        if legacy_count != 1 or canonical_count != 0:
            raise RuntimeError(
                "expected exactly one legacy or canonical Windows rollout "
                f"identity block in {path}; legacy={legacy_count}, "
                f"canonical={canonical_count}"
            )
        rendered = rendered.replace(legacy, canonical, 1)
        changed = True

    if changed:
        path.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    changed = repair()
    print("repaired: native_rollout_tail.rs" if changed else "Windows rollout identity already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
