#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "crates" / "syntavra-cli" / "src" / "native_provider_gateway_prepare.rs"
VERIFY = ROOT / "tools" / "verify_r31_provider_prepare_parity.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    rust = RUST.read_text(encoding="utf-8")
    rust = replace_once(
        rust,
        "            drop(map);\n",
        "            let _ = map;\n",
        "drop-reference-warning",
    )
    rust = replace_once(
        rust,
        "pub(crate) fn prepare(state_root: &Path) -> Result<Value, String> {\n",
        "fn prepare_impl(state_root: &Path) -> Result<Value, String> {\n",
        "prepare-impl-rename",
    )
    wrapper = '''\n\npub(crate) fn prepare(state_root: &Path) -> Result<Value, String> {\n    match prepare_impl(state_root) {\n        Ok(value) => Ok(value),\n        Err(error) => {\n            eprintln!("{error}");\n            std::process::exit(1);\n        }\n    }\n}\n'''
    if "fn prepare_impl(state_root: &Path)" not in rust:
        raise SystemExit("prepare wrapper anchor missing")
    if "pub(crate) fn prepare(state_root: &Path)" in rust:
        raise SystemExit("prepare wrapper already exists unexpectedly")
    rust = rust.rstrip() + wrapper
    RUST.write_text(rust, encoding="utf-8")

    verify = VERIFY.read_text(encoding="utf-8")
    old = '''    if use_output:\n        assert py_output is not None and rs_output is not None\n        py_file = json.loads(py_output.read_text(encoding="utf-8"))\n        rs_file = json.loads(rs_output.read_text(encoding="utf-8"))\n        py_plan = py_file\n        rs_plan = rs_file\n        wrapper_equal = py_value == rs_value\n    else:\n'''
    new = '''    if use_output:\n        assert py_output is not None and rs_output is not None\n        py_file = json.loads(py_output.read_text(encoding="utf-8"))\n        rs_file = json.loads(rs_output.read_text(encoding="utf-8"))\n        py_plan = py_file\n        rs_plan = rs_file\n        wrapper_equal = (\n            py_value.get("ok") is True\n            and rs_value.get("ok") is True\n            and py_value.get("output") == rs_value.get("output") == "prepare.json"\n            and py_value.get("bytes") == py_output.stat().st_size\n            and rs_value.get("bytes") == rs_output.stat().st_size\n        )\n    else:\n'''
    verify = replace_once(verify, old, new, "output-wrapper-semantics")

    old_reject = '''    return {\n        "ok": py_code != 0 and rs_code != 0 and py_shape == rs_shape,\n        "python_exit": py_code,\n        "rust_exit": rs_code,\n'''
    new_reject = '''    return {\n        "ok": py_code == rs_code == 1 and py_shape == rs_shape,\n        "python_exit": py_code,\n        "rust_exit": rs_code,\n'''
    verify = replace_once(verify, old_reject, new_reject, "credential-exit-parity")
    VERIFY.write_text(verify, encoding="utf-8")

    print("patched prepare exit code and output wrapper parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
