#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "crates" / "syntavra-cli" / "src" / "native_backup.rs"
TEST = ROOT / "tests" / "runtime" / "test_native_backup_create_r38.py"

EVIDENCE_INITIALIZER = r'''fn decode_evidence_environment_key(value: &str) -> Result<[u8; 32], String> {
    let bytes = if value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        let mut decoded = [0_u8; 32];
        for (index, slot) in decoded.iter_mut().enumerate() {
            let start = index * 2;
            *slot = u8::from_str_radix(&value[start..start + 2], 16)
                .map_err(|error| format!("EVIDENCE_KEY_HEX_INVALID:{error}"))?;
        }
        decoded.to_vec()
    } else {
        let mut padded = value.to_owned();
        while padded.len() % 4 != 0 {
            padded.push('=');
        }
        base64::engine::general_purpose::URL_SAFE
            .decode(padded)
            .map_err(|error| format!("EVIDENCE_KEY_BASE64_INVALID:{error}"))?
    };
    bytes
        .try_into()
        .map_err(|_| "EVIDENCE_KEY_LENGTH_INVALID".to_owned())
}

fn initialize_evidence_state(state_root: &Path) -> Result<(), String> {
    let root = state_root.join("evidence");
    let keys = root.join("keys");
    fs::create_dir_all(root.join("objects"))
        .map_err(|error| format!("EVIDENCE_OBJECT_ROOT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(root.join("metadata"))
        .map_err(|error| format!("EVIDENCE_METADATA_ROOT_CREATE_FAILED:{error}"))?;
    fs::create_dir_all(&keys)
        .map_err(|error| format!("EVIDENCE_KEY_ROOT_CREATE_FAILED:{error}"))?;

    let managed = match env::var("SYNTAVRA_EVIDENCE_KEY") {
        Ok(value) if !value.trim().is_empty() => {
            decode_evidence_environment_key(value.trim())?;
            true
        }
        _ => false,
    };
    let active_path = keys.join("active.json");
    let active_version = if active_path.is_file() {
        let value = serde_json::from_slice::<Value>(
            &fs::read(&active_path)
                .map_err(|error| format!("EVIDENCE_ACTIVE_READ_FAILED:{error}"))?,
        )
        .map_err(|error| format!("EVIDENCE_ACTIVE_INVALID:{error}"))?;
        let version = value["active_version"]
            .as_u64()
            .ok_or_else(|| "EVIDENCE_ACTIVE_VERSION_INVALID".to_owned())?;
        if version == 0 {
            return Err("EVIDENCE_ACTIVE_VERSION_INVALID".to_owned());
        }
        version
    } else {
        let mut payload = serde_json::to_vec(&json!({
            "schema_version": 1,
            "active_version": 1,
        }))
        .map_err(|error| format!("EVIDENCE_ACTIVE_SERIALIZE_FAILED:{error}"))?;
        payload.push(b'\n');
        atomic_write(&active_path, &payload, true)?;
        1
    };
    if !managed {
        let key_path = keys.join(format!("master-v{active_version}.key"));
        if key_path.is_file() {
            if fs::metadata(&key_path)
                .map_err(|error| format!("EVIDENCE_KEY_METADATA_FAILED:{error}"))?
                .len()
                != 32
            {
                return Err("EVIDENCE_KEY_FILE_LENGTH_INVALID".to_owned());
            }
        } else {
            let mut key = [0_u8; 32];
            OsRng.fill_bytes(&mut key);
            atomic_write(&key_path, &key, true)?;
        }
    }

    let index_path = root.join("evidence.sqlite3");
    let connection = Connection::open(&index_path)
        .map_err(|error| format!("EVIDENCE_INDEX_OPEN_FAILED:{error}"))?;
    connection
        .query_row("PRAGMA journal_mode=WAL", [], |row| row.get::<_, String>(0))
        .map_err(|error| format!("EVIDENCE_INDEX_WAL_FAILED:{error}"))?;
    connection
        .execute_batch(
            r#"
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=30000;
            PRAGMA synchronous=FULL;
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS evidence_objects(
                digest TEXT PRIMARY KEY,
                plaintext_bytes INTEGER NOT NULL,
                stored_bytes INTEGER NOT NULL,
                key_version INTEGER NOT NULL,
                created_at REAL NOT NULL,
                last_accessed_at REAL NOT NULL,
                expires_at REAL,
                ref_count INTEGER NOT NULL DEFAULT 0,
                legal_hold INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS evidence_references(
                digest TEXT NOT NULL,
                reference TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(digest, reference),
                FOREIGN KEY(digest) REFERENCES evidence_objects(digest) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS evidence_expiry_idx ON evidence_objects(expires_at);
            COMMIT;
            "#,
        )
        .map_err(|error| format!("EVIDENCE_INDEX_SCHEMA_FAILED:{error}"))?;
    drop(connection);
    set_private(&index_path);
    Ok(())
}

'''
SOURCE_ANCHOR = "fn initialize_roots(state_root: &Path) -> Result<(), String> {\n"
SOURCE_MARKER = "fn initialize_evidence_state(state_root: &Path)"
CALL_ANCHOR = "    initialize_roots(state_root)?;\n"
CALL = "    initialize_evidence_state(state_root)?;\n"

