from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.6.0"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8", newline="\n")


def replace_exact(path: str, old: str, new: str, *, required: bool = True) -> bool:
    text = read(path)
    if old not in text:
        if required:
            raise RuntimeError(f"expected text missing in {path}: {old!r}")
        return False
    write(path, text.replace(old, new))
    return True


def set_json_version(path: str) -> None:
    value = json.loads(read(path))
    if "version" in value:
        value["version"] = VERSION
    if "softwareVersion" in value:
        value["softwareVersion"] = VERSION
    write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n")


def set_skill_version(path: str) -> None:
    text = read(path)
    updated, count = re.subn(
        r'(?m)^version:\s*["\']?[^"\'\s]+["\']?\s*$',
        f'version: "{VERSION}"',
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"skill version line missing in {path}")
    write(path, updated)


def update_pyproject() -> None:
    path = "pyproject.toml"
    text = read(path)
    text, count = re.subn(r'(?m)^version\s*=\s*"[^"]+"$', f'version = "{VERSION}"', text, count=1)
    if count != 1:
        raise RuntimeError("pyproject project.version missing")
    text = text.replace(
        'signalcore = "signalcore_runtime.cli:main"',
        'signalcore = "signalcore_runtime.unified_cli:main"',
    ).replace(
        'signalcore-product = "signalcore_runtime.product_v5_cli:main"',
        'signalcore-product = "signalcore_runtime.unified_cli:product_compat_main"',
    )
    if 'dependencies = ["cryptography>=43"]' not in text:
        marker = 'requires-python = ">=3.11"\n'
        if marker not in text:
            raise RuntimeError("pyproject requires-python marker missing")
        text = text.replace(marker, marker + 'dependencies = ["cryptography>=43"]\n', 1)
    write(path, text)


def update_current_runtime_versions() -> None:
    targets = (
        "signalcore_runtime/cli.py",
        "signalcore_runtime/bootstrap.py",
        "signalcore_runtime/installer.py",
        "signalcore_runtime/mcp_server.py",
        "tests/runtime/test_runtime_v02_core.py",
        "tests/runtime/test_runtime_v03_unified.py",
        "benchmarks/signalbench/arms.example.json",
    )
    for path in targets:
        text = read(path)
        if "0.3.0" in text:
            write(path, text.replace("0.3.0", VERSION))


def update_validators() -> None:
    text = read("tools/validate.py")
    text = text.replace('EXPECTED_VERSION = "0.3.0"', f'EXPECTED_VERSION = "{VERSION}"')
    text = text.replace("'version: \"0.3.0\"' in bundled_skill", f"'version: \"{VERSION}\"' in bundled_skill")
    required_anchor = '    ROOT / "docs" / "architecture" / "UNIFIED_RUNTIME_V03.md",\n'
    additions = (
        '    ROOT / "docs" / "architecture" / "UNIFIED_PRODUCTION_CORE_V6.md",\n'
        '    ROOT / "benchmarks" / "v6_production_core_benchmark.py",\n'
        '    ROOT / "signalcore_runtime" / "runtime_pipeline.py",\n'
        '    ROOT / "signalcore_runtime" / "config_v6.py",\n'
        '    ROOT / "signalcore_runtime" / "crypto.py",\n'
        '    ROOT / "signalcore_runtime" / "backup.py",\n'
        '    ROOT / "signalcore_runtime" / "identity.py",\n'
        '    ROOT / "signalcore_runtime" / "observability.py",\n'
        '    ROOT / "signalcore_runtime" / "migrations.py",\n'
        '    ROOT / "signalcore_runtime" / "plugin_sdk.py",\n'
        '    ROOT / "signalcore_runtime" / "job_scheduler.py",\n'
        '    ROOT / "signalcore_runtime" / "policy_rollout.py",\n'
        '    ROOT / "signalcore_runtime" / "streaming.py",\n'
        '    ROOT / "signalcore_runtime" / "unified_cli.py",\n'
    )
    if additions not in text:
        if required_anchor not in text:
            raise RuntimeError("validate.py required-file anchor missing")
        text = text.replace(required_anchor, required_anchor + additions, 1)
    write("tools/validate.py", text)

    text = read("tools/validate_runtime.py")
    text = text.replace('== "0.3.0"', f'== "{VERSION}"')
    anchor = '    "session_runtime.py", "output_governor.py", "signalbench.py",\n'
    addition = (
        '    "runtime_pipeline.py", "config_v6.py", "crypto.py", "backup.py",\n'
        '    "identity.py", "observability.py", "migrations.py", "plugin_sdk.py",\n'
        '    "job_scheduler.py", "policy_rollout.py", "streaming.py", "unified_cli.py",\n'
    )
    if addition not in text:
        if anchor not in text:
            raise RuntimeError("validate_runtime.py required-file anchor missing")
        text = text.replace(anchor, anchor + addition, 1)
    write("tools/validate_runtime.py", text)


def insert_install_step(text: str) -> str:
    marker = "      - name: Compile\n"
    if "Install runtime dependencies" in text:
        return text
    if marker not in text:
        raise RuntimeError("workflow compile marker missing")
    return text.replace(
        marker,
        "      - name: Install runtime dependencies\n"
        "        run: python -m pip install --disable-pip-version-check -e .\n"
        + marker,
        1,
    )


