#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines, clippy::cast_precision_loss)]

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

#[path = "native_remaining71_graph_core.rs"]
mod core_parity;

const IGNORE_PARTS: &[&str] = &[
    ".git",
    ".hg",
    ".svn",
    ".syntavra",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "vendor",
    "coverage",
    ".cache",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
];

const ACTIONS: &[&str] = &[
    "graph-index",
    "graph-query",
    "graph-impact",
    "language",
    "semantic-services",
    "semantic-import",
    "evidence-stats",
    "evidence-neighbors",
];

#[derive(Debug, Clone)]
struct Detection {
    language_id: String,
    confidence: f64,
    evidence: String,
    capability_level: String,
    descriptor_source: String,
    text_encoding: Option<String>,
    binary: bool,
    generated: bool,
    minified: bool,
    diagnostics: Vec<String>,
    candidates: Vec<String>,
}

impl Detection {
    fn json(&self) -> Value {
        json!({
            "language_id": self.language_id,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "capability_level": self.capability_level,
            "descriptor_source": self.descriptor_source,
            "text_encoding": self.text_encoding,
            "binary": self.binary,
            "generated": self.generated,
            "minified": self.minified,
            "diagnostics": self.diagnostics,
            "candidates": self.candidates,
        })
    }
}

pub(crate) fn supports(command: &[String]) -> bool {
    command.len() == 2 && command[0] == "run" && ACTIONS.contains(&command[1].as_str())
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
    if matches!(
        command.get(1).map(String::as_str),
        Some("graph-index" | "graph-query" | "graph-impact" | "language" | "semantic-services")
    ) {
        return core_parity::execute(command, arguments, project_root, state_root).map(Some);
    }
    let unified = state_root.join("unified");
    fs::create_dir_all(&unified).map_err(|error| format!("GRAPH_STATE_CREATE_FAILED:{error}"))?;
    let database = unified.join("semantic-graph.sqlite3");
    initialize_graph(&database)?;
    let action = command[1].as_str();
    match action {
        "graph-index" => {
            let max_file_bytes =
                option_i64(arguments, "--max-file-bytes", 2_000_000)?.max(1) as u64;
            Ok(Some(index_repository(
                project_root,
                &unified,
                &database,
                max_file_bytes,
            )?))
        }
        "graph-query" => {
            let query = positional_after(arguments, "graph-query", 0)?;
            let limit = option_i64(arguments, "--limit", 20)?.clamp(1, 200);
            let results = query_repository(&database, query, limit)?;
            Ok(Some(
                json!({"ok": true, "query": query, "results": results}),
            ))
        }
        "graph-impact" => {
            let node_id = positional_after(arguments, "graph-impact", 0)?;
            let max_depth = option_i64(arguments, "--max-depth", 6)?.max(0);
            Ok(Some(impact(&database, node_id, max_depth)?))
        }
        "language" => Ok(Some(language_action(
            arguments,
            project_root,
            &unified,
            &database,
        )?)),
        "semantic-services" => Ok(Some(language_status(project_root, &database)?)),
        "semantic-import" => Ok(Some(semantic_import(
            arguments,
            project_root,
            &database,
            &unified,
        )?)),
        "evidence-stats" => Ok(Some(runtime_evidence_stats(&unified)?)),
        "evidence-neighbors" => Ok(Some(runtime_evidence_neighbors(arguments, &unified)?)),
        _ => Ok(None),
    }
}

fn initialize_graph(path: &Path) -> Result<(), String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA busy_timeout=30000;\
             PRAGMA journal_mode=WAL;\
             CREATE TABLE IF NOT EXISTS files(\
               path TEXT PRIMARY KEY,sha256 TEXT NOT NULL,language TEXT NOT NULL,indexed_at TEXT NOT NULL,\
               analysis_key TEXT NOT NULL DEFAULT '',detector TEXT NOT NULL DEFAULT 'legacy',\
               confidence REAL NOT NULL DEFAULT 0.0,capability_level TEXT NOT NULL DEFAULT 'lexical',\
               metadata_json TEXT NOT NULL DEFAULT '{}');\
             CREATE TABLE IF NOT EXISTS nodes(\
               node_id TEXT PRIMARY KEY,path TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,\
               qualified_name TEXT NOT NULL,start_line INTEGER NOT NULL,end_line INTEGER NOT NULL,\
               language TEXT NOT NULL,evidence_ref TEXT NOT NULL,metadata_json TEXT NOT NULL);\
             CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path);\
             CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);\
             CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);\
             CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);\
             CREATE TABLE IF NOT EXISTS edges(\
               source TEXT NOT NULL,target TEXT NOT NULL,edge_type TEXT NOT NULL,confidence REAL NOT NULL,\
               evidence_ref TEXT NOT NULL,metadata_json TEXT NOT NULL,\
               PRIMARY KEY(source,target,edge_type,evidence_ref));\
             CREATE INDEX IF NOT EXISTS idx_edges_source_type ON edges(source,edge_type);\
             CREATE INDEX IF NOT EXISTS idx_edges_target_type ON edges(target,edge_type);\
             CREATE TABLE IF NOT EXISTS semantic_sources(\
               source_key TEXT PRIMARY KEY,source_name TEXT NOT NULL,source_path TEXT NOT NULL,format TEXT NOT NULL,\
               source_sha256 TEXT NOT NULL,repository_commit TEXT,current_commit TEXT,stale INTEGER NOT NULL,\
               imported_at TEXT NOT NULL,node_count INTEGER NOT NULL,edge_count INTEGER NOT NULL,diagnostics_json TEXT NOT NULL);\
             CREATE TABLE IF NOT EXISTS semantic_source_nodes(\
               source_key TEXT NOT NULL,node_id TEXT NOT NULL,PRIMARY KEY(source_key,node_id));\
             CREATE TABLE IF NOT EXISTS semantic_source_edges(\
               source_key TEXT NOT NULL,source TEXT NOT NULL,target TEXT NOT NULL,edge_type TEXT NOT NULL,\
               evidence_ref TEXT NOT NULL,PRIMARY KEY(source_key,source,target,edge_type,evidence_ref));",
        )
        .map_err(|error| format!("GRAPH_DATABASE_INIT_FAILED:{error}"))?;
    let _ = connection.execute_batch(
        "CREATE VIRTUAL TABLE IF NOT EXISTS node_search USING fts5(\
           node_id UNINDEXED,name,qualified_name,path,kind,language,tokenize='unicode61 remove_diacritics 2');",
    );
    Ok(())
}

