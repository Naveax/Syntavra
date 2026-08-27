#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "c0dc3c7689aaad08f0f7d92fe9544b32f76cbacf"
OLD_BASE_SHA = "1292e0d3f4cee4c156006e165052fd1165067fdd"
OLD_HEAD_SHA = "5844faa70501c792b70b2334ad2c82bd7adeae3d"
TARGET_BRANCH = "automation/runtime-contract-version-graph-rebuild-v2-20260827"
TEMP_PATHS = (
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v2.yml",
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v3.yml",
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v4.yml",
    "tools/tmp_runtime_contract_graph_rebuild_v4.py",
)
EXPECTED_PATHS = sorted(
    (
        ".github/workflows/release-main-merge-gate.yml",
        ".github/workflows/runtime-contract-version-graph.yml",
        "MANIFEST.sha256",
        "contracts/python/capability-completeness-registry-v1.json",
        "contracts/python/python-completion-certificate-v1.json",
        "contracts/python/runtime-contract-version-graph-v1.json",
        "syntavra_runtime/contract_version_graph.py",
        "tests/runtime/test_python_capability_completeness.py",
        "tests/runtime/test_python_completion_certificate_v1.py",
        "tests/runtime/test_release_action_pins.py",
        "tests/runtime/test_runtime_contract_version_graph_v1.py",
        "tools/certify_python_capability_completeness.py",
        "tools/certify_python_completion_certificate_v1.py",
        "tools/certify_runtime_contract_version_graph_v1.py",
    )
)


def run(*args: str, capture: bool = False) -> str:
    proc = subprocess.run(
        list(args),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if proc.returncode:
        if capture:
            print(proc.stdout, end="")
            print(proc.stderr, end="", file=sys.stderr)
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args)}")
    return proc.stdout if capture else ""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def patch_completion_freeze() -> None:
    path = ROOT / "tools/certify_python_completion_certificate_v1.py"
    text = path.read_text(encoding="utf-8")

    anchor = "\ndef derive_contract_freeze(repo: Path, registry: dict[str, Any]) -> dict[str, Any]:\n"
    require(anchor in text, "completion freeze function anchor missing")
    projection = '''
def _completion_registry_projection(registry: dict[str, Any]) -> dict[str, Any]:
    """Project the registry to state authoritative for the Python COMPLETE decision.

    Post-completion tracking stays in the canonical registry but must not
    retroactively rewrite the already-admitted completion corpus. Completion
    authority, policy, milestone order, required capability rows, and persisted
    completion state remain hash-bound.
    """
    policy = {
        str(key): value
        for key, value in (registry.get("policy") or {}).items()
        if not str(key).startswith("post_completion_")
    }
    required_capabilities = [
        capability
        for capability in (registry.get("capabilities") or [])
        if isinstance(capability, dict)
        and capability.get("required_for_python_complete") is True
    ]
    return {
        "schema_version": registry.get("schema_version"),
        "family": registry.get("family"),
        "phase": registry.get("phase"),
        "claim": registry.get("claim"),
        "strict": registry.get("strict"),
        "authority": registry.get("authority"),
        "state_vocabulary": registry.get("state_vocabulary"),
        "classification_vocabulary": registry.get("classification_vocabulary"),
        "policy": policy,
        "milestone_order": registry.get("milestone_order"),
        "capabilities": required_capabilities,
        "python_complete": registry.get("python_complete"),
    }

'''
    text = text.replace(anchor, "\n" + projection + anchor.lstrip("\n"), 1)

    old_hash = "            rows[relative] = _sha256_bytes(path.read_bytes())\n"
    new_hash = '''            if relative == REGISTRY.as_posix():
                rows[relative] = _sha256_bytes(
                    canonical_json(_completion_registry_projection(registry))
                )
            else:
                rows[relative] = _sha256_bytes(path.read_bytes())
'''
    require(old_hash in text, "completion registry raw-hash anchor missing")
    text = text.replace(old_hash, new_hash, 1)

    old_mode = '        "mode": "registry-derived-certified-python-contracts",\n'
    new_mode = '        "mode": "registry-derived-certified-python-contracts-completion-projection-v1",\n'
    require(old_mode in text, "completion freeze mode anchor missing")
    text = text.replace(old_mode, new_mode, 1)

    old_validation = '''    _require(freeze_cfg.get("mode") == freeze["mode"], "Python contract freeze mode drift")
    _require(int(freeze_cfg.get("expected_contract_count", -1)) == freeze["contract_count"], "Python contract freeze count drift")
'''
    new_validation = '''    _require(freeze_cfg.get("mode") == freeze["mode"], "Python contract freeze mode drift")
    _require(
        freeze_cfg.get("registry_projection") == "completion-relevant-fields-and-required-capabilities-v1",
        "Python contract freeze registry projection drift",
    )
    _require(
        freeze_cfg.get("post_completion_registry_extensions_excluded") is True,
        "post-completion registry extensions must remain outside the completed Python freeze",
    )
    _require(int(freeze_cfg.get("expected_contract_count", -1)) == freeze["contract_count"], "Python contract freeze count drift")
'''
    require(old_validation in text, "completion freeze validation anchor missing")
    text = text.replace(old_validation, new_validation, 1)
    path.write_text(text, encoding="utf-8")


