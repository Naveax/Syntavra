from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.tool_externalization import ExternalizationPolicy, ToolOutputExternalizer, ToolPayload


class MemoryEvidence:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put(self, data: bytes, *, kind: str = "generic", metadata=None) -> str:
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        handle = f"sc://sha256/{digest}"
        self.values[handle] = bytes(data)
        return handle

    def get(self, handle: str, *, max_bytes=None) -> bytes:
        data = self.values[handle]
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("too large")
        return data

    def verify(self, handle: str) -> bool:
        return handle in self.values


class DeltaToolResponseProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="syntavra-te-p0-09-")
        self.evidence = MemoryEvidence()
        self.engine = ToolOutputExternalizer(
            Path(self.temp.name) / "externalizer.sqlite3",
            evidence=self.evidence,
            policy=ExternalizationPolicy.for_profile("compact"),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _large_log(marker: str = "") -> str:
        body = "\n".join(f"INFO request={index} route=/api" for index in range(5000)) + "\n"
        return body + marker

    def test_delta_requires_validated_invalidation_fingerprint(self) -> None:
        initial = self._large_log()
        first = self.engine.externalize(
            ToolPayload(command="service logs", stdout=initial, path="service.log", scope_key="s")
        )
        current = self.engine.externalize(
            ToolPayload(
                command="service logs",
                stdout=initial + "ERROR changed at app.py:9\n",
                path="service.log",
                scope_key="s",
            )
        )
        self.assertNotEqual(current.mode, "delta-externalized")
        self.assertIsNone(current.baseline_artifact_id)
        self.assertEqual(current.metadata["delta_protocol"]["baseline_status"], "CURRENT_INVALIDATION_MISSING")
        self.assertEqual(self.engine.restore(first.artifact_id), initial.encode())
        self.assertIn("ERROR changed", self.engine.restore(current.artifact_id).decode())

    def test_stable_fingerprint_emits_exact_delta_with_baseline_identity(self) -> None:
        metadata = {"invalidation_fingerprint": "workspace-generation-stable"}
        initial = self._large_log()
        first = self.engine.externalize(
            ToolPayload(command="service logs", stdout=initial, path="service.log", scope_key="s", metadata=metadata)
        )
        updated_raw = initial + "FATAL delta needle src/security.py:91\n"
        current = self.engine.externalize(
            ToolPayload(command="service logs", stdout=updated_raw, path="service.log", scope_key="s", metadata=metadata)
        )
        protocol = current.metadata["delta_protocol"]
        self.assertEqual(current.mode, "delta-externalized")
        self.assertEqual(current.baseline_artifact_id, first.artifact_id)
        self.assertEqual(protocol["baseline_status"], "VALID")
        self.assertTrue(protocol["exact_baseline_verified"])
        self.assertTrue(protocol["delta_emitted"])
        self.assertEqual(len(protocol["invalidation_fingerprint"]), 64)
        self.assertIn(first.artifact_id, current.preview)
        self.assertIn(protocol["invalidation_fingerprint"], current.preview)
        self.assertIn("security.py:91", current.preview)
        self.assertEqual(self.engine.restore(current.artifact_id), updated_raw.encode())
        self.assertTrue(self.engine.verify(current.artifact_id)["ok"])

    def test_changed_invalidation_fingerprint_falls_back_to_full_current_protocol(self) -> None:
        initial = self._large_log()
        first = self.engine.externalize(
            ToolPayload(
                command="service logs",
                stdout=initial,
                path="service.log",
                scope_key="s",
                metadata={"invalidation_fingerprint": "workspace-a"},
            )
        )
        current_raw = initial + "ERROR changed generation at app.py:22\n"
        current = self.engine.externalize(
            ToolPayload(
                command="service logs",
                stdout=current_raw,
                path="service.log",
                scope_key="s",
                metadata={"invalidation_fingerprint": "workspace-b"},
            )
        )
        self.assertNotEqual(current.mode, "delta-externalized")
        self.assertIsNone(current.baseline_artifact_id)
        self.assertEqual(current.metadata["delta_protocol"]["baseline_status"], "INVALIDATION_MISMATCH")
        self.assertEqual(self.engine.restore(first.artifact_id), initial.encode())
        self.assertEqual(self.engine.restore(current.artifact_id), current_raw.encode())

    def test_same_bytes_with_changed_invalidation_are_not_deduplicated(self) -> None:
        raw = self._large_log()
        first = self.engine.externalize(
            ToolPayload(
                command="service logs",
                stdout=raw,
                path="service.log",
                scope_key="s",
                metadata={"invalidation_fingerprint": "generation-a"},
            )
        )
        second = self.engine.externalize(
            ToolPayload(
                command="service logs",
                stdout=raw,
                path="service.log",
                scope_key="s",
                metadata={"invalidation_fingerprint": "generation-b"},
            )
        )
        self.assertNotEqual(first.artifact_id, second.artifact_id)
        self.assertFalse(second.repeated)
        self.assertNotEqual(second.mode, "dedup-reference")

    def test_missing_exact_baseline_disables_delta(self) -> None:
        metadata = {"invalidation_fingerprint": "stable-exact-check"}
        initial = self._large_log()
        first = self.engine.externalize(
            ToolPayload(command="service logs", stdout=initial, path="service.log", scope_key="s", metadata=metadata)
        )
        self.evidence.values.pop(first.exact_handle, None)
        current_raw = initial + "ERROR current remains exact app.py:31\n"
        current = self.engine.externalize(
            ToolPayload(command="service logs", stdout=current_raw, path="service.log", scope_key="s", metadata=metadata)
        )
        self.assertNotEqual(current.mode, "delta-externalized")
        self.assertIsNone(current.baseline_artifact_id)
        self.assertEqual(current.metadata["delta_protocol"]["baseline_status"], "BASELINE_EXACT_UNAVAILABLE")
        self.assertEqual(self.engine.restore(current.artifact_id), current_raw.encode())


if __name__ == "__main__":
    unittest.main()
