#![forbid(unsafe_code)]

use std::collections::{BTreeSet, HashSet};
use std::fs;
use std::io::{self, Read};

use serde_json::{json, Map, Value};

#[derive(Debug, Clone, Copy)]
struct Profile {
    name: &'static str,
    max_bytes: usize,
    max_sections: usize,
    max_items_per_section: usize,
    include_evidence: bool,
    include_details: bool,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "output" && matches!(action.as_str(), "compact" | "govern"))
}

fn profile(name: &str) -> Result<Profile, String> {
    match name {
        "terse" => Ok(Profile {
            name: "terse",
            max_bytes: 1_400,
            max_sections: 4,
            max_items_per_section: 4,
            include_evidence: true,
            include_details: false,
        }),
        "compact" => Ok(Profile {
            name: "compact",
            max_bytes: 4_096,
            max_sections: 5,
            max_items_per_section: 6,
            include_evidence: true,
            include_details: false,
        }),
        "balanced" => Ok(Profile {
            name: "balanced",
            max_bytes: 12_000,
            max_sections: 8,
            max_items_per_section: 12,
            include_evidence: true,
            include_details: true,
        }),
        "detailed" => Ok(Profile {
            name: "detailed",
            max_bytes: 32_000,
            max_sections: 16,
            max_items_per_section: 30,
            include_evidence: true,
            include_details: true,
        }),
        "audit" => Ok(Profile {
            name: "audit",
            max_bytes: 96_000,
            max_sections: 64,
            max_items_per_section: 100,
            include_evidence: true,
            include_details: true,
        }),
        _ => Err(format!("OUTPUT_PROFILE_UNKNOWN:{name}")),
    }
}

fn contract_fields(name: &str) -> &'static [&'static str] {
    match name {
        "implementation" => &[
            "result",
            "changed_files",
            "behavior",
            "verification",
            "limitations",
            "evidence",
        ],
        "failure" => &[
            "root_cause",
            "location",
            "affected_boundary",
            "next_action",
            "evidence",
        ],
        "audit" => &[
            "claim",
            "status",
            "supporting_evidence",
            "contradicting_evidence",
            "confidence",
            "uncertainty",
        ],
        "benchmark" => &[
            "status",
            "workload",
            "quality",
            "efficiency",
            "statistics",
            "limitations",
            "evidence",
        ],
        _ => &["result", "details", "limitations", "evidence"],
    }
}

fn option_value(arguments: &[String], flag: &str) -> Result<Option<String>, String> {
    let mut value = None;
    let equals = format!("{flag}=");
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == flag {
            index += 1;
            value = Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_VALUE_MISSING"))?
                    .clone(),
            );
        } else if let Some(current) = arguments[index].strip_prefix(&equals) {
            value = Some(current.to_owned());
        }
        index += 1;
    }
    Ok(value)
}

fn python_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(value) => {
            if *value {
                "True".to_owned()
            } else {
                "False".to_owned()
            }
        }
        Value::Number(value) => value.to_string(),
        Value::String(value) => format!("'{}'", value.replace('\\', "\\\\").replace('\'', "\\'")),
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(python_repr)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(values) => format!(
            "{{{}}}",
            values
                .iter()
                .map(|(key, value)| {
                    format!(
                        "'{}': {}",
                        key.replace('\\', "\\\\").replace('\'', "\\'"),
                        python_repr(value)
                    )
                })
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn python_string(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        Value::Array(_) | Value::Object(_) => python_repr(value),
        _ => python_repr(value),
    }
}

fn values(value: Option<&Value>) -> Vec<String> {
    match value {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::String(value)) => vec![value.clone()],
        Some(Value::Object(value)) => value
            .iter()
            .map(|(key, item)| format!("{key}: {}", python_string(item)))
            .collect(),
        Some(Value::Array(value)) => value.iter().map(python_string).collect(),
        Some(value) => vec![python_string(value)],
    }
}

