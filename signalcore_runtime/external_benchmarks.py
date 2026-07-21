from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .arm_runner import ArmExecutionPolicy, ArmRunReceipt, SecureArmRunner
from .release_identity import CHANNEL, VERSION
from .util import canonical_json, sha256_bytes


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ExternalSuiteSpec:
    suite_id: str
    display_name: str
    task_domain: str
    upstream: str
    primary_reference: str
    required_metrics: tuple[str, ...]
    quality_direction: str = "higher-is-better"
    requires_repository: bool = False
    requires_provider_receipt: bool = True


SUITES: tuple[ExternalSuiteSpec, ...] = (
    ExternalSuiteSpec(
        "swe-bench",
        "SWE-bench",
        "real-repository-coding",
        "https://github.com/SWE-bench/SWE-bench",
        "https://arxiv.org/abs/2310.06770",
        ("resolved", "tests_passed", "input_tokens", "output_tokens", "cost_usd", "wall_time_ms"),
        requires_repository=True,
    ),
    ExternalSuiteSpec(
        "oolong",
        "Oolong",
        "long-context-analysis-and-aggregation",
        "https://github.com/bartbussmann/oolong",
        "https://arxiv.org/abs/2511.02817",
        ("score", "required_fact_recall", "input_tokens", "output_tokens", "cost_usd", "wall_time_ms"),
    ),
    ExternalSuiteSpec(
        "longbench-v2",
        "LongBench v2",
        "realistic-long-context-reasoning",
        "https://github.com/THUDM/LongBench",
        "https://aclanthology.org/2025.acl-long.183/",
        ("accuracy", "input_tokens", "output_tokens", "cost_usd", "wall_time_ms"),
    ),
    ExternalSuiteSpec(
        "infinitebench",
        "InfiniteBench",
        "100k-plus-long-context",
        "https://github.com/OpenBMB/InfiniteBench",
        "https://aclanthology.org/2024.acl-long.814/",
        ("score", "input_tokens", "output_tokens", "cost_usd", "wall_time_ms"),
    ),
    ExternalSuiteSpec(
        "recursive-long-context",
        "Recursive long-context paired tasks",
        "recursive-programmatic-context",
        "https://github.com/alexzhang13/rlm",
        "https://arxiv.org/abs/2512.24601",
        ("quality_score", "success", "recursive_calls", "input_tokens", "output_tokens", "cost_usd", "wall_time_ms"),
    ),
)


class ExternalSuiteRegistry:
    @staticmethod
    def by_id(suite_id: str) -> ExternalSuiteSpec:
        normalized = suite_id.strip().casefold()
        for suite in SUITES:
            if suite.suite_id == normalized:
                return suite
        raise KeyError(suite_id)

    @staticmethod
    def manifest() -> dict[str, Any]:
        rows = [asdict(item) for item in SUITES]
        return {
            "version": VERSION,
            "channel": CHANNEL,
            "suites": rows,
            "suite_count": len(rows),
            "manifest_hash": sha256_bytes(canonical_json(rows)),
            "claim_boundary": "Configured suites and internal fixtures are not external benchmark results.",
        }


