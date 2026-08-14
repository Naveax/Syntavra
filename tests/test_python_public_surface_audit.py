from __future__ import annotations

import argparse
import json
import unittest

from tools import report_missing_native_public_routes as public_surface


class PythonPublicSurfaceAuditTests(unittest.TestCase):
    def test_canonical_manifest_matches_python_contract(self) -> None:
        contract = json.loads(public_surface.CONTRACT.read_text(encoding="utf-8"))
        expected = contract["python_surface"]
        manifest = public_surface.python_public_route_sources()

        self.assertEqual(len(manifest), int(expected["public_command_count"]))
        self.assertEqual(
            public_surface._digest(manifest),
            str(expected["command_paths_sha256"]),
        )
        self.assertFalse(
            {route: sources for route, sources in manifest.items() if len(sources) > 1}
        )

    def test_production_parser_namespace_has_no_ancestor_child_dest_collisions(self) -> None:
        self.assertEqual(public_surface.python_public_namespace_collisions(), [])

    def test_audit_detects_historical_subparser_positional_shadowing_shape(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        child = sub.add_parser("run")
        child.add_argument("command")

        collisions = public_surface._namespace_dest_collisions(
            parser,
            source="synthetic",
        )

        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["dest"], "command")
        self.assertEqual(collisions[0]["route"], "run")
        self.assertEqual(collisions[0]["ancestor_route"], "<root>")

    def test_metavar_does_not_create_false_namespace_collision(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        child = sub.add_parser("headless-submit")
        child.add_argument("headless_command", metavar="command")

        self.assertEqual(
            public_surface._namespace_dest_collisions(
                parser,
                source="synthetic",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
