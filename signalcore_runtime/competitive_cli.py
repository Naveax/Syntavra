from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from .competitive_fabric import CompetitiveContextFabric
from .evidence import EvidenceStore
from .host_installation import HostInstallationManager
from .provider_gateway import ProviderGateway, ProviderPlan
from .provider_proxy import ProviderProxyRuntime, ProxyConfig
from .usage_receipt_ledger import UsageReceiptLedger
from .util import stable_project_id


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _emit(value: Any) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _project(args: argparse.Namespace) -> Path:
    return Path(args.project).resolve(strict=True)


def _state_root(args: argparse.Namespace) -> Path:
    if args.state_root:
        return Path(args.state_root).resolve(strict=False)
    return _project(args) / ".signalcore" / "runtime-v3"


def _skill_root(args: argparse.Namespace) -> Path:
    configured = getattr(args, "skill_root", None)
    if configured:
        return Path(configured).resolve(strict=True)
    bundled = Path(__file__).resolve().parent.parent / "skills" / "signal-core"
    return bundled.resolve(strict=True)


def _read_text(path: str | None, *, fallback: str = "") -> str:
    if not path:
        return fallback
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _read_json(path: str | None, *, inline: str = "") -> Any:
    raw = _read_text(path, fallback=inline)
    if not raw.strip():
        return {}
    return json.loads(raw)


def _write_json(path: str | None, value: Any) -> None:
    if not path:
        _emit(value)
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    _emit({"ok": True, "output": str(target), "bytes": target.stat().st_size})


def _argv(values: list[str]) -> tuple[str, ...]:
    result = list(values)
    if result and result[0] == "--":
        result = result[1:]
    if not result:
        raise SystemExit("command argv is required after --")
    return tuple(result)


def _fabric(args: argparse.Namespace) -> CompetitiveContextFabric:
    return CompetitiveContextFabric(
        _state_root(args) / "competitive-fabric.sqlite3",
        project=_project(args),
        host=str(args.host),
    )


def _gateway(args: argparse.Namespace) -> ProviderGateway:
    project = _project(args)
    state = _state_root(args)
    evidence = EvidenceStore(state / "evidence", project_id=stable_project_id(project))
    usage = UsageReceiptLedger(state / "usage-receipts.sqlite3")
    return ProviderGateway(state / "provider-gateway.sqlite3", evidence=evidence, usage_ledger=usage)


def _installer(args: argparse.Namespace) -> HostInstallationManager:
    return HostInstallationManager(
        _state_root(args) / "host-installations.sqlite3",
        project=_project(args),
        skill_root=_skill_root(args),
        home=Path(args.home).resolve(strict=False) if getattr(args, "home", None) else None,
    )


def command_fabric(args: argparse.Namespace) -> int:
    fabric = _fabric(args)
    if args.fabric_action == "profile":
        available = [row["name"] for row in args.catalog()] if callable(args.catalog) else []
        result = fabric.profile(args.task, available, requested_profile=args.profile)
    elif args.fabric_action == "route":
        result = fabric.route(
            _argv(args.command),
            network_untrusted=args.network_untrusted,
            repeated=args.repeated,
        )
    elif args.fabric_action == "compact":
        stdout = _read_text(args.stdout_file, fallback=args.stdout or "")
        stderr = _read_text(args.stderr_file, fallback=args.stderr or "")
        result = fabric.compact(
            _argv(args.command),
            stdout,
            stderr,
            budget_bytes=args.budget_bytes,
        )
    elif args.fabric_action == "cache-align":
        payload = _read_json(args.input, inline=args.payload)
        messages = payload.get("messages") if isinstance(payload, Mapping) else payload
        if not isinstance(messages, list):
            raise TypeError("input must be a message list or an object containing messages")
        result = fabric.align_cache(messages, keep_tail=args.keep_tail)
    elif args.fabric_action == "platform-plan":
        if args.all:
            result = fabric.platforms.all_plans(project=_project(args), scope=args.scope)
        else:
            result = fabric.platforms.plan(args.host_name or args.host, project=_project(args), scope=args.scope)
    elif args.fabric_action == "install":
        result = _installer(args).apply(args.host_name, scope=args.scope, dry_run=args.dry_run)
    elif args.fabric_action == "verify-install":
        result = _installer(args).verify(args.host_name, scope=args.scope)
        _write_json(args.output, result)
        return 0 if result["ok"] else 3
    elif args.fabric_action == "rollback-install":
        result = _installer(args).rollback(args.transaction_id)
    elif args.fabric_action == "installations":
        result = _installer(args).transactions(host=args.host_name or "", limit=args.limit)
    elif args.fabric_action == "doctor":
        result = fabric.doctor()
    elif args.fabric_action == "insights":
        result = fabric.insights(since_seconds=args.since_seconds)
    else:
        raise ValueError(args.fabric_action)
    _write_json(args.output, result)
    return 0


