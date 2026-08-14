#[derive(Clone, Copy)]
struct RewriteRegistration {
    name: &'static str,
    executable: &'static str,
    argument_pattern: &'static str,
    append: &'static [&'static str],
    replace: &'static [(&'static str, &'static str)],
    blocked: &'static [&'static str],
}
macro_rules! rr {
    ($name:expr,$exe:expr,$pattern:expr,$append:expr,$replace:expr,$blocked:expr) => {
        RewriteRegistration { name:$name, executable:$exe, argument_pattern:$pattern, append:$append, replace:$replace, blocked:$blocked }
    };
}
const REWRITE_RULES: &[RewriteRegistration] = &[
rr!("git-status","git","^status(?:\\s|$)",&["--porcelain=v2","--branch"],&[],&["--short","--porcelain","-s"]),
rr!("git-log","git","^log(?:\\s|$)",&["--oneline","--decorate=no","-n","40"],&[],&["--format","--pretty","--oneline","-p","--patch"]),
rr!("git-diff-stat","git","^diff(?:\\s|$)",&["--stat","--compact-summary"],&[],&["--stat","--name-only","--name-status","--numstat","-p","--patch"]),
rr!("git-show-stat","git","^show(?:\\s|$)",&["--stat","--oneline"],&[],&["--stat","--format","--pretty","-p","--patch"]),
rr!("git-branch","git","^branch(?:\\s|$)",&["--format=%(refname:short) %(objectname:short) %(upstream:trackshort)"],&[],&["--format","-v","-vv"]),
rr!("git-stash","git","^stash\\s+list(?:\\s|$)",&["--format=%gd %h %s"],&[],&["--format"]),
rr!("git-worktree","git","^worktree\\s+list(?:\\s|$)",&["--porcelain"],&[],&["--porcelain"]),
rr!("git-fetch","git","^fetch(?:\\s|$)",&["--quiet"],&[],&["--verbose","-v","--quiet"]),
rr!("git-remote","git","^remote\\s+-v(?:\\s|$)",&[],&[("-v","get-url --all")],&[]),
rr!("git-tag","git","^tag(?:\\s|$)",&["--sort=-creatordate"],&[],&["--sort","--format"]),
rr!("rg","rg",".*",&["--no-heading","--line-number","--color=never"],&[],&["--heading","--json","--vimgrep","--color"]),
rr!("grep","grep",".*",&["-n","--color=never"],&[],&["-n","--line-number","--color"]),
rr!("fd","fd",".*",&["--color=never"],&[],&["--color"]),
rr!("find","find",".*",&[],&[],&[]),
rr!("ls","ls",".*",&["-1","--color=never"],&[],&["-l","--long","-1","--color"]),
rr!("tree","tree",".*",&["--noreport","-C"],&[],&["--noreport","-C","--dirsfirst"]),
rr!("pytest","pytest",".*",&["-q","--tb=short"],&[],&["-q","--quiet","-v","--verbose","--tb"]),
rr!("py-test","py.test",".*",&["-q","--tb=short"],&[],&["-q","--quiet","-v","--verbose","--tb"]),
rr!("cargo-test","cargo","^test(?:\\s|$)",&["--quiet"],&[],&["--quiet","-q","--verbose","-v"]),
rr!("cargo-check","cargo","^check(?:\\s|$)",&["--quiet"],&[],&["--quiet","-q","--verbose","-v"]),
rr!("go-test","go","^test(?:\\s|$)",&["-json"],&[],&["-json","-v"]),
rr!("npm-test","npm","^(?:test|run\\s+test)(?:\\s|$)",&["--silent"],&[],&["--silent","--loglevel"]),
rr!("npm-list","npm","^(?:list|ls)(?:\\s|$)",&["--depth=0","--json"],&[],&["--depth","--json","--long"]),
rr!("pnpm-test","pnpm","^(?:test|run\\s+test)(?:\\s|$)",&["--silent"],&[],&["--silent","--reporter"]),
rr!("pnpm-list","pnpm","^(?:list|ls)(?:\\s|$)",&["--depth=0","--json"],&[],&["--depth","--json"]),
rr!("yarn-test","yarn","^(?:test|run\\s+test)(?:\\s|$)",&["--silent"],&[],&["--silent","--verbose"]),
rr!("yarn-list","yarn","^list(?:\\s|$)",&["--depth=0","--json"],&[],&["--depth","--json"]),
rr!("jest","jest",".*",&["--silent","--reporters=default"],&[],&["--silent","--verbose","--json","--reporters"]),
rr!("vitest","vitest",".*",&["--reporter=basic"],&[],&["--reporter","--silent"]),
rr!("playwright","playwright","^test(?:\\s|$)",&["--reporter=line"],&[],&["--reporter"]),
rr!("ruff","ruff","^(?:check|format)(?:\\s|$)",&["--output-format=concise"],&[],&["--output-format","--verbose"]),
rr!("mypy","mypy",".*",&["--no-error-summary","--show-column-numbers"],&[],&["--pretty","--no-error-summary"]),
rr!("eslint","eslint",".*",&["--format=compact"],&[],&["--format","-f"]),
rr!("biome","biome","^(?:check|lint)(?:\\s|$)",&["--reporter=summary"],&[],&["--reporter"]),
rr!("coverage","coverage","^report(?:\\s|$)",&["--format=total"],&[],&["--format","-m"]),
rr!("docker-build","docker","^build(?:\\s|$)",&["--progress=plain"],&[],&["--progress","--quiet","-q"]),
rr!("docker-ps","docker","^ps(?:\\s|$)",&["--format={{.ID}} {{.Image}} {{.Status}} {{.Names}}"],&[],&["--format"]),
rr!("docker-images","docker","^images(?:\\s|$)",&["--format={{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}"],&[],&["--format"]),
rr!("docker-logs","docker","^logs(?:\\s|$)",&["--tail=200"],&[],&["--tail","-n","--follow","-f"]),
rr!("docker-inspect","docker","^inspect(?:\\s|$)",&["--format={{json .}}"],&[],&["--format","-f"]),
rr!("docker-stats","docker","^stats(?:\\s|$)",&["--no-stream","--format={{.Name}} {{.CPUPerc}} {{.MemUsage}}"],&[],&["--no-stream","--format"]),
rr!("kubectl-get","kubectl","^get(?:\\s|$)",&["-o","json"],&[],&["-o","--output","-w","--watch"]),
rr!("kubectl-describe","kubectl","^describe(?:\\s|$)",&[],&[],&[]),
rr!("kubectl-logs","kubectl","^logs(?:\\s|$)",&["--tail=200"],&[],&["--tail","-f","--follow"]),
rr!("kubectl-events","kubectl","^events(?:\\s|$)",&["-o","json"],&[],&["-o","--output","--watch"]),
rr!("gh-pr","gh","^pr\\s+(?:view|checks)(?:\\s|$)",&["--json","number,title,state,statusCheckRollup"],&[],&["--json","--web"]),
rr!("gh-issue","gh","^issue\\s+(?:list|view)(?:\\s|$)",&["--json","number,title,state,labels"],&[],&["--json","--web"]),
rr!("gh-run","gh","^run\\s+(?:list|view)(?:\\s|$)",&["--json","databaseId,name,status,conclusion,headSha"],&[],&["--json","--log"]),
rr!("pip-list","pip","^list(?:\\s|$)",&["--format=json"],&[],&["--format","--verbose"]),
rr!("pip-show","pip","^show(?:\\s|$)",&[],&[],&[]),
rr!("uv-pip-list","uv","^pip\\s+list(?:\\s|$)",&["--format=json"],&[],&["--format"]),
rr!("poetry-show","poetry","^show(?:\\s|$)",&["--tree"],&[],&["--tree","--latest"]),
rr!("terraform-plan","terraform","^plan(?:\\s|$)",&["-no-color","-compact-warnings"],&[],&["-json","-no-color"]),
rr!("terraform-show","terraform","^show(?:\\s|$)",&["-json"],&[],&["-json","-no-color"]),
rr!("ansible","ansible-playbook",".*",&["-v"],&[],&["-v","-vv","-vvv","-vvvv"]),
rr!("aws","aws",".*",&["--output","json","--no-cli-pager"],&[],&["--output","--cli-auto-prompt"]),
rr!("gcloud","gcloud",".*",&["--format=json","--quiet"],&[],&["--format","--verbosity"]),
rr!("az","az",".*",&["--output","json"],&[],&["--output","-o"]),
rr!("systemctl","systemctl","^(?:status|list-units)(?:\\s|$)",&["--no-pager","--plain"],&[],&["--no-pager","--output"]),
rr!("journalctl","journalctl",".*",&["--no-pager","-n","200","-o","short-iso"],&[],&["--no-pager","-n","--lines","-o","--output","-f"]),
rr!("dotnet-test","dotnet","^test(?:\\s|$)",&["--verbosity","minimal"],&[],&["--verbosity","-v"]),
rr!("maven-test","mvn",".*",&["-q"],&[],&["-q","-X","--debug"]),
rr!("gradle-test","gradle",".*",&["--console=plain","--warning-mode=summary"],&[],&["--console","--info","--debug"]),
rr!("cmake","cmake",".*",&["--log-level=WARNING"],&[],&["--log-level","--trace"]),
rr!("ctest","ctest",".*",&["--output-on-failure","--no-tests=error"],&[],&["--verbose","-V"]),
rr!("git-grep","git","^grep(?:\\s|$)",&["-n"],&[],&["-n","--line-number","--column"]),
rr!("git-reflog","git","^reflog(?:\\s|$)",&["--oneline","-n","40"],&[],&["--format","--pretty","--oneline","-n","--max-count"]),
rr!("git-shortlog","git","^shortlog(?:\\s|$)",&["-sne"],&[],&["-s","-n","-e","--summary","--numbered","--email"]),
rr!("cargo-tree","cargo","^tree(?:\\s|$)",&["--depth","3"],&[],&["--depth","--prefix","--format"]),
rr!("cargo-metadata","cargo","^metadata(?:\\s|$)",&["--format-version","1","--no-deps"],&[],&["--format-version","--no-deps","--filter-platform"]),
rr!("cargo-audit","cargo","^audit(?:\\s|$)",&["--json"],&[],&["--json","--quiet"]),
rr!("cargo-deny","cargo","^deny(?:\\s|$)",&["--format","json"],&[],&["--format"]),
rr!("cargo-nextest","cargo","^nextest\\s+run(?:\\s|$)",&["--status-level","fail","--final-status-level","fail"],&[],&["--status-level","--final-status-level"]),
rr!("go-vet","go","^vet(?:\\s|$)",&["-json"],&[],&["-json","-v"]),
rr!("go-list","go","^list(?:\\s|$)",&["-json"],&[],&["-json","-f","-template"]),
rr!("golangci-lint","golangci-lint","^(?:run)?(?:\\s|$)",&["--out-format=line-number"],&[],&["--out-format","--output"]),
rr!("govulncheck","govulncheck",".*",&["-json"],&[],&["-json","-mode"]),
rr!("bun-test","bun","^(?:test|run\\s+test)(?:\\s|$)",&["--reporter=dot"],&[],&["--reporter","--verbose"]),
rr!("bun-install","bun","^(?:install|add|update)(?:\\s|$)",&["--silent"],&[],&["--silent","--verbose"]),
rr!("deno-test","deno","^test(?:\\s|$)",&["--quiet"],&[],&["--quiet","--reporter"]),
rr!("deno-lint","deno","^lint(?:\\s|$)",&["--json"],&[],&["--json","--compact"]),
rr!("mocha","mocha",".*",&["--reporter","dot"],&[],&["--reporter","-R"]),
rr!("ava","ava",".*",&["--tap"],&[],&["--tap","--verbose"]),
rr!("tox","tox",".*",&["-q"],&[],&["-q","--quiet","-v","--verbose"]),
rr!("semgrep","semgrep",".*",&["--json","--quiet"],&[],&["--json","--quiet","--text"]),
rr!("sqlfluff","sqlfluff","^(?:lint|parse)(?:\\s|$)",&["--format","json"],&[],&["--format","-f"]),
rr!("trivy","trivy",".*",&["--format","json","--quiet"],&[],&["--format","-f","--quiet"]),
rr!("snyk","snyk","^(?:test|code\\s+test|container\\s+test)(?:\\s|$)",&["--json"],&[],&["--json","--sarif"]),
rr!("composer-show","composer","^show(?:\\s|$)",&["--format=json"],&[],&["--format","-f"]),
rr!("phpunit","phpunit",".*",&["--colors=never"],&[],&["--colors","--testdox"]),
rr!("rspec","rspec",".*",&["--format","progress","--no-color"],&[],&["--format","-f","--color","--no-color"]),
rr!("swift-test","swift","^test(?:\\s|$)",&["--quiet"],&[],&["--quiet","--verbose"]),
rr!("xcodebuild","xcodebuild",".*",&["-quiet"],&[],&["-quiet","-verbose"]),
rr!("jq","jq",".*",&["-c"],&[],&["-c","--compact-output","-r","--raw-output"]),
rr!("yq","yq",".*",&["-o=json","-I=0"],&[],&["-o","--output-format","-I","--indent"]),
rr!("sqlite3","sqlite3",".*",&["-json"],&[],&["-json","-csv","-table","-line"]),
rr!("psql","psql",".*",&["--csv","--quiet"],&[],&["--csv","--quiet","-q","--tuples-only"]),
rr!("mysql","mysql",".*",&["--batch","--skip-column-names"],&[],&["--batch","-B","--skip-column-names","-N"]),
rr!("redis-cli","redis-cli",".*",&["--raw"],&[],&["--raw","--json","--csv"]),
rr!("gh-release","gh","^release\\s+list(?:\\s|$)",&["--json","tagName,name,isDraft,isPrerelease,publishedAt"],&[],&["--json","--web"]),
rr!("gh-workflow","gh","^workflow\\s+list(?:\\s|$)",&["--json","id,name,state"],&[],&["--json","--web"]),
rr!("gh-secret","gh","^secret\\s+list(?:\\s|$)",&["--json","name,updatedAt"],&[],&["--json"]),
rr!("gh-variable","gh","^variable\\s+list(?:\\s|$)",&["--json","name,value,updatedAt"],&[],&["--json"]),
rr!("gh-codespace","gh","^codespace\\s+list(?:\\s|$)",&["--json","name,state,repository"],&[],&["--json"]),
rr!("docker-system","docker","^system\\s+df(?:\\s|$)",&["--format={{json .}}"],&[],&["--format","-v","--verbose"]),
rr!("docker-volume","docker","^volume\\s+ls(?:\\s|$)",&["--format={{json .}}"],&[],&["--format","-q","--quiet"]),
rr!("docker-network","docker","^network\\s+ls(?:\\s|$)",&["--format={{json .}}"],&[],&["--format","-q","--quiet"]),
rr!("hadolint","hadolint",".*",&["--format","json"],&[],&["--format","-f"]),
rr!("shellcheck","shellcheck",".*",&["--format","json1"],&[],&["--format","-f"]),
rr!("bats","bats",".*",&["--formatter","tap"],&[],&["--formatter","-F"]),
rr!("bazel","bazel","^(?:test|build)(?:\\s|$)",&["--noshow_progress","--ui_event_filters=-info"],&[],&["--show_progress","--noshow_progress","--ui_event_filters"]),
rr!("buck2","buck2","^(?:test|build)(?:\\s|$)",&["--console=simple"],&[],&["--console"]),
rr!("pants","pants",".*",&["--level=warn"],&[],&["--level"]),
rr!("msbuild","msbuild",".*",&["-nologo","-verbosity:minimal"],&[],&["-nologo","-verbosity","/verbosity"]),
rr!("terraform-validate","terraform","^validate(?:\\s|$)",&["-json"],&[],&["-json","-no-color"]),
rr!("helm-list","helm","^list(?:\\s|$)",&["-o","json"],&[],&["-o","--output"]),
rr!("helm-status","helm","^status(?:\\s|$)",&["-o","json"],&[],&["-o","--output"]),
rr!("kubectl-config","kubectl","^config\\s+get-contexts(?:\\s|$)",&["-o","name"],&[],&["-o","--output"]),
];
const REWRITE_RULE_COUNT: usize = REWRITE_RULES.len();

