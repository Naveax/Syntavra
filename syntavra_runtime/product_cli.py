from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .arm_runner import ArmExecutionPolicy, SecureArmRunner
from .data_router import DataRoutePolicy, DataRouter, result_dict
from .evidence import EvidenceStore
from .policy_tuner import AdaptivePolicyTuner, PolicyObservation
from .service_manager import ProviderProxyServiceManager, ServiceSpec
from .util import stable_project_id


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _project(args: argparse.Namespace) -> Path:
    return Path(args.project).resolve(strict=False)


def _state(args: argparse.Namespace) -> Path:
    return Path(args.state_root).resolve(strict=False) if args.state_root else _project(args) / ".syntavra" / "runtime-v3"


def _evidence(args: argparse.Namespace) -> EvidenceStore:
    project = _project(args)
    return EvidenceStore(_state(args) / "evidence", project_id=stable_project_id(project))


def _read_payload(path: str | None, text: str | None) -> Any:
    raw = Path(path).read_text(encoding="utf-8") if path else (text if text is not None else sys.stdin.read())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def command_data(args: argparse.Namespace) -> int:
    router = DataRouter(_evidence(args))
    result = router.route(
        _read_payload(args.input, args.text), hint=args.hint, query=args.query,
        policy=DataRoutePolicy(
            budget_bytes=args.budget_bytes, max_rows=args.max_rows,
            max_columns=args.max_columns, max_depth=args.max_depth,
        ),
    )
    _emit(result_dict(result))
    return 0


def _tuner(args: argparse.Namespace) -> AdaptivePolicyTuner:
    return AdaptivePolicyTuner(_state(args) / "adaptive-policy.sqlite3")


def command_policy(args: argparse.Namespace) -> int:
    tuner = _tuner(args)
    if args.policy_action == "record":
        sequence = tuner.record(PolicyObservation(
            family=args.family, host=args.host, model=args.model,
            raw_bytes=args.raw_bytes, visible_bytes=args.visible_bytes,
            latency_ms=args.latency_ms, success=args.success,
            quality=args.quality, cache_hit=args.cache_hit,
            security_regressions=args.security_regressions,
        ))
        _emit({"ok": True, "sequence": sequence})
        return 0
    if args.policy_action == "recommend":
        recommendation = tuner.recommend(
            args.family, host=args.host, model=args.model,
            minimum_samples=args.minimum_samples, window=args.window,
        )
        _emit(asdict(recommendation))
        return 0
    if args.policy_action == "promote":
        recommendation = tuner.recommend(
            args.family, host=args.host, model=args.model,
            minimum_samples=args.minimum_samples, window=args.window,
        )
        if not recommendation.canary:
            _emit({"ok": False, "reason": "recommendation-not-canary-safe", "recommendation": asdict(recommendation)})
            return 2
        sequence = tuner.stage(recommendation, promote=True)
        _emit({"ok": True, "sequence": sequence, "policy_hash": recommendation.policy_hash})
        return 0
    if args.policy_action == "active":
        _emit(tuner.active(args.family, host=args.host, model=args.model) or {"ok": False, "reason": "no-promoted-policy"})
        return 0
    if args.policy_action == "rollback":
        _emit({"ok": True, "active": tuner.rollback(args.family, host=args.host, model=args.model)})
        return 0
    raise ValueError(args.policy_action)


def _service_spec(args: argparse.Namespace) -> ServiceSpec:
    command = tuple(args.command[1:] if args.command and args.command[0] == "--" else args.command)
    return ServiceSpec(
        name=args.name, command=command,
        environment_file=args.environment_file,
        working_directory=args.working_directory,
        description=args.description,
        restart_seconds=args.restart_seconds,
    )


def command_service(args: argparse.Namespace) -> int:
    manager = ProviderProxyServiceManager(args.home)
    spec = _service_spec(args)
    if args.service_action == "plan":
        _emit(asdict(manager.plan(spec, platform_name=args.platform)))
    elif args.service_action == "install":
        _emit(manager.install(spec, platform_name=args.platform, activate=args.activate, dry_run=args.dry_run))
    elif args.service_action == "verify":
        result = manager.verify(spec, platform_name=args.platform)
        _emit(result)
        return 0 if result["ok"] else 2
    elif args.service_action == "uninstall":
        _emit(manager.uninstall(spec, platform_name=args.platform, deactivate=not args.no_deactivate, dry_run=args.dry_run))
    else:
        raise ValueError(args.service_action)
    return 0


