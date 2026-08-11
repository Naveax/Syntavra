#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines, clippy::cast_precision_loss)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const ADAPTER_LANGUAGES: &[&str] = &[];

const BUILTIN_LANGUAGES: &[&str] = &[
    "ada",
    "agda",
    "apex",
    "assembly",
    "astro",
    "awk",
    "batch",
    "bazel",
    "c",
    "cairo",
    "capnp",
    "clojure",
    "cmake",
    "cobol",
    "common-lisp",
    "coq",
    "cpp",
    "crystal",
    "csharp",
    "css",
    "cuda",
    "cue",
    "d",
    "dart",
    "dockerfile",
    "elixir",
    "elm",
    "erlang",
    "fish",
    "flatbuffers",
    "fortran",
    "fsharp",
    "gdscript",
    "go",
    "graphql",
    "groovy",
    "haskell",
    "hcl",
    "html",
    "idris",
    "ini",
    "java",
    "javascript",
    "json",
    "julia",
    "kotlin",
    "lean",
    "less",
    "llvm-ir",
    "lua",
    "luau",
    "make",
    "markdown",
    "matlab",
    "meson",
    "move",
    "nim",
    "ninja",
    "nix",
    "nushell",
    "objective-c",
    "ocaml",
    "octave",
    "opencl",
    "pascal",
    "perl",
    "php",
    "powershell",
    "prolog",
    "protobuf",
    "purescript",
    "python",
    "qsharp",
    "r",
    "racket",
    "raku",
    "reason",
    "rego",
    "renpy",
    "ruby",
    "rust",
    "sass",
    "scala",
    "scheme",
    "scss",
    "shell",
    "smalltalk",
    "solidity",
    "sql",
    "svelte",
    "swift",
    "systemverilog",
    "tcl",
    "terraform",
    "thrift",
    "toml",
    "typescript",
    "verilog",
    "vhdl",
    "visual-basic",
    "vue",
    "vyper",
    "webassembly-text",
    "xml",
    "yaml",
    "zig",
];

#[derive(Debug, Clone)]
struct Descriptor {
    id: String,
    suffixes: Vec<String>,
    filenames: Vec<String>,
    shebangs: Vec<String>,
    capability: String,
    source: String,
}

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

#[derive(Debug, Clone)]
struct Symbol {
    name: String,
    kind: String,
    line: i64,
    end_line: i64,
}

#[derive(Debug, Clone)]
struct NodeRow {
    node_id: String,
    path: String,
    kind: String,
    name: String,
    qualified_name: String,
    start_line: i64,
    end_line: i64,
    language: String,
    evidence_ref: String,
    metadata_json: String,
}

