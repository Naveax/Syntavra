from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .execution_sandbox import NativeSandboxBroker, SandboxPolicy
from .language_platform import LanguageParseResult


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,127}$")
_CAPABILITIES = frozenset(
    {
        "lexical",
        "syntax",
        "semantic",
        "definitions",
        "references",
        "implementations",
        "types",
        "call-hierarchy",
        "diagnostics",
        "rename",
        "code-actions",
    }
)


@dataclass(frozen=True)
class LanguageServiceManifest:
    service_id: str
    language_ids: tuple[str, ...]
    command: tuple[str, ...]
    executable_sha256: str
    capabilities: frozenset[str]
    timeout_seconds: float = 30.0
    max_output_bytes: int = 8 * 1024 * 1024
    max_nodes: int = 250_000
    max_edges: int = 1_000_000
    strict_native: bool = True
    source: str = ""

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        source: str,
        base_directory: Path | None = None,
    ) -> "LanguageServiceManifest":
        service_id = str(value.get("id") or value.get("service_id") or "").strip().casefold()
        if not _ID_RE.fullmatch(service_id):
            raise ValueError("language service id is invalid")

        raw_languages = value.get("languages") or value.get("language_ids") or ()
        if isinstance(raw_languages, str):
            raw_languages = [raw_languages]
        language_ids = tuple(dict.fromkeys(str(item).strip().casefold() for item in raw_languages if str(item).strip()))
        if not language_ids or any(not _ID_RE.fullmatch(item) for item in language_ids):
            raise ValueError("language service requires valid language ids")

        raw_command = value.get("command") or ()
        if isinstance(raw_command, str):
            raise ValueError("language service command must be an argv sequence, never a shell string")
        command = tuple(str(item) for item in raw_command)
        if not command or any(not item or "\x00" in item for item in command):
            raise ValueError("language service command must be a non-empty safe argv sequence")
        executable = Path(command[0]).expanduser()
        if not executable.is_absolute() and base_directory is not None:
            local = (base_directory / executable).resolve(strict=False)
            if local.exists():
                executable = local
                command = (str(executable), *command[1:])

        digest = str(value.get("executable_sha256") or "").strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("language service executable_sha256 is mandatory")

        raw_capabilities = value.get("capabilities") or ("syntax",)
        if isinstance(raw_capabilities, str):
            raw_capabilities = [raw_capabilities]
        capabilities = frozenset(str(item).casefold() for item in raw_capabilities)
        unknown = capabilities - _CAPABILITIES
        if unknown:
            raise ValueError(f"unsupported language service capabilities: {sorted(unknown)}")
        if not capabilities:
            raise ValueError("language service capabilities cannot be empty")

        timeout = float(value.get("timeout_seconds", 30.0))
        output_limit = int(value.get("max_output_bytes", 8 * 1024 * 1024))
        max_nodes = int(value.get("max_nodes", 250_000))
        max_edges = int(value.get("max_edges", 1_000_000))
        if not 0.1 <= timeout <= 600:
            raise ValueError("language service timeout must be between 0.1 and 600 seconds")
        if not 1024 <= output_limit <= 128 * 1024 * 1024:
            raise ValueError("language service output limit is out of bounds")
        if not 1 <= max_nodes <= 2_000_000 or not 1 <= max_edges <= 8_000_000:
            raise ValueError("language service graph limits are out of bounds")

        return cls(
            service_id=service_id,
            language_ids=language_ids,
            command=command,
            executable_sha256=digest,
            capabilities=capabilities,
            timeout_seconds=timeout,
            max_output_bytes=output_limit,
            max_nodes=max_nodes,
            max_edges=max_edges,
            strict_native=bool(value.get("strict_native", True)),
            source=source,
        )

    @property
    def manifest_hash(self) -> str:
        payload = {
            "service_id": self.service_id,
            "language_ids": self.language_ids,
            "command": self.command,
            "executable_sha256": self.executable_sha256,
            "capabilities": sorted(self.capabilities),
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "max_nodes": self.max_nodes,
            "max_edges": self.max_edges,
            "strict_native": self.strict_native,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class SandboxedLanguageServiceAdapter:
    """Hash-pinned, argv-only, sandboxed analyzer protocol.

    The analyzer receives one JSON object on stdin and must return one JSON
    object on stdout. It never receives repository credentials or the caller's
    environment. An analyzer cannot upgrade its evidence to semantic unless the
    manifest explicitly declares semantic capability.
    """

    def __init__(
        self,
        manifest: LanguageServiceManifest,
        *,
        workspace: Path,
        state_root: Path,
        broker: NativeSandboxBroker | None = None,
    ) -> None:
        self.manifest = manifest
        self.language_ids = manifest.language_ids
        self.capabilities = manifest.capabilities
        self.workspace = workspace.resolve(strict=True)
        self.state_root = state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.broker = broker or NativeSandboxBroker(self.state_root)
        self._executable = self._resolve_and_verify_executable()

    def _resolve_and_verify_executable(self) -> Path:
        raw = self.manifest.command[0]
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=True)
        else:
            located = shutil.which(raw)
            if not located:
                raise FileNotFoundError(f"language service executable not found: {raw}")
            resolved = Path(located).resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("language service executable must be a regular file")
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if not hashlib.compare_digest(actual, self.manifest.executable_sha256):
            raise PermissionError("language service executable hash mismatch")
        return resolved

    @staticmethod
    def _safe_line(value: Any, *, minimum: int = 1) -> int:
        line = int(value)
        if line < minimum or line > 2_147_483_647:
            raise ValueError("language service line value is out of bounds")
        return line

    def _nodes(self, raw_nodes: Any, *, path: str, evidence_ref: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw_nodes, list):
            raise ValueError("language service nodes must be an array")
        if len(raw_nodes) > self.manifest.max_nodes:
            raise ValueError("language service node limit exceeded")
        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()
        exact = "semantic" in self.manifest.capabilities
        for index, raw in enumerate(raw_nodes):
            if not isinstance(raw, Mapping):
                raise ValueError("language service node must be an object")
            name = str(raw.get("name") or "").strip()
            kind = str(raw.get("kind") or "symbol").strip().casefold()
            if not name or len(name) > 4096 or not kind or len(kind) > 128:
                raise ValueError("language service node name or kind is invalid")
            start = self._safe_line(raw.get("start_line", 1))
            end = self._safe_line(raw.get("end_line", start))
            if end < start:
                raise ValueError("language service node range is inverted")
            node_id = str(raw.get("node_id") or f"{self.manifest.service_id}:{path}:{start}:{index}")
            if not node_id or len(node_id) > 8192 or node_id in seen:
                raise ValueError("language service node id is invalid or duplicated")
            seen.add(node_id)
            metadata = raw.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            metadata.update(
                {
                    "source": f"language-service:{self.manifest.service_id}",
                    "service_manifest_sha256": self.manifest.manifest_hash,
                    "service_executable_sha256": self.manifest.executable_sha256,
                    "exact_semantic": exact,
                    "capability_level": "semantic" if exact else "syntax",
                }
            )
            nodes.append(
                {
                    "node_id": node_id,
                    "kind": kind,
                    "name": name,
                    "qualified_name": str(raw.get("qualified_name") or f"{path}:{name}"),
                    "start_line": start,
                    "end_line": end,
                    "evidence_ref": evidence_ref,
                    "metadata": metadata,
                }
            )
        return tuple(nodes)

    def _edges(self, raw_edges: Any, *, node_ids: set[str], evidence_ref: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw_edges, list):
            raise ValueError("language service edges must be an array")
        if len(raw_edges) > self.manifest.max_edges:
            raise ValueError("language service edge limit exceeded")
        exact = "semantic" in self.manifest.capabilities
        edges: list[dict[str, Any]] = []
        for raw in raw_edges:
            if not isinstance(raw, Mapping):
                raise ValueError("language service edge must be an object")
            source = str(raw.get("source") or "")
            target = str(raw.get("target") or "")
            edge_type = str(raw.get("edge_type") or "references").casefold()
            if source not in node_ids:
                raise ValueError("language service edge source is unknown")
            if target not in node_ids and not target.startswith("external:"):
                raise ValueError("language service edge target is unknown")
            if not edge_type or len(edge_type) > 128:
                raise ValueError("language service edge type is invalid")
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 1.0 if exact else 0.8))))
            metadata = raw.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            metadata.update(
                {
                    "source": f"language-service:{self.manifest.service_id}",
                    "service_manifest_sha256": self.manifest.manifest_hash,
                    "exact_semantic": exact,
                }
            )
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "edge_type": edge_type,
                    "confidence": confidence,
                    "evidence_ref": evidence_ref,
                    "metadata": metadata,
                }
            )
        return tuple(edges)

    def parse(self, *, path: str, text: str, evidence_ref: str) -> LanguageParseResult:
        if os.environ.get("SYNTAVRA_ALLOW_LANGUAGE_SERVICES", "").casefold() not in {"1", "true", "yes"}:
            raise PermissionError("language service execution requires explicit SYNTAVRA_ALLOW_LANGUAGE_SERVICES authorization")
        request = {
            "protocol": "syntavra-language-service",
            "operation": "analyze",
            "path": path,
            "language_ids": self.manifest.language_ids,
            "capabilities": sorted(self.manifest.capabilities),
            "evidence_ref": evidence_ref,
            "text": text,
        }
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        command = (str(self._executable), *self.manifest.command[1:])
        receipt = self.broker.run(
            command,
            policy=SandboxPolicy(
                workspace=self.workspace,
                timeout_seconds=self.manifest.timeout_seconds,
                strict_native=self.manifest.strict_native,
                allow_child_processes=False,
                network_hosts=(),
            ),
            input_bytes=encoded,
        )
        if not receipt.ok:
            raise RuntimeError(f"language service failed: exit={receipt.exit_code} timeout={receipt.timed_out}")
        output = receipt.stdout.encode("utf-8")
        if len(output) > self.manifest.max_output_bytes:
            raise ValueError("language service output limit exceeded")
        try:
            payload = json.loads(receipt.stdout)
        except json.JSONDecodeError as error:
            raise ValueError("language service returned invalid JSON") from error
        if not isinstance(payload, Mapping):
            raise ValueError("language service response must be an object")
        if payload.get("protocol") not in {None, "syntavra-language-service"}:
            raise ValueError("language service protocol mismatch")
        nodes = self._nodes(payload.get("nodes", []), path=path, evidence_ref=evidence_ref)
        node_ids = {str(node["node_id"]) for node in nodes}
        edges = self._edges(payload.get("edges", []), node_ids=node_ids, evidence_ref=evidence_ref)
        diagnostics_raw = payload.get("diagnostics") or []
        if not isinstance(diagnostics_raw, list) or len(diagnostics_raw) > 10_000:
            raise ValueError("language service diagnostics are invalid")
        diagnostics = tuple(str(item)[:4096] for item in diagnostics_raw)
        level = "semantic" if "semantic" in self.manifest.capabilities else "syntax"
        return LanguageParseResult(
            nodes=nodes,
            edges=edges,
            capability_level=level,
            evidence_source=f"language-service:{self.manifest.service_id}:{self.manifest.manifest_hash}",
            diagnostics=diagnostics,
        )


