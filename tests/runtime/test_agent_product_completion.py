from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

from syntavra_runtime.agent_runtime import (
    AgentDeliveryManager,
    AgentDeliveryMode,
    AgentEventJournal,
    GatewayPatchProvider,
    StructuredEditCompiler,
)
from syntavra_runtime.autonomous_agent import AgentMode, AgentTask
from syntavra_runtime.model_gateway import SequenceModelGateway
from syntavra_runtime.project_model import ProjectModel
from syntavra_runtime.repository_query import RepositoryQueryEngine


def test_structured_edit_compiler_is_exact_and_git_apply_compatible(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    compiler = StructuredEditCompiler()
    patch = compiler.compile(
        project,
        [
            {"path": "module.py", "operation": "replace", "old": "VALUE = 1", "new": "VALUE = 2", "count": 1},
            {"path": "new.py", "operation": "create", "content": "CREATED = True\n"},
        ],
    )
    checked = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=project,
        input=patch,
        text=True,
        capture_output=True,
        check=False,
    )
    assert "\r" not in patch
    assert checked.returncode == 0, checked.stderr


def test_repository_query_connection_is_closed(tmp_path: Path) -> None:
    database = tmp_path / "graph.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE nodes (
                node_id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL,
                name TEXT NOT NULL, qualified_name TEXT NOT NULL, start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL, language TEXT NOT NULL, evidence_ref TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE edges (
                source TEXT NOT NULL, target TEXT NOT NULL, edge_type TEXT NOT NULL,
                confidence REAL NOT NULL, evidence_ref TEXT NOT NULL, metadata_json TEXT NOT NULL
            );
            """
        )
    engine = RepositoryQueryEngine(database)
    with engine._connect() as connection:
        connection.execute("SELECT 1").fetchone()
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("repository query connection remained open")


def test_gateway_structured_edit_and_event_stream(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "tests").mkdir()

    class Graph:
        def query(self, query: str, *, limit: int = 20):
            return [{"node_id": "n1", "name": "VALUE", "path": "module.py", "query": query, "limit": limit}]

        def impact(self, node_id: str, *, max_depth: int = 6):
            return {"root": node_id, "impacted": [], "max_depth": max_depth}

    events = []
    journal = AgentEventJournal(events.append)
    gateway = SequenceModelGateway(
        [
            {"action": "search", "query": "VALUE"},
            {"action": "inspect", "paths": ["module.py"]},
            {
                "action": "edit",
                "rationale": "update value",
                "edits": [
                    {"path": "module.py", "operation": "replace", "old": "VALUE = 1", "new": "VALUE = 2", "count": 1}
                ],
            },
        ]
    )
    model = ProjectModel(project)
    provider = GatewayPatchProvider(gateway, project=project, graph=Graph(), project_model=model, journal=journal)
    proposal = provider.propose(
        AgentTask("change VALUE", model.primary_verifier().argv, mode=AgentMode.SAFE_AUTONOMOUS),
        {
            "workspace": str(project),
            "attempt": 1,
            "semantic_results": Graph().query("VALUE"),
            "current_diff": "",
            "changed_files": [],
        },
        None,
    )
    assert "+VALUE = 2" in proposal.patch
    assert [item["action"] for item in provider.trace] == ["search", "inspect", "edit"]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert any(event.event_type == "patch-proposed" for event in events)


def test_delivery_apply_requires_authorization_and_uses_verified_diff(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    (project / "value.txt").write_text("old\n", encoding="utf-8")
    run = SimpleNamespace(
        workspace=str(project),
        ok=True,
        changed_files=("value.txt",),
        final_diff=(
            "diff --git a/value.txt b/value.txt\n"
            "--- a/value.txt\n"
            "+++ b/value.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
        run_id="agent:test",
        task=SimpleNamespace(instruction="replace value"),
    )
    manager = AgentDeliveryManager(project)
    denied = manager.deliver(run, mode=AgentDeliveryMode.APPLY, authorized=False)
    assert denied.ok is False
    applied = manager.deliver(run, mode=AgentDeliveryMode.APPLY, authorized=True)
    assert applied.ok is True
    assert (project / "value.txt").read_text(encoding="utf-8") == "new\n"
