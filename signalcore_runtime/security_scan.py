from __future__ import annotations

import base64
import binascii
import gzip
import math
import re
import unicodedata
import urllib.parse
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

_ANSI = re.compile(r"\x1b(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("generic-assignment", re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|passwd|secret|bearer|"
        r"private[_-]?key|client[_-]?secret|session[_-]?id|cookie)\b\s*[:=]\s*([^\s,;]+)"
    )),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("database-uri", re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+")),
    ("private-key", re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----", re.S)),
    ("payment-card", re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?is)(ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
        r"do\s+not\s+follow\s+(?:the\s+)?(?:system|developer)|"
        r"reveal\s+(?:the\s+)?(?:system\s+)?prompt|"
        r"you\s+are\s+(?:chatgpt|an?\s+assistant)|"
        r"<\/?(?:system|assistant|developer|tool)>|"
        r"system\s+message\s*:|developer\s+message\s*:)")
    ,
    re.compile(
        r"(?is)(önceki\s+(?:tüm\s+)?talimatları\s+(?:yoksay|unut)|"
        r"sistem\s+istemini\s+(?:göster|açıkla)|"
        r"geliştirici\s+mesajını\s+(?:göster|ifşa\s+et))")
    ,
    re.compile(
        r"(?is)(ignora\s+(?:todas\s+)?las\s+instrucciones\s+anteriores|"
        r"忽略(?:之前|所有).*指令|"
        r"以前の指示を.*無視)")
    ,
)

_BASE64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{32,}={0,2}(?![A-Za-z0-9+/_-])")
_HEX_TOKEN = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")
_URL_TOKEN = re.compile(r"(?:%[0-9A-Fa-f]{2}){8,}")
_HIGH_ENTROPY = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-+/=]{24,}(?![A-Za-z0-9])")
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})


@dataclass(frozen=True)
class SecurityScan:
    normalized_text: str
    redacted_text: str
    secret_types: tuple[str, ...]
    injection_risk: bool
    injection_reasons: tuple[str, ...]
    encoded_payloads_checked: int
    pii_types: tuple[str, ...] = ()
    high_entropy_tokens: int = 0
    confusable_risk: bool = False

    @property
    def secrets_found(self) -> int:
        return len(self.secret_types)


def normalize_untrusted_text(text: str) -> str:
    value = _ANSI.sub("", text)
    value = unicodedata.normalize("NFKC", value).translate(_ZERO_WIDTH)
    return value.replace("\r\n", "\n").replace("\r", "\n")


def confusable_skeleton(text: str) -> str:
    return normalize_untrusted_text(text).translate(_CONFUSABLES)


