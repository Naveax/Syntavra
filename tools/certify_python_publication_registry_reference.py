#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import tomllib
import traceback
from pathlib import Path
from typing import Any, Callable

from syntavra_runtime.public_proof import DISTRIBUTIONS, write_prerelease_manifest
from syntavra_runtime.release_identity import (
    CHANNEL,
    STABILITY,
    VERSION,
    VERSION_LOCKED,
    identity,
    prerelease_metadata,
    validate_repository_identity,
)
from syntavra_runtime.schema_registry import SchemaDefinition, SchemaError, SchemaRegistry
from syntavra_runtime.util import canonical_json
from tools import report_missing_native_public_routes as public_surface

FIXTURE_RELATIVE = Path("contracts/python/publication-registry-reference-v1.json")


def _head(repo: Path) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _public_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(_public_json(value))).hexdigest()


def _expect_error(label: str, function: Callable[[], Any], expected: str) -> str:
    try:
        function()
    except (SchemaError, ValueError, TypeError) as exc:
        observed = str(exc)
        if observed != expected:
            raise AssertionError(f"{label} error drift: {observed!r} != {expected!r}") from exc
        return observed
    raise AssertionError(f"{label}: expected failure {expected!r}")


def _public_cli_contract(fixture: dict[str, Any]) -> dict[str, Any]:
    routes = sorted(public_surface.python_public_route_sources())
    expected = fixture["public_cli"]
    if len(routes) != expected["canonical_route_count"]:
        raise AssertionError(
            f"canonical public route count drift: {len(routes)} != {expected['canonical_route_count']}"
        )
    owned = list(expected["owned_routes"])
    if owned:
        raise AssertionError(f"publication/registry unexpectedly claims public CLI routes: {owned}")
    forbidden_tokens = tuple(expected["forbidden_public_route_tokens"])
    matching = [
        route
        for route in routes
        if any(token in route.split() for token in forbidden_tokens)
    ]
    if matching:
        raise AssertionError(f"publication/registry public CLI leaf unexpectedly exists: {matching}")
    return {
        "canonical_route_count": len(routes),
        "canonical_route_sha256": public_surface._digest(routes),
        "owned_routes": [],
        "forbidden_tokens": list(forbidden_tokens),
        "matching_routes": matching,
    }


