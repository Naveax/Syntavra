from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from syntavra_runtime.agent_runtime import GatewayPatchProvider
from syntavra_runtime.artifacts import ArtifactStore
from syntavra_runtime.canonical_graph import CanonicalRepositoryGraph
from syntavra_runtime.data_router import DataRoutePolicy, DataRouter
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.model_gateway import SequenceModelGateway
from syntavra_runtime.process_broker import ProcessBroker
from syntavra_runtime.project_model import ProjectModel
from syntavra_runtime.terminal_engine import TerminalOutputEngine
from syntavra_runtime.autonomous_agent import AgentMode, AgentTask


def test_canonical_graph_uses_indexed_query_and_tree_sitter(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.ts").write_text(
        "export function greet(name: string): string { return format(name); }\n"
        "function format(value: string): string { return value.trim(); }\n",
        encoding="utf-8",
    )
    graph = CanonicalRepositoryGraph(tmp_path / "graph.sqlite3")
    result = graph.index_repository(project)
    rows = graph.query("greet", limit=10)

    assert result["canonical_graph"] is True
    assert result["repository_query"]["backend"] in {"sqlite-fts5", "sqlite-like"}
    assert rows
    assert rows[0]["name"] == "greet"
    assert rows[0]["query_backend"] in {"sqlite-fts5", "sqlite-like"}
    assert graph.language_status(project)["tree_sitter"]["installed"] is True


def test_terminal_engine_never_worse_and_exact_stream_recovery(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    engine = TerminalOutputEngine(store)

    small = engine.capture("echo", "ok\n", command="echo ok")
    assert small.compact_view == "ok\n" or small.compact_view == "ok"
    assert small.visible_bytes <= small.original_bytes
    assert store.read(small.artifact_id) == b"ok\n"

    raw = ("progress\n" * 5000 + "FAILED tests/test_example.py:20 assertion error\n").encode()
    session = engine.open(tool="pytest", command="pytest -q", exit_code=1)
    for offset in range(0, len(raw), 997):
        session.feed(raw[offset : offset + 997])
    receipt = session.finalize()

    assert receipt.exact_recovery is True
    assert receipt.visible_bytes < receipt.original_bytes
    assert "FAILED tests/test_example.py:20" in receipt.compact_view
    assert store.read(receipt.artifact_id) == raw


def test_streaming_table_is_deterministic_and_exact(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path / "evidence", project_id="project")
    router = DataRouter(evidence)

    def rows():
        for index in range(2000):
            yield {"id": index, "status": "error" if index % 317 == 0 else "ok", "value": index * 3}

    policy = DataRoutePolicy(budget_bytes=4096, max_rows=8, reservoir_size=32)
    first = router.route_rows(rows(), query="errors", policy=policy)
    second = router.route_rows(rows(), query="errors", policy=policy)

    assert first.exact_hash == second.exact_hash
    assert first.visible == second.visible
    assert first.original_bytes > first.visible_bytes
    assert first.exact_handle


def test_process_broker_custom_environment_preserves_parent_environment(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path / "evidence", project_id="project")
    broker = ProcessBroker(tmp_path / "broker", evidence, heartbeat_interval=0.05)
    result = broker.run(
        (
            sys.executable,
            "-c",
            "import os; print(bool(os.environ.get('PATH')), os.environ.get('SYN_TEST'))",
        ),
        cwd=tmp_path,
        timeout=20,
        env={"SYN_TEST": "1"},
    )
    assert result.exit_code == 0
    exact = evidence.get(result.evidence_handle).decode("utf-8", errors="replace")
    assert "True 1" in exact


def test_project_model_discovers_verifier_and_gateway_tool_loop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests").mkdir()
    model = ProjectModel(project)
    verifier = model.primary_verifier()
    assert verifier.argv[1:3] == ("-m", "pytest")

    class Graph:
        def query(self, query: str, *, limit: int = 20):
            return [{"name": "VALUE", "path": "module.py", "query": query, "limit": limit}]

    gateway = SequenceModelGateway(
        [
            {"action": "inspect", "paths": ["module.py"]},
            {
                "action": "patch",
                "rationale": "update value",
                "patch": "diff --git a/module.py b/module.py\n--- a/module.py\n+++ b/module.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n",
            },
        ]
    )
    provider = GatewayPatchProvider(gateway, project=project, graph=Graph(), project_model=model)
    proposal = provider.propose(
        AgentTask("change VALUE", verifier.argv, mode=AgentMode.SAFE_AUTONOMOUS),
        {"semantic_results": Graph().query("VALUE"), "current_diff": "", "changed_files": []},
        None,
    )
    assert "+VALUE = 2" in proposal.patch
    assert [item["action"] for item in provider.trace] == ["inspect", "patch"]
