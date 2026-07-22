from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syntavra_runtime.data_router import DataRoutePolicy, DataRouter
from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.security_scan import IncrementalSecurityScanner
from syntavra_runtime.streaming import StreamSemanticProcessor
from syntavra_runtime.util import atomic_write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="benchmarks/results/v6-production-core/internal.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="syntavra-v6-bench-") as temp_name:
        root = Path(temp_name)
        store = EvidenceStore(root / "evidence", project_id="benchmark")
        rows = [{
            "id": index,
            "status": "error" if index % 997 == 0 else "ok",
            "latency_ms": (index * 17) % 503,
            "message": "authentication token rotation failed" if index % 997 == 0 else "ordinary event " + "x" * 96,
        } for index in range(10_000)]
        payload = {"rows": rows}
        started = time.perf_counter()
        routed = DataRouter(store).route(
            payload, hint="sql", query="authentication token rotation failure",
            policy=DataRoutePolicy(budget_bytes=4096, max_rows=8, max_columns=8),
        )
        route_ms = (time.perf_counter() - started) * 1000
        parsed = json.loads(routed.visible)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        handle = store.put(raw, kind="benchmark")
        digest = handle.rsplit("/", 1)[1]
        encrypted = (store.objects / digest[:2] / digest[2:]).read_bytes()
        stream = StreamSemanticProcessor(content_type="text/event-stream", max_event_bytes=1024 * 1024)
        scanner = IncrementalSecurityScanner()
        for part in (b'data: {"delta":"safe"}\n\n', b'data: {"usage":{"input_tokens":10}}\n\n', b'data: [DONE]\n\n'):
            stream.feed(part)
            scanner.feed(part.decode("utf-8"))
        stream_summary = stream.finalize()
        scanner_summary = scanner.result()
        result = {
            "schema_version": 1,
            "benchmark": "v6-production-core",
            "rows": len(rows),
            "raw_bytes": len(raw),
            "visible_bytes": routed.visible_bytes,
            "visible_reduction": routed.reduction_ratio,
            "route_ms": route_ms,
            "valid_json": isinstance(parsed, dict) and int(parsed.get("_syntavra", {}).get("schema_version", 0)) >= 1,
            "exact_roundtrip": store.get(handle) == raw,
            "encrypted_at_rest": raw[:256] not in encrypted,
            "evidence_verify": store.verify(handle),
            "stream_events": stream_summary.event_count,
            "stream_chain_root": stream_summary.chain_root,
            "stream_usage": stream_summary.usage,
            "stream_security_clean": not scanner_summary.secret_types and not scanner_summary.injection_risk,
            "passes": bool(
                routed.visible_bytes <= 4096 and routed.reduction_ratio > 0.99 and
                isinstance(parsed, dict) and int(parsed.get("_syntavra", {}).get("schema_version", 0)) >= 1 and
                store.get(handle) == raw and raw[:256] not in encrypted and store.verify(handle) and
                stream_summary.event_count >= 2 and not scanner_summary.secret_types
            ),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output, result, mode=0o644)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["passes"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