def _release_contract(repo: Path, fixture: dict[str, Any], temp_root: Path) -> dict[str, Any]:
    expected_identity = fixture["release_identity"]
    release_value = identity().to_dict()
    if VERSION != expected_identity["version"]:
        raise AssertionError(f"release version drift: {VERSION}")
    if CHANNEL != expected_identity["channel"]:
        raise AssertionError(f"release channel drift: {CHANNEL}")
    if STABILITY != expected_identity["stability"]:
        raise AssertionError(f"release stability drift: {STABILITY}")
    if VERSION_LOCKED is not expected_identity["version_locked"]:
        raise AssertionError(f"release version lock drift: {VERSION_LOCKED}")

    repository_identity = validate_repository_identity(repo)
    if repository_identity.get("ok") is not True:
        raise AssertionError(f"repository release identity drift: {repository_identity}")

    pre_release = json.loads((repo / "release" / "pre-release.json").read_text(encoding="utf-8"))
    readiness = json.loads((repo / "release" / "publish-readiness.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    npm_installer = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    typescript = json.loads((repo / "sdk" / "typescript" / "package.json").read_text(encoding="utf-8"))
    vscode = json.loads((repo / "integrations" / "vscode-syntavra" / "package.json").read_text(encoding="utf-8"))

    required_pre_release_keys = {
        "channel",
        "claim_boundaries",
        "competitive_feature_manifest",
        "development_status",
        "product",
        "publish_as_prerelease",
        "stable",
        "version",
        "version_change_requires_owner_instruction",
        "version_locked",
    }
    if set(pre_release) != required_pre_release_keys:
        raise AssertionError(f"pre-release metadata key drift: {sorted(pre_release)}")
    if pre_release.get("product") != expected_identity["product"]:
        raise AssertionError("pre-release product identity drift")
    if pre_release.get("version") != expected_identity["version"] or pre_release.get("channel") != expected_identity["channel"]:
        raise AssertionError("pre-release version/channel drift")
    if pre_release.get("development_status") != expected_identity["stability"]:
        raise AssertionError("pre-release development status drift")
    if pre_release.get("publish_as_prerelease") is not expected_identity["publish_as_prerelease"]:
        raise AssertionError("pre-release publication mode drift")
    if pre_release.get("stable") is not expected_identity["stable"]:
        raise AssertionError("pre-release stability claim drift")
    if pre_release.get("version_locked") is not expected_identity["version_locked"]:
        raise AssertionError("pre-release version lock drift")
    if pre_release.get("claim_boundaries", {}).get("registry_publication") != expected_identity["registry_publication_claim"]:
        raise AssertionError("registry publication claim boundary drift")

    target_names = ["python", "npm", "vscode", "native"]
    if set(readiness) != {"version", "channel", *target_names, "claim_boundary"}:
        raise AssertionError(f"publish-readiness key drift: {sorted(readiness)}")
    if readiness.get("version") != expected_identity["version"] or readiness.get("channel") != expected_identity["channel"]:
        raise AssertionError("publish-readiness version/channel drift")
    for name in target_names:
        observed = readiness.get(name)
        expected = fixture["publication_targets"][name]
        if observed != expected:
            raise AssertionError(f"publication target drift for {name}: {observed} != {expected}")
        if observed.get("published") is not False:
            raise AssertionError(f"publication target {name} must remain unclaimed/unpublished")
    expected_boundary = "Registry publication requires owner credentials and successful release receipts."
    if readiness.get("claim_boundary") != expected_boundary:
        raise AssertionError("publish-readiness claim boundary drift")

    package_expected = fixture["package_metadata"]
    package_observed = {
        "python_name": pyproject.get("project", {}).get("name"),
        "npm_installer_name": npm_installer.get("name"),
        "typescript_sdk_name": typescript.get("name"),
        "vscode_name": vscode.get("name"),
        "npm_tag": npm_installer.get("publishConfig", {}).get("tag"),
        "npm_provenance": npm_installer.get("publishConfig", {}).get("provenance"),
        "typescript_tag": typescript.get("publishConfig", {}).get("tag"),
        "typescript_provenance": typescript.get("publishConfig", {}).get("provenance"),
    }
    if package_observed != package_expected:
        raise AssertionError(f"package publication metadata drift: {package_observed} != {package_expected}")
    package_versions = {
        "python": pyproject.get("project", {}).get("version"),
        "npm_installer": npm_installer.get("version"),
        "typescript_sdk": typescript.get("version"),
        "vscode": vscode.get("version"),
    }
    if set(package_versions.values()) != {expected_identity["version"]}:
        raise AssertionError(f"publication package version drift: {package_versions}")

    generated_one = write_prerelease_manifest(temp_root / "one" / "pre-release.json")
    generated_two = write_prerelease_manifest(temp_root / "two" / "pre-release.json")
    generated_one_public = _public_json(generated_one)
    generated_two_public = _public_json(generated_two)
    if generated_one_public != generated_two_public:
        raise AssertionError("generated prerelease manifest is nondeterministic")
    persisted = json.loads((temp_root / "one" / "pre-release.json").read_text(encoding="utf-8"))
    if persisted != generated_one_public:
        raise AssertionError("generated prerelease manifest write/read drift")
    generated_without_hash = dict(generated_one_public)
    manifest_hash = str(generated_without_hash.pop("manifest_hash"))
    if manifest_hash != hashlib.sha256(canonical_json(generated_without_hash)).hexdigest():
        raise AssertionError("generated prerelease manifest hash drift")

    distributions = _public_json([item.__dict__ for item in DISTRIBUTIONS])
    if generated_one_public.get("distributions") != distributions:
        raise AssertionError("generated prerelease distribution metadata drift")
    if any(row.get("status") != "configured" for row in distributions):
        raise AssertionError("distribution metadata unexpectedly claims a published state")

    metadata_snapshot = {
        "identity": release_value,
        "prerelease_metadata": prerelease_metadata(),
        "repository_identity": repository_identity,
        "pre_release": pre_release,
        "publish_readiness": readiness,
        "package_metadata": package_observed,
        "package_versions": package_versions,
        "generated_prerelease_manifest": generated_one_public,
    }
    digest = _sha(metadata_snapshot)
    frozen = fixture["frozen_snapshots"].get("release_metadata_sha256")
    if frozen is not None and frozen != digest:
        raise AssertionError(f"release metadata snapshot drift: {digest} != {frozen}")
    return {
        "snapshot_sha256": digest,
        "identity": release_value,
        "prerelease_metadata": prerelease_metadata(),
        "repository_identity": repository_identity,
        "pre_release": pre_release,
        "publish_readiness": readiness,
        "package_metadata": package_observed,
        "package_versions": package_versions,
        "generated_manifest_sha256": _sha(generated_one_public),
        "generated_manifest_hash": generated_one_public["manifest_hash"],
        "distributions": distributions,
    }


def _schema_registry_contract(fixture: dict[str, Any]) -> dict[str, Any]:
    config = fixture["schema_registry"]
    name = config["fixture_name"]
    errors = config["errors"]

    v1 = SchemaDefinition(
        name,
        1,
        required=("schema_version", "package", "version"),
        properties={"schema_version": int, "package": str, "version": str},
        allow_extra=False,
    )
    v2 = SchemaDefinition(
        name,
        2,
        required=("schema_version", "package", "version", "channel"),
        properties={"schema_version": int, "package": str, "version": str, "channel": str},
        allow_extra=False,
    )
    registry = SchemaRegistry()
    registry.register(v1)
    registry.register(v2)
    registry.register_migration(name, 1, lambda value: {**value, "channel": "pre-release"})

    v2_value = {"schema_version": 2, "package": "syntavra-runtime", "version": "0.0.1", "channel": "pre-release"}
    validated = registry.validate(name, v2_value)
    if validated != v2_value or validated is v2_value:
        raise AssertionError("schema registry validation copy semantics drift")

    v1_value = {"schema_version": 1, "package": "syntavra-runtime", "version": "0.0.1"}
    original_v1 = dict(v1_value)
    migrated = registry.migrate(name, v1_value)
    if migrated != v2_value:
        raise AssertionError(f"schema registry migration drift: {migrated}")
    if v1_value != original_v1:
        raise AssertionError("schema registry migration mutated source payload")

    catalog = registry.catalog()
    expected_catalog = {name: {"latest": config["latest"], "versions": config["versions"]}}
    if catalog != expected_catalog:
        raise AssertionError(f"schema registry catalog drift: {catalog} != {expected_catalog}")

    observed_errors: dict[str, str] = {}
    invalid_registry = SchemaRegistry()
    observed_errors["invalid_identity"] = _expect_error(
        "invalid identity",
        lambda: invalid_registry.register(SchemaDefinition("", 1)),
        errors["invalid_identity"],
    )
    observed_errors["duplicate_schema"] = _expect_error(
        "duplicate schema",
        lambda: registry.register(v2),
        errors["duplicate_schema"],
    )
    observed_errors["duplicate_migration"] = _expect_error(
        "duplicate migration",
        lambda: registry.register_migration(name, 1, lambda value: value),
        errors["duplicate_migration"],
    )

    missing_endpoint_registry = SchemaRegistry()
    missing_endpoint_registry.register(v1)
    observed_errors["missing_migration_endpoints"] = _expect_error(
        "missing migration endpoints",
        lambda: missing_endpoint_registry.register_migration(name, 1, lambda value: value),
        errors["missing_migration_endpoints"],
    )
    observed_errors["missing_required"] = _expect_error(
        "missing required property",
        lambda: registry.validate(name, {"schema_version": 2, "version": "0.0.1", "channel": "pre-release"}),
        errors["missing_required"],
    )
    observed_errors["unknown_property"] = _expect_error(
        "unknown property",
        lambda: registry.validate(name, {**v2_value, "extra": True}),
        errors["unknown_property"],
    )
    observed_errors["invalid_type"] = _expect_error(
        "invalid property type",
        lambda: registry.validate(name, {**v2_value, "package": 7}),
        errors["invalid_type"],
    )
    observed_errors["unknown_schema"] = _expect_error(
        "unknown schema",
        lambda: registry.validate(name, {**v2_value, "schema_version": 3}),
        errors["unknown_schema"],
    )
    observed_errors["downgrade_forbidden"] = _expect_error(
        "schema downgrade",
        lambda: registry.migrate(name, v2_value, target_version=1),
        errors["downgrade_forbidden"],
    )

    no_migration_registry = SchemaRegistry()
    no_migration_registry.register(v1)
    no_migration_registry.register(v2)
    observed_errors["missing_migration"] = _expect_error(
        "missing migration",
        lambda: no_migration_registry.migrate(name, v1_value),
        errors["missing_migration"],
    )
    if observed_errors != errors:
        raise AssertionError(f"schema registry failure envelope drift: {observed_errors} != {errors}")

    snapshot = {
        "catalog": catalog,
        "validated": validated,
        "migrated": migrated,
        "source_after_migration": v1_value,
        "errors": observed_errors,
    }
    digest = _sha(snapshot)
    frozen = fixture["frozen_snapshots"].get("schema_registry_sha256")
    if frozen is not None and frozen != digest:
        raise AssertionError(f"schema registry snapshot drift: {digest} != {frozen}")
    return {"snapshot_sha256": digest, **snapshot}


def certify(repo: Path) -> dict[str, Any]:
    fixture_path = repo / FIXTURE_RELATIVE
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="syntavra-python-publication-registry-") as directory:
        temp_root = Path(directory)
        result = {
            "public_cli": _public_cli_contract(fixture),
            "release": _release_contract(repo, fixture, temp_root),
            "schema_registry": _schema_registry_contract(fixture),
        }
    return {
        "ok": True,
        "schema_version": 1,
        "family": "publication-registry",
        "engine": "python",
        "exact_head": _head(repo),
        "fixture": str(FIXTURE_RELATIVE).replace("\\", "/"),
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        **result,
        "network_boundary": fixture["network_boundary"],
        "ownership_notes": fixture["ownership_notes"],
        "claim_boundary": fixture["release_identity"]["registry_publication_claim"],
        "nondeterministic_fields": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify Python publication/registry reference behavior")
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
            "family": "publication-registry",
            "engine": "python",
            "exact_head": _head(repo),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
