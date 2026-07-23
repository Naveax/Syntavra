from __future__ import annotations

from pathlib import Path
from typing import Any

from .command_compactors import CommandCompactorRegistry
from .command_rewriter import CommandRewriteEngine
from .host_adapters import KNOWN_HOSTS, coverage_report
from .optimization_modes import MODES
from .provider_registry import default_provider_registry


def manifest(project: Path | None = None) -> dict[str, Any]:
    root = Path(project or ".").resolve(strict=False)
    feature_groups = {
        "pre_execution": ["pretool-command-rewrite", "instant-optimization-modes", "prompt-cache-layout", "prompt-cache-expiry"],
        "output": ["exact-first-externalization", "command-specific-compaction", "secret-redaction", "lossless-wire-format"],
        "repository": ["live-watcher", "incremental-reindex", "call-hierarchy", "class-hierarchy", "dead-code", "untested-symbols", "pagerank", "hotspots", "cycles", "coupling", "module-boundaries", "signal-chain", "duplicates", "provenance", "pr-risk", "delete-safe", "refactor-plan", "cross-language-anti-patterns", "cross-repo-contracts"],
        "memory": ["llm-or-heuristic-extraction", "validity-roi-ranking", "bm25-cosine-rerank", "embedding-backfill", "jsonl-export", "critical-notifications"],
        "routing": ["quota-aware-fallback", "rate-limit-switching", "complexity-model-routing", "automatic-subtask-delegation", "short-handoff-subagents", "provider-gateway-presets"],
        "experience": ["host-statusline", "live-savings-badge", "local-web-dashboard", "pwa-dashboard", "vscode-extension", "native-rust-companion", "agent-config-auditor", "transcript-opportunity-miner"],
        "evidence": ["provider-billed-signalbench", "provider-receipt-gate", "registry-publication-readiness", "fail-closed-public-claims"],
    }
    files = {
        "vscode_extension": root / "integrations/vscode-syntavra/package.json",
        "native_binary": root / "native/syntavra-native/Cargo.toml",
        "publish_readiness": root / "release/publish-readiness.json",
    }
    providers = default_provider_registry().catalog()["providers"]
    body = {
        "version": "0.0.1",
        "channel": "pre-release",
        "feature_groups": feature_groups,
        "feature_count": sum(len(rows) for rows in feature_groups.values()),
        "optimization_modes": sorted(MODES),
        "rewrite_rules": CommandRewriteEngine().manifest()["count"],
        "compactors": CommandCompactorRegistry().manifest()["count"],
        "hosts": len(KNOWN_HOSTS),
        "controlled_hosts": coverage_report()["controlled_hosts"],
        "provider_presets": len(providers),
        "artifacts": {name: path.is_file() for name, path in files.items()},
        "external_claims": {
            "registry_published": False,
            "competitor_superiority": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "live_certification": "LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN",
        },
    }
    body["ok"] = bool(
        body["rewrite_rules"] >= 60
        and body["compactors"] >= 60
        and body["hosts"] >= 30
        and body["provider_presets"] >= 40
        and all(body["artifacts"].values())
    )
    return body
