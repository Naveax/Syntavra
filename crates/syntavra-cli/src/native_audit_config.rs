#![forbid(unsafe_code)]

use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::{Component, Path, PathBuf};

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const CONFIG_CANDIDATES: [&str; 12] = [
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".cursorrules",
    ".clinerules",
    ".github/copilot-instructions.md",
    ".windsurfrules",
    ".continue/rules",
    ".cursor/rules",
    ".roo/rules",
    ".kilocode/rules",
    ".qwen/rules",
];
const MAX_PATH_CANDIDATE_CHARS: usize = 4_096;

#[derive(Debug, Clone)]
struct Finding {
    path: String,
    severity: &'static str,
    kind: &'static str,
    message: String,
    line: Option<usize>,
    estimated_tokens: usize,
}

impl Finding {
    fn value(&self) -> Value {
        json!({
            "path": self.path,
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "line": self.line,
            "estimated_tokens": self.estimated_tokens,
        })
    }
}

fn recursive_files(path: &Path, found: &mut BTreeSet<PathBuf>) -> Result<(), String> {
    let entries =
        fs::read_dir(path).map_err(|error| format!("AUDIT_CONFIG_DISCOVERY_FAILED:{error}"))?;
    for entry in entries {
        let entry = entry.map_err(|error| format!("AUDIT_CONFIG_DISCOVERY_FAILED:{error}"))?;
        let child = entry.path();
        let file_type = entry
            .file_type()
            .map_err(|error| format!("AUDIT_CONFIG_DISCOVERY_FAILED:{error}"))?;
        if file_type.is_dir() {
            recursive_files(&child, found)?;
        } else if child.is_file() {
            found.insert(child);
        }
    }
    Ok(())
}

fn discover(project: &Path) -> Result<Vec<PathBuf>, String> {
    let mut found = BTreeSet::new();
    for candidate in CONFIG_CANDIDATES {
        let path = project.join(candidate);
        if path.is_file() {
            found.insert(path);
        } else if path.is_dir() {
            recursive_files(&path, &mut found)?;
        }
    }
    Ok(found.into_iter().collect())
}

fn posix_relative(project: &Path, path: &Path) -> Result<String, String> {
    let relative = path
        .strip_prefix(project)
        .map_err(|error| format!("AUDIT_CONFIG_RELATIVE_PATH_FAILED:{error}"))?;
    let mut parts = Vec::new();
    for component in relative.components() {
        if let Component::Normal(value) = component {
            parts.push(value.to_string_lossy().into_owned());
        }
    }
    Ok(parts.join("/"))
}

fn strip_instruction_prefix(line: &str) -> &str {
    let trimmed = line.trim_start_matches(char::is_whitespace);
    let mut iterator = trimmed.char_indices().peekable();
    let Some((_, first)) = iterator.peek().copied() else {
        return trimmed;
    };

    let mut end = 0usize;
    if matches!(first, '-' | '*' | '#' | '>') {
        while let Some((index, character)) = iterator.peek().copied() {
            if !matches!(character, '-' | '*' | '#' | '>') {
                break;
            }
            iterator.next();
            end = index + character.len_utf8();
        }
    } else if first.is_ascii_digit() {
        while let Some((index, character)) = iterator.peek().copied() {
            if !character.is_ascii_digit() {
                break;
            }
            iterator.next();
            end = index + character.len_utf8();
        }
        match iterator.peek().copied() {
            Some((index, character @ ('.' | ')'))) => {
                iterator.next();
                end = index + character.len_utf8();
            }
            _ => return trimmed,
        }
    } else {
        return trimmed;
    }

    while let Some((index, character)) = iterator.peek().copied() {
        if !character.is_whitespace() {
            break;
        }
        iterator.next();
        end = index + character.len_utf8();
    }
    &trimmed[end..]
}

fn normalize_line(line: &str) -> String {
    let source = strip_instruction_prefix(line);
    let mut normalized = String::new();
    let mut pending_space = false;
    for character in source.chars() {
        if character.is_whitespace() {
            if !normalized.is_empty() {
                pending_space = true;
            }
            continue;
        }
        if pending_space {
            normalized.push(' ');
            pending_space = false;
        }
        for lowered in character.to_lowercase() {
            normalized.push(lowered);
        }
    }
    normalized.trim().to_owned()
}

