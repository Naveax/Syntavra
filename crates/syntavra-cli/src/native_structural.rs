#![forbid(unsafe_code)]
#![allow(
    clippy::pedantic,
    clippy::too_many_lines,
    clippy::cast_precision_loss,
    clippy::cast_possible_truncation
)]

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, Row, TransactionBehavior};
use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const IGNORE_PARTS: &[&str] = &[
    ".git",
    ".syntavra",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".next",
    ".gradle",
    "vendor",
    "coverage",
    ".idea",
    ".vscode",
];

const GRAPH_EDGE_TYPES: &[&str] = &[
    "calls",
    "calls-short",
    "imports",
    "inherits",
    "implements",
    "overrides",
    "reads",
    "writes",
    "instantiates",
    "test-covers",
];

#[derive(Debug, Clone)]
struct ParsedSymbol {
    path: String,
    name: String,
    qualified_name: String,
    kind: String,
    line: i64,
    end_line: i64,
    signature: String,
    confidence: f64,
    parser: String,
}

#[derive(Debug, Clone)]
struct ParsedEdge {
    source_path: String,
    source_symbol: String,
    edge_type: String,
    target: String,
    target_path: String,
    line: i64,
    confidence: f64,
    metadata_json: String,
}

#[derive(Debug)]
struct ParseResult {
    language: String,
    parser: String,
    semantic: bool,
    diagnostics: Vec<String>,
    symbols: Vec<ParsedSymbol>,
    edges: Vec<ParsedEdge>,
}

#[derive(Debug, Clone)]
struct Context {
    indent: usize,
    name: String,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "inspect"
            && matches!(action.as_str(), "symbol" | "impact" | "paths" | "map" | "stats"))
}

pub fn execute(
    command: &[String],
    arguments: &[String],
    project_root: &Path,
    state_root: &Path,
) -> Result<Value, String> {
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("STRUCTURAL_PROJECT_RESOLVE_FAILED:{error}"))?;
    fs::create_dir_all(state_root)
        .map_err(|error| format!("STRUCTURAL_STATE_DIRECTORY_CREATE_FAILED:{error}"))?;
    let database = state_root.join("structural.sqlite3");
    let mut connection = initialize(&database)?;
    index_repository(&mut connection, &project)?;
    let repository_id = stable_project_id(&project);

    match command {
        [root, action] if root == "inspect" && action == "symbol" => {
            let query = positional_after(arguments, "symbol", 0)?;
            let limit = integer_option(arguments, "--limit", 20)?.max(1);
            inspect_symbol(&connection, query, limit)
        }
        [root, action] if root == "inspect" && action == "impact" => {
            let query = positional_after(arguments, "impact", 0)?;
            let depth = integer_option(arguments, "--max-depth", 4)?.max(0);
            inspect_impact(&connection, query, depth)
        }
        [root, action] if root == "inspect" && action == "paths" => {
            let paths = positionals_after(arguments, "paths")?;
            let depth = integer_option(arguments, "--max-depth", 4)?.max(0);
            impacted_by_paths(&connection, &paths, depth)
        }
        [root, action] if root == "inspect" && action == "map" => {
            let query = positional_after(arguments, "map", 0)?;
            let budget = integer_option(arguments, "--token-budget", 2000)?;
            let depth = integer_option(arguments, "--max-depth", 4)?.max(0);
            repository_map(&connection, query, budget, depth)
        }
        [root, action] if root == "inspect" && action == "stats" => {
            stats(&connection, &repository_id)
        }
        _ => Err("STRUCTURAL_COMMAND_UNSUPPORTED".to_owned()),
    }
}

