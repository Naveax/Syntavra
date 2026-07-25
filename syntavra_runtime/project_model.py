from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class VerifierSpec:
    name: str
    argv: tuple[str, ...]
    stage: str
    confidence: float
    reason: str
    source: str


class ProjectModel:
    """Repository-aware project and verifier discovery without shell strings."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)

    def _exists(self, name: str) -> bool:
        return (self.root / name).exists()

    @staticmethod
    def _available(argv: tuple[str, ...]) -> bool:
        executable = argv[0]
        if executable.startswith("./") or executable.startswith(".\\"):
            return True
        return shutil.which(executable) is not None

    def _python(self) -> list[VerifierSpec]:
        specs: list[VerifierSpec] = []
        pyproject = self.root / "pyproject.toml"
        payload: dict[str, Any] = {}
        if pyproject.is_file():
            try:
                payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                payload = {}
        pytest_configured = "pytest" in (payload.get("tool") or {}) or self._exists("pytest.ini") or self._exists("tox.ini")
        if pytest_configured or (self.root / "tests").is_dir():
            specs.append(VerifierSpec("pytest", (sys.executable, "-m", "pytest", "-q"), "test", 0.95, "Python tests discovered", "pyproject/tests"))
        if "ruff" in (payload.get("tool") or {}) or self._exists("ruff.toml"):
            specs.append(VerifierSpec("ruff", (sys.executable, "-m", "ruff", "check", "."), "lint", 0.9, "Ruff configuration discovered", "pyproject/ruff.toml"))
        if "mypy" in (payload.get("tool") or {}) or self._exists("mypy.ini"):
            specs.append(VerifierSpec("mypy", (sys.executable, "-m", "mypy", "."), "typecheck", 0.82, "Mypy configuration discovered", "pyproject/mypy.ini"))
        return specs

    def _node(self) -> list[VerifierSpec]:
        package = self.root / "package.json"
        if not package.is_file():
            return []
        try:
            payload = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        scripts = payload.get("scripts") if isinstance(payload, dict) else {}
        scripts = scripts if isinstance(scripts, dict) else {}
        manager = "pnpm" if self._exists("pnpm-lock.yaml") else "yarn" if self._exists("yarn.lock") else "npm"
        run = (manager, "run") if manager != "yarn" else (manager,)
        specs: list[VerifierSpec] = []
        for name, stage, confidence in (
            ("test", "test", 0.95),
            ("typecheck", "typecheck", 0.9),
            ("lint", "lint", 0.86),
            ("build", "build", 0.8),
        ):
            if name in scripts:
                specs.append(VerifierSpec(f"{manager}-{name}", (*run, name), stage, confidence, f"package.json script '{name}'", "package.json"))
        return specs

    def discover_verifiers(self, changed_paths: Iterable[str] = ()) -> tuple[VerifierSpec, ...]:
        del changed_paths  # reserved for affected-test narrowing.
        specs = [*self._python(), *self._node()]
        if self._exists("Cargo.toml"):
            specs.append(VerifierSpec("cargo-test", ("cargo", "test", "--workspace"), "test", 0.96, "Cargo workspace discovered", "Cargo.toml"))
        if self._exists("go.mod"):
            specs.append(VerifierSpec("go-test", ("go", "test", "./..."), "test", 0.96, "Go module discovered", "go.mod"))
        if self._exists("pom.xml"):
            executable = "./mvnw" if self._exists("mvnw") else "mvn"
            specs.append(VerifierSpec("maven-test", (executable, "test"), "test", 0.94, "Maven project discovered", "pom.xml"))
        if self._exists("build.gradle") or self._exists("build.gradle.kts"):
            executable = "./gradlew" if self._exists("gradlew") else "gradle"
            specs.append(VerifierSpec("gradle-test", (executable, "test"), "test", 0.94, "Gradle project discovered", "build.gradle"))
        if self._exists("CMakeLists.txt"):
            specs.append(VerifierSpec("ctest", ("ctest", "--output-on-failure"), "test", 0.68, "CMake project discovered; assumes configured build tree", "CMakeLists.txt"))
        if self._exists("Makefile"):
            specs.append(VerifierSpec("make-test", ("make", "test"), "test", 0.55, "Makefile discovered; target availability is candidate evidence", "Makefile"))

        dedup: dict[tuple[str, ...], VerifierSpec] = {}
        for spec in specs:
            if self._available(spec.argv):
                current = dedup.get(spec.argv)
                if current is None or spec.confidence > current.confidence:
                    dedup[spec.argv] = spec
        ordered = sorted(dedup.values(), key=lambda item: (-item.confidence, {"test": 0, "typecheck": 1, "lint": 2, "build": 3}.get(item.stage, 9), item.name))
        return tuple(ordered)

    def primary_verifier(self) -> VerifierSpec:
        verifiers = self.discover_verifiers()
        if not verifiers:
            raise RuntimeError("no executable project verifier could be discovered")
        return verifiers[0]

    def describe(self) -> dict[str, Any]:
        files = [
            name
            for name in (
                "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml",
                "build.gradle", "build.gradle.kts", "CMakeLists.txt", "Makefile",
            )
            if self._exists(name)
        ]
        return {
            "root": str(self.root),
            "project_files": files,
            "verifiers": [asdict(item) for item in self.discover_verifiers()],
            "environment": {
                "platform": os.name,
                "python": shutil.which("python") or shutil.which("python3") or "",
            },
        }


__all__ = ["ProjectModel", "VerifierSpec"]