@dataclass(frozen=True)
class ExternalBenchmarkReceipt:
    receipt_id: str
    suite_id: str
    task_id: str
    arm: str
    repetition: int
    dataset_version: str
    harness_commit: str
    verifier_commit: str
    environment_image_digest: str
    repository_commit: str
    provider: str
    model: str
    model_config_hash: str
    result_artifact_hash: str
    raw_provider_receipt_hash: str
    quality_score: float
    success: bool
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float
    wall_time_ms: float
    recursive_calls: int
    synthetic: bool
    metadata: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExternalBenchmarkReceipt":
        return cls(
            receipt_id=str(value.get("receipt_id", "")),
            suite_id=str(value.get("suite_id", "")),
            task_id=str(value.get("task_id", "")),
            arm=str(value.get("arm", "")),
            repetition=int(value.get("repetition", 0)),
            dataset_version=str(value.get("dataset_version", "")),
            harness_commit=str(value.get("harness_commit", "")),
            verifier_commit=str(value.get("verifier_commit", "")),
            environment_image_digest=str(value.get("environment_image_digest", "")),
            repository_commit=str(value.get("repository_commit", "")),
            provider=str(value.get("provider", "")),
            model=str(value.get("model", "")),
            model_config_hash=str(value.get("model_config_hash", "")),
            result_artifact_hash=str(value.get("result_artifact_hash", "")),
            raw_provider_receipt_hash=str(value.get("raw_provider_receipt_hash", "")),
            quality_score=float(value.get("quality_score", -1)),
            success=bool(value.get("success", False)),
            input_tokens=int(value.get("input_tokens", -1)),
            cached_input_tokens=int(value.get("cached_input_tokens", -1)),
            output_tokens=int(value.get("output_tokens", -1)),
            cost_usd=float(value.get("cost_usd", -1)),
            wall_time_ms=float(value.get("wall_time_ms", -1)),
            recursive_calls=int(value.get("recursive_calls", 0)),
            synthetic=bool(value.get("synthetic", True)),
            metadata=dict(value.get("metadata") or {}),
        )

    @property
    def total_billable_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens) + self.output_tokens

    def validate(self) -> tuple[str, ...]:
        reasons: list[str] = []
        try:
            suite = ExternalSuiteRegistry.by_id(self.suite_id)
        except KeyError:
            suite = None
            reasons.append("unknown-suite")
        for field_name in (
            "receipt_id", "task_id", "dataset_version", "provider", "model",
            "model_config_hash", "result_artifact_hash", "raw_provider_receipt_hash",
        ):
            if not getattr(self, field_name):
                reasons.append(f"missing-{field_name.replace('_', '-')}")
        if self.arm not in {"baseline", "signalcore", "token-savior", "context-mode", "headroom", "volt-lcm", "recursive"}:
            reasons.append("unsupported-arm")
        if self.repetition < 1:
            reasons.append("invalid-repetition")
        if not _COMMIT.fullmatch(self.harness_commit):
            reasons.append("invalid-harness-commit")
        if not _COMMIT.fullmatch(self.verifier_commit):
            reasons.append("invalid-verifier-commit")
        if not _DIGEST.fullmatch(self.environment_image_digest):
            reasons.append("invalid-environment-image-digest")
        if suite and suite.requires_repository and not _COMMIT.fullmatch(self.repository_commit):
            reasons.append("invalid-repository-commit")
        if not _HASH.fullmatch(self.model_config_hash):
            reasons.append("invalid-model-config-hash")
        if not _HASH.fullmatch(self.result_artifact_hash):
            reasons.append("invalid-result-artifact-hash")
        if not _HASH.fullmatch(self.raw_provider_receipt_hash):
            reasons.append("invalid-provider-receipt-hash")
        if not 0.0 <= self.quality_score <= 1.0:
            reasons.append("invalid-quality-score")
        if min(self.input_tokens, self.cached_input_tokens, self.output_tokens, self.recursive_calls) < 0:
            reasons.append("invalid-count")
        if self.cached_input_tokens > self.input_tokens:
            reasons.append("cached-input-exceeds-input")
        if self.cost_usd < 0:
            reasons.append("invalid-cost")
        if self.wall_time_ms < 0:
            reasons.append("invalid-wall-time")
        return tuple(dict.fromkeys(reasons))


