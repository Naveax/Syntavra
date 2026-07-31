from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .adaptive_provider_router import AdaptiveProviderRouter
from .agent_config_auditor import AgentConfigAuditor
from .background_workers import BackgroundIntelligenceWorker
from .code_intelligence import CodeIntelligenceIndex
from .command_rewriter import CommandRewriteEngine
from .dashboard import LocalDashboard
from .memory_intelligence import MemoryIntelligenceStore
from .notifications import NotificationFeed
from .optimization_modes import ALIASES, MODES, OptimizationModeStore, SavingsLedger, render_statusline
from .prompt_cache_optimizer import PromptCacheOptimizer
from .provider_account_pool import ProviderAccountPool
from .repository_watcher import RepositoryWatcher
from .secret_redaction import SecretRedactor
from .subtask_router import AutomaticSubtaskDelegator
from .transcript_miner import TranscriptOpportunityMiner
from .wire_format import LosslessWireCodec
from .competitive_features import manifest as competitive_feature_manifest
from .infinite_context import CONTEXT_TIERS, UnboundedContextCoordinator
from .platform import SyntavraPlatform
from .platform_cli import add_run_subcommands as add_platform_run_subcommands, handle as handle_platform
from .integration_matrix import IntegrationMatrix
from .long_context_quality import LongContextQualityGate, LongContextReceipt, manifest as long_context_manifest
from .product_maturity import ProductMaturityGate, load_maturity_document
from .product_surface import (
    MCP_PROFILES,
    MeasuredBenchmarkGate,
    PlatformAdapterRegistry,
    ProductSurface,
    ReceiptValidator,
    SessionAnalyticsStore,
    ToolRoutingEnforcer,
    write_receipt_schema,
)
from .proxy_product import ProxyProductRegistry
from .public_proof import PublicProofGate
from .release_identity import VERSION, identity, validate_repository_identity
from .session_product import SessionContinuityController
from .paired_benchmark import CodingCorpusPlanner, PairedSchedule, SuperiorityGate, default_arms
from .semantic_structure import GraphEdge, GraphNode, SemanticGraph
from .signalbench import SignalBenchRunner, load_results
from .usage_receipt_ledger import UsageReceiptLedger
from .util import stable_project_id
from .zero_friction import ZeroFrictionManager


PRIMARY_COMMANDS = {"setup", "status", "run", "prove"}
COMPATIBILITY_COMMANDS = {
    "version", "install", "wrap", "doctor", "stats", "upgrade", "repair",
    "integrations", "context-stress", "signalbench", "signalbench2", "proof",
    "semantic-demo", "structural-v2",
}
PRE_RELEASE_COMMANDS = PRIMARY_COMMANDS | COMPATIBILITY_COMMANDS


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _load_json_argument(value: str) -> Any:
    path = Path(value)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _load_long_context_receipts(path: Path) -> list[LongContextReceipt]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("receipts", value) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError("long-context receipt file must contain a list or {'receipts': [...]} object")
    return [LongContextReceipt.from_mapping(item) for item in rows if isinstance(item, dict)]


def _session_controller(project: Path, state: Path) -> SessionContinuityController:
    return SessionContinuityController(
        state / "sessions.sqlite3",
        project_id=stable_project_id(project),
        analytics_path=state / "analytics" / "events.jsonl",
    )


def _add_proxy_service_options(parser: argparse.ArgumentParser, *, mutating: bool) -> None:
    parser.add_argument("provider")
    parser.add_argument("--upstream", default="")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8787)
    parser.add_argument("--cache-policy", choices=("off", "auto", "read", "read-write"), default="auto")
    parser.add_argument("--environment-file", default="")
    parser.add_argument("--platform", choices=("linux", "darwin", "windows"))
    parser.add_argument("--home")
    if mutating:
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--activate", action="store_true")


