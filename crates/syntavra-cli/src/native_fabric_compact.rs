#![forbid(unsafe_code)]
#![allow(clippy::too_many_lines)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{self, Read as _};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use base64::{engine::general_purpose, Engine as _};
use regex::Regex;
use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};
use unicode_normalization::UnicodeNormalization;

const BOUNDED_MARKER: &str =
    "\n[… exact output externalized; search or reveal for omitted evidence …]";

pub fn supports(command: &[String]) -> bool {
    matches!(command, [fabric, action] if fabric == "fabric" && action == "compact")
}

#[derive(Debug)]
struct Options {
    stdout_file: Option<String>,
    stderr_file: Option<String>,
    stdout: String,
    stderr: String,
    budget_bytes: usize,
    output: Option<PathBuf>,
    command: Vec<String>,
}

#[derive(Debug)]
struct SecurityScan {
    normalized_text: String,
    redacted_text: String,
    secret_types: Vec<String>,
    injection_risk: bool,
    injection_reasons: Vec<String>,
}

#[derive(Debug, Clone, Copy)]
enum Strategy {
    Test,
    GitStatus,
    GitDiff,
    GitLog,
    Search,
    Lint,
    DockerBuild,
    Table,
    JsonOrTable,
    HeadTail,
}

fn command_start(arguments: &[String]) -> Result<usize, String> {
    arguments
        .windows(2)
        .position(|row| row[0] == "fabric" && row[1] == "compact")
        .map(|index| index + 2)
        .ok_or_else(|| "FABRIC_COMPACT_COMMAND_MISSING".to_owned())
}

fn take_value(arguments: &[String], index: &mut usize, name: &str) -> Result<String, String> {
    *index += 1;
    arguments
        .get(*index)
        .cloned()
        .ok_or_else(|| format!("{name}_VALUE_MISSING"))
}

fn parse_options(arguments: &[String]) -> Result<Options, String> {
    let mut stdout_file = None;
    let mut stderr_file = None;
    let mut stdout = String::new();
    let mut stderr = String::new();
    let mut budget_bytes = 4096usize;
    let mut output = None;
    let mut command = Vec::new();
    let mut index = command_start(arguments)?;
    while index < arguments.len() {
        let item = &arguments[index];
        if item == "--" {
            command.extend(arguments[index + 1..].iter().cloned());
            break;
        }
        match item.as_str() {
            "--stdout-file" => stdout_file = Some(take_value(arguments, &mut index, item)?),
            "--stderr-file" => stderr_file = Some(take_value(arguments, &mut index, item)?),
            "--stdout" => stdout = take_value(arguments, &mut index, item)?,
            "--stderr" => stderr = take_value(arguments, &mut index, item)?,
            "--budget-bytes" => {
                budget_bytes = take_value(arguments, &mut index, item)?
                    .parse::<usize>()
                    .map_err(|error| format!("--budget-bytes_INVALID:{error}"))?;
            }
            "--output" => {
                output = Some(PathBuf::from(take_value(arguments, &mut index, item)?));
            }
            _ => {
                if let Some(value) = item.strip_prefix("--stdout-file=") {
                    stdout_file = Some(value.to_owned());
                } else if let Some(value) = item.strip_prefix("--stderr-file=") {
                    stderr_file = Some(value.to_owned());
                } else if let Some(value) = item.strip_prefix("--stdout=") {
                    stdout = value.to_owned();
                } else if let Some(value) = item.strip_prefix("--stderr=") {
                    stderr = value.to_owned();
                } else if let Some(value) = item.strip_prefix("--budget-bytes=") {
                    budget_bytes = value
                        .parse::<usize>()
                        .map_err(|error| format!("--budget-bytes_INVALID:{error}"))?;
                } else if let Some(value) = item.strip_prefix("--output=") {
                    output = Some(PathBuf::from(value));
                } else {
                    command.extend(arguments[index..].iter().cloned());
                    break;
                }
            }
        }
        index += 1;
    }
    if command.is_empty() {
        return Err("command argv is required after --".to_owned());
    }
    Ok(Options {
        stdout_file,
        stderr_file,
        stdout,
        stderr,
        budget_bytes,
        output,
        command,
    })
}

fn option_value(arguments: &[String], name: &str) -> Option<String> {
    let prefix = format!("{name}=");
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        if arguments[index] == name {
            if let Some(value) = arguments.get(index + 1) {
                found = Some(value.clone());
                index += 1;
            }
        } else if let Some(value) = arguments[index].strip_prefix(&prefix) {
            found = Some(value.to_owned());
        }
        index += 1;
    }
    found
}

fn read_text(path: Option<&str>, fallback: &str, label: &str) -> Result<String, String> {
    match path {
        Some("-") => {
            let mut value = String::new();
            io::stdin()
                .read_to_string(&mut value)
                .map_err(|error| format!("FABRIC_COMPACT_{label}_STDIN_READ_FAILED:{error}"))?;
            Ok(value)
        }
        Some(path) => fs::read_to_string(path)
            .map_err(|error| format!("FABRIC_COMPACT_{label}_READ_FAILED:{error}")),
        None => Ok(fallback.to_owned()),
    }
}

fn regex(pattern: &str, label: &str) -> Result<Regex, String> {
    Regex::new(pattern).map_err(|error| format!("FABRIC_COMPACT_{label}_REGEX_FAILED:{error}"))
}

fn dedup(values: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut seen = BTreeSet::new();
    values
        .into_iter()
        .filter(|value| !value.trim().is_empty())
        .filter(|value| seen.insert(value.clone()))
        .collect()
}

fn normalize_text(text: &str) -> Result<String, String> {
    let ansi = regex(
        r"\x1b(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))",
        "ANSI",
    )?;
    let stripped = ansi.replace_all(text, "");
    let normalized: String = stripped
        .nfkc()
        .filter(|value| {
            !matches!(
                *value,
                '\u{200b}' | '\u{200c}' | '\u{200d}' | '\u{2060}' | '\u{feff}'
            )
        })
        .collect();
    Ok(normalized.replace("\r\n", "\n").replace('\r', "\n"))
}

