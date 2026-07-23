#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import py_compile
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "syntavra"
EXPECTED_VERSION = "0.0.1"
EXPECTED_CHANNEL = "pre-release"

REQUIRED = [
    ROOT / "README.md",
    ROOT / "CHANGELOG.md",
    ROOT / "LICENSE",
    ROOT / "MANIFEST.sha256",
    ROOT / "COMPATIBILITY.md",
    ROOT / "AGENTS.md",
    ROOT / "llms.txt",
    ROOT / "gemini-extension.json",
    ROOT / ".claude-plugin" / "marketplace.json",
    ROOT / "pyproject.toml",
    ROOT / "package.json",
    ROOT / "sdk" / "typescript" / "package.json",
    ROOT / "release" / "pre-release.json",
    ROOT / "docs" / "001_PRE_RELEASE.md",
    ROOT / "docs" / "ARCHITECTURE.md",
    ROOT / "docs" / "UNIFIED_PLAN.md",
    ROOT / "docs" / "SECURITY_MODEL.md",
    ROOT / "docs" / "ADAPTER_PLATFORM.md",
    ROOT / "docs" / "SIGNALBENCH.md",
    ROOT / "docs" / "OPERATIONS.md",
    ROOT / "docs" / "TOKEN_SAVER_PLAN_001.md",
    ROOT / "docs" / "COMPLETE_COMPETITIVE_FEATURE_SET_001.md",
    ROOT / "benchmarks" / "syntavra_component_benchmark.py",
    ROOT / "benchmarks" / "signalbench" / "README.md",
    ROOT / "benchmarks" / "signalbench" / "tasks.example.json",
    ROOT / "benchmarks" / "signalbench" / "arms.example.json",
    ROOT / "benchmarks" / "signalbench" / "adapters" / "external_cli.py",
    ROOT / "schemas" / "token-attribution-receipt.json",
    ROOT / "syntavra_runtime" / "__init__.py",
    ROOT / "syntavra_runtime" / "unified_cli.py",
    ROOT / "syntavra_runtime" / "prerelease_cli.py",
    ROOT / "syntavra_runtime" / "platform.py",
    ROOT / "syntavra_runtime" / "platform_cli.py",
    ROOT / "syntavra_runtime" / "artifacts.py",
    ROOT / "syntavra_runtime" / "semantic_intelligence.py",
    ROOT / "syntavra_runtime" / "semantic_services.py",
    ROOT / "syntavra_runtime" / "runtime_evidence.py",
    ROOT / "syntavra_runtime" / "session_memory.py",
    ROOT / "syntavra_runtime" / "capability_security.py",
    ROOT / "syntavra_runtime" / "execution_sandbox.py",
    ROOT / "syntavra_runtime" / "sandbox_runtime.py",
    ROOT / "syntavra_runtime" / "autonomous_agent.py",
    ROOT / "syntavra_runtime" / "adapter_platform.py",
    ROOT / "syntavra_runtime" / "adapter_runtime.py",
    ROOT / "syntavra_runtime" / "secretless_gateway.py",
    ROOT / "syntavra_runtime" / "headless_runtime.py",
    ROOT / "syntavra_runtime" / "interactive_console.py",
    ROOT / "syntavra_runtime" / "reliability_lab.py",
    ROOT / "syntavra_runtime" / "update_manager.py",
    ROOT / "syntavra_runtime" / "paired_benchmark.py",
    ROOT / "syntavra_runtime" / "product_surface.py",
    ROOT / "syntavra_runtime" / "tool_registry.py",
    ROOT / "syntavra_runtime" / "mcp_application.py",
    ROOT / "syntavra_runtime" / "token_attribution.py",
    ROOT / "syntavra_runtime" / "command_compactors.py",
    ROOT / "syntavra_runtime" / "context_pack.py",
    ROOT / "syntavra_runtime" / "optimization_modes.py",
    ROOT / "syntavra_runtime" / "command_rewriter.py",
    ROOT / "syntavra_runtime" / "transcript_miner.py",
    ROOT / "syntavra_runtime" / "prompt_cache_optimizer.py",
    ROOT / "syntavra_runtime" / "repository_watcher.py",
    ROOT / "syntavra_runtime" / "background_workers.py",
    ROOT / "syntavra_runtime" / "dashboard.py",
    ROOT / "syntavra_runtime" / "agent_config_auditor.py",
    ROOT / "syntavra_runtime" / "secret_redaction.py",
    ROOT / "syntavra_runtime" / "wire_format.py",
    ROOT / "syntavra_runtime" / "code_intelligence.py",
    ROOT / "syntavra_runtime" / "memory_intelligence.py",
    ROOT / "syntavra_runtime" / "notifications.py",
    ROOT / "syntavra_runtime" / "adaptive_provider_router.py",
    ROOT / "syntavra_runtime" / "subtask_router.py",
    ROOT / "syntavra_runtime" / "competitive_features.py",
    ROOT / "native" / "syntavra-native" / "Cargo.toml",
    ROOT / "native" / "syntavra-native" / "src" / "main.rs",
    ROOT / "integrations" / "vscode-syntavra" / "package.json",
    ROOT / "integrations" / "vscode-syntavra" / "extension.js",
    ROOT / "integrations" / "vscode-syntavra" / "extension.test.mjs",
    ROOT / "release" / "publish-readiness.json",
    ROOT / "syntavra_runtime" / "release_identity.py",
    ROOT / "syntavra_runtime" / "bundled_skill" / "SKILL.md",
    ROOT / "syntavra_runtime" / "bundled_skill" / "hosts.json",
    ROOT / "signalcore_runtime" / "__init__.py",
    ROOT / "tests" / "runtime" / "test_syntavra_unified_platform.py",
    ROOT / "tests" / "runtime" / "test_token_saver_unification_v001.py",
    ROOT / "tests" / "runtime" / "test_complete_competitive_features_v001.py",
    SKILL / "SKILL.md",
    SKILL / "data" / "platforms.json",
    SKILL / "scripts" / "platforms.py",
    SKILL / "profiles" / "roblox_studio" / "profile.json",
    ROOT / "tools" / "validate_runtime.py",
    ROOT / "tools" / "validate_release.py",
    ROOT / "tools" / "check_repository_hygiene.py",
]

