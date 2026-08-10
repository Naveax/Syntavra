#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rand::{rngs::OsRng, RngCore as _};
use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Map, Value};
use sha2::{Digest as _, Sha256};

const SESSION_VIEWS: &[&str] = &[
    "task", "decision", "change", "failure", "security", "dependency", "repository", "test",
    "provider", "handoff",
];

pub(crate) fn supports(command: &[String]) -> bool {
    command.len() == 2
        && command[0] == "run"
        && matches!(
            command[1].as_str(),
            "memory-add"
                | "memory-extract"
                | "memory-search"
                | "memory-export"
                | "memory-backfill"
                | "memory-intelligence-status"
                | "memory-open"
                | "memory-append"
                | "memory-compact"
                | "memory-retrieve"
                | "memory-checkpoint"
                | "memory-fork"
                | "memory-merge"
                | "memory-restore"
                | "memory-verify"
        )
}

fn sha256_bytes(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

fn sorted(value: &Value) -> Value {
    match value {
        Value::Array(rows) => Value::Array(rows.iter().map(sorted).collect()),
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                output.insert(key.clone(), sorted(&map[key]));
            }
            Value::Object(output)
        }
        _ => value.clone(),
    }
}

fn canonical_json(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&sorted(value)).map_err(|error| format!("MEMORY_JSON_FAILED:{error}"))
}

fn now_seconds() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("MEMORY_CLOCK_FAILED:{error}"))
}

fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = if shifted >= 0 { shifted } else { shifted - 146_096 } / 146_097;
    let day_of_era = shifted - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    let year = year + if month <= 2 { 1 } else { 0 };
    (year, month as u32, day as u32)
}

fn now_iso() -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("MEMORY_CLOCK_FAILED:{error}"))?;
    let seconds = i64::try_from(duration.as_secs()).map_err(|_| "MEMORY_CLOCK_RANGE".to_owned())?;
    let days = seconds / 86_400;
    let second_of_day = seconds % 86_400;
    let (year, month, day) = civil_from_days(days);
    let hour = second_of_day / 3_600;
    let minute = (second_of_day % 3_600) / 60;
    let second = second_of_day % 60;
    Ok(format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{:06}Z",
        duration.subsec_micros()
    ))
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut value = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let found = if item == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            item.strip_prefix(flag)
                .and_then(|tail| tail.strip_prefix('='))
                .map(str::to_owned)
        };
        if let Some(found) = found {
            if value.is_some() {
                return Err(format!("{flag}_DUPLICATE"));
            }
            value = Some(found);
        }
        index += 1;
    }
    Ok(value)
}

fn repeated_values(arguments: &[String], flag: &str) -> Result<Vec<String>, String> {
    let mut values = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        if item == flag {
            index += 1;
            values.push(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = item
            .strip_prefix(flag)
            .and_then(|tail| tail.strip_prefix('='))
        {
            values.push(value.to_owned());
        }
        index += 1;
    }
    Ok(values)
}

fn action_index(arguments: &[String], action: &str) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == action)
        .map(|index| index + 1)
        .ok_or_else(|| format!("MEMORY_ACTION_NOT_FOUND:{action}"))
}

fn positional_after_action(arguments: &[String], action: &str) -> Result<Vec<String>, String> {
    let start = action_index(arguments, action)? + 1;
    let flags_with_values = [
        "--kind", "--importance", "--confidence", "--limit", "--session-id", "--parent",
        "--metadata", "--view", "--label",
    ];
    let mut output = Vec::new();
    let mut index = start;
    while index < arguments.len() {
        let value = &arguments[index];
        if flags_with_values.contains(&value.as_str()) {
            index += 2;
            continue;
        }
        if value.starts_with("--") {
            index += 1;
            continue;
        }
        output.push(value.clone());
        index += 1;
    }
    Ok(output)
}

fn read_text_or_path(value: &str) -> Result<String, String> {
    let path = Path::new(value);
    if path.is_file() {
        fs::read_to_string(path).map_err(|error| format!("MEMORY_SOURCE_READ_FAILED:{error}"))
    } else {
        Ok(value.to_owned())
    }
}

fn read_json_or_inline(value: &str) -> Result<Value, String> {
    let path = Path::new(value);
    let raw = if path.is_file() {
        fs::read_to_string(path).map_err(|error| format!("MEMORY_JSON_READ_FAILED:{error}"))?
    } else {
        value.to_owned()
    };
    serde_json::from_str(&raw).map_err(|error| format!("MEMORY_JSON_INVALID:{error}"))
}

fn open_db(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("MEMORY_DB_PARENT_FAILED:{error}"))?;
    }
    let connection = Connection::open(path).map_err(|error| format!("MEMORY_DB_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL; PRAGMA busy_timeout=30000;")
        .map_err(|error| format!("MEMORY_DB_PRAGMA_FAILED:{error}"))?;
    Ok(connection)
}

// ---------- MemoryIntelligenceStore ----------

#[derive(Clone)]
struct Observation {
    observation_id: String,
    text: String,
    kind: String,
    importance: f64,
    confidence: f64,
    validity: f64,
    reuse_count: i64,
    success_count: i64,
    failure_count: i64,
    created_at: f64,
    updated_at: f64,
    source_hash: String,
    metadata: Value,
}

impl Observation {
    fn roi(&self) -> f64 {
        let evidence = (self.success_count + 1) as f64 / (self.failure_count + 1) as f64;
        self.importance
            * self.confidence
            * self.validity
            * evidence
            * (2.0 + self.reuse_count as f64).log2()
    }

