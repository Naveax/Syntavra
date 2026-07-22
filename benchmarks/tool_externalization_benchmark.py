from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from syntavra_runtime.evidence import EvidenceStore
from syntavra_runtime.tool_externalization import ExternalizationPolicy, ToolOutputExternalizer, ToolPayload


def fixtures():
    diff = []
    for file_index in range(180):
        diff += [f"diff --git a/src/f{file_index}.py b/src/f{file_index}.py", f"--- a/src/f{file_index}.py", f"+++ b/src/f{file_index}.py", "@@ -10,3 +10,4 @@", "-old = compute()", "+new = compute_safe()", "+assert new is not None"]
    tests = [f"tests/test_{i}.py ." for i in range(4000)]
    tests += ["FAILURES", "tests/test_security.py:91: AssertionError", "E AssertionError: token refresh rejected", "1 failed, 3999 passed in 14.2s"]
    logs = [f"2026-07-20 12:00:{i%60:02d} INFO request={i} route=/api item={i%40}" for i in range(25000)]
    logs.insert(19331, "2026-07-20 12:12:11 FATAL auth refresh rejected at src/security.py:91 authorization=bench-secret")
    rows = [{"id": i, "status": "ok", "owner": f"team-{i%20}", "payload": "x" * 80} for i in range(9000)]
    rows[7331]["error"] = "rare checksum mismatch in shard-17"
    search = [f"src/package_{i%80}/module_{i}.py:{i%300+1}: result_{i}" for i in range(18000)]
    binary = bytes(range(256)) * 2400
    return [
        ("diff", ToolPayload(command="git diff", stdout="\n".join(diff), scope_key="bench", path="changes.diff"), "compute_safe"),
        ("tests", ToolPayload(command="pytest -q", stdout="\n".join(tests), scope_key="bench"), "token refresh rejected"),
        ("logs", ToolPayload(command="service logs", stdout="\n".join(logs), scope_key="bench", path="service.log"), "auth refresh security.py"),
        ("json", ToolPayload(command="cat issues.json", stdout=json.dumps(rows), scope_key="bench", path="issues.json"), "checksum mismatch shard-17"),
        ("search", ToolPayload(command="rg result_", stdout="\n".join(search), scope_key="bench"), "module_17331.py"),
        ("binary", ToolPayload(command="cat model.bin", stdout=binary, scope_key="bench", path="model.bin"), ""),
    ]


def run(profile: str):
    with tempfile.TemporaryDirectory() as temp:
        engine=ToolOutputExternalizer(Path(temp)/"ext.db", evidence=EvidenceStore(Path(temp)/"evidence", project_id="tool-output-externalization-benchmark"), policy=ExternalizationPolicy.for_profile(profile))
        rows=[]; raw_total=0; visible_total=0
        start=time.perf_counter()
        for name,payload,query in fixtures():
            result=engine.externalize(payload); verification=engine.verify(result.artifact_id)
            hits=engine.search(query, artifact_id=result.artifact_id) if query else []
            critical=engine.reveal(result.artifact_id, lens="critical", budget_bytes=4096)
            pack=engine.search_pack(query, artifact_id=result.artifact_id, budget_bytes=4096) if query else None
            proof=engine.segment_proof(result.artifact_id, 0)
            raw_total += result.original_bytes; visible_total += result.visible_bytes
            rows.append({"name":name,"family":result.family,"mode":result.mode,"raw_bytes":result.original_bytes,"visible_bytes":result.visible_bytes,"reduction_ratio":result.reduction_ratio,"segments":result.segment_count,"quality":result.quality_gate_passed,"roundtrip":verification["ok"],"search_found":True if not query else bool(hits),"search_pack_bytes":0 if pack is None else pack.visible_bytes,"merkle_proof":proof["verified"],"critical_bytes":critical.bytes_returned,"injection_risk":result.injection_risk})
        base="\n".join(f"2026-07-20 INFO request={i}" for i in range(12000))+"\n"
        first=engine.externalize(ToolPayload(command="service logs",stdout=base,path="append.log",scope_key="delta"))
        duplicate=engine.externalize(ToolPayload(command="service logs",stdout=base,path="append.log",scope_key="delta"))
        updated=engine.externalize(ToolPayload(command="service logs",stdout=base+"2026-07-20 FATAL delta needle src/delta.py:9\n",path="append.log",scope_key="delta"))
        seconds=time.perf_counter()-start
        return {"profile":profile,"aggregate":{"raw_bytes":raw_total,"visible_bytes":visible_total,"reduction_ratio":1-visible_total/max(1,raw_total),"seconds":seconds,"peak_python_bytes":None,"all_quality":all(row["quality"] for row in rows),"all_roundtrips":all(row["roundtrip"] for row in rows),"all_searches":all(row["search_found"] for row in rows),"all_merkle_proofs":all(row["merkle_proof"] for row in rows),"dedup_mode":duplicate.mode,"dedup_visible_bytes":duplicate.visible_bytes,"delta_mode":updated.mode,"delta_baseline":updated.baseline_artifact_id==first.artifact_id,"delta_needle_visible":"delta.py:9" in updated.preview,"delta_lineage_depth":len(engine.lineage(updated.artifact_id))},"rows":rows,"stats":engine.stats()}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--output"); args=parser.parse_args()
    result={"schema_version":1,"claim":"SUPERIORITY_NOT_PROVEN","boundary":"Internal deterministic tool-output externalization benchmark. No competitor executable or provider-token arm was run.","profiles":[run(name) for name in ("compact","balanced","audit")]}
    text=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)
    if args.output: Path(args.output).write_text(text+"\n",encoding="utf-8")
    print(text)

if __name__=="__main__": main()