fn collapse_whitespace(value: &str) -> String {
    let mut result = String::new();
    let mut whitespace = false;
    for character in value.chars() {
        if character.is_whitespace() {
            if !whitespace {
                result.push(' ');
                whitespace = true;
            }
        } else {
            result.extend(character.to_lowercase());
            whitespace = false;
        }
    }
    result
}

fn is_filler(value: &str) -> bool {
    let lower = value.to_lowercase();
    [
        "sure",
        "of course",
        "absolutely",
        "here's",
        "here is",
        "i'll ",
        "i will ",
        "as requested",
        "hope this helps",
        "let me know if",
    ]
    .iter()
    .any(|prefix| lower.starts_with(prefix))
}

fn dedupe<I>(lines: I) -> Vec<String>
where
    I: IntoIterator<Item = String>,
{
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for raw in lines {
        let line = raw.trim_end().to_owned();
        let stripped = line.trim();
        let key = collapse_whitespace(stripped);
        if stripped.is_empty() || seen.contains(&key) || is_filler(stripped) {
            continue;
        }
        seen.insert(key);
        result.push(line);
    }
    result
}

fn is_word_character(value: char) -> bool {
    value.is_alphanumeric() || value == '_'
}

fn contains_word_phrase(text: &str, phrase: &str) -> bool {
    let lower = text.to_lowercase();
    for (index, _) in lower.match_indices(phrase) {
        let before = lower[..index].chars().next_back();
        let after = lower[index + phrase.len()..].chars().next();
        if before.is_none_or(|value| !is_word_character(value))
            && after.is_none_or(|value| !is_word_character(value))
        {
            return true;
        }
    }
    false
}

fn is_critical(line: &str) -> bool {
    [
        "error",
        "failed",
        "failure",
        "warning",
        "security",
        "unsafe",
        "blocked",
        "not proven",
        "limitation",
        "regression",
        "denied",
        "exception",
        "traceback",
    ]
    .iter()
    .any(|word| contains_word_phrase(line, word))
}

fn has_exit_status(line: &str) -> bool {
    let lower = line.to_lowercase();
    for word in ["exit", "exited"] {
        for (index, _) in lower.match_indices(word) {
            let before = lower[..index].chars().next_back();
            if before.is_some_and(is_word_character) {
                continue;
            }
            let tail = &lower[index + word.len()..];
            let mut characters = tail.chars();
            if !characters.next().is_some_and(char::is_whitespace) {
                continue;
            }
            let remainder = tail.trim_start();
            if remainder
                .chars()
                .next()
                .is_some_and(|value| value.is_ascii_digit())
            {
                return true;
            }
        }
    }
    false
}

fn has_status(line: &str) -> bool {
    [
        "pass", "passed", "success", "ok", "fail", "failed", "blocked", "skipped", "timeout",
    ]
    .iter()
    .any(|word| contains_word_phrase(line, word))
        || has_exit_status(line)
}

fn has_command(line: &str) -> bool {
    let value = line.trim_start();
    if value.starts_with('$') || value.starts_with('>') || value.starts_with("PS>") {
        return true;
    }
    [
        "python", "pytest", "git", "npm", "pnpm", "cargo", "go", "dotnet",
    ]
    .iter()
    .any(|word| {
        value.starts_with(word)
            && value[word.len()..]
                .chars()
                .next()
                .is_none_or(|character| !is_word_character(character))
    })
}

fn has_code(line: &str) -> bool {
    let value = line.trim_start();
    if value.starts_with('+') || value.starts_with('-') || value.starts_with("@@") {
        return true;
    }
    for keyword in [
        "def", "class", "fn", "func", "function", "const", "let", "var", "import", "from",
        "return", "raise", "throw", "if", "for", "while", "match", "case", "SELECT", "INSERT",
        "UPDATE", "DELETE",
    ] {
        if value.starts_with(keyword)
            && value[keyword.len()..]
                .chars()
                .next()
                .is_none_or(|character| !is_word_character(character))
        {
            return true;
        }
    }
    let delimiter = value
        .char_indices()
        .find(|(_, character)| matches!(character, '=' | ':'));
    let Some((index, delimiter)) = delimiter else {
        return false;
    };
    let left = value[..index].trim_end();
    let right = value[index + delimiter.len_utf8()..].trim_start();
    !left.is_empty()
        && left.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '_' | '.' | '-')
        })
        && !right.is_empty()
        && !right.contains(',')
}