pub(super) fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let unified = state_root.join("unified");
    fs::create_dir_all(&unified).map_err(|error| format!("GRAPH_STATE_CREATE_FAILED:{error}"))?;
    let database = unified.join("semantic-graph.sqlite3");
    super::initialize_graph(&database)?;
    match command.get(1).map(String::as_str) {
        Some("graph-index") => {
            let max_file_bytes =
                super::option_i64(arguments, "--max-file-bytes", 2_000_000)?.max(1) as u64;
            index_repository(project_root, &unified, &database, max_file_bytes)
        }
        Some("graph-query") => {
            let query = super::positional_after(arguments, "graph-query", 0)?;
            let limit = super::option_i64(arguments, "--limit", 20)?.clamp(1, 200);
            Ok(
                json!({"ok": true, "query": query, "results": query_repository(&database, query, limit)?}),
            )
        }
        Some("graph-impact") => {
            let node_id = super::positional_after(arguments, "graph-impact", 0)?;
            let max_depth = super::option_i64(arguments, "--max-depth", 6)?.max(0);
            super::impact(&database, node_id, max_depth)
        }
        Some("language") => {
            let operation = super::positional_after(arguments, "language", 0)?;
            match operation {
                "inventory" | "doctor" => language_status(project_root, &database),
                "detect" => {
                    let raw = super::positional_after(arguments, "language", 1)?;
                    let source = super::safe_project_path(project_root, raw, true)?;
                    if !source.is_file() {
                        return Err("language detection path must be a file".to_owned());
                    }
                    let project = fs::canonicalize(project_root)
                        .map_err(|error| format!("LANGUAGE_PROJECT_RESOLVE_FAILED:{error}"))?;
                    let data = fs::read(&source)
                        .map_err(|error| format!("LANGUAGE_SOURCE_READ_FAILED:{error}"))?;
                    let detection = detect_language(&project, &source, &data, false)?;
                    let relative = source
                        .strip_prefix(&project)
                        .map_err(|_| "LANGUAGE_PATH_ESCAPE".to_owned())?
                        .to_string_lossy()
                        .replace('\\', "/");
                    Ok(json!({"ok": true, "path": relative, "detection": detection.json()}))
                }
                "index" => {
                    let max_file_bytes =
                        super::option_i64(arguments, "--max-file-bytes", 2_000_000)?.max(1) as u64;
                    index_repository(project_root, &unified, &database, max_file_bytes)
                }
                "query" => {
                    let query = super::positional_after(arguments, "language", 1)?;
                    let limit = super::option_i64(arguments, "--limit", 20)?.clamp(1, 200);
                    Ok(
                        json!({"ok": true, "query": query, "results": query_repository(&database, query, limit)?}),
                    )
                }
                _ => super::language_action(arguments, project_root, &unified, &database),
            }
        }
        Some("semantic-services") => language_status(project_root, &database),
        _ => Err("GRAPH_CORE_ROUTE_UNSUPPORTED".to_owned()),
    }
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
    let inspect = vec!["inspect".to_owned(), "stats".to_owned()];
    let _ = super::super::native_structural::execute(&inspect, &inspect, &project, &scratch)?;
    let structural = Connection::open(scratch.join("structural.sqlite3"))
        .map_err(|error| format!("GRAPH_STRUCTURAL_OPEN_FAILED:{error}"))?;

    let mut graph = Connection::open(database)
        .map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let known = load_analysis_keys(&graph)?;
    let mut discovered = BTreeSet::<String>::new();
    let mut changed = 0i64;
    let mut unchanged = 0i64;
    let mut binary_skipped = 0i64;
    let mut oversized_skipped = 0i64;
    let mut errors = Vec::<Value>::new();
    let mut candidates = Vec::<PathBuf>::new();

    super::visit_files(&project, &mut |path| {
        candidates.push(path.to_path_buf());
        Ok(())
    })?;
    candidates.sort();

    let transaction = graph
        .transaction()
        .map_err(|error| format!("GRAPH_TRANSACTION_FAILED:{error}"))?;

    for path in candidates {
        let relative = path
            .strip_prefix(&project)
            .map_err(|_| "GRAPH_PATH_OUTSIDE_PROJECT".to_owned())?
            .to_string_lossy()
            .replace('\\', "/");
        let size = match fs::metadata(&path) {
            Ok(value) => value.len(),
            Err(error) => {
                errors.push(json!({"path": relative, "error": format!("{}: {error}", std::any::type_name::<std::io::Error>())}));
                continue;
            }
        };
        if size > max_file_bytes {
            oversized_skipped += 1;
            continue;
        }
        let data = match fs::read(&path) {
            Ok(value) => value,
            Err(error) => {
                errors.push(json!({"path": relative, "error": format!("Error: {error}")}));
                continue;
            }
        };
        let detection = detect_language(&project, &path, &data, true)?;
        if detection.binary {
            binary_skipped += 1;
            continue;
        }
        let text = String::from_utf8_lossy(&data).into_owned();
        discovered.insert(relative.clone());
        let digest = sha256_hex(&data);
        let analysis_key = sha256_hex(
            format!(
                "{}\0{}\0{}\0{}\0{}",
                digest,
                detection.language_id,
                detection.evidence,
                detection.descriptor_source,
                detection.capability_level
            )
            .as_bytes(),
        );
        if known.get(&relative) == Some(&analysis_key) {
            unchanged += 1;
            continue;
        }

        super::remove_local_file(&transaction, &relative)?;
        materialize_file(
            &transaction,
            &structural,
            &relative,
            &path,
            &text,
            &digest,
            &analysis_key,
            &detection,
        )?;
        changed += 1;
    }

    let stale = known
        .keys()
        .filter(|path| !discovered.contains(*path))
        .cloned()
        .collect::<Vec<_>>();
    for relative in &stale {
        super::remove_local_file(&transaction, relative)?;
    }

    transaction
        .commit()
        .map_err(|error| format!("GRAPH_COMMIT_FAILED:{error}"))?;
    super::refresh_search(database)?;

    let mut value = super::graph_stats(database)?;
    let object = value.as_object_mut().expect("graph stats object");
    object.insert("ok".to_owned(), Value::Bool(errors.is_empty()));
    object.insert("changed_files".to_owned(), Value::from(changed));
    object.insert("unchanged_files".to_owned(), Value::from(unchanged));
    object.insert("removed_files".to_owned(), Value::from(stale.len()));
    object.insert("binary_skipped".to_owned(), Value::from(binary_skipped));
    object.insert(
        "oversized_skipped".to_owned(),
        Value::from(oversized_skipped),
    );
    object.insert("errors".to_owned(), Value::Array(errors));
    object.insert("warnings".to_owned(), Value::Array(Vec::new()));
    object.insert(
        "language_platform".to_owned(),
        language_inventory(&project)?,
    );
    object.insert("language_services".to_owned(), empty_service_inventory());
    object.insert("lsp_services".to_owned(), empty_service_inventory());
    object.insert(
        "repository_query".to_owned(),
        repository_query_refresh(database)?,
    );
    object.insert("canonical_graph".to_owned(), Value::Bool(true));
    Ok(value)
}

