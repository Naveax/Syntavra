fn cc_full_error_re() -> Result<regex::Regex, String> {
    regex::Regex::new(r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout|segmentation fault|not found|permission denied)\b")
        .map_err(|error| format!("COMPACTOR_ERROR_REGEX_FAILED:{error}"))
}

fn cc_full_location_re() -> Result<regex::Regex, String> {
    regex::Regex::new(r#"(?:[^\s:]+\.(?:py|rs|js|jsx|ts|tsx|java|cs|go|rb|php|lua|luau|cpp|c|h):\d+|File "[^"]+", line \d+|\bat [\w.$<>]+\([^)]*:\d+\))"#)
        .map_err(|error| format!("COMPACTOR_LOCATION_REGEX_FAILED:{error}"))
}

fn cc_full_test_summary_re() -> Result<regex::Regex, String> {
    regex::Regex::new(r"(?i)(?:\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b|tests?:\s*\d+|test suites?:|failures?:\s*\d+|successes?:\s*\d+|test result:|packages?\s+\d+|ok\s+\S+\s+[\d.]+s)")
        .map_err(|error| format!("COMPACTOR_TEST_SUMMARY_REGEX_FAILED:{error}"))
}

fn cc_full_dedup<I>(lines: I) -> Vec<String>
where
    I: IntoIterator<Item = String>,
{
    let mut seen = std::collections::HashSet::<String>::new();
    let mut output = Vec::<String>::new();
    for line in lines {
        if line.trim().is_empty() {
            continue;
        }
        if seen.insert(line.clone()) {
            output.push(line);
        }
    }
    output
}

fn cc_full_errors(lines: &[String]) -> Result<Vec<String>, String> {
    let error_re = cc_full_error_re()?;
    let location_re = cc_full_location_re()?;
    Ok(cc_full_dedup(
        lines
            .iter()
            .filter(|line| error_re.is_match(line) || location_re.is_match(line))
            .cloned(),
    ))
}

fn cc_full_head_tail(lines: &[String], head: usize, tail: usize) -> Vec<String> {
    if lines.len() <= head + tail {
        return lines.to_vec();
    }
    let mut output = Vec::with_capacity(head + tail + 1);
    output.extend(lines.iter().take(head).cloned());
    output.push(format!("[… {} lines omitted …]", lines.len() - head - tail));
    output.extend(lines.iter().skip(lines.len() - tail).cloned());
    output
}

fn cc_full_select_test(lines: &[String]) -> Result<Vec<String>, String> {
    let retained_errors = cc_full_errors(lines)?;
    let summary_re = cc_full_test_summary_re()?;
    let error_re = cc_full_error_re()?;
    let summaries = cc_full_dedup(lines.iter().filter(|line| summary_re.is_match(line)).cloned());
    let failures = cc_full_dedup(
        lines
            .iter()
            .filter(|line| {
                let stripped = line.trim_start();
                stripped.starts_with("E ")
                    || stripped.starts_with("F ")
                    || stripped.starts_with("FAILED")
                    || stripped.starts_with('>')
                    || stripped.starts_with("--- FAIL")
                    || stripped.starts_with("failures:")
                    || error_re.is_match(line)
            })
            .cloned(),
    );
    let mut combined = Vec::<String>::new();
    combined.extend(summaries.iter().skip(summaries.len().saturating_sub(16)).cloned());
    combined.extend(failures.iter().take(64).cloned());
    combined.extend(retained_errors.iter().take(32).cloned());
    Ok(cc_full_dedup(combined))
}

fn cc_full_select_git_status(lines: &[String]) -> Result<Vec<String>, String> {
    let retained_errors = cc_full_errors(lines)?;
    let selected = cc_full_dedup(
        lines
            .iter()
            .filter(|line| {
                line.starts_with("On branch ")
                    || line.starts_with("Your branch ")
                    || line.starts_with("Changes ")
                    || line.starts_with("Untracked ")
                    || line.starts_with("nothing to commit")
                    || matches!(line.get(..2).unwrap_or(""), " M" | "M " | "A " | " D" | "D " | "??" | "R " | "UU")
            })
            .cloned(),
    );
    let mut output = selected.into_iter().take(160).collect::<Vec<_>>();
    output.extend(retained_errors.iter().take(24).cloned());
    Ok(output)
}

fn cc_full_select_git_diff(lines: &[String]) -> Result<Vec<String>, String> {
    let retained_errors = cc_full_errors(lines)?;
    let headers = lines
        .iter()
        .filter(|line| line.starts_with("diff --git") || line.starts_with("index ") || line.starts_with("--- ") || line.starts_with("+++ ") || line.starts_with("@@ "))
        .cloned()
        .collect::<Vec<_>>();
    let changes = lines
        .iter()
        .filter(|line| (line.starts_with('+') || line.starts_with('-')) && !line.starts_with("+++") && !line.starts_with("---"))
        .cloned()
        .collect::<Vec<_>>();
    let mut combined = Vec::<String>::new();
    combined.extend(headers.into_iter().take(120));
    combined.extend(changes.iter().take(48).cloned());
    combined.extend(changes.iter().skip(changes.len().saturating_sub(24)).cloned());
    combined.extend(retained_errors.iter().take(24).cloned());
    Ok(cc_full_dedup(combined))
}

fn cc_full_select_git_log(lines: &[String]) -> Result<Vec<String>, String> {
    let retained_errors = cc_full_errors(lines)?;
    let commits = lines
        .iter()
        .filter(|line| line.starts_with("commit ") || line.starts_with("Author:") || line.starts_with("Date:") || line.starts_with("    "))
        .cloned()
        .collect::<Vec<_>>();
    let mut output = cc_full_head_tail(&commits, 40, 20);
    output.extend(retained_errors.iter().take(16).cloned());
    Ok(output)
}

fn cc_full_select_search(lines: &[String]) -> Result<Vec<String>, String> {
    let retained_errors = cc_full_errors(lines)?;
    let mut grouped = std::collections::BTreeMap::<String, Vec<String>>::new();
    let mut insertion = Vec::<String>::new();
    for line in lines {
        if line.trim().is_empty() {
            continue;
        }
        let key = if let Some((before, _)) = line.split_once(':') {
            before.to_owned()
        } else {
            let parent = Path::new(line).parent().unwrap_or_else(|| Path::new("."));
            let rendered = parent.to_string_lossy().to_string();
            if rendered.is_empty() { ".".to_owned() } else { rendered }
        };
        if !grouped.contains_key(&key) {
            insertion.push(key.clone());
        }
        grouped.entry(key).or_default().push(line.clone());
    }
    let total = grouped.values().map(Vec::len).sum::<usize>();
    let mut selected = vec![format!("results={total} groups={}", grouped.len())];
    for key in insertion.into_iter().take(50) {
        if let Some(values) = grouped.get(&key) {
            selected.push(format!("[{key}] {}", values.len()));
            selected.extend(values.iter().take(5).cloned());
        }
    }
    selected.extend(retained_errors.iter().take(24).cloned());
    Ok(selected)
}