    fn value(&self, include_roi: bool) -> Value {
        let mut value = json!({
            "observation_id": self.observation_id,
            "text": self.text,
            "kind": self.kind,
            "importance": self.importance,
            "confidence": self.confidence,
            "validity": self.validity,
            "reuse_count": self.reuse_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_hash": self.source_hash,
            "metadata": self.metadata,
        });
        if include_roi {
            value.as_object_mut().expect("object").insert("roi".to_owned(), Value::from(self.roi()));
        }
        value
    }
}

fn init_intelligence(path: &Path) -> Result<Connection, String> {
    let db = open_db(path)?;
    db.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS observations(
          observation_id TEXT PRIMARY KEY,text TEXT NOT NULL,kind TEXT NOT NULL,
          importance REAL NOT NULL,confidence REAL NOT NULL,validity REAL NOT NULL,
          reuse_count INTEGER NOT NULL DEFAULT 0,success_count INTEGER NOT NULL DEFAULT 0,
          failure_count INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL,
          source_hash TEXT NOT NULL,metadata_json TEXT NOT NULL,embedding_json TEXT
        );
        CREATE INDEX IF NOT EXISTS observations_kind_idx ON observations(kind);
        "#,
    )
    .map_err(|error| format!("MEMORY_INTELLIGENCE_SCHEMA_FAILED:{error}"))?;
    Ok(db)
}

fn observation_from_row(row: &Row<'_>) -> rusqlite::Result<Observation> {
    let metadata_raw: String = row.get("metadata_json")?;
    let metadata = serde_json::from_str(&metadata_raw).unwrap_or_else(|_| json!({}));
    Ok(Observation {
        observation_id: row.get("observation_id")?,
        text: row.get("text")?,
        kind: row.get("kind")?,
        importance: row.get("importance")?,
        confidence: row.get("confidence")?,
        validity: row.get("validity")?,
        reuse_count: row.get("reuse_count")?,
        success_count: row.get("success_count")?,
        failure_count: row.get("failure_count")?,
        created_at: row.get("created_at")?,
        updated_at: row.get("updated_at")?,
        source_hash: row.get("source_hash")?,
        metadata,
    })
}

fn token_regex() -> Result<Regex, String> {
    Regex::new(r"[\w.-]+").map_err(|error| format!("MEMORY_TOKEN_REGEX_FAILED:{error}"))
}

fn tokens(text: &str) -> Result<Vec<String>, String> {
    let regex = token_regex()?;
    Ok(regex.find_iter(text).map(|item| item.as_str().to_lowercase()).collect())
}

fn embedding(text: &str) -> Result<Vec<f64>, String> {
    let mut values = vec![0.0_f64; 128];
    for token in tokens(text)? {
        let digest = Sha256::digest(token.as_bytes());
        let index = u32::from_be_bytes([digest[0], digest[1], digest[2], digest[3]]) as usize % 128;
        let sign = if digest[4] & 1 == 1 { -1.0 } else { 1.0 };
        let length = token.chars().count() as f64;
        values[index] += sign * (1.0 + length.ln_1p());
    }
    let norm = values.iter().map(|value| value * value).sum::<f64>().sqrt();
    let norm = if norm == 0.0 { 1.0 } else { norm };
    for value in &mut values {
        *value /= norm;
    }
    Ok(values)
}

fn embedding_json(text: &str) -> Result<String, String> {
    serde_json::to_string(&embedding(text)?).map_err(|error| format!("MEMORY_EMBED_SERIALIZE_FAILED:{error}"))
}

fn metadata_json(value: &Value) -> Result<String, String> {
    serde_json::to_string(&sorted(value)).map_err(|error| format!("MEMORY_METADATA_SERIALIZE_FAILED:{error}"))
}

fn add_observation(
    path: &Path,
    text: &str,
    kind: &str,
    importance: f64,
    confidence: f64,
    validity: f64,
    metadata: &Value,
    embed: bool,
) -> Result<Observation, String> {
    let clean = text.trim();
    if clean.is_empty() {
        return Err("memory text is required".to_owned());
    }
    let now = now_seconds()?;
    let source_hash = sha256_bytes(clean.as_bytes());
    let observation_id = sha256_bytes(&canonical_json(&json!({
        "text": clean,
        "kind": kind,
        "source_hash": source_hash,
    }))?);
    let embed_json = if embed { Some(embedding_json(text)?) } else { None };
    let db = init_intelligence(path)?;
    db.execute(
        r#"
        INSERT INTO observations(
          observation_id,text,kind,importance,confidence,validity,reuse_count,success_count,
          failure_count,created_at,updated_at,source_hash,metadata_json,embedding_json
        ) VALUES(?1,?2,?3,?4,?5,?6,0,0,0,?7,?8,?9,?10,?11)
        ON CONFLICT(observation_id) DO UPDATE SET
          importance=MAX(observations.importance,excluded.importance),
          confidence=MAX(observations.confidence,excluded.confidence),
          validity=MAX(observations.validity,excluded.validity),
          updated_at=excluded.updated_at,
          metadata_json=excluded.metadata_json,
          embedding_json=COALESCE(observations.embedding_json,excluded.embedding_json)
        "#,
        params![
            observation_id,
            clean,
            kind,
            importance.clamp(0.0, 1.0),
            confidence.clamp(0.0, 1.0),
            validity.clamp(0.0, 1.0),
            now,
            now,
            source_hash,
            metadata_json(metadata)?,
            embed_json,
        ],
    )
    .map_err(|error| format!("MEMORY_INTELLIGENCE_ADD_FAILED:{error}"))?;
    db.query_row(
        "SELECT * FROM observations WHERE observation_id=?1",
        [observation_id],
        observation_from_row,
    )
    .map_err(|error| format!("MEMORY_INTELLIGENCE_READ_FAILED:{error}"))
}

