from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "post-r38-release-provenance-diagnostic.yml"
TEST = ROOT / "tests" / "runtime" / "test_release_action_pins.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    old = '''      - name: Enforce non-publishing release readiness
        if: always()
        shell: bash
        run: |
          set -euo pipefail
          printf '%s\\n' \\
            "baseline=${{ steps.baseline.outcome }}" \\
            "python_package=${{ steps.python_package.outcome }}" \\
            "npm_installer=${{ steps.npm_installer.outcome }}" \\
            "npm_sdk=${{ steps.npm_sdk.outcome }}" \\
            "vscode_package=${{ steps.vscode_package.outcome }}" \\
            "rust_production=${{ steps.rust_production.outcome }}" \\
            "rust_legacy=${{ steps.rust_legacy.outcome }}" \\
            "identity=${{ steps.identity.outcome }}" \\
            "clean_tree=${{ steps.clean_tree.outcome }}" \\
            "attestation=${{ steps.attestation.outcome }}"
          test "${{ steps.baseline.outcome }}" = success
          test "${{ steps.python_package.outcome }}" = success
          test "${{ steps.npm_installer.outcome }}" = success
          test "${{ steps.npm_sdk.outcome }}" = success
          test "${{ steps.vscode_package.outcome }}" = success
          test "${{ steps.rust_production.outcome }}" = success
          test "${{ steps.rust_legacy.outcome }}" = success
          test "${{ steps.identity.outcome }}" = success
          test "${{ steps.clean_tree.outcome }}" = success
          if [ "${{ github.event_name }}" = "push" ]; then
            test "${{ steps.attestation.outcome }}" = success
          fi
'''
    new = '''      - name: Enforce non-publishing release readiness
        if: always()
        shell: bash
        env:
          BASELINE_OUTCOME: ${{ steps.baseline.outcome }}
          PYTHON_PACKAGE_OUTCOME: ${{ steps.python_package.outcome }}
          NPM_INSTALLER_OUTCOME: ${{ steps.npm_installer.outcome }}
          NPM_SDK_OUTCOME: ${{ steps.npm_sdk.outcome }}
          VSCODE_PACKAGE_OUTCOME: ${{ steps.vscode_package.outcome }}
          RUST_PRODUCTION_OUTCOME: ${{ steps.rust_production.outcome }}
          RUST_LEGACY_OUTCOME: ${{ steps.rust_legacy.outcome }}
          IDENTITY_OUTCOME: ${{ steps.identity.outcome }}
          CLEAN_TREE_OUTCOME: ${{ steps.clean_tree.outcome }}
          ATTESTATION_OUTCOME: ${{ steps.attestation.outcome }}
        run: |
          set -euo pipefail
          ACTUAL_HEAD="$(git rev-parse HEAD)"
          printf '%s\\n' \\
            "target_head=$TARGET_HEAD" \\
            "actual_head=$ACTUAL_HEAD" \\
            "baseline=$BASELINE_OUTCOME" \\
            "python_package=$PYTHON_PACKAGE_OUTCOME" \\
            "npm_installer=$NPM_INSTALLER_OUTCOME" \\
            "npm_sdk=$NPM_SDK_OUTCOME" \\
            "vscode_package=$VSCODE_PACKAGE_OUTCOME" \\
            "rust_production=$RUST_PRODUCTION_OUTCOME" \\
            "rust_legacy=$RUST_LEGACY_OUTCOME" \\
            "identity=$IDENTITY_OUTCOME" \\
            "clean_tree=$CLEAN_TREE_OUTCOME" \\
            "attestation=$ATTESTATION_OUTCOME"
          [[ "$TARGET_HEAD" =~ ^[0-9a-f]{40}$ ]]
          test "$ACTUAL_HEAD" = "$TARGET_HEAD"
          test "$BASELINE_OUTCOME" = success
          test "$PYTHON_PACKAGE_OUTCOME" = success
          test "$NPM_INSTALLER_OUTCOME" = success
          test "$NPM_SDK_OUTCOME" = success
          test "$VSCODE_PACKAGE_OUTCOME" = success
          test "$RUST_PRODUCTION_OUTCOME" = success
          test "$RUST_LEGACY_OUTCOME" = success
          test "$IDENTITY_OUTCOME" = success
          test "$CLEAN_TREE_OUTCOME" = success
          if [ "$GITHUB_EVENT_NAME" = "push" ]; then
            test "$ATTESTATION_OUTCOME" = success
          else
            test "$ATTESTATION_OUTCOME" = skipped
          fi
'''
    text = replace_once(text, old, new, "provenance final readiness")
    WORKFLOW.write_text(text, encoding="utf-8", newline="\n")


def patch_contract() -> None:
    text = TEST.read_text(encoding="utf-8")
    anchor = '''    def test_publish_pr_contract_tracks_pin_policy_changes(self) -> None:
        text = (ROOT / ".github/workflows/publish-pre-release.yml").read_text(encoding="utf-8")
        self.assertIn("tests/runtime/test_release_action_pins.py", text)
        self.assertIn("tests.runtime.test_release_action_pins", text)
        self.assertIn("'immutable_release_action_pins': True", text)
'''
    replacement = anchor + '''
    def test_provenance_final_readiness_revalidates_exact_checked_out_head(self) -> None:
        text = (ROOT / ".github/workflows/post-r38-release-provenance-diagnostic.yml").read_text(encoding="utf-8")
        self.assertIn('ACTUAL_HEAD="$(git rev-parse HEAD)"', text)
        self.assertIn('[[ "$TARGET_HEAD" =~ ^[0-9a-f]{40}$ ]]', text)
        self.assertIn('test "$ACTUAL_HEAD" = "$TARGET_HEAD"', text)
        self.assertIn('BASELINE_OUTCOME: ${{ steps.baseline.outcome }}', text)
        self.assertIn('ATTESTATION_OUTCOME: ${{ steps.attestation.outcome }}', text)
        self.assertIn('if [ "$GITHUB_EVENT_NAME" = "push" ]; then', text)
        self.assertIn('test "$ATTESTATION_OUTCOME" = skipped', text)
'''
    text = replace_once(text, anchor, replacement, "release action pin contract extension")
    TEST.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    patch_workflow()
    patch_contract()


if __name__ == "__main__":
    main()
