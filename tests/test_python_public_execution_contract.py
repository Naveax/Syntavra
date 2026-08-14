from __future__ import annotations

import argparse
import unittest

from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract


class PythonPublicExecutionContractTests(unittest.TestCase):
    def test_all_245_routes_have_one_python_execution_owner(self) -> None:
        value = execution_contract.report()
        python = value["python"]

        self.assertEqual(python["route_count"], 245)
        self.assertEqual(python["unique_execution_owner_count"], 245)
        self.assertEqual(python["owner_failure_count"], 0)
        self.assertEqual(python["owner_failures"], [])

    def test_parser_leaves_are_reachable_via_leaf_help(self) -> None:
        value = execution_contract.report()["python"]
        self.assertGreater(value["parser_leaf_reachability_count"], 0)
        self.assertEqual(value["parser_leaf_reachability_failures"], [])

    def test_shadow_rules_have_live_override_owners(self) -> None:
        value = execution_contract.report()["python"]
        self.assertGreater(value["shadow_rule_count"], 0)
        self.assertEqual(value["shadow_failure_count"], 0)
        self.assertEqual(value["shadow_failures"], [])

    def test_parser_aliases_do_not_conflict_with_canonical_routes(self) -> None:
        value = execution_contract.report()["python"]
        self.assertEqual(value["alias_conflict_count"], 0)
        self.assertEqual(value["alias_conflicts"], [])

    def test_unknown_public_commands_use_argparse_exit_two(self) -> None:
        value = execution_contract.report()["python"]
        self.assertEqual(value["parser_error_failures"], [])
        self.assertTrue(value["parser_error_contract"])
        for row in value["parser_error_contract"]:
            self.assertEqual(row["invalid_command_exit"], 2, row)

    def test_positional_values_do_not_become_public_route_identities(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        run = sub.add_parser("run")
        run.add_argument("internal_selector")

        # Route identity stops at argparse command choices. Arbitrary internal
        # selector values are runtime data and must never inflate the public
        # command inventory.
        self.assertEqual(public_surface._parser_paths(parser), {"run"})

    def test_execution_contract_is_clean(self) -> None:
        value = execution_contract.report()
        self.assertTrue(value["ok"], value)
        self.assertEqual(value["python"]["duplicate_route_count"], 0)
        self.assertEqual(value["python"]["namespace_collision_count"], 0)


if __name__ == "__main__":
    unittest.main()
