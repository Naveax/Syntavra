from __future__ import annotations

import hashlib
import json
import re
from typing import Any

WIRE_HEADER = "R7RCPT1"
PRODUCT_VERSION = "0.0.1"
CONTRACT_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1

_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")

STATE_LAYOUT: dict[str, Any] = {
    "schema_version": 1,
    "contract_version": 1,
    "layout_id": "syntavra-state-layout-v1",
    "root": ".syntavra",
    "project_binding": {
        "algorithm": "sha256-normalized-absolute-path-v1",
        "field": "project_id",
        "required_for": ["receipt-envelope", "state-inspection"],
        "mismatch_policy": "fail-closed",
    },
    "engine_policy": {
        "single_writer": True,
        "fallback_after_mutation": False,
        "lock_root": ".syntavra/locks",
        "selection_precedence": [
            "command",
            "environment",
            "project",
            "user",
            "builtin",
        ],
        "environment_override": "SYNTAVRA_ENGINE",
        "builtin_default": "python",
        "auto_policy_r4": "python",
        "unknown_selection": "fail-closed",
    },
    "shared_paths": [
        {
            "id": "project-config",
            "path": ".syntavra/config.toml",
            "kind": "configuration",
            "readers": ["python", "rust"],
            "writers": ["python"],
            "rust_r7_access": "contract-metadata-only",
            "rust_r8_access": "bounded-file-metadata-and-hash",
        },
        {
            "id": "engine-selection",
            "path": ".syntavra/engine.json",
            "kind": "engine-selection",
            "readers": ["python", "rust"],
            "writers": ["python"],
            "rust_r7_access": "contract-metadata-only",
            "rust_r8_access": "bounded-file-metadata-and-hash",
        },
        {
            "id": "pre-release-state",
            "path": ".syntavra/pre-release",
            "kind": "product-state",
            "readers": ["python", "rust"],
            "writers": ["python"],
            "rust_r7_access": "not-proven",
            "rust_r8_access": "directory-metadata-only",
        },
        {
            "id": "runtime-v3-state",
            "path": ".syntavra/runtime-v3",
            "kind": "runtime-state",
            "readers": ["python", "rust"],
            "writers": ["python"],
            "rust_r7_access": "not-proven",
            "rust_r8_access": "directory-metadata-only",
        },
    ],
    "receipt_envelope": {
        "wire_header": "R7RCPT1",
        "schema_version": 1,
        "hash_algorithm": "sha256",
        "hash_scope": "canonical-wire-excluding-receipt-hash",
        "project_binding_required": True,
        "unknown_fields": "fail-closed",
        "unknown_schema": "fail-closed",
    },
    "r7_access": {
        "rust": "contract-metadata-and-receipt-parse-only",
        "filesystem_state_reads": False,
        "filesystem_mutation": False,
        "database_access": False,
    },
    "r8_access": {
        "rust": "contract-declared-state-root-inspection",
        "command": "state.inspect",
        "filesystem_state_reads": True,
        "filesystem_mutation": False,
        "database_access": False,
        "recursive_directory_read": False,
        "symlink_policy": "fail-closed",
        "unsupported_file_type_policy": "fail-closed",
        "max_file_bytes": 1048576,
    },
    "compatibility_rules": [
        "A mutating operation selects one engine before the first state write.",
        "An engine may fall back only after capability preflight reports unsupported and before mutation.",
        "Unknown schema versions, fields, paths, and engine values fail closed.",
        "R4 auto mode resolves to Python.",
        "Logical SQLite records, not physical page layout, define future database parity.",
        "R7 proves only state-layout metadata and receipt-envelope parsing.",
        "R8 permits only declared state-root metadata reads and bounded hashes; databases remain unopened.",
    ],
}


class ReceiptContractError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def state_layout() -> dict[str, Any]:
    return json.loads(json.dumps(STATE_LAYOUT, ensure_ascii=False))


def _decode_hex_text(value: str) -> str:
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ReceiptContractError("RECEIPT_HEX_INVALID") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptContractError("RECEIPT_UTF8_INVALID") from exc


def _valid_hash(value: str) -> bool:
    return bool(_LOWER_HEX_64.fullmatch(value))


def _valid_identifier(value: str) -> bool:
    return bool(_IDENTIFIER.fullmatch(value))


