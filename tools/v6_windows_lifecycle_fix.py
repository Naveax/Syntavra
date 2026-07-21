from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path.cwd()
TEMPORARY = {
    ".github/workflows/v6-windows-fix-blobs.yml",
    "tools/v6_windows_lifecycle_fix.py",
}
GENERATED = {
    "fusion-release-smoke.json",
    "release-smoke.json",
    "platform-registry.json",
    "native-dry-run.json",
}
EXPECTED_BLOBS = {
    "signalcore_runtime/evidence.py": "72febdd5e40af28ffac3075c96ceb3fde717f96c",
    "tools/validate.py": "80b9dd27390fb3b337dcce8e59951ebf3c5a0610",
    "MANIFEST.sha256": "12374739d96eda9901eda0ed4a0257509b1d05a9",
}


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def generated_path(relative: Path) -> bool:
    parts = relative.parts
    return (
        bool(parts) and parts[0] in {".git", ".signalcore", "build", "dist"}
    ) or any(
        part in {"__pycache__", ".pytest_cache"} or part.endswith(".egg-info")
        for part in parts
    )


def patch_evidence() -> None:
    path = ROOT / "signalcore_runtime/evidence.py"
    text = path.read_text(encoding="utf-8")
    old = '''    def stats(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) objects,COALESCE(SUM(plaintext_bytes),0) plaintext_bytes,COALESCE(SUM(stored_bytes),0) stored_bytes,COALESCE(SUM(ref_count),0) AS [references] FROM evidence_objects"
            ).fetchone()
            expired = db.execute(
                "SELECT COUNT(*) FROM evidence_objects WHERE expires_at IS NOT NULL AND expires_at<=? AND ref_count=0 AND legal_hold=0",
                (time.time(),),
            ).fetchone()[0]
        return {**dict(row), "collectable": int(expired), "encrypted": True, "active_key_version": self.keys.active_version}
'''
    new = '''    def stats(self) -> dict[str, Any]:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT COUNT(*) objects,COALESCE(SUM(plaintext_bytes),0) plaintext_bytes,COALESCE(SUM(stored_bytes),0) stored_bytes,COALESCE(SUM(ref_count),0) AS [references] FROM evidence_objects"
            ).fetchone()
            expired = db.execute(
                "SELECT COUNT(*) FROM evidence_objects WHERE expires_at IS NOT NULL AND expires_at<=? AND ref_count=0 AND legal_hold=0",
                (time.time(),),
            ).fetchone()[0]
        finally:
            db.close()
        return {**dict(row), "collectable": int(expired), "encrypted": True, "active_key_version": self.keys.active_version}
'''
    if old not in text:
        raise SystemExit("evidence stats lifecycle pattern not found")
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def patch_validator() -> None:
    path = ROOT / "tools/validate.py"
    text = path.read_text(encoding="utf-8")
    old = '''def _is_generated_path(relative: Path) -> bool:
    return any(
        part in {".git", ".signalcore", "__pycache__", ".pytest_cache", "build", "dist"}
        or part.endswith(".egg-info")
        for part in relative.parts
    )
'''
    new = '''def _is_generated_path(relative: Path) -> bool:
    parts = relative.parts
    return (
        bool(parts) and parts[0] in {".git", ".signalcore", "build", "dist"}
    ) or any(
        part in {"__pycache__", ".pytest_cache"} or part.endswith(".egg-info")
        for part in parts
    )
'''
    if old not in text:
        raise SystemExit("generated path integrity pattern not found")
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def regenerate_manifest() -> None:
    candidates: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if generated_path(relative):
            continue
        if relative.as_posix() in TEMPORARY:
            continue
        if (path.name == "MANIFEST.sha256" and path.parent == ROOT) or path.name in GENERATED or path.suffix == ".pyc":
            continue
        candidates.append(path)
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(ROOT).as_posix()}"
        for path in sorted(candidates, key=lambda value: value.relative_to(ROOT).as_posix())
    ]
    (ROOT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def verify() -> None:
    for relative, expected in EXPECTED_BLOBS.items():
        data = (ROOT / relative).read_bytes()
        actual = git_blob_sha(data)
        if actual != expected:
            raise SystemExit(f"blob mismatch for {relative}: expected={expected} actual={actual}")
    print({"blobs": EXPECTED_BLOBS, "expected_tree": "fd030709b17f861e8e69bb6558ccab403918d424"})


if __name__ == "__main__":
    patch_evidence()
    patch_validator()
    regenerate_manifest()
    verify()
