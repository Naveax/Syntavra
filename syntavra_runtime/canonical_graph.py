from __future__ import annotations

from pathlib import Path
from typing import Any

from .repository_query import RepositoryQueryEngine
from .semantic_intelligence import IncrementalCodeIntelligenceGraph
from .tree_sitter_adapter import TreeSitterLanguageAdapter


class CanonicalRepositoryGraph(IncrementalCodeIntelligenceGraph):
    """Single product graph joining syntax, LSP and imported semantic evidence."""

    def __init__(self, path: Path, **kwargs: Any) -> None:
        super().__init__(path, **kwargs)
        self.tree_sitter_adapter = TreeSitterLanguageAdapter()
        if self.tree_sitter_adapter.installed:
            available = tuple(
                language
                for language in self.tree_sitter_adapter.language_ids
                if language != "python" and self.languages.adapter_for(language) is None
            )
            if available:
                # Register only unclaimed language ids. LanguageRegistry maps every
                # advertised id at once, so retaining the full class-level tuple here
                # could overwrite an explicitly injected semantic adapter.
                self.tree_sitter_adapter.language_ids = available
                self.languages.register_adapter(self.tree_sitter_adapter)
        self.repository_query = RepositoryQueryEngine(path)
        self.repository_query.refresh()

    def index_repository(self, root: Path, *, max_file_bytes: int = 2_000_000) -> dict[str, Any]:
        result = super().index_repository(root, max_file_bytes=max_file_bytes)
        query_index = self.repository_query.refresh()
        return {**result, "repository_query": query_index, "canonical_graph": True}

    def import_semantic_index(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().import_semantic_index(*args, **kwargs)
        result["repository_query"] = self.repository_query.refresh()
        return result

    def remove_semantic_index(self, source_key: str) -> dict[str, Any]:
        result = super().remove_semantic_index(source_key)
        result["repository_query"] = self.repository_query.refresh()
        return result

    def query(self, text: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.repository_query.query(text, limit=limit)

    def language_status(self, repository_root: Path | None = None) -> dict[str, Any]:
        value = super().language_status(repository_root)
        value["tree_sitter"] = self.tree_sitter_adapter.manifest()
        value["repository_query"] = self.repository_query.stats()
        value["canonical_graph"] = True
        return value

    def stats(self) -> dict[str, Any]:
        value = super().stats()
        value["repository_query"] = self.repository_query.stats()
        value["canonical_graph"] = True
        return value


__all__ = ["CanonicalRepositoryGraph"]
