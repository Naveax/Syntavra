from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence


_SYMBOL_KINDS = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum-member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type-parameter",
}


def _safe_argv(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError("LSP server command must be an argv array")
    command = tuple(str(item) for item in value)
    if not command or any(not item or "\x00" in item for item in command):
        raise ValueError("LSP server command is invalid")
    return command


def _resolve_executable(command: tuple[str, ...], expected_sha256: str) -> tuple[str, ...]:
    executable = Path(command[0]).expanduser()
    if executable.is_absolute():
        resolved = executable.resolve(strict=True)
    else:
        located = shutil.which(command[0])
        if not located:
            raise FileNotFoundError(f"LSP server executable not found: {command[0]}")
        resolved = Path(located).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("LSP server executable must be a regular file")
    actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if not hmac.compare_digest(actual, expected_sha256.casefold()):
        raise PermissionError("LSP server executable hash mismatch")
    return (str(resolved), *command[1:])


class _StderrDrain:
    def __init__(self, limit: int = 1024 * 1024) -> None:
        self.limit = limit
        self.data = bytearray()
        self.exceeded = False

    def run(self, stream: BinaryIO) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                remaining = self.limit - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.exceeded = True
        finally:
            stream.close()


class JsonRpcStdio:
    def __init__(self, process: subprocess.Popen[bytes], *, max_message_bytes: int, timeout_seconds: float) -> None:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("LSP stdio pipes are unavailable")
        self.process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.max_message_bytes = max_message_bytes
        self.timeout_seconds = timeout_seconds
        self.messages: queue.Queue[Any] = queue.Queue()
        self.write_lock = threading.Lock()
        self.next_id = 1
        self.reader_error: BaseException | None = None
        self.reader = threading.Thread(target=self._read_loop, daemon=True, name="syntavra-lsp-reader")
        self.reader.start()

    def _read_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self.stdout.read(length - len(chunks))
            if not chunk:
                raise EOFError("LSP server closed stdout during a message")
            chunks.extend(chunk)
        return bytes(chunks)

    def _read_loop(self) -> None:
        try:
            while True:
                headers: dict[str, str] = {}
                header_bytes = 0
                while True:
                    line = self.stdout.readline(16 * 1024 + 1)
                    if not line:
                        return
                    header_bytes += len(line)
                    if header_bytes > 16 * 1024:
                        raise ValueError("LSP header block exceeds limit")
                    if line in {b"\r\n", b"\n"}:
                        break
                    decoded = line.decode("ascii", errors="strict").strip()
                    if ":" not in decoded:
                        raise ValueError("malformed LSP header")
                    name, value = decoded.split(":", 1)
                    headers[name.strip().casefold()] = value.strip()
                if "content-length" not in headers:
                    raise ValueError("LSP message is missing Content-Length")
                length = int(headers["content-length"])
                if length < 0 or length > self.max_message_bytes:
                    raise ValueError("LSP message exceeds configured limit")
                body = self._read_exact(length)
                message = json.loads(body)
                self.messages.put(message)
        except BaseException as error:
            self.reader_error = error
            self.messages.put({"__syntavra_reader_error__": type(error).__name__})

    def send(self, message: Mapping[str, Any]) -> None:
        body = json.dumps(dict(message), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > self.max_message_bytes:
            raise ValueError("outbound LSP message exceeds configured limit")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self.write_lock:
            self.stdin.write(header)
            self.stdin.write(body)
            self.stdin.flush()

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": dict(params or {})})

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        request_id = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})})
        deadline = time.monotonic() + self.timeout_seconds
        deferred: list[Any] = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"LSP request timed out: {method}")
                try:
                    message = self.messages.get(timeout=remaining)
                except queue.Empty as error:
                    raise TimeoutError(f"LSP request timed out: {method}") from error
                if isinstance(message, Mapping) and "__syntavra_reader_error__" in message:
                    raise RuntimeError("LSP reader failed") from self.reader_error
                if isinstance(message, Mapping) and message.get("id") == request_id and "method" not in message:
                    if "error" in message:
                        raise RuntimeError(f"LSP error response for {method}: {message['error']}")
                    return message.get("result")
                if isinstance(message, Mapping) and "id" in message and "method" in message:
                    self.send(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {"code": -32601, "message": "Client method not implemented"},
                        }
                    )
                else:
                    deferred.append(message)
        finally:
            for message in deferred:
                self.messages.put(message)


def _position(value: Any) -> tuple[int, int]:
    if not isinstance(value, Mapping):
        return 0, 0
    return max(0, int(value.get("line", 0))), max(0, int(value.get("character", 0)))


def _range(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, Mapping):
        return 0, 0, 0, 0
    start_line, start_character = _position(value.get("start"))
    end_line, end_character = _position(value.get("end"))
    return start_line, start_character, max(start_line, end_line), end_character


