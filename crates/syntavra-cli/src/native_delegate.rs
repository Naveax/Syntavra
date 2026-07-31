#![forbid(unsafe_code)]

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const DEFAULT_MAX_TASKS: i64 = 8;
const MAX_OUTPUT_TOKENS: i64 = 1_200;

const CAPABILITY_PATTERNS: &[(&[&str], &str)] = &[
    (&["test", "coverage", "verify", "benchmark"], "verification"),
    (
        &["security", "secret", "permission", "auth", "sandbox"],
        "security",
    ),
    (
        &["ui", "dashboard", "extension", "frontend", "statusline"],
        "interface",
    ),
    (
        &["database", "schema", "migration", "index", "memory"],
        "data",
    ),
    (
        &["provider", "model", "routing", "quota", "rate limit"],
        "provider",
    ),
    (
        &["ast", "symbol", "call graph", "class hierarchy", "refactor"],
        "code-intelligence",
    ),
];

fn trim_task_markers(value: &str) -> String {
    value
        .trim_matches(|character| matches!(character, ' ' | '-' | '*' | '\t'))
        .to_owned()
}

fn flush_sentence(current: &mut String, output: &mut Vec<String>) {
    if current.trim().chars().count() >= 8 {
        output.push(trim_task_markers(current));
    }
    current.clear();
}

fn sentences(text: &str) -> Vec<String> {
    let characters = text.chars().collect::<Vec<_>>();
    let mut output = Vec::new();
    let mut current = String::new();
    let mut index = 0usize;
    while index < characters.len() {
        let character = characters[index];
        if character == '\n' {
            flush_sentence(&mut current, &mut output);
            while index < characters.len() && characters[index] == '\n' {
                index += 1;
            }
            continue;
        }
        if character.is_whitespace()
            && index > 0
            && matches!(characters[index - 1], '.' | '!' | '?')
        {
            flush_sentence(&mut current, &mut output);
            while index < characters.len() && characters[index].is_whitespace() {
                index += 1;
            }
            continue;
        }
        current.push(character);
        index += 1;
    }
    flush_sentence(&mut current, &mut output);
    output
}

fn is_word_character(character: char) -> bool {
    character.is_alphanumeric() || character == '_'
}

fn contains_bounded_phrase(corpus: &str, phrase: &str) -> bool {
    corpus.match_indices(phrase).any(|(start, value)| {
        let end = start + value.len();
        let left_is_word = corpus[..start]
            .chars()
            .next_back()
            .is_some_and(is_word_character);
        let right_is_word = corpus[end..].chars().next().is_some_and(is_word_character);
        !left_is_word && !right_is_word
    })
}

fn capability(sentence: &str) -> &'static str {
    let corpus = sentence.to_lowercase();
    for (patterns, name) in CAPABILITY_PATTERNS {
        if patterns
            .iter()
            .any(|pattern| contains_bounded_phrase(&corpus, pattern))
        {
            return name;
        }
    }
    "implementation"
}

fn capability_title(value: &str) -> &'static str {
    match value {
        "code-intelligence" => "Code Intelligence",
        "data" => "Data",
        "interface" => "Interface",
        "provider" => "Provider",
        "security" => "Security",
        "verification" => "Verification",
        _ => "Implementation",
    }
}

fn add_receipt(body: Value) -> Result<Value, String> {
    let canonical =
        serde_json::to_vec(&body).map_err(|_| "DELEGATION_PLAN_RENDER_FAILED".to_owned())?;
    let mut output = body
        .as_object()
        .cloned()
        .ok_or_else(|| "DELEGATION_PLAN_RENDER_FAILED".to_owned())?;
    output.insert(
        "receipt_hash".to_owned(),
        Value::String(sha256_hex(&canonical)),
    );
    Ok(Value::Object(output))
}

