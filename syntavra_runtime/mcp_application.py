from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .mcp_policy import MCPToolPolicy
from .product_surface import SessionAnalyticsStore
from .tool_registry import ToolSchemaCompiler, normalize_profile
from .secret_redaction import SecretRedactor
from .wire_format import LosslessWireCodec


class MCPApplicationPipeline:
    """Explicit MCP request pipeline.

    The pipeline owns profile selection, catalog compilation, call authorization,
    argument alias decoding, result externalization and route receipts. Extension
    modules may add tools and handlers, but they do not bypass this boundary.
    """

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.analytics = SessionAnalyticsStore(self.state_root / "analytics" / "events.jsonl")
        self.compiler = ToolSchemaCompiler()
        self.redactor = SecretRedactor()
        self.wire = LosslessWireCodec()
        self.wire_mode = os.environ.get("SYNTAVRA_WIRE_MODE", "off").strip().casefold() or "off"
        if self.wire_mode not in {"off", "auto"}:
            raise ValueError(f"unknown Syntavra wire mode: {self.wire_mode}")
        self.policy = MCPToolPolicy(self._requested_profile())
        self.schema_mode = os.environ.get("SYNTAVRA_SCHEMA_MODE", "compact").strip().casefold() or "compact"
        if self.schema_mode not in {"compact", "raw"}:
            raise ValueError(f"unknown Syntavra schema mode: {self.schema_mode}")

    def _installed_profile(self) -> str:
        path = self.state_root / "mcp-profile.json"
        if not path.is_file():
            return "minimal"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "minimal"
        if not isinstance(value, Mapping):
            return "minimal"
        return str(value.get("name") or "minimal")

    def _requested_profile(self) -> str:
        return normalize_profile(os.environ.get("SYNTAVRA_MCP_PROFILE") or self._installed_profile())

    def refresh(self) -> None:
        requested = self._requested_profile()
        if requested != self.policy.profile:
            self.policy = MCPToolPolicy(requested)

    def list_tools(self, catalog: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        self.refresh()
        selected = self.policy.filter_catalog(catalog)
        if self.schema_mode == "raw":
            # Still compile once so status can report the exact potential saving.
            self.compiler.compile_catalog(selected)
            return selected
        compiled, _ = self.compiler.compile_catalog(selected)
        return compiled

    @staticmethod
    def _sanitize_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
        sanitized = dict(arguments)
        sanitized.pop("_syntavra_authorization", None)
        sanitized.pop("_approved", None)
        return sanitized

    def schema_status(self) -> dict[str, Any]:
        compilation = self.compiler.last_compilation
        return {
            "mode": self.schema_mode,
            "profile": self.policy.profile,
            "compilation": compilation.to_dict() if compilation else None,
        }

    def _record_route(self, server: Any, decision: Any) -> None:
        self.analytics.record({
            "kind": "mcp-tool-route",
            "repository_hash": getattr(server.evidence, "project_id", ""),
            "tool_route_allowed": decision.allowed,
            "success": decision.allowed,
            "metadata": {
                "tool": decision.tool,
                "profile": decision.profile,
                "risk": decision.risk,
                "reason": decision.reason,
                "receipt_hash": decision.receipt_hash,
                "schema_mode": self.schema_mode,
            },
        })

    def call(self, server: Any, message: Mapping[str, Any]) -> dict[str, Any]:
        request_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, Mapping):
            params = {}
        tool_name = str(params.get("name") or "")
        raw_arguments = params.get("arguments") or {}
        if not isinstance(raw_arguments, Mapping):
            raw_arguments = {}

        exposed = self.list_tools(server.tools())
        decision = self.policy.authorize(
            tool_name,
            raw_arguments,
            exposed_tools=(row["name"] for row in exposed),
        )
        self._record_route(server, decision)
        if not decision.allowed:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32001,
                    "message": f"PermissionError: {decision.reason}",
                    "data": MCPToolPolicy.serializable(decision),
                },
            }

        decoded = self.compiler.decode_arguments(tool_name, raw_arguments)
        arguments = self._sanitize_arguments(decoded)
        try:
            value = server.call_tool(tool_name, arguments)
            value = server.output_pipeline.capture_mcp_result(tool_name, arguments, value)
            value, redaction_receipt = self.redactor.redact(value)
            wire_receipt = self.wire.encode(value) if self.wire_mode == "auto" else {"encoding": "json", "payload": value, "savings_ratio": 0.0}
            rendered_value = wire_receipt if self.wire_mode == "auto" and wire_receipt.get("encoding") != "json" else value
        except KeyError:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        except (TypeError, ValueError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": f"Invalid parameters: {type(exc).__name__}"},
            }
        except Exception:
            # Runtime details may contain paths, provider payloads or secret-bearing
            # exception text. The exact failure remains in local evidence/telemetry.
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "Syntavra tool execution failed"},
            }

        result = {
            "content": [{"type": "text", "text": json.dumps(rendered_value, ensure_ascii=False, default=str)}],
            "_meta": {
                "syntavra_route_receipt": decision.receipt_hash,
                "syntavra_profile": decision.profile,
                "syntavra_risk": decision.risk,
                "syntavra_schema_mode": self.schema_mode,
                "syntavra_schema_compilation": self.schema_status()["compilation"],
                "syntavra_secret_redaction": redaction_receipt,
                "syntavra_wire": {key: wire_receipt.get(key) for key in ("encoding", "original_bytes", "encoded_bytes", "savings_ratio", "original_hash") if key in wire_receipt},
            },
        }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def manifest(self) -> dict[str, Any]:
        return {
            "pipeline": [
                "profile-selection",
                "catalog-filter",
                "schema-compilation",
                "authorization",
                "argument-decoding",
                "execution",
                "exact-output-capture",
                "secret-redaction",
                "optional-lossless-wire-encoding",
                "route-receipt",
            ],
            "policy": {
                "profile": self.policy.profile,
                "legacy_profile": self.policy.legacy_profile,
            },
            "schema": self.schema_status(),
        }
