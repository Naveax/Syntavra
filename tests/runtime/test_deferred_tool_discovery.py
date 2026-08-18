from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.deferred_tool_discovery import (
    DeferredToolDiscoveryEngine,
    HostToolCapabilities,
    ToolHealthRegistry,
)
from syntavra_runtime.mcp_application import MCPApplicationPipeline


def _tool(name: str, description: str, *, properties: dict | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties or {}},
    }


MINIMAL_CATALOG = [
    _tool("syntavra.status", "alpha runtime health status"),
    _tool("syntavra.inspect.map", "alpha repository structure map", properties={"query": {"type": "string"}}),
    _tool("syntavra.output.search", "search exact externalized output", properties={"query": {"type": "string"}}),
    _tool("syntavra.output.reveal", "reveal exact externalized output", properties={"artifact_id": {"type": "string"}}),
    _tool("syntavra.fabric.route", "route task through context fabric", properties={"query": {"type": "string"}}),
]

BALANCED_CATALOG = MINIMAL_CATALOG + [
    _tool("syntavra.inspect.source", "read exact source for symbol", properties={"query": {"type": "string"}}),
    _tool("syntavra.inspect.range", "read exact bounded source range", properties={"path": {"type": "string"}}),
    _tool("syntavra.provider.prepare", "prepare provider request", properties={"provider": {"type": "string"}}),
    _tool("syntavra.sandbox.execute", "execute sandbox command", properties={"argv": {"type": "array", "items": {"type": "string"}}}),
]


