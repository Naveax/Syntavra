from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "82a9fba571eb3b14a5f53a0448bc35b6b90353fb"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-82a9-materialize.py")
STAGE_BRANCH = "automation/signalbench-v14r3-materialized-candidate-v1"


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "signalbench_v14r3_previous_82a9_materialize",
        PREVIOUS_RUNNER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load SignalBench 82a9 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_materialization_hook() -> None:
    hook = Path(".git/hooks/post-commit")
    script = r'''#!/usr/bin/env bash
set -euo pipefail

if [ "$(git log -1 --format=%s)" != "Harden SignalBench Python product v1 V14" ]; then
  exit 0
fi

: "${TARGET_HEAD:?missing TARGET_HEAD}"
stage_branch="automation/signalbench-v14r3-materialized-candidate-v1"
full_commit="$(git rev-parse HEAD)"
parent="$(git rev-parse "${full_commit}^")"
test "$parent" = "$TARGET_HEAD"

release_path=".github/workflows/release-main-merge-gate.yml"
product_path=".github/workflows/signalbench-python-product.yml"
temp_release="tools/tmp_signalbench_generated_release_main_merge_gate.yml.txt"
temp_product="tools/tmp_signalbench_generated_product_workflow.yml.txt"

release_blob="$(git rev-parse "$full_commit:$release_path")"
product_blob="$(git rev-parse "$full_commit:$product_path")"
base_release_blob="$(git rev-parse "$TARGET_HEAD:$release_path")"

index="/tmp/signalbench-stage-index-$$"
trap 'rm -f "$index"' EXIT
rm -f "$index"
GIT_INDEX_FILE="$index" git read-tree "$full_commit"
GIT_INDEX_FILE="$index" git update-index --cacheinfo "100644,$base_release_blob,$release_path"
GIT_INDEX_FILE="$index" git update-index --force-remove -- "$product_path"
GIT_INDEX_FILE="$index" git update-index --add --cacheinfo "100644,$release_blob,$temp_release"
GIT_INDEX_FILE="$index" git update-index --add --cacheinfo "100644,$product_blob,$temp_product"
stage_tree="$(GIT_INDEX_FILE="$index" git write-tree)"

stage_commit="$(
  printf '%s\n' 'Materialize SignalBench V14 candidate blobs' |
    git commit-tree "$stage_tree" -p "$TARGET_HEAD"
)"

test -z "$(git diff --name-only "$TARGET_HEAD" "$stage_commit" -- '.github/workflows/**')"

changed="$(git diff --name-only "$TARGET_HEAD" "$stage_commit" | sort)"
expected="$(printf '%s\n' \
  MANIFEST.sha256 \
  benchmarks/signalbench/adapters/external_cli.py \
  benchmarks/signalbench/arms.example.json \
  benchmarks/signalbench/tasks.example.json \
  contracts/python/capability-completeness-registry-v1.json \
  contracts/python/signalbench-python-product-v1.json \
  syntavra_runtime/cli.py \
  syntavra_runtime/signalbench.py \
  syntavra_runtime/signalbench_external_adapter.py \
  syntavra_runtime/signalbench_hardened.py \
  tests/runtime/test_complete_competitive_features_v001.py \
  tests/runtime/test_release_action_pins.py \
  tests/runtime/test_signalbench_python_product_v1.py \
  tools/certify_signalbench_python_product_v1.py \
  "$temp_product" \
  "$temp_release" | sort)"
test "$changed" = "$expected"

test "$(git rev-parse "$stage_commit:$temp_release")" = "$release_blob"
test "$(git rev-parse "$stage_commit:$temp_product")" = "$product_blob"

remote="$(git ls-remote origin "refs/heads/$stage_branch" | awk '{print $1}')"
test -z "$remote"
git push origin "$stage_commit:refs/heads/$stage_branch"
printf 'materialized_stage_commit=%s\n' "$stage_commit"
'''
    hook.write_text(script, encoding="utf-8")
    hook.chmod(0o755)


def main() -> None:
    previous = _load_previous()
    _install_materialization_hook()
    previous.main()


if __name__ == "__main__":
    main()
