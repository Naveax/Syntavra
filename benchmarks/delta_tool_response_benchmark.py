#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.tool_externalization import ExternalizationPolicy, ToolOutputExternalizer, ToolPayload


ROUNDS = 24
BASE_ROWS = 5000


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="syntavra-delta-tool-response-") as directory:
        root = Path(directory)
        engine = ToolOutputExternalizer(
            root / "externalizer.sqlite3",
            evidence=EvidenceStore(root / "evidence", project_id="delta-tool-response-benchmark"),
            policy=ExternalizationPolicy.for_profile("compact"),
        )
        current = "\n".join(
            f"2026-09-08 INFO request={index} route=/api status=200"
            for index in range(BASE_ROWS)
        ) + "\n"
        metadata = {"invalidation_fingerprint": "polling-contract-v1"}
        full_current_replay_bytes = 0
        candidate_visible_bytes = 0
        delta_rounds = 0
        exact_roundtrips = 0
        validated_baselines = 0
        previous_artifact = ""

        for round_number in range(1, ROUNDS + 1):
            if round_number > 1:
                current += f"2026-09-08 WARN poll={round_number} changed-field=value-{round_number}\n"
            full_current_replay_bytes += len(current.encode("utf-8"))
            artifact = engine.externalize(
                ToolPayload(
                    command="service logs",
                    stdout=current,
                    path="service.log",
                    scope_key="polling-loop",
                    metadata=metadata,
                )
            )
            candidate_visible_bytes += artifact.visible_bytes
            if engine.restore(artifact.artifact_id) == current.encode("utf-8") and engine.verify(artifact.artifact_id)["ok"]:
                exact_roundtrips += 1
            protocol = artifact.metadata["delta_protocol"]
            if artifact.mode == "delta-externalized":
                delta_rounds += 1
                validated_baselines += int(
                    protocol["baseline_status"] == "VALID"
                    and protocol["exact_baseline_verified"]
                    and artifact.baseline_artifact_id == previous_artifact
                )
            previous_artifact = artifact.artifact_id

        reduction_ratio = 1.0 - candidate_visible_bytes / max(1, full_current_replay_bytes)
        gates = {
            "all_exact_roundtrips": exact_roundtrips == ROUNDS,
            "all_post_warmup_rounds_delta": delta_rounds == ROUNDS - 1,
            "all_delta_baselines_validated": validated_baselines == ROUNDS - 1,
            "candidate_lower_than_full_current_replay": candidate_visible_bytes < full_current_replay_bytes,
            "local_structural_reduction_above_90pct": reduction_ratio > 0.90,
            "no_provider_savings_claim": True,
        }
        report = {
            "schema_version": 1,
            "family": "syntavra-te-p0-09-delta-tool-response-polling",
            "claim_boundary": "LOCAL_STRUCTURAL_POLLING_REGRESSION_ONLY_NOT_PROVIDER_BILLED_PROOF",
            "rounds": ROUNDS,
            "full_current_replay_bytes": full_current_replay_bytes,
            "candidate_visible_bytes": candidate_visible_bytes,
            "local_structural_byte_reduction_ratio": reduction_ratio,
            "delta_rounds": delta_rounds,
            "validated_baselines": validated_baselines,
            "exact_roundtrips": exact_roundtrips,
            "gates": gates,
        }
        if not all(gates.values()):
            raise AssertionError(report)
        return report


def main() -> int:
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