def command_provider(args: argparse.Namespace) -> int:
    gateway = _gateway(args)
    if args.provider_action == "capabilities":
        result = gateway.capabilities(args.provider)
    elif args.provider_action == "prepare":
        request = _read_json(args.input, inline=args.request)
        if not isinstance(request, Mapping):
            raise TypeError("provider request must be a JSON object")
        result = gateway.prepare(
            args.provider,
            request,
            model=args.model,
            cache_policy=args.cache_policy,
            replay_ttl_seconds=args.replay_ttl_seconds,
            prompt_cache_ttl_seconds=args.prompt_cache_ttl_seconds,
            explicit_cache_name=args.explicit_cache_name,
            allow_tool_replay=args.allow_tool_replay,
        )
    elif args.provider_action == "capture":
        plan_data = _read_json(args.plan)
        response = _read_json(args.response)
        receipt = _read_json(args.receipt) if args.receipt else None
        if not isinstance(plan_data, Mapping) or not isinstance(response, Mapping):
            raise TypeError("plan and response must be JSON objects")
        result = gateway.capture(
            ProviderPlan(**dict(plan_data)),
            response,
            store_replay=not args.no_replay,
            replay_ttl_seconds=args.replay_ttl_seconds,
            preview_bytes=args.preview_bytes,
            receipt=receipt,
        )
    elif args.provider_action == "replay":
        if args.plan:
            plan_data = _read_json(args.plan)
            if not isinstance(plan_data, Mapping):
                raise TypeError("plan must be a JSON object")
            target: ProviderPlan | str = ProviderPlan(**dict(plan_data))
        else:
            target = args.cache_key
        result = gateway.replay(target)
        if result is None:
            _write_json(args.output, {"hit": False})
            return 4
    elif args.provider_action == "proxy":
        config = ProxyConfig(
            provider=args.provider,
            upstream_base=args.upstream,
            listen_host=args.listen_host,
            listen_port=args.listen_port,
            credential_env=args.credential_env,
            credential_header=args.credential_header,
            credential_prefix=args.credential_prefix,
            control_token_env=args.control_token_env,
            allow_remote=args.allow_remote,
            allow_insecure_upstream=args.allow_insecure_upstream,
            cache_policy=args.cache_policy,
            replay_ttl_seconds=args.replay_ttl_seconds,
            prompt_cache_ttl_seconds=args.prompt_cache_ttl_seconds,
            timeout_seconds=args.timeout_seconds,
            max_request_bytes=args.max_request_bytes,
            max_buffered_response_bytes=args.max_response_bytes,
        )
        config.validate()
        if args.dry_run:
            result = {"ok": True, "config": asdict(config)}
        else:
            if args.output:
                raise ValueError("--output is supported only with --dry-run for a long-running proxy")
            runtime = ProviderProxyRuntime(
                config,
                gateway=gateway,
                insight_path=_state_root(args) / "provider-proxy-insights.sqlite3",
            )
            host, port = runtime.start()
            _emit({
                "event": "PROVIDER_PROXY_READY",
                "provider": ProviderGateway.capabilities(args.provider)["provider"],
                "listen": {"host": host, "port": port},
                "upstream_origin_hash": runtime.status()["upstream_origin_hash"],
                "cache_policy": args.cache_policy,
            })
            sys.stdout.flush()
            try:
                runtime.wait()
            finally:
                runtime.shutdown()
            return 0
    elif args.provider_action == "stats":
        result = gateway.stats()
    elif args.provider_action == "verify":
        result = gateway.verify()
        _write_json(args.output, result)
        return 0 if result["ok"] else 3
    else:
        raise ValueError(args.provider_action)
    _write_json(args.output, result)
    return 0


