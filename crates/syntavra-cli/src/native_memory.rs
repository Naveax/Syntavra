#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::fs;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::types::Value as SqlValue;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row, TransactionBehavior};
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

static ID_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone)]
struct MemoryRow {
    memory_id: String,
    memory_class: String,
    text: String,
    confidence: f64,
    provenance_json: String,
    created_at: f64,
    superseded_by: Option<String>,
    expires_at: Option<f64>,
    tags_json: String,
}

impl MemoryRow {
    fn from_row(row: &Row<'_>, offset: usize) -> rusqlite::Result<Self> {
        Ok(Self {
            memory_id: row.get(offset)?,
            memory_class: row.get(offset + 1)?,
            text: row.get(offset + 2)?,
            confidence: row.get(offset + 3)?,
            provenance_json: row.get(offset + 4)?,
            created_at: row.get(offset + 5)?,
            superseded_by: row.get(offset + 6)?,
            expires_at: row.get(offset + 7)?,
            tags_json: row.get(offset + 8)?,
        })
    }

    fn into_json(self) -> Result<Value, String> {
        let provenance: Value = serde_json::from_str(&self.provenance_json)
            .map_err(|_| "MEMORY_PROVENANCE_JSON_INVALID".to_owned())?;
        let tags: Value = serde_json::from_str(&self.tags_json)
            .map_err(|_| "MEMORY_TAGS_JSON_INVALID".to_owned())?;
        if !tags.is_array() {
            return Err("MEMORY_TAGS_JSON_INVALID".to_owned());
        }
        Ok(json!({
            "memory_id": self.memory_id,
            "memory_class": self.memory_class,
            "text": self.text,
            "confidence": self.confidence,
            "provenance": provenance,
            "created_at": self.created_at,
            "superseded_by": self.superseded_by,
            "expires_at": self.expires_at,
            "tags": tags,
        }))
    }
}

#[derive(Debug)]
struct SearchCandidate {
    memory: MemoryRow,
    lexical_rank: f64,
    relation_weight: f64,
    score: f64,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "memory" && matches!(action.as_str(), "add" | "search" | "link" | "neighbors"))
}

fn initialize(state_root: &Path) -> Result<(Connection, bool), String> {
    fs::create_dir_all(state_root)
        .map_err(|error| format!("MEMORY_STATE_DIRECTORY_CREATE_FAILED:{error}"))?;
    let connection = Connection::open(state_root.join("memory.sqlite3"))
        .map_err(|error| format!("MEMORY_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA busy_timeout=30000;\
             PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS memories(\
               memory_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,user_id TEXT NOT NULL,\
               memory_class TEXT NOT NULL,text TEXT NOT NULL,confidence REAL NOT NULL,\
               provenance_json TEXT NOT NULL,content_hash TEXT NOT NULL,created_at REAL NOT NULL,\
               superseded_by TEXT,expires_at REAL,tags_json TEXT NOT NULL DEFAULT '[]',\
               FOREIGN KEY(superseded_by) REFERENCES memories(memory_id));\
             CREATE INDEX IF NOT EXISTS memories_scope_idx\
               ON memories(project_id,user_id,memory_class,created_at DESC);\
             CREATE TABLE IF NOT EXISTS memory_relations(\
               source_id TEXT NOT NULL,relation TEXT NOT NULL,target_id TEXT NOT NULL,\
               weight REAL NOT NULL,created_at REAL NOT NULL,\
               PRIMARY KEY(source_id,relation,target_id),\
               FOREIGN KEY(source_id) REFERENCES memories(memory_id),\
               FOREIGN KEY(target_id) REFERENCES memories(memory_id));\
             CREATE INDEX IF NOT EXISTS memory_relation_target_idx\
               ON memory_relations(target_id,relation);",
        )
        .map_err(|error| format!("MEMORY_DATABASE_INITIALIZE_FAILED:{error}"))?;

    let columns = {
        let mut statement = connection
            .prepare("PRAGMA table_info(memories)")
            .map_err(|error| format!("MEMORY_SCHEMA_INSPECT_FAILED:{error}"))?;
        let rows = statement
            .query_map([], |row| row.get::<_, String>(1))
            .map_err(|error| format!("MEMORY_SCHEMA_INSPECT_FAILED:{error}"))?;
        let mut columns = BTreeSet::new();
        for row in rows {
            columns.insert(
                row.map_err(|error| format!("MEMORY_SCHEMA_ROW_FAILED:{error}"))?,
            );
        }
        columns
    };
    if !columns.contains("expires_at") {
        connection
            .execute("ALTER TABLE memories ADD COLUMN expires_at REAL", [])
            .map_err(|error| format!("MEMORY_SCHEMA_MIGRATION_FAILED:{error}"))?;
    }
    if !columns.contains("tags_json") {
        connection
            .execute(
                "ALTER TABLE memories ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'",
                [],
            )
            .map_err(|error| format!("MEMORY_SCHEMA_MIGRATION_FAILED:{error}"))?;
    }

    let fts_available = connection
        .execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts \
             USING fts5(memory_id UNINDEXED, text, tokenize='unicode61')",
            [],
        )
        .is_ok();
    Ok((connection, fts_available))
}

