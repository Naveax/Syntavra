from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_PATHS = {
    Path("tools/_identity_claim_cleanup.py"),
    Path(".github/workflows/identity-claim-cleanup.yml"),
}
TEXT_SUFFIXES = {
    "", ".c", ".cc", ".cpp", ".css", ".go", ".h", ".hpp", ".html", ".ini",
    ".java", ".js", ".json", ".jsonl", ".md", ".mjs", ".py", ".rs", ".sh",
    ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}

BRAND_REPLACEMENTS = (
    ("SIGNALCORE", "SYNTAVRA"),
    ("SignalCore", "Syntavra"),
    ("signalcore", "syntavra"),
    ("Signal Core", "Syntavra"),
)

PATH_RENAMES = {
    "docs/COMPLETE_COMPETITIVE_FEATURE_SET_001.md": "docs/IMPLEMENTED_FEATURE_INVENTORY_001.md",
    "docs/COMPETITIVE_GAP_CLOSURE_001.md": "docs/COMPETITIVE_GAP_ASSESSMENT_001.md",
    "docs/P0_P2_CLOSURE_001.md": "docs/P0_P2_IMPLEMENTATION_STATUS_001.md",
    "tools/validate_competitive_gap_closure.py": "tools/validate_competitive_gap_assessment.py",
}

TEXT_REPLACEMENTS = (
    ("COMPLETE_COMPETITIVE_FEATURE_SET_001", "IMPLEMENTED_FEATURE_INVENTORY_001"),
    ("COMPETITIVE_GAP_CLOSURE_001", "COMPETITIVE_GAP_ASSESSMENT_001"),
    ("P0_P2_CLOSURE_001", "P0_P2_IMPLEMENTATION_STATUS_001"),
    ("validate_competitive_gap_closure", "validate_competitive_gap_assessment"),
    ("complete_competitive_feature_set", "implemented_feature_inventory"),
    ("competitive_gap_closure", "competitive_gap_assessment"),
    ("Complete Competitive Feature Set", "Implemented Feature Inventory"),
    ("complete competitive feature set", "implemented feature inventory"),
    ("Competitive Gap Closure", "Competitive Gap Assessment"),
    ("competitive gap closure", "competitive gap assessment"),
    ("P0–P2 Closure", "P0–P2 Implementation Status"),
    ("P0-P2 Closure", "P0-P2 Implementation Status"),
    ("all gaps closed", "all tracked implementation items addressed; external evidence gaps remain open"),
    ("closed technical competitive gaps", "implemented tracked technical gap work"),
    ("close technical competitive gaps", "implement tracked technical gap work"),
)


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def tracked_paths() -> list[Path]:
    return [Path(item) for item in run("git", "ls-files").splitlines() if item]


def is_text_path(path: Path) -> bool:
    return path.suffix.casefold() in TEXT_SUFFIXES and not any(
        part in {"node_modules", "target", "dist", "build", ".git"} for part in path.parts
    )


def rewrite_text(path: Path) -> None:
    if path in TEMP_PATHS or not is_text_path(path):
        return
    absolute = ROOT / path
    if not absolute.is_file():
        return
    try:
        text = absolute.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    updated = text
    for old, new in BRAND_REPLACEMENTS:
        updated = updated.replace(old, new)
    for old, new in TEXT_REPLACEMENTS:
        updated = updated.replace(old, new)
    updated = re.sub(r"\bsyntavra_runtime\s+syntavra_runtime\b", "syntavra_runtime", updated)
    if updated != text:
        absolute.write_text(updated, encoding="utf-8", newline="\n")


def rename_paths() -> None:
    for source, destination in PATH_RENAMES.items():
        src = ROOT / source
        dst = ROOT / destination
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            subprocess.check_call(["git", "mv", source, destination], cwd=ROOT)


def remove_legacy_namespace() -> None:
    legacy = ROOT / ("signal" + "core_runtime")
    if legacy.exists():
        shutil.rmtree(legacy)
    subprocess.run(["git", "rm", "-r", "--ignore-unmatch", "signal" + "core_runtime"], cwd=ROOT, check=False)


def normalize_pyproject() -> None:
    path = ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'include\s*=\s*\[[^\n]*\]', 'include = ["syntavra_runtime*"]', text, count=1)
    path.write_text(text, encoding="utf-8", newline="\n")