fn path_candidates(line: &str) -> Vec<String> {
    let mut output = Vec::new();
    let mut start = None;
    let mut character_count = 0usize;
    let mut overflowed = false;

    for (index, character) in line
        .char_indices()
        .chain(std::iter::once((line.len(), '\0')))
    {
        let allowed = character.is_alphanumeric() || matches!(character, '_' | '.' | '/' | '-');
        if allowed {
            if start.is_none() {
                start = Some(index);
                character_count = 1;
                overflowed = false;
            } else {
                character_count += 1;
                if character_count > MAX_PATH_CANDIDATE_CHARS {
                    overflowed = true;
                }
            }
            continue;
        }
        let Some(begin) = start.take() else {
            continue;
        };
        if overflowed {
            overflowed = false;
            continue;
        }
        let candidate = line[begin..index].trim_end_matches('.');
        if candidate.is_empty()
            || !candidate.contains('/')
            || candidate.starts_with('/')
            || candidate.ends_with('/')
            || candidate.contains("//")
        {
            continue;
        }
        let parts = candidate.split('/').collect::<Vec<_>>();
        if parts.len() < 2 || parts.iter().any(|part| part.is_empty() || *part == "..") {
            continue;
        }
        if !parts.last().is_some_and(|part| {
            part.chars()
                .any(|value| value.is_alphanumeric() || value == '_')
        }) {
            continue;
        }
        output.push(candidate.to_owned());
    }
    output
}

fn project_candidate(project: &Path, raw: &str) -> Option<PathBuf> {
    let mut relative = PathBuf::new();
    for part in raw.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if !relative.pop() {
                    return None;
                }
            }
            value => relative.push(value),
        }
    }
    Some(project.join(relative))
}

fn contains_wildcard(raw: &str) -> bool {
    raw.chars()
        .any(|character| matches!(character, '*' | '{' | '}' | '[' | ']'))
}

fn overloaded_rule(line: &str) -> bool {
    if line.chars().count() <= 240 {
        return false;
    }
    let mut count = 0usize;
    let mut word = String::new();
    for character in line.chars().chain(std::iter::once(' ')) {
        if character.is_alphanumeric() || character == '_' {
            for lowered in character.to_lowercase() {
                word.push(lowered);
            }
            continue;
        }
        if matches!(word.as_str(), "always" | "never" | "must") {
            count += 1;
        }
        word.clear();
        if count >= 2 {
            return true;
        }
    }
    false
}