fn now() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "MEMORY_SYSTEM_CLOCK_INVALID".to_owned())
}

fn generated_id() -> Result<String, String> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "MEMORY_SYSTEM_CLOCK_INVALID".to_owned())?;
    let counter = ID_COUNTER.fetch_add(1, Ordering::Relaxed);
    let material = format!(
        "{}:{}:{}",
        duration.as_nanos(),
        std::process::id(),
        counter
    );
    Ok(sha256_hex(material.as_bytes())[..32].to_owned())
}

fn option_value<'a>(arguments: &'a [String], flag: &str) -> Result<Option<&'a str>, String> {
    let mut result = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            result = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .as_str(),
            );
        } else if let Some(value) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            result = Some(value);
        }
        index += 1;
    }
    Ok(result)
}

fn repeated_option(arguments: &[String], flag: &str) -> Result<Vec<String>, String> {
    let mut values = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            values.push(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = arguments[index]
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            values.push(value.to_owned());
        }
        index += 1;
    }
    Ok(values)
}

fn integer_option(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?.map_or(Ok(default), |value| {
        value
            .parse::<i64>()
            .map_err(|_| format!("{flag}_VALUE_INVALID"))
    })
}

fn float_option(arguments: &[String], flag: &str, default: f64) -> Result<f64, String> {
    option_value(arguments, flag)?.map_or(Ok(default), |value| {
        value
            .parse::<f64>()
            .map_err(|_| format!("{flag}_VALUE_INVALID"))
    })
}

fn flag(arguments: &[String], name: &str) -> bool {
    arguments.iter().any(|value| value == name)
}

fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    offset: usize,
) -> Result<&'a str, String> {
    let index = arguments
        .windows(2)
        .position(|window| window[0] == "memory" && window[1] == action)
        .ok_or_else(|| format!("MEMORY_{}_ACTION_MISSING", action.to_ascii_uppercase()))?;
    arguments
        .get(index + 2 + offset)
        .map(String::as_str)
        .ok_or_else(|| format!("MEMORY_{}_ARGUMENT_MISSING:{offset}", action.to_ascii_uppercase()))
}

fn python_json_string(value: &str) -> Result<String, String> {
    serde_json::to_string(value).map_err(|_| "MEMORY_CANONICAL_JSON_FAILED".to_owned())
}

fn content_hash(memory_class: &str, text: &str, tags: &[String]) -> Result<String, String> {
    let rendered_tags = tags
        .iter()
        .map(|tag| python_json_string(tag))
        .collect::<Result<Vec<_>, _>>()?
        .join(", ");
    let rendered = format!(
        "{{\"class\": {}, \"tags\": [{}], \"text\": {}}}",
        python_json_string(memory_class)?,
        rendered_tags,
        python_json_string(text)?
    );
    Ok(sha256_hex(rendered.as_bytes()))
}

