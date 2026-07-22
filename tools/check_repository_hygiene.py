#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.0.1"
CHANNEL = "pre-release"


def load_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def check_repository() -> dict:
    failures: list[str] = []

    root_package = load_json("package.json")
    root_lock = load_json("package-lock.json")
    sdk_package = load_json("sdk/typescript/package.json")
    sdk_lock = load_json("sdk/typescript/package-lock.json")
    release = load_json("release/pre-release.json")

    identities = {
        "installer-package": root_package.get("version"),
        "installer-lock": root_lock.get("version"),
        "typescript-package": sdk_package.get("version"),
        "typescript-lock": sdk_lock.get("version"),
        "release": release.get("version"),
        "version-file": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
    }
    failures.extend(
        f"wrong-version:{name}:{value}"
        for name, value in identities.items()
        if value != VERSION
    )
    if root_package.get("name") != "@syntavra/install" or root_lock.get("name") != "@syntavra/install":
        failures.append("missing-installer-package-identity")
    if sdk_package.get("name") != "@syntavra/sdk" or sdk_lock.get("name") != "@syntavra/sdk":
        failures.append("missing-typescript-sdk-identity")
    if release.get("channel") != CHANNEL or release.get("version_locked") is not True:
        failures.append("release-policy-not-locked")

    sdk_typescript = sdk_lock.get("packages", {}).get("node_modules/typescript", {}).get("version")
    if not isinstance(sdk_typescript, str) or not sdk_typescript:
        failures.append("typescript-lock-missing")
    if "node --test" not in sdk_package.get("scripts", {}).get("test", ""):
        failures.append("typescript-tests-not-wired")

    required = [
        "install/index.mjs",
        "install/index.test.mjs",
        "CONTRIBUTING.md",
        "SUPPORT.md",
        "CODE_OF_CONDUCT.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/dependabot.yml",
        ".github/workflows/codeql.yml",
        ".github/workflows/dependency-review.yml",
        ".github/workflows/validate.yml",
        ".github/workflows/validate-fusion-runtime.yml",
        ".github/workflows/portable-runtime.yml",
        ".github/workflows/pre-release-artifacts.yml",
        "docs/001_PRE_RELEASE.md",
        "docs/ARCHITECTURE.md",
        "docs/UNIFIED_PLAN.md",
        "docs/SECURITY_MODEL.md",
        "docs/ADAPTER_PLATFORM.md",
        "docs/SIGNALBENCH.md",
        "docs/OPERATIONS.md",
        "syntavra_runtime/platform.py",
        "syntavra_runtime/platform_cli.py",
        "syntavra_runtime/runtime_evidence.py",
        "syntavra_runtime/semantic_services.py",
        "syntavra_runtime/session_memory.py",
        "syntavra_runtime/execution_sandbox.py",
        "syntavra_runtime/sandbox_runtime.py",
        "syntavra_runtime/autonomous_agent.py",
        "syntavra_runtime/adapter_runtime.py",
        "syntavra_runtime/headless_runtime.py",
        "syntavra_runtime/interactive_console.py",
        "syntavra_runtime/reliability_lab.py",
        "syntavra_runtime/update_manager.py",
        "tests/runtime/test_syntavra_unified_platform.py",
    ]
    failures.extend(f"missing:{path}" for path in required if not (ROOT / path).is_file())

    workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    workflow_text = "\n".join(path.read_text(encoding="utf-8") for path in workflows)
    if "git push origin HEAD:main" in workflow_text:
        failures.append("workflow-direct-main-push")
    if "npm ci" not in workflow_text:
        failures.append("npm-ci-not-enforced")
    if "npm test" not in workflow_text:
        failures.append("npm-tests-not-enforced")
    if "attest-build-provenance" not in workflow_text:
        failures.append("artifact-provenance-not-enforced")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if "npx @syntavra/install" not in readme:
        failures.append("one-command-install-not-documented")
    if "0.0.1 / pre-release" not in readme:
        failures.append("version-lock-not-documented")

    public_paths = [
        ROOT / "README.md",
        ROOT / "package.json",
        ROOT / "sdk" / "typescript" / "package.json",
        ROOT / "docs" / "001_PRE_RELEASE.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "UNIFIED_PLAN.md",
        ROOT / "docs" / "SECURITY_MODEL.md",
        ROOT / "docs" / "ADAPTER_PLATFORM.md",
        ROOT / "docs" / "SIGNALBENCH.md",
        ROOT / "docs" / "OPERATIONS.md",
    ]
    forbidden = {
        "legacy-product-name": re.compile(r"\bSignalCore\b", re.IGNORECASE),
        "competitive-runtime-version": re.compile(r"Competitive Runtime V\d+", re.IGNORECASE),
        "component-version-suffix": re.compile(r"\b(?:Context Compiler|Memory DAG|Capability Security|Reference Agent) V\d+\b", re.IGNORECASE),
        "milestone-version-range": re.compile(r"\bV8\s*[–-]\s*V20\b", re.IGNORECASE),
    }
    for path in public_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in forbidden.items():
            if pattern.search(text):
                failures.append(f"public-name-policy:{label}:{path.relative_to(ROOT).as_posix()}")

    return {
        "ok": not failures,
        "product": "Syntavra",
        "version": VERSION,
        "channel": CHANNEL,
        "failures": failures,
        "checks": {
            "identities": identities,
            "required_files": len(required),
            "workflow_files": len(workflows),
            "typescript": sdk_typescript,
            "public_identity_files": len(public_paths),
        },
    }


def main() -> int:
    result = check_repository()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
