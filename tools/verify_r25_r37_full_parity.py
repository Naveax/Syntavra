#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.config_contract import encode_config_wire
from syntavra_runtime.full_parity_runtime import FullParityError, execute_full_parity
from syntavra_runtime.state_snapshot_contract import project_id_for_root

RUST_SOURCE = ROOT / "crates" / "syntavra-cli" / "src" / "full_parity_runtime.rs"
RUST_BIN_SOURCE = ROOT / "crates" / "syntavra-cli" / "src" / "bin" / "syntavra-full-parity.rs"
SQLITE_RELATIVE = Path(".syntavra/pre-release/full-parity/broker.sqlite3")
INTELLIGENCE_RELATIVE = Path(".syntavra/pre-release/full-parity/intelligence.sqlite3")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def request(phase: str, operation: str, **payload: Any) -> bytes:
    return canonical({"operation": operation, "payload": payload, "phase": phase, "schema_version": 1})


def rust_binary() -> Path:
    subprocess.run(
        ["cargo", "build", "--quiet", "--locked", "--bin", "syntavra-full-parity"],
        cwd=ROOT,
        check=True,
    )
    suffix = ".exe" if os.name == "nt" else ""
    return ROOT / "target" / "debug" / f"syntavra-full-parity{suffix}"


def python_call(project: Path, project_id: str, raw: bytes) -> dict[str, Any]:
    try:
        return execute_full_parity(
            project_root=project,
            expected_project_id=project_id,
            request=raw,
        )
    except FullParityError as error:
        return {"error": error.code}