ACTUAL_SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{20,})")
GENERATED_FILES = {
    "fusion-release-smoke.json", "release-smoke.json", "platform-registry.json",
    "native-dry-run.json", "syntavra-component-measurement.json",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _skill_version(text: str) -> str | None:
    match = re.search(r'^version:\s*["\']?([^"\'\s]+)', text, flags=re.MULTILINE)
    return match.group(1) if match else None


def _is_generated_path(relative: Path) -> bool:
    return (
        bool(relative.parts) and relative.parts[0] in {".git", ".syntavra", "build", "dist"}
    ) or any(part in {"__pycache__", ".pytest_cache"} or part.endswith(".egg-info") for part in relative.parts)


def _source_files() -> list[Path]:
    roots = [SKILL / "scripts", SKILL / "profiles", ROOT / "syntavra_runtime", ROOT / "signalcore_runtime", ROOT / "tools", ROOT / "benchmarks"]
    return sorted({path for base in roots if base.exists() for path in base.rglob("*.py")})


def _scan_files() -> list[Path]:
    skipped_suffixes = {".pyc", ".sqlite3", ".db", ".log", ".zip", ".gz", ".xz", ".png", ".jpg", ".jpeg", ".webp"}
    return [
        path for path in ROOT.rglob("*")
        if path.is_file()
        and not _is_generated_path(path.relative_to(ROOT))
        and path.suffix.casefold() not in skipped_suffixes
    ]


def _manifest_candidates() -> list[Path]:
    candidates: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if _is_generated_path(relative):
            continue
        if (path.name == "MANIFEST.sha256" and path.parent == ROOT) or path.name in GENERATED_FILES or path.suffix == ".pyc":
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda value: value.relative_to(ROOT).as_posix())


