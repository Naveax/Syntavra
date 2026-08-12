#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from syntavra_runtime.util import canonical_json

CONTRACT_RELATIVE = Path("contracts/python/python-reference-suite-v1.json")
CREDENTIAL_ENV = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "OPENROUTER_API_KEY",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "PYPI_TOKEN",
    "TWINE_PASSWORD",
    "TWINE_USERNAME",
    "GITHUB_TOKEN",
    "GH_TOKEN",
}
CI_HEAD_ENV = {"GITHUB_SHA"}


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_status(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git status failed: {proc.stderr.strip()}")
    return proc.stdout


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_plan(repo: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    contract_path = repo / CONTRACT_RELATIVE
    contract = _read_json(contract_path)
    if contract.get("schema_version") != 1 or contract.get("family") != "python-reference-suite":
        raise AssertionError("Python reference suite contract identity drift")

    catalog_relative = Path(str(contract.get("catalog") or ""))
    catalog_path = repo / catalog_relative
    catalog = _read_json(catalog_path)
    if catalog.get("schema_version") != 1 or catalog.get("family") != "fixture-golden-catalog":
        raise AssertionError("fixture/golden catalog identity drift")

    families = list(catalog.get("families") or [])
    expected_behavior = int(contract.get("expected_behavior_family_count") or -1)
    if len(families) != expected_behavior:
        raise AssertionError(f"behavior family count drift: {len(families)} != {expected_behavior}")

    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in families:
        family = str(row.get("family") or "")
        section = str(row.get("section") or "")
        certifier = str(row.get("certifier") or "")
        if not family or not section or not certifier:
            raise AssertionError(f"invalid catalog family execution row: {row}")
        if family in seen:
            raise AssertionError(f"duplicate suite family: {family}")
        seen.add(family)
        plan.append(
            {
                "section": section,
                "family": family,
                "certifier": certifier,
                "nondeterministic_fields": list(row.get("nondeterministic_fields") or []),
                "meta": False,
            }
        )

    for row in list(contract.get("meta_certifiers") or []):
        family = str(row.get("family") or "")
        certifier = str(row.get("path") or "")
        if not family or not certifier or family in seen:
            raise AssertionError(f"invalid meta certifier row: {row}")
        seen.add(family)
        plan.append(
            {
                "section": "Q",
                "family": family,
                "certifier": certifier,
                "nondeterministic_fields": None,
                "meta": True,
            }
        )

    expected_total = int(contract.get("expected_total_certifiers") or -1)
    if len(plan) != expected_total:
        raise AssertionError(f"suite certifier count drift: {len(plan)} != {expected_total}")

    for row in plan:
        path = repo / row["certifier"]
        if not path.is_file():
            raise AssertionError(f"missing suite certifier: {row['family']} -> {row['certifier']}")

    return contract, catalog, plan


def _isolated_env(repo: Path, scratch: Path, contract: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    for key in CREDENTIAL_ENV:
        env.pop(key, None)
    for key in CI_HEAD_ENV:
        env.pop(key, None)

    temp_root = scratch / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)

    execution = contract["execution"]
    sink = str(execution["external_http_proxy_sink"])
    no_proxy = str(execution["localhost_no_proxy"])
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "TMPDIR": str(temp_root),
            "TMP": str(temp_root),
            "TEMP": str(temp_root),
            "HTTP_PROXY": sink,
            "HTTPS_PROXY": sink,
            "ALL_PROXY": sink,
            "http_proxy": sink,
            "https_proxy": sink,
            "all_proxy": sink,
            "NO_PROXY": no_proxy,
            "no_proxy": no_proxy,
            "PIP_NO_INDEX": "1",
            "UV_OFFLINE": "1",
            "npm_config_offline": "true",
        }
    )
    return env


def _artifact_name(section: str, family: str) -> str:
    safe = family.replace("/", "-").replace(" ", "-")
    return f"{section.lower()}-{safe}"


