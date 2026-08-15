from __future__ import annotations

from pathlib import Path


WORKFLOW = Path('.github/workflows/publish-pre-release.yml')


def main() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    old = """          assert value['npm']['authenticated'] is True\n          assert value['crates_io']['token_present'] is True\n"""
    new = """          assert value['npm']['authenticated'] is True\n          assert value['npm']['scope_publish_rights_verified'] is True\n          assert value['npm']['scope_authorization']['verified'] is True\n          assert value['crates_io']['token_present'] is True\n"""
    if text.count(old) != 1:
        raise SystemExit(f'expected exactly one npm credential evidence assertion block, found {text.count(old)}')
    WORKFLOW.write_text(text.replace(old, new, 1), encoding='utf-8')


if __name__ == '__main__':
    main()
