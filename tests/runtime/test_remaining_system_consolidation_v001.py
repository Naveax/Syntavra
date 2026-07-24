from __future__ import annotations

from pathlib import Path

from syntavra_runtime.code_intelligence import CodeIntelligenceIndex
from syntavra_runtime.competitive_fabric import StructuralNavigator
from syntavra_runtime.context_pack import TaskContextAssembler
from syntavra_runtime.optimization_modes import MODES, normalize_mode
from syntavra_runtime.structural import StructuralIndex


def _project(root: Path) -> None:
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "auth.py").write_text(
        "def refresh_token(value):\n    return value.strip()\n\n"
        "def login(value):\n    return refresh_token(value)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_auth.py").write_text(
        "from src.auth import refresh_token\n\ndef test_refresh():\n    assert refresh_token(' a ') == 'a'\n",
        encoding="utf-8",
    )


def test_structural_path_update_and_code_intelligence_share_one_sqlite_graph(tmp_path: Path) -> None:
    _project(tmp_path)
    state = tmp_path / ".syntavra" / "structural.sqlite3"
    intelligence = CodeIntelligenceIndex(tmp_path, state_path=state)
    intelligence.build_incremental(state)
    assert state.is_file()
    assert not (state.parent / "code-intelligence-index.json").exists()

    (tmp_path / "src" / "auth.py").write_text(
        "def rotate_token(value):\n    return value.strip()\n",
        encoding="utf-8",
    )
    intelligence.refresh_paths(["src/auth.py"], cache_path=state)
    assert intelligence.last_build_stats["mode"] == "path-incremental"
    assert intelligence.last_build_stats["parsed_files"] == 1
    assert intelligence.resolve("rotate_token")
    assert not intelligence.resolve("refresh_token")


def test_natural_language_context_pack_is_hard_capped_at_1500_tokens(tmp_path: Path) -> None:
    _project(tmp_path)
    index = StructuralIndex(tmp_path / ".syntavra" / "structural.sqlite3", repository_root=tmp_path, repository_id="repo")
    pack = TaskContextAssembler(index, StructuralNavigator(tmp_path)).assemble(
        "fix login token refresh behavior and update its tests",
        token_budget=8_000,
    )
    assert pack.budget_tokens == 1_500
    assert pack.used_tokens <= 1_500
    assert any("refresh_token" in seed or "login" in seed for seed in pack.seed_symbols)
    assert "src/auth.py" in pack.affected_paths


def test_codex_ultra_is_a_real_alias_with_1500_context_budget() -> None:
    assert normalize_mode("codex-ultra") == "ultra"
    assert MODES["ultra"].context_budget_tokens == 1_500
    assert MODES["ultra"].schema_profile == "minimal"
