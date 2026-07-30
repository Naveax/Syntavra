from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "crates" / "syntavra-cli" / "src" / "main.rs"
text = path.read_text(encoding="utf-8")
old = '''        Command::PrimitiveNormalizePath(path) => {
            let normalized =
                normalize_repository_path(&path).map_err(|error| error.code().to_owned())?;
            println!("{{\\"path\\":{}}}", json_string(&normalized));
        }
        Command::Help => print!("{USAGE}"),
'''
new = '''        Command::PrimitiveNormalizePath(path) => {
            let normalized =
                normalize_repository_path(&path).map_err(|error| error.code().to_owned())?;
            println!("{{\\"path\\":{}}}", json_string(&normalized));
        }
        Command::SchedulerStats { .. } | Command::SchedulerList { .. } => {
            unreachable!("scheduler commands are handled before the main match")
        }
        Command::Help => print!("{USAGE}"),
'''
if new not in text:
    if text.count(old) != 1:
        raise SystemExit(f"expected one main match tail, found {text.count(old)}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8", newline="\n")
print("scheduler exhaustive arm added")