def command_arm(args: argparse.Namespace) -> int:
    if args.arm_action == "validate":
        value = json.loads(Path(args.result).read_text(encoding="utf-8"))
        valid, receipt_valid, reasons = SecureArmRunner.validate_result(
            value, pair_key=args.pair_key, arm_id=args.arm_id, require_receipt=not args.allow_missing_receipt,
        )
        _emit({"ok": valid, "provider_receipt_valid": receipt_valid, "reasons": reasons})
        return 0 if valid else 2
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    command = tuple(args.command[1:] if args.command and args.command[0] == "--" else args.command)
    runner = SecureArmRunner(_state(args) / "arm-runs", evidence=_evidence(args))
    receipt = runner.run(
        arm_id=args.arm_id, pair_key=args.pair_key, argv=command,
        workspace=Path(args.workspace), request=request,
        policy=ArmExecutionPolicy(
            timeout_seconds=args.timeout,
            max_artifact_bytes=args.max_artifact_bytes,
            max_visible_bytes=args.max_visible_bytes,
            require_receipt=not args.allow_missing_receipt,
        ),
    )
    _emit(asdict(receipt))
    return 0 if receipt.success else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="syntavra-product")
    parser.add_argument("--project", default=".")
    parser.add_argument("--state-root")
    sub = parser.add_subparsers(dest="command_name", required=True)

    data = sub.add_parser("data-route")
    data.add_argument("--input"); data.add_argument("--text")
    data.add_argument("--hint", default=""); data.add_argument("--query", default="")
    data.add_argument("--budget-bytes", type=int, default=8192)
    data.add_argument("--max-rows", type=int, default=8)
    data.add_argument("--max-columns", type=int, default=12)
    data.add_argument("--max-depth", type=int, default=5)
    data.set_defaults(func=command_data)

    policy = sub.add_parser("policy")
    ps = policy.add_subparsers(dest="policy_action", required=True)
    record = ps.add_parser("record")
    record.add_argument("family"); record.add_argument("--host", default="unknown"); record.add_argument("--model", default="unknown")
    record.add_argument("--raw-bytes", type=int, required=True); record.add_argument("--visible-bytes", type=int, required=True)
    record.add_argument("--latency-ms", type=float, required=True)
    success = record.add_mutually_exclusive_group(required=True)
    success.add_argument("--success", action="store_true", dest="success")
    success.add_argument("--failure", action="store_false", dest="success")
    record.add_argument("--quality", type=float, default=1.0); record.add_argument("--cache-hit", action="store_true")
    record.add_argument("--security-regressions", type=int, default=0)
    for name in ("recommend", "promote", "active", "rollback"):
        item = ps.add_parser(name); item.add_argument("family"); item.add_argument("--host", default="unknown"); item.add_argument("--model", default="unknown")
        if name in {"recommend", "promote"}:
            item.add_argument("--minimum-samples", type=int, default=12); item.add_argument("--window", type=int, default=200)
    policy.set_defaults(func=command_policy)

    service = sub.add_parser("service")
    ss = service.add_subparsers(dest="service_action", required=True)
    for name in ("plan", "install", "verify", "uninstall"):
        item = ss.add_parser(name)
        item.add_argument("--name", default="syntavra-provider-proxy")
        item.add_argument("--platform", choices=("linux", "darwin", "windows"))
        item.add_argument("--home"); item.add_argument("--environment-file", default="")
        item.add_argument("--working-directory", default=""); item.add_argument("--description", default="Syntavra provider proxy")
        item.add_argument("--restart-seconds", type=int, default=3)
        item.add_argument("command", nargs=argparse.REMAINDER)
        if name in {"install", "uninstall"}:
            item.add_argument("--dry-run", action="store_true")
        if name == "install":
            item.add_argument("--activate", action="store_true")
        if name == "uninstall":
            item.add_argument("--no-deactivate", action="store_true")
    service.set_defaults(func=command_service)

    arm = sub.add_parser("arm")
    arm_sub = arm.add_subparsers(dest="arm_action", required=True)
    validate = arm_sub.add_parser("validate")
    validate.add_argument("--result", required=True); validate.add_argument("--pair-key", required=True); validate.add_argument("--arm-id", required=True)
    validate.add_argument("--allow-missing-receipt", action="store_true")
    run = arm_sub.add_parser("run")
    run.add_argument("--request", required=True); run.add_argument("--workspace", required=True)
    run.add_argument("--pair-key", required=True); run.add_argument("--arm-id", required=True)
    run.add_argument("--timeout", type=float, default=1200); run.add_argument("--max-artifact-bytes", type=int, default=64 * 1024 * 1024)
    run.add_argument("--max-visible-bytes", type=int, default=16 * 1024); run.add_argument("--allow-missing-receipt", action="store_true")
    run.add_argument("command", nargs=argparse.REMAINDER)
    arm.set_defaults(func=command_arm)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
