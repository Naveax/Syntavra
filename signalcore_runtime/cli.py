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
from .context_governor import evaluate, pack_context
from .evidence import EvidenceStore
from .hooks import HookEngine, run_hook
from .host_adapters import negotiate
from .mcp_server import MCPServer
from .memory import PersistentMemory
from .models import ContextItem
from .process_broker import ProcessBroker
from .rollout import RolloutTailer, discover_rollouts, select_active_rollout
from .status import inspect_runtime
from .structural import StructuralIndex
from .util import atomic_write_json, stable_project_id
from .verifier_graph import VerifierGraph

VERSION = "0.2.0"


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
    return root() / "skills" / "signal-core"


def project(args: argparse.Namespace, *, strict: bool = True) -> Path:
    return Path(args.project).resolve(strict=strict)


def state_root(args: argparse.Namespace) -> Path:
    return Path(args.state_root).resolve(strict=False) if args.state_root else project(args, strict=False) / ".signalcore" / "runtime-v2"


def evidence_store(args: argparse.Namespace) -> EvidenceStore:
    return EvidenceStore(state_root(args) / "evidence", project_id=stable_project_id(project(args)))


def broker(args: argparse.Namespace) -> ProcessBroker:
    return ProcessBroker(state_root(args) / "broker", evidence_store(args), heartbeat_interval=getattr(args, "heartbeat", 1.0))


def command_init(args):
    emit(start_runtime(
        args.task,
        project=project(args),
        skill_root=Path(args.skill_root),
        state_root=state_root(args),
        codex_home=Path(args.codex_home) if args.codex_home else None,
        host=args.host,
    ))
    return 0


def command_status(args):
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


def command_run(args):
    argv = tuple(args.command[1:] if args.command and args.command[0] == "--" else args.command)
    if not argv:
        raise SystemExit("signalcore run requires argv after --")
    active = broker(args)
    if args.background:
        job = active.submit(argv, cwd=project(args), timeout=args.timeout, repository_tree=args.repository_tree)
        emit({
            "event": "JOB_ACCEPTED",
            "job": job,
            "model_polling_calls": 0,
            "completion_cursor": 0,
            "completion_queue": str(state_root(args) / "broker" / "broker.sqlite3"),
        })
        return 0
    result = active.run(argv, cwd=project(args), timeout=args.timeout, repository_tree=args.repository_tree)
    emit(result)
    return 124 if result.timed_out else 130 if result.cancelled else int(result.exit_code or 0)


def command_job(args):
    active = broker(args)
    if args.action == "list":
        emit({"jobs": active.list_jobs(states=tuple(args.state), limit=args.limit)})
        return 0
    if args.action == "show":
        emit(active.show(args.job_id))
        return 0
    if args.action == "cancel":
        emit(active.cancel(args.job_id))
        return 0
    if args.action == "recover":
        emit({"orphaned": active.recover()})
        return 0
    if args.action == "completions":
        emit(active.drain_completions(after=args.after, limit=args.limit))
        return 0
    raise ValueError(args.action)


def command_rollout(args):
    candidates = discover_rollouts(Path(args.codex_home) if args.codex_home else resolve_codex_home())
    selected = Path(args.rollout).resolve(strict=True) if args.rollout else select_active_rollout(candidates, session_id=args.session_hint)
    if not selected:
        emit({"ok": False, "reason": "no rollout found"})
        return 2
    state = Path(args.state_file) if args.state_file else state_root(args) / "rollout-state.json"
    emit({"rollout": str(selected), **RolloutTailer(selected, state).poll()})
    return 0


def command_evidence(args):
    store = evidence_store(args)
    if args.action == "get":
        data = store.get(args.handle, max_bytes=args.max_bytes)
        if args.output:
            Path(args.output).write_bytes(data)
            emit({"handle": args.handle, "bytes": len(data), "output": args.output})
        else:
            emit({"handle": args.handle, "bytes": len(data), "text": data.decode("utf-8", errors="replace")})
        return 0
    if args.action == "describe":
        emit(store.describe(args.handle))
        return 0
    raise ValueError(args.action)


def _memory(args) -> PersistentMemory:
    return PersistentMemory(state_root(args) / "memory.sqlite3", project_id=stable_project_id(project(args)), user_id=args.user_id)


def command_memory(args):
    memory = _memory(args)
    if args.action == "add":
        emit(memory.add(
            args.memory_class,
            args.text,
            confidence=args.confidence,
            provenance={"source": args.source},
            expires_at=args.expires_at,
            tags=args.tag,
        ))
        return 0
    if args.action == "search":
        emit(memory.search(
            args.query,
            limit=args.limit,
            memory_classes=args.memory_class,
            include_superseded=args.include_superseded,
            include_expired=args.include_expired,
        ))
        return 0
    if args.action == "link":
        memory.link(args.source_id, args.relation, args.target_id, weight=args.weight)
        emit({"ok": True})
        return 0
    if args.action == "neighbors":
        emit({"results": memory.neighbors(args.memory_id, relation=args.relation, limit=args.limit)})
        return 0
    raise ValueError(args.action)


