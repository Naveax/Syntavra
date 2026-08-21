from __future__ import annotations

import json
from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def canonicalize_task_families() -> None:
    path = Path("benchmarks/signalbench/tasks.example.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    mapping = {
        "bug-fix": "bug-diagnosis",
        "test-failure-repair": "bug-diagnosis",
        "cross-file-refactor": "multi-file-implementation",
        "dependency-impact": "call-graph-impact",
        "configuration-repair": "bug-diagnosis",
        "cli-contract": "known-edit",
        "documentation-code-consistency": "known-edit",
        "long-session-continuation": "long-session-continuity",
        "large-tool-output-diagnosis": "output-heavy-verification",
    }
    changed = 0
    for task in value.get("tasks", []):
        family = task.get("family")
        if family in mapping:
            task["family"] = mapping[family]
            changed += 1
    if changed != len(mapping):
        raise RuntimeError(f"unexpected task-template family rewrite count: {changed}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def harden_receipts_and_comparator() -> None:
    path = Path("syntavra_runtime/signalbench_hardened.py")
    text = path.read_text(encoding="utf-8")
    old_validate = '''    def validate(self) -> list[str]:
        reasons: list[str] = []
        if not self.task_id or not self.arm_id or self.repetition <= 0 or not self.cache_mode: reasons.append("receipt-identity-incomplete")
        if not self.provider or len(self.request_id_hash) != 64 or len(self.provider_response_hash) != 64: reasons.append("provider-evidence-incomplete")
        if any(value < 0 for value in (self.fresh_input_tokens, self.cached_input_tokens, self.output_tokens, self.reasoning_tokens)): reasons.append("negative-token-count")
        if not math.isfinite(self.quota_cost) or self.quota_cost <= 0: reasons.append("invalid-quota-cost")
        if len(self.hardware_hash) != 64: reasons.append("hardware-hash-invalid")
        if self.receipt_hash != self.expected_hash(): reasons.append("receipt-hash-mismatch")
        return reasons
'''
    new_validate = '''    def validate(self) -> list[str]:
        reasons: list[str] = []
        if not self.task_id or not self.arm_id or self.repetition <= 0 or not self.cache_mode:
            reasons.append("receipt-identity-incomplete")

        def valid_sha256(value: str) -> bool:
            return len(value) == 64 and value == value.casefold() and all(ch in "0123456789abcdef" for ch in value)

        if not self.provider or not valid_sha256(self.request_id_hash) or not valid_sha256(self.provider_response_hash):
            reasons.append("provider-evidence-incomplete")
        if any(value < 0 for value in (self.fresh_input_tokens, self.cached_input_tokens, self.output_tokens, self.reasoning_tokens)):
            reasons.append("negative-token-count")
        if not math.isfinite(self.quota_cost) or self.quota_cost <= 0:
            reasons.append("invalid-quota-cost")
        if not valid_sha256(self.hardware_hash):
            reasons.append("hardware-hash-invalid")
        if not valid_sha256(self.receipt_hash):
            reasons.append("receipt-hash-invalid")
        elif self.receipt_hash != self.expected_hash():
            reasons.append("receipt-hash-mismatch")
        return reasons
'''
    if text.count(old_validate) != 1:
        raise RuntimeError(f"UsageReceipt.validate anchor drift: {text.count(old_validate)}")
    text = text.replace(old_validate, new_validate, 1)
    start = text.index("    @classmethod\n    def compare(", text.index("class HardenedSignalBench"))
    robust_compare = '''    @classmethod
    def compare(cls, rows: Iterable[Mapping[str, Any] | Any], *, baseline_arm: str, candidate_arm: str, receipts: Iterable[UsageReceipt] = (), minimum_pairs: int = 10, require_receipts: bool = True) -> dict[str, Any]:
        rows = list(rows)
        receipt_rows = list(receipts)
        invalid: list[dict[str, Any]] = []
        identity_mismatches: list[dict[str, Any]] = []
        receipt_errors: list[dict[str, Any]] = []

        keyed: dict[tuple[str, int, str, str], Mapping[str, Any] | Any] = {}
        for row in rows:
            key = (
                str(cls._value(row, "task_id", "")),
                int(cls._value(row, "repetition", 0)),
                str(cls._value(row, "cache_mode", "")),
                str(cls._value(row, "arm_id", "")),
            )
            if key in keyed:
                invalid.append({"task": key[0], "arm": key[3], "repetition": key[1], "cache": key[2], "reason": "duplicate-result-key"})
                continue
            keyed[key] = row

        receipt_index: dict[tuple[str, int, str, str], UsageReceipt] = {}
        for item in receipt_rows:
            key = (item.task_id, item.repetition, item.cache_mode, item.arm_id)
            if key in receipt_index:
                receipt_errors.append({"task": item.task_id, "arm": item.arm_id, "repetition": item.repetition, "cache": item.cache_mode, "reasons": ["receipt-duplicate"]})
                continue
            receipt_index[key] = item

        pair_keys = sorted({
            (task, repetition, cache)
            for task, repetition, cache, arm in keyed
            if arm in {baseline_arm, candidate_arm}
        })
        ratios: list[float] = []
        totals = {
            baseline_arm: {"attempts": 0, "successes": 0, "work": 0.0, "quota": 0.0, "security": 0, "skips": 0},
            candidate_arm: {"attempts": 0, "successes": 0, "work": 0.0, "quota": 0.0, "security": 0, "skips": 0},
        }
        matched_pairs = 0

        for task_id, repetition, cache_mode in pair_keys:
            base = keyed.get((task_id, repetition, cache_mode, baseline_arm))
            candidate = keyed.get((task_id, repetition, cache_mode, candidate_arm))
            for arm, row in ((baseline_arm, base), (candidate_arm, candidate)):
                bucket = totals[arm]
                bucket["attempts"] += 1
                if row is None:
                    continue
                success = bool(cls._value(row, "success", False) and cls._value(row, "verifier_success", False))
                bucket["successes"] += int(success)
                bucket["work"] += float(cls._value(row, "verified_work", 0.0) or 0.0)
                bucket["security"] += int(cls._value(row, "security_regressions", 0) or 0)
                bucket["skips"] += int(cls._value(row, "verifier_skips", 0) or 0)

            if base is None or candidate is None:
                invalid.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "reason": "missing-arm", "missing": baseline_arm if base is None else candidate_arm})
                continue
            matched_pairs += 1

            mismatched: list[str] = []
            for field in cls.identity_fields:
                base_value = cls._value(base, field)
                candidate_value = cls._value(candidate, field)
                missing = base_value is None or candidate_value is None or base_value == "" or candidate_value == ""
                if field == "context_window":
                    try:
                        missing = missing or int(base_value) <= 0 or int(candidate_value) <= 0
                    except (TypeError, ValueError):
                        missing = True
                if missing or base_value != candidate_value:
                    mismatched.append(field)
            base_receipt = receipt_index.get((task_id, repetition, cache_mode, baseline_arm))
            candidate_receipt = receipt_index.get((task_id, repetition, cache_mode, candidate_arm))
            base_provider = str(cls._value(base, "provider", "") or (base_receipt.provider if base_receipt is not None else ""))
            candidate_provider = str(cls._value(candidate, "provider", "") or (candidate_receipt.provider if candidate_receipt is not None else ""))
            if not base_provider or not candidate_provider or base_provider != candidate_provider:
                mismatched.append("provider")
            for row, label in ((base, baseline_arm), (candidate, candidate_arm)):
                if bool(cls._value(row, "usage_receipt_hash", "")) and not str(cls._value(row, "arm_version", "")):
                    mismatched.append(f"arm_version:{label}")
            if mismatched:
                identity_mismatches.append({"task": task_id, "repetition": repetition, "cache": cache_mode, "fields": list(dict.fromkeys(mismatched))})

            effective_quota: dict[str, float | None] = {}
            for arm, row in ((baseline_arm, base), (candidate_arm, candidate)):
                quota = cls._value(row, "quota_cost")
                receipt = receipt_index.get((task_id, repetition, cache_mode, arm))
                if receipt is not None:
                    reasons = receipt.validate()
                    row_bindings = {
                        "provider": receipt.provider,
                        "request_id_hash": receipt.request_id_hash,
                        "provider_response_hash": receipt.provider_response_hash,
                        "hardware_hash": receipt.hardware_hash,
                        "fresh_input_tokens": receipt.fresh_input_tokens,
                        "cached_input_tokens": receipt.cached_input_tokens,
                        "output_tokens": receipt.output_tokens,
                        "reasoning_tokens": receipt.reasoning_tokens,
                    }
                    strict_row = bool(cls._value(row, "usage_receipt_hash", ""))
                    for field, expected in row_bindings.items():
                        actual = cls._value(row, field, None)
                        missing = actual is None or actual == ""
                        if strict_row:
                            if missing or actual != expected:
                                reasons.append(f"receipt-row-mismatch:{field}")
                        elif not missing and actual != expected:
                            reasons.append(f"receipt-row-mismatch:{field}")
                    try:
                        if float(cls._value(row, "quota_cost")) != float(receipt.quota_cost):
                            reasons.append("receipt-row-mismatch:quota_cost")
                    except (TypeError, ValueError):
                        reasons.append("receipt-row-mismatch:quota_cost")
                    if reasons:
                        receipt_errors.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reasons": list(dict.fromkeys(reasons))})
                    else:
                        quota = receipt.quota_cost
                elif require_receipts:
                    receipt_errors.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reasons": ["receipt-missing"]})
                try:
                    numeric_quota = float(quota) if quota is not None else 0.0
                except (TypeError, ValueError, OverflowError):
                    numeric_quota = 0.0
                if not math.isfinite(numeric_quota) or numeric_quota <= 0:
                    invalid.append({"task": task_id, "arm": arm, "repetition": repetition, "cache": cache_mode, "reason": "quota-unavailable"})
                    effective_quota[arm] = None
                else:
                    effective_quota[arm] = numeric_quota
                    totals[arm]["quota"] += numeric_quota

            base_success = bool(cls._value(base, "success", False) and cls._value(base, "verifier_success", False))
            candidate_success = bool(cls._value(candidate, "success", False) and cls._value(candidate, "verifier_success", False))
            equal_work = float(cls._value(base, "verified_work", 0.0) or 0.0) == float(cls._value(candidate, "verified_work", 0.0) or 0.0)
            if base_success and candidate_success and equal_work and effective_quota.get(baseline_arm) and effective_quota.get(candidate_arm):
                ratios.append(float(effective_quota[baseline_arm]) / float(effective_quota[candidate_arm]))

        pass_rates = {arm: bucket["successes"] / bucket["attempts"] if bucket["attempts"] else 0.0 for arm, bucket in totals.items()}
        utility = {arm: bucket["work"] / bucket["quota"] if bucket["quota"] > 0 else 0.0 for arm, bucket in totals.items()}
        aggregate_ratio = utility[candidate_arm] / utility[baseline_arm] if utility[baseline_arm] > 0 else 0.0
        ratios.sort()
        ci = _bootstrap_ci(ratios)
        median = ratios[len(ratios) // 2] if ratios else None
        claimable = bool(
            matched_pairs >= minimum_pairs
            and len(ratios) >= minimum_pairs
            and ci
            and ci[0] > 1
            and aggregate_ratio > 1
            and pass_rates[candidate_arm] >= pass_rates[baseline_arm]
            and not invalid
            and not identity_mismatches
            and not receipt_errors
            and totals[candidate_arm]["security"] == 0
            and totals[candidate_arm]["skips"] == 0
        )
        return {
            "schema_version": 1,
            "baseline": baseline_arm,
            "candidate": candidate_arm,
            "matched_pairs": matched_pairs,
            "successful_equal_work_pairs": len(ratios),
            "median_success_pair_ratio": median,
            "confidence_interval_95": ci,
            "pass_rates": pass_rates,
            "total_verified_work": {arm: totals[arm]["work"] for arm in totals},
            "total_quota": {arm: totals[arm]["quota"] for arm in totals},
            "verified_work_per_quota": utility,
            "failure_inclusive_efficiency_ratio": aggregate_ratio,
            "identity_mismatches": identity_mismatches,
            "receipt_errors": receipt_errors,
            "invalid": invalid,
            "claimable_superiority": claimable,
            "claim": "SUPERIORITY_PROVEN" if claimable else "NOT_PROVEN",
        }
'''
    path.write_text(text[:start] + robust_compare, encoding="utf-8")


def harden_cli() -> None:
    path = Path("syntavra_runtime/cli.py")
    text = path.read_text(encoding="utf-8")
    start = text.index("def command_signalbench(args: argparse.Namespace) -> int:\n")
    end = text.index("\n\ndef command_claim", start)
    replacement = '''def command_signalbench(args: argparse.Namespace) -> int:
    if args.action == "compare":
        result = SignalBenchRunner.compare(load_results(Path(args.results)), baseline_arm=args.baseline_arm, candidate_arm=args.candidate_arm)
        if args.output:
            atomic_write_json(Path(args.output), result, mode=0o644)
        emit(result)
        return 0 if result["claimable_superiority"] else 3

    runner = SignalBenchRunner(Path(args.output_root), seed=args.seed)
    tasks = runner.load_tasks(Path(args.tasks))
    arms = runner.load_arms(Path(args.arms))
    if args.action == "validate":
        result = runner.validate_product(tasks, arms)
        emit(result)
        return 0 if result["ok"] else 3
    if args.action == "manifest":
        validation = runner.validate_product(tasks, arms)
        if not validation["ok"]:
            emit(validation)
            return 3
        emit(runner.write_manifest(Path(args.output), tasks, arms))
        return 0
    if args.action == "run":
        result = runner.run(
            tasks, arms,
            repetitions=args.repetitions, cache_modes=tuple(args.cache_mode), randomized=not args.no_randomize,
        )
        emit({"result_hash": result["result_hash"], "runs": len(result["results"]), "output": str(Path(args.output_root) / "results.json")})
        return 0
    raise ValueError(args.action)
'''
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def harden_contract() -> None:
    path = Path("contracts/python/signalbench-python-product-v1.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["runtime"]["usage_receipt_ledger"] = "syntavra_runtime/usage_receipt_ledger.py:UsageReceiptLedger"
    value["measurement"].update({
        "duplicate_result_keys_fail_closed": True,
        "duplicate_receipts_fail_closed": True,
        "missing_arm_pairs_fail_closed": True,
        "receipt_hashes_require_sha256_hex": True,
    })
    value["ownership_policy"]["existing_usage_receipt_ledger_reused"] = True
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def harden_tests() -> None:
    path = Path("tests/runtime/test_signalbench_python_product_v1.py")
    text = path.read_text(encoding="utf-8")
    old_import = "from syntavra_runtime.signalbench import ArmSpec, RunResult, SignalBenchProtocol, SignalBenchRunner, TaskSpec\n"
    new_import = "from syntavra_runtime.signalbench import ArmSpec, RunResult, SignalBenchProtocol, SignalBenchRunner, TASK_FAMILIES, TaskSpec\nfrom syntavra_runtime.cli import build_parser\n"
    if text.count(old_import) != 1:
        raise RuntimeError("SignalBench generated test import anchor drift")
    text = text.replace(old_import, new_import, 1)
    tail = '\n\nif __name__ == "__main__":\n    unittest.main()\n'
    additions = '''
    def test_shipped_task_templates_use_canonical_families(self) -> None:
        tasks = SignalBenchRunner.load_tasks(ROOT / "benchmarks/signalbench/tasks.example.json")
        self.assertTrue(tasks)
        self.assertEqual(sorted({task.family for task in tasks if task.family not in TASK_FAMILIES}), [])

    def test_missing_and_duplicate_pair_data_fail_closed(self) -> None:
        rows = []
        for repetition in range(1, 11):
            rows.extend([self._result("base", repetition, 10.0), self._result("candidate", repetition, 1.0)])
        missing = SignalBenchRunner.compare(rows[:-1], baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(missing["claimable_superiority"])
        self.assertTrue(any(item.get("reason") == "missing-arm" for item in missing["invalid"]))
        duplicate = SignalBenchRunner.compare(rows + [rows[-1]], baseline_arm="base", candidate_arm="candidate")
        self.assertFalse(duplicate["claimable_superiority"])
        self.assertTrue(any(item.get("reason") == "duplicate-result-key" for item in duplicate["invalid"]))

    def test_usage_receipt_hash_fields_require_lowercase_sha256_hex(self) -> None:
        row = self._result("base", 1, 1.0)
        receipt = row.usage_receipt()
        self.assertIsNotNone(receipt)
        bad = UsageReceipt(**{**asdict(receipt), "request_id_hash": "g" * 64})
        self.assertIn("provider-evidence-incomplete", bad.validate())
        upper = UsageReceipt(**{**asdict(receipt), "hardware_hash": "A" * 64})
        self.assertIn("hardware-hash-invalid", upper.validate())

    def test_compare_parser_does_not_require_run_only_arguments(self) -> None:
        args = build_parser().parse_args([
            "signalbench", "compare", "--results", "results.json",
            "--baseline-arm", "base", "--candidate-arm", "candidate",
        ])
        self.assertFalse(hasattr(args, "output_root"))
        self.assertFalse(hasattr(args, "seed"))
        cli = (ROOT / "syntavra_runtime/cli.py").read_text(encoding="utf-8")
        block = cli[cli.index("def command_signalbench"):cli.index("def command_claim")]
        self.assertLess(block.index('if args.action == "compare"'), block.index("runner = SignalBenchRunner"))

    def test_manifest_route_rejects_placeholder_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.json"
            args = build_parser().parse_args([
                "signalbench", "manifest",
                "--tasks", str(ROOT / "benchmarks/signalbench/tasks.example.json"),
                "--arms", str(ROOT / "benchmarks/signalbench/arms.example.json"),
                "--output-root", str(Path(temp) / "runs"),
                "--output", str(output),
            ])
            self.assertEqual(args.func(args), 3)
            self.assertFalse(output.exists())
'''
    if text.count(tail) != 1:
        raise RuntimeError("SignalBench generated test tail anchor drift")
    path.write_text(text.replace(tail, "\n" + additions + tail, 1), encoding="utf-8")


def harden_certifier() -> None:
    path = Path("tools/certify_signalbench_python_product_v1.py")
    text = path.read_text(encoding="utf-8")
    anchor = '    require(measurement.get("verifier_skips_allowed") == 0, "verifier skip gate weakened")\n'
    replacement = '''    require(measurement.get("verifier_skips_allowed") == 0, "verifier skip gate weakened")
    require(measurement.get("duplicate_result_keys_fail_closed") is True, "duplicate result gate disabled")
    require(measurement.get("duplicate_receipts_fail_closed") is True, "duplicate receipt gate disabled")
    require(measurement.get("missing_arm_pairs_fail_closed") is True, "missing arm gate disabled")
    require(measurement.get("receipt_hashes_require_sha256_hex") is True, "receipt hash format gate disabled")
    require(contract.get("runtime", {}).get("usage_receipt_ledger") == "syntavra_runtime/usage_receipt_ledger.py:UsageReceiptLedger", "usage receipt ledger authority drift")
    require((repo / "syntavra_runtime/usage_receipt_ledger.py").is_file(), "usage receipt ledger implementation missing")
'''
    if text.count(anchor) != 1:
        raise RuntimeError("SignalBench certifier measurement anchor drift")
    text = text.replace(anchor, replacement, 1)
    anchor = '    require(tamper_report.get("claimable_superiority") is False and tamper_report.get("receipt_errors"), "receipt tampering did not fail closed")\n'
    replacement = '''    require(tamper_report.get("claimable_superiority") is False and tamper_report.get("receipt_errors"), "receipt tampering did not fail closed")
    missing_report = SignalBenchRunner.compare(rows[:-1], baseline_arm="base", candidate_arm="candidate")
    require(missing_report.get("claimable_superiority") is False and any(item.get("reason") == "missing-arm" for item in missing_report.get("invalid", [])), "missing arm did not fail closed")
    duplicate_report = SignalBenchRunner.compare(rows + [rows[-1]], baseline_arm="base", candidate_arm="candidate")
    require(duplicate_report.get("claimable_superiority") is False and any(item.get("reason") == "duplicate-result-key" for item in duplicate_report.get("invalid", [])), "duplicate result did not fail closed")
'''
    if text.count(anchor) != 1:
        raise RuntimeError("SignalBench certifier tamper anchor drift")
    text = text.replace(anchor, replacement, 1)
    text = text.replace('        "sealed_usage_receipts": True,\n', '        "sealed_usage_receipts": True,\n        "usage_receipt_ledger_authority": True,\n', 1)
    path.write_text(text, encoding="utf-8")


def harden_registry() -> None:
    path = Path("contracts/python/capability-completeness-registry-v1.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    row = next(item for item in value["capabilities"] if item["id"] == "signalbench_python_product_v1")
    evidence = "syntavra_runtime/usage_receipt_ledger.py"
    if evidence not in row["implementation_evidence"]:
        row["implementation_evidence"].append(evidence)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    canonicalize_task_families()
    harden_receipts_and_comparator()
    harden_cli()
    harden_contract()
    harden_tests()
    harden_certifier()
    harden_registry()


if __name__ == "__main__":
    main()