def rust_call(binary: Path, project: Path, project_id: str, raw: bytes) -> dict[str, Any]:
    completed = subprocess.run(
        [str(binary), str(project), project_id, raw.hex()],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return json.loads(completed.stdout)
    return {"error": completed.stderr.strip()}


def config_wire(profile: str = "balanced") -> str:
    return encode_config_wire(
        [{"project": {"runtime": {"profile": profile}, "routing": {"budget_bytes": 4096}}}]
    ).hex()


class Handler(BaseHTTPRequestHandler):
    body = b"syntavra-loopback-parity"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_server() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def run_sequence(
    call: Callable[[Path, str, bytes], dict[str, Any]], project: Path, project_id: str, port: int
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []

    def invoke(phase: str, operation: str, **payload: Any) -> dict[str, Any]:
        value = call(project, project_id, request(phase, operation, **payload))
        outputs.append(value)
        if "error" in value:
            raise RuntimeError(f"unexpected {phase}/{operation} failure: {value}")
        return value

    invoke("R25", "profile.create", name="default", config_wire_hex=config_wire(), metadata={"owner": "parity"}, select=True)
    invoke("R25", "profile.list")
    invoke("R25", "profile.update", name="default", config_wire_hex=config_wire("compact"), metadata={}, select=False)
    invoke("R25", "profile.select", name="default")
    invoke("R25", "profile.delete", name="default")

    ephemeral = encode_config_wire([{"session": {"runtime": {"profile": "compact"}}}]).hex()
    outputs.append(call(project, project_id, request("R25", "profile.create", name="bad", config_wire_hex=ephemeral, metadata={}, select=True)))

    content = b'{"engine":"rust"}\n'
    invoke("R26", "state.write", target="engine-selection", content_hex=content.hex())
    invoke("R26", "receipt.write", engine="rust", receipt_id="r26-1", receipt_operation="state.write", payload_hash="a" * 64, previous_hash=None, created_at_ms=123)

    invoke("R27", "broker.enqueue", job_id="job-1", argv=["echo", "hi"], priority=9)
    invoke("R27", "broker.claim", job_id="job-1", worker="worker-a")
    invoke("R27", "broker.complete", job_id="job-1", exit_code=0, stdout_hash="b" * 64)
    invoke("R27", "broker.enqueue", job_id="job-2", argv=["false"], priority=1)
    invoke("R27", "broker.cancel", job_id="job-2")
    invoke("R27", "broker.list")

    invoke("R28", "process.execute", mode="echo", value="hello", timeout_ms=5000)
    invoke("R28", "process.execute", mode="hash", value="hello", timeout_ms=5000)
    invoke("R28", "process.execute", mode="fail", value="no", timeout_ms=5000)
    invoke("R28", "process.execute", mode="sleep", value="2", timeout_ms=100)

    invoke("R29", "context.rewrite", text="alpha  alpha\r\nomega", replacements={"omega": "final"})
    compacted = invoke("R29", "context.compact", events=["alpha" * 100, "alpha" * 100, "omega" * 100], budget_bytes=128)
    invoke("R29", "context.restore", artifact_sha256=compacted["result"]["artifact_sha256"])

    invoke("R30", "memory.add", memory_id="m1", text="Rust parity architecture", tags=["rust"])
    invoke("R30", "memory.search", query="rust architecture")
    invoke("R30", "repository.index", files={"src/main.rs": "fn parity_runtime() {}"})
    invoke("R30", "repository.query", query="parity_runtime")

    candidates = [
        {"provider": "b", "model": "m2", "cost_micros": 10, "latency_ms": 5, "available": True, "max_context": 32768, "supports_tools": True},
        {"provider": "a", "model": "m1", "cost_micros": 10, "latency_ms": 5, "available": True, "max_context": 32768, "supports_tools": True},
    ]
    invoke("R31", "provider.route", candidates=candidates, task={"required_context": 4096, "max_cost_micros": 100, "require_tools": True})
    invoke("R31", "provider.loopback", host="127.0.0.1", port=port, path="/parity")

    invoke("R32", "mcp.catalog", profile="audit")
    invoke("R32", "mcp.call", tool="syntavra.parity.status", arguments={})
    invoke("R32", "mcp.call", tool="syntavra.memory.search", arguments={"query": "rust"})

    invoke("R33", "setup.plan", host="codex")
    invoke("R33", "setup.apply", host="codex")
    invoke("R33", "setup.verify", host="codex")
    repaired = invoke("R33", "setup.repair", host="codex")
    invoke(
    "R33",
    "setup.rollback",
    host="codex",
    transaction_id=repaired["result"]["transaction_id"],
)

    invoke("R34", "benchmark.compare", baseline=[{"work": 100, "quota": 10, "quality_ppm": 900000, "success": True}], candidate=[{"work": 120, "quota": 10, "quality_ppm": 910000, "success": True}])
    body = {"previous_hash": None, "value": 1}
    receipt_hash = hashlib.sha256(canonical(body)).hexdigest()
    invoke("R34", "evidence.validate", receipts=[{"body": body, "receipt_hash": receipt_hash}])

    published = invoke("R35", "publication.build", artifacts={"syntavra.zip": {"bytes": 10, "sha256": "c" * 64}})
    invoke("R35", "publication.verify", manifest_sha256=published["result"]["manifest_sha256"])

    distribution = invoke("R36", "distribution.manifest", platform="linux", architecture="x86_64", binary_sha256="d" * 64, files=["syntavra-rs", "README.md"])
    invoke("R36", "distribution.verify", manifest=distribution["result"])

    phases = {f"R{phase}": True for phase in range(25, 37)}
    dimensions = {name: True for name in ["cli", "host_setup", "mcp", "platform_packaging", "state_mutation"]}
    invoke("R37", "certification.evaluate", phases=phases, dimensions=dimensions)
    phases["R30"] = False
    invoke("R37", "certification.evaluate", phases=phases, dimensions=dimensions)
    return outputs


def logical_state(project: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for path in sorted(project.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(project)
        if relative.name.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")):
            continue
        files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()

    def query_rows(database: Path, statement: str) -> list[list[Any]]:
        if not database.exists():
            return []
        connection = sqlite3.connect(database)
        try:
            return [list(row) for row in connection.execute(statement)]
        finally:
            connection.close()

    broker_rows = query_rows(
        project / SQLITE_RELATIVE,
        "SELECT job_id, argv_json, priority, state, worker, exit_code, stdout_hash "
        "FROM jobs ORDER BY job_id",
    )
    memory_rows = query_rows(
        project / INTELLIGENCE_RELATIVE,
        "SELECT memory_id, text, tokens_json, tags_json FROM memories ORDER BY memory_id",
    )
    repository_rows = query_rows(
        project / INTELLIGENCE_RELATIVE,
        "SELECT path, content_sha256, tokens_json, language "
        "FROM repository_files ORDER BY path",
    )
    return {
        "files": files,
        "jobs": broker_rows,
        "memories": memory_rows,
        "repository_files": repository_rows,
    }

def verify() -> dict[str, Any]:
    source = RUST_SOURCE.read_text(encoding="utf-8") + RUST_BIN_SOURCE.read_text(encoding="utf-8")
    forbidden = ['Command::new("python', 'Command::new("python3', "PYTHONHOME", "PYTHONPATH"]
    present = [item for item in forbidden if item in source]
    if present:
        raise RuntimeError(f"Rust runtime invokes or embeds Python: {present}")

    binary = rust_binary()
    server, thread = start_server()
    try:
        with tempfile.TemporaryDirectory(prefix="syntavra-r25-r37-") as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            project_id = project_id_for_root(project)

            python_outputs = run_sequence(python_call, project, project_id, server.server_port)
            python_state = logical_state(project)
            shutil.rmtree(project)
            project.mkdir()

            def rust_adapter(path: Path, identifier: str, raw: bytes) -> dict[str, Any]:
                return rust_call(binary, path, identifier, raw)

            rust_outputs = run_sequence(rust_adapter, project, project_id, server.server_port)
            rust_state = logical_state(project)

        if len(python_outputs) != len(rust_outputs):
            raise RuntimeError(
                f"R25-R37 output length mismatch: python={len(python_outputs)} rust={len(rust_outputs)}"
            )
        if python_outputs != rust_outputs:
            for index, (left, right) in enumerate(zip(python_outputs, rust_outputs, strict=True)):
                if left != right:
                    raise RuntimeError(
                        f"R25-R37 output mismatch at fixture {index}:\npython={json.dumps(left, sort_keys=True)}\nrust={json.dumps(right, sort_keys=True)}"
                    )
        if python_state != rust_state:
            raise RuntimeError(
                f"R25-R37 state mismatch:\npython={json.dumps(python_state, sort_keys=True)}\nrust={json.dumps(rust_state, sort_keys=True)}"
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return {
        "claim": "FULL_PARITY_PROVEN",
        "engines": ["python", "rust"],
        "fixture_count": len(python_outputs),
        "ok": True,
        "phases": [f"R{phase}" for phase in range(25, 38)],
        "python_invocation_by_rust": False,
        "state_comparison": "exact-files-and-logical-sqlite",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
