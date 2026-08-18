from __future__ import annotations

import unittest

from syntavra_runtime.context_namespace import ContextNamespace, ContextNamespaceAddress
from syntavra_runtime.universal_context_item import (
    ContextFreshness,
    ContextProvenance,
    ContextTrust,
    RecoveryHandle,
    UniversalContextItem,
)


def _item(name: str, *, representation: str = "exact", source: str = "fixture") -> UniversalContextItem:
    return UniversalContextItem.build(
        kind="repository-symbol",
        representation=representation,
        content={
            "name": name,
            "text": f"exact payload for {name}",
            "nested": {"count": 3, "rows": ["alpha", "beta", "gamma"]},
        },
        provenance=ContextProvenance(
            source=source,
            repository_commit="a" * 40,
            parent_item_ids=(),
            metadata={"fixture": True},
        ),
        trust=ContextTrust(level="verified", confidence=1.0, reasons=("fixture",)),
        freshness=ContextFreshness(state="fresh", observed_at="2026-08-18T00:00:00Z"),
        recovery=(
            RecoveryHandle(
                kind="file-range",
                locator={"path": "src/example.py", "start_line": 1, "end_line": 9},
                integrity="sha256:" + "1" * 64,
                exact=True,
            ),
        ),
        metadata={"tokens": 12, "fixture_name": name},
    )


def _repo_namespace() -> tuple[ContextNamespace, dict[str, str]]:
    namespace = ContextNamespace()
    uris = {
        "repo": ContextNamespaceAddress.repository("Naveax-Syntavra").uri,
        "dir": ContextNamespaceAddress.repository("Naveax-Syntavra", directory="syntavra_runtime").uri,
        "file": ContextNamespaceAddress.repository(
            "Naveax-Syntavra", directory="syntavra_runtime", file="syntavra_runtime/context_pack.py"
        ).uri,
        "symbol": ContextNamespaceAddress.repository(
            "Naveax-Syntavra",
            directory="syntavra_runtime",
            file="syntavra_runtime/context_pack.py",
            symbol="TaskContextAssembler.assemble",
        ).uri,
        "lines": ContextNamespaceAddress.repository(
            "Naveax-Syntavra",
            directory="syntavra_runtime",
            file="syntavra_runtime/context_pack.py",
            symbol="TaskContextAssembler.assemble",
            lines=(88, 144),
        ).uri,
    }
    parent = None
    for key in ("repo", "dir", "file", "symbol", "lines"):
        namespace.bind_item(
            uris[key],
            _item(key),
            label=key,
            reason=f"{key} selected for task evidence",
            parent_uri=parent,
            tags=("repository", key),
        )
        parent = uris[key]
    return namespace, uris


class ContextNamespaceAddressTests(unittest.TestCase):
    def test_roundtrip_is_canonical(self) -> None:
        address = ContextNamespaceAddress("memory", ("project", "decision 1"))
        self.assertEqual(address.uri, "syntavra://memory/project/decision%201")
        self.assertEqual(ContextNamespaceAddress.parse(address.uri), address)

    def test_noncanonical_and_unsafe_uris_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            ContextNamespaceAddress.parse("https://repo/project")
        with self.assertRaises(ValueError):
            ContextNamespaceAddress("repo", ("..",))
        with self.assertRaises(ValueError):
            ContextNamespaceAddress.parse("syntavra://repo/project?query=x")
        with self.assertRaises(ValueError):
            ContextNamespaceAddress.parse("syntavra://repo/a%2Fb")

    def test_repository_progressive_address_shape(self) -> None:
        address = ContextNamespaceAddress.repository(
            "Naveax-Syntavra",
            directory="src/runtime",
            file="src/runtime/context.py",
            symbol="Context.open",
            lines=(10, 20),
        )
        self.assertEqual(
            address.uri,
            "syntavra://repo/Naveax-Syntavra/dir/src/runtime/file/context.py/symbol/Context.open/lines/10-20",
        )
        with self.assertRaises(ValueError):
            ContextNamespaceAddress.repository("repo", symbol="x")
        with self.assertRaises(ValueError):
            ContextNamespaceAddress.repository("repo", file="x.py", lines=(1, 2))


