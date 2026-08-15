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
    "      - 'tests/runtime/test_pre_release_publish_credentials.py'\n",
    "      - 'tests/runtime/test_pre_release_publish_credentials.py'\n"
    "      - 'tests/runtime/test_release_main_protection.py'\n",
    'pull request protection test path',
)
replace_once(
    "      - 'tools/check_pre_release_publish_credentials.py'\n",
    "      - 'tools/check_pre_release_publish_credentials.py'\n"
    "      - 'tools/check_release_main_protection.py'\n",
    'pull request protection checker path',
)
replace_once(
    "          python -m unittest tests.runtime.test_pre_release_publish_credentials -v\n",
    "          python -m unittest tests.runtime.test_pre_release_publish_credentials -v\n"
    "          python -m unittest tests.runtime.test_release_main_protection -v\n",
    'contract protection test',
)
replace_once(
    "          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n\n      - name: Download exact-head candidate receipt authority\n",
    "          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n"
    "          python -m unittest tests.runtime.test_release_main_protection -v\n\n"
    "      - name: Download exact-head candidate receipt authority\n",
    'authority protection test',
)

protection_steps = r'''      - name: Probe release main protection authority
        shell: bash
        run: |
          set -euo pipefail
          gh api "repos/$GITHUB_REPOSITORY/branches/main" > /tmp/release-main-branch.json
          gh api "repos/$GITHUB_REPOSITORY/rules/branches/main" > /tmp/release-main-rules.json
          protection_args=(
            --exact-head "$TARGET_HEAD"
            --branch-json /tmp/release-main-branch.json
            --rules-json /tmp/release-main-rules.json
            --output /tmp/release-main-protection.json
          )
          if [ "$MODE" = 'publish' ]; then
            protection_args+=(--require-ready)
          fi
          python tools/check_release_main_protection.py "${protection_args[@]}"

      - name: Upload release main protection evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-main-protection-${{ steps.authority.outputs.target_head }}-${{ github.run_id }}
          path: /tmp/release-main-protection.json
          if-no-files-found: error
          retention-days: 30

'''
replace_once(
    '      - name: Probe live registry version availability\n',
    protection_steps + '      - name: Probe live registry version availability\n',
    'protection authority insertion',
)
replace_once(
    "                  'zero_write_publish_credential_preflight': True,\n",
    "                  'zero_write_publish_credential_preflight': True,\n"
    "                  'release_main_protection': True,\n",
    'dry run protection gate',
)

path.write_text(text, encoding='utf-8')
