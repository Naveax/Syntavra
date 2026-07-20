from __future__ import annotations

import atexit
import base64
import gzip
import hashlib
import json
from pathlib import Path


def _attach_manifest() -> None:
    output = Path("runtime-v03-internal.json")
    if not output.is_file():
        return
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    root = Path(".")
    generated = {
        "fusion-release-smoke.json",
        "release-smoke.json",
        "platform-registry.json",
        "native-dry-run.json",
        "runtime-v03-internal.json",
    }
    rows: list[tuple[str, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in {".git", ".signalcore", "__pycache__", ".pytest_cache"} for part in relative.parts):
            continue
        if (path.name == "MANIFEST.sha256" and path.parent == root) or path.name in generated or path.suffix == ".pyc":
            continue
        rows.append((relative.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    text = "".join(f"{digest}  {name}\n" for name, digest in sorted(rows))
    payload["_manifest_probe_gzip_base64"] = base64.b64encode(gzip.compress(text.encode("utf-8"), mtime=0)).decode("ascii")
    output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


atexit.register(_attach_manifest)
