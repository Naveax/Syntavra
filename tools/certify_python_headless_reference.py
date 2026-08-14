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

from syntavra_runtime.headless_runtime import HeadlessRuntime, JobState


ROUTES = [
    "run headless-submit",
    "run headless-run",
    "run headless-status",
    "run headless-events",
    "run headless-cancel",
    "run headless-resume",
    "run headless-export",
    "run headless-import",
]

EXPECTED_JOB_KEYS = {
    "attempts",
    "claimed_by",
    "command",
    "created_at",
    "job_id",
    "metadata",
    "policy",
    "result",
    "state",
    "updated_at",
    "workspace",
    "workspace_type",
}
EXPECTED_EVENT_KEYS = {"created_at", "event_type", "job_id", "payload", "sequence"}
EXPECTED_EXECUTION_KEYS = {
    "backend",
    "command",
    "cwd",
    "duration_ms",
    "environment_keys",
    "exit_code",
    "output_limit_exceeded",
    "policy",
    "receipt_id",
    "started_at",
    "stderr",
    "stderr_bytes_seen",
    "stderr_sha256",
    "stdout",
    "stdout_bytes_seen",
    "stdout_sha256",
    "timed_out",
}
EXPECTED_BACKEND_KEYS = {
    "available",
    "command_prefix",
    "detail",
    "enforced",
    "name",
    "platform",
    "unsupported",
}
EXPECTED_ALLOWED = {
    "queued": {"claimed", "cancelled"},
    "claimed": {"running", "queued", "cancelled"},
    "running": {"verifying", "completed", "failed", "blocked", "cancelled"},
    "verifying": {"completed", "failed", "blocked", "cancelled"},
    "blocked": {"queued", "cancelled"},
    "completed": set(),
    "failed": {"queued"},
    "cancelled": {"queued"},
}
DYNAMIC_KEYS = {"created_at", "updated_at", "started_at", "duration_ms", "receipt_id"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _exact_head(repo: Path) -> str:
    env_head = os.environ.get("GITHUB_SHA", "").strip()
    if env_head:
        return env_head
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _run_python(repo: Path, project: Path, state: Path, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
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


def _success(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 0 or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit 0, got {result}")
    value = result.get("value")
    if not isinstance(value, dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return value


def _public_failure(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 4 or result["stderr"]:
        raise AssertionError(f"{label}: expected exit 4 with empty stderr, got {result}")
    value = result.get("value")
    if not isinstance(value, dict) or value.get("ok") is not False:
        raise AssertionError(f"{label}: expected public JSON failure envelope, got {result}")
    error = value.get("error")
    if not isinstance(error, dict) or error.get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: wrong public failure code: {result}")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    return {
        "exit": 4,
        "stdout_format": "json-object",
        "stderr_empty": True,
        "error_code": error["code"],
        "detail": details.get("error"),
    }


def _argparse_failure(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 2 or result["stdout"] or "usage:" not in result["stderr"].casefold():
        raise AssertionError(f"{label}: expected argparse usage error, got {result}")
    return {"exit": 2, "stdout_format": "empty", "stderr_format": "argparse-usage-error"}


def _job(value: dict[str, Any], *, wrapped: bool) -> dict[str, Any]:
    job = value.get("job") if wrapped else value
    if not isinstance(job, dict) or set(job) != EXPECTED_JOB_KEYS:
        raise AssertionError(f"headless job schema drift: {job}")
    identifier = str(job.get("job_id") or "")
    if not identifier.startswith("sha256:") or len(identifier) != 71:
        raise AssertionError(f"headless job id is not sha256-shaped: {identifier!r}")
    return job


def _events(value: dict[str, Any]) -> list[dict[str, Any]]:
    rows = value.get("events")
    if not isinstance(rows, list):
        raise AssertionError(f"headless events output drift: {value}")
    for row in rows:
        if not isinstance(row, dict) or set(row) != EXPECTED_EVENT_KEYS:
            raise AssertionError(f"headless event schema drift: {row}")
    sequences = [int(row["sequence"]) for row in rows]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise AssertionError(f"headless event ordering drift: {sequences}")
    return rows


def _validate_execution(job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result")
    if not isinstance(result, dict):
        raise AssertionError(f"job result is not an object: {job}")
    execution = result.get("execution")
    if not isinstance(execution, dict) or set(execution) != EXPECTED_EXECUTION_KEYS:
        raise AssertionError(f"sandbox execution receipt schema drift: {execution}")
    backend = execution.get("backend")
    if not isinstance(backend, dict) or set(backend) != EXPECTED_BACKEND_KEYS:
        raise AssertionError(f"sandbox backend receipt schema drift: {backend}")
    if execution.get("exit_code") != 0 or execution.get("timed_out") is not False:
        raise AssertionError(f"sandbox success semantics drift: {execution}")
    if execution.get("output_limit_exceeded") is not False:
        raise AssertionError(f"sandbox output limit unexpectedly exceeded: {execution}")
    if execution.get("stdout") != "headless-reference":
        raise AssertionError(f"sandbox stdout drift: {execution.get('stdout')!r}")
    if execution.get("stdout_sha256") != hashlib.sha256(b"headless-reference").hexdigest():
        raise AssertionError("sandbox stdout digest drift")
    if execution.get("stderr") != "" or execution.get("stderr_sha256") != hashlib.sha256(b"").hexdigest():
        raise AssertionError("sandbox stderr contract drift")
    return execution


def _verify_bundle(path: Path) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if set(envelope) != {"payload", "sha256"}:
        raise AssertionError(f"headless bundle envelope drift: {envelope.keys()}")
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or set(payload) != {"schema", "job", "events"}:
        raise AssertionError(f"headless bundle payload drift: {payload}")
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    if envelope.get("sha256") != digest:
        raise AssertionError("headless bundle sha256 integrity drift")
    if payload.get("schema") != "syntavra-headless-job":
        raise AssertionError(f"headless bundle schema drift: {payload.get('schema')!r}")
    return envelope


def _sqlite_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(f"headless SQLite database missing: {path}")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        jobs = [dict(row) for row in db.execute("SELECT job_id,state,attempts,claimed_by FROM jobs ORDER BY created_at,job_id")]
        events = [dict(row) for row in db.execute("SELECT sequence,job_id,event_type FROM events ORDER BY sequence")]
        tables = sorted(
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
        indexes = sorted(
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        )
    finally:
        db.close()
    if tables != ["events", "jobs"]:
        raise AssertionError(f"headless SQLite table contract drift: {tables}")
    if indexes != ["idx_events_job", "idx_jobs_state"]:
        raise AssertionError(f"headless SQLite index contract drift: {indexes}")
    sequences = [int(row["sequence"]) for row in events]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise AssertionError(f"SQLite event sequence drift: {sequences}")
    return {"tables": tables, "indexes": indexes, "jobs": jobs, "events": events}


def _normalize(value: Any, *, project: Path, job_ids: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize(child, project=project, job_ids=job_ids)
            for key, child in value.items()
            if key not in DYNAMIC_KEYS
        }
    if isinstance(value, list):
        return [_normalize(child, project=project, job_ids=job_ids) for child in value]
    if isinstance(value, str):
        rendered = value.replace(str(project.resolve(strict=False)), "<project>")
        for job_id in job_ids:
            rendered = rendered.replace(job_id, "<job-id>")
        return rendered
    return value


def _reach_state(runtime: HeadlessRuntime, project: Path, state: JobState, *, case: str) -> str:
    job = runtime.submit(["/bin/true"], workspace=project, job_id=f"contract:{case}")
    path: dict[JobState, list[JobState]] = {
        JobState.QUEUED: [],
        JobState.CLAIMED: [JobState.CLAIMED],
        JobState.RUNNING: [JobState.CLAIMED, JobState.RUNNING],
        JobState.VERIFYING: [JobState.CLAIMED, JobState.RUNNING, JobState.VERIFYING],
        JobState.COMPLETED: [JobState.CLAIMED, JobState.RUNNING, JobState.COMPLETED],
        JobState.FAILED: [JobState.CLAIMED, JobState.RUNNING, JobState.FAILED],
        JobState.CANCELLED: [JobState.CANCELLED],
        JobState.BLOCKED: [JobState.CLAIMED, JobState.RUNNING, JobState.BLOCKED],
    }
    for target in path[state]:
        job = runtime.transition(job.job_id, target)
    if job.state != state:
        raise AssertionError(f"failed to reach state {state.value}: {job}")
    return job.job_id


def _state_machine_contract(root: Path, project: Path) -> dict[str, Any]:
    actual_allowed: dict[str, list[str]] = {}
    allowed_cases = 0
    forbidden_cases = 0
    all_states = list(JobState)

    for source_name, targets in EXPECTED_ALLOWED.items():
        source = JobState(source_name)
        actual_allowed[source_name] = sorted(targets)
        for target_name in sorted(targets):
            target = JobState(target_name)
            case = f"allowed-{source.value}-{target.value}"
            runtime = HeadlessRuntime(root / f"{case}.sqlite3", root / f"{case}-state")
            job_id = _reach_state(runtime, project, source, case=case)
            result = runtime.transition(job_id, target)
            if result.state != target:
                raise AssertionError(f"allowed transition failed: {source.value}->{target.value}")
            allowed_cases += 1

    for source in all_states:
        allowed = EXPECTED_ALLOWED[source.value]
        for target in all_states:
            if target.value in allowed:
                continue
            case = f"forbidden-{source.value}-{target.value}"
            runtime = HeadlessRuntime(root / f"{case}.sqlite3", root / f"{case}-state")
            job_id = _reach_state(runtime, project, source, case=case)
            try:
                runtime.transition(job_id, target)
            except ValueError as exc:
                expected = f"invalid job transition: {source.value} -> {target.value}"
                if str(exc) != expected:
                    raise AssertionError(f"forbidden transition error drift: {exc!r}") from exc
            else:
                raise AssertionError(f"forbidden transition succeeded: {source.value}->{target.value}")
            forbidden_cases += 1

    return {
        "allowed": {key: sorted(value) for key, value in EXPECTED_ALLOWED.items()},
        "allowed_case_count": allowed_cases,
        "forbidden_case_count": forbidden_cases,
        "all_states": [state.value for state in all_states],
    }


def certify(repo: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="syntavra-python-headless-reference-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        project.mkdir()
        (project / ".git").mkdir()

        def call(*args: str) -> dict[str, Any]:
            return _success(" ".join(args), _run_python(repo, project, state, list(args)))

        empty_status = call("run", "headless-status")
        if empty_status != {"ok": True, "jobs": 0, "states": {}}:
            raise AssertionError(f"empty headless status drift: {empty_status}")

        submitted = call(
            "run",
            "headless-submit",
            '["/bin/sh","-c","printf headless-reference"]',
            "--policy",
            '{"timeout_seconds":10,"network_hosts":[],"writable_paths":[]}',
            "--metadata",
            '{"case":"execution","unicode":"İstanbul"}',
        )
        run_job = _job(submitted, wrapped=False)
        if run_job["state"] != "queued" or run_job["attempts"] != 0 or run_job["result"] != {}:
            raise AssertionError(f"submit state drift: {run_job}")
        run_id = str(run_job["job_id"])

        queued_status = call("run", "headless-status", run_id)
        if _job(queued_status, wrapped=True)["state"] != "queued":
            raise AssertionError(f"queued status drift: {queued_status}")
        initial_events = _events(call("run", "headless-events", run_id))
        if [row["event_type"] for row in initial_events] != ["submitted"]:
            raise AssertionError(f"submitted event drift: {initial_events}")

        run_once = call("run", "headless-run", "--worker", "python-reference-worker")
        completed_job = _job(run_once, wrapped=True)
        if completed_job["state"] != "completed" or completed_job["attempts"] != 1:
            raise AssertionError(f"headless-run state drift: {completed_job}")
        if completed_job["claimed_by"] != "python-reference-worker":
            raise AssertionError(f"headless worker ownership drift: {completed_job}")
        execution = _validate_execution(completed_job)

        completed_status = _job(call("run", "headless-status", run_id), wrapped=True)
        if completed_status != completed_job:
            raise AssertionError("headless-status does not reflect durable completed job")
        run_events = _events(call("run", "headless-events", run_id))
        if [row["event_type"] for row in run_events] != ["submitted", "claimed", "running", "completed"]:
            raise AssertionError(f"headless execution event ordering drift: {run_events}")

        event_count_before_idempotent_cancel = len(run_events)
        cancel_completed = _job(call("run", "headless-cancel", run_id, "--reason", "ignored-final-cancel"), wrapped=True)
        if cancel_completed != completed_job:
            raise AssertionError("cancelling a completed job is no longer idempotent")
        if len(_events(call("run", "headless-events", run_id))) != event_count_before_idempotent_cancel:
            raise AssertionError("idempotent final-state cancel unexpectedly appended an event")

        lifecycle_submit = call(
            "run",
            "headless-submit",
            '["/bin/sh","-c","printf lifecycle"]',
            "--workspace-type",
            "local-worktree",
            "--policy",
            '{"timeout_seconds":11,"strict_native":false}',
            "--metadata",
            '{"case":"lifecycle","unicode":"İstanbul"}',
        )
        lifecycle_job = _job(lifecycle_submit, wrapped=False)
        lifecycle_id = str(lifecycle_job["job_id"])
        cancelled = _job(
            call("run", "headless-cancel", lifecycle_id, "--reason", "python reference cancellation"),
            wrapped=True,
        )
        if cancelled["state"] != "cancelled" or cancelled["result"] != {"cancel_reason": "python reference cancellation"}:
            raise AssertionError(f"headless cancel contract drift: {cancelled}")
        resumed = _job(call("run", "headless-resume", lifecycle_id), wrapped=True)
        if resumed["state"] != "queued" or resumed["claimed_by"] != "":
            raise AssertionError(f"headless resume contract drift: {resumed}")
        lifecycle_events = _events(call("run", "headless-events", lifecycle_id))
        if [row["event_type"] for row in lifecycle_events] != ["submitted", "cancelled", "resumed"]:
            raise AssertionError(f"headless lifecycle event ordering drift: {lifecycle_events}")

        resume_queued = _run_python(repo, project, state, ["run", "headless-resume", lifecycle_id])
        resume_queued_case = _public_failure("resume queued", resume_queued)
        if "job cannot be resumed from queued" not in str(resume_queued_case["detail"]):
            raise AssertionError(f"queued resume reason drift: {resume_queued_case}")

        bundle = project / "job-bundle.json"
        exported = call("run", "headless-export", lifecycle_id, str(bundle))
        if set(exported) != {"ok", "path", "sha256", "job_id"} or exported.get("ok") is not True:
            raise AssertionError(f"headless export schema drift: {exported}")
        envelope = _verify_bundle(bundle)
        if exported["sha256"] != envelope["sha256"] or exported["job_id"] != lifecycle_id:
            raise AssertionError("headless export receipt and bundle disagree")

        imported_value = call("run", "headless-import", str(bundle), "--workspace", str(project))
        imported = _job(imported_value, wrapped=True)
        imported_id = str(imported["job_id"])
        if imported["state"] != "queued" or imported["result"] != {}:
            raise AssertionError(f"headless import state drift: {imported}")
        if imported["command"] != lifecycle_job["command"] or imported["policy"] != lifecycle_job["policy"]:
            raise AssertionError("headless import lost command/policy semantics")
        if imported["metadata"].get("case") != "lifecycle" or imported["metadata"].get("unicode") != "İstanbul":
            raise AssertionError(f"headless import metadata drift: {imported}")
        if imported["metadata"].get("imported_from") != str(bundle):
            raise AssertionError(f"headless import provenance drift: {imported}")
        if _job(call("run", "headless-status", imported_id), wrapped=True) != imported:
            raise AssertionError("headless imported status does not match durable job")

        final_status = call("run", "headless-status")
        if final_status.get("jobs") != 3 or final_status.get("states") != {"completed": 1, "queued": 2}:
            raise AssertionError(f"final headless stats drift: {final_status}")

        unknown_status = _public_failure(
            "unknown status",
            _run_python(repo, project, state, ["run", "headless-status", "sha256:" + "0" * 64]),
        )
        unknown_events = _public_failure(
            "unknown events",
            _run_python(repo, project, state, ["run", "headless-events", "sha256:" + "0" * 64]),
        )
        malformed_submit = _public_failure(
            "malformed submit command",
            _run_python(repo, project, state, ["run", "headless-submit", "{}"]),
        )
        malformed_policy = _public_failure(
            "malformed submit policy",
            _run_python(
                repo,
                project,
                state,
                ["run", "headless-submit", '["/bin/true"]', "--policy", "[]"],
            ),
        )
        missing_events_arg = _argparse_failure(
            "missing headless-events job id",
            _run_python(repo, project, state, ["run", "headless-events"]),
        )

        tampered = project / "tampered-bundle.json"
        tampered_value = json.loads(bundle.read_text(encoding="utf-8"))
        tampered_value["sha256"] = "0" * 64
        tampered.write_text(json.dumps(tampered_value), encoding="utf-8")
        tampered_import = _public_failure(
            "tampered bundle import",
            _run_python(repo, project, state, ["run", "headless-import", str(tampered), "--workspace", str(project)]),
        )
        if "headless bundle integrity failure" not in str(tampered_import["detail"]):
            raise AssertionError(f"tampered bundle reason drift: {tampered_import}")

        db_snapshot = _sqlite_snapshot(state / "unified" / "headless.sqlite3")
        db_states: dict[str, int] = {}
        for row in db_snapshot["jobs"]:
            db_states[row["state"]] = db_states.get(row["state"], 0) + 1
        if db_states != final_status["states"]:
            raise AssertionError(f"SQLite state counts disagree with CLI stats: {db_states} vs {final_status}")
        if len(db_snapshot["jobs"]) != final_status["jobs"]:
            raise AssertionError("SQLite job count disagrees with CLI stats")

        state_machine = _state_machine_contract(root / "state-machine", project)
        job_ids = {run_id, lifecycle_id, imported_id}
        normalized_bundle = _normalize(envelope["payload"], project=project, job_ids=job_ids)
        normalized_completed = _normalize(completed_job, project=project, job_ids=job_ids)
        normalized_imported = _normalize(imported, project=project, job_ids=job_ids)

        cases = {
            "empty_status": empty_status,
            "submit_queued": {
                "state": run_job["state"],
                "attempts": run_job["attempts"],
                "job_keys": sorted(run_job),
                "event_types": [row["event_type"] for row in initial_events],
            },
            "run_completed": {
                "state": completed_job["state"],
                "attempts": completed_job["attempts"],
                "claimed_by": completed_job["claimed_by"],
                "event_types": [row["event_type"] for row in run_events],
                "execution_keys": sorted(execution),
                "backend_keys": sorted(execution["backend"]),
                "normalized_job": normalized_completed,
            },
            "cancel_resume": {
                "cancelled_state": cancelled["state"],
                "resumed_state": resumed["state"],
                "event_types": [row["event_type"] for row in lifecycle_events],
                "completed_cancel_idempotent": True,
            },
            "export_import": {
                "export_keys": sorted(exported),
                "bundle_schema": envelope["payload"]["schema"],
                "bundle_payload_keys": sorted(envelope["payload"]),
                "normalized_bundle": normalized_bundle,
                "normalized_imported_job": normalized_imported,
            },
            "resume_queued_error": resume_queued_case,
            "unknown_status_error": unknown_status,
            "unknown_events_error": unknown_events,
            "malformed_submit_error": malformed_submit,
            "malformed_policy_error": malformed_policy,
            "missing_events_argument": missing_events_arg,
            "tampered_bundle_error": tampered_import,
            "sqlite": {
                "tables": db_snapshot["tables"],
                "indexes": db_snapshot["indexes"],
                "job_count": len(db_snapshot["jobs"]),
                "event_count": len(db_snapshot["events"]),
                "states": db_states,
            },
        }

    return {
        "ok": True,
        "schema_version": 1,
        "family": "headless",
        "engine": "python",
        "exact_head": _exact_head(repo),
        "routes": ROUTES,
        "exit_policy": {
            "success": 0,
            "application_error": 4,
            "argument_parser_error": 2,
        },
        "sqlite_state_machine": state_machine,
        "nondeterministic_fields": [
            "created_at",
            "updated_at",
            "sandbox started_at",
            "sandbox duration_ms",
            "sandbox receipt_id",
            "time-derived sha256 job_id",
            "temporary project/state paths",
            "export bundle sha256 when dynamic fields differ",
        ],
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the Python-only headless public reference contract")
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
            "family": "headless",
            "engine": "python",
            "exact_head": _exact_head(repo),
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