class LanguageServiceRegistry:
    """Data-only manifest discovery. Discovery never executes analyzer code."""

    def __init__(self) -> None:
        self.manifests: dict[str, LanguageServiceManifest] = {}
        self.diagnostics: list[str] = []

    def register(self, manifest: LanguageServiceManifest) -> None:
        current = self.manifests.get(manifest.service_id)
        if current is not None and current.manifest_hash != manifest.manifest_hash:
            raise ValueError(f"conflicting language service id: {manifest.service_id}")
        self.manifests[manifest.service_id] = manifest

    def discover(self, root: Path | None = None) -> None:
        directories: list[Path] = []
        if root is not None:
            directories.append(root / ".syntavra" / "language-services")
        configured = os.environ.get("SYNTAVRA_LANGUAGE_SERVICE_PATH", "")
        directories.extend(Path(item).expanduser() for item in configured.split(os.pathsep) if item.strip())
        directories.append(Path.home() / ".syntavra" / "language-services")
        seen: set[Path] = set()
        for directory in directories:
            try:
                resolved = directory.resolve(strict=False)
            except OSError:
                continue
            if resolved in seen or not resolved.is_dir():
                continue
            seen.add(resolved)
            for path in sorted(resolved.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    values: Sequence[Any] = payload.get("services", ()) if isinstance(payload, Mapping) and "services" in payload else (payload,)
                    for value in values:
                        if not isinstance(value, Mapping):
                            raise ValueError("language service manifest entry must be an object")
                        self.register(
                            LanguageServiceManifest.from_mapping(
                                value,
                                source=f"manifest:{path}",
                                base_directory=path.parent,
                            )
                        )
                except Exception as error:
                    self.diagnostics.append(f"manifest:{path}: {type(error).__name__}: {error}")

    def for_language(self, language_id: str) -> tuple[LanguageServiceManifest, ...]:
        normalized = language_id.casefold()
        return tuple(
            sorted(
                (manifest for manifest in self.manifests.values() if normalized in manifest.language_ids),
                key=lambda item: ("semantic" not in item.capabilities, item.service_id),
            )
        )

    def adapters(
        self,
        *,
        workspace: Path,
        state_root: Path,
        broker: NativeSandboxBroker | None = None,
    ) -> tuple[SandboxedLanguageServiceAdapter, ...]:
        return tuple(
            SandboxedLanguageServiceAdapter(
                manifest,
                workspace=workspace,
                state_root=state_root,
                broker=broker,
            )
            for manifest in sorted(self.manifests.values(), key=lambda item: item.service_id)
        )

    def inventory(self) -> dict[str, Any]:
        return {
            "services": len(self.manifests),
            "service_ids": sorted(self.manifests),
            "languages": sorted({language for manifest in self.manifests.values() for language in manifest.language_ids}),
            "execution_authorized": os.environ.get("SYNTAVRA_ALLOW_LANGUAGE_SERVICES", "").casefold() in {"1", "true", "yes"},
            "diagnostics": list(self.diagnostics),
        }


__all__ = [
    "LanguageServiceManifest",
    "LanguageServiceRegistry",
    "SandboxedLanguageServiceAdapter",
]