fn all_observations(path: &Path) -> Result<Vec<(Observation, Option<String>)>, String> {
    let db = init_intelligence(path)?;
    let mut statement = db
        .prepare("SELECT * FROM observations")
        .map_err(|error| format!("MEMORY_INTELLIGENCE_QUERY_PREPARE:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            let item = observation_from_row(row)?;
            let embedding: Option<String> = row.get("embedding_json")?;
            Ok((item, embedding))
        })
        .map_err(|error| format!("MEMORY_INTELLIGENCE_QUERY_FAILED:{error}"))?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|error| format!("MEMORY_INTELLIGENCE_ROW_FAILED:{error}"))
}

fn intelligence_search(path: &Path, query: &str, limit: usize) -> Result<Value, String> {
    let query_tokens = tokens(query)?;
    let query_embedding = embedding(query)?;
    let rows = all_observations(path)?;
    let docs = rows
        .iter()
        .map(|(row, _)| tokens(&row.text))
        .collect::<Result<Vec<_>, _>>()?;
    let n = docs.len().max(1) as f64;
    let mut df = HashMap::<String, usize>::new();
    for term in query_tokens.iter().cloned().collect::<BTreeSet<_>>() {
        let count = docs.iter().filter(|doc| doc.contains(&term)).count();
        df.insert(term, count);
    }
    let average = if docs.is_empty() {
        1.0
    } else {
        docs.iter().map(Vec::len).sum::<usize>() as f64 / n
    };
    let mut results = Vec::<(f64, String, Value)>::new();
    for ((item, embedding_raw), doc) in rows.into_iter().zip(docs.into_iter()) {
        if item.validity <= 0.0 {
            continue;
        }
        let length = doc.len().max(1) as f64;
        let mut counts = HashMap::<String, usize>::new();
        for term in query_tokens.iter().cloned().collect::<BTreeSet<_>>() {
            counts.insert(term.clone(), doc.iter().filter(|value| **value == term).count());
        }
        let mut bm25 = 0.0;
        for term in &query_tokens {
            let tf = *counts.get(term).unwrap_or(&0) as f64;
            if tf == 0.0 {
                continue;
            }
            let dfi = *df.get(term).unwrap_or(&0) as f64;
            let idf = (1.0 + (n - dfi + 0.5) / (dfi + 0.5)).ln();
            bm25 += idf * (tf * 2.2) / (tf + 1.2 * (1.0 - 0.75 + 0.75 * length / average.max(1.0)));
        }
        let candidate_embedding = match embedding_raw {
            Some(raw) => serde_json::from_str::<Vec<f64>>(&raw).unwrap_or(embedding(&item.text)?),
            None => embedding(&item.text)?,
        };
        let cosine = query_embedding
            .iter()
            .zip(candidate_embedding.iter())
            .map(|(left, right)| left * right)
            .sum::<f64>();
        let score = bm25 * 4.0 + cosine * 25.0 + item.roi() * 5.0;
        if score > 0.0 {
            let id = item.observation_id.clone();
            results.push((
                score,
                id,
                json!({
                    "observation": item.value(true),
                    "bm25": bm25,
                    "cosine": cosine,
                    "score": score,
                }),
            ));
        }
    }
    results.sort_by(|left, right| {
        right
            .0
            .partial_cmp(&left.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.1.cmp(&right.1))
    });
    Ok(Value::Array(
        results
            .into_iter()
            .take(limit.max(1))
            .map(|(_, _, value)| value)
            .collect(),
    ))
}

fn ranked_observations(path: &Path, limit: usize) -> Result<Vec<Value>, String> {
    let mut rows = all_observations(path)?
        .into_iter()
        .map(|(row, _)| row)
        .collect::<Vec<_>>();
    rows.sort_by(|left, right| {
        right
            .roi()
            .partial_cmp(&left.roi())
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.observation_id.cmp(&right.observation_id))
    });
    Ok(rows.into_iter().take(limit).map(|row| row.value(true)).collect())
}

fn intelligence_stats(path: &Path) -> Result<Value, String> {
    let db = init_intelligence(path)?;
    let observations: i64 = db
        .query_row("SELECT COUNT(*) FROM observations", [], |row| row.get(0))
        .map_err(|error| format!("MEMORY_STATS_FAILED:{error}"))?;
    let valid: i64 = db
        .query_row("SELECT COUNT(*) FROM observations WHERE validity>0", [], |row| row.get(0))
        .map_err(|error| format!("MEMORY_STATS_FAILED:{error}"))?;
    let missing: i64 = db
        .query_row("SELECT COUNT(*) FROM observations WHERE embedding_json IS NULL", [], |row| row.get(0))
        .map_err(|error| format!("MEMORY_STATS_FAILED:{error}"))?;
    Ok(json!({"observations": observations, "valid": valid, "missing_embeddings": missing}))
}

