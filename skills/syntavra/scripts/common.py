#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC_VERSION = "0.0.1"
WORD_RE = re.compile(r"[\w.+#/@:-]+", re.UNICODE)
TRANSLIT = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "ß": "ss", "æ": "ae", "œ": "oe", "ł": "l", "Ł": "l",
})
SECRET_PATTERNS = [
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|authorization)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
]


@lru_cache(maxsize=65536)
def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text)).translate(TRANSLIT).casefold()
    value = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    value = re.sub(r"[^\w.+#/@:-]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=65536)
def normalized_tokens(text: str) -> tuple[str, ...]:
    return tuple(WORD_RE.findall(normalize(text)))


def tokens(text: str) -> list[str]:
    return list(normalized_tokens(text))


def estimate_tokens(text: str) -> int:
    # Conservative multilingual approximation; optional tiktoken is never required.
    try:
        if os.environ.get("SYNTAVRA_USE_TIKTOKEN") == "1":
            import tiktoken  # type: ignore
            return len(tiktoken.get_encoding("o200k_base").encode(text))
    except Exception:
        pass
    chars = len(text)
    words = len(WORD_RE.findall(text))
    cjk = sum(1 for ch in text if "\u3000" <= ch <= "\u9fff")
    return max(1, (chars + 3) // 4, int(words * 1.18), int(cjk * 0.95))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    atomic_write(path, text.encode("utf-8"), mode=mode)


def run(command: Sequence[str], *, cwd: Path | None = None, timeout: int = 30, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    if not command or any("\x00" in str(part) for part in command):
        raise ValueError("invalid command")
    return subprocess.run(
        [str(part) for part in command], cwd=str(cwd) if cwd else None,
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        timeout=timeout, check=False, shell=False, env=env,
    )


def inside(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=False)
    resolved.relative_to(root.resolve(strict=False))
    return resolved


def redact_secrets(text: str) -> tuple[str, int]:
    output = str(text)
    count = 0
    for pattern in SECRET_PATTERNS:
        output, found = pattern.subn("[REDACTED_SECRET]", output)
        count += found
    return output, count


def contains_secret(text: str) -> bool:
    return any(pattern.search(str(text)) for pattern in SECRET_PATTERNS)


def git_root(path: Path) -> Path:
    path = path.resolve()
    result = run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], timeout=10)
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 and result.stdout.strip() else path


def git_head(path: Path) -> str:
    result = run(["git", "-C", str(path), "rev-parse", "HEAD"], timeout=10)
    return result.stdout.strip() if result.returncode == 0 else ""


def git_branch(path: Path) -> str:
    result = run(["git", "-C", str(path), "branch", "--show-current"], timeout=10)
    return result.stdout.strip() if result.returncode == 0 else ""


def iter_files(root: Path, excluded: set[str] | None = None) -> Iterator[Path]:
    excluded = excluded or set()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if any(part in excluded for part in path.relative_to(root).parts):
            continue
        yield path


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not match:
        raise ValueError("missing YAML frontmatter")
    result: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if ":" not in raw:
            raise ValueError(f"invalid frontmatter line: {raw!r}")
        key, value = raw.strip().split(":", 1)
        value = value.strip()
        if indent:
            if current is None:
                raise ValueError("nested value without parent")
            current[key] = _yaml_scalar(value)
            index += 1
            continue
        if value in {">", "|"}:
            folded = value == ">"
            chunks: list[str] = []
            index += 1
            while index < len(lines):
                nxt = lines[index]
                if nxt.strip() and len(nxt) - len(nxt.lstrip(" ")) == 0:
                    break
                chunks.append(nxt.strip() if folded else nxt[2:] if nxt.startswith("  ") else nxt)
                index += 1
            result[key] = (" " if folded else "\n").join(chunks).strip()
            current = None
            continue
        if value == "":
            current = {}
            result[key] = current
        else:
            result[key] = _yaml_scalar(value)
            current = None
        index += 1
    return result, text[match.end():]


def _yaml_scalar(value: str) -> Any:
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value in {"true", "false"}:
        return value == "true"
    if value in {"null", "~"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value