fn load_analysis_keys(connection: &Connection) -> Result<BTreeMap<String, String>, String> {
    let mut statement = connection
        .prepare("SELECT path,analysis_key FROM files")
        .map_err(|error| format!("GRAPH_ANALYSIS_KEY_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .map_err(|error| format!("GRAPH_ANALYSIS_KEY_QUERY_FAILED:{error}"))?;
    let mut output = BTreeMap::new();
    for row in rows {
        let (path, key) = row.map_err(|error| format!("GRAPH_ANALYSIS_KEY_ROW_FAILED:{error}"))?;
        output.insert(path, key);
    }
    Ok(output)
}

fn materialize_file(
    transaction: &rusqlite::Transaction<'_>,
    structural: &Connection,
    relative: &str,
    path: &Path,
    text: &str,
    digest: &str,
    analysis_key: &str,
    detection: &Detection,
) -> Result<(), String> {
    let evidence_ref = format!("sha256:{digest}");
    if detection.language_id == "python" {
        materialize_python(
            transaction,
            structural,
            relative,
            text,
            &evidence_ref,
            detection,
        )?;
    } else {
        materialize_generic(transaction, relative, text, &evidence_ref, detection)?;
    }

    transaction
        .execute(
            "INSERT OR REPLACE INTO files(path,sha256,language,indexed_at,analysis_key,detector,confidence,capability_level,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
            params![
                relative,
                digest,
                detection.language_id,
                super::now_string(),
                analysis_key,
                detection.evidence,
                detection.confidence,
                detection.capability_level,
                super::metadata_json(json!({
                    "descriptor_source": detection.descriptor_source,
                    "encoding": detection.text_encoding,
                    "generated": detection.generated,
                    "minified": detection.minified,
                    "adapter": false,
                }))?,
            ],
        )
        .map_err(|error| format!("GRAPH_FILE_INSERT_FAILED:{error}"))?;

    let _ = path;
    Ok(())
}

fn materialize_python(
    transaction: &rusqlite::Transaction<'_>,
    structural: &Connection,
    relative: &str,
    text: &str,
    evidence_ref: &str,
    detection: &Detection,
) -> Result<(), String> {
    let module_id = super::node_id(relative, "module", relative, 1);
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
                i64::try_from(text.lines().count().max(1)).unwrap_or(i64::MAX),
                "python",
                evidence_ref,
                super::metadata_json(json!({
                    "source": "python-ast",
                    "exact_semantic": true,
                    "capability_level": detection.capability_level,
                    "detection_confidence": detection.confidence,
                    "detection_evidence": detection.evidence,
                    "generated": detection.generated,
                    "minified": detection.minified,
                }))?,
            ],
        )
        .map_err(|error| format!("GRAPH_PYTHON_MODULE_INSERT_FAILED:{error}"))?;

    let symbols = python_symbols(structural, relative)?;
    let mut by_name = BTreeMap::<String, String>::new();
    for symbol in &symbols {
        let canonical_kind = if symbol.kind == "class" {
            "class"
        } else {
            "function"
        };
        let node_id = super::node_id(relative, canonical_kind, &symbol.name, symbol.line);
        by_name.insert(symbol.name.clone(), node_id.clone());
        transaction
            .execute(
                "INSERT OR REPLACE INTO nodes(node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                params![
                    node_id,
                    relative,
                    canonical_kind,
                    symbol.name,
                    format!("{relative}:{}", symbol.name),
                    symbol.line.max(1),
                    symbol.end_line.max(symbol.line).max(1),
                    "python",
                    evidence_ref,
                    super::metadata_json(json!({
                        "source": "python-ast",
                        "exact_semantic": true,
                        "capability_level": "syntax",
                    }))?,
                ],
            )
            .map_err(|error| format!("GRAPH_PYTHON_SYMBOL_INSERT_FAILED:{error}"))?;
        transaction
            .execute(
                "INSERT OR REPLACE INTO edges(source,target,edge_type,confidence,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?)",
                params![
                    module_id,
                    by_name.get(&symbol.name).expect("python symbol inserted"),
                    "defines",
                    1.0f64,
                    evidence_ref,
                    super::metadata_json(json!({"source":"python-ast","exact_semantic":true}))?,
                ],
            )
            .map_err(|error| format!("GRAPH_PYTHON_DEFINE_INSERT_FAILED:{error}"))?;
    }

    let mut edge_statement = structural
        .prepare("SELECT source_symbol,edge_type,target FROM structural_edges WHERE source_path=? ORDER BY line,edge_type,target")
        .map_err(|error| format!("GRAPH_PYTHON_EDGE_PREPARE_FAILED:{error}"))?;
    let edge_rows = edge_statement
        .query_map(params![relative], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })
        .map_err(|error| format!("GRAPH_PYTHON_EDGE_QUERY_FAILED:{error}"))?;
    let mut seen_imports = BTreeSet::<String>::new();
    let mut seen_calls = BTreeSet::<(String, String)>::new();
    for row in edge_rows {
        let (source_name, edge_type, target_name) =
            row.map_err(|error| format!("GRAPH_PYTHON_EDGE_ROW_FAILED:{error}"))?;
        if edge_type == "imports" {
            if !seen_imports.insert(target_name.clone()) {
                continue;
            }
            let external_id = format!("external:{target_name}");
            transaction
                .execute(
                    "INSERT OR IGNORE INTO nodes(node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json) VALUES(?,?, 'external', ?, ?,0,0,'external',?,?)",
                    params![
                        external_id,
                        relative,
                        target_name,
                        external_id,
                        evidence_ref,
                        super::metadata_json(json!({"source":"external-reference","exact_semantic":false}))?,
                    ],
                )
                .map_err(|error| format!("GRAPH_PYTHON_EXTERNAL_INSERT_FAILED:{error}"))?;
            transaction
                .execute(
                    "INSERT OR REPLACE INTO edges(source,target,edge_type,confidence,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?)",
                    params![
                        module_id,
                        external_id,
                        "imports",
                        0.98f64,
                        evidence_ref,
                        super::metadata_json(json!({"external":true,"source":"python-ast","exact_semantic":true}))?,
                    ],
                )
                .map_err(|error| format!("GRAPH_PYTHON_IMPORT_INSERT_FAILED:{error}"))?;
            continue;
        }
        if edge_type != "calls" {
            continue;
        }
        let target_short = target_name
            .rsplit(['.', ':'])
            .next()
            .unwrap_or(target_name.as_str());
        let Some(target_id) = by_name.get(target_short) else {
            continue;
        };
        let source_short = source_name
            .rsplit(['.', ':'])
            .next()
            .unwrap_or(source_name.as_str());
        let source_id = by_name
            .get(source_short)
            .cloned()
            .unwrap_or_else(|| module_id.clone());
        if !seen_calls.insert((source_id.clone(), target_id.clone())) {
            continue;
        }
        transaction
            .execute(
                "INSERT OR REPLACE INTO edges(source,target,edge_type,confidence,evidence_ref,metadata_json) VALUES(?,?,?,?,?,?)",
                params![
                    source_id,
                    target_id,
                    "calls",
                    0.92f64,
                    evidence_ref,
                    super::metadata_json(json!({"source":"python-ast","exact_semantic":false,"resolution":"same-file-name"}))?,
                ],
            )
            .map_err(|error| format!("GRAPH_PYTHON_CALL_INSERT_FAILED:{error}"))?;
    }
    Ok(())
}

