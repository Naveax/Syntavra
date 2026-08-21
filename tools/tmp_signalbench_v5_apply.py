from __future__ import annotations

import importlib.util
from pathlib import Path

FIXER = Path('/tmp/signalbench-fix.py')
HELPER = Path('/tmp/signalbench-original-helper.yml')


def extract_yaml_python_block(text: str) -> str:
    lines = text.splitlines()
    start_marker = "          python - <<'PY'"
    end_marker = "          PY"
    try:
        start = lines.index(start_marker) + 1
    except ValueError as exc:
        raise RuntimeError('helper apply block start marker missing') from exc
    end = None
    for index in range(start, len(lines)):
        if lines[index] == end_marker:
            end = index
            break
    if end is None:
        raise RuntimeError('helper apply block end marker missing')
    prefix = ' ' * 10
    raw = lines[start:end]
    return '\n'.join(line[len(prefix):] if line.startswith(prefix) else line for line in raw) + '\n'


def main() -> None:
    spec = importlib.util.spec_from_file_location('signalbench_fix', FIXER)
    if spec is None or spec.loader is None:
        raise RuntimeError('unable to load SignalBench V3 fix module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HELPER = HELPER
    module._extract_apply_block = extract_yaml_python_block
    module.apply()


if __name__ == '__main__':
    main()
