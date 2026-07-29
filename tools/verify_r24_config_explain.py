#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from syntavra_runtime.config_contract import resolve_config_wire
from syntavra_runtime.config_explain_contract import explain_result
from syntavra_runtime.live_config_discovery import discover_live_config_wire

ROOT = Path(__file__).resolve().parents[1]


def _rust_explain(wire: bytes, path: str) -> dict[str, object]:
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
            "explain",
            wire.hex(),
            path.encode("utf-8").hex(),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Rust config.explain failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("Rust config.explain output must be a JSON object")
    return value


def verify() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="syntavra-r24-config-explain-") as directory:
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
        snapshot = resolve_config_wire(wire)

        paths = (
            "runtime.profile",
            "routing.budget_bytes",
            "provider.credential_ref",
            "missing.value",
        )
        results: dict[str, object] = {}
        for path in paths:
            expected = explain_result(snapshot, path)
            candidate = _rust_explain(wire, path)
            if candidate != expected:
                raise RuntimeError(f"Python/Rust config.explain differs for {path}")
            results[path] = candidate

        credential = results["provider.credential_ref"]
        if not isinstance(credential, dict) or credential.get("value") != "[secret-ref]":
            raise RuntimeError("credential reference was not redacted")
        if (project / ".syntavra" / "pre-release").exists():
            raise RuntimeError("config.explain created product state")

        invalid = subprocess.run(
            [
                "cargo",
                "run",
                "--quiet",
                "--locked",
                "-p",
                "syntavra-cli",
                "--",
                "config",
                "explain",
                wire.hex(),
                "2e72756e74696d65",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if invalid.returncode == 0:
            raise RuntimeError("invalid config explain path did not fail closed")

        return {
            "ok": True,
            "phase": "R24",
            "command": "config.explain",
            "capability": "config.explain",
            "input_profile": "live-config-discovery-v1",
            "input_format": "R6CFG1",
            "wire_bytes": len(wire),
            "paths": list(paths),
            "credential_reference_redacted": True,
            "invalid_path_fail_closed": True,
            "filesystem_write": False,
            "fallback_policy": "none",
            "claim": "RUST_CONFIG_EXPLAIN_READ_ONLY_PARITY_PROVEN_R24",
        }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
