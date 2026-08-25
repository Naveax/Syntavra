from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "4cb9f33585ad430ac219bb15d41c24e71a502bb1"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-4cb9.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_4cb9", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench 4cb9 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_generated_method_normalizer() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    start_marker = '    test_path = Path("tests/runtime/test_signalbench_python_product_v1.py")\n'
    end_marker = '    for generated in (\n        test_path,\n        Path("tools/certify_signalbench_python_product_v1.py"),\n    ):\n        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")\n'
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError("V14 generated test normalizer start drift")
    end_at = source.find(end_marker, start)
    if end_at < 0:
        raise RuntimeError("V14 generated compile guard end drift")
    end = end_at + len(end_marker)
    replacement = '''    test_path = Path("tests/runtime/test_signalbench_python_product_v1.py")
    test_text = test_path.read_text(encoding="utf-8")
    method_start = test_text.index("    def test_exact_tree_and_clean_worktree_are_enforced(")
    method_end = test_text.index("\\n    @staticmethod\\n    def _result(", method_start)
    method = test_text[method_start:method_end]
    method_lines = method.splitlines(keepends=True)
    if not method_lines:
        raise RuntimeError("generated valid-task method missing")
    unexpected_colons = [
        line.strip()
        for line in method_lines
        if line.strip().endswith(":")
        and not line.strip().startswith("def test_exact_tree_and_clean_worktree_are_enforced")
        and line.strip() != "with tempfile.TemporaryDirectory() as temp:"
    ]
    if unexpected_colons:
        raise RuntimeError(f"generated valid-task method gained nested blocks: {unexpected_colons}")
    normalized = []
    for index, line in enumerate(method_lines):
        stripped = line.strip()
        newline = "\\n" if line.endswith("\\n") else ""
        if not stripped:
            normalized.append(newline)
            continue
        if index == 0:
            indent = "    "
        elif stripped == "with tempfile.TemporaryDirectory() as temp:":
            indent = "        "
        else:
            indent = "            "
        normalized.append(indent + stripped + newline)
    method = "".join(normalized)
    if method.count("repo, commit, tree = self._repo(Path(temp))") != 1:
        raise RuntimeError("generated valid-task repo identity drift")
    if method.count("repository_commit=commit") != 1:
        raise RuntimeError("generated valid-task commit binding drift")
    test_text = test_text[:method_start] + method + test_text[method_end:]
    test_path.write_text(test_text, encoding="utf-8")
    for generated in (
        test_path,
        Path("tools/certify_signalbench_python_product_v1.py"),
    ):
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")
'''
    source = source[:start] + replacement + source[end:]
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_generated_method_normalizer()


if __name__ == "__main__":
    main()
