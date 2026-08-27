#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import tmp_runtime_contract_graph_rebuild_v4 as h

ROOT = h.ROOT
TEMP_PATHS = (
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v2.yml",
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v3.yml",
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v4.yml",
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v5.yml",
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v6.yml",
    ".github/workflows/tmp-runtime-contract-version-graph-rebuild-v7.yml",
    "tools/tmp_runtime_contract_graph_rebuild_v4.py",
    "tools/tmp_runtime_contract_graph_rebuild_v7.py",
)


def run(*args: str, capture: bool = False) -> str:
    return h.run(*args, capture=capture)


def require(condition: bool, message: str) -> None:
    h.require(condition, message)


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_precommit_reports() -> None:
    graph = load("/tmp/runtime-contract-version-graph.json")
    completeness = load("/tmp/python-capability-completeness.json")
    rust = load("/tmp/rust-feature-freeze-guard.json")

    require(graph.get("ok") is True, f"graph certifier red: {graph}")
    require(graph.get("lifecycle_state") == "implemented", f"graph lifecycle drift: {graph}")
    require(graph.get("python_complete_ready") is True, f"graph Python COMPLETE drift: {graph}")
    require(
        graph.get("post_completion_current_milestone") == "runtime_contract_version_graph_v1",
        f"graph post-completion milestone drift: {graph}",
    )
    require(graph.get("rust_resume_allowed") is False, f"graph Rust resume drift: {graph}")
    runtime = graph.get("runtime") or {}
    require(runtime.get("transitive_reverse_dependency_invalidation") is True, f"graph invalidation drift: {graph}")
    require(runtime.get("external_contracts_are_metadata_only_leaves") is True, f"graph metadata-leaf drift: {graph}")

    require(completeness.get("ok") is True, f"completeness red: {completeness}")
    require(completeness.get("current_milestone") == "python_complete", f"Python milestone drift: {completeness}")
    require(
        completeness.get("post_completion_current_milestone") == "runtime_contract_version_graph_v1",
        f"post-completion milestone drift: {completeness}",
    )
    require(
        completeness.get("post_completion_milestone_order") == ["runtime_contract_version_graph_v1"],
        f"post-completion order drift: {completeness}",
    )
    require(completeness.get("python_complete_ready") is True, f"Python COMPLETE reopened: {completeness}")
    require(completeness.get("rust_resume_allowed") is False, f"Rust resume opened: {completeness}")
    require(completeness.get("uncertified_required_count") == 0, f"required capability regression: {completeness}")
    require(
        (completeness.get("current_state_report_consistency") or {}).get("stale_surfaces") == [],
        f"stale current-state report surfaces: {completeness}",
    )

    require(rust.get("ok") is True, f"Rust freeze certifier red: {rust}")
    require(rust.get("python_complete_ready") is True, f"Rust freeze lost Python COMPLETE: {rust}")
    require(rust.get("rust_resume_allowed") is False, f"Rust resume opened: {rust}")
    state = rust.get("rust") or {}
    require(state.get("feature_development_frozen") is True, f"Rust feature freeze opened: {rust}")
    require(state.get("production_promotion_frozen") is True, f"Rust promotion freeze opened: {rust}")
    require(state.get("production_promoted") == 174, f"Rust promotion counter drift: {rust}")
    require(state.get("remaining_parity_promotion") == 71, f"Rust remaining counter drift: {rust}")


def validate_completion_report() -> None:
    report = load("/tmp/python-completion-certificate.json")
    require(report.get("ok") is True, f"completion certifier red: {report}")
    require(report.get("python_complete_ready") is True, f"completion lost Python COMPLETE: {report}")
    require(report.get("rust_resume_allowed") is False, f"completion opened Rust resume: {report}")
    require((report.get("gates") or {}).get("python_contract_freeze") is True, f"completion contract freeze red: {report}")
    freeze = report.get("contract_freeze") or {}
    require(
        freeze.get("mode") == "registry-derived-certified-python-contracts-completion-projection-v1",
        f"completion freeze mode drift: {report}",
    )
    require(
        freeze.get("sha256") == "1e36d28b7958b64b21916b83b0a8cfdd5d9f963fcf9351fc8ab54e496b806dbe",
        f"completion freeze digest drift: {report}",
    )


def cleanup_generated() -> None:
    subprocess.run(["rm", "-rf", "syntavra_runtime.egg-info"], cwd=ROOT, check=False)
    for relative in (
        "fusion-release-smoke.json",
        "platform-registry.json",
        "native-dry-run.json",
    ):
        (ROOT / relative).unlink(missing_ok=True)


