from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "a8a72dda1bc5089bc0de2a833e82332f3d51e808"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-a8a72.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_a8a72", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench a8a72 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repair_fixture_dispatch_order() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    start_marker = "def _apply_fixture_pair(text: str, old: str, new: str, label: str) -> str:\n"
    end_marker = "\n\ndef patch_tests_and_certifier() -> None:\n"
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError("V14 fixture dispatcher start missing")
    end = source.find(end_marker, start)
    if end < 0:
        raise RuntimeError("V14 fixture dispatcher end missing")

    previous = source[start:end]
    if "def _apply_fixture_pair_previous(" in previous:
        raise RuntimeError("V14 fixture dispatcher already wrapped unexpectedly")
    previous = previous.replace(
        "def _apply_fixture_pair(",
        "def _apply_fixture_pair_previous(",
        1,
    )
    dispatcher = r'''
def _apply_fixture_pair(text: str, old: str, new: str, label: str) -> str:
    if label in {
        "valid task fixture",
        "result identity",
        "request identity",
        "cert request",
        "cert result identity",
        "cert task",
        "cert negative",
        "cert contract keys",
        "cert output",
    }:
        return _apply_fixture_pair_previous(
            text,
            "\0__syntavra_semantic_old__",
            "\0__syntavra_semantic_new__",
            label,
        )
    return _apply_fixture_pair_previous(text, old, new, label)
'''
    source = source[:start] + previous + dispatcher + source[end:]

    main_call = "    patch_tests_and_certifier()\n"
    guard = '''    patch_tests_and_certifier()
    for generated in (
        Path("tests/runtime/test_signalbench_python_product_v1.py"),
        Path("tools/certify_signalbench_python_product_v1.py"),
    ):
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")
'''
    if source.count(main_call) != 1:
        raise RuntimeError(f"V14 patch_tests main call drift: {source.count(main_call)}")
    source = source.replace(main_call, guard, 1)

    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _repair_fixture_dispatch_order()


if __name__ == "__main__":
    main()