def _add_benchmark_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser], name: str, *, hidden: bool = False) -> None:
    benchmark = sub.add_parser(name, help=argparse.SUPPRESS if hidden else "plan or evaluate paired SignalBench runs")
    actions = benchmark.add_subparsers(dest="action", required=True)
    plan = actions.add_parser("plan")
    plan.add_argument("--repetitions", type=int, default=30)
    gate = actions.add_parser("gate")
    gate.add_argument("receipts")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syntavra",
        description="Syntavra 0.0.1 pre-release token and context optimization skill",
        epilog="Product workflow: setup -> status -> run -> prove",
    )
    parser.add_argument("--project", default=".")
    parser.add_argument("--state-root")
    sub = parser.add_subparsers(dest="command", required=True, metavar="{setup,status,run,prove}")

    setup = sub.add_parser("setup", help="install, configure or repair Syntavra")
    setup.add_argument("--apply", action="store_true")
    setup.add_argument("--all", action="store_true")
    setup.add_argument("--mcp-profile", choices=tuple(MCP_PROFILES), default="minimal")
    setup.add_argument("--repair", action="store_true", help="repair the existing installation")

    status = sub.add_parser("status", help="show health, usage, readiness and evidence gates")
    status.add_argument("--receipts")
    status.add_argument("--doctor", action="store_true", help="show installation and integration health only")
    status.add_argument("--savings", action="store_true", help="show token attribution and observed savings")
    status.add_argument("--profile", action="store_true", help="show the active MCP profile")
    status.add_argument("--memory", action="store_true", help="show session-memory continuity state")
    status.add_argument("--evidence", action="store_true", help="show receipt integrity and claim boundaries")

    run = sub.add_parser("run", help="execute an enforced platform operation")
    run_sub = run.add_subparsers(dest="action", required=True)
    run_sub.add_parser("manifest")

    route = run_sub.add_parser("route")
    route.add_argument("tool")
    route.add_argument("--profile", choices=tuple(MCP_PROFILES), default="minimal")
    route.add_argument("--sandboxed", action="store_true")
    route.add_argument("--no-exact-evidence", action="store_true")
    route.add_argument("--user-authorized", action="store_true")

    record = run_sub.add_parser("record")
    record.add_argument("event", help="JSON object or path to a JSON object")

    proxy_plan = run_sub.add_parser("proxy-plan")
    proxy_plan.add_argument("provider")
    proxy_plan.add_argument("--upstream", default="")

    proxy_service = run_sub.add_parser("proxy-service")
    proxy_service_sub = proxy_service.add_subparsers(dest="service_action", required=True)
    for service_action in ("plan", "install", "verify", "uninstall"):
        item = proxy_service_sub.add_parser(service_action)
        _add_proxy_service_options(item, mutating=service_action in {"install", "uninstall"})

    session_open = run_sub.add_parser("session-open")
    session_open.add_argument("--session-id")
    session_open.add_argument("--metadata", default="{}")
    session_append = run_sub.add_parser("session-append")
    session_append.add_argument("session_id")
    session_append.add_argument("event_type")
    session_append.add_argument("payload")
    session_compact = run_sub.add_parser("session-compact")
    session_compact.add_argument("session_id")
    session_compact.add_argument("--force", action="store_true")
    session_continuity = run_sub.add_parser("session-continuity")
    session_continuity.add_argument("session_id")
    session_continuity.add_argument("--token-budget", type=int, default=32_000)
    run_sub.add_parser("session-status")

    mode = run_sub.add_parser("mode")
    mode.add_argument("mode", nargs="?", choices=tuple((*MODES, *ALIASES)))
    mode.add_argument("--source", default="user")
    statusline = run_sub.add_parser("statusline")
    statusline.add_argument("--verbose", action="store_true")
    rewrite = run_sub.add_parser("rewrite")
    rewrite.add_argument("rewrite_argv", nargs=argparse.REMAINDER)
    transcript = run_sub.add_parser("transcript-mine")
    transcript.add_argument("source")
    watch = run_sub.add_parser("watch")
    watch.add_argument("--iterations", type=int, default=1)
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--no-index", action="store_true")
    dashboard = run_sub.add_parser("dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8788)
    dashboard.add_argument("--open", action="store_true")
    dashboard.add_argument("--snapshot", action="store_true")
    run_sub.add_parser("audit-config")
    worker = run_sub.add_parser("worker")
    worker.add_argument("worker_action", choices=("run","start","status"))
    worker.add_argument("--iterations", type=int, default=1)
    worker.add_argument("--interval", type=float, default=2.0)
    cache_plan = run_sub.add_parser("cache-plan")
    cache_plan.add_argument("source", help="JSON message list or path")
    cache_plan.add_argument("--provider", required=True)
    cache_plan.add_argument("--model", required=True)
    cache_plan.add_argument("--ttl", type=int)
    cache_plan.add_argument("--no-reorder", action="store_true")
    run_sub.add_parser("cache-health")
    amortize = run_sub.add_parser("cache-amortize")
    amortize.add_argument("--write", type=int, required=True)
    amortize.add_argument("--read", type=int, required=True)
    amortize.add_argument("--uncached", type=int, required=True)
    amortize.add_argument("--requests", type=int, required=True)
    memory_add = run_sub.add_parser("memory-add")
    memory_add.add_argument("text")
    memory_add.add_argument("--kind", default="observation")
    memory_add.add_argument("--importance", type=float, default=.5)
    memory_add.add_argument("--confidence", type=float, default=.7)
    memory_extract = run_sub.add_parser("memory-extract")
    memory_extract.add_argument("source")
    memory_search = run_sub.add_parser("memory-search")
    memory_search.add_argument("query")
    memory_search.add_argument("--limit", type=int, default=20)
    memory_export = run_sub.add_parser("memory-export")
    memory_export.add_argument("path")
    memory_backfill = run_sub.add_parser("memory-backfill")
    memory_backfill.add_argument("--limit", type=int, default=1000)
    run_sub.add_parser("memory-intelligence-status")
    notify = run_sub.add_parser("notify")
    notify.add_argument("title")
    notify.add_argument("body")
    notify.add_argument("--severity", choices=("info","warning","critical"), default="info")
    notify.add_argument("--channel", default="local")
    notify.add_argument("--discord-webhook", default="")
    notify.add_argument("--telegram-token", default="")
    notify.add_argument("--telegram-chat", default="")
    provider_route = run_sub.add_parser("provider-route")
    provider_route.add_argument("task")
    provider_route.add_argument("candidates", help="JSON list or path")
    provider_route.add_argument("--changed-files", type=int, default=0)
    provider_route.add_argument("--tokens", type=int, default=0)
    provider_pool = run_sub.add_parser("provider-pool")
    provider_pool.add_argument("pool_action", choices=("add", "feedback", "list", "route"))
    provider_pool.add_argument("provider", nargs="?", default="")
    provider_pool.add_argument("account", nargs="?", default="")
    provider_pool.add_argument("value", nargs="?", default="")
    provider_pool.add_argument("--subscription", action="store_true")
    provider_pool.add_argument("--priority", type=int, default=0)
    provider_pool.add_argument("--quota", type=float)
    provider_pool.add_argument("--quota-reset-at", type=float)
    provider_pool.add_argument("--model", action="append", default=[])
    provider_pool.add_argument("--success", action="store_true")
    provider_pool.add_argument("--failure", action="store_true")
    provider_pool.add_argument("--latency-ms", type=float, default=0.0)
    provider_pool.add_argument("--retry-after", type=float, default=0.0)
    provider_pool.add_argument("--changed-files", type=int, default=0)
    provider_pool.add_argument("--tokens", type=int, default=0)
    delegate = run_sub.add_parser("delegate")
    delegate.add_argument("objective")
    delegate.add_argument("--context-path", action="append", default=[])
    delegate.add_argument("--max-tasks", type=int, default=8)
    redact = run_sub.add_parser("redact")
    redact.add_argument("source", help="JSON/text value or path")
    wire = run_sub.add_parser("wire")
    wire.add_argument("wire_action", choices=("encode","decode"))
    wire.add_argument("source", help="JSON value or path")
    wire.add_argument("--minimum-savings", type=float, default=.08)
    code = run_sub.add_parser("code-intel")
    code.add_argument("intel_action", choices=("report","call","class","implementations","blast-radius","parser-manifest","dead","untested","provenance","risk","pagerank","hotspots","cycles","coupling","boundaries","signal","duplicates","delete","refactor","anti-patterns","cross-repo"))
    code.add_argument("query", nargs="?", default="")
    code.add_argument("--path", action="append", default=[])
    code.add_argument("--target-name", default="")
    add_platform_run_subcommands(run_sub)

    prove = sub.add_parser("prove", help="validate measured external evidence")
    prove_sub = prove.add_subparsers(dest="action", required=True)
    prove_sub.add_parser("plan")
    receipts = prove_sub.add_parser("receipts")
    receipts.add_argument("path")
    benchmark = prove_sub.add_parser("benchmark")
    benchmark.add_argument("path")
    long_context = prove_sub.add_parser("long-context")
    long_context.add_argument("path", nargs="?")
    maturity = prove_sub.add_parser("maturity")
    maturity.add_argument("path")
    readiness = prove_sub.add_parser("readiness")
    readiness.add_argument("--receipts")
    provider_billed = prove_sub.add_parser("provider-billed")
    provider_billed.add_argument("path")
    provider_billed.add_argument("--baseline", default="plain-host")
    provider_billed.add_argument("--candidate", default="syntavra-minimal")
    schema = prove_sub.add_parser("schema")
    schema.add_argument("--output", default="schemas/provider-usage-receipt.json")

    # Compatibility commands remain executable for existing installations, but the
    # public product model and help surface remain setup/status/run/prove.
    sub.add_parser("version", help=argparse.SUPPRESS)
    install = sub.add_parser("install", help=argparse.SUPPRESS)
    install.add_argument("--auto", action="store_true")
    install.add_argument("--all", action="store_true")
    install.add_argument("--apply", action="store_true")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--mcp-profile", choices=tuple(MCP_PROFILES), default="minimal")
    wrap = sub.add_parser("wrap", help=argparse.SUPPRESS)
    wrap.add_argument("host")
    wrap.add_argument("--output")
    sub.add_parser("doctor", help=argparse.SUPPRESS)
    sub.add_parser("stats", help=argparse.SUPPRESS)
    upgrade = sub.add_parser("upgrade", help=argparse.SUPPRESS)
    upgrade.add_argument("--target", default=VERSION)
    repair = sub.add_parser("repair", help=argparse.SUPPRESS)
    repair.add_argument("--apply", action="store_true")
    integrations = sub.add_parser("integrations", help=argparse.SUPPRESS)
    integrations.add_argument("--family", choices=("provider", "framework", "host"))
    stress = sub.add_parser("context-stress", help=argparse.SUPPRESS)
    stress.add_argument("--budget", type=int, default=4096)
    stress.add_argument("--max-tier", type=int, default=max(CONTEXT_TIERS))
    _add_benchmark_parser(sub, "signalbench", hidden=True)
    _add_benchmark_parser(sub, "signalbench2", hidden=True)
    proof = sub.add_parser("proof", help=argparse.SUPPRESS)
    proof_sub = proof.add_subparsers(dest="action", required=True)
    proof_sub.add_parser("status")
    for command in ("semantic-demo", "structural-v2"):
        semantic = sub.add_parser(command, help=argparse.SUPPRESS)
        semantic_sub = semantic.add_subparsers(dest="action", required=True)
        demo = semantic_sub.add_parser("demo")
        demo.add_argument("query")
    return parser


def _handle_prove(args: argparse.Namespace, state: Path) -> int:
    if args.action == "plan":
        _emit({
            "product": "Syntavra",
            "version": VERSION,
            "channel": "pre-release",
            "receipt_schema": "syntavra prove schema",
            "workloads": ProductSurface.manifest()["proof"]["workloads"],
            "long_context": long_context_manifest(),
            "maturity": {
                "minimum_days": ProductMaturityGate.minimum_days,
                "minimum_onboarding_receipts": ProductMaturityGate.minimum_onboarding_receipts,
                "minimum_users": ProductMaturityGate.minimum_users,
                "minimum_repositories": ProductMaturityGate.minimum_repositories,
                "minimum_public_downloads": ProductMaturityGate.minimum_public_downloads,
                "minimum_verified_releases": ProductMaturityGate.minimum_verified_releases,
            },
            "measured_fields": ProductSurface.manifest()["proof"]["measured_fields"],
            "minimums": {
                "paired_runs": MeasuredBenchmarkGate.minimum_pairs,
                "repositories": MeasuredBenchmarkGate.minimum_repositories,
                "tasks": MeasuredBenchmarkGate.minimum_tasks,
                "workload_families": MeasuredBenchmarkGate.minimum_workload_families,
            },
            "claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
        })
        return 0
    if args.action == "provider-billed":
        rows = load_results(Path(args.path))
        value = SignalBenchRunner.compare(rows, baseline_arm=args.baseline, candidate_arm=args.candidate)
        value["provider_observed_runs"] = sum(bool(row.provider_observed) for row in rows)
        value["total_runs"] = len(rows)
        value["fail_closed"] = True
        _emit(value)
        return 0 if value["claimable_superiority"] else 4
    if args.action == "schema":
        output = Path(args.output)
        _emit({"ok": True, "output": str(output), "schema": write_receipt_schema(output)})
        return 0
    if args.action == "long-context":
        if not args.path:
            _emit(long_context_manifest())
            return 0
        value = LongContextQualityGate.evaluate(_load_long_context_receipts(Path(args.path)))
        _emit(value)
        return 0 if value["ok"] else 4
    if args.action == "maturity":
        document = _load_json_argument(args.path)
        if not isinstance(document, dict):
            raise ValueError("maturity document must be a JSON object")
        onboarding, distributions, releases = load_maturity_document(document)
        value = ProductMaturityGate.evaluate(onboarding, distributions, releases)
        _emit(value)
        return 0 if value["ok"] else 4
    receipt_path = Path(args.path) if hasattr(args, "path") else Path(args.receipts) if args.receipts else None
    rows = ReceiptValidator.load(receipt_path) if receipt_path else []
    if args.action == "receipts":
        value = ReceiptValidator.evaluate(rows)
    elif args.action == "benchmark":
        value = MeasuredBenchmarkGate.evaluate(rows)
    else:
        value = ProductSurface.readiness(state, rows)
    _emit(value)
    return 0 if value["ok"] else 4



def _read_text_or_path(value: str) -> str:
    path = Path(value)
    return path.read_text(encoding="utf-8") if path.is_file() else value


def _handle_competitive_run(args: argparse.Namespace, *, project: Path, state: Path) -> tuple[bool, Any, int]:
    action = args.action
    if action == "mode":
        store = OptimizationModeStore(state)
        return True, store.set(args.mode, source=args.source) if args.mode else store.manifest(), 0
    if action == "statusline":
        return True, {"statusline": render_statusline(state, compact=not args.verbose), "mode": OptimizationModeStore(state).manifest(), "savings": SavingsLedger(state).summary()}, 0
    if action == "rewrite":
        if not args.rewrite_argv: raise ValueError("rewrite command is required")
        return True, CommandRewriteEngine().rewrite(args.rewrite_argv).to_dict(), 0
    if action == "transcript-mine":
        return True, TranscriptOpportunityMiner().analyze(Path(args.source) if Path(args.source).is_file() else args.source), 0
    if action == "watch":
        watcher = RepositoryWatcher(project, state)
        callback = None if args.no_index else lambda changes: {"index": CodeIntelligenceIndex(project, state_path=state / "structural.sqlite3").refresh_paths((*changes.added, *changes.modified), deleted_paths=changes.deleted), "changed": list(changes.changed)}
        rows = watcher.watch(interval_seconds=args.interval, iterations=args.iterations, callback=callback)
        return True, {"changes": [asdict(row) for row in rows], "status": watcher.status()}, 0
    if action == "dashboard":
        dashboard = LocalDashboard(project=project, state_root=state)
        if args.snapshot: return True, dashboard.snapshot(), 0
        dashboard.serve(host=args.host, port=args.port, open_browser=args.open)
        return True, {"ok": True, "stopped": True}, 0
    if action == "worker":
        worker=BackgroundIntelligenceWorker(project=project,state_root=state)
        if args.worker_action=="status": return True,worker.status(),0
        if args.worker_action=="start": return True,worker.spawn(project=project,state_root=state,interval_seconds=args.interval),0
        return True,worker.run(iterations=args.iterations,interval_seconds=args.interval),0
    if action == "audit-config":
        return True, AgentConfigAuditor(project).audit(), 0
    if action == "cache-plan":
        messages = _load_json_argument(args.source)
        if not isinstance(messages, list): raise ValueError("cache-plan source must contain a JSON message list")
        plan = PromptCacheOptimizer(state).plan(messages, provider=args.provider, model=args.model, ttl_seconds=args.ttl, reorder=not args.no_reorder)
        return True, asdict(plan), 0
    if action == "cache-health":
        return True, PromptCacheOptimizer(state).health(), 0
    if action == "cache-amortize":
        return True, PromptCacheOptimizer.amortization(cache_write_tokens=args.write, cache_read_tokens=args.read, uncached_input_tokens=args.uncached, requests=args.requests), 0
    memory = MemoryIntelligenceStore(state / "memory-intelligence.sqlite3", notification_feed=NotificationFeed(state))
    if action == "memory-add":
        return True, asdict(memory.add(args.text, kind=args.kind, importance=args.importance, confidence=args.confidence)), 0
    if action == "memory-extract":
        return True, {"observations": [asdict(row) for row in memory.extract(_read_text_or_path(args.source))]}, 0
    if action == "memory-search":
        return True, {"results": memory.search(args.query, limit=args.limit)}, 0
    if action == "memory-export":
        return True, memory.export_jsonl(Path(args.path)), 0
    if action == "memory-backfill":
        return True, memory.backfill_embeddings(limit=args.limit), 0
    if action == "memory-intelligence-status":
        return True, {"stats": memory.stats(), "ranked": memory.ranked(limit=100)}, 0
    if action == "notify":
        feed=NotificationFeed(state); item=feed.record(channel=args.channel,severity=args.severity,title=args.title,body=args.body)
        delivered=feed.deliver(item,discord_webhook=args.discord_webhook,telegram_bot_token=args.telegram_token,telegram_chat_id=args.telegram_chat)
        return True, {"notification":asdict(item),"delivery":delivered}, 0 if all(row.get("ok", True) for row in delivered.values()) else 3
    if action == "provider-route":
        rows=_load_json_argument(args.candidates)
        if not isinstance(rows,list): raise ValueError("provider candidates must be a JSON list")
        return True, asdict(AdaptiveProviderRouter.from_mappings(rows).route(args.task,changed_files=args.changed_files,token_estimate=args.tokens)), 0
    if action == "provider-pool":
        pool=ProviderAccountPool(state / "provider-accounts.sqlite3")
        if args.pool_action == "add":
            if not args.provider or not args.account or not args.value: raise ValueError("add requires provider account credential_ref")
            value=pool.register(args.provider,args.account,credential_ref=args.value,subscription=args.subscription,priority=args.priority,quota_remaining=1.0 if args.quota is None else args.quota,quota_reset_at=0.0 if args.quota_reset_at is None else args.quota_reset_at,model_allowlist=args.model)
            return True,asdict(value)|{"health_ratio":value.health_ratio},0
        if args.pool_action == "feedback":
            if args.success == args.failure: raise ValueError("feedback requires exactly one of --success or --failure")
            value=pool.record_result(args.provider,args.account,success=args.success,latency_ms=args.latency_ms,quota_remaining=args.quota,quota_reset_at=args.quota_reset_at,retry_after_seconds=args.retry_after)
            return True,asdict(value)|{"health_ratio":value.health_ratio},0
        if args.pool_action == "list": return True,pool.receipt(),0
        if args.pool_action == "route":
            rows=_load_json_argument(args.value)
            if not isinstance(rows,list): raise ValueError("route requires model JSON list/path as value")
            return True,asdict(pool.route(args.provider,rows,changed_files=args.changed_files,token_estimate=args.tokens)),0
        raise RuntimeError(args.pool_action)
    if action == "delegate":
        return True, asdict(AutomaticSubtaskDelegator().plan(args.objective,context_paths=args.context_path,max_tasks=args.max_tasks)), 0
    if action == "redact":
        raw=_read_text_or_path(args.source)
        try: value=json.loads(raw)
        except json.JSONDecodeError: value=raw
        redacted,receipt=SecretRedactor().redact(value)
        return True,{"value":redacted,"receipt":receipt},0
    if action == "wire":
        value=_load_json_argument(args.source)
        codec=LosslessWireCodec()
        return True, codec.encode(value,min_savings_ratio=args.minimum_savings) if args.wire_action=="encode" else codec.decode(value),0
    if action == "code-intel":
        index=CodeIntelligenceIndex(project, state_path=state / "structural.sqlite3"); index.build_incremental(state / "structural.sqlite3"); name=args.intel_action
        if name=="report": value=index.report()
        elif name=="call": value=index.call_hierarchy(args.query)
        elif name=="class": value=index.class_hierarchy(args.query)
        elif name=="implementations": value=index.implementations(args.query)
        elif name=="blast-radius": value=index.blast_radius(args.query)
        elif name=="parser-manifest": value=index.parser_manifest()
        elif name=="dead": value=index.dead_code()
        elif name=="untested": value=index.untested_symbols()
        elif name=="provenance": value=index.provenance(args.query)
        elif name=="risk": value=index.pr_risk(args.path)
        elif name=="pagerank": value=index.pagerank()
        elif name=="hotspots": value=index.hotspots()
        elif name=="cycles": value=index.cycles()
        elif name=="coupling": value=index.coupling()
        elif name=="boundaries": value=index.module_boundaries()
        elif name=="signal": value=index.signal_chain(args.query)
        elif name=="duplicates": value=index.duplicates()
        elif name=="delete": value=index.delete_safe(args.query)
        elif name=="refactor": value=index.refactor_plan(args.query,target_name=args.target_name)
        elif name=="anti-patterns": value=index.anti_patterns()
        elif name=="cross-repo": value=index.cross_repo_contracts([Path(item) for item in args.path])
        else: raise RuntimeError(name)
        return True,value,0
    return False,None,0

def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = Path(args.project).resolve(strict=False)
    state = Path(args.state_root).resolve(strict=False) if args.state_root else project / ".syntavra" / "pre-release"
    manager = ZeroFrictionManager(project, state)

    if args.command == "setup":
        value = manager.repair(apply=args.apply) if args.repair else manager.install(
            all_hosts=args.all, dry_run=not args.apply, profile=args.mcp_profile
        )
        _emit(value)
        return 0 if value["ok"] else 2

    if args.command == "status":
        receipt_rows = ReceiptValidator.load(Path(args.receipts)) if args.receipts else []
        platform = SyntavraPlatform(project, state / "unified")
        usage = UsageReceiptLedger(state / "usage-receipts.sqlite3")
        profile_path = state / "mcp-profile.json"
        try:
            active_profile = (
                json.loads(profile_path.read_text(encoding="utf-8"))
                if profile_path.is_file() else MCP_PROFILES["minimal"].to_dict()
            )
        except (OSError, json.JSONDecodeError):
            active_profile = {"name": "minimal", "invalid_profile_file": True}
        doctor = manager.doctor()
        evidence = {
            "provider_usage": usage.verify(),
            "token_attribution": usage.attribution_summary(),
            "claim_boundary": {
                "external_superiority": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
                "live_integration": "LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN",
                "public_maturity": "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN",
            },
        }
        focused: list[tuple[str, Any]] = []
        if args.doctor:
            focused.append(("doctor", doctor))
        if args.savings:
            focused.append(("savings", evidence["token_attribution"]))
        if args.profile:
            focused.append(("profile", active_profile))
        if args.memory:
            focused.append(("memory", _session_controller(project, state).status()))
        if args.evidence:
            focused.append(("evidence", evidence))
        if focused:
            value = {"product": "Syntavra", "version": VERSION, "channel": "pre-release", **dict(focused)}
        else:
            value = {
                "product": "Syntavra",
                "version": VERSION,
                "channel": "pre-release",
                "role": "token-and-context-optimization-skill",
                "doctor": doctor,
                "stats": manager.stats(),
                "savings": evidence["token_attribution"],
                "profile": active_profile,
                "readiness": ProductSurface.readiness(state, receipt_rows),
                "evidence": evidence,
                "proxy_presets": ProxyProductRegistry.validate(),
                "primary_workflow": ["setup", "status", "run", "prove"],
                "platform": platform.status(),
                "competitive_features": competitive_feature_manifest(project),
            }
        _emit(value)
        return 0 if doctor["ok"] else 2

    if args.command == "run":
        platform_result = handle_platform(args, project=project, state=state)
        if platform_result is not None:
            _emit(platform_result)
            return 0 if platform_result.get("ok", True) else 3
        handled, competitive_value, competitive_code = _handle_competitive_run(args, project=project, state=state)
        if handled:
            _emit(competitive_value)
            return competitive_code
        if args.action == "manifest":
            value = ProductSurface.manifest()
            value["proxy_presets"] = ProxyProductRegistry.validate()
            value["competitive_features"] = competitive_feature_manifest(project)
            _emit(value)
            return 0
        if args.action == "route":
            decision = ToolRoutingEnforcer.decide(
                args.tool,
                profile=args.profile,
                sandboxed=args.sandboxed,
                exact_evidence=not args.no_exact_evidence,
                explicit_user_authorization=args.user_authorized,
            )
            _emit(asdict(decision))
            return 0 if decision.allowed else 5
        if args.action == "record":
            event = _load_json_argument(args.event)
            if not isinstance(event, dict):
                raise ValueError("analytics event must be a JSON object")
            _emit(SessionAnalyticsStore(state / "analytics" / "events.jsonl").record(event))
            return 0
        if args.action == "proxy-plan":
            value = ProxyProductRegistry.plan(args.provider, upstream=args.upstream)
            _emit(value)
            return 0 if value["ok"] else 3
        if args.action == "proxy-service":
            value = ProxyProductRegistry.service(
                args.service_action,
                args.provider,
                project=project,
                state_root=state,
                home=Path(args.home).resolve(strict=False) if args.home else None,
                upstream=args.upstream,
                listen_host=args.listen_host,
                listen_port=args.listen_port,
                cache_policy=args.cache_policy,
                environment_file=args.environment_file,
                platform_name=args.platform,
                apply=bool(getattr(args, "apply", False)),
                activate=bool(getattr(args, "activate", False)),
            )
            _emit(value)
            return 0 if value.get("ok", False) else 3
        controller = _session_controller(project, state)
        if args.action == "session-open":
            metadata = _load_json_argument(args.metadata)
            if not isinstance(metadata, dict):
                raise ValueError("session metadata must be a JSON object")
            value = controller.open_or_resume(args.session_id, metadata=metadata)
        elif args.action == "session-append":
            payload = _load_json_argument(args.payload)
            if not isinstance(payload, dict):
                raise ValueError("session payload must be a JSON object")
            value = controller.append(args.session_id, args.event_type, payload)
        elif args.action == "session-compact":
            value = controller.compact_once(args.session_id, force=args.force)
        elif args.action == "session-continuity":
            value = controller.continuity_receipt(args.session_id, token_budget=args.token_budget)
        elif args.action == "session-status":
            value = controller.status()
        else:
            raise RuntimeError(args.action)
        _emit(value)
        return 0 if value.get("ok", True) else 3

    if args.command == "prove":
        return _handle_prove(args, state)

    if args.command == "version":
        _emit({"identity": identity().to_dict(), "repository": validate_repository_identity(project)})
        return 0
    if args.command == "install":
        dry_run = bool(args.dry_run or not args.apply)
        _emit(manager.install(all_hosts=args.all, dry_run=dry_run, profile=args.mcp_profile))
        return 0
    if args.command == "wrap":
        output = Path(args.output) if args.output else state / "wrappers" / (args.host + (".cmd" if os.name == "nt" else ""))
        _emit(manager.write_wrapper(args.host, output))
        return 0
    if args.command == "doctor":
        value = manager.doctor(); _emit(value); return 0 if value["ok"] else 2
    if args.command == "stats":
        _emit(manager.stats()); return 0
    if args.command == "upgrade":
        _emit(manager.upgrade(args.target)); return 0
    if args.command == "repair":
        _emit(manager.repair(apply=args.apply)); return 0
    if args.command == "integrations":
        _emit({
            "coverage": IntegrationMatrix.validate(),
            "integrations": IntegrationMatrix.records(args.family),
            "platform_adapters": PlatformAdapterRegistry.validate(),
            "proxy_presets": ProxyProductRegistry.validate(),
        })
        return 0
    if args.command == "context-stress":
        reports = [row for row in UnboundedContextCoordinator.stress_tiers(active_budget=args.budget) if row["tier_tokens"] <= args.max_tier]
        result = {"ok": bool(reports) and all(row["within_budget"] and row["all_referenced"] and not row["forced_restart"] for row in reports), "tiers": reports}
        _emit(result)
        return 0 if result["ok"] else 3
    if args.command in {"signalbench", "signalbench2"}:
        if args.action == "plan":
            tasks = CodingCorpusPlanner.generate_slots()
            schedule = PairedSchedule(tasks, default_arms(), repetitions=args.repetitions)
            _emit({"corpus": {"tasks": len(tasks), "live": False}, "schedule": {"runs": schedule.count, "repetitions": args.repetitions}, "manifest": schedule.manifest()})
            return 0
        receipts_value = json.loads(Path(args.receipts).read_text(encoding="utf-8"))
        value = SuperiorityGate.evaluate(receipts_value)
        _emit(value)
        return 0 if value["ok"] else 4
    if args.command == "proof":
        _emit({
            "release": PublicProofGate.release_readiness(
                sbom=False,
                provenance=False,
                reproducible_build=False,
                signed_tags=False,
                migration_guides=True,
                rollback=True,
            ),
            "workloads": PublicProofGate.workload_manifest(),
            "maturity": "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN",
        })
        return 0
    if args.command in {"semantic-demo", "structural-v2"}:
        graph = SemanticGraph()
        graph.add_node(GraphNode("auth", "function", "auth.refresh", "src/auth.py", 10, 40, "python", "syntavra://evidence/auth", 0.9, ("security",)))
        graph.add_node(GraphNode("test", "test", "test_auth_refresh", "tests/test_auth.py", 5, 30, "python", "syntavra://evidence/test"))
        graph.add_edge(GraphEdge("test", "auth", "calls", evidence_ref="syntavra://evidence/edge"))
        _emit({"results": [asdict(row) for row in graph.query(args.query)], "impact": graph.impact("auth")})
        return 0
    raise RuntimeError(args.command)
