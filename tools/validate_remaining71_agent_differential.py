#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PATCH = """diff --git a/sample.py b/sample.py
--- a/sample.py
+++ b/sample.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""
VERIFIER = ["/bin/sh", "-c", "grep -q 'VALUE = 2' sample.py"]
DYNAMIC_KEYS = {
    "started_at",
    "finished_at",
    "duration_ms",
    "receipt_id",
    "created_at",
}


def run_engine(
    engine: str,
    args: list[str],
    *,
    repo: Path,
    rust_bin: Path,
    project: Path,
    state_root: Path,
    timeout: int = 90,
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
        timeout=timeout,
        check=False,
    )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        value = None
    return {
        "exit": completed.returncode,
        "value": value,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def require_success(result: dict[str, Any], *, engine: str, label: str) -> dict[str, Any]:
    if result["exit"] != 0:
        raise RuntimeError(f"{engine} {label} failed: {result}")
    value = result["value"]
    if not isinstance(value, dict):
        raise RuntimeError(f"{engine} {label} returned non-object JSON: {result}")
    return value


def git(*args: str, cwd: Path) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr}")


def prepare_project(project: Path) -> None:
    project.mkdir(parents=True)
    (project / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "Makefile").write_text(
        "test:\n\tgrep -q 'VALUE = 2' sample.py\n",
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# Agent parity fixture\n\nThe task changes VALUE from 1 to 2.\n",
        encoding="utf-8",
    )
    git("init", "-q", cwd=project)
    git("config", "user.name", "Syntavra Differential", cwd=project)
    git("config", "user.email", "syntavra-differential@example.invalid", cwd=project)
    git("add", ".", cwd=project)
    git("commit", "-qm", "fixture", cwd=project)


