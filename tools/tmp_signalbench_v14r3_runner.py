from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

TARGET_BRANCH = os.environ.get("TARGET_BRANCH", "agent/signalbench-python-product-v1")
TARGET_HEAD = os.environ.get("TARGET_HEAD", "")
MAIN_BASE = os.environ.get("MAIN_BASE", "")
TEST_PATH = Path("tests/runtime/test_token_saver_unification_v001.py")
MANIFEST_PATH = Path("MANIFEST.sha256")

FRESH_MAIN_WORKFLOW_HASHES = {
    ".github/workflows/output-intelligence.yml": "0336862bcfa2d55d8fc3070a3f1cba003199049bad1c657b4be3b7525291fb26",
    ".github/workflows/epistemic-safety.yml": "13d77280b0065f22dced9a797ee9bf96123401b643924c196b1b99274cd0a912",
    ".github/workflows/multi-graph-retrieval.yml": "5e1e14e4b08ceb3b2bfef804941daf194c4e0cd96976f64596d443ec1a266995",
    ".github/workflows/host-adapter-conformance.yml": "8f98eed500fe022554f8a04fef133096d7674ec9c1d5bd963f17e6bd8f9c719e",
    ".github/workflows/context-reset-handoff.yml": "99ec48e952e7c2075ec820e283c37f8861f4509ff5308d2e33050ab583e83faa",
    ".github/workflows/memory-retrieval.yml": "b62d02e1f57fa87f7e113e54c7ca30fbdf1155c47f3e89959e3009f79c274e49",
    ".github/workflows/observability-attribution.yml": "da64f254603f6446e43387aa0b2cdc4e7079d1a690a003b065531d22b4ae7318",
    ".github/workflows/adaptive-context-policy.yml": "ed6bb2213af66a1572edfd5c2dd62b2fed9e8c70f5d42f13b12558bccb99f288",
    ".github/workflows/cache-provider-budget.yml": "fe0cf3424b7ad0b1eab17fc6155096390edc3ca6c5e480d33f883c2ac012de50",
}

EXPECTED_CANONICAL_SCOPE = sorted([
    ".github/workflows/release-main-merge-gate.yml",
    ".github/workflows/signalbench-python-product.yml",
    "MANIFEST.sha256",
    "benchmarks/signalbench/adapters/external_cli.py",
    "benchmarks/signalbench/arms.example.json",
    "benchmarks/signalbench/tasks.example.json",
    "contracts/python/capability-completeness-registry-v1.json",
    "contracts/python/signalbench-python-product-v1.json",
    "syntavra_runtime/cli.py",
    "syntavra_runtime/signalbench.py",
    "syntavra_runtime/signalbench_external_adapter.py",
    "syntavra_runtime/signalbench_hardened.py",
    "tests/runtime/test_complete_competitive_features_v001.py",
    "tests/runtime/test_release_action_pins.py",
    "tests/runtime/test_signalbench_python_product_v1.py",
    "tests/runtime/test_token_saver_unification_v001.py",
    "tools/certify_signalbench_python_product_v1.py",
])


def _run(*argv: str) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True)


def _output(*argv: str) -> str:
    return subprocess.check_output(argv, text=True).strip()


def _patch_fixture() -> None:
    text = TEST_PATH.read_text(encoding="utf-8")
    old = '            request.write_text(json.dumps({"task": {"prompt": "verify"}}), encoding="utf-8")\n'
    new = '''            request.write_text(\n                json.dumps({\n                    "task": {"prompt": "verify"},\n                    "arm": {\n                        "arm_id": "fake",\n                        "version": "test-v1",\n                        "model": "test",\n                        "reasoning": "none",\n                        "context_window": 8192,\n                    },\n                }),\n                encoding="utf-8",\n            )\n'''
    if text.count(old) != 1:
        raise RuntimeError(f"legacy SignalBench request fixture anchor drift: {text.count(old)}")
    text = text.replace(old, new, 1)
    assertion = '            self.assertEqual(value["provider_receipt"]["provider"], "test")\n'
    bound_assertion = assertion + '            self.assertEqual(value["arm_identity"]["arm_id"], "fake")\n'
    if text.count(assertion) != 1:
        raise RuntimeError(f"SignalBench provider assertion anchor drift: {text.count(assertion)}")
    text = text.replace(assertion, bound_assertion, 1)
    compile(text, str(TEST_PATH), "exec")
    TEST_PATH.write_text(text, encoding="utf-8")