def _canonical_material(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def inspect_receipt_wire(data: bytes, *, expected_project_id: str) -> dict[str, Any]:
    if not _valid_hash(expected_project_id):
        raise ReceiptContractError("RECEIPT_EXPECTED_PROJECT_INVALID")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptContractError("RECEIPT_UTF8_INVALID") from exc
    if not text.endswith("\n"):
        raise ReceiptContractError("RECEIPT_TRAILING_NEWLINE_REQUIRED")

    lines = text.splitlines()
    if len(lines) != 16 or lines[0] != WIRE_HEADER:
        raise ReceiptContractError("RECEIPT_WIRE_SHAPE_INVALID")

    keys = [
        "schema_version",
        "product_version",
        "contract_version",
        "engine",
        "operation_hex",
        "created_at_ms",
        "project_id",
        "receipt_id_hex",
        "payload_hash",
        "previous_hash",
        "fallback_from",
        "fallback_to",
        "fallback_reason_hex",
        "fallback_state_mutated",
        "receipt_hash",
    ]
    values: dict[str, str] = {}
    for line, expected in zip(lines[1:], keys, strict=True):
        key, separator, value = line.partition("=")
        if not separator or key != expected:
            raise ReceiptContractError("RECEIPT_FIELD_ORDER_INVALID")
        values[key] = value

    try:
        schema_version = int(values["schema_version"])
        contract_version = int(values["contract_version"])
        created_at_ms = int(values["created_at_ms"])
    except ValueError as exc:
        raise ReceiptContractError("RECEIPT_INTEGER_INVALID") from exc

    if schema_version != RECEIPT_SCHEMA_VERSION:
        raise ReceiptContractError("RECEIPT_SCHEMA_UNSUPPORTED")
    if values["product_version"] != PRODUCT_VERSION:
        raise ReceiptContractError("RECEIPT_PRODUCT_VERSION_MISMATCH")
    if contract_version != CONTRACT_VERSION:
        raise ReceiptContractError("RECEIPT_CONTRACT_VERSION_MISMATCH")
    if values["engine"] not in {"python", "rust"}:
        raise ReceiptContractError("RECEIPT_ENGINE_INVALID")
    if created_at_ms < 0:
        raise ReceiptContractError("RECEIPT_CREATED_AT_INVALID")

    operation = _decode_hex_text(values["operation_hex"])
    receipt_id = _decode_hex_text(values["receipt_id_hex"])
    fallback_reason = _decode_hex_text(values["fallback_reason_hex"])
    if not _valid_identifier(operation):
        raise ReceiptContractError("RECEIPT_OPERATION_INVALID")
    if not _valid_identifier(receipt_id):
        raise ReceiptContractError("RECEIPT_ID_INVALID")

    project_id = values["project_id"]
    if not _valid_hash(project_id):
        raise ReceiptContractError("RECEIPT_PROJECT_ID_INVALID")
    if project_id != expected_project_id:
        raise ReceiptContractError("RECEIPT_PROJECT_MISMATCH")
    if not _valid_hash(values["payload_hash"]):
        raise ReceiptContractError("RECEIPT_PAYLOAD_HASH_INVALID")

    previous_hash = values["previous_hash"]
    if previous_hash == "-":
        previous: str | None = None
    elif _valid_hash(previous_hash):
        previous = previous_hash
    else:
        raise ReceiptContractError("RECEIPT_PREVIOUS_HASH_INVALID")

    mutated_raw = values["fallback_state_mutated"]
    if mutated_raw not in {"true", "false"}:
        raise ReceiptContractError("RECEIPT_FALLBACK_MUTATION_INVALID")
    state_mutated = mutated_raw == "true"

    fallback_from = values["fallback_from"]
    fallback_to = values["fallback_to"]
    if fallback_from == "-" and fallback_to == "-" and not fallback_reason and not state_mutated:
        fallback: dict[str, Any] | None = None
    else:
        if fallback_from not in {"python", "rust"} or fallback_to not in {"python", "rust"}:
            raise ReceiptContractError("RECEIPT_FALLBACK_ENGINE_INVALID")
        if fallback_from == fallback_to:
            raise ReceiptContractError("RECEIPT_FALLBACK_DIRECTION_INVALID")
        if not fallback_reason:
            raise ReceiptContractError("RECEIPT_FALLBACK_REASON_REQUIRED")
        if state_mutated:
            raise ReceiptContractError("RECEIPT_FALLBACK_AFTER_MUTATION")
        if values["engine"] != fallback_to:
            raise ReceiptContractError("RECEIPT_FALLBACK_TARGET_MISMATCH")
        fallback = {
            "from": fallback_from,
            "to": fallback_to,
            "reason": fallback_reason,
            "state_mutated": False,
        }

    receipt_hash = values["receipt_hash"]
    if not _valid_hash(receipt_hash):
        raise ReceiptContractError("RECEIPT_HASH_INVALID")
    calculated = hashlib.sha256(_canonical_material(lines[:-1])).hexdigest()
    if calculated != receipt_hash:
        raise ReceiptContractError("RECEIPT_HASH_MISMATCH")

    return {
        "ok": True,
        "schema_version": schema_version,
        "product_version": values["product_version"],
        "contract_version": contract_version,
        "engine": values["engine"],
        "operation": operation,
        "created_at_ms": created_at_ms,
        "project_id": project_id,
        "receipt_id": receipt_id,
        "payload_hash": values["payload_hash"],
        "previous_hash": previous,
        "fallback": fallback,
        "receipt_hash": receipt_hash,
        "project_binding": {
            "expected": expected_project_id,
            "actual": project_id,
            "matched": True,
        },
        "hash_valid": True,
        "claim": "RUST_STATE_LAYOUT_RECEIPT_PARITY_PROVEN_R7_FIXTURES",
    }