fn push_unique(values: &mut Vec<String>, value: &str) {
    if !values.iter().any(|item| item == value) {
        values.push(value.to_owned());
    }
}

fn luhn(value: &str) -> bool {
    let digits = value
        .chars()
        .filter_map(|character| character.to_digit(10))
        .collect::<Vec<_>>();
    if !(13..=19).contains(&digits.len()) || digits.iter().all(|digit| *digit == digits[0]) {
        return false;
    }
    let parity = digits.len() % 2;
    let mut total = 0u32;
    for (index, mut digit) in digits.into_iter().enumerate() {
        if index % 2 == parity {
            digit *= 2;
            if digit > 9 {
                digit -= 9;
            }
        }
        total += digit;
    }
    total % 10 == 0
}

fn entropy(token: &str) -> f64 {
    if token.is_empty() {
        return 0.0;
    }
    let mut counts = BTreeMap::<char, usize>::new();
    for character in token.chars() {
        *counts.entry(character).or_default() += 1;
    }
    let length = token.chars().count() as f64;
    counts
        .values()
        .map(|count| {
            let probability = *count as f64 / length;
            -probability * probability.log2()
        })
        .sum()
}

fn redact_security_patterns(normalized: &str) -> Result<(String, Vec<String>), String> {
    let mut redacted = normalized.to_owned();
    let mut secret_types = Vec::new();

    let generic = regex(
        r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|passwd|secret|bearer|private[_-]?key|client[_-]?secret|session[_-]?id|cookie)\b\s*[:=]\s*([^\s,;]+)",
        "GENERIC_SECRET",
    )?;
    let mut found = false;
    redacted = generic
        .replace_all(&redacted, |captures: &regex::Captures<'_>| {
            found = true;
            format!("{}=<redacted:generic-assignment>", &captures[1])
        })
        .into_owned();
    if found {
        push_unique(&mut secret_types, "generic-assignment");
    }

    for (name, pattern, replacement) in [
        (
            "aws-access-key",
            r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
            "<redacted:aws-access-key>",
        ),
        (
            "github-token",
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b",
            "<redacted:github-token>",
        ),
        (
            "jwt",
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
            "<redacted:jwt>",
        ),
        (
            "database-uri",
            r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+",
            "<redacted:database-uri>",
        ),
    ] {
        let matcher = regex(pattern, "SECRET")?;
        if matcher.is_match(&redacted) {
            push_unique(&mut secret_types, name);
            redacted = matcher.replace_all(&redacted, replacement).into_owned();
        }
    }

    let private_key = regex(
        r"(?s)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
        "PRIVATE_KEY",
    )?;
    if private_key.is_match(&redacted) {
        push_unique(&mut secret_types, "private-key");
        redacted = private_key
            .replace_all(
                &redacted,
                "-----BEGIN PRIVATE KEY-----<redacted:private-key>-----END PRIVATE KEY-----",
            )
            .into_owned();
    }

    let payment = regex(r"(?P<card>(?:\d[ -]*?){13,19})", "PAYMENT_CARD")?;
    redacted = payment
        .replace_all(&redacted, |captures: &regex::Captures<'_>| {
            let value = captures.name("card").map_or("", |item| item.as_str());
            if luhn(value) {
                "<redacted:payment-card>".to_owned()
            } else {
                value.to_owned()
            }
        })
        .into_owned();

    let email = regex(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "EMAIL",
    )?;
    redacted = email
        .replace_all(&redacted, "<redacted:email>")
        .into_owned();
    Ok((redacted, secret_types))
}

fn confusable_skeleton(text: &str) -> String {
    text.chars()
        .map(|character| match character {
            'а' => 'a',
            'е' => 'e',
            'о' => 'o',
            'р' => 'p',
            'с' => 'c',
            'у' => 'y',
            'х' => 'x',
            'Α' => 'A',
            'Β' => 'B',
            'Ε' => 'E',
            'Ζ' => 'Z',
            'Η' => 'H',
            'Ι' => 'I',
            'Κ' => 'K',
            'Μ' => 'M',
            'Ν' => 'N',
            'Ο' => 'O',
            'Ρ' => 'P',
            'Τ' => 'T',
            'Υ' => 'Y',
            'Χ' => 'X',
            other => other,
        })
        .collect()
}

fn injection_reasons(normalized: &str) -> Result<Vec<String>, String> {
    let patterns = [
        r"(?is)(ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|do\s+not\s+follow\s+(?:the\s+)?(?:system|developer)|reveal\s+(?:the\s+)?(?:system\s+)?prompt|you\s+are\s+(?:chatgpt|an?\s+assistant)|</?(?:system|assistant|developer|tool)>|system\s+message\s*:|developer\s+message\s*:)",
        r"(?is)(önceki\s+(?:tüm\s+)?talimatları\s+(?:yoksay|unut)|sistem\s+istemini\s+(?:göster|açıkla)|geliştirici\s+mesajını\s+(?:göster|ifşa\s+et))",
        r"(?is)(ignora\s+(?:todas\s+)?las\s+instrucciones\s+anteriores|忽略(?:之前|所有).*指令|以前の指示を.*無視)",
    ];
    let skeleton = confusable_skeleton(normalized);
    let mut reasons = Vec::new();
    for (index, pattern) in patterns.into_iter().enumerate() {
        let matcher = regex(pattern, "INJECTION")?;
        if matcher.is_match(normalized) {
            reasons.push(format!("direct-pattern-{}", index + 1));
        } else if skeleton != normalized && matcher.is_match(&skeleton) {
            reasons.push(format!("confusable-pattern-{}", index + 1));
        }
    }
    Ok(reasons)
}

fn decode_hex(token: &str) -> Option<Vec<u8>> {
    if token.len() % 2 != 0 {
        return None;
    }
    let mut output = Vec::with_capacity(token.len() / 2);
    for index in (0..token.len()).step_by(2) {
        output.push(u8::from_str_radix(&token[index..index + 2], 16).ok()?);
    }
    Some(output)
}

