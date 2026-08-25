from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "c148e42214f2ccb04e9463f95718099aff9ba3e6"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous-c148.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous_c148", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench c148 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_post_v14_legacy_provider_billed() -> None:
    source = V14_APPLY.read_text(encoding="utf-8")
    marker = "def _compile_generated_python(path: Path) -> None:\n"
    helper = '''def _repair_legacy_provider_billed_compat(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    class_start = text.index("class HardenedSignalBench:")
    compare_start = text.index("    @classmethod\\n    def compare(", class_start)
    compare_block = text[compare_start:]

    receipt_errors_anchor = "        receipt_errors: list[dict[str, Any]] = []\\n"
    helper_marker = "        def legacy_provider_row(row: Mapping[str, Any] | Any) -> bool:\\n"
    if helper_marker not in compare_block:
        if compare_block.count(receipt_errors_anchor) != 1:
            raise RuntimeError(
                f"hardened receipt error anchor drift after V14: {compare_block.count(receipt_errors_anchor)}"
            )
        legacy_helper = (
            "        def legacy_provider_row(row: Mapping[str, Any] | Any) -> bool:\n"
            "            request_hash = str(cls._value(row, \"request_id_hash\", \"\") or \"\")\n"
            "            provider_receipt_hash = str(cls._value(row, \"provider_receipt_hash\", \"\") or \"\")\n"
            "\n"
            "            def valid_sha256(value: str) -> bool:\n"
            "                return (\n"
            "                    len(value) == 64\n"
            "                    and value == value.casefold()\n"
            "                    and all(ch in \"0123456789abcdef\" for ch in value)\n"
            "                )\n"
            "\n"
            "            return bool(\n"
            "                cls._value(row, \"provider_observed\", False)\n"
            "                and str(cls._value(row, \"provider\", \"\") or \"\")\n"
            "                and str(cls._value(row, \"model\", \"\") or \"\")\n"
            "                and valid_sha256(request_hash)\n"
            "                and valid_sha256(provider_receipt_hash)\n"
            "                and not str(cls._value(row, \"usage_receipt_hash\", \"\") or \"\")\n"
            "                and not str(cls._value(row, \"provider_response_hash\", \"\") or \"\")\n"
            "                and not str(cls._value(row, \"arm_version\", \"\") or \"\")\n"
            "            )\n"
            "\n"
        )
        compare_block = compare_block.replace(
            receipt_errors_anchor,
            receipt_errors_anchor + legacy_helper,
            1,
        )

    strict_pair_anchor = (
        "            strict_pair_identity = bool(\n"
        "                cls._value(base, \"usage_receipt_hash\", \"\")\n"
        "                or cls._value(candidate, \"usage_receipt_hash\", \"\")\n"
        "            )\n"
    )
    legacy_pair_line = "            legacy_provider_pair = legacy_provider_row(base) and legacy_provider_row(candidate)\\n"
    if legacy_pair_line not in compare_block:
        if compare_block.count(strict_pair_anchor) != 1:
            raise RuntimeError(
                f"strict pair identity anchor drift after V14: {compare_block.count(strict_pair_anchor)}"
            )
        compare_block = compare_block.replace(
            strict_pair_anchor,
            strict_pair_anchor + legacy_pair_line,
            1,
        )

    legacy_fields_end = (
        "                \"hardware_hash\",\n"
        "            }\n"
        "            for field in cls.identity_fields:\n"
    )
    legacy_fields_compat = (
        "                \"hardware_hash\",\n"
        "            }\n"
        "            if legacy_provider_pair:\n"
        "                legacy_identity_fields -= {\"reasoning\", \"context_window\", \"hardware_hash\"}\n"
        "            for field in cls.identity_fields:\n"
    )
    if legacy_fields_compat not in compare_block:
        if compare_block.count(legacy_fields_end) != 1:
            raise RuntimeError(
                f"legacy identity field anchor drift after V14: {compare_block.count(legacy_fields_end)}"
            )
        compare_block = compare_block.replace(
            legacy_fields_end,
            legacy_fields_compat,
            1,
        )

    receipt_missing = (
        "                elif require_receipts:\n"
        "                    receipt_errors.append({\"task\": task_id, \"arm\": arm, \"repetition\": repetition, \"cache\": cache_mode, \"reasons\": [\"receipt-missing\"]})\n"
    )
    receipt_compat = (
        "                elif require_receipts and not legacy_provider_row(row):\n"
        "                    receipt_errors.append({\"task\": task_id, \"arm\": arm, \"repetition\": repetition, \"cache\": cache_mode, \"reasons\": [\"receipt-missing\"]})\n"
    )
    if receipt_compat not in compare_block:
        if compare_block.count(receipt_missing) != 1:
            raise RuntimeError(
                f"receipt-missing branch drift after V14: {compare_block.count(receipt_missing)}"
            )
        compare_block = compare_block.replace(
            receipt_missing,
            receipt_compat,
            1,
        )

    required = (
        "        legacy_unversioned_arms = set()\\n",
        "legacy_provider_pair = legacy_provider_row(base) and legacy_provider_row(candidate)",
        "elif require_receipts and not legacy_provider_row(row):",
        "strict_row = bool(cls._value(row, \"usage_receipt_hash\", \"\"))",
        "if bool(cls._value(row, \"usage_receipt_hash\", \"\")) and not str(cls._value(row, \"arm_version\", \"\")):",
    )
    missing = [item for item in required if item not in compare_block]
    if missing:
        raise RuntimeError(f"legacy provider compatibility guards missing: {missing}")

    text = text[:compare_start] + compare_block
    path.write_text(text, encoding="utf-8")


def _diagnose_legacy_provider_billed_compat() -> None:
    import hashlib
    import json

    from syntavra_runtime.signalbench_hardened import HardenedSignalBench

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def row(task: str, repetition: int, arm: str, quota: float) -> dict[str, object]:
        return {
            "run_id": f"{arm}-{repetition}",
            "task_id": task,
            "arm_id": arm,
            "repetition": repetition,
            "cache_mode": "cold",
            "success": True,
            "verifier_success": True,
            "verified_work": 1.0,
            "wall_seconds": 1.0,
            "exit_code": 0,
            "fresh_input_tokens": 100,
            "cached_input_tokens": 0,
            "output_tokens": 10,
            "reasoning_tokens": 0,
            "quota_cost": quota,
            "model_turns": 1,
            "tool_calls": 1,
            "wait_calls": 0,
            "compactions": 0,
            "security_regressions": 0,
            "verifier_skips": 0,
            "repository_tree": "provider-repo-tree",
            "prompt_hash": "provider-prompt",
            "verifier_hash": "provider-verifier",
            "permissions_hash": "provider-permissions",
            "artifact_dir": f"artifact-{repetition}",
            "error": "",
            "provider_observed": True,
            "provider": "openai",
            "model": "fixture-model",
            "request_id_hash": digest(f"req-{repetition}"),
            "provider_receipt_hash": digest(f"provider-{repetition}"),
            "arm_version": "",
            "reasoning": "",
            "context_window": 0,
            "hardware_hash": "",
            "provider_response_hash": "",
            "usage_receipt_hash": "",
        }

    rows: list[dict[str, object]] = []
    for index in range(10):
        repetition = index + 1
        task = f"provider-task-{index}"
        rows.extend(
            (
                row(task, repetition, "plain-host", 2.0),
                row(task, repetition, "syntavra-minimal", 1.0),
            )
        )

    result = HardenedSignalBench.compare(
        rows,
        baseline_arm="plain-host",
        candidate_arm="syntavra-minimal",
    )
    print("legacy provider-billed comparator diagnostic:")
    print(json.dumps(result, sort_keys=True, indent=2))
    if result.get("claimable_superiority") is not True:
        raise RuntimeError("well-formed legacy provider-billed compatibility self-check failed")

    malformed = [dict(item) for item in rows]
    for item in malformed:
        item["request_id_hash"] = "r"
        item["provider_receipt_hash"] = "h"
    rejected = HardenedSignalBench.compare(
        malformed,
        baseline_arm="plain-host",
        candidate_arm="syntavra-minimal",
    )
    if rejected.get("claimable_superiority") is not False or not rejected.get("receipt_errors"):
        raise RuntimeError("malformed legacy provider evidence did not fail closed")
'''

    if "_repair_legacy_provider_billed_compat(path: Path)" not in source:
        location = source.index(marker)
        source = source[:location] + helper + "\n\n" + source[location:]

    anchor = (
        '    _repair_legacy_hardened_identity_scope(Path("syntavra_runtime/signalbench_hardened.py"))\n'
        "    _diagnose_legacy_hardened_compare()\n"
        "    for generated in (\n"
    )
    replacement = (
        '    _repair_legacy_hardened_identity_scope(Path("syntavra_runtime/signalbench_hardened.py"))\n'
        '    _repair_legacy_provider_billed_compat(Path("syntavra_runtime/signalbench_hardened.py"))\n'
        "    _diagnose_legacy_hardened_compare()\n"
        "    _diagnose_legacy_provider_billed_compat()\n"
        "    for generated in (\n"
    )
    if source.count(anchor) != 1:
        raise RuntimeError(f"post-V14 legacy provider call anchor drift: {source.count(anchor)}")
    source = source.replace(anchor, replacement, 1)
    compile(source, str(V14_APPLY), "exec")
    V14_APPLY.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_post_v14_legacy_provider_billed()


if __name__ == "__main__":
    main()
