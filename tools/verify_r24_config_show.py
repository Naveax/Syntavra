#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.config_contract import resolve_config_wire
from syntavra_runtime.config_show_contract import show_result
from syntavra_runtime.live_config_discovery import discover_live_config_wire

ROOT = Path(__file__).resolve().parents[1]


def _rust_show(wire: bytes) -> dict[str, object]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--locked",
            "-p",
            "syntavra-cli",
            "--",
            "config",
            "show",
            wire.hex(),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust config.show failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust config.show output must be a JSON object")
    return value


def verify() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="syntavra-r24-config-show-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        config = project / ".syntavra" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            '[runtime]\nprofile = "compact"\n\n[routing]\nbudget_bytes = 4096\n',
            encoding="utf-8",
        )
        environment = {
            "HOME": str(root / "home"),
            "SYNTAVRA_CFG__PROVIDER__CREDENTIAL_REF": "secret://provider/key",
        }
        wire = discover_live_config_wire(project_root=project, env=environment)
        expected = show_result(resolve_config_wire(wire))
        candidate = _rust_show(wire)
        if candidate != expected:
            raise RuntimeError("Python/Rust canonical config.show snapshots differ")
        if "loaded_at" in candidate:
            raise RuntimeError("config.show exposed nondeterministic loaded_at metadata")
        credential_rows = [
            row
            for row in candidate.get("provenance", [])
            if isinstance(row, dict) and row.get("path") == "provider.credential_ref"
        ]
        if not credential_rows or credential_rows[-1].get("value") != "[secret-ref]":
            raise RuntimeError("credential-reference provenance was not redacted")
        if (project / ".syntavra" / "pre-release").exists():
            raise RuntimeError("config.show created product state")

        return {
            "ok": True,
            "phase": "R24",
            "command": "config.show",
            "capability": "config.show",
            "input_profile": "live-config-discovery-v1",
            "input_format": "R6CFG1",
            "wire_bytes": len(wire),
            "result_keys": sorted(candidate),
            "loaded_at_forbidden": True,
            "credential_reference_provenance_redacted": True,
            "filesystem_write": False,
            "fallback_policy": "none",
            "claim": "RUST_CONFIG_SHOW_CANONICAL_READ_ONLY_PARITY_PROVEN_R24",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
