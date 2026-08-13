fn cc_strategy_error_re() -> Option<regex::Regex> {
    regex::Regex::new(r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout|segmentation fault|not found|permission denied)\b").ok()
}
fn cc_strategy_location_re() -> Option<regex::Regex> {
    regex::Regex::new(r#"(?:[^\s:]+\.(?:py|rs|js|jsx|ts|tsx|java|cs|go|rb|php|lua|luau|cpp|c|h):\d+|File \"[^\"]+\", line \d+|\bat [\w.$<>]+\([^)]*:\d+\))"#).ok()
}
fn cc_strategy_test_summary_re() -> Option<regex::Regex> {
    regex::Regex::new(r"(?i)(?:\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b|tests?:\s*\d+|test suites?:|failures?:\s*\d+|successes?:\s*\d+|test result:|packages?\s+\d+|ok\s+\S+\s+[\d.]+s)").ok()
}
fn cc_strategy_dedup<I: IntoIterator<Item=String>>(lines:I)->Vec<String>{
    let mut seen=std::collections::HashSet::<String>::new(); let mut out=Vec::new();
    for line in lines { if !line.trim().is_empty() && seen.insert(line.clone()) { out.push(line); } }
    out
}
fn cc_strategy_errors(lines:&[String])->Vec<String>{
    let Some(er)=cc_strategy_error_re() else{return Vec::new()}; let Some(lr)=cc_strategy_location_re() else{return Vec::new()};
    cc_strategy_dedup(lines.iter().filter(|line|er.is_match(line)||lr.is_match(line)).cloned())
}
fn cc_strategy_head_tail(lines:&[String],head:usize,tail:usize)->Vec<String>{
    if lines.len()<=head+tail{return lines.to_vec()}
    let mut out=Vec::with_capacity(head+tail+1); out.extend(lines.iter().take(head).cloned());
    out.push(format!("[… {} lines omitted …]",lines.len()-head-tail)); out.extend(lines.iter().skip(lines.len()-tail).cloned()); out
}
fn cc_strategy_test(lines:&[String])->Vec<String>{
    let errors=cc_strategy_errors(lines); let Some(sr)=cc_strategy_test_summary_re() else{return Vec::new()}; let Some(er)=cc_strategy_error_re() else{return Vec::new()};
    let summaries=cc_strategy_dedup(lines.iter().filter(|line|sr.is_match(line)).cloned());
    let failures=cc_strategy_dedup(lines.iter().filter(|line|{let s=line.trim_start();s.starts_with("E ")||s.starts_with("F ")||s.starts_with("FAILED")||s.starts_with('>')||s.starts_with("--- FAIL")||s.starts_with("failures:")||er.is_match(line)}).cloned());
    let mut v=Vec::new();v.extend(summaries.iter().skip(summaries.len().saturating_sub(16)).cloned());v.extend(failures.iter().take(64).cloned());v.extend(errors.iter().take(32).cloned());cc_strategy_dedup(v)
}
fn cc_strategy_git_status(lines:&[String])->Vec<String>{
    let errors=cc_strategy_errors(lines); let selected=cc_strategy_dedup(lines.iter().filter(|line|line.starts_with("On branch ")||line.starts_with("Your branch ")||line.starts_with("Changes ")||line.starts_with("Untracked ")||line.starts_with("nothing to commit")||matches!(line.get(..2).unwrap_or("")," M"|"M "|"A "|" D"|"D "|"??"|"R "|"UU")).cloned());
    let mut out=selected.into_iter().take(160).collect::<Vec<_>>();out.extend(errors.iter().take(24).cloned());out
}
fn cc_strategy_git_diff(lines:&[String])->Vec<String>{
    let errors=cc_strategy_errors(lines);let headers=lines.iter().filter(|l|l.starts_with("diff --git")||l.starts_with("index ")||l.starts_with("--- ")||l.starts_with("+++ ")||l.starts_with("@@ ")).cloned().collect::<Vec<_>>();
    let changes=lines.iter().filter(|l|(l.starts_with('+')||l.starts_with('-'))&&!l.starts_with("+++")&&!l.starts_with("---")).cloned().collect::<Vec<_>>();
    let mut v=Vec::new();v.extend(headers.into_iter().take(120));v.extend(changes.iter().take(48).cloned());v.extend(changes.iter().skip(changes.len().saturating_sub(24)).cloned());v.extend(errors.iter().take(24).cloned());cc_strategy_dedup(v)
}
fn cc_strategy_git_log(lines:&[String])->Vec<String>{
    let errors=cc_strategy_errors(lines);let commits=lines.iter().filter(|l|l.starts_with("commit ")||l.starts_with("Author:")||l.starts_with("Date:")||l.starts_with("    ")).cloned().collect::<Vec<_>>();let mut out=cc_strategy_head_tail(&commits,40,20);out.extend(errors.iter().take(16).cloned());out
}
