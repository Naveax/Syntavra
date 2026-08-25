from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "f1c3b5a9a3f6833461850fdef25a820f59bc214d"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-f1c3.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_f1c3", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench f1c3 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_post_v14_legacy_identity_scope() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    marker = "def _compile_generated_python(path: Path) -> None:\n"
    helper = '''def _repair_legacy_hardened_identity_scope(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    class_start = text.index("class HardenedSignalBench:")
    compare_start = text.index("    @classmethod\\n    def compare(", class_start)
    compare_block = text[compare_start:]

    loop = "            for field in cls.identity_fields:\\n"
    if compare_block.count(loop) != 1:
        raise RuntimeError(
            f"hardened identity loop drift after V14: {compare_block.count(loop)}"
        )
    strict_marker = "            strict_pair_identity = bool(\\n"
    if strict_marker not in compare_block:
        replacement = """            strict_pair_identity = bool(
                cls._value(base, \"usage_receipt_hash\", \"\")
                or cls._value(candidate, \"usage_receipt_hash\", \"\")
            )
            legacy_identity_fields = {
                \"repository_tree\",
                \"prompt_hash\",
                \"verifier_hash\",
                \"permissions_hash\",
                \"cache_mode\",
                \"model\",
                \"reasoning\",
                \"context_window\",
                \"hardware_hash\",
            }
            for field in cls.identity_fields:
                if not strict_pair_identity and field not in legacy_identity_fields:
                    continue
"""
        compare_block = compare_block.replace(loop, replacement, 1)

    required_compatibility = (
        'base_provider = str(cls._value(base, "provider", "") or (base_receipt.provider if base_receipt is not None else ""))',
        'candidate_provider = str(cls._value(candidate, "provider", "") or (candidate_receipt.provider if candidate_receipt is not None else ""))',
        'if bool(cls._value(row, "usage_receipt_hash", "")) and not str(cls._value(row, "arm_version", "")):',
        'strict_row = bool(cls._value(row, "usage_receipt_hash", ""))',
    )
    missing = [item for item in required_compatibility if item not in compare_block]
    if missing:
        raise RuntimeError(f"post-V14 compatibility guards missing: {missing}")

    text = text[:compare_start] + compare_block
    path.write_text(text, encoding="utf-8")
'''

    if "_repair_legacy_hardened_identity_scope(path: Path)" not in source:
        location = source.index(marker)
        source = source[:location] + helper + "\n\n" + source[location:]

    anchor = "    _normalize_signalbench_runner_ten_space_drift(runtime_signalbench)\n    for generated in (\n"
    replacement = "    _normalize_signalbench_runner_ten_space_drift(runtime_signalbench)\n    _repair_legacy_hardened_identity_scope(Path(\"syntavra_runtime/signalbench_hardened.py\"))\n    for generated in (\n"
    if source.count(anchor) != 1:
        raise RuntimeError(f"post-V14 compatibility call anchor drift: {source.count(anchor)}")
    source = source.replace(anchor, replacement, 1)
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_post_v14_legacy_identity_scope()


if __name__ == "__main__":
    main()
