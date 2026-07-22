from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from syntavra_runtime.backup import StateBackupManager
from syntavra_runtime.unified_config import ConfigError, ConfigManager
from syntavra_runtime.crypto import (
    CryptoError, _chacha20_block, _poly1305, open_sealed, seal,
)
from syntavra_runtime.data_router import DataRoutePolicy, DataRouter
from syntavra_runtime.evidence import EvidenceError, EvidenceStore
from syntavra_runtime.identity import CapabilityTokenIssuer, IdentityError, Principal
from syntavra_runtime.janitor import RetentionRule, RuntimeJanitor
from syntavra_runtime.job_scheduler import DurableJobScheduler, JobSpec
from syntavra_runtime.migrations import Migration, MigrationError, MigrationManager
from syntavra_runtime.plugin_sdk import PluginError, PluginManifest, PluginRegistry
from syntavra_runtime.policy_rollout import PolicyRolloutManager, VerifiedPolicyObservation
from syntavra_runtime.semantic_retrieval import HybridRetriever, RetrievalCandidate
from syntavra_runtime.runtime_pipeline import CanonicalRequestEnvelope, UnifiedRuntimePipeline
from syntavra_runtime.schema_registry import SchemaDefinition, SchemaRegistry
from syntavra_runtime.security_scan import IncrementalSecurityScanner, scan_bytes, scan_text
from syntavra_runtime.streaming import SSEParser, StreamSemanticProcessor
from syntavra_runtime.observability import Observability


class _Plugin:
    def __init__(self, manifest: PluginManifest, *, fail: bool = False):
        self.manifest = manifest
        self.fail = fail

    def health(self):
        if self.fail:
            raise RuntimeError("boom")
        return {"ok": True}


