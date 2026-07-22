from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .benchmark_harness import compare_results, generate_synthetic_repository, load_arm_results, validate_config, write_config
from .bootstrap import resolve_codex_home, start_runtime
from .claim_governance import verify_claim
from .competitive_cli import add_competitive_commands
from .compression import ContentRouter, ReversibleContentStore
from .context_governor import evaluate, pack_context
from .evidence import EvidenceStore
from .hooks import HookEngine, run_hook
from .host_adapters import KNOWN_HOSTS, detect_hosts, environment_capabilities, negotiate
from .installer import HostInstaller
from .mcp_server import MCPServer
from .memory import PersistentMemory
from .models import ContextItem
from .output_governor import CONTRACTS, PROFILES, OutputGovernor
from .process_broker import ProcessBroker
from .rollout import RolloutTailer, discover_rollouts, select_active_rollout
from .sandbox import SandboxManager, SandboxPolicy
from .session_runtime import SessionRuntime
from .signalbench import SignalBenchRunner, load_results
from .status import inspect_runtime
from .structural import StructuralIndex
from .util import atomic_write_json, stable_project_id
from .verifier_graph import VerifierGraph

VERSION = "0.0.1"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def emit(value: Any) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, default=str))


def root() -> Path:
    return Path(__file__).resolve().parents[1]


def skill_root() -> Path:
    repository_skill = root() / "skills" / "syntavra"
    if repository_skill.is_dir():
        return repository_skill
    bundled = Path(__file__).resolve().parent / "bundled_skill"
    return bundled


def project(args: argparse.Namespace, *, strict: bool = True) -> Path:
    return Path(args.project).resolve(strict=strict)


def state_root(args: argparse.Namespace) -> Path:
    return Path(args.state_root).resolve(strict=False) if args.state_root else project(args, strict=False) / ".syntavra" / "runtime-v3"


def evidence_store(args: argparse.Namespace) -> EvidenceStore:
    return EvidenceStore(state_root(args) / "evidence", project_id=stable_project_id(project(args)))


def compression_store(args: argparse.Namespace) -> ReversibleContentStore:
    return ReversibleContentStore(state_root(args) / "compression.sqlite3", evidence=evidence_store(args))


def compressor(args: argparse.Namespace) -> ContentRouter:
    return ContentRouter(compression_store(args), repository_root=project(args))


def broker(args: argparse.Namespace) -> ProcessBroker:
    return ProcessBroker(state_root(args) / "broker", evidence_store(args), heartbeat_interval=getattr(args, "heartbeat", 1.0))


def session_runtime(args: argparse.Namespace) -> SessionRuntime:
    return SessionRuntime(state_root(args) / "sessions.sqlite3", project_id=stable_project_id(project(args)))


def sandbox_manager(args: argparse.Namespace) -> SandboxManager:
    return SandboxManager(state_root(args) / "sandbox", project=project(args), evidence=evidence_store(args))


def command_init(args: argparse.Namespace) -> int:
    emit(start_runtime(
        args.task,
        project=project(args),
        skill_root=Path(args.skill_root),
        state_root=state_root(args),
        codex_home=Path(args.codex_home) if args.codex_home else None,
        host=args.host,
    ))
    return 0


def command_status(args: argparse.Namespace) -> int:
    health = inspect_runtime(
        project_root=project(args, strict=False),
        skill_root=Path(args.skill_root),
        state_root=state_root(args),
        codex_home=Path(args.codex_home) if args.codex_home else resolve_codex_home(),
        host=args.host,
        require_rollout=args.require_rollout,
    )
    emit({"version": VERSION, **asdict(health)})
    return 0 if health.healthy else 2


def _installer(args: argparse.Namespace) -> HostInstaller:
    return HostInstaller(
        project=project(args),
        skill_root=Path(args.skill_root),
        home=Path(args.home).resolve(strict=False) if getattr(args, "home", None) else None,
    )


def command_doctor(args: argparse.Namespace) -> int:
    health = inspect_runtime(
        project_root=project(args, strict=False),
        skill_root=Path(args.skill_root),
        state_root=state_root(args),
        codex_home=Path(args.codex_home) if args.codex_home else resolve_codex_home(),
        host=args.host,
        require_rollout=args.require_rollout,
    )
    installer = _installer(args).doctor()
    result = {"version": VERSION, "runtime": asdict(health), "installation": installer}
    emit(result)
    return 0 if health.healthy and installer["ok"] else 2


