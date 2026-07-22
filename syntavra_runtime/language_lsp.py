from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .execution_sandbox import NativeSandboxBroker, SandboxPolicy
from .language_platform import LanguageParseResult


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,127}$")


@dataclass(frozen=True)
class LSPServiceManifest:
    service_id: str
    language_ids: tuple[str, ...]
    server_command: tuple[str, ...]
    server_executable_sha256: str
    initialization_options: Mapping[str, Any]
    timeout_seconds: float = 30.0
    max_message_bytes: int = 16 * 1024 * 1024
    max_output_bytes: int = 16 * 1024 * 1024
    strict_native: bool = True
    source: str = ""

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        source: str,
        base_directory: Path | None = None,
    ) -> "LSPServiceManifest":
        service_id = str(value.get("id") or value.get("service_id") or "").strip().casefold()
        if not _ID_RE.fullmatch(service_id):
            raise ValueError("LSP service id is invalid")
        raw_languages = value.get("languages") or value.get("language_ids") or ()
        if isinstance(raw_languages, str):
            raw_languages = [raw_languages]
        language_ids = tuple(dict.fromkeys(str(item).strip().casefold() for item in raw_languages if str(item).strip()))
        if not language_ids or any(not _ID_RE.fullmatch(item) for item in language_ids):
            raise ValueError("LSP service requires valid language ids")
        raw_command = value.get("server_command") or value.get("command") or ()
        if isinstance(raw_command, str):
            raise ValueError("LSP server command must be an argv sequence")
        command = tuple(str(item) for item in raw_command)
        if not command or any(not item or "\x00" in item for item in command):
            raise ValueError("LSP server command is invalid")
        executable = Path(command[0]).expanduser()
        if not executable.is_absolute() and base_directory is not None:
            local = (base_directory / executable).resolve(strict=False)
            if local.exists():
                command = (str(local), *command[1:])
        digest = str(value.get("server_executable_sha256") or value.get("executable_sha256") or "").strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("LSP server executable hash is mandatory")
        initialization = value.get("initialization_options") or {}
        if not isinstance(initialization, Mapping):
            raise ValueError("LSP initialization_options must be an object")
        serialized_options = json.dumps(initialization, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(serialized_options.encode("utf-8")) > 1024 * 1024:
            raise ValueError("LSP initialization_options exceed limit")
        timeout = float(value.get("timeout_seconds", 30.0))
        max_message = int(value.get("max_message_bytes", 16 * 1024 * 1024))
        max_output = int(value.get("max_output_bytes", 16 * 1024 * 1024))
        if not 0.1 <= timeout <= 600:
            raise ValueError("LSP timeout is out of bounds")
        if not 1024 <= max_message <= 128 * 1024 * 1024:
            raise ValueError("LSP message limit is out of bounds")
        if not 1024 <= max_output <= 128 * 1024 * 1024:
            raise ValueError("LSP output limit is out of bounds")
        return cls(
            service_id=service_id,
            language_ids=language_ids,
            server_command=command,
            server_executable_sha256=digest,
            initialization_options=dict(initialization),
            timeout_seconds=timeout,
            max_message_bytes=max_message,
            max_output_bytes=max_output,
            strict_native=bool(value.get("strict_native", True)),
            source=source,
        )

    @property
    def manifest_hash(self) -> str:
        payload = {
            "service_id": self.service_id,
            "language_ids": self.language_ids,
            "server_command": self.server_command,
            "server_executable_sha256": self.server_executable_sha256,
            "initialization_options": self.initialization_options,
            "timeout_seconds": self.timeout_seconds,
            "max_message_bytes": self.max_message_bytes,
            "max_output_bytes": self.max_output_bytes,
            "strict_native": self.strict_native,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class GenericLSPAdapter:
    language_ids: tuple[str, ...]
    capabilities = (
        "syntax",
        "semantic",
        "definitions",
        "types",
        "document-symbols",
    )

    def __init__(
        self,
        manifest: LSPServiceManifest,
        *,
        workspace: Path,
        state_root: Path,
        broker: NativeSandboxBroker | None = None,
    ) -> None:
        self.manifest = manifest
        self.language_ids = manifest.language_ids
        self.workspace = workspace.resolve(strict=True)
        self.state_root = state_root.resolve(strict=False)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.broker = broker or NativeSandboxBroker(self.state_root)
        self.server_executable = self._verify_server_executable()

    def _verify_server_executable(self) -> Path:
        raw = self.manifest.server_command[0]
        path = Path(raw).expanduser()
        if path.is_absolute():
            resolved = path.resolve(strict=True)
        else:
            located = shutil.which(raw)
            if not located:
                raise FileNotFoundError(f"LSP server executable not found: {raw}")
            resolved = Path(located).resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("LSP server executable must be a regular file")
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if not hmac.compare_digest(actual, self.manifest.server_executable_sha256):
            raise PermissionError("LSP server executable hash mismatch")
        return resolved

    @staticmethod
    def _validate_nodes(raw: Any, *, evidence_ref: str, manifest: LSPServiceManifest) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw, list) or len(raw) > 500_000:
            raise ValueError("LSP node result is invalid or too large")
        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("LSP node must be an object")
            node_id = str(item.get("node_id") or "")
            name = str(item.get("name") or "").strip()
            kind = str(item.get("kind") or "symbol").strip().casefold()
            start = max(1, int(item.get("start_line", 1)))
            end = max(start, int(item.get("end_line", start)))
            if not node_id or node_id in seen or len(node_id) > 8192 or not name or len(name) > 4096:
                raise ValueError("LSP node identity is invalid")
            seen.add(node_id)
            metadata = item.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            metadata.update(
                {
                    "source": f"lsp:{manifest.service_id}",
                    "lsp_manifest_sha256": manifest.manifest_hash,
                    "lsp_server_sha256": manifest.server_executable_sha256,
                    "exact_semantic": True,
                    "capability_level": "semantic",
                }
            )
            nodes.append(
                {
                    "node_id": node_id,
                    "kind": kind,
                    "name": name,
                    "qualified_name": str(item.get("qualified_name") or name),
                    "start_line": start,
                    "end_line": end,
                    "evidence_ref": evidence_ref,
                    "metadata": metadata,
                }
            )
        return tuple(nodes)

    @staticmethod
    def _validate_edges(raw: Any, *, node_ids: set[str], evidence_ref: str, manifest: LSPServiceManifest) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw, list) or len(raw) > 2_000_000:
            raise ValueError("LSP edge result is invalid or too large")
        edges: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("LSP edge must be an object")
            source = str(item.get("source") or "")
            target = str(item.get("target") or "")
            if source not in node_ids or target not in node_ids:
                raise ValueError("LSP edge endpoint is unknown")
            edge_type = str(item.get("edge_type") or "contains").casefold()
            if not edge_type or len(edge_type) > 128:
                raise ValueError("LSP edge type is invalid")
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "edge_type": edge_type,
                    "confidence": 1.0,
                    "evidence_ref": evidence_ref,
                    "metadata": {
                        "source": f"lsp:{manifest.service_id}",
                        "lsp_manifest_sha256": manifest.manifest_hash,
                        "exact_semantic": True,
                    },
                }
            )
        return tuple(edges)

    def parse(self, *, path: str, text: str, evidence_ref: str) -> LanguageParseResult:
        if os.environ.get("SYNTAVRA_ALLOW_LSP_SERVICES", "").casefold() not in {"1", "true", "yes"}:
            raise PermissionError("LSP execution requires explicit SYNTAVRA_ALLOW_LSP_SERVICES authorization")
        runtime_directory = self.workspace / ".syntavra" / "lsp-runtime" / self.manifest.service_id
        runtime_directory.mkdir(parents=True, exist_ok=True)
        request = {
            "protocol": "syntavra-lsp-bridge",
            "workspace": str(self.workspace),
            "path": path,
            "language_id": self.language_ids[0],
            "text": text,
            "server_command": [str(self.server_executable), *self.manifest.server_command[1:]],
            "server_executable_sha256": self.manifest.server_executable_sha256,
            "initialization_options": self.manifest.initialization_options,
            "timeout_seconds": self.manifest.timeout_seconds,
            "max_message_bytes": self.manifest.max_message_bytes,
        }
        receipt = self.broker.run(
            (sys.executable, "-m", "syntavra_runtime.lsp_worker"),
            policy=SandboxPolicy(
                workspace=self.workspace,
                writable_paths=(runtime_directory,),
                network_hosts=(),
                timeout_seconds=self.manifest.timeout_seconds + 5,
                memory_bytes=1024 * 1024 * 1024,
                cpu_seconds=max(1, int(self.manifest.timeout_seconds + 2)),
                allow_child_processes=True,
                strict_native=self.manifest.strict_native,
                max_stdout_bytes=self.manifest.max_output_bytes,
                max_stderr_bytes=2 * 1024 * 1024,
            ),
            input_bytes=json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        if not receipt.ok:
            raise RuntimeError(
                f"LSP bridge failed: exit={receipt.exit_code} timeout={receipt.timed_out} output_limit={receipt.output_limit_exceeded}"
            )
        try:
            payload = json.loads(receipt.stdout)
        except json.JSONDecodeError as error:
            raise ValueError("LSP bridge returned invalid JSON") from error
        if not isinstance(payload, Mapping) or payload.get("protocol") != "syntavra-lsp-bridge":
            raise ValueError("LSP bridge protocol mismatch")
        if payload.get("server_stderr_truncated"):
            raise ValueError("LSP server stderr exceeded limit")
        nodes = self._validate_nodes(payload.get("nodes", []), evidence_ref=evidence_ref, manifest=self.manifest)
        node_ids = {str(item["node_id"]) for item in nodes}
        edges = self._validate_edges(payload.get("edges", []), node_ids=node_ids, evidence_ref=evidence_ref, manifest=self.manifest)
        diagnostics_raw = payload.get("diagnostics") or []
        if not isinstance(diagnostics_raw, list) or len(diagnostics_raw) > 10_000:
            raise ValueError("LSP diagnostics are invalid")
        return LanguageParseResult(
            nodes=nodes,
            edges=edges,
            capability_level="semantic",
            evidence_source=f"lsp:{self.manifest.service_id}:{self.manifest.manifest_hash}",
            diagnostics=tuple(str(item)[:4096] for item in diagnostics_raw),
        )


class LSPServiceRegistry:
    def __init__(self) -> None:
        self.manifests: dict[str, LSPServiceManifest] = {}
        self.diagnostics: list[str] = []

    def register(self, manifest: LSPServiceManifest) -> None:
        current = self.manifests.get(manifest.service_id)
        if current is not None and current.manifest_hash != manifest.manifest_hash:
            raise ValueError(f"conflicting LSP service id: {manifest.service_id}")
        self.manifests[manifest.service_id] = manifest

    def discover(self, root: Path | None = None) -> None:
        directories: list[Path] = []
        if root is not None:
            directories.append(root / ".syntavra" / "lsp-services")
        configured = os.environ.get("SYNTAVRA_LSP_SERVICE_PATH", "")
        directories.extend(Path(item).expanduser() for item in configured.split(os.pathsep) if item.strip())
        directories.append(Path.home() / ".syntavra" / "lsp-services")
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
                            raise ValueError("LSP manifest entry must be an object")
                        self.register(
                            LSPServiceManifest.from_mapping(
                                value,
                                source=f"manifest:{path}",
                                base_directory=path.parent,
                            )
                        )
                except Exception as error:
                    self.diagnostics.append(f"manifest:{path}: {type(error).__name__}: {error}")

    def inventory(self) -> dict[str, Any]:
        return {
            "services": len(self.manifests),
            "service_ids": sorted(self.manifests),
            "languages": sorted({language for manifest in self.manifests.values() for language in manifest.language_ids}),
            "execution_authorized": os.environ.get("SYNTAVRA_ALLOW_LSP_SERVICES", "").casefold() in {"1", "true", "yes"},
            "diagnostics": list(self.diagnostics),
        }


__all__ = ["GenericLSPAdapter", "LSPServiceManifest", "LSPServiceRegistry"]
