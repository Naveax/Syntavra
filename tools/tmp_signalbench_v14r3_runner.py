from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "f1c3b5a9a3f6833461850fdef25a820f59bc214d"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-f1c3.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_f1c3", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench f1c3 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_post_v14_legacy_identity_scope() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    marker = "def _compile_generated_python(path: Path) -> None:\n"
    helper = '''def _repair_legacy_hardened_identity_scope(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    class_start = text.index("class HardenedSignalBench:")
    compare_start = text.index("    @classmethod\\n    def compare(", class_start)
    compare_block = text[compare_start:]

    value_old = (
        "    @staticmethod\\n"
        "    def _value(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:\\n"
        "        return row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)\\n"
    )
    value_new = (
        "    @staticmethod\\n"
        "    def _value(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:\\n"
        "        value = row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)\\n"
        "        if key == \\\"arm_version\\\" and (value is None or value == \\\"\\\"):\\n"
        "            usage_receipt_hash = row.get(\\\"usage_receipt_hash\\\", \\\"\\\") if isinstance(row, Mapping) else getattr(row, \\\"usage_receipt_hash\\\", \\\"\\\")\\n"
        "            if not usage_receipt_hash:\\n"
        "                return \\\"__legacy_unversioned__\\\"\\n"
        "        return value\\n"
    )
    prefix = text[:compare_start]
    if prefix.count(value_old) == 1:
        prefix = prefix.replace(value_old, value_new, 1)
    elif value_new not in prefix:
        raise RuntimeError("post-V14 HardenedSignalBench._value anchor drift")

    loop = "            for field in cls.identity_fields:\\n"
    if compare_block.count(loop) != 1:
        raise RuntimeError(
            f"hardened identity loop drift after V14: {compare_block.count(loop)}"
        )
    strict_marker = "            strict_pair_identity = bool(\\n"
    if strict_marker not in compare_block:
        replacement = (
            "            strict_pair_identity = bool(\\n"
            "                cls._value(base, \\\"usage_receipt_hash\\\", \\\"\\\")\\n"
            "                or cls._value(candidate, \\\"usage_receipt_hash\\\", \\\"\\\")\\n"
            "            )\\n"
            "            legacy_identity_fields = {\\n"
            "                \\\"repository_tree\\\",\\n"
            "                \\\"prompt_hash\\\",\\n"
            "                \\\"verifier_hash\\\",\\n"
            "                \\\"permissions_hash\\\",\\n"
            "                \\\"cache_mode\\\",\\n"
            "                \\\"model\\\",\\n"
            "                \\\"reasoning\\\",\\n"
            "                \\\"context_window\\\",\\n"
            "                \\\"hardware_hash\\\",\\n"
            "            }\\n"
            "            for field in cls.identity_fields:\\n"
            "                if not strict_pair_identity and field not in legacy_identity_fields:\\n"
            "                    continue\\n"
        )
        compare_block = compare_block.replace(loop, replacement, 1)

    required_compatibility = (
        'base_provider = str(cls._value(base, "provider", "") or (base_receipt.provider if base_receipt is not None else ""))',
        'candidate_provider = str(cls._value(candidate, "provider", "") or (candidate_receipt.provider if candidate_receipt is not None else ""))',
        'if bool(cls._value(row, "usage_receipt_hash", "")) and not str(cls._value(row, "arm_version", "")):',
        'strict_row = bool(cls._value(row, "usage_receipt_hash", ""))',
    )
    missing = [item for item in required_compatibility if item not in compare_block]
    if missing:
        raise RuntimeError(f"post-V14 compatibility guards missing: {missing}")

    text = prefix + compare_block
    path.write_text(text, encoding="utf-8")


def _diagnose_legacy_hardened_compare() -> None:
    import hashlib
    import json

    from syntavra_runtime.signalbench_hardened import HardenedSignalBench, UsageReceipt

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def row(task: str, arm: str, quota: float) -> dict[str, object]:
        return {
            "task_id": task,
            "arm_id": arm,
            "repetition": 1,
            "cache_mode": "cold",
            "success": True,
            "verifier_success": True,
            "verified_work": 1.0,
            "quota_cost": quota,
            "security_regressions": 0,
            "verifier_skips": 0,
            "repository_tree": "tree",
            "prompt_hash": digest("prompt"),
            "verifier_hash": digest("verifier"),
            "permissions_hash": digest("permissions"),
            "model": "same",
            "reasoning": "same",
            "context_window": 200000,
            "hardware_hash": digest("hw"),
        }

    def receipt(task: str, arm: str, quota: float) -> UsageReceipt:
        return UsageReceipt.seal(
            task_id=task,
            arm_id=arm,
            repetition=1,
            cache_mode="cold",
            provider="test",
            request_id_hash=digest(f"req:{task}:{arm}"),
            provider_response_hash=digest(f"res:{task}:{arm}"),
            fresh_input_tokens=100,
            cached_input_tokens=0,
            output_tokens=10,
            reasoning_tokens=5,
            quota_cost=quota,
            hardware_hash=digest("hw"),
        )

    rows: list[dict[str, object]] = []
    receipts: list[UsageReceipt] = []
    for index in range(12):
        task = f"t{index}"
        rows.extend((row(task, "plain", 10.0), row(task, "syntavra", 2.0)))
        receipts.extend((receipt(task, "plain", 10.0), receipt(task, "syntavra", 2.0)))

    result = HardenedSignalBench.compare(
        rows,
        baseline_arm="plain",
        candidate_arm="syntavra",
        receipts=receipts,
    )
    print("legacy SignalBench comparator diagnostic:")
    print(json.dumps(result, sort_keys=True, indent=2))
    if result.get("claimable_superiority") is not True:
        raise RuntimeError("legacy SignalBench comparator compatibility self-check failed")
'''

    if "_repair_legacy_hardened_identity_scope(path: Path)" not in source:
        location = source.index(marker)
        source = source[:location] + helper + "\n\n" + source[location:]

    anchor = "    _normalize_signalbench_runner_ten_space_drift(runtime_signalbench)\n    for generated in (\n"
    replacement = "    _normalize_signalbench_runner_ten_space_drift(runtime_signalbench)\n    _repair_legacy_hardened_identity_scope(Path(\"syntavra_runtime/signalbench_hardened.py\"))\n    _diagnose_legacy_hardened_compare()\n    for generated in (\n"
    if source.count(anchor) != 1:
        raise RuntimeError(f"post-V14 compatibility call anchor drift: {source.count(anchor)}")
    source = source.replace(anchor, replacement, 1)
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_post_v14_legacy_identity_scope()


if __name__ == "__main__":
    main()
