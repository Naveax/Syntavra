from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.programmatic_execution import (
    ArtifactReference,
    ProgrammaticExecutionPlane,
    ProgrammaticFunctionRegistry,
    ProgrammaticStep,
)


class ProgrammaticExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = ProgrammaticFunctionRegistry()
        self.registry.register("add", lambda left, right: left + right)
        self.registry.register("square", lambda value: value * value)
        self.registry.register("is-even", lambda value: value % 2 == 0)
        self.registry.register("sum", lambda accumulator, value: accumulator + value)
        self.registry.register("length", lambda value: len(value))
        self.registry.register("identity", lambda value: value)
        self.registry.register("impure", lambda value: value, pure=False)

        def explode(_: object) -> object:
            raise ValueError("token=sk-proj-" + "A" * 32)

        self.registry.register("explode", explode)
        self.plane = ProgrammaticExecutionPlane(
            Path(self.temp.name),
            registry=self.registry,
            max_inline_bytes=128,
            max_preview_bytes=96,
            max_items=8,
            max_workers=4,
        )

    def test_call_is_typed_and_content_addressed(self) -> None:
        step = ProgrammaticStep("call-add", "call", "add", arguments=(2, 3))
        first = self.plane.require(step)
        second = self.plane.require(step)
        self.assertEqual(first.result.inline_value, 5)
        self.assertEqual(first.step_sha256, step.step_sha256)
        self.assertEqual(first.receipt_id, second.receipt_id)
        self.assertEqual(first.result.sha256, second.result.sha256)

    def test_map_preserves_input_order(self) -> None:
        receipt = self.plane.require(ProgrammaticStep("map-square", "map", "square", items=(3, 1, 2)))
        self.assertEqual(receipt.result.inline_value, [9, 1, 4])
        self.assertEqual(receipt.input_items, 3)

    def test_parallel_preserves_input_order_for_pure_callable(self) -> None:
        receipt = self.plane.require(
            ProgrammaticStep("parallel-square", "parallel", "square", items=(5, 2, 4, 1), max_workers=4)
        )
        self.assertEqual(receipt.result.inline_value, [25, 4, 16, 1])
        self.assertTrue(receipt.pure_function)

    def test_parallel_rejects_impure_callable(self) -> None:
        receipt = self.plane.execute(ProgrammaticStep("parallel-impure", "parallel", "impure", items=(1, 2)))
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.error_type, "PermissionError")
        self.assertIsNone(receipt.result)

    def test_filter_returns_original_matching_items(self) -> None:
        receipt = self.plane.require(ProgrammaticStep("filter-even", "filter", "is-even", items=(1, 2, 3, 4, 5, 6)))
        self.assertEqual(receipt.result.inline_value, [2, 4, 6])

    def test_reduce_supports_explicit_initial_value(self) -> None:
        receipt = self.plane.require(
            ProgrammaticStep("reduce-sum", "reduce", "sum", items=(1, 2, 3), initial=10, has_initial=True)
        )
        self.assertEqual(receipt.result.inline_value, 16)

    def test_reduce_without_initial_rejects_empty_input(self) -> None:
        receipt = self.plane.execute(ProgrammaticStep("reduce-empty", "reduce", "sum", items=()))
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.error_type, "ValueError")

    def test_large_result_externalizes_to_existing_artifact_store(self) -> None:
        value = [f"row-{index}-" + "x" * 40 for index in range(8)]
        receipt = self.plane.require(ProgrammaticStep("externalize", "call", "identity", arguments=(value,)))
        result = receipt.result
        self.assertTrue(result.externalized)
        self.assertIsNone(result.inline_value)
        self.assertIsInstance(result.artifact, ArtifactReference)
        self.assertTrue(result.exact_recovery)
        self.assertEqual(self.plane.recover(result.artifact), value)
        self.assertTrue(self.plane.artifacts.verify(result.artifact.artifact_id)["ok"])

    def test_artifact_reference_can_feed_a_later_call(self) -> None:
        value = [f"item-{index}-" + "y" * 40 for index in range(8)]
        first = self.plane.require(ProgrammaticStep("produce", "call", "identity", arguments=(value,)))
        self.assertIsNotNone(first.result.artifact)
        second = self.plane.require(
            ProgrammaticStep("consume", "call", "length", arguments=(first.result.artifact,))
        )
        self.assertEqual(second.result.inline_value, len(value))

    def test_item_limit_fails_closed(self) -> None:
        receipt = self.plane.execute(
            ProgrammaticStep("too-many", "map", "identity", items=tuple(range(9)))
        )
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.error_type, "ValueError")
        self.assertIn("item limit exceeded", receipt.error_message)

    def test_unknown_function_fails_before_execution(self) -> None:
        with self.assertRaises(KeyError):
            self.plane.execute(ProgrammaticStep("unknown", "call", "missing", arguments=(1,)))

    def test_failure_receipt_redacts_secret_and_is_bounded(self) -> None:
        receipt = self.plane.execute(ProgrammaticStep("explode", "call", "explode", arguments=(1,)))
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.error_type, "ValueError")
        self.assertNotIn("sk-proj-", receipt.error_message)
        self.assertIn("<redacted:", receipt.error_message)
        self.assertLessEqual(len(receipt.error_message), 2000)

    def test_non_json_result_fails_closed(self) -> None:
        self.registry.register("set-result", lambda: {1, 2, 3})
        receipt = self.plane.execute(ProgrammaticStep("bad-json", "call", "set-result"))
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.error_type, "TypeError")

    def test_duplicate_registry_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.registry.register("add", lambda left, right: left + right)

    def test_invalid_operation_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ProgrammaticStep("invalid", "eval", "identity")


if __name__ == "__main__":
    unittest.main()
