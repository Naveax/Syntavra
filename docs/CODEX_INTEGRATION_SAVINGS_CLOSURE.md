# Codex integration and savings closure

Status: implementation branch for the Codex regressions observed in real 2026 rollout history.

## Observed failures being closed

1. A user/global Codex MCP installation could pin `--project` and `cwd` to the repository from which Syntavra was installed. A later Codex session in another repository could therefore build `syntavra.inspect.map` from Syntavra's own tree.
2. Runtime and installer defaults could create `.syntavra/...` inside the target repository. This invalidated a clean-tree security review and forced a complete re-review.
3. Codex can use MCP and background jobs but does not expose Syntavra pre-tool hooks or result replacement. `syntavra.fabric.route` could recommend a background replacement and the caller could still execute the original command.
4. Local tool-output/schema reductions could be mistaken for provider-billed or whole-session savings.
5. Older Syntavra Codex metadata targeted `.codex/mcp.json` and `.codex/skills/syntavra`; the current Syntavra Codex contract uses TOML MCP configuration and Agent Skills under `.agents/skills`.
6. Depending on the host surface, process cwd is not strong enough evidence of repository identity for a user/global MCP process.

## Implemented design

### Project identity

Normal Syntavra CLI invocations resolve an explicit project when one is supplied. Otherwise they start at the host working directory and walk to the nearest Git worktree. They never derive the task project from the installed Syntavra package directory.

Every normal canonical invocation injects one resolved `--project` and one resolved `--state-root` before handing control to the unified/pre-release/legacy surfaces. Child Syntavra commands inherit the same identity through `SYNTAVRA_PROJECT` and `SYNTAVRA_STATE_ROOT`.

Codex MCP startup is intentionally different. The internal `codex-mcp-bridge` route runs before normal project/state canonicalization so a user/global MCP process cannot accidentally bind itself from process cwd.

### Explicit Codex workspace bridge

The managed Codex MCP entry reuses the launcher that installed Syntavra:

- classic Python checkout/runtime: `python -m syntavra_runtime codex-mcp-bridge`,
- product/portable launcher: `syntavra codex-mcp-bridge`.

A Python source-checkout install receives an MCP-process-only `PYTHONPATH` pointing at the Syntavra source root so the module stays importable when Codex starts the global MCP process from another working directory. That import path is not repository authority.

User/global bridge behavior is fail-closed:

1. start unbound,
2. expose `syntavra.project.bind`,
3. reject repository/process tools while unbound,
4. accept only an existing directory inside a Git worktree,
5. canonicalize the supplied directory to the exact Git root,
6. construct project-keyed external state only after binding,
7. expose `syntavra.status` for an exact `details.project_root` cross-check before retrieval/execution.

Trusted project-scope Codex configuration may auto-bind because its local config carries an exact `SYNTAVRA_PROJECT` and `cwd` for that project.

### Out-of-tree runtime state

Default state is keyed by `stable_project_id(project)` beneath a per-user state home:

- Windows: `%LOCALAPPDATA%/Syntavra/projects/<project-id>/...`
- POSIX with XDG: `$XDG_STATE_HOME/syntavra/projects/<project-id>/...`
- fallback: `~/.local/state/syntavra/projects/<project-id>/...`

An explicit `--state-root` remains authoritative. Runtime state is therefore outside the target worktree by default without forbidding an intentional custom state path. `ZeroFrictionManager` uses the same external default when called directly, so the guarantee is not limited to the CLI bootstrap path.

### Current Syntavra Codex installation contract

The Syntavra Codex adapter uses:

- user MCP configuration: `~/.codex/config.toml`,
- trusted project MCP configuration: `.codex/config.toml`,
- user Agent Skill: `~/.agents/skills/syntavra`,
- repository Agent Skill: `.agents/skills/syntavra`.

Both Syntavra installer implementations use this contract: the classic `HostInstaller` and the zero-friction `HostInstallationManager` used by product setup/repair flows.

User-scope MCP configuration no longer contains:

- a static `--project <installation-repo>` argument,
- a static task-repository `cwd`,
- `SYNTAVRA_PROJECT`, or
- `SYNTAVRA_STATE_ROOT`.

Project-scope configuration may bind to that exact project because the configuration itself is project-local. TOML writes are fail-closed on invalid input, replace only Syntavra's MCP table, and preserve unrelated user configuration including ordinary tables and arrays-of-tables.

### Codex execution contract

The minimal MCP profile exposes `syntavra.process.submit` and `syntavra.process.completions` in addition to the route tool. A long authorized command can therefore enter the durable broker directly instead of requiring the model to interpret a route recommendation and then shell-poll.