class ContextNamespaceTests(unittest.TestCase):
    def test_registration_requires_resolvable_integrity_checked_item(self) -> None:
        namespace = ContextNamespace()
        item = _item("root")
        with self.assertRaises(KeyError):
            namespace.bind_resolver(
                "syntavra://context/root",
                item.item_id,
                lambda _: None,
                label="root",
                reason="fixture",
            )

    def test_parent_must_exist_and_duplicate_uri_fails(self) -> None:
        namespace = ContextNamespace()
        item = _item("root")
        with self.assertRaises(KeyError):
            namespace.bind_item(
                "syntavra://context/child",
                item,
                label="child",
                reason="fixture",
                parent_uri="syntavra://context/missing",
            )
        namespace.bind_item("syntavra://context/root", item, label="root", reason="fixture")
        with self.assertRaises(ValueError):
            namespace.bind_item("syntavra://context/root", item, label="root2", reason="fixture2")

    def test_l0_is_identity_only(self) -> None:
        namespace, uris = _repo_namespace()
        result = namespace.reveal(uris["symbol"], level="L0")
        view = result["view"]
        self.assertEqual(view["level"], "L0")
        self.assertNotIn("content", view)
        self.assertNotIn("reason", view)
        self.assertNotIn("structure", view)
        self.assertEqual(view["uri"], uris["symbol"])
        self.assertTrue(result["receipt"]["receipt_hash"])

    def test_l1_adds_explanation_without_exact_payload(self) -> None:
        namespace, uris = _repo_namespace()
        view = namespace.reveal(uris["symbol"], level="L1")["view"]
        self.assertEqual(view["reason"], "symbol selected for task evidence")
        self.assertEqual(view["trust"]["level"], "verified")
        self.assertTrue(view["exact_recovery_available"])
        self.assertNotIn("content", view)
        self.assertNotIn("exact payload for symbol", str(view))

    def test_l2_is_structural_and_does_not_leak_scalar_text(self) -> None:
        namespace, uris = _repo_namespace()
        view = namespace.reveal(uris["symbol"], level="L2")["view"]
        self.assertEqual(view["structure"]["type"], "object")
        self.assertIn("text", view["structure"]["keys"])
        self.assertNotIn("exact payload for symbol", str(view))
        self.assertNotIn("alpha", str(view))
        self.assertNotIn("content", view)

    def test_l3_reveals_integrity_checked_exact_item_content(self) -> None:
        namespace, uris = _repo_namespace()
        view = namespace.reveal(uris["symbol"], level="L3")["view"]
        self.assertEqual(view["content"]["text"], "exact payload for symbol")
        self.assertEqual(view["recovery"][0]["kind"], "file-range")
        self.assertTrue(view["recovery"][0]["exact"])
        self.assertEqual(view["content_sha256"], _item("symbol").content_sha256)

    def test_resolver_drift_fails_closed_after_registration(self) -> None:
        namespace = ContextNamespace()
        item = _item("root")
        state = {"item": item}
        namespace.bind_resolver(
            "syntavra://context/root",
            item.item_id,
            lambda _: state["item"],
            label="root",
            reason="fixture",
        )
        state["item"] = _item("different")
        with self.assertRaises(ValueError):
            namespace.reveal("syntavra://context/root", level="L3")

    def test_browser_descends_repo_directory_file_symbol_lines(self) -> None:
        namespace, uris = _repo_namespace()
        order = ("repo", "dir", "file", "symbol")
        for parent, child in zip(order, ("dir", "file", "symbol", "lines")):
            result = namespace.browse(uris[parent], level="L0")
            self.assertEqual(result["child_count"], 1)
            self.assertEqual(result["children"][0]["uri"], uris[child])
            self.assertFalse(result["truncated"])

    def test_browser_limit_is_bounded(self) -> None:
        namespace, uris = _repo_namespace()
        with self.assertRaises(ValueError):
            namespace.browse(uris["repo"], limit=0)
        with self.assertRaises(ValueError):
            namespace.browse(uris["repo"], limit=257)

    def test_why_is_explicit_and_payload_free(self) -> None:
        namespace, uris = _repo_namespace()
        result = namespace.why(uris["lines"])
        explanation = result["explanation"]
        self.assertEqual(explanation["reason"], "lines selected for task evidence")
        self.assertEqual(explanation["parent_uri"], uris["symbol"])
        self.assertEqual(explanation["trust_level"], "verified")
        self.assertNotIn("exact payload", str(explanation))
        self.assertTrue(result["receipt"]["receipt_hash"])

    def test_retrieval_trajectory_records_browse_why_reveal(self) -> None:
        namespace, uris = _repo_namespace()
        trajectory = namespace.start_trajectory("inspect assembler", root_uri=uris["repo"])
        namespace.browse(uris["repo"], trajectory_id=trajectory)
        namespace.why(uris["symbol"], trajectory_id=trajectory)
        namespace.reveal(uris["symbol"], level="L3", trajectory_id=trajectory)
        receipt = namespace.trajectory_receipt(trajectory)
        self.assertEqual([row["sequence"] for row in receipt["steps"]], [1, 2, 3])
        self.assertEqual([row["operation"] for row in receipt["steps"]], ["browse", "why", "reveal"])
        self.assertTrue(receipt["trajectory_hash"])
        self.assertEqual(len(receipt["trajectory_hash"]), 64)

    def test_retrieval_trajectory_is_deterministic_for_same_operations(self) -> None:
        first, first_uris = _repo_namespace()
        second, second_uris = _repo_namespace()
        first_id = first.start_trajectory("inspect assembler", root_uri=first_uris["repo"])
        second_id = second.start_trajectory("inspect assembler", root_uri=second_uris["repo"])
        self.assertEqual(first_id, second_id)
        for namespace, uris, trajectory in (
            (first, first_uris, first_id),
            (second, second_uris, second_id),
        ):
            namespace.browse(uris["repo"], trajectory_id=trajectory)
            namespace.reveal(uris["symbol"], level="L2", trajectory_id=trajectory)
        self.assertEqual(first.trajectory_receipt(first_id), second.trajectory_receipt(second_id))

    def test_unknown_level_trajectory_and_uri_fail_closed(self) -> None:
        namespace, uris = _repo_namespace()
        with self.assertRaises(ValueError):
            namespace.reveal(uris["repo"], level="L9")
        with self.assertRaises(KeyError):
            namespace.reveal("syntavra://repo/missing")
        with self.assertRaises(KeyError):
            namespace.trajectory_receipt("missing")

    def test_namespace_has_no_parallel_persistent_store(self) -> None:
        namespace, _ = _repo_namespace()
        status = namespace.status()
        self.assertFalse(status["persistent_store"])
        self.assertEqual(status["scheme"], "syntavra")
        self.assertEqual(status["levels"], ["L0", "L1", "L2", "L3"])
        self.assertEqual(status["entries"], 5)


if __name__ == "__main__":
    unittest.main()