fn python_symbols(structural: &Connection, relative: &str) -> Result<Vec<Symbol>, String> {
    let mut statement = structural
        .prepare("SELECT name,kind,line,end_line FROM structural_symbols WHERE path=? AND kind IN ('class','function','method') ORDER BY line,name")
        .map_err(|error| format!("GRAPH_PYTHON_SYMBOL_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map(params![relative], |row| {
            Ok(Symbol {
                name: row.get(0)?,
                kind: row.get(1)?,
                line: row.get(2)?,
                end_line: row.get(3)?,
            })
        })
        .map_err(|error| format!("GRAPH_PYTHON_SYMBOL_QUERY_FAILED:{error}"))?;
    let mut output = Vec::new();
    for row in rows {
        output.push(row.map_err(|error| format!("GRAPH_PYTHON_SYMBOL_ROW_FAILED:{error}"))?);
    }
    Ok(output)
}

fn materialize_generic(
    transaction: &rusqlite::Transaction<'_>,
    relative: &str,
    text: &str,
    evidence_ref: &str,
    detection: &Detection,
) -> Result<(), String> {
    let module_id = super::node_id(relative, "module", relative, 1);
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
                i64::try_from(text.lines().count().max(1)).unwrap_or(i64::MAX),
                detection.language_id,
                evidence_ref,
                super::metadata_json(json!({
                    "source": "universal-fallback",
                    "exact_semantic": false,
                    "capability_level": detection.capability_level,
                    "detection_confidence": detection.confidence,
                    "detection_evidence": detection.evidence,
                    "generated": detection.generated,
                    "minified": detection.minified,
                }))?,
            ],
        )
        .map_err(|error| format!("GRAPH_GENERIC_MODULE_INSERT_FAILED:{error}"))?;
    Ok(())
}

fn query_repository(path: &Path, text: &str, limit: i64) -> Result<Vec<Value>, String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let normalized = text.trim().to_lowercase();
    let terms = query_tokens(text);
    let candidate_limit = (limit * 8).max(40);

    let mut rows = BTreeMap::<String, NodeRow>::new();
    let mut ranks = BTreeMap::<String, f64>::new();

    if !normalized.is_empty() {
        let mut statement = connection
            .prepare(
                "SELECT node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json FROM nodes WHERE kind!='external' AND (lower(name)=? OR lower(qualified_name)=?) ORDER BY path,start_line LIMIT ?",
            )
            .map_err(|error| format!("GRAPH_QUERY_EXACT_PREPARE_FAILED:{error}"))?;
        let mapped = statement
            .query_map(
                params![normalized, normalized, candidate_limit],
                node_from_row,
            )
            .map_err(|error| format!("GRAPH_QUERY_EXACT_FAILED:{error}"))?;
        for row in mapped {
            let value = row.map_err(|error| format!("GRAPH_QUERY_EXACT_ROW_FAILED:{error}"))?;
            ranks.insert(value.node_id.clone(), 120.0);
            rows.insert(value.node_id.clone(), value);
        }
    }

    if !terms.is_empty() && fts_available(&connection) {
        let expression = terms
            .iter()
            .map(|term| format!("\"{}\"*", term.replace('"', "\"\"")))
            .collect::<Vec<_>>()
            .join(" OR ");
        let mut statement = connection
            .prepare(
                "SELECT n.node_id,n.path,n.kind,n.name,n.qualified_name,n.start_line,n.end_line,n.language,n.evidence_ref,n.metadata_json,bm25(node_search,0.0,8.0,5.0,3.0,1.0,1.0) FROM node_search JOIN nodes n USING(node_id) WHERE node_search MATCH ? ORDER BY bm25(node_search,0.0,8.0,5.0,3.0,1.0,1.0) LIMIT ?",
            )
            .map_err(|error| format!("GRAPH_QUERY_FTS_PREPARE_FAILED:{error}"))?;
        let mapped = statement
            .query_map(params![expression, candidate_limit], |row| {
                Ok((node_from_row(row)?, row.get::<_, f64>(10)?))
            })
            .map_err(|error| format!("GRAPH_QUERY_FTS_FAILED:{error}"))?;
        for row in mapped {
            let (value, fts_rank) =
                row.map_err(|error| format!("GRAPH_QUERY_FTS_ROW_FAILED:{error}"))?;
            let score = 70.0 + (-fts_rank).max(-20.0);
            let rank = ranks.entry(value.node_id.clone()).or_insert(0.0);
            *rank = rank.max(score);
            rows.entry(value.node_id.clone()).or_insert(value);
        }
    }

    if rows.is_empty() {
        let pattern = format!("%{normalized}%");
        let mut statement = connection
            .prepare(
                "SELECT node_id,path,kind,name,qualified_name,start_line,end_line,language,evidence_ref,metadata_json FROM nodes WHERE kind!='external' AND (lower(name) LIKE ? OR lower(qualified_name) LIKE ? OR lower(path) LIKE ?) ORDER BY path,start_line LIMIT ?",
            )
            .map_err(|error| format!("GRAPH_QUERY_LIKE_PREPARE_FAILED:{error}"))?;
        let mapped = statement
            .query_map(
                params![pattern, pattern, pattern, candidate_limit],
                node_from_row,
            )
            .map_err(|error| format!("GRAPH_QUERY_LIKE_FAILED:{error}"))?;
        for row in mapped {
            let value = row.map_err(|error| format!("GRAPH_QUERY_LIKE_ROW_FAILED:{error}"))?;
            ranks.insert(value.node_id.clone(), 40.0);
            rows.insert(value.node_id.clone(), value);
        }
    }

    let ids = rows.keys().cloned().collect::<Vec<_>>();
    let degrees = degree_map(&connection, &ids)?;
    let query_terms = terms.iter().cloned().collect::<BTreeSet<_>>();
    let mut scored = Vec::<(f64, Value)>::new();

    for (node_id, row) in rows {
        let metadata =
            serde_json::from_str::<Value>(&row.metadata_json).unwrap_or_else(|_| json!({}));
        let corpus = query_tokens(&format!(
            "{} {} {} {} {}",
            row.name, row.qualified_name, row.path, row.kind, row.language
        ))
        .into_iter()
        .collect::<BTreeSet<_>>();
        let matched = query_terms
            .intersection(&corpus)
            .cloned()
            .collect::<Vec<_>>();
        let exact_name = normalized == row.name.to_lowercase()
            || normalized == row.qualified_name.to_lowercase();
        let semantic_bonus = if metadata["exact_semantic"].as_bool().unwrap_or(false) {
            10.0
        } else if metadata["exact_syntax"].as_bool().unwrap_or(false) {
            4.0
        } else {
            0.0
        };
        let degree = *degrees.get(&node_id).unwrap_or(&0);
        let score = ranks.get(&node_id).copied().unwrap_or(0.0)
            + semantic_bonus
            + (degree as f64 * 0.4).min(12.0)
            + if exact_name { 20.0 } else { 0.0 };
        let rounded = (score * 1_000_000.0).round() / 1_000_000.0;
        let value = json!({
            "node_id": row.node_id,
            "path": row.path,
            "kind": row.kind,
            "name": row.name,
            "qualified_name": row.qualified_name,
            "start_line": row.start_line,
            "end_line": row.end_line,
            "language": row.language,
            "evidence_ref": row.evidence_ref,
            "metadata_json": row.metadata_json,
            "metadata": metadata,
            "score": rounded,
            "matched_terms": matched,
            "degree": degree,
            "semantic_status": if metadata["exact_semantic"].as_bool().unwrap_or(false) {
                "exact"
            } else if metadata["exact_syntax"].as_bool().unwrap_or(false) {
                "syntax"
            } else {
                "candidate"
            },
            "query_backend": if fts_available(&connection) {"sqlite-fts5"} else {"sqlite-like"},
        });
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
            .then_with(|| left.1["node_id"].as_str().cmp(&right.1["node_id"].as_str()))
    });

    Ok(scored
        .into_iter()
        .take(limit as usize)
        .map(|(_, value)| value)
        .collect())
}

