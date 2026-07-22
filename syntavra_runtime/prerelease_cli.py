from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
        description="Syntavra 0.0.1 pre-release AI engineering platform",
        epilog="Product workflow: setup -> status -> run -> prove",
    )
    parser.add_argument("--project", default=".")
    parser.add_argument("--state-root")
    sub = parser.add_subparsers(dest="command", required=True, metavar="{setup,status,run,prove}")

    setup = sub.add_parser("setup", help="install, configure or repair Syntavra")
    setup.add_argument("--apply", action="store_true")
    setup.add_argument("--all", action="store_true")
    setup.add_argument("--mcp-profile", choices=tuple(MCP_PROFILES), default="minimal")

    status = sub.add_parser("status", help="show health, usage, readiness and evidence gates")
    status.add_argument("--receipts")

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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = Path(args.project).resolve(strict=False)
    state = Path(args.state_root).resolve(strict=False) if args.state_root else project / ".syntavra" / "pre-release"
    manager = ZeroFrictionManager(project, state)

    if args.command == "setup":
        value = manager.install(all_hosts=args.all, dry_run=not args.apply, profile=args.mcp_profile)
        _emit(value)
        return 0 if value["ok"] else 2

    if args.command == "status":
        receipt_rows = ReceiptValidator.load(Path(args.receipts)) if args.receipts else []
        platform = SyntavraPlatform(project, state / "unified")
        value = {
            "product": "Syntavra",
            "version": VERSION,
            "channel": "pre-release",
            "doctor": manager.doctor(),
            "stats": manager.stats(),
            "readiness": ProductSurface.readiness(state, receipt_rows),
            "proxy_presets": ProxyProductRegistry.validate(),
            "primary_workflow": ["setup", "status", "run", "prove"],
            "platform": platform.status(),
        }
        _emit(value)
        return 0 if value["doctor"]["ok"] else 2

    if args.command == "run":
        platform_result = handle_platform(args, project=project, state=state)
        if platform_result is not None:
            _emit(platform_result)
            return 0 if platform_result.get("ok", True) else 3
        if args.action == "manifest":
            value = ProductSurface.manifest()
            value["proxy_presets"] = ProxyProductRegistry.validate()
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
