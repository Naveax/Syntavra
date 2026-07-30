from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "crates" / "syntavra-cli" / "src" / "main.rs"
text = path.read_text(encoding="utf-8")

anchor = '''fn run(command: Command) -> Result<(), String> {
    if run_scheduler(&command)? {
        return Ok(());
    }
    match command {
'''
replacement = '''fn run_primitive(command: &Command) -> Result<bool, String> {
    match command {
        Command::PrimitiveSha256(input_hex) => {
            let input = decode_hex(input_hex)?;
            println!(
                "{{\\"algorithm\\":\\"sha256\\",\\"digest\\":\\"{}\\",\\"input_hex\\":\\"{}\\"}}",
                sha256_hex(&input),
                bytes_to_hex(&input)
            );
            Ok(true)
        }
        Command::PrimitiveCanonicalize { path, input_hex } => {
            let input = decode_hex(input_hex)?;
            let normalized =
                normalize_repository_path(path).map_err(|error| error.code().to_owned())?;
            let canonical = canonical_manifest_bytes(&normalized, &input)
                .map_err(|error| error.code().to_owned())?;
            println!(
                concat!(
                    "{{\\"path\\":{},",
                    "\\"canonical_hex\\":\\"{}\\",",
                    "\\"digest\\":\\"{}\\"}}"
                ),
                json_string(&normalized),
                bytes_to_hex(&canonical),
                sha256_hex(&canonical)
            );
            Ok(true)
        }
        Command::PrimitiveManifestDigest { path, input_hex } => {
            let input = decode_hex(input_hex)?;
            let normalized =
                normalize_repository_path(path).map_err(|error| error.code().to_owned())?;
            let digest = manifest_digest_hex(&normalized, &input)
                .map_err(|error| error.code().to_owned())?;
            println!(
                "{{\\"path\\":{},\\"algorithm\\":\\"sha256\\",\\"digest\\":\\"{}\\"}}",
                json_string(&normalized),
                digest
            );
            Ok(true)
        }
        Command::PrimitiveNormalizePath(path) => {
            let normalized =
                normalize_repository_path(path).map_err(|error| error.code().to_owned())?;
            println!("{{\\"path\\":{}}}", json_string(&normalized));
            Ok(true)
        }
        _ => Ok(false),
    }
}

fn run(command: Command) -> Result<(), String> {
    if run_scheduler(&command)? || run_primitive(&command)? {
        return Ok(());
    }
    match command {
'''
if replacement not in text:
    if text.count(anchor) != 1:
        raise SystemExit(f"expected one run anchor, found {text.count(anchor)}")
    text = text.replace(anchor, replacement, 1)

primitive_arms = '''        Command::PrimitiveSha256(input_hex) => {
            let input = decode_hex(&input_hex)?;
            println!(
                "{{\\"algorithm\\":\\"sha256\\",\\"digest\\":\\"{}\\",\\"input_hex\\":\\"{}\\"}}",
                sha256_hex(&input),
                bytes_to_hex(&input)
            );
        }
        Command::PrimitiveCanonicalize { path, input_hex } => {
            let input = decode_hex(&input_hex)?;
            let normalized =
                normalize_repository_path(&path).map_err(|error| error.code().to_owned())?;
            let canonical = canonical_manifest_bytes(&normalized, &input)
                .map_err(|error| error.code().to_owned())?;
            println!(
                concat!(
                    "{{\\"path\\":{},",
                    "\\"canonical_hex\\":\\"{}\\",",
                    "\\"digest\\":\\"{}\\"}}"
                ),
                json_string(&normalized),
                bytes_to_hex(&canonical),
                sha256_hex(&canonical)
            );
        }
        Command::PrimitiveManifestDigest { path, input_hex } => {
            let input = decode_hex(&input_hex)?;
            let normalized =
                normalize_repository_path(&path).map_err(|error| error.code().to_owned())?;
            let digest = manifest_digest_hex(&normalized, &input)
                .map_err(|error| error.code().to_owned())?;
            println!(
                "{{\\"path\\":{},\\"algorithm\\":\\"sha256\\",\\"digest\\":\\"{}\\"}}",
                json_string(&normalized),
                digest
            );
        }
        Command::PrimitiveNormalizePath(path) => {
            let normalized =
                normalize_repository_path(&path).map_err(|error| error.code().to_owned())?;
            println!("{{\\"path\\":{}}}", json_string(&normalized));
        }
'''
if primitive_arms in text:
    text = text.replace(primitive_arms, "", 1)
elif "fn run_primitive(command: &Command)" not in text:
    raise SystemExit("primitive command arms were not found")

scheduler_tail = '''        Command::SchedulerStats { .. } | Command::SchedulerList { .. } => {
            unreachable!("scheduler commands are handled before the main match")
        }
'''
all_tail = '''        Command::SchedulerStats { .. }
        | Command::SchedulerList { .. }
        | Command::PrimitiveSha256(_)
        | Command::PrimitiveCanonicalize { .. }
        | Command::PrimitiveManifestDigest { .. }
        | Command::PrimitiveNormalizePath(_) => {
            unreachable!("pre-dispatched commands are handled before the main match")
        }
'''
if all_tail not in text:
    if text.count(scheduler_tail) != 1:
        raise SystemExit(f"expected one exhaustive tail, found {text.count(scheduler_tail)}")
    text = text.replace(scheduler_tail, all_tail, 1)

path.write_text(text, encoding="utf-8", newline="\n")
print("primitive command runner extracted")