fn node_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<NodeRow> {
    Ok(NodeRow {
        node_id: row.get(0)?,
        path: row.get(1)?,
        kind: row.get(2)?,
        name: row.get(3)?,
        qualified_name: row.get(4)?,
        start_line: row.get(5)?,
        end_line: row.get(6)?,
        language: row.get(7)?,
        evidence_ref: row.get(8)?,
        metadata_json: row.get(9)?,
    })
}

fn degree_map(connection: &Connection, ids: &[String]) -> Result<BTreeMap<String, i64>, String> {
    let mut output = BTreeMap::new();
    for node_id in ids {
        let degree = connection
            .query_row(
                "SELECT COUNT(*) FROM (SELECT source FROM edges WHERE source=? UNION ALL SELECT target FROM edges WHERE target=?)",
                params![node_id, node_id],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|error| format!("GRAPH_QUERY_DEGREE_FAILED:{error}"))?;
        output.insert(node_id.clone(), degree);
    }
    Ok(output)
}

fn query_tokens(text: &str) -> Vec<String> {
    let chars = text.chars().collect::<Vec<_>>();
    let mut output = Vec::<String>::new();
    let mut seen = BTreeSet::<String>::new();
    let mut index = 0usize;
    while index < chars.len() {
        if chars[index].is_alphanumeric() && chars[index] != '_' {
            let start = index;
            index += 1;
            while index < chars.len() && chars[index].is_alphanumeric() && chars[index] != '_' {
                index += 1;
            }
            let token = chars[start..index]
                .iter()
                .collect::<String>()
                .to_lowercase();
            if token.chars().count() > 1 && seen.insert(token.clone()) {
                output.push(token);
            }
            continue;
        }
        if chars[index].is_ascii_alphanumeric()
            || matches!(chars[index], '_' | '.' | '/' | ':' | '-')
        {
            let start = index;
            index += 1;
            while index < chars.len()
                && (chars[index].is_ascii_alphanumeric()
                    || matches!(chars[index], '_' | '.' | '/' | ':' | '-'))
            {
                index += 1;
            }
            let token = chars[start..index]
                .iter()
                .collect::<String>()
                .to_lowercase();
            if token.chars().count() > 1 && seen.insert(token.clone()) {
                output.push(token);
            }
            continue;
        }
        index += 1;
    }
    output
}

fn fts_available(connection: &Connection) -> bool {
    connection
        .prepare("SELECT bm25(node_search) FROM node_search LIMIT 1")
        .is_ok()
}

fn repository_query_refresh(path: &Path) -> Result<Value, String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let indexed_nodes = if fts_available(&connection) {
        connection
            .query_row("SELECT COUNT(*) FROM node_search", [], |row| {
                row.get::<_, i64>(0)
            })
            .map_err(|error| format!("GRAPH_QUERY_INDEX_COUNT_FAILED:{error}"))?
    } else {
        connection
            .query_row(
                "SELECT COUNT(*) FROM nodes WHERE kind!='external'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|error| format!("GRAPH_QUERY_NODE_COUNT_FAILED:{error}"))?
    };
    Ok(json!({
        "backend": if fts_available(&connection) {"sqlite-fts5"} else {"sqlite-like"},
        "indexed_nodes": indexed_nodes,
        "incremental": false,
    }))
}

