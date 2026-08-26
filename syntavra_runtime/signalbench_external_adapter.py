from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    return value


def _command(product: str) -> list[str]:
    key = "SYNTAVRA_SIGNALBENCH_" + product.upper().replace("-", "_") + "_COMMAND_JSON"
    raw = os.environ.get(key, "")
    if not raw:
        raise RuntimeError(f"missing external arm command: {key}")
    value = json.loads(raw)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{key} must be a non-empty JSON argv array")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed SignalBench external product adapter")
    parser.add_argument("--product", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--profile", default="")
    args = parser.parse_args()

    request_path = Path(args.request).resolve(strict=True)
    output_path = Path(args.output).resolve(strict=False)
    workspace = Path(args.workspace).resolve(strict=True)
    request = _load(request_path)
    agent_result = output_path.with_name("external-agent-result.json")
    prompt_path = output_path.with_name("external-agent-prompt.txt")
    task = request.get("task") if isinstance(request.get("task"), dict) else {}
    prompt_path.write_text(str(task.get("prompt", "")), encoding="utf-8")

    substitutions = {
        "{request}": str(request_path),
        "{workspace}": str(workspace),
        "{prompt}": str(prompt_path),
        "{result}": str(agent_result),
        "{profile}": args.profile,
    }
    argv = []
    for item in _command(args.product):
        for marker, replacement in substitutions.items():
            item = item.replace(marker, replacement)
        argv.append(item)

    environment = dict(os.environ)
    environment.update({
        "SIGNALBENCH_PRODUCT": args.product,
        "SIGNALBENCH_PROFILE": args.profile,
        "SIGNALBENCH_REQUEST": str(request_path),
        "SIGNALBENCH_WORKSPACE": str(workspace),
        "SIGNALBENCH_AGENT_RESULT": str(agent_result),
    })
    completed = subprocess.run(
        argv,
        cwd=workspace,
        env=environment,
        stdin=subprocess.DEVNULL,
        timeout=max(1.0, float(task.get("timeout_seconds", 1200.0))),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"external arm exited with {completed.returncode}")
    if not agent_result.is_file():
        raise RuntimeError("external arm did not write its bound result JSON")
    result = _load(agent_result)
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
    if not isinstance(metrics, dict):
        raise ValueError("external arm result is missing metrics")
    required_metrics = ("fresh_input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "quota_cost")
    if any(metrics.get(key) is None for key in required_metrics):
        raise ValueError("external arm result is missing provider-observed usage")
    provider_receipt = result.get("provider_receipt")
    if not isinstance(provider_receipt, dict) or not all(provider_receipt.get(key) for key in ("provider", "model", "request_id", "response_hash")):
        raise ValueError("external arm result is missing a provider receipt")
    output_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SignalBench adapter failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
