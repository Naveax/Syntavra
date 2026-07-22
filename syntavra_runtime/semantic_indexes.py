from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class SemanticIndexNode:
    node_id: str
    path: str
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    language: str
    evidence_ref: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class SemanticIndexEdge:
    source: str
    target: str
    edge_type: str
    confidence: float
    evidence_ref: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class SemanticIndexBundle:
    format: str
    source_sha256: str
    repository_commit: str | None
    nodes: tuple[SemanticIndexNode, ...]
    edges: tuple[SemanticIndexEdge, ...]
    diagnostics: tuple[str, ...]


class _IndexLimits:
    def __init__(
        self,
        *,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        max_records: int = 10_000_000,
        max_nodes: int = 5_000_000,
        max_edges: int = 20_000_000,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_records = max_records
        self.max_nodes = max_nodes
        self.max_edges = max_edges


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)

    def normalize(self, value: str) -> str:
        text = str(value)
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme == "file":
            decoded = urllib.parse.unquote(parsed.path)
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                raise ValueError("remote file URI is not accepted in semantic index")
            if re.match(r"^/[A-Za-z]:/", decoded):
                decoded = decoded[1:]
            path = Path(decoded)
        else:
            path = Path(text)
        if path.is_absolute():
            resolved = path.resolve(strict=False)
        else:
            resolved = (self.root / path).resolve(strict=False)
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as error:
            raise PermissionError(f"semantic index path escapes repository: {value}") from error


def _source_digest(path: Path, limit: int) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > limit:
        raise ValueError("semantic index exceeds configured size limit")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _line_range(value: Any) -> tuple[int, int]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = [int(item) for item in value]
        if len(values) == 3:
            return values[0] + 1, values[0] + 1
        if len(values) >= 4:
            return values[0] + 1, max(values[0], values[2]) + 1
    if isinstance(value, Mapping):
        start = value.get("start") if isinstance(value.get("start"), Mapping) else {}
        end = value.get("end") if isinstance(value.get("end"), Mapping) else start
        start_line = max(0, int(start.get("line", 0))) + 1
        end_line = max(start_line - 1, int(end.get("line", start_line - 1))) + 1
        return start_line, end_line
    return 1, 1


