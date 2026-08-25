from __future__ import annotations

import ast
import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "96356dba3d9ea257f131d71dffdfcc50c045ef61"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-96356-fixed.py")

OLD = "helper = '''def _repair_legacy_provider_billed_compat(path: Path) -> None:"
NEW = "helper = r'''def _repair_legacy_provider_billed_compat(path: Path) -> None:"


def _load_fixed_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    if source.count(OLD) != 1:
        raise RuntimeError(f"legacy provider helper literal anchor drift: {source.count(OLD)}")
    source = source.replace(OLD, NEW, 1)
    compile(source, str(PREVIOUS_RUNNER), "exec")

    tree = ast.parse(source, filename=str(PREVIOUS_RUNNER))
    helper_source = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "helper"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value.startswith(
                "def _repair_legacy_provider_billed_compat("
            ):
                helper_source = value
                break
    if helper_source is None:
        raise RuntimeError("patched legacy provider helper source not found")
    compile(helper_source, "/tmp/signalbench-v14-legacy-provider-helper.py", "exec")

    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "signalbench_v14r3_previous_96356_fixed",
        PREVIOUS_RUNNER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load fixed SignalBench 96356 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    previous = _load_fixed_previous()
    previous.main()


if __name__ == "__main__":
    main()
