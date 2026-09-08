#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

AGENT_TELEMETRY_KEYS = frozenset({
    "provider_observation",
    "provider_observations",
    "provider_events",
    "provider_event",
    "provider_calls_avoided",
    "inference_skip",
    "inference_skip_cache",
    "retry_economics",
    "context_compilation",
    "context_policy",
    "raw_history_replayed",
    "active_evidence",
    "causal_receipts",
    "compiled_input_tokens",
    "prepared_input_tokens",
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_reasoning_tokens",
    "provider_visible_tokens",
})
AGENT_PROMPT_PATH_PREFIX = "live_requests["


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("differential report must be an object")
    return value


def _semantic_headless_error(reason: Any) -> str:
    text = str(reason or "").casefold()
    if "queued" in text and ("resume" in text or "resum" in text):
        return "cannot-resume-queued"
    return ""


def _path_keys(path: str) -> tuple[str, ...]:
    cleaned = path.replace("[", ".").replace("]", "")
    return tuple(part for part in cleaned.split(".") if part and not part.isdigit())


def _allowed_agent_mismatch(mismatch: Mapping[str, Any]) -> tuple[bool, str]:
    path = str(mismatch.get("path") or "")
    keys = set(_path_keys(path))
    telemetry = sorted(keys & AGENT_TELEMETRY_KEYS)
    if telemetry:
        return True, f"post-retirement-python-telemetry:{telemetry[0]}"
    if path.startswith(AGENT_PROMPT_PATH_PREFIX) and path.endswith(".system_prompt"):
        python_prompt = str(mismatch.get("python") or "")
        rust_prompt = str(mismatch.get("rust") or "")
        if python_prompt.strip() and rust_prompt.strip():
            return True, "post-retirement-python-provider-prompt"
    return False, ""


def _allowed_headless_mismatch(mismatch: Mapping[str, Any]) -> tuple[bool, str]:
    path = str(mismatch.get("path") or "")
    if not path.endswith("resume_queued_error.reason"):
        return False, ""
    left = _semantic_headless_error(mismatch.get("python"))
    right = _semantic_headless_error(mismatch.get("rust"))
    if left and left == right:
        return True, f"semantic-error-detail:{left}"
    return False, ""


def certify(report: Mapping[str, Any], kind: str) -> dict[str, Any]:
    differential = report.get("differential")
    if not isinstance(differential, Mapping):
        raise ValueError("missing differential object")
    mismatches = differential.get("mismatches")
    if not isinstance(mismatches, list):
        raise ValueError("differential.mismatches must be a list")

    accepted: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    classifier = _allowed_agent_mismatch if kind == "agent" else _allowed_headless_mismatch

    for raw in mismatches:
        if not isinstance(raw, Mapping):
            blocking.append({"path": "", "reason": "malformed-mismatch", "raw": raw})
            continue
        allowed, reason = classifier(raw)
        evidence = {
            "path": str(raw.get("path") or ""),
            "reason": reason,
            "python_sha256": _hash(raw.get("python")) if "python" in raw else "",
            "rust_sha256": _hash(raw.get("rust")) if "rust" in raw else "",
        }
        if allowed:
            accepted.append(evidence)
        else:
            blocking.append({**evidence, "raw": dict(raw)})

    ok = not blocking
    return {
        "schema_version": 1,
        "family": "syntavra-retired-engine-differential",
        "kind": kind,
        "authority": {
            "rust_retired": True,
            "rust_resume_allowed": False,
            "policy": "shared-public-contract-blocking; explicitly-bounded-post-retirement-divergence-evidence-only",
        },
        "raw_differential_ok": bool(differential.get("ok")),
        "raw_mismatch_count": len(mismatches),
        "accepted_retirement_divergence_count": len(accepted),
        "blocking_mismatch_count": len(blocking),
        "accepted": accepted,
        "blocking": blocking,
        "claim_boundary": (
            "The raw Python/Rust differential remains evidence. This certificate does not declare the retired Rust "
            "engine current. Shared/public semantic mismatches still block. Only explicitly classified post-retirement "
            "Python telemetry/provider-prompt drift, or headless free-form detail with the same stable semantic error "
            "category, is non-blocking while rust_retired=true and rust_resume_allowed=false."
        ),
        "ok": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify bounded Python/Rust divergence while Rust is retired")
    parser.add_argument("--kind", choices=("agent", "headless"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = _load(args.report)
    result = certify(report, args.kind)
    rendered = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
