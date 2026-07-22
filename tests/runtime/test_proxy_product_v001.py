from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntavra_runtime.proxy_product import ProxyProductRegistry


class ProxyProductV001Tests(unittest.TestCase):
    def test_declared_provider_matrix_has_explicit_presets(self) -> None:
        value = ProxyProductRegistry.validate()
        self.assertTrue(value["ok"], value)
        self.assertEqual(value["providers"], 10)
        self.assertGreaterEqual(value["zero_code_compatible"], 7)

    def test_direct_proxy_command_is_fixed_origin_and_credential_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = ProxyProductRegistry.command(
                "openai",
                project=root,
                state_root=root / "state",
                listen_port=9876,
            )
            self.assertIn("provider", command)
            self.assertIn("proxy", command)
            self.assertIn("https://api.openai.com", command)
            self.assertIn("OPENAI_API_KEY", command)
            self.assertNotIn("sk-test-secret", command)
            self.assertEqual(command[command.index("--listen-port") + 1], "9876")

    def test_signed_or_oauth_providers_fail_closed_as_adapter_required(self) -> None:
        bedrock = ProxyProductRegistry.plan("aws-bedrock")
        vertex = ProxyProductRegistry.plan("vertex-ai")
        self.assertFalse(bedrock["ok"])
        self.assertFalse(vertex["ok"])
        self.assertIn("signed-adapter-required", bedrock["reasons"])
        self.assertIn("signed-adapter-required", vertex["reasons"])

    def test_cross_platform_service_descriptors_are_user_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected_home = (root / "home").resolve(strict=False)
            for platform in ("linux", "darwin", "windows"):
                value = ProxyProductRegistry.service(
                    "plan",
                    "anthropic",
                    project=root,
                    state_root=root / "state",
                    home=root / "home",
                    platform_name=platform,
                )
                self.assertTrue(value["ok"], value)
                self.assertTrue(value["plan"]["user_scoped"])
                self.assertIn("ANTHROPIC_API_KEY", value["spec"]["command"])
                descriptor = Path(value["plan"]["descriptor_path"]).resolve(strict=False)
                self.assertTrue(descriptor.is_relative_to(expected_home), (descriptor, expected_home))


if __name__ == "__main__":
    unittest.main()