def command_install(args: argparse.Namespace) -> int:
    hosts = sorted(KNOWN_HOSTS) if args.all else args.host_name or [args.host]
    result = _installer(args).install(hosts, scope=args.scope, dry_run=args.dry_run)
    emit(result)
    return 0


def command_uninstall(args: argparse.Namespace) -> int:
    emit(_installer(args).uninstall(dry_run=args.dry_run))
    return 0


def command_run(args: argparse.Namespace) -> int:
    argv = tuple(args.command[1:] if args.command and args.command[0] == "--" else args.command)
    if not argv:
        raise SystemExit("syntavra run requires argv after --")
    active = broker(args)
    if args.background:
        job = active.submit(argv, cwd=project(args), timeout=args.timeout, repository_tree=args.repository_tree)
        emit({
            "event": "JOB_ACCEPTED", "job": job, "model_polling_calls": 0,
            "completion_cursor": 0, "completion_queue": str(state_root(args) / "broker" / "broker.sqlite3"),
        })
        return 0
    result = active.run(argv, cwd=project(args), timeout=args.timeout, repository_tree=args.repository_tree)
    emit(result)
    return 124 if result.timed_out else 130 if result.cancelled else int(result.exit_code or 0)


def command_job(args: argparse.Namespace) -> int:
    active = broker(args)
    if args.action == "list":
        emit({"jobs": active.list_jobs(states=tuple(args.state), limit=args.limit)})
    elif args.action == "show":
        emit(active.show(args.job_id))
    elif args.action == "cancel":
        emit(active.cancel(args.job_id))
    elif args.action == "recover":
        emit({"orphaned": active.recover()})
    elif args.action == "completions":
        emit(active.drain_completions(after=args.after, limit=args.limit))
    else:
        raise ValueError(args.action)
    return 0


def command_rollout(args: argparse.Namespace) -> int:
    candidates = discover_rollouts(Path(args.codex_home) if args.codex_home else resolve_codex_home())
    selected = Path(args.rollout).resolve(strict=True) if args.rollout else select_active_rollout(candidates, session_id=args.session_hint)
    if not selected:
        emit({"ok": False, "reason": "no rollout found"})
        return 2
    state = Path(args.state_file) if args.state_file else state_root(args) / "rollout-state.json"
    emit({"rollout": str(selected), **RolloutTailer(selected, state).poll()})
    return 0


def command_evidence(args: argparse.Namespace) -> int:
    store = evidence_store(args)
    if args.action == "get":
        data = store.get(args.handle, max_bytes=args.max_bytes)
        if args.output:
            Path(args.output).write_bytes(data)
            emit({"handle": args.handle, "bytes": len(data), "output": args.output})
        else:
            emit({"handle": args.handle, "bytes": len(data), "text": data.decode("utf-8", errors="replace")})
    elif args.action == "describe":
        emit(store.describe(args.handle))
    else:
        raise ValueError(args.action)
    return 0


def _memory(args: argparse.Namespace) -> PersistentMemory:
    return PersistentMemory(state_root(args) / "memory.sqlite3", project_id=stable_project_id(project(args)), user_id=args.user_id)


def command_memory(args: argparse.Namespace) -> int:
    memory = _memory(args)
    if args.action == "add":
        emit(memory.add(args.memory_class, args.text, confidence=args.confidence, provenance={"source": args.source}, expires_at=args.expires_at, tags=args.tag))
    elif args.action == "search":
        emit(memory.search(args.query, limit=args.limit, memory_classes=args.memory_class, include_superseded=args.include_superseded, include_expired=args.include_expired))
    elif args.action == "link":
        memory.link(args.source_id, args.relation, args.target_id, weight=args.weight)
        emit({"ok": True})
    elif args.action == "neighbors":
        emit({"results": memory.neighbors(args.memory_id, relation=args.relation, limit=args.limit)})
    else:
        raise ValueError(args.action)
    return 0


def structural(args: argparse.Namespace) -> StructuralIndex:
    index = StructuralIndex(state_root(args) / "structural.sqlite3", repository_root=project(args), repository_id=stable_project_id(project(args)))
    index.index()
    return index


