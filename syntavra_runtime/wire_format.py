from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class WireEnvelope:
    version: int
    keys: tuple[str, ...]
    paths: tuple[str, ...]
    payload: Any
    original_bytes: int
    encoded_bytes: int
    savings_ratio: float
    original_hash: str


class LosslessWireCodec:
    """Lossless compact JSON wire format using key dictionaries and path handles."""

    VERSION = 1

    @staticmethod
    def _collect_keys(value: Any, counts: dict[str, int]) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                name = str(key)
                counts[name] = counts.get(name, 0) + 1
                LosslessWireCodec._collect_keys(child, counts)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value:
                LosslessWireCodec._collect_keys(child, counts)

    @staticmethod
    def _looks_path(value: str) -> bool:
        return len(value) > 8 and ("/" in value or "\\" in value) and not value.startswith(("http://", "https://"))

    def encode(self, value: Any, *, min_savings_ratio: float = 0.08) -> dict[str, Any]:
        counts: dict[str, int] = {}
        self._collect_keys(value, counts)
        keys = tuple(key for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if count >= 2 and len(key) >= 3)
        key_index = {key: index for index, key in enumerate(keys)}
        path_counts: dict[str, int] = {}

        def collect_paths(item: Any) -> None:
            if isinstance(item, str) and self._looks_path(item):
                path_counts[item] = path_counts.get(item, 0) + 1
            elif isinstance(item, Mapping):
                for child in item.values(): collect_paths(child)
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                for child in item: collect_paths(child)
        collect_paths(value)
        paths = tuple(path for path, count in sorted(path_counts.items(), key=lambda item: (-item[1], item[0])) if count >= 2)
        path_index = {path: index for index, path in enumerate(paths)}

        def compact(item: Any) -> Any:
            if isinstance(item, Mapping):
                return {str(key_index.get(str(key), str(key))): compact(child) for key, child in item.items()}
            if isinstance(item, list):
                return [compact(child) for child in item]
            if isinstance(item, tuple):
                return {"@tuple": [compact(child) for child in item]}
            if isinstance(item, str) and item in path_index:
                return {"@path": path_index[item]}
            return item

        original = canonical_json(value)
        envelope = {"v": self.VERSION, "k": keys, "p": paths, "d": compact(value), "h": sha256_bytes(original)}
        encoded = canonical_json(envelope)
        ratio = max(0.0, (len(original) - len(encoded)) / len(original)) if original else 0.0
        if ratio < min_savings_ratio:
            return {"encoding": "json", "payload": value, "original_bytes": len(original), "encoded_bytes": len(original), "savings_ratio": 0.0, "original_hash": sha256_bytes(original)}
        return {"encoding": "syntavra-wire-v1", "payload": envelope, "original_bytes": len(original), "encoded_bytes": len(encoded), "savings_ratio": ratio, "original_hash": envelope["h"]}

    def decode(self, encoded: Mapping[str, Any]) -> Any:
        if encoded.get("encoding") == "json":
            return encoded.get("payload")
        envelope = encoded.get("payload") if "payload" in encoded else encoded
        if not isinstance(envelope, Mapping) or int(envelope.get("v", 0)) != self.VERSION:
            raise ValueError("unsupported wire envelope")
        keys = tuple(envelope.get("k") or ())
        paths = tuple(envelope.get("p") or ())

        def expand(item: Any) -> Any:
            if isinstance(item, Mapping):
                if set(item) == {"@path"}:
                    return paths[int(item["@path"])]
                if set(item) == {"@tuple"}:
                    return tuple(expand(child) for child in item["@tuple"])
                result: dict[str, Any] = {}
                for key, child in item.items():
                    name = keys[int(key)] if str(key).isdigit() and int(key) < len(keys) else str(key)
                    result[name] = expand(child)
                return result
            if isinstance(item, list):
                return [expand(child) for child in item]
            return item

        value = expand(envelope.get("d"))
        if sha256_bytes(canonical_json(value)) != envelope.get("h"):
            raise ValueError("wire payload integrity mismatch")
        return value
