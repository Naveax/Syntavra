fn cc_stateful_now() -> Result<f64, String> {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_secs_f64())
        .map_err(|error| format!("STATEFUL_CLOCK_FAILED:{error}"))
}

fn cc_stateful_write_json(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("STATEFUL_PARENT_FAILED:{error}"))?;
    }
    let data = serde_json::to_vec_pretty(value).map_err(|error| format!("STATEFUL_JSON_FAILED:{error}"))?;
    let temp = path.with_extension(format!("{}.tmp", path.extension().and_then(|v| v.to_str()).unwrap_or("json")));
    fs::write(&temp, data).map_err(|error| format!("STATEFUL_WRITE_FAILED:{error}"))?;
    fs::rename(&temp, path).map_err(|error| format!("STATEFUL_RENAME_FAILED:{error}"))
}

fn cc_stateful_read_json(path: &Path) -> Result<Option<Value>, String> {
    if !path.is_file() { return Ok(None); }
    let raw = fs::read(path).map_err(|error| format!("STATEFUL_READ_FAILED:{error}"))?;
    serde_json::from_slice(&raw).map(Some).map_err(|error| format!("STATEFUL_PARSE_FAILED:{error}"))
}

fn cc_stateful_option_f64(arguments: &[String], name: &str, default: f64) -> Result<f64, String> {
    if let Some(index) = arguments.iter().position(|item| item == name) {
        return arguments.get(index + 1).ok_or_else(|| format!("OPTION_VALUE_MISSING:{name}"))?
            .parse::<f64>().map_err(|_| format!("OPTION_FLOAT_INVALID:{name}"));
    }
    Ok(default)
}

fn cc_stateful_option_i64(arguments: &[String], name: &str, default: i64) -> Result<i64, String> {
    if let Some(index) = arguments.iter().position(|item| item == name) {
        return arguments.get(index + 1).ok_or_else(|| format!("OPTION_VALUE_MISSING:{name}"))?
            .parse::<i64>().map_err(|_| format!("OPTION_INTEGER_INVALID:{name}"));
    }
    Ok(default)
}

fn cc_stateful_action_index(arguments: &[String], action: &str) -> Option<usize> {
    arguments.windows(2).position(|row| row[0] == "run" && row[1] == action).map(|index| index + 2)
}
