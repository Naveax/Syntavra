from __future__ import annotations

import os
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


_UNSAFE = re.compile(r"(?:\|\||&&|[|;`]|\$\(|\n|\r|>|<)")


@dataclass(frozen=True)
class RewriteRule:
    name: str
    executable: str
    argument_pattern: re.Pattern[str]
    append: tuple[str, ...] = ()
    replace: tuple[tuple[str, str], ...] = ()
    blocked_flags: tuple[str, ...] = ()
    reason: str = "reduce machine-irrelevant output"


@dataclass(frozen=True)
class RewriteResult:
    original: tuple[str, ...]
    rewritten: tuple[str, ...]
    changed: bool
    rule: str | None
    safe: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _rule(name: str, executable: str, pattern: str, *, append: Sequence[str] = (), replace: Sequence[tuple[str, str]] = (), blocked: Sequence[str] = (), reason: str = "reduce machine-irrelevant output") -> RewriteRule:
    return RewriteRule(name, executable, re.compile(pattern), tuple(append), tuple(replace), tuple(blocked), reason)


# 64 deterministic, fail-closed rewrite rules. Rules only add compact/non-interactive
# flags when the user did not request an incompatible human-oriented format.
RULES: tuple[RewriteRule, ...] = (
    _rule("git-status", "git", r"^status(?:\s|$)", append=("--porcelain=v2", "--branch"), blocked=("--short", "--porcelain", "-s")),
    _rule("git-log", "git", r"^log(?:\s|$)", append=("--oneline", "--decorate=no", "-n", "40"), blocked=("--format", "--pretty", "--oneline", "-p", "--patch")),
    _rule("git-diff-stat", "git", r"^diff(?:\s|$)", append=("--stat", "--compact-summary"), blocked=("--stat", "--name-only", "--name-status", "--numstat", "-p", "--patch")),
    _rule("git-show-stat", "git", r"^show(?:\s|$)", append=("--stat", "--oneline"), blocked=("--stat", "--format", "--pretty", "-p", "--patch")),
    _rule("git-branch", "git", r"^branch(?:\s|$)", append=("--format=%(refname:short) %(objectname:short) %(upstream:trackshort)" ,), blocked=("--format", "-v", "-vv")),
    _rule("git-stash", "git", r"^stash\s+list(?:\s|$)", append=("--format=%gd %h %s",), blocked=("--format",)),
    _rule("git-worktree", "git", r"^worktree\s+list(?:\s|$)", append=("--porcelain",), blocked=("--porcelain",)),
    _rule("git-fetch", "git", r"^fetch(?:\s|$)", append=("--quiet",), blocked=("--verbose", "-v", "--quiet")),
    _rule("git-remote", "git", r"^remote\s+-v(?:\s|$)", replace=(("-v", "get-url --all"),), blocked=()),
    _rule("git-tag", "git", r"^tag(?:\s|$)", append=("--sort=-creatordate",), blocked=("--sort", "--format")),
    _rule("rg", "rg", r".*", append=("--no-heading", "--line-number", "--color=never"), blocked=("--heading", "--json", "--vimgrep", "--color")),
    _rule("grep", "grep", r".*", append=("-n", "--color=never"), blocked=("-n", "--line-number", "--color")),
    _rule("fd", "fd", r".*", append=("--color=never",), blocked=("--color",)),
    _rule("find", "find", r".*", append=(), blocked=()),
    _rule("ls", "ls", r".*", append=("-1", "--color=never"), blocked=("-l", "--long", "-1", "--color")),
    _rule("tree", "tree", r".*", append=("--noreport", "-C"), blocked=("--noreport", "-C", "--dirsfirst")),
    _rule("pytest", "pytest", r".*", append=("-q", "--tb=short",), blocked=("-q", "--quiet", "-v", "--verbose", "--tb")),
    _rule("py-test", "py.test", r".*", append=("-q", "--tb=short"), blocked=("-q", "--quiet", "-v", "--verbose", "--tb")),
    _rule("cargo-test", "cargo", r"^test(?:\s|$)", append=("--quiet",), blocked=("--quiet", "-q", "--verbose", "-v")),
    _rule("cargo-check", "cargo", r"^check(?:\s|$)", append=("--quiet",), blocked=("--quiet", "-q", "--verbose", "-v")),
    _rule("go-test", "go", r"^test(?:\s|$)", append=("-json",), blocked=("-json", "-v")),
    _rule("npm-test", "npm", r"^(?:test|run\s+test)(?:\s|$)", append=("--silent",), blocked=("--silent", "--loglevel")),
    _rule("npm-list", "npm", r"^(?:list|ls)(?:\s|$)", append=("--depth=0", "--json"), blocked=("--depth", "--json", "--long")),
    _rule("pnpm-test", "pnpm", r"^(?:test|run\s+test)(?:\s|$)", append=("--silent",), blocked=("--silent", "--reporter")),
    _rule("pnpm-list", "pnpm", r"^(?:list|ls)(?:\s|$)", append=("--depth=0", "--json"), blocked=("--depth", "--json")),
    _rule("yarn-test", "yarn", r"^(?:test|run\s+test)(?:\s|$)", append=("--silent",), blocked=("--silent", "--verbose")),
    _rule("yarn-list", "yarn", r"^list(?:\s|$)", append=("--depth=0", "--json"), blocked=("--depth", "--json")),
    _rule("jest", "jest", r".*", append=("--silent", "--reporters=default"), blocked=("--silent", "--verbose", "--json", "--reporters")),
    _rule("vitest", "vitest", r".*", append=("--reporter=basic",), blocked=("--reporter", "--silent")),
    _rule("playwright", "playwright", r"^test(?:\s|$)", append=("--reporter=line",), blocked=("--reporter",)),
    _rule("ruff", "ruff", r"^(?:check|format)(?:\s|$)", append=("--output-format=concise",), blocked=("--output-format", "--verbose")),
    _rule("mypy", "mypy", r".*", append=("--no-error-summary", "--show-column-numbers"), blocked=("--pretty", "--no-error-summary")),
    _rule("eslint", "eslint", r".*", append=("--format=compact",), blocked=("--format", "-f")),
    _rule("biome", "biome", r"^(?:check|lint)(?:\s|$)", append=("--reporter=summary",), blocked=("--reporter",)),
    _rule("coverage", "coverage", r"^report(?:\s|$)", append=("--format=total",), blocked=("--format", "-m")),
    _rule("docker-build", "docker", r"^build(?:\s|$)", append=("--progress=plain",), blocked=("--progress", "--quiet", "-q")),
    _rule("docker-ps", "docker", r"^ps(?:\s|$)", append=("--format={{.ID}} {{.Image}} {{.Status}} {{.Names}}",), blocked=("--format",)),
    _rule("docker-images", "docker", r"^images(?:\s|$)", append=("--format={{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}",), blocked=("--format",)),
    _rule("docker-logs", "docker", r"^logs(?:\s|$)", append=("--tail=200",), blocked=("--tail", "-n", "--follow", "-f")),
    _rule("docker-inspect", "docker", r"^inspect(?:\s|$)", append=("--format={{json .}}",), blocked=("--format", "-f")),
    _rule("docker-stats", "docker", r"^stats(?:\s|$)", append=("--no-stream", "--format={{.Name}} {{.CPUPerc}} {{.MemUsage}}"), blocked=("--no-stream", "--format")),
    _rule("kubectl-get", "kubectl", r"^get(?:\s|$)", append=("-o", "json"), blocked=("-o", "--output", "-w", "--watch")),
    _rule("kubectl-describe", "kubectl", r"^describe(?:\s|$)", append=(), blocked=()),
    _rule("kubectl-logs", "kubectl", r"^logs(?:\s|$)", append=("--tail=200",), blocked=("--tail", "-f", "--follow")),
    _rule("kubectl-events", "kubectl", r"^events(?:\s|$)", append=("-o", "json"), blocked=("-o", "--output", "--watch")),
    _rule("gh-pr", "gh", r"^pr\s+(?:view|checks)(?:\s|$)", append=("--json", "number,title,state,statusCheckRollup"), blocked=("--json", "--web")),
    _rule("gh-issue", "gh", r"^issue\s+(?:list|view)(?:\s|$)", append=("--json", "number,title,state,labels"), blocked=("--json", "--web")),
    _rule("gh-run", "gh", r"^run\s+(?:list|view)(?:\s|$)", append=("--json", "databaseId,name,status,conclusion,headSha"), blocked=("--json", "--log")),
    _rule("pip-list", "pip", r"^list(?:\s|$)", append=("--format=json",), blocked=("--format", "--verbose")),
    _rule("pip-show", "pip", r"^show(?:\s|$)", append=(), blocked=()),
    _rule("uv-pip-list", "uv", r"^pip\s+list(?:\s|$)", append=("--format=json",), blocked=("--format",)),
    _rule("poetry-show", "poetry", r"^show(?:\s|$)", append=("--tree",), blocked=("--tree", "--latest")),
    _rule("terraform-plan", "terraform", r"^plan(?:\s|$)", append=("-no-color", "-compact-warnings"), blocked=("-json", "-no-color")),
    _rule("terraform-show", "terraform", r"^show(?:\s|$)", append=("-json",), blocked=("-json", "-no-color")),
    _rule("ansible", "ansible-playbook", r".*", append=("-v",), blocked=("-v", "-vv", "-vvv", "-vvvv")),
    _rule("aws", "aws", r".*", append=("--output", "json", "--no-cli-pager"), blocked=("--output", "--cli-auto-prompt")),
    _rule("gcloud", "gcloud", r".*", append=("--format=json", "--quiet"), blocked=("--format", "--verbosity")),
    _rule("az", "az", r".*", append=("--output", "json"), blocked=("--output", "-o")),
    _rule("systemctl", "systemctl", r"^(?:status|list-units)(?:\s|$)", append=("--no-pager", "--plain"), blocked=("--no-pager", "--output")),
    _rule("journalctl", "journalctl", r".*", append=("--no-pager", "-n", "200", "-o", "short-iso"), blocked=("--no-pager", "-n", "--lines", "-o", "--output", "-f")),
    _rule("dotnet-test", "dotnet", r"^test(?:\s|$)", append=("--verbosity", "minimal"), blocked=("--verbosity", "-v")),
    _rule("maven-test", "mvn", r".*", append=("-q",), blocked=("-q", "-X", "--debug")),
    _rule("gradle-test", "gradle", r".*", append=("--console=plain", "--warning-mode=summary"), blocked=("--console", "--info", "--debug")),
    _rule("cmake", "cmake", r".*", append=("--log-level=WARNING",), blocked=("--log-level", "--trace")),
    _rule("ctest", "ctest", r".*", append=("--output-on-failure", "--no-tests=error"), blocked=("--verbose", "-V")),
)


