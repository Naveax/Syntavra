from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .autonomous_agent import AgentMode, AgentTask, PatchProposal
from .execution_sandbox import SandboxPolicy
from .interactive_console import TokenPanel
from .platform import (
    AdapterRegistry,
    ContextIRItem,
    SecretlessProviderGateway,
    SyntavraPlatform,
    manifest,
)
from .update_manager import UpdateArtifact


CANONICAL_ACTIONS = {
    "platform-status", "platform-doctor", "platform-manifest",
    "context-compile", "output-capture",
    "artifact-put", "artifact-query", "artifact-verify", "artifact-stats",
    "graph-index", "graph-query", "graph-impact", "language",
    "semantic-services", "semantic-import", "evidence-stats", "evidence-neighbors",
    "memory-open", "memory-append", "memory-compact", "memory-retrieve",
    "memory-checkpoint", "memory-fork", "memory-merge", "memory-restore", "memory-verify",
    "capability-decide", "capability-issue", "capability-verify",
    "sandbox-status", "sandbox-run", "gateway-plan",
    "adapters", "adapter-conformance", "adapter-configure", "adapter-certify",
    "agent-plan", "agent-execute",
    "headless-submit", "headless-run", "headless-status", "headless-events",
    "headless-cancel", "headless-resume", "headless-export", "headless-import",
    "console", "reliability-run", "update-install", "update-rollback",
}
COMPATIBILITY_ACTIONS = {"competitive-status", "competitive-doctor", "competitive-manifest"}
ACTIONS = CANONICAL_ACTIONS | COMPATIBILITY_ACTIONS


def _load(value: str) -> Any:
    path = Path(value)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _input(value: str) -> str:
    path = Path(value)
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else value


def _json_object(value: str, label: str) -> dict[str, Any]:
    item = _load(value)
    if not isinstance(item, dict):
        raise ValueError(f"{label} must be a JSON object")
    return item


def _argv(value: str, label: str) -> list[str]:
    item = _load(value)
    if not isinstance(item, list) or not item or not all(isinstance(part, str) and "\x00" not in part for part in item):
        raise ValueError(f"{label} must be a non-empty JSON argv list")
    return item


def _project_path(project: Path, value: str) -> Path:
    candidate = Path(value)
    return (candidate if candidate.is_absolute() else project / candidate).resolve(strict=False)


