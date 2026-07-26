from __future__ import annotations

import json
from pathlib import Path

import pytest

from syntavra_runtime.state_receipt_contract import (
    ReceiptContractError,
    inspect_receipt_wire,
    state_layout,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "parity" / "fixtures" / "state-receipts-v1.json"


def fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_state_layout_matches_contract_fixture() -> None:
    value = fixture()
    assert state_layout() == value["layout_expected"]


@pytest.mark.parametrize("row", fixture()["valid_receipts"], ids=lambda row: row["name"])
def test_valid_receipt_fixtures(row: dict[str, object]) -> None:
    actual = inspect_receipt_wire(
        bytes.fromhex(str(row["wire_hex"])),
        expected_project_id=str(row["expected_project_id"]),
    )
    assert actual == row["expected"]


@pytest.mark.parametrize("row", fixture()["invalid_receipts"], ids=lambda row: row["name"])
def test_invalid_receipts_fail_closed(row: dict[str, object]) -> None:
    with pytest.raises(ReceiptContractError) as caught:
        inspect_receipt_wire(
            bytes.fromhex(str(row["wire_hex"])),
            expected_project_id=str(row["expected_project_id"]),
        )
    assert caught.value.code == row["error"]


def test_state_layout_keeps_rust_read_only() -> None:
    access = state_layout()["r7_access"]
    assert access == {
        "rust": "contract-metadata-and-receipt-parse-only",
        "filesystem_state_reads": False,
        "filesystem_mutation": False,
        "database_access": False,
    }