def main() -> int:
    os.chdir(ROOT)
    run("git", "merge-base", "--is-ancestor", h.BASE_SHA, "HEAD")

    run(
        "git",
        "fetch",
        "--no-tags",
        "origin",
        "refs/heads/agent/runtime-contract-version-graph-v1-post-completion-prep:refs/remotes/origin/runtime-contract-version-graph-v1-post-completion-prep",
    )
    observed_old = run(
        "git",
        "rev-parse",
        "refs/remotes/origin/runtime-contract-version-graph-v1-post-completion-prep",
        capture=True,
    ).strip()
    require(observed_old == h.OLD_HEAD_SHA, f"old 240 head moved: {observed_old}")
    observed_parent = run("git", "rev-parse", f"{h.OLD_HEAD_SHA}^", capture=True).strip()
    require(observed_parent == h.OLD_BASE_SHA, f"old 240 parent moved: {observed_parent}")

    patch_path = Path("/tmp/runtime-contract-version-graph-v1.patch")
    with patch_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(
            [
                "git",
                "diff",
                "--binary",
                h.OLD_BASE_SHA,
                h.OLD_HEAD_SHA,
                "--",
                ".",
                ":(exclude)MANIFEST.sha256",
            ],
            cwd=ROOT,
            text=True,
            stdout=handle,
            check=False,
        )
    require(proc.returncode == 0 and patch_path.stat().st_size > 0, "unable to materialize old 240 patch")
    run("git", "apply", "--3way", "--index", str(patch_path))
    conflicts = run("git", "diff", "--name-only", "--diff-filter=U", capture=True).strip()
    require(not conflicts, f"3-way conflicts remain: {conflicts}")

    h.patch_completion_freeze()
    h.patch_completion_tests()
    h.patch_completion_contract_shell()

    for relative in TEMP_PATHS:
        run("git", "rm", "-f", "--ignore-unmatch", relative)

    freeze = h.compute_and_pin_freeze()
    require(freeze["contract_count"] == 20, f"completion freeze contract-count drift: {freeze}")
    require(
        freeze["sha256"] == "1e36d28b7958b64b21916b83b0a8cfdd5d9f963fcf9351fc8ab54e496b806dbe",
        f"completion freeze digest drift before commit: {freeze}",
    )

    run(sys.executable, "tools/refresh_manifest.py")
    run("git", "add", "-A")
    h.assert_paths()

    run(sys.executable, "-m", "unittest", "tests.runtime.test_runtime_contract_version_graph_v1", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_python_capability_completeness", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_python_completion_certificate_v1", "-v")
    run(sys.executable, "-m", "unittest", "tests.runtime.test_release_action_pins", "-v")

    run(
        sys.executable,
        "tools/certify_runtime_contract_version_graph_v1.py",
        "--repo",
        ".",
        "--out",
        "/tmp/runtime-contract-version-graph.json",
    )
    run(
        sys.executable,
        "tools/certify_python_capability_completeness.py",
        "--out",
        "/tmp/python-capability-completeness.json",
    )
    run(
        sys.executable,
        "tools/certify_rust_feature_freeze_guard.py",
        "--out",
        "/tmp/rust-feature-freeze-guard.json",
    )
    validate_precommit_reports()

    cleanup_generated()
    run("git", "diff", "--check")
    run("git", "add", "-A")
    conflicts = run("git", "diff", "--name-only", "--diff-filter=U", capture=True).strip()
    require(not conflicts, f"conflicts before commit: {conflicts}")
    run("git", "config", "user.name", "Naveax")
    run("git", "config", "user.email", "Omersevik095@gmail.com")
    run("git", "commit", "-m", "python: rebuild runtime contract version graph v1 after completion")

    clean = run("git", "status", "--porcelain", "--untracked-files=all", capture=True).strip()
    require(not clean, f"local candidate commit is dirty before exact-head certification: {clean}")

    run(
        sys.executable,
        "tools/certify_python_completion_certificate_v1.py",
        "--out",
        "/tmp/python-completion-certificate.json",
    )
    validate_completion_report()

    run(sys.executable, "tools/validate.py")
    run(
        sys.executable,
        "tools/validate_release.py",
        "--smoke",
        "--output",
        "/tmp/runtime-contract-version-graph-release-validation.json",
    )
    cleanup_generated()
    run(sys.executable, "tools/refresh_manifest.py", "--check")

    run(
        sys.executable,
        "tools/certify_runtime_contract_version_graph_v1.py",
        "--repo",
        ".",
        "--out",
        "/tmp/runtime-contract-version-graph-postcommit.json",
    )
    run(
        sys.executable,
        "tools/certify_python_capability_completeness.py",
        "--out",
        "/tmp/python-capability-completeness-postcommit.json",
    )
    run(
        sys.executable,
        "tools/certify_rust_feature_freeze_guard.py",
        "--out",
        "/tmp/rust-feature-freeze-guard-postcommit.json",
    )
    cleanup_generated()
    run("git", "diff", "--check")
    status = run("git", "status", "--porcelain", "--untracked-files=all", capture=True).strip()
    require(not status, f"validated candidate worktree dirty: {status}")

    final_commit = run("git", "rev-parse", "HEAD", capture=True).strip()
    final_tree = run("git", "rev-parse", "HEAD^{tree}", capture=True).strip()
    receipt = {
        "base_sha": h.BASE_SHA,
        "final_commit": final_commit,
        "final_tree": final_tree,
        "freeze_sha256": freeze["sha256"],
        "permanent_paths": h.EXPECTED_PATHS,
        "python_complete_ready": True,
        "rust_resume_allowed": False,
        "rust_production_promoted": 174,
        "rust_remaining_parity_promotion": 71,
    }
    Path("/tmp/runtime-contract-version-graph-rebuild-v7.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"FINAL_COMMIT={final_commit}")
    print(f"FINAL_TREE={final_tree}")
    run("git", "push", "origin", f"HEAD:refs/heads/{h.TARGET_BRANCH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
