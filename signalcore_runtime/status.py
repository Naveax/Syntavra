from __future__ import annotations

import shutil
import time
from pathlib import Path

from .bootstrap import runtime_health


def inspect_runtime(*, project_root: Path, skill_root: Path, state_root: Path, codex_home: Path, host: str = "codex", require_rollout: bool = False):
    return runtime_health(project=project_root, skill_root=skill_root, state_root=state_root, codex_home=codex_home, host=host, require_rollout=require_rollout)


def backup_and_repair(source: Path, destination: Path) -> dict:
    if not source.exists():
        raise FileNotFoundError(source)
    backup = destination / f"backup-{int(time.time())}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir(): shutil.copytree(source, backup)
    else: shutil.copy2(source, backup)
    return {"backup": str(backup), "repaired": False, "reason": "backup-created; automatic destructive repair intentionally disabled"}
