#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "release" / "publish-readiness.json"
VSCODE_PACKAGE = ROOT / "integrations" / "vscode-syntavra" / "package.json"

VERSION = "0.0.1"
CHANNEL = "pre-release"
USER_AGENT = "Syntavra pre-release registry preflight/0.0.1"

Probe = Callable[[str], dict[str, Any]]
VsceProbe = Callable[[str, str], dict[str, Any]]


def _http_probe(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read(1)
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        exc.read(1)
        status = int(exc.code)
    except Exception as exc:
        return {
            "status": "unreachable",
            "http_status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "url": url,
        }

    if status == 404:
        state = "available"
    elif status == 200:
        state = "occupied"
    else:
        state = "unreachable"
    return {"status": state, "http_status": status, "error": None, "url": url}


def _parse_vsce_show(*, returncode: int, stdout: str, stderr: str, version: str) -> dict[str, Any]:
    stdout = stdout.strip()
    stderr = stderr.strip()
    if returncode != 0:
        return {
            "status": "unreachable",
            "extension_exists": None,
            "version_exists": None,
            "observed_versions": [],
            "error": stderr or f"vsce show exited {returncode}",
        }
    if stdout == "undefined":
        return {
            "status": "available",
            "extension_exists": False,
            "version_exists": False,
            "observed_versions": [],
            "error": None,
        }
    if not stdout:
        return {
            "status": "unreachable",
            "extension_exists": None,
            "version_exists": None,
            "observed_versions": [],
            "error": "vsce show returned empty stdout",
        }
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {
            "status": "unreachable",
            "extension_exists": None,
            "version_exists": None,
            "observed_versions": [],
            "error": f"invalid vsce JSON: {exc}",
        }
    if not isinstance(value, dict):
        return {
            "status": "unreachable",
            "extension_exists": None,
            "version_exists": None,
            "observed_versions": [],
            "error": "vsce JSON is not an object",
        }
    versions = sorted(
        {
            str(row.get("version"))
            for row in value.get("versions", [])
            if isinstance(row, dict) and isinstance(row.get("version"), str)
        }
    )
    exists = version in versions
    return {
        "status": "occupied" if exists else "available",
        "extension_exists": True,
        "version_exists": exists,
        "observed_versions": versions,
        "error": None,
    }


def _vsce_probe(extension_id: str, version: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["npx", "--yes", "@vscode/vsce", "show", extension_id, "--json"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
            check=False,
        )
    except Exception as exc:
        return {
            "status": "unreachable",
            "extension_exists": None,
            "version_exists": None,
            "observed_versions": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    value = _parse_vsce_show(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        version=version,
    )
    value["extension_id"] = extension_id
    value["tool"] = "npx --yes @vscode/vsce show <extension-id> --json"
    return value


def _quoted(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _validate_metadata(readiness: dict[str, Any], vscode: dict[str, Any]) -> None:
    if readiness.get("version") != VERSION or readiness.get("channel") != CHANNEL:
        raise ValueError("release readiness version/channel drift")
    required = ("python", "npm", "npm_sdk", "vscode", "native", "legacy_native_companion")
    for target in required:
        value = readiness.get(target)
        if not isinstance(value, dict):
            raise ValueError(f"missing release target: {target}")
        if value.get("published") is not False:
            raise ValueError(f"release target must remain unpublished before preflight: {target}")
    if readiness["python"].get("package") != "syntavra-runtime":
        raise ValueError("Python publication identity drift")
    if readiness["npm"].get("package") != "@syntavra/install":
        raise ValueError("npm installer publication identity drift")
    if readiness["npm_sdk"].get("package") != "@syntavra/sdk":
        raise ValueError("npm SDK publication identity drift")
    if readiness["vscode"].get("package") != "syntavra-vscode":
        raise ValueError("VS Code publication identity drift")
    if readiness["native"].get("publish_order") != ["syntavra-contracts", "syntavra-core", "syntavra-cli"]:
        raise ValueError("Rust production publish order drift")
    if readiness["legacy_native_companion"].get("package") != "syntavra-native":
        raise ValueError("legacy native publication identity drift")
    if vscode.get("version") != VERSION or vscode.get("name") != readiness["vscode"]["package"]:
        raise ValueError("VS Code package metadata drift")
    if not isinstance(vscode.get("publisher"), str) or not vscode["publisher"]:
        raise ValueError("VS Code publisher metadata missing")


def build_report(
    *,
    http_probe: Probe = _http_probe,
    vsce_probe: VsceProbe = _vsce_probe,
) -> dict[str, Any]:
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    vscode = json.loads(VSCODE_PACKAGE.read_text(encoding="utf-8"))
    _validate_metadata(readiness, vscode)

    python_name = readiness["python"]["package"]
    npm_name = readiness["npm"]["package"]
    npm_sdk_name = readiness["npm_sdk"]["package"]
    native_names = list(readiness["native"]["publish_order"])
    legacy_name = readiness["legacy_native_companion"]["package"]
    extension_id = f"{vscode['publisher']}.{vscode['name']}"

    targets: dict[str, Any] = {
        "python": {
            "registry": "pypi",
            "package": python_name,
            "version": VERSION,
            **http_probe(f"https://pypi.org/pypi/{_quoted(python_name)}/{VERSION}/json"),
        },
        "npm": {
            "registry": "npm",
            "package": npm_name,
            "version": VERSION,
            **http_probe(f"https://registry.npmjs.org/{_quoted(npm_name)}/{VERSION}"),
        },
        "npm_sdk": {
            "registry": "npm",
            "package": npm_sdk_name,
            "version": VERSION,
            **http_probe(f"https://registry.npmjs.org/{_quoted(npm_sdk_name)}/{VERSION}"),
        },
        "vscode": {
            "registry": "vscode-marketplace",
            "package": readiness["vscode"]["package"],
            "version": VERSION,
            **vsce_probe(extension_id, VERSION),
        },
        "native": {
            "registry": "crates.io",
            "publish_order": native_names,
            "version": VERSION,
            "packages": {},
        },
        "legacy_native_companion": {
            "registry": "crates.io",
            "package": legacy_name,
            "version": VERSION,
            **http_probe(f"https://crates.io/api/v1/crates/{_quoted(legacy_name)}/{VERSION}"),
        },
    }
    for name in native_names:
        targets["native"]["packages"][name] = {
            "package": name,
            "version": VERSION,
            **http_probe(f"https://crates.io/api/v1/crates/{_quoted(name)}/{VERSION}"),
        }
    native_states = [row["status"] for row in targets["native"]["packages"].values()]
    targets["native"]["status"] = (
        "available"
        if all(state == "available" for state in native_states)
        else "occupied"
        if any(state == "occupied" for state in native_states)
        else "unreachable"
    )

    production_names = ("python", "npm", "npm_sdk", "vscode", "native")
    production_available = all(targets[name]["status"] == "available" for name in production_names)
    legacy_available = targets["legacy_native_companion"]["status"] == "available"
    all_observed = all(targets[name]["status"] != "unreachable" for name in (*production_names, "legacy_native_companion"))
    return {
        "schema_version": 1,
        "product": "Syntavra",
        "version": VERSION,
        "channel": CHANNEL,
        "network_boundary": "anonymous read-only public registry queries; no credentials and no registry mutation",
        "publication_performed": False,
        "targets": targets,
        "production_available": production_available,
        "legacy_available": legacy_available,
        "all_observed": all_observed,
        "claim": "REGISTRY_VERSION_PREFLIGHT_AVAILABLE" if production_available else "REGISTRY_VERSION_PREFLIGHT_BLOCKED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--require-production-available", action="store_true")
    parser.add_argument("--require-legacy-available", action="store_true")
    args = parser.parse_args(argv)

    try:
        value = build_report()
    except Exception as exc:
        value = {
            "schema_version": 1,
            "product": "Syntavra",
            "version": VERSION,
            "channel": CHANNEL,
            "publication_performed": False,
            "production_available": False,
            "legacy_available": False,
            "all_observed": False,
            "claim": "REGISTRY_VERSION_PREFLIGHT_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }
        rc = 3
    else:
        rc = 0
        if args.require_production_available and not value["production_available"]:
            rc = 2
        if args.require_legacy_available and not value["legacy_available"]:
            rc = 2

    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