fn repository_query_stats(path: &Path) -> Result<Value, String> {
    let connection =
        Connection::open(path).map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))?;
    let graph_nodes = connection
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE kind!='external'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(|error| format!("GRAPH_QUERY_NODE_COUNT_FAILED:{error}"))?;
    let indexed_nodes = if fts_available(&connection) {
        connection
            .query_row("SELECT COUNT(*) FROM node_search", [], |row| {
                row.get::<_, i64>(0)
            })
            .map_err(|error| format!("GRAPH_QUERY_INDEX_COUNT_FAILED:{error}"))?
    } else {
        graph_nodes
    };
    Ok(json!({
        "backend": if fts_available(&connection) {"sqlite-fts5"} else {"sqlite-like"},
        "graph_nodes": graph_nodes,
        "indexed_nodes": indexed_nodes,
    }))
}

fn language_status(project: &Path, database: &Path) -> Result<Value, String> {
    let registry = language_inventory(project)?;
    let semantic = Connection::open(database)
        .map_err(|error| format!("GRAPH_DATABASE_OPEN_FAILED:{error}"))
        .and_then(|connection| super::semantic_stats(&connection))?;
    let declared = registry["registered_languages"].as_i64().unwrap_or(0);
    let available = i64::try_from(ADAPTER_LANGUAGES.len()).unwrap_or(i64::MAX);
    Ok(json!({
        "ok": true,
        "universal_text_fallback": true,
        "language_registry": registry,
        "sandboxed_analyzers": empty_service_inventory(),
        "lsp_services": empty_service_inventory(),
        "semantic_indexes": semantic,
        "evidence_levels": ["lexical","syntax","semantic"],
        "claim_boundary": "declared support is not live certification; unknown and future text languages remain navigable, while exact semantic claims require validated parser, analyzer, LSP, LSIF or SCIP evidence",
        "universal_claim_boundary": "unknown and future text languages remain navigable; exact type, call, implementation and override claims require validated parser, analyzer, LSP, LSIF or SCIP evidence",
        "tree_sitter": {
            "adapter": "native-structural",
            "installed": false,
            "available_languages": ADAPTER_LANGUAGES,
            "capability_level": "syntax",
            "claim_boundary": "cross-file semantic identity requires LSP, LSIF or SCIP confirmation",
        },
        "repository_query": repository_query_stats(database)?,
        "canonical_graph": true,
        "declared": declared,
        "available": available,
    }))
}

fn language_inventory(project: &Path) -> Result<Value, String> {
    let descriptors = descriptors(project, true)?;
    let mut languages = descriptors
        .values()
        .map(|descriptor| descriptor.id.clone())
        .collect::<Vec<_>>();
    languages.sort();
    languages.dedup();
    Ok(json!({
        "registered_languages": languages.len(),
        "languages": languages,
        "adapters": ADAPTER_LANGUAGES,
        "diagnostics": ["entry-point-discovery-disabled: explicit SYNTAVRA_ALLOW_LANGUAGE_PLUGINS authorization required"],
        "entry_point_plugins_authorized": false,
        "universal_text_fallback": true,
    }))
}

fn empty_service_inventory() -> Value {
    json!({
        "services": 0,
        "service_ids": [],
        "languages": [],
        "execution_authorized": false,
        "diagnostics": [],
    })
}

fn detect_language(
    project: &Path,
    path: &Path,
    data: &[u8],
    include_manifests: bool,
) -> Result<Detection, String> {
    if data.iter().take(8192).any(|byte| *byte == 0) {
        return Ok(Detection {
            language_id: "binary".to_owned(),
            confidence: 1.0,
            evidence: "binary-probe".to_owned(),
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
        let lowered = line.to_lowercase();
        lowered.contains("generated file")
            || lowered.contains("generated code")
            || lowered.contains("do not edit")
            || lowered.contains("auto-generated")
            || lowered.contains("auto generated")
            || lowered.contains("machine generated")
    });
    let lines = text.lines().collect::<Vec<_>>();
    let longest = lines.iter().map(|line| line.len()).max().unwrap_or(0);
    let average = if lines.is_empty() {
        0.0
    } else {
        lines.iter().map(|line| line.len()).sum::<usize>() as f64 / lines.len() as f64
    };
    let minified = longest > 10_000 || (lines.len() < 8 && average > 1_500.0);
    let registry = descriptors(project, include_manifests)?;
    let filename = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_lowercase();

    let filename_matches = registry
        .values()
        .filter(|descriptor| descriptor.filenames.iter().any(|value| value == &filename))
        .collect::<Vec<_>>();
    if filename_matches.len() == 1 {
        return Ok(detection_from_descriptor(
            filename_matches[0],
            1.0,
            "filename".to_owned(),
            generated,
            minified,
        ));
    }

    let suffix = path
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| format!(".{}", value.to_lowercase()));
    if let Some(ext) = &suffix {
        let matches = registry
            .values()
            .filter(|descriptor| descriptor.suffixes.iter().any(|value| value == ext))
            .collect::<Vec<_>>();
        if matches.len() == 1 {
            return Ok(detection_from_descriptor(
                matches[0],
                0.99,
                format!("suffix:{ext}"),
                generated,
                minified,
            ));
        }
        if matches.len() > 1 {
            let candidates = matches
                .iter()
                .map(|descriptor| descriptor.id.clone())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>();
            return Ok(Detection {
                language_id: format!("ambiguous:{}", candidates.join("|")),
                confidence: 0.4,
                evidence: format!("suffix:{ext}:ambiguous"),
                capability_level: "lexical".to_owned(),
                descriptor_source: "ambiguous".to_owned(),
                text_encoding: Some("utf-8".to_owned()),
                binary: false,
                generated,
                minified,
                diagnostics: vec!["Multiple languages share this identifier; exact semantic claims are disabled until stronger evidence or an adapter is available.".to_owned()],
                candidates,
            });
        }
    }

    if let Some(first_line) = text.lines().next() {
        if first_line.starts_with("#!") {
            let lowered = first_line.to_lowercase();
            for descriptor in registry.values() {
                if descriptor
                    .shebangs
                    .iter()
                    .any(|token| lowered.contains(token))
                {
                    return Ok(detection_from_descriptor(
                        descriptor,
                        0.98,
                        format!(
                            "shebang:{}",
                            first_line
                                .split_whitespace()
                                .next()
                                .unwrap_or_default()
                                .rsplit('/')
                                .next()
                                .unwrap_or_default()
                        ),
                        generated,
                        minified,
                    ));
                }
            }
        }
    }

    let fallback_id = suffix
        .as_deref()
        .map(|value| format!("unknown:{}", value.trim_start_matches('.')))
        .unwrap_or_else(|| "unknown:text".to_owned());
    Ok(Detection {
        language_id: fallback_id,
        confidence: 0.35,
        evidence: "text-fallback".to_owned(),
        capability_level: "lexical".to_owned(),
        descriptor_source: "fallback".to_owned(),
        text_encoding: Some("utf-8".to_owned()),
        binary: false,
        generated,
        minified,
        diagnostics: vec![
            "No registered grammar or descriptor; exact semantic claims are disabled.".to_owned(),
        ],
        candidates: Vec::new(),
    })
}