def _verify_manifest() -> tuple[bool, str]:
    manifest = ROOT / "MANIFEST.sha256"
    failures: list[str] = []
    entries: dict[str, str] = {}
    for number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            digest, relative = raw.split("  ", 1)
        except ValueError:
            failures.append(f"malformed-line:{number}")
            continue
        if relative in entries:
            failures.append(f"duplicate:{relative}")
            continue
        entries[relative] = digest
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            failures.append(f"hash-mismatch:{relative}")
    expected = {path.relative_to(ROOT).as_posix() for path in _manifest_candidates()}
    present = set(entries)
    failures.extend(f"unlisted:{relative}" for relative in sorted(expected - present))
    failures.extend(f"unexpected:{relative}" for relative in sorted(present - expected))
    return not failures, ", ".join(failures[:30])


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED if not path.is_file()]
    checks.append(("required_files", not missing, ", ".join(missing)))

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    bundled_skill = (ROOT / "syntavra_runtime" / "bundled_skill" / "SKILL.md").read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    marketplace = _json(ROOT / ".claude-plugin" / "marketplace.json")
    gemini = _json(ROOT / "gemini-extension.json")
    codemeta = _json(ROOT / "codemeta.json")
    installer = _json(ROOT / "package.json")
    typescript = _json(ROOT / "sdk" / "typescript" / "package.json")
    prerelease = _json(ROOT / "release" / "pre-release.json")

    versions = {
        "VERSION": version,
        "skill": _skill_version(skill_text),
        "bundled_skill": _skill_version(bundled_skill),
        "pyproject": pyproject.get("project", {}).get("version"),
        "installer": installer.get("version"),
        "typescript": typescript.get("version"),
        "marketplace": marketplace.get("version"),
        "gemini": gemini.get("version"),
        "codemeta": codemeta.get("version"),
        "prerelease": prerelease.get("version"),
    }
    checks.append(("version_consistency", all(value == EXPECTED_VERSION for value in versions.values()), json.dumps(versions, sort_keys=True)))
    checks.append(("product_identity", pyproject.get("project", {}).get("name") == "syntavra-runtime" and installer.get("name") == "@syntavra/install" and typescript.get("name") == "@syntavra/sdk" and prerelease.get("product") == "Syntavra", "canonical package identities"))
    checks.append(("pre_release_identity", prerelease.get("channel") == EXPECTED_CHANNEL and prerelease.get("publish_as_prerelease") is True and prerelease.get("version_locked") is True and prerelease.get("stable") is False, json.dumps(prerelease, sort_keys=True)))
    checks.append(("pre_alpha_classifier", "Development Status :: 2 - Pre-Alpha" in pyproject.get("project", {}).get("classifiers", []), "PEP 301 pre-alpha"))
    checks.append(("skill_identity", "name: syntavra" in skill_text and "version_locked: true" in skill_text, "canonical locked skill"))
    checks.append(("build_backend", pyproject.get("build-system", {}).get("build-backend") == "setuptools.build_meta", "PEP 517 wheel"))

    platforms = _json(SKILL / "data" / "platforms.json")
    ids = [item["id"] for item in platforms["platforms"]]
    checks.append(("platform_registry", len(ids) >= 20 and len(ids) == len(set(ids)), f"platform_count={len(ids)}"))
    required_hosts = {"codex", "claude-code", "gemini-cli", "windsurf", "opencode", "vscode-copilot"}
    checks.append(("native_core", required_hosts.issubset(ids), f"missing={sorted(required_hosts - set(ids))}"))

    from syntavra_runtime.competitive_features import manifest as competitive_feature_manifest
    from syntavra_runtime.command_compactors import CommandCompactorRegistry
    from syntavra_runtime.command_rewriter import CommandRewriteEngine
    from syntavra_runtime.host_adapters import coverage_report
    from syntavra_runtime.provider_registry import default_provider_registry
    feature_manifest = competitive_feature_manifest(ROOT)
    checks.append(("competitive_feature_manifest", bool(feature_manifest.get("ok")), json.dumps(feature_manifest.get("gates", {}), sort_keys=True)))
    checks.append(("pretool_rewrite_coverage", CommandRewriteEngine().manifest()["count"] >= 60, str(CommandRewriteEngine().manifest()["count"])))
    checks.append(("command_compactor_coverage", CommandCompactorRegistry().manifest()["count"] >= 60, str(CommandCompactorRegistry().manifest()["count"])))
    host_coverage = coverage_report()
    checks.append(("host_installation_coverage", host_coverage["controlled_hosts"] >= 30, json.dumps(host_coverage, sort_keys=True)))
    provider_count = len(default_provider_registry().catalog()["providers"])
    checks.append(("provider_gateway_coverage", provider_count >= 40, f"providers={provider_count}"))
    readiness = _json(ROOT / "release" / "publish-readiness.json")
    publication_targets = [readiness.get(name, {}) for name in ("python", "npm", "vscode", "native")]
    checks.append(("publication_claim_boundary", readiness.get("version") == EXPECTED_VERSION and len(publication_targets) == 4 and all(isinstance(row, dict) and row.get("published") is False for row in publication_targets), "publication remains credential-gated"))

    roblox = _json(SKILL / "profiles" / "roblox_studio" / "profile.json")
    activation = roblox.get("activation", {})
    checks.append(("roblox_profile_hidden", roblox.get("discoverable") is False and roblox.get("direct_invocation") is False, ""))
    checks.append(("roblox_profile_studio_only", activation.get("mode") == "signed_studio_session" and activation.get("allow_cli") is False and activation.get("allow_ide") is False, ""))
    checks.append(("roblox_profile_fail_closed", activation.get("require_process_attestation") is True and activation.get("single_use_nonce") is True, ""))
    checks.append(("pairing_key_not_vendored", not any(path.name == "pairing.key" for path in ROOT.rglob("*")), ""))

    forbidden_paths: list[str] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in {".git", ".syntavra"} for part in relative.parts):
            continue
        if path.name in {".syntavra-direct", ".syntavra-transfer"} or path.match("payload-*.b64"):
            forbidden_paths.append(str(relative))
    checks.append(("no_transfer_payloads", not forbidden_paths, ", ".join(forbidden_paths)))

    try:
        source_files = _source_files()
        for path in source_files:
            py_compile.compile(str(path), doraise=True)
        checks.append(("python_compile", True, f"compiled={len(source_files)}"))
    except Exception as exc:
        checks.append(("python_compile", False, f"{type(exc).__name__}: {exc}"))

    secret_hits: list[str] = []
    for path in _scan_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if ACTUAL_SECRET.search(text):
            secret_hits.append(str(path.relative_to(ROOT)))
    checks.append(("secret_scan", not secret_hits, ", ".join(secret_hits)))

    for tier in ("1x", "20x", "30x", "100x"):
        path = ROOT / "benchmarks" / "configs" / f"{tier}.json"
        try:
            config = _json(path)
            config_ok = config.get("schema_version") == 2 and "observed_baseline" in config
            detail = f"schema={config.get('schema_version')}"
        except (OSError, json.JSONDecodeError) as exc:
            config_ok = False
            detail = str(exc)
        checks.append((f"benchmark_config_{tier}", config_ok, detail))

    manifest_ok, manifest_detail = _verify_manifest()
    checks.append(("release_manifest", manifest_ok, manifest_detail))

    from tools.check_repository_hygiene import check_repository
    hygiene = check_repository()
    checks.append(("repository_hygiene", bool(hygiene.get("ok")), ", ".join(hygiene.get("failures", []))))

    result = {
        "ok": all(passed for _, passed, _ in checks),
        "product": "Syntavra",
        "version": version,
        "release_channel": EXPECTED_CHANNEL,
        "checks": [{"name": name, "passed": passed, **({"detail": detail} if detail else {})} for name, passed, detail in checks],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
