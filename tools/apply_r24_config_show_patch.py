#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one config show patch anchor, found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


# Native Rust CLI command and parser coverage.
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '    "  syntavra-rs config resolve <config-wire-hex>\\n",\n',
    '    "  syntavra-rs config resolve <config-wire-hex>\\n",\n'
    '    "  syntavra-rs config show <config-wire-hex>\\n",\n',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    "    ConfigResolve(String),\n",
    "    ConfigResolve(String),\n    ConfigShow(String),\n",
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        [config, action, wire] if config == "config" && action == "resolve" => {
            Ok(Command::ConfigResolve(wire.clone()))
        }
''',
    '''        [config, action, wire] if config == "config" && action == "resolve" => {
            Ok(Command::ConfigResolve(wire.clone()))
        }
        [config, action, wire] if config == "config" && action == "show" => {
            Ok(Command::ConfigShow(wire.clone()))
        }
''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        Command::ConfigResolve(encoded) => {
            let wire = decode_hex(&encoded)?;
            let snapshot = resolve_config_wire(&wire)?;
            println!("{}", snapshot_json(&snapshot)?);
        }
''',
    '''        Command::ConfigResolve(encoded) => {
            let wire = decode_hex(&encoded)?;
            let snapshot = resolve_config_wire(&wire)?;
            println!("{}", snapshot_json(&snapshot)?);
        }
        Command::ConfigShow(encoded) => {
            let wire = decode_hex(&encoded)?;
            let snapshot = resolve_config_wire(&wire)?;
            println!("{}", snapshot_json(&snapshot)?);
        }
''',
)
replace_once(
    "crates/syntavra-cli/src/main.rs",
    '''        assert_eq!(
            parse_command(&args(&["config", "resolve", "00"])),
            Ok(Command::ConfigResolve("00".to_owned()))
        );
''',
    '''        assert_eq!(
            parse_command(&args(&["config", "resolve", "00"])),
            Ok(Command::ConfigResolve("00".to_owned()))
        );
        assert_eq!(
            parse_command(&args(&["config", "show", "00"])),
            Ok(Command::ConfigShow("00".to_owned()))
        );
''',
)

# Rust capability contract and deterministic descriptor.
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '''    Capability {
        name: "config.resolve",
        maturity: "preview",
        mutation: "read-only",
    },
''',
    '''    Capability {
        name: "config.resolve",
        maturity: "preview",
        mutation: "read-only",
    },
    Capability {
        name: "config.show",
        maturity: "preview",
        mutation: "read-only",
    },
''',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '    "capability=config.resolve|preview|read-only\\n",\n',
    '    "capability=config.resolve|preview|read-only\\n",\n'
    '    "capability=config.show|preview|read-only\\n",\n',
)
replace_once(
    "crates/syntavra-contracts/src/lib.rs",
    '        assert!(capabilities_json().contains("\\"name\\":\\"config.resolve\\""));\n',
    '        assert!(capabilities_json().contains("\\"name\\":\\"config.resolve\\""));\n'
    '        assert!(capabilities_json().contains("\\"name\\":\\"config.show\\""));\n',
)
replace_once(
    "contracts/engine/descriptor.txt",
    "capability=config.resolve|preview|read-only\n",
    "capability=config.resolve|preview|read-only\n"
    "capability=config.show|preview|read-only\n",
)
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "config.resolve",\n',
    '    "config.resolve",\n    "config.show",\n',
)
replace_once(
    "syntavra_runtime/engine_selector.py",
    '    "capability=config.resolve|preview|read-only\\n"\n',
    '    "capability=config.resolve|preview|read-only\\n"\n'
    '    "capability=config.show|preview|read-only\\n"\n',
)
replace_once(
    "tests/runtime/test_engine_selector_r4.py",
    '                    "config.resolve",\n',
    '                    "config.resolve",\n                    "config.show",\n',
)
replace_once(
    "tests/runtime/test_engine_selector_r4.py",
    '        "config.resolve",\n',
    '        "config.resolve",\n        "config.show",\n',
)
replace_once(
    "tools/run_engine_parity.py",
    '        "config.resolve",\n',
    '        "config.resolve",\n        "config.show",\n',
)

# Direct Python core CLI must use the same state-free canonical projection.
replace_once(
    "syntavra_runtime/unified_cli.py",
    "from .unified_config import ConfigManager\n",
    "from .config_contract import resolve_config_wire\n"
    "from .config_show_contract import show_result\n"
    "from .live_config_discovery import discover_live_config_wire\n"
    "from .unified_config import ConfigManager\n",
)
replace_once(
    "syntavra_runtime/unified_cli.py",
    '''    if args.command == "plugins":
        _emit(plugin_inventory())
        return 0

    evidence = EvidenceStore(state / "evidence", project_id=project_id)
