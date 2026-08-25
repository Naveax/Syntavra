from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_v9():
    spec = importlib.util.spec_from_file_location("signalbench_v9", "/tmp/signalbench-v9-apply.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load SignalBench V9 module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_fix_hardened_compatibility() -> None:
    path = Path("/tmp/signalbench-fix.py")
    source = path.read_text(encoding="utf-8")
    start_marker = "def _repair_hardened_compatibility() -> None:\n"
    end_marker = "\n\ndef _repair_legacy_receipt_gate_test() -> None:\n"
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError("fix hardened compatibility function start missing")
    end = source.find(end_marker, start)
    if end < 0:
        raise RuntimeError("fix hardened compatibility function end missing")
    replacement = 'def _repair_hardened_compatibility() -> None:\n    path = Path(\'syntavra_runtime/signalbench_hardened.py\')\n    text = path.read_text(encoding=\'utf-8\')\n\n    with_provider = \'    identity_fields = ("repository_tree", "prompt_hash", "verifier_hash", "permissions_hash", "cache_mode", "provider", "model", "reasoning", "context_window", "hardware_hash")\\n\'\n    without_provider = \'    identity_fields = ("repository_tree", "prompt_hash", "verifier_hash", "permissions_hash", "cache_mode", "model", "reasoning", "context_window", "hardware_hash")\\n\'\n    if text.count(with_provider) == 1:\n        text = text.replace(with_provider, without_provider, 1)\n    elif text.count(without_provider) != 1:\n        raise RuntimeError(\'post-helper provider identity tuple drift\')\n\n    compare_start = text.index("    @classmethod\\n    def compare(", text.index("class HardenedSignalBench"))\n    mismatch_start = text.index("            mismatched = ", compare_start)\n    mismatch_end = text.index("            if mismatched:", mismatch_start)\n    new_mismatch = """            mismatched = []\n            for field in cls.identity_fields:\n                base_value = cls._value(base, field)\n                candidate_value = cls._value(candidate, field)\n                missing = base_value is None or candidate_value is None or base_value == "" or candidate_value == ""\n                if field == "context_window":\n                    try:\n                        missing = missing or int(base_value) <= 0 or int(candidate_value) <= 0\n                    except (TypeError, ValueError):\n                        missing = True\n                if missing or base_value != candidate_value:\n                    mismatched.append(field)\n            base_receipt = receipt_index.get((task_id, repetition, cache_mode, baseline_arm))\n            candidate_receipt = receipt_index.get((task_id, repetition, cache_mode, candidate_arm))\n            base_provider = str(cls._value(base, "provider", "") or (base_receipt.provider if base_receipt is not None else ""))\n            candidate_provider = str(cls._value(candidate, "provider", "") or (candidate_receipt.provider if candidate_receipt is not None else ""))\n            if not base_provider or not candidate_provider or base_provider != candidate_provider:\n                mismatched.append("provider")\n            for row, label in ((base, baseline_arm), (candidate, candidate_arm)):\n                if bool(cls._value(row, "usage_receipt_hash", "")) and not str(cls._value(row, "arm_version", "")):\n                    mismatched.append(f"arm_version:{label}")\n"""\n    text = text[:mismatch_start] + new_mismatch + text[mismatch_end:]\n\n    receipt_start = text.index("                if receipt is not None:\\n", compare_start)\n    receipt_end = text.index("                elif require_receipts:", receipt_start)\n    new_receipt = """                if receipt is not None:\n                    reasons = receipt.validate()\n                    row_bindings = {\n                        "provider": receipt.provider,\n                        "request_id_hash": receipt.request_id_hash,\n                        "provider_response_hash": receipt.provider_response_hash,\n                        "hardware_hash": receipt.hardware_hash,\n                        "fresh_input_tokens": receipt.fresh_input_tokens,\n                        "cached_input_tokens": receipt.cached_input_tokens,\n                        "output_tokens": receipt.output_tokens,\n                        "reasoning_tokens": receipt.reasoning_tokens,\n                    }\n                    strict_row = bool(cls._value(row, "usage_receipt_hash", ""))\n                    for field, expected in row_bindings.items():\n                        actual = cls._value(row, field, None)\n                        missing = actual is None or actual == ""\n                        if strict_row:\n                            if missing or actual != expected:\n                                reasons.append(f"receipt-row-mismatch:{field}")\n                        elif not missing and actual != expected:\n                            reasons.append(f"receipt-row-mismatch:{field}")\n                    try:\n                        if float(cls._value(row, "quota_cost")) != float(receipt.quota_cost):\n                            reasons.append("receipt-row-mismatch:quota_cost")\n                    except (TypeError, ValueError):\n                        reasons.append("receipt-row-mismatch:quota_cost")\n                    if reasons:\n                        receipt_errors.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reasons": list(dict.fromkeys(reasons))})\n                    else:\n                        quota = receipt.quota_cost\n"""\n    text = text[:receipt_start] + new_receipt + text[receipt_end:]\n    path.write_text(text, encoding=\'utf-8\')\n'
    patched = source[:start] + replacement + source[end:]
    compile(patched, str(path), "exec")
    path.write_text(patched, encoding="utf-8")


def _patch_pair_anchor(script: str) -> str:
    start_marker = "old_pair = '''    @classmethod\n    def pair_identity"
    end_marker = "text = text.replace(old_pair, new_pair, 1)\n"
    start = script.find(start_marker)
    if start < 0:
        raise RuntimeError("pair patch source start missing")
    end_at = script.find(end_marker, start)
    if end_at < 0:
        raise RuntimeError("pair patch source end missing")
    end = end_at + len(end_marker)
    semantic = '''pair_start = text.index("    @classmethod\\n    def pair_identity(")
pair_end = text.index("\\n\\n\\nclass SignalBenchRunner:", pair_start)
new_pair = \'''    @classmethod
    def pair_identity(cls, task: TaskSpec, arm: ArmSpec, *, cache_mode: str, hardware_hash: str = "") -> dict[str, Any]:
        return {
            "task_hash": cls.task_hash(task),
            "repository_tree": task.repository_tree,
            "arm_version": arm.version,
            "model": arm.model,
            "reasoning": arm.reasoning,
            "context_window": arm.context_window,
            "hardware_hash": hardware_hash,
            "prompt_hash": sha256_bytes(task.prompt.encode()),
            "verifier_hash": cls.verifier_hash(task),
            "permissions_hash": cls.permissions_hash(task),
            "timeout_seconds": task.timeout_seconds,
            "cache_mode": cache_mode,
        }
\'''
text = text[:pair_start] + new_pair + text[pair_end:]
'''
    return script[:start] + semantic + script[end:]


def _patch_receipt_anchor(script: str) -> str:
    brittle = '''if text.count(old_receipt) != 1:
    raise SystemExit('hardened receipt anchor drift')
text = text.replace(old_receipt, new_receipt, 1)
'''
    count = script.count(brittle)
    if count != 1:
        raise RuntimeError(f"receipt patch source drift: {count}")
    semantic = '''compare_start = text.index("    @classmethod\\n    def compare(", text.index("class HardenedSignalBench"))
receipt_start = text.index("                if receipt is not None:\\n", compare_start)
receipt_end = text.index("                elif require_receipts:", receipt_start)
if receipt_end <= receipt_start:
    raise SystemExit("hardened receipt semantic anchor invalid")
text = text[:receipt_start] + new_receipt + text[receipt_end:]
'''
    return script.replace(brittle, semantic, 1)


def _patch_v14_result_identity() -> None:
    path = Path("/tmp/signalbench-v14-apply.py")
    source = path.read_text(encoding="utf-8")
    start_marker = "def patch_result_identity() -> None:\n"
    end_marker = "\n\ndef patch_hardened_invariants() -> None:\n"
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError("V14 result identity patch start missing")
    end = source.find(end_marker, start)
    if end < 0:
        raise RuntimeError("V14 result identity patch end missing")
    replacement = '''def patch_result_identity() -> None:
    path = Path("syntavra_runtime/signalbench.py")
    text = path.read_text(encoding="utf-8")
    run_start = text.index("    def run_one(", text.index("class SignalBenchRunner"))
    provider_start = text.index("        sealed_usage = None\\n", run_start)
    result_start = text.index("        result = RunResult(\\n", provider_start)
    result_close = text.index("        )\\n", result_start)
    block_end = result_close + len("        )\\n")
    block = text[result_start:block_end]
    required = (
        '            repository_commit=task.repository_commit,\\n',
        '            task_hash=identity["task_hash"],\\n',
        '            timeout_seconds=float(task.timeout_seconds),\\n',
    )
    present = tuple(item in block for item in required)
    if all(present):
        return
    if any(present):
        raise RuntimeError("result identity partially applied")
    insertion = "".join(required)
    offset = result_close - result_start
    block = block[:offset] + insertion + block[offset:]
    path.write_text(text[:result_start] + block + text[block_end:], encoding="utf-8")
'''
    patched = source[:start] + replacement + source[end:]
    compile(patched, str(path), "exec")
    path.write_text(patched, encoding="utf-8")


def _extract(text: str) -> str:
    lines = text.splitlines()
    start_marker = "          python - <<'PY'"
    try:
        start = lines.index(start_marker) + 1
    except ValueError as exc:
        raise RuntimeError("outer helper apply block start marker missing") from exc
    try:
        tail = next(
            index for index in range(start, len(lines))
            if "registry_path.write_text(json.dumps(registry" in lines[index]
        )
    except StopIteration as exc:
        raise RuntimeError("outer helper apply tail marker missing") from exc
    try:
        end = next(index for index in range(tail + 1, len(lines)) if lines[index] == "          PY")
    except StopIteration as exc:
        raise RuntimeError("outer helper apply closing marker missing") from exc
    prefix = " " * 10
    script = "\n".join(
        line[len(prefix):] if line.startswith(prefix) else line
        for line in lines[start:end]
    ) + "\n"
    script = _patch_pair_anchor(script)
    return _patch_receipt_anchor(script)


def main() -> None:
    _patch_fix_hardened_compatibility()
    _patch_v14_result_identity()
    module = _load_v9()
    module.extract_outer_yaml_python_block = _extract
    module.main()


if __name__ == "__main__":
    main()
