#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from syntavra_runtime.language_platform import LanguageDescriptor, LanguageRegistry
from syntavra_runtime.tree_sitter_adapter import TreeSitterLanguageAdapter


ROUTES = [
    "run graph-index",
    "run graph-query",
    "run graph-impact",
    "run language detect",
    "run language inventory",
    "run language index",
    "run language query",
    "run language doctor",
    "run semantic-services",
]

DYNAMIC_FIELDS = {"created_at", "updated_at", "indexed_at", "imported_at"}


def _head(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _seed(project: Path) -> None:
    project.mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "alpha.py").write_text(
        "def helper(value: int) -> int:\n"
        "    return value + 1\n\n"
        "def alpha(value: int) -> int:\n"
        "    return helper(value)\n",
        encoding="utf-8",
    )
    (project / "test_alpha.py").write_text(
        "from alpha import alpha\n\n"
        "def test_alpha():\n"
        "    assert alpha(1) == 2\n",
        encoding="utf-8",
    )
    manifest = project / ".syntavra" / "languages"
    manifest.mkdir(parents=True)
    (manifest / "fixture.json").write_text(
        json.dumps(
            {
                "id": "fixturelang",
                "suffixes": [".fixture"],
                "aliases": ["fixture-alias"],
                "capabilities": ["lexical"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    # A malformed neighboring manifest must be diagnostic-only, not fatal to the
    # valid repository-local language contract.
    (manifest / "broken.json").write_text("{not-json\n", encoding="utf-8")
    (project / "sample.fixture").write_text("fixture token alpha\n", encoding="utf-8")
    (project / "unknown.futurelang").write_text("future token alpha\n", encoding="utf-8")
    (project / "Program.cs").write_text("class Program { static void Main() {} }\n", encoding="utf-8")


def _run(repo: Path, project: Path, state: Path, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("SYNTAVRA_BULK_PARITY_PROBE", None)
    env.pop("SYNTAVRA_ALLOW_LANGUAGE_PLUGINS", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "syntavra_runtime.engine_entry",
            "--engine",
            "python",
            "--project",
            str(project),
            "--state-root",
            str(state),
            *args,
        ],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    try:
        value = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError:
        value = None
    return {"exit": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr, "value": value}


def _ok(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 0 or result["stderr"]:
        raise AssertionError(f"{label}: expected clean exit 0, got {result}")
    value = result.get("value")
    if not isinstance(value, dict):
        raise AssertionError(f"{label}: expected JSON object stdout, got {result}")
    return value


def _public_error(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 4 or result["stderr"]:
        raise AssertionError(f"{label}: expected public exit 4 with empty stderr, got {result}")
    value = result.get("value")
    if not isinstance(value, dict) or value.get("ok") is not False:
        raise AssertionError(f"{label}: expected JSON failure envelope, got {result}")
    error = value.get("error")
    if not isinstance(error, dict) or error.get("code") != "PYTHON_PUBLIC_COMMAND_FAILED":
        raise AssertionError(f"{label}: public error code drift: {result}")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    return {
        "exit": 4,
        "stdout_format": "json-object",
        "stderr_empty": True,
        "error_code": error["code"],
        "detail": details.get("error"),
    }


def _argparse_error(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if result["exit"] != 2 or result["stdout"] or "usage:" not in result["stderr"].casefold():
        raise AssertionError(f"{label}: expected argparse usage error, got {result}")
    return {"exit": 2, "stdout_format": "empty", "stderr_format": "argparse-usage-error"}


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(child) for key, child in value.items() if key not in DYNAMIC_FIELDS}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _registry_contract(project: Path) -> dict[str, Any]:
    registry = LanguageRegistry(discover_entry_points=False)
    registry.discover_manifests(project)
    inventory = registry.inventory()
    sample = registry.detect(project / "sample.fixture", (project / "sample.fixture").read_bytes())
    alias_descriptor = registry._descriptors.get("fixture-alias")
    canonical_descriptor = registry._descriptors.get("fixturelang")
    if canonical_descriptor is None or alias_descriptor is not canonical_descriptor:
        raise AssertionError("repository language alias no longer resolves to the canonical descriptor")
    if sample.language_id != "fixturelang" or sample.capability_level != "lexical":
        raise AssertionError(f"repository manifest detection drift: {sample}")

    synthetic = LanguageRegistry(discover_entry_points=False)
    descriptor = LanguageDescriptor(
        "contractlang",
        suffixes=(".contract",),
        aliases=("contract-alias",),
        capabilities=frozenset({"lexical", "syntax"}),
        source="contract-fixture",
    )
    synthetic.register_descriptor(descriptor)
    if synthetic._descriptors.get("contract-alias") is not synthetic._descriptors.get("contractlang"):
        raise AssertionError("descriptor alias identity drift")

    tree = TreeSitterLanguageAdapter()
    if set(tree.capabilities) != {"syntax", "definitions", "references"}:
        raise AssertionError(f"tree-sitter capability declaration drift: {tree.capabilities}")
    language_ids = set(tree.language_ids)
    if not {"c_sharp", "csharp"}.issubset(language_ids):
        raise AssertionError(f"C# adapter alias contract drift: {sorted(language_ids)}")

    return {
        "registered_languages": inventory["registered_languages"],
        "languages": inventory["languages"],
        "adapters": inventory["adapters"],
        "diagnostics": inventory["diagnostics"],
        "entry_point_plugins_authorized": inventory["entry_point_plugins_authorized"],
        "universal_text_fallback": inventory["universal_text_fallback"],
        "fixture_alias_identity": True,
        "tree_sitter": {
            "installed": tree.installed,
            "language_ids": sorted(language_ids),
            "available_languages": list(tree.available_languages()),
            "capabilities": sorted(tree.capabilities),
        },
    }


def _sqlite_contract(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(f"semantic graph SQLite database missing: {path}")
    db = sqlite3.connect(path)
    try:
        tables = sorted(
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
        indexes = sorted(
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
        )
        counts: dict[str, int] = {}
        for table in tables:
            if table.replace("_", "").isalnum():
                counts[table] = int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    finally:
        db.close()
    if not any(counts.get(name, 0) > 0 for name in ("nodes", "graph_nodes")):
        # Schema names are allowed to evolve only through this contract; if neither
        # canonical node table exists, the durable graph disappeared entirely.
        raise AssertionError(f"semantic graph contains no populated node table: {counts}")
    return {"tables": tables, "indexes": indexes, "row_counts": counts}


def certify(repo: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="syntavra-python-graph-language-") as directory:
        root = Path(directory)
        project = root / "project"
        state = root / "state"
        _seed(project)
        (root / "outside.py").write_text("print('outside')\n", encoding="utf-8")

        registry = _registry_contract(project)

        def call(*parts: str) -> dict[str, Any]:
            return _ok(" ".join(parts), _run(repo, project, state, list(parts)))

        # Querying an unmaterialized graph is intentionally a successful empty read.
        empty_query = call("run", "graph-query", "alpha", "--limit", "20")
        if empty_query != {"ok": True, "query": "alpha", "results": []}:
            raise AssertionError(f"empty graph query contract drift: {empty_query}")

        fixture_detect = call("run", "language", "detect", "sample.fixture")
        fixture_detection = fixture_detect.get("detection")
        if not isinstance(fixture_detection, dict) or fixture_detection.get("language_id") != "fixturelang":
            raise AssertionError(f"public manifest-backed detection drift: {fixture_detect}")
        if not str(fixture_detection.get("descriptor_source", "")).startswith("manifest:"):
            raise AssertionError(f"public manifest source evidence drift: {fixture_detect}")

        unknown_detect = call("run", "language", "detect", "unknown.futurelang")
        unknown = unknown_detect.get("detection")
        if not isinstance(unknown, dict) or unknown.get("language_id") != "unknown:futurelang":
            raise AssertionError(f"universal text fallback drift: {unknown_detect}")
        if unknown.get("capability_level") != "lexical" or unknown.get("descriptor_source") != "fallback":
            raise AssertionError(f"unknown-language claim boundary drift: {unknown_detect}")

        csharp_detect = call("run", "language", "detect", "Program.cs")
        csharp = csharp_detect.get("detection")
        if not isinstance(csharp, dict) or csharp.get("language_id") != "csharp":
            raise AssertionError(f"C# public language identity drift: {csharp_detect}")

        inventory = call("run", "language", "inventory")
        doctor = call("run", "language", "doctor")
        services = call("run", "semantic-services")
        for label, value in (("inventory", inventory), ("doctor", doctor), ("semantic-services", services)):
            language_registry = value.get("language_registry")
            if not isinstance(language_registry, dict):
                raise AssertionError(f"{label}: missing language_registry: {value}")
            if "fixturelang" not in language_registry.get("languages", []):
                raise AssertionError(f"{label}: repository manifest absent from status: {language_registry}")
            adapters = set(language_registry.get("adapters", []))
            if not {"c_sharp", "csharp"}.issubset(adapters):
                raise AssertionError(f"{label}: C# adapter aliases drift: {sorted(adapters)}")
            if language_registry.get("entry_point_plugins_authorized") is not False:
                raise AssertionError(f"{label}: implicit plugin execution became authorized")
            diagnostics = language_registry.get("diagnostics", [])
            if not any("broken.json" in str(item) for item in diagnostics):
                raise AssertionError(f"{label}: malformed manifest diagnostic disappeared: {diagnostics}")

        index = call("run", "graph-index", "--max-file-bytes", "2000000")
        if index.get("ok") is not True or index.get("canonical_graph") is not True:
            raise AssertionError(f"graph-index contract drift: {index}")
        if index.get("unknown_language_files") != 1:
            raise AssertionError(f"unknown language accounting drift: {index}")
        indexed_languages = {row.get("language"): row.get("files") for row in index.get("languages", []) if isinstance(row, dict)}
        if indexed_languages.get("fixturelang") != 1 or indexed_languages.get("python") != 2 or indexed_languages.get("csharp") != 1:
            raise AssertionError(f"indexed language accounting drift: {indexed_languages}")

        graph_query = call("run", "graph-query", "alpha", "--limit", "20")
        results = graph_query.get("results")
        if not isinstance(results, list) or not results:
            raise AssertionError(f"graph-query returned no alpha results: {graph_query}")
        first = results[0]
        if not isinstance(first, dict) or not isinstance(first.get("node_id"), str):
            raise AssertionError(f"graph-query result schema drift: {first}")
        if first.get("query_backend") != "sqlite-fts5":
            raise AssertionError(f"repository query backend drift: {first}")
        scores = [float(row.get("score", 0.0)) for row in results if isinstance(row, dict)]
        if scores != sorted(scores, reverse=True):
            raise AssertionError(f"graph-query sorting drift: {scores}")

        impact = call("run", "graph-impact", str(first["node_id"]), "--max-depth", "4")
        if impact.get("root") != first["node_id"] or not isinstance(impact.get("impacted"), list):
            raise AssertionError(f"graph-impact contract drift: {impact}")
        if impact.get("exact_evidence") is not True:
            raise AssertionError(f"graph-impact exact evidence drift: {impact}")

        language_index = call("run", "language", "index", "--max-file-bytes", "2000000")
        language_query = call("run", "language", "query", "helper", "--limit", "20")
        helper_results = language_query.get("results")
        if not isinstance(helper_results, list) or not helper_results or helper_results[0].get("name") != "helper":
            raise AssertionError(f"language query contract drift: {language_query}")
        no_match = call("run", "language", "query", "definitely-no-such-symbol", "--limit", "20")
        if no_match.get("results") != []:
            raise AssertionError(f"no-match language query drift: {no_match}")

        missing_detect = _public_error(
            "missing detect file",
            _run(repo, project, state, ["run", "language", "detect", "missing.fixture"]),
        )
        escaped_detect = _public_error(
            "escaped detect path",
            _run(repo, project, state, ["run", "language", "detect", "../outside.py"]),
        )
        bad_limit = _argparse_error(
            "invalid graph query limit",
            _run(repo, project, state, ["run", "graph-query", "alpha", "--limit", "not-an-int"]),
        )
        missing_detect_arg = _argparse_error(
            "missing language detect path",
            _run(repo, project, state, ["run", "language", "detect"]),
        )

        db = _sqlite_contract(state / "unified" / "semantic-graph.sqlite3")
        if int(index.get("nodes", 0)) <= 0 or int(index.get("edges", 0)) <= 0:
            raise AssertionError(f"graph materialization counts drift: {index}")

        common_status = {
            "inventory": _normalize(inventory),
            "doctor": _normalize(doctor),
            "semantic_services": _normalize(services),
        }
        if common_status["inventory"] != common_status["doctor"] or common_status["inventory"] != common_status["semantic_services"]:
            raise AssertionError("inventory, doctor and semantic-services no longer expose one canonical language status contract")

        return {
            "ok": True,
            "schema_version": 1,
            "family": "graph-language-semantic",
            "engine": "python",
            "exact_head": _head(repo),
            "routes": ROUTES,
            "exit_policy": {"success": 0, "application_error": 4, "argument_parser_error": 2},
            "registry_contract": registry,
            "cases": {
                "empty_graph_query": empty_query,
                "fixture_detect": _normalize(fixture_detect),
                "unknown_detect": _normalize(unknown_detect),
                "csharp_detect": _normalize(csharp_detect),
                "language_status": common_status["inventory"],
                "graph_index": _normalize(index),
                "graph_query": _normalize(graph_query),
                "graph_impact": _normalize(impact),
                "language_index": _normalize(language_index),
                "language_query": _normalize(language_query),
                "language_query_no_match": _normalize(no_match),
                "missing_detect_error": missing_detect,
                "escaped_detect_error": escaped_detect,
                "invalid_limit_error": bad_limit,
                "missing_detect_argument": missing_detect_arg,
                "sqlite": db,
            },
            "nondeterministic_fields": sorted(DYNAMIC_FIELDS),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the Python-only graph/language/semantic reference contract")
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
            "family": "graph-language-semantic",
            "engine": "python",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
