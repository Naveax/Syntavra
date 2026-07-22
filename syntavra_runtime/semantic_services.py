from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .runtime_evidence import RuntimeEvidenceGraph


@dataclass(frozen=True)
class LanguageServiceSpec:
    language: str
    suffixes: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]
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


DEFAULT_LANGUAGE_SERVICES: tuple[LanguageServiceSpec, ...] = (
    LanguageServiceSpec("python", (".py",), (("pyright-langserver", "--stdio"), ("basedpyright-langserver", "--stdio"), ("pylsp",))),
    LanguageServiceSpec("typescript", (".ts", ".tsx", ".js", ".jsx"), (("typescript-language-server", "--stdio"),)),
    LanguageServiceSpec("rust", (".rs",), (("rust-analyzer",),)),
    LanguageServiceSpec("go", (".go",), (("gopls",),)),
    LanguageServiceSpec("java", (".java",), (("jdtls",),)),
    LanguageServiceSpec("csharp", (".cs",), (("csharp-ls",), ("omnisharp", "-lsp"))),
    LanguageServiceSpec("cpp", (".c", ".h", ".cc", ".cpp", ".hpp"), (("clangd",),)),
    LanguageServiceSpec("kotlin", (".kt", ".kts"), (("kotlin-language-server",),)),
    LanguageServiceSpec("swift", (".swift",), (("sourcekit-lsp",),)),
    LanguageServiceSpec("lua", (".lua", ".luau"), (("lua-language-server",),)),
    LanguageServiceSpec("ruby", (".rb",), (("ruby-lsp",),)),
    LanguageServiceSpec("php", (".php",), (("intelephense", "--stdio"),)),
    LanguageServiceSpec("dart", (".dart",), (("dart", "language-server", "--protocol=lsp"),)),
    LanguageServiceSpec("shell", (".sh", ".bash", ".zsh"), (("bash-language-server", "start"),)),
    LanguageServiceSpec("powershell", (".ps1", ".psm1"), (("pwsh", "-NoLogo", "-NoProfile", "-Command", "Start-EditorServices"),)),
    LanguageServiceSpec("sql", (".sql",), (("sqls",),)),
)


class LanguageServiceRegistry:
    def __init__(self, specs: Sequence[LanguageServiceSpec] = DEFAULT_LANGUAGE_SERVICES):
        self.specs = tuple(specs)

    @staticmethod
    def _available(command: tuple[str, ...]) -> bool:
        return bool(command and shutil.which(command[0]))

    def detect(self) -> list[LanguageServiceStatus]:
        statuses: list[LanguageServiceStatus] = []
        for spec in self.specs:
            selected = next((command for command in spec.commands if self._available(command)), None)
            statuses.append(
                LanguageServiceStatus(
                    language=spec.language,
                    available=selected is not None,
                    command=selected,
                    features=spec.semantic_features,
                    detail="available" if selected else "language server executable not found",
                )
            )
        return statuses

    def status(self) -> dict[str, Any]:
        rows = self.detect()
        return {
            "ok": True,
            "languages": [asdict(row) for row in rows],
            "available": sum(1 for row in rows if row.available),
            "declared": len(rows),
            "claim_boundary": "declared support is not live certification without a successful semantic receipt",
        }

    def for_path(self, path: Path) -> LanguageServiceStatus | None:
        suffix = path.suffix.casefold()
        return next((row for row in self.detect() if suffix in next(spec.suffixes for spec in self.specs if spec.language == row.language)), None)


class LSPProtocolError(RuntimeError):
    pass


