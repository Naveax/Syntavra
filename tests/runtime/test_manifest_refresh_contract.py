from __future__ import annotations

from pathlib import Path

import pytest

from tools.refresh_manifest import is_generated_path


@pytest.mark.parametrize(
    "value",
    [
        "target/.rustc_info.json",
        "target/debug/syntavra",
        "crates/syntavra-cli/target/debug/syntavra",
        "node_modules/package/index.js",
        ".venv/lib/site.py",
        "build/package.whl",
        "dist/package.tar.gz",
        ".git/objects/00/object",
    ],
)
def test_transient_build_paths_are_excluded(value: str) -> None:
    assert is_generated_path(Path(value)) is True


@pytest.mark.parametrize(
    "value",
    [
        "Cargo.toml",
        "crates/syntavra-cli/src/bin/syntavra.rs",
        "syntavra_runtime/engine_entry.py",
        "tests/runtime/test_native_stats_r38.py",
    ],
)
def test_repository_sources_remain_manifest_candidates(value: str) -> None:
    assert is_generated_path(Path(value)) is False