def normalize_validate() -> None:
    path = ROOT / "tools" / "validate.py"
    text = path.read_text(encoding="utf-8")
    required_line = '    ROOT / "syntavra_runtime" / "__init__.py",'
    seen = False
    lines: list[str] = []
    for line in text.splitlines():
        if line == required_line:
            if seen:
                continue
            seen = True
        lines.append(line)
    text = "\n".join(lines) + "\n"
    text = text.replace(
        'ROOT / "syntavra_runtime", ROOT / "syntavra_runtime",',
        'ROOT / "syntavra_runtime",',
    )
    if 'ROOT / "tools" / "validate_claim_integrity.py"' not in text:
        anchor = '    ROOT / "tools" / "check_repository_hygiene.py",\n'
        text = text.replace(anchor, anchor + '    ROOT / "tools" / "validate_claim_integrity.py",\n')
    path.write_text(text, encoding="utf-8", newline="\n")


def normalize_hygiene() -> None:
    path = ROOT / "tools" / "check_repository_hygiene.py"
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r'"legacy-product-name":\s*re\.compile\([^\n]+\),',
        '"legacy-product-name": re.compile(r"\\bsignal[\\s_-]*core\\b", re.IGNORECASE),',
        text,
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def normalize_readme() -> None:
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace("## Competitive feature set", "## Implemented code surfaces")
    text = text.replace(
        "Syntavra 0.0.1 now includes a fail-closed",
        "The Syntavra 0.0.1 codebase implements a fail-closed",
    )
    inventory = (
        "The configured registry currently contains 118 fail-closed rewrite rules, 131 command-specific "
        "compactors, 44 controlled host contracts, 48 provider presets, a credential-reference-only "
        "provider account pool, and a 30-language parser registry with optional tree-sitter support. "
        "These are code-inventory counts, not live certification, measured quality, adoption, or market "
        "superiority. Registry publication and provider-billed competitor results remain external, "
        "credential-gated actions. See `docs/IMPLEMENTED_FEATURE_INVENTORY_001.md`."
    )
    text = re.sub(
        r"The implementation registry currently exposes 118 fail-closed rewrite rules,.*?See `docs/IMPLEMENTED_FEATURE_INVENTORY_001\.md`\.",
        inventory,
        text,
        flags=re.DOTALL,
    )
    if "- `docs/CLAIM_POLICY.md`" not in text:
        text = text.replace("- `docs/OPERATIONS.md`\n", "- `docs/OPERATIONS.md`\n- `docs/CLAIM_POLICY.md`\n")
    path.write_text(text, encoding="utf-8", newline="\n")