class CommandRewriteEngine:
    def __init__(self, rules: Sequence[RewriteRule] = RULES):
        self.rules = tuple(rules)

    @staticmethod
    def _parse(command: str | Iterable[str]) -> tuple[tuple[str, ...], bool]:
        if isinstance(command, str):
            unsafe = bool(_UNSAFE.search(command))
            try:
                return tuple(shlex.split(command, posix=os.name != "nt")), unsafe
            except ValueError:
                return tuple(command.split()), True
        return tuple(str(item) for item in command), False

    @staticmethod
    def _arguments(argv: Sequence[str]) -> str:
        return " ".join(argv[1:]).casefold()

    @staticmethod
    def _has_blocked(argv: Sequence[str], blocked: Sequence[str]) -> bool:
        for token in argv[1:]:
            for flag in blocked:
                if token == flag or token.startswith(flag + "="):
                    return True
        return False

    def rewrite(self, command: str | Iterable[str]) -> RewriteResult:
        argv, unsafe = self._parse(command)
        if not argv:
            return RewriteResult(argv, argv, False, None, False, "empty command")
        if unsafe:
            return RewriteResult(argv, argv, False, None, False, "shell composition is not rewritten")
        executable = Path(argv[0]).name.casefold()
        args = self._arguments(argv)
        for rule in self.rules:
            if executable != rule.executable or not rule.argument_pattern.search(args):
                continue
            if self._has_blocked(argv, rule.blocked_flags):
                return RewriteResult(argv, argv, False, rule.name, True, "explicit user format preserved")
            rewritten = list(argv)
            for before, after in rule.replace:
                try:
                    index = rewritten.index(before)
                except ValueError:
                    continue
                rewritten[index:index + 1] = shlex.split(after)
            rewritten.extend(rule.append)
            result = tuple(rewritten)
            return RewriteResult(argv, result, result != argv, rule.name, True, rule.reason)
        return RewriteResult(argv, argv, False, None, True, "no matching rewrite rule")

    def manifest(self) -> dict[str, object]:
        return {
            "rules": [rule.name for rule in self.rules],
            "count": len(self.rules),
            "fail_closed": True,
            "shell_composition_rewritten": False,
        }