fn record_by_id(
    connection: &Connection,
    memory_id: &str,
    project_id: &str,
    user_id: &str,
) -> Result<MemoryRow, String> {
    connection
        .query_row(
            "SELECT memory_id,memory_class,text,confidence,provenance_json,created_at,\
                    superseded_by,expires_at,tags_json\
             FROM memories WHERE memory_id=?1 AND project_id=?2 AND user_id=?3",
            params![memory_id, project_id, user_id],
            |row| MemoryRow::from_row(row, 0),
        )
        .optional()
        .map_err(|error| format!("MEMORY_RECORD_QUERY_FAILED:{error}"))?
        .ok_or_else(|| "MEMORY_NOT_FOUND".to_owned())
}

fn add(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
    fts_available: bool,
) -> Result<Value, String> {
    let memory_class = positional_after(arguments, "add", 0)?;
    let text = positional_after(arguments, "add", 1)?;
    let clean = text.trim();
    if clean.is_empty() {
        return Err("MEMORY_TEXT_EMPTY".to_owned());
    }
    let confidence = float_option(arguments, "--confidence", 1.0)?;
    if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) {
        return Err("MEMORY_CONFIDENCE_OUT_OF_RANGE".to_owned());
    }
    let source = option_value(arguments, "--source")?.unwrap_or("user");
    let expires_at = option_value(arguments, "--expires-at")?
        .map(|value| {
            value
                .parse::<f64>()
                .map_err(|_| "--expires-at_VALUE_INVALID".to_owned())
        })
        .transpose()?;
    let user_id = option_value(arguments, "--user-id")?.unwrap_or("default");
    let tags = repeated_option(arguments, "--tag")?
        .into_iter()
        .map(|tag| tag.trim().to_owned())
        .filter(|tag| !tag.is_empty())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let digest = content_hash(memory_class, clean, &tags)?;

    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("MEMORY_ADD_TRANSACTION_FAILED:{error}"))?;
    let existing = transaction
        .query_row(
            "SELECT memory_id,memory_class,text,confidence,provenance_json,created_at,\
                    superseded_by,expires_at,tags_json\
             FROM memories WHERE project_id=?1 AND user_id=?2 AND memory_class=?3\
               AND content_hash=?4 AND superseded_by IS NULL",
            params![project_id, user_id, memory_class, digest],
            |row| MemoryRow::from_row(row, 0),
        )
        .optional()
        .map_err(|error| format!("MEMORY_DEDUP_QUERY_FAILED:{error}"))?;
    if let Some(existing) = existing {
        transaction
            .commit()
            .map_err(|error| format!("MEMORY_ADD_COMMIT_FAILED:{error}"))?;
        return existing.into_json();
    }

    let memory_id = generated_id()?;
    let created_at = now()?;
    let provenance_json = serde_json::to_string(&json!({"source": source}))
        .map_err(|_| "MEMORY_PROVENANCE_JSON_FAILED".to_owned())?;
    let tags_json = serde_json::to_string(&tags)
        .map_err(|_| "MEMORY_TAGS_JSON_FAILED".to_owned())?;
    transaction
        .execute(
            "INSERT INTO memories(\
               memory_id,project_id,user_id,memory_class,text,confidence,provenance_json,\
               content_hash,created_at,superseded_by,expires_at,tags_json)\
             VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,NULL,?10,?11)",
            params![
                memory_id,
                project_id,
                user_id,
                memory_class,
                clean,
                confidence,
                provenance_json,
                digest,
                created_at,
                expires_at,
                tags_json
            ],
        )
        .map_err(|error| format!("MEMORY_INSERT_FAILED:{error}"))?;
    if fts_available {
        transaction
            .execute(
                "INSERT INTO memories_fts(memory_id,text) VALUES(?1,?2)",
                params![memory_id, clean],
            )
            .map_err(|error| format!("MEMORY_FTS_INSERT_FAILED:{error}"))?;
    }
    let record = record_by_id(&transaction, &memory_id, project_id, user_id)?;
    transaction
        .commit()
        .map_err(|error| format!("MEMORY_ADD_COMMIT_FAILED:{error}"))?;
    record.into_json()
}

