#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

DYNAMIC_VALUE_KEYS = {"created_at", "updated_at", "started_at", "finished_at", "created", "duration_ms", "receipt_id"}


def run_engine(
    engine: str,
    args: list[str],
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state_root: Path,
) -> dict[str, Any]:
    common = ["--project", str(project), "--state-root", str(state_root), *args]
    if engine == "python":
        command = [sys.executable, "-m", "syntavra_runtime.engine_entry", "--engine", "python", *common]
    elif engine == "rust":
        command = [str(rust_bin), "--engine", "rust", *common]
    else:
        raise ValueError(engine)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "SYNTAVRA_BULK_PARITY_PROBE": "1",
        }
    )
    completed = subprocess.run(
        command,
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        if completed.returncode == 0:
            raise RuntimeError(
                f"{engine} emitted non-JSON success for {' '.join(args)}\n"
                f"exit={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
            ) from exc
        value = None
    return {"exit": completed.returncode, "value": value, "stderr": completed.stderr}


def require_success(result: dict[str, Any], *, engine: str, label: str) -> dict[str, Any]:
    if result["exit"] != 0:
        raise RuntimeError(f"{engine} {label} failed: {result}")
    value = result["value"]
    if not isinstance(value, dict):
        raise RuntimeError(f"{engine} {label} returned non-object JSON: {value!r}")
    return value


def error_signature(result: dict[str, Any]) -> dict[str, Any]:
    text = (result.get("stderr") or "") + "\n" + json.dumps(result.get("value"), ensure_ascii=False, default=str)
    known = "job cannot be resumed from queued"
    if known in text:
        reason = known
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        reason = lines[-1] if lines else ""
    return {"exit": int(result["exit"]), "reason": reason}


def validate_dynamic_fields(value: Any, *, engine: str, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in {"created_at", "updated_at", "started_at", "finished_at", "created"}:
                if not isinstance(child, str):
                    raise RuntimeError(f"{engine} dynamic timestamp is not text at {child_path}: {child!r}")
                try:
                    parsed = datetime.fromisoformat(child)
                except ValueError as exc:
                    raise RuntimeError(f"{engine} dynamic timestamp is not ISO-8601 at {child_path}: {child!r}") from exc
                if parsed.tzinfo is None:
                    raise RuntimeError(f"{engine} dynamic timestamp lacks timezone at {child_path}: {child!r}")
            elif key == "duration_ms":
                if not isinstance(child, (int, float)) or isinstance(child, bool) or child < 0:
                    raise RuntimeError(f"{engine} duration_ms invalid at {child_path}: {child!r}")
            elif key == "receipt_id":
                if not isinstance(child, str) or not child.startswith("sha256:") or len(child) != 71:
                    raise RuntimeError(f"{engine} receipt_id invalid at {child_path}: {child!r}")
            validate_dynamic_fields(child, engine=engine, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_dynamic_fields(child, engine=engine, path=f"{path}[{index}]")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def verify_bundle(path: Path) -> dict[str, Any]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    payload = envelope["payload"]
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    if digest != envelope.get("sha256"):
        raise RuntimeError(f"bundle integrity failure in validator: {path}")
    return envelope


def normalize(
    value: Any,
    *,
    project: Path,
    bundle: Path,
    job_ids: set[str],
) -> Any:
    if isinstance(value, dict):
        return {
            key: normalize(child, project=project, bundle=bundle, job_ids=job_ids)
            for key, child in value.items()
            if key not in DYNAMIC_VALUE_KEYS
        }
    if isinstance(value, list):
        return [normalize(child, project=project, bundle=bundle, job_ids=job_ids) for child in value]
    if isinstance(value, str):
        if value in job_ids:
            return "<job-id>"
        rendered = value.replace(str(project.resolve(strict=False)), "<project>")
        rendered = rendered.replace(str(bundle.resolve(strict=False)), "<bundle>")
        for job_id in job_ids:
            rendered = rendered.replace(job_id, "<job-id>")
        return rendered
    return value


def exercise(
    engine: str,
    *,
    repo: Path,
    rust_bin: Path,
    root: Path,
) -> dict[str, Any]:
    project = root / f"{engine}-project"
    state = root / f"{engine}-state"
    project.mkdir()
    (project / ".git").mkdir()
    bundle = project / "job-bundle.json"

    def call(*args: str) -> dict[str, Any]:
        return require_success(
            run_engine(
                engine,
                list(args),
                repo=repo,
                rust_bin=rust_bin,
                project=project,
                state_root=state,
            ),
            engine=engine,
            label=" ".join(args),
        )

    empty_status = call("run", "headless-status")

    run_submit = call(
        "run",
        "headless-submit",
        '["/bin/sh","-c","printf headless-parity"]',
        "--policy",
        '{"timeout_seconds":10,"network_hosts":[],"writable_paths":[]}',
        "--metadata",
        '{"case":"run"}',
    )
    run_job_id = str(run_submit.get("job_id") or (run_submit.get("job") or {}).get("job_id") or "")
    if not run_job_id:
        raise RuntimeError(f"{engine} headless-submit did not return job_id: {run_submit}")
    run_once = call("run", "headless-run", "--worker", "diff-worker")
    run_status = call("run", "headless-status", run_job_id)
    run_events = call("run", "headless-events", run_job_id)

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
    lifecycle_id = str(
        lifecycle_submit.get("job_id")
        or (lifecycle_submit.get("job") or {}).get("job_id")
        or ""
    )
    if not lifecycle_id:
        raise RuntimeError(f"{engine} lifecycle submit did not return job_id: {lifecycle_submit}")
    cancelled = call("run", "headless-cancel", lifecycle_id, "--reason", "differential cancellation")
    resumed = call("run", "headless-resume", lifecycle_id)
    lifecycle_events = call("run", "headless-events", lifecycle_id)
    exported = call("run", "headless-export", lifecycle_id, str(bundle))
    envelope = verify_bundle(bundle)
    imported = call("run", "headless-import", str(bundle), "--workspace", str(project))
    imported_id = str((imported.get("job") or {}).get("job_id") or imported.get("job_id") or "")
    if not imported_id:
        raise RuntimeError(f"{engine} headless-import did not return job_id: {imported}")
    imported_status = call("run", "headless-status", imported_id)
    final_status = call("run", "headless-status")

    # Error behavior is part of the public contract too. Resuming a queued job
    # must fail rather than silently mutate it.
    resume_queued_error = run_engine(
        engine,
        ["run", "headless-resume", lifecycle_id],
        repo=repo,
        rust_bin=rust_bin,
        project=project,
        state_root=state,
    )

    if resume_queued_error["exit"] == 0:
        raise RuntimeError(f"{engine} unexpectedly resumed queued job: {resume_queued_error}")

    job_ids = {run_job_id, lifecycle_id, imported_id}
    normalized_bundle_payload = normalize(
        envelope["payload"], project=project, bundle=bundle, job_ids=job_ids
    )
    result = {
        "empty_status": empty_status,
        "run_submit": run_submit,
        "run_once": run_once,
        "run_status": run_status,
        "run_events": run_events,
        "lifecycle_submit": lifecycle_submit,
        "cancelled": cancelled,
        "resumed": resumed,
        "lifecycle_events": lifecycle_events,
        "exported": exported,
        "bundle_payload": normalized_bundle_payload,
        "imported": imported,
        "imported_status": imported_status,
        "final_status": final_status,
        "resume_queued_error": error_signature(resume_queued_error),
    }
    validate_dynamic_fields(result, engine=engine)
    result["exported"]["sha256"] = "<bundle-sha256>"
    return normalize(result, project=project, bundle=bundle, job_ids=job_ids)


def diff_values(path: str, python: Any, rust: Any, out: list[dict[str, Any]]) -> None:
    if type(python) is not type(rust):
        out.append({"path": path, "python": python, "rust": rust})
        return
    if isinstance(python, dict):
        keys = sorted(set(python) | set(rust))
        for key in keys:
            child = f"{path}.{key}" if path else key
            if key not in python or key not in rust:
                out.append({"path": child, "python": python.get(key), "rust": rust.get(key)})
            else:
                diff_values(child, python[key], rust[key], out)
        return
    if isinstance(python, list):
        if len(python) != len(rust):
            out.append({"path": f"{path}.length", "python": len(python), "rust": len(rust)})
        for index, (left, right) in enumerate(zip(python, rust)):
            diff_values(f"{path}[{index}]", left, right, out)
        return
    if python != rust:
        out.append({"path": path, "python": python, "rust": rust})


def compare(python_result: dict[str, Any], rust_result: dict[str, Any]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    diff_values("", python_result, rust_result, mismatches)

    invariants = [
        ("python.empty_status.jobs", python_result["empty_status"].get("jobs"), 0),
        ("rust.empty_status.jobs", rust_result["empty_status"].get("jobs"), 0),
        ("python.run_status.state", (python_result["run_status"].get("job") or {}).get("state"), "completed"),
        ("rust.run_status.state", (rust_result["run_status"].get("job") or {}).get("state"), "completed"),
        ("python.resumed.state", (python_result["resumed"].get("job") or {}).get("state"), "queued"),
        ("rust.resumed.state", (rust_result["resumed"].get("job") or {}).get("state"), "queued"),
        ("python.resume_queued_error.nonzero", python_result["resume_queued_error"].get("exit") != 0, True),
        ("rust.resume_queued_error.nonzero", rust_result["resume_queued_error"].get("exit") != 0, True),
    ]
    for path, actual, expected in invariants:
        if actual != expected:
            mismatches.append({"path": path, "expected": expected, "actual": actual})

    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "routes": [
            "run headless-submit",
            "run headless-run",
            "run headless-status",
            "run headless-events",
            "run headless-cancel",
            "run headless-resume",
            "run headless-export",
            "run headless-import",
        ],
        "claim_boundary": (
            "validated timestamps, engine-specific temporary project paths, time-derived job IDs, execution timing/receipt IDs, and the raw bundle digest are normalized; "
            "state transitions, event ordering/payloads, command/policy/metadata/result objects, exit codes, and normalized exported payload remain exact"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Python/Rust remaining-71 headless parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)

    with tempfile.TemporaryDirectory(prefix="syntavra-headless-diff-") as directory:
        root = Path(directory)
        python_result = exercise("python", repo=repo, rust_bin=rust_bin, root=root)
        rust_result = exercise("rust", repo=repo, rust_bin=rust_bin, root=root)
        differential = compare(python_result, rust_result)
        result = {
            "ok": differential["ok"],
            "python": python_result,
            "rust": rust_result,
            "differential": differential,
        }

    rendered = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