class ExternalBenchmarkGate:
    minimum_pairs = 30
    quality_non_inferiority_margin = 0.01
    success_non_inferiority_margin = 0.02

    @staticmethod
    def load(path: Path) -> list[ExternalBenchmarkReceipt]:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value.get("receipts", value) if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise ValueError("external benchmark file must contain a list or {'receipts': [...]} object")
        return [ExternalBenchmarkReceipt.from_mapping(item) for item in rows if isinstance(item, Mapping)]

    @staticmethod
    def _pair_key(row: ExternalBenchmarkReceipt) -> tuple[str, str, int, str, str, str, str, str]:
        return (
            row.suite_id,
            row.task_id,
            row.repetition,
            row.dataset_version,
            row.harness_commit,
            row.verifier_commit,
            row.provider,
            row.model,
        )

    @classmethod
    def evaluate(cls, receipts: Iterable[ExternalBenchmarkReceipt], *, suite_id: str | None = None) -> dict[str, Any]:
        rows = [row for row in receipts if suite_id is None or row.suite_id == suite_id]
        reasons: list[str] = []
        invalid = [{"receipt_id": row.receipt_id, "reasons": list(row.validate())} for row in rows if row.validate()]
        if invalid:
            reasons.append("invalid-receipts")
        if not rows:
            reasons.append("no-receipts")
        if any(row.synthetic for row in rows):
            reasons.append("synthetic-receipts-present")
        duplicates = sorted({row.receipt_id for row in rows if sum(item.receipt_id == row.receipt_id for item in rows) > 1})
        if duplicates:
            reasons.append("duplicate-receipt-ids")

        grouped: dict[tuple[str, str, int, str, str, str, str, str], dict[str, ExternalBenchmarkReceipt]] = {}
        for row in rows:
            grouped.setdefault(cls._pair_key(row), {})[row.arm] = row
        pairs = [value for value in grouped.values() if "baseline" in value and "signalcore" in value]
        if len(pairs) < cls.minimum_pairs:
            reasons.append("insufficient-paired-runs")

        quality_deltas: list[float] = []
        success_deltas: list[float] = []
        token_ratios: list[float] = []
        cost_ratios: list[float] = []
        wall_ratios: list[float] = []
        for pair in pairs:
            baseline = pair["baseline"]
            signalcore = pair["signalcore"]
            parity = (
                baseline.dataset_version == signalcore.dataset_version
                and baseline.harness_commit == signalcore.harness_commit
                and baseline.verifier_commit == signalcore.verifier_commit
                and baseline.environment_image_digest == signalcore.environment_image_digest
                and baseline.repository_commit == signalcore.repository_commit
                and baseline.provider == signalcore.provider
                and baseline.model == signalcore.model
                and baseline.model_config_hash == signalcore.model_config_hash
            )
            if not parity:
                reasons.append("pair-parity-failed")
                continue
            quality_deltas.append(signalcore.quality_score - baseline.quality_score)
            success_deltas.append(float(signalcore.success) - float(baseline.success))
            if baseline.total_billable_tokens > 0:
                token_ratios.append(signalcore.total_billable_tokens / baseline.total_billable_tokens)
            if baseline.cost_usd > 0:
                cost_ratios.append(signalcore.cost_usd / baseline.cost_usd)
            if baseline.wall_time_ms > 0:
                wall_ratios.append(signalcore.wall_time_ms / baseline.wall_time_ms)

        quality_delta = statistics.fmean(quality_deltas) if quality_deltas else -1.0
        success_delta = statistics.fmean(success_deltas) if success_deltas else -1.0
        if quality_delta < -cls.quality_non_inferiority_margin:
            reasons.append("quality-non-inferiority-failed")
        if success_delta < -cls.success_non_inferiority_margin:
            reasons.append("success-non-inferiority-failed")
        if not token_ratios:
            reasons.append("no-measurable-token-pairs")

        suites = sorted({row.suite_id for row in rows})
        ok = not reasons
        return {
            "ok": ok,
            "claim": "EXTERNAL_SUITE_EVIDENCE_VERIFIED" if ok else "EXTERNAL_SUITE_EVIDENCE_NOT_PROVEN",
            "public_superiority": "ELIGIBLE_FOR_MANUAL_REVIEW" if ok else "EXTERNAL_SUPERIORITY_NOT_PROVEN",
            "version": VERSION,
            "channel": CHANNEL,
            "suites": suites,
            "reasons": sorted(set(reasons)),
            "invalid": invalid,
            "duplicate_receipt_ids": duplicates,
            "metrics": {
                "receipts": len(rows),
                "pairs": len(pairs),
                "tasks": len({row.task_id for row in rows}),
                "mean_quality_delta": quality_delta if quality_deltas else None,
                "mean_success_delta": success_delta if success_deltas else None,
                "mean_token_ratio": statistics.fmean(token_ratios) if token_ratios else None,
                "mean_cost_ratio": statistics.fmean(cost_ratios) if cost_ratios else None,
                "mean_wall_time_ratio": statistics.fmean(wall_ratios) if wall_ratios else None,
            },
            "requirements": {
                "minimum_pairs": cls.minimum_pairs,
                "quality_non_inferiority_margin": cls.quality_non_inferiority_margin,
                "success_non_inferiority_margin": cls.success_non_inferiority_margin,
                "identical_harness_dataset_verifier_environment_provider_model": True,
            },
        }


class ExternalSuiteRunner:
    """Bind an external benchmark harness to SignalCore's secure arm runner."""

    def __init__(self, root: Path, *, evidence: Any | None = None):
        self.runner = SecureArmRunner(root, evidence=evidence)

    def run(
        self,
        *,
        suite_id: str,
        arm_id: str,
        pair_key: str,
        argv: Sequence[str],
        workspace: Path,
        dataset_version: str,
        harness_commit: str,
        verifier_commit: str,
        environment_image_digest: str,
        task_id: str,
        repository_commit: str = "",
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 3600.0,
    ) -> ArmRunReceipt:
        suite = ExternalSuiteRegistry.by_id(suite_id)
        request = {
            "suite": asdict(suite),
            "task_id": task_id,
            "dataset_version": dataset_version,
            "harness_commit": harness_commit,
            "verifier_commit": verifier_commit,
            "environment_image_digest": environment_image_digest,
            "repository_commit": repository_commit,
            "required_result_schema": "external-benchmark-receipt-v1",
        }
        return self.runner.run(
            arm_id=arm_id,
            pair_key=pair_key,
            argv=argv,
            workspace=workspace,
            request=request,
            environment=environment,
            policy=ArmExecutionPolicy(timeout_seconds=timeout_seconds, require_result=True, require_receipt=True),
        )