fn plan(objective: &str, context_paths: &[String], max_tasks: i64) -> Result<Value, String> {
    let sentence_rows = sentences(objective);
    let mut groups = BTreeMap::<String, Vec<String>>::new();
    for sentence in &sentence_rows {
        groups
            .entry(capability(sentence).to_owned())
            .or_default()
            .push(sentence.clone());
    }
    if groups.len() <= 1 && sentence_rows.len() <= 3 {
        return add_receipt(json!({
            "delegated": false,
            "reason": "task is small enough for one agent",
            "tasks": [],
        }));
    }

    let mut tasks = Vec::<Value>::new();
    let mut previous = Vec::<String>::new();
    for (index, (capability_name, items)) in groups.iter().enumerate() {
        if i64::try_from(tasks.len()).unwrap_or(i64::MAX) >= max_tasks {
            break;
        }
        let task_id = format!("T{:02}", index + 1);
        let dependency_start = previous.len().saturating_sub(2);
        let dependencies = previous[dependency_start..].to_vec();
        let handoff = format!(
            "{task_id} {capability_name}: return decisions, changed paths, verification commands, blockers; omit narration."
        );
        tasks.push(json!({
            "id": task_id,
            "title": capability_title(capability_name),
            "objective": items.join(" "),
            "capability": capability_name,
            "context_paths": context_paths,
            "dependencies": dependencies,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "handoff": handoff,
        }));
        previous.push(format!("T{:02}", index + 1));
    }
    add_receipt(json!({
        "delegated": true,
        "reason": "independent capability groups detected",
        "tasks": tasks,
    }))
}

fn repeated_string_flag(arguments: &[String], flag: &str) -> Result<Vec<String>, String> {
    let mut output = Vec::new();
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        if item == flag {
            index += 1;
            output.push(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_MISSING"))?
                    .clone(),
            );
        } else if let Some(value) = item
            .strip_prefix(flag)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            output.push(value.to_owned());
        }
        index += 1;
    }
    Ok(output)
}

fn integer_flag(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    let mut result = default;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let raw = if item == flag {
            index += 1;
            Some(
                arguments
                    .get(index)
                    .ok_or_else(|| format!("{flag}_MISSING"))?
                    .as_str(),
            )
        } else {
            item.strip_prefix(flag)
                .and_then(|suffix| suffix.strip_prefix('='))
        };
        if let Some(value) = raw {
            result = value
                .parse::<i64>()
                .map_err(|_| format!("{flag}_INVALID"))?;
        }
        index += 1;
    }
    Ok(result)
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    let action = arguments
        .iter()
        .position(|value| value == "delegate")
        .ok_or_else(|| "DELEGATE_ACTION_MISSING".to_owned())?;
    let objective = arguments
        .get(action + 1)
        .ok_or_else(|| "DELEGATE_OBJECTIVE_MISSING".to_owned())?;
    plan(
        objective,
        &repeated_string_flag(arguments, "--context-path")?,
        integer_flag(arguments, "--max-tasks", DEFAULT_MAX_TASKS)?,
    )
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{capability, plan, sentences};

    #[test]
    fn sentence_split_and_capability_order_match_reference() {
        let rows = sentences(
            "* Implement the API layer. Verify coverage and benchmark results.\nAudit security permissions.",
        );
        assert_eq!(
            rows,
            [
                "Implement the API layer.",
                "Verify coverage and benchmark results.",
                "Audit security permissions.",
            ]
        );
        assert_eq!(capability(&rows[0]), "implementation");
        assert_eq!(capability(&rows[1]), "verification");
        assert_eq!(capability(&rows[2]), "security");
    }

    #[test]
    fn small_task_stays_with_one_agent() {
        let value = plan("Rename the variable carefully.", &[], 8).expect("plan");
        assert!(!value["delegated"].as_bool().expect("delegated"));
        assert_eq!(value["reason"], "task is small enough for one agent");
        assert_eq!(value["tasks"], json!([]));
        assert_eq!(value["receipt_hash"].as_str().map(str::len), Some(64));
    }

    #[test]
    fn delegated_groups_are_sorted_and_dependency_bounded() {
        let value = plan(
            "Implement the API layer. Verify coverage and benchmark results. Audit security permissions. Update database migration and memory index.",
            &["src/lib.rs".to_owned(), "tests/runtime".to_owned()],
            4,
        )
        .expect("plan");
        assert!(value["delegated"].as_bool().expect("delegated"));
        assert_eq!(
            value["tasks"]
                .as_array()
                .expect("tasks")
                .iter()
                .map(|task| task["capability"].as_str().expect("capability"))
                .collect::<Vec<_>>(),
            ["data", "implementation", "security", "verification"]
        );
        assert_eq!(value["tasks"][0]["dependencies"], json!([]));
        assert_eq!(value["tasks"][1]["dependencies"], json!(["T01"]));
        assert_eq!(value["tasks"][2]["dependencies"], json!(["T01", "T02"]));
        assert_eq!(value["tasks"][3]["dependencies"], json!(["T02", "T03"]));
        assert_eq!(
            value["tasks"][0]["context_paths"],
            json!(["src/lib.rs", "tests/runtime"])
        );
    }
}