def add_competitive_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    catalog: Any,
) -> None:
    fabric = subparsers.add_parser(
        "fabric",
        help="Unified routing, compaction, cache, platform, installation, and analytics control plane",
    )
    fs = fabric.add_subparsers(dest="fabric_action", required=True)

    profile = fs.add_parser("profile", help="Select a task-conditioned MCP tool surface")
    profile.add_argument("--task", required=True)
    profile.add_argument("--profile", choices=("auto", "tiny", "optimized", "full"), default="auto")
    profile.add_argument("--output")

    route = fs.add_parser("route", help="Classify and safely route a shell command")
    route.add_argument("--network-untrusted", action="store_true")
    route.add_argument("--repeated", action="store_true")
    route.add_argument("--output")
    route.add_argument("command", nargs=argparse.REMAINDER)

    compact = fs.add_parser("compact", help="Compact command output while retaining exact evidence signals")
    compact.add_argument("--stdout-file")
    compact.add_argument("--stderr-file")
    compact.add_argument("--stdout")
    compact.add_argument("--stderr")
    compact.add_argument("--budget-bytes", type=int, default=4096)
    compact.add_argument("--output")
    compact.add_argument("command", nargs=argparse.REMAINDER)

    align = fs.add_parser("cache-align", help="Build a stable request-prefix fingerprint")
    align.add_argument("--input")
    align.add_argument("--payload", default="")
    align.add_argument("--keep-tail", type=int, default=1)
    align.add_argument("--output")

    platform = fs.add_parser("platform-plan", help="Generate host installation and enforcement plans")
    platform.add_argument("--host-name")
    platform.add_argument("--all", action="store_true")
    platform.add_argument("--scope", choices=("project", "user"), default="project")
    platform.add_argument("--output")

    install = fs.add_parser("install", help="Atomically install SignalCore into a host")
    install.add_argument("host_name")
    install.add_argument("--scope", choices=("project", "user"), default="project")
    install.add_argument("--skill-root")
    install.add_argument("--home")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--output")

    verify_install = fs.add_parser("verify-install", help="Verify an installed host integration")
    verify_install.add_argument("host_name")
    verify_install.add_argument("--scope", choices=("project", "user"), default="project")
    verify_install.add_argument("--skill-root")
    verify_install.add_argument("--home")
    verify_install.add_argument("--output")

    rollback_install = fs.add_parser("rollback-install", help="Rollback one host installation transaction")
    rollback_install.add_argument("transaction_id")
    rollback_install.add_argument("--skill-root")
    rollback_install.add_argument("--home")
    rollback_install.add_argument("--output")

    installations = fs.add_parser("installations", help="List auditable host installation transactions")
    installations.add_argument("--host-name")
    installations.add_argument("--limit", type=int, default=20)
    installations.add_argument("--skill-root")
    installations.add_argument("--home")
    installations.add_argument("--output")

    doctor = fs.add_parser("doctor", help="Diagnose competitive runtime coverage")
    doctor.add_argument("--output")

    insights = fs.add_parser("insights", help="Inspect local savings and reliability analytics")
    insights.add_argument("--since-seconds", type=float)
    insights.add_argument("--output")

    fabric.set_defaults(func=command_fabric, catalog=catalog)

    provider = subparsers.add_parser(
        "provider",
        help="Provider-neutral prompt-cache, exact capture, replay, proxy, and usage gateway",
    )
    ps = provider.add_subparsers(dest="provider_action", required=True)

    capabilities = ps.add_parser("capabilities")
    capabilities.add_argument("provider", nargs="?")
    capabilities.add_argument("--output")

    prepare = ps.add_parser("prepare")
    prepare.add_argument("provider")
    prepare.add_argument("--input")
    prepare.add_argument("--request", default="")
    prepare.add_argument("--model", default="")
    prepare.add_argument("--cache-policy", choices=("off", "auto", "read", "read-write"), default="auto")
    prepare.add_argument("--replay-ttl-seconds", type=int, default=900)
    prepare.add_argument("--prompt-cache-ttl-seconds", type=int, default=300)
    prepare.add_argument("--explicit-cache-name", default="")
    prepare.add_argument("--allow-tool-replay", action="store_true")
    prepare.add_argument("--output")

    capture = ps.add_parser("capture")
    capture.add_argument("--plan", required=True)
    capture.add_argument("--response", required=True)
    capture.add_argument("--receipt")
    capture.add_argument("--no-replay", action="store_true")
    capture.add_argument("--replay-ttl-seconds", type=int, default=900)
    capture.add_argument("--preview-bytes", type=int, default=4096)
    capture.add_argument("--output")

    replay = ps.add_parser("replay")
    replay_target = replay.add_mutually_exclusive_group(required=True)
    replay_target.add_argument("--plan")
    replay_target.add_argument("--cache-key")
    replay.add_argument("--output")

    proxy = ps.add_parser("proxy", help="Run a credential-isolated fixed-origin provider reverse proxy")
    proxy.add_argument("--provider", required=True)
    proxy.add_argument("--upstream", required=True)
    proxy.add_argument("--listen-host", default="127.0.0.1")
    proxy.add_argument("--listen-port", type=int, default=8787)
    proxy.add_argument("--credential-env", default="")
    proxy.add_argument("--credential-header", default="")
    proxy.add_argument("--credential-prefix", default="")
    proxy.add_argument("--control-token-env", default="SIGNALCORE_PROXY_CONTROL_TOKEN")
    proxy.add_argument("--allow-remote", action="store_true")
    proxy.add_argument("--allow-insecure-upstream", action="store_true")
    proxy.add_argument("--cache-policy", choices=("off", "auto", "read", "read-write"), default="auto")
    proxy.add_argument("--replay-ttl-seconds", type=int, default=900)
    proxy.add_argument("--prompt-cache-ttl-seconds", type=int, default=300)
    proxy.add_argument("--timeout-seconds", type=float, default=180)
    proxy.add_argument("--max-request-bytes", type=int, default=16 * 1024 * 1024)
    proxy.add_argument("--max-response-bytes", type=int, default=64 * 1024 * 1024)
    proxy.add_argument("--dry-run", action="store_true")
    proxy.add_argument("--output")

    stats = ps.add_parser("stats")
    stats.add_argument("--output")
    verify = ps.add_parser("verify")
    verify.add_argument("--output")

    provider.set_defaults(func=command_provider)