fn initialize(path: &Path) -> Result<Connection, String> {
    let connection = Connection::open(path)
        .map_err(|error| format!("STRUCTURAL_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "PRAGMA busy_timeout=30000;\
             PRAGMA journal_mode=WAL;\
             PRAGMA foreign_keys=ON;\
             PRAGMA synchronous=NORMAL;\
             CREATE TABLE IF NOT EXISTS structural_files(\
               path TEXT PRIMARY KEY,\
               content_hash TEXT NOT NULL,\
               language TEXT NOT NULL,\
               parser TEXT NOT NULL DEFAULT '',\
               semantic INTEGER NOT NULL DEFAULT 0,\
               diagnostics_json TEXT NOT NULL DEFAULT '[]',\
               indexed_at REAL NOT NULL);\
             CREATE TABLE IF NOT EXISTS structural_symbols(\
               symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,\
               path TEXT NOT NULL,\
               name TEXT NOT NULL,\
               qualified_name TEXT NOT NULL DEFAULT '',\
               kind TEXT NOT NULL,\
               line INTEGER NOT NULL,\
               end_line INTEGER NOT NULL DEFAULT 0,\
               signature TEXT NOT NULL DEFAULT '',\
               confidence REAL NOT NULL DEFAULT 1.0,\
               parser TEXT NOT NULL DEFAULT '',\
               UNIQUE(path,qualified_name,kind,line));\
             CREATE INDEX IF NOT EXISTS structural_symbol_name_idx \
               ON structural_symbols(name,qualified_name);\
             CREATE INDEX IF NOT EXISTS structural_symbol_path_idx \
               ON structural_symbols(path,line);\
             CREATE TABLE IF NOT EXISTS structural_edges(\
               source_path TEXT NOT NULL,\
               source_symbol TEXT NOT NULL,\
               edge_type TEXT NOT NULL,\
               target TEXT NOT NULL,\
               target_path TEXT NOT NULL DEFAULT '',\
               line INTEGER NOT NULL,\
               confidence REAL NOT NULL,\
               metadata_json TEXT NOT NULL DEFAULT '{}',\
               UNIQUE(source_path,source_symbol,edge_type,target,line));\
             CREATE INDEX IF NOT EXISTS structural_edge_target_idx \
               ON structural_edges(target,edge_type);\
             CREATE INDEX IF NOT EXISTS structural_edge_source_idx \
               ON structural_edges(source_symbol,edge_type);\
             CREATE INDEX IF NOT EXISTS structural_edge_path_idx \
               ON structural_edges(source_path,target_path);",
        )
        .map_err(|error| format!("STRUCTURAL_DATABASE_INITIALIZE_FAILED:{error}"))?;
    Ok(connection)
}

fn index_repository(connection: &mut Connection, project: &Path) -> Result<(), String> {
    let paths = source_paths(project)?;
    let current = paths
        .iter()
        .map(|path| {
            let relative = relative_path(project, path)?;
            let digest = sha256_hex(
                &fs::read(path)
                    .map_err(|error| format!("STRUCTURAL_FILE_READ_FAILED:{relative}:{error}"))?,
            );
            Ok((relative, path.clone(), digest))
        })
        .collect::<Result<Vec<_>, String>>()?;

    let known = {
        let mut statement = connection
            .prepare("SELECT path,content_hash FROM structural_files")
            .map_err(|error| format!("STRUCTURAL_KNOWN_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })
            .map_err(|error| format!("STRUCTURAL_KNOWN_QUERY_FAILED:{error}"))?;
        let mut values = BTreeMap::new();
        for row in rows {
            let (path, digest) =
                row.map_err(|error| format!("STRUCTURAL_KNOWN_ROW_FAILED:{error}"))?;
            values.insert(path, digest);
        }
        values
    };

    for (relative, path, digest) in &current {
        if known.get(relative) == Some(digest) {
            continue;
        }
        let text = String::from_utf8_lossy(
            &fs::read(path)
                .map_err(|error| format!("STRUCTURAL_SOURCE_READ_FAILED:{relative}:{error}"))?,
        )
        .into_owned();
        let parsed = parse_source(relative, path, &text);
        replace_file(connection, relative, digest, &parsed)?;
    }

    let current_names = current
        .iter()
        .map(|(relative, _, _)| relative.clone())
        .collect::<BTreeSet<_>>();
    let removed = known
        .keys()
        .filter(|path| !current_names.contains(*path))
        .cloned()
        .collect::<Vec<_>>();
    if !removed.is_empty() {
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| format!("STRUCTURAL_REMOVE_TRANSACTION_FAILED:{error}"))?;
        for relative in removed {
            transaction
                .execute(
                    "DELETE FROM structural_edges WHERE source_path=? OR target_path=?",
                    params![relative, relative],
                )
                .map_err(|error| format!("STRUCTURAL_REMOVE_EDGES_FAILED:{error}"))?;
            transaction
                .execute(
                    "DELETE FROM structural_symbols WHERE path=?",
                    params![relative],
                )
                .map_err(|error| format!("STRUCTURAL_REMOVE_SYMBOLS_FAILED:{error}"))?;
            transaction
                .execute(
                    "DELETE FROM structural_files WHERE path=?",
                    params![relative],
                )
                .map_err(|error| format!("STRUCTURAL_REMOVE_FILE_FAILED:{error}"))?;
        }
        transaction
            .commit()
            .map_err(|error| format!("STRUCTURAL_REMOVE_COMMIT_FAILED:{error}"))?;
    }
    resolve_edges(connection)
}

fn replace_file(
    connection: &mut Connection,
    relative: &str,
    digest: &str,
    parsed: &ParseResult,
) -> Result<(), String> {
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("STRUCTURAL_INDEX_TRANSACTION_FAILED:{error}"))?;
    transaction
        .execute(
            "DELETE FROM structural_edges WHERE source_path=?",
            params![relative],
        )
        .map_err(|error| format!("STRUCTURAL_INDEX_DELETE_EDGES_FAILED:{error}"))?;
    transaction
        .execute(
            "DELETE FROM structural_symbols WHERE path=?",
            params![relative],
        )
        .map_err(|error| format!("STRUCTURAL_INDEX_DELETE_SYMBOLS_FAILED:{error}"))?;
    for symbol in &parsed.symbols {
        transaction
            .execute(
                "INSERT OR IGNORE INTO structural_symbols(\
                   path,name,qualified_name,kind,line,end_line,signature,confidence,parser) \
                 VALUES(?,?,?,?,?,?,?,?,?)",
                params![
                    symbol.path,
                    symbol.name,
                    symbol.qualified_name,
                    symbol.kind,
                    symbol.line,
                    symbol.end_line,
                    symbol.signature,
                    symbol.confidence,
                    symbol.parser,
                ],
            )
            .map_err(|error| format!("STRUCTURAL_INDEX_SYMBOL_FAILED:{error}"))?;
    }
    for edge in &parsed.edges {
        transaction
            .execute(
                "INSERT OR IGNORE INTO structural_edges(\
                   source_path,source_symbol,edge_type,target,target_path,line,confidence,metadata_json) \
                 VALUES(?,?,?,?,?,?,?,?)",
                params![
                    edge.source_path,
                    edge.source_symbol,
                    edge.edge_type,
                    edge.target,
                    edge.target_path,
                    edge.line,
                    edge.confidence,
                    edge.metadata_json,
                ],
            )
            .map_err(|error| format!("STRUCTURAL_INDEX_EDGE_FAILED:{error}"))?;
    }
    transaction
        .execute(
            "INSERT INTO structural_files(\
               path,content_hash,language,parser,semantic,diagnostics_json,indexed_at) \
             VALUES(?,?,?,?,?,?,?) \
             ON CONFLICT(path) DO UPDATE SET\
               content_hash=excluded.content_hash,\
               language=excluded.language,\
               parser=excluded.parser,\
               semantic=excluded.semantic,\
               diagnostics_json=excluded.diagnostics_json,\
               indexed_at=excluded.indexed_at",
            params![
                relative,
                digest,
                parsed.language,
                parsed.parser,
                if parsed.semantic { 1i64 } else { 0i64 },
                serde_json::to_string(&parsed.diagnostics)
                    .map_err(|_| "STRUCTURAL_DIAGNOSTICS_JSON_FAILED".to_owned())?,
                unix_time()?,
            ],
        )
        .map_err(|error| format!("STRUCTURAL_INDEX_FILE_FAILED:{error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("STRUCTURAL_INDEX_COMMIT_FAILED:{error}"))
}

fn resolve_edges(connection: &mut Connection) -> Result<(), String> {
    let symbols = load_symbol_identities(connection)?;
    let mut by_exact: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut by_short: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (path, name, qualified) in symbols {
        by_exact.entry(qualified).or_default().insert(path.clone());
        by_short.entry(name).or_default().insert(path);
    }
    let unresolved = {
        let mut statement = connection
            .prepare(
                "SELECT rowid,target FROM structural_edges \
                 WHERE target_path='' OR target_path IS NULL",
            )
            .map_err(|error| format!("STRUCTURAL_RESOLVE_PREPARE_FAILED:{error}"))?;
        let rows = statement
            .query_map([], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
            })
            .map_err(|error| format!("STRUCTURAL_RESOLVE_QUERY_FAILED:{error}"))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("STRUCTURAL_RESOLVE_ROW_FAILED:{error}"))?
    };
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| format!("STRUCTURAL_RESOLVE_TRANSACTION_FAILED:{error}"))?;
    for (row_id, target) in unresolved {
        let candidates = by_exact
            .get(&target)
            .filter(|values| !values.is_empty())
            .or_else(|| by_short.get(&short_name(&target)));
        if let Some(values) = candidates {
            if values.len() == 1 {
                let path = values.iter().next().expect("one value");
                transaction
                    .execute(
                        "UPDATE structural_edges SET target_path=? WHERE rowid=?",
                        params![path, row_id],
                    )
                    .map_err(|error| format!("STRUCTURAL_RESOLVE_UPDATE_FAILED:{error}"))?;
            }
        }
    }
    transaction
        .commit()
        .map_err(|error| format!("STRUCTURAL_RESOLVE_COMMIT_FAILED:{error}"))
}

fn source_paths(project: &Path) -> Result<Vec<PathBuf>, String> {
    let mut paths = Vec::new();
    visit_directory(project, project, &mut paths)?;
    paths.sort();
    Ok(paths)
}

