from __future__ import annotations

import json
import re
from pathlib import Path


def require_once(text: str, old: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}: {old[:160]!r}")
    return text


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    require_once(text, old, label=path)
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def harden_memory_runtime() -> None:
    path = Path("syntavra_runtime/memory_intelligence.py")
    text = path.read_text(encoding="utf-8")

    old = """        return True\n\n    def _record(self, memory_id: str) -> dict[str, Any]:\n"""
    new = """        return True\n\n    @staticmethod\n    def _scope_equal(candidate: Mapping[str, Any], requested: MemoryScope) -> bool:\n        return {\n            \"project_id\": str(candidate.get(\"project_id\") or \"\"),\n            \"user_id\": str(candidate.get(\"user_id\") or \"\"),\n            \"session_id\": str(candidate.get(\"session_id\") or \"\"),\n        } == requested.as_dict()\n\n    def _record(self, memory_id: str) -> dict[str, Any]:\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = """        for key in (\"scope_json\", \"provenance_json\", \"supersedes_json\", \"conflicts_json\"):\n            value[key.removesuffix(\"_json\")] = json.loads(value.pop(key))\n        return value\n\n    def remember(\n"""
    new = """        for key in (\"scope_json\", \"provenance_json\", \"supersedes_json\", \"conflicts_json\"):\n            value[key.removesuffix(\"_json\")] = json.loads(value.pop(key))\n        return value\n\n    def _record_visible(self, memory_id: str, scope: MemoryScope) -> dict[str, Any]:\n        record = self._record(memory_id)\n        if not self._scope_matches(record[\"scope\"], scope):\n            raise PermissionError(\"memory scope mismatch\")\n        return record\n\n    def _record_mutable(self, memory_id: str, scope: MemoryScope) -> dict[str, Any]:\n        record = self._record(memory_id)\n        if not self._scope_equal(record[\"scope\"], scope):\n            raise PermissionError(\"memory mutation scope mismatch\")\n        return record\n\n    def remember(\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = """        for related in (*supersedes_refs, *conflicts):\n            self._record(related)\n"""
    new = """        for related in (*supersedes_refs, *conflicts):\n            self._record_mutable(related, scope)\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = """            \"scope\": scope.as_dict(),\n            \"provenance_refs\": list(provenance),\n        }\n"""
    new = """            \"scope\": scope.as_dict(),\n            \"provenance_refs\": list(provenance),\n            \"supersedes\": list(supersedes_refs),\n            \"conflicts_with\": list(conflicts),\n        }\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    start_anchor = """        with self.store._connection() as db:\n            db.execute(\n                \"\"\"\n                INSERT INTO memory_retrieval_records(\n"""
    start = text.index(start_anchor, text.index("    def remember("))
    end_anchor = "        return self.recover(memory_id)\n"
    end = text.index(end_anchor, start) + len(end_anchor)
    new_block = """        with self.store._connection() as db:\n            cursor = db.execute(\n                \"\"\"\n                INSERT OR IGNORE INTO memory_retrieval_records(\n                  memory_id,observation_id,memory_kind,scope_json,provenance_json,\n                  supersedes_json,conflicts_json,state,created_at,updated_at\n                ) VALUES(?,?,?,?,?,?,?,?,?,?)\n                \"\"\",\n                (\n                    memory_id,\n                    observation.observation_id,\n                    normalized_kind,\n                    json.dumps(scope.as_dict(), ensure_ascii=False, sort_keys=True),\n                    json.dumps(list(provenance), ensure_ascii=False, sort_keys=True),\n                    json.dumps(list(supersedes_refs), ensure_ascii=False, sort_keys=True),\n                    json.dumps(list(conflicts), ensure_ascii=False, sort_keys=True),\n                    \"active\",\n                    now,\n                    now,\n                ),\n            )\n            inserted = cursor.rowcount == 1\n            if inserted:\n                for old_id in supersedes_refs:\n                    db.execute(\n                        \"UPDATE memory_retrieval_records SET state='superseded',updated_at=? WHERE memory_id=?\",\n                        (now, old_id),\n                    )\n                for conflict_id in conflicts:\n                    row = db.execute(\n                        \"SELECT conflicts_json FROM memory_retrieval_records WHERE memory_id=?\",\n                        (conflict_id,),\n                    ).fetchone()\n                    existing = set(json.loads(row[\"conflicts_json\"])) if row else set()\n                    existing.add(memory_id)\n                    db.execute(\n                        \"UPDATE memory_retrieval_records SET conflicts_json=?,updated_at=? WHERE memory_id=?\",\n                        (json.dumps(sorted(existing)), now, conflict_id),\n                    )\n        return self.recover(memory_id, scope=scope)\n"""
    text = text[:start] + new_block + text[end:]

    old = """    def forget(self, memory_id: str, *, reason: str) -> dict[str, Any]:\n        if not reason.strip():\n            raise ValueError(\"forget reason is required\")\n        self._record(memory_id)\n        now = time.time()\n        with self.store._connection() as db:\n            db.execute(\n                \"UPDATE memory_retrieval_records SET state='forgotten',updated_at=? WHERE memory_id=?\",\n                (now, memory_id),\n            )\n        recovered = self.recover(memory_id)\n        recovered[\"forget_reason\"] = reason.strip()\n        return recovered\n"""
    new = """    def forget(self, memory_id: str, *, scope: MemoryScope, reason: str) -> dict[str, Any]:\n        if not reason.strip():\n            raise ValueError(\"forget reason is required\")\n        self._record_mutable(memory_id, scope)\n        now = time.time()\n        with self.store._connection() as db:\n            db.execute(\n                \"UPDATE memory_retrieval_records SET state='forgotten',updated_at=? WHERE memory_id=?\",\n                (now, memory_id),\n            )\n        recovered = self.recover(memory_id, scope=scope)\n        recovered[\"forget_reason\"] = reason.strip()\n        return recovered\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = """        for memory_id in parents:\n            self._record(memory_id)\n"""
    new = """        for memory_id in parents:\n            self._record_mutable(memory_id, scope)\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = """    def recover(self, memory_id: str) -> dict[str, Any]:\n        record = self._record(memory_id)\n"""
    new = """    def recover(self, memory_id: str, *, scope: MemoryScope) -> dict[str, Any]:\n        record = self._record_visible(memory_id, scope)\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = "        base = self.store.search(expanded, limit=max(64, bounded_limit * 8), include_invalid=True)\n"
    new = "        base = self.store.search(expanded, limit=max(64, bounded_limit * 8), include_invalid=False)\n"
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = "        latest = max((float(row[\"updated_at\"]) for row in records), default=0.0)\n"
    new = """        records = [\n            record\n            for record in records\n            if self._scope_matches(json.loads(record[\"scope_json\"]), scope)\n        ]\n        latest = max((float(row[\"updated_at\"]) for row in records), default=0.0)\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = """        if include_session and self.session_memory is not None and scope.session_id:\n            if getattr(self.session_memory, \"project_id\", None) != scope.project_id:\n                raise RuntimeError(\"session memory project scope mismatch\")\n            verification = self.session_memory.verify(scope.session_id)\n            if verification.get(\"ok\") is not True:\n                raise RuntimeError(\"session memory chain verification failed\")\n            session_result = self.session_memory.retrieve(scope.session_id, query, limit=bounded_limit)\n            if session_result.get(\"exact_recovery\") is not True:\n                raise RuntimeError(\"session memory exact recovery failed\")\n"""
    new = """        if include_session and self.session_memory is not None and scope.session_id:\n            if getattr(self.session_memory, \"project_id\", None) != scope.project_id:\n                raise RuntimeError(\"session memory project scope mismatch\")\n            session = self.session_memory.describe(scope.session_id)\n            if str(session.get(\"project_id\") or \"\") != scope.project_id:\n                raise RuntimeError(\"session memory project scope mismatch\")\n            owner = str((session.get(\"metadata\") or {}).get(\"user_id\") or \"\")\n            if owner != scope.user_id:\n                raise RuntimeError(\"session memory user scope mismatch\")\n            verification = self.session_memory.verify(scope.session_id)\n            if verification.get(\"ok\") is not True:\n                raise RuntimeError(\"session memory chain verification failed\")\n            session_result = self.session_memory.retrieve(scope.session_id, query, limit=bounded_limit)\n            if session_result.get(\"exact_recovery\") is not True:\n                raise RuntimeError(\"session memory exact recovery failed\")\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = "        recovered = [self.recover(memory_id) for memory_id in ids]\n"
    new = "        recovered = [self.recover(memory_id, scope=scope) for memory_id in ids]\n"
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    old = """            \"scoped_retrieval\": True,\n            \"exact_recovery\": True,\n            \"cross_agent_handoff_receipts\": True,\n"""
    new = """            \"scoped_retrieval\": True,\n            \"scoped_recovery\": True,\n            \"scope_safe_mutation\": True,\n            \"session_user_binding\": True,\n            \"no_silent_reactivation\": True,\n            \"invalid_memory_excluded\": True,\n            \"exact_recovery\": True,\n            \"cross_agent_handoff_receipts\": True,\n"""
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    path.write_text(text, encoding="utf-8")


def harden_session_memory() -> None:
    path = Path("syntavra_runtime/session_memory.py")
    text = path.read_text(encoding="utf-8")
    old = """    @staticmethod\n    def _require_session(db: Any, session_id: str) -> Any:\n        session = db.execute(\"SELECT * FROM sessions WHERE session_id = ?\", (session_id,)).fetchone()\n        if not session:\n            raise KeyError(session_id)\n        return session\n\n    def open(self, session_id: str | None = None, *, parents: Sequence[str] = (), metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:\n"""
    new = """    @staticmethod\n    def _require_session(db: Any, session_id: str) -> Any:\n        session = db.execute(\"SELECT * FROM sessions WHERE session_id = ?\", (session_id,)).fetchone()\n        if not session:\n            raise KeyError(session_id)\n        return session\n\n    def describe(self, session_id: str) -> dict[str, Any]:\n        with _connect(self.path) as db:\n            session = self._require_session(db, session_id)\n        return dict(session) | {\n            \"parents\": json.loads(session[\"parents_json\"]),\n            \"metadata\": json.loads(session[\"metadata_json\"]),\n        }\n\n    def open(self, session_id: str | None = None, *, parents: Sequence[str] = (), metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:\n"""
    require_once(text, old, label=str(path))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def harden_memory_tests() -> None:
    path = Path("tests/runtime/test_memory_retrieval_v1.py")
    text = path.read_text(encoding="utf-8")
    old = '        self.sessions.open("session-a")\n'
    new = '        self.sessions.open("session-a", metadata={"user_id": "naveax"})\n'
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)
    text = re.sub(
        r'self\.engine\.recover\(([^\n,)]+\["memory_id"\])\)',
        r'self.engine.recover(\1, scope=self.scope)',
        text,
    )
    old = '        forgotten = self.engine.forget(new["memory_id"], reason="superseded by external authority")\n'
    new = '        forgotten = self.engine.forget(new["memory_id"], scope=self.scope, reason="superseded by external authority")\n'
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)

    marker = '\n\nif __name__ == "__main__":\n'
    require_once(text, marker, label=str(path))
    hardening_tests = '''
    def test_cross_scope_recovery_and_mutation_fail_closed(self) -> None:
        local = self.remember("local scoped memory", kind="project")
        foreign_scope = MemoryScope(project_id="other-project", user_id="other-user")
        foreign = self.engine.remember(
            "foreign scoped memory",
            kind="project",
            scope=foreign_scope,
            provenance_refs=("evidence:foreign",),
        )
        with self.assertRaises(PermissionError):
            self.engine.recover(foreign["memory_id"], scope=self.scope)
        with self.assertRaises(PermissionError):
            self.engine.forget(foreign["memory_id"], scope=self.scope, reason="must not cross scope")
        with self.assertRaises(PermissionError):
            self.engine.remember(
                "illegal cross-scope supersession",
                kind="semantic",
                scope=self.scope,
                provenance_refs=("evidence:cross-scope",),
                supersedes=(foreign["memory_id"],),
            )
        with self.assertRaises(PermissionError):
            self.engine.remember(
                "illegal cross-scope conflict",
                kind="semantic",
                scope=self.scope,
                provenance_refs=("evidence:cross-conflict",),
                conflicts_with=(foreign["memory_id"],),
            )
        with self.assertRaises(PermissionError):
            self.engine.consolidate(
                (local["memory_id"], foreign["memory_id"]),
                "illegal cross-scope consolidation",
                kind="semantic",
                scope=self.scope,
            )

    def test_duplicate_identity_does_not_reactivate_forgotten_memory(self) -> None:
        item = self.remember("logical forgetting remains durable", kind="semantic")
        self.engine.forget(item["memory_id"], scope=self.scope, reason="retire")
        duplicate = self.remember("logical forgetting remains durable", kind="semantic")
        self.assertEqual(duplicate["memory_id"], item["memory_id"])
        self.assertEqual(duplicate["state"], "forgotten")
        result = self.engine.retrieve("logical forgetting", scope=self.scope, include_session=False)
        self.assertNotIn(item["memory_id"], {row["memory_id"] for row in result["results"]})

    def test_lifecycle_relations_are_part_of_memory_identity(self) -> None:
        parent = self.remember("identity parent", kind="semantic")
        conflict = self.remember(
            "same lifecycle content",
            kind="semantic",
            provenance_refs=("evidence:lifecycle",),
            conflicts_with=(parent["memory_id"],),
        )
        superseding = self.remember(
            "same lifecycle content",
            kind="semantic",
            provenance_refs=("evidence:lifecycle",),
            supersedes=(parent["memory_id"],),
        )
        self.assertNotEqual(conflict["memory_id"], superseding["memory_id"])

    def test_invalid_memory_is_excluded_from_active_retrieval(self) -> None:
        invalid = self.remember("invalid evidence must not rank", kind="semantic", validity=0.0)
        result = self.engine.retrieve("invalid evidence", scope=self.scope, include_session=False)
        self.assertNotIn(invalid["memory_id"], {row["memory_id"] for row in result["results"]})

    def test_session_user_binding_fails_closed(self) -> None:
        self.sessions.open("session-other", metadata={"user_id": "other-user"})
        foreign_user_scope = MemoryScope(project_id="syntavra", user_id="naveax", session_id="session-other")
        with self.assertRaises(RuntimeError):
            self.engine.retrieve("anything", scope=foreign_user_scope)
        self.sessions.open("session-unowned")
        unowned_scope = MemoryScope(project_id="syntavra", user_id="naveax", session_id="session-unowned")
        with self.assertRaises(RuntimeError):
            self.engine.retrieve("anything", scope=unowned_scope)

    def test_project_global_memory_is_visible_but_not_mutable_from_private_scope(self) -> None:
        project_scope = MemoryScope(project_id="syntavra")
        global_item = self.engine.remember(
            "project global policy",
            kind="project",
            scope=project_scope,
            provenance_refs=("evidence:global",),
        )
        recovered = self.engine.recover(global_item["memory_id"], scope=self.scope)
        self.assertEqual(recovered["text"], "project global policy")
        with self.assertRaises(PermissionError):
            self.engine.forget(global_item["memory_id"], scope=self.scope, reason="private scope cannot mutate global")
'''
    path.write_text(text.replace(marker, hardening_tests + marker, 1), encoding="utf-8")


def harden_memory_certifier_and_contract() -> None:
    path = Path("tools/certify_memory_retrieval_v1.py")
    text = path.read_text(encoding="utf-8")
    old = '        sessions.open("session-cert")\n'
    new = '        sessions.open("session-cert", metadata={"user_id": "certifier"})\n'
    require_once(text, old, label=str(path))
    text = text.replace(old, new, 1)
    text = text.replace(
        'engine.recover(first["memory_id"])',
        'engine.recover(first["memory_id"], scope=scope)',
    )
    old = """        _require(status.get(\"new_persistent_database\") is False, \"parallel memory database introduced\")\n        _require(status.get(\"exact_recovery\") is True, \"runtime exact recovery disabled\")\n"""
    new = """        _require(status.get(\"new_persistent_database\") is False, \"parallel memory database introduced\")\n        _require(status.get(\"scoped_recovery\") is True, \"scoped memory recovery disabled\")\n        _require(status.get(\"scope_safe_mutation\") is True, \"memory mutation scope guard disabled\")\n        _require(status.get(\"session_user_binding\") is True, \"session user binding disabled\")\n        _require(status.get(\"no_silent_reactivation\") is True, \"memory may silently reactivate\")\n        _require(status.get(\"invalid_memory_excluded\") is True, \"invalid memory may be retrieved\")\n        _require(status.get(\"exact_recovery\") is True, \"runtime exact recovery disabled\")\n"""
    require_once(text, old, label=str(path))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")

    contract_path = Path("contracts/python/memory-retrieval-v1.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["scope"].update(
        {
            "recovery_requires_visible_scope": True,
            "relation_mutation_requires_exact_scope": True,
            "forget_requires_exact_scope": True,
            "consolidation_requires_exact_scope": True,
            "session_user_binding_required": True,
        }
    )
    contract["storage"].update(
        {
            "memory_identity_includes_lifecycle_relations": True,
            "duplicate_identity_does_not_reactivate": True,
            "invalid_memory_excluded_from_retrieval": True,
        }
    )
    contract_path.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def advance_registry() -> None:
    full_order = [
        "python_authority_v1",
        "capability_completeness_registry_v1",
        "rust_feature_freeze_guard_v1",
        "universal_context_item_v1",
        "evidence_store_v2",
        "typed_context_object_store_v1",
        "programmatic_execution_v1",
        "deferred_tool_discovery_v1",
        "unified_context_namespace_v1",
        "multi_graph_retrieval_v1",
        "adaptive_context_policy_v1",
        "context_reset_handoff_v1",
        "memory_retrieval_v1",
        "epistemic_safety_v1",
        "cache_provider_budget_v1",
        "output_intelligence_v1",
        "host_adapter_conformance_v1",
        "observability_attribution_v1",
        "signalbench_python_product_v1",
    ]

    path = Path("contracts/python/capability-completeness-registry-v1.json")
    registry = json.loads(path.read_text(encoding="utf-8"))
    current_order = list(registry.get("milestone_order") or [])
    if current_order not in [full_order[:12], full_order]:
        raise RuntimeError(f"unexpected canonical milestone order: {current_order}")
    registry["milestone_order"] = full_order
    caps = {row["id"]: row for row in registry["capabilities"]}

    reset = caps["context_reset_handoff_v1"]
    if reset["state"] not in {"partial", "implemented", "certified"}:
        raise RuntimeError(f"unexpected Context Reset lifecycle state: {reset['state']}")
    reset["state"] = "certified"
    reset["implementation_evidence"] = [
        "syntavra_runtime/session_memory.py",
        "syntavra_runtime/autonomous_agent.py",
        "syntavra_runtime/context_reset_handoff.py",
        "contracts/python/context-reset-handoff-v1.json",
        "tools/certify_context_reset_handoff.py",
        "tests/runtime/test_context_reset_handoff.py",
        ".github/workflows/context-reset-handoff.yml",
    ]
    reset["certification_evidence"] = [
        "contracts/python/context-reset-handoff-v1.json",
        "syntavra_runtime/context_reset_handoff.py",
        "tools/certify_context_reset_handoff.py",
        "tests/runtime/test_context_reset_handoff.py",
        ".github/workflows/context-reset-handoff.yml",
    ]

    memory = caps["memory_retrieval_v1"]
    if memory["state"] not in {"partial", "implemented"}:
        raise RuntimeError(f"unexpected Memory Retrieval lifecycle state: {memory['state']}")
    memory["state"] = "implemented"
    memory["implementation_evidence"] = [
        "syntavra_runtime/memory_intelligence.py",
        "syntavra_runtime/session_memory.py",
        "contracts/python/memory-retrieval-v1.json",
        "tools/certify_memory_retrieval_v1.py",
        "tests/runtime/test_memory_retrieval_v1.py",
        ".github/workflows/memory-retrieval.yml",
    ]
    memory["certification_evidence"] = []
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    certifier = Path("tools/certify_python_capability_completeness.py")
    text = certifier.read_text(encoding="utf-8")
    rendered = "EXPECTED_MILESTONE_PREFIX = [\n" + "".join(
        f'    "{item}",\n' for item in full_order
    ) + "]\n"
    pattern = re.compile(r'EXPECTED_MILESTONE_PREFIX = \[\n(?:    "[^"]+",\n)+\]\n')
    text, count = pattern.subn(rendered, text, count=1)
    if count != 1:
        raise RuntimeError("capability milestone prefix anchor drifted")
    certifier.write_text(text, encoding="utf-8")


def repair_continuation_docs() -> None:
    checklist = Path("docs/SYNTAVRA_PYTHON_FIRST_CONTINUATION_CHECKLIST.md")
    text = checklist.read_text(encoding="utf-8")
    text = text.replace("Status checkpoint: **2026-08-18**", "Status checkpoint: **2026-08-19**", 1)
    text = text.replace(
        "- [ ] Add a machine-enforced Rust feature-freeze guard once Python-first implementation begins.",
        "- [x] Add a machine-enforced Rust feature-freeze guard once Python-first implementation begins.",
        1,
    )
    start = text.index("## Repository state at checkpoint")
    end = text.index("## Existing Python baseline: do not reimplement blindly")
    current = """## Repository state at checkpoint

- Admitted `main` before Memory work: `9838235404a834cfbc4882ebfb989cf1b40ef42e`.
- Context Reset / Handoff v1 was exact-head admitted and merged through PR #148.
- Active Memory branch: `agent/memory-retrieval-v1`.
- Active PR: #151 — `Add Memory Retrieval v1`.
- Memory Retrieval pre-seal implementation passed its dedicated exact-head workflow and was subsequently scope/lifecycle hardened before admission.
- Python COMPLETE remains false.
- Rust remains feature-frozen at 174/245 production promotion with 71 remaining.

## Immediate exact task

- [x] Merge Context Reset / Handoff v1.
- [x] Implement Memory Retrieval v1 without a parallel memory database.
- [x] Add scoped retrieval, provenance, conflict/supersession, consolidation/forgetting, exact recovery and handoff receipts.
- [x] Harden recovery/mutation/session ownership boundaries and prevent silent memory reactivation.
- [x] Bind Memory Retrieval to exact-head CI, Release Main and immutable action-pin enforcement.
- [ ] Pass final exact-head admission gates on the permanent sealed PR #151 tree.
- [ ] Merge PR #151 only after all load-bearing gates pass.
- [ ] Re-read fresh `main`, then advance to `epistemic_safety_v1`.

"""
    text = text[:start] + current + text[end:]
    first = text.index("## First exact Python commits after PR #132")
    wave = text.index("## Wave P0-A: authority / contracts / reproducibility")
    ordered = """## Canonical Python milestone order

Do these in order. Do not begin item N+1 until N has acceptance tests and an exact-head receipt.

- [x] `python_authority_v1`
- [x] `capability_completeness_registry_v1`
- [x] `rust_feature_freeze_guard_v1`
- [x] `universal_context_item_v1`
- [x] `evidence_store_v2`
- [x] `typed_context_object_store_v1`
- [x] `programmatic_execution_v1`
- [x] `deferred_tool_discovery_v1`
- [x] `unified_context_namespace_v1`
- [x] `multi_graph_retrieval_v1`
- [x] `adaptive_context_policy_v1`
- [x] `context_reset_handoff_v1`
- [ ] `memory_retrieval_v1` — current admission candidate
- [ ] `epistemic_safety_v1`
- [ ] `cache_provider_budget_v1`
- [ ] `output_intelligence_v1`
- [ ] `host_adapter_conformance_v1`
- [ ] `observability_attribution_v1`
- [ ] `signalbench_python_product_v1`

"""
    text = text[:first] + ordered + text[wave:]
    checklist.write_text(text, encoding="utf-8")

    live = Path("docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md")
    live.write_text(
        """# Syntavra Python-First Live Checkpoint

Updated: **2026-08-19**

This file is the volatile continuation authority. Historical milestones live in `SYNTAVRA_PYTHON_FIRST_CHECKPOINT.txt`; the capability registry is the machine-readable lifecycle authority.

## Current admitted base

- `main` before Memory work: `9838235404a834cfbc4882ebfb989cf1b40ef42e`
- Context Reset / Handoff v1: admitted through PR #148.
- Python feature authority: active.
- Python COMPLETE: false.
- Rust feature development: frozen.
- Rust production promotion: 174/245.
- Remaining Rust parity/promotion set: 71.

## Current implementation

### PR #151 — Memory Retrieval v1

- Branch: `agent/memory-retrieval-v1`.
- Existing `MemoryIntelligenceStore` SQLite authority is reused.
- Existing `SessionMemory` exact hash-chain authority is reused.
- No parallel persistent memory database is introduced.
- Episodic, semantic, procedural, project, user and temporal memory scopes are represented.
- Project/user/session retrieval isolation fails closed.
- Exact recovery requires a visible scope.
- Lifecycle mutation, forgetting and consolidation require exact scope ownership.
- Session retrieval binds the session's recorded user to the requested memory scope.
- Provenance is mandatory.
- Conflicts remain explicit; supersession is explicit.
- Lifecycle relation sets are part of memory identity.
- Consolidation preserves parent lineage.
- Forgetting is logical and durable: inactive for retrieval, exact payload preserved, duplicate remember does not silently reactivate it.
- Invalid observations are excluded from active retrieval.
- Hybrid BM25/vector retrieval uses deterministic query expansion and importance/confidence/validity/recency reranking.
- Retrieval and cross-agent handoff emit deterministic content-addressed receipts.

## Verification state

- The original Memory Retrieval implementation passed its dedicated exact-head workflow (7/7 initial regressions).
- Release Main on that pre-seal head passed runtime/certifier/freeze checks and stopped only at the intentionally stale manifest.
- Admission review then found and hardened cross-scope relation mutation/recovery/forgetting, session-user binding, invalid-memory retrieval and silent reactivation edges before merge.
- Final exact-head admission gates must pass on the permanent sealed tree before PR #151 can merge.

## Current lifecycle repair

- `context_reset_handoff_v1` advances from stale `partial` metadata to `certified`.
- `memory_retrieval_v1` is recorded as `implemented` pending final exact-head admission/merge.
- Machine-readable milestone order now continues through Epistemic Safety, Cache/Provider/Budget, Output Intelligence, Host Adapter Conformance, Observability Attribution and SignalBench.
- Capability completeness enforces the full order; omitted future milestones cannot accidentally yield Python COMPLETE.

## Next exact task

1. Pass final exact-head Memory Retrieval, Capability Completeness, Context Reset, Rust Freeze, Release Main and Package Provenance gates.
2. Merge PR #151 only after all load-bearing checks pass.
3. Re-read fresh `main`.
4. Begin `epistemic_safety_v1`; reuse existing security scan, trust/taint, claim governance, capability authorization and Adaptive Context Policy primitives instead of duplicating them.

## Required continuation instruction

```text
Continue Syntavra in PYTHON-FIRST mode from docs/SYNTAVRA_PYTHON_FIRST_LIVE_CHECKPOINT.md.
Cross-check contracts/python/capability-completeness-registry-v1.json before choosing work.
Do not resume Rust feature development or alter the 174/245 production-promotion boundary.
Do not start a later milestone while the first non-admitted canonical milestone is still open.
When CI is active, track the existing run instead of creating a duplicate; continue independent work while it runs.
```
""",
        encoding="utf-8",
    )

    checkpoint = Path("docs/SYNTAVRA_PYTHON_FIRST_CHECKPOINT.txt")
    text = checkpoint.read_text(encoding="utf-8")
    marker = "APPENDED CURRENT CHECKPOINT — 2026-08-19 — MEMORY RETRIEVAL ACTIVE"
    if marker not in text:
        text += """

======================================================================
APPENDED CURRENT CHECKPOINT — 2026-08-19 — MEMORY RETRIEVAL ACTIVE
======================================================================

CURRENT ADMITTED MAIN BEFORE MEMORY:
9838235404a834cfbc4882ebfb989cf1b40ef42e

ACTIVE IMPLEMENTATION BRANCH:
agent/memory-retrieval-v1

ACTIVE PR:
#151 — Add Memory Retrieval v1

PYTHON STATUS:
PYTHON-FIRST ACTIVE — Context Reset / Handoff v1 is admitted; memory_retrieval_v1 is the current milestone.

RUST STATUS:
FEATURE FROZEN AND CI-ENFORCED — production promotion 174/245; remaining parity/promotion 71; Rust resume false.

CURRENT IMPLEMENTATION:
- existing MemoryIntelligenceStore and SessionMemory authorities are reused.
- no parallel persistent memory database was introduced.
- scoped episodic/semantic/procedural/project/user/temporal memory records added.
- provenance, conflict, supersession, consolidation, logical forgetting and exact recovery added.
- recovery and lifecycle mutation are scope-gated; session retrieval is user-bound.
- duplicate identities do not reactivate forgotten/superseded memories.
- invalid memories are excluded from active retrieval.
- BM25/vector retrieval extended with deterministic query expansion and importance/confidence/validity/recency reranking.
- session retrieval remains hash-chain verified and exact recoverable.
- deterministic retrieval and cross-agent handoff receipts added.
- Memory contract, regression suite, certifier, exact-head workflow, Release Main binding and immutable action-pin coverage added.

VERIFIED PRE-SEAL:
- original Memory Retrieval exact-head workflow PASS.
- initial Memory Retrieval regression PASS 7/7.
- Memory certifier admission_ready=true; exact recovery=true; new_persistent_database=false.
- Rust Freeze and supporting Python-first gates PASS.
- Release Main reached the canonical manifest check after runtime/certifier checks passed; failure was stale MANIFEST.sha256 only.
- admission review subsequently found and hardened cross-scope and silent-reactivation edge cases before merge.

LIFECYCLE REPAIR IN THIS ADMISSION:
- context_reset_handoff_v1 stale partial metadata advances to certified.
- memory_retrieval_v1 advances to implemented pending final exact-head admission and merge.
- milestone order/certifier now covers every remaining Python-first milestone through SignalBench.

NEXT EXACT TASK:
- pass final exact-head admission gates on the permanent sealed PR #151 tree.
- merge only after all load-bearing checks pass.
- re-read fresh main and advance to epistemic_safety_v1.
"""
        checkpoint.write_text(text, encoding="utf-8")


def main() -> int:
    harden_memory_runtime()
    harden_session_memory()
    harden_memory_tests()
    harden_memory_certifier_and_contract()
    advance_registry()
    repair_continuation_docs()
    print(json.dumps({"ok": True, "claim": "MEMORY_RETRIEVAL_ADMISSION_HARDENING"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
