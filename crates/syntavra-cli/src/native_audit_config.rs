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

type SeenInstructions = HashMap<String, (String, usize)>;

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

struct FileAudit {
    relative: String,
    bytes: usize,
    tokens: usize,
    findings: Vec<Finding>,
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
    let parts = relative
        .components()
        .filter_map(|component| match component {
            Component::Normal(value) => Some(value.to_string_lossy().into_owned()),
            _ => None,
        })
        .collect::<Vec<_>>();
    Ok(parts.join("/"))
}

fn normalize_universal_newlines(text: &str) -> String {
    if !text.contains('\r') {
        return text.to_owned();
    }
    let mut normalized = String::with_capacity(text.len());
    let mut characters = text.chars().peekable();
    while let Some(character) = characters.next() {
        if character == '\r' {
            if characters.peek() == Some(&'\n') {
                characters.next();
            }
            normalized.push('\n');
        } else {
            normalized.push(character);
        }
    }
    normalized
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
        while let Some((_, character)) = iterator.peek().copied() {
            if !character.is_ascii_digit() {
                break;
            }
            iterator.next();
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
        normalized.extend(character.to_lowercase());
    }
    normalized.trim().to_owned()
}

fn valid_path_candidate(candidate: &str) -> bool {
    if candidate.is_empty()
        || !candidate.contains('/')
        || candidate.starts_with('/')
        || candidate.ends_with('/')
        || candidate.contains("//")
    {
        return false;
    }
    let parts = candidate.split('/').collect::<Vec<_>>();
    if parts.len() < 2 || parts.iter().any(|part| part.is_empty() || *part == "..") {
        return false;
    }
    parts.last().is_some_and(|part| {
        part.chars()
            .any(|value| value.is_alphanumeric() || value == '_')
    })
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
                overflowed |= character_count > MAX_PATH_CANDIDATE_CHARS;
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
        if valid_path_candidate(candidate) {
            output.push(candidate.to_owned());
        }
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
            word.extend(character.to_lowercase());
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

fn duplicate_finding(
    relative: &str,
    line: &str,
    number: usize,
    normalized: String,
    seen: &mut SeenInstructions,
) -> Option<Finding> {
    if normalized.chars().count() < 24 {
        return None;
    }
    if let Some((previous_path, previous_line)) = seen.get(&normalized) {
        return Some(Finding {
            path: relative.to_owned(),
            severity: "warning",
            kind: "duplicate-instruction",
            message: format!("duplicates {previous_path}:{previous_line}"),
            line: Some(number),
            estimated_tokens: std::cmp::max(1, line.chars().count() / 4),
        });
    }
    seen.insert(normalized, (relative.to_owned(), number));
    None
}

fn line_findings(
    project: &Path,
    relative: &str,
    line: &str,
    number: usize,
    seen: &mut SeenInstructions,
) -> Vec<Finding> {
    let mut findings = Vec::new();
    if let Some(finding) = duplicate_finding(relative, line, number, normalize_line(line), seen) {
        findings.push(finding);
    }
    for candidate in path_candidates(line) {
        let Some(target) = project_candidate(project, &candidate) else {
            continue;
        };
        if !target.exists() && !contains_wildcard(&candidate) {
            findings.push(Finding {
                path: relative.to_owned(),
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
            path: relative.to_owned(),
            severity: "warning",
            kind: "overloaded-rule",
            message: "one rule combines multiple absolute constraints".to_owned(),
            line: Some(number),
            estimated_tokens: std::cmp::max(1, line.chars().count() / 4),
        });
    }
    findings
}

fn audit_file(
    project: &Path,
    path: &Path,
    seen: &mut SeenInstructions,
) -> Result<FileAudit, String> {
    let relative = posix_relative(project, path)?;
    let raw = fs::read(path).map_err(|error| format!("AUDIT_CONFIG_READ_FAILED:{error}"))?;
    let decoded = String::from_utf8_lossy(&raw);
    let text = normalize_universal_newlines(decoded.as_ref());
    let bytes = text.as_bytes().len();
    let tokens = std::cmp::max(1, bytes / 4);
    let mut findings = Vec::new();
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
        findings.extend(line_findings(project, &relative, line, offset + 1, seen));
    }
    let folded = text.to_lowercase();
    if folded.contains("ignore previous") || folded.contains("disregard all") {
        findings.push(Finding {
            path: relative.clone(),
            severity: "error",
            kind: "instruction-injection",
            message: "configuration contains an instruction-override phrase".to_owned(),
            line: None,
            estimated_tokens: 0,
        });
    }
    Ok(FileAudit {
        relative,
        bytes,
        tokens,
        findings,
    })
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

fn severity_counts(findings: &[Finding]) -> Map<String, Value> {
    ["error", "warning", "info"]
        .into_iter()
        .map(|severity| {
            let count = findings
                .iter()
                .filter(|finding| finding.severity == severity)
                .count();
            (severity.to_owned(), Value::from(count))
        })
        .collect()
}

pub fn execute(project_root: &Path) -> Result<Value, String> {
    let project = fs::canonicalize(project_root)
        .map_err(|error| format!("AUDIT_CONFIG_PROJECT_INVALID:{error}"))?;
    let paths = discover(&project)?;
    let mut seen = SeenInstructions::new();
    let mut files = Vec::new();
    let mut findings = Vec::new();
    let mut total_bytes = 0usize;
    let mut total_tokens = 0usize;

    for path in &paths {
        let audit = audit_file(&project, path, &mut seen)?;
        files.push(audit.relative);
        total_bytes += audit.bytes;
        total_tokens += audit.tokens;
        findings.extend(audit.findings);
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
    let mut body = json!({
        "files": files,
        "file_count": paths.len(),
        "bytes": total_bytes,
        "estimated_tokens": total_tokens,
        "estimated_reclaimable_tokens": estimated_reclaimable_tokens,
        "findings": findings.iter().map(Finding::value).collect::<Vec<_>>(),
        "counts": severity_counts(&findings),
    });
    let mut canonical = String::new();
    canonical_json(&body, &mut canonical)?;
    body.as_object_mut()
        .ok_or_else(|| "AUDIT_CONFIG_RESULT_INVALID".to_owned())?
        .insert(
            "audit_hash".to_owned(),
            Value::String(sha256_hex(canonical.as_bytes())),
        );
    Ok(body)
}

#[cfg(test)]
mod tests {
    use super::{normalize_line, normalize_universal_newlines, path_candidates};

    #[test]
    fn normalizes_bullets_and_numbered_rules() {
        assert_eq!(
            normalize_line("  - Always   inspect src/lib.rs "),
            "always inspect src/lib.rs"
        );
        assert_eq!(normalize_line("12) NEVER skip tests"), "never skip tests");
    }

    #[test]
    fn normalizes_cross_platform_newlines() {
        assert_eq!(normalize_universal_newlines("a\r\nb\rc\n"), "a\nb\nc\n");
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
