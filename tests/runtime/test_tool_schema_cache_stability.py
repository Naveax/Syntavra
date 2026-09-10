from __future__ import annotations

import unittest

from syntavra_runtime.tool_registry import ToolSchemaCompiler, profile_manifest, profile_tools


class ToolSchemaCacheStabilityTests(unittest.TestCase):
    @staticmethod
    def _tool(name: str, *, required: tuple[str, ...] = ("repository_tree", "continuation_token")) -> dict:
        return {
            "name": name,
            "description": "Retrieve a query-conditioned repository response through exact externalization with more explanatory words than needed",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repository_tree": {"type": "string"},
                    "continuation_token": {"type": "string"},
                    "budget_bytes": {"type": "integer"},
                },
                "required": list(required),
            },
        }

    def test_async_catalog_order_cannot_change_compiled_prefix_identity(self) -> None:
        first_catalog = [self._tool("syntavra.zeta"), self._tool("syntavra.alpha")]
        second_catalog = list(reversed(first_catalog))

        first, first_receipt = ToolSchemaCompiler().compile_catalog(first_catalog)
        second, second_receipt = ToolSchemaCompiler().compile_catalog(second_catalog)

        self.assertEqual([row["name"] for row in first], ["syntavra.alpha", "syntavra.zeta"])
        self.assertEqual(first, second)
        self.assertEqual(first_receipt.catalog_hash, second_receipt.catalog_hash)
        self.assertEqual(first_receipt.compiled_hash, second_receipt.compiled_hash)
        self.assertEqual(first_receipt.compiled.tokens, second_receipt.compiled.tokens)

    def test_json_schema_required_set_is_canonicalized_without_changing_meaning(self) -> None:
        left = [self._tool("syntavra.example", required=("repository_tree", "continuation_token"))]
        right = [self._tool("syntavra.example", required=("continuation_token", "repository_tree"))]

        left_compiled, left_receipt = ToolSchemaCompiler().compile_catalog(left)
        right_compiled, right_receipt = ToolSchemaCompiler().compile_catalog(right)

        self.assertEqual(left_compiled, right_compiled)
        self.assertEqual(left_receipt.compiled_hash, right_receipt.compiled_hash)
        required = left_compiled[0]["inputSchema"]["required"]
        self.assertEqual(required, sorted(required, key=lambda value: (value.casefold(), value)))
        self.assertEqual(set(required), {"repo_tree", "cursor"})

    def test_audit_profile_is_stable_across_registration_order(self) -> None:
        names_a = ("syntavra.z", "syntavra.a", "syntavra.m")
        names_b = tuple(reversed(names_a))
        self.assertEqual(profile_tools("audit", names_a), profile_tools("audit", names_b))
        self.assertEqual(profile_tools("audit", names_a), ("syntavra.a", "syntavra.m", "syntavra.z"))

    def test_audit_profile_manifest_keeps_same_hash_for_same_effective_set(self) -> None:
        catalog_a = [self._tool("syntavra.zeta"), self._tool("syntavra.alpha")]
        catalog_b = list(reversed(catalog_a))
        manifest_a = profile_manifest("audit", catalog_a)
        manifest_b = profile_manifest("audit", catalog_b)
        self.assertEqual(manifest_a["selected_tools"], manifest_b["selected_tools"])
        self.assertEqual(manifest_a["profile_hash"], manifest_b["profile_hash"])
        self.assertEqual(
            manifest_a["schema_compilation"]["compiled_hash"],
            manifest_b["schema_compilation"]["compiled_hash"],
        )

    def test_alias_decoding_remains_backward_compatible(self) -> None:
        compiler = ToolSchemaCompiler()
        compiler.compile_catalog([self._tool("syntavra.example")])
        decoded = compiler.decode_arguments(
            "syntavra.example",
            {"repo_tree": "tree", "cursor": "next", "budget": 256},
        )
        self.assertEqual(decoded["repository_tree"], "tree")
        self.assertEqual(decoded["continuation_token"], "next")
        self.assertEqual(decoded["budget_bytes"], 256)


if __name__ == "__main__":
    unittest.main()