''',
    '''    if args.command == "plugins":
        _emit(plugin_inventory())
        return 0
    if args.command == "config" and args.action == "show":
        wire = discover_live_config_wire(project_root=project)
        _emit(show_result(resolve_config_wire(wire)))
        return 0

    evidence = EvidenceStore(state / "evidence", project_id=project_id)
''',
)

# Regression for direct unified_cli invocation, not only engine_entry routing.
replace_once(
    "tests/runtime/test_config_show_r24.py",
    "from syntavra_runtime.engine_entry import main as engine_main\n",
    "from syntavra_runtime.engine_entry import main as engine_main\n"
    "from syntavra_runtime.unified_cli import main as unified_main\n",
)
replace_once(
    "tests/runtime/test_config_show_r24.py",
    '''    assert not state.exists()
    assert not (project / ".syntavra" / "config-last-good.json").exists()


def test_auto_runs_native_rust_config_show(tmp_path: Path) -> None:
''',
    '''    assert not state.exists()
    assert not (project / ".syntavra" / "config-last-good.json").exists()


def test_direct_python_core_config_show_is_state_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(project, '[runtime]\\nprofile = "audit"\\n')
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in tuple(os.environ):
        if name.startswith("SYNTAVRA_CFG__"):
            monkeypatch.delenv(name, raising=False)

    state = project / ".syntavra" / "pre-release"
    assert unified_main(
        [
            "--project",
            str(project),
            "--state-root",
            str(state),
            "config",
            "show",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["runtime"]["profile"] == "audit"
    assert "loaded_at" not in result
    assert not state.exists()
    assert not (project / ".syntavra" / "config-last-good.json").exists()


def test_auto_runs_native_rust_config_show(tmp_path: Path) -> None:
''',
)

# Full parity catalog and aggregate R0-R24 gate.
replace_once(
    "contracts/parity/python-rust-full-parity-v1.json",
    '{"id":"route.config.explain","target_phase":"R24","category":"cli","python_owner":"syntavra_runtime/config_explain_contract.py","rust_owner":"crates/syntavra-cli/src/config_contract.rs","status":"PARITY_PROVEN","mutation":"read-only","contract":"contracts/cli/config-explain-v1.json","parity_tests":["tools/verify_r24_config_explain.py"]},\n',
    '{"id":"route.config.explain","target_phase":"R24","category":"cli","python_owner":"syntavra_runtime/config_explain_contract.py","rust_owner":"crates/syntavra-cli/src/config_contract.rs","status":"PARITY_PROVEN","mutation":"read-only","contract":"contracts/cli/config-explain-v1.json","parity_tests":["tools/verify_r24_config_explain.py"]},\n'
    '    {"id":"route.config.show","target_phase":"R24","category":"cli","python_owner":"syntavra_runtime/config_show_contract.py","rust_owner":"crates/syntavra-cli/src/config_contract.rs","status":"PARITY_PROVEN","mutation":"read-only","contract":"contracts/cli/config-show-v1.json","parity_tests":["tools/verify_r24_config_show.py"]},\n',
)
replace_once(
    "tools/run_engine_parity_r24.py",
    "from verify_r24_config_explain import verify as verify_r24_config_explain\n",
    "from verify_r24_config_explain import verify as verify_r24_config_explain\n"
    "from verify_r24_config_show import verify as verify_r24_config_show\n",
)
replace_once(
    "tools/run_engine_parity_r24.py",
    "    config_explain = verify_r24_config_explain()\n",
    "    config_explain = verify_r24_config_explain()\n"
    "    config_show = verify_r24_config_show()\n",
)
replace_once(
    "tools/run_engine_parity_r24.py",
    '''    if (
        config_explain.get("ok") is not True
        or config_explain.get("phase") != "R24"
        or config_explain.get("command") != "config.explain"
        or config_explain.get("capability") != "config.explain"
    ):
        raise RuntimeError("R24 config.explain parity regression")
''',
    '''    if (
        config_explain.get("ok") is not True
        or config_explain.get("phase") != "R24"
        or config_explain.get("command") != "config.explain"
        or config_explain.get("capability") != "config.explain"
    ):
        raise RuntimeError("R24 config.explain parity regression")
    if (
        config_show.get("ok") is not True
        or config_show.get("phase") != "R24"
        or config_show.get("command") != "config.show"
        or config_show.get("capability") != "config.show"
    ):
        raise RuntimeError("R24 config.show parity regression")
''',
)
replace_once(
    "tools/run_engine_parity_r24.py",
    '        "config_explain": config_explain,\n',
    '        "config_explain": config_explain,\n        "config_show": config_show,\n',
)
