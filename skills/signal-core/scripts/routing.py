#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from common import DATA, dump_json, load_json, normalize, normalized_tokens

LEXICON = load_json(DATA / "lexicon.json")
CATEGORY_ORDER = tuple(LEXICON["categories"].keys())


@lru_cache(maxsize=8192)
def _phrase_tokens(phrase: str) -> tuple[str, ...]:
    return normalized_tokens(phrase)


def _morph_match(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if len(expected) >= 4 and actual.startswith(expected):
        suffix = actual[len(expected):]
        if suffix in {"s", "es", "ed", "ing", "ler", "lar", "lari", "leri", "in", "un", "i", "e", "de", "den", "dan", "ni", "n", "en", "er", "es", "e", "mente", "cion", "tion", "eur", "ant", "ов", "ам", "ами"}:
            return True
    if len(actual) >= 6 and len(expected) >= 6:
        mismatches = sum(a != b for a, b in zip(expected, actual)) + abs(len(expected) - len(actual))
        return mismatches <= 1
    return False


def _phrase_score(text: str, text_tokens: Sequence[str], phrase: str) -> float:
    normalized_phrase = normalize(phrase)
    if not normalized_phrase:
        return 0.0
    if normalized_phrase in text:
        return 2.0 + 1.25 * len(_phrase_tokens(phrase))
    parts = _phrase_tokens(phrase)
    if len(parts) >= 2 and all(any(_morph_match(part, token) for token in text_tokens) for part in parts):
        return 0.9 * len(parts)
    if len(parts) == 1 and any(_morph_match(parts[0], token) for token in text_tokens):
        return 0.75
    return 0.0


@dataclass(frozen=True)
class RouteDecision:
    activate: bool
    confidence: float
    categories: tuple[str, ...]
    scores: dict[str, float]
    integrity: str
    complexity: str
    anti_trigger_score: float
    reason: str


def classify(task: str, *, known_file_edit: bool = False) -> RouteDecision:
    normalized = normalize(task)
    toks = normalized_tokens(task)
    scores: dict[str, float] = {}
    for category, phrases in LEXICON["categories"].items():
        scores[category] = sum(_phrase_score(normalized, toks, phrase) for phrase in phrases)
    anti = sum(_phrase_score(normalized, toks, phrase) for phrase in LEXICON["anti_trigger"])
    coding_signal = scores["repository"] + scores["graph"] + scores["debug"] + scores["verification"] + scores["security"]
    categories = tuple(category for category in CATEGORY_ORDER if scores[category] >= 1.5)
    total = sum(scores.values())
    max_score = max(scores.values()) if scores else 0.0
    intent_count = len(categories)
    exact_signal = scores["security"] >= 1.5 or any(term in normalized for term in ("exact error", "tam hata", "byte exact", "checksum", "hash", "exit code", "cikis kodu"))
    reversible_signal = any(term in normalized for term in ("reversible", "deduplicate", "fold", "tersinir", "geri getirilebilir"))
    integrity = "T0" if exact_signal else "T1" if reversible_signal else "T2"
    complexity_points = intent_count + int(scores["bulk"] >= 1.5) * 2 + int(scores["graph"] >= 1.5) + int(scores["long_session"] >= 1.5)
    complexity = "large" if complexity_points >= 5 else "medium" if complexity_points >= 2 else "small"
    weak = total < 2.0
    simple_known = known_file_edit and complexity == "small" and not exact_signal and scores["graph"] < 1.5
    anti_wins = anti >= 2.0 and coding_signal < 2.0
    activate = not weak and not simple_known and not anti_wins
    margin = max_score - (anti if anti_wins else 0.0)
    confidence = 1.0 / (1.0 + math.exp(-0.65 * margin))
    if not activate:
        confidence = max(confidence, 0.82 if anti_wins or simple_known else 0.68)
    reason = "positive-complex-coding-signal" if activate else "anti-trigger" if anti_wins else "known-small-edit" if simple_known else "insufficient-signal"
    return RouteDecision(activate, round(confidence, 6), categories, scores, integrity, complexity, anti, reason)


def main() -> int:
    parser = argparse.ArgumentParser(description="SignalCore deterministic activation and task routing")
    parser.add_argument("task")
    parser.add_argument("--known-file-edit", action="store_true")
    args = parser.parse_args()
    print(dump_json(asdict(classify(args.task, known_file_edit=args.known_file_edit))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
