from __future__ import annotations

from pathlib import Path


PUBLISH = Path('.github/workflows/publish-pre-release.yml')
MERGE_GATE = Path('.github/workflows/release-main-merge-gate.yml')
CONTRACT_TEST = Path('tests/runtime/test_pre_release_publish_workflow_contract.py')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def patch_publish() -> None:
    text = PUBLISH.read_text(encoding='utf-8')

    old_paths = """      - 'tests/runtime/test_pre_release_publish_credentials.py'\n      - 'tests/runtime/test_release_main_protection.py'\n"""
    new_paths = """      - 'tests/runtime/test_pre_release_publish_credentials.py'\n      - 'tests/runtime/test_pre_release_publication_visibility.py'\n      - 'tests/runtime/test_pre_release_publication_attempt_ledger.py'\n      - 'tests/runtime/test_release_main_protection.py'\n"""
    text = replace_once(text, old_paths, new_paths, 'publish PR test paths')

    old_tool_paths = """      - 'tools/check_pre_release_publish_credentials.py'\n      - 'tools/check_release_main_protection.py'\n"""
    new_tool_paths = """      - 'tools/check_pre_release_publish_credentials.py'\n      - 'tools/check_pre_release_publication_visibility.py'\n      - 'tools/build_pre_release_publication_attempt_ledger.py'\n      - 'tools/check_release_main_protection.py'\n"""
    text = replace_once(text, old_tool_paths, new_tool_paths, 'publish PR tool paths')

    old_contract = """          python -m unittest tests.runtime.test_pre_release_publish_credentials -v\n          python -m unittest tests.runtime.test_release_main_protection -v\n"""
    new_contract = """          python -m unittest tests.runtime.test_pre_release_publish_credentials -v\n          python -m unittest tests.runtime.test_pre_release_publication_visibility -v\n          python -m unittest tests.runtime.test_pre_release_publication_attempt_ledger -v\n          python -m unittest tests.runtime.test_release_main_protection -v\n"""
    text = replace_once(text, old_contract, new_contract, 'publish PR contract commands')

    marker = '  publication-attempt-boundary:\n'
    index = text.index(marker)
    boundary = '''  publication-attempt-boundary:
    if: always() && github.event_name == 'workflow_dispatch' && inputs.mode == 'publish'
    needs:
      - authority
      - credential-preflight
      - publish-pypi
      - publish-npm-installer
      - publish-npm-sdk
      - publish-rust-production
      - publish-vscode
      - publish-legacy-native
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      actions: read
    env:
      TARGET_HEAD: ${{ inputs.exact_head }}
      PUBLISH_LEGACY_NATIVE: ${{ inputs.publish_legacy_native }}
    steps:
      - name: Checkout publication ledger authority
        uses: actions/checkout@v4
        with:
          ref: ${{ github.sha }}
          fetch-depth: 0

      - name: Download same-run public visibility evidence
        if: always()
        continue-on-error: true
        uses: actions/download-artifact@v4
        with:
          pattern: pre-release-publication-visibility-*
          path: /tmp/publication-visibility-artifacts
          merge-multiple: false

      - name: Record publication job outcomes
        if: always()
        shell: bash
        run: |
          set -euo pipefail
          python - <<'PY'
          import json
          from pathlib import Path

          value = {
              'authority': '${{ needs.authority.result }}',
              'credential_preflight': '${{ needs.credential-preflight.result }}',
              'pypi': '${{ needs.publish-pypi.result }}',
              'npm_installer': '${{ needs.publish-npm-installer.result }}',
              'npm_sdk': '${{ needs.publish-npm-sdk.result }}',
              'rust_production': '${{ needs.publish-rust-production.result }}',
              'vscode': '${{ needs.publish-vscode.result }}',
              'legacy_native_companion': '${{ needs.publish-legacy-native.result }}',
          }
          Path('/tmp/publication-job-results.json').write_text(
              json.dumps(value, indent=2, sort_keys=True) + '\\n', encoding='utf-8'
          )
          PY

      - name: Build non-canonical publication attempt ledger
        if: always()
        shell: bash
        run: |
          set -euo pipefail
          python tools/build_pre_release_publication_attempt_ledger.py \\
            --exact-head "$TARGET_HEAD" \\
            --visibility-root /tmp/publication-visibility-artifacts \\
            --job-results-json /tmp/publication-job-results.json \\
            --legacy-requested "$PUBLISH_LEGACY_NATIVE" \\
            --output /tmp/publication-attempt-ledger.json

      - name: Upload publication attempt ledger
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-publication-attempt-ledger-${{ env.TARGET_HEAD }}-${{ github.run_id }}
          path: /tmp/publication-attempt-ledger.json
          if-no-files-found: error
          retention-days: 90
'''
    text = text[:index] + boundary
    PUBLISH.write_text(text, encoding='utf-8')


def patch_merge_gate() -> None:
    text = MERGE_GATE.read_text(encoding='utf-8')
    old = """          python -m unittest tests.runtime.test_pre_release_publish_workflow_contract -v\n          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n"""
    new = """          python -m unittest tests.runtime.test_pre_release_publish_workflow_contract -v\n          python -m unittest tests.runtime.test_pre_release_publication_visibility -v\n          python -m unittest tests.runtime.test_pre_release_publication_attempt_ledger -v\n          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n"""
    text = replace_once(text, old, new, 'release-main merge gate commands')
    MERGE_GATE.write_text(text, encoding='utf-8')


def patch_contract_test() -> None:
    text = CONTRACT_TEST.read_text(encoding='utf-8')
    old = '''    def test_post_publish_job_never_claims_canonical_registry_receipts(self) -> None:
        text = self.text
        self.assertIn("canonical_readiness_mutated': False", text)
        self.assertIn("registry_receipts_admitted': False", text)
        self.assertIn("REGISTRY_PUBLICATION_RECEIPTS_NOT_YET_ADMITTED", text)
        self.assertIn("separate exact-head reviewed change", text)
        self.assertNotRegex(text, r"(?m)^\\s*git\\s+(commit|push)\\b")
'''
    new = '''    def test_post_publish_job_never_claims_canonical_registry_receipts(self) -> None:
        text = self.text
        ledger = (ROOT / "tools" / "build_pre_release_publication_attempt_ledger.py").read_text(encoding="utf-8")
        self.assertIn("build_pre_release_publication_attempt_ledger.py", text)
        self.assertIn("pre-release-publication-attempt-ledger-${{ env.TARGET_HEAD }}", text)
        self.assertIn('"canonical_readiness_mutated": False', ledger)
        self.assertIn('"registry_receipts_admitted": False', ledger)
        self.assertIn("PUBLIC_VISIBILITY_EVIDENCE_ONLY_NOT_CANONICAL_REGISTRY_RECEIPT_ADMISSION", ledger)
        self.assertIn("separate exact-head reviewed change", ledger)
        self.assertNotRegex(text, r"(?m)^\\s*git\\s+(commit|push)\\b")
'''
    text = replace_once(text, old, new, 'post-publish contract test')
    CONTRACT_TEST.write_text(text, encoding='utf-8')


def main() -> None:
    patch_publish()
    patch_merge_gate()
    patch_contract_test()


if __name__ == '__main__':
    main()
