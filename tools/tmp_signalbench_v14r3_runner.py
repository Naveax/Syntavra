from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "e1597a9051e023cb3e4ac62523a2f70ad1144655"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-e159.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_e159", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench e159 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_runtime_compile_guard() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    marker = "def patch_tests_and_certifier() -> None:\n"
    helper = r'''def _normalize_usage_receipt_method(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    starts = [
        index
        for index, line in enumerate(lines)
        if line.strip().startswith("def usage_receipt(self)")
    ]
    if len(starts) != 1:
        raise RuntimeError(f"usage_receipt method drift: {len(starts)}")
    start = starts[0]
    ends = [
        index
        for index in range(start + 1, len(lines))
        if lines[index].startswith("TASK_FAMILIES = (")
    ]
    if len(ends) != 1:
        raise RuntimeError(f"usage_receipt method end drift: {len(ends)}")
    end = ends[0]

    def rewrite(index: int, indent: int) -> None:
        newline = "\n" if lines[index].endswith("\n") else ""
        lines[index] = " " * indent + lines[index].strip() + newline

    rewrite(start, 4)
    if_matches = [
        index for index in range(start + 1, end)
        if lines[index].strip() == "if not self.provider_observed or not self.usage_receipt_hash:"
    ]
    none_matches = [
        index for index in range(start + 1, end)
        if lines[index].strip() == "return None"
    ]
    ctor_matches = [
        index for index in range(start + 1, end)
        if lines[index].strip() == "return UsageReceipt("
    ]
    if len(if_matches) != 1 or len(none_matches) != 1 or len(ctor_matches) != 1:
        block = "".join(lines[start:end])
        raise RuntimeError(
            "usage_receipt structure drift "
            f"if={len(if_matches)} none={len(none_matches)} ctor={len(ctor_matches)}\n{block}"
        )
    if_index = if_matches[0]
    none_index = none_matches[0]
    ctor_index = ctor_matches[0]
    if not (start < if_index < none_index < ctor_index < end):
        raise RuntimeError("usage_receipt statement ordering drift")

    rewrite(if_index, 8)
    rewrite(none_index, 12)
    rewrite(ctor_index, 8)

    depth = 1
    close_index = None
    for index in range(ctor_index + 1, end):
        stripped = lines[index].strip()
        if not stripped:
            continue
        depth += stripped.count("(") - stripped.count(")")
        if depth <= 0:
            close_index = index
            break
        rewrite(index, 12)
    if close_index is None:
        raise RuntimeError("usage_receipt constructor close drift")
    rewrite(close_index, 8)

    path.write_text("".join(lines), encoding="utf-8")


def _compile_generated_python(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    try:
        compile(text, str(path), "exec")
    except SyntaxError as exc:
        lines = text.splitlines()
        lineno = int(exc.lineno or 1)
        start = max(1, lineno - 8)
        end = min(len(lines), lineno + 8)
        print(f"=== generated syntax context: {path}:{lineno} ===")
        for number in range(start, end + 1):
            print(f"{number:04d}: {lines[number - 1]!r}")
        raise
'''

    if "_normalize_usage_receipt_method(path: Path)" not in source:
        location = source.index(marker)
        source = source[:location] + helper + "\n\n" + source[location:]

    compile_anchor = '''    for generated in (
        test_path,
        certifier_path,
    ):
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")
'''
    replacement = '''    for generated in (
        test_path,
        certifier_path,
    ):
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")

    runtime_signalbench = Path("syntavra_runtime/signalbench.py")
    _normalize_usage_receipt_method(runtime_signalbench)
    for generated in (
        runtime_signalbench,
        Path("syntavra_runtime/signalbench_hardened.py"),
        Path("syntavra_runtime/signalbench_external_adapter.py"),
        Path("syntavra_runtime/usage_receipt_ledger.py"),
        Path("syntavra_runtime/cli.py"),
    ):
        _compile_generated_python(generated)
'''
    if source.count(compile_anchor) != 1:
        raise RuntimeError(f"V14 generated compile anchor drift: {source.count(compile_anchor)}")
    source = source.replace(compile_anchor, replacement, 1)
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_runtime_compile_guard()


if __name__ == "__main__":
    main()
