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


def patch_generated_runtime() -> None:
    path = Path('syntavra_runtime/signalbench.py')
    text = path.read_text(encoding='utf-8')

    declaration = '''    @classmethod
    def validate_product(cls, tasks: Iterable[TaskSpec], arms: Iterable[ArmSpec]) -> dict[str, Any]:
'''
    if text.count(declaration) != 1:
        raise RuntimeError(f'generated validate_product declaration drift: {text.count(declaration)}')
    start = text.index(declaration)
    try:
        end = text.index('\n    @staticmethod\n    def _copy_repository', start)
    except ValueError as exc:
        raise RuntimeError('generated validate_product end anchor missing') from exc
    block = text[start:end]

    replacements = (
        ('    @classmethod\n', '', 'validate_product classmethod decorator'),
        ('    def validate_product(cls,', '    def validate_product(self,', 'validate_product receiver'),
        ('        base = cls.validate(tasks, arms)\n', '        base = self.validate(tasks, arms)\n', 'validate_product base validation'),
        ('cls._frozen_repository_reasons(task)', 'self._frozen_repository_reasons(task)', 'validate_product frozen repository binding'),
    )
    for old, new, label in replacements:
        count = block.count(old)
        if count != 1:
            raise RuntimeError(f'{label} drift: {count}')
        block = block.replace(old, new, 1)
    text = text[:start] + block + text[end:]

    old_run_one = 'SignalBenchProtocol.validate_product([task], [arm])'
    if text.count(old_run_one) != 1:
        raise RuntimeError(f'run_one product-validation binding drift: {text.count(old_run_one)}')
    text = text.replace(old_run_one, 'self.validate_product([task], [arm])', 1)
    path.write_text(text, encoding='utf-8')


def main() -> None:
    spec = importlib.util.spec_from_file_location('signalbench_fix', FIXER)
    if spec is None or spec.loader is None:
        raise RuntimeError('unable to load SignalBench V3 post-fix module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    helper_text = HELPER.read_text(encoding='utf-8')
    script = extract_yaml_python_block(helper_text)
    namespace = {'__name__': '__signalbench_impl_apply__'}
    exec(compile(script, str(HELPER), 'exec'), namespace, namespace)

    patch_generated_runtime()
    module._repair_generated_tests()
    module._repair_generated_certifier()
    module._bind_external_adapter_identity()
    module._repair_hardened_compatibility()
    module._repair_legacy_receipt_gate_test()

    runtime = Path('syntavra_runtime/signalbench.py').read_text(encoding='utf-8')
    if 'def validate_product(self,' not in runtime:
        raise RuntimeError('SignalBenchRunner.validate_product is not instance-bound')
    if 'SignalBenchProtocol.validate_product([task], [arm])' in runtime:
        raise RuntimeError('run_one still calls nonexistent SignalBenchProtocol.validate_product')
    if 'self.validate_product([task], [arm])' not in runtime:
        raise RuntimeError('run_one does not enforce product validation')

    adapter = Path('benchmarks/signalbench/adapters/external_cli.py').read_text(encoding='utf-8')
    if 'result["arm_identity"] = arm_identity' not in adapter:
        raise RuntimeError('external adapter does not bind frozen arm identity')

    hardened = Path('syntavra_runtime/signalbench_hardened.py').read_text(encoding='utf-8')
    if 'strict_row = bool(cls._value(row, "usage_receipt_hash", ""))' not in hardened:
        raise RuntimeError('hardened comparator lost strict sealed-row compatibility gate')


if __name__ == '__main__':
    main()