def command_structural(args: argparse.Namespace) -> int:
    index = structural(args)
    if args.action == "symbol":
        emit(index.inspect_symbol(args.query, limit=args.limit))
    elif args.action == "impact":
        emit(index.inspect_impact(args.query, max_depth=args.max_depth))
    elif args.action == "paths":
        emit(index.impacted_by_paths(args.path, max_depth=args.max_depth))
    elif args.action == "map":
        emit(index.repository_map(args.query, token_budget=args.token_budget, max_depth=args.max_depth))
    elif args.action == "stats":
        emit(index.stats())
    else:
        raise ValueError(args.action)
    return 0


def command_context(args: argparse.Namespace) -> int:
    if args.context_action == "evaluate":
        emit(evaluate(args.used, args.window, churn=args.churn, evidence_pressure=args.evidence_pressure))
    elif args.context_action == "pack":
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        items = [ContextItem(**row) for row in payload.get("items", [])]
        emit(pack_context(items, budget=args.budget, mandatory_roles=args.mandatory_role))
    else:
        raise ValueError(args.context_action)
    return 0


def command_host(args: argparse.Namespace) -> int:
    if args.host_action == "detect":
        emit({"hosts": detect_hosts(project(args), home=Path(args.home).resolve(strict=False) if args.home else None)})
    elif args.host_action == "capabilities":
        emit(environment_capabilities())
    else:
        emit(negotiate(args.host_name or args.host, runtime_available=not args.runtime_unavailable))
    return 0


def command_hook(args: argparse.Namespace) -> int:
    payload = args.payload if args.payload is not None else sys.stdin.read()
    engine = HookEngine(project_root=project(args), compressor=compressor(args))
    print(run_hook(engine, args.phase, payload))
    return 0


def command_mcp(args: argparse.Namespace) -> int:
    server = MCPServer(
        project=project(args), state_root=state_root(args), skill_root=Path(args.skill_root),
        codex_home=Path(args.codex_home) if args.codex_home else resolve_codex_home(), host=args.host,
    )
    return server.serve()


def _policy(args: argparse.Namespace) -> SandboxPolicy:
    return SandboxPolicy(
        backend=args.backend,
        network=args.network,
        read_only_repository=args.read_only,
        timeout_seconds=args.timeout,
        memory_mb=args.memory_mb,
        cpu_count=args.cpus,
        process_limit=args.pids,
        writable_paths=tuple(args.writable),
        strict=not args.allow_degraded,
    )


def command_sandbox(args: argparse.Namespace) -> int:
    manager = sandbox_manager(args)
    if args.action == "backends":
        emit(manager.backends())
        return 0
    argv = tuple(args.command[1:] if args.command and args.command[0] == "--" else args.command)
    if not argv:
        raise SystemExit("sandbox command requires argv after --")
    policy = _policy(args)
    if args.action == "plan":
        emit(manager.plan(argv, policy=policy, cwd=args.cwd))
        return 0
    result = manager.execute(argv, policy=policy, cwd=args.cwd)
    emit(result)
    return 124 if result.timed_out else result.exit_code


def command_compression(args: argparse.Namespace) -> int:
    store = compression_store(args)
    if args.action == "put":
        if args.input:
            data = Path(args.input).read_bytes()
            path = args.path or args.input
        else:
            data = args.text if args.text is not None else sys.stdin.read()
            path = args.path or ""
        result = ContentRouter(store, repository_root=project(args)).compress(data, hint=args.hint, path=path, budget_bytes=args.budget_bytes)
        emit(result)
    elif args.action == "get":
        data = store.restore(args.compression_id, chunk=args.chunk)
        if args.output:
            Path(args.output).write_bytes(data)
            emit({"compression_id": args.compression_id, "bytes": len(data), "output": args.output})
        else:
            emit({"compression_id": args.compression_id, "bytes": len(data), "text": data.decode("utf-8", errors="replace")})
    elif args.action == "describe":
        emit(store.describe(args.compression_id))
    elif args.action == "verify":
        ok = store.verify_roundtrip(args.compression_id)
        emit({"compression_id": args.compression_id, "ok": ok})
        return 0 if ok else 3
    else:
        raise ValueError(args.action)
    return 0


