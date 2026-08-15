from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path('.github/workflows/publish-pre-release.yml')


def job_span(text: str, name: str) -> tuple[int, int]:
    marker = f'  {name}:\n'
    start = text.index(marker)
    match = re.search(r'(?m)^  [A-Za-z0-9_-]+:\n', text[start + len(marker):])
    end = len(text) if match is None else start + len(marker) + match.start()
    return start, end


def patch_job(text: str, name: str, transform) -> str:
    start, end = job_span(text, name)
    block = text[start:end]
    updated = transform(block)
    if updated == block:
        raise SystemExit(f'job patch made no change: {name}')
    return text[:start] + updated + text[end:]


def add_predecessor(block: str, predecessor: str) -> str:
    old = '    needs:\n      - authority\n      - credential-preflight\n'
    new = old + f'      - {predecessor}\n'
    if block.count(old) != 1:
        raise SystemExit(f'expected one standard needs block, found {block.count(old)}')
    return block.replace(old, new, 1)


def add_checkout_before(block: str, needle: str) -> str:
    checkout = (
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: ${{ env.TARGET_HEAD }}\n'
        '          fetch-depth: 0\n'
    )
    if block.count(needle) != 1:
        raise SystemExit(f'expected one checkout insertion point, found {block.count(needle)}')
    return block.replace(needle, checkout + needle, 1)


def append_steps(block: str, steps: str) -> str:
    return block.rstrip('\n') + '\n' + steps + '\n\n'