def _validate_family_report(
    *,
    report: dict[str, Any],
    expected_family: str,
    expected_head: str,
    expected_nondeterministic: list[str] | None,
    meta: bool,
) -> None:
    if report.get("ok") is not True:
        raise AssertionError(f"certifier returned ok=false: {report.get('error') or report}")
    if report.get("family") != expected_family:
        raise AssertionError(f"family drift: {report.get('family')!r} != {expected_family!r}")
    report_head = report.get("exact_head")
    if report_head is not None and report_head != expected_head:
        raise AssertionError(f"exact-head drift: {report_head!r} != {expected_head!r}")
    if not meta:
        observed = report.get("nondeterministic_fields")
        if not isinstance(observed, list):
            raise AssertionError("family certifier must expose nondeterministic_fields")
        expected = list(expected_nondeterministic or [])
        if len(expected) != len(set(expected)) or len(observed) != len(set(observed)):
            raise AssertionError("duplicate nondeterministic field declaration")
        unexpected = [field for field in observed if field not in expected]
        if unexpected:
            raise AssertionError(
                f"unexpected nondeterminism drift: {unexpected!r}; observed={observed!r}; catalog={expected!r}"
            )


def run_suite(repo: Path, artifact_dir: Path) -> dict[str, Any]:
    contract, catalog, plan = load_plan(repo)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    expected_head = _head(repo)
    if not expected_head:
        raise AssertionError("unable to resolve exact git HEAD")

    pre_status = _git_status(repo)
    timeout_seconds = int(contract["execution"]["per_family_timeout_seconds"])
    by_family: dict[str, Any] = {}
    artifact_paths: dict[str, Any] = {}
    suite_errors: list[str] = []
    passed = 0
    failed = 0
    skipped = 0

    scratch_root = artifact_dir / "_scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)

    for row in plan:
        section = str(row["section"])
        family = str(row["family"])
        base = _artifact_name(section, family)
        json_path = artifact_dir / f"{base}.json"
        stdout_path = artifact_dir / f"{base}.stdout.log"
        stderr_path = artifact_dir / f"{base}.stderr.log"
        scratch = scratch_root / base
        scratch.mkdir(parents=True, exist_ok=True)

        command = [
            sys.executable,
            str(repo / row["certifier"]),
            "--repo",
            str(repo),
            "--output",
            str(json_path),
        ]
        status = "failed"
        reason: str | None = None
        exit_code: int | None = None
        timed_out = False

        try:
            proc = subprocess.run(
                command,
                cwd=repo,
                env=_isolated_env(repo, scratch, contract),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
            exit_code = proc.returncode
            stdout_path.write_text(proc.stdout, encoding="utf-8")
            stderr_path.write_text(proc.stderr, encoding="utf-8")

            if not json_path.is_file():
                raise AssertionError("required family JSON artifact was not created")
            report = _read_json(json_path)
            _validate_family_report(
                report=report,
                expected_family=family,
                expected_head=expected_head,
                expected_nondeterministic=row["nondeterministic_fields"],
                meta=bool(row["meta"]),
            )
            if proc.returncode != 0:
                raise AssertionError(f"certifier exit drift: {proc.returncode} != 0")
            status = "passed"
            passed += 1
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            reason = f"TimeoutExpired: certifier exceeded {timeout_seconds}s"
            exit_code = None
            stdout_path.write_text((exc.stdout or "") if isinstance(exc.stdout, str) else "", encoding="utf-8")
            stderr_path.write_text((exc.stderr or "") if isinstance(exc.stderr, str) else "", encoding="utf-8")
            failed += 1
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            failed += 1
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        if not stdout_path.exists():
            stdout_path.write_text("", encoding="utf-8")
        if not stderr_path.exists():
            stderr_path.write_text("", encoding="utf-8")

        json_exists = json_path.is_file()
        artifact_paths[family] = {
            "json": json_path.relative_to(artifact_dir).as_posix() if json_exists else None,
            "stdout": stdout_path.relative_to(artifact_dir).as_posix(),
            "stderr": stderr_path.relative_to(artifact_dir).as_posix(),
        }
        by_family[family] = {
            "section": section,
            "status": status,
            "certifier": row["certifier"],
            "exit_code": exit_code,
            "timed_out": timed_out,
            "artifact_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest() if json_exists else None,
            "reason": reason,
        }

    shutil.rmtree(scratch_root, ignore_errors=True)
    post_status = _git_status(repo)
    repository_status_preserved = pre_status == post_status
    repository_clean_before = pre_status == ""
    repository_clean_after = post_status == ""
    if not repository_status_preserved:
        suite_errors.append("repository status changed during Python reference suite")

    expected_skipped = int(contract.get("expected_skipped") or 0)
    if skipped != expected_skipped:
        suite_errors.append(f"skipped count drift: {skipped} != {expected_skipped}")

    catalog_sha = hashlib.sha256(canonical_json(catalog)).hexdigest()
    summary = {
        "ok": failed == 0 and not suite_errors,
        "schema_version": 1,
        "family": "python-reference-suite",
        "engine": "python",
        "exact_head": expected_head,
        "total": len(plan),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "by_family": by_family,
        "artifact_paths": artifact_paths,
        "repository_status_preserved": repository_status_preserved,
        "repository_clean_before": repository_clean_before,
        "repository_clean_after": repository_clean_after,
        "suite_errors": suite_errors,
        "offline_policy": contract["offline_policy"],
        "catalog_semantic_sha256": catalog_sha,
        "contract_sha256": hashlib.sha256((repo / CONTRACT_RELATIVE).read_bytes()).hexdigest(),
        "credential_env_removed": sorted(CREDENTIAL_ENV),
        "ci_head_env_removed": sorted(CI_HEAD_ENV),
        "runtime_home_preserved": bool(contract["execution"].get("preserve_runtime_home")),
        "runtime_xdg_preserved": bool(contract["execution"].get("preserve_runtime_xdg")),
    }

    required = list(contract.get("required_summary_fields") or [])
    missing_summary = [field for field in required if field not in summary]
    if missing_summary:
        summary["ok"] = False
        summary["suite_errors"].append(f"missing required summary fields: {missing_summary}")

    if summary["total"] != int(contract["expected_total_certifiers"]):
        summary["ok"] = False
        summary["suite_errors"].append("total certifier count drift")
    if summary["passed"] + summary["failed"] + summary["skipped"] != summary["total"]:
        summary["ok"] = False
        summary["suite_errors"].append("summary arithmetic drift")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete frozen Python reference certification suite")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--artifact-dir")
    parser.add_argument("--output")
    parser.add_argument("--list", action="store_true", dest="list_only")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)

    try:
        contract, catalog, plan = load_plan(repo)
        if args.list_only:
            result = {
                "ok": True,
                "schema_version": 1,
                "family": "python-reference-suite-plan",
                "engine": "python",
                "exact_head": _head(repo),
                "total": len(plan),
                "families": [row["family"] for row in plan],
                "certifiers": [row["certifier"] for row in plan],
                "catalog_semantic_sha256": hashlib.sha256(canonical_json(catalog)).hexdigest(),
                "offline_policy": contract["offline_policy"],
            }
        else:
            if args.artifact_dir:
                artifact_dir = Path(args.artifact_dir).resolve()
            else:
                artifact_dir = Path(tempfile.mkdtemp(prefix="syntavra-python-reference-suite-artifacts-"))
            result = run_suite(repo, artifact_dir)
            result["artifact_dir"] = str(artifact_dir)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "python-reference-suite",
            "engine": "python",
            "exact_head": _head(repo),
            "total": 0,
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "by_family": {},
            "artifact_paths": {},
            "repository_status_preserved": False,
            "offline_policy": "suite failed before execution policy could be certified",
            "catalog_semantic_sha256": None,
            "suite_errors": [f"{type(exc).__name__}: {exc}"],
        }

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
