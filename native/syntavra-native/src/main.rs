use std::{env, fs, path::PathBuf};

const VERSION: &str = "0.0.1";
const MODES: [&str; 6] = ["full", "lite", "ultra", "commit", "review", "compress"];

fn state_root() -> PathBuf {
    env::var_os("SYNTAVRA_STATE_ROOT").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(".syntavra/pre-release"))
}
fn mode_path() -> PathBuf { state_root().join("optimization-mode.native") }
fn current_mode() -> String {
    fs::read_to_string(mode_path()).ok().map(|v| v.trim().to_string()).filter(|v| MODES.contains(&v.as_str())).unwrap_or_else(|| "full".into())
}
fn set_mode(value: &str) -> Result<(), String> {
    if !MODES.contains(&value) { return Err(format!("unknown mode: {value}")); }
    let path = mode_path();
    if let Some(parent) = path.parent() { fs::create_dir_all(parent).map_err(|e| e.to_string())?; }
    fs::write(path, format!("{value}\n")).map_err(|e| e.to_string())
}
fn saved_tokens() -> u64 {
    let path = state_root().join("analytics/savings.jsonl");
    let Ok(text) = fs::read_to_string(path) else { return 0 };
    text.lines().filter_map(|line| {
        let key = "\"saved_tokens\":";
        let start = line.find(key)? + key.len();
        let digits: String = line[start..].chars().skip_while(|c| c.is_whitespace()).take_while(|c| c.is_ascii_digit()).collect();
        digits.parse::<u64>().ok()
    }).sum()
}
fn short_number(value: u64) -> String {
    if value >= 1_000_000 { format!("{:.1}m", value as f64 / 1_000_000.0) }
    else if value >= 1_000 { format!("{:.1}k", value as f64 / 1_000.0) }
    else { value.to_string() }
}
fn rewrite(args: &[String]) -> Vec<String> {
    if args.len() == 2 && args[0] == "git" && args[1] == "status" { return vec!["git".into(), "status".into(), "--porcelain=v2".into(), "--branch".into()]; }
    if args.first().map(String::as_str) == Some("rg") && !args.iter().any(|v| v.starts_with("--color")) {
        let mut out=args.to_vec(); out.extend(["--no-heading".into(),"--line-number".into(),"--color=never".into()]); return out;
    }
    args.to_vec()
}
fn json_array(values: &[String]) -> String { format!("[{}]", values.iter().map(|v| format!("\"{}\"", v.replace('\\', "\\\\").replace('"', "\\\""))).collect::<Vec<_>>().join(",")) }
fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("version") => format!("{{\"version\":\"{VERSION}\",\"channel\":\"pre-release\"}}"),
        Some("mode") if args.len() == 1 => format!("{{\"mode\":\"{}\"}}", current_mode()),
        Some("mode") => match set_mode(&args[1]) { Ok(()) => format!("{{\"mode\":\"{}\",\"updated\":true}}", args[1]), Err(e) => { eprintln!("{e}"); std::process::exit(2); } },
        Some("statusline") => format!("[SYN:{}] ⇩{}", current_mode().to_uppercase(), short_number(saved_tokens())),
        Some("rewrite") => { let original=&args[1..]; let changed=rewrite(original); format!("{{\"original\":{},\"rewritten\":{},\"changed\":{}}}", json_array(original), json_array(&changed), original != changed) },
        _ => { eprintln!("usage: syntavra-native <version|mode [name]|statusline|rewrite command...>"); std::process::exit(2); }
    };
    println!("{result}");
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rewrites_git_status() {
        let input = vec!["git".to_string(), "status".to_string()];
        let output = rewrite(&input);
        assert!(output.iter().any(|item| item == "--porcelain=v2"));
    }

    #[test]
    fn preserves_unknown_command() {
        let input = vec!["cargo".to_string(), "check".to_string()];
        assert_eq!(rewrite(&input), input);
    }
}