def patch_completion_tests() -> None:
    path = ROOT / "tests/runtime/test_python_completion_certificate_v1.py"
    text = path.read_text(encoding="utf-8")
    if "import copy\n" not in text:
        text = text.replace(
            "from __future__ import annotations\n\n",
            "from __future__ import annotations\n\nimport copy\n",
            1,
        )
    anchor = "    def test_platform_receipts_require_both_exact_head_operating_systems(self) -> None:\n"
    require(anchor in text, "completion test insertion anchor missing")
    addition = '''    def test_post_completion_registry_extensions_do_not_rewrite_completion_freeze(self) -> None:
        registry = self._registry()
        baseline = derive_contract_freeze(ROOT, registry)
        mutated = copy.deepcopy(registry)
        mutated.setdefault("policy", {})["post_completion_future_hardening"] = True
        mutated.setdefault("post_completion_milestone_order", []).append(
            "future_post_completion_capability"
        )
        mutated.setdefault("capabilities", []).append(
            {
                "id": "future_post_completion_capability",
                "group": "post-completion-test",
                "state": "planned",
                "classification": "NEW",
                "required_for_python_complete": False,
                "implementation_evidence": [],
                "certification_evidence": [],
                "acceptance": "test-only post-completion extension",
            }
        )
        observed = derive_contract_freeze(ROOT, mutated)
        self.assertEqual(observed["sha256"], baseline["sha256"])
        self.assertEqual(observed["contracts"], baseline["contracts"])

    def test_completion_relevant_registry_drift_changes_completion_freeze(self) -> None:
        registry = self._registry()
        baseline = derive_contract_freeze(ROOT, registry)
        mutated = copy.deepcopy(registry)
        row = next(
            item
            for item in mutated["capabilities"]
            if item.get("required_for_python_complete") is True
        )
        row["acceptance"] = str(row.get("acceptance") or "") + " completion-drift"
        observed = derive_contract_freeze(ROOT, mutated)
        self.assertNotEqual(observed["sha256"], baseline["sha256"])

'''
    text = text.replace(anchor, addition + anchor, 1)
    path.write_text(text, encoding="utf-8")


def patch_completion_contract_shell() -> None:
    path = ROOT / "contracts/python/python-completion-certificate-v1.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    freeze = contract.setdefault("contract_freeze", {})
    freeze["mode"] = "registry-derived-certified-python-contracts-completion-projection-v1"
    freeze["registry_projection"] = "completion-relevant-fields-and-required-capabilities-v1"
    freeze["post_completion_registry_extensions_excluded"] = True
    freeze["post_completion_exclusion_boundary"] = (
        "Only post-completion policy/order/capability extensions with "
        "required_for_python_complete=false are excluded; completion-relevant "
        "authority, policy, milestone order, required capability rows, and "
        "persisted completion state remain hash-bound."
    )
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compute_and_pin_freeze() -> dict:
    code = r'''
import json
from pathlib import Path
from tools.certify_python_completion_certificate_v1 import derive_contract_freeze
root = Path('.').resolve()
registry = json.loads((root / 'contracts/python/capability-completeness-registry-v1.json').read_text(encoding='utf-8'))
print(json.dumps(derive_contract_freeze(root, registry), sort_keys=True))
'''
    raw = run(sys.executable, "-c", code, capture=True).strip()
    freeze = json.loads(raw)
    path = ROOT / "contracts/python/python-completion-certificate-v1.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["contract_freeze"]["expected_contract_count"] = freeze["contract_count"]
    contract["contract_freeze"]["expected_sha256"] = freeze["sha256"]
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(freeze, indent=2, sort_keys=True))
    return freeze


