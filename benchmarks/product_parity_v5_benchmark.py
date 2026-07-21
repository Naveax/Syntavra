from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from signalcore_runtime.data_router import DataRoutePolicy, DataRouter
from signalcore_runtime.policy_tuner import AdaptivePolicyTuner, PolicyObservation
from signalcore_runtime.service_manager import ProviderProxyServiceManager, ServiceSpec
from signalcore_runtime.util import atomic_write_json, canonical_json, sha256_bytes


def main() -> int:
    started = time.perf_counter()
    rows = [
        {
            "id": index,
            "status": "error" if index % 997 == 0 else "ok",
            "latency_ms": (index % 250) * 1.25,
            "source": f"module_{index % 40}.py",
            "message": ("authentication cache regression " if index == 8973 else "ordinary event ") + ("x" * 120),
        }
        for index in range(10_000)
    ]
    raw_bytes = len(canonical_json({"rows": rows}))
    route_started = time.perf_counter()
    routed = DataRouter().route(
        {"rows": rows}, hint="sql", query="authentication cache regression",
        policy=DataRoutePolicy(budget_bytes=4096, max_rows=8, max_columns=8),
    )
    route_ms = (time.perf_counter() - route_started) * 1000

    with tempfile.TemporaryDirectory() as temp:
        tuner = AdaptivePolicyTuner(Path(temp) / "policy.sqlite3")
        for index in range(100):
            tuner.record(PolicyObservation(
                family="table", host="benchmark", model="same-model",
                raw_bytes=raw_bytes, visible_bytes=routed.visible_bytes,
                latency_ms=route_ms + (index % 5), success=True, quality=1.0,
                cache_hit=index % 3 == 0,
            ))
        recommendation = tuner.recommend("table", host="benchmark", model="same-model")
        service = ProviderProxyServiceManager(temp)
        spec = ServiceSpec("signalcore-proxy", (sys.executable, "-m", "signalcore_runtime.product_v5_cli", "--help"))
        service_plans = {
            platform: asdict(service.plan(spec, platform_name=platform))
            for platform in ("linux", "darwin", "windows")
        }

    typescript = ROOT / "sdk" / "typescript" / "dist" / "index.js"
    quality = {
        "route_within_budget": routed.visible_bytes <= 4096,
        "query_evidence_selected": "authentication cache regression" in routed.visible,
        "exact_hash_present": len(routed.exact_hash) == 64,
        "policy_canary": recommendation.canary,
        "policy_quality": recommendation.mean_quality,
        "policy_success_rate": recommendation.success_rate,
        "service_platforms": sorted(service_plans),
        "typescript_distribution_present": typescript.is_file(),
    }
    payload = {
        "schema_version": 1,
        "boundary": "Internal product-parity mechanisms only; no external competitor or provider-quality claim.",
        "claim": "EXTERNAL_SUPERIORITY_NOT_PROVEN",
        "workload": {"table_rows": len(rows), "raw_bytes": raw_bytes, "visible_budget_bytes": 4096, "policy_observations": 100},
        "data_routing": {
            "family": routed.family,
            "route": routed.route,
            "visible_bytes": routed.visible_bytes,
            "reduction_ratio": routed.reduction_ratio,
            "records_seen": routed.records_seen,
            "records_visible": routed.records_visible,
            "latency_ms": route_ms,
        },
        "adaptive_policy": asdict(recommendation),
        "service_descriptor_hashes": {key: value["descriptor_hash"] for key, value in service_plans.items()},
        "quality": quality,
        "seconds": time.perf_counter() - started,
    }
    payload["result_hash"] = sha256_bytes(canonical_json(payload))
    output = ROOT / "benchmarks" / "results" / "product-parity-v5" / "internal.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, payload, mode=0o644)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    booleans = [value for value in quality.values() if isinstance(value, bool)]
    return 0 if booleans and all(booleans) else 2


if __name__ == "__main__":
    raise SystemExit(main())
