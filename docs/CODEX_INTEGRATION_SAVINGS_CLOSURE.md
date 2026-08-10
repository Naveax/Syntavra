# Codex integration and savings closure

Status: implementation branch for the Codex regressions observed in real 2026 rollout history.

## Observed failures being closed

1. A user/global Codex MCP installation could pin `--project` and `cwd` to the repository from which Syntavra was installed. A later Codex session in another repository could therefore build `syntavra.inspect.map` from Syntavra's own tree.
2. Runtime and installer defaults could create `.syntavra/...` inside the target repository. This invalidated a clean-tree security review and forced a complete re-review.
3. Codex can use MCP and background jobs but does not expose Syntavra pre-tool hooks or result replacement. `syntavra.fabric.route` could recommend a background replacement and the caller could still execute the original command.
4. Local tool-output/schema reductions could be mistaken for provider-billed or whole-session savings.

## Implemented design

### Project identity

The canonical Python entrypoint resolves an explicit project when one is supplied. Otherwise it starts at the host working directory and walks to the nearest Git worktree. It never derives the task project from the installed Syntavra package directory.

Every canonical invocation injects one resolved `--project` and one resolved `--state-root` before handing control to the unified/pre-release/legacy surfaces. Child Syntavra commands inherit the same identity through `SYNTAVRA_PROJECT` and `SYNTAVRA_STATE_ROOT`.

### Out-of-tree runtime state

Default state is keyed by `stable_project_id(project)` beneath a per-user state home:

- Windows: `%LOCALAPPDATA%/Syntavra/projects/<project-id>/...`
- POSIX with XDG: `$XDG_STATE_HOME/syntavra/projects/<project-id>/...`
- fallback: `~/.local/state/syntavra/projects/<project-id>/...`

An explicit `--state-root` remains authoritative. Runtime state is therefore outside the target worktree by default without forbidding an intentional custom state path.

### User/global Codex MCP install

User-scope MCP configuration is dynamic. It no longer contains:

- a static `--project <installation-repo>` argument,
- a static `cwd`,
- `SYNTAVRA_PROJECT`, or
- `SYNTAVRA_STATE_ROOT`.

Project-scope configuration may bind to that exact project because the configuration itself is project-local.

### Codex execution contract

The minimal MCP profile exposes `syntavra.process.submit` and `syntavra.process.completions` in addition to the route tool. A long authorized command can therefore enter the durable broker directly instead of requiring the model to interpret a route recommendation and then shell-poll.

`syntavra.process.submit` still requires exact evidence and explicit per-call user authorization. The redundant process-wide `SYNTAVRA_ALLOW_UNSANDBOXED_PROCESS` switch is no longer a second authority gate.

The Agent Skill contract requires:

- use broker submission for long authorized commands,
- do not execute the original command after `JOB_ACCEPTED`,
- use completion events instead of shell polling,
- obey a non-empty `replacement_argv`,
- stop on `blocked`, and
- reject repository retrieval that unexpectedly resolves to Syntavra's own source tree for a different task repository.

### Savings evidence boundary

`SavingsLedger` is now explicitly `LOCAL_MODEL_VISIBLE_ESTIMATE`. It can report local original/visible/saved tokens but cannot claim provider/session superiority. The statusline no longer renders dollar savings from that local ledger.

Net token, cost or latency superiority remains gated by paired provider-observed baseline/candidate receipts, equivalent workload/cache/model/environment conditions, and verifier success.

## Legacy repair

`tools/repair_codex_integration.py` is dry-run by default. With `--apply` it:

1. backs up the user Codex MCP config before mutation,
2. removes legacy Syntavra project/state argv pins, static `cwd`, and static project/state environment values,
3. moves only known legacy runtime directories (`pre-release`, `runtime-v3`, `install`) out of `<project>/.syntavra` into the external project state quarantine,
4. preserves unknown/user-managed files rather than deleting the whole `.syntavra` directory blindly.

Example on Windows:

```powershell
python tools/repair_codex_integration.py --project C:\Users\navea\Desktop\NXM
python tools/repair_codex_integration.py --project C:\Users\navea\Desktop\NXM --apply
```

Restart Codex after an applied user MCP config repair.

## Regression gates

The branch must keep these invariants:

- auto project discovery resolves the active worktree,
- default state is outside the project,
- canonical entry injects exactly one project/state pair,
- user-scope Codex config has no static project binding,
- project-scope config binds only to its own project,
- minimal MCP includes submit/completion broker tools,
- an explicitly authorized broker call does not require a second environment switch,
- local savings cannot claim net provider savings,
- legacy repair is backup-first and leaves unrelated MCP servers/config fields untouched.

## Real A/B closure protocol

The implementation fixes are not sufficient evidence for a marketing claim. The final efficiency gate is a paired Codex experiment:

1. Choose at least 10 identical repository tasks; 30 pairs is preferred.
2. For every task run a plain-host baseline and `syntavra-minimal` candidate with the same model, reasoning setting, repository tree, permissions, cache mode and verifier.
3. Record provider-observed fresh input, cached input, output/reasoning tokens, wall time and quota/cost fields.
4. Require verifier success and zero security regressions for both arms.
5. Feed the receipts to the existing provider-billed / hardened paired comparison gate.
6. Claim net savings only when the paired gate says the result is proven. Otherwise publish the local context reduction separately and report net superiority as not proven.