def write_claim_policy() -> None:
    path = ROOT / "docs" / "CLAIM_POLICY.md"
    path.write_text(
        """# Syntavra claim policy\n\n"
        "Syntavra 0.0.1 is a pre-release project. Public statements must distinguish code inventory, "
        "internal verification, provider-observed measurement, independent verification, and real-world "
        "adoption.\n\n"
        "## Evidence tiers\n\n"
        "1. `DECLARED`: configuration or contract exists in source.\n"
        "2. `INTERNAL_VERIFIED`: deterministic tests passed in the project repository.\n"
        "3. `PROVIDER_OBSERVED`: provider receipts record usage and cost for verified work.\n"
        "4. `INDEPENDENT_VERIFIED`: an unaffiliated party reproduced the result.\n"
        "5. `ADOPTION_OBSERVED`: real users and sustained workloads produced public evidence.\n\n"
        "Code counts may be published only as generated inventory and must never be presented as live "
        "certification, quality, savings, market rank, or maturity. Forecasts, synthetic fixtures, local "
        "tokenization, and unpaired runs cannot support competitor claims.\n\n"
        "Absolute completion, market-leadership, and production-maturity wording is prohibited unless the "
        "corresponding external evidence is linked and machine-verifiable.\n\n"
        "## Current boundary\n\n"
        "```text\n"
        "EXTERNAL_SUPERIORITY_NOT_PROVEN\n"
        "MEASURED_AGENT_BENCHMARK_NOT_PROVEN\n"
        "LIVE_INTEGRATION_CERTIFICATION_NOT_PROVEN\n"
        "PUBLIC_PRODUCT_MATURITY_NOT_PROVEN\n"
        "REGISTRY_PUBLICATION_NOT_PERFORMED\n"
        "```\n",
        encoding="utf-8",
        newline="\n",
    )


def write_claim_validator() -> None:
    path = ROOT / "tools" / "validate_claim_integrity.py"
    path.write_text(
        '''#!/usr/bin/env python3\nfrom __future__ import annotations\n\nimport json\nimport re\nimport subprocess\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nTEXT_SUFFIXES = {"", ".json", ".md", ".mjs", ".py", ".rs", ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml"}\nCLAIM_SURFACES = {"README.md", "BENCHMARKS.md", "CHANGELOG.md", "llms.txt"}\nLEGACY = re.compile(r"(?i)\\bsignal[\\s_-]*core\\b")\nINFLATED = {\n    "absolute-feature-completion": re.compile(r"(?i)\\bcomplete competitive feature set\\b"),\n    "absolute-gap-closure": re.compile(r"(?i)\\bcompetitive gap closure\\b|\\ball (?:competitive )?gaps (?:are )?closed\\b"),\n    "unsupported-production-maturity": re.compile(r"(?i)\\bproduction[- ]ready\\b"),\n    "unsupported-market-rank": re.compile(r"(?i)\\bbest[- ]in[- ]class\\b|\\bindustry[- ]leading\\b"),\n    "unsupported-savings-percent": re.compile(r"(?i)\\b(?:99|100)%\\s+(?:token|cost|context)\\s+(?:saving|savings|reduction)\\b"),\n}\nSKIP = {"tools/validate_claim_integrity.py"}\n\ndef tracked() -> list[Path]:\n    try:\n        raw = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)\n        return [Path(row) for row in raw.splitlines() if row]\n    except (OSError, subprocess.CalledProcessError):\n        return [path.relative_to(ROOT) for path in ROOT.rglob("*") if path.is_file()]\n\ndef is_claim_surface(path: Path) -> bool:\n    return path.as_posix() in CLAIM_SURFACES or (path.parts and path.parts[0] in {"docs", "release", "skills"})\n\ndef audit_repository() -> dict[str, object]:\n    failures: list[str] = []\n    scanned = 0\n    for relative in tracked():\n        if relative.as_posix() in SKIP or relative.suffix.casefold() not in TEXT_SUFFIXES:\n            continue\n        absolute = ROOT / relative\n        if not absolute.is_file():\n            continue\n        try:\n            text = absolute.read_text(encoding="utf-8")\n        except UnicodeDecodeError:\n            continue\n        scanned += 1\n        if LEGACY.search(text):\n            failures.append(f"legacy-identity:{relative.as_posix()}")\n        if is_claim_surface(relative):\n            for label, pattern in INFLATED.items():\n                if pattern.search(text):\n                    failures.append(f"{label}:{relative.as_posix()}")\n    readme = (ROOT / "README.md").read_text(encoding="utf-8")\n    if "code-inventory counts, not live certification" not in readme:\n        failures.append("readme-missing-inventory-boundary")\n    if not (ROOT / "docs" / "CLAIM_POLICY.md").is_file():\n        failures.append("missing-claim-policy")\n    legacy_dir = ROOT / ("signal" + "core_runtime")\n    if legacy_dir.exists():\n        failures.append("legacy-namespace-directory")\n    return {"ok": not failures, "scanned_text_files": scanned, "failures": failures}\n\ndef main() -> int:\n    result = audit_repository()\n    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))\n    return 0 if result["ok"] else 1\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
        encoding="utf-8",
        newline="\n",
    )


def write_claim_test() -> None:
    path = ROOT / "tests" / "runtime" / "test_claim_integrity_v001.py"
    path.write_text(
        '''from pathlib import Path\n\nfrom tools.validate_claim_integrity import audit_repository\n\n\ndef test_repository_claim_integrity() -> None:\n    result = audit_repository()\n    assert result["ok"], result\n\n\ndef test_only_canonical_runtime_namespace_exists() -> None:\n    legacy = Path("signal" + "core_runtime")\n    assert not legacy.exists()\n    assert Path("syntavra_runtime").is_dir()\n''',
        encoding="utf-8",
        newline="\n",
    )


def append_changelog() -> None:
    path = ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    note = (
        "\n### Identity and claim integrity\n\n"
        "- Removed the pre-rename compatibility namespace and converted all managed markers, tests, "
        "installer paths, workflows, and package discovery to the canonical Syntavra identity.\n"
        "- Replaced absolute completion/closure language with evidence-gated implementation inventory "
        "and assessment terminology.\n"
        "- Added repository-wide claim-integrity validation and an explicit public claim policy.\n"
    )
    if "### Identity and claim integrity" not in text:
        text = text.rstrip() + "\n" + note
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    remove_legacy_namespace()
    rename_paths()
    for path in tracked_paths():
        rewrite_text(path)
    normalize_pyproject()
    normalize_validate()
    normalize_hygiene()
    normalize_readme()
    write_claim_policy()
    write_claim_validator()
    write_claim_test()
    append_changelog()
    for path in tracked_paths():
        rewrite_text(path)
    print(json.dumps({"ok": True, "renames": PATH_RENAMES}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
