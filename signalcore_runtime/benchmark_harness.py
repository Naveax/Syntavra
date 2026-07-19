from __future__ import annotations

import json
import random
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .claim_governance import decide_claim
from .difficulty import AXES, evaluate_difficulty
from .evidence import EvidenceStore
from .process_broker import ProcessBroker
from .util import atomic_write_json, canonical_json, sha256_bytes


TIER_CONFIGS: dict[str, dict[str, float]] = {
    "1X": {axis: 1.0 for axis in AXES},
    "20X": {"R": 35, "C": 32, "O": 40, "T": 30, "P": 22, "V": 34, "X": 18, "H": 24, "S": 16, "F": 20},
    "30X": {"R": 60, "C": 55, "O": 70, "T": 50, "P": 35, "V": 58, "X": 28, "H": 40, "S": 26, "F": 32},
    "100X": {"R": 240, "C": 220, "O": 280, "T": 200, "P": 130, "V": 230, "X": 100, "H": 150, "S": 90, "F": 120},
}


@dataclass(frozen=True)
class ArmResult:
    arm: str
    repetition: int
    success: bool
    verified_work: float
    quota_cost: float | None
    fresh_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    wall_seconds: float
    model_turns: int
    wait_calls: int
    verifier_skips: int
    security_regressions: int
    repository_tree: str
    model: str
    reasoning: str
    prompt_hash: str
    verifier_hash: str
    cache_mode: str


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    tier = str(config.get("tier"))
    axes = config.get("axes") or {}
    controls = config.get("controls") or {}
    required = {
        "same_prompt", "same_model", "same_reasoning", "same_repository", "same_verifier",
        "same_permissions", "same_timeout", "balanced_cache", "no_artificial_sleep", "no_meaningless_duplication",
    }
    integrity = {name: bool(controls.get(name)) for name in required}
    difficulty = evaluate_difficulty(tier, axes, integrity=integrity)
    return {"ok": difficulty.qualified, "difficulty": asdict(difficulty)}


def write_config(path: Path, tier: str) -> dict[str, Any]:
    config = {
        "schema_version": 1,
        "tier": tier,
        "axes": TIER_CONFIGS[tier],
        "controls": {
            "same_prompt": True, "same_model": True, "same_reasoning": True, "same_repository": True,
            "same_verifier": True, "same_permissions": True, "same_timeout": True, "balanced_cache": True,
            "no_artificial_sleep": True, "no_meaningless_duplication": True,
        },
    }
    atomic_write_json(path, config, mode=0o644)
    return config


def generate_synthetic_repository(path: Path, *, files: int = 50, depth: int = 5, fanout: int = 3, faults: int = 1) -> dict[str, Any]:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    ground_truth: dict[str, Any] = {"symbols": {}, "faults": []}
    for index in range(files):
        callers = [f"func_{max(0, index - step)}" for step in range(1, min(fanout, index) + 1)]
        body = [f"def func_{index}(value):"]
        if callers:
            body.append("    total = value")
            for caller in callers:
                body.append(f"    total += {caller}(value - 1) if value > 0 else 0")
            body.append("    return total")
        else:
            body.append("    return value")
        file_path = path / f"module_{index:04d}.py"
        file_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        ground_truth["symbols"][f"func_{index}"] = {"path": file_path.name, "calls": callers}
    for index in range(min(faults, files)):
        file_path = path / f"fault_{index:04d}.py"
        file_path.write_text(f"def fault_{index}():\n    raise RuntimeError('SC_FAULT_{index}')\n", encoding="utf-8")
        ground_truth["faults"].append({"marker": f"SC_FAULT_{index}", "path": file_path.name})
    atomic_write_json(path / "ground_truth.json", ground_truth, mode=0o644)
    return {"files": files + min(faults, files) + 1, "depth": depth, "fanout": fanout, "faults": len(ground_truth["faults"]), "ground_truth_hash": sha256_bytes(canonical_json(ground_truth))}


def load_arm_results(path: Path) -> list[ArmResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("results", [])
    return [ArmResult(**row) for row in rows]


def compare_results(baseline: list[ArmResult], signalcore: list[ArmResult], *, tier: str, config: dict[str, Any]) -> dict[str, Any]:
    validation = validate_config(config)
    valid_pairs: list[tuple[ArmResult, ArmResult]] = []
    invalid: list[dict[str, Any]] = []
    signal_by_rep = {row.repetition: row for row in signalcore}
    for base in baseline:
        sig = signal_by_rep.get(base.repetition)
        if not sig:
            invalid.append({"repetition": base.repetition, "reason": "missing-signalcore-arm"}); continue
        identity = (base.repository_tree, base.model, base.reasoning, base.prompt_hash, base.verifier_hash, base.cache_mode)
        other = (sig.repository_tree, sig.model, sig.reasoning, sig.prompt_hash, sig.verifier_hash, sig.cache_mode)
        if identity != other:
            invalid.append({"repetition": base.repetition, "reason": "paired-identity-mismatch"}); continue
        if not base.success or not sig.success or base.verified_work != sig.verified_work:
            invalid.append({"repetition": base.repetition, "reason": "unequal-verified-work"}); continue
        valid_pairs.append((base, sig))
    difficulty = evaluate_difficulty(tier, config.get("axes", {}), integrity={key: bool(value) for key, value in config.get("controls", {}).items()})
    quota_available = bool(valid_pairs) and all(base.quota_cost and sig.quota_cost for base, sig in valid_pairs)
    decision = decide_claim(
        tier=tier,
        baseline_costs=[float(base.quota_cost or 0) for base, _ in valid_pairs],
        signalcore_costs=[float(sig.quota_cost or 0) for _, sig in valid_pairs],
        difficulty=difficulty,
        required_verifier_skips=sum(sig.verifier_skips for _, sig in valid_pairs),
        security_regressions=sum(sig.security_regressions for _, sig in valid_pairs),
        integrity_violations=len(invalid) + (0 if validation["ok"] else 1),
        actual_quota_available=quota_available,
    )
    return {"valid_pairs": len(valid_pairs), "invalid_runs": invalid, "difficulty": asdict(difficulty), "claim": asdict(decision)}


def run_command_arm(*, arm: str, command: tuple[str, ...], cwd: Path, output: Path, repetitions: int, identity: dict[str, str], quota_cost: float | None = None) -> list[ArmResult]:
    state = output.parent / f".{arm}-state"
    evidence = EvidenceStore(state / "evidence", project_id=sha256_bytes(str(cwd.resolve()).encode()))
    broker = ProcessBroker(state / "broker", evidence, heartbeat_interval=0.1)
    rows: list[ArmResult] = []
    order = list(range(repetitions)); random.Random(1337).shuffle(order)
    for repetition in order:
        started = time.time(); result = broker.run(command, cwd=cwd, timeout=3600)
        rows.append(ArmResult(
            arm=arm, repetition=repetition, success=result.exit_code == 0, verified_work=1.0 if result.exit_code == 0 else 0.0,
            quota_cost=quota_cost, fresh_input_tokens=0, cached_input_tokens=0, output_tokens=0, reasoning_tokens=0,
            wall_seconds=time.time() - started, model_turns=0, wait_calls=0, verifier_skips=0, security_regressions=0,
            repository_tree=identity["repository_tree"], model=identity["model"], reasoning=identity["reasoning"],
            prompt_hash=identity["prompt_hash"], verifier_hash=identity["verifier_hash"], cache_mode=identity["cache_mode"],
        ))
    atomic_write_json(output, {"results": [asdict(row) for row in rows]}, mode=0o644)
    return rows
