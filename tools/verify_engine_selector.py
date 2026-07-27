#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from syntavra_runtime.engine_selector import EngineSelectionError, EngineSelector
from syntavra_runtime.util import atomic_write_json


def _write(path: Path, engine: str, *, schema_version: int = 1) -> None:
    atomic_write_json(path, {"schema_version": schema_version, "engine": engine})


def verify() -> dict[str, object]:
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="syntavra-r4-selector-") as directory:
        root = Path(directory)
        project = root / "project"
        user = root / "user" / "engine.json"
        env = {"HOME": str(root / "home")}

        selector = EngineSelector(project_root=project, user_config=user, env=env)
        default = selector.resolve()
        checks["builtin_python"] = default.requested == "python" and default.resolved == "python"

        _write(user, "rust")
        user_selection = selector.resolve()
        checks["user_precedence"] = user_selection.requested == "rust" and user_selection.scope == "user"

        _write(project / ".syntavra" / "engine.json", "auto")
        project_selection = selector.resolve()
        checks["project_precedence"] = project_selection.requested == "auto" and project_selection.scope == "project"
        checks["auto_is_python"] = project_selection.resolved == "python"

        environment_selector = EngineSelector(
            project_root=project,
            user_config=user,
            env={**env, "SYNTAVRA_ENGINE": "rust"},
        )
        checks["environment_precedence"] = environment_selector.resolve().source == "SYNTAVRA_ENGINE"
        checks["command_precedence"] = environment_selector.resolve(cli_override="python").source == "--engine"

        _write(project / ".syntavra" / "engine.json", "python", schema_version=2)
        try:
            selector.resolve()
        except EngineSelectionError as exc:
            checks["unknown_schema_fails_closed"] = exc.code == "ENGINE_CONFIG_SCHEMA_UNSUPPORTED"
        else:
            checks["unknown_schema_fails_closed"] = False

    if not all(checks.values()):
        raise RuntimeError(f"R4 engine selector contract failed: {checks}")
    return {
        "ok": True,
        "phase": "R4",
        "checks": checks,
        "selection_precedence": ["command", "environment", "project", "user", "builtin"],
        "auto_policy": "python",
        "fallback_policy": "fail-closed",
        "claim": "RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT",
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
