from __future__ import annotations

import textwrap
from pathlib import Path

HELPER = Path('.github/workflows/tmp-signalbench-python-product-v1-impl.yml')


def _extract_apply_block(text: str) -> str:
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
    block = '\n'.join(lines[start:end]) + '\n'
    return textwrap.dedent(block)


def _repair_apply_script(script: str) -> str:
    old = '''    @classmethod
    def validate_product(cls, tasks: Iterable[TaskSpec], arms: Iterable[ArmSpec]) -> dict[str, Any]:
        tasks = list(tasks)
        arms = list(arms)
        base = cls.validate(tasks, arms)
        reasons = list(base["reasons"])
        for task in tasks:
            reasons.extend(f"task:{task.task_id}:{reason}" for reason in cls._frozen_repository_reasons(task))
'''
    new = '''    def validate_product(self, tasks: Iterable[TaskSpec], arms: Iterable[ArmSpec]) -> dict[str, Any]:
        tasks = list(tasks)
        arms = list(arms)
        base = self.validate(tasks, arms)
        reasons = list(base["reasons"])
        for task in tasks:
            reasons.extend(f"task:{task.task_id}:{reason}" for reason in self._frozen_repository_reasons(task))
'''
    if script.count(old) != 1:
        raise RuntimeError(f'validate_product binding anchor drift: {script.count(old)}')
    script = script.replace(old, new, 1)

    old_run_one = "SignalBenchProtocol.validate_product([task], [arm])"
    if script.count(old_run_one) != 1:
        raise RuntimeError(f'run_one product-validation anchor drift: {script.count(old_run_one)}')
    script = script.replace(old_run_one, "self.validate_product([task], [arm])", 1)
    return script


def _repair_generated_tests() -> None:
    path = Path('tests/runtime/test_signalbench_python_product_v1.py')
    text = path.read_text(encoding='utf-8')
    anchor = 'ROOT = Path(__file__).resolve().parents[2]\n\n\nclass SignalBenchPythonProductV1Tests'
    helper = '''ROOT = Path(__file__).resolve().parents[2]


def _validation_runner() -> SignalBenchRunner:
    return SignalBenchRunner(Path(tempfile.gettempdir()) / "syntavra-signalbench-product-validation")


class SignalBenchPythonProductV1Tests'''
    if text.count(anchor) != 1:
        raise RuntimeError('generated test helper anchor drift')
    text = text.replace(anchor, helper, 1)
    text = text.replace('SignalBenchProtocol.validate_product(', '_validation_runner().validate_product(')
    text = text.replace('SignalBenchProtocol.validate(', '_validation_runner().validate(')
    if 'SignalBenchProtocol.validate_product(' in text or 'SignalBenchProtocol.validate(' in text:
        raise RuntimeError('generated test still calls validation on SignalBenchProtocol')
    path.write_text(text, encoding='utf-8')


def _repair_generated_certifier() -> None:
    path = Path('tools/certify_signalbench_python_product_v1.py')
    text = path.read_text(encoding='utf-8')
    text = text.replace('SignalBenchProtocol.validate_product(', 'SignalBenchRunner(root / "validation").validate_product(')
    if 'SignalBenchProtocol.validate_product(' in text:
        raise RuntimeError('generated certifier still calls validation on SignalBenchProtocol')
    path.write_text(text, encoding='utf-8')


def _bind_external_adapter_identity() -> None:
    path = Path('benchmarks/signalbench/adapters/external_cli.py')
    text = path.read_text(encoding='utf-8')
    anchor = '''    result = _load(agent_result)
    metrics = result.get("metrics")
'''
    replacement = '''    result = _load(agent_result)
    arm = request.get("arm") if isinstance(request.get("arm"), dict) else {}
    arm_identity = {
        "arm_id": str(arm.get("arm_id") or ""),
        "version": str(arm.get("version") or ""),
        "model": str(arm.get("model") or ""),
        "reasoning": str(arm.get("reasoning") or ""),
        "context_window": int(arm.get("context_window") or 0),
    }
    if not all((arm_identity["arm_id"], arm_identity["version"], arm_identity["model"], arm_identity["reasoning"])) or arm_identity["context_window"] <= 0:
        raise ValueError("SignalBench request is missing frozen arm identity")
    result["arm_identity"] = arm_identity
    metrics = result.get("metrics")
'''
    if text.count(anchor) != 1:
        raise RuntimeError(f'external adapter identity anchor drift: {text.count(anchor)}')
    path.write_text(text.replace(anchor, replacement, 1), encoding='utf-8')


def apply() -> None:
    helper_text = HELPER.read_text(encoding='utf-8')
    script = _repair_apply_script(_extract_apply_block(helper_text))
    namespace = {'__name__': '__signalbench_impl_apply__'}
    exec(compile(script, str(HELPER), 'exec'), namespace, namespace)
    _repair_generated_tests()
    _repair_generated_certifier()
    _bind_external_adapter_identity()

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


if __name__ == '__main__':
    apply()
