# SignalCore 0.0.1

**Cross-platform Context Intelligence and runtime-governance layer for AI coding agents.**

SignalCore targets Codex, Claude Code, Gemini CLI, Antigravity, Windsurf, OpenCode, VS Code/Copilot, Cursor, Cline, Continue, Junie, and Agent Skills-compatible hosts. Version remains **0.0.1**.

> **Proof policy:** SignalCore publishes maturity labels, direct source, tests, artifacts, and limitations. It does not claim provider, competitor, or market superiority without fair executed comparisons and independent reproduction.

## Repository truth

Source is committed as ordinary reviewable files. CI rejects `.signalcore-direct`, `payload-*.b64`, and source-reconstruction workflows. The failed payload-transfer incident is documented in [the transfer audit](docs/audits/ROBLOX_PROFILE_TRANSFER_FAILURE.md).

## Roblox Studio orchestration profile

The hidden Roblox profile is a control plane, not a Roblox plugin, MCP implementation, Luau compiler, Blender worker, device simulator, or DataStore migration engine.

### Maturity

| Capability | Status | Evidence |
|---|---|---|
| Signed activation | **INTERNALLY_VERIFIED** [claim:roblox.activation] | [activation source](skills/signal-core/profiles/roblox_studio/activation.py), [tests](tests/roblox_profile/test_activation.py) |
| TaskState V2 | **INTERNALLY_VERIFIED** [claim:roblox.task_state] | [source](skills/signal-core/profiles/roblox_studio/task_state.py), [schema](skills/signal-core/profiles/roblox_studio/schemas/task-state.schema.json) |
| Capability graph | **INTERNALLY_VERIFIED** [claim:roblox.capabilities] | [33 capability records](skills/signal-core/profiles/roblox_studio/capabilities.py), [graph](skills/signal-core/profiles/roblox_studio/capability_graph.py) |
| Simulated orchestration | **SIMULATED** [claim:roblox.simulated] | [runner](benchmarks/roblox_profile_benchmark.py), [raw result](benchmarks/results/roblox-profile/simulated-50.json) |
| Transcript adapter | **IMPLEMENTED** [claim:roblox.transcript] | [adapter contract](skills/signal-core/profiles/roblox_studio/adapters/transcript/__init__.py); no real transcript result claimed |
| Live Studio bridge | **PLANNED** [claim:roblox.live] | [disabled live contract](skills/signal-core/profiles/roblox_studio/adapters/live/__init__.py) |
| DataStore migration execution | **PLANNED** [claim:roblox.datastore] | external validator/engine boundary only |
| Asset, animation and Blender execution | **PLANNED** [claim:roblox.external_engines] | external engine contracts only |

### Working vertical slice

```text
signed activation
→ RobloxTaskState V2
→ transitive capability planning
→ evidence ledger
→ mandatory-role context budget
→ simulated engine execution
→ independent validation
→ checkpoint
→ telemetry replay
```

### Reproduced controlled results

- Roblox profile tests: **119 / 119 PASS** [claim:roblox.tests]
- Capability records with planner/execution/validator/positive/negative metadata: **33 / 33** [claim:roblox.capabilities]
- Simulated benchmark: **50 / 50 verified**, unsafe execution **0** [claim:roblox.simulated]

These results are simulated and internal. They do not establish live Studio quality, provider billing savings, production DataStore safety, or competitor superiority.

## Validation

```bash
python -m compileall -q skills/signal-core/profiles/roblox_studio
python -m unittest discover -s tests/roblox_profile -q
python tools/validate_roblox_profile.py
python benchmarks/roblox_profile_benchmark.py --cases 50 --mode simulated --output /tmp/roblox-profile-smoke.json
python tools/verify_claims.py
```

## Compatibility

The general SignalCore layer remains platform-neutral. The restricted Roblox profile can only be activated by an authorized Studio bridge and is deliberately unavailable to ordinary CLI/IDE/Agent Skill prompts.

Read [Roblox Studio mode](ROBLOX_STUDIO_MODE.md), [architecture](skills/signal-core/profiles/roblox_studio/ARCHITECTURE.md), [completion report](skills/signal-core/profiles/roblox_studio/COMPLETION_REPORT.md), [benchmark boundary](skills/signal-core/profiles/roblox_studio/BENCHMARK_RESULTS.md), and [threat model](THREAT_MODEL.md).

## Competitor-group status

| Group | Current status |
|---|---|
| Context Efficiency Pack | **NOT_COMPARABLE** |
| Enterprise Intelligence Pack | **NOT_COMPARABLE** |
| Native Agent Runtime Pack | **NOT_COMPARABLE** |
| Roblox Production Pack | **NOT_COMPARABLE** |
| Full Rival Mega-Pack | **NOT_COMPARABLE** |

No score is assigned without identical tasks, models, providers, permissions, hardware, cache state, timeout, and verifier.

## Status

- Version: **0.0.1**
- Roblox control plane: **INTERNALLY_VERIFIED**
- External execution: **SIMULATED** or **PLANNED**, as labeled
- Public provider superiority: **not established**
- Independent reproduction: **pending**