class LSIFImporter:
    def __init__(self, *, limits: _IndexLimits | None = None) -> None:
        self.limits = limits or _IndexLimits()

    def load(self, path: Path, *, repository_root: Path, repository_commit: str | None = None) -> SemanticIndexBundle:
        path = path.resolve(strict=True)
        digest = _source_digest(path, self.limits.max_bytes)
        paths = _Paths(repository_root)
        vertices: dict[str, Mapping[str, Any]] = {}
        edges: list[Mapping[str, Any]] = []
        diagnostics: list[str] = []
        record_count = 0
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                record_count += 1
                if record_count > self.limits.max_records:
                    raise ValueError("LSIF record limit exceeded")
                if len(line.encode("utf-8")) > 64 * 1024 * 1024:
                    raise ValueError("LSIF line exceeds limit")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"invalid LSIF JSON at line {line_number}") from error
                if not isinstance(record, Mapping):
                    raise ValueError("LSIF record must be an object")
                record_id = str(record.get("id") or "")
                record_type = record.get("type")
                if record_type == "vertex":
                    if not record_id or record_id in vertices:
                        raise ValueError("LSIF vertex id is missing or duplicated")
                    vertices[record_id] = record
                elif record_type == "edge":
                    edges.append(record)
                else:
                    diagnostics.append(f"unknown-record-type:{line_number}")

        documents = {key: value for key, value in vertices.items() if value.get("label") == "document"}
        ranges = {key: value for key, value in vertices.items() if value.get("label") == "range"}
        monikers = {key: value for key, value in vertices.items() if value.get("label") == "moniker"}
        metadata = next((value for value in vertices.values() if value.get("label") == "metaData"), None)
        index_commit = repository_commit
        if isinstance(metadata, Mapping):
            project_root = metadata.get("projectRoot")
            if project_root:
                try:
                    paths.normalize(str(project_root))
                except (ValueError, PermissionError):
                    diagnostics.append("metadata-project-root-outside-repository")
            version = metadata.get("version")
            if version:
                diagnostics.append(f"lsif-version:{version}")

        contains: dict[str, str] = {}
        next_edges: dict[str, str] = {}
        moniker_edges: dict[str, str] = {}
        result_links: dict[str, list[str]] = {}
        relation_result: dict[tuple[str, str], str] = {}
        for edge in edges:
            label = str(edge.get("label") or "")
            out_v = str(edge.get("outV") or "")
            in_v = str(edge.get("inV") or "")
            in_vs = [str(item) for item in edge.get("inVs", [])] if isinstance(edge.get("inVs"), list) else []
            if label == "contains" and out_v in documents:
                for target in in_vs:
                    if target in ranges:
                        contains[target] = out_v
            elif label == "next" and out_v and in_v:
                next_edges[out_v] = in_v
            elif label == "moniker" and out_v and in_v in monikers:
                moniker_edges[out_v] = in_v
            elif label in {"textDocument/definition", "textDocument/references", "textDocument/implementation"} and out_v and in_v:
                relation_result[(out_v, label)] = in_v
            elif label == "item" and out_v:
                targets = in_vs or ([in_v] if in_v else [])
                result_links.setdefault(out_v, []).extend(targets)

        node_by_range: dict[str, SemanticIndexNode] = {}
        semantic_nodes: list[SemanticIndexNode] = []
        for range_id, value in ranges.items():
            document_id = contains.get(range_id)
            document = documents.get(document_id or "")
            if not document:
                continue
            relative = paths.normalize(str(document.get("uri") or ""))
            result_set = next_edges.get(range_id, range_id)
            moniker_id = moniker_edges.get(result_set) or moniker_edges.get(range_id)
            moniker = monikers.get(moniker_id or "")
            identifier = str((moniker or {}).get("identifier") or "").strip()
            name = identifier.rsplit("/", 1)[-1].rsplit("#", 1)[-1] if identifier else f"range@{range_id}"
            start_line, end_line = _line_range(value)
            node_id = "lsif:" + hashlib.sha256(f"{digest}\0{range_id}".encode("utf-8")).hexdigest()
            node = SemanticIndexNode(
                node_id=node_id,
                path=relative,
                kind="symbol" if identifier else "semantic-range",
                name=name,
                qualified_name=identifier or f"{relative}:{start_line}",
                start_line=start_line,
                end_line=end_line,
                language=str(document.get("languageId") or "unknown").casefold(),
                evidence_ref=f"sha256:{digest}",
                metadata={
                    "source": "lsif",
                    "exact_semantic": bool(identifier),
                    "lsif_range_id": range_id,
                    "lsif_moniker_kind": (moniker or {}).get("kind"),
                    "repository_commit": index_commit,
                },
            )
            node_by_range[range_id] = node
            semantic_nodes.append(node)
            if len(semantic_nodes) > self.limits.max_nodes:
                raise ValueError("LSIF node limit exceeded")

        semantic_edges: list[SemanticIndexEdge] = []
        relation_types = {
            "textDocument/definition": "defines",
            "textDocument/references": "references",
            "textDocument/implementation": "implements",
        }
        for source_range, source_node in node_by_range.items():
            lookup = next_edges.get(source_range, source_range)
            for label, edge_type in relation_types.items():
                result_id = relation_result.get((lookup, label)) or relation_result.get((source_range, label))
                if not result_id:
                    continue
                for target_range in result_links.get(result_id, []):
                    target_node = node_by_range.get(target_range)
                    if target_node is None:
                        continue
                    semantic_edges.append(
                        SemanticIndexEdge(
                            source=source_node.node_id,
                            target=target_node.node_id,
                            edge_type=edge_type,
                            confidence=1.0,
                            evidence_ref=f"sha256:{digest}",
                            metadata={"source": "lsif", "exact_semantic": True, "repository_commit": index_commit},
                        )
                    )
                    if len(semantic_edges) > self.limits.max_edges:
                        raise ValueError("LSIF edge limit exceeded")

        return SemanticIndexBundle(
            format="lsif",
            source_sha256=digest,
            repository_commit=index_commit,
            nodes=tuple(semantic_nodes),
            edges=tuple(semantic_edges),
            diagnostics=tuple(diagnostics),
        )


