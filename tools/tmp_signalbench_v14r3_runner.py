from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "8c0f25352eb3098636942984ce3b4d33a3a92096"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-8c0f.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_8c0f", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench 8c0f runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_validate_product_indentation() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    marker = "def _compile_generated_python(path: Path) -> None:\n"
    helper = r'''def _normalize_validate_product_method(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    starts = [
        index
        for index, line in enumerate(lines)
        if line.strip().startswith("def validate_product(")
    ]
    if len(starts) != 1:
        raise RuntimeError(f"validate_product method drift: {len(starts)}")
    start = starts[0]

    ends = [
        index
        for index in range(start + 1, len(lines))
        if lines[index].startswith("    @staticmethod")
        and index + 1 < len(lines)
        and lines[index + 1].strip().startswith("def _copy_repository")
    ]
    if len(ends) != 1:
        raise RuntimeError(f"validate_product method end drift: {len(ends)}")
    end = ends[0]

    expected = {
        'reasons.extend(f"task:{task.task_id}:{reason}" for reason in self._frozen_repository_reasons(task))': 12,
        'for field, value in (("version", arm.version), ("model", arm.model), ("reasoning", arm.reasoning)):': 12,
        'if _is_placeholder(str(value)):': 16,
        'reasons.append(f"arm:{arm.arm_id}:{field}-not-exact")': 20,
    }

    for stripped, indent in expected.items():
        matches = [
            index
            for index in range(start + 1, end)
            if lines[index].strip() == stripped
        ]
        if len(matches) != 1:
            block = "".join(lines[start:end])
            raise RuntimeError(
                f"validate_product indentation anchor drift for {stripped!r}: {len(matches)}\n{block}"
            )
        index = matches[0]
        newline = "\n" if lines[index].endswith("\n") else ""
        lines[index] = " " * indent + stripped + newline

    path.write_text("".join(lines), encoding="utf-8")
'''

    if "_normalize_validate_product_method(path: Path)" not in source:
        location = source.index(marker)
        source = source[:location] + helper + "\n\n" + source[location:]

    anchor = '''    runtime_signalbench = Path("syntavra_runtime/signalbench.py")
    _normalize_usage_receipt_method(runtime_signalbench)
    for generated in (
'''
    replacement = '''    runtime_signalbench = Path("syntavra_runtime/signalbench.py")
    _normalize_usage_receipt_method(runtime_signalbench)
    _normalize_validate_product_method(runtime_signalbench)
    for generated in (
'''
    if source.count(anchor) != 1:
        raise RuntimeError(f"runtime compile normalizer anchor drift: {source.count(anchor)}")
    source = source.replace(anchor, replacement, 1)
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_validate_product_indentation()


if __name__ == "__main__":
    main()
