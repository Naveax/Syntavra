#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "docs" / "claims" / "claims.json"
README = ROOT / "README.md"
PROFILE = ROOT / "skills" / "syntavra" / "profiles" / "roblox_studio"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DETERMINISTIC_SCOPE = "DETERMINISTIC_SIMULATED_CORRECTNESS_ONLY"


def source_tree_hash() -> str:
    hasher = hashlib.sha256()
    paths: list[Path] = []
    for base in (PROFILE, ROOT / "tests" / "roblox_profile"):
        paths.extend(
            path
            for path in base.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".json"}
            and "__pycache__" not in path.parts
            and path.name != "MANIFEST.sha256"
        )
    paths.append(ROOT / "benchmarks" / "roblox_profile_benchmark.py")
    for path in sorted(paths):
        hasher.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def markdown_links(text: str) -> list[str]:
    return [
        target
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        if not target.startswith(("http://", "https://", "#", "mailto:"))
    ]


def main() -> int:
    registry = json.loads(CLAIMS.read_text(encoding="utf-8"))
    readme_text = README.read_text(encoding="utf-8")
    failures: list[str] = []

    for target in markdown_links(readme_text):
        clean = unquote(target.split("#", 1)[0])
        if clean and not (ROOT / clean).exists():
            failures.append(f"broken README link: {target}")

    for claim in registry["claims"]:
        marker = f"[claim:{claim['claim_id']}]"
        if marker not in readme_text:
            failures.append(f"README marker missing: {claim['claim_id']}")
        for path in claim["source_paths"] + claim["artifact_paths"]:
            if not (ROOT / path).exists():
                failures.append(f"claim path missing: {claim['claim_id']} -> {path}")
        if claim["status"] not in {
            "IMPLEMENTED",
            "INTERNALLY_VERIFIED",
            "SIMULATED",
            "LIVE_INTEGRATION_VERIFIED",
            "PUBLICLY_BENCHMARKED",
            "INDEPENDENTLY_REPRODUCED",
            "PLANNED",
        }:
            failures.append(f"invalid maturity label: {claim['claim_id']}")

    test_artifact = json.loads(
        (ROOT / "benchmarks" / "results" / "roblox-profile" / "profile-tests.json").read_text(encoding="utf-8")
    )
    actual_tests = unittest.TestLoader().discover(str(ROOT / "tests" / "roblox_profile")).countTestCases()
    if test_artifact["tests_run"] != actual_tests:
        failures.append(f"test count drift: artifact={test_artifact['tests_run']} actual={actual_tests}")
    if test_artifact.get("status") != "PASS" or test_artifact.get("failures") != 0 or test_artifact.get("errors") != 0:
        failures.append("profile test artifact is not a clean pass")

    benchmark = json.loads(
        (ROOT / "benchmarks" / "results" / "roblox-profile" / "simulated-50.json").read_text(encoding="utf-8")
    )
    current_hash = source_tree_hash()
    if registry.get("source_tree_hash") != current_hash:
        failures.append("claim registry source_tree_hash mismatch")
    if benchmark.get("source_tree_hash") != current_hash:
        failures.append("benchmark source_tree_hash mismatch")
    if test_artifact.get("source_tree_hash") not in {None, current_hash}:
        failures.append("profile test source_tree_hash mismatch")

    expected_commit = registry.get("source_commit", "")
    if not COMMIT_RE.fullmatch(expected_commit):
        failures.append("claim registry source_commit is not a full immutable SHA")
    for artifact in (test_artifact, benchmark):
        if artifact.get("source_commit") != expected_commit:
            failures.append("source commit mismatch between claims and artifacts")

    if benchmark.get("mode") != "simulated" or benchmark.get("maturity") != "SIMULATED_VERIFIED":
        failures.append("simulated benchmark labeling is invalid")
    if benchmark.get("cases") != 50 or benchmark.get("task_success") != 50 or benchmark.get("validator_success") != 50:
        failures.append("simulated benchmark result is incomplete")
    if benchmark.get("unsafe_execution") != 0:
        failures.append("unsafe execution claim is not zero")
    if benchmark.get("measurement_scope") != DETERMINISTIC_SCOPE:
        failures.append("simulated receipt is not labeled as deterministic correctness-only evidence")
    if benchmark.get("latency_p50") is not None or benchmark.get("latency_p95") is not None:
        failures.append("volatile latency values must not be committed as deterministic evidence")
    if benchmark.get("hardware") != "not-recorded" or benchmark.get("operating_system") != "not-recorded" or benchmark.get("python_version") != "not-recorded":
        failures.append("volatile environment identity must not be committed in deterministic evidence")

    result = {
        "ok": not failures,
        "source_tree_hash": current_hash,
        "source_commit": expected_commit,
        "failures": failures,
        "claims": len(registry["claims"]),
        "tests": actual_tests,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