class DeferredToolDiscoveryTests(unittest.TestCase):
    def test_descriptors_and_fingerprints_are_deterministic(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        first = engine.describe_catalog(MINIMAL_CATALOG)
        second = engine.describe_catalog(list(reversed(MINIMAL_CATALOG)))
        self.assertEqual(first, second)
        self.assertTrue(all(len(item.capability_fingerprint) == 64 for item in first))
        self.assertTrue(all(len(item.schema_fingerprint) == 64 for item in first))

    def test_namespace_tree_contains_identity_not_full_schema(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        tree = engine.namespace_tree(engine.describe_catalog(MINIMAL_CATALOG))
        self.assertEqual(tree["syntavra"]["inspect"]["map"]["$tool"], "syntavra.inspect.map")
        self.assertNotIn("inputSchema", str(tree))
        self.assertNotIn("properties", str(tree))

    def test_stage1_defers_full_tool_schemas(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        result = engine.stage1(MINIMAL_CATALOG, query="repository structure")
        self.assertEqual(result["stage"], 1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["families"][0]["family"], "inspect")
        serialized = str(result)
        self.assertNotIn("inputSchema", serialized)
        self.assertNotIn("properties", serialized)
        self.assertTrue(result["receipt"]["receipt_hash"])

    def test_stage1_cache_is_catalog_profile_host_and_health_bound(self) -> None:
        health = ToolHealthRegistry()
        engine = DeferredToolDiscoveryEngine(profile="minimal", health_registry=health)
        first = engine.stage1(MINIMAL_CATALOG, query="repository structure")
        second = engine.stage1(MINIMAL_CATALOG, query="repository structure")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        health.set("syntavra.inspect.map", state="unavailable", reason="fixture")
        third = engine.stage1(MINIMAL_CATALOG, query="repository structure")
        self.assertFalse(third["cache_hit"])
        self.assertEqual(third["status"], "unknown")

    def test_no_tool_needed_classifier_is_explicit(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        result = engine.stage1(MINIMAL_CATALOG, query="just explain the concept without tools")
        self.assertEqual(result["status"], "no-tool-needed")
        self.assertTrue(result["no_tool_needed"]["no_tool_needed"])
        self.assertEqual(result["families"], [])

    def test_unknown_query_fails_closed(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        result = engine.stage1(MINIMAL_CATALOG, query="quasar-neutrino-zeta")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["families"], [])

    def test_equal_top_family_matches_are_ambiguous(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        result = engine.stage1(MINIMAL_CATALOG, query="alpha")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual({row["family"] for row in result["families"]}, {"status", "inspect"})

    def test_stage2_exact_tool_expands_schema(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        result = engine.stage2(MINIMAL_CATALOG, selector="syntavra.inspect.map", query="repository")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["tools"][0]["name"], "syntavra.inspect.map")
        self.assertIn("inputSchema", result["tools"][0])
        self.assertLessEqual(result["schema_tokens"], result["token_budget"])

    def test_stage2_family_expansion_is_profile_bounded(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="balanced")
        result = engine.stage2(BALANCED_CATALOG, selector="inspect", query="source")
        names = [row["name"] for row in result["tools"]]
        self.assertIn("syntavra.inspect.source", names)
        self.assertIn("syntavra.inspect.map", names)
        self.assertNotIn("syntavra.status", names)

    def test_stage2_unknown_selector_raises(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        with self.assertRaises(KeyError):
            engine.stage2(MINIMAL_CATALOG, selector="syntavra.missing")

    def test_host_namespace_filter_is_enforced(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="balanced")
        host = HostToolCapabilities(namespace_prefixes=("syntavra.inspect",), schema_budget_tokens=2_000)
        result = engine.stage2(BALANCED_CATALOG, selector="inspect", host=host)
        self.assertTrue(result["tools"])
        self.assertTrue(all(row["name"].startswith("syntavra.inspect.") for row in result["tools"]))
        with self.assertRaises(KeyError):
            engine.stage2(BALANCED_CATALOG, selector="syntavra.provider.prepare", host=host)

    def test_host_risk_capabilities_filter_execution(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="balanced")
        host = HostToolCapabilities(allowed_risks=("read-or-plan", "safe-state-write"), schema_budget_tokens=2_000)
        negotiated = engine.negotiate(BALANCED_CATALOG, host)
        self.assertNotIn("sandbox", negotiated["families"])
        with self.assertRaises(KeyError):
            engine.stage2(BALANCED_CATALOG, selector="syntavra.sandbox.execute", host=host)

    def test_health_and_compatibility_exclude_tools(self) -> None:
        health = ToolHealthRegistry()
        health.set("syntavra.inspect.source", state="unavailable", reason="offline")
        health.set("syntavra.inspect.range", compatible=False, reason="host-incompatible")
        engine = DeferredToolDiscoveryEngine(profile="balanced", health_registry=health)
        result = engine.stage2(BALANCED_CATALOG, selector="inspect", query="source")
        names = [row["name"] for row in result["tools"]]
        self.assertNotIn("syntavra.inspect.source", names)
        self.assertNotIn("syntavra.inspect.range", names)

    def test_capability_fingerprint_changes_with_schema(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        first = engine.describe_catalog(MINIMAL_CATALOG)
        mutated = [dict(row) for row in MINIMAL_CATALOG]
        mutated[1] = _tool(
            "syntavra.inspect.map",
            "alpha repository structure map",
            properties={"query": {"type": "string"}, "depth": {"type": "integer"}},
        )
        second = engine.describe_catalog(mutated)
        first_map = next(item for item in first if item.name == "syntavra.inspect.map")
        second_map = next(item for item in second if item.name == "syntavra.inspect.map")
        self.assertNotEqual(first_map.schema_fingerprint, second_map.schema_fingerprint)
        self.assertNotEqual(first_map.capability_fingerprint, second_map.capability_fingerprint)

    def test_virtualization_returns_only_family_summaries(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="balanced")
        virtual = engine.virtualize(BALANCED_CATALOG)
        self.assertTrue(virtual)
        self.assertTrue(all(row["kind"] == "virtual-tool-family" for row in virtual))
        self.assertNotIn("inputSchema", str(virtual))
        self.assertNotIn("properties", str(virtual))

    def test_negotiation_is_deterministic(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="balanced")
        host = HostToolCapabilities(host="codex", max_tools=7, schema_budget_tokens=1_200)
        first = engine.negotiate(BALANCED_CATALOG, host)
        second = engine.negotiate(list(reversed(BALANCED_CATALOG)), host)
        self.assertEqual(first, second)
        self.assertEqual(first["max_tools"], 7)
        self.assertEqual(first["schema_budget_tokens"], 1_200)

    def test_requested_budget_above_host_negotiation_fails(self) -> None:
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        host = HostToolCapabilities(schema_budget_tokens=400)
        with self.assertRaises(ValueError):
            engine.stage2(MINIMAL_CATALOG, selector="syntavra.inspect.map", host=host, token_budget=401)

    def test_single_large_schema_can_fail_budget_explicitly(self) -> None:
        huge = _tool(
            "syntavra.inspect.map",
            "map repository",
            properties={f"field_{index}": {"type": "string", "description": "x" * 80} for index in range(80)},
        )
        engine = DeferredToolDiscoveryEngine(profile="minimal")
        host = HostToolCapabilities(schema_budget_tokens=128)
        result = engine.stage2([huge], selector="syntavra.inspect.map", host=host, token_budget=128)
        self.assertEqual(result["status"], "budget-exceeded")
        self.assertEqual(result["tools"], [])
        self.assertGreater(result["schema_tokens"], result["token_budget"])

    def test_mcp_application_exposes_additive_discovery_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = MCPApplicationPipeline(Path(directory))
            self.assertTrue(hasattr(pipeline, "discover_tools"))
            result = pipeline.discover_tools(MINIMAL_CATALOG, query="repository structure")
            self.assertEqual(result["stage"], 1)
            self.assertNotIn("inputSchema", str(result))


if __name__ == "__main__":
    unittest.main()