fn index_repository(
    project_root: &Path,
    unified: &Path,
    database: &Path,
    max_file_bytes: u64,
) -> Result<Value, String> {
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("GRAPH_PROJECT_RESOLVE_FAILED:{error}"))?;
    let scratch = unified.join("native-graph-structural");
    fs::create_dir_all(&scratch).map_err(|error| format!("GRAPH_SCRATCH_CREATE_FAILED:{error}"))?;
    let command = vec!["inspect".to_owned(), "stats".to_owned()];
    let _ = super::native_structural::execute(&command, &command, &project, &scratch)?;
    let structural_path = scratch.join("structural.sqlite3");
    let structural = Connection::open(&structural_path)
        .map_err(|error| format!("GRAPH_STRUCTURAL_OPEN_FAILED:{error}"))?;
    let mut graph = Connection::open(database)
        .map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;

    let old_hashes = load_file_hashes(&graph)?;
    let mut current = BTreeMap::<String, (String, String, String, bool, Vec<String>)>::new();
    {
        let mut statement = structural
            .prepare("SELECT path,content_hash,language,parser,semantic,diagnostics_json FROM structural_files ORDER BY path")
            .map_err(|error| format!("GRAPH_STRUCTURAL_FILES_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, i64>(4)? != 0,
                    row.get::<_, String>(5)?,
                ))
            })
            .map_err(|error| format!("GRAPH_STRUCTURAL_FILES_QUERY_FAILED:{error}"))?;
        for row in rows {
            let (path, digest, language, parser, semantic, diagnostics_json) =
                row.map_err(|error| format!("GRAPH_STRUCTURAL_FILES_ROW_FAILED:{error}"))?;
            let full = project.join(&path);
            let size = fs::metadata(&full).map(|value| value.len()).unwrap_or(0);
            if size > max_file_bytes {
                continue;
            }
            let diagnostics =
                serde_json::from_str::<Vec<String>>(&diagnostics_json).unwrap_or_default();
            current.insert(path, (digest, language, parser, semantic, diagnostics));
        }
    }

    let stale = old_hashes
        .keys()
        .filter(|path| !current.contains_key(*path))
        .cloned()
        .collect::<Vec<_>>();
    let mut changed = 0i64;
    let mut unchanged = 0i64;
    let transaction = graph
        .transaction()
        .map_err(|error| format!("GRAPH_TRANSACTION_FAILED:{error}"))?;
    for path in &stale {
        remove_local_file(&transaction, path)?;
    }

    for (relative, (digest, language, parser, semantic, diagnostics)) in &current {
        if old_hashes.get(relative) == Some(digest) {
            unchanged += 1;
            continue;
        }
        changed += 1;
        remove_local_file(&transaction, relative)?;
        let evidence_ref = format!("sha256:{digest}");
        let capability = if *semantic {
            "semantic"
        } else if parser != "lexical" {
            "syntax"
        } else {
            "lexical"
        };
        let module_id = node_id(relative, "module", relative, 1);
        let module_name = Path::new(relative)
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or(relative);
        transaction
            .execute(
                "INSERT OR REPLACE INTO nodes(node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                params![
                    module_id,
                    relative,
                    "module",
                    module_name,
                    relative,
                    1i64,
                    file_line_count(&project.join(relative)),
                    language,
                    evidence_ref,
                    metadata_json(json!({
                        "source": parser,
                        "exact_semantic": *semantic,
                        "exact_syntax": parser != "lexical",
                        "capability_level": capability,
                    }))?,
                ],
            )
            .map_err(|error| format!("GRAPH_MODULE_INSERT_FAILED:{error}"))?;

        let mut symbol_ids = BTreeMap::<String, String>::new();
        let mut statement = structural
            .prepare("SELECT name,qualified_name,kind,line,end_line,signature,confidence,parser FROM structural_symbols WHERE path=? ORDER BY line,name")
            .map_err(|error| format!("GRAPH_SYMBOLS_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map(params![relative], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)?,
                    row.get::<_, i64>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, f64>(6)?,
                    row.get::<_, String>(7)?,
                ))
            })
            .map_err(|error| format!("GRAPH_SYMBOLS_QUERY_FAILED:{error}"))?;
        for row in rows {
            let (name, qualified, kind, line, end_line, signature, confidence, symbol_parser) =
                row.map_err(|error| format!("GRAPH_SYMBOLS_ROW_FAILED:{error}"))?;
            let exact = *semantic;
            let canonical_kind = if exact {
                match kind.as_str() {
                    "class" => "class",
                    "function" | "method" => "function",
                    other => other,
                }
                .to_owned()
            } else {
                "symbol-candidate".to_owned()
            };
            let identifier = node_id(relative, &canonical_kind, &name, line);
            symbol_ids.insert(name.clone(), identifier.clone());
            symbol_ids.insert(qualified.clone(), identifier.clone());
            transaction
                .execute(
                    "INSERT OR REPLACE INTO nodes(node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    params![
                        identifier,
                        relative,
                        canonical_kind,
                        name,
                        format!("{relative}:{}", qualified),
                        line.max(1),
                        end_line.max(line).max(1),
                        language,
                        evidence_ref,
                        metadata_json(json!({
                            "source": symbol_parser,
                            "exact_semantic": exact,
                            "exact_syntax": symbol_parser != "lexical",
                            "capability_level": capability,
                            "signature": signature,
                            "structural_confidence": confidence,
                        }))?,
                    ],
                )
                .map_err(|error| format!("GRAPH_SYMBOL_INSERT_FAILED:{error}"))?;
            transaction
                .execute(
                    "INSERT OR REPLACE INTO edges(source,target,edge_type,confidence,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?)",
                    params![
                        module_id,
                        symbol_ids.get(&name).expect("inserted symbol"),
                        if exact { "defines" } else { "defines-candidate" },
                        if exact { 1.0 } else { confidence.min(0.65) },
                        evidence_ref,
                        metadata_json(json!({"source": symbol_parser, "exact_semantic": exact}))?,
                    ],
                )
                .map_err(|error| format!("GRAPH_DEFINE_EDGE_INSERT_FAILED:{error}"))?;
        }

        let mut edge_statement = structural
            .prepare("SELECT source_symbol,edge_type,target,target_path,confidence,metadata_json FROM structural_edges WHERE source_path=? ORDER BY line,edge_type,target")
            .map_err(|error| format!("GRAPH_EDGES_PREPARE_FAILED:{error}"))?;
        let edge_rows = edge_statement
            .query_map(params![relative], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, f64>(4)?,
                    row.get::<_, String>(5)?,
                ))
            })
            .map_err(|error| format!("GRAPH_EDGES_QUERY_FAILED:{error}"))?;
        for row in edge_rows {
            let (source_name, edge_type, target_name, target_path, confidence, raw_metadata) =
                row.map_err(|error| format!("GRAPH_EDGES_ROW_FAILED:{error}"))?;
            if edge_type == "defines" {
                continue;
            }
            let source_id = if source_name == "<module>" {
                module_id.clone()
            } else {
                symbol_ids
                    .get(&source_name)
                    .cloned()
                    .unwrap_or_else(|| module_id.clone())
            };
            let target_id = resolve_target_node(&transaction, &target_name, &target_path)?
                .unwrap_or_else(|| format!("external:{target_name}"));
            if target_id.starts_with("external:") {
                transaction
                    .execute(
                        "INSERT OR IGNORE INTO nodes(node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json) VALUES(?,?, 'external', ?, ?,0,0,'external',?,?)",
                        params![
                            target_id,
                            relative,
                            target_name,
                            target_id,
                            evidence_ref,
                            metadata_json(json!({"source":"external-reference","exact_semantic":false}))?,
                        ],
                    )
                    .map_err(|error| format!("GRAPH_EXTERNAL_INSERT_FAILED:{error}"))?;
            }
            let canonical_edge = if *semantic {
                edge_type.as_str()
            } else if edge_type == "imports" {
                "imports-candidate"
            } else {
                edge_type.as_str()
            };
            let mut metadata =
                serde_json::from_str::<Value>(&raw_metadata).unwrap_or_else(|_| json!({}));
            if let Value::Object(map) = &mut metadata {
                map.insert("source".to_owned(), Value::String(parser.clone()));
                map.insert("exact_semantic".to_owned(), Value::Bool(*semantic));
            }
            transaction
                .execute(
                    "INSERT OR REPLACE INTO edges(source,target,edge_type,confidence,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?)",
                    params![source_id, target_id, canonical_edge, confidence, evidence_ref, metadata_json(metadata)?],
                )
                .map_err(|error| format!("GRAPH_EDGE_INSERT_FAILED:{error}"))?;
        }
        transaction
            .execute(
                "INSERT OR REPLACE INTO files(path,sha256,language,indexed_at,analysis_key,detector,confidence,capability_level,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
                params![
                    relative,
                    digest,
                    language,
                    now_string(),
                    sha256_hex(format!("{digest}\0{language}\0{parser}\0{capability}").as_bytes()),
                    parser,
                    if *semantic { 1.0 } else { 0.8 },
                    capability,
                    metadata_json(json!({
                        "descriptor_source":"native-structural",
                        "encoding":"utf-8",
                        "generated":false,
                        "minified":false,
                        "adapter":false,
                        "diagnostics": diagnostics,
                    }))?,
                ],
            )
            .map_err(|error| format!("GRAPH_FILE_INSERT_FAILED:{error}"))?;
    }
    transaction
        .commit()
        .map_err(|error| format!("GRAPH_COMMIT_FAILED:{error}"))?;
    refresh_search(database)?;
    let mut value = graph_stats(database)?;
    let object = value.as_object_mut().expect("stats object");
    object.insert("ok".to_owned(), Value::Bool(true));
    object.insert("changed_files".to_owned(), Value::from(changed));
    object.insert("unchanged_files".to_owned(), Value::from(unchanged));
    object.insert("removed_files".to_owned(), Value::from(stale.len()));
    object.insert("binary_skipped".to_owned(), Value::from(0));
    object.insert(
        "oversized_skipped".to_owned(),
        Value::from(count_oversized(&project, max_file_bytes)?),
    );
    object.insert("errors".to_owned(), Value::Array(Vec::new()));
    object.insert("warnings".to_owned(), Value::Array(Vec::new()));
    object.insert(
        "language_platform".to_owned(),
        language_inventory(&project)?,
    );
    object.insert(
        "language_services".to_owned(),
        empty_service_inventory("analyzers"),
    );
    object.insert("lsp_services".to_owned(), empty_service_inventory("lsp"));
    object.insert(
        "repository_query".to_owned(),
        repository_query_stats(database)?,
    );
    object.insert("canonical_graph".to_owned(), Value::Bool(true));
    Ok(value)
}

fn load_file_hashes(connection: &Connection) -> Result<BTreeMap<String, String>, String> {
    let mut statement = connection
        .prepare("SELECT path,sha256 FROM files")
        .map_err(|error| format!("GRAPH_HASH_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .map_err(|error| format!("GRAPH_HASH_QUERY_FAILED:{error}"))?;
    let mut output = BTreeMap::new();
    for row in rows {
        let (path, digest) = row.map_err(|error| format!("GRAPH_HASH_ROW_FAILED:{error}"))?;
        output.insert(path, digest);
    }
    Ok(output)
}

fn remove_local_file(connection: &Connection, relative: &str) -> Result<(), String> {
    let ids = {
        let mut statement = connection
            .prepare("SELECT node_id FROM nodes WHERE path=? AND node_id NOT IN (SELECT node_id FROM semantic_source_nodes)")
            .map_err(|error| format!("GRAPH_REMOVE_IDS_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map(params![relative], |row| row.get::<_, String>(0))
            .map_err(|error| format!("GRAPH_REMOVE_IDS_QUERY_FAILED:{error}"))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("GRAPH_REMOVE_IDS_ROW_FAILED:{error}"))?
    };
    for id in ids {
        connection
            .execute(
                "DELETE FROM edges WHERE source=? OR target=?",
                params![id, id],
            )
            .map_err(|error| format!("GRAPH_REMOVE_EDGE_FAILED:{error}"))?;
        connection
            .execute("DELETE FROM nodes WHERE node_id=?", params![id])
            .map_err(|error| format!("GRAPH_REMOVE_NODE_FAILED:{error}"))?;
    }
    connection
        .execute("DELETE FROM files WHERE path=?", params![relative])
        .map_err(|error| format!("GRAPH_REMOVE_FILE_FAILED:{error}"))?;
    Ok(())
}

fn resolve_target_node(
    connection: &Connection,
    target: &str,
    target_path: &str,
) -> Result<Option<String>, String> {
    if !target_path.is_empty() {
        if let Some(value) = connection
            .query_row(
                "SELECT node_id FROM nodes WHERE path=? AND (name=? OR qualified_name LIKE ?) ORDER BY start_line LIMIT 1",
                params![target_path, short_name(target), format!("%{}", target)],
                |row| row.get::<_, String>(0),
            )
            .optional()
            .map_err(|error| format!("GRAPH_TARGET_PATH_QUERY_FAILED:{error}"))?
        {
            return Ok(Some(value));
        }
    }
    connection
        .query_row(
            "SELECT node_id FROM nodes WHERE name=? OR qualified_name LIKE ? ORDER BY path,start_line LIMIT 1",
            params![short_name(target), format!("%{}", target)],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(|error| format!("GRAPH_TARGET_QUERY_FAILED:{error}"))
}

fn node_id(path: &str, kind: &str, name: &str, line: i64) -> String {
    sha256_hex(format!("{path}\0{kind}\0{name}\0{line}").as_bytes())
}

fn short_name(value: &str) -> &str {
    value.rsplit(['.', ':']).next().unwrap_or(value)
}

fn file_line_count(path: &Path) -> i64 {
    fs::read_to_string(path)
        .map(|text| i64::try_from(text.lines().count().max(1)).unwrap_or(i64::MAX))
        .unwrap_or(1)
}

fn count_oversized(project: &Path, max_file_bytes: u64) -> Result<i64, String> {
    let mut count = 0i64;
    visit_files(project, &mut |path| {
        if fs::metadata(path)
            .map(|value| value.len() > max_file_bytes)
            .unwrap_or(false)
        {
            count += 1;
        }
        Ok(())
    })?;
    Ok(count)
}

fn visit_files<F>(root: &Path, callback: &mut F) -> Result<(), String>
where
    F: FnMut(&Path) -> Result<(), String>,
{
    let mut entries = fs::read_dir(root)
        .map_err(|error| format!("GRAPH_DIRECTORY_READ_FAILED:{}:{error}", root.display()))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("GRAPH_DIRECTORY_ENTRY_FAILED:{error}"))?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let file_type = entry
            .file_type()
            .map_err(|error| format!("GRAPH_FILE_TYPE_FAILED:{error}"))?;
        if file_type.is_dir() {
            if path
                .file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|name| IGNORE_PARTS.contains(&name))
            {
                continue;
            }
            visit_files(&path, callback)?;
        } else if file_type.is_file() {
            callback(&path)?;
        }
    }
    Ok(())
}

fn metadata_json(value: Value) -> Result<String, String> {
    serde_json::to_string(&sort_json(&value))
        .map_err(|error| format!("GRAPH_METADATA_JSON_FAILED:{error}"))
}

fn sort_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            let mut output = Map::new();
            for key in keys {
                output.insert(key.clone(), sort_json(&map[key]));
            }
            Value::Object(output)
        }
        Value::Array(rows) => Value::Array(rows.iter().map(sort_json).collect()),
        _ => value.clone(),
    }
}

