#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise RuntimeError(f"unexpected match count in {path}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


replace("crates/syntavra-cli/Cargo.toml", 'default-run = "syntavra"', 'default-run = "syntavra-rs"')
selector = "crates/syntavra-cli/src/bin/syntavra.rs"
replace(selector, "const NATIVE_COMMAND_COUNT: u64 = 11;", "const NATIVE_COMMAND_COUNT: u64 = 12;")
replace(
    selector,
    '''fn python_program() -> Option<Program> {
    if let Some(value) = env::var_os("SYNTAVRA_PYTHON") {
        let program = Program {
            executable: PathBuf::from(value),
            prefix: Vec::new(),
        };
        if program_works(&program, &["--version"]) {
            return Some(program);
        }
    }
    let candidates: &[(&str, &[&str])] = if cfg!(windows) {
        &[("py", &["-3"]), ("python", &[])]
    } else {
        &[("python3", &[]), ("python", &[])]
    };
    for (executable, prefix) in candidates {
        let program = Program {
            executable: PathBuf::from(executable),
            prefix: prefix.iter().map(|value| (*value).to_owned()).collect(),
        };
        if program_works(&program, &["--version"]) {
            return Some(program);
        }
    }
    None
}
''',
    '''fn python_at_root(root: &Path, virtual_environment: bool) -> PathBuf {
    if cfg!(windows) {
        if virtual_environment {
            root.join("Scripts").join("python.exe")
        } else {
            root.join("python.exe")
        }
    } else {
        root.join("bin").join("python")
    }
}

fn checked_python(executable: PathBuf, prefix: Vec<String>) -> Option<Program> {
    let program = Program { executable, prefix };
    program_works(&program, &["--version"]).then_some(program)
}

fn python_program() -> Option<Program> {
    if let Some(value) = env::var_os("SYNTAVRA_PYTHON") {
        if let Some(program) = checked_python(PathBuf::from(value), Vec::new()) {
            return Some(program);
        }
    }
    if let Some(value) = env::var_os("VIRTUAL_ENV") {
        if let Some(program) = checked_python(python_at_root(&PathBuf::from(value), true), Vec::new()) {
            return Some(program);
        }
    }
    for variable in ["pythonLocation", "Python3_ROOT_DIR", "Python_ROOT_DIR"] {
        if let Some(value) = env::var_os(variable) {
            if let Some(program) = checked_python(python_at_root(&PathBuf::from(value), false), Vec::new()) {
                return Some(program);
            }
        }
    }
    let candidates: &[(&str, &[&str])] = if cfg!(windows) {
        &[("python", &[]), ("py", &["-3"])]
    } else {
        &[("python3", &[]), ("python", &[])]
    };
    candidates.iter().find_map(|(executable, prefix)| {
        checked_python(
            PathBuf::from(executable),
            prefix.iter().map(|value| (*value).to_owned()).collect(),
        )
    })
}
''',
)
replace(
    selector,
    '''fn translate_rust(parsed: &Parsed) -> Result<Option<Vec<String>>, String> {
''',
    '''fn required_integer_flag(arguments: &[String], flag: &str, label: &str) -> Result<i64, String> {
    let mut found = None;
    let mut index = 0usize;
    while index < arguments.len() {
        let item = &arguments[index];
        let raw = if item == flag {
            index += 1;
            Some(arguments.get(index).ok_or_else(|| format!("CACHE_AMORTIZE_{label}_MISSING"))?.as_str())
        } else {
            item.strip_prefix(flag).and_then(|suffix| suffix.strip_prefix('='))
        };
        if let Some(value) = raw {
            if found.is_some() {
                return Err(format!("CACHE_AMORTIZE_{label}_DUPLICATE"));
            }
            found = Some(value.parse::<i64>().map_err(|_| format!("CACHE_AMORTIZE_{label}_INVALID"))?);
        }
        index += 1;
    }
    found.ok_or_else(|| format!("CACHE_AMORTIZE_{label}_MISSING"))
}

fn has_direct_rust_route(parsed: &Parsed) -> bool {
    command_tokens(&parsed.forwarded) == ["run", "cache-amortize"]
}

fn direct_rust_result(parsed: &Parsed) -> Result<Option<Value>, String> {
    if !has_direct_rust_route(parsed) {
        return Ok(None);
    }
    let write = required_integer_flag(&parsed.forwarded, "--write", "WRITE")?;
    let read = required_integer_flag(&parsed.forwarded, "--read", "READ")?;
    let uncached = required_integer_flag(&parsed.forwarded, "--uncached", "UNCACHED")?;
    let requests = required_integer_flag(&parsed.forwarded, "--requests", "REQUESTS")?.max(1);
    let baseline = uncached as f64 * requests as f64;
    let optimized = write as f64 * 1.25 + read as f64 * 0.1 * (requests - 1).max(0) as f64;
    let saved = (baseline - optimized).max(0.0);
    let savings_ratio = if baseline == 0.0 { 0.0 } else { ((baseline - optimized) / baseline).max(0.0) };
    let denominator = (uncached as f64 - read as f64 * 0.1).max(1.0);
    Ok(Some(json!({
        "baseline_equivalent": baseline,
        "optimized_equivalent": optimized,
        "saved_equivalent": saved,
        "savings_ratio": savings_ratio,
        "break_even_requests": write as f64 * 1.25 / denominator,
    })))
}

fn translate_rust(parsed: &Parsed) -> Result<Option<Vec<String>>, String> {
''',
)
replace(
    selector,
    '''fn run_rust(parsed: &Parsed) -> Result<ExitCode, String> {
    let arguments = translate_rust(parsed)?.ok_or_else(|| {
''',
    '''fn run_rust(parsed: &Parsed) -> Result<ExitCode, String> {
    if let Some(value) = direct_rust_result(parsed)? {
        emit(&value);
        return Ok(ExitCode::SUCCESS);
    }
    let arguments = translate_rust(parsed)?.ok_or_else(|| {
''',
)
replace(
    selector,
    '''    let resolved = if requested == Engine::Auto {
        match translate_rust(&parsed) {
            Ok(Some(_)) if program_works(&rust_program(), &["version"]) => Engine::Rust,
            Ok(_) => Engine::Python,
            Err(code) => return fail(&code, "auto routing failed", json!({})),
        }
    } else {
        requested
    };
''',
    '''    let resolved = if requested == Engine::Auto {
        if has_direct_rust_route(&parsed) {
            Engine::Rust
        } else {
            match translate_rust(&parsed) {
                Ok(Some(_)) if program_works(&rust_program(), &["version"]) => Engine::Rust,
                Ok(_) => Engine::Python,
                Err(code) => return fail(&code, "auto routing failed", json!({})),
            }
        }
    } else {
        requested
    };
''',
)
replace(selector, 'use super::{command_tokens, parse_arguments, translate_rust, Engine};', 'use super::{command_tokens, direct_rust_result, parse_arguments, translate_rust, Engine};')
replace(
    selector,
    '''    #[test]
    fn unsupported_rust_command_is_not_silently_fallbacked() {
''',
    '''    #[test]
    fn cache_amortization_is_a_native_exact_route() {
        let parsed = parse_arguments(&values(&[
            "run", "cache-amortize", "--write", "100", "--read", "0",
            "--uncached", "1000", "--requests", "1",
        ])).expect("arguments");
        let result = direct_rust_result(&parsed).expect("execution").expect("result");
        assert_eq!(result["baseline_equivalent"].as_f64(), Some(1000.0));
        assert_eq!(result["optimized_equivalent"].as_f64(), Some(125.0));
        assert_eq!(result["saved_equivalent"].as_f64(), Some(875.0));
        assert_eq!(result["savings_ratio"].as_f64(), Some(0.875));
        assert_eq!(result["break_even_requests"].as_f64(), Some(0.125));
    }

    #[test]
    fn unsupported_rust_command_is_not_silently_fallbacked() {
''',
)

contract_path = ROOT / "contracts/engine/dual-engine-public-surface-v2.json"
contract = json.loads(contract_path.read_text(encoding="utf-8"))
rust = contract["rust_surface"]
commands = sorted([*rust["native_public_commands"], "run cache-amortize"])
rust["native_public_commands"] = commands
rust["native_public_command_count"] = len(commands)
rust["missing_native_public_command_count"] = contract["python_surface"]["public_command_count"] - len(commands)
rust["native_coverage_ppm"] = len(commands) * 1_000_000 // contract["python_surface"]["public_command_count"]
contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
replace(
    "tests/runtime/test_dual_engine_public_surface_r38.py",
    'assert result["rust"]["native_public_command_count"] == 11\n    assert result["rust"]["missing_native_public_command_count"] == 246',
    'assert result["rust"]["native_public_command_count"] == 12\n    assert result["rust"]["missing_native_public_command_count"] == 245',
)
print("R38 native selector slice applied")
