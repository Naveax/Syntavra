from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "e734054e392a9f44e299d7afb5a4a7f07b5b34f8"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-e734-callsite.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "signalbench_v14r3_previous_e734_callsite",
        PREVIOUS_RUNNER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load SignalBench e734 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_exact_callsite_fix(module) -> None:
    original = module._patch_clean_legacy_provider_billed

    def fixed() -> None:
        original()
        source = module.V14_APPLY.read_text(encoding="utf-8")

        repair_call = (
            '    _repair_legacy_provider_billed_compat('
            'Path("syntavra_runtime/signalbench_hardened.py"))\n'
        )
        diagnostic_call = "    _diagnose_legacy_provider_billed_compat()\n"
        anchor = (
            '    _repair_legacy_hardened_identity_scope('
            'Path("syntavra_runtime/signalbench_hardened.py"))\n'
            "    _diagnose_legacy_hardened_compare()\n"
            "    for generated in (\n"
        )
        replacement = (
            '    _repair_legacy_hardened_identity_scope('
            'Path("syntavra_runtime/signalbench_hardened.py"))\n'
            + repair_call
            + "    _diagnose_legacy_hardened_compare()\n"
            + diagnostic_call
            + "    for generated in (\n"
        )

        repair_count = source.count(repair_call)
        diagnostic_count = source.count(diagnostic_call)
        if repair_count == 0 and diagnostic_count == 0:
            if source.count(anchor) != 1:
                raise RuntimeError(
                    f"post-V14 exact call-site anchor drift: {source.count(anchor)}"
                )
            source = source.replace(anchor, replacement, 1)
        elif repair_count != 1 or diagnostic_count != 1:
            raise RuntimeError(
                "post-V14 compatibility call-site partial/duplicate state: "
                f"repair={repair_count} diagnostic={diagnostic_count}"
            )

        if source.count(repair_call) != 1 or source.count(diagnostic_call) != 1:
            raise RuntimeError(
                "post-V14 compatibility call-site postcondition failed: "
                f"repair={source.count(repair_call)} "
                f"diagnostic={source.count(diagnostic_call)}"
            )

        compile(source, str(module.V14_APPLY), "exec")
        module.V14_APPLY.write_text(source, encoding="utf-8")

    module._patch_clean_legacy_provider_billed = fixed


def main() -> None:
    previous = _load_previous()
    _install_exact_callsite_fix(previous)
    previous.main()


if __name__ == "__main__":
    main()