def command_session(args: argparse.Namespace) -> int:
    runtime = session_runtime(args)
    if args.action == "open":
        emit(runtime.create_session(session_id=args.session_id, parent_ids=args.parent, metadata={"task": args.task} if args.task else {}))
    elif args.action == "list":
        emit({"sessions": runtime.list_sessions(state=args.state)})
    elif args.action == "append":
        payload = json.loads(args.payload)
        emit(runtime.append(args.session_id, args.event_type, payload))
    elif args.action == "context":
        emit(runtime.active_context(args.session_id, token_budget=args.token_budget, recent_events=args.recent_events))
    elif args.action == "compact":
        emit({"session_id": args.session_id, "root_summary_id": runtime.compact(args.session_id, leaf_size=args.leaf_size, fanout=args.fanout, force=args.force)})
    elif args.action == "checkpoint":
        emit(runtime.checkpoint(args.session_id, metadata={"label": args.label} if args.label else {}))
    elif args.action == "fork":
        emit(runtime.fork(args.session_id, metadata={"label": args.label} if args.label else {}))
    elif args.action == "merge":
        emit(runtime.merge(args.session_id, metadata={"label": args.label} if args.label else {}))
    elif args.action == "verify":
        result = runtime.verify(args.session_id)
        emit(result)
        return 0 if result["ok"] else 3
    elif args.action == "recover":
        result = runtime.recover()
        emit(result)
        return 0 if result["ok"] else 3
    elif args.action == "export":
        emit(runtime.export(args.session_id, Path(args.output)))
    elif args.action == "import":
        emit(runtime.import_session(Path(args.input), new_session_id=args.session_id))
    elif args.action == "close":
        emit(runtime.close(args.session_id))
    else:
        raise ValueError(args.action)
    return 0


def command_output(args: argparse.Namespace) -> int:
    governor = OutputGovernor(args.profile)
    if args.action == "profiles":
        emit({"profiles": {name: asdict(value) for name, value in PROFILES.items()}, "contracts": CONTRACTS})
    elif args.action == "govern":
        payload = json.loads(Path(args.input).read_text(encoding="utf-8")) if args.input else json.loads(args.payload)
        emit(governor.render(payload, contract=args.contract))
    elif args.action == "compact":
        text = Path(args.input).read_text(encoding="utf-8") if args.input else (args.text if args.text is not None else sys.stdin.read())
        emit(governor.compact_text(text))
    return 0


def command_benchmark(args: argparse.Namespace) -> int:
    if args.action == "generate-config":
        config = write_config(Path(args.output), args.tier)
        emit({"config": config, "validation": validate_config(config)})
    elif args.action == "generate-repo":
        emit(generate_synthetic_repository(Path(args.output), files=args.files, depth=args.depth, fanout=args.fanout, faults=args.faults))
    elif args.action == "validate-config":
        result = validate_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
        emit(result)
        return 0 if result["ok"] else 3
    elif args.action == "compare":
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        result = compare_results(load_arm_results(Path(args.baseline)), load_arm_results(Path(args.syntavra)), tier=args.tier, config=config)
        if args.output:
            atomic_write_json(Path(args.output), result, mode=0o644)
        emit(result)
        return 0 if result["claim"]["status"] == "PASS" else 3
    else:
        raise ValueError(args.action)
    return 0


def command_signalbench(args: argparse.Namespace) -> int:
    runner = SignalBenchRunner(Path(args.output_root), seed=args.seed)
    if args.action == "validate":
        result = runner.validate(runner.load_tasks(Path(args.tasks)), runner.load_arms(Path(args.arms)))
        emit(result)
        return 0 if result["ok"] else 3
    if args.action == "manifest":
        emit(runner.write_manifest(Path(args.output), runner.load_tasks(Path(args.tasks)), runner.load_arms(Path(args.arms))))
        return 0
    if args.action == "run":
        result = runner.run(
            runner.load_tasks(Path(args.tasks)), runner.load_arms(Path(args.arms)),
            repetitions=args.repetitions, cache_modes=tuple(args.cache_mode), randomized=not args.no_randomize,
        )
        emit({"result_hash": result["result_hash"], "runs": len(result["results"]), "output": str(Path(args.output_root) / "results.json")})
        return 0
    if args.action == "compare":
        result = runner.compare(load_results(Path(args.results)), baseline_arm=args.baseline_arm, candidate_arm=args.candidate_arm)
        if args.output:
            atomic_write_json(Path(args.output), result, mode=0o644)
        emit(result)
        return 0 if result["claimable_superiority"] else 3
    raise ValueError(args.action)


def command_claim(args: argparse.Namespace) -> int:
    result = verify_claim(Path(args.receipt))
    emit(result)
    return 0 if result["ok"] else 3


