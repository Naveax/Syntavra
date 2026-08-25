from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "36a6625d6712a43f0e68d5600f1640e9825bf251"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-36a6.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_36a6", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench 36a6 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_generated_loop_indentation() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    compile_guard = '''    for generated in (
        test_path,
        Path("tools/certify_signalbench_python_product_v1.py"),
    ):
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")
'''
    replacement = '''    def normalize_loop_bodies(path: Path, needle: str) -> None:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        matches = [index for index, line in enumerate(lines) if line.strip().startswith(needle)]
        if not matches:
            raise RuntimeError(f"generated loop body missing for {needle}")
        for index in matches:
            previous = index - 1
            while previous >= 0 and not lines[previous].strip():
                previous -= 1
            if previous < 0 or not lines[previous].strip().startswith("for repetition in range("):
                raise RuntimeError(f"generated loop parent drift for {needle} at {index + 1}")
            parent_indent = len(lines[previous]) - len(lines[previous].lstrip(" "))
            newline = "\\n" if lines[index].endswith("\\n") else ""
            lines[index] = " " * (parent_indent + 4) + lines[index].strip() + newline
        path.write_text("".join(lines), encoding="utf-8")

    certifier_path = Path("tools/certify_signalbench_python_product_v1.py")
    normalize_loop_bodies(test_path, 'rows.extend([self._result(')
    normalize_loop_bodies(certifier_path, 'rows.extend([fixture_result(')
    for generated in (
        test_path,
        certifier_path,
    ):
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")
'''
    if source.count(compile_guard) != 1:
        raise RuntimeError(f"V14 generated compile guard drift: {source.count(compile_guard)}")
    source = source.replace(compile_guard, replacement, 1)
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_generated_loop_indentation()


if __name__ == "__main__":
    main()