fn detection_from_descriptor(
    descriptor: &Descriptor,
    confidence: f64,
    evidence: String,
    generated: bool,
    minified: bool,
) -> Detection {
    Detection {
        language_id: descriptor.id.clone(),
        confidence,
        evidence,
        capability_level: descriptor.capability.clone(),
        descriptor_source: descriptor.source.clone(),
        text_encoding: Some("utf-8".to_owned()),
        binary: false,
        generated,
        minified,
        diagnostics: Vec::new(),
        candidates: vec![descriptor.id.clone()],
    }
}

fn descriptors(
    project: &Path,
    include_manifests: bool,
) -> Result<BTreeMap<String, Descriptor>, String> {
    let mut output = builtin_descriptors();
    if !include_manifests {
        return Ok(output);
    }
    let directory = project.join(".syntavra/languages");
    if !directory.is_dir() {
        return Ok(output);
    }
    let mut entries = fs::read_dir(&directory)
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
            &fs::read(&path).map_err(|error| format!("LANGUAGE_MANIFEST_READ_FAILED:{error}"))?,
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
        let suffixes = json_strings(&value["suffixes"])
            .into_iter()
            .map(|item| {
                if item.starts_with('.') {
                    item.to_lowercase()
                } else {
                    format!(".{}", item.to_lowercase())
                }
            })
            .collect();
        let filenames = json_strings(&value["filenames"])
            .into_iter()
            .map(|item| item.to_lowercase())
            .collect();
        let shebangs = json_strings(&value["shebangs"])
            .into_iter()
            .map(|item| item.to_lowercase())
            .collect();
        let capabilities = json_strings(&value["capabilities"]);
        let capability = if capabilities
            .iter()
            .any(|value| value.eq_ignore_ascii_case("semantic"))
        {
            "semantic"
        } else if capabilities
            .iter()
            .any(|value| value.eq_ignore_ascii_case("syntax"))
        {
            "syntax"
        } else {
            "lexical"
        };
        output.insert(
            id.clone(),
            Descriptor {
                id,
                suffixes,
                filenames,
                shebangs,
                capability: capability.to_owned(),
                source: format!("manifest:{}", path.display()),
            },
        );
    }
    Ok(output)
}

