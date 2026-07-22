from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import syntavra_runtime
from syntavra_runtime.framework_adapters import (
    AnthropicMessagesTransport,
    GeminiGenerateTransport,
    LangChainCallbackHandler,
    LiteLLMTransport,
    OpenAIChatTransport,
    OpenAIResponsesTransport,
    SyntavraMiddleware,
    framework_capabilities,
)
from syntavra_runtime.host_adapters import KNOWN_HOSTS, coverage_report, negotiate
from syntavra_runtime.long_session_planner import ContextPlanPolicy, LongSessionPlanner
from syntavra_runtime.mcp_server import MCPServer
from syntavra_runtime.output_governor import OutputGovernor, PROFILES
from syntavra_runtime.real_task_corpus import RealTaskCorpus
from syntavra_runtime.sdk import SyntavraClient
from syntavra_runtime.session_runtime import SessionRuntime


def provider_response(text: str = "ok") -> dict:
    return {
        "id": "resp-1",
        "output_text": text,
        "usage": {
            "input_tokens": 30,
            "input_tokens_details": {"cached_tokens": 10},
            "output_tokens": 5,
        },
    }


def task(index: int) -> dict:
    return {
        "task_id": f"task-{index:03d}",
        "repository": f"example/repo{index}",
        "commit": f"{index + 1:040x}"[-40:],
        "issue": str(index),
        "setup_argv": ["python", "-m", "pip", "install", "-e", "."],
        "test_argv": ["python", "-m", "pytest", "-q"],
        "verification_argv": ["python", "-m", "pytest", "-q"],
        "expected_paths": [f"src/m{index}.py"],
        "language": "python",
        "difficulty": "real",
    }


def arm(index: int, *, model: str = "same-model") -> dict:
    return {
        "arm_id": f"arm-{index}",
        "executable_argv": ["python", "-m", f"arm_{index}"],
        "provider": "provider-x",
        "model": model,
        "tool_permissions": ["filesystem", "process"],
        "cache_modes": ["cold", "warm"],
        "environment_fingerprint": "a" * 64,
        "version": "1.0",
    }


class SDKFrameworkTests(unittest.TestCase):
    def test_sync_replay_async_and_framework_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = SyntavraClient(Path(temp), project=Path(temp))
            calls: list[dict] = []

            def transport(request):
                calls.append(dict(request))
                return provider_response("first")

            request = {
                "model": "test-model",
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0,
            }
            first = client.invoke("openai", request, transport)
            second = client.invoke("openai", request, transport)
            self.assertFalse(first.replayed)
            self.assertTrue(second.replayed)
            self.assertEqual(len(calls), 1)
            self.assertTrue(client.verify()["ok"])

            async def async_transport(value):
                await asyncio.sleep(0)
                return provider_response(str(value["messages"][0]["content"]))

            invocation = asyncio.run(client.ainvoke(
                "openai-compatible",
                {"model": "local", "messages": [{"role": "user", "content": "async"}], "temperature": 0},
                async_transport,
            ))
            self.assertEqual(invocation.response["output_text"], "async")
            middleware = SyntavraMiddleware(
                client,
                provider="openai-compatible",
                transport=lambda value: provider_response("middleware"),
                defaults={"cache_policy": "off"},
            )
            self.assertEqual(middleware({"model": "local", "messages": []}).response["output_text"], "middleware")

            recorder = []
            def record(**kwargs):
                recorder.append(kwargs)
                return provider_response("transport")
            dummy = SimpleNamespace(
                responses=SimpleNamespace(create=record),
                chat=SimpleNamespace(completions=SimpleNamespace(create=record)),
                messages=SimpleNamespace(create=record),
                models=SimpleNamespace(generate_content=record),
            )
            for adapter in (
                OpenAIResponsesTransport(dummy),
                OpenAIChatTransport(dummy),
                AnthropicMessagesTransport(dummy),
                GeminiGenerateTransport(dummy),
                LiteLLMTransport(record),
            ):
                self.assertEqual(adapter({"model": "m", "messages": []})["output_text"], "transport")
            self.assertEqual(len(recorder), 5)
            self.assertIn("langchain-callback", {row["name"] for row in framework_capabilities()["frameworks"]})

    def test_langchain_observer_captures_success_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = SyntavraClient(Path(temp), project=Path(temp))
            handler = LangChainCallbackHandler(client, model="callback-model")
            handler.on_llm_start({"name": "callback-model"}, ["hello"], run_id="a")
            handler.on_llm_end({"output_text": "done", "usage": {"input_tokens": 4, "output_tokens": 2}}, run_id="a")
            handler.on_llm_start({"name": "callback-model"}, ["hello"], run_id="b")
            handler.on_llm_error(RuntimeError("provider failed"), run_id="b")
            self.assertEqual(len(handler.captures), 2)
            self.assertTrue(all(item.response_handle.startswith("sc://sha256/") for item in handler.captures))


