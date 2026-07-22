from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.arm_runner import ArmExecutionPolicy, SecureArmRunner
from syntavra_runtime.data_router import DataRoutePolicy, DataRouter
from syntavra_runtime.policy_tuner import AdaptivePolicyTuner, PolicyObservation
from syntavra_runtime.product_extension import product_tools
from syntavra_runtime.service_manager import ProviderProxyServiceManager, ServiceSpec


class _Evidence:
    def __init__(self) -> None:
        self.values: list[bytes] = []

    def put(self, value: bytes, **_: object) -> str:
        self.values.append(value)
        return f"sc://test/{len(self.values)}"


class ProductParityV5Tests(unittest.TestCase):
    def test_table_router_preserves_exact_and_bounds_visible_output(self) -> None:
        evidence = _Evidence()
        rows = [
            {
                "id": index,
                "status": "error" if index == 777 else "ok",
                "latency_ms": index * 0.5,
                "message": "authentication failure" if index == 777 else "ordinary record " + ("x" * 80),
            }
            for index in range(1000)
        ]
        result = DataRouter(evidence).route(
            {"rows": rows}, hint="sql", query="authentication failure",
            policy=DataRoutePolicy(budget_bytes=4096, max_rows=6, max_columns=6),
        )
        self.assertEqual(result.family, "table")
        self.assertLessEqual(result.visible_bytes, 4096)
        self.assertGreater(result.reduction_ratio, 0.90)
        self.assertEqual(result.exact_handle, "sc://test/1")
        self.assertEqual(evidence.values[0], json.dumps({"rows": rows}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        self.assertIn("authentication failure", result.visible)

    def test_rag_router_deduplicates_and_preserves_sources(self) -> None:
        payload = {
            "results": [
                {"id": "a", "score": 0.9, "source": "a.py", "text": "token cache policy implementation"},
                {"id": "a", "score": 0.8, "source": "a.py", "text": "duplicate"},
                {"id": "b", "score": 0.7, "source": "b.py", "text": "unrelated"},
            ] * 100
        }
        result = DataRouter().route(payload, hint="rag", query="cache policy", policy=DataRoutePolicy(budget_bytes=2048))
        decoded = json.loads(result.visible)
        self.assertEqual(decoded["unique_count"], 2)
        self.assertEqual(decoded["results"][0]["source"], "a.py")
        self.assertLessEqual(result.visible_bytes, 2048)

    def test_policy_tuner_is_sparse_and_security_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tuner = AdaptivePolicyTuner(Path(temp) / "policy.sqlite3")
            sparse = tuner.recommend("rag", host="codex", model="model")
            self.assertFalse(sparse.canary)
            self.assertEqual(sparse.output_profile, "balanced")
            for index in range(20):
                tuner.record(PolicyObservation(
                    family="rag", host="codex", model="model",
                    raw_bytes=10000, visible_bytes=1000, latency_ms=50 + index,
                    success=True, quality=1.0, cache_hit=index % 2 == 0,
                ))
            recommendation = tuner.recommend("rag", host="codex", model="model")
            self.assertTrue(recommendation.canary)
            self.assertEqual(recommendation.output_profile, "terse")
            sequence = tuner.stage(recommendation, promote=True)
            self.assertGreater(sequence, 0)
            self.assertEqual(tuner.active("rag", host="codex", model="model")["policy_hash"], recommendation.policy_hash)
            tuner.record(PolicyObservation(
                family="security", host="codex", model="model", raw_bytes=100, visible_bytes=50,
                latency_ms=1, success=True, quality=1.0, security_regressions=1,
            ))
            for _ in range(11):
                tuner.record(PolicyObservation(
                    family="security", host="codex", model="model", raw_bytes=100, visible_bytes=50,
                    latency_ms=1, success=True, quality=1.0,
                ))
            unsafe = tuner.recommend("security", host="codex", model="model")
            self.assertFalse(unsafe.canary)
            self.assertEqual(unsafe.cache_policy, "off")
            self.assertTrue(tuner.integrity_check())

    def test_service_plans_are_user_scoped_and_byte_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manager = ProviderProxyServiceManager(temp)
            canonical_home = Path(temp).resolve(strict=False)
            spec = ServiceSpec("syntavra-proxy", (sys.executable, "-m", "syntavra_runtime.product_cli", "--help"))
            for platform in ("linux", "darwin", "windows"):
                plan = manager.plan(spec, platform_name=platform)
                self.assertTrue(plan.user_scoped)
                self.assertTrue(Path(plan.descriptor_path).resolve(strict=False).is_relative_to(canonical_home))
                self.assertTrue(plan.descriptor_hash)
                self.assertIn(spec.name, plan.descriptor_path)
            installed = manager.install(spec, platform_name="linux", activate=False)
            self.assertTrue(installed["ok"])
            self.assertTrue(manager.verify(spec, platform_name="linux")["ok"])
            self.assertTrue(manager.uninstall(spec, platform_name="linux", deactivate=False)["ok"])

    def test_secure_arm_runner_requires_bound_result_and_provider_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            script = root / "arm.py"
            script.write_text(
                """
import json, os
result = {
  'schema_version': 1,
  'pair_key': os.environ['SIGNALBENCH_PAIR_KEY'],
  'arm_id': os.environ['SIGNALBENCH_ARM_ID'],
  'success': True,
  'metrics': {'fresh_input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 3, 'reasoning_tokens': 1},
  'provider_receipt': {'provider': 'test', 'model': 'same-model', 'request_id': 'r1', 'response_hash': 'a' * 64}
}
with open(os.environ['SIGNALBENCH_OUTPUT'], 'w', encoding='utf-8') as handle:
    json.dump(result, handle)
print('arm complete')
""".strip(),
                encoding="utf-8",
            )
            evidence = _Evidence()
            receipt = SecureArmRunner(root / "runs", evidence=evidence).run(
                arm_id="candidate", pair_key="pair-001", argv=(sys.executable, str(script)),
                workspace=workspace, request={"task": "test"},
                policy=ArmExecutionPolicy(timeout_seconds=30),
            )
            self.assertTrue(receipt.success)
            self.assertTrue(receipt.result_valid)
            self.assertTrue(receipt.provider_receipt_valid)
            self.assertEqual(receipt.failure_reasons, ())
            self.assertEqual(len(evidence.values), 2)

    def test_typescript_distribution_has_valid_javascript_and_security_guards(self) -> None:
        root = Path(__file__).resolve().parents[2]
        javascript = root / "sdk" / "typescript" / "dist" / "index.js"
        declarations = root / "sdk" / "typescript" / "dist" / "index.d.ts"
        package = json.loads((root / "sdk" / "typescript" / "package.json").read_text(encoding="utf-8"))
        self.assertTrue(javascript.is_file())
        self.assertTrue(declarations.is_file())
        self.assertEqual(package["type"], "module")
        text = javascript.read_text(encoding="utf-8")
        self.assertIn("provider credentials", text)
        self.assertIn("allowRemote", text)
        node = shutil.which("node")
        if node:
            completed = subprocess.run((node, "--check", str(javascript)), capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_product_mcp_catalog_has_no_duplicate_names(self) -> None:
        names = [row["name"] for row in product_tools()]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("syntavra.data.route", names)
        self.assertIn("syntavra.policy.recommend", names)
        self.assertIn("syntavra.service.plan", names)


if __name__ == "__main__":
    unittest.main()