fn visit_directory(
    project: &Path,
    directory: &Path,
    output: &mut Vec<PathBuf>,
) -> Result<(), String> {
    let mut entries = fs::read_dir(directory)
        .map_err(|error| {
            format!(
                "STRUCTURAL_DIRECTORY_READ_FAILED:{}:{error}",
                directory.display()
            )
        })?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("STRUCTURAL_DIRECTORY_ENTRY_FAILED:{error}"))?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let file_type = entry
            .file_type()
            .map_err(|error| format!("STRUCTURAL_FILE_TYPE_FAILED:{error}"))?;
        if file_type.is_symlink() {
            continue;
        }
        let path = entry.path();
        let relative = path.strip_prefix(project).unwrap_or(&path);
        if relative.components().any(|part| {
            let value = part.as_os_str().to_string_lossy();
            IGNORE_PARTS.iter().any(|ignored| *ignored == value)
        }) {
            continue;
        }
        if file_type.is_dir() {
            visit_directory(project, &path, output)?;
        } else if file_type.is_file() && supported_path(&path) {
            output.push(path);
        }
    }
    Ok(())
}

fn supported_path(path: &Path) -> bool {
    matches!(
        path.extension()
            .and_then(|value| value.to_str())
            .map(str::to_ascii_lowercase)
            .as_deref(),
        Some(
            "py" | "js"
                | "jsx"
                | "mjs"
                | "cjs"
                | "ts"
                | "tsx"
                | "rs"
                | "go"
                | "java"
                | "cs"
                | "c"
                | "h"
                | "cc"
                | "cpp"
                | "cxx"
                | "hpp"
                | "hh"
                | "rb"
                | "php"
                | "lua"
                | "luau"
        )
    )
}

fn relative_path(project: &Path, path: &Path) -> Result<String, String> {
    path.strip_prefix(project)
        .map(|relative| relative.to_string_lossy().replace('\\', "/"))
        .map_err(|_| "STRUCTURAL_PATH_OUTSIDE_PROJECT".to_owned())
}

fn parse_source(relative: &str, path: &Path, text: &str) -> ParseResult {
    match path
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        Some("py") => parse_python(relative, text),
        extension => parse_lexical(relative, text, extension.unwrap_or("")),
    }
}