fn percent_decode(token: &str) -> Option<Vec<u8>> {
    let bytes = token.as_bytes();
    let mut output = Vec::new();
    let mut index = 0usize;
    while index < bytes.len() {
        if bytes[index] != b'%' || index + 2 >= bytes.len() {
            return None;
        }
        let value = u8::from_str_radix(&token[index + 1..index + 3], 16).ok()?;
        output.push(value);
        index += 3;
    }
    Some(output)
}

fn decoded_candidates(text: &str) -> Result<Vec<String>, String> {
    let base64_token = regex(r"(?P<token>[A-Za-z0-9+/_-]{32,}={0,2})", "BASE64_TOKEN")?;
    let hex_token = regex(r"(?P<token>[0-9a-fA-F]{32,})", "HEX_TOKEN")?;
    let url_token = regex(r"(?P<token>(?:%[0-9A-Fa-f]{2}){8,})", "URL_TOKEN")?;
    let mut result = Vec::new();
    let mut seen = BTreeSet::<Vec<u8>>::new();
    let mut consumed = 0usize;
    for captures in base64_token.captures_iter(text) {
        if result.len() >= 128 {
            break;
        }
        let Some(token) = captures.name("token").map(|item| item.as_str()) else {
            continue;
        };
        let padded = format!("{token}{}", "=".repeat((4 - token.len() % 4) % 4));
        let raw = general_purpose::STANDARD
            .decode(&padded)
            .or_else(|_| general_purpose::URL_SAFE.decode(&padded));
        let Ok(raw) = raw else { continue };
        if raw.is_empty() || seen.contains(&raw) || consumed + raw.len() > 2 * 1024 * 1024 {
            continue;
        }
        if let Ok(value) = String::from_utf8(raw.clone()) {
            consumed += raw.len();
            seen.insert(raw);
            result.push(value);
        }
    }
    for (matcher, decoder) in [
        (&hex_token, decode_hex as fn(&str) -> Option<Vec<u8>>),
        (&url_token, percent_decode as fn(&str) -> Option<Vec<u8>>),
    ] {
        for captures in matcher.captures_iter(text) {
            if result.len() >= 128 {
                break;
            }
            let Some(token) = captures.name("token").map(|item| item.as_str()) else {
                continue;
            };
            let Some(raw) = decoder(token) else { continue };
            if raw.is_empty() || seen.contains(&raw) || consumed + raw.len() > 2 * 1024 * 1024 {
                continue;
            }
            if let Ok(value) = String::from_utf8(raw.clone()) {
                consumed += raw.len();
                seen.insert(raw);
                result.push(value);
            }
        }
    }
    Ok(result)
}

fn scan_text(text: &str, inspect_encoded: bool) -> Result<SecurityScan, String> {
    let normalized = normalize_text(text)?;
    let (redacted, mut secret_types) = redact_security_patterns(&normalized)?;
    let mut reasons = injection_reasons(&normalized)?;
    let entropy_token = regex(r"(?P<token>[A-Za-z0-9_\-+/=]{24,})", "ENTROPY_TOKEN")?;
    for captures in entropy_token.captures_iter(&normalized) {
        let Some(token) = captures.name("token").map(|item| item.as_str()) else {
            continue;
        };
        let unique = token.chars().collect::<BTreeSet<_>>().len();
        if entropy(token) >= 4.2
            && unique >= 10
            && !token.to_ascii_lowercase().contains("http")
            && !token.to_ascii_lowercase().contains("sha256")
        {
            push_unique(&mut secret_types, "high-entropy-token");
        }
    }
    if inspect_encoded {
        for decoded in decoded_candidates(&normalized)? {
            let nested = scan_text(&decoded, false)?;
            for kind in nested.secret_types {
                push_unique(&mut secret_types, &kind);
            }
            if nested.injection_risk {
                push_unique(&mut reasons, "encoded-instruction");
            }
        }
    }
    Ok(SecurityScan {
        normalized_text: normalized,
        redacted_text: redacted,
        secret_types,
        injection_risk: !reasons.is_empty(),
        injection_reasons: reasons,
    })
}

fn executable(command: &[String]) -> String {
    command
        .first()
        .and_then(|value| value.rsplit(['/', '\\']).next())
        .unwrap_or_default()
        .to_ascii_lowercase()
}

fn family(command: &[String]) -> &'static str {
    if command.is_empty() {
        return "empty";
    }
    let exe = executable(command);
    let joined = command.join(" ").to_ascii_lowercase();
    if exe == "git" {
        "git"
    } else if exe == "gh" {
        "github"
    } else if matches!(
        exe.as_str(),
        "pytest" | "py.test" | "jest" | "vitest" | "ctest"
    ) || format!(" {joined}").contains(" test")
    {
        "test"
    } else if matches!(exe.as_str(), "grep" | "rg" | "find" | "fd" | "ls" | "tree") {
        "search"
    } else if matches!(
        exe.as_str(),
        "cat" | "head" | "tail" | "sed" | "type" | "get-content"
    ) {
        "read"
    } else if matches!(
        exe.as_str(),
        "npm" | "pnpm" | "yarn" | "pip" | "uv" | "cargo"
    ) {
        "package"
    } else if matches!(exe.as_str(), "kubectl" | "aws" | "gcloud" | "az") {
        "cloud"
    } else if matches!(exe.as_str(), "docker" | "podman") {
        "container"
    } else if matches!(exe.as_str(), "curl" | "wget" | "iwr" | "invoke-webrequest") {
        "network"
    } else if matches!(
        exe.as_str(),
        "make" | "cmake" | "ninja" | "gradle" | "mvn" | "dotnet" | "go"
    ) {
        "build"
    } else {
        "generic"
    }
}