class SCIPJSONImporter:
    def __init__(self, *, limits: _IndexLimits | None = None) -> None:
        self.limits = limits or _IndexLimits()

    def load(self, path: Path, *, repository_root: Path, repository_commit: str | None = None) -> SemanticIndexBundle:
        path = path.resolve(strict=True)
        digest = _source_digest(path, self.limits.max_bytes)
        paths = _Paths(repository_root)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, Mapping):
            raise ValueError("SCIP JSON root must be an object")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
        index_commit = repository_commit or str(metadata.get("version") or metadata.get("revision") or "") or None
        documents = payload.get("documents") or []
        if not isinstance(documents, list) or len(documents) > self.limits.max_records:
            raise ValueError("SCIP document list is invalid or too large")
        nodes: list[SemanticIndexNode] = []
        edges: list[SemanticIndexEdge] = []
        symbol_nodes: dict[str, str] = {}
        occurrence_nodes: dict[tuple[str, int], str] = {}
        diagnostics: list[str] = []

        for document in documents:
            if not isinstance(document, Mapping):
                raise ValueError("SCIP document must be an object")
            relative = paths.normalize(str(document.get("relative_path") or document.get("relativePath") or ""))
            language = str(document.get("language") or metadata.get("tool_info", {}).get("name") or "unknown").casefold()
            symbols = document.get("symbols") or []
            occurrences = document.get("occurrences") or []
            if not isinstance(symbols, list) or not isinstance(occurrences, list):
                raise ValueError("SCIP symbols and occurrences must be arrays")

            for raw in symbols:
                if not isinstance(raw, Mapping):
                    raise ValueError("SCIP symbol information must be an object")
                symbol = str(raw.get("symbol") or "").strip()
                if not symbol:
                    continue
                node_id = symbol_nodes.get(symbol)
                if node_id is None:
                    node_id = "scip-symbol:" + hashlib.sha256(f"{digest}\0{symbol}".encode("utf-8")).hexdigest()
                    symbol_nodes[symbol] = node_id
                    display_name = symbol.rstrip("/.").rsplit("/", 1)[-1].rsplit("#", 1)[-1] or symbol
                    nodes.append(
                        SemanticIndexNode(
                            node_id=node_id,
                            path=relative,
                            kind="symbol",
                            name=display_name,
                            qualified_name=symbol,
                            start_line=1,
                            end_line=1,
                            language=language,
                            evidence_ref=f"sha256:{digest}",
                            metadata={"source": "scip", "exact_semantic": True, "repository_commit": index_commit},
                        )
                    )
                relationships = raw.get("relationships") or []
                if not isinstance(relationships, list):
                    raise ValueError("SCIP relationships must be an array")
                for relationship in relationships:
                    if not isinstance(relationship, Mapping):
                        continue
                    target_symbol = str(relationship.get("symbol") or "").strip()
                    if not target_symbol:
                        continue
                    target_id = symbol_nodes.get(target_symbol)
                    if target_id is None:
                        target_id = "scip-symbol:" + hashlib.sha256(f"{digest}\0{target_symbol}".encode("utf-8")).hexdigest()
                        symbol_nodes[target_symbol] = target_id
                        nodes.append(
                            SemanticIndexNode(
                                node_id=target_id,
                                path=relative,
                                kind="external-symbol",
                                name=target_symbol.rstrip("/.").rsplit("/", 1)[-1] or target_symbol,
                                qualified_name=target_symbol,
                                start_line=1,
                                end_line=1,
                                language=language,
                                evidence_ref=f"sha256:{digest}",
                                metadata={"source": "scip", "exact_semantic": True, "external": True, "repository_commit": index_commit},
                            )
                        )
                    if relationship.get("is_implementation") or relationship.get("isImplementation"):
                        edge_type = "implements"
                    elif relationship.get("is_type_definition") or relationship.get("isTypeDefinition"):
                        edge_type = "type-definition"
                    elif relationship.get("is_reference") or relationship.get("isReference"):
                        edge_type = "references"
                    else:
                        edge_type = "related"
                    edges.append(
                        SemanticIndexEdge(
                            source=node_id,
                            target=target_id,
                            edge_type=edge_type,
                            confidence=1.0,
                            evidence_ref=f"sha256:{digest}",
                            metadata={"source": "scip", "exact_semantic": True, "repository_commit": index_commit},
                        )
                    )

            for occurrence_index, occurrence in enumerate(occurrences):
                if not isinstance(occurrence, Mapping):
                    raise ValueError("SCIP occurrence must be an object")
                symbol = str(occurrence.get("symbol") or "").strip()
                if not symbol:
                    continue
                start_line, end_line = _line_range(occurrence.get("range"))
                node_id = "scip-occurrence:" + hashlib.sha256(
                    f"{digest}\0{relative}\0{occurrence_index}\0{symbol}".encode("utf-8")
                ).hexdigest()
                occurrence_nodes[(relative, occurrence_index)] = node_id
                nodes.append(
                    SemanticIndexNode(
                        node_id=node_id,
                        path=relative,
                        kind="symbol-occurrence",
                        name=symbol.rstrip("/.").rsplit("/", 1)[-1] or symbol,
                        qualified_name=symbol,
                        start_line=start_line,
                        end_line=end_line,
                        language=language,
                        evidence_ref=f"sha256:{digest}",
                        metadata={
                            "source": "scip",
                            "exact_semantic": True,
                            "symbol_roles": occurrence.get("symbol_roles") or occurrence.get("symbolRoles"),
                            "syntax_kind": occurrence.get("syntax_kind") or occurrence.get("syntaxKind"),
                            "repository_commit": index_commit,
                        },
                    )
                )
                target_id = symbol_nodes.get(symbol)
                if target_id:
                    edges.append(
                        SemanticIndexEdge(
                            source=node_id,
                            target=target_id,
                            edge_type="resolves-to",
                            confidence=1.0,
                            evidence_ref=f"sha256:{digest}",
                            metadata={"source": "scip", "exact_semantic": True, "repository_commit": index_commit},
                        )
                    )
            if len(nodes) > self.limits.max_nodes or len(edges) > self.limits.max_edges:
                raise ValueError("SCIP graph limit exceeded")

        return SemanticIndexBundle(
            format="scip-json",
            source_sha256=digest,
            repository_commit=index_commit,
            nodes=tuple(nodes),
            edges=tuple(edges),
            diagnostics=tuple(diagnostics),
        )


def load_semantic_index(
    path: Path,
    *,
    repository_root: Path,
    format: str = "auto",
    repository_commit: str | None = None,
) -> SemanticIndexBundle:
    selected = format.casefold()
    if selected == "auto":
        name = path.name.casefold()
        if name.endswith(".lsif") or name.endswith(".lsif.jsonl") or name.endswith(".lsif.json"):
            selected = "lsif"
        elif name.endswith(".scip.json") or name.endswith(".scip-json"):
            selected = "scip-json"
        elif name.endswith(".scip"):
            raise ValueError("binary SCIP requires a hash-pinned conversion service; use SCIP JSON export")
        else:
            raise ValueError("semantic index format cannot be detected")
    if selected == "lsif":
        return LSIFImporter().load(path, repository_root=repository_root, repository_commit=repository_commit)
    if selected in {"scip-json", "scip_json"}:
        return SCIPJSONImporter().load(path, repository_root=repository_root, repository_commit=repository_commit)
    raise ValueError(f"unsupported semantic index format: {format}")


__all__ = [
    "LSIFImporter",
    "SCIPJSONImporter",
    "SemanticIndexBundle",
    "SemanticIndexEdge",
    "SemanticIndexNode",
    "load_semantic_index",
]