fn parse_path_prefix(value: &str) -> Option<usize> {
    let bytes = value.as_bytes();
    let mut index = 0usize;
    if bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':' {
        index = 2;
    }
    let path_start = index;
    while index < bytes.len() && bytes[index] != b':' && !bytes[index].is_ascii_whitespace() {
        index += 1;
    }
    if index == path_start || index >= bytes.len() || bytes[index] != b':' {
        return None;
    }
    let path = value[..index].to_ascii_lowercase();
    if ![
        ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".c", ".cc", ".cpp", ".h", ".hpp", ".go",
        ".java", ".cs", ".rb", ".php", ".lua", ".luau",
    ]
    .iter()
    .any(|extension| path.ends_with(extension))
    {
        return None;
    }
    index += 1;
    let line_start = index;
    while index < bytes.len() && bytes[index].is_ascii_digit() {
        index += 1;
    }
    if index == line_start {
        return None;
    }
    if index < bytes.len() && bytes[index] == b':' {
        let column_marker = index;
        index += 1;
        let column_start = index;
        while index < bytes.len() && bytes[index].is_ascii_digit() {
            index += 1;
        }
        if index == column_start {
            index = column_marker;
        }
    }
    Some(index)
}

fn find_paths(text: &str) -> Vec<String> {
    let mut result = Vec::new();
    let mut start = 0usize;
    while start < text.len() {
        if !text.is_char_boundary(start) {
            start += 1;
            continue;
        }
        if let Some(length) = parse_path_prefix(&text[start..]) {
            result.push(text[start..start + length].to_owned());
            start += length.max(1);
            continue;
        }
        start += text[start..].chars().next().map_or(1, char::len_utf8);
    }
    result
}

fn priority(line: &str) -> i32 {
    let mut score = 0;
    if is_critical(line) {
        score += 100;
    }
    if !find_paths(line).is_empty() {
        score += 90;
    }
    if has_status(line) {
        score += 55;
    }
    if has_code(line) {
        score += 45;
    }
    if has_command(line) {
        score += 40;
    }
    score
}

fn split_lines(text: &str) -> Vec<&str> {
    text.lines().collect()
}

