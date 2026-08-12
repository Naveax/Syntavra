#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Iterable

from syntavra_runtime.util import canonical_json
from tools import report_missing_native_public_routes as public_surface
from tools import report_python_public_execution_contract as execution_contract

CONTRACT_RELATIVE = Path("contracts/python/core-legacy-route-reference-v1.json")
CREDENTIAL_ENV = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "OPENROUTER_API_KEY",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "PYPI_TOKEN",
    "TWINE_PASSWORD",
    "TWINE_USERNAME",
    "GITHUB_TOKEN",
    "GH_TOKEN",
}
SAFE_BOOLEAN_DESTS = {"once", "dry_run", "no_wait", "non_interactive"}
SAFE_BOUNDED_VALUE_DESTS = {
    "limit",
    "timeout",
    "timeout_seconds",
    "max_events",
    "max_items",
    "max_results",
    "max_iterations",
    "iterations",
    "count",
}
DYNAMIC_PATH_PART = re.compile(
    r"(?:[0-9a-f]{64}|[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f-]{27,}|\d{10,})",
    re.IGNORECASE,
)


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_status(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git status failed: {proc.stderr.strip()}")
    return proc.stdout


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _all_strings(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _all_strings(child)


def _literal_string_collection(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        values.append(item.value)
    return values


def _route_literals_from_certifier(path: Path, canonical: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not any("ROUTE" in name.upper() for name in names):
            continue
        value = node.value
        literals = _literal_string_collection(value) if value is not None else None
        if literals is None:
            continue
        routes.update(item for item in literals if item in canonical)
    return routes


def _dp_explicit_routes(repo: Path, canonical: set[str]) -> tuple[set[str], list[dict[str, Any]]]:
    catalog_path = repo / "contracts/python/fixture-golden-catalog-v1.json"
    catalog = _read_json(catalog_path)
    families = list(catalog.get("families") or [])
    routes: set[str] = set()
    rows: list[dict[str, Any]] = []
    for row in families:
        if not isinstance(row, dict):
            raise AssertionError(f"invalid fixture catalog family row: {row!r}")
        family = str(row.get("family") or "")
        certifier = repo / str(row.get("certifier") or "")
        if not family or not certifier.is_file():
            raise AssertionError(f"invalid fixture catalog certifier row: {row!r}")
        found = {item for item in _all_strings(row) if item in canonical}
        found.update(_route_literals_from_certifier(certifier, canonical))
        contract_path = row.get("contract")
        if contract_path:
            static_contract = _read_json(repo / str(contract_path))
            found.update(item for item in _all_strings(static_contract) if item in canonical)
        routes.update(found)
        rows.append({"family": family, "route_count": len(found), "routes": sorted(found)})
    return routes, rows


def _parser_leaf_index() -> dict[tuple[str, str], tuple[argparse.ArgumentParser, tuple[argparse.ArgumentParser, ...]]]:
    index: dict[tuple[str, str], tuple[argparse.ArgumentParser, tuple[argparse.ArgumentParser, ...]]] = {}

    def visit(
        source: str,
        parser: argparse.ArgumentParser,
        *,
        prefix: tuple[str, ...],
        lineage: tuple[argparse.ArgumentParser, ...],
        skip_top_level: frozenset[str],
    ) -> None:
        active_lineage = (*lineage, parser)
        sub_actions = public_surface._subparsers(parser)
        route = " ".join(prefix)
        if route and (not sub_actions or not any(action.required for action in sub_actions)):
            index[(source, route)] = (parser, active_lineage)
        for action in sub_actions:
            seen: set[int] = set()
            for name, child in action.choices.items():
                identity = id(child)
                if identity in seen:
                    continue
                seen.add(identity)
                if not prefix and name in skip_top_level:
                    continue
                visit(
                    source,
                    child,
                    prefix=(*prefix, name),
                    lineage=active_lineage,
                    skip_top_level=skip_top_level,
                )

    for source, parser, skip in public_surface.python_public_parser_surfaces():
        visit(source, parser, prefix=(), lineage=(), skip_top_level=skip)
    return index


def _seed_fixture(project: Path, state: Path, home: Path) -> dict[str, Path]:
    project.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (project / ".git").mkdir(exist_ok=True)
    syntavra = project / ".syntavra"
    syntavra.mkdir(exist_ok=True)
    (project / "README.md").write_text("# fixture\n", encoding="utf-8")
    (project / "sample.py").write_text(
        "def fixture(value: int = 1) -> int:\n    return value + 1\n",
        encoding="utf-8",
    )
    (project / "input.txt").write_text("fixture input\n", encoding="utf-8")
    (project / "payload.json").write_text("{}\n", encoding="utf-8")
    (syntavra / "config.toml").write_text("[syntavra]\nversion = 1\n", encoding="utf-8")
    database = project / "fixture.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS fixture (id INTEGER PRIMARY KEY, value TEXT)")
        connection.commit()
    return {
        "project": project,
        "state": state,
        "home": home,
        "database": database,
        "sample": project / "sample.py",
        "input": project / "input.txt",
        "payload": project / "payload.json",
        "output": project / "output.json",
        "config": syntavra / "config.toml",
    }


def _first_choice(action: argparse.Action) -> str | None:
    choices = getattr(action, "choices", None)
    if choices is None:
        return None
    values = list(choices)
    if not values:
        return None
    return str(values[0])


def _placeholder(action: argparse.Action, fixture: dict[str, Path]) -> str:
    choice = _first_choice(action)
    if choice is not None:
        return choice
    dest = str(action.dest).casefold()
    action_type = getattr(action, "type", None)
    if action_type is int:
        return "1"
    if action_type is float:
        return "0.1"
    if "port" in dest:
        return "0"
    if "timeout" in dest or dest.endswith("seconds") or dest.endswith("_ms"):
        return "0.1"
    if "hash" in dest or "digest" in dest or dest in {"project_id", "expected_project_id"}:
        return "0" * 64
    if dest.endswith("_hex") or "json_hex" in dest or "wire_hex" in dest:
        return "7b7d"
    if "database" in dest or dest.endswith("_db") or dest == "db":
        return str(fixture["database"])
    if "output" in dest and ("path" in dest or "file" in dest):
        return str(fixture["output"])
    if "config" in dest and ("path" in dest or "file" in dest):
        return str(fixture["config"])
    if "path" in dest or "file" in dest:
        return str(fixture["input"])
    if "root" in dest or "directory" in dest or dest.endswith("_dir"):
        return str(fixture["project"])
    if "url" in dest or "endpoint" in dest:
        return "http://127.0.0.1:9"
    if "json" in dest or "payload" in dest or "metadata" in dest:
        return "{}"
    if "argv" in dest:
        return '["python","-c","print(1)"]'
    if dest in {"command", "cmd"}:
        return "true"
    if "query" in dest:
        return "fixture"
    if "symbol" in dest:
        return "fixture"
    if "pattern" in dest or "regex" in dest:
        return "fixture"
    if "host" in dest:
        return "codex"
    if "scope" in dest:
        return "project"
    if "engine" in dest:
        return "python"
    if dest.endswith("id") or dest.endswith("_id"):
        return "fixture-id"
    return "fixture"


def _values_for_action(action: argparse.Action, fixture: dict[str, Path]) -> list[str]:
    value = _placeholder(action, fixture)
    nargs = getattr(action, "nargs", None)
    if nargs in ("*", "?"):
        return []
    if nargs == "+":
        return [value]
    if isinstance(nargs, int):
        return [value for _ in range(max(0, nargs))]
    return [value]


def _minimum_extra_args(
    lineage: tuple[argparse.ArgumentParser, ...], fixture: dict[str, Path]
) -> tuple[list[str], list[dict[str, Any]]]:
    selected_group_actions: set[int] = set()
    required_group_actions: list[argparse.Action] = []
    for parser in lineage:
        for group in parser._mutually_exclusive_groups:
            if not group.required:
                continue
            candidates = [
                action
                for action in group._group_actions
                if not isinstance(action, argparse._SubParsersAction)
                and not isinstance(action, argparse._HelpAction)
            ]
            if candidates:
                required_group_actions.append(candidates[0])
                selected_group_actions.add(id(candidates[0]))

    actions: list[argparse.Action] = []
    seen: set[int] = set()
    for parser in lineage:
        for action in parser._actions:
            if id(action) in seen:
                continue
            seen.add(id(action))
            if isinstance(action, (argparse._SubParsersAction, argparse._HelpAction)):
                continue
            actions.append(action)

    extras: list[str] = []
    metadata: list[dict[str, Any]] = []
    for action in actions:
        positional = not action.option_strings
        required = positional and getattr(action, "nargs", None) not in ("?", "*")
        required = required or bool(getattr(action, "required", False)) or id(action) in selected_group_actions
        safe_optional = bool(action.option_strings) and (
            str(action.dest) in SAFE_BOOLEAN_DESTS or str(action.dest) in SAFE_BOUNDED_VALUE_DESTS
        )
        if not required and not safe_optional:
            continue

        row = {
            "dest": str(action.dest),
            "option_strings": list(action.option_strings),
            "required": required,
            "safe_optional": safe_optional,
            "nargs": getattr(action, "nargs", None),
            "choices": [str(item) for item in list(action.choices)] if getattr(action, "choices", None) is not None else None,
        }
        metadata.append(row)

        if isinstance(action, argparse._StoreTrueAction):
            if action.option_strings:
                extras.append(action.option_strings[0])
            continue
        if isinstance(action, argparse._StoreFalseAction):
            if required and action.option_strings:
                extras.append(action.option_strings[0])
            continue
        values = _values_for_action(action, fixture)
        if action.option_strings:
            if not values and not required:
                continue
            extras.append(action.option_strings[0])
            extras.extend(values or [_placeholder(action, fixture)])
        else:
            extras.extend(values)

    for action in required_group_actions:
        if id(action) in {id(item) for item in actions}:
            continue
        # Defensive only; group actions normally appear in parser._actions.
        if action.option_strings:
            extras.append(action.option_strings[0])
            if not isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
                extras.append(_placeholder(action, fixture))
    return extras, metadata


def _isolated_env(repo: Path, fixture: dict[str, Path], contract: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    for key in CREDENTIAL_ENV:
        env.pop(key, None)
    env.pop("GITHUB_SHA", None)
    home = fixture["home"]
    xdg = home / ".xdg"
    cache = xdg / "cache"
    config = xdg / "config"
    data = xdg / "data"
    for path in (cache, config, data):
        path.mkdir(parents=True, exist_ok=True)
    sink = str(contract["execution"]["external_http_proxy_sink"])
    no_proxy = str(contract["execution"]["localhost_no_proxy"])
    env.update(
        {
            "PYTHONPATH": str(repo),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CACHE_HOME": str(cache),
            "XDG_CONFIG_HOME": str(config),
            "XDG_DATA_HOME": str(data),
            "HTTP_PROXY": sink,
            "HTTPS_PROXY": sink,
            "ALL_PROXY": sink,
            "http_proxy": sink,
            "https_proxy": sink,
            "all_proxy": sink,
            "NO_PROXY": no_proxy,
            "no_proxy": no_proxy,
            "PIP_NO_INDEX": "1",
            "UV_OFFLINE": "1",
            "npm_config_offline": "true",
        }
    )
    return env


def _snapshot_files(fixture: dict[str, Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for label in ("project", "state", "home"):
        root = fixture[label]
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest = "<unreadable>"
            result[f"{label}/{relative}"] = digest
    return result


def _normalize_effect_path(value: str) -> str:
    parts = value.split("/")
    return "/".join(DYNAMIC_PATH_PART.sub("<dynamic>", part) for part in parts)


def _filesystem_delta(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    before_keys = set(before)
    after_keys = set(after)
    created = sorted({_normalize_effect_path(item) for item in after_keys - before_keys})
    deleted = sorted({_normalize_effect_path(item) for item in before_keys - after_keys})
    modified = sorted(
        {
            _normalize_effect_path(item)
            for item in before_keys & after_keys
            if before[item] != after[item]
        }
    )
    return {
        "created": created,
        "modified": modified,
        "deleted": deleted,
        "created_count": len(created),
        "modified_count": len(modified),
        "deleted_count": len(deleted),
    }


def _output_projection(stdout: str, stderr: str, exit_code: int | None, timed_out: bool) -> dict[str, Any]:
    def format_of(text: str, *, stderr_stream: bool = False) -> tuple[str, Any]:
        stripped = text.strip()
        if not stripped:
            return "empty", None
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            if stderr_stream and "usage:" in stripped.casefold():
                return "argparse-usage-error", None
            return "text", None
        if isinstance(value, dict):
            return "json-object", value
        if isinstance(value, list):
            return "json-list", value
        return "json-scalar", value

    stdout_format, stdout_value = format_of(stdout)
    stderr_format, _ = format_of(stderr, stderr_stream=True)
    top_keys = sorted(stdout_value) if isinstance(stdout_value, dict) else None
    ok_value = stdout_value.get("ok") if isinstance(stdout_value, dict) and isinstance(stdout_value.get("ok"), bool) else None
    error_code = None
    error_type = None
    if isinstance(stdout_value, dict):
        error = stdout_value.get("error")
        if isinstance(error, dict):
            if isinstance(error.get("code"), str):
                error_code = error["code"]
            if isinstance(error.get("type"), str):
                error_type = error["type"]
            details = error.get("details")
            if error_type is None and isinstance(details, dict) and isinstance(details.get("type"), str):
                error_type = details["type"]
    traceback_present = "traceback (most recent call last)" in (stdout + "\n" + stderr).casefold()
    return {
        "exit": exit_code,
        "timed_out": timed_out,
        "stdout_format": stdout_format,
        "stderr_format": stderr_format,
        "stdout_top_level_keys": top_keys,
        "ok": ok_value,
        "error_code": error_code,
        "error_type": error_type,
        "traceback_present": traceback_present,
    }


def _invoke(
    repo: Path,
    fixture: dict[str, Path],
    argv: list[str],
    contract: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    command = [
        sys.executable,
        "-m",
        "syntavra_runtime.engine_entry",
        "--engine",
        "python",
        "--project",
        str(fixture["project"]),
        "--state-root",
        str(fixture["state"]),
        *argv,
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=repo,
            env=_isolated_env(repo, fixture, contract),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(contract["execution"]["per_invocation_timeout_seconds"]),
            check=False,
        )
        projection = _output_projection(proc.stdout, proc.stderr, proc.returncode, False)
        return projection, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        projection = _output_projection(stdout, stderr, None, True)
        return projection, stdout, stderr


def _route_contract_row(
    repo: Path,
    route: str,
    execution_row: dict[str, Any],
    parser_index: dict[tuple[str, str], tuple[argparse.ArgumentParser, tuple[argparse.ArgumentParser, ...]]],
    root: Path,
    contract: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    source = str((execution_row.get("sources") or [""])[0])
    parser_owned = bool(execution_row.get("parser_owned"))
    route_root = root / hashlib.sha256(route.encode("utf-8")).hexdigest()[:16]
    fixture = _seed_fixture(route_root / "project", route_root / "state", route_root / "home")

    extras: list[str] = []
    parser_metadata: list[dict[str, Any]] = []
    if parser_owned:
        indexed = parser_index.get((source, route))
        if indexed is None:
            errors.append("parser-owned target route has no leaf parser index")
        else:
            _, lineage = indexed
            extras, parser_metadata = _minimum_extra_args(lineage, fixture)

    argv = [*route.split(" "), *extras]
    before = _snapshot_files(fixture)
    first, first_stdout, first_stderr = _invoke(repo, fixture, argv, contract)
    middle = _snapshot_files(fixture)
    first_delta = _filesystem_delta(before, middle)
    second, second_stdout, second_stderr = _invoke(repo, fixture, argv, contract)
    after = _snapshot_files(fixture)
    second_delta = _filesystem_delta(middle, after)

    if first["timed_out"] or second["timed_out"]:
        errors.append("minimum execution timed out")
    if first["traceback_present"] or second["traceback_present"]:
        errors.append("minimum execution exposed a Python traceback")
    if parser_owned and (first["exit"] == 2 or second["exit"] == 2):
        errors.append("synthesized parser-valid minimum invocation exited argparse code 2")

    repeat_same_shape = {
        key: first.get(key) == second.get(key)
        for key in (
            "exit",
            "stdout_format",
            "stderr_format",
            "stdout_top_level_keys",
            "ok",
            "error_code",
            "error_type",
        )
    }
    return (
        {
            "route": route,
            "source": source,
            "entrypoint": execution_row.get("entrypoint"),
            "parser_owned": parser_owned,
            "success_exit_contract": execution_row.get("success_exit"),
            "parser_error_exit_contract": execution_row.get("parser_error_exit"),
            "minimum_argv": argv,
            "synthesized_actions": parser_metadata,
            "first": first,
            "second": second,
            "first_filesystem_delta": first_delta,
            "second_filesystem_delta": second_delta,
            "repeat_same_shape": repeat_same_shape,
            "stdout_bytes": [len(first_stdout.encode("utf-8")), len(second_stdout.encode("utf-8"))],
            "stderr_bytes": [len(first_stderr.encode("utf-8")), len(second_stderr.encode("utf-8"))],
        },
        errors,
    )


def _strict_hash(contract: dict[str, Any], key: str, observed: str) -> None:
    expected = (contract.get("derived_freeze") or {}).get(key)
    if expected is None:
        if contract.get("strict"):
            raise AssertionError(f"strict core/legacy reference missing hash: {key}")
        return
    if observed != expected:
        raise AssertionError(f"core/legacy reference drift for {key}: {observed} != {expected}")


def certify(repo: Path) -> dict[str, Any]:
    contract = _read_json(repo / CONTRACT_RELATIVE)
    if contract.get("schema_version") != 1 or contract.get("family") != "core-legacy-route-reference":
        raise AssertionError("core/legacy route reference contract identity drift")
    exact_head = _head(repo)
    if not exact_head:
        raise AssertionError("unable to resolve exact git HEAD")

    surface = public_surface.report()
    if surface.get("ok") is not True:
        raise AssertionError(f"canonical public surface is red: {surface}")
    execution = execution_contract.report()
    if execution.get("ok") is not True:
        raise AssertionError(f"Python public execution authority is red: {execution}")
    canonical = {row["route"] for row in surface["python"]["manifest"]}
    selection = contract["selection"]
    if len(canonical) != int(selection["expected_canonical_route_count"]):
        raise AssertionError("canonical route count drift")
    if surface["python"]["derived_sha256"] != selection["expected_canonical_route_sha256"]:
        raise AssertionError("canonical route digest drift")

    dp_routes, dp_rows = _dp_explicit_routes(repo, canonical)
    if len(dp_routes) != int(selection["expected_dp_explicit_route_count"]):
        raise AssertionError(
            f"D-P explicit route derivation drift: {len(dp_routes)} != {selection['expected_dp_explicit_route_count']}"
        )
    targets = sorted(canonical - dp_routes)
    if len(targets) != int(selection["expected_target_route_count"]):
        raise AssertionError(f"core/legacy target count drift: {len(targets)}")

    execution_by_route = {row["route"]: row for row in execution["python"]["manifest"]}
    source_counts: dict[str, int] = {}
    for route in targets:
        row = execution_by_route[route]
        sources = list(row.get("sources") or [])
        if len(sources) != 1:
            raise AssertionError(f"target route does not have one source: {route} -> {sources}")
        source = str(sources[0])
        source_counts[source] = source_counts.get(source, 0) + 1
    if source_counts != selection["expected_source_counts"]:
        raise AssertionError(f"core/legacy source-count drift: {source_counts}")

    parser_index = _parser_leaf_index()
    pre_status = _git_status(repo)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="syntavra-core-legacy-reference-") as temp_name:
        root = Path(temp_name)
        for route in targets:
            row, errors = _route_contract_row(
                repo,
                route,
                execution_by_route[route],
                parser_index,
                root,
                contract,
            )
            rows.append(row)
            if errors:
                failures.append({"route": route, "errors": errors, "projection": row})
    post_status = _git_status(repo)
    repository_status_preserved = pre_status == post_status
    if not repository_status_preserved:
        failures.append({"route": "<repository>", "errors": ["repository status changed during certification"]})

    route_contract_projection = [
        {
            "route": row["route"],
            "source": row["source"],
            "parser_owned": row["parser_owned"],
            "success_exit_contract": row["success_exit_contract"],
            "parser_error_exit_contract": row["parser_error_exit_contract"],
            "synthesized_actions": row["synthesized_actions"],
            "first": row["first"],
            "second": row["second"],
            "repeat_same_shape": row["repeat_same_shape"],
        }
        for row in rows
    ]
    side_effect_projection = [
        {
            "route": row["route"],
            "first": row["first_filesystem_delta"],
            "second": row["second_filesystem_delta"],
        }
        for row in rows
    ]
    idempotency_projection = [
        {
            "route": row["route"],
            "repeat_same_shape": row["repeat_same_shape"],
            "first_exit": row["first"]["exit"],
            "second_exit": row["second"]["exit"],
        }
        for row in rows
    ]
    route_contract_sha = _semantic_sha(route_contract_projection)
    side_effect_sha = _semantic_sha(side_effect_projection)
    idempotency_sha = _semantic_sha(idempotency_projection)
    _strict_hash(contract, "expected_route_contract_sha256", route_contract_sha)
    _strict_hash(contract, "expected_side_effect_sha256", side_effect_sha)
    _strict_hash(contract, "expected_idempotency_sha256", idempotency_sha)

    ok = not failures
    return {
        "ok": ok,
        "schema_version": 1,
        "family": "core-legacy-route-reference",
        "engine": "python",
        "phase": "T",
        "claim": contract["claim"],
        "strict": bool(contract["strict"]),
        "exact_head": exact_head,
        "canonical_route_count": len(canonical),
        "dp_explicit_route_count": len(dp_routes),
        "target_route_count": len(targets),
        "source_counts": source_counts,
        "dp_family_route_coverage": dp_rows,
        "target_routes": targets,
        "normalization": contract["normalization"],
        "routes": rows,
        "failure_count": len(failures),
        "failures": failures,
        "repository_status_preserved": repository_status_preserved,
        "repository_clean_before": pre_status == "",
        "repository_clean_after": post_status == "",
        "derived_hashes": {
            "route_contract_sha256": route_contract_sha,
            "side_effect_sha256": side_effect_sha,
            "idempotency_sha256": idempotency_sha,
        },
        "rust_native_promotion_credit": False,
        "frozen_rust_native_count": int(contract["frozen_rust_native_count"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify derived core/legacy Python public-route behavior")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    try:
        result = certify(repo)
    except Exception as exc:
        result = {
            "ok": False,
            "schema_version": 1,
            "family": "core-legacy-route-reference",
            "engine": "python",
            "phase": "T",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
