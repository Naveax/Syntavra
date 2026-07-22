from __future__ import annotations

import ast
from typing import Any, Iterable

from .language_platform import LanguageDetection

_SCOPE_TYPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _scope_nodes(scope: ast.AST) -> Iterable[ast.AST]:
    """Yield nodes owned by one lexical scope without entering nested scopes."""

    stack = list(reversed(list(ast.iter_child_nodes(scope))))
    while stack:
        item = stack.pop()
        yield item
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(item))))


def _scope_binds_name(scope: ast.AST, module: ast.Module, name: str) -> bool:
    """Return whether a lexical scope can shadow a module-level symbol name."""

    if scope is module:
        return False

    owned = tuple(_scope_nodes(scope))
    global_names = {
        declared
        for item in owned
        if isinstance(item, ast.Global)
        for declared in item.names
    }
    if name in global_names:
        return False

    for item in owned:
        if isinstance(item, ast.arg) and item.arg == name:
            return True
        if isinstance(item, ast.Name) and item.id == name and isinstance(item.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == name:
            return True
        if isinstance(item, ast.alias):
            bound = item.asname or item.name.split(".", 1)[0]
            if bound == name:
                return True
        if isinstance(item, ast.Nonlocal) and name in item.names:
            return True
    return False


def _nearest_scope(node: ast.AST, parents: dict[ast.AST, ast.AST], module: ast.Module) -> ast.AST:
    current: ast.AST = parents.get(node, module)
    while not isinstance(current, _SCOPE_TYPES):
        current = parents.get(current, module)
    return current


def scope_aware_python_parse(
    self: Any,
    relative: str,
    text: str,
    evidence: str,
    detection: LanguageDetection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Build Python syntax evidence without overstating ambiguous name resolution."""

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    tree = ast.parse(text, filename=relative)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    module = self._module_node(relative, text, "python", evidence, detection, exact=True, source="python-ast")
    module_id = module["node_id"]
    nodes.append(module)

    top_level_counts: dict[str, int] = {}
    for candidate in tree.body:
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top_level_counts[candidate.name] = top_level_counts.get(candidate.name, 0) + 1

    symbol_ids_by_node: dict[ast.AST, str] = {}
    top_level_symbols: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        node_id = self._node_id(relative, kind, node.name, node.lineno)
        symbol_ids_by_node[node] = node_id
        if node in tree.body and top_level_counts.get(node.name) == 1:
            top_level_symbols[node.name] = node_id
        nodes.append(
            {
                "node_id": node_id,
                "path": relative,
                "kind": kind,
                "name": node.name,
                "qualified_name": f"{relative}:{node.name}",
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "language": "python",
                "evidence_ref": evidence,
                "metadata_json": self._metadata(source="python-ast", exact_semantic=True, capability_level="syntax"),
            }
        )
        edges.append(
            {
                "source": module_id,
                "target": node_id,
                "edge_type": "defines",
                "confidence": 1.0,
                "evidence_ref": evidence,
                "metadata_json": self._metadata(source="python-ast", exact_semantic=True),
            }
        )

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        names = [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        for name in names:
            edges.append(
                {
                    "source": module_id,
                    "target": f"external:{name}",
                    "edge_type": "imports",
                    "confidence": 0.98,
                    "evidence_ref": evidence,
                    "metadata_json": self._metadata(external=True, source="python-ast", exact_semantic=True),
                }
            )

    for call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
        name = (
            call.func.id
            if isinstance(call.func, ast.Name)
            else call.func.attr
            if isinstance(call.func, ast.Attribute)
            else ""
        )
        target_id = top_level_symbols.get(name)
        if target_id is None:
            continue

        scope = _nearest_scope(call, parents, tree)
        source_id = symbol_ids_by_node.get(scope, module_id)
        exact_resolution = isinstance(call.func, ast.Name) and not _scope_binds_name(scope, tree, name)
        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "edge_type": "calls",
                "confidence": 1.0 if exact_resolution else 0.72,
                "evidence_ref": evidence,
                "metadata_json": self._metadata(
                    source="python-ast",
                    exact_semantic=exact_resolution,
                    resolution=(
                        "unique-top-level-lexical-scope"
                        if exact_resolution
                        else "same-file-name-shadow-or-attribute-risk"
                    ),
                ),
            }
        )
    return nodes, edges, []


def install(graph_type: type[Any]) -> None:
    """Install the resolver idempotently on the shared graph implementation."""

    if getattr(graph_type, "_syntavra_scope_aware_python", False):
        return
    graph_type._python = scope_aware_python_parse
    graph_type._syntavra_scope_aware_python = True


__all__ = ["install", "scope_aware_python_parse"]
