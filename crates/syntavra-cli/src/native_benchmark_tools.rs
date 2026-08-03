#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};
use syntavra_core::sha256_hex;

const AXES: [&str; 10] = ["R", "C", "O", "T", "P", "V", "X", "H", "S", "F"];
const CRITICAL: [&str; 5] = ["R", "C", "O", "T", "V"];
const REQUIRED_CONTROLS: [&str; 10] = [
    "balanced_cache",
    "no_artificial_sleep",
    "no_meaningless_duplication",
    "same_model",
    "same_permissions",
    "same_prompt",
    "same_reasoning",
    "same_repository",
    "same_timeout",
    "same_verifier",
];

#[derive(Debug, Clone, Copy)]
struct TierRule {
    score: f64,
    participation_count: usize,
    participation_floor: f64,
    critical_count: usize,
    critical_high: f64,
    critical_floor: f64,
}

pub fn supports(command: &[String]) -> bool {
    matches!(command, [root, action]
        if root == "benchmark" && matches!(action.as_str(), "validate-config" | "generate-repo"))
}

fn tier_rule(tier: &str) -> Result<TierRule, String> {
    match tier {
        "1X" => Ok(TierRule {
            score: 0.0,
            participation_count: 0,
            participation_floor: 0.0,
            critical_count: 0,
            critical_high: 0.0,
            critical_floor: 0.0,
        }),
        "20X" => Ok(TierRule {
            score: 20.0,
            participation_count: 5,
            participation_floor: 5.0,
            critical_count: 3,
            critical_high: 10.0,
            critical_floor: 2.0,
        }),
        "30X" => Ok(TierRule {
            score: 30.0,
            participation_count: 6,
            participation_floor: 7.5,
            critical_count: 3,
            critical_high: 15.0,
            critical_floor: 3.0,
        }),
        "100X" => Ok(TierRule {
            score: 100.0,
            participation_count: 7,
            participation_floor: 20.0,
            critical_count: 4,
            critical_high: 50.0,
            critical_floor: 5.0,
        }),
        _ => Err(format!("BENCHMARK_UNKNOWN_TIER:{tier}")),
    }
}