def _symbol_node(
    *,
    path: str,
    name: str,
    kind_value: Any,
    range_value: Any,
    container: str,
    index: int,
) -> dict[str, Any]:
    start_line, start_character, end_line, end_character = _range(range_value)
    kind = _SYMBOL_KINDS.get(int(kind_value or 0), "symbol")
    identity = f"{path}\0{kind}\0{name}\0{start_line}\0{start_character}\0{index}"
    node_id = "lsp:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    qualified = f"{container}.{name}" if container else f"{path}:{name}"
    return {
        "node_id": node_id,
        "kind": kind,
        "name": name,
        "qualified_name": qualified,
        "start_line": start_line + 1,
        "end_line": end_line + 1,
        "metadata": {
            "lsp_start_character": start_character,
            "lsp_end_character": end_character,
        },
    }


def _flatten_document_symbols(symbols: Any, *, path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if symbols is None:
        return [], []
    if not isinstance(symbols, list):
        raise ValueError("LSP documentSymbol result must be an array or null")
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def visit(raw: Any, parent_id: str | None = None, container: str = "") -> None:
        if not isinstance(raw, Mapping):
            raise ValueError("LSP symbol entry must be an object")
        name = str(raw.get("name") or "").strip()
        if not name or len(name) > 4096:
            raise ValueError("LSP symbol name is invalid")
        location = raw.get("location") if isinstance(raw.get("location"), Mapping) else None
        range_value = raw.get("range") or (location or {}).get("range")
        node = _symbol_node(
            path=path,
            name=name,
            kind_value=raw.get("kind"),
            range_value=range_value,
            container=str(raw.get("containerName") or container),
            index=len(nodes),
        )
        nodes.append(node)
        if parent_id is not None:
            edges.append(
                {
                    "source": parent_id,
                    "target": node["node_id"],
                    "edge_type": "contains",
                    "confidence": 1.0,
                }
            )
        children = raw.get("children") or []
        if not isinstance(children, list):
            raise ValueError("LSP symbol children must be an array")
        for child in children:
            visit(child, node["node_id"], node["qualified_name"])

    for symbol in symbols:
        visit(symbol)
    return nodes, edges


def analyze(request: Mapping[str, Any]) -> dict[str, Any]:
    workspace = Path(os.environ.get("SYNTAVRA_WORKSPACE") or request.get("workspace") or "").resolve(strict=True)
    path = str(request.get("path") or "")
    source_path = (workspace / path).resolve(strict=False)
    try:
        source_path.relative_to(workspace)
    except ValueError as error:
        raise PermissionError("LSP document path escapes workspace") from error
    language_id = str(request.get("language_id") or "plaintext")
    text = str(request.get("text") or "")
    server_command = _resolve_executable(
        _safe_argv(request.get("server_command")),
        str(request.get("server_executable_sha256") or ""),
    )
    timeout = float(request.get("timeout_seconds", 30.0))
    max_message = int(request.get("max_message_bytes", 16 * 1024 * 1024))
    if not 0.1 <= timeout <= 600 or not 1024 <= max_message <= 128 * 1024 * 1024:
        raise ValueError("LSP timeout or message limit is invalid")

    process = subprocess.Popen(
        server_command,
        cwd=workspace,
        env=dict(os.environ),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    if process.stderr is None:
        process.kill()
        raise RuntimeError("LSP stderr pipe is unavailable")
    stderr = _StderrDrain()
    stderr_thread = threading.Thread(target=stderr.run, args=(process.stderr,), daemon=True, name="syntavra-lsp-stderr")
    stderr_thread.start()
    transport = JsonRpcStdio(process, max_message_bytes=max_message, timeout_seconds=timeout)
    uri = source_path.as_uri()
    diagnostics: list[str] = []
    try:
        initialize_result = transport.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": workspace.as_uri(),
                "workspaceFolders": [{"uri": workspace.as_uri(), "name": workspace.name}],
                "capabilities": {
                    "textDocument": {
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "implementation": {"dynamicRegistration": False},
                    },
                    "workspace": {"workspaceFolders": True},
                },
                "initializationOptions": request.get("initialization_options") or {},
                "clientInfo": {"name": "Syntavra", "version": "0.0.1"},
            },
        )
        transport.notify("initialized", {})
        transport.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )
        symbols = transport.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        nodes, edges = _flatten_document_symbols(symbols, path=path)
        capabilities = initialize_result.get("capabilities", {}) if isinstance(initialize_result, Mapping) else {}
        diagnostics.append("lsp-server-capabilities-sha256:" + hashlib.sha256(json.dumps(capabilities, sort_keys=True, default=str).encode("utf-8")).hexdigest())
        return {
            "protocol": "syntavra-lsp-bridge",
            "nodes": nodes,
            "edges": edges,
            "diagnostics": diagnostics,
            "server_stderr_truncated": stderr.exceeded,
        }
    finally:
        try:
            transport.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        except Exception:
            pass
        try:
            transport.request("shutdown", {})
        except Exception:
            pass
        try:
            transport.notify("exit", {})
        except Exception:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stderr_thread.join(timeout=2)
        if stderr_thread.is_alive():
            process.kill()


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("LSP bridge request exceeds limit")
        payload = json.loads(raw)
        if not isinstance(payload, Mapping):
            raise ValueError("LSP bridge request must be an object")
        result = analyze(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as error:
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
