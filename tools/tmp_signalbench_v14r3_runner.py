from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "82a9fba571eb3b14a5f53a0448bc35b6b90353fb"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-82a9-materialize.py")
STAGE_BRANCH = "automation/signalbench-v14r3-materialized-candidate-v2"


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
stage_branch="automation/signalbench-v14r3-materialized-candidate-v2"
full_commit="$(git rev-parse HEAD)"
parent="$(git rev-parse "${full_commit}^")"
test "$parent" = "$TARGET_HEAD"

release_path=".github/workflows/release-main-merge-gate.yml"
product_path=".github/workflows/signalbench-python-product.yml"
temp_release="tools/tmp_signalbench_generated_release_main_merge_gate.yml.txt"
temp_product="tools/tmp_signalbench_generated_product_workflow.yml.txt"
corrected_product="/tmp/signalbench-product-workflow-corrected.yml"
corrected_manifest="/tmp/signalbench-manifest-corrected.sha256"

# The historical generator emitted valid workflow content except that mapping and
# block-scalar children under `with:` / `run: |` lost their YAML indentation.
# Normalize only those semantic blocks, then syntax-parse the result before it is
# allowed to become a materialized candidate blob.
python - "$product_path" "$corrected_product" <<'PYFIX'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
out: list[str] = []
block: str | None = None
changed = 0

for line in lines:
    newline = "\n" if line.endswith("\n") else ""
    text = line[:-1] if newline else line

    if text.startswith("      - name:"):
        block = None
        out.append(line)
        continue

    if text in {"        with:", "        run: |"}:
        block = text.strip().split(":", 1)[0]
        out.append(line)
        continue

    if block is not None and text:
        if not text.startswith("          "):
            out.append("          " + text.lstrip(" ") + newline)
            changed += 1
            continue

    out.append(line)

fixed = "".join(out)
if changed < 1:
    raise SystemExit("SignalBench product workflow indentation repair made no changes")
for forbidden in ("\nref: ${{", "\nfetch-depth:", "\npython-version:", "\nset -euo pipefail"):
    if forbidden in fixed:
        raise SystemExit(f"SignalBench product workflow still has unindented content: {forbidden!r}")
for required in (
    "        with:\n          ref: ${{ github.event.pull_request.head.sha || github.sha }}\n          fetch-depth: 0\n",
    "        with:\n          python-version: '3.12'\n",
    "        run: |\n          set -euo pipefail\n",
    "        with:\n          name: signalbench-python-product-${{ github.event.pull_request.head.sha || github.sha }}\n",
):
    if required not in fixed:
        raise SystemExit(f"SignalBench product workflow normalization postcondition missing: {required!r}")

target.write_text(fixed, encoding="utf-8")
print(f"normalized_product_workflow_lines={changed}")
PYFIX

command -v ruby >/dev/null
ruby -e 'require "yaml"; YAML.parse_file(ARGV.fetch(0)); puts "signalbench-product-workflow-yaml=ok"' "$corrected_product"

release_blob="$(git rev-parse "$full_commit:$release_path")"
base_release_blob="$(git rev-parse "$TARGET_HEAD:$release_path")"
original_product_hash="$(sha256sum "$product_path" | awk '{print $1}')"
corrected_product_hash="$(sha256sum "$corrected_product" | awk '{print $1}')"
test "$original_product_hash" != "$corrected_product_hash"
corrected_product_blob="$(git hash-object -w "$corrected_product")"

# Refresh exactly the product-workflow manifest row. The full candidate already
# passed refresh_manifest --check, so changing any other row would be a drift.
git show "$full_commit:MANIFEST.sha256" > "$corrected_manifest"
python - "$corrected_manifest" "$original_product_hash" "$corrected_product_hash" <<'PYMANIFEST'
from pathlib import Path
import sys

path = Path(sys.argv[1])
old_hash = sys.argv[2]
new_hash = sys.argv[3]
workflow = ".github/workflows/signalbench-python-product.yml"
lines = path.read_text(encoding="utf-8").splitlines()
old = f"{old_hash}  {workflow}"
new = f"{new_hash}  {workflow}"
matches = [index for index, line in enumerate(lines) if line == old]
if len(matches) != 1:
    raise SystemExit(f"SignalBench product manifest row drift: {len(matches)}")
lines[matches[0]] = new
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PYMANIFEST
corrected_manifest_blob="$(git hash-object -w "$corrected_manifest")"

index="/tmp/signalbench-stage-index-$$"
trap 'rm -f "$index"' EXIT
rm -f "$index"
GIT_INDEX_FILE="$index" git read-tree "$full_commit"
GIT_INDEX_FILE="$index" git update-index --cacheinfo "100644,$base_release_blob,$release_path"
GIT_INDEX_FILE="$index" git update-index --force-remove -- "$product_path"
GIT_INDEX_FILE="$index" git update-index --cacheinfo "100644,$corrected_manifest_blob,MANIFEST.sha256"
GIT_INDEX_FILE="$index" git update-index --add --cacheinfo "100644,$release_blob,$temp_release"
GIT_INDEX_FILE="$index" git update-index --add --cacheinfo "100644,$corrected_product_blob,$temp_product"
stage_tree="$(GIT_INDEX_FILE="$index" git write-tree)"

stage_commit="$(
  printf '%s\n' 'Materialize syntax-valid SignalBench V14 candidate blobs' |
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
test "$(git rev-parse "$stage_commit:$temp_product")" = "$corrected_product_blob"
test "$(git rev-parse "$stage_commit:MANIFEST.sha256")" = "$corrected_manifest_blob"

remote="$(git ls-remote origin "refs/heads/$stage_branch" | awk '{print $1}')"
test -z "$remote"
git push origin "$stage_commit:refs/heads/$stage_branch"
printf 'materialized_stage_commit=%s\n' "$stage_commit"
printf 'corrected_product_sha256=%s\n' "$corrected_product_hash"
'''
    hook.write_text(script, encoding="utf-8")
    hook.chmod(0o755)


def main() -> None:
    previous = _load_previous()
    _install_materialization_hook()
    previous.main()


if __name__ == "__main__":
    main()
