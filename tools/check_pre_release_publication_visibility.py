#!/usr/bin/env python3
"""Read-only post-publication visibility verifier for Syntavra 0.0.1.

This tool never publishes or mutates a registry. It polls one exact release target
until the public registry reports version 0.0.1 as occupied, then emits a
machine-readable visibility receipt. The canonical published/readiness state is
still admitted separately through a reviewed exact-head change.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

from tools.check_pre_release_registry_availability import (
    READINESS,
    VERSION,
    VSCODE_PACKAGE,
    _http_probe,
    _quoted,
    _vsce_probe,
)

HttpProbe = Callable[[str], dict[str, Any]]
VsceProbe = Callable[[str, str], dict[str, Any]]

TARGETS = (
    "python",
    "npm",
    "npm_sdk",
    "rust_contracts",
    "rust_core",
    "rust_cli",
    "vscode",
    "legacy_native_companion",
)


def _metadata() -> tuple[dict[str, Any], dict[str, Any]]:
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    vscode = json.loads(VSCODE_PACKAGE.read_text(encoding="utf-8"))
    if readiness.get("version") != VERSION:
        raise ValueError("release readiness version drift")
    if vscode.get("version") != VERSION:
        raise ValueError("VS Code package version drift")
    if readiness.get("channel") != "pre-release":
        raise ValueError("release readiness channel drift")
    expected_rust = ["syntavra-contracts", "syntavra-core", "syntavra-cli"]
    if readiness.get("native", {}).get("publish_order") != expected_rust:
        raise ValueError("Rust production publish order drift")
    return readiness, vscode


def build_target_report(
    target: str,
    *,
    http_probe: HttpProbe = _http_probe,
    vsce_probe: VsceProbe = _vsce_probe,
) -> dict[str, Any]:
    if target not in TARGETS:
        raise ValueError(f"unknown publication visibility target: {target}")

    readiness, vscode = _metadata()
    registry: str
    package: str
    probe: dict[str, Any]

    if target == "python":
        registry = "pypi"
        package = readiness["python"]["package"]
        probe = http_probe(f"https://pypi.org/pypi/{_quoted(package)}/{VERSION}/json")
    elif target == "npm":
        registry = "npm"
        package = readiness["npm"]["package"]
        probe = http_probe(f"https://registry.npmjs.org/{_quoted(package)}/{VERSION}")
    elif target == "npm_sdk":
        registry = "npm"
        package = readiness["npm_sdk"]["package"]
        probe = http_probe(f"https://registry.npmjs.org/{_quoted(package)}/{VERSION}")
    elif target in {"rust_contracts", "rust_core", "rust_cli"}:
        registry = "crates.io"
        package = {
            "rust_contracts": "syntavra-contracts",
            "rust_core": "syntavra-core",
            "rust_cli": "syntavra-cli",
        }[target]
        probe = http_probe(f"https://crates.io/api/v1/crates/{_quoted(package)}/{VERSION}")
    elif target == "legacy_native_companion":
        registry = "crates.io"
        package = readiness["legacy_native_companion"]["package"]
        probe = http_probe(f"https://crates.io/api/v1/crates/{_quoted(package)}/{VERSION}")
    else:
        registry = "vscode-marketplace"
        package = readiness["vscode"]["package"]
        publisher = vscode.get("publisher")
        if not isinstance(publisher, str) or not publisher:
            raise ValueError("VS Code publisher metadata missing")
        extension_id = f"{publisher}.{package}"
        probe = vsce_probe(extension_id, VERSION)

    status = probe.get("status")
    visible = status == "occupied"
    return {
        "schema_version": 1,
        "product": "Syntavra",
        "version": VERSION,
        "channel": "pre-release",
        "target": target,
        "registry": registry,
        "package": package,
        "status": status,
        "visibility_verified": visible,
        "publication_performed_by_checker": False,
        "canonical_readiness_mutated": False,
        "network_boundary": "anonymous read-only public registry visibility query; no credentials and no registry mutation",
        "probe": probe,
        "claim": "PUBLIC_VERSION_VISIBLE" if visible else "PUBLIC_VERSION_NOT_YET_VISIBLE",
    }


def verify_with_retries(
    target: str,
    *,
    attempts: int,
    delay_seconds: float,
    http_probe: HttpProbe = _http_probe,
    vsce_probe: VsceProbe = _vsce_probe,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative")

    final: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        final = build_target_report(target, http_probe=http_probe, vsce_probe=vsce_probe)
        final["attempts_used"] = attempt
        final["attempt_limit"] = attempts
        final["delay_seconds"] = delay_seconds
        if final["visibility_verified"]:
            return final
        if attempt != attempts:
            sleeper(delay_seconds)
    assert final is not None
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--delay-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)

    try:
        report = verify_with_retries(
            args.target,
            attempts=args.attempts,
            delay_seconds=args.delay_seconds,
        )
    except Exception as exc:
        report = {
            "schema_version": 1,
            "product": "Syntavra",
            "version": VERSION,
            "channel": "pre-release",
            "target": args.target,
            "visibility_verified": False,
            "publication_performed_by_checker": False,
            "canonical_readiness_mutated": False,
            "claim": "PUBLIC_VERSION_VISIBILITY_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }
        rc = 3
    else:
        rc = 0 if report["visibility_verified"] else 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
