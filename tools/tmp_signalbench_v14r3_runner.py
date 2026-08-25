from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "a0386b605c5a8e5588d12b03635b9a88cda74ab9"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-a0386.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_a0386", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench a0386 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_generated_test_normalizer() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    old = '''    patch_tests_and_certifier()
    for generated in (
        Path("tests/runtime/test_signalbench_python_product_v1.py"),
        Path("tools/certify_signalbench_python_product_v1.py"),
    ):
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")
'''
    new = '''    patch_tests_and_certifier()
    test_path = Path("tests/runtime/test_signalbench_python_product_v1.py")
    test_lines = test_path.read_text(encoding="utf-8").splitlines(keepends=True)
    target = "repo, commit, tree = self._repo(Path(temp))"
    matches = [index for index, line in enumerate(test_lines) if line.strip() == target]
    if len(matches) != 1:
        raise RuntimeError(f"generated valid-task repo line drift: {len(matches)}")
    index = matches[0]
    newline = "\\n" if test_lines[index].endswith("\\n") else ""
    test_lines[index] = "            " + target + newline
    test_path.write_text("".join(test_lines), encoding="utf-8")
    for generated in (
        test_path,
        Path("tools/certify_signalbench_python_product_v1.py"),
    ):
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")
'''
    if source.count(old) != 1:
        raise RuntimeError(f"V14 generated compile guard drift: {source.count(old)}")
    source = source.replace(old, new, 1)
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_generated_test_normalizer()


if __name__ == "__main__":
    main()