def main() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')

    predecessors = {
        'publish-npm-installer': 'publish-rust-production',
        'publish-npm-sdk': 'publish-npm-installer',
        'publish-pypi': 'publish-npm-sdk',
        'publish-vscode': 'publish-pypi',
        'publish-legacy-native': 'publish-vscode',
    }
    for job, predecessor in predecessors.items():
        text = patch_job(text, job, lambda block, p=predecessor: add_predecessor(block, p))

    text = patch_job(
        text,
        'publish-pypi',
        lambda block: add_checkout_before(block, '      - uses: actions/download-artifact@v4\n'),
    )
    for job in ('publish-npm-installer', 'publish-npm-sdk', 'publish-vscode'):
        text = patch_job(
            text,
            job,
            lambda block: add_checkout_before(block, '      - uses: actions/setup-node@v4\n'),
        )

    pypi_steps = '''      - name: Verify PyPI public version visibility
        shell: bash
        run: |
          set -euo pipefail
          python3 tools/check_pre_release_publication_visibility.py \\
            --target python \\
            --output /tmp/publication-visibility-python.json
      - name: Upload PyPI publication visibility evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-publication-visibility-python-${{ env.TARGET_HEAD }}-${{ github.run_id }}
          path: /tmp/publication-visibility-python.json
          if-no-files-found: warn
          retention-days: 90'''
    text = patch_job(text, 'publish-pypi', lambda block: append_steps(block, pypi_steps))

    def npm_installer(block: str) -> str:
        old = '          npm publish "$package" --tag next --access public --provenance\n'
        new = old + (
            '          python3 tools/check_pre_release_publication_visibility.py \\\n'
            '            --target npm \\\n'
            '            --output /tmp/publication-visibility-npm.json\n'
        )
        if block.count(old) != 1:
            raise SystemExit('npm installer publish command drift')
        block = block.replace(old, new, 1)
        return append_steps(block, '''      - name: Upload npm installer publication visibility evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-publication-visibility-npm-${{ env.TARGET_HEAD }}-${{ github.run_id }}
          path: /tmp/publication-visibility-npm.json
          if-no-files-found: warn
          retention-days: 90''')

    text = patch_job(text, 'publish-npm-installer', npm_installer)

    def npm_sdk(block: str) -> str:
        old = '          npm publish "$package" --tag next --access public --provenance\n'
        new = old + (
            '          python3 tools/check_pre_release_publication_visibility.py \\\n'
            '            --target npm_sdk \\\n'
            '            --output /tmp/publication-visibility-npm-sdk.json\n'
        )
        if block.count(old) != 1:
            raise SystemExit('npm SDK publish command drift')
        block = block.replace(old, new, 1)
        return append_steps(block, '''      - name: Upload npm SDK publication visibility evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-publication-visibility-npm-sdk-${{ env.TARGET_HEAD }}-${{ github.run_id }}
          path: /tmp/publication-visibility-npm-sdk.json
          if-no-files-found: warn
          retention-days: 90''')

    text = patch_job(text, 'publish-npm-sdk', npm_sdk)

    def rust(block: str) -> str:
        old = '''          cargo +1.82.0 publish --locked -p syntavra-contracts
          cargo +1.82.0 publish --locked -p syntavra-core
          cargo +1.82.0 publish --locked -p syntavra-cli
'''
        new = '''          cargo +1.82.0 publish --locked -p syntavra-contracts
          python3 tools/check_pre_release_publication_visibility.py \\
            --target rust_contracts \\
            --output /tmp/publication-visibility-rust/rust-contracts.json
          cargo +1.82.0 publish --locked -p syntavra-core
          python3 tools/check_pre_release_publication_visibility.py \\
            --target rust_core \\
            --output /tmp/publication-visibility-rust/rust-core.json
          cargo +1.82.0 publish --locked -p syntavra-cli
          python3 tools/check_pre_release_publication_visibility.py \\
            --target rust_cli \\
            --output /tmp/publication-visibility-rust/rust-cli.json
'''
        if block.count(old) != 1:
            raise SystemExit('Rust publish sequence drift')
        block = block.replace(old, new, 1)
        return append_steps(block, '''      - name: Upload Rust publication visibility evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-publication-visibility-rust-${{ env.TARGET_HEAD }}-${{ github.run_id }}
          path: /tmp/publication-visibility-rust
          if-no-files-found: warn
          retention-days: 90''')

    text = patch_job(text, 'publish-rust-production', rust)

    def vscode(block: str) -> str:
        old = '          npx --yes @vscode/vsce publish --oidc --pre-release --packagePath "$package"\n'
        new = old + (
            '          python3 tools/check_pre_release_publication_visibility.py \\\n'
            '            --target vscode \\\n'
            '            --output /tmp/publication-visibility-vscode.json\n'
        )
        if block.count(old) != 1:
            raise SystemExit('VS Code publish command drift')
        block = block.replace(old, new, 1)
        return append_steps(block, '''      - name: Upload VS Code publication visibility evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-publication-visibility-vscode-${{ env.TARGET_HEAD }}-${{ github.run_id }}
          path: /tmp/publication-visibility-vscode.json
          if-no-files-found: warn
          retention-days: 90''')

    text = patch_job(text, 'publish-vscode', vscode)

    def legacy(block: str) -> str:
        old = '          cargo +1.82.0 publish --manifest-path native/syntavra-native/Cargo.toml\n'
        new = old + (
            '          python3 tools/check_pre_release_publication_visibility.py \\\n'
            '            --target legacy_native_companion \\\n'
            '            --output /tmp/publication-visibility-legacy-native.json\n'
        )
        if block.count(old) != 1:
            raise SystemExit('legacy native publish command drift')
        block = block.replace(old, new, 1)
        return append_steps(block, '''      - name: Upload legacy native publication visibility evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pre-release-publication-visibility-legacy-native-${{ env.TARGET_HEAD }}-${{ github.run_id }}
          path: /tmp/publication-visibility-legacy-native.json
          if-no-files-found: warn
          retention-days: 90''')

    text = patch_job(text, 'publish-legacy-native', legacy)

    gates_old = "                  'release_main_protection': True,\n"
    gates_new = gates_old + (
        "                  'serialized_publication_chain': True,\n"
        "                  'post_write_public_visibility_verification': True,\n"
    )
    if text.count(gates_old) != 1:
        raise SystemExit('dry-run gate block drift')
    text = text.replace(gates_old, gates_new, 1)

    order_anchor = "              'publication_steps': [\n"
    order_block = (
        "              'publication_order': [\n"
        "                  'native',\n"
        "                  'npm',\n"
        "                  'npm_sdk',\n"
        "                  'python',\n"
        "                  'vscode',\n"
        "                  'legacy_native_companion',\n"
        "              ],\n"
    )
    if text.count(order_anchor) != 1:
        raise SystemExit('publication_steps anchor drift')
    text = text.replace(order_anchor, order_block + order_anchor, 1)

    WORKFLOW.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
