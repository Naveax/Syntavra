from __future__ import annotations

from pathlib import Path

from syntavra_runtime.agent_config_auditor import (
    MAX_PATH_CANDIDATE_CHARS,
    AgentConfigAuditor,
    _iter_path_candidates,
)


def test_path_candidate_scanner_extracts_precise_relative_paths() -> None:
    assert list(
        _iter_path_candidates(
            "Read ./src/app.py, docs/guide.md; reject invalid//path and /absolute/path."
        )
    ) == ["./src/app.py", "docs/guide.md"]


def test_path_candidate_scanner_skips_oversized_tokens_without_materializing_them() -> None:
    oversized = "root/" + "x" * (MAX_PATH_CANDIDATE_CHARS + 1)
    assert list(_iter_path_candidates(oversized)) == []


def test_agent_config_audit_handles_million_character_adversarial_line(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Read missing/deep/file.py before editing.\n"
        + ("segment/" + "x" * 64) * 14_000
        + "\n",
        encoding="utf-8",
    )

    audit = AgentConfigAuditor(tmp_path).audit()
    stale = [
        finding["message"]
        for finding in audit["findings"]
        if finding["kind"] == "stale-path"
    ]
    assert stale == ["referenced path does not exist: missing/deep/file.py"]
