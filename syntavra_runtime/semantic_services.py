from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .language_lsp import LSPServiceRegistry
from .language_platform import LanguageDescriptor, LanguageRegistry as UniversalLanguageRegistry
from .language_services import LanguageServiceRegistry as SandboxedLanguageServiceRegistry


@dataclass(frozen=True)
class LanguageServiceSpec:
    """Compatibility descriptor.

    Commands are retained only as descriptive migration metadata. They are
    never executed by this compatibility layer because unpinned executables
    cannot establish trusted semantic evidence.
    """

    language: str
    suffixes: tuple[str, ...] = ()
    commands: tuple[tuple[str, ...], ...] = ()
    semantic_features: tuple[str, ...] = (
        "definition",
        "references",
        "implementation",
        "type-definition",
        "call-hierarchy",
        "workspace-symbol",
        "diagnostics",
        "rename-preview",
        "code-actions",
    )


@dataclass(frozen=True)
class LanguageServiceStatus:
    language: str
    available: bool
    command: tuple[str, ...] | None
    features: tuple[str, ...]
    detail: str = ""
    evidence_level: str = "lexical"
    source: str = "universal-registry"


# A closed product whitelist is intentionally empty. Runtime descriptors,
# repository manifests, hash-pinned analyzers, generic LSP manifests and
# semantic indexes define actual language coverage.
DEFAULT_LANGUAGE_SERVICES: tuple[LanguageServiceSpec, ...] = ()


class LanguageServiceRegistry:
    """Compatibility facade over the universal language platform.

    Existing callers may continue to request status, but availability now
    means a trusted adapter or data manifest exists. Merely finding an
    executable on PATH is not treated as certified semantic support.
    """

    def __init__(self, specs: Sequence[LanguageServiceSpec] = DEFAULT_LANGUAGE_SERVICES):
        self.specs = tuple(specs)
        self.languages = UniversalLanguageRegistry()
        self.analyzers = SandboxedLanguageServiceRegistry()
        self.lsp_services = LSPServiceRegistry()
        for spec in self.specs:
            self.languages.register_descriptor(
                LanguageDescriptor(
                    language_id=spec.language.casefold(),
                    suffixes=tuple(suffix.casefold() for suffix in spec.suffixes),
                    capabilities=frozenset({"lexical"}),
                    source="compatibility-spec",
                )
            )

    def discover(self, root: Path | None = None) -> None:
        self.languages.discover_manifests(root)
        self.analyzers.discover(root)
        self.lsp_services.discover(root)

    def detect(self, root: Path | None = None) -> list[LanguageServiceStatus]:
        self.discover(root)
        language_ids = set(self.languages.inventory()["languages"])
        language_ids.update(self.analyzers.inventory()["languages"])
        language_ids.update(self.lsp_services.inventory()["languages"])
        feature_by_language = {spec.language.casefold(): spec.semantic_features for spec in self.specs}
        rows: list[LanguageServiceStatus] = []
        for language_id in sorted(language_ids):
            adapter = self.languages.adapter_for(language_id)
            analyzer_manifests = self.analyzers.for_language(language_id)
            lsp_manifests = tuple(
                manifest for manifest in self.lsp_services.manifests.values() if language_id in manifest.language_ids
            )
            available = adapter is not None or bool(analyzer_manifests) or bool(lsp_manifests)
            evidence_level = "semantic" if available else "lexical"
            if adapter is not None:
                source = f"adapter:{type(adapter).__module__}.{type(adapter).__qualname__}"
                detail = "trusted adapter registered"
            elif analyzer_manifests:
                source = "sandboxed-analyzer-manifest"
                detail = "hash-pinned analyzer manifest discovered; execution requires explicit authorization"
            elif lsp_manifests:
                source = "generic-lsp-manifest"
                detail = "hash-pinned LSP manifest discovered; execution requires explicit authorization"
            else:
                source = "universal-fallback"
                detail = "lexical navigation available; exact semantic evidence not configured"
            rows.append(
                LanguageServiceStatus(
                    language=language_id,
                    available=available,
                    command=None,
                    features=tuple(feature_by_language.get(language_id, ())),
                    detail=detail,
                    evidence_level=evidence_level,
                    source=source,
                )
            )
        return rows

    def status(self, root: Path | None = None) -> dict[str, Any]:
        rows = self.detect(root)
        return {
            "ok": True,
            "languages": [asdict(row) for row in rows],
            "available": sum(1 for row in rows if row.available),
            "declared": len(rows),
            "universal_text_fallback": True,
            "fixed_language_whitelist": False,
            "analyzers": self.analyzers.inventory(),
            "lsp_services": self.lsp_services.inventory(),
            "claim_boundary": (
                "lexical fallback is universal; exact semantic support requires a validated adapter, "
                "hash-pinned analyzer, hash-pinned LSP server, or fresh LSIF/SCIP evidence"
            ),
        }

    def for_path(self, path: Path) -> LanguageServiceStatus | None:
        source = path.resolve(strict=True)
        if not source.is_file():
            return None
        detection = self.languages.detect(source, source.read_bytes())
        rows = {row.language: row for row in self.detect(source.parent)}
        return rows.get(detection.language_id) or LanguageServiceStatus(
            language=detection.language_id,
            available=False,
            command=None,
            features=(),
            detail="universal lexical fallback",
            evidence_level=detection.capability_level,
            source=detection.evidence,
        )


