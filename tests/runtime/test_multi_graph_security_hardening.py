from __future__ import annotations

import unittest

from syntavra_runtime.context_namespace import ContextNamespaceAddress
from syntavra_runtime.multi_graph_retrieval import GraphEdge, GraphNode, MultiGraphRetrieval


URI = ContextNamespaceAddress.repository(
    "syntavra-security-test",
    directory="syntavra_runtime",
    file="syntavra_runtime/example.py",
    symbol="Example.run",
).uri


class MultiGraphSecurityHardeningTests(unittest.TestCase):
    def test_include_tainted_never_bypasses_explicit_security_deny(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer(
            "code",
            "code",
            [
                GraphNode(
                    "code",
                    "code-target",
                    "credential repair",
                    namespace_uri=URI,
                    trust_level="verified",
                    tainted=True,
                )
            ],
        )
        engine.add_layer(
            "security",
            "security",
            [
                GraphNode(
                    "security",
                    "deny-target",
                    "credential deny",
                    namespace_uri=URI,
                    trust_level="verified",
                    metadata={"disposition": "deny"},
                )
            ],
        )

        result = engine.retrieve("credential repair", include_tainted=True)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["blocked_identity_count"], 1)

    def test_include_tainted_still_allows_taint_without_security_deny(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer(
            "code",
            "code",
            [
                GraphNode(
                    "code",
                    "tainted-only",
                    "repair retry",
                    trust_level="verified",
                    tainted=True,
                )
            ],
        )

        self.assertEqual(engine.retrieve("repair retry")["candidate_count"], 0)
        allowed = engine.retrieve("repair retry", include_tainted=True)
        self.assertEqual(allowed["candidate_count"], 1)
        self.assertEqual(allowed["blocked_identity_count"], 0)

    def test_security_deny_blocks_namespace_alias_when_security_has_item_id(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer(
            "code",
            "code",
            [
                GraphNode(
                    "code",
                    "uri-only",
                    "credential rotation",
                    namespace_uri=URI,
                    trust_level="verified",
                )
            ],
        )
        engine.add_layer(
            "security",
            "security",
            [
                GraphNode(
                    "security",
                    "item-and-uri",
                    "credential policy",
                    namespace_uri=URI,
                    item_id="ctx-security-1",
                    trust_level="verified",
                    metadata={"disposition": "deny"},
                )
            ],
        )

        result = engine.retrieve("credential rotation", include_tainted=True)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["blocked_identity_count"], 1)
        self.assertEqual(result["blocked_item_id_count"], 1)
        self.assertEqual(result["blocked_namespace_uri_count"], 1)

    def test_denied_node_cannot_seed_or_propagate_to_allowed_neighbor(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer(
            "security",
            "security",
            [
                GraphNode(
                    "security",
                    "denied-source",
                    "forbidden trigger phrase",
                    trust_level="verified",
                    metadata={"disposition": "deny"},
                ),
                GraphNode(
                    "security",
                    "allowed-neighbor",
                    "unrelated safe neighbor",
                    trust_level="verified",
                    metadata={"disposition": "allow"},
                ),
            ],
            [
                GraphEdge(
                    "denied-source",
                    "allowed-neighbor",
                    "influences",
                    confidence=1.0,
                )
            ],
        )

        result = engine.retrieve("forbidden trigger phrase", max_hops=4)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["blocked_identity_count"], 1)

    def test_tainted_node_cannot_propagate_until_taint_is_explicitly_included(self) -> None:
        engine = MultiGraphRetrieval()
        engine.add_layer(
            "code",
            "code",
            [
                GraphNode(
                    "code",
                    "tainted-source",
                    "poison trigger phrase",
                    trust_level="verified",
                    tainted=True,
                ),
                GraphNode(
                    "code",
                    "safe-neighbor",
                    "unrelated safe neighbor",
                    trust_level="verified",
                ),
            ],
            [
                GraphEdge(
                    "tainted-source",
                    "safe-neighbor",
                    "influences",
                    confidence=1.0,
                )
            ],
        )

        blocked = engine.retrieve("poison trigger phrase", max_hops=4)
        self.assertEqual(blocked["candidate_count"], 0)
        allowed = engine.retrieve(
            "poison trigger phrase",
            max_hops=4,
            include_tainted=True,
        )
        identities = {row["identity"] for row in allowed["candidates"]}
        self.assertIn("node:code:tainted-source", identities)
        self.assertIn("node:code:safe-neighbor", identities)


if __name__ == "__main__":
    unittest.main()
