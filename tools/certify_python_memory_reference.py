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
DYNAMIC_KEYS = {"created_at", "updated_at"}
SESSION_TABLES = ["checkpoints", "events", "sessions", "summaries"]
SESSION_INDEXES = ["idx_summary_session_view"]
INTELLIGENCE_TABLES = ["observations"]
INTELLIGENCE_INDEXES = ["observations_kind_idx"]


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
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
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
    return {
        "exit": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "value": value,
    }


def _ok(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 0 or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit 0, got {result}")
    value = result.get("value")
    if not isinstance(value, dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
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


def _strip_dynamic(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_dynamic(child) for key, child in value.items() if key not in DYNAMIC_KEYS}
    if isinstance(value, list):
        return [_strip_dynamic(item) for item in value]
    return value


def _sqlite_schema(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(f"expected SQLite file does not exist: {path}")
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
        event_hash = str(row.pop("event_hash", ""))
        row["event_hash_shape"] = len(event_hash) == 64 and all(ch in "0123456789abcdef" for ch in event_hash)
        rows.append(row)
    return rows


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _create_external_extractor(root: Path) -> str:
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


def _scores_descending(rows: list[dict[str, Any]], *, key: str) -> bool:
    values = [float(row[key]) for row in rows]
    return values == sorted(values, reverse=True)


def _roi_order(rows: list[dict[str, Any]]) -> bool:
    actual = [(float(row["roi"]), str(row["observation_id"])) for row in rows]
    expected = sorted(actual, key=lambda item: (-item[0], item[1]))
    return actual == expected


def _session_contract(repo: Path, project: Path, state: Path) -> dict[str, Any]:
    def call(*args: str) -> dict[str, Any]:
        return _ok(" ".join(args), _run(repo, project, state, list(args)))

    opened = call(
        "run",
        "memory-open",
        "--session-id",
        "session-a",
        "--metadata",
        '{"task":"python memory reference","owner":"syntavra"}',
    )
    if opened.get("restored") is not False or opened.get("state") != "ACTIVE":
        raise AssertionError(f"new session open drift: {opened}")
    reopened = call("run", "memory-open", "--session-id", "session-a", "--metadata", '{"ignored":true}')
    if reopened.get("restored") is not True or reopened.get("metadata") != {"owner": "syntavra", "task": "python memory reference"}:
        raise AssertionError(f"session restore/open idempotency drift: {reopened}")

    empty_verify = call("run", "memory-verify", "session-a")
    if empty_verify != {
        "ok": True,
        "session_id": "session-a",
        "events": 0,
        "last_hash": "0" * 64,
        "failures": [],
    }:
        raise AssertionError(f"existing empty-session verification drift: {empty_verify}")
    empty_retrieve = call("run", "memory-retrieve", "session-a", "anything")
    if empty_retrieve.get("results") != [] or empty_retrieve.get("exact_recovery") is not True:
        raise AssertionError(f"empty session retrieval drift: {empty_retrieve}")

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
    for sequence, event in enumerate(events, 1):
        if event.get("sequence") != sequence or event.get("session_id") != "session-a":
            raise AssertionError(f"session event ordering drift: {events}")
        previous = "0" * 64 if sequence == 1 else str(events[sequence - 2]["event_hash"])
        if event.get("previous_hash") != previous:
            raise AssertionError(f"session event hash-chain linkage drift: {event}")

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
    if compact.get("exact_history_preserved") is not True or compact.get("events") != 3:
        raise AssertionError(f"memory compaction drift: {compact}")
    summaries = compact.get("summaries")
    if not isinstance(summaries, list) or [row.get("view") for row in summaries] != ["decision", "test", "repository"]:
        raise AssertionError(f"memory summary view ordering drift: {compact}")
    repository_summary = next(row for row in summaries if row.get("view") == "repository")
    if "agent/reference" not in str(repository_summary.get("summary")) or "memory" not in str(repository_summary.get("summary")):
        raise AssertionError(f"repository intelligence summary drift: {repository_summary}")

    retrieve = call("run", "memory-retrieve", "session-a", "cache verify", "--limit", "8")
    results = retrieve.get("results")
    if not isinstance(results, list) or not results or retrieve.get("exact_recovery") is not True:
        raise AssertionError(f"session retrieval contract drift: {retrieve}")
    if not _scores_descending(results, key="score"):
        raise AssertionError(f"session retrieval ranking drift: {results}")

    checkpoint = call("run", "memory-checkpoint", "session-a", "--label", "stable")
    checkpoint_again = call("run", "memory-checkpoint", "session-a", "--label", "stable")
    if checkpoint.get("checkpoint_id") != checkpoint_again.get("checkpoint_id") or checkpoint.get("sequence") != 3:
        raise AssertionError(f"checkpoint determinism drift: {checkpoint} / {checkpoint_again}")

    post_checkpoint = call(
        "run",
        "memory-append",
        "session-a",
        "change",
        '{"change":"after checkpoint","file":"src/example.py"}',
    )
    restore = call("run", "memory-restore", str(checkpoint["checkpoint_id"]))
    if restore.get("exact_recovery") is not True or len(restore.get("events") or []) != 3:
        raise AssertionError(f"checkpoint exact recovery drift: {restore}")
    verified_after = call("run", "memory-verify", "session-a")
    if verified_after.get("ok") is not True or verified_after.get("events") != 4:
        raise AssertionError("memory-restore unexpectedly mutated the live event chain")

    fork = call("run", "memory-fork", "session-a", "--label", "child")
    child = fork.get("child")
    if not isinstance(child, dict) or child.get("parents") != ["session-a"] or child.get("restored") is not False:
        raise AssertionError(f"fork parent contract drift: {fork}")
    child_id = str(child.get("session_id") or "")
    child_event = call(
        "run",
        "memory-append",
        child_id,
        "task",
        '{"task":"child branch work","goal":"verify fork"}',
    )
    child_verify = call("run", "memory-verify", child_id)
    if child_event.get("sequence") != 1 or child_verify.get("ok") is not True:
        raise AssertionError(f"forked session chain drift: {child_event} / {child_verify}")

    session_b = call("run", "memory-open", "--session-id", "session-b", "--metadata", '{"task":"secondary"}')
    call("run", "memory-append", "session-b", "decision", '{"decision":"merge later"}')
    merge = call("run", "memory-merge", "session-a", "session-b", "--label", "combined")
    merged = merge.get("merged")
    if not isinstance(merged, dict) or merged.get("parents") != ["session-a", "session-b"]:
        raise AssertionError(f"merge parent contract drift: {merge}")
    merged_id = str(merged.get("session_id") or "")
    merged_verify = call("run", "memory-verify", merged_id)
    if merged_verify.get("ok") is not True or merged_verify.get("events") != 1:
        raise AssertionError(f"merged session chain drift: {merged_verify}")

    missing_cases = {
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

    tamper_open = call("run", "memory-open", "--session-id", "tamper-session")
    del tamper_open
    call("run", "memory-append", "tamper-session", "decision", '{"decision":"original"}')
    call("run", "memory-append", "tamper-session", "test", '{"test":"second"}')
    session_db = state / "unified" / "session-memory.sqlite3"
    with sqlite3.connect(session_db) as db:
        db.execute(
            "UPDATE events SET payload_json=? WHERE session_id=? AND sequence=1",
            ('{"decision":"tampered"}', "tamper-session"),
        )
    tampered_verify = call("run", "memory-verify", "tamper-session")
    if tampered_verify.get("ok") is not False or tampered_verify.get("failures") != ["hash:1"]:
        raise AssertionError(f"tampered memory chain was not detected exactly: {tampered_verify}")

    schema = _sqlite_schema(session_db)
    if schema["tables"] != SESSION_TABLES or schema["indexes"] != SESSION_INDEXES:
        raise AssertionError(f"session-memory SQLite schema drift: {schema}")
    if schema["row_counts"].get("sessions", 0) < 5 or schema["row_counts"].get("events", 0) < 9:
        raise AssertionError(f"session-memory durable state unexpectedly sparse: {schema}")

    generated_ids = {child_id, merged_id}

    def normalize_generated(value: Any) -> Any:
        result = _strip_dynamic(value)
        if isinstance(result, dict):
            result = dict(result)
            for key in ("session_id",):
                if str(result.get(key) or "") in generated_ids:
                    result[key] = "<generated-session>"
            for key in ("event_hash", "last_hash"):
                if key in result and any(generated in json.dumps(value, ensure_ascii=False) for generated in generated_ids):
                    result[key] = "<generated-session-hash>"
            for child_key in ("child", "merged"):
                child_value = result.get(child_key)
                if isinstance(child_value, dict) and str(child_value.get("session_id") or "") in generated_ids:
                    child_value = dict(child_value)
                    child_value["session_id"] = "<generated-session>"
                    result[child_key] = child_value
        return result

    return {
        "opened": _strip_dynamic(opened),
        "reopened": _strip_dynamic(reopened),
        "empty_verify": empty_verify,
        "empty_retrieve": _strip_dynamic(empty_retrieve),
        "events": [_strip_dynamic(row) for row in events],
        "verified_before": verified_before,
        "compact": _strip_dynamic(compact),
        "retrieve": _strip_dynamic(retrieve),
        "checkpoint": _strip_dynamic(checkpoint),
        "checkpoint_idempotent": checkpoint.get("checkpoint_id") == checkpoint_again.get("checkpoint_id"),
        "post_checkpoint": _strip_dynamic(post_checkpoint),
        "restore": _strip_dynamic(restore),
        "verified_after": verified_after,
        "fork": normalize_generated(fork),
        "child_event": normalize_generated(child_event),
        "child_verify": normalize_generated(child_verify),
        "session_b": _strip_dynamic(session_b),
        "merge": normalize_generated(merge),
        "merged_verify": normalize_generated(merged_verify),
        "missing_and_malformed": missing_cases,
        "tampered_verify": tampered_verify,
        "sqlite": schema,
    }


def _memory_intelligence_contract(repo: Path, project: Path, state: Path, root: Path) -> dict[str, Any]:
    def call(*args: str, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
        return _ok(" ".join(args), _run(repo, project, state, list(args), extra_env=extra_env))

    empty_status = call("run", "memory-intelligence-status")
    if empty_status != {"stats": {"observations": 0, "valid": 0, "missing_embeddings": 0}, "ranked": []}:
        raise AssertionError(f"memory intelligence empty status drift: {empty_status}")
    empty_search = call("run", "memory-search", "nothing", "--limit", "10")
    if empty_search != {"results": []}:
        raise AssertionError(f"empty memory search drift: {empty_search}")
    empty_export_path = state / "empty-memory-export.jsonl"
    empty_export = call("run", "memory-export", str(empty_export_path))
    if empty_export.get("observations") != 0 or empty_export.get("sha256") != hashlib.sha256(b"").hexdigest():
        raise AssertionError(f"empty memory export drift: {empty_export}")
    if empty_export_path.read_bytes() != b"":
        raise AssertionError("empty memory export is no longer a zero-byte JSONL file")

    duplicate_first = call(
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
    duplicate_second = call(
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
    if duplicate_first.get("observation_id") != duplicate_second.get("observation_id"):
        raise AssertionError("memory duplicate upsert no longer preserves observation identity")
    if duplicate_second.get("importance") != 0.8 or duplicate_second.get("confidence") != 0.9:
        raise AssertionError(f"memory duplicate max-strength upsert drift: {duplicate_second}")

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
    observations = heuristic.get("observations")
    if not isinstance(observations, list) or [row.get("kind") for row in observations] != ["decision", "failure", "constraint", "preference"]:
        raise AssertionError(f"heuristic extraction ordering drift: {heuristic}")

    critical = call(
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
    extractor_command = _create_external_extractor(root)
    external = call(
        "run",
        "memory-extract",
        "external extractor transcript",
        extra_env={"SYNTAVRA_MEMORY_EXTRACTOR_COMMAND_JSON": extractor_command},
    )
    external_rows = external.get("observations")
    if not isinstance(external_rows, list) or len(external_rows) != 1 or external_rows[0].get("text") != "external extractor observation":
        raise AssertionError(f"external extractor contract drift: {external}")

    unicode_search = call("run", "memory-search", "STRASSE cache", "--limit", "10")
    search_rows = unicode_search.get("results")
    if not isinstance(search_rows, list) or not search_rows:
        raise AssertionError(f"Unicode memory search returned no results: {unicode_search}")
    if not _scores_descending(search_rows, key="score"):
        raise AssertionError(f"memory search score ordering drift: {search_rows}")
    if search_rows[0].get("observation", {}).get("observation_id") != unicode_add.get("observation_id"):
        raise AssertionError(f"Unicode casefold ranking drift: {unicode_search}")

    intelligence_db = state / "memory-intelligence.sqlite3"
    with sqlite3.connect(intelligence_db) as db:
        db.execute(
            "UPDATE observations SET embedding_json=NULL WHERE observation_id=?",
            (duplicate_first["observation_id"],),
        )
    backfill = call("run", "memory-backfill", "--limit", "1000")
    if backfill != {"embedded": 1, "remaining": 0}:
        raise AssertionError(f"memory embedding backfill drift: {backfill}")

    status = call("run", "memory-intelligence-status")
    stats = status.get("stats")
    ranked = status.get("ranked")
    if stats != {"observations": 8, "valid": 8, "missing_embeddings": 0}:
        raise AssertionError(f"memory intelligence durable counts drift: {status}")
    if not isinstance(ranked, list) or len(ranked) != 8 or not _roi_order(ranked):
        raise AssertionError(f"memory ROI ranking drift: {status}")
    if ranked[0].get("observation_id") != critical.get("observation_id"):
        raise AssertionError(f"critical observation no longer leads ROI ranking: {ranked}")

    notifications = _notifications(state)
    if len(notifications) != 1:
        raise AssertionError(f"critical-memory notification side-effect drift: {notifications}")
    notification = notifications[0]
    if notification != {
        "channel": "memory",
        "severity": "critical",
        "title": "Critical security",
        "body": "critical memory reference observation",
        "event_hash_shape": True,
    }:
        raise AssertionError(f"critical-memory notification schema drift: {notification}")

    export_path = state / "memory-export.jsonl"
    exported = call("run", "memory-export", str(export_path))
    rows = _jsonl(export_path)
    file_sha = hashlib.sha256(export_path.read_bytes()).hexdigest()
    if exported.get("sha256") != file_sha or exported.get("observations") != 8 or len(rows) != 8:
        raise AssertionError(f"memory export integrity drift: {exported} rows={len(rows)} sha={file_sha}")
    if not _roi_order(rows):
        raise AssertionError("memory export no longer preserves canonical ROI ranking")
    if [row["observation_id"] for row in rows] != [row["observation_id"] for row in ranked]:
        raise AssertionError("memory export ordering differs from status ranked output")

    negative = {
        "empty_text": _public_error(
            "empty memory text",
            _run(repo, project, state, ["run", "memory-add", ""]),
        ),
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
    call_corrupt = lambda *parts: _ok(
        " ".join(parts),
        _run(repo, project, corrupt_state, list(parts)),
    )
    corrupt_added = call_corrupt("run", "memory-add", "corrupt metadata fixture")
    corrupt_db = corrupt_state / "memory-intelligence.sqlite3"
    with sqlite3.connect(corrupt_db) as db:
        db.execute(
            "UPDATE observations SET metadata_json=? WHERE observation_id=?",
            ("{not-json", corrupt_added["observation_id"]),
        )
    malformed_state = _public_error(
        "malformed memory-intelligence durable metadata",
        _run(repo, project, corrupt_state, ["run", "memory-intelligence-status"]),
    )
    if "JSONDecodeError" not in str(malformed_state.get("detail")):
        raise AssertionError(f"malformed durable memory reason drift: {malformed_state}")

    schema = _sqlite_schema(intelligence_db)
    if schema["tables"] != INTELLIGENCE_TABLES or schema["indexes"] != INTELLIGENCE_INDEXES:
        raise AssertionError(f"memory-intelligence SQLite schema drift: {schema}")
    if schema["row_counts"].get("observations") != 8:
        raise AssertionError(f"memory-intelligence SQLite count drift: {schema}")

    return {
        "empty_status": empty_status,
        "empty_search": empty_search,
        "empty_export": {"observations": 0, "sha256": empty_export["sha256"], "zero_bytes": True},
        "duplicate_upsert": {
            "same_observation_id": True,
            "importance": duplicate_second["importance"],
            "confidence": duplicate_second["confidence"],
        },
        "unicode_add": _strip_dynamic(unicode_add),
        "heuristic_extract": _strip_dynamic(heuristic),
        "critical_add": _strip_dynamic(critical),
        "external_extract": _strip_dynamic(external),
        "unicode_search": _strip_dynamic(unicode_search),
        "backfill": backfill,
        "status": _strip_dynamic(status),
        "notifications": notifications,
        "export": {
            "observations": exported["observations"],
            "sha256_matches_file": exported["sha256"] == file_sha,
            "rank_order_matches_status": True,
        },
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
        session = _session_contract(repo, project, state)
        intelligence = _memory_intelligence_contract(repo, project, state, root)

    return {
        "ok": True,
        "schema_version": 1,
        "family": "memory-intelligence",
        "engine": "python",
        "exact_head": _head(repo),
        "routes": ROUTES,
        "exit_policy": {
            "success": 0,
            "application_error": 4,
            "argument_parser_error": 2,
            "integrity_report_failure": "exit 0 with ok=false for memory-verify",
        },
        "nondeterministic_fields": [
            "created_at",
            "updated_at",
            "generated fork/merge session IDs",
            "hashes derived from generated session IDs",
            "temporary state/export paths",
            "notification event_hash because it includes created_at",
        ],
        "session_memory": session,
        "memory_intelligence": intelligence,
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
            "schema_version": 1,
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