fn command_parts(command: &[String]) -> (String, String) {
    let mut index = 0usize;
    while index < command.len() {
        let value = &command[index];
        let assignment = value.split_once('=').is_some_and(|(name, _)| {
            let mut chars = name.chars();
            chars
                .next()
                .is_some_and(|item| item.is_ascii_alphabetic() || item == '_')
                && chars.all(|item| item.is_ascii_alphanumeric() || item == '_')
        });
        if !assignment {
            break;
        }
        index += 1;
    }
    while index < command.len() {
        let wrapper = command[index]
            .rsplit(['/', '\\'])
            .next()
            .unwrap_or_default()
            .to_ascii_lowercase();
        if !matches!(wrapper.as_str(), "command" | "env" | "sudo" | "time") {
            break;
        }
        index += 1;
        if wrapper == "env" {
            while index < command.len() && command[index].contains('=') {
                index += 1;
            }
        } else if command
            .get(index)
            .is_some_and(|value| value.starts_with('-'))
        {
            return (String::new(), String::new());
        }
    }
    if index >= command.len() {
        return (String::new(), String::new());
    }
    let exe = command[index]
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let arguments = command[index + 1..].join(" ").to_ascii_lowercase();
    (exe, arguments)
}

fn starts(arguments: &str, prefixes: &[&str]) -> bool {
    prefixes.iter().any(|prefix| {
        arguments == *prefix
            || arguments
                .strip_prefix(prefix)
                .is_some_and(|tail| tail.starts_with(char::is_whitespace))
    })
}

fn plugin(command: &[String]) -> Option<(&'static str, Strategy)> {
    let (exe, arguments) = command_parts(command);
    let value =
        match exe.as_str() {
            "git" if starts(&arguments, &["status"]) => ("git-status", Strategy::GitStatus),
            "git" if starts(&arguments, &["diff", "show"]) => ("git-diff", Strategy::GitDiff),
            "git" if starts(&arguments, &["log", "reflog", "shortlog"]) => {
                ("git-log", Strategy::GitLog)
            }
            "git" if starts(&arguments, &["grep", "ls-files"]) => ("git-grep", Strategy::Search),
            "git"
                if starts(
                    &arguments,
                    &[
                        "branch",
                        "stash",
                        "worktree",
                        "fetch",
                        "pull",
                        "push",
                        "remote",
                        "tag",
                        "clean",
                        "reset",
                        "restore",
                        "checkout",
                        "switch",
                        "blame",
                        "submodule",
                        "config",
                        "rev-list",
                        "bisect",
                        "notes",
                    ],
                ) =>
            {
                ("git-table", Strategy::Table)
            }
            "rg" => ("ripgrep", Strategy::Search),
            "grep" => ("grep", Strategy::Search),
            "find" | "fd" => ("find", Strategy::Search),
            "tree" => ("tree", Strategy::Search),
            "ls" | "dir" => ("ls", Strategy::Table),
            "pytest" | "py.test" => ("pytest", Strategy::Test),
            "cargo" if arguments.split_whitespace().any(|item| item == "test") => {
                ("cargo-test", Strategy::Test)
            }
            "cargo" if starts(&arguments, &["check", "clippy", "build"]) => {
                ("cargo-check", Strategy::Lint)
            }
            "cargo" if starts(&arguments, &["nextest"]) => ("cargo-nextest", Strategy::Test),
            "cargo" if starts(&arguments, &["metadata", "audit", "deny"]) => {
                ("cargo-json", Strategy::JsonOrTable)
            }
            "cargo" if starts(&arguments, &["tree"]) => ("cargo-tree", Strategy::Table),
            "go" if starts(&arguments, &["test"]) => ("go-test", Strategy::Test),
            "go" if starts(&arguments, &["build", "vet", "list"]) => ("go-build", Strategy::Lint),
            "npm" if starts(&arguments, &["test", "run test"]) => ("npm-test", Strategy::Test),
            "npm" if starts(&arguments, &["list", "ls", "outdated", "audit"]) => {
                ("npm-list", Strategy::JsonOrTable)
            }
            "pnpm" if starts(&arguments, &["test", "run test"]) => ("pnpm-test", Strategy::Test),
            "pnpm" if starts(&arguments, &["list", "ls", "outdated", "audit"]) => {
                ("pnpm-list", Strategy::JsonOrTable)
            }
            "yarn" if starts(&arguments, &["test", "run test"]) => ("yarn-test", Strategy::Test),
            "yarn" if starts(&arguments, &["list", "info", "audit"]) => {
                ("yarn-list", Strategy::JsonOrTable)
            }
            "jest" | "vitest" | "playwright" | "coverage" | "mocha" | "ava" | "tox" | "nox"
            | "phpunit" | "rspec" | "swift" | "xcodebuild" | "bats" | "bazel" | "buck2"
            | "pants" | "msbuild" => ("test", Strategy::Test),
            "ruff" | "mypy" | "eslint" | "biome" | "pylint" | "flake8" | "gofmt"
            | "golangci-lint" | "staticcheck" | "sqlfluff" | "hadolint" | "shellcheck"
            | "rustc" => ("lint", Strategy::Lint),
            "docker" | "podman" if starts(&arguments, &["build"]) => {
                ("docker-build", Strategy::DockerBuild)
            }
            "docker" | "podman"
                if starts(
                    &arguments,
                    &["inspect", "stats", "info", "system", "volume", "network"],
                ) =>
            {
                ("docker-json", Strategy::JsonOrTable)
            }
            "docker" | "podman" if starts(&arguments, &["ps", "images", "logs"]) => {
                ("docker-table", Strategy::Table)
            }
            "docker-compose" | "podman-compose" => ("compose", Strategy::Table),
            "kubectl"
                if starts(
                    &arguments,
                    &["get", "describe", "events", "top", "api-resources"],
                ) =>
            {
                ("kubectl-json", Strategy::JsonOrTable)
            }
            "kubectl" if starts(&arguments, &["logs"]) => ("kubectl-logs", Strategy::Table),
            "gh" if starts(
                &arguments,
                &[
                    "pr",
                    "issue",
                    "run",
                    "repo",
                    "api",
                    "release",
                    "workflow",
                    "secret",
                    "variable",
                    "codespace",
                ],
            ) =>
            {
                ("gh-json", Strategy::JsonOrTable)
            }
            "curl" | "wget" => ("curl", Strategy::HeadTail),
            "dotnet" | "mvn" | "mvnw" | "gradle" | "gradlew" | "cmake" | "ctest" | "ninja"
            | "make" => ("build-test", Strategy::Test),
            "pip" | "pip3" | "uv" | "poetry" | "aws" | "gcloud" | "az" | "helm" | "terraform"
            | "tofu" | "semgrep" | "trivy" | "snyk" | "composer" | "bundle" | "jq" | "yq"
            | "sqlite3" | "kustomize" | "nuget" => ("json-or-table", Strategy::JsonOrTable),
            "ansible" | "ansible-playbook" | "systemctl" | "journalctl" | "ps" | "tasklist"
            | "du" | "df" | "java" | "javac" | "pwsh" | "powershell" | "apt" | "apt-get"
            | "dnf" | "yum" | "brew" | "winget" | "choco" | "scoop" | "psql" | "mysql"
            | "redis-cli" => ("table", Strategy::Table),
            _ => return None,
        };
    Some(value)
}

