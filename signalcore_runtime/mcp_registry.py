from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .schema_registry import SchemaDefinition, SchemaRegistry


class ToolRegistryError(RuntimeError):
    pass


ToolHandler = Callable[[Any, dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: ToolHandler
    version: str = "1"
    permissions: tuple[str, ...] = ()
    timeout_seconds: float = 60.0
    cost_budget: float = 0.0
    sandbox_profile: str = "none"
    approval_required: bool = False
    deprecated: bool = False
    replacement: str = ""

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
            "annotations": {
                "signalcore/version": self.version,
                "signalcore/permissions": list(self.permissions),
                "signalcore/timeout_seconds": self.timeout_seconds,
                "signalcore/cost_budget": self.cost_budget,
                "signalcore/sandbox_profile": self.sandbox_profile,
                "signalcore/approval_required": self.approval_required,
                "signalcore/deprecated": self.deprecated,
                "signalcore/replacement": self.replacement,
            },
        }


class MCPToolRegistry:
    """Modular MCP tool registry with versioning, permissions and deprecation."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if not definition.name.startswith("signalcore.") or definition.name in self._tools:
            raise ToolRegistryError("invalid or duplicate tool name")
        if definition.timeout_seconds <= 0 or definition.cost_budget < 0:
            raise ToolRegistryError("invalid tool execution policy")
        self._tools[definition.name] = definition

    def tools(self, *, include_deprecated: bool = False) -> list[dict[str, Any]]:
        return [
            self._tools[name].catalog_entry()
            for name in sorted(self._tools)
            if include_deprecated or not self._tools[name].deprecated
        ]

    def invoke(
        self,
        server: Any,
        name: str,
        arguments: dict[str, Any],
        *,
        granted_permissions: tuple[str, ...] = ("*",),
        approved: bool = False,
    ) -> Any:
        definition = self._tools.get(name)
        if definition is None:
            raise KeyError(name)
        granted = set(granted_permissions)
        missing = [item for item in definition.permissions if "*" not in granted and item not in granted]
        if missing:
            raise ToolRegistryError("tool permissions denied: " + ",".join(sorted(missing)))
        if definition.approval_required and not approved:
            raise ToolRegistryError("tool requires explicit approval")
        return definition.handler(server, arguments)

    def definition(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]

    def catalog(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "tools": self.tools(include_deprecated=True),
            "count": len(self._tools),
        }