fn now_string() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| format!("{}.{:09}Z", value.as_secs(), value.subsec_nanos()))
        .unwrap_or_else(|_| "0Z".to_owned())
}

fn graph_stats(path: &Path) -> Result<Value, String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let files = scalar_i64(&connection, "SELECT COUNT(*) FROM files")?;
    let nodes = scalar_i64(&connection, "SELECT COUNT(*) FROM nodes")?;
    let edges = scalar_i64(&connection, "SELECT COUNT(*) FROM edges")?;
    let languages = grouped(
        &connection,
        "SELECT language,COUNT(*) FROM files GROUP BY language ORDER BY language",
        "language",
        "files",
    )?;
    let capabilities = grouped(&connection, "SELECT capability_level,COUNT(*) FROM files GROUP BY capability_level ORDER BY capability_level", "capability_level", "files")?;
    let detectors = grouped(
        &connection,
        "SELECT detector,COUNT(*) FROM files GROUP BY detector ORDER BY detector",
        "detector",
        "files",
    )?;
    let unknown = scalar_i64(
        &connection,
        "SELECT COUNT(*) FROM files WHERE language LIKE 'unknown:%'",
    )?;
    let semantic = semantic_stats(&connection)?;
    Ok(json!({
        "files": files,
        "nodes": nodes,
        "edges": edges,
        "languages": languages,
        "capabilities": capabilities,
        "detectors": detectors,
        "unknown_language_files": unknown,
        "universal_text_fallback": true,
        "semantic_index_sources": semantic["semantic_index_sources"],
        "stale_semantic_index_sources": semantic["stale_semantic_index_sources"],
        "semantic_index_nodes": semantic["semantic_index_nodes"],
        "semantic_index_edges": semantic["semantic_index_edges"],
        "semantic_index_formats": semantic["semantic_index_formats"],
    }))
}

fn scalar_i64(connection: &Connection, sql: &str) -> Result<i64, String> {
    connection
        .query_row(sql, [], |row| row.get::<_, i64>(0))
        .map_err(|error| format!("GRAPH_SCALAR_QUERY_FAILED:{error}"))
}

fn grouped(
    connection: &Connection,
    sql: &str,
    key: &str,
    count: &str,
) -> Result<Vec<Value>, String> {
    let mut statement = connection
        .prepare(sql)
        .map_err(|error| format!("GRAPH_GROUP_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        })
        .map_err(|error| format!("GRAPH_GROUP_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        let (name, value) = row.map_err(|error| format!("GRAPH_GROUP_ROW_FAILED:{error}"))?;
        output.push(json!({key: name, count: value}));
    }
    Ok(output)
}

fn refresh_search(path: &Path) -> Result<(), String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    if connection.execute("DELETE FROM node_search", []).is_err() {
        return Ok(());
    }
    connection
        .execute(
            "INSERT INTO node_search(node_id,name,qualified_name,path,kind,language) SELECT node_id,name,qualified_name,path,kind,language FROM nodes WHERE kind!='external'",
            [],
        )
        .map_err(|error| format!("GRAPH_SEARCH_REFRESH_FAILED:{error}"))?;
    Ok(())
}

fn repository_query_stats(path: &Path) -> Result<Value, String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let graph_nodes = scalar_i64(
        &connection,
        "SELECT COUNT(*) FROM nodes WHERE kind!='external'",
    )?;
    let indexed = connection
        .query_row("SELECT COUNT(*) FROM node_search", [], |row| {
            row.get::<_, i64>(0)
        })
        .unwrap_or(graph_nodes);
    let backend = if connection
        .prepare("SELECT bm25(node_search) FROM node_search LIMIT 1")
        .is_ok()
    {
        "sqlite-fts5"
    } else {
        "sqlite-like"
    };
    Ok(json!({"backend":backend,"graph_nodes":graph_nodes,"indexed_nodes":indexed}))
}

fn query_repository(path: &Path, text: &str, limit: i64) -> Result<Vec<Value>, String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let normalized = text.trim().to_lowercase();
    let terms = tokens(text);
    let candidate_limit = (limit * 8).max(40);
    let mut rows = Vec::<Value>::new();
    let mut seen = BTreeSet::<String>::new();
    {
        let mut statement = connection
            .prepare("SELECT node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json FROM nodes WHERE kind!='external' AND (lower(name)=? OR lower(qualified_name)=?) ORDER BY path,start_line LIMIT ?")
            .map_err(|error| format!("GRAPH_QUERY_EXACT_PREPARE_FAILED:{error}"))?;
        let mapped = statement
            .query_map(
                params![normalized, normalized, candidate_limit],
                row_to_node,
            )
            .map_err(|error| format!("GRAPH_QUERY_EXACT_FAILED:{error}"))?;
        for row in mapped {
            let mut value = row.map_err(|error| format!("GRAPH_QUERY_EXACT_ROW_FAILED:{error}"))?;
            value["_rank"] = Value::from(120.0);
            seen.insert(value["node_id"].as_str().unwrap_or_default().to_owned());
            rows.push(value);
        }
    }
    if rows.is_empty() {
        let pattern = format!("%{normalized}%");
        let mut statement = connection
            .prepare("SELECT node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json FROM nodes WHERE kind!='external' AND (lower(name) LIKE ? OR lower(qualified_name) LIKE ? OR lower(path) LIKE ?) ORDER BY path,start_line LIMIT ?")
            .map_err(|error| format!("GRAPH_QUERY_LIKE_PREPARE_FAILED:{error}"))?;
        let mapped = statement
            .query_map(
                params![pattern, pattern, pattern, candidate_limit],
                row_to_node,
            )
            .map_err(|error| format!("GRAPH_QUERY_LIKE_FAILED:{error}"))?;
        for row in mapped {
            let mut value = row.map_err(|error| format!("GRAPH_QUERY_LIKE_ROW_FAILED:{error}"))?;
            value["_rank"] = Value::from(40.0);
            rows.push(value);
        }
    }
    let mut scored = Vec::<(f64, Value)>::new();
    for mut value in rows {
        let node_id = value["node_id"].as_str().unwrap_or_default();
        let degree = connection
            .query_row("SELECT COUNT(*) FROM (SELECT source FROM edges WHERE source=? UNION ALL SELECT target FROM edges WHERE target=?)", params![node_id,node_id], |row| row.get::<_,i64>(0))
            .unwrap_or(0);
        let metadata =
            serde_json::from_str::<Value>(value["metadata_json"].as_str().unwrap_or("{}"))
                .unwrap_or_else(|_| json!({}));
        let corpus = tokens(&format!(
            "{} {} {} {} {}",
            value["name"].as_str().unwrap_or_default(),
            value["qualified_name"].as_str().unwrap_or_default(),
            value["path"].as_str().unwrap_or_default(),
            value["kind"].as_str().unwrap_or_default(),
            value["language"].as_str().unwrap_or_default()
        ));
        let matched = terms.intersection(&corpus).cloned().collect::<Vec<_>>();
        let exact = normalized == value["name"].as_str().unwrap_or_default().to_lowercase()
            || normalized
                == value["qualified_name"]
                    .as_str()
                    .unwrap_or_default()
                    .to_lowercase();
        let semantic_bonus = if metadata["exact_semantic"].as_bool().unwrap_or(false) {
            10.0
        } else if metadata["exact_syntax"].as_bool().unwrap_or(false) {
            4.0
        } else {
            0.0
        };
        let rank = value["_rank"].as_f64().unwrap_or(0.0);
        let score = rank
            + semantic_bonus
            + (degree as f64 * 0.4).min(12.0)
            + if exact { 20.0 } else { 0.0 };
        value.as_object_mut().expect("node object").remove("_rank");
        value
            .as_object_mut()
            .expect("node object")
            .remove("metadata_json");
        value["metadata"] = metadata.clone();
        value["score"] = Value::from((score * 1_000_000.0).round() / 1_000_000.0);
        value["matched_terms"] = serde_json::to_value(matched)
            .map_err(|error| format!("GRAPH_QUERY_MATCHED_JSON_FAILED:{error}"))?;
        value["degree"] = Value::from(degree);
        value["semantic_status"] = Value::String(
            if metadata["exact_semantic"].as_bool().unwrap_or(false) {
                "exact"
            } else if metadata["exact_syntax"].as_bool().unwrap_or(false) {
                "syntax"
            } else {
                "candidate"
            }
            .to_owned(),
        );
        value["query_backend"] = Value::String(
            repository_query_stats(path)?["backend"]
                .as_str()
                .unwrap_or("sqlite-like")
                .to_owned(),
        );
        scored.push((score, value));
    }
    scored.sort_by(|left, right| {
        right
            .0
            .partial_cmp(&left.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.1["path"].as_str().cmp(&right.1["path"].as_str()))
            .then_with(|| {
                left.1["start_line"]
                    .as_i64()
                    .cmp(&right.1["start_line"].as_i64())
            })
    });
    Ok(scored
        .into_iter()
        .take(limit as usize)
        .map(|(_, value)| value)
        .collect())
}

