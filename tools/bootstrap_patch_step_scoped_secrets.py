from __future__ import annotations

import re
from pathlib import Path


PUBLISH = Path('.github/workflows/publish-pre-release.yml')
MERGE_GATE = Path('.github/workflows/release-main-merge-gate.yml')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


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


def strip_job_secret(block: str, variable: str, secret: str) -> str:
    line = f'      {variable}: ${{{{ secrets.{secret} }}}}\n'
    return replace_once(block, line, '', f'{variable} job secret')


def add_step_env(block: str, anchor: str, lines: list[tuple[str, str]], label: str) -> str:
    env = '        env:\n' + ''.join(
        f'          {variable}: ${{{{ secrets.{secret} }}}}\n'
        for variable, secret in lines
    )
    return replace_once(block, anchor, anchor + env, label)


def patch_publish() -> None:
    text = PUBLISH.read_text(encoding='utf-8')

    text = replace_once(
        text,
        "      - 'tests/runtime/test_pre_release_publication_attempt_ledger.py'\n      - 'tests/runtime/test_release_main_protection.py'\n",
        "      - 'tests/runtime/test_pre_release_publication_attempt_ledger.py'\n      - 'tests/runtime/test_pre_release_secret_scope.py'\n      - 'tests/runtime/test_release_main_protection.py'\n",
        'publish PR secret-scope test path',
    )
    text = replace_once(
        text,
        '          python -m unittest tests.runtime.test_pre_release_publication_attempt_ledger -v\n          python -m unittest tests.runtime.test_release_main_protection -v\n',
        '          python -m unittest tests.runtime.test_pre_release_publication_attempt_ledger -v\n          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_release_main_protection -v\n',
        'publish PR secret-scope test command',
    )

    def authority(block: str) -> str:
        block = strip_job_secret(block, 'SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')
        return add_step_env(
            block,
            "      - name: Arm publish mode only with independent gates\n        if: inputs.mode == 'publish'\n",
            [('SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')],
            'authority arming env',
        )

    text = patch_job(text, 'authority', authority)

    def credential(block: str) -> str:
        for variable, secret in (
            ('NODE_AUTH_TOKEN', 'NPM_TOKEN'),
            ('CRATES_IO_TOKEN', 'CRATES_IO_TOKEN'),
            ('SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED'),
        ):
            block = strip_job_secret(block, variable, secret)
        block = add_step_env(
            block,
            '      - name: Re-arm protected zero-write credential preflight\n        shell: bash\n',
            [
                ('SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED'),
                ('NODE_AUTH_TOKEN', 'NPM_TOKEN'),
                ('CRATES_IO_TOKEN', 'CRATES_IO_TOKEN'),
            ],
            'credential re-arm env',
        )
        block = add_step_env(
            block,
            '      - name: Verify npm token authentication without publication\n        shell: bash\n',
            [('NODE_AUTH_TOKEN', 'NPM_TOKEN')],
            'npm whoami env',
        )
        block = add_step_env(
            block,
            '      - name: Exchange trusted-publisher credentials without publication\n        shell: bash\n',
            [('NODE_AUTH_TOKEN', 'NPM_TOKEN')],
            'credential exchange npm env',
        )
        return block

    text = patch_job(text, 'credential-preflight', credential)

    def pypi(block: str) -> str:
        block = strip_job_secret(block, 'SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')
        return add_step_env(
            block,
            '      - name: Re-arm irreversible publication\n',
            [('SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')],
            'pypi re-arm env',
        )

    text = patch_job(text, 'publish-pypi', pypi)

    def npm(block: str, publish_name: str) -> str:
        block = strip_job_secret(block, 'NODE_AUTH_TOKEN', 'NPM_TOKEN')
        block = strip_job_secret(block, 'SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')
        block = add_step_env(
            block,
            '      - name: Re-arm irreversible publication\n        shell: bash\n',
            [
                ('SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED'),
                ('NODE_AUTH_TOKEN', 'NPM_TOKEN'),
            ],
            f'{publish_name} re-arm env',
        )
        block = add_step_env(
            block,
            f'      - name: {publish_name}\n        shell: bash\n',
            [('NODE_AUTH_TOKEN', 'NPM_TOKEN')],
            f'{publish_name} publish env',
        )
        return block

    text = patch_job(
        text,
        'publish-npm-installer',
        lambda block: npm(block, 'Publish npm installer with provenance'),
    )
    text = patch_job(
        text,
        'publish-npm-sdk',
        lambda block: npm(block, 'Publish npm TypeScript SDK with provenance'),
    )

    def rust(block: str) -> str:
        block = strip_job_secret(block, 'CARGO_REGISTRY_TOKEN', 'CRATES_IO_TOKEN')
        block = strip_job_secret(block, 'SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')
        block = add_step_env(
            block,
            '      - name: Re-arm irreversible publication\n        shell: bash\n',
            [
                ('SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED'),
                ('CARGO_REGISTRY_TOKEN', 'CRATES_IO_TOKEN'),
            ],
            'rust re-arm env',
        )
        return add_step_env(
            block,
            '      - name: Publish production Rust graph in dependency order\n        shell: bash\n',
            [('CARGO_REGISTRY_TOKEN', 'CRATES_IO_TOKEN')],
            'rust publish env',
        )

    text = patch_job(text, 'publish-rust-production', rust)

    def vscode(block: str) -> str:
        block = strip_job_secret(block, 'SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')
        return add_step_env(
            block,
            '      - name: Re-arm irreversible publication\n',
            [('SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')],
            'vscode re-arm env',
        )

    text = patch_job(text, 'publish-vscode', vscode)

    def legacy(block: str) -> str:
        block = strip_job_secret(block, 'CARGO_REGISTRY_TOKEN', 'CRATES_IO_TOKEN')
        block = strip_job_secret(block, 'SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED')
        block = add_step_env(
            block,
            '      - name: Re-arm irreversible optional companion publication\n        shell: bash\n',
            [
                ('SYNTAVRA_PUBLISH_ARMED', 'SYNTAVRA_PUBLISH_ARMED'),
                ('CARGO_REGISTRY_TOKEN', 'CRATES_IO_TOKEN'),
            ],
            'legacy re-arm env',
        )
        return add_step_env(
            block,
            '      - name: Publish legacy non-production native companion\n        shell: bash\n',
            [('CARGO_REGISTRY_TOKEN', 'CRATES_IO_TOKEN')],
            'legacy publish env',
        )

    text = patch_job(text, 'publish-legacy-native', legacy)

    text = replace_once(
        text,
        "                  'post_write_public_visibility_verification': True,\n",
        "                  'post_write_public_visibility_verification': True,\n                  'step_scoped_publisher_secrets': True,\n",
        'dry-run secret-scope gate',
    )

    PUBLISH.write_text(text, encoding='utf-8')


def patch_merge_gate() -> None:
    text = MERGE_GATE.read_text(encoding='utf-8')
    text = replace_once(
        text,
        '          python -m unittest tests.runtime.test_pre_release_publication_attempt_ledger -v\n          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n',
        '          python -m unittest tests.runtime.test_pre_release_publication_attempt_ledger -v\n          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n',
        'merge gate secret-scope command',
    )
    MERGE_GATE.write_text(text, encoding='utf-8')


def main() -> None:
    patch_publish()
    patch_merge_gate()


if __name__ == '__main__':
    main()
