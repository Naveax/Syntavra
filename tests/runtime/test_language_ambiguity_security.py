from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from syntavra_runtime.language_platform import LanguageDescriptor, LanguageRegistry


class LanguageAmbiguityAndTrustTests(unittest.TestCase):
    @staticmethod
    def registry() -> LanguageRegistry:
        registry = LanguageRegistry(discover_entry_points=False)
        registry.register_descriptor(LanguageDescriptor("synthetic-alpha", suffixes=(".ambx",), source="test"))
        registry.register_descriptor(LanguageDescriptor("synthetic-beta", suffixes=(".ambx",), source="test"))
        return registry

    def test_unresolved_shared_suffix_remains_ambiguous(self) -> None:
        registry = self.registry()
        detection = registry.detect(Path("model.ambx"), b"alpha beta gamma\n")
        self.assertTrue(detection.language_id.startswith("ambiguous:"))
        self.assertEqual(detection.capability_level, "lexical")
        self.assertEqual(detection.confidence, 0.4)
        self.assertEqual(set(detection.candidates), {"synthetic-alpha", "synthetic-beta"})
        self.assertIn("exact semantic claims are disabled", detection.diagnostics[0])

    def test_manifest_descriptor_wins_only_as_explicit_repository_override(self) -> None:
        registry = self.registry()
        registry.register_descriptor(
            LanguageDescriptor(
                "future-matrix-language",
                suffixes=(".ambx",),
                capabilities=frozenset({"lexical"}),
                source="manifest:/repo/.syntavra/languages/future.json",
            )
        )
        detection = registry.detect(Path("model.ambx"), b"alpha beta gamma\n")
        self.assertEqual(detection.language_id, "future-matrix-language")
        self.assertEqual(detection.descriptor_source, "manifest:/repo/.syntavra/languages/future.json")
        self.assertIn("manifest-override", detection.evidence)

    def test_builtin_content_probes_resolve_real_collisions(self) -> None:
        registry = LanguageRegistry(discover_entry_points=False)
        registry.register_descriptor(LanguageDescriptor("objective-c", suffixes=(".m",), source="test"))
        registry.register_descriptor(LanguageDescriptor("matlab", suffixes=(".m",), source="test"))
        objc = registry.detect(Path("AppDelegate.m"), b"#import <Foundation/Foundation.h>\n@interface AppDelegate : NSObject\n@end\n")
        matlab = registry.detect(Path("solver.m"), b"function result = solver(x)\n% matrix solver\nresult = x;\nend\n")
        self.assertEqual(objc.language_id, "objective-c")
        self.assertEqual(matlab.language_id, "matlab")
        self.assertIn("content-probe", objc.evidence)
        self.assertIn("content-probe", matlab.evidence)

    def test_coq_and_verilog_shared_suffix_are_resolved_by_content(self) -> None:
        registry = LanguageRegistry(discover_entry_points=False)
        registry.register_descriptor(LanguageDescriptor("verilog", suffixes=(".v",), source="test"))
        registry.register_descriptor(LanguageDescriptor("coq", suffixes=(".v",), source="test"))
        coq = registry.detect(Path("proof.v"), b"Theorem identity : forall x, x = x.\nProof. intros. reflexivity. Qed.\n")
        verilog = registry.detect(Path("counter.v"), b"module counter(input wire clk);\nalways @(posedge clk) begin end\nendmodule\n")
        self.assertEqual(coq.language_id, "coq")
        self.assertEqual(verilog.language_id, "verilog")

    def test_entry_point_code_is_not_loaded_without_explicit_authorization(self) -> None:
        entry_point = Mock()
        entry_point.name = "malicious-language-plugin"
        entry_point.load.side_effect = AssertionError("plugin code must not execute")
        points = Mock()
        points.select.return_value = [entry_point]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SYNTAVRA_ALLOW_LANGUAGE_PLUGINS", None)
            with patch("syntavra_runtime.language_platform.importlib_metadata.entry_points", return_value=points):
                registry = LanguageRegistry(discover_entry_points=True)
        entry_point.load.assert_not_called()
        self.assertTrue(any("explicit SYNTAVRA_ALLOW_LANGUAGE_PLUGINS" in item for item in registry.diagnostics))
        self.assertFalse(registry.inventory()["entry_point_plugins_authorized"])

    def test_authorized_entry_point_failure_is_isolated_to_diagnostics(self) -> None:
        entry_point = Mock()
        entry_point.name = "broken-language-plugin"
        entry_point.load.side_effect = RuntimeError("broken plugin")
        points = Mock()
        points.select.return_value = [entry_point]
        with patch.dict(os.environ, {"SYNTAVRA_ALLOW_LANGUAGE_PLUGINS": "1"}, clear=False):
            with patch("syntavra_runtime.language_platform.importlib_metadata.entry_points", return_value=points):
                registry = LanguageRegistry(discover_entry_points=True)
        entry_point.load.assert_called_once()
        self.assertTrue(any("broken-language-plugin" in item for item in registry.diagnostics))
        self.assertTrue(registry.inventory()["universal_text_fallback"])


if __name__ == "__main__":
    unittest.main()