fn row_to_node(row: &rusqlite::Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "node_id": row.get::<_,String>(0)?,
        "path": row.get::<_,String>(1)?,
        "kind": row.get::<_,String>(2)?,
        "name": row.get::<_,String>(3)?,
        "qualified_name": row.get::<_,String>(4)?,
        "start_line": row.get::<_,i64>(5)?,
        "end_line": row.get::<_,i64>(6)?,
        "language": row.get::<_,String>(7)?,
        "evidence_ref": row.get::<_,String>(8)?,
        "metadata_json": row.get::<_,String>(9)?,
    }))
}

fn tokens(value: &str) -> BTreeSet<String> {
    let mut output = BTreeSet::new();
    let mut current = String::new();
    for character in value.chars().flat_map(char::to_lowercase) {
        if character.is_alphanumeric() || matches!(character, '_' | '.' | '/' | ':' | '-') {
            current.push(character);
        } else {
            if current.chars().count() > 1 {
                output.insert(std::mem::take(&mut current));
            }
            current.clear();
        }
    }
    if current.chars().count() > 1 {
        output.insert(current);
    }
    output
}

fn impact(path: &Path, node_id: &str, max_depth: i64) -> Result<Value, String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let exists = connection
        .query_row(
            "SELECT 1 FROM nodes WHERE node_id=?",
            params![node_id],
            |_| Ok(()),
        )
        .optional()
        .map_err(|error| format!("GRAPH_IMPACT_ROOT_QUERY_FAILED:{error}"))?
        .is_some();
    if !exists {
        return Err(format!("GRAPH_NODE_NOT_FOUND:{node_id}"));
    }
    let allowed = [
        "calls",
        "imports",
        "depends-on",
        "implements",
        "overrides",
        "tested-by",
        "defines-candidate",
        "imports-candidate",
        "contains-identifier-candidate",
    ];
    let mut queue = VecDeque::from([(node_id.to_owned(), 0i64)]);
    let mut seen = BTreeSet::from([node_id.to_owned()]);
    let mut ordered = vec![node_id.to_owned()];
    let mut traversed = Vec::<Value>::new();
    while let Some((current, depth)) = queue.pop_front() {
        if depth >= max_depth {
            continue;
        }
        let mut statement = connection
            .prepare("SELECT source,target,edge_type,confidence,evidence_ref,metadata_json FROM edges WHERE target=? ORDER BY source,edge_type")
            .map_err(|error| format!("GRAPH_IMPACT_EDGE_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map(params![current], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, f64>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                ))
            })
            .map_err(|error| format!("GRAPH_IMPACT_EDGE_QUERY_FAILED:{error}"))?;
        for row in rows {
            let (source, target, edge_type, confidence, evidence_ref, metadata_json) =
                row.map_err(|error| format!("GRAPH_IMPACT_EDGE_ROW_FAILED:{error}"))?;
            if !allowed.contains(&edge_type.as_str()) {
                continue;
            }
            let metadata =
                serde_json::from_str::<Value>(&metadata_json).unwrap_or_else(|_| json!({}));
            traversed.push(json!({"source":source,"target":target,"edge_type":edge_type,"confidence":confidence,"evidence_ref":evidence_ref,"metadata":metadata}));
            if seen.insert(source.clone()) {
                ordered.push(source.clone());
                queue.push_back((source, depth + 1));
            }
        }
    }
    let mut nodes = Vec::<Value>::new();
    let mut exact_flags = Vec::<bool>::new();
    for id in &ordered {
        if let Some(mut value) = connection
            .query_row("SELECT node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json FROM nodes WHERE node_id=?", params![id], row_to_node)
            .optional()
            .map_err(|error| format!("GRAPH_IMPACT_NODE_QUERY_FAILED:{error}"))?
        {
            let metadata = serde_json::from_str::<Value>(value["metadata_json"].as_str().unwrap_or("{}"))
                .unwrap_or_else(|_| json!({}));
            exact_flags.push(metadata["exact_semantic"].as_bool().unwrap_or(false));
            value.as_object_mut().expect("node object").remove("metadata_json");
            value["metadata"] = metadata;
            nodes.push(value);
        }
    }
    let tests = nodes
        .iter()
        .filter(|node| {
            node["path"]
                .as_str()
                .unwrap_or_default()
                .to_lowercase()
                .contains("test")
                || node["kind"]
                    .as_str()
                    .unwrap_or_default()
                    .starts_with("test")
        })
        .cloned()
        .collect::<Vec<_>>();
    let edge_exact = traversed.iter().all(|edge| {
        edge["metadata"]["exact_semantic"]
            .as_bool()
            .unwrap_or(false)
    });
    let exact = !exact_flags.is_empty() && exact_flags.iter().all(|value| *value) && edge_exact;
    Ok(json!({
        "root": node_id,
        "impacted": nodes,
        "affected_tests": tests,
        "edges": traversed,
        "exact_evidence": exact,
        "candidate_evidence_present": exact_flags.iter().any(|value| !*value) || !edge_exact,
    }))
}

fn language_action(
    arguments: &[String],
    project: &Path,
    unified: &Path,
    database: &Path,
) -> Result<Value, String> {
    let operation = positional_after(arguments, "language", 0)?;
    match operation {
        "inventory" | "doctor" => language_status(project, database),
        "detect" => {
            let raw = positional_after(arguments, "language", 1)?;
            let source = safe_project_path(project, raw, true)?;
            if !source.is_file() {
                return Err("language detection path must be a file".to_owned());
            }
            let data = fs::read(&source)
                .map_err(|error| format!("LANGUAGE_SOURCE_READ_FAILED:{error}"))?;
            let detection = detect_language(project, &source, &data)?;
            Ok(json!({
                "ok": true,
                "path": source.strip_prefix(fs::canonicalize(project).map_err(|error| format!("LANGUAGE_PROJECT_RESOLVE_FAILED:{error}"))?).map_err(|_| "LANGUAGE_PATH_ESCAPE".to_owned())?.to_string_lossy().replace('\\', "/"),
                "detection": detection.json(),
            }))
        }
        "index" => {
            let max = option_i64(arguments, "--max-file-bytes", 2_000_000)?.max(1) as u64;
            index_repository(project, unified, database, max)
        }
        "query" => {
            let query = positional_after(arguments, "language", 1)?;
            let limit = option_i64(arguments, "--limit", 20)?.clamp(1, 200);
            Ok(json!({"ok":true,"query":query,"results":query_repository(database,query,limit)?}))
        }
        "import-index" => import_semantic_index(arguments, project, database),
        "remove-index" => {
            let source_key = positional_after(arguments, "language", 1)?;
            remove_semantic_source(database, source_key)
        }
        other => Err(format!("LANGUAGE_OPERATION_UNSUPPORTED:{other}")),
    }
}

fn language_status(project: &Path, database: &Path) -> Result<Value, String> {
    let registry = language_inventory(project)?;
    let semantic = Connection::open(database)
        .map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))
        .and_then(|connection| semantic_stats(&connection))?;
    let mut value = json!({
        "ok": true,
        "universal_text_fallback": true,
        "language_registry": registry,
        "sandboxed_analyzers": empty_service_inventory("analyzers"),
        "lsp_services": empty_service_inventory("lsp"),
        "semantic_indexes": semantic,
        "evidence_levels": ["lexical","syntax","semantic"],
        "claim_boundary": "declared support is not live certification; unknown and future text languages remain navigable, while exact semantic claims require validated parser, analyzer, LSP, LSIF or SCIP evidence",
        "universal_claim_boundary": "unknown and future text languages remain navigable; exact type, call, implementation and override claims require validated parser, analyzer, LSP, LSIF or SCIP evidence",
        "tree_sitter": {"installed":false,"available_languages":[],"source":"native-structural"},
        "repository_query": repository_query_stats(database)?,
        "canonical_graph": true,
    });
    let declared = value["language_registry"]["registered_languages"]
        .as_i64()
        .unwrap_or(0);
    value["declared"] = Value::from(declared);
    value["available"] = Value::from(0);
    Ok(value)
}

