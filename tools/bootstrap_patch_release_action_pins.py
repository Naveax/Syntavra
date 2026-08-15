from __future__ import annotations

from pathlib import Path


WORKFLOWS = (
    Path('.github/workflows/publish-pre-release.yml'),
    Path('.github/workflows/post-r38-release-provenance-diagnostic.yml'),
    Path('.github/workflows/pre-release-candidate-receipt-plan.yml'),
    Path('.github/workflows/python-publication-registry-reference.yml'),
    Path('.github/workflows/pre-release-publisher-prerequisites.yml'),
    Path('.github/workflows/release-main-merge-gate.yml'),
)
PINS = {
    'actions/checkout@v4': 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4',
    'actions/setup-python@v5': 'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5',
    'actions/setup-node@v4': 'actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4',
    'actions/upload-artifact@v4': 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4',
    'actions/download-artifact@v4': 'actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4',
    'actions/attest@v4': 'actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6 # v4',
    'pypa/gh-action-pypi-publish@release/v1': 'pypa/gh-action-pypi-publish@dc376bdcf25e3f5d3d8d4672cd692373fb0730b2 # release/v1',
}


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def main() -> None:
    total_counts = {old: 0 for old in PINS}
    for path in WORKFLOWS:
        text = path.read_text(encoding='utf-8')
        for old, new in PINS.items():
            count = text.count(old)
            total_counts[old] += count
            if count:
                text = text.replace(old, new)
        path.write_text(text, encoding='utf-8')

    for old, count in total_counts.items():
        if count < 1:
            raise SystemExit(f'expected mutable action ref was not present: {old}')

    publish = WORKFLOWS[0]
    text = publish.read_text(encoding='utf-8')
    text = replace_once(
        text,
        "      - 'tests/runtime/test_pre_release_secret_scope.py'\n      - 'tests/runtime/test_release_main_protection.py'\n",
        "      - 'tests/runtime/test_pre_release_secret_scope.py'\n      - 'tests/runtime/test_release_action_pins.py'\n      - 'tests/runtime/test_release_main_protection.py'\n",
        'publish PR action-pin test path',
    )
    text = replace_once(
        text,
        '          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_release_main_protection -v\n',
        '          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_release_action_pins -v\n          python -m unittest tests.runtime.test_release_main_protection -v\n',
        'publish PR action-pin test command',
    )
    text = replace_once(
        text,
        "                  'step_scoped_publisher_secrets': True,\n",
        "                  'step_scoped_publisher_secrets': True,\n                  'immutable_release_action_pins': True,\n",
        'dry-run immutable action gate',
    )
    text = text.replace(
        "'command': 'pypa/gh-action-pypi-publish@release/v1'",
        "'command': 'pypa/gh-action-pypi-publish@dc376bdcf25e3f5d3d8d4672cd692373fb0730b2'",
    )
    publish.write_text(text, encoding='utf-8')

    merge_gate = WORKFLOWS[-1]
    text = merge_gate.read_text(encoding='utf-8')
    text = replace_once(
        text,
        '          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n',
        '          python -m unittest tests.runtime.test_pre_release_secret_scope -v\n          python -m unittest tests.runtime.test_release_action_pins -v\n          python -m unittest tests.runtime.test_pre_release_registry_availability -v\n',
        'merge-gate action pin test command',
    )
    merge_gate.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