def update_workflows() -> None:
    package = insert_install_step(read(".github/workflows/validate.yml"))
    write(".github/workflows/validate.yml", package)

    runtime = read(".github/workflows/validate-fusion-runtime.yml")
    runtime = runtime.replace("Validate SignalCore Runtime 0.3", "Validate SignalCore Runtime 0.6")
    runtime = runtime.replace("== '0.3.0'", f"== '{VERSION}'")
    if "Install runtime dependencies" not in runtime:
        marker = "      - name: Compile ordinary source\n"
        if marker not in runtime:
            raise RuntimeError("runtime workflow compile marker missing")
        runtime = runtime.replace(
            marker,
            "      - name: Install runtime dependencies\n"
            "        run: python -m pip install --disable-pip-version-check -e .\n"
            + marker,
            1,
        )
    runtime = runtime.replace(
        "python -m pip wheel --no-build-isolation --no-deps . -w dist",
        "python -m pip wheel --no-build-isolation . -w dist",
    )
    runtime = runtime.replace(
        "/tmp/signalcore-wheel/bin/python -m pip install --no-index dist/signalcore_runtime-0.3.0-py3-none-any.whl",
        f"/tmp/signalcore-wheel/bin/python -m pip install --no-index --find-links dist signalcore-runtime=={VERSION}",
    )
    benchmark_marker = "      - name: Run committed v0.3 component benchmark\n"
    if "Run V6 production-core benchmark" not in runtime:
        if benchmark_marker not in runtime:
            raise RuntimeError("runtime benchmark marker missing")
        runtime = runtime.replace(
            benchmark_marker,
            "      - name: Run V6 production-core benchmark\n"
            "        run: python benchmarks/v6_production_core_benchmark.py --output v6-production-core.json\n"
            + benchmark_marker,
            1,
        )
    write(".github/workflows/validate-fusion-runtime.yml", runtime)


def update_readme() -> None:
    path = "README.md"
    text = read(path)
    marker = "## Unified Production Core 0.6.0"
    if marker not in text:
        text += f"""

{marker}

SignalCore 0.6.0 unifies encrypted exact evidence, authenticated fail-closed
provider streaming, valid typed data envelopes, configuration provenance,
transactional migrations, structured observability, retention, backup,
policy rollout, durable scheduling and permissioned plugins behind one
canonical runtime pipeline. See
`docs/architecture/UNIFIED_PRODUCTION_CORE_V6.md`.
"""
    write(path, text)


def update_changelog() -> None:
    path = "CHANGELOG.md"
    text = read(path) if (ROOT / path).is_file() else "# Changelog\n"
    marker = "## 0.6.0"
    if marker in text:
        return
    entry = """## 0.6.0 — Unified Production Core

- Encrypt exact evidence at rest with authenticated project-scoped keys and lifecycle controls.
- Guarantee parseable typed data envelopes; byte truncation can no longer corrupt JSON.
- Require control authentication on loopback and TLS for remote proxy bindings.
- Commit and DLP-scan streams before delivery, preventing partial unverified responses.
- Require immutable digest-pinned, non-root container sandbox execution.
- Add canonical configuration, migrations, observability, backup, identity, retention,
  durable scheduling, policy rollout, schema, retrieval and plugin production layers.
- Consolidate the public CLI and harden the TypeScript SDK with TLS, timeout, retry and SSE parsing.

"""
    if text.startswith("# Changelog"):
        first, _, rest = text.partition("\n")
        text = first + "\n\n" + entry + rest.lstrip("\n")
    else:
        text = entry + text
    write(path, text)


def update_typescript_readme() -> None:
    write("sdk/typescript/README.md", """# @signalcore/client

Typed ESM/TypeScript client for SignalCore Unified Production Core.
Provider credentials remain in the proxy process environment; the client rejects
credential-shaped request fields and provider authorization headers.

```ts
import { SignalCoreClient } from "@signalcore/client";

const client = new SignalCoreClient({
  baseUrl: "http://127.0.0.1:8787",
  controlToken: process.env.SIGNALCORE_PROXY_CONTROL_TOKEN,
  timeoutMs: 180_000,
});

const response = await client.openAI({model: "gpt-5", input: "Inspect this repository"});
console.log(response.data, response.evidenceHandle, response.requestId);
```

Remote connections require HTTPS. The package provides bounded retries with
`Retry-After`, abort/timeout handling, typed SSE iteration, health and integrity
verification, and helpers for OpenAI Responses/Chat, Anthropic Messages and
Gemini generate-content. It does not bundle provider credentials.
""")


def main() -> int:
    (ROOT / "VERSION").write_text(VERSION + "\n", encoding="utf-8", newline="\n")
    update_pyproject()
    for path in (".claude-plugin/marketplace.json", "gemini-extension.json", "codemeta.json"):
        set_json_version(path)
    for path in ("skills/signal-core/SKILL.md", "signalcore_runtime/bundled_skill/SKILL.md"):
        set_skill_version(path)
    update_current_runtime_versions()
    update_validators()
    update_workflows()
    update_readme()
    update_changelog()
    update_typescript_readme()
    print(json.dumps({"ok": True, "version": VERSION}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