fn canonical_json(value: &Value, output: &mut String) -> Result<(), String> {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => output.push_str(
            &serde_json::to_string(value)
                .map_err(|error| format!("AUDIT_CONFIG_SERIALIZE_FAILED:{error}"))?,
        ),
        Value::Array(values) => {
            output.push('[');
            for (index, child) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                canonical_json(child, output)?;
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            let mut keys = values.keys().collect::<Vec<_>>();
            keys.sort_unstable();
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(
                    &serde_json::to_string(key)
                        .map_err(|error| format!("AUDIT_CONFIG_SERIALIZE_FAILED:{error}"))?,
                );
                output.push(':');
                canonical_json(&values[*key], output)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

pub fn execute(project_root: &Path) -> Result<Value, String> {
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("AUDIT_CONFIG_PROJECT_INVALID:{error}"))?;
    let paths = discover(&project)?;
    let mut findings = Vec::new();
    let mut normalized_seen: HashMap<String, (String, usize)> = HashMap::new();
    let mut total_bytes = 0usize;
    let mut total_tokens = 0usize;
    let mut files = Vec::new();

    for path in &paths {
        let relative = posix_relative(&project, path)?;
        files.push(relative.clone());
        let raw = fs::read(path).map_err(|error| format!("AUDIT_CONFIG_READ_FAILED:{error}"))?;
        let text = String::from_utf8_lossy(&raw).into_owned();
        let encoded_bytes = text.as_bytes().len();
        total_bytes += encoded_bytes;
        let tokens = std::cmp::max(1, encoded_bytes / 4);
        total_tokens += tokens;
        if tokens > 2_000 {
            findings.push(Finding {
                path: relative.clone(),
                severity: "warning",
                kind: "oversized-config",
                message: format!("configuration is approximately {tokens} tokens"),
                line: None,
                estimated_tokens: tokens,
            });
        }

        for (offset, line) in text.lines().enumerate() {
            let number = offset + 1;
            let normalized = normalize_line(line);
            if normalized.chars().count() >= 24 {
                if let Some((previous_path, previous_line)) = normalized_seen.get(&normalized) {
                    findings.push(Finding {
                        path: relative.clone(),
                        severity: "warning",
                        kind: "duplicate-instruction",
                        message: format!("duplicates {previous_path}:{previous_line}"),
                        line: Some(number),
                        estimated_tokens: std::cmp::max(1, line.chars().count() / 4),
                    });
                } else {
                    normalized_seen.insert(normalized, (relative.clone(), number));
                }
            }
            for candidate in path_candidates(line) {
                let Some(target) = project_candidate(&project, &candidate) else {
                    continue;
                };
                if !target.exists() && !contains_wildcard(&candidate) {
                    findings.push(Finding {
                        path: relative.clone(),
                        severity: "error",
                        kind: "stale-path",
                        message: format!("referenced path does not exist: {candidate}"),
                        line: Some(number),
                        estimated_tokens: 0,
                    });
                }
            }
            if overloaded_rule(line) {
                findings.push(Finding {
                    path: relative.clone(),
                    severity: "warning",
                    kind: "overloaded-rule",
                    message: "one rule combines multiple absolute constraints".to_owned(),
                    line: Some(number),
                    estimated_tokens: std::cmp::max(1, line.chars().count() / 4),
                });
            }
        }

        let folded = text.to_lowercase();
        if folded.contains("ignore previous") || folded.contains("disregard all") {
            findings.push(Finding {
                path: relative,
                severity: "error",
                kind: "instruction-injection",
                message: "configuration contains an instruction-override phrase".to_owned(),
                line: None,
                estimated_tokens: 0,
            });
        }
    }

    let estimated_reclaimable_tokens = findings
        .iter()
        .filter(|finding| {
            matches!(
                finding.kind,
                "duplicate-instruction" | "oversized-config" | "overloaded-rule"
            )
        })
        .map(|finding| finding.estimated_tokens)
        .sum::<usize>();

    let mut counts = Map::new();
    for severity in ["error", "warning", "info"] {
        counts.insert(
            severity.to_owned(),
            Value::from(
                findings
                    .iter()
                    .filter(|finding| finding.severity == severity)
                    .count(),
            ),
        );
    }
    let mut body = json!({
        "files": files,
        "file_count": paths.len(),
        "bytes": total_bytes,
        "estimated_tokens": total_tokens,
        "estimated_reclaimable_tokens": estimated_reclaimable_tokens,
        "findings": findings.iter().map(Finding::value).collect::<Vec<_>>(),
        "counts": counts,
    });
    let mut canonical = String::new();
    canonical_json(&body, &mut canonical)?;
    let audit_hash = sha256_hex(canonical.as_bytes());
    body.as_object_mut()
        .ok_or_else(|| "AUDIT_CONFIG_RESULT_INVALID".to_owned())?
        .insert("audit_hash".to_owned(), Value::String(audit_hash));
    Ok(body)
}

#[cfg(test)]
mod tests {
    use super::{normalize_line, path_candidates};

    #[test]
    fn normalizes_bullets_and_numbered_rules() {
        assert_eq!(
            normalize_line("  - Always   inspect src/lib.rs "),
            "always inspect src/lib.rs"
        );
        assert_eq!(normalize_line("12) NEVER skip tests"), "never skip tests");
    }

    #[test]
    fn extracts_bounded_relative_paths() {
        assert_eq!(
            path_candidates("read src/lib.rs and docs/guide.md."),
            ["src/lib.rs", "docs/guide.md"]
        );
        assert!(path_candidates("/etc/passwd ../outside.txt").is_empty());
    }
}
