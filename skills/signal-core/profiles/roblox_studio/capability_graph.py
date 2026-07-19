from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable, Mapping

from .capabilities import CapabilitySpec
from .errors import CapabilityError


class CapabilityGraph:
    def __init__(self, specs: Mapping[str, CapabilitySpec], *, engines: Iterable[str], validators: Iterable[str]) -> None:
        self.specs = dict(specs)
        self.engines = set(engines)
        self.validators = set(validators)
        self.validate()

    def validate(self) -> None:
        for capability_id, spec in self.specs.items():
            missing = set(spec.dependencies) - self.specs.keys()
            if missing:
                raise CapabilityError(f"{capability_id} has missing dependencies: {sorted(missing)}")
            unknown_conflicts = set(spec.conflicts) - (self.specs.keys() | {"read_only"})
            if unknown_conflicts:
                raise CapabilityError(f"{capability_id} has unknown conflicts: {sorted(unknown_conflicts)}")
            if not set(spec.required_validators).issubset(self.validators):
                raise CapabilityError(f"{capability_id} references unknown validators")
            if not set(spec.engine_requirements).issubset(self.engines):
                raise CapabilityError(f"{capability_id} references unknown engines")
            if not all((spec.execution_contract, spec.positive_test, spec.negative_test)):
                raise CapabilityError(f"{capability_id} lacks executable coverage metadata")
        self._topological_order()

    def _topological_order(self) -> tuple[str, ...]:
        indegree = {key: 0 for key in self.specs}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for key, spec in self.specs.items():
            for dependency in spec.dependencies:
                indegree[key] += 1
                outgoing[dependency].append(key)
        queue = deque(sorted(key for key, degree in indegree.items() if degree == 0))
        order: list[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for child in sorted(outgoing[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(self.specs):
            raise CapabilityError("capability dependency cycle detected")
        return tuple(order)

    def plan(self, requested: Iterable[str], authorized: Iterable[str]) -> tuple[str, ...]:
        requested_set = set(requested)
        authorized_set = set(authorized)
        unknown = requested_set - self.specs.keys()
        if unknown:
            raise CapabilityError(f"unknown requested capabilities: {sorted(unknown)}")
        closure: set[str] = set()
        stack = list(requested_set)
        while stack:
            current = stack.pop()
            if current in closure:
                continue
            closure.add(current)
            stack.extend(self.specs[current].dependencies)
        if not closure.issubset(authorized_set):
            raise CapabilityError(f"transitive authorization violation: {sorted(closure - authorized_set)}")
        for capability_id in closure:
            conflicts = set(self.specs[capability_id].conflicts)
            if conflicts & closure:
                raise CapabilityError(f"capability conflict for {capability_id}")
        order = self._topological_order()
        return tuple(item for item in order if item in closure)