fn language_inventory(project: &Path) -> Result<Value, String> {
    let mut descriptors = builtin_descriptors();
    let manifest_root = project.join(".syntavra/languages");
    if manifest_root.is_dir() {
        let mut entries = fs::read_dir(&manifest_root)
            .map_err(|error| format!("LANGUAGE_MANIFEST_DIRECTORY_READ_FAILED:{error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("LANGUAGE_MANIFEST_ENTRY_FAILED:{error}"))?;
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            let path = entry.path();
            if path.extension().and_then(|value| value.to_str()) != Some("json") {
                continue;
            }
            let value = serde_json::from_slice::<Value>(
                &fs::read(&path)
                    .map_err(|error| format!("LANGUAGE_MANIFEST_READ_FAILED:{error}"))?,
            )
            .map_err(|error| format!("LANGUAGE_MANIFEST_JSON_INVALID:{error}"))?;
            let id = value["id"]
                .as_str()
                .or_else(|| value["language_id"].as_str())
                .unwrap_or_default()
                .trim()
                .to_lowercase();
            if id.is_empty() {
                continue;
            }
            let suffixes = value["suffixes"]
                .as_array()
                .map(|rows| {
                    rows.iter()
                        .filter_map(Value::as_str)
                        .map(|item| {
                            if item.starts_with('.') {
                                item.to_lowercase()
                            } else {
                                format!(".{}", item.to_lowercase())
                            }
                        })
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            descriptors.insert(
                id.clone(),
                (
                    suffixes,
                    "repository-manifest".to_owned(),
                    "lexical".to_owned(),
                ),
            );
        }
    }
    let rows = descriptors.iter().map(|(id,(suffixes,source,capability))| json!({"id":id,"suffixes":suffixes,"source":source,"capabilities":[capability]})).collect::<Vec<_>>();
    Ok(json!({
        "registered_languages": descriptors.len(),
        "adapters": [],
        "descriptors": rows,
        "diagnostics": [],
        "universal_text_fallback": true,
    }))
}

fn builtin_descriptors() -> BTreeMap<String, (Vec<String>, String, String)> {
    let rows: &[(&str, &[&str], &str)] = &[
        ("python", &[".py", ".pyi", ".pyw"], "semantic"),
        ("javascript", &[".js", ".jsx", ".mjs", ".cjs"], "lexical"),
        ("typescript", &[".ts", ".tsx", ".mts", ".cts"], "lexical"),
        ("rust", &[".rs"], "lexical"),
        ("go", &[".go"], "lexical"),
        ("java", &[".java"], "lexical"),
        ("kotlin", &[".kt", ".kts"], "lexical"),
        ("csharp", &[".cs", ".csx"], "lexical"),
        ("c", &[".c", ".h"], "lexical"),
        (
            "cpp",
            &[".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"],
            "lexical",
        ),
        ("shell", &[".sh", ".bash", ".zsh", ".ksh"], "lexical"),
        ("powershell", &[".ps1", ".psm1", ".psd1"], "lexical"),
        ("ruby", &[".rb", ".rake", ".gemspec"], "lexical"),
        ("php", &[".php", ".phtml"], "lexical"),
        ("lua", &[".lua"], "lexical"),
        ("sql", &[".sql", ".ddl", ".dml"], "lexical"),
        ("json", &[".json", ".jsonc", ".json5"], "lexical"),
        ("yaml", &[".yaml", ".yml"], "lexical"),
        ("toml", &[".toml"], "lexical"),
        ("markdown", &[".md", ".mdx", ".markdown"], "lexical"),
        ("html", &[".html", ".htm", ".xhtml"], "lexical"),
        ("css", &[".css"], "lexical"),
        ("scss", &[".scss"], "lexical"),
        ("swift", &[".swift"], "lexical"),
        ("zig", &[".zig"], "lexical"),
        ("dart", &[".dart"], "lexical"),
        ("haskell", &[".hs", ".lhs"], "lexical"),
        ("elixir", &[".ex", ".exs"], "lexical"),
        ("erlang", &[".erl", ".hrl"], "lexical"),
        ("clojure", &[".clj", ".cljs", ".cljc", ".edn"], "lexical"),
        ("solidity", &[".sol"], "lexical"),
        ("terraform", &[".tf", ".tfvars"], "lexical"),
        ("nix", &[".nix"], "lexical"),
        ("protobuf", &[".proto"], "lexical"),
    ];
    rows.iter()
        .map(|(id, suffixes, capability)| {
            (
                (*id).to_owned(),
                (
                    suffixes.iter().map(|value| (*value).to_owned()).collect(),
                    "builtin".to_owned(),
                    (*capability).to_owned(),
                ),
            )
        })
        .collect()
}

fn detect_language(project: &Path, path: &Path, data: &[u8]) -> Result<Detection, String> {
    if data.iter().take(8192).any(|byte| *byte == 0) {
        return Ok(Detection {
            language_id: "binary".to_owned(),
            confidence: 1.0,
            evidence: "binary-nul".to_owned(),
            capability_level: "none".to_owned(),
            descriptor_source: "builtin".to_owned(),
            text_encoding: None,
            binary: true,
            generated: false,
            minified: false,
            diagnostics: Vec::new(),
            candidates: Vec::new(),
        });
    }
    let text = String::from_utf8_lossy(data);
    let generated = text.lines().take(8).any(|line| {
        let value = line.to_lowercase();
        value.contains("generated file")
            || value.contains("generated code")
            || value.contains("do not edit")
            || value.contains("auto-generated")
    });
    let minified = text.lines().take(4).any(|line| line.len() > 10_000);
    let descriptors = builtin_descriptors();
    let filename = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_lowercase();
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| format!(".{}", value.to_lowercase()));
    if extension.is_none() && text.starts_with("#!") {
        let first = text.lines().next().unwrap_or_default().to_lowercase();
        let id = if first.contains("python") {
            Some("python")
        } else if first.contains("bash") || first.contains("/sh") || first.contains(" zsh") {
            Some("shell")
        } else if first.contains("pwsh") || first.contains("powershell") {
            Some("powershell")
        } else if first.contains("node") || first.contains("deno") {
            Some("javascript")
        } else {
            None
        };
        if let Some(id) = id {
            return Ok(Detection {
                language_id: id.to_owned(),
                confidence: 0.98,
                evidence: "shebang".to_owned(),
                capability_level: if id == "python" {
                    "semantic"
                } else {
                    "lexical"
                }
                .to_owned(),
                descriptor_source: "builtin".to_owned(),
                text_encoding: Some("utf-8".to_owned()),
                binary: false,
                generated,
                minified,
                diagnostics: Vec::new(),
                candidates: vec![id.to_owned()],
            });
        }
    }
    if let Some(ext) = extension {
        let matches = descriptors
            .iter()
            .filter(|(_, (suffixes, _, _))| suffixes.contains(&ext))
            .map(|(id, (_, source, cap))| (id.clone(), source.clone(), cap.clone()))
            .collect::<Vec<_>>();
        if let Some((id, source, capability)) = matches.first() {
            return Ok(Detection {
                language_id: id.clone(),
                confidence: if matches.len() == 1 { 0.97 } else { 0.72 },
                evidence: "suffix".to_owned(),
                capability_level: capability.clone(),
                descriptor_source: source.clone(),
                text_encoding: Some("utf-8".to_owned()),
                binary: false,
                generated,
                minified,
                diagnostics: Vec::new(),
                candidates: matches.iter().map(|(id, _, _)| id.clone()).collect(),
            });
        }
        let custom = project.join(".syntavra/languages");
        if custom.is_dir() {
            for entry in fs::read_dir(custom)
                .map_err(|error| format!("LANGUAGE_MANIFEST_DIRECTORY_READ_FAILED:{error}"))?
            {
                let path = entry
                    .map_err(|error| format!("LANGUAGE_MANIFEST_ENTRY_FAILED:{error}"))?
                    .path();
                if path.extension().and_then(|value| value.to_str()) != Some("json") {
                    continue;
                }
                let value = serde_json::from_slice::<Value>(
                    &fs::read(&path)
                        .map_err(|error| format!("LANGUAGE_MANIFEST_READ_FAILED:{error}"))?,
                )
                .map_err(|error| format!("LANGUAGE_MANIFEST_JSON_INVALID:{error}"))?;
                let suffixes = value["suffixes"].as_array().cloned().unwrap_or_default();
                if suffixes.iter().filter_map(Value::as_str).any(|item| {
                    ext == if item.starts_with('.') {
                        item.to_lowercase()
                    } else {
                        format!(".{}", item.to_lowercase())
                    }
                }) {
                    let id = value["id"]
                        .as_str()
                        .or_else(|| value["language_id"].as_str())
                        .unwrap_or("unknown")
                        .to_lowercase();
                    return Ok(Detection {
                        language_id: id.clone(),
                        confidence: 0.97,
                        evidence: "suffix".to_owned(),
                        capability_level: "lexical".to_owned(),
                        descriptor_source: "repository-manifest".to_owned(),
                        text_encoding: Some("utf-8".to_owned()),
                        binary: false,
                        generated,
                        minified,
                        diagnostics: Vec::new(),
                        candidates: vec![id],
                    });
                }
            }
        }
        return Ok(Detection {
            language_id: format!("unknown:{}", ext.trim_start_matches('.')),
            confidence: 0.2,
            evidence: "unregistered-text".to_owned(),
            capability_level: "lexical".to_owned(),
            descriptor_source: "fallback".to_owned(),
            text_encoding: Some("utf-8".to_owned()),
            binary: false,
            generated,
            minified,
            diagnostics: Vec::new(),
            candidates: Vec::new(),
        });
    }
    let id = match filename.as_str() {
        "makefile" | "gnumakefile" => "make",
        "dockerfile" => "dockerfile",
        "cmakelists.txt" => "cmake",
        _ => "unknown:extensionless",
    };
    Ok(Detection {
        language_id: id.to_owned(),
        confidence: if id.starts_with("unknown:") {
            0.2
        } else {
            0.95
        },
        evidence: "filename".to_owned(),
        capability_level: "lexical".to_owned(),
        descriptor_source: if id.starts_with("unknown:") {
            "fallback"
        } else {
            "builtin"
        }
        .to_owned(),
        text_encoding: Some("utf-8".to_owned()),
        binary: false,
        generated,
        minified,
        diagnostics: Vec::new(),
        candidates: Vec::new(),
    })
}

fn empty_service_inventory(kind: &str) -> Value {
    json!({"services":0,"declared":0,"available":0,"records":[],"diagnostics":[],"kind":kind})
}

fn semantic_stats(connection: &Connection) -> Result<Value, String> {
    let sources = scalar_i64(connection, "SELECT COUNT(*) FROM semantic_sources")?;
    let stale = scalar_i64(
        connection,
        "SELECT COUNT(*) FROM semantic_sources WHERE stale=1",
    )?;
    let nodes = scalar_i64(connection, "SELECT COUNT(*) FROM semantic_source_nodes")?;
    let edges = scalar_i64(connection, "SELECT COUNT(*) FROM semantic_source_edges")?;
    let formats = grouped(
        connection,
        "SELECT format,COUNT(*) FROM semantic_sources GROUP BY format ORDER BY format",
        "format",
        "sources",
    )?;
    Ok(
        json!({"semantic_index_sources":sources,"stale_semantic_index_sources":stale,"semantic_index_nodes":nodes,"semantic_index_edges":edges,"semantic_index_formats":formats}),
    )
}

fn import_semantic_index(
    arguments: &[String],
    project: &Path,
    database: &Path,
) -> Result<Value, String> {
    let path_raw = positional_after(arguments, "language", 1)?;
    let path = safe_project_or_absolute(project, path_raw, true)?;
    let format = option_value(arguments, "--format")?.unwrap_or_else(|| "auto".to_owned());
    let repository_commit = option_value(arguments, "--repository-commit")?;
    let current_commit = option_value(arguments, "--current-commit")?.or_else(|| git_head(project));
    let allow_stale = has_flag(arguments, "--allow-stale");
    let source_name = option_value(arguments, "--source-name")?;
    semantic_index_import(
        database,
        &path,
        &format,
        repository_commit.as_deref(),
        current_commit.as_deref(),
        allow_stale,
        source_name.as_deref(),
    )
}

fn semantic_index_import(
    database: &Path,
    path: &Path,
    format: &str,
    repository_commit: Option<&str>,
    current_commit: Option<&str>,
    allow_stale: bool,
    source_name: Option<&str>,
) -> Result<Value, String> {
    let raw = fs::read(path).map_err(|error| format!("SEMANTIC_INDEX_READ_FAILED:{error}"))?;
    let source_sha = sha256_hex(&raw);
    let value = serde_json::from_slice::<Value>(&raw)
        .map_err(|error| format!("SEMANTIC_INDEX_JSON_INVALID:{error}"))?;
    let normalized_format = if format == "auto" {
        if value.get("documents").is_some() || value.get("metadata").is_some() {
            "scip-json"
        } else {
            "lsif"
        }
    } else {
        format
    };
    if !matches!(normalized_format, "lsif" | "scip-json") {
        return Err("SEMANTIC_INDEX_FORMAT_INVALID".to_owned());
    }
    let index_commit = repository_commit.map(str::to_lowercase);
    let current = current_commit.map(str::to_lowercase);
    let stale = index_commit
        .as_ref()
        .zip(current.as_ref())
        .is_some_and(|(left, right)| left != right);
    if stale && !allow_stale {
        return Err(format!(
            "semantic index commit mismatch: index={} repository={}",
            index_commit.as_deref().unwrap_or(""),
            current.as_deref().unwrap_or("")
        ));
    }
    let identity = source_name
        .map(str::to_owned)
        .unwrap_or_else(|| path.to_string_lossy().into_owned());
    let source_key = format!(
        "semantic-source:{}",
        sha256_hex(format!("{}\0{}", normalized_format, identity).as_bytes())
    );
    let mut connection = Connection::open(database)
        .map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let transaction = connection
        .transaction()
        .map_err(|error| format!("SEMANTIC_TRANSACTION_FAILED:{error}"))?;
    remove_semantic_source_tx(&transaction, &source_key)?;
    let (nodes, edges) = extract_semantic_bundle(&value, normalized_format, &source_sha, stale)?;
    for node in &nodes {
        let node_id = node["node_id"]
            .as_str()
            .ok_or_else(|| "SEMANTIC_NODE_ID_INVALID".to_owned())?;
        let collision = transaction
            .query_row(
                "SELECT 1 FROM nodes WHERE node_id=?",
                params![node_id],
                |_| Ok(()),
            )
            .optional()
            .map_err(|error| format!("SEMANTIC_COLLISION_QUERY_FAILED:{error}"))?
            .is_some();
        if collision {
            return Err(format!("semantic index node id collision: {node_id}"));
        }
    }
    transaction.execute("INSERT INTO semantic_sources(source_key,source_name,source_path,format,source_sha256,repository_commit,current_commit,stale,imported_at,node_count,edge_count,diagnostics_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", params![source_key, source_name.unwrap_or_else(|| path.file_name().and_then(|value|value.to_str()).unwrap_or("index")), path.to_string_lossy(), normalized_format, source_sha, index_commit, current, if stale{1}else{0}, now_string(), nodes.len() as i64, edges.len() as i64, "[]"]).map_err(|error| format!("SEMANTIC_SOURCE_INSERT_FAILED:{error}"))?;
    for node in &nodes {
        transaction.execute("INSERT INTO nodes(node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)", params![node["node_id"].as_str(),node["path"].as_str(),node["kind"].as_str(),node["name"].as_str(),node["qualified_name"].as_str(),node["start_line"].as_i64(),node["end_line"].as_i64(),node["language"].as_str(),node["evidence_ref"].as_str(),metadata_json(node["metadata"].clone())?]).map_err(|error| format!("SEMANTIC_NODE_INSERT_FAILED:{error}"))?;
        transaction
            .execute(
                "INSERT INTO semantic_source_nodes(source_key,node_id) VALUES(?,?)",
                params![source_key, node["node_id"].as_str()],
            )
            .map_err(|error| format!("SEMANTIC_SOURCE_NODE_INSERT_FAILED:{error}"))?;
    }
    for edge in &edges {
        transaction.execute("INSERT OR REPLACE INTO edges(source,target,edge_type,confidence,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?)", params![edge["source"].as_str(),edge["target"].as_str(),edge["edge_type"].as_str(),edge["confidence"].as_f64(),edge["evidence_ref"].as_str(),metadata_json(edge["metadata"].clone())?]).map_err(|error| format!("SEMANTIC_EDGE_INSERT_FAILED:{error}"))?;
        transaction.execute("INSERT INTO semantic_source_edges(source_key,source,target,edge_type,evidence_ref) VALUES(?,?,?,?,?)", params![source_key,edge["source"].as_str(),edge["target"].as_str(),edge["edge_type"].as_str(),edge["evidence_ref"].as_str()]).map_err(|error| format!("SEMANTIC_SOURCE_EDGE_INSERT_FAILED:{error}"))?;
    }
    transaction
        .commit()
        .map_err(|error| format!("SEMANTIC_COMMIT_FAILED:{error}"))?;
    refresh_search(database)?;
    Ok(
        json!({"ok":true,"source_key":source_key,"format":normalized_format,"source_sha256":source_sha,"repository_commit":index_commit,"current_commit":current,"stale":stale,"evidence_status":if stale{"candidate-stale"}else{"exact"},"nodes":nodes.len(),"edges":edges.len(),"diagnostics":[]}),
    )
}

fn extract_semantic_bundle(
    value: &Value,
    format: &str,
    source_sha: &str,
    stale: bool,
) -> Result<(Vec<Value>, Vec<Value>), String> {
    let mut nodes = Vec::<Value>::new();
    let mut edges = Vec::<Value>::new();
    if let Some(raw_nodes) = value.get("nodes").and_then(Value::as_array) {
        for (index, row) in raw_nodes.iter().enumerate() {
            if !row.is_object() {
                continue;
            }
            let name = row["name"].as_str().unwrap_or("symbol");
            let path = row["path"].as_str().unwrap_or("");
            let node_id = row["node_id"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| {
                    format!(
                        "semantic:{}",
                        sha256_hex(format!("{path}\0{name}\0{index}").as_bytes())
                    )
                });
            let mut metadata = row["metadata"].clone();
            if !metadata.is_object() {
                metadata = json!({});
            }
            metadata["semantic_source_sha256"] = Value::String(source_sha.to_owned());
            metadata["stale_semantic_index"] = Value::Bool(stale);
            metadata["exact_semantic"] = Value::Bool(!stale);
            nodes.push(json!({"node_id":node_id,"path":path,"kind":row["kind"].as_str().unwrap_or("symbol"),"name":name,"qualified_name":row["qualified_name"].as_str().unwrap_or(name),"start_line":row["start_line"].as_i64().unwrap_or(1),"end_line":row["end_line"].as_i64().unwrap_or(1),"language":row["language"].as_str().unwrap_or("unknown"),"evidence_ref":row["evidence_ref"].as_str().unwrap_or("sha256:index"),"metadata":metadata}));
        }
    } else if format == "scip-json" {
        if let Some(documents) = value.get("documents").and_then(Value::as_array) {
            for document in documents {
                let path = document["relative_path"]
                    .as_str()
                    .or_else(|| document["relativePath"].as_str())
                    .unwrap_or("");
                if let Some(occurrences) = document["occurrences"].as_array() {
                    for (index, occurrence) in occurrences.iter().enumerate() {
                        let symbol = occurrence["symbol"].as_str().unwrap_or("");
                        if symbol.is_empty() {
                            continue;
                        }
                        let name = short_name(symbol).trim_matches(['`', '.', '#']).to_owned();
                        let range = occurrence["range"].as_array().cloned().unwrap_or_default();
                        let line = range.first().and_then(Value::as_i64).unwrap_or(0) + 1;
                        let node_id = format!(
                            "scip:{}",
                            sha256_hex(format!("{path}\0{symbol}\0{index}").as_bytes())
                        );
                        nodes.push(json!({"node_id":node_id,"path":path,"kind":"symbol","name":name,"qualified_name":symbol,"start_line":line,"end_line":line,"language":Path::new(path).extension().and_then(|v|v.to_str()).unwrap_or("unknown"),"evidence_ref":format!("sha256:{source_sha}"),"metadata":{"source":"scip-json","exact_semantic":!stale,"stale_semantic_index":stale,"semantic_source_sha256":source_sha}}));
                    }
                }
            }
        }
    }
    if let Some(raw_edges) = value.get("edges").and_then(Value::as_array) {
        for row in raw_edges {
            let source = row["source"].as_str().unwrap_or("");
            let target = row["target"].as_str().unwrap_or("");
            if source.is_empty() || target.is_empty() {
                continue;
            }
            edges.push(json!({"source":source,"target":target,"edge_type":row["edge_type"].as_str().unwrap_or("references"),"confidence":if stale{row["confidence"].as_f64().unwrap_or(1.0).min(0.6)}else{row["confidence"].as_f64().unwrap_or(1.0)},"evidence_ref":row["evidence_ref"].as_str().unwrap_or("sha256:index"),"metadata":{"source":format,"exact_semantic":!stale,"stale_semantic_index":stale,"semantic_source_sha256":source_sha}}));
        }
    }
    let ids = nodes
        .iter()
        .filter_map(|node| node["node_id"].as_str())
        .collect::<BTreeSet<_>>();
    edges.retain(|edge| {
        edge["source"].as_str().is_some_and(|id| ids.contains(id))
            && edge["target"].as_str().is_some_and(|id| ids.contains(id))
    });
    Ok((nodes, edges))
}

fn remove_semantic_source(database: &Path, source_key: &str) -> Result<Value, String> {
    let mut connection = Connection::open(database)
        .map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let exists = connection
        .query_row(
            "SELECT 1 FROM semantic_sources WHERE source_key=?",
            params![source_key],
            |_| Ok(()),
        )
        .optional()
        .map_err(|error| format!("SEMANTIC_SOURCE_QUERY_FAILED:{error}"))?
        .is_some();
    if !exists {
        return Ok(json!({"ok":true,"removed":false,"source_key":source_key}));
    }
    let transaction = connection
        .transaction()
        .map_err(|error| format!("SEMANTIC_REMOVE_TRANSACTION_FAILED:{error}"))?;
    remove_semantic_source_tx(&transaction, source_key)?;
    transaction
        .commit()
        .map_err(|error| format!("SEMANTIC_REMOVE_COMMIT_FAILED:{error}"))?;
    refresh_search(database)?;
    Ok(json!({"ok":true,"removed":true,"source_key":source_key}))
}

fn remove_semantic_source_tx(connection: &Connection, source_key: &str) -> Result<(), String> {
    let nodes = {
        let mut statement = connection
            .prepare("SELECT node_id FROM semantic_source_nodes WHERE source_key=?")
            .map_err(|error| format!("SEMANTIC_REMOVE_NODE_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map(params![source_key], |row| row.get::<_, String>(0))
            .map_err(|error| format!("SEMANTIC_REMOVE_NODE_QUERY_FAILED:{error}"))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("SEMANTIC_REMOVE_NODE_ROW_FAILED:{error}"))?
    };
    let edges = {
        let mut statement=connection.prepare("SELECT source,target,edge_type,evidence_ref FROM semantic_source_edges WHERE source_key=?").map_err(|error|format!("SEMANTIC_REMOVE_EDGE_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map(params![source_key], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                ))
            })
            .map_err(|error| format!("SEMANTIC_REMOVE_EDGE_QUERY_FAILED:{error}"))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("SEMANTIC_REMOVE_EDGE_ROW_FAILED:{error}"))?
    };
    for (source, target, edge_type, evidence_ref) in edges {
        connection
            .execute(
                "DELETE FROM edges WHERE source=? AND target=? AND edge_type=? AND evidence_ref=?",
                params![source, target, edge_type, evidence_ref],
            )
            .map_err(|error| format!("SEMANTIC_REMOVE_GRAPH_EDGE_FAILED:{error}"))?;
    }
    for node in nodes {
        connection
            .execute("DELETE FROM nodes WHERE node_id=?", params![node])
            .map_err(|error| format!("SEMANTIC_REMOVE_GRAPH_NODE_FAILED:{error}"))?;
    }
    connection
        .execute(
            "DELETE FROM semantic_source_edges WHERE source_key=?",
            params![source_key],
        )
        .map_err(|error| format!("SEMANTIC_REMOVE_OWNED_EDGES_FAILED:{error}"))?;
    connection
        .execute(
            "DELETE FROM semantic_source_nodes WHERE source_key=?",
            params![source_key],
        )
        .map_err(|error| format!("SEMANTIC_REMOVE_OWNED_NODES_FAILED:{error}"))?;
    connection
        .execute(
            "DELETE FROM semantic_sources WHERE source_key=?",
            params![source_key],
        )
        .map_err(|error| format!("SEMANTIC_REMOVE_SOURCE_FAILED:{error}"))?;
    Ok(())
}

fn semantic_import(
    arguments: &[String],
    project: &Path,
    database: &Path,
    unified: &Path,
) -> Result<Value, String> {
    let format = positional_after(arguments, "semantic-import", 0)?;
    let raw_path = positional_after(arguments, "semantic-import", 1)?;
    let path = safe_project_or_absolute(project, raw_path, true)?;
    let repository_commit =
        option_value(arguments, "--repository-commit")?.unwrap_or_else(|| "unknown".to_owned());
    let allow_stale = has_flag(arguments, "--allow-stale");
    match format {
        "lsif" | "scip-json" => semantic_index_import(
            database,
            &path,
            format,
            Some(&repository_commit),
            git_head(project).as_deref(),
            allow_stale,
            None,
        ),
        "coverage" => {
            let value = serde_json::from_slice::<Value>(
                &fs::read(&path)
                    .map_err(|error| format!("RUNTIME_EVIDENCE_COVERAGE_READ_FAILED:{error}"))?,
            )
            .map_err(|error| format!("RUNTIME_EVIDENCE_COVERAGE_JSON_INVALID:{error}"))?;
            let test_id = option_value(arguments, "--test-id")?
                .unwrap_or_else(|| "coverage-suite".to_owned());
            runtime_import_coverage(unified, &value, &test_id, &repository_commit)
        }
        "trace" => {
            let value = serde_json::from_slice::<Value>(
                &fs::read(&path)
                    .map_err(|error| format!("RUNTIME_EVIDENCE_TRACE_READ_FAILED:{error}"))?,
            )
            .map_err(|error| format!("RUNTIME_EVIDENCE_TRACE_JSON_INVALID:{error}"))?;
            let spans = value
                .get("spans")
                .unwrap_or(&value)
                .as_array()
                .ok_or_else(|| {
                    "trace import requires a list or {'spans': [...]} object".to_owned()
                })?;
            runtime_import_trace(unified, spans, &repository_commit)
        }
        _ => Err("SEMANTIC_IMPORT_FORMAT_INVALID".to_owned()),
    }
}

fn runtime_evidence_database(unified: &Path) -> PathBuf {
    unified.join("runtime-evidence.sqlite3")
}
fn initialize_runtime_evidence(path: &Path) -> Result<(), String> {
    let connection =
        Connection::open(path).map_err(|error| format!("RUNTIME_EVIDENCE_OPEN_FAILED:{error}"))?;
    connection.execute_batch("PRAGMA journal_mode=WAL;CREATE TABLE IF NOT EXISTS nodes(node_id TEXT PRIMARY KEY,kind TEXT NOT NULL,label TEXT NOT NULL,source TEXT NOT NULL,confidence REAL NOT NULL,repository_commit TEXT NOT NULL,metadata_json TEXT NOT NULL);CREATE TABLE IF NOT EXISTS edges(evidence TEXT PRIMARY KEY,source TEXT NOT NULL,target TEXT NOT NULL,relation TEXT NOT NULL,confidence REAL NOT NULL,repository_commit TEXT NOT NULL,observed_at TEXT NOT NULL,metadata_json TEXT NOT NULL);CREATE INDEX IF NOT EXISTS idx_evidence_source ON edges(source,relation);CREATE INDEX IF NOT EXISTS idx_evidence_target ON edges(target,relation);").map_err(|error|format!("RUNTIME_EVIDENCE_INIT_FAILED:{error}"))?;
    Ok(())
}
fn evidence_node_id(kind: &str, label: &str, source: &str) -> String {
    sha256_hex(format!("{kind}\0{label}\0{source}").as_bytes())
}
fn put_evidence_node(
    connection: &Connection,
    kind: &str,
    label: &str,
    source: &str,
    commit: &str,
    confidence: f64,
    metadata: Value,
) -> Result<Value, String> {
    let id = evidence_node_id(kind, label, source);
    connection.execute("INSERT OR REPLACE INTO nodes(node_id,kind,label,source,confidence,repository_commit,metadata_json) VALUES(?,?,?,?,?,?,?)",params![id,kind,label,source,confidence.clamp(0.0,1.0),commit,metadata_json(metadata.clone())?]).map_err(|error|format!("RUNTIME_EVIDENCE_NODE_INSERT_FAILED:{error}"))?;
    Ok(
        json!({"node_id":id,"kind":kind,"label":label,"source":source,"confidence":confidence.clamp(0.0,1.0),"repository_commit":commit,"metadata":metadata}),
    )
}
fn put_evidence_edge(
    connection: &Connection,
    source: &str,
    target: &str,
    relation: &str,
    commit: &str,
    confidence: f64,
    metadata: Value,
) -> Result<(), String> {
    let body = sort_json(
        &json!({"source":source,"target":target,"relation":relation,"repository_commit":commit,"metadata":metadata}),
    );
    let evidence = format!(
        "sha256:{}",
        sha256_hex(
            serde_json::to_string(&body)
                .map_err(|error| format!("RUNTIME_EVIDENCE_BODY_JSON_FAILED:{error}"))?
                .as_bytes()
        )
    );
    connection.execute("INSERT OR REPLACE INTO edges(evidence,source,target,relation,confidence,repository_commit,observed_at,metadata_json) VALUES(?,?,?,?,?,?,?,?)",params![evidence,source,target,relation,confidence.clamp(0.0,1.0),commit,now_string(),metadata_json(metadata)?]).map_err(|error|format!("RUNTIME_EVIDENCE_EDGE_INSERT_FAILED:{error}"))?;
    Ok(())
}
fn runtime_import_coverage(
    unified: &Path,
    value: &Value,
    test_id: &str,
    commit: &str,
) -> Result<Value, String> {
    let path = runtime_evidence_database(unified);
    initialize_runtime_evidence(&path)?;
    let connection =
        Connection::open(path).map_err(|error| format!("RUNTIME_EVIDENCE_OPEN_FAILED:{error}"))?;
    let files = value["files"]
        .as_object()
        .ok_or_else(|| "coverage document must contain a files object".to_owned())?;
    let test = put_evidence_node(
        &connection,
        "test",
        test_id,
        "coverage",
        commit,
        1.0,
        json!({}),
    )?;
    let test_node = test["node_id"].as_str().unwrap_or_default().to_owned();
    let mut imported = 0;
    for (filename, details) in files {
        let Some(details) = details.as_object() else {
            continue;
        };
        let file = put_evidence_node(
            &connection,
            "file",
            filename,
            "coverage",
            commit,
            1.0,
            json!({}),
        )?;
        put_evidence_edge(
            &connection,
            &test_node,
            file["node_id"].as_str().unwrap_or_default(),
            "COVERS",
            commit,
            1.0,
            json!({"executed_lines":details.get("executed_lines").cloned().unwrap_or_else(||json!([])),"missing_lines":details.get("missing_lines").cloned().unwrap_or_else(||json!([]))}),
        )?;
        imported += 1;
    }
    Ok(json!({"ok":true,"files":imported,"test":test}))
}
fn runtime_import_trace(unified: &Path, spans: &[Value], commit: &str) -> Result<Value, String> {
    let path = runtime_evidence_database(unified);
    initialize_runtime_evidence(&path)?;
    let connection =
        Connection::open(path).map_err(|error| format!("RUNTIME_EVIDENCE_OPEN_FAILED:{error}"))?;
    let mut imported = 0;
    for span in spans {
        let source = span["source"].as_str().unwrap_or("").trim();
        let target = span["target"].as_str().unwrap_or("").trim();
        if source.is_empty() || target.is_empty() {
            continue;
        }
        let source_node = put_evidence_node(
            &connection,
            span["source_kind"].as_str().unwrap_or("runtime-symbol"),
            source,
            "trace",
            commit,
            1.0,
            json!({}),
        )?;
        let target_node = put_evidence_node(
            &connection,
            span["target_kind"].as_str().unwrap_or("runtime-symbol"),
            target,
            "trace",
            commit,
            1.0,
            json!({}),
        )?;
        let mut metadata = span.clone();
        if let Value::Object(map) = &mut metadata {
            map.remove("source");
            map.remove("target");
        }
        put_evidence_edge(
            &connection,
            source_node["node_id"].as_str().unwrap_or_default(),
            target_node["node_id"].as_str().unwrap_or_default(),
            span["relation"].as_str().unwrap_or("RUNTIME_CALL"),
            commit,
            span["confidence"].as_f64().unwrap_or(1.0),
            metadata,
        )?;
        imported += 1;
    }
    Ok(json!({"ok":true,"spans":imported}))
}
fn runtime_evidence_stats(unified: &Path) -> Result<Value, String> {
    let path = runtime_evidence_database(unified);
    initialize_runtime_evidence(&path)?;
    let connection =
        Connection::open(path).map_err(|error| format!("RUNTIME_EVIDENCE_OPEN_FAILED:{error}"))?;
    let nodes = scalar_i64(&connection, "SELECT COUNT(*) FROM nodes")?;
    let edges = scalar_i64(&connection, "SELECT COUNT(*) FROM edges")?;
    let relations = grouped(
        &connection,
        "SELECT relation,COUNT(*) FROM edges GROUP BY relation ORDER BY relation",
        "relation",
        "count",
    )?;
    Ok(json!({"ok":true,"nodes":nodes,"edges":edges,"relations":relations}))
}
fn runtime_evidence_neighbors(arguments: &[String], unified: &Path) -> Result<Value, String> {
    let node_id = positional_after(arguments, "evidence-neighbors", 0)?;
    let relation = option_value(arguments, "--relation")?;
    let reverse = has_flag(arguments, "--reverse");
    let path = runtime_evidence_database(unified);
    initialize_runtime_evidence(&path)?;
    let connection =
        Connection::open(path).map_err(|error| format!("RUNTIME_EVIDENCE_OPEN_FAILED:{error}"))?;
    let (direction, other) = if reverse {
        ("target", "source")
    } else {
        ("source", "target")
    };
    let sql = if relation.is_some() {
        format!("SELECT evidence,source,target,relation,confidence,repository_commit,observed_at,metadata_json FROM edges WHERE {direction}=? AND relation=? ORDER BY observed_at DESC")
    } else {
        format!("SELECT evidence,source,target,relation,confidence,repository_commit,observed_at,metadata_json FROM edges WHERE {direction}=? ORDER BY observed_at DESC")
    };
    let mut statement = connection
        .prepare(&sql)
        .map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_PREPARE_FAILED:{error}"))?;
    let collect = |row: &rusqlite::Row<'_>| -> rusqlite::Result<(
        String,
        String,
        String,
        String,
        f64,
        String,
        String,
        String,
    )> {
        Ok((
            row.get(0)?,
            row.get(1)?,
            row.get(2)?,
            row.get(3)?,
            row.get(4)?,
            row.get(5)?,
            row.get(6)?,
            row.get(7)?,
        ))
    };
    let mut rows = Vec::new();
    if let Some(relation) = relation {
        for row in statement
            .query_map(params![node_id, relation], collect)
            .map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_QUERY_FAILED:{error}"))?
        {
            rows.push(
                row.map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_ROW_FAILED:{error}"))?,
            );
        }
    } else {
        for row in statement
            .query_map(params![node_id], collect)
            .map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_QUERY_FAILED:{error}"))?
        {
            rows.push(
                row.map_err(|error| format!("RUNTIME_EVIDENCE_NEIGHBOR_ROW_FAILED:{error}"))?,
            );
        }
    }
    let mut output = Vec::new();
    for (evidence, source, target, relation, confidence, commit, observed, metadata_json) in rows {
        let linked = if other == "source" { &source } else { &target };
        let node=connection.query_row("SELECT node_id,kind,label,source,confidence,repository_commit,metadata_json FROM nodes WHERE node_id=?",params![linked],|row|Ok(json!({"node_id":row.get::<_,String>(0)?,"kind":row.get::<_,String>(1)?,"label":row.get::<_,String>(2)?,"source":row.get::<_,String>(3)?,"confidence":row.get::<_,f64>(4)?,"repository_commit":row.get::<_,String>(5)?,"metadata_json":row.get::<_,String>(6)?}))).optional().map_err(|error|format!("RUNTIME_EVIDENCE_LINKED_QUERY_FAILED:{error}"))?;
        output.push(json!({"evidence":evidence,"source":source,"target":target,"relation":relation,"confidence":confidence,"repository_commit":commit,"observed_at":observed,"metadata":serde_json::from_str::<Value>(&metadata_json).unwrap_or_else(|_|json!({})),"node":node}));
    }
    Ok(json!({"ok":true,"neighbors":output}))
}

fn safe_project_path(project: &Path, value: &str, must_exist: bool) -> Result<PathBuf, String> {
    let root = fs::canonicalize(project)
        .map_err(|error| format!("GRAPH_PROJECT_RESOLVE_FAILED:{error}"))?;
    let candidate = Path::new(value);
    let joined = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        root.join(candidate)
    };
    let resolved = if must_exist {
        fs::canonicalize(&joined).map_err(|error| format!("GRAPH_PATH_RESOLVE_FAILED:{error}"))?
    } else {
        joined
    };
    if !resolved.starts_with(&root) {
        return Err("language detection path escapes project".to_owned());
    }
    Ok(resolved)
}
fn safe_project_or_absolute(
    project: &Path,
    value: &str,
    must_exist: bool,
) -> Result<PathBuf, String> {
    let candidate = Path::new(value);
    if candidate.is_absolute() {
        return if must_exist {
            fs::canonicalize(candidate)
                .map_err(|error| format!("GRAPH_PATH_RESOLVE_FAILED:{error}"))
        } else {
            Ok(candidate.to_path_buf())
        };
    }
    safe_project_path(project, value, must_exist)
}
fn git_head(project: &Path) -> Option<String> {
    let output = Command::new("git")
        .args(["-C", project.to_str()?, "rev-parse", "HEAD"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let value = String::from_utf8(output.stdout).ok()?.trim().to_lowercase();
    (value.len() == 40 && value.bytes().all(|byte| byte.is_ascii_hexdigit())).then_some(value)
}
fn has_flag(arguments: &[String], flag: &str) -> bool {
    arguments.iter().any(|value| value == flag)
}
fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut value = None;
    let mut index = 0;
    while index < arguments.len() {
        let current = &arguments[index];
        let found = if current == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            )
        } else {
            current
                .strip_prefix(flag)
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
fn option_i64(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?
        .map(|value| value.parse::<i64>().map_err(|_| format!("{flag}_INVALID")))
        .transpose()
        .map(|value| value.unwrap_or(default))
}
fn action_position(arguments: &[String], action: &str) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "run" && row[1] == action)
        .map(|index| index + 2)
        .ok_or_else(|| format!("GRAPH_ACTION_NOT_FOUND:{action}"))
}
fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    position: usize,
) -> Result<&'a str, String> {
    let mut index = action_position(arguments, action)?;
    let value_flags = [
        "--max-file-bytes",
        "--limit",
        "--max-depth",
        "--format",
        "--repository-commit",
        "--current-commit",
        "--source-name",
        "--test-id",
        "--relation",
    ];
    let mut values = Vec::<&str>::new();
    while index < arguments.len() {
        let current = &arguments[index];
        if value_flags.contains(&current.as_str()) {
            index += 2;
            continue;
        }
        if current.starts_with("--") {
            index += 1;
            continue;
        }
        values.push(current);
        index += 1;
    }
    values
        .get(position)
        .copied()
        .ok_or_else(|| format!("GRAPH_POSITIONAL_MISSING:{action}:{position}"))
}