fn memory_intelligence(action: &str, arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let db_path = state_root.join("memory-intelligence.sqlite3");
    match action {
        "memory-add" => {
            let positional = positional_after_action(arguments, action)?;
            let text = positional.first().ok_or_else(|| "memory text is required".to_owned())?;
            let kind = option_value(arguments, "--kind")?.unwrap_or_else(|| "observation".to_owned());
            let importance = option_value(arguments, "--importance")?
                .map(|value| value.parse::<f64>().map_err(|_| "MEMORY_IMPORTANCE_INVALID".to_owned()))
                .transpose()?
                .unwrap_or(0.5);
            let confidence = option_value(arguments, "--confidence")?
                .map(|value| value.parse::<f64>().map_err(|_| "MEMORY_CONFIDENCE_INVALID".to_owned()))
                .transpose()?
                .unwrap_or(0.7);
            Ok(add_observation(&db_path, text, &kind, importance, confidence, 1.0, &json!({}), true)?.value(false))
        }
        "memory-extract" => {
            let positional = positional_after_action(arguments, action)?;
            let source = positional.first().ok_or_else(|| "MEMORY_SOURCE_REQUIRED".to_owned())?;
            let text = read_text_or_path(source)?;
            let patterns = [
                ("decision", r"(?im)^\s*(?:decision|decided|we will|keep|use)\s*[:-]?\s*(.+)$", 0.8),
                ("failure", r"(?im)^\s*(?:root cause|failure|error cause)\s*[:-]?\s*(.+)$", 0.75),
                ("constraint", r"(?im)^\s*(?:constraint|must|never)\s*[:-]?\s*(.+)$", 0.85),
                ("preference", r"(?im)^\s*(?:preference|prefer)\s*[:-]?\s*(.+)$", 0.65),
            ];
            let mut observations = Vec::new();
            for (kind, pattern, importance) in patterns {
                let regex = Regex::new(pattern).map_err(|error| format!("MEMORY_EXTRACT_REGEX:{error}"))?;
                for capture in regex.captures_iter(&text) {
                    let Some(found) = capture.get(1) else { continue };
                    let value = found.as_str().trim();
                    if value.is_empty() { continue; }
                    observations.push(
                        add_observation(
                            &db_path,
                            value,
                            kind,
                            importance,
                            0.65,
                            1.0,
                            &json!({"extraction": "heuristic"}),
                            true,
                        )?
                        .value(false),
                    );
                }
            }
            Ok(json!({"observations": observations}))
        }
        "memory-search" => {
            let positional = positional_after_action(arguments, action)?;
            let query = positional.first().ok_or_else(|| "MEMORY_QUERY_REQUIRED".to_owned())?;
            let limit = option_value(arguments, "--limit")?
                .map(|value| value.parse::<usize>().map_err(|_| "MEMORY_LIMIT_INVALID".to_owned()))
                .transpose()?
                .unwrap_or(20);
            Ok(json!({"results": intelligence_search(&db_path, query, limit)?}))
        }
        "memory-export" => {
            let positional = positional_after_action(arguments, action)?;
            let target = PathBuf::from(positional.first().ok_or_else(|| "MEMORY_EXPORT_PATH_REQUIRED".to_owned())?);
            let rows = ranked_observations(&db_path, 1_000_000)?;
            if let Some(parent) = target.parent().filter(|value| !value.as_os_str().is_empty()) {
                fs::create_dir_all(parent).map_err(|error| format!("MEMORY_EXPORT_PARENT_FAILED:{error}"))?;
            }
            let mut output = OpenOptions::new()
                .create(true)
                .truncate(true)
                .write(true)
                .open(&target)
                .map_err(|error| format!("MEMORY_EXPORT_OPEN_FAILED:{error}"))?;
            for row in &rows {
                let mut line = canonical_json(row)?;
                line.push(b'\n');
                output.write_all(&line).map_err(|error| format!("MEMORY_EXPORT_WRITE_FAILED:{error}"))?;
            }
            output.flush().map_err(|error| format!("MEMORY_EXPORT_FLUSH_FAILED:{error}"))?;
            let raw = fs::read(&target).map_err(|error| format!("MEMORY_EXPORT_READBACK_FAILED:{error}"))?;
            Ok(json!({"path": target.to_string_lossy(), "observations": rows.len(), "sha256": sha256_bytes(&raw)}))
        }
        "memory-backfill" => {
            let limit = option_value(arguments, "--limit")?
                .map(|value| value.parse::<usize>().map_err(|_| "MEMORY_LIMIT_INVALID".to_owned()))
                .transpose()?
                .unwrap_or(1000);
            let db = init_intelligence(&db_path)?;
            let mut statement = db
                .prepare("SELECT observation_id,text FROM observations WHERE embedding_json IS NULL LIMIT ?1")
                .map_err(|error| format!("MEMORY_BACKFILL_PREPARE:{error}"))?;
            let rows = statement
                .query_map([i64::try_from(limit).unwrap_or(i64::MAX)], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
                .map_err(|error| format!("MEMORY_BACKFILL_QUERY:{error}"))?
                .collect::<rusqlite::Result<Vec<_>>>()
                .map_err(|error| format!("MEMORY_BACKFILL_ROWS:{error}"))?;
            drop(statement);
            for (id, text) in &rows {
                db.execute(
                    "UPDATE observations SET embedding_json=?1,updated_at=?2 WHERE observation_id=?3",
                    params![embedding_json(text)?, now_seconds()?, id],
                )
                .map_err(|error| format!("MEMORY_BACKFILL_UPDATE:{error}"))?;
            }
            let remaining = intelligence_stats(&db_path)?["missing_embeddings"].clone();
            Ok(json!({"embedded": rows.len(), "remaining": remaining}))
        }
        "memory-intelligence-status" => Ok(json!({
            "stats": intelligence_stats(&db_path)?,
            "ranked": ranked_observations(&db_path, 100)?,
        })),
        _ => Err(format!("MEMORY_INTELLIGENCE_UNSUPPORTED:{action}")),
    }
}

// ---------- SessionMemory ----------

fn init_session(path: &Path) -> Result<Connection, String> {
    let db = open_db(path)?;
    db.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, state TEXT NOT NULL,
            parents_json TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(session_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS summaries (
            summary_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, view TEXT NOT NULL,
            source_sequences_json TEXT NOT NULL, parent_summaries_json TEXT NOT NULL,
            summary_text TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_summary_session_view ON summaries(session_id, view, created_at);
        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, label TEXT NOT NULL,
            sequence INTEGER NOT NULL, event_hash TEXT NOT NULL, created_at TEXT NOT NULL
        );
        "#,
    )
    .map_err(|error| format!("SESSION_MEMORY_SCHEMA_FAILED:{error}"))?;
    Ok(db)
}

fn random_session_id() -> String {
    let mut bytes = [0u8; 12];
    OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn session_project_id(project_root: &Path) -> String {
    sha256_bytes(project_root.to_string_lossy().as_bytes())
}

fn open_session(
    path: &Path,
    project_root: &Path,
    requested_id: Option<String>,
    parents: &[String],
    metadata: &Value,
) -> Result<Value, String> {
    let session_id = requested_id.filter(|value| !value.is_empty()).unwrap_or_else(random_session_id);
    let now = now_iso()?;
    let db = init_session(path)?;
    let existing = db
        .query_row(
            "SELECT session_id,project_id,state,parents_json,metadata_json,created_at,updated_at FROM sessions WHERE session_id=?1",
            [&session_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?, row.get::<_, String>(4)?, row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("SESSION_MEMORY_OPEN_QUERY:{error}"))?;
    if let Some((id, project_id, state, parents_json, metadata_json, created_at, updated_at)) = existing {
        return Ok(json!({
            "session_id": id,
            "project_id": project_id,
            "state": state,
            "parents_json": parents_json,
            "metadata_json": metadata_json,
            "created_at": created_at,
            "updated_at": updated_at,
            "parents": serde_json::from_str::<Value>(&parents_json).unwrap_or_else(|_| json!([])),
            "metadata": serde_json::from_str::<Value>(&metadata_json).unwrap_or_else(|_| json!({})),
            "restored": true,
        }));
    }
    for parent in parents {
        let exists: Option<i64> = db
            .query_row("SELECT 1 FROM sessions WHERE session_id=?1", [parent], |row| row.get(0))
            .optional()
            .map_err(|error| format!("SESSION_PARENT_QUERY:{error}"))?;
        if exists.is_none() {
            return Err(format!("parent session not found: {parent}"));
        }
    }
    let project_id = session_project_id(project_root);
    let parents_json = serde_json::to_string(parents).map_err(|error| format!("SESSION_PARENTS_JSON:{error}"))?;
    let metadata_json = serde_json::to_string(&sorted(metadata)).map_err(|error| format!("SESSION_METADATA_JSON:{error}"))?;
    db.execute(
        "INSERT INTO sessions VALUES(?1,?2,'ACTIVE',?3,?4,?5,?6)",
        params![session_id, project_id, parents_json, metadata_json, now, now],
    )
    .map_err(|error| format!("SESSION_OPEN_INSERT:{error}"))?;
    Ok(json!({
        "session_id": session_id,
        "project_id": project_id,
        "state": "ACTIVE",
        "parents": parents,
        "metadata": metadata,
        "created_at": now,
        "updated_at": now,
        "restored": false,
    }))
}

fn session_events(path: &Path, session_id: &str) -> Result<Vec<Value>, String> {
    let db = init_session(path)?;
    let mut statement = db
        .prepare("SELECT session_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at FROM events WHERE session_id=?1 ORDER BY sequence")
        .map_err(|error| format!("SESSION_EVENTS_PREPARE:{error}"))?;
    let rows = statement
        .query_map([session_id], |row| {
            let payload_raw: String = row.get(3)?;
            let payload = serde_json::from_str::<Value>(&payload_raw).unwrap_or_else(|_| json!({}));
            Ok(json!({
                "session_id": row.get::<_, String>(0)?,
                "sequence": row.get::<_, i64>(1)?,
                "event_type": row.get::<_, String>(2)?,
                "payload_json": payload_raw,
                "previous_hash": row.get::<_, String>(4)?,
                "event_hash": row.get::<_, String>(5)?,
                "created_at": row.get::<_, String>(6)?,
                "payload": payload,
            }))
        })
        .map_err(|error| format!("SESSION_EVENTS_QUERY:{error}"))?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|error| format!("SESSION_EVENTS_ROWS:{error}"))
}

fn append_session(path: &Path, session_id: &str, event_type: &str, payload: &Value) -> Result<Value, String> {
    let db = init_session(path)?;
    let exists: Option<i64> = db
        .query_row("SELECT 1 FROM sessions WHERE session_id=?1", [session_id], |row| row.get(0))
        .optional()
        .map_err(|error| format!("SESSION_APPEND_SESSION_QUERY:{error}"))?;
    if exists.is_none() {
        return Err(session_id.to_owned());
    }
    let last = db
        .query_row(
            "SELECT sequence,event_hash FROM events WHERE session_id=?1 ORDER BY sequence DESC LIMIT 1",
            [session_id],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()
        .map_err(|error| format!("SESSION_APPEND_LAST_QUERY:{error}"))?;
    let (sequence, previous) = last.map_or((1, "0".repeat(64)), |(seq, hash)| (seq + 1, hash));
    let body = json!({
        "session_id": session_id,
        "sequence": sequence,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous,
    });
    let digest = sha256_bytes(&canonical_json(&body)?);
    let created = now_iso()?;
    let payload_json = String::from_utf8(canonical_json(payload)?).map_err(|error| format!("SESSION_PAYLOAD_UTF8:{error}"))?;
    db.execute(
        "INSERT INTO events VALUES(?1,?2,?3,?4,?5,?6,?7)",
        params![session_id, sequence, event_type, payload_json, previous, digest, created],
    )
    .map_err(|error| format!("SESSION_APPEND_INSERT:{error}"))?;
    db.execute("UPDATE sessions SET updated_at=?1 WHERE session_id=?2", params![created, session_id])
        .map_err(|error| format!("SESSION_APPEND_UPDATE:{error}"))?;
    let mut output = body;
    output.as_object_mut().expect("object").insert("event_hash".to_owned(), Value::String(digest));
    output.as_object_mut().expect("object").insert("created_at".to_owned(), Value::String(created));
    Ok(output)
}

fn verify_session(path: &Path, session_id: &str) -> Result<Value, String> {
    let events = session_events(path, session_id)?;
    let mut previous = "0".repeat(64);
    let mut failures = Vec::<String>::new();
    for event in &events {
        let sequence = event["sequence"].as_i64().unwrap_or_default();
        let body = json!({
            "session_id": session_id,
            "sequence": sequence,
            "event_type": event["event_type"].clone(),
            "payload": event["payload"].clone(),
            "previous_hash": previous,
        });
        let digest = sha256_bytes(&canonical_json(&body)?);
        if event["previous_hash"].as_str().unwrap_or_default() != previous {
            failures.push(format!("previous:{sequence}"));
        }
        if event["event_hash"].as_str().unwrap_or_default() != digest {
            failures.push(format!("hash:{sequence}"));
        }
        previous = event["event_hash"].as_str().unwrap_or_default().to_owned();
    }
    Ok(json!({
        "ok": failures.is_empty(),
        "session_id": session_id,
        "events": events.len(),
        "last_hash": previous,
        "failures": failures,
    }))
}

fn summary_terms(view: &str) -> &'static [&'static str] {
    match view {
        "task" => &["task", "goal", "request", "plan"],
        "decision" => &["decision", "decide", "chosen", "keep", "revert", "supersede"],
        "change" => &["patch", "edit", "change", "file", "commit", "diff"],
        "failure" => &["fail", "error", "exception", "test", "panic", "timeout"],
        "security" => &["security", "authorization", "policy", "secret", "sandbox", "capability"],
        "dependency" => &["dependency", "import", "package", "provider", "adapter"],
        "repository" => &["repository", "branch", "commit", "symbol", "module", "worktree"],
        "test" => &["test", "verify", "coverage", "assert", "benchmark"],
        "provider" => &["provider", "model", "token", "cost", "receipt", "cache"],
        "handoff" => &["handoff", "agent", "resume", "migration", "fork", "merge"],
        _ => &[],
    }
}

fn event_summary(view: &str, events: &[Value]) -> Result<String, String> {
    let mut selected = Vec::<String>::new();
    for event in events {
        let payload = serde_json::to_string(&sorted(&event["payload"]))
            .map_err(|error| format!("SESSION_SUMMARY_JSON:{error}"))?;
        let corpus = format!("{} {payload}", event["event_type"].as_str().unwrap_or_default()).to_lowercase();
        if summary_terms(view).iter().any(|term| corpus.contains(term)) {
            let sequence = event["sequence"].as_i64().unwrap_or_default();
            let event_type = event["event_type"].as_str().unwrap_or_default();
            let shortened = payload.chars().take(700).collect::<String>();
            selected.push(format!("#{sequence} {event_type}: {shortened}"));
        }
    }
    if selected.is_empty() {
        Ok(format!("No {view} events in selected range."))
    } else {
        let start = selected.len().saturating_sub(100);
        Ok(selected[start..].join("\n"))
    }
}

fn compact_session(path: &Path, session_id: &str, requested_views: &[String]) -> Result<Value, String> {
    let events = session_events(path, session_id)?;
    let views = if requested_views.is_empty() {
        SESSION_VIEWS.iter().map(|value| (*value).to_owned()).collect::<Vec<_>>()
    } else {
        requested_views.to_vec()
    };
    let unknown = views
        .iter()
        .filter(|view| !SESSION_VIEWS.contains(&view.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        return Err(format!("unsupported summary views: {unknown:?}"));
    }
    let db = init_session(path)?;
    let mut created = Vec::<Value>::new();
    for view in views {
        let text = event_summary(&view, &events)?;
        let source_sequences = events.iter().map(|event| event["sequence"].clone()).collect::<Vec<_>>();
        let parent: Option<String> = db
            .query_row(
                "SELECT summary_id FROM summaries WHERE session_id=?1 AND view=?2 ORDER BY created_at DESC LIMIT 1",
                params![session_id, view],
                |row| row.get(0),
            )
            .optional()
            .map_err(|error| format!("SESSION_SUMMARY_PARENT:{error}"))?;
        let parents = parent.into_iter().map(Value::String).collect::<Vec<_>>();
        let body = json!({
            "session_id": session_id,
            "view": view,
            "source_sequences": source_sequences,
            "parents": parents,
            "summary": text,
        });
        let summary_id = sha256_bytes(&canonical_json(&body)?);
        db.execute(
            "INSERT OR IGNORE INTO summaries VALUES(?1,?2,?3,?4,?5,?6,?7)",
            params![
                summary_id,
                session_id,
                view,
                serde_json::to_string(&source_sequences).map_err(|error| format!("SESSION_SUMMARY_SEQS:{error}"))?,
                serde_json::to_string(&parents).map_err(|error| format!("SESSION_SUMMARY_PARENTS:{error}"))?,
                text,
                now_iso()?,
            ],
        )
        .map_err(|error| format!("SESSION_SUMMARY_INSERT:{error}"))?;
        let mut row = body;
        row.as_object_mut().expect("object").insert("summary_id".to_owned(), Value::String(summary_id));
        created.push(row);
    }
    let verified = verify_session(path, session_id)?["ok"].as_bool().unwrap_or(false);
    Ok(json!({"ok": true, "session_id": session_id, "events": events.len(), "summaries": created, "exact_history_preserved": verified}))
}

fn payload_weight(payload: &Value) -> f64 {
    let importance = payload["importance"].as_f64().unwrap_or(0.0);
    let pinned = if payload["pinned"].as_bool().unwrap_or(false) { 15.0 } else { 0.0 };
    let stale = if payload["stale"].as_bool().unwrap_or(false)
        || payload["reverted"].as_bool().unwrap_or(false)
        || payload["superseded"].as_bool().unwrap_or(false)
    {
        35.0
    } else {
        0.0
    };
    (importance * 10.0 + pinned - stale).clamp(-40.0, 30.0)
}

fn session_tokens(text: &str) -> Result<BTreeSet<String>, String> {
    let regex = Regex::new(r"[A-Za-z0-9_./:-]+").map_err(|error| format!("SESSION_TOKEN_REGEX:{error}"))?;
    let mut output = BTreeSet::new();
    for found in regex.find_iter(&text.to_lowercase()) {
        let token = found.as_str();
        if token.len() > 1 {
            output.insert(token.to_owned());
        }
        for part in token.split(['.', '_', '/', ':', '-']) {
            if part.len() > 1 {
                output.insert(part.to_owned());
            }
        }
    }
    Ok(output)
}

fn retrieve_session(path: &Path, session_id: &str, query: &str, limit: usize) -> Result<Value, String> {
    let query_terms = session_tokens(query)?;
    let events = session_events(path, session_id)?;
    let total = events.len().max(1) as f64;
    let mut candidates = Vec::<(f64, String, Value)>::new();
    for event in &events {
        let payload_rendered = serde_json::to_string(&sorted(&event["payload"]))
            .map_err(|error| format!("SESSION_RETRIEVE_JSON:{error}"))?;
        let event_type = event["event_type"].as_str().unwrap_or_default();
        let corpus = format!("{event_type} {payload_rendered}");
        let terms = session_tokens(&corpus)?;
        let matched = query_terms.intersection(&terms).count();
        if !query_terms.is_empty() && matched == 0 { continue; }
        let relevance = matched as f64 / query_terms.len().max(1) as f64 * 65.0;
        let sequence = event["sequence"].as_i64().unwrap_or_default();
        let recency = sequence as f64 / total * 15.0;
        let score = relevance + recency + payload_weight(&event["payload"]);
        candidates.push((
            score,
            sequence.to_string(),
            json!({
                "type": "event", "score": score, "sequence": sequence,
                "event_type": event["event_type"].clone(), "payload": event["payload"].clone(),
                "event_hash": event["event_hash"].clone(),
            }),
        ));
    }
    let db = init_session(path)?;
    let mut statement = db
        .prepare("SELECT summary_id,view,summary_text,source_sequences_json FROM summaries WHERE session_id=?1")
        .map_err(|error| format!("SESSION_RETRIEVE_SUMMARY_PREPARE:{error}"))?;
    let summaries = statement
        .query_map([session_id], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?, row.get::<_, String>(3)?)))
        .map_err(|error| format!("SESSION_RETRIEVE_SUMMARY_QUERY:{error}"))?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|error| format!("SESSION_RETRIEVE_SUMMARY_ROWS:{error}"))?;
    for (summary_id, view, summary, source_raw) in summaries {
        let terms = session_tokens(&format!("{view} {summary}"))?;
        let matched = query_terms.intersection(&terms).count();
        if !query_terms.is_empty() && matched == 0 { continue; }
        let score = matched as f64 / query_terms.len().max(1) as f64 * 70.0 + 8.0;
        candidates.push((
            score,
            summary_id.clone(),
            json!({
                "type": "summary", "score": score, "summary_id": summary_id,
                "view": view, "summary": summary,
                "source_sequences": serde_json::from_str::<Value>(&source_raw).unwrap_or_else(|_| json!([])),
            }),
        ));
    }
    candidates.sort_by(|left, right| {
        right.0.partial_cmp(&left.0).unwrap_or(std::cmp::Ordering::Equal).then_with(|| left.1.cmp(&right.1))
    });
    let verified = verify_session(path, session_id)?["ok"].as_bool().unwrap_or(false);
    Ok(json!({
        "session_id": session_id,
        "query": query,
        "results": candidates.into_iter().take(limit.max(1)).map(|(_, _, value)| value).collect::<Vec<_>>(),
        "exact_recovery": verified,
    }))
}

