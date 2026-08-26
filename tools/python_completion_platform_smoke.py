#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLATFORM_CLAIM = "PYTHON_COMPLETION_PLATFORM_SMOKE_V1"


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def smoke(repo: Path, platform: str) -> dict[str, object]:
    repo = repo.resolve()
    if platform not in {"linux", "windows"}:
        raise AssertionError(f"unsupported platform label: {platform}")
    exact_head = _head(repo)
    version = importlib.metadata.version("syntavra-runtime")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"

    with tempfile.TemporaryDirectory(prefix="syntavra-python-completion-platform-") as directory:
        fresh = Path(directory) / "fresh-repository"
        fresh.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(fresh)], check=True, env=env)
        probe_code = (
            "import json; import pathlib; import syntavra_runtime; "
            "from syntavra_runtime.cli import build_parser; "
            "print(json.dumps({'module': str(pathlib.Path(syntavra_runtime.__file__).resolve()), "
            "'parser_ready': build_parser() is not None}))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe_code],
            cwd=fresh,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(f"installed runtime import failed: {proc.stderr.strip()}")
        probe = json.loads(proc.stdout.strip())
        module_path = Path(str(probe["module"])).resolve()
        source_import_isolation = not module_path.is_relative_to(repo)
        if not source_import_isolation:
            raise AssertionError(f"runtime imported from source checkout instead of installed distribution: {module_path}")
        if probe.get("parser_ready") is not True:
            raise AssertionError("installed runtime parser did not initialize")

        cli = shutil.which("syntavra")
        if not cli:
            raise AssertionError("installed syntavra console script missing")
        cli_proc = subprocess.run(
            [cli, "--help"],
            cwd=fresh,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if cli_proc.returncode != 0:
            raise AssertionError(f"installed syntavra --help failed: {cli_proc.stderr.strip()}")

    return {
        "ok": True,
        "schema_version": 1,
        "claim": PLATFORM_CLAIM,
        "exact_head": exact_head,
        "platform": platform,
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "distribution": "syntavra-runtime",
        "distribution_version": version,
        "installed_module_path": str(module_path),
        "clean_install": True,
        "source_import_isolation": True,
        "fresh_repository_smoke": True,
        "basic_runtime": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an exact-head Python completion platform smoke receipt")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--platform", required=True, choices=("linux", "windows"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        report = smoke(Path(args.repo), args.platform)
    except Exception as exc:
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": PLATFORM_CLAIM,
            "platform": args.platform,
            "error": f"{type(exc).__name__}: {exc}",
        }
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
