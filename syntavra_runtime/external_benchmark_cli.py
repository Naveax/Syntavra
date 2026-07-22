from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .external_benchmarks import ExternalBenchmarkGate, ExternalSuiteRegistry, SUITES
from .live_certification import LiveCertificationGate


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _load_receipt_rows(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("receipts", value) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError("receipt file must contain a list or {'receipts': [...]} object")
    return [dict(item) for item in rows if isinstance(item, dict)]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="syntavra prove")
    value.add_argument("--project", default=".")
    value.add_argument("--state-root")
    prove = value.add_subparsers(dest="command", required=True).add_parser("prove")
    actions = prove.add_subparsers(dest="action", required=True)
    actions.add_parser("suites", help="show pinned external benchmark contracts")
    external = actions.add_parser("external-suite", help="validate exact external suite receipts")
    external.add_argument("path", type=Path)
    external.add_argument("--suite", choices=tuple(row.suite_id for row in SUITES))
    integrations = actions.add_parser("integrations", help="validate live integration certification receipts")
    integrations.add_argument("path", type=Path)
    integrations.add_argument("--integration")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.action == "suites":
        _emit(ExternalSuiteRegistry.manifest())
        return 0
    if args.action == "integrations":
        receipts = LiveCertificationGate.load_rows(_load_receipt_rows(args.path))
        result = LiveCertificationGate.evaluate(receipts, integration_id=args.integration)
        _emit(result)
        return 0 if result["ok"] else 4
    receipts = ExternalBenchmarkGate.load(args.path)
    result = ExternalBenchmarkGate.evaluate(receipts, suite_id=args.suite)
    _emit(result)
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
