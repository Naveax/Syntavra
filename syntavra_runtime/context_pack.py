from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable

from .competitive_fabric import StructuralNavigator
from .structural import StructuralIndex
from .token_attribution import TokenEstimator
from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class ContextPackItem:
    tier: str
    kind: str
    path: str
    start_line: int
    end_line: int
    text: str
    tokens: int
    token_confidence: str
    file_hash: str
    reason: str


@dataclass(frozen=True)
class TaskContextPack:
    query: str
    budget_tokens: int
    used_tokens: int
    seed_symbols: tuple[str, ...]
    items: tuple[ContextPackItem, ...]
    affected_paths: tuple[str, ...]
    affected_tests: tuple[str, ...]
    required_verifiers: tuple[str, ...]
    recoverable_paths: tuple[str, ...]
    pack_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskContextAssembler:
    """Assemble minimum exact repository context for one coding task."""

    def __init__(self, index: StructuralIndex, navigator: StructuralNavigator):
        self.index = index
        self.navigator = navigator

    def _item(self, *, tier: str, kind: str, path: str, start: int, end: int, reason: str) -> ContextPackItem:
        source = self.navigator.read_range(path, start_line=start, end_line=end, max_bytes=48 * 1024)
        tokens, confidence = TokenEstimator.text(source["text"])
        return ContextPackItem(
            tier=tier,
            kind=kind,
            path=path,
            start_line=int(source["start_line"]),
            end_line=int(source["end_line"]),
            text=str(source["text"]),
            tokens=tokens,
            token_confidence=confidence,
            file_hash=str(source["file_hash"]),
            reason=reason,
        )

    @staticmethod
    def _fit_item(item: ContextPackItem, remaining_tokens: int) -> ContextPackItem | None:
        if remaining_tokens <= 0:
            return None
        if item.tokens <= remaining_tokens:
            return item
        text = item.text
        if not text:
            return None
        low, high = 1, len(text)
        best = ""
        best_tokens = 0
        best_confidence = item.token_confidence
        while low <= high:
            middle = (low + high) // 2
            candidate = text[:middle].rstrip()
            tokens, confidence = TokenEstimator.text(candidate)
            if tokens <= remaining_tokens:
                best, best_tokens, best_confidence = candidate, tokens, confidence
                low = middle + 1
            else:
                high = middle - 1
        if not best:
            return None
        return replace(item, text=best, tokens=best_tokens, token_confidence=best_confidence, reason=item.reason + " (budget-clipped)")

    def assemble(
        self,
        query: str,
        *,
        changed_paths: Iterable[str] = (),
        token_budget: int = 8_000,
        max_depth: int = 4,
    ) -> TaskContextPack:
        if not query.strip():
            raise ValueError("context query is required")
        if token_budget < 256:
            raise ValueError("token_budget must be at least 256")
        effective_budget = min(int(token_budget), 1_500)
        self.index.index()
        seeds = self.index.task_seeds(query, limit=8)
        seed_names = tuple(dict.fromkeys(str(row.get("qualified_name") or row.get("name")) for row in seeds if row.get("qualified_name") or row.get("name")))
        impacts = [self.index.inspect_impact(seed, max_depth=max_depth) for seed in (seed_names or (query,))]
        impact = {
            "definitions": [row for value in impacts for row in value.get("definitions", [])],
            "affected_paths": sorted({row for value in impacts for row in value.get("affected_paths", [])}),
            "affected_tests": sorted({row for value in impacts for row in value.get("affected_tests", [])}),
            "required_verifiers": sorted({row for value in impacts for row in value.get("required_verifiers", [])}),
        }
        changed = tuple(dict.fromkeys(str(path) for path in changed_paths))
        path_impact = self.index.impacted_by_paths(changed, max_depth=max_depth) if changed else {
            "affected_paths": [], "affected_tests": [], "required_verifiers": []
        }
        repository_map = self.index.repository_map(seed_names[0] if seed_names else query, token_budget=max(256, effective_budget // 3), max_depth=max_depth)

        candidates: list[ContextPackItem] = []
        seen_ranges: set[tuple[str, int, int]] = set()

        for definition in impact.get("definitions", []):
            start = max(1, int(definition.get("line", 1)) - 2)
            end = int(definition.get("end_line") or definition.get("line") or start) + 2
            key = (str(definition["path"]), start, end)
            if key not in seen_ranges:
                candidates.append(self._item(
                    tier="mandatory", kind="definition", path=key[0], start=start, end=end,
                    reason=f"exact definition for {definition.get('qualified_name') or definition.get('name')}",
                ))
                seen_ranges.add(key)

        for row in repository_map.get("selected", []):
            start = max(1, int(row.get("line", 1)) - 1)
            end = int(row.get("end_line") or row.get("line") or start) + 1
            key = (str(row["path"]), start, end)
            if key in seen_ranges:
                continue
            tier = "likely" if row["path"] in impact.get("affected_paths", []) else "optional"
            candidates.append(self._item(
                tier=tier, kind=str(row.get("kind", "symbol")), path=key[0], start=start, end=end,
                reason="graph-ranked affected symbol" if tier == "likely" else "query-ranked repository symbol",
            ))
            seen_ranges.add(key)

        tier_order = {"mandatory": 0, "likely": 1, "optional": 2}
        candidates.sort(key=lambda item: (tier_order[item.tier], item.tokens, item.path, item.start_line))
        selected: list[ContextPackItem] = []
        used = 0
        for item in candidates:
            remaining = effective_budget - used
            if remaining <= 0:
                break
            if item.tokens > remaining and item.tier != "mandatory":
                continue
            fitted = self._fit_item(item, remaining) if item.tokens > remaining else item
            if fitted is None:
                continue
            selected.append(fitted)
            used += fitted.tokens

        affected_paths = tuple(sorted(set(impact.get("affected_paths", [])) | set(path_impact.get("affected_paths", []))))
        affected_tests = tuple(sorted(set(impact.get("affected_tests", [])) | set(path_impact.get("affected_tests", []))))
        verifiers = tuple(sorted(set(impact.get("required_verifiers", [])) | set(path_impact.get("required_verifiers", []))))
        included_paths = {item.path for item in selected}
        recoverable = tuple(path for path in affected_paths if path not in included_paths)
        body = {
            "query": query,
            "budget_tokens": effective_budget,
            "used_tokens": used,
            "seed_symbols": seed_names,
            "items": [asdict(item) for item in selected],
            "affected_paths": affected_paths,
            "affected_tests": affected_tests,
            "required_verifiers": verifiers,
            "recoverable_paths": recoverable,
        }
        return TaskContextPack(
            query=query,
            budget_tokens=effective_budget,
            used_tokens=used,
            seed_symbols=seed_names,
            items=tuple(selected),
            affected_paths=affected_paths,
            affected_tests=affected_tests,
            required_verifiers=verifiers,
            recoverable_paths=recoverable,
            pack_hash=sha256_bytes(canonical_json(body)),
        )