def add_run_subcommands(run_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for name in ("platform-status", "platform-doctor", "platform-manifest"):
        run_sub.add_parser(name)
    for name in sorted(COMPATIBILITY_ACTIONS):
        run_sub.add_parser(name, help=argparse.SUPPRESS)

    context = run_sub.add_parser("context-compile")
    context.add_argument("items", help="JSON list or path")
    context.add_argument("--provider", default="generic")
    context.add_argument("--model", default="unknown")
    context.add_argument("--budget", type=int, default=32000)
    context.add_argument("--previous", default="{}")

    output = run_sub.add_parser("output-capture")
    output.add_argument("tool")
    output.add_argument("input", help="text file path or literal text")
    output.add_argument("--exit-code", type=int, default=0)
    output.add_argument("--duration-ms", type=float, default=0.0)
    output.add_argument("--media-type", default="text/plain")

    artifact_put = run_sub.add_parser("artifact-put")
    artifact_put.add_argument("input")
    artifact_put.add_argument("--kind", default="generic")
    artifact_put.add_argument("--media-type", default="text/plain")
    artifact_query = run_sub.add_parser("artifact-query")
    artifact_query.add_argument("artifact_id")
    artifact_query.add_argument("--mode", choices=("head", "tail", "errors", "failures", "regex", "json"), default="head")
    artifact_query.add_argument("--expression", default="")
    artifact_query.add_argument("--limit", type=int, default=80)
    artifact_verify = run_sub.add_parser("artifact-verify")
    artifact_verify.add_argument("artifact_id", nargs="?")
    run_sub.add_parser("artifact-stats")

    graph_index = run_sub.add_parser("graph-index")
    graph_index.add_argument("--max-file-bytes", type=int, default=2_000_000)
    graph_query = run_sub.add_parser("graph-query")
    graph_query.add_argument("query")
    graph_query.add_argument("--limit", type=int, default=20)
    graph_impact = run_sub.add_parser("graph-impact")
    graph_impact.add_argument("node_id")
    graph_impact.add_argument("--max-depth", type=int, default=6)

    language = run_sub.add_parser("language")
    language_sub = language.add_subparsers(dest="language_action", required=True)
    language_sub.add_parser("inventory")
    language_detect = language_sub.add_parser("detect")
    language_detect.add_argument("path")
    language_index = language_sub.add_parser("index")
    language_index.add_argument("--max-file-bytes", type=int, default=2_000_000)
    language_query = language_sub.add_parser("query")
    language_query.add_argument("query")
    language_query.add_argument("--limit", type=int, default=20)
    language_import = language_sub.add_parser("import-index")
    language_import.add_argument("path")
    language_import.add_argument("--format", choices=("auto", "lsif", "scip-json"), default="auto")
    language_import.add_argument("--repository-commit")
    language_import.add_argument("--current-commit")
    language_import.add_argument("--allow-stale", action="store_true")
    language_import.add_argument("--source-name")
    language_remove = language_sub.add_parser("remove-index")
    language_remove.add_argument("source_key")
    language_sub.add_parser("doctor")

    run_sub.add_parser("semantic-services")
    semantic_import = run_sub.add_parser("semantic-import")
    semantic_import.add_argument("format", choices=("lsif", "scip-json", "coverage", "trace"))
    semantic_import.add_argument("path")
    semantic_import.add_argument("--repository-commit")
    semantic_import.add_argument("--allow-stale", action="store_true")
    semantic_import.add_argument("--test-id", default="coverage-suite")
    run_sub.add_parser("evidence-stats")
    evidence_neighbors = run_sub.add_parser("evidence-neighbors")
    evidence_neighbors.add_argument("node_id")
    evidence_neighbors.add_argument("--relation")
    evidence_neighbors.add_argument("--reverse", action="store_true")

    memory_open = run_sub.add_parser("memory-open")
    memory_open.add_argument("--session-id")
    memory_open.add_argument("--parent", action="append", default=[])
    memory_open.add_argument("--metadata", default="{}")
    memory_append = run_sub.add_parser("memory-append")
    memory_append.add_argument("session_id")
    memory_append.add_argument("event_type")
    memory_append.add_argument("payload")
    memory_compact = run_sub.add_parser("memory-compact")
    memory_compact.add_argument("session_id")
    memory_compact.add_argument("--view", action="append", default=[])
    memory_retrieve = run_sub.add_parser("memory-retrieve")
    memory_retrieve.add_argument("session_id")
    memory_retrieve.add_argument("query")
    memory_retrieve.add_argument("--limit", type=int, default=12)
    memory_checkpoint = run_sub.add_parser("memory-checkpoint")
    memory_checkpoint.add_argument("session_id")
    memory_checkpoint.add_argument("--label", default="")
    memory_fork = run_sub.add_parser("memory-fork")
    memory_fork.add_argument("session_id")
    memory_fork.add_argument("--label", default="")
    memory_merge = run_sub.add_parser("memory-merge")
    memory_merge.add_argument("session_id", nargs="+")
    memory_merge.add_argument("--label", default="")
    memory_restore = run_sub.add_parser("memory-restore")
    memory_restore.add_argument("checkpoint_id")
    memory_verify = run_sub.add_parser("memory-verify")
    memory_verify.add_argument("session_id")

    capability_decide = run_sub.add_parser("capability-decide")
    capability_decide.add_argument("tool")
    capability_decide.add_argument("arguments")
    capability_decide.add_argument("--resource", default="workspace:/")
    capability_decide.add_argument("--sandboxed", action="store_true")
    capability_decide.add_argument("--user-authorized", action="store_true")
    capability_decide.add_argument("--network-host", action="append", default=[])
    capability_issue = run_sub.add_parser("capability-issue")
    capability_issue.add_argument("session_id")
    capability_issue.add_argument("tool")
    capability_issue.add_argument("arguments")
    capability_issue.add_argument("--resource", default="workspace:/")
    capability_issue.add_argument("--permission", action="append", default=[])
    capability_issue.add_argument("--ttl", type=int, default=300)
    capability_issue.add_argument("--reusable", action="store_true")
    capability_verify = run_sub.add_parser("capability-verify")
    capability_verify.add_argument("token")
    capability_verify.add_argument("tool")
    capability_verify.add_argument("arguments")
    capability_verify.add_argument("--resource", default="workspace:/")
    capability_verify.add_argument("--no-consume", action="store_true")

    run_sub.add_parser("sandbox-status")
    sandbox_run = run_sub.add_parser("sandbox-run")
    sandbox_run.add_argument("command", help="JSON argv or path")
    sandbox_run.add_argument("--cwd")
    sandbox_run.add_argument("--timeout", type=float, default=300.0)
    sandbox_run.add_argument("--strict-native", action="store_true")
    sandbox_run.add_argument("--network-host", action="append", default=[])
    sandbox_run.add_argument("--writable-path", action="append", default=[])
    sandbox_run.add_argument("--memory-bytes", type=int)
    sandbox_run.add_argument("--cpu-seconds", type=int)
    sandbox_run.add_argument("--no-child-processes", action="store_true")

    gateway = run_sub.add_parser("gateway-plan")
    gateway.add_argument("provider")
    gateway.add_argument("--upstream", default="")
    gateway.add_argument("--credential-source", default="os-broker")

    adapters = run_sub.add_parser("adapters")
    adapters.add_argument("--detect", action="store_true")
    adapter_conformance = run_sub.add_parser("adapter-conformance")
    adapter_conformance.add_argument("adapter_id")
    adapter_configure = run_sub.add_parser("adapter-configure")
    adapter_configure.add_argument("adapter_id")
    adapter_configure.add_argument("path")
    adapter_configure.add_argument("desired")
    adapter_configure.add_argument("--apply", action="store_true")
    adapter_certify = run_sub.add_parser("adapter-certify")
    adapter_certify.add_argument("adapter_id")
    adapter_certify.add_argument("receipt")

    agent = run_sub.add_parser("agent-plan")
    agent.add_argument("task")
    agent.add_argument("--session-id")
    agent.add_argument("--max-symbols", type=int, default=12)
    agent.add_argument("--index", action="store_true")
    execute = run_sub.add_parser("agent-execute")
    execute.add_argument("task")
    execute.add_argument("patches", help="JSON list of unified diff strings or proposal objects")
    execute.add_argument("verifier", help="JSON argv")
    execute.add_argument("--mode", choices=tuple(item.value for item in AgentMode), default=AgentMode.REVIEW_REQUIRED.value)
    execute.add_argument("--attempts", type=int, default=3)
    execute.add_argument("--timeout", type=float, default=900.0)
    execute.add_argument("--token-budget", type=int)
    execute.add_argument("--cost-budget", type=float)
    execute.add_argument("--authorized", action="store_true")
    execute.add_argument("--session-id")
    execute.add_argument("--retain-workspace", action="store_true")

    submit = run_sub.add_parser("headless-submit")
    submit.add_argument("command")
    submit.add_argument("--workspace", default=".")
    submit.add_argument("--workspace-type", default="local-worktree")
    submit.add_argument("--policy", default="{}")
    submit.add_argument("--metadata", default="{}")
    runner = run_sub.add_parser("headless-run")
    runner.add_argument("--worker", default="local")
    status = run_sub.add_parser("headless-status")
    status.add_argument("job_id", nargs="?")
    events = run_sub.add_parser("headless-events")
    events.add_argument("job_id")
    cancel = run_sub.add_parser("headless-cancel")
    cancel.add_argument("job_id")
    cancel.add_argument("--reason", default="operator cancellation")
    resume = run_sub.add_parser("headless-resume")
    resume.add_argument("job_id")
    export_job = run_sub.add_parser("headless-export")
    export_job.add_argument("job_id")
    export_job.add_argument("destination")
    import_job = run_sub.add_parser("headless-import")
    import_job.add_argument("source")
    import_job.add_argument("--workspace")

    console = run_sub.add_parser("console")
    console.add_argument("snapshot", help="JSON object or path")
    console.add_argument("--json", action="store_true")
    console.add_argument("--output")
    reliability = run_sub.add_parser("reliability-run")
    reliability.add_argument("--cases", type=int, default=1000)
    reliability.add_argument("--seed", type=int, default=1)

    update_install = run_sub.add_parser("update-install")
    update_install.add_argument("source")
    update_install.add_argument("artifact")
    update_install.add_argument("--name", default="syntavra")
    update_rollback = run_sub.add_parser("update-rollback")
    update_rollback.add_argument("--name", default="syntavra")
    update_rollback.add_argument("--sha256", default="")


class _SequenceProvider:
    def __init__(self, rows: list[Any]):
        self.rows = rows
        self.index = 0

    def propose(self, task: AgentTask, context: Mapping[str, Any], previous_failure: Mapping[str, Any] | None) -> PatchProposal:
        if self.index >= len(self.rows):
            return PatchProposal("")
        row = self.rows[self.index]
        self.index += 1
        return PatchProposal(**row) if isinstance(row, dict) else PatchProposal(str(row))


def handle(args: argparse.Namespace, *, project: Path, state: Path) -> dict[str, Any] | None:
    if getattr(args, "action", "") not in ACTIONS:
        return None
    runtime = SyntavraPlatform(project, state / "unified")
    action = args.action
    if action in {"platform-status", "competitive-status"}:
        return runtime.status()
    if action in {"platform-doctor", "competitive-doctor"}:
        return runtime.doctor()
    if action in {"platform-manifest", "competitive-manifest"}:
        return manifest()
    if action == "context-compile":
        rows = _load(args.items)
        if not isinstance(rows, list):
            raise ValueError("context items must be a JSON list")
        items = [ContextIRItem(**row) for row in rows]
        previous = _json_object(args.previous, "previous context")
        return asdict(runtime.context.compile(items, provider=args.provider, model=args.model, budget_tokens=args.budget, previous=previous))
    if action == "output-capture":
        return asdict(runtime.firewall.capture(args.tool, _input(args.input), exit_code=args.exit_code, duration_ms=args.duration_ms, media_type=args.media_type))
    if action == "artifact-put":
        return asdict(runtime.artifacts.put(_input(args.input), media_type=args.media_type, kind=args.kind))
    if action == "artifact-query":
        return runtime.artifacts.query(args.artifact_id, mode=args.mode, expression=args.expression, limit=args.limit)
    if action == "artifact-verify":
        return runtime.artifacts.verify(args.artifact_id)
    if action == "artifact-stats":
        return runtime.artifacts.stats()
    if action == "graph-index":
        return runtime.graph.index_repository(project, max_file_bytes=args.max_file_bytes)
    if action == "graph-query":
        return {"ok": True, "query": args.query, "results": runtime.graph.query(args.query, limit=args.limit)}
    if action == "graph-impact":
        return runtime.graph.impact(args.node_id, max_depth=args.max_depth)
    if action == "language":
        operation = args.language_action
        if operation in {"inventory", "doctor"}:
            return runtime.graph.language_status(project)
        if operation == "detect":
            source = _project_path(project, args.path).resolve(strict=True)
            try:
                source.relative_to(project.resolve(strict=True))
            except ValueError as error:
                raise PermissionError("language detection path escapes project") from error
            if not source.is_file():
                raise ValueError("language detection path must be a file")
            detection = runtime.graph.languages.detect(source, source.read_bytes())
            return {"ok": True, "path": source.relative_to(project).as_posix(), "detection": asdict(detection)}
        if operation == "index":
            return runtime.graph.index_repository(project, max_file_bytes=args.max_file_bytes)
        if operation == "query":
            return {"ok": True, "query": args.query, "results": runtime.graph.query(args.query, limit=args.limit)}
        if operation == "import-index":
            return runtime.graph.import_semantic_index(
                _project_path(project, args.path).resolve(strict=True),
                repository_root=project,
                format=args.format,
                repository_commit=args.repository_commit,
                current_commit=args.current_commit,
                allow_stale=args.allow_stale,
                source_name=args.source_name,
            )
        if operation == "remove-index":
            return runtime.graph.remove_semantic_index(args.source_key)
        raise RuntimeError(operation)
    if action == "semantic-services":
        return runtime.graph.language_status(project)
    if action == "semantic-import":
        source = _project_path(project, args.path)
        if args.format in {"lsif", "scip-json"}:
            return runtime.graph.import_semantic_index(
                source.resolve(strict=True),
                repository_root=project,
                format=args.format,
                repository_commit=args.repository_commit,
                allow_stale=args.allow_stale,
            )
        value = _load(str(source))
        if args.format == "coverage":
            return runtime.runtime_evidence.import_coverage(value, test_id=args.test_id, repository_commit=args.repository_commit)
        spans = value.get("spans", value) if isinstance(value, dict) else value
        if not isinstance(spans, list):
            raise ValueError("trace import requires a list or {'spans': [...]} object")
        return runtime.runtime_evidence.import_trace(spans, repository_commit=args.repository_commit)
    if action == "evidence-stats":
        return runtime.runtime_evidence.stats()
    if action == "evidence-neighbors":
        return {"ok": True, "neighbors": runtime.runtime_evidence.neighbors(args.node_id, relation=args.relation, reverse=args.reverse)}
    if action == "memory-open":
        return runtime.memory.open(args.session_id, parents=args.parent, metadata=_json_object(args.metadata, "metadata"))
    if action == "memory-append":
        return runtime.memory.append(args.session_id, args.event_type, _json_object(args.payload, "payload"))
    if action == "memory-compact":
        return runtime.memory.compact(args.session_id, views=args.view or None)
    if action == "memory-retrieve":
        return runtime.memory.retrieve(args.session_id, args.query, limit=args.limit)
    if action == "memory-checkpoint":
        return runtime.memory.checkpoint(args.session_id, args.label)
    if action == "memory-fork":
        return runtime.memory.fork(args.session_id, label=args.label)
    if action == "memory-merge":
        return runtime.memory.merge(args.session_id, label=args.label)
    if action == "memory-restore":
        return runtime.memory.restore(args.checkpoint_id)
    if action == "memory-verify":
        return runtime.memory.verify(args.session_id)
    if action == "capability-decide":
        return asdict(runtime.security.decide(args.tool, _load(args.arguments), resource=args.resource, sandboxed=args.sandboxed, user_authorized=args.user_authorized, network_allowlist=args.network_host))
    if action == "capability-issue":
        token = runtime.security.issue(session_id=args.session_id, tool=args.tool, arguments=_load(args.arguments), resource=args.resource, permissions=args.permission, ttl_seconds=args.ttl, single_use=not args.reusable)
        return {"ok": True, "token": token, "single_use": not args.reusable}
    if action == "capability-verify":
        return runtime.security.verify(args.token, tool=args.tool, arguments=_load(args.arguments), resource=args.resource, consume=not args.no_consume)
    if action == "sandbox-status":
        return runtime.sandbox.health(project)
    if action == "sandbox-run":
        command = _argv(args.command, "sandbox command")
        writable = tuple(_project_path(project, value) for value in args.writable_path) or (project,)
        receipt = runtime.sandbox.run(
            command,
            policy=SandboxPolicy(
                workspace=project,
                writable_paths=writable,
                timeout_seconds=args.timeout,
                strict_native=args.strict_native,
                network_hosts=tuple(args.network_host),
                memory_bytes=args.memory_bytes,
                cpu_seconds=args.cpu_seconds,
                allow_child_processes=not args.no_child_processes,
            ),
            cwd=_project_path(project, args.cwd).resolve(strict=True) if args.cwd else None,
        )
        return asdict(receipt) | {"ok": receipt.ok}
    if action == "gateway-plan":
        return SecretlessProviderGateway.plan(args.provider, upstream=args.upstream, credential_source=args.credential_source)
    if action == "adapters":
        return {"ok": True, "validation": AdapterRegistry.validate(), "adapters": AdapterRegistry.detect(project=project) if args.detect else AdapterRegistry.records()}
    if action == "adapter-conformance":
        return asdict(runtime.adapters.conformance(args.adapter_id))
    if action == "adapter-configure":
        return asdict(runtime.adapters.configure_json(args.adapter_id, args.path, _json_object(args.desired, "desired config"), apply=args.apply))
    if action == "adapter-certify":
        return asdict(runtime.adapters.certify(args.adapter_id, _json_object(args.receipt, "external receipt")))
    if action == "agent-plan":
        if args.index:
            runtime.graph.index_repository(project)
        return runtime.agent.plan(args.task, session_id=args.session_id, max_symbols=args.max_symbols)
    if action == "agent-execute":
        rows = _load(args.patches)
        if not isinstance(rows, list):
            raise ValueError("patches must be a JSON list")
        verifier = _argv(args.verifier, "verifier")
        receipt = runtime.autonomous_agent.execute(
            AgentTask(
                instruction=args.task,
                verifier=tuple(verifier),
                mode=AgentMode(args.mode),
                max_attempts=args.attempts,
                timeout_seconds=args.timeout,
                token_budget=args.token_budget,
                cost_budget=args.cost_budget,
                retain_workspace=args.retain_workspace,
            ),
            _SequenceProvider(rows),
            session_id=args.session_id,
            authorized=args.authorized,
        )
        return asdict(receipt) | {"ok": receipt.ok}
    if action == "headless-submit":
        command = _argv(args.command, "headless command")
        workspace = _project_path(project, args.workspace).resolve(strict=True)
        return asdict(runtime.headless.submit(command, workspace=workspace, workspace_type=args.workspace_type, policy=_json_object(args.policy, "policy"), metadata=_json_object(args.metadata, "metadata")))
    if action == "headless-run":
        job = runtime.headless.run_once(args.worker)
        return {"ok": True, "job": asdict(job) if job else None}
    if action == "headless-status":
        return ({"ok": True, "job": asdict(runtime.headless.get(args.job_id))} if args.job_id else runtime.headless.stats())
    if action == "headless-events":
        return {"ok": True, "events": runtime.headless.events(args.job_id)}
    if action == "headless-cancel":
        return {"ok": True, "job": asdict(runtime.headless.cancel(args.job_id, args.reason))}
    if action == "headless-resume":
        return {"ok": True, "job": asdict(runtime.headless.resume(args.job_id))}
    if action == "headless-export":
        return runtime.headless.export_bundle(args.job_id, _project_path(project, args.destination))
    if action == "headless-import":
        workspace = _project_path(project, args.workspace).resolve(strict=True) if args.workspace else None
        return {"ok": True, "job": asdict(runtime.headless.import_bundle(_project_path(project, args.source), workspace_override=workspace))}
    if action == "console":
        values = dict(_json_object(args.snapshot, "console snapshot"))
        token_values = values.pop("tokens", values.pop("token_panel", {}))
        snapshot = runtime.console.snapshot(tokens=TokenPanel(**token_values), **values)
        if args.output:
            return runtime.console.write_dashboard_payload(snapshot, _project_path(project, args.output))
        return {"ok": True, "format": "json" if args.json else "tui", "output": runtime.console.json(snapshot) if args.json else runtime.console.render(snapshot)}
    if action == "reliability-run":
        runtime.reliability.reseed(args.seed)
        report = runtime.reliability.campaign(artifact_store=runtime.artifacts, capability_security=runtime.security, parser_cases=max(0, args.cases))
        return asdict(report) | {"ok": report.ok}
    if action == "update-install":
        artifact = UpdateArtifact(**_json_object(args.artifact, "artifact"))
        receipt = runtime.distribution.install(_project_path(project, args.source), artifact, executable_name=args.name)
        return asdict(receipt) | {"ok": receipt.ok}
    if action == "update-rollback":
        return runtime.distribution.rollback(args.name, expected_previous_sha256=args.sha256)
    raise RuntimeError(action)