def command_verifier(args: argparse.Namespace) -> int:
    graph = VerifierGraph(state_root(args) / "verifier.sqlite3")
    if args.action == "lookup":
        result = graph.lookup(args.command, tree_hash=args.tree_hash, environment_hash=args.environment_hash, dependency_hash=args.dependency_hash, toolchain_hash=args.toolchain_hash)
        emit(result if result else {"hit": False})
    elif args.action == "invalidated-by":
        emit({"invalidated": graph.invalidated_by(args.path)})
    else:
        raise ValueError(args.action)
    return 0


def _add_sandbox_policy(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=("auto", "docker", "podman", "bwrap", "local-restricted"), default="auto")
    parser.add_argument("--network", choices=("none", "inherit"), default="none")
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--cpus", type=float, default=2)
    parser.add_argument("--pids", type=int, default=256)
    parser.add_argument("--writable", action="append", default=[])
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument("--cwd", default=".")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syntavra", description="Syntavra 0.0.1 unified runtime control plane")
    parser.add_argument("--project", default=".")
    parser.add_argument("--state-root")
    parser.add_argument("--skill-root", default=str(skill_root()))
    parser.add_argument("--codex-home")
    parser.add_argument("--host", default="codex")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    init = sub.add_parser("init")
    init.add_argument("task")
    init.set_defaults(func=command_init)

    status = sub.add_parser("status")
    status.add_argument("--require-rollout", action="store_true")
    status.set_defaults(func=command_status)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--require-rollout", action="store_true")
    doctor.add_argument("--home")
    doctor.set_defaults(func=command_doctor)

    install = sub.add_parser("install")
    install.add_argument("--host-name", action="append", default=[])
    install.add_argument("--all", action="store_true")
    install.add_argument("--scope", choices=("project", "user"), default="project")
    install.add_argument("--home")
    install.add_argument("--dry-run", action="store_true")
    install.set_defaults(func=command_install)
    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--home")
    uninstall.add_argument("--dry-run", action="store_true")
    uninstall.set_defaults(func=command_uninstall)

    run = sub.add_parser("run")
    run.add_argument("--timeout", type=float, default=1200)
    run.add_argument("--heartbeat", type=float, default=1)
    run.add_argument("--background", action="store_true")
    run.add_argument("--repository-tree", default="unknown")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=command_run)

    jobs = sub.add_parser("job")
    job_sub = jobs.add_subparsers(dest="action", required=True)
    jl = job_sub.add_parser("list"); jl.add_argument("--state", action="append", default=[]); jl.add_argument("--limit", type=int, default=100)
    js = job_sub.add_parser("show"); js.add_argument("job_id")
    jc = job_sub.add_parser("cancel"); jc.add_argument("job_id")
    job_sub.add_parser("recover")
    jcomp = job_sub.add_parser("completions"); jcomp.add_argument("--after", type=int, default=0); jcomp.add_argument("--limit", type=int, default=100)
    jobs.set_defaults(func=command_job)

    rollout = sub.add_parser("rollout-tail")
    rollout.add_argument("--rollout"); rollout.add_argument("--state-file"); rollout.add_argument("--session-hint")
    rollout.set_defaults(func=command_rollout)

    evidence = sub.add_parser("evidence")
    es = evidence.add_subparsers(dest="action", required=True)
    eg = es.add_parser("get"); eg.add_argument("handle"); eg.add_argument("--max-bytes", type=int); eg.add_argument("--output")
    ed = es.add_parser("describe"); ed.add_argument("handle")
    evidence.set_defaults(func=command_evidence)

    memory = sub.add_parser("memory")
    ms = memory.add_subparsers(dest="action", required=True)
    ma = ms.add_parser("add"); ma.add_argument("memory_class"); ma.add_argument("text"); ma.add_argument("--confidence", type=float, default=1); ma.add_argument("--source", default="user"); ma.add_argument("--expires-at", type=float); ma.add_argument("--tag", action="append", default=[]); ma.add_argument("--user-id", default="default")
    mq = ms.add_parser("search"); mq.add_argument("query"); mq.add_argument("--limit", type=int, default=10); mq.add_argument("--memory-class", action="append", default=[]); mq.add_argument("--include-superseded", action="store_true"); mq.add_argument("--include-expired", action="store_true"); mq.add_argument("--user-id", default="default")
    ml = ms.add_parser("link"); ml.add_argument("source_id"); ml.add_argument("relation"); ml.add_argument("target_id"); ml.add_argument("--weight", type=float, default=1); ml.add_argument("--user-id", default="default")
    mn = ms.add_parser("neighbors"); mn.add_argument("memory_id"); mn.add_argument("--relation"); mn.add_argument("--limit", type=int, default=50); mn.add_argument("--user-id", default="default")
    memory.set_defaults(func=command_memory)

    inspect = sub.add_parser("inspect")
    ins = inspect.add_subparsers(dest="action", required=True)
    sym = ins.add_parser("symbol"); sym.add_argument("query"); sym.add_argument("--limit", type=int, default=20)
    imp = ins.add_parser("impact"); imp.add_argument("query"); imp.add_argument("--max-depth", type=int, default=4)
    paths = ins.add_parser("paths"); paths.add_argument("path", nargs="+", action="extend"); paths.add_argument("--max-depth", type=int, default=4)
    repo_map = ins.add_parser("map"); repo_map.add_argument("query"); repo_map.add_argument("--token-budget", type=int, default=2000); repo_map.add_argument("--max-depth", type=int, default=4)
    ins.add_parser("stats")
    inspect.set_defaults(func=command_structural)

    context = sub.add_parser("context")
    context_sub = context.add_subparsers(dest="context_action")
    ce = context_sub.add_parser("evaluate"); ce.add_argument("--used", type=int, required=True); ce.add_argument("--window", type=int, required=True); ce.add_argument("--churn", type=float, default=0); ce.add_argument("--evidence-pressure", type=float, default=0)
    cp = context_sub.add_parser("pack"); cp.add_argument("--input", required=True); cp.add_argument("--budget", type=int, required=True); cp.add_argument("--mandatory-role", action="append", default=[])
    context.set_defaults(func=command_context)

    host = sub.add_parser("host")
    hs = host.add_subparsers(dest="host_action")
    hn = hs.add_parser("negotiate"); hn.add_argument("host_name", nargs="?"); hn.add_argument("--runtime-unavailable", action="store_true")
    hd = hs.add_parser("detect"); hd.add_argument("--home")
    hs.add_parser("capabilities")
    host.set_defaults(func=command_host, host_action="negotiate", host_name=None, runtime_unavailable=False)

    hook = sub.add_parser("hook")
    hook.add_argument("phase", choices=("pre", "post", "session-start", "prompt", "pre-compact", "post-compact", "stop", "session-end"))
    hook.add_argument("--payload")
    hook.set_defaults(func=command_hook)

    mcp = sub.add_parser("mcp"); mcp.add_argument("action", choices=("serve",), default="serve"); mcp.set_defaults(func=command_mcp)

    sandbox = sub.add_parser("sandbox")
    ss = sandbox.add_subparsers(dest="action", required=True)
    ss.add_parser("backends")
    for action in ("plan", "execute"):
        item = ss.add_parser(action); _add_sandbox_policy(item); item.add_argument("command", nargs=argparse.REMAINDER)
    sandbox.set_defaults(func=command_sandbox)

    compression = sub.add_parser("compress")
    cs = compression.add_subparsers(dest="action", required=True)
    cput = cs.add_parser("put"); cput.add_argument("--input"); cput.add_argument("--text"); cput.add_argument("--hint", default=""); cput.add_argument("--path", default=""); cput.add_argument("--budget-bytes", type=int, default=8192)
    cget = cs.add_parser("get"); cget.add_argument("compression_id"); cget.add_argument("--chunk", type=int); cget.add_argument("--output")
    cdesc = cs.add_parser("describe"); cdesc.add_argument("compression_id")
    cver = cs.add_parser("verify"); cver.add_argument("compression_id")
    compression.set_defaults(func=command_compression)

    session = sub.add_parser("session")
    ses = session.add_subparsers(dest="action", required=True)
    so = ses.add_parser("open"); so.add_argument("--session-id"); so.add_argument("--parent", action="append", default=[]); so.add_argument("--task")
    sl = ses.add_parser("list"); sl.add_argument("--state")
    sa = ses.add_parser("append"); sa.add_argument("session_id"); sa.add_argument("event_type"); sa.add_argument("payload")
    sc = ses.add_parser("context"); sc.add_argument("session_id"); sc.add_argument("--token-budget", type=int, default=32000); sc.add_argument("--recent-events", type=int, default=24)
    scomp = ses.add_parser("compact"); scomp.add_argument("session_id"); scomp.add_argument("--leaf-size", type=int, default=32); scomp.add_argument("--fanout", type=int, default=8); scomp.add_argument("--force", action="store_true")
    sch = ses.add_parser("checkpoint"); sch.add_argument("session_id"); sch.add_argument("--label")
    sf = ses.add_parser("fork"); sf.add_argument("session_id"); sf.add_argument("--label")
    sm = ses.add_parser("merge"); sm.add_argument("session_id", nargs="+"); sm.add_argument("--label")
    sv = ses.add_parser("verify"); sv.add_argument("session_id")
    ses.add_parser("recover")
    se = ses.add_parser("export"); se.add_argument("session_id"); se.add_argument("--output", required=True)
    si = ses.add_parser("import"); si.add_argument("--input", required=True); si.add_argument("--session-id")
    scl = ses.add_parser("close"); scl.add_argument("session_id")
    session.set_defaults(func=command_session)

    output = sub.add_parser("output")
    osub = output.add_subparsers(dest="action", required=True)
    osub.add_parser("profiles")
    og = osub.add_parser("govern"); og.add_argument("--profile", choices=tuple(PROFILES), default="balanced"); og.add_argument("--contract", choices=tuple(CONTRACTS), default="generic"); og.add_argument("--input"); og.add_argument("--payload", default="{}")
    oc = osub.add_parser("compact"); oc.add_argument("--profile", choices=tuple(PROFILES), default="compact"); oc.add_argument("--input"); oc.add_argument("--text")
    output.set_defaults(func=command_output, profile="balanced")

    benchmark = sub.add_parser("benchmark")
    bs = benchmark.add_subparsers(dest="action", required=True)
    bg = bs.add_parser("generate-config"); bg.add_argument("--tier", choices=("1X", "20X", "30X", "100X"), required=True); bg.add_argument("--output", required=True)
    br = bs.add_parser("generate-repo"); br.add_argument("--output", required=True); br.add_argument("--files", type=int, default=50); br.add_argument("--depth", type=int, default=5); br.add_argument("--fanout", type=int, default=3); br.add_argument("--faults", type=int, default=1)
    bv = bs.add_parser("validate-config"); bv.add_argument("--config", required=True)
    bc = bs.add_parser("compare"); bc.add_argument("--baseline", required=True); bc.add_argument("--syntavra", required=True); bc.add_argument("--config", required=True); bc.add_argument("--tier", required=True); bc.add_argument("--output")
    benchmark.set_defaults(func=command_benchmark)

    signalbench = sub.add_parser("signalbench")
    sbs = signalbench.add_subparsers(dest="action", required=True)
    for name in ("validate", "manifest", "run"):
        item = sbs.add_parser(name); item.add_argument("--tasks", required=True); item.add_argument("--arms", required=True); item.add_argument("--output-root", default="signalbench-results"); item.add_argument("--seed", type=int, default=1337)
        if name == "manifest": item.add_argument("--output", required=True)
        if name == "run": item.add_argument("--repetitions", type=int, default=3); item.add_argument("--cache-mode", action="append", default=["cold", "warm"]); item.add_argument("--no-randomize", action="store_true")
    sbc = sbs.add_parser("compare"); sbc.add_argument("--results", required=True); sbc.add_argument("--baseline-arm", required=True); sbc.add_argument("--candidate-arm", required=True); sbc.add_argument("--output"); sbc.add_argument("--output-root", default="signalbench-results"); sbc.add_argument("--seed", type=int, default=1337)
    signalbench.set_defaults(func=command_signalbench)

    claim = sub.add_parser("claim"); claim.add_argument("receipt"); claim.set_defaults(func=command_claim)
    verifier = sub.add_parser("verifier")
    vs = verifier.add_subparsers(dest="action", required=True)
    vl = vs.add_parser("lookup"); vl.add_argument("command", nargs="+"); vl.add_argument("--tree-hash", required=True); vl.add_argument("--environment-hash", required=True); vl.add_argument("--dependency-hash", required=True); vl.add_argument("--toolchain-hash", required=True)
    vi = vs.add_parser("invalidated-by"); vi.add_argument("--path", action="append", required=True)
    verifier.set_defaults(func=command_verifier)
    add_competitive_commands(sub, catalog=MCPServer.tools)
    return parser


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if "context" in values:
        index = values.index("context")
        if index + 1 < len(values) and values[index + 1].startswith("--"):
            values.insert(index + 1, "evaluate")
    args = build_parser().parse_args(values)
    return int(args.func(args))