fn parse_python(relative: &str, text: &str) -> ParseResult {
    let lines = text.lines().collect::<Vec<_>>();
    let mut symbols = Vec::new();
    let mut edges = Vec::new();
    let mut contexts: Vec<Context> = Vec::new();
    let mut aliases = BTreeMap::new();

    for (offset, raw_line) in lines.iter().enumerate() {
        let line_number = i64::try_from(offset + 1).unwrap_or(i64::MAX);
        let trimmed = raw_line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let indent = indentation(raw_line);
        while contexts
            .last()
            .is_some_and(|context| indent <= context.indent)
        {
            contexts.pop();
        }
        let current = context_name(&contexts);

        if let Some(rest) = trimmed.strip_prefix("import ") {
            for item in rest.split(',') {
                let item = item.trim();
                let mut pieces = item.split_whitespace();
                let module = pieces.next().unwrap_or("");
                let alias = if pieces.next() == Some("as") {
                    pieces.next().unwrap_or(module)
                } else {
                    module.split('.').next().unwrap_or(module)
                };
                if !module.is_empty() {
                    aliases.insert(alias.to_owned(), module.to_owned());
                    edges.push(edge(
                        relative,
                        &current,
                        "imports",
                        module,
                        line_number,
                        1.0,
                    ));
                }
            }
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix("from ") {
            if let Some((module, imported)) = rest.split_once(" import ") {
                let module = module.trim();
                if !module.is_empty() {
                    edges.push(edge(
                        relative,
                        &current,
                        "imports",
                        module,
                        line_number,
                        1.0,
                    ));
                }
                for item in imported.split(',') {
                    let item = item.trim();
                    let mut pieces = item.split_whitespace();
                    let name = pieces.next().unwrap_or("");
                    let alias = if pieces.next() == Some("as") {
                        pieces.next().unwrap_or(name)
                    } else {
                        name
                    };
                    if !name.is_empty() {
                        aliases.insert(alias.to_owned(), format!("{module}.{name}"));
                    }
                }
            }
            continue;
        }

        if let Some(class) = python_class(trimmed) {
            let qualified = qualify(&contexts, &class.0);
            symbols.push(ParsedSymbol {
                path: relative.to_owned(),
                name: class.0.clone(),
                qualified_name: qualified.clone(),
                kind: "class".to_owned(),
                line: line_number,
                end_line: block_end(&lines, offset, indent),
                signature: String::new(),
                confidence: 1.0,
                parser: "python-ast-v3".to_owned(),
            });
            edges.push(edge(
                relative,
                &current,
                "defines",
                &qualified,
                line_number,
                1.0,
            ));
            for base in class.1 {
                edges.push(edge(
                    relative,
                    &qualified,
                    "inherits",
                    &base,
                    line_number,
                    0.98,
                ));
            }
            contexts.push(Context {
                indent,
                name: class.0,
            });
            continue;
        }

        if let Some(function) = python_function(trimmed) {
            let qualified = qualify(&contexts, &function.0);
            symbols.push(ParsedSymbol {
                path: relative.to_owned(),
                name: function.0.clone(),
                qualified_name: qualified.clone(),
                kind: if contexts.is_empty() {
                    "function".to_owned()
                } else {
                    "method".to_owned()
                },
                line: line_number,
                end_line: block_end(&lines, offset, indent),
                signature: function.1,
                confidence: 1.0,
                parser: "python-ast-v3".to_owned(),
            });
            edges.push(edge(
                relative,
                &current,
                "defines",
                &qualified,
                line_number,
                1.0,
            ));
            contexts.push(Context {
                indent,
                name: function.0,
            });
            continue;
        }

        add_python_calls(
            relative,
            trimmed,
            &current,
            line_number,
            &aliases,
            &mut edges,
        );
        add_python_names(relative, trimmed, &current, line_number, &mut edges);
    }

    ParseResult {
        language: "python".to_owned(),
        parser: "python-ast-v3".to_owned(),
        semantic: true,
        diagnostics: Vec::new(),
        symbols,
        edges,
    }
}

fn parse_lexical(relative: &str, text: &str, extension: &str) -> ParseResult {
    let (language, parser) = match extension {
        "js" | "jsx" | "mjs" | "cjs" | "ts" | "tsx" => ("javascript", "regex-structural-v3"),
        "rs" => ("rust", "regex-structural-v3"),
        "go" => ("go", "regex-structural-v3"),
        "java" => ("java", "regex-structural-v3"),
        "cs" => ("csharp", "regex-structural-v3"),
        "c" | "h" | "cc" | "cpp" | "cxx" | "hpp" | "hh" => ("cpp", "regex-structural-v3"),
        "rb" => ("ruby", "regex-structural-v3"),
        "php" => ("php", "regex-structural-v3"),
        "lua" | "luau" => ("lua", "regex-structural-v3"),
        _ => ("unknown", "regex-structural-v3"),
    };
    let mut symbols = Vec::new();
    let mut edges = Vec::new();
    for (offset, line) in text.lines().enumerate() {
        let number = i64::try_from(offset + 1).unwrap_or(i64::MAX);
        let trimmed = line.trim();
        if let Some((kind, name)) = lexical_definition(trimmed, extension) {
            symbols.push(ParsedSymbol {
                path: relative.to_owned(),
                name: name.clone(),
                qualified_name: name.clone(),
                kind,
                line: number,
                end_line: number,
                signature: String::new(),
                confidence: 0.82,
                parser: parser.to_owned(),
            });
            edges.push(edge(relative, "<module>", "defines", &name, number, 0.82));
        }
        if let Some(target) = lexical_import(trimmed, extension) {
            edges.push(edge(relative, "<module>", "imports", &target, number, 0.8));
        }
    }
    ParseResult {
        language: language.to_owned(),
        parser: parser.to_owned(),
        semantic: false,
        diagnostics: Vec::new(),
        symbols,
        edges,
    }
}

fn python_class(line: &str) -> Option<(String, Vec<String>)> {
    let rest = line.strip_prefix("class ")?;
    let name_end = rest
        .find(|character: char| !(character.is_ascii_alphanumeric() || character == '_'))
        .unwrap_or(rest.len());
    let name = rest[..name_end].to_owned();
    if name.is_empty() {
        return None;
    }
    let bases = rest
        .find('(')
        .zip(rest.rfind(')'))
        .filter(|(start, end)| start < end)
        .map_or_else(Vec::new, |(start, end)| {
            rest[start + 1..end]
                .split(',')
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
                .collect()
        });
    Some((name, bases))
}

fn python_function(line: &str) -> Option<(String, String)> {
    let rest = line
        .strip_prefix("async def ")
        .or_else(|| line.strip_prefix("def "))?;
    let open = rest.find('(')?;
    let name = rest[..open].trim().to_owned();
    if name.is_empty() {
        return None;
    }
    let close = matching_paren(rest, open)?;
    let arguments = rest[open + 1..close]
        .split(',')
        .filter_map(|raw| {
            let value = raw.trim().trim_start_matches('*');
            if value.is_empty() {
                return None;
            }
            let name = value.split([':', '=']).next().unwrap_or("").trim();
            (!name.is_empty()).then(|| name.to_owned())
        })
        .collect::<Vec<_>>()
        .join(", ");
    let mut signature = format!("({arguments})");
    if let Some(annotation) = rest[close + 1..].split_once("->").map(|(_, value)| value) {
        let annotation = annotation.trim().trim_end_matches(':').trim();
        if !annotation.is_empty() {
            signature.push_str(" -> ");
            signature.push_str(annotation);
        }
    }
    Some((name, signature))
}

fn matching_paren(value: &str, start: usize) -> Option<usize> {
    let mut depth = 0usize;
    for (offset, character) in value[start..].char_indices() {
        match character {
            '(' => depth += 1,
            ')' => {
                depth = depth.saturating_sub(1);
                if depth == 0 {
                    return Some(start + offset);
                }
            }
            _ => {}
        }
    }
    None
}

fn block_end(lines: &[&str], start: usize, indent: usize) -> i64 {
    let mut end = start + 1;
    for (offset, line) in lines.iter().enumerate().skip(start + 1) {
        if line.trim().is_empty() {
            continue;
        }
        if indentation(line) <= indent {
            break;
        }
        end = offset + 1;
    }
    i64::try_from(end).unwrap_or(i64::MAX)
}

fn add_python_calls(
    relative: &str,
    line: &str,
    source: &str,
    line_number: i64,
    aliases: &BTreeMap<String, String>,
    output: &mut Vec<ParsedEdge>,
) {
    let controls = ["if", "for", "while", "return", "with", "assert", "lambda"];
    for (index, character) in line.char_indices() {
        if character != '(' {
            continue;
        }
        let prefix = &line[..index];
        let candidate = prefix
            .trim_end()
            .rsplit(|value: char| !(value.is_ascii_alphanumeric() || matches!(value, '_' | '.')))
            .next()
            .unwrap_or("");
        if candidate.is_empty() || controls.contains(&candidate) {
            continue;
        }
        let head = candidate.split('.').next().unwrap_or(candidate);
        let target = aliases.get(head).map_or_else(
            || candidate.to_owned(),
            |module| format!("{module}{}", &candidate[head.len()..]),
        );
        output.push(edge(relative, source, "calls", &target, line_number, 0.99));
        if let Some((_, short)) = target.rsplit_once('.') {
            output.push(edge(
                relative,
                source,
                "calls-short",
                short,
                line_number,
                0.9,
            ));
        }
    }
}

fn add_python_names(
    relative: &str,
    line: &str,
    source: &str,
    line_number: i64,
    output: &mut Vec<ParsedEdge>,
) {
    let assignment = line.find('=');
    for token in identifiers(line) {
        if [
            "return", "if", "else", "elif", "for", "while", "in", "and", "or", "not", "True",
            "False", "None", "with", "as", "try", "except", "raise", "yield",
        ]
        .contains(&token.as_str())
        {
            continue;
        }
        let position = line.find(&token).unwrap_or(usize::MAX);
        let edge_type = if assignment.is_some_and(|equal| position < equal) {
            "writes"
        } else {
            "reads"
        };
        output.push(edge(relative, source, edge_type, &token, line_number, 0.82));
    }
}

fn identifiers(value: &str) -> Vec<String> {
    let mut result = Vec::new();
    let mut current = String::new();
    for character in value.chars() {
        if character.is_ascii_alphanumeric() || character == '_' {
            current.push(character);
        } else if !current.is_empty() {
            if current
                .chars()
                .next()
                .is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
            {
                result.push(current.clone());
            }
            current.clear();
        }
    }
    if !current.is_empty() {
        result.push(current);
    }
    result
}

fn lexical_definition(line: &str, extension: &str) -> Option<(String, String)> {
    let patterns: &[(&str, &str)] = match extension {
        "rs" => &[
            ("fn ", "function"),
            ("pub fn ", "function"),
            ("struct ", "struct"),
            ("pub struct ", "struct"),
            ("enum ", "enum"),
            ("pub enum ", "enum"),
            ("trait ", "trait"),
            ("pub trait ", "trait"),
            ("mod ", "module"),
            ("pub mod ", "module"),
        ],
        "go" => &[("func ", "function"), ("type ", "type")],
        "js" | "jsx" | "mjs" | "cjs" | "ts" | "tsx" => &[
            ("class ", "class"),
            ("export class ", "class"),
            ("function ", "function"),
            ("export function ", "function"),
            ("interface ", "interface"),
            ("export interface ", "interface"),
        ],
        "java" | "cs" | "c" | "h" | "cc" | "cpp" | "cxx" | "hpp" | "hh" => &[
            ("class ", "class"),
            ("struct ", "struct"),
            ("enum ", "enum"),
        ],
        "rb" => &[("def ", "function"), ("class ", "class")],
        "php" => &[("function ", "function"), ("class ", "class")],
        "lua" | "luau" => &[("function ", "function")],
        _ => &[],
    };
    for (prefix, kind) in patterns {
        if let Some(rest) = line.strip_prefix(prefix) {
            let name = rest
                .trim_start_matches("async ")
                .trim_start_matches("unsafe ")
                .split(|character: char| {
                    !(character.is_ascii_alphanumeric() || character == '_' || character == '$')
                })
                .next()
                .unwrap_or("");
            if !name.is_empty() {
                return Some(((*kind).to_owned(), name.to_owned()));
            }
        }
    }
    None
}

fn lexical_import(line: &str, extension: &str) -> Option<String> {
    match extension {
        "rs" => line
            .strip_prefix("use ")
            .map(|value| value.trim_end_matches(';').trim().to_owned()),
        "go" => line
            .strip_prefix("import ")
            .map(|value| value.trim().trim_matches('"').to_owned()),
        "java" => line
            .strip_prefix("import ")
            .map(|value| value.trim_end_matches(';').trim().to_owned()),
        "cs" => line
            .strip_prefix("using ")
            .map(|value| value.trim_end_matches(';').trim().to_owned()),
        "js" | "jsx" | "mjs" | "cjs" | "ts" | "tsx" => quoted_value(line).map(str::to_owned),
        _ => None,
    }
}

fn quoted_value(line: &str) -> Option<&str> {
    let quote = line.find(['\'', '"'])?;
    let delimiter = line.as_bytes()[quote];
    let end = line.as_bytes()[quote + 1..]
        .iter()
        .position(|value| *value == delimiter)?;
    Some(&line[quote + 1..quote + 1 + end])
}

fn edge(
    path: &str,
    source: &str,
    edge_type: &str,
    target: &str,
    line: i64,
    confidence: f64,
) -> ParsedEdge {
    ParsedEdge {
        source_path: path.to_owned(),
        source_symbol: source.to_owned(),
        edge_type: edge_type.to_owned(),
        target: target.to_owned(),
        target_path: String::new(),
        line,
        confidence,
        metadata_json: "{}".to_owned(),
    }
}

fn indentation(line: &str) -> usize {
    line.chars()
        .take_while(|character| character.is_whitespace())
        .map(|character| if character == '\t' { 4 } else { 1 })
        .sum()
}

fn context_name(contexts: &[Context]) -> String {
    if contexts.is_empty() {
        "<module>".to_owned()
    } else {
        contexts
            .iter()
            .map(|context| context.name.as_str())
            .collect::<Vec<_>>()
            .join(".")
    }
}

fn qualify(contexts: &[Context], name: &str) -> String {
    if contexts.is_empty() {
        name.to_owned()
    } else {
        format!("{}.{}", context_name(contexts), name)
    }
}

fn load_symbol_identities(
    connection: &Connection,
) -> Result<Vec<(String, String, String)>, String> {
    let mut statement = connection
        .prepare("SELECT path,name,qualified_name FROM structural_symbols")
        .map_err(|error| format!("STRUCTURAL_SYMBOL_IDENTITIES_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .map_err(|error| format!("STRUCTURAL_SYMBOL_IDENTITIES_QUERY_FAILED:{error}"))?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("STRUCTURAL_SYMBOL_IDENTITIES_ROW_FAILED:{error}"))
}

fn inspect_symbol(connection: &Connection, query: &str, limit: i64) -> Result<Value, String> {
    let mut statement = connection
        .prepare(
            "SELECT path,name,qualified_name,kind,line,end_line,signature,confidence,parser \
             FROM structural_symbols \
             WHERE name LIKE ? OR qualified_name LIKE ? OR signature LIKE ? \
             ORDER BY CASE WHEN name=? OR qualified_name=? THEN 0 ELSE 1 END,\
                      confidence DESC,path,line \
             LIMIT ?",
        )
        .map_err(|error| format!("STRUCTURAL_SYMBOL_PREPARE_FAILED:{error}"))?;
    let pattern = format!("%{query}%");
    let rows = statement
        .query_map(
            params![&pattern, &pattern, &pattern, query, query, limit],
            symbol_row_json,
        )
        .map_err(|error| format!("STRUCTURAL_SYMBOL_QUERY_FAILED:{error}"))?;
    let symbols = rows
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("STRUCTURAL_SYMBOL_ROW_FAILED:{error}"))?;
    Ok(json!({"query": query, "symbols": symbols}))
}

fn inspect_impact(connection: &Connection, query: &str, max_depth: i64) -> Result<Value, String> {
    let symbols = graph_symbols(connection)?;
    let edges = graph_edges(connection)?;
    let short_query = short_name(query);
    let definitions = symbols
        .iter()
        .filter(|row| {
            row["name"].as_str() == Some(query)
                || row["qualified_name"].as_str() == Some(query)
                || row["qualified_name"]
                    .as_str()
                    .is_some_and(|value| short_name(value) == short_query)
        })
        .map(definition_json)
        .collect::<Vec<_>>();

    let mut seeds = BTreeSet::from([query.to_owned(), short_query]);
    for definition in &definitions {
        if let Some(value) = definition["qualified_name"].as_str() {
            seeds.insert(value.to_owned());
        }
        if let Some(value) = definition["name"].as_str() {
            seeds.insert(value.to_owned());
        }
    }

    let mut reverse: BTreeMap<String, Vec<(String, f64)>> = BTreeMap::new();
    let mut edges_by_target: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut symbol_paths: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for row in &symbols {
        let path = string_field(row, "path")?;
        let name = string_field(row, "name")?;
        let qualified = string_field(row, "qualified_name")?;
        symbol_paths
            .entry(qualified)
            .or_default()
            .insert(path.clone());
        symbol_paths.entry(name).or_default().insert(path);
    }
    for row in &edges {
        let target = string_field(row, "target")?;
        let short_target = short_name(&target);
        let source = string_field(row, "source_symbol")?;
        let confidence =
            number_field(row, "confidence")? * edge_weight(&string_field(row, "edge_type")?);
        reverse
            .entry(target.clone())
            .or_default()
            .push((source.clone(), confidence));
        reverse
            .entry(short_target.clone())
            .or_default()
            .push((source, confidence));
        edges_by_target
            .entry(target.clone())
            .or_default()
            .push(row.clone());
        if short_target != target {
            edges_by_target
                .entry(short_target)
                .or_default()
                .push(row.clone());
        }
    }

    let mut queue = seeds
        .iter()
        .cloned()
        .map(|seed| (seed, 0i64))
        .collect::<VecDeque<_>>();
    let mut best_depth = seeds
        .iter()
        .cloned()
        .map(|seed| (seed, 0i64))
        .collect::<BTreeMap<_, _>>();
    let mut affected_paths = definitions
        .iter()
        .filter_map(|row| row["path"].as_str().map(str::to_owned))
        .collect::<BTreeSet<_>>();
    let mut traversed = Vec::new();
    while let Some((target, depth)) = queue.pop_front() {
        if depth >= max_depth {
            continue;
        }
        for row in edges_by_target.get(&target).into_iter().flatten() {
            let caller = string_field(row, "source_symbol")?;
            let next_depth = depth + 1;
            if best_depth
                .get(&caller)
                .is_some_and(|previous| *previous <= next_depth)
            {
                continue;
            }
            best_depth.insert(caller.clone(), next_depth);
            affected_paths.insert(string_field(row, "source_path")?);
            let target_path = string_field(row, "target_path")?;
            if !target_path.is_empty() {
                affected_paths.insert(target_path);
            }
            if let Some(paths) = symbol_paths.get(&caller) {
                affected_paths.extend(paths.iter().cloned());
            }
            let mut enriched = row.clone();
            enriched["depth"] = json!(next_depth);
            traversed.push(enriched);
            queue.push_back((caller, next_depth));
        }
    }

    let direct = traversed
        .iter()
        .filter(|row| row["depth"] == 1)
        .cloned()
        .collect::<Vec<_>>();
    let rank = personalized_rank(&reverse, &seeds);
    let mut ranked_symbols = best_depth
        .iter()
        .filter(|(_, depth)| **depth > 0)
        .map(|(symbol, depth)| {
            json!({
                "symbol": symbol,
                "depth": depth,
                "rank": rank.get(symbol).copied().unwrap_or(0.0),
                "paths": symbol_paths.get(symbol).map_or_else(Vec::new, |values| values.iter().cloned().collect()),
            })
        })
        .collect::<Vec<_>>();
    ranked_symbols.sort_by(|left, right| {
        integer_json(left, "depth")
            .cmp(&integer_json(right, "depth"))
            .then_with(|| number_json(right, "rank").total_cmp(&number_json(left, "rank")))
            .then_with(|| string_json(left, "symbol").cmp(&string_json(right, "symbol")))
    });
    traversed.sort_by(|left, right| {
        integer_json(left, "depth")
            .cmp(&integer_json(right, "depth"))
            .then_with(|| {
                number_json(right, "confidence").total_cmp(&number_json(left, "confidence"))
            })
            .then_with(|| string_json(left, "source_path").cmp(&string_json(right, "source_path")))
            .then_with(|| integer_json(left, "line").cmp(&integer_json(right, "line")))
    });

    let affected = affected_paths.into_iter().collect::<Vec<_>>();
    let tests = affected
        .iter()
        .filter(|path| is_test_path(path))
        .cloned()
        .collect::<Vec<_>>();
    let confidence = if definitions.is_empty() {
        0.4
    } else if definitions
        .iter()
        .all(|row| number_json(row, "confidence") >= 0.9)
    {
        1.0
    } else {
        0.75
    };
    Ok(json!({
        "query": query,
        "definitions": definitions,
        "direct_references": direct,
        "transitive_references": traversed,
        "ranked_symbols": ranked_symbols,
        "affected_paths": affected,
        "affected_tests": tests,
        "required_verifiers": required_verifiers(&affected),
        "max_depth": max_depth,
        "confidence": confidence,
        "recall_boundary": "semantic-snapshot+python-ast+language-specific-static-graph",
    }))
}

fn impacted_by_paths(
    connection: &Connection,
    paths: &[String],
    max_depth: i64,
) -> Result<Value, String> {
    let normalized = paths
        .iter()
        .map(|path| path.replace('\\', "/"))
        .collect::<BTreeSet<_>>();
    let mut symbols = BTreeSet::new();
    if !normalized.is_empty() {
        let all = graph_symbols(connection)?;
        for row in all {
            let path = string_field(&row, "path")?;
            if normalized.contains(&path) {
                let qualified = string_field(&row, "qualified_name")?;
                let name = string_field(&row, "name")?;
                symbols.insert(if qualified.is_empty() {
                    name
                } else {
                    qualified
                });
            }
        }
    }
    let mut affected = normalized.clone();
    for symbol in &symbols {
        let impact = inspect_impact(connection, symbol, max_depth)?;
        if let Some(values) = impact["affected_paths"].as_array() {
            affected.extend(values.iter().filter_map(Value::as_str).map(str::to_owned));
        }
    }
    let affected_paths = affected.into_iter().collect::<Vec<_>>();
    let affected_tests = affected_paths
        .iter()
        .filter(|path| is_test_path(path))
        .cloned()
        .collect::<Vec<_>>();
    let verifiers = required_verifiers(&affected_paths);
    Ok(json!({
        "changed_paths": normalized.into_iter().collect::<Vec<_>>(),
        "seed_symbols": symbols.into_iter().collect::<Vec<_>>(),
        "affected_paths": affected_paths,
        "affected_tests": affected_tests,
        "required_verifiers": verifiers,
    }))
}

fn repository_map(
    connection: &Connection,
    query: &str,
    token_budget: i64,
    max_depth: i64,
) -> Result<Value, String> {
    let impact = inspect_impact(connection, query, max_depth)?;
    let symbols = graph_symbols(connection)?;
    let rank = impact["ranked_symbols"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|row| Some((row["symbol"].as_str()?.to_owned(), row["rank"].as_f64()?)))
        .collect::<BTreeMap<_, _>>();
    let seeds = impact["definitions"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|row| {
            [row["qualified_name"].as_str(), row["name"].as_str()]
                .into_iter()
                .flatten()
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .collect::<BTreeSet<_>>();
    let affected = impact["affected_paths"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect::<BTreeSet<_>>();
    let query_lower = query.to_lowercase();
    let mut candidates = symbols
        .into_iter()
        .map(|row| {
            let path = string_json(&row, "path");
            let qualified = string_json(&row, "qualified_name");
            let name = string_json(&row, "name");
            let parser = string_json(&row, "parser");
            let text = format!(
                "{}:{} {} {} {}",
                path,
                integer_json(&row, "line"),
                string_json(&row, "kind"),
                qualified,
                string_json(&row, "signature")
            )
            .trim()
            .to_owned();
            let estimated =
                i64::try_from(text.chars().count().div_ceil(4).max(1)).unwrap_or(i64::MAX);
            let lexical = if qualified.to_lowercase().contains(&query_lower) {
                1.0
            } else {
                0.0
            };
            let graph = rank
                .get(&qualified)
                .or_else(|| rank.get(&name))
                .copied()
                .unwrap_or(0.0);
            let affected_score = if affected.contains(&path) { 0.4 } else { 0.0 };
            let semantic = if parser.starts_with("semantic") || parser.starts_with("python-ast") {
                0.2
            } else {
                0.0
            };
            let confidence = number_json(&row, "confidence");
            let mut score = 5.0 * lexical
                + 3.0 * graph
                + affected_score
                + semantic
                + confidence / (estimated as f64 + 2.0).log2().max(1.0);
            if seeds.contains(&qualified) || seeds.contains(&name) {
                score += 10.0;
            }
            (score, row, estimated)
        })
        .collect::<Vec<_>>();
    candidates.sort_by(|left, right| {
        right
            .0
            .total_cmp(&left.0)
            .then_with(|| string_json(&left.1, "path").cmp(&string_json(&right.1, "path")))
            .then_with(|| integer_json(&left.1, "line").cmp(&integer_json(&right.1, "line")))
    });
    let mut used = 0i64;
    let mut selected = Vec::new();
    for (score, row, estimated) in candidates {
        if used.saturating_add(estimated) > token_budget {
            continue;
        }
        selected.push(json!({
            "path": row["path"],
            "line": row["line"],
            "end_line": row["end_line"],
            "kind": row["kind"],
            "symbol": row["qualified_name"],
            "signature": row["signature"],
            "score": score,
            "estimated_tokens": estimated,
            "parser": row["parser"],
        }));
        used = used.saturating_add(estimated);
    }
    let mut payload = json!({
        "query": query,
        "budget": token_budget,
        "used": used,
        "selected": selected,
        "affected_paths": impact["affected_paths"],
        "affected_tests": impact["affected_tests"],
        "required_verifiers": impact["required_verifiers"],
    });
    let digest = sha256_hex(canonical_json(&payload).as_bytes());
    payload["map_hash"] = json!(digest);
    Ok(payload)
}

fn stats(connection: &Connection, repository_id: &str) -> Result<Value, String> {
    let files = scalar_count(connection, "SELECT COUNT(*) FROM structural_files")?;
    let symbols = scalar_count(connection, "SELECT COUNT(*) FROM structural_symbols")?;
    let edges = scalar_count(connection, "SELECT COUNT(*) FROM structural_edges")?;
    let semantic = scalar_count(
        connection,
        "SELECT COUNT(*) FROM structural_files WHERE semantic=1",
    )?;
    let languages = grouped_counts(
        connection,
        "SELECT language,COUNT(*) count FROM structural_files GROUP BY language",
    )?;
    let parsers = grouped_counts(
        connection,
        "SELECT parser,COUNT(*) count FROM structural_files GROUP BY parser",
    )?;
    let graph = json!({
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "languages": languages,
        "parsers": parsers,
    });
    Ok(json!({
        "repository_id": repository_id,
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "languages": graph["languages"],
        "parsers": graph["parsers"],
        "semantic_files": semantic,
        "graph_hash": sha256_hex(canonical_json(&graph).as_bytes()),
    }))
}

fn graph_symbols(connection: &Connection) -> Result<Vec<Value>, String> {
    let mut statement = connection
        .prepare("SELECT * FROM structural_symbols")
        .map_err(|error| format!("STRUCTURAL_GRAPH_SYMBOLS_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], full_symbol_row_json)
        .map_err(|error| format!("STRUCTURAL_GRAPH_SYMBOLS_QUERY_FAILED:{error}"))?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("STRUCTURAL_GRAPH_SYMBOLS_ROW_FAILED:{error}"))
}

fn graph_edges(connection: &Connection) -> Result<Vec<Value>, String> {
    let placeholders = GRAPH_EDGE_TYPES
        .iter()
        .map(|value| format!("'{value}'"))
        .collect::<Vec<_>>()
        .join(",");
    let mut statement = connection
        .prepare(&format!(
            "SELECT * FROM structural_edges WHERE edge_type IN ({placeholders})"
        ))
        .map_err(|error| format!("STRUCTURAL_GRAPH_EDGES_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], edge_row_json)
        .map_err(|error| format!("STRUCTURAL_GRAPH_EDGES_QUERY_FAILED:{error}"))?;
    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("STRUCTURAL_GRAPH_EDGES_ROW_FAILED:{error}"))
}

fn symbol_row_json(row: &Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "path": row.get::<_, String>(0)?,
        "name": row.get::<_, String>(1)?,
        "qualified_name": row.get::<_, String>(2)?,
        "kind": row.get::<_, String>(3)?,
        "line": row.get::<_, i64>(4)?,
        "end_line": row.get::<_, i64>(5)?,
        "signature": row.get::<_, String>(6)?,
        "confidence": row.get::<_, f64>(7)?,
        "parser": row.get::<_, String>(8)?,
    }))
}

fn full_symbol_row_json(row: &Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "symbol_id": row.get::<_, i64>(0)?,
        "path": row.get::<_, String>(1)?,
        "name": row.get::<_, String>(2)?,
        "qualified_name": row.get::<_, String>(3)?,
        "kind": row.get::<_, String>(4)?,
        "line": row.get::<_, i64>(5)?,
        "end_line": row.get::<_, i64>(6)?,
        "signature": row.get::<_, String>(7)?,
        "confidence": row.get::<_, f64>(8)?,
        "parser": row.get::<_, String>(9)?,
    }))
}

fn edge_row_json(row: &Row<'_>) -> rusqlite::Result<Value> {
    Ok(json!({
        "source_path": row.get::<_, String>(0)?,
        "source_symbol": row.get::<_, String>(1)?,
        "edge_type": row.get::<_, String>(2)?,
        "target": row.get::<_, String>(3)?,
        "target_path": row.get::<_, String>(4)?,
        "line": row.get::<_, i64>(5)?,
        "confidence": row.get::<_, f64>(6)?,
        "metadata_json": row.get::<_, String>(7)?,
    }))
}

fn definition_json(row: &Value) -> Value {
    json!({
        "path": row["path"],
        "name": row["name"],
        "qualified_name": row["qualified_name"],
        "kind": row["kind"],
        "line": row["line"],
        "end_line": row["end_line"],
        "signature": row["signature"],
        "confidence": row["confidence"],
        "parser": row["parser"],
    })
}

fn personalized_rank(
    reverse: &BTreeMap<String, Vec<(String, f64)>>,
    seeds: &BTreeSet<String>,
) -> BTreeMap<String, f64> {
    let mut nodes = reverse.keys().cloned().collect::<BTreeSet<_>>();
    for callers in reverse.values() {
        nodes.extend(callers.iter().map(|(source, _)| source.clone()));
    }
    nodes.extend(seeds.iter().cloned());
    if nodes.is_empty() {
        return BTreeMap::new();
    }
    let actual = seeds.intersection(&nodes).cloned().collect::<BTreeSet<_>>();
    let actual = if actual.is_empty() {
        nodes.clone()
    } else {
        actual
    };
    let denominator = actual.len() as f64;
    let teleport = nodes
        .iter()
        .map(|node| {
            (
                node.clone(),
                if actual.contains(node) {
                    1.0 / denominator
                } else {
                    0.0
                },
            )
        })
        .collect::<BTreeMap<_, _>>();
    let mut rank = teleport.clone();
    for _ in 0..32 {
        let mut updated = nodes
            .iter()
            .map(|node| (node.clone(), (1.0 - 0.85) * teleport[node]))
            .collect::<BTreeMap<_, _>>();
        for (target, callers) in reverse {
            let total = callers
                .iter()
                .map(|(_, weight)| weight.max(0.01))
                .sum::<f64>();
            for (caller, weight) in callers {
                *updated.entry(caller.clone()).or_default() +=
                    0.85 * rank.get(target).copied().unwrap_or(0.0) * weight.max(0.01) / total;
            }
        }
        rank = updated;
    }
    rank
}

fn edge_weight(edge_type: &str) -> f64 {
    match edge_type {
        "calls" => 1.0,
        "calls-short" => 0.86,
        "imports" => 0.62,
        "inherits" | "implements" => 0.94,
        "overrides" => 0.96,
        "instantiates" => 0.88,
        "reads" => 0.46,
        "writes" => 0.58,
        "test-covers" => 1.0,
        _ => 0.5,
    }
}

fn required_verifiers(paths: &[String]) -> Vec<String> {
    let suffixes = paths
        .iter()
        .filter_map(|path| Path::new(path).extension().and_then(|value| value.to_str()))
        .map(str::to_ascii_lowercase)
        .collect::<BTreeSet<_>>();
    let mut commands = Vec::new();
    if suffixes.contains("py") {
        commands.push("python -m unittest discover -s tests -q".to_owned());
    }
    if suffixes
        .iter()
        .any(|value| ["js", "jsx", "ts", "tsx"].contains(&value.as_str()))
    {
        commands.push("npm test -- --runInBand".to_owned());
    }
    if suffixes.contains("rs") {
        commands.push("cargo test --all-targets".to_owned());
    }
    if suffixes.contains("go") {
        commands.push("go test ./...".to_owned());
    }
    if suffixes.contains("java") {
        commands.push("./gradlew test".to_owned());
    }
    if suffixes.contains("cs") {
        commands.push("dotnet test".to_owned());
    }
    if suffixes
        .iter()
        .any(|value| ["c", "cc", "cpp", "cxx", "h", "hpp"].contains(&value.as_str()))
    {
        commands.push("ctest --test-dir build --output-on-failure".to_owned());
    }
    if suffixes.contains("rb") {
        commands.push("bundle exec rake test".to_owned());
    }
    if suffixes.contains("php") {
        commands.push("vendor/bin/phpunit".to_owned());
    }
    commands
}

fn is_test_path(path: &str) -> bool {
    let normalized = path.replace('\\', "/").to_lowercase();
    let components = normalized.split('/').collect::<Vec<_>>();
    components
        .iter()
        .any(|part| matches!(*part, "test" | "tests" | "spec" | "specs"))
        || normalized.contains("/test_")
        || normalized.contains("/spec_")
        || normalized.contains("_test.")
        || normalized.contains(".spec.")
        || normalized.contains(".test.")
}

fn grouped_counts(connection: &Connection, query: &str) -> Result<Value, String> {
    let mut statement = connection
        .prepare(query)
        .map_err(|error| format!("STRUCTURAL_GROUP_PREPARE_FAILED:{error}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        })
        .map_err(|error| format!("STRUCTURAL_GROUP_QUERY_FAILED:{error}"))?;
    let mut values = Map::new();
    for row in rows {
        let (key, count) = row.map_err(|error| format!("STRUCTURAL_GROUP_ROW_FAILED:{error}"))?;
        values.insert(key, json!(count));
    }
    Ok(Value::Object(values))
}

fn scalar_count(connection: &Connection, query: &str) -> Result<i64, String> {
    connection
        .query_row(query, [], |row| row.get(0))
        .map_err(|error| format!("STRUCTURAL_COUNT_FAILED:{error}"))
}

fn short_name(value: &str) -> String {
    value
        .replace("::", ".")
        .replace(':', ".")
        .rsplit('.')
        .next()
        .unwrap_or(value)
        .to_owned()
}

fn stable_project_id(project: &Path) -> String {
    let normalized = project.to_string_lossy().into_owned();
    #[cfg(windows)]
    let normalized = normalized
        .strip_prefix(r"\\?\")
        .unwrap_or(&normalized)
        .to_lowercase();
    sha256_hex(normalized.as_bytes())
}

fn canonical_json(value: &Value) -> String {
    match value {
        Value::Null => "null".to_owned(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        Value::String(value) => serde_json::to_string(value).unwrap_or_else(|_| "\"\"".to_owned()),
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(canonical_json)
                .collect::<Vec<_>>()
                .join(",")
        ),
        Value::Object(values) => {
            let mut keys = values.keys().collect::<Vec<_>>();
            keys.sort();
            format!(
                "{{{}}}",
                keys.iter()
                    .map(|key| format!(
                        "{}:{}",
                        serde_json::to_string(key).unwrap_or_else(|_| "\"\"".to_owned()),
                        canonical_json(&values[*key])
                    ))
                    .collect::<Vec<_>>()
                    .join(",")
            )
        }
    }
}

fn unix_time() -> Result<f64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| "STRUCTURAL_SYSTEM_CLOCK_INVALID".to_owned())
}

fn command_position(arguments: &[String], action: &str) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|window| window[0] == "inspect" && window[1] == action)
        .ok_or_else(|| format!("STRUCTURAL_{}_ACTION_MISSING", action.to_ascii_uppercase()))
}