fn cc_casefold(value: &str) -> String {
    value.chars().flat_map(char::to_lowercase).collect::<String>().replace('ſ', "s").replace('K', "k")
}
fn cc_basename(value: &str) -> &str { value.rsplit(['/', '\\']).next().unwrap_or(value) }
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
        if wrapper == "env" { while index < argv.len() && cc_assignment(&argv[index]) { index += 1; } }
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
fn cc_shell_split_posix(value: &str) -> Result<Vec<String>, String> {
    #[derive(Clone, Copy, PartialEq, Eq)] enum State { Unquoted, SingleQuoted, DoubleQuoted }
    let mut chars = value.chars().peekable();
    let mut output = Vec::<String>::new();
    let mut token = String::new();
    let mut started = false;
    let mut state = State::Unquoted;
    while let Some(ch) = chars.next() {
        match state {
            State::Unquoted => {
                if ch.is_whitespace() { if started { output.push(std::mem::take(&mut token)); started=false; } continue; }
                started = true;
                match ch {
                    '\'' => state = State::SingleQuoted,
                    '"' => state = State::DoubleQuoted,
                    '\\' => token.push(chars.next().ok_or_else(|| "No escaped character".to_owned())?),
                    _ => token.push(ch),
                }
            }
            State::SingleQuoted => { if ch == '\'' { state = State::Unquoted; } else { token.push(ch); } }
            State::DoubleQuoted => {
                if ch == '"' { state = State::Unquoted; }
                else if ch == '\\' {
                    let next = chars.next().ok_or_else(|| "No escaped character".to_owned())?;
                    if matches!(next, '"' | '\\') { token.push(next); } else { token.push('\\'); token.push(next); }
                } else { token.push(ch); }
            }
        }
    }
    if state != State::Unquoted { return Err("No closing quotation".to_owned()); }
    if started { output.push(token); }
    Ok(output)
}
#[cfg(windows)]
fn cc_shell_split_platform(value: &str) -> Result<Vec<String>, String> {
    let chars = value.chars().collect::<Vec<_>>();
    let mut output = Vec::<String>::new();
    let mut index = 0usize;
    while index < chars.len() {
        while index < chars.len() && chars[index].is_whitespace() { index += 1; }
        if index >= chars.len() { break; }
        if matches!(chars[index], '\'' | '"') {
            let quote=chars[index]; let mut token=String::new(); token.push(quote); index+=1; let mut closed=false;
            while index < chars.len() { let ch=chars[index]; token.push(ch); index+=1; if ch==quote { closed=true; break; } }
            if !closed { return Err("No closing quotation".to_owned()); }
            output.push(token); continue;
        }
        let mut token=String::new();
        while index < chars.len() && !chars[index].is_whitespace() { token.push(chars[index]); index+=1; }
        output.push(token);
    }
    Ok(output)
}
#[cfg(not(windows))]
fn cc_shell_split_platform(value: &str) -> Result<Vec<String>, String> { cc_shell_split_posix(value) }
fn cc_rewrite_argv(argv: &[String]) -> Result<Value, String> {
    if argv.is_empty() { return Ok(cc_rewrite_result(argv, Vec::new(), None, false, "empty command")); }
    let Some(index)=cc_executable_index(argv) else { return Ok(cc_rewrite_result(argv, argv.to_vec(), None, false, "wrapper options are not rewritten")); };
    let command=&argv[index..];
    let executable=cc_casefold(cc_basename(&command[0]));
    let arguments=command.iter().skip(1).cloned().collect::<Vec<_>>().join(" ").to_lowercase();
    for rule in REWRITE_RULES {
        if executable != rule.executable { continue; }
        let pattern=regex::Regex::new(rule.argument_pattern).map_err(|error| format!("REWRITE_RULE_REGEX_FAILED:{}:{error}",rule.name))?;
        if !pattern.is_match(&arguments) { continue; }
        if cc_has_blocked(command, rule.blocked) { return Ok(cc_rewrite_result(argv, argv.to_vec(), Some(rule.name), true, "explicit user format preserved")); }
        let mut rewritten_command=command.to_vec();
        for (before, after) in rule.replace {
            if let Some(position)=rewritten_command.iter().position(|item| item==before) {
                let replacement=cc_shell_split_posix(after)?;
                rewritten_command.splice(position..position+1,replacement);
            }
        }
        rewritten_command.extend(rule.append.iter().map(|item|(*item).to_owned()));
        let mut rewritten=argv[..index].to_vec(); rewritten.extend(rewritten_command);
        return Ok(cc_rewrite_result(argv, rewritten, Some(rule.name), true, "reduce machine-irrelevant output"));
    }
    Ok(cc_rewrite_result(argv, argv.to_vec(), None, true, "no matching rewrite rule"))
}
fn cc_split(command: &str) -> Vec<String> { command.split_whitespace().map(str::to_owned).collect() }
fn cc_rewrite_text(command: &str) -> Result<Value, String> {
    let unsafe_re=regex::Regex::new(r"(?:\|\||&&|[|;`]|\$\(|\n|\r|>|<)").map_err(|error|format!("REWRITE_UNSAFE_REGEX_FAILED:{error}"))?;
    let raw_unsafe=unsafe_re.is_match(command);
    let (argv,parse_failed)=match cc_shell_split_platform(command) { Ok(value)=>(value,false), Err(_)=>(cc_split(command),true) };
    if argv.is_empty() { return Ok(cc_rewrite_result(&argv, Vec::new(), None, false, "empty command")); }
    if raw_unsafe || parse_failed { return Ok(cc_rewrite_result(&argv, argv.clone(), None, false, "shell composition is not rewritten")); }
    cc_rewrite_argv(&argv)
}
fn cc_rewrite_action(arguments: &[String]) -> Result<Value, String> {
    let start=arguments.windows(2).position(|items|items[0]=="run"&&items[1]=="rewrite").map(|i|i+2).ok_or_else(||"REWRITE_ACTION_NOT_FOUND".to_owned())?;
    let argv=arguments[start..].iter().filter(|value|value.as_str()!="--").cloned().collect::<Vec<_>>();
    if argv.is_empty() { return Err("rewrite command is required".to_owned()); }
    cc_rewrite_argv(&argv)
}
