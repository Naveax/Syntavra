from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class Subtask:
    id: str
    title: str
    objective: str
    capability: str
    context_paths: tuple[str, ...]
    dependencies: tuple[str, ...]
    max_output_tokens: int
    handoff: str


@dataclass(frozen=True)
class DelegationPlan:
    delegated: bool
    reason: str
    tasks: tuple[Subtask, ...]
    receipt_hash: str


class AutomaticSubtaskDelegator:
    CAPABILITIES = (
        (re.compile(r"(?i)\b(test|coverage|verify|benchmark)\b"), "verification"),
        (re.compile(r"(?i)\b(security|secret|permission|auth|sandbox)\b"), "security"),
        (re.compile(r"(?i)\b(ui|dashboard|extension|frontend|statusline)\b"), "interface"),
        (re.compile(r"(?i)\b(database|schema|migration|index|memory)\b"), "data"),
        (re.compile(r"(?i)\b(provider|model|routing|quota|rate limit)\b"), "provider"),
        (re.compile(r"(?i)\b(ast|symbol|call graph|class hierarchy|refactor)\b"), "code-intelligence"),
    )

    @staticmethod
    def _sentences(text: str) -> list[str]:
        return [row.strip(" -*\t") for row in re.split(r"(?:\n+|(?<=[.!?])\s+)", text) if len(row.strip()) >= 8]

    def plan(self, objective: str, *, context_paths: Sequence[str] = (), max_tasks: int = 8) -> DelegationPlan:
        sentences = self._sentences(objective)
        groups: dict[str, list[str]] = {}
        for sentence in sentences:
            capability = "implementation"
            for pattern, name in self.CAPABILITIES:
                if pattern.search(sentence):
                    capability = name
                    break
            groups.setdefault(capability, []).append(sentence)
        if len(groups) <= 1 and len(sentences) <= 3:
            body = {"delegated": False, "reason": "task is small enough for one agent", "tasks": ()}
            return DelegationPlan(**body, receipt_hash=sha256_bytes(canonical_json(body)))
        tasks: list[Subtask] = []
        previous: list[str] = []
        for index, (capability, items) in enumerate(sorted(groups.items()), 1):
            if len(tasks) >= max_tasks:
                break
            task_id = f"T{index:02d}"
            objective_text = " ".join(items)
            handoff = f"{task_id} {capability}: return decisions, changed paths, verification commands, blockers; omit narration."
            tasks.append(Subtask(task_id, capability.replace("-", " ").title(), objective_text, capability, tuple(context_paths), tuple(previous[-2:]), 1200, handoff))
            previous.append(task_id)
        body = {"delegated": True, "reason": "independent capability groups detected", "tasks": tuple(tasks)}
        return DelegationPlan(**body, receipt_hash=sha256_bytes(canonical_json({"delegated": True, "reason": body["reason"], "tasks": [asdict(item) for item in tasks]})))
