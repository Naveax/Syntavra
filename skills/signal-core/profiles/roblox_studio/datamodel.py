from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class NodeType(StrEnum):
    SERVICE="Service"; INSTANCE="Instance"; SCRIPT="Script"; LOCAL_SCRIPT="LocalScript"; MODULE_SCRIPT="ModuleScript"; REMOTE_EVENT="RemoteEvent"; REMOTE_FUNCTION="RemoteFunction"; BINDABLE_EVENT="BindableEvent"; BINDABLE_FUNCTION="BindableFunction"; DATASTORE="DataStore"; ATTRIBUTE="Attribute"; TAG="Tag"; ASSET="Asset"; ANIMATION="Animation"; UI_ELEMENT="UIElement"; TEST_SCENARIO="TestScenario"; RUNTIME_EVENT="RuntimeEvent"; PLAYER="Player"; CLIENT="Client"; SERVER="Server"


class EdgeType(StrEnum):
    PARENTS="PARENTS"; REQUIRES="REQUIRES"; FIRES="FIRES"; INVOKES="INVOKES"; HANDLES="HANDLES"; READS="READS"; WRITES="WRITES"; REPLICATES_TO="REPLICATES_TO"; OWNS="OWNS"; USES_DATASTORE="USES_DATASTORE"; USES_ASSET="USES_ASSET"; VALIDATED_BY="VALIDATED_BY"; TESTED_BY="TESTED_BY"; PRODUCES="PRODUCES"; CONSUMES="CONSUMES"


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: str
    node_type: NodeType
    path: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    edge_type: EdgeType


@dataclass(frozen=True, slots=True)
class LuauFinding:
    code: str
    severity: str
    line: int
    message: str


_REQUIRE = re.compile(r"require\s*\(([^)]+)\)")


def analyze_luau(source: str) -> tuple[tuple[str, ...], tuple[LuauFinding, ...]]:
    requires = tuple(match.group(1).strip() for match in _REQUIRE.finditer(source))
    findings: list[LuauFinding] = []
    lines = source.splitlines()
    for index, line in enumerate(lines, start=1):
        lowered = line.casefold()
        if ":fireserver(" in lowered and "rate" not in source.casefold():
            findings.append(LuauFinding("MISSING_RATE_LIMIT", "HIGH", index, "RemoteEvent client call has no visible rate-limit contract"))
        if "onserverevent" in lowered and "validate" not in source.casefold():
            findings.append(LuauFinding("MISSING_SERVER_VALIDATION", "CRITICAL", index, "RemoteEvent handler lacks visible server-side validation"))
        if "while true do" in lowered and "task.wait" not in source.casefold():
            findings.append(LuauFinding("EXPENSIVE_LOOP", "HIGH", index, "Unbounded loop has no yield"))
        if "waitforchild(" in lowered and "," not in line:
            findings.append(LuauFinding("INFINITE_YIELD_RISK", "MEDIUM", index, "WaitForChild has no timeout"))
        if "getasync(" in lowered and "pcall" not in source.casefold():
            findings.append(LuauFinding("UNSAFE_DATASTORE", "HIGH", index, "DataStore call has no visible pcall"))
        if ".connect(" in lowered and "disconnect" not in source.casefold():
            findings.append(LuauFinding("LEAKED_CONNECTION", "MEDIUM", index, "Connection lifecycle is not visible"))
    return requires, tuple(findings)