fn link(
    arguments: &[String],
    project_id: &str,
    connection: &mut Connection,
) -> Result<Value, String> {
    let source_id = positional_after(arguments, "link", 0)?;
    let relation = positional_after(arguments, "link", 1)?.trim();
    let target_id = positional_after(arguments, "link", 2)?;
    if relation.is_empty() {
        return Err("MEMORY_RELATION_EMPTY".to_owned());
    }
    let weight = float_option(arguments, "--weight", 1.0)?;
    if !weight.is_finite() || weight <= 0.0 {
        return Err("MEMORY_WEIGHT_INVALID".to_owned());
    }
    let user_id = option_value(arguments, "--user-id")?.unwrap_or("default");
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("MEMORY_LINK_TRANSACTION_FAILED:{error}"))?;
    let mut statement = transaction
        .prepare(
            "SELECT memory_id FROM memories WHERE memory_id IN (?1,?2)\
             AND project_id=?3 AND user_id=?4",
        )
        .map_err(|error| format!("MEMORY_LINK_SCOPE_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map(
            params![source_id, target_id, project_id, user_id],
            |row| row.get::<_, String>(0),
        )
        .map_err(|error| format!("MEMORY_LINK_SCOPE_QUERY_FAILED:{error}"))?;
    let mut found = BTreeSet::new();
    for row in rows {
        found.insert(row.map_err(|error| format!("MEMORY_LINK_SCOPE_ROW_FAILED:{error}"))?);
    }
    drop(statement);
    let expected = [source_id.to_owned(), target_id.to_owned()]
        .into_iter()
        .collect::<BTreeSet<_>>();
    if found != expected {
        return Err("MEMORY_RELATION_SCOPE_MISMATCH".to_owned());
    }
    transaction
        .execute(
            "INSERT OR REPLACE INTO memory_relations(source_id,relation,target_id,weight,created_at)\
             VALUES(?1,?2,?3,?4,?5)",
            params![source_id, relation, target_id, weight, now()?],
        )
        .map_err(|error| format!("MEMORY_LINK_INSERT_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("MEMORY_LINK_COMMIT_FAILED:{error}"))?;
    Ok(json!({"ok": true}))
}

fn neighbors(
    arguments: &[String],
    project_id: &str,
    connection: &Connection,
) -> Result<Value, String> {
    let memory_id = positional_after(arguments, "neighbors", 0)?;
    let relation = option_value(arguments, "--relation")?.filter(|value| !value.is_empty());
    let limit = integer_option(arguments, "--limit", 50)?.max(1);
    let user_id = option_value(arguments, "--user-id")?.unwrap_or("default");
    let mut sql = String::from(
        "SELECT r.relation,r.weight,\
                m.memory_id,m.memory_class,m.text,m.confidence,m.provenance_json,m.created_at,\
                m.superseded_by,m.expires_at,m.tags_json\
         FROM memory_relations r JOIN memories m ON m.memory_id=r.target_id\
         WHERE r.source_id=? AND m.project_id=? AND m.user_id=?",
    );
    let mut values = vec![
        SqlValue::Text(memory_id.to_owned()),
        SqlValue::Text(project_id.to_owned()),
        SqlValue::Text(user_id.to_owned()),
    ];
    if let Some(relation) = relation {
        sql.push_str(" AND r.relation=?");
        values.push(SqlValue::Text(relation.to_owned()));
    }
    sql.push_str(" ORDER BY r.weight DESC,m.created_at DESC LIMIT ?");
    values.push(SqlValue::Integer(limit));
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| format!("MEMORY_NEIGHBORS_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map(params_from_iter(values.iter()), |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, f64>(1)?,
                MemoryRow::from_row(row, 2)?,
            ))
        })
        .map_err(|error| format!("MEMORY_NEIGHBORS_QUERY_FAILED:{error}"))?;
    let mut results = Vec::new();
    for row in rows {
        let (relation, weight, memory) =
            row.map_err(|error| format!("MEMORY_NEIGHBORS_ROW_FAILED:{error}"))?;
        results.push(json!({
            "relation": relation,
            "weight": weight,
            "memory": memory.into_json()?,
        }));
    }
    Ok(json!({"results": results}))
}

