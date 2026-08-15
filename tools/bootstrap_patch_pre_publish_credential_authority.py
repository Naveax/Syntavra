#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


path = Path('.github/workflows/publish-pre-release.yml')
text = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, got {count}')
    text = text.replace(old, new, 1)


replace_once(
    "      - 'tests/runtime/test_pre_release_registry_availability.py'\n",
    "      - 'tests/runtime/test_pre_release_registry_availability.py'\n"
    "      - 'tests/runtime/test_pre_release_publish_credentials.py'\n",
    'pull request credential test path',
)
replace_once(
    "      - 'tools/check_pre_release_registry_availability.py'\n",
    "      - 'tools/check_pre_release_registry_availability.py'\n"
    "      - 'tools/check_pre_release_publish_credentials.py'\n",
    'pull request credential checker path',
)
contract_tests = (
    "          python -m unittest tests.runtime.test_pre_release_publish_workflow_contract -v\n"
    "          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n"
)
replace_once(
    contract_tests,
    contract_tests + "          python -m unittest tests.runtime.test_pre_release_publish_credentials -v\n",
    'contract credential test',
)
replace_once(
    "                  'production_versions_must_be_available': True,\n",
    "                  'production_versions_must_be_available': True,\n"
    "                  'zero_write_publish_credential_preflight': True,\n",
    'dry run credential gate',
)

credential_job = r'''  credential-preflight:
    if: github.event_name == 'workflow_dispatch' && inputs.mode == 'publish'
    needs: authority
    runs-on: ubuntu-24.04
    environment: pre-release
    permissions:
      contents: read
      id-token: write
    env:
      TARGET_HEAD: ${{ needs.authority.outputs.target_head }}
      NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
      CRATES_IO_TOKEN: ${{ secrets.CRATES_IO_TOKEN }}
      SYNTAVRA_PUBLISH_ARMED: ${{ secrets.SYNTAVRA_PUBLISH_ARMED }}
      PYTHONPATH: ${{ github.workspace }}
      PYTHONUTF8: '1'
    steps:
      - name: Re-arm protected zero-write credential preflight
        shell: bash
        run: |
          set -euo pipefail
          test "$SYNTAVRA_PUBLISH_ARMED" = 'PUBLISH_0_0_1_PRE_RELEASE'
          test -n "$NODE_AUTH_TOKEN"
          test -n "$CRATES_IO_TOKEN"

      - uses: actions/checkout@v4
        with:
          ref: ${{ env.TARGET_HEAD }}
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          registry-url: https://registry.npmjs.org

      - name: Verify npm token authentication without publication
        shell: bash
        run: |
          set -euo pipefail
          npm_identity="$(npm whoami --registry=https://registry.npmjs.org)"
          test -n "$npm_identity"
          printf '%s\n' "$npm_identity" > /tmp/npm-identity.txt

      - name: Exchange trusted-publisher credentials without publication
        shell: bash
        run: |
          set -euo pipefail
          python tools/check_pre_release_publish_credentials.py \
            --exact-head "$TARGET_HEAD" \
            --npm-authenticated true \
            --npm-identity "$(cat /tmp/npm-identity.txt)" \
            --crates-token-present true \
            --output /tmp/pre-release-publish-credentials.json

      - name: Re-verify zero-write credential boundary
        shell: bash
        run: |
          set -euo pipefail
          python - <<'PY'
          import json
          from pathlib import Path
          value = json.loads(Path('/tmp/pre-release-publish-credentials.json').read_text(encoding='utf-8'))
          assert value['publish_auth_ready'] is True
          assert value['claim'] == 'ZERO_WRITE_PUBLISH_AUTH_READY'
          assert value['publication_performed'] is False
          assert value['credential_values_exposed'] is False
          assert value['pypi_trusted_publisher']['verified'] is True
          assert value['vscode_marketplace_trusted_publisher']['verified'] is True
          assert value['npm']['authenticated'] is True
          assert value['crates_io']['token_present'] is True
          PY

      - name: Upload zero-write publish credential evidence
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-publish-credentials-${{ env.TARGET_HEAD }}-${{ github.run_id }}
          path: /tmp/pre-release-publish-credentials.json
          if-no-files-found: error
          retention-days: 30

      - name: Enforce exact clean credential-preflight head
        shell: bash
        run: |
          set -euo pipefail
          test "$(git rev-parse HEAD)" = "$TARGET_HEAD"
          git diff --check
          test -z "$(git status --porcelain --untracked-files=all)"

'''
replace_once(
    '  publish-pypi:\n',
    credential_job + '  publish-pypi:\n',
    'credential preflight insertion',
)

for job in (
    'publish-pypi',
    'publish-npm-installer',
    'publish-npm-sdk',
    'publish-rust-production',
    'publish-vscode',
):
    old = (
        f"  {job}:\n"
        "    if: github.event_name == 'workflow_dispatch' && inputs.mode == 'publish'\n"
        "    needs: authority\n"
    )
    new = (
        f"  {job}:\n"
        "    if: github.event_name == 'workflow_dispatch' && inputs.mode == 'publish'\n"
        "    needs:\n"
        "      - authority\n"
        "      - credential-preflight\n"
    )
    replace_once(old, new, f'{job} needs preflight')

replace_once(
    "  publish-legacy-native:\n"
    "    if: github.event_name == 'workflow_dispatch' && inputs.mode == 'publish' && inputs.publish_legacy_native\n"
    "    needs: authority\n",
    "  publish-legacy-native:\n"
    "    if: github.event_name == 'workflow_dispatch' && inputs.mode == 'publish' && inputs.publish_legacy_native\n"
    "    needs:\n"
    "      - authority\n"
    "      - credential-preflight\n",
    'legacy native needs preflight',
)
replace_once(
    "    needs:\n      - authority\n      - publish-pypi\n",
    "    needs:\n      - authority\n      - credential-preflight\n      - publish-pypi\n",
    'attempt boundary needs preflight',
)
replace_once(
    "              'job_results': {\n                  'pypi': '${{ needs.publish-pypi.result }}',\n",
    "              'job_results': {\n                  'credential_preflight': '${{ needs.credential-preflight.result }}',\n                  'pypi': '${{ needs.publish-pypi.result }}',\n",
    'attempt boundary credential result',
)

path.write_text(text, encoding='utf-8')
