from __future__ import annotations

import pytest

from syntavra_runtime.platform import SecretlessProviderGateway


def test_gateway_plan_has_explicit_success_without_secret_material() -> None:
    plan = SecretlessProviderGateway.plan("openai")

    assert plan["ok"] is True
    assert plan["provider"] == "openai"
    assert plan["agent_environment_contains_secret"] is False
    assert plan["child_process_secret_inheritance"] == "denied"
    assert plan["transport_injection"]["visibility"] == "gateway-process-only"
    assert "secret" not in str(plan).casefold().replace("contains_secret", "")


def test_gateway_plan_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unsupported provider"):
        SecretlessProviderGateway.plan("unknown-provider")