fn fts_query(query: &str) -> String {
    let mut tokens = Vec::new();
    let mut current = String::new();
    for character in query.chars() {
        if character.is_alphanumeric() || matches!(character, '_' | '.' | '-') {
            current.push(character);
        } else if !current.is_empty() {
            tokens.push(std::mem::take(&mut current));
            if tokens.len() == 32 {
                break;
            }
        }
    }
    if !current.is_empty() && tokens.len() < 32 {
        tokens.push(current);
    }
    tokens
        .into_iter()
        .map(|token| format!("\"{}\"", token.replace('"', "")))
        .collect::<Vec<_>>()
        .join(" OR ")
}

fn search_rows(
    connection: &Connection,
    project_id: &str,
    user_id: &str,
    classes: &[String],
    include_superseded: bool,
    include_expired: bool,
    query: &str,
    limit: i64,
    fts_available: bool,
    current_time: f64,
) -> Result<(String, Vec<SearchCandidate>), String> {
    let mut clauses = vec!["m.project_id=?".to_owned(), "m.user_id=?".to_owned()];
    let mut scope_values = vec![
        SqlValue::Text(project_id.to_owned()),
        SqlValue::Text(user_id.to_owned()),
    ];
    if !include_superseded {
        clauses.push("m.superseded_by IS NULL".to_owned());
    }
    if !include_expired {
        clauses.push("(m.expires_at IS NULL OR m.expires_at>?)".to_owned());
        scope_values.push(SqlValue::Real(current_time));
    }
    if !classes.is_empty() {
        clauses.push(format!(
            "m.memory_class IN ({})",
            vec!["?"; classes.len()].join(",")
        ));
        scope_values.extend(classes.iter().cloned().map(SqlValue::Text));
    }
    let candidate_limit = (limit * 4).max(limit);
    let expression = fts_query(query);
    let mut mode = "LEXICAL_ONLY".to_owned();
    let mut candidates = Vec::new();

    if fts_available && !expression.is_empty() {
        let sql = format!(
            "SELECT m.memory_id,m.memory_class,m.text,m.confidence,m.provenance_json,m.created_at,\
                    m.superseded_by,m.expires_at,m.tags_json,bm25(memories_fts) AS lexical_rank,\
                    COALESCE((SELECT SUM(r.weight) FROM memory_relations r\
                              WHERE r.target_id=m.memory_id),0) AS relation_weight\
             FROM memories_fts JOIN memories m ON m.memory_id=memories_fts.memory_id\
             WHERE memories_fts MATCH ? AND {} ORDER BY lexical_rank LIMIT ?",
            clauses.join(" AND ")
        );
        let mut values = vec![SqlValue::Text(expression)];
        values.extend(scope_values.iter().cloned());
        values.push(SqlValue::Integer(candidate_limit));
        match connection.prepare(&sql) {
            Ok(mut statement) => match statement.query_map(params_from_iter(values.iter()), |row| {
                Ok(SearchCandidate {
                    memory: MemoryRow::from_row(row, 0)?,
                    lexical_rank: row.get(9)?,
                    relation_weight: row.get(10)?,
                    score: 0.0,
                })
            }) {
                Ok(rows) => {
                    mode = "FTS5_GRAPH".to_owned();
                    for row in rows {
                        candidates.push(
                            row.map_err(|error| format!("MEMORY_SEARCH_FTS_ROW_FAILED:{error}"))?,
                        );
                    }
                }
                Err(_) => mode = "LEXICAL_DEGRADED".to_owned(),
            },
            Err(_) => mode = "LEXICAL_DEGRADED".to_owned(),
        }
    }

    if candidates.is_empty() {
        let sql = format!(
            "SELECT m.memory_id,m.memory_class,m.text,m.confidence,m.provenance_json,m.created_at,\
                    m.superseded_by,m.expires_at,m.tags_json,0 AS lexical_rank,\
                    COALESCE(SUM(r.weight),0) AS relation_weight\
             FROM memories m LEFT JOIN memory_relations r ON r.target_id=m.memory_id\
             WHERE m.text LIKE ? AND {}\
             GROUP BY m.memory_id ORDER BY m.created_at DESC LIMIT ?",
            clauses.join(" AND ")
        );
        let mut values = vec![SqlValue::Text(format!("%{query}%"))];
        values.extend(scope_values);
        values.push(SqlValue::Integer(candidate_limit));
        let mut statement = connection
            .prepare(&sql)
            .map_err(|error| format!("MEMORY_SEARCH_FALLBACK_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map(params_from_iter(values.iter()), |row| {
                Ok(SearchCandidate {
                    memory: MemoryRow::from_row(row, 0)?,
                    lexical_rank: row.get(9)?,
                    relation_weight: row.get(10)?,
                    score: 0.0,
                })
            })
            .map_err(|error| format!("MEMORY_SEARCH_FALLBACK_QUERY_FAILED:{error}"))?;
        for row in rows {
            candidates.push(
                row.map_err(|error| format!("MEMORY_SEARCH_FALLBACK_ROW_FAILED:{error}"))?,
            );
        }
    }
    Ok((mode, candidates))
}