fn utf8_prefix(text: &str, maximum: usize) -> &str {
    let mut end = text.len().min(maximum);
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

fn truncate_preserving_critical(text: &str, active: Profile) -> String {
    if text.len() <= active.max_bytes {
        return text.to_owned();
    }
    let lines = split_lines(text);
    let mut ranked = lines
        .iter()
        .enumerate()
        .map(|(index, line)| (index, *line, priority(line)))
        .collect::<Vec<_>>();
    ranked.sort_by(|left, right| right.2.cmp(&left.2).then_with(|| left.0.cmp(&right.0)));
    let suffix = "\n[bounded; exact evidence available]";
    let budget = active.max_bytes.saturating_sub(suffix.len());
    let mut selected = Vec::new();
    let mut used = 0usize;
    for (index, line, _) in ranked {
        let line_bytes = line.len() + 1;
        if line_bytes > budget.saturating_sub(used) {
            continue;
        }
        selected.push((index, line));
        used += line_bytes;
        if used >= budget {
            break;
        }
    }
    if selected.is_empty() {
        return format!("{}{}", utf8_prefix(text, budget).trim_end(), suffix);
    }
    selected.sort_by_key(|(index, _)| *index);
    let body = selected
        .into_iter()
        .map(|(_, line)| line)
        .collect::<Vec<_>>()
        .join("\n");
    let combined = format!("{body}{suffix}");
    utf8_prefix(&combined, active.max_bytes).to_owned()
}

fn path_set(text: &str) -> Vec<Value> {
    find_paths(text)
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .map(Value::String)
        .collect()
}

fn compact_text(text: &str, active: Profile) -> Value {
    let lines = dedupe(split_lines(text).into_iter().map(str::to_owned));
    let mut ranked = lines
        .iter()
        .enumerate()
        .map(|(index, line)| (index, priority(line)))
        .collect::<Vec<_>>();
    ranked.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.cmp(&right.0)));
    let selected = if active.name == "terse" {
        let limit = 8usize.max(active.max_items_per_section * 3);
        let selected_indices = ranked
            .iter()
            .take(limit)
            .map(|(index, _)| *index)
            .collect::<BTreeSet<_>>();
        lines
            .iter()
            .enumerate()
            .filter(|(index, _)| selected_indices.contains(index))
            .map(|(_, line)| line.clone())
            .collect::<Vec<_>>()
    } else {
        let critical = lines
            .iter()
            .filter(|line| {
                is_critical(line)
                    || !find_paths(line).is_empty()
                    || has_code(line)
                    || has_command(line)
            })
            .cloned()
            .collect::<Vec<_>>();
        let critical_set = critical.iter().cloned().collect::<HashSet<_>>();
        critical
            .into_iter()
            .chain(
                lines
                    .iter()
                    .filter(|line| !critical_set.contains(*line))
                    .cloned(),
            )
            .collect::<Vec<_>>()
    };
    let output = truncate_preserving_critical(&selected.join("\n"), active);
    let input_lines = split_lines(text).len();
    let output_lines = split_lines(&output);
    json!({
        "profile": active.name,
        "text": output,
        "bytes": output.len(),
        "removed_lines": input_lines.saturating_sub(output_lines.len()),
        "preserved_paths": path_set(&output),
        "preserved_critical_lines": output_lines.iter().filter(|line| is_critical(line)).count(),
        "preserved_code_lines": output_lines.iter().filter(|line| has_code(line)).count(),
    })
}

fn required_field(field: &str) -> bool {
    matches!(
        field,
        "result" | "status" | "root_cause" | "claim" | "verification"
    )
}

fn render(payload: &Value, active: Profile, contract: &str) -> Result<Value, String> {
    let payload = payload
        .as_object()
        .ok_or_else(|| "OUTPUT_GOVERNOR_PAYLOAD_OBJECT_REQUIRED".to_owned())?;
    let mut sections = Vec::<(&str, Vec<String>)>::new();
    let mut missing = Vec::new();
    for field in contract_fields(contract) {
        let mut current = dedupe(values(payload.get(*field)));
        if current.is_empty() {
            if required_field(field) {
                missing.push(*field);
            }
            continue;
        }
        if !active.include_details
            && matches!(
                *field,
                "details" | "behavior" | "supporting_evidence" | "contradicting_evidence"
            )
        {
            current.truncate(if active.name == "terse" { 1 } else { 2 });
        }
        if *field == "evidence" && !active.include_evidence {
            continue;
        }
        current.truncate(active.max_items_per_section);
        sections.push((field, current));
        if sections.len() >= active.max_sections {
            break;
        }
    }
    if !missing.is_empty() {
        return Err(format!(
            "OUTPUT_CONTRACT_MISSING_REQUIRED_FIELDS:{}",
            missing.join(",")
        ));
    }
    let terse = active.name == "terse";
    let mut output = Vec::new();
    for (index, (field, current)) in sections.iter().enumerate() {
        let title = field
            .split('_')
            .map(|part| {
                let mut characters = part.chars();
                characters.next().map_or_else(String::new, |first| {
                    first.to_uppercase().collect::<String>() + characters.as_str()
                })
            })
            .collect::<Vec<_>>()
            .join(" ");
        if terse {
            if current.len() == 1 {
                output.push(format!("{title}: {}", current[0]));
            } else {
                output.push(format!("{title}: {}", current.join(" | ")));
            }
        } else if current.len() == 1 {
            output.push(format!("{title}: {}", current[0]));
        } else {
            output.push(format!("{title}:"));
            output.extend(current.iter().map(|value| format!("- {value}")));
        }
        if !terse && index + 1 < sections.len() {
            output.push(String::new());
        }
    }
    let text = truncate_preserving_critical(output.join("\n").trim(), active);
    let section_names = sections
        .iter()
        .map(|(field, _)| Value::String((*field).to_owned()))
        .collect::<Vec<_>>();
    let mut result = Map::new();
    result.insert("profile".to_owned(), Value::String(active.name.to_owned()));
    result.insert("contract".to_owned(), Value::String(contract.to_owned()));
    result.insert("text".to_owned(), Value::String(text.clone()));
    result.insert("bytes".to_owned(), Value::from(text.len()));
    result.insert("sections".to_owned(), Value::Array(section_names));
    result.insert("preserved_paths".to_owned(), Value::Array(path_set(&text)));
    Ok(Value::Object(result))
}

