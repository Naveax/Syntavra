#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.prompt_cache_optimizer import PromptCacheOptimizer

CONTRACT = Path("contracts/python/token-economy-p0-07-cache-eviction-governor-v1.json")
WORKFLOW = Path(".github/workflows/token-economy-p0-06-07-cache-governance.yml")
TEST = Path("tests/runtime/test_cache_eviction_governor.py")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def certify(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    _require(repo == ROOT, "certifier must run against its own checkout")
    contract = json.loads((repo / CONTRACT).read_text(encoding="utf-8"))
    _require(contract["schema_version"] == 1, "contract schema drift")
    _require(contract["claim"] == "TE_P0_07_CACHE_BREAK_EVEN_EVICTION_GOVERNOR_V1", "claim drift")
    _require(contract["strict"] is True, "contract must remain strict")
    _require((repo / TEST).is_file(), "governor regression suite missing")
    workflow = (repo / WORKFLOW).read_text(encoding="utf-8")
    for required in (
        "tests.runtime.test_cache_provider_budget_v1",
        "tools/certify_cache_provider_budget_v1.py",
        "tests.runtime.test_cache_eviction_governor",
        "tools/certify_token_economy_p0_07_cache_eviction_governor.py",
    ):
        _require(required in workflow, f"workflow lost required gate: {required}")

    with tempfile.TemporaryDirectory(prefix="syntavra-te-p0-07-") as td:
        state = Path(td)
        cache = PromptCacheOptimizer(state, max_entries=2)
        def msg(label: str) -> list[dict[str, str]]:
            return [{"role": "system", "content": "stable-" + label}]
        high = cache.plan(msg("high"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.9, rebuild_cost_tokens=100)
        low = cache.plan(msg("low"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.1, rebuild_cost_tokens=100)
        mid = cache.plan(msg("mid"), provider="openai", model="m", ttl_seconds=100, now=1000, reuse_probability=0.5, rebuild_cost_tokens=100)
        plans = json.loads((state / "cache" / "plans.json").read_text(encoding="utf-8"))
        keys = set(plans["plans"])
        _require(cache._cache_key(high) in keys, "high-reuse plan was evicted")
        _require(cache._cache_key(mid) in keys, "middle-value plan was evicted")
        _require(cache._cache_key(low) not in keys, "lowest-value plan remained pinned")
        _require(set(plans) == {"plans", "updated_at"}, "plans.json parity surface was polluted by governance metadata")
        receipt = cache.governance_receipts(limit=1)[0]
        _require(receipt["evicted_keys"] == [cache._cache_key(low)], "eviction receipt mismatch")
        for field in (
            "decision", "reason", "cache_key", "reuse_probability", "ttl_seconds",
            "rebuild_cost_tokens", "value_score", "max_entries", "expired_keys",
            "evicted_keys", "retained_entries",
        ):
            _require(field in receipt, f"receipt field missing: {field}")

        stale = cache.plan(msg("stale"), provider="anthropic", model="m", ttl_seconds=5, now=2000)
        maintenance = cache.maintain(now=2006)
        _require(cache._cache_key(stale) in maintenance["expired_keys"], "expired plan not reclaimed")

    exact_head = _head(repo)
    return {
        "ok": True,
        "schema_version": 1,
        "claim": contract["claim"],
        "exact_head": exact_head,
        "te_p0_06_prerequisite": "REVALIDATED_BY_WORKFLOW",
        "te_p0_07": "DETERMINISTIC_GOVERNOR_TESTED",
        "provider_proof": False,
        "claim_boundary": contract["claim_boundary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        report = certify(Path(args.repo))
    except Exception as exc:
        report = {
            "ok": False,
            "schema_version": 1,
            "claim": "TE_P0_07_CACHE_BREAK_EVEN_EVICTION_GOVERNOR_V1",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