class LSPClient:
    """Small synchronous JSON-RPC/LSP client used for conformance and indexing.

    It deliberately exposes raw LSP methods rather than pretending every server
    has identical behavior. Calls are bounded by timeout and the child process is
    always terminated on close.
    """

    def __init__(self, command: Sequence[str], root: Path, *, timeout: float = 15.0):
        if not command:
            raise ValueError("language server command is required")
        self.command = tuple(command)
        self.root = root.resolve(strict=True)
        self.timeout = max(0.1, float(timeout))
        self.process: subprocess.Popen[bytes] | None = None
        self._next_id = 0
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader: threading.Thread | None = None

    def start(self) -> dict[str, Any]:
        if self.process is not None:
            raise RuntimeError("language server already started")
        env = {key: value for key, value in os.environ.items() if not any(token in key.upper() for token in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))}
        self.process = subprocess.Popen(
            self.command,
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._reader = threading.Thread(target=self._read_loop, name="syntavra-lsp-reader", daemon=True)
        self._reader.start()
        result = self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.root.as_uri(),
                "capabilities": {
                    "workspace": {"workspaceFolders": True, "symbol": {"dynamicRegistration": False}},
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "implementation": {"dynamicRegistration": False},
                        "typeDefinition": {"dynamicRegistration": False},
                        "callHierarchy": {"dynamicRegistration": False},
                    },
                },
                "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
            },
        )
        self.notify("initialized", {})
        return result if isinstance(result, dict) else {"result": result}

    def _read_exact(self, count: int) -> bytes:
        assert self.process and self.process.stdout
        chunks = bytearray()
        while len(chunks) < count:
            data = self.process.stdout.read(count - len(chunks))
            if not data:
                raise EOFError("language server closed stdout")
            chunks.extend(data)
        return bytes(chunks)

    def _read_loop(self) -> None:
        try:
            assert self.process and self.process.stdout
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        return
                    if line in {b"\r\n", b"\n"}:
                        break
                    name, _, value = line.decode("ascii", errors="replace").partition(":")
                    headers[name.strip().casefold()] = value.strip()
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                message = json.loads(self._read_exact(length).decode("utf-8"))
                (self._responses if "id" in message else self._notifications).put(message)
        except Exception as error:  # reader errors are surfaced to pending callers
            self._responses.put({"_reader_error": f"{type(error).__name__}: {error}"})

    def _send(self, message: Mapping[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("language server is not running")
        body = json.dumps(dict(message), ensure_ascii=False, separators=(",", ":")).encode()
        self.process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.process.stdin.flush()

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)})
        deadline = time.monotonic() + self.timeout
        deferred: list[dict[str, Any]] = []
        try:
            while time.monotonic() < deadline:
                try:
                    message = self._responses.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
                except queue.Empty:
                    if self.process and self.process.poll() is not None:
                        raise LSPProtocolError(f"language server exited with {self.process.returncode}")
                    continue
                if "_reader_error" in message:
                    raise LSPProtocolError(str(message["_reader_error"]))
                if message.get("id") != request_id:
                    deferred.append(message)
                    continue
                if "error" in message:
                    raise LSPProtocolError(json.dumps(message["error"], ensure_ascii=False))
                return message.get("result")
            raise TimeoutError(f"LSP request timed out: {method}")
        finally:
            for message in deferred:
                self._responses.put(message)

    def workspace_symbols(self, query: str) -> list[dict[str, Any]]:
        result = self.request("workspace/symbol", {"query": query})
        return list(result or []) if isinstance(result, list) else []

    def close(self) -> None:
        if not self.process:
            return
        try:
            self.request("shutdown", {})
            self.notify("exit", {})
        except Exception:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        self.process = None

    def __enter__(self) -> "LSPClient":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class SemanticIndexImporter:
    """Imports line-oriented LSIF and JSON SCIP exports into runtime evidence."""

    def __init__(self, evidence: RuntimeEvidenceGraph):
        self.evidence = evidence

    def import_lsif(self, path: Path, *, repository_commit: str = "unknown") -> dict[str, Any]:
        vertices: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            identifier = str(row.get("id", ""))
            if row.get("type") == "vertex":
                vertices[identifier] = row
            elif row.get("type") == "edge":
                edges.append(row)
        node_ids: dict[str, str] = {}
        for identifier, vertex in vertices.items():
            label = str(vertex.get("label", "vertex"))
            display = str(vertex.get("name") or vertex.get("uri") or identifier)
            node = self.evidence.put_node(
                kind=f"lsif:{label}",
                label=display,
                source=str(path),
                repository_commit=repository_commit,
                metadata=vertex,
            )
            node_ids[identifier] = node.node_id
        imported_edges = 0
        for edge in edges:
            source = node_ids.get(str(edge.get("outV", "")))
            targets = edge.get("inVs") or [edge.get("inV")]
            if not source:
                continue
            for target_raw in targets:
                target = node_ids.get(str(target_raw))
                if not target:
                    continue
                self.evidence.put_edge(
                    source,
                    target,
                    f"LSIF_{str(edge.get('label', 'EDGE')).upper()}",
                    repository_commit=repository_commit,
                    metadata=edge,
                )
                imported_edges += 1
        return {"ok": True, "format": "lsif", "nodes": len(node_ids), "edges": imported_edges}

    def import_scip_json(self, path: Path, *, repository_commit: str = "unknown") -> dict[str, Any]:
        document = json.loads(path.read_text(encoding="utf-8"))
        documents = document.get("documents", []) if isinstance(document, Mapping) else []
        symbols: dict[str, str] = {}
        occurrences: list[tuple[str, Mapping[str, Any]]] = []
        for item in documents:
            if not isinstance(item, Mapping):
                continue
            relative = str(item.get("relative_path", item.get("relativePath", "")))
            for info in item.get("symbols", []):
                if not isinstance(info, Mapping):
                    continue
                symbol = str(info.get("symbol", ""))
                if not symbol:
                    continue
                node = self.evidence.put_node(
                    kind="scip:symbol",
                    label=symbol,
                    source=relative,
                    repository_commit=repository_commit,
                    metadata=info,
                )
                symbols[symbol] = node.node_id
            for occurrence in item.get("occurrences", []):
                if isinstance(occurrence, Mapping):
                    occurrences.append((relative, occurrence))
        imported_edges = 0
        for relative, occurrence in occurrences:
            symbol = str(occurrence.get("symbol", ""))
            target = symbols.get(symbol)
            if not target:
                continue
            file_node = self.evidence.put_node(
                kind="file",
                label=relative,
                source="scip",
                repository_commit=repository_commit,
            )
            role = int(occurrence.get("symbol_roles", occurrence.get("symbolRoles", 0)) or 0)
            relation = "SCIP_DEFINES" if role & 1 else "SCIP_REFERENCES"
            self.evidence.put_edge(
                file_node.node_id,
                target,
                relation,
                repository_commit=repository_commit,
                metadata=occurrence,
            )
            imported_edges += 1
        return {"ok": True, "format": "scip-json", "symbols": len(symbols), "occurrences": imported_edges}


__all__ = [
    "DEFAULT_LANGUAGE_SERVICES",
    "LSPClient",
    "LSPProtocolError",
    "LanguageServiceRegistry",
    "LanguageServiceSpec",
    "LanguageServiceStatus",
    "SemanticIndexImporter",
]