fn positional_after<'a>(
    arguments: &'a [String],
    action: &str,
    offset: usize,
) -> Result<&'a str, String> {
    let index = command_position(arguments, action)? + 2 + offset;
    arguments
        .get(index)
        .filter(|value| !value.starts_with("--"))
        .map(String::as_str)
        .ok_or_else(|| {
            format!(
                "STRUCTURAL_{}_ARGUMENT_MISSING:{offset}",
                action.to_ascii_uppercase()
            )
        })
}

fn positionals_after(arguments: &[String], action: &str) -> Result<Vec<String>, String> {
    let mut output = Vec::new();
    let mut index = command_position(arguments, action)? + 2;
    while let Some(value) = arguments.get(index) {
        if value.starts_with("--") {
            break;
        }
        output.push(value.clone());
        index += 1;
    }
    if output.is_empty() {
        return Err(format!(
            "STRUCTURAL_{}_ARGUMENT_MISSING:0",
            action.to_ascii_uppercase()
        ));
    }
    Ok(output)
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

fn integer_option(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?.map_or(Ok(default), |value| {
        value
            .parse::<i64>()
            .map_err(|_| format!("{flag}_VALUE_INVALID"))
    })
}

fn string_field(value: &Value, name: &str) -> Result<String, String> {
    value[name]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("STRUCTURAL_FIELD_INVALID:{name}"))
}

fn number_field(value: &Value, name: &str) -> Result<f64, String> {
    value[name]
        .as_f64()
        .ok_or_else(|| format!("STRUCTURAL_FIELD_INVALID:{name}"))
}

fn string_json(value: &Value, name: &str) -> String {
    value[name].as_str().unwrap_or("").to_owned()
}

fn number_json(value: &Value, name: &str) -> f64 {
    value[name].as_f64().unwrap_or(0.0)
}

fn integer_json(value: &Value, name: &str) -> i64 {
    value[name].as_i64().unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::{canonical_json, short_name, supports};
    use serde_json::json;

    #[test]
    fn routes_all_structural_inspection_commands() {
        for action in ["symbol", "impact", "paths", "map", "stats"] {
            assert!(supports(&["inspect".to_owned(), action.to_owned()]));
        }
    }

    #[test]
    fn canonical_json_orders_keys() {
        assert_eq!(
            canonical_json(&json!({"z": 1, "a": [true, null]})),
            "{\"a\":[true,null],\"z\":1}"
        );
    }

    #[test]
    fn short_names_normalize_rust_and_python_qualification() {
        assert_eq!(short_name("crate::module::name"), "name");
        assert_eq!(short_name("Class.method"), "method");
    }
}
