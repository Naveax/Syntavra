#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from syntavra_runtime.provider_e5 import certify_external_superiority, certify_provider_e5
from syntavra_runtime.util import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "contracts" / "python" / "token-economy-frozen-workloads-v1.json"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def certify(replay_path: Path, contract_path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    replay = _json(replay_path)
    contract = _json(contract_path)
    e5 = certify_provider_e5(replay, contract)
    superiority = certify_external_superiority(e5, replay)
    return {
        "schema_version": 1,
        "claim": "PROVIDER_E5_AND_SUPERIORITY_CERTIFICATE",
        "claim_boundary": "Provider E5 evidence validity and paired superiority are deliberately independent gates.",
        "provider_e5": e5,
        "external_superiority": superiority,
        "provider_proof_score": e5["score"],
        "external_superiority_score": superiority["score"],
        "ok": bool(e5["provider_evidence_complete"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Syntavra E5 provider evidence and scoped external superiority.")
    parser.add_argument("replay", type=Path)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-e5", action="store_true")
    parser.add_argument("--require-superiority", action="store_true")
    args = parser.parse_args()

    report = certify(args.replay, args.contract)
    if args.output:
        atomic_write_json(args.output, report, mode=0o644)
    print(json.dumps(report, indent=2, sort_keys=True))

    if args.require_e5 and not report["provider_e5"]["provider_evidence_complete"]:
        return 4
    if args.require_superiority and not report["external_superiority"]["superiority_proven"]:
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
