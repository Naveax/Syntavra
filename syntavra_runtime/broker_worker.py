from __future__ import annotations

import argparse
from pathlib import Path

from .evidence import EvidenceStore
from .process_broker import ProcessBroker
from .state import StateDB


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve(strict=True)
    db = StateDB(root / "broker.sqlite3")
    row = db.job(args.job_id)
    if not row:
        return 2
    project_id = row.get("project_id") or "unknown-project"
    evidence = EvidenceStore(root.parent / "evidence", project_id=project_id)
    broker = ProcessBroker(root, evidence)
    cancel = root / "jobs" / args.job_id / "cancel"
    try:
        result = broker._execute(args.job_id, cancel_file=cancel)
        return int(result.exit_code or 0)
    except Exception as exc:
        db.update_job(args.job_id, state="FAILED", error=f"worker failure: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
