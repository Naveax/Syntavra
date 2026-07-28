from __future__ import annotations

import json
from pathlib import Path

import pytest

from syntavra_runtime.config_contract import (
    MAX_CONFIG_WIRE_BYTES,
    decode_config_wire,
    decode_config_wire_hex,
    encode_config_wire,
    resolve_config_phases,
    resolve_config_wire,
)
from syntavra_runtime.unified_config import ConfigError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "parity" / "fixtures" / "config-status-v1.json"


def test_r13_config_wire_round_trips_all_r6_fixtures() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        phases = case["phases"]
        wire = encode_config_wire(phases)
        decoded = decode_config_wire(wire)
        assert encode_config_wire(decoded) == wire
        assert resolve_config_wire(wire) == resolve_config_phases(phases)
        assert decode_config_wire_hex(wire.hex()) == wire


def test_r13_config_wire_rejects_duplicate_assignments() -> None:
    wire = (
        b"R6CFG1\n"
        b"phase\t0\n"
        b"a\tproject\t70726f6a6563742d636f6e666967\t"
        b"72756e74696d652e70726f66696c65\ts\t636f6d70616374\n"
        b"a\tproject\t70726f6a6563742d636f6e666967\t"
        b"72756e74696d652e70726f66696c65\ts\t7465727365\n"
    )
    with pytest.raises(ConfigError, match="duplicate assignment"):
        decode_config_wire(wire)


def test_r13_config_wire_rejects_noncanonical_source() -> None:
    wire = (
        b"R6CFG1\n"
        b"phase\t0\n"
        b"a\tproject\t77726f6e67\t"
        b"72756e74696d652e70726f66696c65\ts\t636f6d70616374\n"
    )
    with pytest.raises(ConfigError, match="source is invalid"):
        decode_config_wire(wire)


def test_r13_config_wire_rejects_oversized_hex_before_decoding() -> None:
    oversized = "00" * (MAX_CONFIG_WIRE_BYTES + 1)
    with pytest.raises(ConfigError, match="exceeds the input limit"):
        decode_config_wire_hex(oversized)


def test_r13_config_wire_requires_canonical_newline_termination() -> None:
    with pytest.raises(ConfigError, match="newline terminated"):
        decode_config_wire(b"R6CFG1\nphase\t0")