fn checkpoint_session(path: &Path, session_id: &str, label: &str) -> Result<Value, String> {
    let db = init_session(path)?;
    let last = db
        .query_row(
            "SELECT sequence,event_hash FROM events WHERE session_id=?1 ORDER BY sequence DESC LIMIT 1",
            [session_id],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()
        .map_err(|error| format!("SESSION_CHECKPOINT_LAST:{error}"))?;
    let (sequence, event_hash) = last.unwrap_or((0, "0".repeat(64)));
    let body = json!({"session_id": session_id, "sequence": sequence, "event_hash": event_hash, "label": label});
    let checkpoint_id = sha256_bytes(&canonical_json(&body)?);
    let created = now_iso()?;
    db.execute(
        "INSERT OR IGNORE INTO checkpoints VALUES(?1,?2,?3,?4,?5,?6)",
        params![checkpoint_id, session_id, label, sequence, event_hash, created],
    )
    .map_err(|error| format!("SESSION_CHECKPOINT_INSERT:{error}"))?;
    let mut output = body;
    output.as_object_mut().expect("object").insert("checkpoint_id".to_owned(), Value::String(checkpoint_id));
    output.as_object_mut().expect("object").insert("created_at".to_owned(), Value::String(created));
    Ok(output)
}

fn restore_session(path: &Path, checkpoint_id: &str) -> Result<Value, String> {
    let db = init_session(path)?;
    let checkpoint = db
        .query_row(
            "SELECT checkpoint_id,session_id,label,sequence,event_hash,created_at FROM checkpoints WHERE checkpoint_id=?1",
            [checkpoint_id],
            |row| Ok(json!({
                "checkpoint_id": row.get::<_, String>(0)?, "session_id": row.get::<_, String>(1)?,
                "label": row.get::<_, String>(2)?, "sequence": row.get::<_, i64>(3)?,
                "event_hash": row.get::<_, String>(4)?, "created_at": row.get::<_, String>(5)?,
            })),
        )
        .optional()
        .map_err(|error| format!("SESSION_RESTORE_QUERY:{error}"))?
        .ok_or_else(|| checkpoint_id.to_owned())?;
    let session_id = checkpoint["session_id"].as_str().unwrap_or_default();
    let limit_sequence = checkpoint["sequence"].as_i64().unwrap_or_default();
    let events = session_events(path, session_id)?
        .into_iter()
        .filter(|event| event["sequence"].as_i64().unwrap_or_default() <= limit_sequence)
        .collect::<Vec<_>>();
    let actual = events.last().and_then(|event| event["event_hash"].as_str()).unwrap_or_else(|| "");
    let actual = if events.is_empty() { "0".repeat(64) } else { actual.to_owned() };
    let valid = actual == checkpoint["event_hash"].as_str().unwrap_or_default();
    Ok(json!({"ok": valid, "checkpoint": checkpoint, "events": events, "exact_recovery": valid}))
}

fn session_memory(action: &str, arguments: &[String], project_root: &Path, state_root: &Path) -> Result<Value, String> {
    let db_path = state_root.join("unified").join("session-memory.sqlite3");
    match action {
        "memory-open" => {
            let requested = option_value(arguments, "--session-id")?;
            let parents = repeated_values(arguments, "--parent")?;
            let metadata_raw = option_value(arguments, "--metadata")?.unwrap_or_else(|| "{}".to_owned());
            let metadata = read_json_or_inline(&metadata_raw)?;
            if !metadata.is_object() { return Err("metadata must be a JSON object".to_owned()); }
            open_session(&db_path, project_root, requested, &parents, &metadata)
        }
        "memory-append" => {
            let positional = positional_after_action(arguments, action)?;
            if positional.len() < 3 { return Err("MEMORY_APPEND_ARGUMENTS_REQUIRED".to_owned()); }
            let payload = read_json_or_inline(&positional[2])?;
            if !payload.is_object() { return Err("payload must be a JSON object".to_owned()); }
            append_session(&db_path, &positional[0], &positional[1], &payload)
        }
        "memory-compact" => {
            let positional = positional_after_action(arguments, action)?;
            let session_id = positional.first().ok_or_else(|| "MEMORY_SESSION_REQUIRED".to_owned())?;
            compact_session(&db_path, session_id, &repeated_values(arguments, "--view")?)
        }
        "memory-retrieve" => {
            let positional = positional_after_action(arguments, action)?;
            if positional.len() < 2 { return Err("MEMORY_RETRIEVE_ARGUMENTS_REQUIRED".to_owned()); }
            let limit = option_value(arguments, "--limit")?
                .map(|value| value.parse::<usize>().map_err(|_| "MEMORY_LIMIT_INVALID".to_owned()))
                .transpose()?
                .unwrap_or(12);
            retrieve_session(&db_path, &positional[0], &positional[1], limit)
        }
        "memory-checkpoint" => {
            let positional = positional_after_action(arguments, action)?;
            let session_id = positional.first().ok_or_else(|| "MEMORY_SESSION_REQUIRED".to_owned())?;
            let label = option_value(arguments, "--label")?.unwrap_or_default();
            checkpoint_session(&db_path, session_id, &label)
        }
        "memory-fork" => {
            let positional = positional_after_action(arguments, action)?;
            let session_id = positional.first().ok_or_else(|| "MEMORY_SESSION_REQUIRED".to_owned())?;
            let label = option_value(arguments, "--label")?.unwrap_or_default();
            let checkpoint = checkpoint_session(&db_path, session_id, if label.is_empty() { "fork" } else { &label })?;
            let child = open_session(
                &db_path,
                project_root,
                None,
                &[session_id.clone()],
                &json!({"fork_checkpoint": checkpoint["checkpoint_id"].clone(), "label": label}),
            )?;
            Ok(json!({"parent": session_id, "child": child, "checkpoint": checkpoint}))
        }
        "memory-merge" => {
            let session_ids = positional_after_action(arguments, action)?;
            if session_ids.iter().collect::<BTreeSet<_>>().len() < 2 {
                return Err("merge requires at least two distinct sessions".to_owned());
            }
            let label = option_value(arguments, "--label")?.unwrap_or_default();
            let mut checkpoints = Vec::new();
            for session_id in &session_ids {
                checkpoints.push(checkpoint_session(&db_path, session_id, if label.is_empty() { "merge" } else { &label })?);
            }
            let ids = checkpoints.iter().map(|item| item["checkpoint_id"].clone()).collect::<Vec<_>>();
            let merged = open_session(
                &db_path,
                project_root,
                None,
                &session_ids,
                &json!({"merge_checkpoints": ids, "label": label}),
            )?;
            let merged_id = merged["session_id"].as_str().unwrap_or_default().to_owned();
            let _ = append_session(&db_path, &merged_id, "merge", &json!({"parents": session_ids, "checkpoints": checkpoints}))?;
            Ok(json!({"merged": merged, "parents": session_ids, "checkpoints": checkpoints}))
        }
        "memory-restore" => {
            let positional = positional_after_action(arguments, action)?;
            restore_session(&db_path, positional.first().ok_or_else(|| "MEMORY_CHECKPOINT_REQUIRED".to_owned())?)
        }
        "memory-verify" => {
            let positional = positional_after_action(arguments, action)?;
            verify_session(&db_path, positional.first().ok_or_else(|| "MEMORY_SESSION_REQUIRED".to_owned())?)
        }
        _ => Err(format!("SESSION_MEMORY_UNSUPPORTED:{action}")),
    }
}

pub(crate) fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Option<Value>, String> {
    if !supports(command) {
        return Ok(None);
    }
    let action = command[1].as_str();
    let value = if matches!(
        action,
        "memory-add" | "memory-extract" | "memory-search" | "memory-export" | "memory-backfill" | "memory-intelligence-status"
    ) {
        memory_intelligence(action, arguments, state_root)?
    } else {
        session_memory(action, arguments, project_root, state_root)?
    };
    Ok(Some(value))
}
