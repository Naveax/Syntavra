from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from syntavra_runtime.adapter_runtime import AdapterMaturity, AdapterPlatformRuntime
from syntavra_runtime.autonomous_agent import (
    AgentMode,
    AgentTask,
    AutonomousCodingAgent,
    CallablePatchProvider,
    PatchProposal,
)
from syntavra_runtime.execution_sandbox import SandboxBackend, SandboxPolicy
from syntavra_runtime.headless_runtime import HeadlessRuntime, JobState
from syntavra_runtime.interactive_console import InteractiveConsole, TokenPanel
from syntavra_runtime.platform import SyntavraPlatform, manifest
from syntavra_runtime.reliability_lab import ReliabilityLaboratory
from syntavra_runtime.sandbox_runtime import HardenedSandboxBroker
from syntavra_runtime.update_manager import DistributionManager, UpdateArtifact


class PortableTestBroker(HardenedSandboxBroker):
    """Deterministic process boundary used where native namespaces are runner-specific."""

    def backend(self, policy: SandboxPolicy) -> SandboxBackend:
        return SandboxBackend(
            name="test-process-boundary",
            platform=sys.platform,
            available=True,
            enforced=("cwd-boundary", "environment-filter", "timeout", "process-group"),
            unsupported=(),
            command_prefix=(),
            detail="test-only deterministic backend",
        )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "module.py").write_text(
        "def alpha(value: int) -> int:\n"
        "    return beta(value)\n\n"
        "def beta(value: int) -> int:\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    return project


def test_platform_identity_and_manifest_are_single_version(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = SyntavraPlatform(project, tmp_path / "state")
    status = runtime.status()
    product_manifest = manifest()

    assert status["product"] == "Syntavra"
    assert status["version"] == "0.0.1"
    assert status["channel"] == "pre-release"
    assert status["capabilities"]["bounded_autonomous_agent"] is True
    assert status["capabilities"]["probed_native_sandbox"] is True
    assert product_manifest["product"] == "Syntavra"
    assert product_manifest["version"] == "0.0.1"
    assert "runtime-evidence" in product_manifest["components"]
    assert "distribution-manager" in product_manifest["components"]
    assert product_manifest["external_claims"] == "NOT_PROVEN_WITHOUT_EXTERNAL_RECEIPTS"


def test_context_output_and_artifact_recovery_are_exact(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = SyntavraPlatform(project, tmp_path / "state")
    raw = ("progress line\n" * 3000) + "FAILED tests/test_example.py:20 assertion error\n"

    receipt = runtime.firewall.capture("pytest", raw, exit_code=1)
    assert receipt.exact_recovery is True
    assert receipt.visible_bytes < receipt.original_bytes
    assert runtime.artifacts.read(receipt.artifact_id).decode("utf-8") == raw
    assert "artifact://sha256:" in receipt.compact_view

    items = [
        {
            "item_id": "policy",
            "layer": "system",
            "kind": "text",
            "source": "policy",
            "content": "Never lose exact evidence.",
            "priority": 1.0,
            "stable": True,
        },
        {
            "item_id": "tool-output",
            "layer": "task",
            "kind": "test",
            "source": "pytest",
            "content": raw,
            "priority": 0.8,
        },
    ]
    first = runtime.context.compile(items, provider="generic", model="test", budget_tokens=2000)
    second = runtime.context.compile(items, provider="generic", model="test", budget_tokens=2000)
    assert first.deterministic is True
    assert first.pack_hash == second.pack_hash
    assert first.cache_prefix_hash == second.cache_prefix_hash
    assert first.artifacts


def test_semantic_graph_runtime_evidence_and_incremental_index(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = SyntavraPlatform(project, tmp_path / "state")

    first = runtime.graph.index_repository(project)
    second = runtime.graph.index_repository(project)
    results = runtime.graph.query("alpha", limit=10)
    assert first["changed_files"] == 1
    assert second["changed_files"] == 0
    assert second["unchanged_files"] == 1
    assert any(item["name"] == "alpha" for item in results)

    coverage = runtime.runtime_evidence.import_coverage(
        {"files": {"module.py": {"executed_lines": [1, 2], "missing_lines": [5]}}},
        test_id="test_alpha",
        repository_commit="abc123",
    )
    trace = runtime.runtime_evidence.import_trace(
        [{"source": "alpha", "target": "beta", "relation": "RUNTIME_CALL"}],
        repository_commit="abc123",
    )
    assert coverage["files"] == 1
    assert trace["spans"] == 1
    stats = runtime.runtime_evidence.stats()
    assert stats["nodes"] >= 4
    assert stats["edges"] >= 2
    services = runtime.language_services.status()
    assert services["declared"] >= 16
    assert services["claim_boundary"].startswith("declared support")


def test_session_memory_is_exact_multiview_and_stale_aware(tmp_path: Path) -> None:
    runtime = SyntavraPlatform(_project(tmp_path), tmp_path / "state")
    session = runtime.memory.open(metadata={"goal": "database migration"})
    session_id = session["session_id"]
    old = runtime.memory.append(
        session_id,
        "decision",
        {"decision": "use sqlite", "stale": True, "importance": 1.0},
    )
    new = runtime.memory.append(
        session_id,
        "decision",
        {"decision": "use postgres", "pinned": True, "importance": 1.0},
    )
    runtime.memory.append(session_id, "test", {"test": "migration suite passed"})

    compacted = runtime.memory.compact(session_id)
    retrieved = runtime.memory.retrieve(session_id, "database decision", limit=5)
    checkpoint = runtime.memory.checkpoint(session_id, "ready")
    restored = runtime.memory.restore(checkpoint["checkpoint_id"])

    assert compacted["exact_history_preserved"] is True
    assert len(compacted["summaries"]) == 10
    assert runtime.memory.verify(session_id)["ok"] is True
    assert restored["exact_recovery"] is True
    event_results = [item for item in retrieved["results"] if item["type"] == "event"]
    assert event_results
    assert event_results[0]["event_hash"] == new["event_hash"]
    assert event_results[-1]["event_hash"] == old["event_hash"]

    forked = runtime.memory.fork(session_id, label="alternate")
    child_id = forked["child"]["session_id"]
    runtime.memory.append(child_id, "handoff", {"agent": "reviewer", "pinned": True})
    merged = runtime.memory.merge((session_id, child_id), label="reviewed")
    assert set(merged["parents"]) == {session_id, child_id}
    assert runtime.memory.verify(merged["merged"]["session_id"])["ok"] is True


def test_capability_security_is_bound_and_replay_protected(tmp_path: Path) -> None:
    runtime = SyntavraPlatform(_project(tmp_path), tmp_path / "state")
    arguments = {"path": "module.py"}
    token = runtime.security.issue(
        session_id="session",
        tool="repo.write",
        arguments=arguments,
        resource="workspace:/module.py",
        permissions=("write",),
        single_use=True,
    )
    first = runtime.security.verify(
        token,
        tool="repo.write",
        arguments=arguments,
        resource="workspace:/module.py",
    )
    replay = runtime.security.verify(
        token,
        tool="repo.write",
        arguments=arguments,
        resource="workspace:/module.py",
    )
    mismatch = runtime.security.verify(
        token,
        tool="repo.write",
        arguments={"path": "other.py"},
        resource="workspace:/module.py",
        consume=False,
    )
    unknown = runtime.security.decide("mystery.tool", {}, user_authorized=True, sandboxed=True)

    assert first["ok"] is True
    assert replay == {**replay, "ok": False}
    assert replay["reason"] == "already-consumed"
    assert mismatch["reason"] == "binding-mismatch"
    assert unknown.allowed is False
    assert unknown.reason == "unknown-tool-fail-closed"


def test_sandbox_filters_secrets_and_runs_bounded_process(tmp_path: Path) -> None:
    project = _project(tmp_path)
    broker = PortableTestBroker(tmp_path / "state")
    receipt = broker.run(
        (sys.executable, "-c", "print('sandbox-ok')"),
        policy=SandboxPolicy(workspace=project, timeout_seconds=10),
    )
    assert receipt.ok is True
    assert receipt.stdout.strip() == "sandbox-ok"
    assert all("TOKEN" not in key.upper() and "SECRET" not in key.upper() for key in receipt.environment_keys)
    with pytest.raises(PermissionError):
        broker.run(
            (sys.executable, "-c", "print('no')"),
            policy=SandboxPolicy(workspace=project),
            environment={"API_KEY": "must-not-pass"},
        )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for worktree mutation")
def test_autonomous_agent_mutates_verifies_and_keeps_exact_receipt(tmp_path: Path) -> None:
    project = tmp_path / "agent-project"
    project.mkdir()
    (project / "value.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=project, check=True)
    subprocess.run(["git", "add", "value.txt"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=project, check=True, capture_output=True)

    patch = """diff --git a/value.txt b/value.txt
--- a/value.txt
+++ b/value.txt
@@ -1 +1 @@
-old
+new
"""
    provider = CallablePatchProvider(lambda task, context, failure: PatchProposal(patch, rationale="replace value", estimated_tokens=20, estimated_cost=0.001))
    agent = AutonomousCodingAgent(
        project,
        tmp_path / "agent-state",
        sandbox=PortableTestBroker(tmp_path / "agent-state"),
    )
    verifier = (
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('value.txt').read_text(encoding='utf-8') == 'new\\n'",
    )
    receipt = agent.execute(
        AgentTask(
            instruction="replace old with new",
            verifier=verifier,
            mode=AgentMode.SAFE_AUTONOMOUS,
            max_attempts=2,
            retain_workspace=True,
        ),
        provider,
        authorized=True,
    )

    assert receipt.ok is True
    assert receipt.stop_reason == "verifier passed"
    assert len(receipt.attempts) == 1
    assert receipt.attempts[0].verifier is not None
    assert receipt.attempts[0].verifier.ok is True
    assert Path(receipt.workspace, "value.txt").read_text(encoding="utf-8") == "new\n"
    assert Path(tmp_path / "agent-state" / "agent-receipts" / f"{receipt.run_id.split(':', 1)[1]}.json").is_file()


def test_headless_queue_bundle_and_resume_surface(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runtime = HeadlessRuntime(
        tmp_path / "headless.sqlite3",
        tmp_path / "headless-state",
        broker=PortableTestBroker(tmp_path / "headless-state"),
    )
    job = runtime.submit(
        (sys.executable, "-c", "print('headless-ok')"),
        workspace=project,
        metadata={"source": "test"},
    )
    completed = runtime.run_once("worker-1")
    assert completed is not None
    assert completed.job_id == job.job_id
    assert completed.state == JobState.COMPLETED
    assert completed.result["execution"]["stdout"].strip() == "headless-ok"
    assert runtime.events(job.job_id)[0]["event_type"] == "submitted"

    bundle = tmp_path / "job.bundle.json"
    exported = runtime.export_bundle(job.job_id, bundle)
    imported = runtime.import_bundle(bundle, workspace_override=project)
    assert exported["ok"] is True
    assert imported.state == JobState.QUEUED
    assert imported.metadata["imported_from"] == str(bundle)


def test_adapter_console_reliability_and_atomic_update(tmp_path: Path) -> None:
    project = _project(tmp_path)
    state = tmp_path / "state"
    adapter = AdapterPlatformRuntime(project, state, home=tmp_path / "home")
    configured = adapter.configure_json(
        "github-copilot-vscode",
        ".vscode/mcp.json",
        {"servers": {"syntavra": {"command": "syntavra", "args": ["run", "platform-status"]}}},
        apply=True,
    )
    assert configured.ok is True
    assert configured.maturity == AdapterMaturity.CONFIGURED
    assert (project / ".vscode" / "mcp.json").is_file()

    console = InteractiveConsole()
    snapshot = console.snapshot(
        task_state="verifying",
        plan=("index", "patch", "test"),
        tokens=TokenPanel(raw_context_tokens=1000, compiled_context_tokens=200, original_output_bytes=10000, visible_output_bytes=500),
        sandbox={"backend": "test"},
        adapters={"configured": 1},
        claim_boundary=("EXTERNAL_SUPERIORITY_NOT_PROVEN",),
    )
    rendered = console.render(snapshot, width=100)
    assert "saved 800" in rendered
    assert "EXTERNAL_SUPERIORITY_NOT_PROVEN" in rendered
    dashboard = console.write_dashboard_payload(snapshot, tmp_path / "dashboard.json")
    assert dashboard["ok"] is True

    platform = SyntavraPlatform(project, state / "platform")
    report = ReliabilityLaboratory(state / "reliability", seed=7).campaign(
        artifact_store=platform.artifacts,
        capability_security=platform.security,
        parser_cases=100,
    )
    assert report.ok is True

    install_root = tmp_path / "install"
    manager = DistributionManager(install_root, state / "distribution")
    first_source = tmp_path / "syntavra-first"
    first_source.write_bytes(b"first-binary")
    first = UpdateArtifact("linux", "x64", first_source.name, hashlib.sha256(first_source.read_bytes()).hexdigest(), first_source.stat().st_size)
    installed = manager.install(first_source, first)
    assert installed.ok is True
    assert (install_root / "syntavra").read_bytes() == b"first-binary"

    second_source = tmp_path / "syntavra-second"
    second_source.write_bytes(b"second-binary")
    second = UpdateArtifact("linux", "x64", second_source.name, hashlib.sha256(second_source.read_bytes()).hexdigest(), second_source.stat().st_size)
    rolled_back = manager.install(second_source, second, health_check=lambda _: {"ok": False, "reason": "synthetic failure"})
    assert rolled_back.status == "rolled-back"
    assert rolled_back.rollback_performed is True
    assert (install_root / "syntavra").read_bytes() == b"first-binary"
