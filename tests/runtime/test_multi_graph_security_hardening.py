from __future__ import annotations

import unittest

from syntavra_runtime.context_namespace import ContextNamespaceAddress
from syntavra_runtime.multi_graph_retrieval import GraphNode, MultiGraphRetrieval


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


if __name__ == "__main__":
    unittest.main()
