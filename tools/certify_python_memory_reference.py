#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any


ROUTES = [
    "run memory-open",
    "run memory-append",
    "run memory-compact",
    "run memory-retrieve",
    "run memory-checkpoint",
    "run memory-fork",
    "run memory-merge",
    "run memory-restore",
    "run memory-verify",
    "run memory-add",
    "run memory-extract",
    "run memory-search",
    "run memory-export",
    "run memory-backfill",
    "run memory-intelligence-status",
]
DYNAMIC_OBSERVATION_KEYS = {"created_at", "updated_at"}


def _head(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _run(
    repo: Path,
    project: Path,
    state: Path,
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    env.pop("SYNTAVRA_MEMORY_EXTRACTOR_COMMAND_JSON", None)
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            *args,
        ],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    try:
        value = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr, "value": value}


def _success(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 0 or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit 0, got {result}")
    if not isinstance(result.get("value"), dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return result["value"]


def _integrity_failure(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 3 or result["stderr"]:
        raise AssertionError(f"{label}: expected integrity exit 3 with empty stderr, got {result}")
    value = result.get("value")
    if not isinstance(value, dict) or value.get("ok") is not False:
        raise AssertionError(f"{label}: expected integrity JSON report with ok=false, got {result}")
    return value


def _public_error(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 4 or result["stderr"]:
        raise AssertionError(f"{label}: expected public exit 4 with empty stderr, got {result}")
    value = result.get("value")
    if not isinstance(value, dict) or value.get("ok") is not False:
        raise AssertionError(f"{label}: expected public JSON failure envelope, got {result}")
    error = value.get("error")
    if not isinstance(error, dict) or error.get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: public error code drift: {result}")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    return {
        "exit": 4,
        "stdout_format": "json-object",
        "stderr_empty": True,
        "error_code": error["code"],
        "detail": details.get("error"),
    }


def _argparse_error(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 2 or result["stdout"] or "usage:" not in result["stderr"].casefold():
        raise AssertionError(f"{label}: expected argparse usage error, got {result}")
    return {"exit": 2, "stdout_format": "empty", "stderr_format": "argparse-usage-error"}


def _schema(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(f"SQLite file missing: {path}")
    db = sqlite3.connect(path)
    try:
        tables = sorted(
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
        indexes = sorted(
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        )
        counts = {
            table: int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
            if table.replace("_", "").isalnum()
        }
    finally:
        db.close()
    return {"tables": tables, "indexes": indexes, "row_counts": counts}


def _clean_observation(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean_observation(child)
            for key, child in value.items()
            if key not in DYNAMIC_OBSERVATION_KEYS
        }
    if isinstance(value, list):
        return [_clean_observation(item) for item in value]
    return value


def _notifications(state: Path) -> list[dict[str, Any]]:
    path = state / "notifications" / "events.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row.pop("created_at", None)
        digest = str(row.pop("event_hash", ""))
        row["event_hash_shape"] = len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)
        rows.append(row)
    return rows


def _extractor_command(root: Path) -> str:
    helper = root / "memory_extractor_fixture.py"
    helper.write_text(
        "import json, sys\n"
        "request = json.load(open(sys.argv[1], encoding='utf-8'))\n"
        "assert request['transcript'] == 'external extractor transcript'\n"
        "payload = {'observations': [{'text': 'external extractor observation', 'kind': 'decision', 'importance': 0.61, 'confidence': 0.73, 'validity': 1.0, 'metadata': {'source': 'fixture'}}]}\n"
        "json.dump(payload, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False, sort_keys=True)\n",
        encoding="utf-8",
    )
    return json.dumps([sys.executable, str(helper), "{request}", "{output}"])


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _session_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    def call(*parts: str) -> dict[str, Any]:
        return _success(" ".join(parts), _run(repo, project, state, list(parts)))

    opened = call(
        "run",
        "memory-open",
        "--session-id",
        "session-a",
        "--metadata",
        '{"task":"python memory reference","owner":"syntavra"}',
    )
    reopened = call("run", "memory-open", "--session-id", "session-a", "--metadata", '{"ignored":true}')
    if opened.get("restored") is not False or reopened.get("restored") is not True:
        raise AssertionError(f"session open/idempotency drift: {opened} / {reopened}")
    if reopened.get("metadata") != {"owner": "syntavra", "task": "python memory reference"}:
        raise AssertionError(f"reopened session metadata drift: {reopened}")

    empty_verify = call("run", "memory-verify", "session-a")
    empty_retrieve = call("run", "memory-retrieve", "session-a", "anything")
    if empty_verify.get("ok") is not True or empty_verify.get("events") != 0:
        raise AssertionError(f"existing empty session verification drift: {empty_verify}")
    if empty_retrieve.get("results") != [] or empty_retrieve.get("exact_recovery") is not True:
        raise AssertionError(f"existing empty session retrieval drift: {empty_retrieve}")

    events = [
        call(
            "run",
            "memory-append",
            "session-a",
            "decision",
            '{"decision":"keep deterministic cache","importance":0.8,"pinned":true}',
        ),
        call(
            "run",
            "memory-append",
            "session-a",
            "test",
            '{"test":"cache verify","result":"passed","coverage":91}',
        ),
        call(
            "run",
            "memory-append",
            "session-a",
            "repository",
            '{"repository":"Syntavra","branch":"agent/reference","module":"memory","commit":"fixture"}',
        ),
    ]
    for index, event in enumerate(events, 1):
        if event.get("sequence") != index:
            raise AssertionError(f"event sequence drift: {events}")
        expected_previous = "0" * 64 if index == 1 else events[index - 2]["event_hash"]
        if event.get("previous_hash") != expected_previous:
            raise AssertionError(f"hash-chain linkage drift: {event}")

    verified_before = call("run", "memory-verify", "session-a")
    if verified_before.get("ok") is not True or verified_before.get("events") != 3:
        raise AssertionError(f"session verification drift: {verified_before}")

    compact = call(
        "run",
        "memory-compact",
        "session-a",
        "--view",
        "decision",
        "--view",
        "test",
        "--view",
        "repository",
    )
    summaries = compact.get("summaries")
    if not isinstance(summaries, list) or [row.get("view") for row in summaries] != ["decision", "test", "repository"]:
        raise AssertionError(f"summary ordering drift: {compact}")
    repository_summary = next(row for row in summaries if row.get("view") == "repository")
    if "agent/reference" not in str(repository_summary.get("summary")) or "memory" not in str(repository_summary.get("summary")):
        raise AssertionError(f"repository intelligence summary drift: {repository_summary}")
    if compact.get("exact_history_preserved") is not True:
        raise AssertionError(f"compaction stopped preserving exact history: {compact}")

    retrieve = call("run", "memory-retrieve", "session-a", "cache verify", "--limit", "8")
    results = retrieve.get("results")
    if not isinstance(results, list) or not results:
        raise AssertionError(f"retrieval returned no results: {retrieve}")
    scores = [float(row["score"]) for row in results]
    if scores != sorted(scores, reverse=True):
        raise AssertionError(f"retrieval score ordering drift: {scores}")

    checkpoint = call("run", "memory-checkpoint", "session-a", "--label", "stable")
    checkpoint_again = call("run", "memory-checkpoint", "session-a", "--label", "stable")
    checkpoint_idempotent = checkpoint.get("checkpoint_id") == checkpoint_again.get("checkpoint_id")
    if not checkpoint_idempotent or checkpoint.get("sequence") != 3:
        raise AssertionError(f"checkpoint determinism drift: {checkpoint} / {checkpoint_again}")

    call(
        "run",
        "memory-append",
        "session-a",
        "change",
        '{"change":"after checkpoint","file":"src/example.py"}',
    )
    restore = call("run", "memory-restore", str(checkpoint["checkpoint_id"]))
    verified_after = call("run", "memory-verify", "session-a")
    if restore.get("exact_recovery") is not True or len(restore.get("events") or []) != 3:
        raise AssertionError(f"checkpoint recovery drift: {restore}")
    if verified_after.get("events") != 4:
        raise AssertionError("memory-restore unexpectedly mutated the live event chain")

    fork = call("run", "memory-fork", "session-a", "--label", "child")
    child = fork.get("child")
    if not isinstance(child, dict) or child.get("parents") != ["session-a"]:
        raise AssertionError(f"fork parent contract drift: {fork}")
    child_id = str(child["session_id"])
    call("run", "memory-append", child_id, "task", '{"task":"child branch work"}')
    child_verify = call("run", "memory-verify", child_id)

    call("run", "memory-open", "--session-id", "session-b", "--metadata", '{"task":"secondary"}')
    call("run", "memory-append", "session-b", "decision", '{"decision":"merge later"}')
    merge = call("run", "memory-merge", "session-a", "session-b", "--label", "combined")
    merged = merge.get("merged")
    if not isinstance(merged, dict) or merged.get("parents") != ["session-a", "session-b"]:
        raise AssertionError(f"merge parent contract drift: {merge}")
    merged_verify = call("run", "memory-verify", str(merged["session_id"]))

    missing_and_malformed = {
        "missing_verify": _public_error(
            "missing verify",
            _run(repo, project, state, ["run", "memory-verify", "missing-session"]),
        ),
        "missing_retrieve": _public_error(
            "missing retrieve",
            _run(repo, project, state, ["run", "memory-retrieve", "missing-session", "query"]),
        ),
        "missing_checkpoint": _public_error(
            "missing checkpoint",
            _run(repo, project, state, ["run", "memory-checkpoint", "missing-session"]),
        ),
        "unsupported_view": _public_error(
            "unsupported view",
            _run(repo, project, state, ["run", "memory-compact", "session-a", "--view", "definitely-unknown"]),
        ),
        "duplicate_merge_parent": _public_error(
            "duplicate merge parent",
            _run(repo, project, state, ["run", "memory-merge", "session-a", "session-a"]),
        ),
        "malformed_payload": _public_error(
            "malformed payload",
            _run(repo, project, state, ["run", "memory-append", "session-a", "decision", "{"]),
        ),
        "missing_append_argument": _argparse_error(
            "missing append argument",
            _run(repo, project, state, ["run", "memory-append", "session-a", "decision"]),
        ),
    }

    call("run", "memory-open", "--session-id", "tamper-session")
    call("run", "memory-append", "tamper-session", "decision", '{"decision":"original"}')
    call("run", "memory-append", "tamper-session", "test", '{"test":"second"}')
    session_db = state / "unified" / "session-memory.sqlite3"
    with sqlite3.connect(session_db) as db:
        db.execute(
            "UPDATE events SET payload_json=? WHERE session_id=? AND sequence=1",
            ('{"decision":"tampered"}', "tamper-session"),
        )
    tampered_verify = _integrity_failure(
        "tampered verify",
        _run(repo, project, state, ["run", "memory-verify", "tamper-session"]),
    )
    if tampered_verify.get("failures") != ["hash:1"]:
        raise AssertionError(f"tampered chain failure classification drift: {tampered_verify}")

    schema = _schema(session_db)
    if schema["tables"] != ["checkpoints", "events", "sessions", "summaries"]:
        raise AssertionError(f"session-memory table drift: {schema}")
    if schema["indexes"] != ["idx_summary_session_view"]:
        raise AssertionError(f"session-memory index drift: {schema}")
    if schema["row_counts"].get("sessions", 0) < 5 or schema["row_counts"].get("events", 0) < 9:
        raise AssertionError(f"session-memory durable counts drift: {schema}")

    return {
        "empty_verify": empty_verify,
        "empty_retrieve": empty_retrieve,
        "verified_before": verified_before,
        "compact": compact,
        "retrieve": retrieve,
        "checkpoint_idempotent": checkpoint_idempotent,
        "restore": restore,
        "verified_after": verified_after,
        "child_verify": child_verify,
        "merged_verify": merged_verify,
        "missing_and_malformed": missing_and_malformed,
        "tampered_verify": tampered_verify,
        "sqlite": schema,
    }


def _intelligence_contract(repo: Path, project: Path, state: Path, root: Path) -> dict[str, Any]:
    def call(*parts: str, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
        return _success(" ".join(parts), _run(repo, project, state, list(parts), extra_env=extra_env))

    empty_status = call("run", "memory-intelligence-status")
    empty_search = call("run", "memory-search", "nothing", "--limit", "10")
    empty_export_path = state / "empty-memory-export.jsonl"
    empty_export = call("run", "memory-export", str(empty_export_path))
    if empty_status != {"stats": {"observations": 0, "valid": 0, "missing_embeddings": 0}, "ranked": []}:
        raise AssertionError(f"empty intelligence status drift: {empty_status}")
    if empty_search != {"results": []}:
        raise AssertionError(f"empty intelligence search drift: {empty_search}")
    if empty_export.get("sha256") != hashlib.sha256(b"").hexdigest() or empty_export_path.read_bytes() != b"":
        raise AssertionError(f"empty intelligence export drift: {empty_export}")

    first = call(
        "run",
        "memory-add",
        "duplicate memory observation",
        "--kind",
        "decision",
        "--importance",
        "0.4",
        "--confidence",
        "0.5",
    )
    second = call(
        "run",
        "memory-add",
        "duplicate memory observation",
        "--kind",
        "decision",
        "--importance",
        "0.8",
        "--confidence",
        "0.9",
    )
    duplicate_upsert = {
        "same_observation_id": first.get("observation_id") == second.get("observation_id"),
        "importance": second.get("importance"),
        "confidence": second.get("confidence"),
    }
    if duplicate_upsert != {"same_observation_id": True, "importance": 0.8, "confidence": 0.9}:
        raise AssertionError(f"duplicate observation upsert drift: {duplicate_upsert}")

    unicode_add = call(
        "run",
        "memory-add",
        "Straße cache decision",
        "--kind",
        "decision",
        "--importance",
        "0.70",
        "--confidence",
        "0.80",
    )
    heuristic = call(
        "run",
        "memory-extract",
        "Decision: Keep deterministic cache\nFailure: Timeout in worker\nConstraint: Never expose secret\nPreference: Prefer local index",
    )
    kinds = [row.get("kind") for row in heuristic.get("observations") or []]
    if kinds != ["decision", "failure", "constraint", "preference"]:
        raise AssertionError(f"heuristic extraction ordering drift: {heuristic}")

    critical_add = call(
        "run",
        "memory-add",
        "critical memory reference observation",
        "--kind",
        "security",
        "--importance",
        "0.95",
        "--confidence",
        "0.90",
    )
    external = call(
        "run",
        "memory-extract",
        "external extractor transcript",
        extra_env={"SYNTAVRA_MEMORY_EXTRACTOR_COMMAND_JSON": _extractor_command(root)},
    )
    external_rows = external.get("observations")
    if not isinstance(external_rows, list) or len(external_rows) != 1 or external_rows[0].get("text") != "external extractor observation":
        raise AssertionError(f"external extractor drift: {external}")

    unicode_search = call("run", "memory-search", "STRASSE cache", "--limit", "10")
    search_rows = unicode_search.get("results")
    if not isinstance(search_rows, list) or not search_rows:
        raise AssertionError(f"Unicode search returned no results: {unicode_search}")
    scores = [float(row["score"]) for row in search_rows]
    if scores != sorted(scores, reverse=True):
        raise AssertionError(f"search score ordering drift: {scores}")
    if search_rows[0].get("observation", {}).get("observation_id") != unicode_add.get("observation_id"):
        raise AssertionError(f"Unicode casefold ranking drift: {unicode_search}")

    intelligence_db = state / "memory-intelligence.sqlite3"
    with sqlite3.connect(intelligence_db) as db:
        db.execute("UPDATE observations SET embedding_json=NULL WHERE observation_id=?", (first["observation_id"],))
    backfill = call("run", "memory-backfill", "--limit", "1000")
    if backfill != {"embedded": 1, "remaining": 0}:
        raise AssertionError(f"embedding backfill drift: {backfill}")

    status = call("run", "memory-intelligence-status")
    ranked = status.get("ranked")
    if status.get("stats") != {"observations": 8, "valid": 8, "missing_embeddings": 0}:
        raise AssertionError(f"memory intelligence count drift: {status}")
    if not isinstance(ranked, list) or len(ranked) != 8:
        raise AssertionError(f"memory ranking cardinality drift: {status}")
    ordering = [(float(row["roi"]), str(row["observation_id"])) for row in ranked]
    if ordering != sorted(ordering, key=lambda item: (-item[0], item[1])):
        raise AssertionError(f"ROI ordering drift: {ordering}")
    if ranked[0].get("observation_id") != critical_add.get("observation_id"):
        raise AssertionError(f"critical observation no longer leads ROI ranking: {ranked}")

    notifications = _notifications(state)
    expected_notification = {
        "channel": "memory",
        "severity": "critical",
        "title": "Critical security",
        "body": "critical memory reference observation",
        "event_hash_shape": True,
    }
    if notifications != [expected_notification]:
        raise AssertionError(f"critical notification drift: {notifications}")

    export_path = state / "memory-export.jsonl"
    exported = call("run", "memory-export", str(export_path))
    exported_rows = _jsonl(export_path)
    exported_ids = [row["observation_id"] for row in exported_rows]
    ranked_ids = [row["observation_id"] for row in ranked]
    export_summary = {
        "observations": exported.get("observations"),
        "sha256_matches_file": exported.get("sha256") == hashlib.sha256(export_path.read_bytes()).hexdigest(),
        "rank_order_matches_status": exported_ids == ranked_ids,
    }
    if export_summary != {"observations": 8, "sha256_matches_file": True, "rank_order_matches_status": True}:
        raise AssertionError(f"memory export contract drift: {export_summary}")

    negative = {
        "empty_text": _public_error("empty text", _run(repo, project, state, ["run", "memory-add", ""])),
        "invalid_extractor_config": _public_error(
            "invalid extractor config",
            _run(
                repo,
                project,
                state,
                ["run", "memory-extract", "external extractor transcript"],
                extra_env={"SYNTAVRA_MEMORY_EXTRACTOR_COMMAND_JSON": "{}"},
            ),
        ),
        "invalid_search_limit": _argparse_error(
            "invalid search limit",
            _run(repo, project, state, ["run", "memory-search", "cache", "--limit", "not-an-int"]),
        ),
        "missing_search_query": _argparse_error(
            "missing search query",
            _run(repo, project, state, ["run", "memory-search"]),
        ),
    }

    corrupt_state = root / "corrupt-intelligence-state"
    corrupt_added = _success(
        "corrupt state seed",
        _run(repo, project, corrupt_state, ["run", "memory-add", "corrupt metadata fixture"]),
    )
    corrupt_db = corrupt_state / "memory-intelligence.sqlite3"
    with sqlite3.connect(corrupt_db) as db:
        db.execute(
            "UPDATE observations SET metadata_json=? WHERE observation_id=?",
            ("{not-json", corrupt_added["observation_id"]),
        )
    malformed_state = _public_error(
        "malformed durable metadata",
        _run(repo, project, corrupt_state, ["run", "memory-intelligence-status"]),
    )
    if "JSONDecodeError" not in str(malformed_state.get("detail")):
        raise AssertionError(f"malformed durable metadata classification drift: {malformed_state}")

    schema = _schema(intelligence_db)
    if schema["tables"] != ["observations"] or schema["indexes"] != ["observations_kind_idx"]:
        raise AssertionError(f"memory-intelligence schema drift: {schema}")
    if schema["row_counts"].get("observations") != 8:
        raise AssertionError(f"memory-intelligence durable count drift: {schema}")

    return {
        "empty_status": empty_status,
        "empty_search": empty_search,
        "empty_export": {"observations": 0, "sha256": empty_export["sha256"], "zero_bytes": True},
        "duplicate_upsert": duplicate_upsert,
        "unicode_add": _clean_observation(unicode_add),
        "heuristic_extract": _clean_observation(heuristic),
        "critical_add": _clean_observation(critical_add),
        "external_extract": _clean_observation(external),
        "unicode_search": _clean_observation(unicode_search),
        "backfill": backfill,
        "status": _clean_observation(status),
        "notifications": notifications,
        "export": export_summary,
        "negative": negative,
        "malformed_state": malformed_state,
        "sqlite": schema,
    }


def certify(repo: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="syntavra-python-memory-reference-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        (project / ".git").mkdir()
        session_memory = _session_contract(repo, project, state)
        memory_intelligence = _intelligence_contract(repo, project, state, root)

    return {
        "ok": True,
        "schema_version": 2,
        "family": "memory-intelligence",
        "engine": "python",
        "exact_head": _head(repo),
        "routes": ROUTES,
        "exit_policy": {
            "success": 0,
            "integrity_failure": 3,
            "application_error": 4,
            "argument_parser_error": 2,
        },
        "nondeterministic_fields": [
            "created_at",
            "updated_at",
            "generated fork/merge session IDs",
            "hashes derived from generated session IDs",
            "temporary state/export paths",
            "notification event_hash because it includes created_at",
        ],
        "session_memory": session_memory,
        "memory_intelligence": memory_intelligence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python-only session-memory and memory-intelligence contracts")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 2,
            "family": "memory-intelligence",
            "engine": "python",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
