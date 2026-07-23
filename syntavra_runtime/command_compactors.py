from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


_ERROR_RE = re.compile(
    r"(?i)\b(error|failed|failure|panic|assertion|traceback|exception|fatal|denied|"
    r"timeout|segmentation fault|not found|permission denied)\b"
)
_LOCATION_RE = re.compile(
    r"(?:[^\s:]+\.(?:py|rs|js|jsx|ts|tsx|java|cs|go|rb|php|lua|luau|cpp|c|h):\d+|"
    r"File \"[^\"]+\", line \d+|\bat [\w.$<>]+\([^)]*:\d+\))"
)
_TEST_SUMMARY_RE = re.compile(
    r"(?i)(?:\b\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed)\b|"
    r"tests?:\s*\d+|test suites?:|failures?:\s*\d+|successes?:\s*\d+|"
    r"test result:|packages?\s+\d+|ok\s+\S+\s+[\d.]+s)"
)


def _dedup(lines: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(line for line in lines if line.strip()))


def _errors(lines: Sequence[str]) -> list[str]:
    return _dedup(line for line in lines if _ERROR_RE.search(line) or _LOCATION_RE.search(line))


def _head_tail(lines: Sequence[str], head: int = 20, tail: int = 12) -> list[str]:
    if len(lines) <= head + tail:
        return list(lines)
    return [*lines[:head], f"[… {len(lines) - head - tail} lines omitted …]", *lines[-tail:]]


