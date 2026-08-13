fn cc_casefold(value: &str) -> String {
    value.chars().flat_map(char::to_lowercase).collect::<String>().replace('ſ', "s").replace('K', "k")
}

fn cc_basename(value: &str) -> &str {
    value.rsplit(['/', '\\']).next().unwrap_or(value)
}

fn cc_assignment(value: &str) -> bool {
    let Some((name, _)) = value.split_once('=') else { return false; };
    let mut chars = name.chars();
    let Some(first) = chars.next() else { return false; };
    (first == '_' || first.is_ascii_alphabetic()) && chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

fn cc_executable_index(argv: &[String]) -> Option<usize> {
    let mut index = 0usize;
    while index < argv.len() && cc_assignment(&argv[index]) { index += 1; }
    while index < argv.len() {
        let wrapper = cc_casefold(cc_basename(&argv[index]));
        if !matches!(wrapper.as_str(), "command" | "env" | "sudo" | "time") { break; }
        index += 1;
        if wrapper == "env" {
            while index < argv.len() && cc_assignment(&argv[index]) { index += 1; }
        }
        if index < argv.len() && argv[index].starts_with('-') { return None; }
    }
    (index < argv.len()).then_some(index)
}

fn cc_has_blocked(argv: &[String], blocked: &[&str]) -> bool {
    argv.iter().skip(1).any(|token| blocked.iter().any(|flag| token == flag || token.starts_with(&format!("{flag}="))))
}

fn cc_rewrite_result(original: &[String], rewritten: Vec<String>, rule: Option<&str>, safe: bool, reason: &str) -> Value {
    serde_json::json!({"original":original,"rewritten":rewritten,"changed":rewritten!=original,"rule":rule,"safe":safe,"reason":reason})
}

fn cc_rewrite_argv(argv: &[String]) -> Result<Value, String> {
    if argv.is_empty() { return Ok(cc_rewrite_result(argv, Vec::new(), None, false, "empty command")); }
    let Some(index) = cc_executable_index(argv) else {
        return Ok(cc_rewrite_result(argv, argv.to_vec(), None, false, "wrapper options are not rewritten"));
    };
    let command = &argv[index..];
    let executable = cc_casefold(cc_basename(&command[0]));
    let arguments = cc_casefold(&command[1..].join(" "));
    let rule = if executable == "git" && (arguments == "status" || arguments.starts_with("status ")) {
        Some(("git-status", &["--porcelain=v2", "--branch"][..], &["--short", "--porcelain", "-s"][..], false))
    } else if executable == "git" && (arguments == "remote -v" || arguments.starts_with("remote -v ")) {
        Some(("git-remote", &[][..], &[][..], true))
    } else if executable == "pytest" {
        Some(("pytest", &["-q", "--tb=short"][..], &["-q", "--quiet", "-v", "--verbose", "--tb"][..], false))
    } else if executable == "kubectl" && (arguments == "logs" || arguments.starts_with("logs ")) {
        Some(("kubectl-logs", &["--tail=200"][..], &["--tail", "-f", "--follow"][..], false))
    } else { None };
    let Some((name, append, blocked, replace_remote)) = rule else {
        return Ok(cc_rewrite_result(argv, argv.to_vec(), None, true, "no matching rewrite rule"));
    };
    if cc_has_blocked(command, blocked) {
        return Ok(cc_rewrite_result(argv, argv.to_vec(), Some(name), true, "explicit user format preserved"));
    }
    let mut rewritten_command = command.to_vec();
    if replace_remote {
        if let Some(position) = rewritten_command.iter().position(|token| token == "-v") {
            rewritten_command.splice(position..=position, ["get-url".to_owned(), "--all".to_owned()]);
        }
    }
    rewritten_command.extend(append.iter().map(|value| (*value).to_owned()));
    let mut rewritten = argv[..index].to_vec();
    rewritten.extend(rewritten_command);
    Ok(cc_rewrite_result(argv, rewritten, Some(name), true, "reduce machine-irrelevant output"))
}

fn cc_raw_unsafe(command: &str) -> bool {
    command.contains("||") || command.contains("&&") || command.contains('|') || command.contains(';') || command.contains('`') || command.contains("$(") || command.contains('\n') || command.contains('\r') || command.contains('>') || command.contains('<')
}

fn cc_split(command: &str) -> Vec<String> { command.split_whitespace().map(str::to_owned).collect() }

fn cc_rewrite_text(command: &str) -> Result<Value, String> {
    let argv = cc_split(command);
    if cc_raw_unsafe(command) {
        return Ok(cc_rewrite_result(&argv, argv.clone(), None, false, "shell composition is not rewritten"));
    }
    cc_rewrite_argv(&argv)
}

fn cc_rewrite_action(arguments: &[String]) -> Result<Value, String> {
    let start = arguments.windows(2).position(|items| items[0] == "run" && items[1] == "rewrite").map(|i| i + 2).ok_or_else(|| "REWRITE_ACTION_NOT_FOUND".to_owned())?;
    let argv = arguments[start..].iter().filter(|value| value.as_str() != "--").cloned().collect::<Vec<_>>();
    if argv.is_empty() { return Err("rewrite command is required".to_owned()); }
    cc_rewrite_argv(&argv)
}
