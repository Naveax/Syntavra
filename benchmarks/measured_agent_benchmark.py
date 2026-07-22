from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from syntavra_runtime.product_surface import MeasuredBenchmarkGate, ReceiptValidator
from syntavra_runtime.release_identity import CHANNEL, VERSION


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Evaluate real, paired coding-agent provider receipts without opening unsupported claims."
    )
    value.add_argument("receipts", type=Path, help="JSON list or {'receipts': [...]} document")
    value.add_argument("--output", type=Path)
    return value


def evaluate(path: Path) -> dict[str, Any]:
    receipts = ReceiptValidator.load(path)
    result = MeasuredBenchmarkGate.evaluate(receipts)
    return {
        "version": VERSION,
        "channel": CHANNEL,
        "protocol": "measured-agent-receipts-v1",
        "source": str(path),
        "receipt_validation": ReceiptValidator.evaluate(receipts),
        "benchmark": result,
        "claim_boundary": {
            "internal_or_synthetic": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "passing_gate": "EXTERNAL_SUPERIORITY_ELIGIBLE_FOR_REVIEW",
            "automatic_public_claim": False,
        },
    }


def main() -> int:
    args = parser().parse_args()
    result = evaluate(args.receipts)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 0 if result["benchmark"]["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
