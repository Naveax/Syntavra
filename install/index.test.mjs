import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { buildPlan, buildPortablePlan, parseArgs, portableAsset } from "./index.mjs";

test("parses one-command defaults and keeps the version locked", () => {
  const parsed = parseArgs(["--plan"]);
  assert.equal(parsed.profile, "minimal");
  assert.equal(parsed.ref, "main");
  assert.equal(parsed.runtime, "auto");
  const plan = buildPlan(parsed, { command: "python3", prefix: [], version: "3.13.0" }, "linux", "x64");
  assert.equal(plan.version, "0.0.1");
  assert.equal(plan.channel, "pre-release");
  assert.equal(plan.selected.mode, "portable");
  assert.equal(plan.fallback.mode, "python");
  assert.match(plan.source, /Syntavra\.git@main$/);
});

test("portable plan supports CLI-independent installation", () => {
  const parsed = parseArgs(["--runtime", "portable", "--plan", "--install-dir", "/tmp/bin"]);
  const asset = portableAsset(parsed, "linux", "x64");
  assert.equal(asset.name, "syntavra-0.0.1-linux-x64");
  const plan = buildPortablePlan(parsed, "linux", "x64");
  assert.equal(plan.mode, "portable");
  assert.equal(plan.commands[0].phase, "setup");
  assert.match(plan.commands[0].command, /syntavra$/);
});

test("rejects unsafe refs, tags, profiles, and runtime modes", () => {
  assert.throws(() => parseArgs(["--ref", "../main"]), /unsafe git ref/);
  assert.throws(() => parseArgs(["--release-tag", "../tag"]), /unsafe release tag/);
  assert.throws(() => parseArgs(["--profile", "everything"]), /unsupported MCP profile/);
  assert.throws(() => parseArgs(["--runtime", "magic"]), /unsupported runtime mode/);
});

test("help is executable without probing Python or downloading", () => {
  const result = spawnSync(process.execPath, ["install/index.mjs", "--help"], {
    cwd: new URL("..", import.meta.url),
    encoding: "utf8"
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /npx @syntavra\/install/);
  assert.match(result.stdout, /0\.0\.1 pre-release/);
  assert.match(result.stdout, /portable/);
});