def _test(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    summaries = _dedup(line for line in lines if _TEST_SUMMARY_RE.search(line))
    failures = _dedup(
        line for line in lines
        if line.lstrip().startswith(("E ", "F ", "FAILED", ">", "--- FAIL", "failures:"))
        or _ERROR_RE.search(line)
    )
    return _dedup([*summaries[-16:], *failures[:64], *errors[:32]]), len(errors)


def _git_status(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    selected = _dedup(
        line for line in lines
        if line.startswith(("On branch ", "Your branch ", "Changes ", "Untracked ", "nothing to commit"))
        or line[:2] in {" M", "M ", "A ", " D", "D ", "??", "R ", "UU"}
    )
    return [*selected[:160], *errors[:24]], len(errors)


def _git_diff(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    headers = [line for line in lines if line.startswith(("diff --git", "index ", "--- ", "+++ ", "@@ "))]
    changes = [line for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    return _dedup([*headers[:120], *changes[:48], *changes[-24:], *errors[:24]]), len(errors)


def _git_log(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    commits = [line for line in lines if line.startswith(("commit ", "Author:", "Date:", "    "))]
    return [*_head_tail(commits, 40, 20), *errors[:16]], len(errors)


def _search(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    grouped: dict[str, list[str]] = {}
    for line in lines:
        if not line.strip():
            continue
        key = line.split(":", 1)[0] if ":" in line else str(Path(line).parent)
        grouped.setdefault(key, []).append(line)
    selected = [f"results={sum(map(len, grouped.values()))} groups={len(grouped)}"]
    for key, values in list(grouped.items())[:50]:
        selected.append(f"[{key}] {len(values)}")
        selected.extend(values[:5])
    return [*selected, *errors[:24]], len(errors)


def _lint(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    diagnostics = _dedup(
        line for line in lines
        if _LOCATION_RE.search(line)
        or re.search(r"(?i)\b(warning|error|note):", line)
        or re.match(r"^\S+\.(?:py|js|jsx|ts|tsx|rs|go):\d+", line)
    )
    summaries = _dedup(line for line in lines if re.search(r"(?i)\b(found|checked|fixed|violations?|problems?)\b", line))
    return [*diagnostics[:120], *summaries[-20:], *errors[:24]], len(errors)


def _docker_build(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    selected = _dedup(
        line for line in lines
        if re.search(r"(?i)(^#\d+|exporting|writing image|naming to|cached|done|warning|error|failed)", line)
    )
    return [*_head_tail(selected, 80, 30), *errors[:32]], len(errors)


def _table(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    nonempty = [line for line in lines if line.strip()]
    return [*_head_tail(nonempty, 35, 15), *errors[:24]], len(errors)


def _json_or_table(lines: Sequence[str]) -> tuple[list[str], int]:
    text = "\n".join(lines)
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return _table(lines)

    def shrink(item, depth: int = 0):
        if isinstance(item, dict):
            keys = sorted(item, key=str)
            result = {str(key): shrink(item[key], depth + 1) for key in keys[:30]}
            if len(keys) > 30:
                result["<omitted_keys>"] = len(keys) - 30
            return result
        if isinstance(item, list):
            if len(item) <= 10:
                return [shrink(child, depth + 1) for child in item]
            return {"length": len(item), "head": [shrink(child, depth + 1) for child in item[:6]], "tail": [shrink(child, depth + 1) for child in item[-2:]]}
        if isinstance(item, str) and len(item) > 300:
            return item[:180] + f"…<{len(item)} chars>…" + item[-60:]
        return item

    rendered = json.dumps(shrink(value), ensure_ascii=False, sort_keys=True, indent=2).splitlines()
    return rendered, len(_errors(lines))


@dataclass(frozen=True)
class CommandCompactorPlugin:
    name: str
    executables: tuple[str, ...]
    argument_pattern: re.Pattern[str] | None
    selector: Callable[[Sequence[str]], tuple[list[str], int]]

    def matches(self, executable: str, arguments: str) -> bool:
        return executable in self.executables and (self.argument_pattern is None or bool(self.argument_pattern.search(arguments)))




def _head_tail_selector(lines: Sequence[str]) -> tuple[list[str], int]:
    errors = _errors(lines)
    return [*_head_tail([line for line in lines if line.strip()], 30, 20), *errors[:32]], len(errors)


class CommandCompactorRegistry:
    """Specific compactors for common coding-agent command output.

    The registry deliberately contains command-family plugins instead of one
    universal truncator. Exact stdout/stderr is externalized before any plugin
    view is returned by the host pipeline.
    """

    def __init__(self) -> None:
        self.plugins: tuple[CommandCompactorPlugin, ...] = (
            CommandCompactorPlugin("git-status", ("git",), re.compile(r"^status\b"), _git_status),
            CommandCompactorPlugin("git-diff", ("git",), re.compile(r"^(?:diff|show)\b"), _git_diff),
            CommandCompactorPlugin("git-log", ("git",), re.compile(r"^log\b"), _git_log),
            CommandCompactorPlugin("git-branch", ("git",), re.compile(r"^branch\b"), _table),
            CommandCompactorPlugin("git-stash", ("git",), re.compile(r"^stash\b"), _table),
            CommandCompactorPlugin("git-worktree", ("git",), re.compile(r"^worktree\b"), _table),
            CommandCompactorPlugin("git-fetch", ("git",), re.compile(r"^(?:fetch|pull|push)\b"), _table),
            CommandCompactorPlugin("git-remote", ("git",), re.compile(r"^remote\b"), _table),
            CommandCompactorPlugin("git-tag", ("git",), re.compile(r"^tag\b"), _table),
            CommandCompactorPlugin("git-clean", ("git",), re.compile(r"^(?:clean|reset|restore|checkout|switch)\b"), _table),
            CommandCompactorPlugin("ripgrep", ("rg",), None, _search),
            CommandCompactorPlugin("grep", ("grep",), None, _search),
            CommandCompactorPlugin("find", ("find", "fd"), None, _search),
            CommandCompactorPlugin("tree", ("tree",), None, _search),
            CommandCompactorPlugin("ls", ("ls", "dir"), None, _table),
            CommandCompactorPlugin("pytest", ("pytest", "py.test"), None, _test),
            CommandCompactorPlugin("cargo-test", ("cargo",), re.compile(r"\btest\b"), _test),
            CommandCompactorPlugin("cargo-check", ("cargo",), re.compile(r"\b(?:check|clippy|build)\b"), _lint),
            CommandCompactorPlugin("go-test", ("go",), re.compile(r"^test\b"), _test),
            CommandCompactorPlugin("go-build", ("go",), re.compile(r"^(?:build|vet|list)\b"), _lint),
            CommandCompactorPlugin("npm-test", ("npm",), re.compile(r"^(?:test|run test)\b"), _test),
            CommandCompactorPlugin("npm-list", ("npm",), re.compile(r"^(?:list|ls|outdated|audit)\b"), _json_or_table),
            CommandCompactorPlugin("pnpm-test", ("pnpm",), re.compile(r"^(?:test|run test)\b"), _test),
            CommandCompactorPlugin("pnpm-list", ("pnpm",), re.compile(r"^(?:list|ls|outdated|audit)\b"), _json_or_table),
            CommandCompactorPlugin("yarn-test", ("yarn",), re.compile(r"^(?:test|run test)\b"), _test),
            CommandCompactorPlugin("yarn-list", ("yarn",), re.compile(r"^(?:list|info|audit)\b"), _json_or_table),
            CommandCompactorPlugin("jest", ("jest",), None, _test),
            CommandCompactorPlugin("vitest", ("vitest",), None, _test),
            CommandCompactorPlugin("playwright", ("playwright",), re.compile(r"^test\b"), _test),
            CommandCompactorPlugin("coverage", ("coverage",), None, _test),
            CommandCompactorPlugin("ruff", ("ruff",), None, _lint),
            CommandCompactorPlugin("mypy", ("mypy",), None, _lint),
            CommandCompactorPlugin("eslint", ("eslint",), None, _lint),
            CommandCompactorPlugin("biome", ("biome",), None, _lint),
            CommandCompactorPlugin("pylint", ("pylint",), None, _lint),
            CommandCompactorPlugin("flake8", ("flake8",), None, _lint),
            CommandCompactorPlugin("docker-build", ("docker", "podman"), re.compile(r"^build\b"), _docker_build),
            CommandCompactorPlugin("docker-ps", ("docker", "podman"), re.compile(r"^(?:ps|images)\b"), _table),
            CommandCompactorPlugin("docker-logs", ("docker", "podman"), re.compile(r"^logs\b"), _table),
            CommandCompactorPlugin("docker-inspect", ("docker", "podman"), re.compile(r"^(?:inspect|stats|info)\b"), _json_or_table),
            CommandCompactorPlugin("docker-compose", ("docker-compose",), None, _table),
            CommandCompactorPlugin("kubectl-get", ("kubectl",), re.compile(r"^(?:get|describe)\b"), _json_or_table),
            CommandCompactorPlugin("kubectl-logs", ("kubectl",), re.compile(r"^logs\b"), _table),
            CommandCompactorPlugin("kubectl-events", ("kubectl",), re.compile(r"^(?:events|top|api-resources)\b"), _json_or_table),
            CommandCompactorPlugin("helm", ("helm",), None, _json_or_table),
            CommandCompactorPlugin("terraform", ("terraform", "tofu"), None, _json_or_table),
            CommandCompactorPlugin("ansible", ("ansible", "ansible-playbook"), None, _table),
            CommandCompactorPlugin("gh-pr", ("gh",), re.compile(r"^pr\b"), _json_or_table),
            CommandCompactorPlugin("gh-issue", ("gh",), re.compile(r"^issue\b"), _json_or_table),
            CommandCompactorPlugin("gh-run", ("gh",), re.compile(r"^run\b"), _json_or_table),
            CommandCompactorPlugin("gh-repo", ("gh",), re.compile(r"^(?:repo|api)\b"), _json_or_table),
            CommandCompactorPlugin("pip", ("pip", "pip3"), None, _json_or_table),
            CommandCompactorPlugin("uv", ("uv",), None, _json_or_table),
            CommandCompactorPlugin("poetry", ("poetry",), None, _json_or_table),
            CommandCompactorPlugin("aws", ("aws",), None, _json_or_table),
            CommandCompactorPlugin("gcloud", ("gcloud",), None, _json_or_table),
            CommandCompactorPlugin("azure-cli", ("az",), None, _json_or_table),
            CommandCompactorPlugin("curl", ("curl", "wget"), None, _head_tail_selector),
            CommandCompactorPlugin("systemctl", ("systemctl",), None, _table),
            CommandCompactorPlugin("journalctl", ("journalctl",), None, _table),
            CommandCompactorPlugin("process-list", ("ps", "tasklist"), None, _table),
            CommandCompactorPlugin("disk-usage", ("du", "df"), None, _table),
            CommandCompactorPlugin("dotnet", ("dotnet",), None, _test),
            CommandCompactorPlugin("maven", ("mvn", "mvnw"), None, _test),
            CommandCompactorPlugin("gradle", ("gradle", "gradlew"), None, _test),
            CommandCompactorPlugin("cmake", ("cmake", "ctest", "ninja", "make"), None, _test),
            CommandCompactorPlugin("java", ("java", "javac"), None, _table),
            CommandCompactorPlugin("rustc", ("rustc",), None, _lint),
            CommandCompactorPlugin("powershell", ("pwsh", "powershell"), None, _table),
            CommandCompactorPlugin("package-manager", ("apt", "apt-get", "dnf", "yum", "brew", "winget", "choco", "scoop"), None, _table),
        )

    @staticmethod
    def _command_parts(command: str | Iterable[str]) -> tuple[str, str]:
        if isinstance(command, str):
            parts = command.strip().split()
        else:
            parts = [str(item) for item in command]
        if not parts:
            return "", ""
        return Path(parts[0]).name.casefold(), " ".join(parts[1:]).casefold()

    def select(self, command: str | Iterable[str], lines: Sequence[str]) -> tuple[str | None, list[str], int]:
        executable, arguments = self._command_parts(command)
        for plugin in self.plugins:
            if plugin.matches(executable, arguments):
                selected, retained_errors = plugin.selector(lines)
                return plugin.name, selected, retained_errors
        return None, [], 0

    def manifest(self) -> dict[str, object]:
        return {
            "plugins": [plugin.name for plugin in self.plugins],
            "count": len(self.plugins),
            "exact_output_required": True,
            "target_minimum": 60,
            "coverage_gate": len(self.plugins) >= 60,
        }
