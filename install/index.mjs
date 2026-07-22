#!/usr/bin/env node
import { createHash } from "node:crypto";
import { createWriteStream, existsSync } from "node:fs";
import { chmod, copyFile, mkdir, readFile, rename, rm, stat } from "node:fs/promises";
import { get } from "node:https";
import os from "node:os";
import process from "node:process";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const VERSION = "0.0.1";
const CHANNEL = "pre-release";
const RELEASE_TAG = "v0.0.1-pre-release";
const PROFILES = new Set(["minimal", "balanced", "audit"]);
const RUNTIMES = new Set(["auto", "portable", "python"]);
const REF_PATTERN = /^(?![-/])(?!.*(?:^|\/)\.\.(?:\/|$))[A-Za-z0-9._/-]+$/;
const MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024;

function usage() {
  return `Syntavra ${VERSION} ${CHANNEL} installer

Usage:
  npx @syntavra/install [options]
  npx github:Naveax/Syntavra [options]

Options:
  --project <path>          Project to configure (default: current directory)
  --profile <name>          minimal, balanced, or audit (default: minimal)
  --ref <git-ref>           Git ref for Python fallback (default: main)
  --runtime <mode>          auto, portable, or python (default: auto)
  --python <command>        Explicit Python executable
  --install-dir <path>      Portable binary directory
  --release-tag <tag>       Portable GitHub release tag (default: ${RELEASE_TAG})
  --offline-artifact <path> Verified local portable binary instead of download
  --plan                    Print the exact plan without changing the system
  --no-setup                Install but do not configure detected hosts
  --skip-status             Do not run final status verification
  -h, --help                Show this help
`;
}

function defaultInstallDir() {
  if (process.platform === "win32") {
    return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "Syntavra", "bin");
  }
  return path.join(os.homedir(), ".local", "bin");
}

export function parseArgs(argv) {
  const options = {
    project: process.cwd(),
    profile: "minimal",
    ref: "main",
    runtime: "auto",
    python: "",
    installDir: defaultInstallDir(),
    releaseTag: RELEASE_TAG,
    offlineArtifact: "",
    plan: false,
    setup: true,
    status: true
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = () => {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) throw new Error(`${arg} requires a value`);
      index += 1;
      return value;
    };
    if (arg === "--project") options.project = path.resolve(next());
    else if (arg === "--profile") options.profile = next();
    else if (arg === "--ref") options.ref = next();
    else if (arg === "--runtime") options.runtime = next();
    else if (arg === "--python") options.python = next();
    else if (arg === "--install-dir") options.installDir = path.resolve(next());
    else if (arg === "--release-tag") options.releaseTag = next();
    else if (arg === "--offline-artifact") options.offlineArtifact = path.resolve(next());
    else if (arg === "--plan") options.plan = true;
    else if (arg === "--no-setup") options.setup = false;
    else if (arg === "--skip-status") options.status = false;
    else if (arg === "-h" || arg === "--help") options.help = true;
    else throw new Error(`unknown option: ${arg}`);
  }
  if (!PROFILES.has(options.profile)) throw new Error(`unsupported MCP profile: ${options.profile}`);
  if (!RUNTIMES.has(options.runtime)) throw new Error(`unsupported runtime mode: ${options.runtime}`);
  if (!REF_PATTERN.test(options.ref)) throw new Error(`unsafe git ref: ${options.ref}`);
  if (!REF_PATTERN.test(options.releaseTag)) throw new Error(`unsafe release tag: ${options.releaseTag}`);
  return options;
}

function candidateCommands(explicit) {
  if (explicit) return [[explicit]];
  if (process.env.PYTHON) return [[process.env.PYTHON]];
  if (process.platform === "win32") {
    return [["py", "-3.13"], ["py", "-3.12"], ["py", "-3.11"], ["python"]];
  }
  return [["python3.13"], ["python3.12"], ["python3.11"], ["python3"], ["python"]];
}

function probe(candidate) {
  const [command, ...prefix] = candidate;
  const result = spawnSync(command, [
    ...prefix,
    "-c",
    "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(sys.version_info < (3, 11))"
  ], { encoding: "utf8", shell: false });
  return result.status === 0 ? { command, prefix, version: result.stdout.trim() } : null;
}

