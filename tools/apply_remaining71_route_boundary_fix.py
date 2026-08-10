#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "crates/syntavra-cli/src/bin/syntavra.rs"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    old = '''fn command_path(arguments: &[String]) -> Vec<String> {\n    arguments\n        .iter()\n        .filter(|value| !value.starts_with('-'))\n        .take(3)\n        .cloned()\n        .collect()\n}\n'''
    new = '''fn command_path(arguments: &[String]) -> Vec<String> {\n    let mut route = Vec::new();\n    for value in arguments {\n        if value == "--" {\n            continue;\n        }\n        if value.starts_with('-') {\n            continue;\n        }\n        route.push(value.clone());\n        // Stop at the first route prefix that the Rust product actually owns.\n        // This prevents positional arguments and option values from being\n        // mistaken for route components (for example provider capture --plan X\n        // or run graph-query symbol). Three components remain the hard ceiling\n        // for legacy engine-route aliases.\n        if native_product::supports(&route) || route.len() >= 3 {\n            break;\n        }\n    }\n    route\n}\n'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"command_path replacement expected 1 match, found {count}")
    TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("selector command_path now stops at the first native-owned route prefix")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