fn error_regex() -> Result<Regex, String> {
    regex(
        r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|timeout|segmentation fault|not found|permission denied)\b",
        "ERROR",
    )
}

fn location_regex() -> Result<Regex, String> {
    regex(
        r#"(?:[^\s:]+\.(?:py|rs|js|jsx|ts|tsx|java|cs|go|rb|php|lua|luau|cpp|c|h):\d+|File \"[^\"]+\", line \d+|\bat [\w.$<>]+\([^)]*:\d+\))"#,
        "LOCATION",
    )
}

fn errors(lines: &[String]) -> Result<Vec<String>, String> {
    let error = error_regex()?;
    let location = location_regex()?;
    Ok(dedup(
        lines
            .iter()
            .filter(|line| error.is_match(line) || location.is_match(line))
            .cloned(),
    ))
}

fn head_tail(lines: &[String], head: usize, tail: usize) -> Vec<String> {
    if lines.len() <= head + tail {
        return lines.to_vec();
    }
    let mut result = lines[..head].to_vec();
    result.push(format!("[… {} lines omitted …]", lines.len() - head - tail));
    result.extend(lines[lines.len() - tail..].iter().cloned());
    result
}

fn select_test(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let summary = regex(
        r"(?i)(?:\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b|tests?:\s*\d+|test suites?:|failures?:\s*\d+|successes?:\s*\d+|test result:|packages?\s+\d+|ok\s+\S+\s+[\d.]+s)",
        "TEST_SUMMARY",
    )?;
    let failure = error_regex()?;
    let summaries = dedup(lines.iter().filter(|line| summary.is_match(line)).cloned());
    let failures = dedup(
        lines
            .iter()
            .filter(|line| {
                let trimmed = line.trim_start();
                trimmed.starts_with("E ")
                    || trimmed.starts_with("F ")
                    || trimmed.starts_with("FAILED")
                    || trimmed.starts_with('>')
                    || trimmed.starts_with("--- FAIL")
                    || trimmed.starts_with("failures:")
                    || failure.is_match(line)
            })
            .cloned(),
    );
    let mut selected = summaries[summaries.len().saturating_sub(16)..].to_vec();
    selected.extend(failures.into_iter().take(64));
    selected.extend(found_errors.iter().take(32).cloned());
    Ok((dedup(selected), found_errors.len()))
}

fn select_git_status(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let mut selected = dedup(
        lines
            .iter()
            .filter(|line| {
                line.starts_with("On branch ")
                    || line.starts_with("Your branch ")
                    || line.starts_with("Changes ")
                    || line.starts_with("Untracked ")
                    || line.starts_with("nothing to commit")
                    || matches!(
                        line.get(..2),
                        Some(" M" | "M " | "A " | " D" | "D " | "??" | "R " | "UU")
                    )
            })
            .cloned(),
    );
    selected.truncate(160);
    selected.extend(found_errors.iter().take(24).cloned());
    Ok((selected, found_errors.len()))
}

fn select_git_diff(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let headers = lines
        .iter()
        .filter(|line| {
            line.starts_with("diff --git")
                || line.starts_with("index ")
                || line.starts_with("--- ")
                || line.starts_with("+++ ")
                || line.starts_with("@@ ")
        })
        .cloned()
        .take(120);
    let changes = lines
        .iter()
        .filter(|line| {
            (line.starts_with('+') || line.starts_with('-'))
                && !line.starts_with("+++")
                && !line.starts_with("---")
        })
        .cloned()
        .collect::<Vec<_>>();
    let mut selected = headers.collect::<Vec<_>>();
    selected.extend(changes.iter().take(48).cloned());
    selected.extend(changes[changes.len().saturating_sub(24)..].iter().cloned());
    selected.extend(found_errors.iter().take(24).cloned());
    Ok((dedup(selected), found_errors.len()))
}

fn select_git_log(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let commits = lines
        .iter()
        .filter(|line| {
            line.starts_with("commit ")
                || line.starts_with("Author:")
                || line.starts_with("Date:")
                || line.starts_with("    ")
        })
        .cloned()
        .collect::<Vec<_>>();
    let mut selected = head_tail(&commits, 40, 20);
    selected.extend(found_errors.iter().take(16).cloned());
    Ok((selected, found_errors.len()))
}

fn group_key(line: &str) -> String {
    if let Some((key, _)) = line.split_once(':') {
        return key.to_owned();
    }
    Path::new(line)
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .map_or_else(
            || ".".to_owned(),
            |path| path.to_string_lossy().into_owned(),
        )
}

fn select_search(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let mut grouped = Vec::<(String, Vec<String>)>::new();
    for line in lines.iter().filter(|line| !line.trim().is_empty()) {
        let key = group_key(line);
        if let Some((_, values)) = grouped.iter_mut().find(|(candidate, _)| *candidate == key) {
            values.push(line.clone());
        } else {
            grouped.push((key, vec![line.clone()]));
        }
    }
    let count = grouped
        .iter()
        .map(|(_, values)| values.len())
        .sum::<usize>();
    let mut selected = vec![format!("results={count} groups={}", grouped.len())];
    for (key, values) in grouped.into_iter().take(50) {
        selected.push(format!("[{key}] {}", values.len()));
        selected.extend(values.into_iter().take(5));
    }
    selected.extend(found_errors.iter().take(24).cloned());
    Ok((selected, found_errors.len()))
}

