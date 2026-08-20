from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.artifacts import ArtifactStore
from syntavra_runtime.output_intelligence import (
    CompressionSafetyClass,
    OutputIntelligenceEngine,
    SemanticPreservationVerifier,
)


class OutputIntelligenceV1Tests(unittest.TestCase):
    def test_semantic_verifier_detects_missing_critical_facts(self) -> None:
        verifier = SemanticPreservationVerifier()
        source = "FAILED src/core.py:42 Permission denied: expected 7, got 9"
        candidate = "FAILED Permission denied"
        report = verifier.verify(source, candidate)
        self.assertFalse(report.ok)
        self.assertTrue(any(item.startswith("path:") for item in report.missing_facts))
        self.assertTrue(any(item.startswith("number:") for item in report.missing_facts))

    def test_large_failure_output_is_bounded_semantic_and_exact_recoverable(self) -> None:
        output = (
            "collecting tests\n"
            + "\n".join(f"progress item {index}" for index in range(500))
            + "\nFAILED tests/test_core.py:42 AssertionError: expected 7 got 9\n"
            + "1 failed, 9 passed in 2.50s\n"
        )
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(Path(td) / "artifacts")
            engine = OutputIntelligenceEngine(store)
            result = engine.process("pytest", output, exit_code=1, budget_bytes=1024)
            exact = store.read(result.artifact_id)
        self.assertEqual(exact, output.encode("utf-8"))
        self.assertTrue(result.exact_recovery)
        self.assertTrue(result.semantic_preservation)
        self.assertTrue(result.no_worse)
        self.assertEqual(result.decision, "COMPACT")
        self.assertLessEqual(result.visible_bytes, 1024)
        self.assertLess(result.visible_bytes, result.original_bytes)
        self.assertEqual(result.compression_safety, CompressionSafetyClass.STRUCTURAL_SAFE.value)

    def test_small_output_uses_no_worse_passthrough(self) -> None:
        text = "ok\n"
        with tempfile.TemporaryDirectory() as td:
            engine = OutputIntelligenceEngine(ArtifactStore(Path(td) / "artifacts"))
            result = engine.process("python -V", text, budget_bytes=512)
        self.assertEqual(result.decision, "PASSTHROUGH")
        self.assertEqual(result.visible_text, text)
        self.assertTrue(result.no_worse)
        self.assertFalse(result.requires_exact_reveal)

    def test_preservation_failure_fails_closed_to_exact_reveal(self) -> None:
        critical = "\n".join(
            f"FAILED tests/test_{index}.py:{1000 + index} AssertionError: expected {index} got {index + 1}"
            for index in range(120)
        )
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(Path(td) / "artifacts")
            engine = OutputIntelligenceEngine(store)
            result = engine.process("custom-tool", critical, exit_code=1, budget_bytes=256)
            exact = store.read(result.artifact_id)
        self.assertEqual(exact, critical.encode("utf-8"))
        self.assertEqual(result.decision, "EXACT_REQUIRED")
        self.assertFalse(result.semantic_preservation)
        self.assertTrue(result.requires_exact_reveal)
        self.assertEqual(result.compression_safety, CompressionSafetyClass.EXACT_ONLY.value)
        self.assertLessEqual(result.visible_bytes, 256)
        self.assertTrue(result.no_worse)

    def test_rewrite_engine_is_reused_and_shell_composition_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            engine = OutputIntelligenceEngine(ArtifactStore(Path(td) / "artifacts"))
            unsafe = engine.plan_command_rewrite("pytest | tee out.txt")
            explicit = engine.plan_command_rewrite("git status --short")
        self.assertFalse(unsafe.changed)
        self.assertFalse(unsafe.safe)
        self.assertEqual(unsafe.reason, "shell composition is not rewritten")
        self.assertFalse(explicit.changed)
        self.assertTrue(explicit.safe)
        self.assertEqual(explicit.reason, "explicit user format preserved")

    def test_receipt_hash_is_deterministic_for_identical_input(self) -> None:
        output = "\n".join(["build progress"] * 300) + "\n1 failed, 2 passed\n"
        hashes = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as td:
                engine = OutputIntelligenceEngine(ArtifactStore(Path(td) / "artifacts"))
                hashes.append(engine.process("pytest", output, exit_code=1, budget_bytes=512).receipt_hash)
        self.assertEqual(hashes[0], hashes[1])

    def test_visible_secrets_are_redacted_while_exact_artifact_is_preserved(self) -> None:
        output = "api_key=super-secret-token-value\noperation complete\n"
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(Path(td) / "artifacts")
            engine = OutputIntelligenceEngine(store)
            result = engine.process("custom-tool", output, budget_bytes=512)
            exact = store.read(result.artifact_id)
        self.assertNotIn("super-secret-token-value", result.visible_text)
        self.assertEqual(exact, output.encode("utf-8"))

    def test_runtime_reuses_existing_authorities_and_adds_no_store_or_cli(self) -> None:
        status = OutputIntelligenceEngine.status()
        for key in (
            "exact_output_store_reused",
            "terminal_output_engine_reused",
            "command_compactor_registry_reused",
            "command_rewriter_reused",
            "semantic_preservation_verifier",
            "compression_safety_classes",
            "no_worse_guard",
            "bounded_visible_output",
            "fail_closed_on_verification_failure",
            "content_addressed_receipt",
        ):
            self.assertTrue(status[key], key)
        self.assertFalse(status["parallel_persistent_store"])
        self.assertFalse(status["public_cli_route"])


if __name__ == "__main__":
    unittest.main()