fn search(
    arguments: &[String],
    project_id: &str,
    connection: &Connection,
    fts_available: bool,
) -> Result<Value, String> {
    let query = positional_after(arguments, "search", 0)?;
    let limit = integer_option(arguments, "--limit", 10)?;
    let classes = repeated_option(arguments, "--memory-class")?;
    let include_superseded = flag(arguments, "--include-superseded");
    let include_expired = flag(arguments, "--include-expired");
    let user_id = option_value(arguments, "--user-id")?.unwrap_or("default");
    let current_time = now()?;
    let (mode, mut candidates) = search_rows(
        connection,
        project_id,
        user_id,
        &classes,
        include_superseded,
        include_expired,
        query,
        limit,
        fts_available,
        current_time,
    )?;
    for candidate in &mut candidates {
        let lexical = 1.0 / (1.0 + candidate.lexical_rank.abs().max(0.0));
        let age_days = ((current_time - candidate.memory.created_at) / 86_400.0).max(0.0);
        let recency = (-age_days / 90.0).exp();
        let relation_boost = (candidate.relation_weight / 5.0).min(1.0);
        candidate.score = 0.55 * lexical
            + 0.25 * candidate.memory.confidence
            + 0.12 * recency
            + 0.08 * relation_boost;
    }
    candidates.sort_by(|left, right| {
        right
            .score
            .total_cmp(&left.score)
            .then_with(|| right.memory.created_at.total_cmp(&left.memory.created_at))
            .then_with(|| left.memory.memory_id.cmp(&right.memory.memory_id))
    });
    let take = usize::try_from(limit.max(1)).unwrap_or(usize::MAX);
    let mut results = Vec::new();
    for candidate in candidates.into_iter().take(take) {
        let score = candidate.score;
        let mut value = candidate.memory.into_json()?;
        value
            .as_object_mut()
            .ok_or_else(|| "MEMORY_RESULT_OBJECT_INVALID".to_owned())?
            .insert("score".to_owned(), json!(score));
        results.push(value);
    }
    Ok(json!({"mode": mode, "results": results}))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let project = project_root.to_string_lossy();
    let project_id = super::super::state_snapshot_contract::project_id_for_root(&project)?;
    let (mut connection, fts_available) = initialize(state_root)?;
    match command {
        [root, action] if root == "memory" && action == "add" => {
            add(arguments, &project_id, &mut connection, fts_available)
        }
        [root, action] if root == "memory" && action == "search" => {
            search(arguments, &project_id, &connection, fts_available)
        }
        [root, action] if root == "memory" && action == "link" => {
            link(arguments, &project_id, &mut connection)
        }
        [root, action] if root == "memory" && action == "neighbors" => {
            neighbors(arguments, &project_id, &connection)
        }
        _ => Err("MEMORY_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn memory_commands_are_supported() {
        for action in ["add", "search", "link", "neighbors"] {
            assert!(supports(&["memory".to_owned(), action.to_owned()]));
        }
    }
}