EVIDENCE_CONSTANT = '''EVIDENCE_FILES = {
    "evidence/evidence.sqlite3",
    "evidence/keys/active.json",
    "evidence/keys/master-v1.key",
}
'''
CONSTANT_ANCHOR = 'FIXED_MASTER_KEY = base64.b64encode(bytes(range(32))).decode("ascii")\n'

OLD_COMPARABLE = '''def _comparable_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    rendered = json.loads(json.dumps(manifest, sort_keys=True))
    rendered.pop("created_at", None)
    return rendered
'''
NEW_COMPARABLE = '''def _extract_archive(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r") as handle:
        handle.extractall(destination, filter="data")
    return destination


def _comparable_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for relative, metadata in sorted(manifest["files"].items()):
        suffix = Path(relative).suffix
        if suffix in {".sqlite", ".sqlite3", ".db"}:
            files[relative] = {"logical_sqlite": True}
        elif relative.startswith("evidence/keys/master-v") and relative.endswith(".key"):
            files[relative] = {"secret_key": True, "bytes": metadata["bytes"]}
        else:
            files[relative] = metadata
    return {
        "schema_version": manifest["schema_version"],
        "project_id": manifest["project_id"],
        "files": files,
    }


def _assert_evidence_foundation(extracted: Path) -> None:
    active = extracted / "evidence" / "keys" / "active.json"
    key = extracted / "evidence" / "keys" / "master-v1.key"
    database = extracted / "evidence" / "evidence.sqlite3"
    assert json.loads(active.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "active_version": 1,
    }
    assert key.stat().st_size == 32
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {"evidence_objects", "evidence_references"}
        assert indexes == {"evidence_expiry_idx"}
        assert [
            row[1] for row in connection.execute("PRAGMA table_info(evidence_objects)")
        ] == [
            "digest",
            "plaintext_bytes",
            "stored_bytes",
            "key_version",
            "created_at",
            "last_accessed_at",
            "expires_at",
            "ref_count",
            "legal_hold",
        ]
        assert [
            row[1] for row in connection.execute("PRAGMA table_info(evidence_references)")
        ] == ["digest", "reference", "created_at"]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_list(evidence_references)"))
        assert len(foreign_keys) == 1
        assert foreign_keys[0][2:5] == ("evidence_objects", "digest", "digest")
        assert foreign_keys[0][6].upper() == "CASCADE"
'''

OLD_EMPTY = '''        assert (state / "backups").is_dir()
        assert (state / "backup-keys" / "keys").is_dir()
        values[engine] = value
        manifests[engine] = _comparable_manifest(manifest)
    assert values["rust"]["files"] == values["python"]["files"]
'''
NEW_EMPTY = '''        assert set(manifest["files"]) == EVIDENCE_FILES
        extracted = _extract_archive(destination, tmp_path / f"{engine}-empty-extract")
        _assert_evidence_foundation(extracted)
        assert (state / "backups").is_dir()
        assert (state / "backup-keys" / "keys").is_dir()
        values[engine] = value
        manifests[engine] = _comparable_manifest(manifest)
    assert values["rust"]["files"] == values["python"]["files"] == 3
'''

