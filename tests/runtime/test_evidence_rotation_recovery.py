from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime import evidence_rotation
from syntavra_runtime.evidence import EvidenceError, EvidenceStore


class EvidenceRotationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "evidence"
        self.store = EvidenceStore(self.root, project_id="rotation-recovery-test")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _fixture(
        self,
        payload: bytes = b"rotation-recovery-payload",
    ) -> tuple[str, str, Path, Path, Path, Path, bytes, bytes]:
        handle = self.store.put(payload, reference="recovery-fixture")
        digest = handle.removeprefix("sc://sha256/")
        object_path = self.store._object_path(digest)
        metadata_path = self.store._metadata_path(digest)
        backup_path = object_path.with_name(f".{object_path.name}.rotate-backup")
        staging_path = object_path.with_name(f".{object_path.name}.rotate-2")
        return (
            handle,
            digest,
            object_path,
            metadata_path,
            backup_path,
            staging_path,
            object_path.read_bytes(),
            metadata_path.read_bytes(),
        )

    def test_staged_ciphertext_replace_failure_restores_original_object(self) -> None:
        payload = b"alpha" * 4096
        (
            handle,
            _digest,
            object_path,
            metadata_path,
            backup_path,
            staging_path,
            original_ciphertext,
            original_metadata,
        ) = self._fixture(payload)
        restore_path = object_path.with_name(f".{object_path.name}.rotate-restore")
        original_replace = evidence_rotation.os.replace

        def fail_staged_replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
            if Path(source) == staging_path and Path(target) == object_path:
                raise OSError("simulated staged ciphertext replace failure")
            original_replace(source, target)

        with patch.object(evidence_rotation.os, "replace", side_effect=fail_staged_replace):
            with self.assertRaisesRegex(EvidenceError, "evidence key rotation failed"):
                evidence_rotation.rotate_evidence_key(self.store, reencrypt=True)

        self.assertTrue(object_path.is_file())
        self.assertEqual(object_path.read_bytes(), original_ciphertext)
        self.assertEqual(metadata_path.read_bytes(), original_metadata)
        self.assertEqual(self.store.get(handle), payload)
        self.assertEqual(self.store.describe(handle)["encryption"]["key_version"], 1)
        self.assertFalse(backup_path.exists())
        self.assertFalse(staging_path.exists())
        self.assertFalse(restore_path.exists())

    def test_rollback_failure_preserves_last_known_good_ciphertext_backup(self) -> None:
        (
            handle,
            _digest,
            object_path,
            metadata_path,
            backup_path,
            _staging_path,
            original_ciphertext,
            _original_metadata,
        ) = self._fixture(b"beta" * 4096)
        original_replace = evidence_rotation.os.replace
        original_atomic_write = evidence_rotation.atomic_write_json
        object_replace_count = 0
        failed_metadata_update = False

        def fail_rollback_replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
            nonlocal object_replace_count
            if Path(target) == object_path:
                object_replace_count += 1
                if object_replace_count == 2:
                    raise OSError("simulated rollback object restore failure")
            original_replace(source, target)

        def fail_first_rotated_metadata_write(path: Path, value: object, *, mode: int = 0o600) -> None:
            nonlocal failed_metadata_update
            key_version = None
            if isinstance(value, dict):
                encryption = value.get("encryption")
                if isinstance(encryption, dict):
                    key_version = encryption.get("key_version")
            if Path(path) == metadata_path and key_version == 2 and not failed_metadata_update:
                failed_metadata_update = True
                raise OSError("simulated rotated metadata write failure")
            original_atomic_write(path, value, mode=mode)

        with (
            patch.object(evidence_rotation.os, "replace", side_effect=fail_rollback_replace),
            patch.object(evidence_rotation, "atomic_write_json", side_effect=fail_first_rotated_metadata_write),
        ):
            with self.assertRaises(EvidenceError) as raised:
                evidence_rotation.rotate_evidence_key(self.store, reencrypt=True)

        message = str(raised.exception)
        self.assertIn("rollback failed", message)
        self.assertIn("backup preserved", message)
        self.assertIn(str(backup_path), message)
        self.assertTrue(backup_path.is_file())
        self.assertEqual(backup_path.read_bytes(), original_ciphertext)
        self.assertTrue(object_path.is_file())
        self.assertEqual(self.store.describe(handle)["encryption"]["key_version"], 1)


if __name__ == "__main__":
    unittest.main()