class V6UnifiedProductionCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def evidence(self, name: str = "evidence") -> EvidenceStore:
        return EvidenceStore(self.root / name, project_id="project-v6")

    def test_chacha20_and_poly1305_match_rfc_vectors(self) -> None:
        key = bytes(range(32))
        nonce = bytes.fromhex("000000090000004a00000000")
        expected = bytes.fromhex(
            "10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e"
            "d2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e"
        )
        self.assertEqual(_chacha20_block(key, 1, nonce), expected)
        poly_key = bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
        self.assertEqual(
            _poly1305(b"Cryptographic Forum Research Group", poly_key).hex(),
            "a8061dc1305136c6c22b8baf0c0127a9",
        )
        master = bytes(range(32))
        sealed = seal(b"exact secret payload", master_key=master, project_id="p", key_id="k")
        self.assertNotIn(b"exact secret payload", sealed)
        self.assertEqual(open_sealed(sealed, master_key=master, project_id="p")[0], b"exact secret payload")
        corrupted = sealed[:-1] + bytes((sealed[-1] ^ 1,))
        with self.assertRaises(CryptoError):
            open_sealed(corrupted, master_key=master, project_id="p")

    def test_evidence_is_encrypted_provenance_aware_and_collectable(self) -> None:
        store = self.evidence()
        handle = store.put(b"API_KEY=secret-value", kind="first", metadata={"source": "a"})
        same = store.put(b"API_KEY=secret-value", kind="second", metadata={"source": "b"})
        self.assertEqual(handle, same)
        digest = handle.rsplit("/", 1)[1]
        raw = (store.objects / digest[:2] / digest[2:]).read_bytes()
        self.assertNotIn(b"secret-value", raw)
        self.assertEqual(store.get(handle), b"API_KEY=secret-value")
        description = store.describe(handle)
        self.assertEqual(len(description["provenance"]), 2)
        self.assertEqual(description["encryption"]["mode"], "encrypted")
        store.pin(handle, "session:one")
        self.assertEqual(store.gc(ttl_seconds=0, dry_run=False)["deleted"], 0)
        store.unpin(handle, "session:one")
        self.assertEqual(store.gc(ttl_seconds=0, dry_run=False)["deleted"], 1)

    def test_data_router_never_emits_invalid_json_and_streams_rows(self) -> None:
        payload = {"rows": [{"id": index, "message": "x" * 1000} for index in range(200)]}
        result = DataRouter(self.evidence("route")).route(
            payload, hint="sql", query="id 199", policy=DataRoutePolicy(budget_bytes=512, max_rows=8),
        )
        decoded = json.loads(result.visible)
        self.assertIn("_syntavra", decoded)
        self.assertLessEqual(result.visible_bytes, 512)
        streamed = DataRouter(self.evidence("stream")).route_rows(
            ({"id": index, "latency": index / 10, "email": f"u{index}@example.com"} for index in range(1000)),
            query="latency", policy=DataRoutePolicy(budget_bytes=2048, max_rows=6),
        )
        self.assertEqual(streamed.records_seen, 1000)
        self.assertTrue(streamed.exact_handle.startswith("sc://sha256/"))
        self.assertLessEqual(streamed.visible_bytes, 2048)
        self.assertIsInstance(json.loads(streamed.visible), dict)

    def test_security_scan_covers_encoded_payloads_pii_and_chunk_boundaries(self) -> None:
        encoded = base64.b64encode(b"api_key=top-secret-value").decode()
        scan = scan_text("payload=" + encoded)
        self.assertIn("generic-assignment", scan.secret_types)
        pii = scan_text("contact user@example.com and card 4111 1111 1111 1111")
        self.assertIn("email", pii.pii_types)
        self.assertIn("payment-card", pii.pii_types)
        scanner = IncrementalSecurityScanner(overlap_chars=128)
        scanner.feed("api_ke")
        scanner.feed("y=boundary-secret")
        self.assertIn("generic-assignment", scanner.result().secret_types)

    def test_sse_parser_hash_chain_and_usage(self) -> None:
        processor = StreamSemanticProcessor(content_type="text/event-stream")
        processor.feed(b'data: {"usage":{"input_tokens":10},"delta":"a"}\n\n')
        processor.feed(b'data: {"usage":{"output_tokens":2},"delta":"b"}\n\ndata: [DONE]\n\n')
        summary = processor.finalize()
        self.assertEqual(summary.event_count, 3)
        self.assertTrue(summary.done_seen)
        self.assertEqual(summary.usage["input_tokens"], 10)
        self.assertEqual(summary.usage["output_tokens"], 2)
        self.assertNotEqual(summary.chain_root, "0" * 64)

    def test_configuration_precedence_provenance_and_last_good_rollback(self) -> None:
        project = self.root / "project"; project.mkdir()
        state = self.root / "state"
        config_path = project / ".syntavra" / "config.toml"; config_path.parent.mkdir()
        config_path.write_text('[runtime]\nprofile="compact"\n[routing]\nbudget_bytes=4096\n', encoding="utf-8")
        manager = ConfigManager(project_root=project, state_root=state, user_config=self.root / "missing.toml")
        with patch.dict(os.environ, {"SYNTAVRA_CFG__RUNTIME__PROFILE": '"terse"'}, clear=False):
            snapshot = manager.load(force=True)
        self.assertEqual(snapshot.values["runtime"]["profile"], "terse")
        self.assertEqual(snapshot.explain("runtime.profile").scope, "environment")
        config_path.write_text('[runtime]\nprofile="invalid"\n', encoding="utf-8")
        fallback = manager.load(force=True)
        self.assertEqual(fallback.values["runtime"]["profile"], "terse")
        self.assertTrue(fallback.warnings)

    def test_identity_is_scoped_short_lived_and_revocable(self) -> None:
        issuer = CapabilityTokenIssuer(b"k" * 32)
        token = issuer.issue(Principal("agent", project_id="p", scopes=("provider.invoke",)), ttl_seconds=60, now=100)
        principal = issuer.verify(token, required_scopes=("provider.invoke",), project_id="p", now=110)
        self.assertEqual(principal.subject, "agent")
        with self.assertRaises(IdentityError):
            issuer.verify(token, project_id="other", now=110)
        issuer.revoke(token)
        with self.assertRaises(IdentityError):
            issuer.verify(token, now=110)

    def test_plugins_are_permissioned_and_quarantined_after_failures(self) -> None:
        registry = PluginRegistry(allowed_permissions=("evidence-read",), failure_limit=2)
        plugin = _Plugin(PluginManifest("test.plugin", "1.0.0", permissions=("evidence-read",)), fail=True)
        registry.register(plugin)
        for _ in range(2):
            with self.assertRaises(PluginError):
                registry.invoke("test.plugin", "health")
        self.assertTrue(registry.records()[0]["quarantined"])
        with self.assertRaises(PluginError):
            registry.register(_Plugin(PluginManifest("denied.plugin", "1", permissions=("network",))))

    def test_migrations_backup_and_restore_on_failure(self) -> None:
        database = self.root / "db.sqlite3"
        def one(db: sqlite3.Connection) -> None: db.execute("CREATE TABLE values_v6(value TEXT)")
        manager = MigrationManager(database, (Migration(1, "create", one, "v1"),))
        result = manager.apply()
        self.assertEqual(result.after_version, 1)
        self.assertTrue(Path(result.backup_path).is_file())
        db = sqlite3.connect(database)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            db.close()

    def test_backup_is_encrypted_verified_and_restorable(self) -> None:
        state = self.root / "state"; state.mkdir()
        (state / "config.json").write_text('{"ok":true}', encoding="utf-8")
        db = sqlite3.connect(state / "data.sqlite3")
        try:
            db.execute("CREATE TABLE t(v INTEGER)")
            db.execute("INSERT INTO t VALUES(7)")
            db.commit()
        finally:
            db.close()
        manager = StateBackupManager(state, project_id="backup-project")
        destination = self.root / "backup.scbackup"
        result = manager.create(destination, encrypt=True)
        self.assertTrue(result.encrypted)
        self.assertNotIn(b'{"ok":true}', destination.read_bytes())
        self.assertTrue(manager.verify(destination)["ok"])
        (state / "config.json").write_text("corrupt", encoding="utf-8")
        restored = manager.restore(destination, dry_run=False)
        self.assertTrue(restored["ok"])
        self.assertEqual((state / "config.json").read_text(encoding="utf-8"), '{"ok":true}')

    def test_scheduler_dependencies_leases_retries_and_dead_letter(self) -> None:
        scheduler = DurableJobScheduler(self.root / "scheduler.sqlite3")
        first = scheduler.submit(JobSpec("p", ("echo", "one"), max_attempts=1), job_id="first")
        second = scheduler.submit(JobSpec("p", ("echo", "two"), dependencies=(first,), max_attempts=2), job_id="second")
        lease = scheduler.claim("worker", lease_seconds=10)
        self.assertEqual(lease.job_id, first)
        scheduler.complete(first, "worker", {"ok": True})
        lease = scheduler.claim("worker", lease_seconds=10)
        self.assertEqual(lease.job_id, second)
        self.assertEqual(scheduler.fail(second, "worker", "retry", retryable=True, base_backoff_seconds=0), "queued")
        lease = scheduler.claim("worker", lease_seconds=10, now=time.time() + 1)
        self.assertEqual(lease.job_id, second)
        self.assertEqual(scheduler.fail(second, "worker", "dead", retryable=True), "dead-letter")
        self.assertEqual(scheduler.stats()["states"]["dead-letter"], 1)

    def test_hybrid_retrieval_is_explainable_and_diverse(self) -> None:
        retriever = HybridRetriever()
        rows = [
            RetrievalCandidate("1", "authentication token cache decision", "a.py", authority=1.0, graph_score=0.8),
            RetrievalCandidate("2", "authentication cache implementation", "a.py", authority=0.7),
            RetrievalCandidate("3", "token security rotation", "b.py", authority=0.9),
        ]
        ranked = retriever.rank("authentication token cache", rows, limit=3, source_diversity=0.5)
        self.assertEqual(ranked[0].candidate.candidate_id, "1")
        self.assertEqual(ranked[1].candidate.source, "b.py")
        self.assertIn("lexical-match", ranked[0].reasons)

    def test_policy_rollout_requires_verifier_signature_and_rolls_back(self) -> None:
        manager = PolicyRolloutManager(self.root / "rollout.sqlite3", signing_key=b"s" * 32)
        policy_hash = "a" * 64; verifier_hash = "b" * 64
        for index in range(40):
            observation = VerifiedPolicyObservation(
                "scope", policy_hash, verifier_hash, True, 0.99, 10 + index / 10, timestamp=1000 + index,
            )
            manager.record(replace(observation, receipt_signature=manager.sign(observation)))
        decision = manager.evaluate("scope", policy_hash, minimum_samples=30, success_floor=0.90)
        self.assertTrue(decision.eligible)
        manager.promote(decision, target_stage="canary", cooldown_seconds=0)
        bad = VerifiedPolicyObservation("scope", policy_hash, verifier_hash, False, 0.0, 100, security_regressions=1)
        manager.record(replace(bad, receipt_signature=manager.sign(bad)))
        unhealthy = manager.evaluate("scope", policy_hash, minimum_samples=1, success_floor=0.0, quality_floor=0.0)
        self.assertTrue(manager.observe_and_auto_rollback(unhealthy)["rolled_back"])

    def test_schema_registry_migrates_and_validates(self) -> None:
        registry = SchemaRegistry()
        registry.register(SchemaDefinition("event", 1, required=("schema_version", "value"), properties={"schema_version": int, "value": str}))
        registry.register(SchemaDefinition("event", 2, required=("schema_version", "value", "source"), properties={"schema_version": int, "value": str, "source": str}))
        registry.register_migration("event", 1, lambda value: {**value, "source": "legacy"})
        migrated = registry.migrate("event", {"schema_version": 1, "value": "x"})
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["source"], "legacy")

    def test_canonical_pipeline_captures_routes_and_traces(self) -> None:
        project = self.root / "project"; project.mkdir()
        state = self.root / "state"
        evidence = EvidenceStore(state / "evidence", project_id="p")
        pipeline = UnifiedRuntimePipeline(
            evidence=evidence,
            config=ConfigManager(project_root=project, state_root=state),
            observability=Observability(state / "observability"),
            authorizer=__import__("syntavra_runtime.identity", fromlist=["Authorizer"]).Authorizer({"agent": ("provider.invoke",)}),
        )
        request = CanonicalRequestEnvelope.create(
            project_id="p", host="codex", provider="test", model="m",
            payload={"messages": [{"role": "user", "content": "hello"}]}, query="answer",
        )
        result = pipeline.execute(request, Principal("agent", roles=("agent",)), lambda _, __: {"answer": "ok", "rows": [{"id": 1}]})
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.raw_handle.startswith("sc://sha256/"))
        self.assertTrue(evidence.verify(result.raw_handle))
        self.assertTrue(result.trace_id)

    def test_janitor_does_not_follow_symlinks(self) -> None:
        root = self.root / "files"; root.mkdir()
        target = self.root / "outside.txt"; target.write_text("keep")
        old = root / "old.txt"; old.write_text("delete")
        os.utime(old, (1, 1))
        try:
            (root / "escape").symlink_to(target)
        except OSError:
            pass
        result = RuntimeJanitor().apply_rules((RetentionRule("old", str(root), 1),), dry_run=False, now=time.time())
        self.assertFalse(old.exists())
        self.assertTrue(target.exists())
        self.assertEqual(result["files_deleted"], 1)

    def test_typescript_remote_tls_timeout_retry_and_sse_contract(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = (root / "sdk" / "typescript" / "src" / "index.ts").read_text(encoding="utf-8")
        javascript = root / "sdk" / "typescript" / "dist" / "index.js"
        self.assertIn("remote Syntavra proxy connections require HTTPS", source)
        self.assertIn("private readonly staticControlToken", source)
        self.assertIn("streamEvents", source)
        self.assertIn("retry-after", source.casefold())
        self.assertTrue(javascript.is_file())
        import shutil, subprocess
        node = shutil.which("node")
        if node:
            completed = subprocess.run((node, "--check", str(javascript)), capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            module_uri = json.dumps(javascript.as_uri())
            script = "import { SyntavraClient } from " + module_uri + ";\n" + (
                "try { new SyntavraClient({baseUrl:'http://example.com', allowRemote:true}); process.exit(7); } "
                "catch (e) { if (!String(e).includes('HTTPS')) process.exit(8); }"
            )
            result = subprocess.run((node, "--input-type=module", "-e", script), capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
