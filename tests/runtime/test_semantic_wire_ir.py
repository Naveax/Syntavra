from __future__ import annotations

import json
import unittest
from pathlib import Path

from syntavra_runtime.semantic_wire_ir import (
    AnswerField,
    CompactAnswerCodec,
    SemanticAtom,
    SemanticWireCompiler,
    WireGrammar,
)


ROOT = Path(__file__).resolve().parents[2]


def whitespace_tokens(text: str) -> int:
    return len(text.split())


class SemanticWireIRTests(unittest.TestCase):
    def test_tokenizer_measured_wire_form_wins(self) -> None:
        compiler = SemanticWireCompiler(
            whitespace_tokens,
            grammar=WireGrammar(operators={"I": "inspect", "S": "symbol", "V": "verify"}),
        )
        packet = compiler.compile(
            [
                SemanticAtom(
                    "TARGET",
                    "Inspect the authentication refresh symbol using only the exact relevant repository range.",
                    "I S22",
                ),
                SemanticAtom(
                    "VERIFY",
                    "Run the targeted authentication verifier after the change and do not run the broad suite.",
                    "V V3",
                ),
            ],
            grammar_already_in_fixed_prefix=True,
        )
        self.assertEqual(packet.mode, "wire")
        self.assertLess(packet.selected_tokens, packet.natural_tokens)
        self.assertGreater(packet.savings_ratio, 0.5)
        self.assertFalse(packet.expanded)

    def test_no_expansion_gate_falls_back_to_natural(self) -> None:
        compiler = SemanticWireCompiler(whitespace_tokens)
        packet = compiler.compile(
            [SemanticAtom("A", "short request", "this compact representation is actually much longer")]
        )
        self.assertEqual(packet.mode, "natural")
        self.assertIn("NO_EXPANSION_GATE", packet.reasons)
        self.assertEqual(packet.selected_tokens, packet.natural_tokens)

    def test_exact_compact_atom_requires_recovery_reference(self) -> None:
        with self.assertRaises(ValueError):
            SemanticAtom("E", "exact failure body", "E8", exact_required=True)

        compiler = SemanticWireCompiler(whitespace_tokens)
        packet = compiler.compile(
            [
                SemanticAtom(
                    "E",
                    "the exact verifier failure output is stored in the local evidence system and must remain recoverable",
                    "E8",
                    exact_required=True,
                    recovery_ref="evidence://failure/8",
                )
            ]
        )
        self.assertEqual(packet.mode, "wire")
        self.assertEqual(packet.recovery_refs, ("evidence://failure/8",))

    def test_missing_compact_form_falls_back_without_guessing(self) -> None:
        compiler = SemanticWireCompiler(whitespace_tokens)
        packet = compiler.compile(
            [
                SemanticAtom("KNOWN", "known compact semantic", "K1"),
                SemanticAtom("UNKNOWN", "unstructured user meaning that has no admitted wire representation"),
            ]
        )
        self.assertEqual(packet.mode, "natural")
        self.assertIn("NO_COMPACT_FORM:UNKNOWN", packet.reasons)

    def test_compact_answer_codec_round_trip(self) -> None:
        codec = CompactAnswerCodec(
            [
                AnswerField("status", "S", required=True, labels={"0": "ok", "1": "failed"}),
                AnswerField("verifier", "V", required=True, labels={"0": "pass", "1": "fail"}),
                AnswerField("regressions", "R", required=True),
            ]
        )
        wire = codec.encode({"status": "ok", "verifier": "pass", "regressions": 0})
        self.assertEqual(wire, "S:0 V:0 R:0")
        self.assertEqual(
            codec.decode(wire),
            {"status": "ok", "verifier": "pass", "regressions": "0"},
        )
        rendered = codec.render(
            wire,
            {
                "status": "status={status}",
                "verifier": "verifier={verifier}",
                "regressions": "regressions={regressions}",
            },
        )
        self.assertEqual(rendered, "status=ok\nverifier=pass\nregressions=0")

    def test_contract_keeps_claim_boundary_and_efficiency_gates(self) -> None:
        value = json.loads(
            (ROOT / "contracts/python/semantic-wire-ir-v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(value["family"], "syntavra-semantic-wire-ir")
        self.assertTrue(value["no_new_capability_namespace"])
        targets = value["targets"]
        self.assertGreaterEqual(targets["avoidable_input_reduction_floor"], 0.85)
        self.assertGreaterEqual(targets["avoidable_output_reduction_floor"], 0.85)
        self.assertEqual(targets["unexplained_envelope_overflow"], 0)
        self.assertIn("not current measured provider savings", value["claim_boundary"])


if __name__ == "__main__":
    unittest.main()