export function resolvePython(explicit = "", { required = true } = {}) {
  for (const candidate of candidateCommands(explicit)) {
    const resolved = probe(candidate);
    if (resolved) return resolved;
  }
  if (!required) return null;
  throw new Error("Python 3.11 or newer was not found and the portable runtime was unavailable.");
}

function platformKey(platform = process.platform, arch = process.arch) {
  const supported = new Set(["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64", "win32-x64", "win32-arm64"]);
  const key = `${platform}-${arch}`;
  if (!supported.has(key)) throw new Error(`portable runtime is not defined for ${key}`);
  return key;
}

export function portableAsset(options, platform = process.platform, arch = process.arch) {
  const key = platformKey(platform, arch);
  const extension = platform === "win32" ? ".exe" : "";
  const name = `syntavra-${VERSION}-${key}${extension}`;
  const base = `https://github.com/Naveax/Syntavra/releases/download/${options.releaseTag}`;
  return {
    key,
    name,
    checksumName: `${name}.sha256`,
    url: `${base}/${name}`,
    checksumUrl: `${base}/${name}.sha256`,
    destination: path.join(options.installDir, platform === "win32" ? "syntavra.exe" : "syntavra")
  };
}

function renderCommand(command, args) {
  return [command, ...args].map((value) => JSON.stringify(value)).join(" ");
}

function run(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit", shell: false });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`command failed with exit code ${result.status}: ${renderCommand(command, args)}`);
}

export function buildPythonPlan(options, python) {
  const source = `git+https://github.com/Naveax/Syntavra.git@${options.ref}`;
  const installArgs = [...python.prefix, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", source];
  const setupArgs = [...python.prefix, "-m", "syntavra_runtime", "--project", options.project, "setup", "--apply", "--mcp-profile", options.profile];
  const statusArgs = [...python.prefix, "-m", "syntavra_runtime", "--project", options.project, "status"];
  return {
    mode: "python",
    source,
    python: { command: python.command, prefix: python.prefix, version: python.version },
    commands: [
      { phase: "install", command: python.command, args: installArgs },
      ...(options.setup ? [{ phase: "setup", command: python.command, args: setupArgs }] : []),
      ...(options.status ? [{ phase: "status", command: python.command, args: statusArgs }] : [])
    ]
  };
}

export function buildPortablePlan(options, platform = process.platform, arch = process.arch) {
  const asset = portableAsset(options, platform, arch);
  return {
    mode: "portable",
    asset,
    offlineArtifact: options.offlineArtifact || null,
    commands: [
      ...(options.setup ? [{ phase: "setup", command: asset.destination, args: ["--project", options.project, "setup", "--apply", "--mcp-profile", options.profile] }] : []),
      ...(options.status ? [{ phase: "status", command: asset.destination, args: ["--project", options.project, "status"] }] : [])
    ]
  };
}

export function buildPlan(options, python = null, platform = process.platform, arch = process.arch) {
  const portable = buildPortablePlan(options, platform, arch);
  const pythonPlan = python ? buildPythonPlan(options, python) : null;
  const selected = options.runtime === "python" ? pythonPlan : options.runtime === "portable" ? portable : portable;
  return {
    installer: "@syntavra/install",
    version: VERSION,
    channel: CHANNEL,
    runtime: options.runtime,
    project: options.project,
    profile: options.profile,
    selected,
    fallback: options.runtime === "auto" ? pythonPlan : null,
    source: pythonPlan?.source || `git+https://github.com/Naveax/Syntavra.git@${options.ref}`,
    commands: selected?.commands || []
  };
}

function request(url, redirects = 5) {
  return new Promise((resolve, reject) => {
    const call = get(url, { headers: { "User-Agent": "@syntavra/install" } }, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location && redirects > 0) {
        response.resume();
        resolve(request(new URL(response.headers.location, url).toString(), redirects - 1));
        return;
      }
      if (response.statusCode !== 200) {
        response.resume();
        reject(new Error(`download failed (${response.statusCode}) for ${url}`));
        return;
      }
      resolve(response);
    });
    call.on("error", reject);
  });
}

async function download(url, destination) {
  const response = await request(url);
  let bytes = 0;
  await new Promise((resolve, reject) => {
    const output = createWriteStream(destination, { flags: "wx" });
    response.on("data", (chunk) => {
      bytes += chunk.length;
      if (bytes > MAX_DOWNLOAD_BYTES) response.destroy(new Error("download exceeded size limit"));
    });
    response.pipe(output);
    output.on("finish", resolve);
    output.on("error", reject);
    response.on("error", reject);
  });
}

