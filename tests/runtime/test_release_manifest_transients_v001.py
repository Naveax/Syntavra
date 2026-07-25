from pathlib import Path

from tools.validate import _is_generated_path


def test_release_manifest_ignores_transient_dependency_trees() -> None:
    assert _is_generated_path(
        Path("sdk/typescript/node_modules/typescript/lib/lib.d.ts")
    )
    assert _is_generated_path(
        Path("node_modules/example/index.js")
    )
    assert _is_generated_path(
        Path(".venv/Lib/site-packages/example.py")
    )
    assert _is_generated_path(
        Path("syntavra_runtime/__pycache__/module.pyc")
    )


def test_release_manifest_keeps_real_source_files() -> None:
    assert not _is_generated_path(
        Path("sdk/typescript/src/index.ts")
    )
    assert not _is_generated_path(
        Path("syntavra_runtime/zero_friction.py")
    )