fn python_string(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => "None".to_owned(),
        Some(Value::Bool(value)) => {
            if *value {
                "True".to_owned()
            } else {
                "False".to_owned()
            }
        }
        Some(Value::Number(value)) => value.to_string(),
        Some(Value::String(value)) => value.clone(),
        Some(Value::Array(value)) => format!(
            "[{}]",
            value
                .iter()
                .map(|item| python_string(Some(item)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Some(Value::Object(value)) => format!(
            "{{{}}}",
            value
                .iter()
                .map(|(key, item)| format!("'{key}': {}", python_string(Some(item))))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn python_float(value: Option<&Value>, axis: &str) -> Result<f64, String> {
    match value {
        None => Ok(0.0),
        Some(Value::Bool(value)) => Ok(f64::from(u8::from(*value))),
        Some(Value::Number(value)) => value
            .as_f64()
            .ok_or_else(|| format!("BENCHMARK_AXIS_INVALID:{axis}")),
        Some(Value::String(value)) => value
            .trim()
            .parse::<f64>()
            .map_err(|_| format!("BENCHMARK_AXIS_INVALID:{axis}")),
        Some(_) => Err(format!("BENCHMARK_AXIS_INVALID:{axis}")),
    }
}

fn python_truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value.as_f64().is_some_and(|number| number != 0.0),
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
    }
}

fn mapping_or_empty(
    value: Option<&Value>,
    code: &str,
) -> Result<Map<String, Value>, String> {
    match value {
        None => Ok(Map::new()),
        Some(value) if !python_truthy(Some(value)) => Ok(Map::new()),
        Some(Value::Object(value)) => Ok(value.clone()),
        Some(_) => Err(code.to_owned()),
    }
}

fn validate_config_value(config: &Value) -> Result<Value, String> {
    let object = config
        .as_object()
        .ok_or_else(|| "BENCHMARK_CONFIG_OBJECT_REQUIRED".to_owned())?;
    let tier = python_string(object.get("tier"));
    let rule = tier_rule(&tier)?;
    let axes = mapping_or_empty(object.get("axes"), "BENCHMARK_AXES_MAPPING_REQUIRED")?;
    let controls = mapping_or_empty(
        object.get("controls"),
        "BENCHMARK_CONTROLS_MAPPING_REQUIRED",
    )?;

    let mut normalized = BTreeMap::<String, f64>::new();
    let mut errors = Vec::<String>::new();
    for axis in AXES {
        let value = python_float(axes.get(axis), axis)?;
        if value <= 0.0 || !value.is_finite() {
            errors.push(format!("invalid-axis:{axis}"));
        }
        normalized.insert(axis.to_owned(), value);
    }
    let safe = normalized
        .iter()
        .map(|(axis, value)| (axis.clone(), (*value).clamp(0.01, 1000.0)))
        .collect::<BTreeMap<_, _>>();
    let geometric = (safe.values().map(|value| value.ln()).sum::<f64>()
        / safe.len() as f64)
        .exp();
    let harmonic = safe.len() as f64 / safe.values().map(|value| 1.0 / value).sum::<f64>();
    let critical_floor = CRITICAL
        .iter()
        .filter_map(|axis| safe.get(*axis))
        .copied()
        .fold(f64::INFINITY, f64::min);
    let score = if tier == "1X" {
        1.0
    } else {
        geometric
            * (harmonic / geometric).powf(0.35)
            * ((critical_floor / geometric.max(0.01)).sqrt() + 0.5).min(1.5)
    };

    let mut checks = Map::new();
    checks.insert("score".to_owned(), Value::Bool(score >= rule.score));
    checks.insert(
        "multi_axis_participation".to_owned(),
        Value::Bool(
            safe.values()
                .filter(|value| **value >= rule.participation_floor)
                .count()
                >= rule.participation_count,
        ),
    );
    checks.insert(
        "critical_high".to_owned(),
        Value::Bool(
            CRITICAL
                .iter()
                .filter(|axis| safe.get(*axis).is_some_and(|value| *value >= rule.critical_high))
                .count()
                >= rule.critical_count,
        ),
    );
    checks.insert(
        "critical_floor".to_owned(),
        Value::Bool(
            CRITICAL
                .iter()
                .all(|axis| safe.get(*axis).is_some_and(|value| *value >= rule.critical_floor)),
        ),
    );
    checks.insert("observed_measurement".to_owned(), Value::Bool(true));
    for name in REQUIRED_CONTROLS {
        let passed = python_truthy(controls.get(name));
        checks.insert(format!("integrity:{name}"), Value::Bool(passed));
        if !passed {
            errors.push(format!("integrity-failed:{name}"));
        }
    }
    let qualified = errors.is_empty() && checks.values().all(|value| value.as_bool() == Some(true));
    let axes_value = normalized
        .into_iter()
        .map(|(axis, value)| {
            serde_json::Number::from_f64(value)
                .map(|number| (axis, Value::Number(number)))
                .ok_or_else(|| "BENCHMARK_AXIS_NONFINITE".to_owned())
        })
        .collect::<Result<Map<String, Value>, String>>()?;
    let score = serde_json::Number::from_f64(score)
        .ok_or_else(|| "BENCHMARK_SCORE_NONFINITE".to_owned())?;
    Ok(json!({
        "ok": qualified,
        "difficulty": {
            "tier": tier,
            "score": score,
            "axes": axes_value,
            "checks": checks,
            "qualified": qualified,
            "integrity_errors": errors,
            "observed": false,
        },
        "claim_eligible": false,
    }))
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

fn integer_option(arguments: &[String], flag: &str, default: i64) -> Result<i64, String> {
    option_value(arguments, flag)?
        .map(|value| {
            value
                .parse::<i64>()
                .map_err(|_| format!("{flag}_VALUE_INVALID"))
        })
        .transpose()
        .map(|value| value.unwrap_or(default))
}

fn canonical_bytes(value: &Value) -> Result<Vec<u8>, String> {
    serde_json::to_vec(value).map_err(|_| "BENCHMARK_JSON_SERIALIZE_FAILED".to_owned())
}

fn now_nanos() -> Result<u128, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .map_err(|_| "BENCHMARK_SYSTEM_CLOCK_INVALID".to_owned())
}

fn temp_file(path: &Path) -> Result<(PathBuf, File), String> {
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)
        .map_err(|error| format!("BENCHMARK_DIRECTORY_CREATE_FAILED:{error}"))?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "BENCHMARK_FILE_NAME_INVALID".to_owned())?;
    for attempt in 0_u32..100 {
        let candidate = parent.join(format!(
            ".{name}.{}.{}.{}",
            std::process::id(),
            now_nanos()?,
            attempt,
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)
        {
            Ok(file) => return Ok((candidate, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("BENCHMARK_TEMP_CREATE_FAILED:{error}")),
        }
    }
    Err("BENCHMARK_TEMP_NAME_EXHAUSTED".to_owned())
}

#[cfg(unix)]
fn set_permissions(path: &Path, mode: u32) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
        .map_err(|error| format!("BENCHMARK_PERMISSION_FAILED:{error}"))
}

#[cfg(not(unix))]
fn set_permissions(_path: &Path, _mode: u32) -> Result<(), String> {
    Ok(())
}

fn atomic_write_json(path: &Path, value: &Value, mode: u32) -> Result<(), String> {
    let mut bytes = canonical_bytes(value)?;
    bytes.push(b'\n');
    let (temporary, mut file) = temp_file(path)?;
    let result = (|| {
        file.write_all(&bytes)
            .map_err(|error| format!("BENCHMARK_WRITE_FAILED:{error}"))?;
        file.flush()
            .map_err(|error| format!("BENCHMARK_FLUSH_FAILED:{error}"))?;
        file.sync_all()
            .map_err(|error| format!("BENCHMARK_SYNC_FAILED:{error}"))?;
        drop(file);
        set_permissions(&temporary, mode)?;
        #[cfg(windows)]
        if path.exists() {
            fs::remove_file(path)
                .map_err(|error| format!("BENCHMARK_REPLACE_REMOVE_FAILED:{error}"))?;
        }
        fs::rename(&temporary, path)
            .map_err(|error| format!("BENCHMARK_REPLACE_FAILED:{error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn validate_config(arguments: &[String]) -> Result<Value, String> {
    let source = option_value(arguments, "--config")?
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "BENCHMARK_CONFIG_REQUIRED".to_owned())?;
    let bytes = fs::read(&source).map_err(|error| format!("BENCHMARK_CONFIG_READ_FAILED:{error}"))?;
    let config: Value = serde_json::from_slice(&bytes)
        .map_err(|_| "BENCHMARK_CONFIG_JSON_INVALID".to_owned())?;
    validate_config_value(&config)
}

fn generate_repository(arguments: &[String]) -> Result<Value, String> {
    let output = option_value(arguments, "--output")?
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| "BENCHMARK_OUTPUT_REQUIRED".to_owned())?;
    let files = integer_option(arguments, "--files", 50)?;
    let depth = integer_option(arguments, "--depth", 5)?;
    let fanout = integer_option(arguments, "--fanout", 3)?;
    let faults = integer_option(arguments, "--faults", 1)?;
    if output.exists() {
        fs::remove_dir_all(&output)
            .map_err(|error| format!("BENCHMARK_OUTPUT_REMOVE_FAILED:{error}"))?;
    }
    fs::create_dir_all(&output)
        .map_err(|error| format!("BENCHMARK_OUTPUT_CREATE_FAILED:{error}"))?;

    let mut symbols = Map::new();
    let mut fault_rows = Vec::new();
    for index in 0..files.max(0) {
        let caller_count = fanout.min(index).max(0);
        let callers = (1..=caller_count)
            .map(|step| format!("func_{}", (index - step).max(0)))
            .collect::<Vec<_>>();
        let mut body = vec![format!("def func_{index}(value):")];
        if callers.is_empty() {
            body.push("    return value".to_owned());
        } else {
            body.push("    total = value".to_owned());
            for caller in &callers {
                body.push(format!(
                    "    total += {caller}(value - 1) if value > 0 else 0"
                ));
            }
            body.push("    return total".to_owned());
        }
        let file_name = format!("module_{index:04}.py");
        fs::write(output.join(&file_name), format!("{}\n", body.join("\n")))
            .map_err(|error| format!("BENCHMARK_MODULE_WRITE_FAILED:{error}"))?;
        symbols.insert(
            format!("func_{index}"),
            json!({"path": file_name, "calls": callers}),
        );
    }
    let actual_faults = faults.min(files).max(0);
    for index in 0..actual_faults {
        let file_name = format!("fault_{index:04}.py");
        fs::write(
            output.join(&file_name),
            format!(
                "def fault_{index}():\n    raise RuntimeError('SC_FAULT_{index}')\n"
            ),
        )
        .map_err(|error| format!("BENCHMARK_FAULT_WRITE_FAILED:{error}"))?;
        fault_rows.push(json!({
            "marker": format!("SC_FAULT_{index}"),
            "path": file_name,
        }));
    }
    let ground_truth = json!({"symbols": symbols, "faults": fault_rows});
    atomic_write_json(&output.join("ground_truth.json"), &ground_truth, 0o644)?;
    let ground_truth_hash = sha256_hex(&canonical_bytes(&ground_truth)?);
    let observed = json!({
        "R": files as f64,
        "C": (depth.saturating_mul(fanout)).max(1) as f64,
        "O": 1.0,
        "T": 1.0,
        "P": 1.0,
        "V": faults.max(1) as f64,
        "X": files.saturating_mul(fanout).max(1) as f64,
        "H": files.div_euclid(5).max(1) as f64,
        "S": 1.0,
        "F": faults.max(1) as f64,
    });
    Ok(json!({
        "files": files.saturating_add(faults.min(files)).saturating_add(1),
        "depth": depth,
        "fanout": fanout,
        "faults": actual_faults,
        "ground_truth_hash": ground_truth_hash,
        "observed_axes": observed,
    }))
}

pub fn execute(command: &[String], arguments: &[String]) -> Result<Value, String> {
    match command {
        [root, action] if root == "benchmark" && action == "validate-config" => {
            validate_config(arguments)
        }
        [root, action] if root == "benchmark" && action == "generate-repo" => {
            generate_repository(arguments)
        }
        _ => Err("BENCHMARK_TOOLS_COMMAND_UNSUPPORTED".to_owned()),
    }
}

#[cfg(test)]
mod tests {
    use super::supports;

    #[test]
    fn benchmark_tools_are_supported() {
        assert!(supports(&[
            "benchmark".to_owned(),
            "validate-config".to_owned(),
        ]));
        assert!(supports(&[
            "benchmark".to_owned(),
            "generate-repo".to_owned(),
        ]));
    }
}