fn select_lint(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let location = location_regex()?;
    let warning = regex(r"(?i)\b(warning|error|note):", "LINT_WARNING")?;
    let file = regex(r"^\S+\.(?:py|js|jsx|ts|tsx|rs|go):\d+", "LINT_FILE")?;
    let summary = regex(
        r"(?i)\b(found|checked|fixed|violations?|problems?)\b",
        "LINT_SUMMARY",
    )?;
    let diagnostics = dedup(
        lines
            .iter()
            .filter(|line| location.is_match(line) || warning.is_match(line) || file.is_match(line))
            .cloned(),
    );
    let summaries = dedup(lines.iter().filter(|line| summary.is_match(line)).cloned());
    let mut selected = diagnostics.into_iter().take(120).collect::<Vec<_>>();
    selected.extend(
        summaries[summaries.len().saturating_sub(20)..]
            .iter()
            .cloned(),
    );
    selected.extend(found_errors.iter().take(24).cloned());
    Ok((selected, found_errors.len()))
}

fn select_docker_build(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let matcher = regex(
        r"(?i)(^#\d+|exporting|writing image|naming to|cached|done|warning|error|failed)",
        "DOCKER_BUILD",
    )?;
    let selected = dedup(lines.iter().filter(|line| matcher.is_match(line)).cloned());
    let mut output = head_tail(&selected, 80, 30);
    output.extend(found_errors.iter().take(32).cloned());
    Ok((output, found_errors.len()))
}

fn select_table(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let nonempty = lines
        .iter()
        .filter(|line| !line.trim().is_empty())
        .cloned()
        .collect::<Vec<_>>();
    let mut output = head_tail(&nonempty, 35, 15);
    output.extend(found_errors.iter().take(24).cloned());
    Ok((output, found_errors.len()))
}

fn shrink_plugin(value: &Value) -> Value {
    match value {
        Value::Object(values) => {
            let ordered = values.iter().collect::<BTreeMap<_, _>>();
            let mut output = Map::new();
            for (key, value) in ordered.iter().take(30) {
                output.insert((*key).clone(), shrink_plugin(value));
            }
            if ordered.len() > 30 {
                output.insert("<omitted_keys>".to_owned(), json!(ordered.len() - 30));
            }
            Value::Object(output)
        }
        Value::Array(values) if values.len() > 10 => json!({
            "length": values.len(),
            "head": values.iter().take(6).map(shrink_plugin).collect::<Vec<_>>(),
            "tail": values[values.len().saturating_sub(2)..].iter().map(shrink_plugin).collect::<Vec<_>>(),
        }),
        Value::Array(values) => Value::Array(values.iter().map(shrink_plugin).collect()),
        Value::String(value) if value.chars().count() > 300 => {
            let chars = value.chars().collect::<Vec<_>>();
            Value::String(format!(
                "{}…<{} chars>…{}",
                chars[..180].iter().collect::<String>(),
                chars.len(),
                chars[chars.len() - 60..].iter().collect::<String>()
            ))
        }
        _ => value.clone(),
    }
}

fn select_json_or_table(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let text = lines.join("\n");
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return select_table(lines);
    };
    let rendered = serde_json::to_string_pretty(&shrink_plugin(&value))
        .map_err(|error| format!("FABRIC_COMPACT_JSON_PREVIEW_FAILED:{error}"))?;
    Ok((
        rendered.lines().map(str::to_owned).collect(),
        errors(lines)?.len(),
    ))
}

fn select_head_tail(lines: &[String]) -> Result<(Vec<String>, usize), String> {
    let found_errors = errors(lines)?;
    let nonempty = lines
        .iter()
        .filter(|line| !line.trim().is_empty())
        .cloned()
        .collect::<Vec<_>>();
    let mut output = head_tail(&nonempty, 30, 20);
    output.extend(found_errors.iter().take(32).cloned());
    Ok((output, found_errors.len()))
}

fn generic_preview(value: &Value, depth: usize) -> Value {
    if depth >= 4 {
        return match value {
            Value::Object(values) => Value::String(format!("<dict:{}>", values.len())),
            Value::Array(values) => Value::String(format!("<list:{}>", values.len())),
            _ => value.clone(),
        };
    }
    match value {
        Value::Object(values) => {
            let ordered = values.iter().collect::<BTreeMap<_, _>>();
            let mut output = Map::new();
            for (key, value) in ordered.iter().take(30) {
                output.insert((*key).clone(), generic_preview(value, depth + 1));
            }
            if ordered.len() > 30 {
                output.insert("<omitted_keys>".to_owned(), json!(ordered.len() - 30));
            }
            Value::Object(output)
        }
        Value::Array(values) if values.len() > 10 => json!({
            "<length>": values.len(),
            "<head>": values.iter().take(5).map(|value| generic_preview(value, depth + 1)).collect::<Vec<_>>(),
            "<tail>": values[values.len().saturating_sub(2)..].iter().map(|value| generic_preview(value, depth + 1)).collect::<Vec<_>>(),
        }),
        Value::Array(values) => Value::Array(
            values
                .iter()
                .map(|value| generic_preview(value, depth + 1))
                .collect(),
        ),
        Value::String(value) if value.chars().count() > 300 => {
            let chars = value.chars().collect::<Vec<_>>();
            Value::String(format!(
                "{}…<{} chars>…{}",
                chars[..180].iter().collect::<String>(),
                chars.len(),
                chars[chars.len() - 60..].iter().collect::<String>()
            ))
        }
        _ => value.clone(),
    }
}

fn json_preview(text: &str) -> Result<Option<Vec<String>>, String> {
    let Ok(value) = serde_json::from_str::<Value>(text) else {
        return Ok(None);
    };
    let rendered = serde_json::to_string_pretty(&generic_preview(&value, 0))
        .map_err(|error| format!("FABRIC_COMPACT_JSON_PREVIEW_FAILED:{error}"))?;
    Ok(Some(rendered.lines().map(str::to_owned).collect()))
}