async function sha256(file) {
  const hash = createHash("sha256");
  hash.update(await readFile(file));
  return hash.digest("hex");
}

async function installPortable(options) {
  const asset = portableAsset(options);
  await mkdir(options.installDir, { recursive: true });
  const temporaryDir = await import("node:fs/promises").then(({ mkdtemp }) => mkdtemp(path.join(os.tmpdir(), "syntavra-install-")));
  const binary = path.join(temporaryDir, asset.name);
  const checksum = path.join(temporaryDir, asset.checksumName);
  try {
    if (options.offlineArtifact) {
      if (!existsSync(options.offlineArtifact)) throw new Error(`offline artifact not found: ${options.offlineArtifact}`);
      await copyFile(options.offlineArtifact, binary);
      const localChecksum = `${options.offlineArtifact}.sha256`;
      if (!existsSync(localChecksum)) throw new Error(`offline checksum not found: ${localChecksum}`);
      await copyFile(localChecksum, checksum);
    } else {
      await download(asset.url, binary);
      await download(asset.checksumUrl, checksum);
    }
    const expected = (await readFile(checksum, "utf8")).trim().split(/\s+/)[0].toLowerCase();
    const actual = await sha256(binary);
    if (!/^[0-9a-f]{64}$/.test(expected) || actual !== expected) throw new Error("portable runtime checksum verification failed");
    if (process.platform !== "win32") await chmod(binary, 0o755);
    const backup = `${asset.destination}.previous`;
    const hadPrevious = existsSync(asset.destination);
    if (hadPrevious) {
      await rm(backup, { force: true });
      await rename(asset.destination, backup);
    }
    try {
      await copyFile(binary, `${asset.destination}.new`);
      if (process.platform !== "win32") await chmod(`${asset.destination}.new`, 0o755);
      await rename(`${asset.destination}.new`, asset.destination);
    } catch (error) {
      await rm(`${asset.destination}.new`, { force: true });
      if (hadPrevious && existsSync(backup) && !existsSync(asset.destination)) await rename(backup, asset.destination);
      throw error;
    }
    const installedSize = (await stat(asset.destination)).size;
    return { ok: true, asset, sha256: actual, bytes: installedSize, backup: existsSync(backup) ? backup : null };
  } finally {
    await rm(temporaryDir, { recursive: true, force: true });
  }
}

async function executePortable(options) {
  const installed = await installPortable(options);
  const plan = buildPortablePlan(options);
  for (const item of plan.commands) {
    process.stdout.write(`\n[${item.phase}] ${renderCommand(item.command, item.args)}\n`);
    run(item.command, item.args);
  }
  return installed;
}

function executePython(options, python) {
  const plan = buildPythonPlan(options, python);
  for (const item of plan.commands) {
    process.stdout.write(`\n[${item.phase}] ${renderCommand(item.command, item.args)}\n`);
    run(item.command, item.args);
  }
  return { ok: true, mode: "python", python: plan.python };
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  if (options.help) {
    process.stdout.write(usage());
    return 0;
  }
  const python = options.runtime === "portable" ? null : resolvePython(options.python, { required: options.runtime === "python" });
  const plan = buildPlan(options, python);
  if (options.plan) {
    process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
    return 0;
  }
  process.stdout.write(`Syntavra ${VERSION} ${CHANNEL}: runtime=${options.runtime}\n`);
  if (options.runtime !== "python") {
    try {
      const result = await executePortable(options);
      process.stdout.write(`\nSyntavra portable installation completed: ${result.asset.destination}\n`);
      return 0;
    } catch (error) {
      if (options.runtime === "portable") throw error;
      process.stderr.write(`Portable runtime unavailable: ${error instanceof Error ? error.message : String(error)}\nFalling back to Python.\n`);
    }
  }
  const resolved = python || resolvePython(options.python);
  executePython(options, resolved);
  process.stdout.write("\nSyntavra Python installation and verification completed.\n");
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().then((code) => { process.exitCode = code; }).catch((error) => {
    process.stderr.write(`Syntavra installer failed: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