class MockGateway:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = None
                owner.requests.append({"path": self.path, "body": body})
                action = json.dumps(
                    {
                        "action": "patch",
                        "patch": PATCH,
                        "rationale": "deterministic parity fixture",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                response = {
                    "id": "mock-agent-response",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": action},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 5,
                        "total_tokens": 12,
                    },
                }
                encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "MockGateway":
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def dynamic_issues(value: Any, *, engine: str, path: str = "") -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in {"started_at", "finished_at"}:
                valid = isinstance(child, str)
                if valid:
                    try:
                        parsed = datetime.fromisoformat(child)
                        valid = parsed.tzinfo is not None
                    except ValueError:
                        valid = False
                if not valid:
                    issues.append(
                        {
                            "path": f"{engine}.{child_path}.iso8601",
                            "expected": True,
                            "actual": child,
                        }
                    )
            elif key == "duration_ms":
                if not isinstance(child, (int, float)) or isinstance(child, bool) or child < 0:
                    issues.append(
                        {
                            "path": f"{engine}.{child_path}.nonnegative",
                            "expected": True,
                            "actual": child,
                        }
                    )
            elif key == "receipt_id":
                if not isinstance(child, str) or not child.startswith("sha256:"):
                    issues.append(
                        {
                            "path": f"{engine}.{child_path}.sha256",
                            "expected": True,
                            "actual": child,
                        }
                    )
            issues.extend(dynamic_issues(child, engine=engine, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(dynamic_issues(child, engine=engine, path=f"{path}[{index}]"))
    return issues


def normalize(value: Any, *, project: Path, state_root: Path) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, child in value.items():
            if key in DYNAMIC_KEYS:
                continue
            if key == "run_id" and isinstance(child, str):
                output[key] = "<run-id>"
                continue
            if key == "plan_hash" and isinstance(child, str):
                output[key] = child
                continue
            output[key] = normalize(child, project=project, state_root=state_root)
        return output
    if isinstance(value, list):
        return [normalize(child, project=project, state_root=state_root) for child in value]
    if isinstance(value, str):
        rendered = value
        rendered = rendered.replace(str(project.resolve(strict=False)), "<project>")
        rendered = rendered.replace(str(state_root.resolve(strict=False)), "<state>")
        # Agent workspaces contain random directory names below the state root.
        if "<state>/unified/agent-workspaces/run-" in rendered:
            prefix = "<state>/unified/agent-workspaces/run-"
            start = rendered.find(prefix)
            tail = rendered[start + len(prefix):]
            token = tail.split("/", 1)[0].split("\\", 1)[0]
            rendered = rendered.replace(prefix + token, "<workspace>")
        if "<state>/agent-product/agent-workspaces/run-" in rendered:
            prefix = "<state>/agent-product/agent-workspaces/run-"
            start = rendered.find(prefix)
            tail = rendered[start + len(prefix):]
            token = tail.split("/", 1)[0].split("\\", 1)[0]
            rendered = rendered.replace(prefix + token, "<workspace>")
        return rendered
    return value


def durable_receipts(state_root: Path) -> list[dict[str, Any]]:
    roots = [
        state_root / "unified" / "agent-receipts",
        state_root / "unified" / "agent-product" / "agent-receipts",
        state_root / "agent-product" / "agent-receipts",
    ]
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.json")):
            resolved = path.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                value = {"read_error": str(exc)}
            rows.append({"name": "<run-id>.json", "value": value})
    return rows


def request_signature(request: dict[str, Any]) -> dict[str, Any]:
    body = request.get("body")
    if not isinstance(body, dict):
        return {"path": request.get("path"), "body": body}
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    system = ""
    if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
        system = str(messages[0].get("content") or "")
    return {
        "path": request.get("path"),
        "model": body.get("model"),
        "stream": body.get("stream"),
        "temperature": body.get("temperature"),
        "max_tokens": body.get("max_tokens"),
        "system_prompt": system,
        "message_roles": [str(item.get("role")) for item in messages if isinstance(item, dict)],
    }


def exercise(
    engine: str,
    *,
    repo: Path,
    rust_bin: Path,
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    project = root / f"{engine}-project"
    state = root / f"{engine}-state"
    prepare_project(project)

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

    def call_raw(*args: str) -> dict[str, Any]:
        result = run_engine(
            engine,
            list(args),
            repo=repo,
            rust_bin=rust_bin,
            project=project,
            state_root=state,
        )
        if not isinstance(result.get("value"), dict):
            raise RuntimeError(f"{engine} {' '.join(args)} returned non-object JSON: {result}")
        return {"exit": result["exit"], "value": result["value"]}

    plan = call(
        "run",
        "agent-plan",
        "change VALUE to 2",
        "--session-id",
        "agent-plan-parity",
        "--max-symbols",
        "5",
        "--index",
    )
    plan_memory = call("run", "memory-verify", "agent-plan-parity")

    call(
        "run",
        "memory-open",
        "--session-id",
        "agent-exec-blocked",
        "--metadata",
        '{"case":"blocked"}',
    )
    blocked = call_raw(
        "run",
        "agent-execute",
        "change VALUE to 2",
        json.dumps([{"patch": PATCH, "rationale": "fixture", "estimated_tokens": 11, "estimated_cost": 0.25}]),
        json.dumps(VERIFIER),
        "--mode",
        "review-required",
        "--session-id",
        "agent-exec-blocked",
    )
    blocked_memory = call("run", "memory-verify", "agent-exec-blocked")
    receipts_after_blocked = durable_receipts(state)

    call(
        "run",
        "memory-open",
        "--session-id",
        "agent-exec-success",
        "--metadata",
        '{"case":"success"}',
    )
    executed = call(
        "run",
        "agent-execute",
        "change VALUE to 2",
        json.dumps([{"patch": PATCH, "rationale": "fixture", "estimated_tokens": 11, "estimated_cost": 0.25}]),
        json.dumps(VERIFIER),
        "--mode",
        "safe-autonomous",
        "--authorized",
        "--session-id",
        "agent-exec-success",
        "--attempts",
        "2",
        "--timeout",
        "30",
    )
    success_memory = call("run", "memory-verify", "agent-exec-success")

    replay = call(
        "agent",
        "replay",
        "change VALUE to 2",
        json.dumps([{"patch": PATCH, "rationale": "fixture", "estimated_tokens": 11, "estimated_cost": 0.25}]),
        json.dumps(VERIFIER),
        "--mode",
        "safe-autonomous",
        "--authorized",
        "--attempts",
        "2",
        "--timeout",
        "30",
    )

    with MockGateway() as gateway:
        live = call(
            "agent",
            "run",
            "change VALUE to 2",
            "--provider",
            "openai-compatible",
            "--model",
            "mock-model",
            "--endpoint",
            gateway.endpoint,
            "--api-mode",
            "chat",
            "--mode",
            "safe-autonomous",
            "--authorized",
            "--attempts",
            "1",
            "--timeout",
            "30",
            "--delivery",
            "diff",
            "--no-post-verifiers",
        )
        requests = [request_signature(item) for item in gateway.requests]

    all_receipts = durable_receipts(state)
    result = {
        "plan": plan,
        "plan_memory": plan_memory,
        "blocked_execute": blocked,
        "blocked_memory": blocked_memory,
        "receipts_after_blocked": receipts_after_blocked,
        "successful_execute": executed,
        "success_memory": success_memory,
        "replay": replay,
        "live_run": live,
        "live_requests": requests,
        "durable_receipts": all_receipts,
    }
    issues = dynamic_issues(result, engine=engine)
    return normalize(result, project=project, state_root=state), issues


def diff_values(path: str, python: Any, rust: Any, out: list[dict[str, Any]]) -> None:
    if type(python) is not type(rust):
        out.append({"path": path, "python": python, "rust": rust})
        return
    if isinstance(python, dict):
        for key in sorted(set(python) | set(rust)):
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


def compare(
    python_result: dict[str, Any],
    rust_result: dict[str, Any],
    dynamic_failures: list[dict[str, Any]],
) -> dict[str, Any]:
    mismatches = list(dynamic_failures)
    diff_values("", python_result, rust_result, mismatches)
    invariants = [
        ("python.plan.execution_mode", python_result["plan"].get("execution_mode"), "plan-only-until-authorized"),
        ("rust.plan.execution_mode", rust_result["plan"].get("execution_mode"), "plan-only-until-authorized"),
        ("python.blocked.state", python_result["blocked_execute"].get("state"), "blocked"),
        ("rust.blocked.state", rust_result["blocked_execute"].get("state"), "blocked"),
        ("python.success.ok", python_result["successful_execute"].get("ok"), True),
        ("rust.success.ok", rust_result["successful_execute"].get("ok"), True),
        ("python.replay.ok", python_result["replay"].get("ok"), True),
        ("rust.replay.ok", rust_result["replay"].get("ok"), True),
        ("python.live.ok", python_result["live_run"].get("ok"), True),
        ("rust.live.ok", rust_result["live_run"].get("ok"), True),
        ("python.live.request_count", len(python_result["live_requests"]), 1),
        ("rust.live.request_count", len(rust_result["live_requests"]), 1),
    ]
    for path, actual, expected in invariants:
        if actual != expected:
            mismatches.append({"path": path, "expected": expected, "actual": actual})
    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "routes": [
            "run agent-plan",
            "run agent-execute",
            "agent replay",
            "agent run",
        ],
        "claim_boundary": (
            "timestamps, execution durations, cryptographic receipt IDs, random run IDs, and temporary workspace/project/state paths are validated then normalized; "
            "plan structure/hash, authorization behavior, patch/verifier receipts, memory verification, durable receipt shape, live model request contract, tool trace, usage, delivery result, exit-success semantics, and all other public fields remain exact"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Python/Rust remaining-71 agent parity")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--rust-bin", default="target/debug/syntavra")
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (repo / rust_bin).resolve(strict=True)
    if shutil.which("git") is None or shutil.which("make") is None:
        raise RuntimeError("agent differential requires git and make")

    with tempfile.TemporaryDirectory(prefix="syntavra-agent-diff-") as directory:
        root = Path(directory)
        python_result, python_dynamic = exercise(
            "python", repo=repo, rust_bin=rust_bin, root=root
        )
        rust_result, rust_dynamic = exercise(
            "rust", repo=repo, rust_bin=rust_bin, root=root
        )
        differential = compare(
            python_result,
            rust_result,
            [*python_dynamic, *rust_dynamic],
        )
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