fn read_stdin() -> Result<String, String> {
    let mut value = String::new();
    io::stdin()
        .read_to_string(&mut value)
        .map_err(|error| format!("OUTPUT_STDIN_READ_FAILED:{error}"))?;
    Ok(value)
}

pub fn execute(command: &[String], arguments: &[String]) -> Result<Value, String> {
    let action = command
        .get(1)
        .map(String::as_str)
        .ok_or_else(|| "OUTPUT_ACTION_MISSING".to_owned())?;
    match action {
        "compact" => {
            let active = profile(
                option_value(arguments, "--profile")?
                    .as_deref()
                    .unwrap_or("compact"),
            )?;
            let text = if let Some(path) = option_value(arguments, "--input")? {
                fs::read_to_string(path)
                    .map_err(|error| format!("OUTPUT_INPUT_READ_FAILED:{error}"))?
            } else if let Some(text) = option_value(arguments, "--text")? {
                text
            } else {
                read_stdin()?
            };
            Ok(compact_text(&text, active))
        }
        "govern" => {
            let active = profile(
                option_value(arguments, "--profile")?
                    .as_deref()
                    .unwrap_or("balanced"),
            )?;
            let contract =
                option_value(arguments, "--contract")?.unwrap_or_else(|| "generic".to_owned());
            let payload_text = if let Some(path) = option_value(arguments, "--input")? {
                fs::read_to_string(path)
                    .map_err(|error| format!("OUTPUT_INPUT_READ_FAILED:{error}"))?
            } else {
                option_value(arguments, "--payload")?.unwrap_or_else(|| "{}".to_owned())
            };
            let payload: Value = serde_json::from_str(&payload_text)
                .map_err(|_| "OUTPUT_PAYLOAD_JSON_INVALID".to_owned())?;
            render(&payload, active, &contract)
        }
        _ => Err("OUTPUT_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::{compact_text, profile, render, supports};
    use serde_json::json;

    #[test]
    fn routes_output_commands() {
        assert!(supports(&["output".to_owned(), "compact".to_owned()]));
        assert!(supports(&["output".to_owned(), "govern".to_owned()]));
    }

    #[test]
    fn compact_preserves_critical_path_and_deduplicates() {
        let result = compact_text(
            "Sure.\nnormal\nERROR src/lib.rs:12 failed\nnormal\n",
            profile("compact").expect("profile"),
        );
        assert_eq!(result["preserved_critical_lines"], 1);
        assert_eq!(result["preserved_paths"], json!(["src/lib.rs:12"]));
        assert!(!result["text"].as_str().expect("text").contains("Sure"));
    }

    #[test]
    fn govern_generic_payload() {
        let value = render(
            &json!({"result": "done", "details": ["one", "two"]}),
            profile("balanced").expect("profile"),
            "generic",
        )
        .expect("render");
        assert_eq!(value["sections"], json!(["result", "details"]));
    }
}