class LongSessionTests(unittest.TestCase):
    def test_query_plan_temporal_exact_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = SessionRuntime(Path(temp) / "sessions.sqlite3", project_id="p")
            runtime.create_session(session_id="s")
            for index in range(1, 241):
                if index == 20:
                    runtime.append("s", "decision", {
                        "subject": "auth-refresh", "decision": "refresh on every retry", "decision_id": "v1",
                    })
                elif index == 180:
                    runtime.append("s", "decision", {
                        "subject": "auth-refresh", "decision": "refresh once then re-authenticate",
                        "decision_id": "v2", "supersedes": "v1",
                    })
                elif index % 53 == 0:
                    runtime.append("s", "error", {"error": f"provider timeout {index}", "path": f"src/provider.py:{index}"})
                else:
                    runtime.append("s", "observation", {"task": f"module-{index % 12}", "result": f"verified-{index}"})
            planner = LongSessionPlanner(runtime)
            policy = ContextPlanPolicy(
                token_budget=1200, recent_events=12, event_preview_chars=500,
                summary_preview_chars=1200, max_candidates=300,
            )
            plan = planner.plan("s", "current auth refresh decision and provider timeout", policy=policy)
            self.assertTrue(plan["verification"]["ok"])
            self.assertLessEqual(plan["used"], 1200)
            self.assertTrue(all(row["reference"].startswith("sc://session/") for row in plan["sections"]))
            current = [row for row in plan["sections"] if "refresh once then re-authenticate" in row["text"]]
            self.assertTrue(current)
            self.assertTrue(all(row["temporal_status"] == "current" for row in current))
            old = [row for row in plan["sections"] if "refresh on every retry" in row["text"]]
            self.assertTrue(all(row["temporal_status"] != "current" for row in old))
            report = planner.stress_report("s", ("module 1", "provider timeout"), policy=policy)
            self.assertTrue(report["chain_ok"])
            self.assertTrue(report["all_within_budget"])
            self.assertTrue(report["all_exactly_referenced"])


class CorpusOutputHostTests(unittest.TestCase):
    def test_corpus_schedule_fail_closed_and_deterministic(self) -> None:
        corpus = RealTaskCorpus.from_values([task(index) for index in range(50)], [arm(index) for index in range(3)])
        self.assertTrue(corpus.validate()["ok"])
        first = corpus.paired_schedule(repetitions=30, seed=99)
        self.assertEqual(first, corpus.paired_schedule(repetitions=30, seed=99))
        self.assertEqual(len(first), 50 * 3 * 30 * 2)
        self.assertEqual(corpus.manifest(repetitions=30)["claim"], "EXTERNAL_SUPERIORITY_NOT_PROVEN")
        mismatch = RealTaskCorpus.from_values([task(index) for index in range(50)], [arm(0), arm(1, model="different"), arm(2)])
        self.assertIn("model-mismatch", mismatch.validate()["reasons"])
        with self.assertRaises(ValueError):
            mismatch.paired_schedule()
        broken = task(1)
        broken["test_argv"] = "pytest -q"
        with self.assertRaises(ValueError):
            RealTaskCorpus.from_values([broken], [arm(0)])

    def test_terse_output_and_host_coverage(self) -> None:
        lines = ["Sure, here is the result."]
        lines.extend(f"ordinary progress line {index}" for index in range(20))
        lines.extend(["ERROR test failed at src/auth.py:42:7", "def refresh_token(user):", "$ pytest -q tests/test_auth.py"])
        result = OutputGovernor("terse").compact_text("\n".join(lines))
        self.assertLessEqual(result["bytes"], PROFILES["terse"].max_bytes)
        self.assertIn("src/auth.py:42:7", result["text"])
        self.assertIn("def refresh_token", result["text"])
        for host in ("zed", "kilo-code", "jetbrains-copilot", "sourcegraph-cody", "goose"):
            self.assertIn(host, KNOWN_HOSTS)
        self.assertEqual(negotiate("goose")["mode"], "MCP_PLUS_PROXY")
        self.assertFalse(negotiate("zed")["verified_adapter"])
        self.assertGreaterEqual(coverage_report()["hosts"], 20)


class EcosystemMCPTests(unittest.TestCase):
    def test_catalog_planner_and_fail_closed_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            server = MCPServer(
                project=project, state_root=root / "state", skill_root=root / "skill",
                codex_home=root / "codex", host="generic-mcp",
            )
            names = {row["name"] for row in server.tools()}
            self.assertIn("syntavra.ecosystem.capabilities", names)
            self.assertIn("syntavra.session.plan", names)
            self.assertTrue(server.call_tool("syntavra.ecosystem.capabilities", {})["long_session"]["query_aware_planning"])
            server.call_tool("syntavra.session.open", {"session_id": "s", "metadata": {}})
            for index in range(40):
                server.call_tool("syntavra.session.append", {
                    "session_id": "s", "event_type": "observation",
                    "payload": {"task": f"module-{index % 4}", "result": f"ok-{index}"},
                })
            plan = server.call_tool("syntavra.session.plan", {
                "session_id": "s", "query": "module 2",
                "policy": {"token_budget": 500, "recent_events": 8},
            })
            self.assertTrue(plan["verification"]["ok"])
            self.assertLessEqual(plan["used"], 500)
            result = server.call_tool("syntavra.corpus.validate", {
                "tasks": [task(1)], "arms": [arm(1)],
            })
            self.assertFalse(result["ok"])
            self.assertIn("real-task-corpus:1/50", result["reasons"])


if __name__ == "__main__":
    unittest.main()