fn select_generic(
    command: &[String],
    family: &str,
    text: &str,
) -> Result<(String, Vec<String>, usize), String> {
    let lines = text.lines().map(str::to_owned).collect::<Vec<_>>();
    if let Some((name, strategy)) = plugin(command) {
        let (selected, retained) = match strategy {
            Strategy::Test => select_test(&lines)?,
            Strategy::GitStatus => select_git_status(&lines)?,
            Strategy::GitDiff => select_git_diff(&lines)?,
            Strategy::GitLog => select_git_log(&lines)?,
            Strategy::Search => select_search(&lines)?,
            Strategy::Lint => select_lint(&lines)?,
            Strategy::DockerBuild => select_docker_build(&lines)?,
            Strategy::Table => select_table(&lines)?,
            Strategy::JsonOrTable => select_json_or_table(&lines)?,
            Strategy::HeadTail => select_head_tail(&lines)?,
        };
        return Ok((name.to_owned(), selected, retained));
    }
    let found_errors = errors(&lines)?;
    let name = format!("generic:{family}");
    if family == "test" {
        let (selected, retained) = select_test(&lines)?;
        return Ok((name, selected, retained));
    }
    if family == "git" {
        let mut selected = lines
            .iter()
            .filter(|line| {
                line.starts_with("On branch ")
                    || line.starts_with("Your branch ")
                    || line.starts_with("Changes ")
                    || line.starts_with("Untracked ")
                    || line.starts_with("diff --git")
                    || line.starts_with("@@ ")
                    || matches!(line.get(..2), Some(" M" | "M " | "A " | " D" | "D " | "??"))
                    || line.starts_with("index ")
                    || line.starts_with("--- ")
                    || line.starts_with("+++ ")
            })
            .cloned()
            .take(80)
            .collect::<Vec<_>>();
        let changes = lines
            .iter()
            .filter(|line| {
                (line.starts_with('+') || line.starts_with('-'))
                    && !line.starts_with("+++")
                    && !line.starts_with("---")
            })
            .cloned()
            .collect::<Vec<_>>();
        selected.extend(changes.iter().take(24).cloned());
        selected.extend(changes[changes.len().saturating_sub(12)..].iter().cloned());
        selected.extend(found_errors.iter().take(24).cloned());
        return Ok((name, selected, found_errors.len()));
    }
    if matches!(family, "search" | "read") {
        let (selected, retained) = select_search(&lines)?;
        return Ok((name, selected, retained));
    }
    if matches!(family, "package" | "cloud" | "container" | "network") {
        if let Some(preview) = json_preview(text)? {
            return Ok((name, preview, found_errors.len()));
        }
        let heading = regex(
            r"(?i)\b(name|version|status|state|image|id|resource|package|total|warning)\b",
            "HEADING",
        )?;
        let mut selected = found_errors.iter().take(32).cloned().collect::<Vec<_>>();
        selected.extend(
            lines
                .iter()
                .filter(|line| heading.is_match(line))
                .take(48)
                .cloned(),
        );
        selected.extend(lines.iter().take(20).cloned());
        selected.extend(lines[lines.len().saturating_sub(10)..].iter().cloned());
        return Ok((name, selected, found_errors.len()));
    }
    if matches!(family, "build" | "github") {
        let summary = regex(
            r"(?i)\b(success|succeeded|complete|completed|warning|changed files?|checks?)\b",
            "BUILD_SUMMARY",
        )?;
        let error = error_regex()?;
        let location = location_regex()?;
        let mut selected = dedup(
            lines
                .iter()
                .filter(|line| {
                    error.is_match(line) || location.is_match(line) || summary.is_match(line)
                })
                .cloned(),
        );
        selected.truncate(80);
        selected.extend(lines[lines.len().saturating_sub(20)..].iter().cloned());
        return Ok((name, selected, found_errors.len()));
    }
    if let Some(preview) = json_preview(text)? {
        return Ok((name, preview, found_errors.len()));
    }
    let mut selected = found_errors.iter().take(40).cloned().collect::<Vec<_>>();
    selected.extend(lines.iter().take(24).cloned());
    selected.extend(lines[lines.len().saturating_sub(12)..].iter().cloned());
    Ok((name, selected, found_errors.len()))
}

