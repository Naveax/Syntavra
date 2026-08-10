#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.provider_gateway import ProviderGateway
from syntavra_runtime.usage_receipt_ledger import UsageReceiptLedger
from syntavra_runtime.util import stable_project_id


def _run(
    argv: list[str],
    *,
    cwd: Path,
    expected_codes: tuple[int, ...] = (0,),
) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode not in expected_codes:
        raise RuntimeError(
            json.dumps(
                {
                    "argv": argv,
                    "returncode": completed.returncode,
                    "expected_codes": expected_codes,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
                ensure_ascii=False,
            )
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {argv!r}: {completed.stdout!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object from {argv!r}: {payload!r}")
    return completed.returncode, payload


def _fixture(project: Path, state: Path) -> None:
    project.mkdir(parents=True)
    (project / ".git").mkdir()
    state.mkdir(parents=True)
    evidence = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
    usage = UsageReceiptLedger(state / "usage-receipts.sqlite3")
    gateway = ProviderGateway(state / "provider-gateway.sqlite3", evidence=evidence, usage_ledger=usage)
    plan = gateway.prepare(
        "openai",
        {
            "messages": [
                {"role": "system", "content": "Return one stable answer."},
                {"role": "user", "content": "provider parity fixture"},
            ],
            "temperature": 0,
            "request_id": "volatile-request-id",
        },
        model="gpt-test",
        cache_policy="read-write",
        replay_ttl_seconds=600,
        prompt_cache_ttl_seconds=300,
    )
    gateway.capture(
        plan,
        {
            "id": "provider-response-id",
            "choices": [
                {"message": {"role": "assistant", "content": "stable provider response"}}
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        },
        preview_bytes=4096,
        store_replay=True,
        replay_ttl_seconds=600,
    )
    replay = gateway.replay(plan)
    if replay is None:
        raise RuntimeError("provider replay fixture was not stored")


def verify(selector: Path) -> dict[str, Any]:
    selector = selector.resolve(strict=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = root / "MiXeD-Project"
        state = root / "state"
        _fixture(project, state)

        python_prefix = [
            sys.executable,
            "-m",
            "syntavra_runtime",
            "--project",
            str(project),
            "--state-root",
            str(state),
        ]
        rust_prefix = [
            str(selector),
            "--engine",
            "rust",
            "--project",
            str(project),
            "--state-root",
            str(state),
        ]

        python_stats_code, python_stats = _run([*python_prefix, "provider", "stats"], cwd=ROOT)
        rust_stats_code, rust_stats = _run([*rust_prefix, "provider", "stats"], cwd=ROOT)
        python_verify_code, python_verify = _run([*python_prefix, "provider", "verify"], cwd=ROOT)
        rust_verify_code, rust_verify = _run([*rust_prefix, "provider", "verify"], cwd=ROOT)

        stats_equal = python_stats == rust_stats and python_stats_code == rust_stats_code == 0
        verify_equal = python_verify == rust_verify and python_verify_code == rust_verify_code == 0
        expected_stats = {
            "requests": 1,
            "cache_entries": 1,
            "replay_hits": 1,
            "active_cache_entries": 1,
            "providers": {"openai": 1},
            "database_integrity": True,
        }
        expected_verify = {
            "ok": True,
            "entries": 1,
            "reasons": [],
            "database_integrity": True,
        }

        with sqlite3.connect(state / "provider-gateway.sqlite3") as database:
            database.execute(
                "UPDATE provider_response_cache SET response_hash=?",
                ("0" * 64,),
            )
            database.commit()

        python_bad_code, python_bad_verify = _run(
            [*python_prefix, "provider", "verify"],
            cwd=ROOT,
            expected_codes=(3,),
        )
        rust_bad_code, rust_bad_verify = _run(
            [*rust_prefix, "provider", "verify"],
            cwd=ROOT,
            expected_codes=(3,),
        )
        negative_equal = (
            python_bad_code == rust_bad_code == 3
            and python_bad_verify == rust_bad_verify
            and python_bad_verify.get("ok") is False
            and python_bad_verify.get("entries") == 1
            and python_bad_verify.get("database_integrity") is True
            and isinstance(python_bad_verify.get("reasons"), list)
            and len(python_bad_verify["reasons"]) == 1
            and str(python_bad_verify["reasons"][0]).startswith("response-hash:")
        )

        return {
            "ok": stats_equal
            and verify_equal
            and negative_equal
            and python_stats == expected_stats
            and python_verify == expected_verify,
            "python_stats": python_stats,
            "rust_stats": rust_stats,
            "python_verify": python_verify,
            "rust_verify": rust_verify,
            "python_bad_verify": python_bad_verify,
            "rust_bad_verify": rust_bad_verify,
            "python_bad_exit": python_bad_code,
            "rust_bad_exit": rust_bad_code,
            "stats_equal": stats_equal,
            "verify_equal": verify_equal,
            "negative_equal": negative_equal,
            "expected_stats_matched": python_stats == expected_stats,
            "expected_verify_matched": python_verify == expected_verify,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify R31 provider stats/verify Python-Rust success and failure parity"
    )
    parser.add_argument("--selector", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = verify(Path(args.selector))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
