from __future__ import annotations

import base64
import importlib.util
import subprocess
import zlib
from pathlib import Path

PREVIOUS_HELPER = "96356dba3d9ea257f131d71dffdfcc50c045ef61"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-96356-cleanpatch.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")
GOOD_HELPER = zlib.decompress(base64.b64decode("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")).decode("utf-8")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "signalbench_v14r3_previous_96356_cleanpatch",
        PREVIOUS_RUNNER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load SignalBench 96356 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_clean_legacy_provider_billed() -> None:
    compile(GOOD_HELPER, "/tmp/signalbench-v14-legacy-provider-helper.py", "exec")
    source = V14_APPLY.read_text(encoding="utf-8")
    marker = "def _compile_generated_python(path: Path) -> None:\n"
    if "_repair_legacy_provider_billed_compat(path)" not in source:
        if source.count(marker) != 1:
            raise RuntimeError(
                f"V14 helper insertion marker drift: {source.count(marker)}"
            )
        location = source.index(marker)
        source = source[:location] + GOOD_HELPER + "\n\n" + source[location:]

    anchor = (
        '    _repair_legacy_hardened_identity_scope(Path("syntavra_runtime/signalbench_hardened.py"))\n'
        '    _diagnose_legacy_hardened_compare()\n'
        '    for generated in (\n'
    )
    replacement = (
        '    _repair_legacy_hardened_identity_scope(Path("syntavra_runtime/signalbench_hardened.py"))\n'
        '    _repair_legacy_provider_billed_compat(Path("syntavra_runtime/signalbench_hardened.py"))\n'
        '    _diagnose_legacy_hardened_compare()\n'
        '    _diagnose_legacy_provider_billed_compat()\n'
        '    for generated in (\n'
    )
    if "_diagnose_legacy_provider_billed_compat()" not in source:
        if source.count(anchor) != 1:
            raise RuntimeError(
                f"post-V14 compatibility call anchor drift: {source.count(anchor)}"
            )
        source = source.replace(anchor, replacement, 1)

    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous._patch_post_v14_legacy_provider_billed = _patch_clean_legacy_provider_billed
    previous.main()


if __name__ == "__main__":
    main()
