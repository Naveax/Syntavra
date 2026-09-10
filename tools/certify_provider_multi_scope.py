#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from syntavra_runtime.provider_e5 import (
    certify_external_superiority,
    certify_multi_scope_superiority,
    certify_provider_e5,
)
from syntavra_runtime.util import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "contracts" / "python" / "token-economy-frozen-workloads-v1.json"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def certify(replay_paths: list[Path], contract_path: Path, minimum_scopes: int) -> dict[str, Any]:
    if len(replay_paths) < minimum_scopes:
        raise ValueError(f"at least {minimum_scopes} raw provider replay files are required")
    contract = _json(contract_path)
    scopes: list[dict[str, Any]] = []
    for path in replay_paths:
        replay = _json(path)
        e5 = certify_provider_e5(replay, contract)
        superiority = certify_external_superiority(e5, replay)
        scopes.append(
            {
                "path": str(path),
                "provider_e5": e5,
                "external_superiority": superiority,
            }
        )
    aggregate = certify_multi_scope_superiority(
        [row["external_superiority"] for row in scopes],
        minimum_independent_scopes=minimum_scopes,
    )
    return {
        "schema_version": 1,
        "claim": "PROVIDER_MULTI_SCOPE_SUPERIORITY_CERTIFICATE",
        "claim_boundary": (
            "Every supplied raw replay is independently re-certified at E5 before multi-scope promotion. "
            "The result remains bounded to the supplied provider/model scopes and frozen B0-B9 corpus."
        ),
        "minimum_independent_scopes": minimum_scopes,
        "scope_count": len(scopes),
        "scopes": scopes,
        "multi_scope": aggregate,
        "score": aggregate["score"],
        "ok": bool(aggregate["multi_scope_superiority_proven"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-certify multiple raw E5 replays and issue a bounded multi-scope superiority certificate."
    )
    parser.add_argument("replays", type=Path, nargs="+")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--minimum-scopes", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-multi-scope", action="store_true")
    args = parser.parse_args()
    if args.minimum_scopes < 2:
        raise SystemExit("--minimum-scopes must be >= 2")
    report = certify(args.replays, args.contract, args.minimum_scopes)
    if args.output:
        atomic_write_json(args.output, report, mode=0o644)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.require_multi_scope and not report["ok"]:
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
