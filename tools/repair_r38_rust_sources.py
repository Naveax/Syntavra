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
        "safe.get(*axis)\n                        .is_some_and(|value| *value >= rule.critical_high)",
        "safe.get(**axis)\n                        .is_some_and(|value| *value >= rule.critical_high)",
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
)


def replace_once(replacement: Replacement) -> bool:
    path = ROOT / replacement.path
    source = path.read_text(encoding="utf-8")
    if replacement.new in source:
        return False
    count = source.count(replacement.old)
    if count != 1:
        raise RuntimeError(
            f"expected exactly one legacy fragment in {replacement.path}, found {count}"
        )
    path.write_text(
        source.replace(replacement.old, replacement.new, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def normalize_native_product_state_modules(
    path: Path | None = None,
) -> bool:
    path = path or ROOT / "crates/syntavra-cli/src/native_product.rs"
    source = path.read_text(encoding="utf-8")
    without_receipt = source.replace(STATE_RECEIPT_DECLARATION, "")
    layout_count = without_receipt.count(STATE_LAYOUT_DECLARATION)
    if layout_count != 1:
        raise RuntimeError(
            f"expected exactly one state layout declaration in {path}, found {layout_count}"
        )
    rendered = without_receipt.replace(
        STATE_LAYOUT_DECLARATION,
        STATE_RECEIPT_DECLARATION + STATE_LAYOUT_DECLARATION,
        1,
    )
    if rendered == source:
        return False
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def repair() -> int:
    changed = []
    for replacement in REPLACEMENTS:
        if replace_once(replacement):
            changed.append(replacement.path)
    if normalize_native_product_state_modules():
        changed.append("crates/syntavra-cli/src/native_product.rs")
    for path in sorted(set(changed)):
        print(f"repaired: {path}")
    if not changed:
        print("R38 Rust sources already canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(repair())
