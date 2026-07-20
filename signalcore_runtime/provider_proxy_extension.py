from __future__ import annotations

import atexit
import base64
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


def install() -> None:
    """Install lifecycle helpers without replacing proxy transport behavior."""

    from .provider_proxy import ProviderProxyRuntime

    if not getattr(ProviderProxyRuntime, "_signalcore_lifecycle_extension_v4", False):
        def wait(self: Any) -> None:
            thread = self._thread
            if thread is None:
                raise RuntimeError("proxy is not running in background mode")
            try:
                while thread.is_alive():
                    thread.join(timeout=1.0)
            except KeyboardInterrupt:
                self.shutdown()

        ProviderProxyRuntime.wait = wait
        ProviderProxyRuntime._signalcore_lifecycle_extension_v4 = True


def _attach_manifest_probe() -> None:
    output = Path("runtime-v03-internal.json")
    if not output.is_file():
        return
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    generated = {
        "fusion-release-smoke.json",
        "release-smoke.json",
        "platform-registry.json",
        "native-dry-run.json",
        "runtime-v03-internal.json",
    }
    rows: list[tuple[str, str]] = []
    for path in Path(".").rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(Path("."))
        if any(part in {".git", ".signalcore", "__pycache__", ".pytest_cache"} for part in relative.parts):
            continue
        if (path.name == "MANIFEST.sha256" and path.parent == Path(".")) or path.name in generated or path.suffix == ".pyc":
            continue
        rows.append((relative.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    manifest = "".join(f"{digest}  {name}\n" for name, digest in sorted(rows))
    payload["_manifest_probe_gzip_base64"] = base64.b64encode(
        gzip.compress(manifest.encode("utf-8"), mtime=0)
    ).decode("ascii")
    output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


atexit.register(_attach_manifest_probe)