def _luhn(value: str) -> bool:
    digits = [int(ch) for ch in value if ch.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _entropy(token: str) -> float:
    if not token:
        return 0.0
    counts = Counter(token)
    length = len(token)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _redact_pattern(text: str, name: str, pattern: re.Pattern[str]) -> tuple[str, bool]:
    found = False

    def replace(match: re.Match[str]) -> str:
        nonlocal found
        if name == "payment-card" and not _luhn(match.group(0)):
            return match.group(0)
        found = True
        if name == "generic-assignment" and match.lastindex and match.lastindex >= 1:
            return f"{match.group(1)}=<redacted:{name}>"
        if name == "private-key":
            return "-----BEGIN PRIVATE KEY-----<redacted:private-key>-----END PRIVATE KEY-----"
        return f"<redacted:{name}>"

    return pattern.sub(replace, text), found


def _bounded_decodes(text: str, *, max_candidates: int = 128, max_total_bytes: int = 2 * 1024 * 1024) -> Iterable[str]:
    checked = 0
    consumed = 0
    seen: set[bytes] = set()

    def emit(raw: bytes) -> Iterable[str]:
        nonlocal checked, consumed
        if not raw or raw in seen or checked >= max_candidates or consumed + len(raw) > max_total_bytes:
            return ()
        seen.add(raw)
        checked += 1
        consumed += len(raw)
        try:
            return (raw.decode("utf-8"),)
        except UnicodeDecodeError:
            return ()

    for match in _BASE64_TOKEN.finditer(text):
        token = match.group(0)
        padded = token + "=" * ((4 - len(token) % 4) % 4)
        for altchars in (None, b"-_"):
            try:
                raw = base64.b64decode(padded, altchars=altchars, validate=False)
            except (ValueError, binascii.Error):
                continue
            yield from emit(raw)
            break
        if checked >= max_candidates:
            return

    for match in _HEX_TOKEN.finditer(text):
        try:
            raw = bytes.fromhex(match.group(0))
        except ValueError:
            continue
        yield from emit(raw)
        if checked >= max_candidates:
            return

    for match in _URL_TOKEN.finditer(text):
        try:
            raw = urllib.parse.unquote_to_bytes(match.group(0))
        except ValueError:
            continue
        yield from emit(raw)
        if checked >= max_candidates:
            return


def _compressed_decodes(data: bytes, *, max_output: int = 2 * 1024 * 1024) -> Iterable[str]:
    candidates: list[bytes] = []
    if data.startswith(b"\x1f\x8b"):
        try:
            candidates.append(gzip.decompress(data))
        except (OSError, EOFError):
            pass
    if data[:2] in {b"x\x01", b"x\x9c", b"x\xda"}:
        try:
            candidates.append(zlib.decompress(data))
        except zlib.error:
            pass
    for raw in candidates:
        if len(raw) <= max_output:
            try:
                yield raw.decode("utf-8")
            except UnicodeDecodeError:
                continue


def scan_text(text: str, *, inspect_encoded: bool = True) -> SecurityScan:
    normalized = normalize_untrusted_text(text)
    redacted = normalized
    secret_types: list[str] = []
    pii_types: list[str] = []
    for name, pattern in _SECRET_PATTERNS:
        redacted, found = _redact_pattern(redacted, name, pattern)
        if found:
            (pii_types if name in {"payment-card", "email"} else secret_types).append(name)

    reasons: list[str] = []
    skeleton = confusable_skeleton(normalized)
    for index, pattern in enumerate(_INJECTION_PATTERNS):
        if pattern.search(normalized):
            reasons.append(f"direct-pattern-{index + 1}")
        elif skeleton != normalized and pattern.search(skeleton):
            reasons.append(f"confusable-pattern-{index + 1}")

    high_entropy = 0
    for match in _HIGH_ENTROPY.finditer(normalized):
        token = match.group(0)
        if _entropy(token) >= 4.2 and len(set(token)) >= 10:
            high_entropy += 1
            if not any(marker in token.casefold() for marker in ("http", "sha256")):
                secret_types.append("high-entropy-token")

    checked = 0
    if inspect_encoded:
        for decoded in _bounded_decodes(normalized):
            checked += 1
            nested = scan_text(decoded, inspect_encoded=False)
            secret_types.extend(nested.secret_types)
            pii_types.extend(nested.pii_types)
            if nested.injection_risk:
                reasons.append("encoded-instruction")

    return SecurityScan(
        normalized,
        redacted,
        tuple(dict.fromkeys(secret_types)),
        bool(reasons),
        tuple(dict.fromkeys(reasons)),
        checked,
        tuple(dict.fromkeys(pii_types)),
        high_entropy,
        skeleton != normalized,
    )


def scan_bytes(data: bytes, *, max_scan_bytes: int | None = None) -> SecurityScan:
    sample = data if max_scan_bytes is None else data[:max_scan_bytes]
    decoded = sample.decode("utf-8", errors="replace")
    scans = [scan_text(decoded)]
    scans.extend(scan_text(value) for value in _compressed_decodes(sample))
    return SecurityScan(
        scans[0].normalized_text,
        scans[0].redacted_text,
        tuple(dict.fromkeys(item for scan in scans for item in scan.secret_types)),
        any(scan.injection_risk for scan in scans),
        tuple(dict.fromkeys(item for scan in scans for item in scan.injection_reasons)),
        sum(scan.encoded_payloads_checked for scan in scans),
        tuple(dict.fromkeys(item for scan in scans for item in scan.pii_types)),
        sum(scan.high_entropy_tokens for scan in scans),
        any(scan.confusable_risk for scan in scans),
    )


class SecurityStreamScanner:
    """Bounded incremental scanner that covers the complete byte stream.

    It scans overlapping windows so secrets split across chunk boundaries are not
    missed. The scanner retains only aggregate findings and a redacted preview.
    """

    def __init__(self, *, overlap: int = 8192, preview_chars: int = 4096):
        self.overlap = max(256, overlap)
        self.preview_chars = max(256, preview_chars)
        self._tail = b""
        self._secret_types: list[str] = []
        self._pii_types: list[str] = []
        self._reasons: list[str] = []
        self._checked = 0
        self._entropy = 0
        self._confusable = False
        self._preview = ""
        self.bytes_scanned = 0

    def update(self, chunk: bytes) -> None:
        value = bytes(chunk)
        self.bytes_scanned += len(value)
        window = self._tail + value
        scan = scan_bytes(window)
        self._secret_types.extend(scan.secret_types)
        self._pii_types.extend(scan.pii_types)
        self._reasons.extend(scan.injection_reasons)
        self._checked += scan.encoded_payloads_checked
        self._entropy += scan.high_entropy_tokens
        self._confusable = self._confusable or scan.confusable_risk
        if len(self._preview) < self.preview_chars:
            remaining = self.preview_chars - len(self._preview)
            self._preview += scan.redacted_text[:remaining]
        self._tail = window[-self.overlap:]

    def finalize(self) -> SecurityScan:
        return SecurityScan(
            "",
            self._preview,
            tuple(dict.fromkeys(self._secret_types)),
            bool(self._reasons),
            tuple(dict.fromkeys(self._reasons)),
            self._checked,
            tuple(dict.fromkeys(self._pii_types)),
            self._entropy,
            self._confusable,
        )


def redact_text(text: str) -> str:
    return scan_text(text, inspect_encoded=False).redacted_text

class IncrementalSecurityScanner(SecurityStreamScanner):
    """Backward-compatible V6 name used by the streaming pipeline."""
    def __init__(self, *, overlap_chars: int = 8192, preview_chars: int = 4096):
        super().__init__(overlap=overlap_chars, preview_chars=preview_chars)
    def feed(self, value: str | bytes) -> None:
        self.update(value.encode('utf-8') if isinstance(value, str) else value)
    def result(self) -> SecurityScan:
        result = self.finalize()
        pii = tuple('payment-card' if item == 'credit-card' else item for item in result.pii_types)
        return SecurityScan(
            result.normalized_text, result.redacted_text, result.secret_types,
            result.injection_risk, result.injection_reasons, result.encoded_payloads_checked,
            pii, result.high_entropy_tokens, result.confusable_risk,
        )
