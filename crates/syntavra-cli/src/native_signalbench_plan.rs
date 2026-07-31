#![forbid(unsafe_code)]

use serde_json::{json, Value};
use syntavra_core::sha256_hex;

const LANGUAGES: [&str; 10] = [
    "python",
    "typescript",
    "rust",
    "go",
    "java",
    "cpp",
    "csharp",
    "ruby",
    "php",
    "luau",
];
const CATEGORIES: [(&str, u32); 9] = [
    ("bug-fix", 30),
    ("feature", 25),
    ("refactor", 20),
    ("test-repair", 15),
    ("performance", 10),
    ("security", 10),
    ("api-migration", 10),
    ("cross-file-reasoning", 15),
    ("repository-exploration", 15),
];
const ARMS: [&str; 6] = [
    "plain-baseline",
    "syntavra",
    "token-savior",
    "context-mode",
    "headroom",
    "volt-lcm",
];
const TASK_COUNT: u64 = 150;
const ARM_COUNT: u64 = 6;
const DEFAULT_REPETITIONS: u64 = 30;
const SEED: u64 = 1337;

fn repetitions(arguments: &[String]) -> Result<u64, String> {
    let mut values = arguments.iter();
    while let Some(argument) = values.next() {
        if argument == "--repetitions" {
            let value = values
                .next()
                .ok_or_else(|| "SIGNALBENCH_REPETITIONS_MISSING".to_owned())?;
            return parse_repetitions(value);
        }
        if let Some(value) = argument.strip_prefix("--repetitions=") {
            return parse_repetitions(value);
        }
    }
    Ok(DEFAULT_REPETITIONS)
}

fn parse_repetitions(value: &str) -> Result<u64, String> {
    let parsed = value
        .parse::<u64>()
        .map_err(|error| format!("SIGNALBENCH_REPETITIONS_INVALID:{error}"))?;
    if parsed < 30 {
        return Err("SIGNALBENCH_REPETITIONS_BELOW_MINIMUM".to_owned());
    }
    Ok(parsed)
}

fn tasks() -> Value {
    let mut rows = Vec::with_capacity(150);
    let mut index = 0_u32;
    let mut language_index = 0_usize;
    for (category, count) in CATEGORIES {
        for offset in 0..count {
            let language = LANGUAGES[language_index % LANGUAGES.len()];
            language_index += 1;
            index += 1;
            rows.push(json!({
                "task_id": format!("coding-{index:03}-{category}-{language}"),
                "category": category,
                "language": language,
                "repository": format!("<materialize:{category}:{language}:{}>", offset + 1),
                "repository_tree": "REQUIRED_AT_MATERIALIZATION",
                "prompt": format!(
                    "Execute the verified {category} task for {language} corpus slot {}.",
                    offset + 1
                ),
                "verifier": ["<external-verifier-required>"],
                "source_kind": "corpus-slot",
                "live_materialized": false,
                "metadata": {
                    "slot_index": index,
                    "claim_eligible": false,
                },
            }));
        }
    }
    Value::Array(rows)
}

fn arms() -> Value {
    Value::Array(
        ARMS
            .into_iter()
            .map(|arm| {
                json!({
                    "arm_id": arm,
                    "command": [format!("<external:{arm}>")],
                    "model": "IDENTICAL_MODEL",
                    "provider": "IDENTICAL_PROVIDER",
                    "reasoning": "IDENTICAL_REASONING",
                    "context_window": 200_000,
                    "permissions": ["read", "write", "execute"],
                })
            })
            .collect(),
    )
}

fn manifest(repetitions: u64) -> Result<Value, String> {
    let run_count = TASK_COUNT
        .checked_mul(ARM_COUNT)
        .and_then(|value| value.checked_mul(repetitions))
        .ok_or_else(|| "SIGNALBENCH_RUN_COUNT_OVERFLOW".to_owned())?;
    let mut value = json!({
        "schema_version": 2,
        "tasks": tasks(),
        "arms": arms(),
        "repetitions": repetitions,
        "seed": SEED,
        "run_count": run_count,
    });
    let rendered = serde_json::to_vec(&value)
        .map_err(|error| format!("SIGNALBENCH_MANIFEST_RENDER_FAILED:{error}"))?;
    let hash = sha256_hex(&rendered);
    let object = value
        .as_object_mut()
        .ok_or_else(|| "SIGNALBENCH_MANIFEST_OBJECT_REQUIRED".to_owned())?;
    object.insert("manifest_hash".to_owned(), Value::String(hash));
    Ok(value)
}

pub fn execute(arguments: &[String]) -> Result<Value, String> {
    let repetitions = repetitions(arguments)?;
    let run_count = TASK_COUNT
        .checked_mul(ARM_COUNT)
        .and_then(|value| value.checked_mul(repetitions))
        .ok_or_else(|| "SIGNALBENCH_RUN_COUNT_OVERFLOW".to_owned())?;
    Ok(json!({
        "corpus": {
            "tasks": TASK_COUNT,
            "live": false,
        },
        "schedule": {
            "runs": run_count,
            "repetitions": repetitions,
        },
        "manifest": manifest(repetitions)?,
    }))
}

#[cfg(test)]
mod tests {
    use super::execute;

    #[test]
    fn default_plan_has_expected_shape() {
        let arguments = vec!["signalbench".to_owned(), "plan".to_owned()];
        let value = execute(&arguments).expect("signalbench plan");
        assert_eq!(value["corpus"]["tasks"], 150);
        assert_eq!(value["schedule"]["runs"], 27_000);
        assert_eq!(value["manifest"]["tasks"].as_array().map(Vec::len), Some(150));
        assert!(value["manifest"]["manifest_hash"].as_str().is_some());
    }
}