def structural(args):
    index = StructuralIndex(
        state_root(args) / "structural.sqlite3",
        repository_root=project(args),
        repository_id=stable_project_id(project(args)),
    )
    index.index()
    return index


def command_structural(args):
    index = structural(args)
    if args.action == "symbol":
        emit(index.inspect_symbol(args.query, limit=args.limit))
        return 0
    if args.action == "impact":
        emit(index.inspect_impact(args.query, max_depth=args.max_depth))
        return 0
    if args.action == "paths":
        emit(index.impacted_by_paths(args.path, max_depth=args.max_depth))
        return 0
    raise ValueError(args.action)


def command_context(args):
    if args.context_action == "evaluate":
        emit(evaluate(args.used, args.window, churn=args.churn, evidence_pressure=args.evidence_pressure))
        return 0
    if args.context_action == "pack":
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        items = [ContextItem(**row) for row in payload.get("items", [])]
        emit(pack_context(items, budget=args.budget, mandatory_roles=args.mandatory_role))
        return 0
    raise ValueError(args.context_action)


def command_host(args):
    emit(negotiate(args.host, runtime_available=not args.runtime_unavailable))
    return 0


def command_hook(args):
    payload = args.payload if args.payload is not None else sys.stdin.read()
    engine = HookEngine(project_root=project(args))
    print(run_hook(engine, args.phase, payload))
    return 0


def command_mcp(args):
    server = MCPServer(
        project=project(args),
        state_root=state_root(args),
        skill_root=Path(args.skill_root),
        codex_home=Path(args.codex_home) if args.codex_home else resolve_codex_home(),
        host=args.host,
    )
    return server.serve()


def command_benchmark(args):
    if args.action == "generate-config":
        config = write_config(Path(args.output), args.tier)
        emit({"config": config, "validation": validate_config(config)})
        return 0
    if args.action == "generate-repo":
        emit(generate_synthetic_repository(Path(args.output), files=args.files, depth=args.depth, fanout=args.fanout, faults=args.faults))
        return 0
    if args.action == "validate-config":
        result = validate_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
        emit(result)
        return 0 if result["ok"] else 3
    if args.action == "compare":
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        result = compare_results(
            load_arm_results(Path(args.baseline)),
            load_arm_results(Path(args.signalcore)),
            tier=args.tier,
            config=config,
        )
        if args.output:
            atomic_write_json(Path(args.output), result, mode=0o644)
        emit(result)
        return 0 if result["claim"]["status"] == "PASS" else 3
    raise ValueError(args.action)


def command_claim(args):
    result = verify_claim(Path(args.receipt))
    emit(result)
    return 0 if result["ok"] else 3


def command_verifier(args):
    graph = VerifierGraph(state_root(args) / "verifier.sqlite3")
    if args.action == "lookup":
        result = graph.lookup(
            args.command,
            tree_hash=args.tree_hash,
            environment_hash=args.environment_hash,
            dependency_hash=args.dependency_hash,
            toolchain_hash=args.toolchain_hash,
        )
        emit(result if result else {"hit": False})
        return 0
    if args.action == "invalidated-by":
        emit({"invalidated": graph.invalidated_by(args.path)})
        return 0
    raise ValueError(args.action)