fn truncate_utf8(text: &str, budget: usize) -> &str {
    let mut end = budget.min(text.len());
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

fn bounded(text: &str, budget_bytes: usize) -> String {
    if text.len() <= budget_bytes {
        return text.to_owned();
    }
    let keep = budget_bytes.saturating_sub(BOUNDED_MARKER.len());
    format!("{}{}", truncate_utf8(text, keep).trim_end(), BOUNDED_MARKER)
}

fn compact(options: &Options, stdout: &str, stderr: &str) -> Result<Value, String> {
    if options.budget_bytes < 256 {
        return Err("budget_bytes must be at least 256".to_owned());
    }
    let family = family(&options.command);
    let mut combined = stdout.trim_end_matches('\n').to_owned();
    if !stderr.is_empty() {
        if combined.is_empty() {
            combined.push_str("[stderr]\n");
        } else {
            combined.push_str("\n[stderr]\n");
        }
        combined.push_str(stderr);
    }
    let security = scan_text(&combined, true)?;
    let (mut compactor, selected, retained_errors) =
        select_generic(&options.command, family, &security.redacted_text)?;
    let header = format!(
        "Syntavra compact family={family} compactor={compactor} lines={} secrets={} injection_risk={}",
        security.normalized_text.lines().count(),
        security.secret_types.len(),
        security.injection_risk
    );
    let selected = dedup(selected);
    let mut visible = bounded(
        &format!("{header}\n{}", selected.join("\n")),
        options.budget_bytes,
    );
    let original_bytes = combined.len();
    let mut visible_bytes = visible.len();
    let raw_visible_bytes = security.redacted_text.len();
    if raw_visible_bytes <= options.budget_bytes && visible_bytes >= raw_visible_bytes {
        visible = security.redacted_text.clone();
        visible_bytes = raw_visible_bytes;
        compactor.push_str(":never-worse-passthrough");
    }
    let savings_ratio = if original_bytes == 0 {
        0.0
    } else {
        (1.0 - visible_bytes as f64 / original_bytes as f64).max(0.0)
    };
    Ok(json!({
        "family": family,
        "visible_text": visible,
        "original_bytes": original_bytes,
        "visible_bytes": visible_bytes,
        "savings_ratio": savings_ratio,
        "exact_required": visible_bytes < original_bytes,
        "secret_types": security.secret_types,
        "injection_risk": security.injection_risk,
        "injection_reasons": security.injection_reasons,
        "retained_error_lines": retained_errors,
        "compactor": compactor,
    }))
}

fn ensure_schema(state_root: &Path) -> Result<Connection, String> {
    fs::create_dir_all(state_root)
        .map_err(|error| format!("FABRIC_STATE_CREATE_FAILED:{error}"))?;
    let connection = Connection::open(state_root.join("competitive-fabric.sqlite3"))
        .map_err(|error| format!("FABRIC_DATABASE_OPEN_FAILED:{error}"))?;
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS fabric_events(\
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,\
                event_type TEXT NOT NULL,\
                family TEXT NOT NULL,\
                host TEXT NOT NULL,\
                raw_bytes INTEGER NOT NULL,\
                visible_bytes INTEGER NOT NULL,\
                latency_ms REAL NOT NULL,\
                success INTEGER NOT NULL,\
                cache_hit INTEGER NOT NULL,\
                metadata_json TEXT NOT NULL,\
                created_at REAL NOT NULL\
            );\
            CREATE INDEX IF NOT EXISTS fabric_event_type_idx \
                ON fabric_events(event_type,created_at);\
            CREATE INDEX IF NOT EXISTS fabric_family_idx \
                ON fabric_events(family,created_at);",
        )
        .map_err(|error| format!("FABRIC_DATABASE_SCHEMA_FAILED:{error}"))?;
    Ok(connection)
}

fn record_event(
    connection: &Connection,
    host: &str,
    value: &Value,
    latency_ms: f64,
) -> Result<(), String> {
    let family = value["family"].as_str().unwrap_or("generic");
    let raw_bytes = value["original_bytes"].as_i64().unwrap_or(0);
    let visible_bytes = value["visible_bytes"].as_i64().unwrap_or(0);
    let injection = value["injection_risk"].as_bool().unwrap_or(false);
    let compactor = value["compactor"].as_str().unwrap_or("generic");
    let secrets = serde_json::to_string(&value["secret_types"])
        .map_err(|error| format!("FABRIC_METADATA_SERIALIZE_FAILED:{error}"))?;
    let metadata = format!(
        "{{\"compactor\": {}, \"injection_risk\": {}, \"secrets\": {secrets}}}",
        serde_json::to_string(compactor)
            .map_err(|error| format!("FABRIC_METADATA_SERIALIZE_FAILED:{error}"))?,
        if injection { "true" } else { "false" },
    );
    let created_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("FABRIC_CLOCK_FAILED:{error}"))?
        .as_secs_f64();
    connection
        .execute(
            "INSERT INTO fabric_events(\
                event_type,family,host,raw_bytes,visible_bytes,latency_ms,\
                success,cache_hit,metadata_json,created_at\
            ) VALUES(?,?,?,?,?,?,?,?,?,?)",
            params![
                "compact",
                family,
                host,
                raw_bytes,
                visible_bytes,
                latency_ms.max(0.0),
                1_i64,
                0_i64,
                metadata,
                created_at,
            ],
        )
        .map_err(|error| format!("FABRIC_EVENT_INSERT_FAILED:{error}"))?;
    Ok(())
}

fn write_output(path: &Path, value: &Value) -> Result<Value, String> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("FABRIC_OUTPUT_PARENT_FAILED:{error}"))?;
        }
    }
    let rendered = serde_json::to_string_pretty(value)
        .map_err(|error| format!("FABRIC_OUTPUT_SERIALIZE_FAILED:{error}"))?
        + "\n";
    fs::write(path, rendered.as_bytes())
        .map_err(|error| format!("FABRIC_OUTPUT_WRITE_FAILED:{error}"))?;
    Ok(json!({
        "ok": true,
        "output": path.to_string_lossy(),
        "bytes": rendered.len(),
    }))
}

pub fn execute(arguments: &[String], state_root: &Path) -> Result<Value, String> {
    let options = parse_options(arguments)?;
    let stdout = read_text(options.stdout_file.as_deref(), &options.stdout, "STDOUT")?;
    let stderr = read_text(options.stderr_file.as_deref(), &options.stderr, "STDERR")?;
    let connection = ensure_schema(state_root)?;
    let host = option_value(arguments, "--host").unwrap_or_else(|| "codex".to_owned());
    let started = Instant::now();
    let value = compact(&options, &stdout, &stderr)?;
    record_event(
        &connection,
        &host,
        &value,
        started.elapsed().as_secs_f64() * 1000.0,
    )?;
    options
        .output
        .as_deref()
        .map_or_else(|| Ok(value.clone()), |path| write_output(path, &value))
}

#[cfg(test)]
mod tests {
    use super::{compact, Options};

    fn options(command: &[&str]) -> Options {
        Options {
            stdout_file: None,
            stderr_file: None,
            stdout: String::new(),
            stderr: String::new(),
            budget_bytes: 4096,
            output: None,
            command: command.iter().map(|value| (*value).to_owned()).collect(),
        }
    }

    #[test]
    fn never_worse_passes_small_output_through() {
        let value = compact(&options(&["pytest", "-q"]), "1 passed\n", "").unwrap();
        assert_eq!(value["visible_text"], "1 passed");
        assert!(value["compactor"]
            .as_str()
            .unwrap()
            .ends_with(":never-worse-passthrough"));
    }

    #[test]
    fn rejects_tiny_budget() {
        let mut value = options(&["pytest", "-q"]);
        value.budget_bytes = 255;
        assert_eq!(
            compact(&value, "", "").unwrap_err(),
            "budget_bytes must be at least 256"
        );
    }
}
