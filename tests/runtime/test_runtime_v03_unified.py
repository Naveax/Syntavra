from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from signalcore_runtime.compression import ContentRouter, ReversibleContentStore
from signalcore_runtime.evidence import EvidenceStore
from signalcore_runtime.host_adapters import KNOWN_HOSTS, detect_hosts, negotiate
from signalcore_runtime.installer import HostInstaller
from signalcore_runtime.output_governor import OutputGovernor
from signalcore_runtime.sandbox import SandboxError, SandboxManager, SandboxPolicy
from signalcore_runtime.session_runtime import SessionRuntime
from signalcore_runtime.signalbench import ArmSpec, RunResult, SignalBenchRunner, TaskSpec
from signalcore_runtime.structural import StructuralIndex
from signalcore_runtime.structural_parsers import ParserRegistry


class RuntimeV03UnifiedTests(unittest.TestCase):
    def project(self, root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        return project

    def test_multilanguage_parser_registry(self) -> None:
        registry = ParserRegistry(Path.cwd())
        fixtures = {
            "a.py": "class A:\n    def run(self):\n        return helper()\n",
            "a.ts": "export class A extends Base { run(){ return helper(); } }",
            "a.rs": "pub struct A; impl A { pub fn run() { helper(); } }",
            "a.go": "package x\nfunc Run(){ Helper() }",
            "A.java": "class A extends Base { void run(){ helper(); } }",
            "A.cs": "class A : Base { void Run(){ Helper(); } }",
            "a.cpp": "class A {}; void run(){ helper(); }",
            "a.rb": "class A\n def run\n helper()\n end\nend",
            "a.php": "<?php class A { function run(){ helper(); } }",
            "a.luau": "local function run() helper() end",
        }
        for path, text in fixtures.items():
            result = registry.parse(path, text)
            self.assertTrue(result.symbols, path)
            self.assertTrue(result.parser, path)

    def test_structural_index_cross_language_impact_and_repo_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self.project(root)
            (project / "core.py").write_text("def target():\n    return 1\n", encoding="utf-8")
            (project / "api.py").write_text("from core import target\ndef route():\n    return target()\n", encoding="utf-8")
            (project / "web.ts").write_text("export function view(){ return route(); }\n", encoding="utf-8")
            (project / "tests").mkdir()
            (project / "tests" / "test_api.py").write_text("from api import route\ndef test_route(): assert route()\n", encoding="utf-8")
            index = StructuralIndex(root / "struct.db", repository_root=project, repository_id="repo")
            indexed = index.index()
            self.assertEqual(indexed["total"], 4)
            impact = index.inspect_impact("target", max_depth=8)
            paths = set(impact["affected_paths"])
            self.assertIn("api.py", paths)
            self.assertTrue(impact["affected_tests"])
            repo_map = index.repository_map("target route", token_budget=500, max_depth=8)
            self.assertLessEqual(repo_map["used"], 500)
            self.assertTrue(repo_map["selected"])

    def test_host_registry_and_installer_idempotent_restore(self) -> None:
        self.assertGreaterEqual(len(KNOWN_HOSTS), 14)
        self.assertEqual(negotiate("claude-code")["mode"], "HOOK_ENFORCED")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self.project(root)
            skill = root / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("signalcore", encoding="utf-8")
            (project / ".claude").mkdir()
            settings = project / ".claude" / "settings.json"
            settings.write_text(json.dumps({"existing": True}), encoding="utf-8")
            installer = HostInstaller(project=project, skill_root=skill, home=root / "home")
            first = installer.install(["claude-code"])
            second = installer.install(["claude-code"])
            self.assertTrue(first["ok"] and second["ok"])
            value = json.loads(settings.read_text())
            self.assertTrue(value["existing"])
            self.assertEqual(value["signalcore"]["version"], "0.6.0")
            self.assertEqual(len(value["hooks"]["PreToolUse"]), 1)
            doctor = installer.doctor()
            self.assertTrue(doctor["ok"])
            uninstalled = installer.uninstall()
            self.assertTrue(uninstalled["ok"])
            restored = json.loads(settings.read_text())
            self.assertEqual(restored, {"existing": True})

    def test_sandbox_fail_closed_and_local_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self.project(root)
            evidence = EvidenceStore(root / "evidence", project_id="p")
            manager = SandboxManager(root / "sandbox", project=project, evidence=evidence)
            with mock.patch.object(manager, "backends", return_value={"docker": None, "podman": None, "bwrap": None, "local-restricted": sys.executable}):
                with self.assertRaises(SandboxError):
                    manager.plan([sys.executable, "-c", "print(1)"], policy=SandboxPolicy(network="none", strict=True))
                policy = SandboxPolicy(network="inherit", backend="local-restricted", strict=False, timeout_seconds=10)
                plan = manager.plan([sys.executable, "-c", "print('sandbox-ok')"], policy=policy)
                self.assertFalse(plan.guarantees["network_isolated"])
                result = manager.execute([sys.executable, "-c", "print('sandbox-ok')"], policy=policy)
                self.assertEqual(result.exit_code, 0)
                self.assertIn("sandbox-ok", result.summary)
                self.assertTrue(result.evidence_handle.startswith("sc://sha256/"))
            with self.assertRaises(SandboxError):
                manager.read("../outside")

    def test_reversible_compression_all_core_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self.project(root)
            evidence = EvidenceStore(root / "evidence", project_id="p")
            store = ReversibleContentStore(root / "compression.db", evidence=evidence, chunk_size=1024)
            router = ContentRouter(store, repository_root=project)
            fixtures = [
                ("data.json", json.dumps({"secret": "abc", "rows": [{"id": i, "name": "x" * 20} for i in range(100)]})),
                ("table.csv", "a,b\n" + "\n".join(f"{i},{i*i}" for i in range(200))),
                ("code.py", "def alpha(x):\n    return x + 1\n" * 100),
                ("trace.log", "INFO repeated\n" * 500 + "ERROR failed at app.py:42\n"),
                ("change.diff", "diff --git a/a b/a\n@@ -1 +1 @@\n-old\n+new\n" * 50),
            ]
            for path, text in fixtures:
                result = router.compress(text, path=path, budget_bytes=1024)
                self.assertLessEqual(result.visible_bytes, 1024)
                self.assertTrue(store.verify_roundtrip(result.compression_id), path)
                self.assertEqual(store.restore(result.compression_id), text.encode(), path)
                self.assertIn("SignalCore CCR", result.visible_text)
            secret = router.compress("password=supersecret\nERROR denied", path="a.log")
            self.assertNotIn("supersecret", secret.visible_text)
            self.assertEqual(store.restore(secret.compression_id), b"password=supersecret\nERROR denied")

    def test_session_runtime_exact_recovery_fork_merge_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = SessionRuntime(root / "sessions.db", project_id="p")
            session = runtime.create_session(metadata={"goal": "v03"})
            for index in range(130):
                runtime.append(session.session_id, "event", {"index": index, "value": "x" * 30})
            verification = runtime.verify(session.session_id)
            self.assertTrue(verification["ok"])
            summary = runtime.compact(session.session_id, leaf_size=8, fanout=4, force=True)
            expanded = runtime.expand_summary(summary)
            self.assertEqual(expanded["coverage"], 130)
            active = runtime.active_context(session.session_id, token_budget=600, recent_events=8)
            self.assertLessEqual(active["used"], 600)
            child = runtime.fork(session.session_id)
            merged = runtime.merge((session.session_id, child.session_id))
            self.assertEqual(len(merged.parent_ids), 2)
            export_path = root / "session.json"
            exported = runtime.export(session.session_id, export_path)
            self.assertEqual(exported["events"], 130)
            imported = runtime.import_session(export_path, new_session_id="imported")
            self.assertTrue(runtime.verify(imported.session_id)["ok"])
            self.assertTrue(runtime.recover()["ok"])

    def test_output_governor_contract_and_critical_preservation(self) -> None:
        governor = OutputGovernor("compact")
        result = governor.render({
            "result": "Implemented",
            "changed_files": ["src/app.py:42", "src/app.py:42"],
            "behavior": "Sure",
            "verification": "34 tests passed",
            "limitations": "WARNING security boundary remains",
            "evidence": "sc://sha256/" + "a" * 64,
        }, contract="implementation")
        self.assertLessEqual(result["bytes"], 4096)
        self.assertIn("src/app.py:42", result["text"])
        self.assertIn("WARNING", result["text"])
        with self.assertRaises(ValueError):
            governor.render({"changed_files": []}, contract="implementation")
        compacted = governor.compact_text("Sure\nERROR failed app.py:9\nERROR failed app.py:9\nDone")
        self.assertIn("ERROR failed app.py:9", compacted["text"])

    def test_signalbench_protocol_and_fail_closed_compare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self.project(root)
            (repository / "x.txt").write_text("x", encoding="utf-8")
            task = TaskSpec("t1", "known-edit", "Do it", str(repository), "tree", (sys.executable, "-c", "raise SystemExit(0)"))
            arm1 = ArmSpec("base", "host", (sys.executable, "adapter.py"), "1", "m", "high", 1000)
            arm2 = ArmSpec("candidate", "host", (sys.executable, "adapter.py"), "1", "m", "high", 1000)
            runner = SignalBenchRunner(root / "bench")
            self.assertTrue(runner.validate([task], [arm1, arm2])["ok"])
            common = dict(task_id="t1", repetition=0, success=True, verifier_success=True, verified_work=1.0,
                          wall_seconds=1.0, exit_code=0, fresh_input_tokens=1, cached_input_tokens=0,
                          output_tokens=1, reasoning_tokens=0, model_turns=1, tool_calls=1, wait_calls=0,
                          compactions=0, security_regressions=0, verifier_skips=0, repository_tree="tree",
                          prompt_hash="p", verifier_hash="v", permissions_hash="r", cache_mode="cold", artifact_dir="a")
            base = RunResult(run_id="b", arm_id="base", quota_cost=10.0, **common)
            cand = RunResult(run_id="c", arm_id="candidate", quota_cost=1.0, **common)
            comparison = runner.compare([base, cand], baseline_arm="base", candidate_arm="candidate")
            self.assertEqual(comparison["valid_pairs"], 1)
            self.assertEqual(comparison["claim"], "NOT_PROVEN")  # CI cannot be established from one pair.
            bad = RunResult(**{**asdict(cand), "run_id": "bad", "security_regressions": 1})
            self.assertFalse(runner.compare([base, bad], baseline_arm="base", candidate_arm="candidate")["claimable_superiority"])

    def test_bundled_skill_is_available_outside_repository_layout(self) -> None:
        from signalcore_runtime import cli
        bundled = Path(cli.__file__).resolve().parent / "bundled_skill"
        self.assertTrue((bundled / "SKILL.md").is_file())
        self.assertIn('version: "0.6.0"', (bundled / "SKILL.md").read_text(encoding="utf-8"))

    def test_host_detection_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self.project(root)
            home = root / "home"
            home.mkdir()
            (project / ".cursor").mkdir()
            (home / ".codex").mkdir()
            hosts = {row["host"] for row in detect_hosts(project, home=home)}
            self.assertIn("cursor", hosts)
            self.assertIn("codex", hosts)


if __name__ == "__main__":
    unittest.main()