def build_parser():
    parser = argparse.ArgumentParser(prog="signalcore", description="SignalCore 0.2.0 runtime control plane")
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
    for name in ("status", "doctor"):
        item = sub.add_parser(name)
        item.add_argument("--require-rollout", action="store_true")
        item.set_defaults(func=command_status)

    run = sub.add_parser("run")
    run.add_argument("--timeout", type=float, default=1200)
    run.add_argument("--heartbeat", type=float, default=1)
    run.add_argument("--background", action="store_true")
    run.add_argument("--repository-tree", default="unknown")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=command_run)

    jobs = sub.add_parser("job")
    job_sub = jobs.add_subparsers(dest="action", required=True)
    jl = job_sub.add_parser("list")
    jl.add_argument("--state", action="append", default=[])
    jl.add_argument("--limit", type=int, default=100)
    js = job_sub.add_parser("show")
    js.add_argument("job_id")
    jc = job_sub.add_parser("cancel")
    jc.add_argument("job_id")
    job_sub.add_parser("recover")
    jcomp = job_sub.add_parser("completions")
    jcomp.add_argument("--after", type=int, default=0)
    jcomp.add_argument("--limit", type=int, default=100)
    jobs.set_defaults(func=command_job)

    rollout = sub.add_parser("rollout-tail")
    rollout.add_argument("--rollout")
    rollout.add_argument("--state-file")
    rollout.add_argument("--session-hint")
    rollout.set_defaults(func=command_rollout)

    evidence = sub.add_parser("evidence")
    es = evidence.add_subparsers(dest="action", required=True)
    eg = es.add_parser("get")
    eg.add_argument("handle")
    eg.add_argument("--max-bytes", type=int)
    eg.add_argument("--output")
    ed = es.add_parser("describe")
    ed.add_argument("handle")
    evidence.set_defaults(func=command_evidence)

    memory = sub.add_parser("memory")
    ms = memory.add_subparsers(dest="action", required=True)
    ma = ms.add_parser("add")
    ma.add_argument("memory_class")
    ma.add_argument("text")
    ma.add_argument("--confidence", type=float, default=1)
    ma.add_argument("--source", default="user")
    ma.add_argument("--expires-at", type=float)
    ma.add_argument("--tag", action="append", default=[])
    ma.add_argument("--user-id", default="default")
    mq = ms.add_parser("search")
    mq.add_argument("query")
    mq.add_argument("--limit", type=int, default=10)
    mq.add_argument("--memory-class", action="append", default=[])
    mq.add_argument("--include-superseded", action="store_true")
    mq.add_argument("--include-expired", action="store_true")
    mq.add_argument("--user-id", default="default")
    ml = ms.add_parser("link")
    ml.add_argument("source_id")
    ml.add_argument("relation")
    ml.add_argument("target_id")
    ml.add_argument("--weight", type=float, default=1.0)
    ml.add_argument("--user-id", default="default")
    mn = ms.add_parser("neighbors")
    mn.add_argument("memory_id")
    mn.add_argument("--relation")
    mn.add_argument("--limit", type=int, default=50)
    mn.add_argument("--user-id", default="default")
    memory.set_defaults(func=command_memory)

    inspect = sub.add_parser("inspect")
    ins = inspect.add_subparsers(dest="action", required=True)
    sym = ins.add_parser("symbol")
    sym.add_argument("query")
    sym.add_argument("--limit", type=int, default=20)
    imp = ins.add_parser("impact")
    imp.add_argument("query")
    imp.add_argument("--max-depth", type=int, default=3)
    paths = ins.add_parser("paths")
    paths.add_argument("path", nargs="+", action="extend")
    paths.add_argument("--max-depth", type=int, default=3)
    inspect.set_defaults(func=command_structural)

    context = sub.add_parser("context")
    context_sub = context.add_subparsers(dest="context_action")
    ce = context_sub.add_parser("evaluate")
    ce.add_argument("--used", type=int, required=True)
    ce.add_argument("--window", type=int, required=True)
    ce.add_argument("--churn", type=float, default=0)
    ce.add_argument("--evidence-pressure", type=float, default=0)
    cp = context_sub.add_parser("pack")
    cp.add_argument("--input", required=True)
    cp.add_argument("--budget", type=int, required=True)
    cp.add_argument("--mandatory-role", action="append", default=[])
    # Backward-compatible direct form: `context --used ...` is normalized in main().
    context.set_defaults(func=command_context)

    host = sub.add_parser("host")
    host.add_argument("--runtime-unavailable", action="store_true")
    host.set_defaults(func=command_host)

    hook = sub.add_parser("hook")
    hook.add_argument("phase", choices=("pre", "post"))
    hook.add_argument("--payload")
    hook.set_defaults(func=command_hook)

    mcp = sub.add_parser("mcp")
    mcp.add_argument("action", choices=("serve",), default="serve")
    mcp.set_defaults(func=command_mcp)

    benchmark = sub.add_parser("benchmark")
    bs = benchmark.add_subparsers(dest="action", required=True)
    bg = bs.add_parser("generate-config")
    bg.add_argument("--tier", choices=("1X", "20X", "30X", "100X"), required=True)
    bg.add_argument("--output", required=True)
    br = bs.add_parser("generate-repo")
    br.add_argument("--output", required=True)
    br.add_argument("--files", type=int, default=50)
    br.add_argument("--depth", type=int, default=5)
    br.add_argument("--fanout", type=int, default=3)
    br.add_argument("--faults", type=int, default=1)
    bv = bs.add_parser("validate-config")
    bv.add_argument("--config", required=True)
    bc = bs.add_parser("compare")
    bc.add_argument("--baseline", required=True)
    bc.add_argument("--signalcore", required=True)
    bc.add_argument("--config", required=True)
    bc.add_argument("--tier", required=True)
    bc.add_argument("--output")
    benchmark.set_defaults(func=command_benchmark)

    claim = sub.add_parser("claim")
    claim.add_argument("receipt")
    claim.set_defaults(func=command_claim)

    verifier = sub.add_parser("verifier")
    vs = verifier.add_subparsers(dest="action", required=True)
    vl = vs.add_parser("lookup")
    vl.add_argument("command", nargs="+")
    vl.add_argument("--tree-hash", required=True)
    vl.add_argument("--environment-hash", required=True)
    vl.add_argument("--dependency-hash", required=True)
    vl.add_argument("--toolchain-hash", required=True)
    vi = vs.add_parser("invalidated-by")
    vi.add_argument("--path", action="append", required=True)
    verifier.set_defaults(func=command_verifier)
    return parser


def main(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    # Preserve the 0.1.0 CLI shape: `context --used 60 --window 100`.
    if "context" in values:
        index = values.index("context")
        if index + 1 < len(values) and values[index + 1].startswith("--"):
            values.insert(index + 1, "evaluate")
    args = build_parser().parse_args(values)
    return int(args.func(args))
