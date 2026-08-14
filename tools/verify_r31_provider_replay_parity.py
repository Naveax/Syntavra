#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
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

REQUEST = {
    "messages": [
        {"role": "system", "content": "Return one stable answer."},
        {"role": "user", "content": "provider replay parity fixture"},
    ],
    "temperature": 0,
    "request_id": "volatile-request-id",
}
RESPONSE = {
    "id": "provider-response-id",
    "choices": [
        {"message": {"role": "assistant", "content": "stable provider replay response"}}
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
}


def _run(argv: list[str], *, cwd: Path, expected_codes: tuple[int, ...] = (0,)) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode not in expected_codes:
        raise RuntimeError(json.dumps({"argv": argv, "returncode": completed.returncode, "expected_codes": expected_codes, "stdout": completed.stdout, "stderr": completed.stderr}, ensure_ascii=False))
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {value!r}")
    return completed.returncode, value


def _fixture(root: Path) -> dict[str, Any]:
    project = root / "MiXeD-Project"
    state = root / "state"
    project.mkdir(parents=True)
    (project / ".git").mkdir()
    state.mkdir(parents=True)
    evidence = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
    usage = UsageReceiptLedger(state / "usage-receipts.sqlite3")
    gateway = ProviderGateway(state / "provider-gateway.sqlite3", evidence=evidence, usage_ledger=usage)
    plan = gateway.prepare("openai", REQUEST, model="gpt-test", cache_policy="read-write", replay_ttl_seconds=600, prompt_cache_ttl_seconds=300)
    gateway.capture(plan, RESPONSE, preview_bytes=4096, store_replay=True, replay_ttl_seconds=600)
    with closing(sqlite3.connect(state / "provider-gateway.sqlite3")) as database:
        row = database.execute("SELECT response_handle FROM provider_response_cache WHERE cache_key=?", (plan.cache_key,)).fetchone()
    if row is None:
        raise RuntimeError("provider replay cache fixture missing")
    plan_path = root / "plan.json"
    plan_path.write_text(json.dumps(asdict(plan), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"project": project, "state": state, "plan": asdict(plan), "plan_path": plan_path, "cache_key": plan.cache_key, "response_handle": str(row[0])}


def _prefix(kind: str, selector: Path, fixture: dict[str, Any]) -> list[str]:
    common = ["--project", str(fixture["project"]), "--state-root", str(fixture["state"])]
    if kind == "python":
        return [sys.executable, "-m", "syntavra_runtime", *common]
    return [str(selector), "--engine", "rust", *common]


def _cache_state(fixture: dict[str, Any]) -> dict[str, Any]:
    with closing(sqlite3.connect(Path(fixture["state"]) / "provider-gateway.sqlite3")) as database:
        row = database.execute("SELECT hit_count,last_hit_at FROM provider_response_cache WHERE cache_key=?", (fixture["cache_key"],)).fetchone()
        total = database.execute("SELECT COUNT(*) FROM provider_response_cache").fetchone()[0]
    return {"exists": row is not None, "hit_count": int(row[0]) if row else 0, "last_hit_positive": bool(row and float(row[1]) > 0), "rows": int(total)}


def _pair(root: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _fixture(root / f"python-{name}"), _fixture(root / f"rust-{name}")


def verify(selector: Path) -> dict[str, Any]:
    selector = selector.resolve(strict=True)
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        py, rs = _pair(root, "cache-hit")
        py_code, py_value = _run([*_prefix("python", selector, py), "provider", "replay", "--cache-key", py["cache_key"]], cwd=py["project"])
        rs_code, rs_value = _run([*_prefix("rust", selector, rs), "provider", "replay", "--cache-key", rs["cache_key"]], cwd=rs["project"])
        checks["cache_key_hit"] = py_code == rs_code == 0 and py_value == rs_value == RESPONSE
        py_state, rs_state = _cache_state(py), _cache_state(rs)
        checks["cache_key_hit_state"] = py_state == rs_state and py_state["hit_count"] == 1 and py_state["last_hit_positive"]

        py, rs = _pair(root, "plan-hit")
        py_code, py_value = _run([*_prefix("python", selector, py), "provider", "replay", "--plan", str(py["plan_path"])], cwd=py["project"])
        rs_code, rs_value = _run([*_prefix("rust", selector, rs), "provider", "replay", "--plan", str(rs["plan_path"])], cwd=rs["project"])
        checks["plan_cache_lookup"] = py_code == rs_code == 0 and py_value == rs_value == RESPONSE
        checks["plan_cache_lookup_state"] = _cache_state(py) == _cache_state(rs) and _cache_state(py)["hit_count"] == 1

        py, rs = _pair(root, "direct-handle")
        for fixture in (py, rs):
            direct = dict(fixture["plan"])
            direct["replay_response_handle"] = fixture["response_handle"]
            Path(fixture["plan_path"]).write_text(json.dumps(direct, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        py_code, py_value = _run([*_prefix("python", selector, py), "provider", "replay", "--plan", str(py["plan_path"])], cwd=py["project"])
        rs_code, rs_value = _run([*_prefix("rust", selector, rs), "provider", "replay", "--plan", str(rs["plan_path"])], cwd=rs["project"])
        checks["direct_handle"] = py_code == rs_code == 0 and py_value == rs_value == RESPONSE
        checks["direct_handle_no_hit_mutation"] = _cache_state(py) == _cache_state(rs) and _cache_state(py)["hit_count"] == 0

        py, rs = _pair(root, "miss")
        missing_key = "missing-cache-key"
        py_code, py_value = _run([*_prefix("python", selector, py), "provider", "replay", "--cache-key", missing_key], cwd=py["project"], expected_codes=(4,))
        rs_code, rs_value = _run([*_prefix("rust", selector, rs), "provider", "replay", "--cache-key", missing_key], cwd=rs["project"], expected_codes=(4,))
        checks["miss_exit"] = py_code == rs_code == 4 and py_value == rs_value == {"hit": False}

        py, rs = _pair(root, "output-hit")
        py_code, py_wrapper = _run([*_prefix("python", selector, py), "provider", "replay", "--cache-key", py["cache_key"], "--output", "replay.json"], cwd=py["project"])
        py_file = json.loads((Path(py["project"]) / "replay.json").read_text(encoding="utf-8"))
        rs_code, rs_wrapper = _run([*_prefix("rust", selector, rs), "provider", "replay", "--cache-key", rs["cache_key"], "--output", "replay.json"], cwd=rs["project"])
        rs_file = json.loads((Path(rs["project"]) / "replay.json").read_text(encoding="utf-8"))
        checks["output_hit"] = py_code == rs_code == 0 and py_wrapper == rs_wrapper and py_file == rs_file == RESPONSE

        py, rs = _pair(root, "output-miss")
        py_code, py_wrapper = _run([*_prefix("python", selector, py), "provider", "replay", "--cache-key", missing_key, "--output", "replay-miss.json"], cwd=py["project"], expected_codes=(4,))
        py_file = json.loads((Path(py["project"]) / "replay-miss.json").read_text(encoding="utf-8"))
        rs_code, rs_wrapper = _run([*_prefix("rust", selector, rs), "provider", "replay", "--cache-key", missing_key, "--output", "replay-miss.json"], cwd=rs["project"], expected_codes=(4,))
        rs_file = json.loads((Path(rs["project"]) / "replay-miss.json").read_text(encoding="utf-8"))
        checks["output_miss"] = py_code == rs_code == 4 and py_wrapper == rs_wrapper and py_file == rs_file == {"hit": False}

        py, rs = _pair(root, "expired")
        for fixture in (py, rs):
            with closing(sqlite3.connect(Path(fixture["state"]) / "provider-gateway.sqlite3")) as database:
                database.execute("UPDATE provider_response_cache SET expires_at=0 WHERE cache_key=?", (fixture["cache_key"],))
                database.commit()
        py_code, py_value = _run([*_prefix("python", selector, py), "provider", "replay", "--cache-key", py["cache_key"]], cwd=py["project"], expected_codes=(4,))
        rs_code, rs_value = _run([*_prefix("rust", selector, rs), "provider", "replay", "--cache-key", rs["cache_key"]], cwd=rs["project"], expected_codes=(4,))
        checks["expired_miss"] = py_code == rs_code == 4 and py_value == rs_value == {"hit": False}
        checks["expired_eviction"] = _cache_state(py) == _cache_state(rs) and _cache_state(py)["rows"] == 0

    result = {"ok": all(checks.values()), "checks": checks}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify provider replay Python-Rust CLI parity")
    parser.add_argument("--selector", required=True)
    args = parser.parse_args()
    result = verify(Path(args.selector))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
