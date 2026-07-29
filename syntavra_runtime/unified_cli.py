from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .backup import StateBackupManager
from .unified_config import ConfigManager
from .evidence import EvidenceStore
from .janitor import RuntimeJanitor
from .job_scheduler import DurableJobScheduler
from .migrations import MigrationManager
from .observability import Observability
from .read_only_cli_contract import pipeline_description, plugin_inventory
from .prerelease_cli import PRE_RELEASE_COMMANDS, main as prerelease_main
from .platform import SyntavraPlatform
from .autonomous_agent import AgentMode, AgentTask, PatchProposal
from .model_gateway import GatewayConfig, create_gateway
from .agent_runtime import AgentDeliveryMode
from .util import stable_project_id


CORE_COMMANDS = {"config", "backup", "maintenance", "pipeline", "plugins", "scheduler", "telemetry", "migrate", "agent"}
EXTERNAL_PROOF_ACTIONS = {"suites", "external-suite", "integrations"}


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(child) for child in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _emit(value: Any) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _global(argv: list[str]) -> tuple[Path, Path, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project", default=".")
    parser.add_argument("--state-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--codex-home")
    parser.add_argument("--host", default="codex")
    parser.add_argument("--json", action="store_true")
    values, rest = parser.parse_known_args(argv)
    project = Path(values.project).resolve(strict=False)
    state = Path(values.state_root).resolve(strict=False) if values.state_root else project / ".syntavra" / "pre-release"
    return project, state, rest


def _find_command(rest: list[str]) -> tuple[str, int]:
    for index, value in enumerate(rest):
        if not value.startswith("-"):
            return value, index
    return "", -1


def _core_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syntavra", description="Syntavra v0.0.1 pre-release unified production core")
    parser.add_argument("--project", default=".")
    parser.add_argument("--state-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--codex-home")
    parser.add_argument("--host", default="codex")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("config")
    cs = config.add_subparsers(dest="action", required=True)
    cs.add_parser("show")
    explain = cs.add_parser("explain"); explain.add_argument("path")
    cs.add_parser("validate")

    backup = sub.add_parser("backup")
    bs = backup.add_subparsers(dest="action", required=True)
    create = bs.add_parser("create"); create.add_argument("path"); create.add_argument("--plaintext", action="store_true")
    verify = bs.add_parser("verify"); verify.add_argument("path"); verify.add_argument("--plaintext", action="store_true")
    restore = bs.add_parser("restore"); restore.add_argument("path"); restore.add_argument("--plaintext", action="store_true"); restore.add_argument("--apply", action="store_true")

    maintenance = sub.add_parser("maintenance")
    ms = maintenance.add_subparsers(dest="action", required=True)
    janitor = ms.add_parser("janitor"); janitor.add_argument("--apply", action="store_true"); janitor.add_argument("--ttl-days", type=float, default=30); janitor.add_argument("--max-delete-bytes", type=int, default=1024*1024*1024)

    pipeline = sub.add_parser("pipeline")
    ps = pipeline.add_subparsers(dest="action", required=True)
    ps.add_parser("describe")

    plugins = sub.add_parser("plugins")
    pls = plugins.add_subparsers(dest="action", required=True)
    pls.add_parser("list")

    scheduler = sub.add_parser("scheduler")
    ss = scheduler.add_subparsers(dest="action", required=True)
    ss.add_parser("stats")
    listing = ss.add_parser("list"); listing.add_argument("--state", action="append", default=[]); listing.add_argument("--limit", type=int, default=100)
    ss.add_parser("reap")

    telemetry = sub.add_parser("telemetry")
    ts = telemetry.add_subparsers(dest="action", required=True)
    metrics = ts.add_parser("metrics"); metrics.add_argument("--prometheus", action="store_true")
    bundle = ts.add_parser("bundle"); bundle.add_argument("path")

    migrate = sub.add_parser("migrate")
    mgs = migrate.add_subparsers(dest="action", required=True)
    plan = mgs.add_parser("plan"); plan.add_argument("database")
    apply = mgs.add_parser("apply"); apply.add_argument("database"); apply.add_argument("--dry-run", action="store_true")
    rollback = mgs.add_parser("rollback"); rollback.add_argument("database"); rollback.add_argument("backup")

    agent = sub.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    agent_run = agent_sub.add_parser("run")
    agent_run.add_argument("task")
    agent_run.add_argument("--provider", default="openai-compatible")
    agent_run.add_argument("--model", required=True)
    agent_run.add_argument("--endpoint", default="")
    agent_run.add_argument("--api-key-env", default="")
    agent_run.add_argument("--api-mode", choices=("auto", "responses", "chat"), default="auto")
    agent_run.add_argument("--mode", choices=tuple(item.value for item in AgentMode), default=AgentMode.REVIEW_REQUIRED.value)
    agent_run.add_argument("--attempts", type=int, default=3)
    agent_run.add_argument("--timeout", type=float, default=900.0)
    agent_run.add_argument("--token-budget", type=int)
    agent_run.add_argument("--cost-budget", type=float)
    agent_run.add_argument("--authorized", action="store_true")
    agent_run.add_argument("--session-id")
    agent_run.add_argument("--no-post-verifiers", action="store_true")
    agent_run.add_argument("--delivery", choices=tuple(item.value for item in AgentDeliveryMode), default=AgentDeliveryMode.DIFF.value)
    agent_run.add_argument("--branch-name", default="")
    agent_run.add_argument("--commit-message", default="")
    agent_run.add_argument("--pr-title", default="")
    agent_run.add_argument("--pr-body", default="")
    agent_run.add_argument("--events-jsonl", nargs="?", const="-", default="")

    replay = agent_sub.add_parser("replay")
    replay.add_argument("task")
    replay.add_argument("patches", help="JSON patch list or path")
    replay.add_argument("verifier", help="JSON argv or path")
    replay.add_argument("--mode", choices=tuple(item.value for item in AgentMode), default=AgentMode.REVIEW_REQUIRED.value)
    replay.add_argument("--attempts", type=int, default=3)
    replay.add_argument("--timeout", type=float, default=900.0)
    replay.add_argument("--authorized", action="store_true")
    return parser


def _core_main(argv: list[str]) -> int:
    args = _core_parser().parse_args(argv)
    project = Path(args.project).resolve(strict=False)
    state = Path(args.state_root).resolve(strict=False) if args.state_root else project / ".syntavra" / "pre-release"
    project_id = stable_project_id(project)

    if args.command == "pipeline":
        _emit(pipeline_description())
        return 0
    if args.command == "plugins":
        _emit(plugin_inventory())
        return 0

    evidence = EvidenceStore(state / "evidence", project_id=project_id)

    if args.command == "config":
        manager = ConfigManager(project_root=project, state_root=state)
        snapshot = manager.load(force=True)
        if args.action == "show":
            _emit(snapshot.to_dict())
        elif args.action == "explain":
            item = snapshot.explain(args.path)
            _emit(asdict(item) if item else {"found": False, "path": args.path})
        else:
            _emit({"ok": True, "config_hash": snapshot.config_hash, "warnings": snapshot.warnings})
        return 0

    if args.command == "backup":
        manager = StateBackupManager(state, project_id=project_id)
        encrypted = not args.plaintext
        if args.action == "create":
            _emit(manager.create(Path(args.path), encrypt=encrypted))
        elif args.action == "verify":
            result = manager.verify(Path(args.path), encrypted=encrypted); _emit(result); return 0 if result["ok"] else 3
        else:
            _emit(manager.restore(Path(args.path), encrypted=encrypted, dry_run=not args.apply))
        return 0

    if args.command == "maintenance":
        result = evidence.gc(
            ttl_seconds=max(0.0, args.ttl_days) * 24 * 60 * 60,
            max_delete_bytes=args.max_delete_bytes,
            dry_run=not args.apply,
        )
        _emit(result)
        return 0

    if args.command == "scheduler":
        scheduler = DurableJobScheduler(state / "scheduler.sqlite3")
        if args.action == "stats": _emit(scheduler.stats())
        elif args.action == "list": _emit({"jobs": scheduler.list(states=args.state, limit=args.limit)})
        else: _emit({"reaped": scheduler.reap()})
        return 0

    if args.command == "telemetry":
        observability = Observability(state / "observability")
        if args.action == "metrics":
            print(observability.metrics.prometheus() if args.prometheus else json.dumps(observability.metrics.snapshot(), indent=2, sort_keys=True))
        else:
            _emit({"path": str(observability.diagnostic_bundle(Path(args.path)))})
        return 0

    if args.command == "migrate":
        manager = MigrationManager(Path(args.database), ())
        if args.action == "plan": _emit(manager.plan())
        elif args.action == "apply": _emit(manager.apply(dry_run=args.dry_run))
        else: manager.rollback(Path(args.backup)); _emit({"ok": True})
        return 0
    if args.command == "agent":
        runtime = SyntavraPlatform(project, state / "unified")
        if args.action == "run":
            gateway = create_gateway(GatewayConfig(
                provider=args.provider,
                model=args.model,
                endpoint=args.endpoint,
                api_key_env=args.api_key_env,
                timeout_seconds=args.timeout,
                api_mode=args.api_mode,
            ))
            event_stream = None
            try:
                if args.events_jsonl:
                    event_stream = sys.stderr if args.events_jsonl == "-" else Path(args.events_jsonl).open("a", encoding="utf-8")
                def emit_event(event):
                    if event_stream is not None:
                        print(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True), file=event_stream, flush=True)
                receipt = runtime.agent_runtime.run(
                    args.task,
                    gateway,
                    mode=AgentMode(args.mode),
                    max_attempts=args.attempts,
                    timeout_seconds=args.timeout,
                    token_budget=args.token_budget,
                    cost_budget=args.cost_budget,
                    authorized=args.authorized,
                    session_id=args.session_id,
                    run_post_verifiers=not args.no_post_verifiers,
                    delivery_mode=AgentDeliveryMode(args.delivery),
                    branch_name=args.branch_name,
                    commit_message=args.commit_message,
                    pr_title=args.pr_title,
                    pr_body=args.pr_body,
                    event_sink=emit_event if event_stream is not None else None,
                )
            finally:
                if event_stream is not None and event_stream is not sys.stderr:
                    event_stream.close()
            _emit(asdict(receipt) | {"ok": receipt.ok})
            return 0 if receipt.ok else 4
        def load_json(value: str):
            candidate = Path(value)
            return json.loads(candidate.read_text(encoding="utf-8")) if candidate.is_file() else json.loads(value)
        rows = load_json(args.patches)
        verifier = load_json(args.verifier)
        if not isinstance(rows, list) or not isinstance(verifier, list) or not verifier:
            raise ValueError("agent replay requires a patch list and non-empty verifier argv")
        class ReplayProvider:
            def __init__(self, values):
                self.values = list(values)
                self.index = 0
            def propose(self, task, context, previous_failure):
                del task, context, previous_failure
                if self.index >= len(self.values):
                    return PatchProposal("")
                value = self.values[self.index]
                self.index += 1
                return PatchProposal(**value) if isinstance(value, dict) else PatchProposal(str(value))
        receipt = runtime.autonomous_agent.execute(
            AgentTask(
                instruction=args.task,
                verifier=tuple(str(item) for item in verifier),
                mode=AgentMode(args.mode),
                max_attempts=args.attempts,
                timeout_seconds=args.timeout,
                retain_workspace=True,
            ),
            ReplayProvider(rows),
            authorized=args.authorized,
        )
        _emit(asdict(receipt) | {"ok": receipt.ok, "surface": "agent-replay"})
        return 0 if receipt.ok else 4
    raise RuntimeError(args.command)


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    _, _, rest = _global(values)
    command, command_index = _find_command(rest)
    if command == "prove" and command_index >= 0 and len(rest) > command_index + 1:
        action = rest[command_index + 1]
        if action in EXTERNAL_PROOF_ACTIONS:
            from .external_benchmark_cli import main as external_proof_main
            return int(external_proof_main(values))
    if command in PRE_RELEASE_COMMANDS:
        return int(prerelease_main(values))
    if command in CORE_COMMANDS:
        return _core_main(values)
    if command == "evidence" and len(rest) > 1 and rest[1] in {"stats", "gc", "rotate-key"}:
        project, state, _ = _global(values)
        store = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
        action = rest[1]
        if action == "stats": _emit(store.stats()); return 0
        if action == "rotate-key": _emit(store.rotate_key(reencrypt=True)); return 0
        dry_run = "--apply" not in rest
        ttl_days = 30.0
        if "--ttl-days" in rest:
            ttl_days = float(rest[rest.index("--ttl-days") + 1])
        _emit(store.gc(ttl_seconds=ttl_days * 86400, dry_run=dry_run)); return 0
    from .cli import main as legacy_main
    return int(legacy_main(values))


def product_compat_main(argv: list[str] | None = None) -> int:
    return int(main(sys.argv[1:] if argv is None else argv))
