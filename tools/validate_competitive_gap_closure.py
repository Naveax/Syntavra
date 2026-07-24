#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from syntavra_runtime.agent_config_auditor import (
    MAX_PATH_CANDIDATE_CHARS,
    AgentConfigAuditor,
    _iter_path_candidates,
)
from syntavra_runtime.code_intelligence import CodeIntelligenceIndex
from syntavra_runtime.command_compactors import CommandCompactorRegistry
from syntavra_runtime.command_rewriter import CommandRewriteEngine
from syntavra_runtime.competitive_fabric import PlatformPlanBuilder
from syntavra_runtime.provider_account_pool import ProviderAccountPool


def _small_project(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "tests").mkdir()
    (root / "pkg" / "base.py").write_text(
        "class Base:\n    def run(self):\n        return helper()\n\ndef helper():\n    return 1\n",
        encoding="utf-8",
    )
    (root / "pkg" / "child.py").write_text(
        "from pkg.base import Base, helper\nclass Child(Base):\n    def work(self):\n        return helper()\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_base.py").write_text(
        "from pkg.base import helper\ndef test_helper(): assert helper() == 1\n",
        encoding="utf-8",
    )


def main() -> int:
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: object = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    compactors = CommandCompactorRegistry().manifest()
    rewrites = CommandRewriteEngine().manifest()
    check("command_compactors_100_plus", compactors["count"] >= 120, compactors["count"])
    check("command_rewrites_100_plus", rewrites["count"] >= 110, rewrites["count"])
    wrapped = CommandRewriteEngine().rewrite("env CI=1 git status")
    rejected = CommandRewriteEngine().rewrite("sudo -u root git status")
    check("safe_wrapper_rewriting", wrapped.changed and wrapped.rewritten[:2] == ("env", "CI=1"))
    check("wrapper_options_fail_closed", not rejected.changed and not rejected.safe)

    workflow = (ROOT / ".github/workflows/security-alert-triage.yml").read_text(encoding="utf-8")
    check("dynamic_code_scanning_enumeration", "/code-scanning/alerts?" in workflow and 'for state in ("open", "fixed", "dismissed")' in workflow)
    check("no_hardcoded_alert_ceiling", "range(1, 7)" not in workflow and "range(1, 6)" not in workflow)
    check(
        "codeql_alert_6_main_closure_gate",
        "Enforce CodeQL alert 6 closure on main" in workflow
        and 'rule.get("id") != "py/redos"' in workflow
        and "CODEQL_ALERT_6_FIXED_ON_MAIN" in workflow,
    )

    check("redos_scanner_materialization_bound", 0 < MAX_PATH_CANDIDATE_CHARS <= 4_096, MAX_PATH_CANDIDATE_CHARS)
    oversized = "root/" + "x" * (MAX_PATH_CANDIDATE_CHARS + 1)
    check("redos_oversized_candidate_rejected", list(_iter_path_candidates(oversized)) == [])

    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory)
        (project / "AGENTS.md").write_text(
            "Read missing/file.py.\n"
            + ("segment/" + "x" * 64) * 14_000
            + "\n",
            encoding="utf-8",
        )
        audit = AgentConfigAuditor(project).audit()
        stale = [row for row in audit["findings"] if row["kind"] == "stale-path"]
        check("redos_safe_agent_config_scan", len(stale) == 1 and stale[0]["message"].endswith("missing/file.py"))

    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory)
        _small_project(project)
        cache = project / ".syntavra" / "structural.sqlite3"
        first = CodeIntelligenceIndex(project)
        first.build_incremental(cache)
        second = CodeIntelligenceIndex(project)
        second.build_incremental(cache)
        check("language_registry_25_plus", second.parser_manifest()["declared_language_count"] >= 25, second.parser_manifest()["declared_language_count"])
        check("incremental_code_index_reuse", second.last_build_stats["parsed_files"] == 0 and second.last_build_stats["reused_files"] >= 3, second.last_build_stats)
        check("implementation_discovery", second.implementations("Base")["implementation_count"] >= 1)
        check("blast_radius", bool(second.blast_radius("helper")["impacted_paths"]))

        pool = ProviderAccountPool(project / ".syntavra" / "provider-accounts.sqlite3")
        pool.register("openai", "subscription", credential_ref="env:OPENAI_API_KEY", subscription=True, priority=10)
        pool.register("openai", "backup", credential_ref="keyring:syntavra/openai-backup", priority=1)
        raw_secret_rejected = False
        try:
            pool.register("openai", "invalid", credential_ref="sk-proj-" + "x" * 30)
        except ValueError:
            raw_secret_rejected = True
        models = [{"provider": "openai", "model": "reasoner", "quality": 0.9, "max_complexity": "reasoning", "context_window": 200000}]
        initial = pool.route("security root cause", models, now=100)
        for offset in range(3):
            pool.record_result("openai", "subscription", success=False, now=100 + offset)
        fallback = pool.route("security root cause", models, now=103)
        check("raw_provider_secret_rejected", raw_secret_rejected)
        check("provider_account_failover", initial.account == "subscription" and fallback.account == "backup")

    claude = PlatformPlanBuilder().plan("claude-code", project=ROOT)
    config = next(row["merge"] for row in claude["files"] if row.get("merge"))
    required_hooks = {"PreToolUse", "PostToolUse", "UserPromptSubmit", "PreCompact", "SessionStart", "Stop", "SessionEnd"}
    check("claude_native_hook_lifecycle", required_hooks <= set(config.get("hooks") or {}), sorted((config.get("hooks") or {}).keys()))
    check("claude_statusline", config.get("statusLine", {}).get("command") == "syntavra run statusline")

    result = {
        "ok": all(bool(row["passed"]) for row in checks),
        "product": "Syntavra",
        "version": "0.0.1",
        "channel": "pre-release",
        "checks": checks,
        "external_gates": {
            "registry_publication": "CREDENTIAL_GATED_NOT_EXECUTED",
            "provider_billed_signalbench": "PROVIDER_ACCOUNT_AND_BUDGET_GATED",
            "independent_validation": "THIRD_PARTY_GATED",
            "public_maturity": "TIME_AND_ADOPTION_GATED",
        },
        "claim_boundary": "Technical gap closure is internally verified; external superiority remains unproven.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