class LSPProtocolError(RuntimeError):
    pass


class LSPClient:
    """Fail-closed compatibility shell for the removed direct LSP client.

    Directly launching an arbitrary executable from a command tuple is no
    longer supported. Use a hash-pinned `LSPServiceManifest` through the
    generic sandboxed LSP bridge.
    """

    def __init__(self, command: Sequence[str], root: Path, *, timeout: float = 15.0, executable_sha256: str | None = None):
        self.command = tuple(command)
        self.root = root.resolve(strict=True)
        self.timeout = float(timeout)
        self.executable_sha256 = executable_sha256
        if not executable_sha256:
            raise ValueError(
                "direct LSP execution is disabled; configure a hash-pinned LSP service manifest instead"
            )

    def start(self) -> dict[str, Any]:
        raise LSPProtocolError(
            "direct LSP client compatibility mode cannot execute; use GenericLSPAdapter"
        )

    def workspace_symbols(self, query: str) -> list[dict[str, Any]]:
        raise LSPProtocolError(
            "direct LSP client compatibility mode cannot execute; use GenericLSPAdapter"
        )

    def close(self) -> None:
        return None

    def __enter__(self) -> "LSPClient":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class SemanticIndexImporter:
    """Compatibility facade for the graph-owned atomic semantic importer."""

    def __init__(self, graph: Any):
        if not hasattr(graph, "import_semantic_index"):
            raise TypeError(
                "SemanticIndexImporter now requires IncrementalCodeIntelligenceGraph; "
                "runtime-evidence-only imports are not exact semantic graph imports"
            )
        self.graph = graph

    def import_lsif(
        self,
        path: Path,
        *,
        repository_root: Path | None = None,
        repository_commit: str | None = None,
        current_commit: str | None = None,
        allow_stale: bool = False,
    ) -> dict[str, Any]:
        source = path.resolve(strict=True)
        return self.graph.import_semantic_index(
            source,
            repository_root=(repository_root or source.parent).resolve(strict=True),
            format="lsif",
            repository_commit=repository_commit,
            current_commit=current_commit,
            allow_stale=allow_stale,
        )

    def import_scip_json(
        self,
        path: Path,
        *,
        repository_root: Path | None = None,
        repository_commit: str | None = None,
        current_commit: str | None = None,
        allow_stale: bool = False,
    ) -> dict[str, Any]:
        source = path.resolve(strict=True)
        return self.graph.import_semantic_index(
            source,
            repository_root=(repository_root or source.parent).resolve(strict=True),
            format="scip-json",
            repository_commit=repository_commit,
            current_commit=current_commit,
            allow_stale=allow_stale,
        )


__all__ = [
    "DEFAULT_LANGUAGE_SERVICES",
    "LSPClient",
    "LSPProtocolError",
    "LanguageServiceRegistry",
    "LanguageServiceSpec",
    "LanguageServiceStatus",
    "SemanticIndexImporter",
]
