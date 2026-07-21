from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Mapping


class SchemaError(RuntimeError):
    pass


Validator = Callable[[Mapping[str, Any]], None]
Migration = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class SchemaDefinition:
    name: str
    version: int
    required: tuple[str, ...] = ()
    properties: Mapping[str, type | tuple[type, ...]] | None = None
    allow_extra: bool = True
    validator: Validator | None = None


class SchemaRegistry:
    """Central typed envelope registry with deterministic forward migrations."""

    def __init__(self):
        self._schemas: dict[tuple[str, int], SchemaDefinition] = {}
        self._latest: dict[str, int] = {}
        self._migrations: dict[tuple[str, int], Migration] = {}

    def register(self, definition: SchemaDefinition) -> None:
        if not definition.name or definition.version < 1:
            raise SchemaError("invalid schema identity")
        key = (definition.name, definition.version)
        if key in self._schemas:
            raise SchemaError("schema version already registered")
        self._schemas[key] = definition
        self._latest[definition.name] = max(definition.version, self._latest.get(definition.name, 0))

    def register_migration(self, name: str, from_version: int, migration: Migration) -> None:
        if (name, from_version) in self._migrations:
            raise SchemaError("schema migration already registered")
        if (name, from_version) not in self._schemas or (name, from_version + 1) not in self._schemas:
            raise SchemaError("both migration endpoint schemas must be registered")
        self._migrations[(name, from_version)] = migration

    def validate(self, name: str, value: Mapping[str, Any], *, version: int | None = None) -> dict[str, Any]:
        selected = int(version or value.get("schema_version") or self._latest.get(name, 0))
        definition = self._schemas.get((name, selected))
        if definition is None:
            raise SchemaError(f"unknown schema: {name}@{selected}")
        missing = [key for key in definition.required if key not in value]
        if missing:
            raise SchemaError("missing required properties: " + ",".join(sorted(missing)))
        properties = dict(definition.properties or {})
        if not definition.allow_extra:
            unknown = set(value) - set(properties) - {"schema_version"}
            if unknown:
                raise SchemaError("unknown properties: " + ",".join(sorted(unknown)))
        for key, expected in properties.items():
            if key in value and not isinstance(value[key], expected):
                raise SchemaError(f"property {key} has invalid type")
        if definition.validator:
            definition.validator(value)
        return copy.deepcopy(dict(value))

    def migrate(self, name: str, value: Mapping[str, Any], *, target_version: int | None = None) -> dict[str, Any]:
        current = int(value.get("schema_version") or 1)
        target = int(target_version or self._latest.get(name, 0))
        if target < current:
            raise SchemaError("automatic schema downgrade is forbidden")
        working = copy.deepcopy(dict(value))
        self.validate(name, working, version=current)
        while current < target:
            migration = self._migrations.get((name, current))
            if migration is None:
                raise SchemaError(f"missing migration: {name}@{current}->{current + 1}")
            working = dict(migration(working))
            current += 1
            working["schema_version"] = current
            self.validate(name, working, version=current)
        return working

    def catalog(self) -> dict[str, Any]:
        return {
            name: {
                "latest": latest,
                "versions": sorted(version for schema_name, version in self._schemas if schema_name == name),
            }
            for name, latest in sorted(self._latest.items())
        }
