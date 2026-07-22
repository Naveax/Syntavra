#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "skills" / "syntavra" / "profiles" / "roblox_studio"
sys.path.insert(0, str(PROFILE.parent))

from roblox_studio.capabilities import default_capabilities
from roblox_studio.capability_graph import CapabilityGraph
from roblox_studio.profile import VALIDATORS
from roblox_studio.registries import default_engines

REQUIRED = [
    PROFILE / "__init__.py", PROFILE / "profile.py", PROFILE / "activation.py",
    PROFILE / "task_state.py", PROFILE / "capabilities.py", PROFILE / "capability_graph.py",
    PROFILE / "registries.py", PROFILE / "evidence_ledger.py", PROFILE / "context_knapsack.py",
    PROFILE / "workflow.py", PROFILE / "validators.py", PROFILE / "telemetry.py",
    PROFILE / "datamodel.py", PROFILE / "output_virtualization.py", PROFILE / "memory.py",
    PROFILE / "scheduler.py", PROFILE / "adapters" / "simulated" / "__init__.py",
    PROFILE / "adapters" / "transcript" / "__init__.py", PROFILE / "adapters" / "live" / "__init__.py",
    ROOT / "benchmarks" / "roblox_profile_benchmark.py", ROOT / "tools" / "verify_claims.py",
]


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("source_directory", PROFILE.is_dir(), str(PROFILE)))
    checks.append(("required_files", all(path.is_file() for path in REQUIRED), "ordinary source files"))
    forbidden_payload = list(ROOT.rglob("payload-*.b64")) + list(ROOT.rglob(".syntavra-direct"))
    checks.append(("no_encoded_source_transport", not forbidden_payload, ",".join(map(str, forbidden_payload))))
    specs = default_capabilities()
    engines = default_engines(specs)
    try:
        CapabilityGraph(specs, engines=engines.engines, validators=VALIDATORS)
        graph_ok = True
    except Exception as exc:
        graph_ok = False
        graph_detail = repr(exc)
    else:
        graph_detail = "33 records with executable coverage metadata"
    checks.append(("capability_graph", graph_ok and len(specs) == 33, graph_detail))
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests" / "roblox_profile"))
    checks.append(("generated_test_count", suite.countTestCases() > 0, str(suite.countTestCases())))
    manifest = PROFILE / "MANIFEST.sha256"
    manifest_ok = manifest.is_file()
    if manifest_ok:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            path = PROFILE / relative
            manifest_ok = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
            if not manifest_ok:
                break
    checks.append(("profile_manifest", manifest_ok, str(manifest)))
    result = {"ok": all(item[1] for item in checks), "checks": [{"name": n, "passed": p, "detail": d} for n,p,d in checks]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
