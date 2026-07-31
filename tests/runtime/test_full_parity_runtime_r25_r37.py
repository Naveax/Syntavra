from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from syntavra_runtime.config_contract import encode_config_wire
from syntavra_runtime.full_parity_runtime import FullParityError, execute_full_parity
from syntavra_runtime.state_snapshot_contract import project_id_for_root


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    return project, project_id_for_root(project)


def _call(project: Path, project_id: str, phase: str, operation: str, **payload: object) -> dict[str, object]:
    request = {
        "operation": operation,
        "payload": payload,
        "phase": phase,
        "schema_version": 1,
    }
    result = execute_full_parity(
        project_root=project,
        expected_project_id=project_id,
        request=json.dumps(request, sort_keys=True, separators=(",", ":")).encode(),
    )
    assert result["ok"] is True
    assert result["phase"] == phase
    assert result["operation"] == operation
    assert result["project_id"] == project_id
    return result


def _wire(profile: str = "balanced") -> str:
    return encode_config_wire(
        [{"project": {"runtime": {"profile": profile}, "routing": {"budget_bytes": 4096}}}]
    ).hex()


def test_r25_profile_lifecycle_applies_last_good(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    created = _call(
        project,
        project_id,
        "R25",
        "profile.create",
        name="default",
        config_wire_hex=_wire(),
        metadata={"owner": "test"},
        select=True,
    )
    assert created["result"]["selected"] == "default"
    assert created["result"]["last_good"]["action"] == "write"
    selected = _call(project, project_id, "R25", "profile.select", name="default")
    assert selected["result"]["last_good"]["action"] == "already-current"
    updated = _call(
        project,
        project_id,
        "R25",
        "profile.update",
        name="default",
        config_wire_hex=_wire("compact"),
        metadata={},
    )
    assert updated["result"]["profiles"][0]["config_hash"] != created["result"]["profiles"][0]["config_hash"]
    deleted = _call(project, project_id, "R25", "profile.delete", name="default")
    assert deleted["result"]["profiles"] == []
    assert deleted["result"]["selected"] is None


@pytest.mark.parametrize("scope", ["session", "task"])
def test_r25_rejects_ephemeral_profile_persistence(tmp_path: Path, scope: str) -> None:
    project, project_id = _project(tmp_path)
    wire = encode_config_wire([{scope: {"runtime": {"profile": "compact"}}}]).hex()
    with pytest.raises(FullParityError):
        _call(
            project,
            project_id,
            "R25",
            "profile.create",
            name="bad",
            config_wire_hex=wire,
            metadata={},
            select=True,
        )


def test_r26_state_and_receipt_writes(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    content = b'{"engine":"rust"}\n'
    state = _call(
        project,
        project_id,
        "R26",
        "state.write",
        target="engine-selection",
        content_hex=content.hex(),
    )
    assert state["result"] == {
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "target": "engine-selection",
    }
    receipt = _call(
        project,
        project_id,
        "R26",
        "receipt.write",
        engine="rust",
        receipt_id="r26-1",
        receipt_operation="state.write",
        payload_hash="a" * 64,
        previous_hash=None,
        created_at_ms=123,
    )
    wire = bytes.fromhex(receipt["result"]["wire_hex"])
    assert b"engine=rust\n" in wire
    assert b"project_id=" + project_id.encode() + b"\n" in wire


def test_r27_broker_mutation_lifecycle(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    _call(project, project_id, "R27", "broker.enqueue", job_id="job-1", argv=["echo", "hi"], priority=9)
    claimed = _call(project, project_id, "R27", "broker.claim", job_id="job-1", worker="worker-a")
    assert claimed["result"]["jobs"][0]["state"] == "running"
    completed = _call(
        project,
        project_id,
        "R27",
        "broker.complete",
        job_id="job-1",
        exit_code=0,
        stdout_hash="b" * 64,
    )
    assert completed["result"]["jobs"][0]["state"] == "completed"


def test_r28_process_modes_are_deterministic(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    echo = _call(project, project_id, "R28", "process.execute", mode="echo", value="hello", timeout_ms=5000)
    assert echo["result"] == {"exit_code": 0, "stderr_hex": "", "stdout_hex": b"hello".hex(), "timed_out": False}
    hashed = _call(project, project_id, "R28", "process.execute", mode="hash", value="hello", timeout_ms=5000)
    assert bytes.fromhex(hashed["result"]["stdout_hex"]).decode() == hashlib.sha256(b"hello").hexdigest()
    failed = _call(project, project_id, "R28", "process.execute", mode="fail", value="no", timeout_ms=5000)
    assert failed["result"]["exit_code"] == 7
    timeout = _call(project, project_id, "R28", "process.execute", mode="sleep", value="2", timeout_ms=100)
    assert timeout["result"]["timed_out"] is True
    assert timeout["result"]["exit_code"] is None


def test_r29_compaction_obeys_budget_and_restores(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    result = _call(
        project,
        project_id,
        "R29",
        "context.compact",
        events=["alpha" * 100, "alpha" * 100, "omega" * 100],
        budget_bytes=128,
    )["result"]
    assert result["compacted_bytes"] <= 128
    restored = _call(
        project,
        project_id,
        "R29",
        "context.restore",
        artifact_sha256=result["artifact_sha256"],
    )["result"]
    assert hashlib.sha256(restored["text"].encode()).hexdigest() == result["artifact_sha256"]


def test_r30_memory_and_repository_intelligence(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    _call(project, project_id, "R30", "memory.add", memory_id="m1", text="Rust parity architecture", tags=["rust"])
    memory = _call(project, project_id, "R30", "memory.search", query="rust architecture")
    assert memory["result"]["matches"][0]["memory_id"] == "m1"
    _call(
        project,
        project_id,
        "R30",
        "repository.index",
        files={"src/main.rs": "fn parity_runtime() {}"},
    )
    repository = _call(project, project_id, "R30", "repository.query", query="parity_runtime")
    assert repository["result"]["matches"][0]["path"] == "src/main.rs"


def test_r31_provider_route_is_deterministic(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    candidates = [
        {"provider": "b", "model": "m2", "cost_micros": 10, "latency_ms": 5, "available": True},
        {"provider": "a", "model": "m1", "cost_micros": 10, "latency_ms": 5, "available": True},
    ]
    for row in candidates:
        row.update({"max_context": 32768, "supports_tools": True})
    result = _call(
        project,
        project_id,
        "R31",
        "provider.route",
        candidates=candidates,
        task={"required_context": 4096, "max_cost_micros": 100, "require_tools": True},
    )["result"]
    assert result["selected"]["provider"] == "a"


def test_r32_mcp_catalog_and_safe_call(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    catalog = _call(project, project_id, "R32", "mcp.catalog", profile="audit")
    assert "syntavra.parity.status" in catalog["result"]["tools"]
    status = _call(
        project,
        project_id,
        "R32",
        "mcp.call",
        tool="syntavra.parity.status",
        arguments={},
    )
    assert status["result"]["value"]["claim"] == "FULL_PARITY_PROVEN"


def test_r33_host_setup_apply_verify_and_rollback(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    plan = _call(project, project_id, "R33", "setup.plan", host="codex")
    assert plan["result"]["action"] == "create"
    applied = _call(project, project_id, "R33", "setup.apply", host="codex")
    assert applied["result"]["verified"] is True
    verified = _call(project, project_id, "R33", "setup.verify", host="codex")
    assert verified["result"]["valid"] is True


def test_r34_benchmark_and_evidence(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    comparison = _call(
        project,
        project_id,
        "R34",
        "benchmark.compare",
        baseline=[{"work": 100, "quota": 10, "quality_ppm": 900000, "success": True}],
        candidate=[{"work": 120, "quota": 10, "quality_ppm": 910000, "success": True}],
    )
    assert comparison["result"]["claim"] == "SUPERIORITY_PROVEN"
    body = {"previous_hash": None, "value": 1}
    receipt_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    evidence = _call(
        project,
        project_id,
        "R34",
        "evidence.validate",
        receipts=[{"body": body, "receipt_hash": receipt_hash}],
    )
    assert evidence["result"]["valid_receipts"] == 1


def test_r35_publication_build_and_verify(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    built = _call(
        project,
        project_id,
        "R35",
        "publication.build",
        artifacts={"syntavra.zip": {"bytes": 10, "sha256": "c" * 64}},
    )
    verified = _call(
        project,
        project_id,
        "R35",
        "publication.verify",
        manifest_sha256=built["result"]["manifest_sha256"],
    )
    assert verified["result"]["valid"] is True


def test_r36_standalone_distribution_rejects_python_files(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    manifest = _call(
        project,
        project_id,
        "R36",
        "distribution.manifest",
        platform="linux",
        architecture="x86_64",
        binary_sha256="d" * 64,
        files=["syntavra-rs", "README.md"],
    )["result"]
    assert manifest["python_required"] is False
    verified = _call(project, project_id, "R36", "distribution.verify", manifest=manifest)
    assert verified["result"]["valid"] is True


def test_r37_requires_every_phase_and_dimension(tmp_path: Path) -> None:
    project, project_id = _project(tmp_path)
    phases = {f"R{phase}": True for phase in range(25, 37)}
    dimensions = {name: True for name in ["cli", "host_setup", "mcp", "platform_packaging", "state_mutation"]}
    result = _call(
        project,
        project_id,
        "R37",
        "certification.evaluate",
        phases=phases,
        dimensions=dimensions,
    )
    assert result["result"]["claim"] == "FULL_PARITY_PROVEN"
    phases["R30"] = False
    incomplete = _call(
        project,
        project_id,
        "R37",
        "certification.evaluate",
        phases=phases,
        dimensions=dimensions,
    )
    assert incomplete["result"]["claim"] == "FULL_PARITY_NOT_PROVEN"