fn json_strings(value: &Value) -> Vec<String> {
    if let Some(text) = value.as_str() {
        return vec![text.to_owned()];
    }
    value
        .as_array()
        .map(|rows| {
            rows.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn builtin_descriptors() -> BTreeMap<String, Descriptor> {
    let mut output = BTreeMap::new();
    for id in BUILTIN_LANGUAGES {
        output.insert(
            (*id).to_owned(),
            Descriptor {
                id: (*id).to_owned(),
                suffixes: suffixes_for(id)
                    .iter()
                    .map(|value| (*value).to_owned())
                    .collect(),
                filenames: filenames_for(id)
                    .iter()
                    .map(|value| (*value).to_owned())
                    .collect(),
                shebangs: shebangs_for(id)
                    .iter()
                    .map(|value| (*value).to_owned())
                    .collect(),
                capability: if *id == "python" {
                    "semantic".to_owned()
                } else {
                    "lexical".to_owned()
                },
                source: "builtin".to_owned(),
            },
        );
    }
    output
}

fn suffixes_for(id: &str) -> &'static [&'static str] {
    match id {
        "python" => &[".py", ".pyi", ".pyw"],
        "javascript" => &[".js", ".jsx", ".mjs", ".cjs"],
        "typescript" => &[".ts", ".tsx", ".mts", ".cts"],
        "rust" => &[".rs"],
        "go" => &[".go"],
        "java" => &[".java"],
        "kotlin" => &[".kt", ".kts"],
        "scala" => &[".scala", ".sc"],
        "csharp" => &[".cs", ".csx"],
        "fsharp" => &[".fs", ".fsi", ".fsx"],
        "visual-basic" => &[".vb"],
        "c" => &[".c", ".h"],
        "cpp" => &[
            ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ixx", ".mpp",
        ],
        "objective-c" => &[".m", ".mm"],
        "swift" => &[".swift"],
        "zig" => &[".zig"],
        "d" => &[".d", ".di"],
        "dart" => &[".dart"],
        "ruby" => &[".rb", ".rake", ".gemspec"],
        "php" => &[".php", ".php3", ".php4", ".php5", ".phtml"],
        "perl" => &[".pl", ".pm", ".t"],
        "raku" => &[".raku", ".rakumod", ".rakutest"],
        "lua" => &[".lua"],
        "luau" => &[".luau"],
        "r" => &[".r", ".rmd", ".qmd"],
        "julia" => &[".jl"],
        "matlab" => &[".m", ".matlab"],
        "octave" => &[".m", ".octave"],
        "haskell" => &[".hs", ".lhs"],
        "elm" => &[".elm"],
        "purescript" => &[".purs"],
        "ocaml" => &[".ml", ".mli"],
        "reason" => &[".re", ".rei"],
        "erlang" => &[".erl", ".hrl", ".escript"],
        "elixir" => &[".ex", ".exs"],
        "clojure" => &[".clj", ".cljs", ".cljc", ".edn"],
        "common-lisp" => &[".lisp", ".lsp", ".cl"],
        "scheme" => &[".scm", ".ss", ".sld"],
        "racket" => &[".rkt", ".rktd", ".rktl"],
        "solidity" => &[".sol"],
        "vyper" => &[".vy"],
        "move" => &[".move"],
        "cairo" => &[".cairo"],
        "shell" => &[".sh", ".bash", ".zsh", ".ksh"],
        "fish" => &[".fish"],
        "powershell" => &[".ps1", ".psm1", ".psd1"],
        "batch" => &[".bat", ".cmd"],
        "nushell" => &[".nu"],
        "sql" => &[".sql", ".ddl", ".dml"],
        "graphql" => &[".graphql", ".gql"],
        "html" => &[".html", ".htm", ".xhtml"],
        "css" => &[".css"],
        "scss" => &[".scss"],
        "sass" => &[".sass"],
        "less" => &[".less"],
        "vue" => &[".vue"],
        "svelte" => &[".svelte"],
        "astro" => &[".astro"],
        "webassembly-text" => &[".wat", ".wast"],
        "assembly" => &[".asm", ".s", ".inc"],
        "llvm-ir" => &[".ll"],
        "cuda" => &[".cu", ".cuh"],
        "opencl" => &[".cl"],
        "verilog" => &[".v", ".vh"],
        "systemverilog" => &[".sv", ".svh"],
        "vhdl" => &[".vhd", ".vhdl"],
        "tcl" => &[".tcl"],
        "awk" => &[".awk"],
        "make" => &[".mk"],
        "cmake" => &[".cmake"],
        "ninja" => &[".ninja"],
        "bazel" => &[".bzl"],
        "nix" => &[".nix"],
        "terraform" => &[".tf", ".tfvars"],
        "hcl" => &[".hcl"],
        "cue" => &[".cue"],
        "rego" => &[".rego"],
        "protobuf" => &[".proto"],
        "thrift" => &[".thrift"],
        "capnp" => &[".capnp"],
        "flatbuffers" => &[".fbs"],
        "ada" => &[".adb", ".ads"],
        "fortran" => &[".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08"],
        "cobol" => &[".cob", ".cbl", ".cpy"],
        "pascal" => &[".pas", ".pp", ".inc"],
        "nim" => &[".nim", ".nims", ".nimble"],
        "crystal" => &[".cr"],
        "groovy" => &[".groovy", ".gradle"],
        "smalltalk" => &[".st"],
        "prolog" => &[".pro", ".prolog", ".plg"],
        "apex" => &[".cls", ".trigger"],
        "gdscript" => &[".gd"],
        "renpy" => &[".rpy"],
        "qsharp" => &[".qs"],
        "lean" => &[".lean"],
        "coq" => &[".v"],
        "agda" => &[".agda", ".lagda"],
        "idris" => &[".idr", ".lidr"],
        "json" => &[".json", ".jsonc", ".json5"],
        "yaml" => &[".yaml", ".yml"],
        "toml" => &[".toml"],
        "xml" => &[".xml", ".xsd", ".xsl", ".xslt", ".svg"],
        "ini" => &[".ini", ".cfg", ".conf", ".properties"],
        "markdown" => &[".md", ".mdx", ".markdown"],
        _ => &[],
    }
}

fn filenames_for(id: &str) -> &'static [&'static str] {
    match id {
        "ruby" => &["rakefile", "gemfile"],
        "matlab" => &["contents.m"],
        "elixir" => &["mix.exs"],
        "shell" => &["bashrc", "zshrc"],
        "make" => &["makefile", "gnumakefile"],
        "cmake" => &["cmakelists.txt"],
        "meson" => &["meson.build", "meson_options.txt"],
        "bazel" => &[
            "build",
            "build.bazel",
            "workspace",
            "workspace.bazel",
            "module.bazel",
        ],
        "dockerfile" => &["dockerfile"],
        _ => &[],
    }
}

fn shebangs_for(id: &str) -> &'static [&'static str] {
    match id {
        "python" => &["python"],
        "javascript" => &["node", "deno"],
        "ruby" => &["ruby"],
        "php" => &["php"],
        "perl" => &["perl"],
        "raku" => &["raku", "perl6"],
        "lua" => &["lua"],
        "r" => &["rscript"],
        "julia" => &["julia"],
        "octave" => &["octave"],
        "erlang" => &["escript"],
        "elixir" => &["elixir"],
        "shell" => &["sh", "bash", "zsh", "ksh", "dash"],
        "fish" => &["fish"],
        "powershell" => &["pwsh", "powershell"],
        "nushell" => &["nu"],
        "tcl" => &["tclsh", "wish"],
        "awk" => &["awk", "gawk"],
        "crystal" => &["crystal"],
        "groovy" => &["groovy"],
        _ => &[],
    }
}
