# SignalCore 0.0.1

**Cross-platform Agent Skill for AI coding agents: Codex, Claude Code, Gemini CLI, Google Antigravity, Windsurf, OpenCode, VS Code/GitHub Copilot, Cursor, Cline, Continue, Junie, AGENTS.md-compatible tools, and universal Markdown/Python bridges.**

SignalCore is an unreleased, local-first coding-agent control layer. It coordinates repository context, exact evidence, persistent state, conservative posterior routing, provider/tool telemetry, retries, verification, token usage, latency, and monetary cost under a success-first policy.

> SignalCore is designed to become a high-efficiency single skill, but it does **not** claim proven market leadership or Token Savior superiority until paired external-provider benchmarks pass.

## Why SignalCore

- **Success before savings:** correctness, exact evidence, and a runnable verifier outrank lower token count.
- **Cross-platform:** one canonical skill, multiple native and rule-based delivery adapters.
- **Local-first:** Python 3.11+ standard library and SQLite WAL; no hosted service required.
- **Exact evidence:** content-addressed storage, hashes, bounded reads, and recovery handles.
- **Measured economics:** normalized provider/tool telemetry instead of unsupported percentages.
- **Progressive disclosure:** complex workflows activate only when their expected value exceeds their overhead.
- **Non-destructive installation:** adapters preserve existing `AGENTS.md`, `CLAUDE.md`, and rule content.

## Compatibility

### Native Agent Skill

- OpenAI Codex
- Claude Code
- Gemini CLI
- Google Antigravity IDE and CLI
- Windsurf Cascade
- OpenCode
- VS Code / GitHub Copilot
- Hosts implementing the Agent Skills standard through `.agents/skills/`

### Rule and instruction bridges

- Cursor
- Cline
- Continue
- JetBrains Junie and integrated agents
- Roo Code, Aider, Zed, Kiro, Qwen Code, Kimi CLI, Goose, and other Markdown/AGENTS.md-capable agents through the universal bridge

See [COMPATIBILITY.md](COMPATIBILITY.md) for exact support levels, paths, invocation syntax, and limitations.

## Locked Roblox Studio domain profile

SignalCore includes a hidden `roblox_studio` profile foundation that cannot be
activated from a normal CLI, IDE prompt, or Agent Skill invocation. It requires a
short-lived signed envelope from an authorized Roblox Studio bridge, live Studio
process attestation, project identity, an explicit capability subset, and a
single-use nonce. See [ROBLOX_STUDIO_MODE.md](ROBLOX_STUDIO_MODE.md).

## Install

### Install every verified native project target

```bash
python tools/install.py install --platforms all-native --scope project --project .
```

### Install selected platforms

```bash
python tools/install.py install \
  --platforms codex,claude-code,gemini-cli,antigravity,antigravity-cli,windsurf,opencode,vscode-copilot \
  --scope project --project .
```

### Install rule/instruction bridges

```bash
python tools/install.py install \
  --platforms cursor,cline,continue,junie,generic-agents-md \
  --scope project --project .
```

### User-wide Codex/Claude/OpenCode/Windsurf install

```bash
python tools/install.py install --platforms codex,claude-code,opencode,windsurf --scope user
```

### Detect and inspect

```bash
python tools/install.py list
python tools/install.py detect --project .
python tools/install.py install --platforms all-verified --scope project --dry-run
python tools/install.py status --platforms all-verified --scope project
```

### Remove SignalCore-managed adapters

```bash
python tools/install.py uninstall --platforms cursor,cline,continue,junie --scope project
```

## Platform-specific distribution

### Claude Code marketplace

Inside Claude Code:

```text
/plugin marketplace add Naveax/SignalCore
/plugin install signal-core@signalcore
```

### Gemini CLI extension

```bash
gemini extensions install https://github.com/Naveax/SignalCore
```

### Windsurf

Install to `.windsurf/skills/signal-core`, then invoke with `@signal-core` or let Cascade activate it.

### Cursor

The installer creates `.cursor/rules/signal-core.mdc`. Cursor currently uses a rule bridge rather than SignalCore claiming native Agent Skill discovery.

## Repository layout

```text
skills/signal-core/
├── SKILL.md
├── data/
│   ├── lexicon.json
│   └── platforms.json
├── profiles/
│   └── roblox_studio/
│       ├── profile.json
│       ├── activation.py
│       └── README.md
└── scripts/
    ├── common.py
    ├── evidence.py
    ├── platforms.py
    ├── posterior.py
    ├── profile_loader.py
    ├── routing.py
    ├── store.py
    ├── task_state.py
    └── telemetry.py

tools/
├── install.py
└── validate.py
```

## Quick checks

```bash
python tools/validate.py
python -m unittest discover -s tests -q
python -m compileall -q skills/signal-core/scripts skills/signal-core/profiles tools
python skills/signal-core/scripts/routing.py \
  "Find the exact root cause, callers, impact boundary, and narrow verifier"
```

## Search and machine discovery

SignalCore includes:

- `llms.txt` for AI/search ingestion
- `codemeta.json` for software metadata
- `AGENTS.md` for cross-agent repository instructions
- `.claude-plugin/marketplace.json` for Claude Code
- `gemini-extension.json` for Gemini CLI
- `.github/copilot-instructions.md` for VS Code/GitHub Copilot
- explicit keywords for Agent Skills, Codex, Claude Code, Gemini CLI, Antigravity, Windsurf, OpenCode, Cursor, Cline, Continue, context engineering, token optimization, and coding-agent memory

## Design rules

1. Correctness and verifier coverage outrank token reduction.
2. Exact/security evidence is never silently summarized.
3. Large evidence is stored by hash and retrieved through bounded handles.
4. SQLite state uses WAL, migrations, bounded transactions, and no pickle/eval.
5. Provider usage is normalized before efficiency comparisons.
6. Platform adapters remain thin; the canonical behavior stays in one `SKILL.md`.
7. Forecasts and internal tests are not market-dominance proof.

## Status

- Version: **0.0.1**
- Stage: **pre-release / frontier core**
- Runtime: Python 3.11+ standard library
- External engines: optional; not vendored
- Repository: `Naveax/SignalCore`
