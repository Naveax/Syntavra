# Syntavra compatibility

Syntavra uses one canonical `SKILL.md` and several delivery adapters. The core Python/SQLite runtime is host-independent; only discovery and instruction packaging vary by product.

## Support levels

- **Native** — the host discovers `SKILL.md` on demand and can use supporting scripts/resources.
- **Rule bridge** — the host loads a concise rule that points to the canonical skill and scripts.
- **Instruction bridge** — the host reads `AGENTS.md` or an equivalent project instruction file.
- **Universal bridge** — the host can read Markdown and run Python, but no native skill integration is claimed.

## Compatibility matrix

| Platform | Level | Project installation | Global installation | Manual invocation |
|---|---|---|---|---|
| Agent Skills standard | Native | `.agents/skills/syntavra` | `~/.agents/skills/syntavra` | Mention `syntavra` |
| OpenAI Codex | Native | `.codex/skills/syntavra` | `~/.codex/skills/syntavra` | `Use $syntavra` |
| Claude Code | Native | `.claude/skills/syntavra` | `~/.claude/skills/syntavra` | Mention/install the skill or plugin |
| Gemini CLI | Native | `.gemini/skills/syntavra` | `~/.gemini/skills/syntavra` | Ask Gemini to activate it |
| Google Antigravity IDE | Native | `.agents/skills/syntavra` | `~/.gemini/config/skills/syntavra` | Mention/select the skill |
| Google Antigravity CLI | Native | `.agent/skills/syntavra` | `~/.gemini/antigravity-cli/skills/syntavra` | `/skills` / mention the skill |
| Windsurf Cascade | Native | `.windsurf/skills/syntavra` | `~/.codeium/windsurf/skills/syntavra` | `@syntavra` |
| OpenCode | Native | `.opencode/skills/syntavra` | `~/.config/opencode/skills/syntavra` | Native `skill` tool |
| VS Code / GitHub Copilot | Native | `.github/skills/syntavra` | — | Mention the skill in Chat |
| Cursor | Rule bridge | `.cursor/rules/syntavra.mdc` | User Rules UI | `@syntavra` / project rule |
| Cline | Rule bridge | `.clinerules/00-syntavra.md` | Cline global Rules | Enable Syntavra rule |
| Continue | Rule bridge | `.continue/rules/00-syntavra.md` | Continue config/rules | Enable Syntavra rule |
| JetBrains Junie | Instruction bridge | `AGENTS.md` | — | Reference Syntavra section |
| JetBrains integrated agents | Instruction/native-selected-agent | `AGENTS.md` or selected agent skill path | — | Depends on selected agent |
| Roo Code, Aider, Zed, Kiro, Qwen Code, Kimi CLI, Goose | Universal bridge | `AGENTS.md` | Tool-specific | Attach instructions or run CLI |

## Installer

List all platforms:

```bash
python tools/install.py list
```

Install all verified native skill targets into a project:

```bash
python tools/install.py install --platforms all-native --scope project --project .
```

Install rule/instruction bridges:

```bash
python tools/install.py install \
  --platforms cursor,cline,continue,junie,generic-agents-md \
  --scope project --project .
```

Install one global skill:

```bash
python tools/install.py install --platforms codex --scope user
```

Inspect without changing files:

```bash
python tools/install.py install --platforms all-verified --scope project --dry-run
python tools/install.py status --platforms all-verified --scope project
python tools/install.py detect --project .
```

Remove only Syntavra-managed files/blocks:

```bash
python tools/install.py uninstall --platforms cursor,cline,continue --scope project
```

## Compatibility boundary

A system is not marked **native** unless its documented discovery model loads Agent Skills or an equivalent skill package. Rule and universal bridges preserve Syntavra's workflow guidance, but they cannot guarantee native lazy loading, tool registration, hooks, usage accounting, or context behavior.

The registry is data-driven in `skills/syntavra/data/platforms.json`. New hosts can be added without changing the routing, evidence, state, posterior, or telemetry cores.
