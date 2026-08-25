from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "19ea57de85a57107a3b30c7f1ce8f18808d502e8"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-19ea.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_19ea", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench 19ea runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_systematic_runner_indentation() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    marker = "def _compile_generated_python(path: Path) -> None:\n"
    helper = r'''def _normalize_signalbench_runner_ten_space_drift(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    starts = [
        index for index, line in enumerate(lines)
        if line.startswith("class SignalBenchRunner:")
    ]
    ends = [
        index for index, line in enumerate(lines)
        if line.startswith("def load_results(")
    ]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise RuntimeError(
            f"SignalBenchRunner boundary drift: starts={len(starts)} ends={len(ends)}"
        )

    start, end = starts[0], ends[0]
    changed: list[tuple[int, int, str]] = []
    for index in range(start + 1, end):
        line = lines[index]
        if not line.strip():
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise RuntimeError(f"tab indentation in generated SignalBenchRunner at {index + 1}")
        indent = len(line) - len(line.lstrip(" "))
        if indent and indent % 4 == 2:
            stripped = line.lstrip(" ")
            lines[index] = " " * (indent + 10) + stripped
            changed.append((index + 1, indent, stripped.strip()))

    if not changed:
        raise RuntimeError("expected V14 ten-space indentation drift was not found")
    print("normalized V14 SignalBenchRunner indentation drift:")
    for line_no, old_indent, stripped in changed:
        print(f"  line {line_no}: {old_indent}->{old_indent + 10} {stripped[:120]}")

    path.write_text("".join(lines), encoding="utf-8")
'''

    if "_normalize_signalbench_runner_ten_space_drift(path: Path)" not in source:
        location = source.index(marker)
        source = source[:location] + helper + "\n\n" + source[location:]

    anchor = '''    runtime_signalbench = Path("syntavra_runtime/signalbench.py")
    _normalize_usage_receipt_method(runtime_signalbench)
    _normalize_validate_product_method(runtime_signalbench)
    for generated in (
'''
    replacement = '''    runtime_signalbench = Path("syntavra_runtime/signalbench.py")
    _normalize_usage_receipt_method(runtime_signalbench)
    _normalize_validate_product_method(runtime_signalbench)
    _normalize_signalbench_runner_ten_space_drift(runtime_signalbench)
    for generated in (
'''
    if source.count(anchor) != 1:
        raise RuntimeError(f"SignalBenchRunner normalizer anchor drift: {source.count(anchor)}")
    source = source.replace(anchor, replacement, 1)
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_systematic_runner_indentation()


if __name__ == "__main__":
    main()