OLD_POPULATED = '''        assert {
            "config.json",
            "nested/data.txt",
            "runtime.sqlite3",
        }.issubset(manifest["files"])
        extract = tmp_path / f"{engine}-extract"
        with tarfile.open(destination, "r") as handle:
            handle.extractall(extract, filter="data")
        with sqlite3.connect(extract / "runtime.sqlite3") as connection:
'''
NEW_POPULATED = '''        assert set(manifest["files"]) == EVIDENCE_FILES | {
            "config.json",
            "nested/data.txt",
            "runtime.sqlite3",
        }
        extracted = _extract_archive(destination, tmp_path / f"{engine}-extract")
        _assert_evidence_foundation(extracted)
        with sqlite3.connect(extracted / "runtime.sqlite3") as connection:
'''


def replace_once(source: str, old: str, new: str, marker: str, label: str) -> tuple[str, bool]:
    if source.count(marker) == 1:
        return source, False
    if source.count(marker) != 0:
        raise RuntimeError(f"{label} marker count invalid")
    if source.count(old) != 1:
        raise RuntimeError(f"{label} legacy contract must be unique")
    return source.replace(old, new, 1), True


def repair_source() -> bool:
    source = BACKUP.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if rendered.count(SOURCE_MARKER) == 0:
        if rendered.count(SOURCE_ANCHOR) != 1:
            raise RuntimeError("evidence initializer anchor must be unique")
        rendered = rendered.replace(SOURCE_ANCHOR, EVIDENCE_INITIALIZER + SOURCE_ANCHOR, 1)
        changed = True
    elif rendered.count(SOURCE_MARKER) != 1:
        raise RuntimeError("evidence initializer marker count invalid")
    if rendered.count(CALL.strip()) == 0:
        if rendered.count(CALL_ANCHOR) != 1:
            raise RuntimeError("evidence call anchor must be unique")
        rendered = rendered.replace(CALL_ANCHOR, CALL + CALL_ANCHOR, 1)
        changed = True
    elif rendered.count(CALL.strip()) != 1:
        raise RuntimeError("evidence call marker count invalid")
    if changed:
        BACKUP.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def repair_test() -> bool:
    source = TEST.read_text(encoding="utf-8")
    rendered = source
    changed = False
    if rendered.count("EVIDENCE_FILES = {") == 0:
        if rendered.count(CONSTANT_ANCHOR) != 1:
            raise RuntimeError("evidence test constant anchor must be unique")
        rendered = rendered.replace(CONSTANT_ANCHOR, CONSTANT_ANCHOR + EVIDENCE_CONSTANT, 1)
        changed = True
    if 'environment.pop("SYNTAVRA_EVIDENCE_KEY", None)' not in rendered:
        anchor = '    environment["PYTHONUTF8"] = "1"\n'
        if rendered.count(anchor) != 1:
            raise RuntimeError("evidence environment anchor must be unique")
        rendered = rendered.replace(
            anchor,
            anchor + '    environment.pop("SYNTAVRA_EVIDENCE_KEY", None)\n',
            1,
        )
        changed = True
    for old, new, marker, label in (
        (OLD_COMPARABLE, NEW_COMPARABLE, "def _assert_evidence_foundation", "logical manifest parity"),
        (OLD_EMPTY, NEW_EMPTY, '== values["python"]["files"] == 3', "empty evidence parity"),
        (OLD_POPULATED, NEW_POPULATED, "_assert_evidence_foundation(extracted)", "populated evidence parity"),
        ('        assert value["files"] >= 3\n', '        assert value["files"] == 6\n', 'assert value["files"] == 6', "encrypted evidence count"),
    ):
        rendered, applied = replace_once(rendered, old, new, marker, label)
        changed = changed or applied
    if changed:
        TEST.write_text(rendered, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    source_changed = repair_source()
    test_changed = repair_test()
    print(
        json.dumps(
            {
                "changed": source_changed or test_changed,
                "ok": True,
                "source_changed": source_changed,
                "surface": "native-backup-evidence-parity",
                "test_changed": test_changed,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