def _verify_manifest_rows() -> None:
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    for path, digest in FRESH_MAIN_WORKFLOW_HASHES.items():
        row = f"{digest}  {path}"
        if row not in manifest:
            raise RuntimeError(f"fresh-main manifest row missing: {row}")
    test_digest = hashlib.sha256(TEST_PATH.read_bytes()).hexdigest()
    test_row = f"{test_digest}  {TEST_PATH.as_posix()}"
    if test_row not in manifest:
        raise RuntimeError(f"fixture manifest row missing: {test_row}")
    print(f"fixture_sha256={test_digest}")


def _changed_paths(base: str, head: str) -> list[str]:
    raw = _output("git", "diff", "--name-only", base, head)
    return sorted(line for line in raw.splitlines() if line)


def main() -> None:
    if not TARGET_HEAD or not MAIN_BASE:
        raise RuntimeError("TARGET_HEAD and MAIN_BASE are required")
    if _output("git", "rev-parse", "HEAD") != TARGET_HEAD:
        raise RuntimeError("closure runner is not on exact canonical target")
    if _output("git", "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("canonical target worktree is not clean")

    _patch_fixture()

    _run("python", "-m", "unittest", "tests.runtime.test_token_saver_unification_v001", "-v")
    _run("python", "-m", "unittest", "tests.runtime.test_observability_attribution_v1", "-v")
    _run("python", "tools/refresh_manifest.py")
    _run("python", "tools/refresh_manifest.py", "--check")
    _verify_manifest_rows()
    _run("python", "tools/validate.py")
    _run("python", "tools/validate_release.py", "--smoke", "--output", "/tmp/signalbench-closure-release-validation.json")
    _run("git", "diff", "--check")

    changed = sorted(line for line in _output("git", "diff", "--name-only").splitlines() if line)
    expected_local = ["MANIFEST.sha256", TEST_PATH.as_posix()]
    if changed != expected_local:
        raise RuntimeError(f"unexpected closure worktree scope: {changed}")

    _run("git", "config", "user.name", "syntavra-ci")
    _run("git", "config", "user.email", "syntavra-ci@users.noreply.github.com")
    _run("git", "add", "--", "MANIFEST.sha256", TEST_PATH.as_posix())
    _run("git", "commit", "-m", "Close SignalBench frozen-arm fixture compatibility")

    new_head = _output("git", "rev-parse", "HEAD")
    parent = _output("git", "rev-parse", "HEAD^")
    if parent != TARGET_HEAD:
        raise RuntimeError(f"closure commit parent drift: {parent}")

    canonical_scope = _changed_paths(MAIN_BASE, new_head)
    if canonical_scope != EXPECTED_CANONICAL_SCOPE:
        raise RuntimeError(f"canonical 17-file scope drift: {canonical_scope}")

    _run(
        "python",
        "tools/check_rust_feature_freeze.py",
        "--base",
        MAIN_BASE,
        "--head",
        new_head,
        "--out",
        "/tmp/signalbench-closure-rust-freeze.json",
    )
    _run("python", "tools/refresh_manifest.py", "--check")

    remote_head = _output("git", "ls-remote", "origin", f"refs/heads/{TARGET_BRANCH}").split()[0]
    if remote_head != TARGET_HEAD:
        raise RuntimeError(f"target moved before push: expected {TARGET_HEAD}, got {remote_head}")

    _run("git", "push", "origin", f"HEAD:{TARGET_BRANCH}")
    print(f"signalbench_closure_commit={new_head}")
    print("canonical_scope_count=17")


if __name__ == "__main__":
    main()
