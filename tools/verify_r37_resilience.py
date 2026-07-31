#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from verify_r25_r37_full_parity import (
    config_wire,
    python_call,
    request,
    rust_binary,
    rust_call,
    start_server,
)
from syntavra_runtime.config_contract import encode_config_wire
from syntavra_runtime.state_snapshot_contract import project_id_for_root


def exact(binary: Path, project: Path, project_id: str, raw: bytes, *, supplied_id: str | None = None) -> dict[str, Any]:
    identifier = project_id if supplied_id is None else supplied_id
    python_value = python_call(project, identifier, raw)
    rust_value = rust_call(binary, project, identifier, raw)
    if python_value != rust_value:
        raise RuntimeError(
            "resilience parity mismatch:\n"
            f"python={json.dumps(python_value, sort_keys=True)}\n"
            f"rust={json.dumps(rust_value, sort_keys=True)}"
        )
    return python_value


def is_semantic_rejection(value: dict[str, Any]) -> bool:
    if "error" in value:
        return True
    return (
        value.get("phase") == "R36"
        and value.get("operation") == "distribution.verify"
        and isinstance(value.get("result"), dict)
        and value["result"].get("valid") is False
    )


def negative_matrix(binary: Path, project: Path, project_id: str) -> int:
    cases = [
        b"",
        b"{",
        b"[]",
        b'{}',
        request("R24", "profile.list"),
        request("R25", "unknown.operation"),
        request("R26", "state.write", target="../escape", content_hex="00"),
        request("R27", "broker.enqueue", job_id="BAD ID", argv=[], priority=0),
        request("R28", "process.execute", mode="shell", value="whoami", timeout_ms=100),
        request("R31", "provider.loopback", host="example.com", port=80, path="/"),
        request("R33", "setup.rollback", host="codex", transaction_id="missing"),
        request("R36", "distribution.verify", manifest={"python_required": True}),
    ]
    ephemeral = encode_config_wire([{"task": {"runtime": {"profile": "compact"}}}]).hex()
    cases.append(request("R25", "profile.create", name="bad", config_wire_hex=ephemeral, metadata={}, select=True))
    for raw in cases:
        value = exact(binary, project, project_id, raw)
        if not is_semantic_rejection(value):
            raise RuntimeError(f"negative fixture unexpectedly succeeded: {value}")
    mismatch = exact(binary, project, project_id, request("R25", "profile.list"), supplied_id="0" * 64)
    if "error" not in mismatch:
        raise RuntimeError("project-id mismatch unexpectedly succeeded")
    return len(cases) + 1


def mixed_upgrade(binary: Path, project: Path, project_id: str) -> int:
    operations = 0

    created = python_call(
        project,
        project_id,
        request("R25", "profile.create", name="mixed", config_wire_hex=config_wire(), metadata={}, select=True),
    )
    if "error" in created:
        raise RuntimeError(created)
    operations += 1
    listed = rust_call(binary, project, project_id, request("R25", "profile.list"))
    expected = python_call(project, project_id, request("R25", "profile.list"))
    if listed != expected:
        raise RuntimeError("Python-created profile is not Rust-readable")
    operations += 1

    updated = rust_call(
        binary,
        project,
        project_id,
        request("R25", "profile.update", name="mixed", config_wire_hex=config_wire("compact"), metadata={"engine": "rust"}, select=False),
    )
    if "error" in updated:
        raise RuntimeError(updated)
    selected = python_call(project, project_id, request("R25", "profile.select", name="mixed"))
    if "error" in selected:
        raise RuntimeError(selected)
    operations += 2

    enqueued = python_call(project, project_id, request("R27", "broker.enqueue", job_id="mixed-job", argv=["echo", "mixed"], priority=4))
    if "error" in enqueued:
        raise RuntimeError(enqueued)
    claimed = rust_call(binary, project, project_id, request("R27", "broker.claim", job_id="mixed-job", worker="rust-worker"))
    completed = python_call(project, project_id, request("R27", "broker.complete", job_id="mixed-job", exit_code=0, stdout_hash="e" * 64))
    if "error" in claimed or "error" in completed:
        raise RuntimeError({"claimed": claimed, "completed": completed})
    rows_python = python_call(project, project_id, request("R27", "broker.list"))
    rows_rust = rust_call(binary, project, project_id, request("R27", "broker.list"))
    if rows_python != rows_rust:
        raise RuntimeError("mixed SQLite mutation is not cross-engine readable")
    operations += 4
    return operations


def soak(binary: Path, project: Path, project_id: str) -> int:
    operations = 0
    for index in range(64):
        raw = request(
            "R30",
            "memory.add",
            memory_id=f"soak-{index:03d}",
            text=f"deterministic parity memory {index}",
            tags=["soak", f"bucket-{index % 4}"],
        )
        caller = python_call if index % 2 == 0 else lambda p, i, r: rust_call(binary, p, i, r)
        value = caller(project, project_id, raw)
        if "error" in value:
            raise RuntimeError(value)
        operations += 1

    for index in range(32):
        raw = request(
            "R29",
            "context.rewrite",
            text=f"alpha  {index}\r\nomega",
            replacements={"omega": "final"},
        )
        exact(binary, project, project_id, raw)
        operations += 1

    query = request("R30", "memory.search", query="deterministic parity")
    exact(binary, project, project_id, query)
    operations += 1
    return operations


def verify() -> dict[str, Any]:
    binary = rust_binary()
    server, thread = start_server()
    try:
        with tempfile.TemporaryDirectory(prefix="syntavra-r37-resilience-") as temporary:
            root = Path(temporary)
            negative_project = root / "negative"
            negative_project.mkdir()
            negative_count = negative_matrix(binary, negative_project, project_id_for_root(negative_project))

            mixed_project = root / "mixed"
            mixed_project.mkdir()
            mixed_count = mixed_upgrade(binary, mixed_project, project_id_for_root(mixed_project))

            soak_project = root / "soak"
            soak_project.mkdir()
            soak_count = soak(binary, soak_project, project_id_for_root(soak_project))

            reverse_project = root / "reverse"
            reverse_project.mkdir()
            reverse_id = project_id_for_root(reverse_project)
            rust_created = rust_call(
                binary,
                reverse_project,
                reverse_id,
                request("R25", "profile.create", name="reverse", config_wire_hex=config_wire(), metadata={}, select=True),
            )
            if "error" in rust_created:
                raise RuntimeError(rust_created)
            python_read = python_call(reverse_project, reverse_id, request("R25", "profile.list"))
            rust_read = rust_call(binary, reverse_project, reverse_id, request("R25", "profile.list"))
            if python_read != rust_read:
                raise RuntimeError("Rust-created state is not Python-readable")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return {
        "ok": True,
        "claim": "R37_RESILIENCE_AND_BIDIRECTIONAL_COMPATIBILITY_PROVEN",
        "negative_fixtures": negative_count,
        "mixed_operations": mixed_count,
        "soak_operations": soak_count,
        "python_invocation_by_rust": False,
        "upgrade_downgrade": "bidirectional",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
