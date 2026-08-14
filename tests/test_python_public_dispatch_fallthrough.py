from __future__ import annotations

import ast
import unittest

from tools import report_python_public_dispatch_fallthrough as dispatch_audit


class PythonPublicDispatchFallthroughTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.value = dispatch_audit.report()
        cls.python = cls.value["python"]

    def test_all_245_routes_have_concrete_python_handler_identity(self) -> None:
        self.assertEqual(self.python["route_count"], 245)
        self.assertEqual(self.python["handler_count"], 245)
        self.assertEqual(self.python["handler_failure_count"], 0)
        self.assertEqual(self.python["handler_failures"], [])

    def test_legacy_routes_resolve_to_argparse_callable_defaults(self) -> None:
        self.assertGreater(self.python["legacy_exact_argparse_handler_count"], 0)
        legacy = [
            row
            for row in self.python["manifest"]
            if row["sources"] == ["legacy"]
        ]
        self.assertTrue(legacy)
        for row in legacy:
            self.assertEqual(row["handler_kind"], "argparse-default", row)
            self.assertTrue(str(row["handler"]).startswith("syntavra_runtime."), row)

    def test_manual_dispatchers_cover_parser_selector_values(self) -> None:
        self.assertEqual(self.python["dispatcher_audit_count"], 4)
        self.assertEqual(self.python["dispatcher_failure_count"], 0)
        self.assertEqual(self.python["dispatcher_failures"], [])

    def test_external_benchmark_fallback_is_explicitly_bounded(self) -> None:
        external = next(
            row for row in self.python["dispatchers"] if row["source"] == "external-benchmark"
        )
        self.assertEqual(external["implicit_selector_values"], ["external-suite"])
        self.assertEqual(external["expected_implicit_fallbacks"], ["external-suite"])
        self.assertEqual(external["generic_runtime_fallthrough_count"], 0)

    def test_prerelease_run_actions_have_exactly_one_owner(self) -> None:
        nested = self.python["nested_run_dispatch"]
        self.assertGreater(nested["parser_route_count"], 0)
        self.assertGreater(nested["parser_action_count"], 0)
        self.assertEqual(nested["missing_action_count"], 0, nested)
        self.assertEqual(nested["missing_actions"], [], nested)
        self.assertEqual(nested["multiple_owner_action_count"], 0, nested)
        self.assertEqual(nested["multiple_owner_actions"], [], nested)
        self.assertEqual(nested["stale_by_owner"], {}, nested)
        self.assertEqual(nested["generic_runtime_fallthrough_count"], 1, nested)
        self.assertFalse(nested["generic_runtime_fallthrough_reachable_from_parser"], nested)
        self.assertEqual(nested["failures"], [], nested)
        for row in nested["ownership"]:
            self.assertEqual(row["owner_count"], 1, row)

    def test_nested_run_owner_sets_are_derived_from_real_dispatchers(self) -> None:
        nested = self.python["nested_run_dispatch"]
        owners = nested["owner_sets"]
        self.assertIn("platform", owners)
        self.assertIn("competitive", owners)
        self.assertIn("prerelease-local", owners)
        self.assertIn("platform-status", owners["platform"])
        self.assertIn("rewrite", owners["competitive"])
        self.assertIn("session-open", owners["prerelease-local"])

    def test_generic_runtime_fallthrough_detector_catches_missing_selector_branch(self) -> None:
        tree = ast.parse(
            "def synthetic(args):\n"
            "    if args.command == 'alpha':\n"
            "        return 0\n"
            "    raise RuntimeError(args.command)\n"
        )
        function = tree.body[0]
        self.assertIsInstance(function, ast.FunctionDef)
        explicit = dispatch_audit._compared_selector_values(function, "command")
        generic = dispatch_audit._generic_runtime_fallthrough_count(function, "command")
        self.assertEqual(explicit, {"alpha"})
        self.assertEqual(generic, 1)
        self.assertEqual({"alpha", "beta"} - explicit, {"beta"})

    def test_selector_alias_analysis_catches_competitive_style_dispatch(self) -> None:
        tree = ast.parse(
            "def synthetic(args):\n"
            "    action = args.action\n"
            "    if action == 'alpha':\n"
            "        return 0\n"
            "    if action in {'beta', 'gamma'}:\n"
            "        return 0\n"
            "    raise RuntimeError(action)\n"
        )
        function = tree.body[0]
        self.assertIsInstance(function, ast.FunctionDef)
        explicit = dispatch_audit._compared_selector_values(function, "action")
        generic = dispatch_audit._generic_runtime_fallthrough_count(function, "action")
        self.assertEqual(explicit, {"alpha", "beta", "gamma"})
        self.assertEqual(generic, 1)

    def test_dispatch_fallthrough_contract_is_clean(self) -> None:
        self.assertEqual(self.value["schema_version"], 2)
        self.assertTrue(self.value["base_execution_contract_ok"], self.value)
        self.assertTrue(self.value["ok"], self.value)


if __name__ == "__main__":
    unittest.main()