`syntavra.process.submit` still requires exact evidence and explicit per-call user authorization. The redundant process-wide `SYNTAVRA_ALLOW_UNSANDBOXED_PROCESS` switch is no longer a second authority gate.

The Agent Skill contract requires:

- bind the active Git workspace before repository/process tools on user/global Codex installs,
- verify `syntavra.status.details.project_root` against the active repository,
- use broker submission for long authorized commands,
- do not execute the original command after `JOB_ACCEPTED`,
- use completion events instead of shell polling,
- obey a non-empty `replacement_argv`,
- stop on `blocked`, and
- reject repository retrieval that unexpectedly resolves to Syntavra's own source tree for a different task repository.

### Savings evidence boundary

`SavingsLedger` is explicitly `LOCAL_MODEL_VISIBLE_ESTIMATE`. It can report local original/visible/saved tokens but cannot claim provider/session superiority. The statusline does not render dollar savings from that local ledger.

Net token, cost or latency superiority remains gated by paired provider-observed baseline/candidate receipts, equivalent workload/cache/model/environment conditions, and verifier success.

## Legacy repair

`tools/repair_codex_integration.py` is dry-run by default. With `--apply` it:

1. validates the current `~/.codex/config.toml` and detects stale/static Syntavra user-scope configuration,
2. backs up and removes the obsolete Syntavra entry from legacy `~/.codex/mcp.json` while preserving unrelated servers,
3. moves only known legacy runtime directories (`pre-release`, `runtime-v3`, `install`) out of `<project>/.syntavra` into the external project-state quarantine,
4. moves legacy user/project `.codex/skills/syntavra` copies into the same quarantine,
5. preserves unknown/user-managed `.syntavra` files rather than deleting the directory blindly,
6. installs the current dynamic user-scope TOML MCP bridge and current `~/.agents/skills/syntavra` skill through the backup-first installer.

Example on Windows:

```powershell
python tools/repair_codex_integration.py --project C:\Users\navea\Desktop\NXM
python tools/repair_codex_integration.py --project C:\Users\navea\Desktop\NXM --apply
```

Restart Codex after an applied user MCP migration so the old MCP subprocess cannot remain resident.

## Regression gates

The branch must keep these invariants:

- auto project discovery resolves the active worktree for normal CLI commands,
- the Codex bridge route bypasses normal cwd-based project canonicalization,
- user/global Codex starts unbound and rejects repository/process calls before `syntavra.project.bind`,
- bind canonicalizes nested paths to the exact Git root and rejects non-Git directories,
- default state is outside the project even when `ZeroFrictionManager` is called directly,
- canonical normal entry injects exactly one project/state pair,
- user-scope Codex TOML config has no static task-project binding,
- project-scope Codex TOML config binds only to its own project,
- the managed MCP launcher works through both the Python runtime launcher and `syntavra` product launcher,
- unrelated TOML/MCP settings survive installation and repair,
- current skill placement is `.agents/skills/syntavra`,
- minimal MCP contains 10 tools including submit/completion broker tools,
- an explicitly authorized broker call does not require a second environment switch,
- local savings cannot claim net provider savings,
- legacy repair is backup-first and migrates only known Syntavra-owned paths,
- exact-head validation leaves the Git working-tree fingerprint unchanged and creates no new `.syntavra` pollution.

## Exact-head validation

Use the repo-owned closure validator after checking out the final candidate head:

```powershell
python tools/validate_codex_integration_closure.py --expected-head <FINAL_SHA> --full-runtime
```

The validator compiles the Python runtime/tests, runs the targeted Codex closure suite, optionally runs the complete runtime unittest discovery, and compares working-tree state before/after. A newly created repository `.syntavra` path or any unexpected tree mutation fails the closure.

## Real A/B closure protocol

The implementation fixes are not sufficient evidence for a marketing claim. The final efficiency gate is a paired Codex experiment:

1. Choose at least 10 identical repository tasks; 30 pairs is preferred.
2. For every task run a plain-host baseline and `syntavra-minimal` candidate with the same model, reasoning setting, repository tree, permissions, cache mode and verifier.
3. Record provider-observed fresh input, cached input, output/reasoning tokens, wall time and quota/cost fields.
4. Require verifier success and zero security regressions for both arms.
5. Feed the receipts to the existing provider-billed / hardened paired comparison gate.
6. Claim net savings only when the paired gate says the result is proven. Otherwise publish the local context reduction separately and report net superiority as not proven.
