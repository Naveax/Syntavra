#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREP = ROOT / "crates/syntavra-cli/src/native_provider_gateway_prepare.rs"
CAPTURE = ROOT / "crates/syntavra-cli/src/native_provider_gateway_capture.rs"
LEGACY = ROOT / "crates/syntavra-cli/src/native_product_legacy.rs"
PRODUCT = ROOT / "crates/syntavra-cli/src/native_product.rs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def expose_prepare_helpers() -> None:
    text = PREP.read_text(encoding="utf-8")
    names = [
        "now_seconds",
        "canonical_json",
        "option_value",
        "initialize_gateway",
        "initialize_usage_ledger",
        "project_root",
        "stable_project_id",
        "output_value",
    ]
    for name in names:
        pattern = rf"(?m)^fn {re.escape(name)}\("
        matches = list(re.finditer(pattern, text))
        if len(matches) != 1:
            raise SystemExit(f"prepare-helper-{name}: expected one private fn, found {len(matches)}")
        text = re.sub(pattern, f"pub(super) fn {name}(", text, count=1)
    PREP.write_text(text, encoding="utf-8")


def fix_capture_compile() -> None:
    text = CAPTURE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "use std::path::{Path, PathBuf};\n",
        "use std::path::Path;\n",
        "capture-unused-pathbuf",
    )
    text = replace_once(
        text,
        "    let mut current: &Value = &Value::Object(value.clone());\n",
        "    let current: &Value = &Value::Object(value.clone());\n",
        "capture-unused-mut",
    )
    old = '''            (\n                task_id,\n                arm_id,\n                repetition,\n                cache_mode,\n                normalized.provider,\n                request_id_hash,\n                provider_response_hash,\n                normalized.fresh_input_tokens,\n                normalized.cached_input_tokens,\n                normalized.output_tokens,\n                normalized.reasoning_tokens,\n                quota_cost,\n                hardware_hash,\n                receipt_hash,\n                previous_chain,\n                chain_hash,\n                signature_mode,\n                signature,\n                raw_usage_hash,\n                raw_usage_json,\n                created_at,\n            ),\n'''
    new = '''            rusqlite::params![\n                task_id,\n                arm_id,\n                repetition,\n                cache_mode,\n                normalized.provider,\n                request_id_hash,\n                provider_response_hash,\n                normalized.fresh_input_tokens,\n                normalized.cached_input_tokens,\n                normalized.output_tokens,\n                normalized.reasoning_tokens,\n                quota_cost,\n                hardware_hash,\n                receipt_hash,\n                previous_chain,\n                chain_hash,\n                signature_mode,\n                signature,\n                raw_usage_hash,\n                raw_usage_json,\n                created_at,\n            ],\n'''
    text = replace_once(text, old, new, "capture-receipt-params")
    CAPTURE.write_text(text, encoding="utf-8")


def wire_capture() -> None:
    text = LEGACY.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '#[path = "native_provider_gateway_prepare.rs"]\nmod native_provider_gateway_prepare;\n',
        '#[path = "native_provider_gateway_prepare.rs"]\nmod native_provider_gateway_prepare;\n#[path = "native_provider_gateway_capture.rs"]\nmod native_provider_gateway_capture;\n',
        "capture-module",
    )
    text = replace_once(
        text,
        '                    "prepare" | "stats" | "verify" | "replay"\n',
        '                    "prepare" | "capture" | "stats" | "verify" | "replay"\n',
        "capture-support",
    )
    text = replace_once(
        text,
        '            "prepare" => native_provider_gateway_prepare::prepare(state_root).map(Some),\n            "stats" => native_provider_gateway_read::stats(state_root).map(Some),\n',
        '            "prepare" => native_provider_gateway_prepare::prepare(state_root).map(Some),\n            "capture" => native_provider_gateway_capture::capture(state_root).map(Some),\n            "stats" => native_provider_gateway_read::stats(state_root).map(Some),\n',
        "capture-execute",
    )
    LEGACY.write_text(text, encoding="utf-8")


def collapse_duplicate_engine_dispatch() -> None:
    text = PRODUCT.read_text(encoding="utf-8")
    block = '''    if native_engine_route_control::supports(command) {\n        let decision =\n            native_engine_route_control::execute(command, &arguments, project_root, state_root)?;\n        if decision.exit_code != 0 {\n            emit_failed_decision(&decision.value, decision.exit_code);\n        }\n        return Ok(Some(decision.value));\n    }\n'''
    count = text.count(block)
    if count < 2:
        raise SystemExit(f"engine-route-duplicate: expected duplicates, found {count}")
    first = text.find(block)
    last = first
    while text.startswith(block, last + len(block)):
        last += len(block)
    run_count = (last - first) // len(block) + 1
    if run_count < 2:
        raise SystemExit(f"engine-route-contiguous-run: expected duplicates, found {run_count}")
    text = text[:first] + block + text[first + run_count * len(block):]
    PRODUCT.write_text(text, encoding="utf-8")
    print(f"collapsed {run_count} contiguous engine-route dispatch blocks to one")


def main() -> int:
    expose_prepare_helpers()
    fix_capture_compile()
    wire_capture()
    collapse_duplicate_engine_dispatch()
    print("stage1 capture wiring and selector cleanup applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