def assert_paths() -> None:
    actual = sorted(
        line
        for line in run("git", "diff", "--name-only", BASE_SHA, capture=True).splitlines()
        if line
    )
    print("expected permanent paths:")
    print("\n".join(EXPECTED_PATHS))
    print("actual permanent paths:")
    print("\n".join(actual))
    require(actual == EXPECTED_PATHS, f"permanent path drift: {actual}")
    require(len(actual) == 14, f"expected 14 permanent paths, got {len(actual)}")
    require(not any("tmp-runtime-contract" in p or "tmp_runtime_contract" in p for p in actual), "temporary helper leaked into final diff")


def load_report(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_reports() -> None:
    graph = load_report("/tmp/runtime-contract-version-graph.json")
    completeness = load_report("/tmp/python-capability-completeness.json")
    completion = load_report("/tmp/python-completion-certificate.json")
    rust = load_report("/tmp/rust-feature-freeze-guard.json")

    require(graph.get("ok") is True, f"graph certifier red: {graph}")
    require(graph.get("lifecycle_state") == "implemented", f"graph lifecycle drift: {graph}")
    require(graph.get("python_complete_ready") is True, f"graph Python COMPLETE drift: {graph}")
    require(graph.get("post_completion_current_milestone") == "runtime_contract_version_graph_v1", f"graph milestone drift: {graph}")
    require(graph.get("rust_resume_allowed") is False, f"graph Rust resume drift: {graph}")

    require(completeness.get("ok") is True, f"completeness red: {completeness}")
    require(completeness.get("current_milestone") == "python_complete", f"completion milestone drift: {completeness}")
    require(completeness.get("post_completion_current_milestone") == "runtime_contract_version_graph_v1", f"post milestone drift: {completeness}")
    require(completeness.get("post_completion_milestone_order") == ["runtime_contract_version_graph_v1"], f"post order drift: {completeness}")
    require(completeness.get("python_complete_ready") is True, f"completeness Python COMPLETE drift: {completeness}")
    require(completeness.get("rust_resume_allowed") is False, f"completeness Rust resume drift: {completeness}")
    require((completeness.get("current_state_report_consistency") or {}).get("stale_surfaces") == [], f"stale report surfaces: {completeness}")

    require(completion.get("ok") is True, f"completion certifier red: {completion}")
    require(completion.get("python_complete_ready") is True, f"completion ready drift: {completion}")
    require(completion.get("rust_resume_allowed") is False, f"completion Rust resume drift: {completion}")
    require((completion.get("gates") or {}).get("python_contract_freeze") is True, f"completion freeze red: {completion}")
    require((completion.get("contract_freeze") or {}).get("mode") == "registry-derived-certified-python-contracts-completion-projection-v1", f"completion freeze mode drift: {completion}")

    require(rust.get("ok") is True, f"Rust freeze certifier red: {rust}")
    require(rust.get("python_complete_ready") is True, f"Rust freeze Python COMPLETE drift: {rust}")
    require(rust.get("rust_resume_allowed") is False, f"Rust resume opened: {rust}")
    rust_state = rust.get("rust") or {}
    require(rust_state.get("feature_development_frozen") is True, f"Rust feature freeze opened: {rust}")
    require(rust_state.get("production_promotion_frozen") is True, f"Rust production promotion opened: {rust}")
    require(rust_state.get("production_promoted") == 174, f"Rust promotion count drift: {rust}")
    require(rust_state.get("remaining_parity_promotion") == 71, f"Rust remaining count drift: {rust}")


def main() -> int:
    os.chdir(ROOT)
    run("git", "merge-base", "--is-ancestor", BASE_SHA, "HEAD")

    run(
        "git",
        "fetch",
        "--no-tags",
        "origin",
        "refs/heads/agent/runtime-contract-version-graph-v1-post-completion-prep:refs/remotes/origin/runtime-contract-version-graph-v1-post-completion-prep",
    )
    observed_old = run("git", "rev-parse", "refs/remotes/origin/runtime-contract-version-graph-v1-post-completion-prep", capture=True).strip()
    require(observed_old == OLD_HEAD_SHA, f"old 240 head moved: {observed_old}")
    observed_parent = run("git", "rev-parse", f"{OLD_HEAD_SHA}^", capture=True).strip()
    require(observed_parent == OLD_BASE_SHA, f"old 240 parent moved: {observed_parent}")

    patch_path = Path("/tmp/runtime-contract-version-graph-v1.patch")
    with patch_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(
            ["git", "diff", "--binary", OLD_BASE_SHA, OLD_HEAD_SHA, "--", ".", ":(exclude)MANIFEST.sha256"],
            cwd=ROOT,
            text=True,
            stdout=handle,
            check=False,
        )
    require(proc.returncode == 0 and patch_path.stat().st_size > 0, "unable to materialize old 240 patch")
    run("git", "apply", "--3way", "--index", str(patch_path))
    conflicts = run("git", "diff", "--name-only", "--diff-filter=U", capture=True).strip()
    require(not conflicts, f"3-way conflicts remain: {conflicts}")

    patch_completion_freeze()
    patch_completion_tests()
    patch_completion_contract_shell()

    for relative in TEMP_PATHS:
        run("git", "rm", "-f", "--ignore-unmatch", relative)

    freeze = compute_and_pin_freeze()
    require(freeze["contract_count"] == 20, f"completion freeze contract count drift: {freeze}")
    run(sys.executable, "tools/refresh_manifest.py")
    run("git", "add", "-A")
    assert_paths()

    run(sys.executable, "-m", "pip", "install", "-e", ".")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_runtime_contract_version_graph_v1", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_python_capability_completeness", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_python_completion_certificate_v1", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_release_action_pins", "-v")

    run(sys.executable, "tools/certify_runtime_contract_version_graph_v1.py", "--repo", ".", "--out", "/tmp/runtime-contract-version-graph.json")
    run(sys.executable, "tools/certify_python_capability_completeness.py", "--out", "/tmp/python-capability-completeness.json")
    run(sys.executable, "tools/certify_python_completion_certificate_v1.py", "--out", "/tmp/python-completion-certificate.json")
    run(sys.executable, "tools/certify_rust_feature_freeze_guard.py", "--out", "/tmp/rust-feature-freeze-guard.json")
    validate_reports()

    run(sys.executable, "tools/validate.py")
    run(sys.executable, "tools/validate_release.py", "--smoke", "--output", "/tmp/runtime-contract-version-graph-release-validation.json")
    run(sys.executable, "tools/refresh_manifest.py", "--check")

    for relative in ("syntavra_runtime.egg-info",):
        subprocess.run(["rm", "-rf", relative], cwd=ROOT, check=False)
    for relative in ("fusion-release-smoke.json", "platform-registry.json", "native-dry-run.json"):
        (ROOT / relative).unlink(missing_ok=True)
    run("git", "diff", "--check")
    run("git", "add", "-A")
    conflicts = run("git", "diff", "--name-only", "--diff-filter=U", capture=True).strip()
    require(not conflicts, f"conflicts before commit: {conflicts}")

    run("git", "config", "user.name", "Naveax")
    run("git", "config", "user.email", "Omersevik095@gmail.com")
    run("git", "commit", "-m", "python: rebuild runtime contract version graph v1 after completion")

    run(sys.executable, "tools/refresh_manifest.py", "--check")
    run(sys.executable, "tools/certify_runtime_contract_version_graph_v1.py", "--repo", ".", "--out", "/tmp/runtime-contract-version-graph-postcommit.json")
    run(sys.executable, "tools/certify_python_capability_completeness.py", "--out", "/tmp/python-capability-completeness-postcommit.json")
    run(sys.executable, "tools/certify_python_completion_certificate_v1.py", "--out", "/tmp/python-completion-certificate-postcommit.json")
    subprocess.run(["rm", "-rf", "syntavra_runtime.egg-info"], cwd=ROOT, check=False)
    status = run("git", "status", "--porcelain", "--untracked-files=all", capture=True).strip()
    require(not status, f"postcommit worktree dirty: {status}")

    final_commit = run("git", "rev-parse", "HEAD", capture=True).strip()
    final_tree = run("git", "rev-parse", "HEAD^{tree}", capture=True).strip()
    receipt = {
        "base_sha": BASE_SHA,
        "final_commit": final_commit,
        "final_tree": final_tree,
        "freeze_sha256": freeze["sha256"],
        "permanent_paths": EXPECTED_PATHS,
    }
    Path("/tmp/runtime-contract-version-graph-rebuild-v4.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"FINAL_COMMIT={final_commit}")
    print(f"FINAL_TREE={final_tree}")
    run("git", "push", "origin", f"HEAD:refs/heads/{TARGET_BRANCH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
