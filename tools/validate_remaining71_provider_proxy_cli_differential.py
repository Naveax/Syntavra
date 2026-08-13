#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools import certify_python_provider_proxy_reference as reference

FIXTURE_RELATIVE = Path("contracts/python/provider-proxy-reference-v1.json")
COVERED_ROUTES = [
    "provider capabilities",
    "provider capture",
    "provider prepare",
    "provider proxy",
    "provider replay",
    "provider stats",
    "provider verify",
    "run gateway-plan",
    "run provider-pool",
    "run provider-route",
    "run proxy-plan",
    "run proxy-service install",
    "run proxy-service plan",
    "run proxy-service uninstall",
    "run proxy-service verify",
]


def run_engine(
    engine: str,
    args: list[str],
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state: Path,
) -> dict[str, Any]:
    common = ["--project", str(project), "--state-root", str(state), *args]
    if engine == "python":
        command = [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            *common,
        ]
    elif engine == "rust":
        command = [str(rust_bin), "--engine", "rust", *common]
    else:
        raise ValueError(engine)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "SYNTAVRA_BULK_PARITY_PROBE": "1",
            "TEST_PROVIDER_KEY": "provider-proxy-cli-reference-key",
            "TEST_CONTROL_TOKEN": "c" * 32,
        }
    )
    result = subprocess.run(
        command,
        cwd=repo,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    try:
        value = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{engine} emitted non-JSON for {' '.join(args)}: "
            f"exit={result.returncode} stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        ) from exc
    return {
        "exit": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "value": value,
    }


def normalize_public_error_code(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("value")
    if not isinstance(value, dict):
        return result
    error = value.get("error")
    if not isinstance(error, dict):
        return result
    code = str(error.get("code") or "")
    if code in {"RUST_PUBLIC_COMMAND_FAILED", "PYTHON_PUBLIC_COMMAND_FAILED"}:
        cloned = json.loads(json.dumps(result))
        cloned["value"]["error"]["code"] = "PYTHON_PUBLIC_COMMAND_FAILED"
        return cloned
    return result


def reference_projection(
    engine: str,
    *,
    repo: Path,
    rust_bin: Path,
    root: Path,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    project = root / engine / "reference-project"
    state = root / engine / "reference-state"
    project.mkdir(parents=True)
    state.mkdir(parents=True)
    (project / ".git").mkdir()

    if engine == "python":
        runner = reference._run
    else:

        def runner(
            current_repo: Path,
            current_project: Path,
            current_state: Path,
            args: list[str],
        ) -> dict[str, Any]:
            return normalize_public_error_code(
                run_engine(
                    "rust",
                    args,
                    repo=current_repo,
                    rust_bin=rust_bin,
                    project=current_project,
                    state=current_state,
                )
            )

    with patch.object(reference, "_run", runner):
        gateway = reference._provider_gateway_contract(repo, project, state, fixture)
        helpers = reference._helper_contract(repo, project, state, fixture)
    return {"provider_gateway": gateway, "helpers": helpers}


def normalize_paths(value: Any, *, project: Path, state: Path, home: Path) -> Any:
    if isinstance(value, dict):
        return {
            key: normalize_paths(item, project=project, state=state, home=home)
            for key, item in value.items()
            if key not in {"created_at", "updated_at", "timestamp", "receipt_hash"}
        }
    if isinstance(value, list):
        return [
            normalize_paths(item, project=project, state=state, home=home)
            for item in value
        ]
    if not isinstance(value, str):
        return value
    rendered = value.replace("\\", "/")
    replacements = {
        str(project.resolve()).replace("\\", "/"): "<PROJECT>",
        str(state.resolve()).replace("\\", "/"): "<STATE>",
        str(home.resolve()).replace("\\", "/"): "<HOME>",
    }
    for source, replacement in replacements.items():
        rendered = rendered.replace(source, replacement)
    return rendered


def lifecycle_projection(
    engine: str,
    *,
    repo: Path,
    rust_bin: Path,
    root: Path,
) -> dict[str, Any]:
    project = root / engine / "lifecycle-project"
    state = root / engine / "lifecycle-state"
    home = root / engine / "lifecycle-home"
    project.mkdir(parents=True)
    state.mkdir(parents=True)
    home.mkdir(parents=True)
    (project / ".git").mkdir()
    results: dict[str, Any] = {}
    for action in ("install", "verify", "uninstall"):
        result = run_engine(
            engine,
            [
                "run",
                "proxy-service",
                action,
                "openai",
                "--platform",
                "linux",
                "--home",
                str(home),
            ],
            repo=repo,
            rust_bin=rust_bin,
            project=project,
            state=state,
        )
        results[action] = {
            "exit": result["exit"],
            "stderr": result["stderr"],
            "value": normalize_paths(
                result["value"], project=project, state=state, home=home
            ),
        }
    return results


def compare(
    python: dict[str, Any],
    rust: dict[str, Any],
    *,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for section in ("reference", "lifecycle"):
        if python[section] != rust[section]:
            mismatches.append(
                {
                    "path": section,
                    "python": python[section],
                    "rust": rust[section],
                }
            )
    frozen_routes = list(fixture["public_routes"])
    if sorted(COVERED_ROUTES) != sorted(frozen_routes):
        mismatches.append(
            {
                "path": "route_coverage",
                "python": sorted(frozen_routes),
                "rust": sorted(COVERED_ROUTES),
            }
        )
    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "frozen_routes": frozen_routes,
        "covered_routes": COVERED_ROUTES,
        "route_count": len(COVERED_ROUTES),
        "claim_boundary": (
            "frozen Phase-1 provider/proxy CLI and durable-state behavior plus dry-run "
            "proxy-service lifecycle; live localhost transport remains certified by the "
            "separate provider-proxy transport differential; no live provider or billing claim"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate frozen Python/Rust provider-proxy CLI and state parity"
    )
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)
    fixture = json.loads((repo / FIXTURE_RELATIVE).read_text(encoding="utf-8"))

    try:
        with tempfile.TemporaryDirectory(
            prefix="syntavra-provider-proxy-cli-diff-"
        ) as directory:
            root = Path(directory)
            python = {
                "reference": reference_projection(
                    "python",
                    repo=repo,
                    rust_bin=rust_bin,
                    root=root,
                    fixture=fixture,
                ),
                "lifecycle": lifecycle_projection(
                    "python", repo=repo, rust_bin=rust_bin, root=root
                ),
            }
            rust = {
                "reference": reference_projection(
                    "rust",
                    repo=repo,
                    rust_bin=rust_bin,
                    root=root,
                    fixture=fixture,
                ),
                "lifecycle": lifecycle_projection(
                    "rust", repo=repo, rust_bin=rust_bin, root=root
                ),
            }
            differential = compare(python, rust, fixture=fixture)
            result: dict[str, Any] = {
                "ok": differential["ok"],
                "schema_version": 1,
                "family": "provider-proxy-cli-state",
                "python": python,
                "rust": rust,
                "differential": differential,
            }
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "provider-proxy-cli-state",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    rendered = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
