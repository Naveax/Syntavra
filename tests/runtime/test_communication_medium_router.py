from __future__ import annotations

import unittest

from syntavra_runtime.semantic_wire_ir import HandleBinding, SemanticAtom, SemanticWireCompiler
from syntavra_runtime.subtask_router import (
    AutomaticSubtaskDelegator,
    CommunicationMediumRouter,
    EndpointCapabilities,
    MediumCostEvidence,
    ProviderWorkEvidence,
    ReceiptReference,
    RouteDecision,
)


class CommunicationMediumRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = CommunicationMediumRouter(SemanticWireCompiler(lambda text: len(text.split())))

    @staticmethod
    def endpoint(
        endpoint_id: str,
        *,
        ownership: str = "local",
        domain: str = "task",
        media: tuple[str, ...] = ("text",),
        provider: str = "",
        handles: bool = False,
        receipts: bool = False,
        latent_id: str = "",
    ) -> EndpointCapabilities:
        return EndpointCapabilities(endpoint_id, ownership, domain, media, provider, handles, receipts, latent_id)

    def test_silence_wins_when_handoff_is_not_required(self) -> None:
        decision = self.router.route(self.endpoint("a"), self.endpoint("b"), "", handoff_required=False)
        self.assertEqual(decision.medium, "silence")
        self.assertEqual(decision.end_to_end_cost_units, 0)
        self.assertTrue(decision.cost_verified)

    def test_verified_exact_handle_is_selected_without_copying_payload(self) -> None:
        src = self.endpoint("a", media=("handle", "swir", "text"))
        dst = self.endpoint("b", media=("handle", "swir", "text"), handles=True)
        handle = HandleBinding("H1", "artifact", "evidence:canonical", "d" * 64, recovery_ref="evidence:exact")
        decision = self.router.route(
            src,
            dst,
            "apply the verified patch after reading the exact canonical artifact",
            exact_required=True,
            handle=handle,
            cost_evidence=(
                MediumCostEvidence("handle", 1, "exact", True, "frozen:handle-cost"),
                MediumCostEvidence("text", 9, "exact", True, "frozen:text-cost"),
            ),
        )
        self.assertEqual(decision.medium, "handle")
        self.assertEqual(decision.payload, "H1")

    def test_exact_required_handle_without_recovery_falls_back_to_text(self) -> None:
        src = self.endpoint("a", media=("handle", "text"))
        dst = self.endpoint("b", media=("handle", "text"), handles=True)
        handle = HandleBinding("H2", "artifact", "evidence:canonical", "d" * 64)
        decision = self.router.route(
            src,
            dst,
            "exact fallback text",
            exact_required=True,
            handle=handle,
            cost_evidence=(MediumCostEvidence("text", 3, "exact", True, "frozen:text"),),
        )
        self.assertEqual(decision.medium, "text")
        self.assertIn("EXACT_HANDLE_RECOVERY_MISSING", decision.reason_codes)

    def test_existing_delegation_receipt_hash_is_reused_opaquely(self) -> None:
        plan = AutomaticSubtaskDelegator().plan("Implement provider routing. Verify tests and coverage.")
        self.assertTrue(plan.delegated)
        ref = ReceiptReference("delegation-plan", plan.receipt_hash)
        src = self.endpoint("a", media=("receipt", "text"))
        dst = self.endpoint("b", media=("receipt", "text"), receipts=True)
        decision = self.router.route(
            src,
            dst,
            "delegation plan fallback",
            receipt=ref,
            cost_evidence=(
                MediumCostEvidence("receipt", 1, "semantic", True, "frozen:receipt"),
                MediumCostEvidence("text", 6, "exact", True, "frozen:text"),
            ),
        )
        self.assertEqual(decision.medium, "receipt")
        self.assertTrue(decision.payload.endswith(plan.receipt_hash))

    def test_swir_is_delegated_to_existing_no_expansion_compiler(self) -> None:
        src = self.endpoint("a", media=("swir", "text"))
        dst = self.endpoint("b", media=("swir", "text"))
        natural = "apply patch after reading canonical source and run verifier"
        atoms = (SemanticAtom("PATCH", natural, "P"),)
        decision = self.router.route(
            src,
            dst,
            natural,
            swir_atoms=atoms,
            cost_evidence=(
                MediumCostEvidence("swir", 2, "semantic", True, "frozen:swir"),
                MediumCostEvidence("text", 8, "exact", True, "frozen:text"),
            ),
        )
        self.assertEqual(decision.medium, "swir")
        self.assertEqual(decision.payload, "P")

    def test_swir_no_expansion_gate_preserves_text_fallback(self) -> None:
        src = self.endpoint("a", media=("swir", "text"))
        dst = self.endpoint("b", media=("swir", "text"))
        natural = "short"
        atoms = (SemanticAtom("SHORT", natural, "this compact form is longer"),)
        decision = self.router.route(
            src,
            dst,
            natural,
            swir_atoms=atoms,
            cost_evidence=(MediumCostEvidence("text", 1, "exact", True, "frozen:text"),),
        )
        self.assertEqual(decision.medium, "text")
        self.assertIn("SWIR_NO_EXPANSION_TEXT_FALLBACK", decision.reason_codes)

    def test_verified_end_to_end_cost_beats_nominal_payload_size(self) -> None:
        src = self.endpoint("a", media=("swir", "text"))
        dst = self.endpoint("b", media=("swir", "text"))
        natural = "one two three four five six seven eight"
        atoms = (SemanticAtom("PAYLOAD", natural, "P"),)
        decision = self.router.route(
            src,
            dst,
            natural,
            swir_atoms=atoms,
            cost_evidence=(
                MediumCostEvidence("swir", 50, "semantic", True, "paired:swir-total"),
                MediumCostEvidence("text", 5, "exact", True, "paired:text-total"),
            ),
        )
        self.assertEqual(decision.medium, "text")
        self.assertGreater(8, 1)
        self.assertEqual(decision.end_to_end_cost_units, 5)

    def test_hosted_endpoint_cannot_advertise_latent_or_kv(self) -> None:
        with self.assertRaises(ValueError):
            self.endpoint("remote", ownership="hosted", domain="provider", media=("text", "kv"), provider="openrouter")

    def test_latent_requires_compatible_local_owned_endpoints_and_security_domain(self) -> None:
        src = self.endpoint("a", media=("text", "kv"), latent_id="llama-kv-v1")
        dst = self.endpoint("b", ownership="owned", media=("text", "kv"), latent_id="llama-kv-v1")
        decision = self.router.route(
            src,
            dst,
            "fallback payload",
            latent_refs={"kv": "kv://slot/7"},
            cost_evidence=(
                MediumCostEvidence("kv", 1, "exact", True, "local:kv-compat"),
                MediumCostEvidence("text", 5, "exact", True, "local:text"),
            ),
        )
        self.assertEqual(decision.medium, "kv")

        isolated = self.endpoint("c", ownership="owned", domain="isolated", media=("text", "kv"), latent_id="llama-kv-v1")
        fallback = self.router.route(
            src,
            isolated,
            "fallback payload",
            latent_refs={"kv": "kv://slot/7"},
            cost_evidence=(MediumCostEvidence("text", 5, "exact", True, "local:text"),),
        )
        self.assertEqual(fallback.medium, "text")
        self.assertIn("LATENT_UNAVAILABLE:kv", fallback.reason_codes)

    def test_paired_provider_work_is_recorded_but_not_promoted_to_savings_claim(self) -> None:
        src = self.endpoint("local")
        dst = self.endpoint("remote", ownership="hosted", domain="provider", provider="openai")
        paired = self.router.route(
            src,
            dst,
            "provider payload",
            cost_evidence=(MediumCostEvidence("text", 4, "exact", True, "paired:route"),),
            provider_work=ProviderWorkEvidence(100, 30, 2, 1, True, "provider:baseline", "provider:selected"),
        )
        self.assertEqual(paired.provider_visible_tokens_avoided, 70)
        self.assertEqual(paired.provider_visible_calls_avoided, 1)
        self.assertFalse(paired.provider_savings_claim)

        unpaired = self.router.route(
            src,
            dst,
            "provider payload",
            cost_evidence=(MediumCostEvidence("text", 4, "exact", True, "single:route"),),
            provider_work=ProviderWorkEvidence(100, 30, 2, 1),
        )
        self.assertEqual(unpaired.provider_visible_tokens_avoided, 0)
        self.assertEqual(unpaired.provider_visible_calls_avoided, 0)

    def test_route_receipt_is_content_addressed_and_tamper_evident(self) -> None:
        decision = self.router.route(
            self.endpoint("a"),
            self.endpoint("b"),
            "fallback",
            cost_evidence=(MediumCostEvidence("text", 1, "exact", True, "frozen:text"),),
        )
        self.assertTrue(RouteDecision.verify_receipt(decision.receipt()))
        tampered = decision.receipt()
        tampered["payload"] = "different"
        with self.assertRaises(ValueError):
            RouteDecision.verify_receipt(tampered)

    def test_endpoint_contract_always_preserves_text_fallback(self) -> None:
        with self.assertRaises(ValueError):
            self.endpoint("bad", media=("swir",))


if __name__ == "__main__":
    unittest.main()
