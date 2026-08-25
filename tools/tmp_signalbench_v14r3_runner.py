from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PREVIOUS_HELPER = "75b1f456440b8fd8bee309383b467d5f0f78879f"
PREVIOUS_RUNNER = Path("/tmp/signalbench-v14r3-previous.py")
V14_APPLY = Path("/tmp/signalbench-v14-apply.py")


def _load_previous():
    source = subprocess.check_output(
        ["git", "show", f"{PREVIOUS_HELPER}:tools/tmp_signalbench_v14r3_runner.py"],
        text=True,
    )
    compile(source, str(PREVIOUS_RUNNER), "exec")
    PREVIOUS_RUNNER.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("signalbench_v14r3_previous", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load previous SignalBench v14r3 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_all_fixture_drift() -> None:
    path = V14_APPLY
    source = path.read_text(encoding="utf-8")

    helper_marker = "def _apply_fixture_pair(text: str, old: str, new: str, label: str) -> str:\n"
    patch_marker = "def patch_tests_and_certifier() -> None:\n"
    helper = r'''def _apply_fixture_pair(text: str, old: str, new: str, label: str) -> str:
    old_count = text.count(old)
    if old_count == 1:
        return text.replace(old, new, 1)
    if old_count == 0 and text.count(new) == 1:
        return text

    if label == "valid task fixture":
        start = text.index("    def test_exact_tree_and_clean_worktree_are_enforced(")
        end = text.index("\n    @staticmethod\n    def _result(", start)
        block = text[start:end]
        if "repo, commit, tree = self._repo(Path(temp))" not in block:
            old_unpack = "repo, tree = self._repo(Path(temp))"
            if block.count(old_unpack) != 1:
                raise RuntimeError("valid task repo unpack drift")
            block = block.replace(old_unpack, "repo, commit, tree = self._repo(Path(temp))", 1)
        if "repository_commit=commit" not in block:
            lines = block.splitlines(keepends=True)
            matches = [index for index, line in enumerate(lines) if "task = TaskSpec(" in line]
            if len(matches) != 1:
                raise RuntimeError(f"valid task TaskSpec drift: {len(matches)}")
            index = matches[0]
            close = lines[index].rfind(")")
            if close < 0:
                raise RuntimeError("valid task TaskSpec close missing")
            lines[index] = lines[index][:close] + ", repository_commit=commit" + lines[index][close:]
            block = "".join(lines)
        return text[:start] + block + text[end:]

    if label == "result identity":
        start = text.index("    @staticmethod\n    def _result(")
        end = text.index("\n    def test_public_compare_uses_hardened_receipt_authority", start)
        block = text[start:end]
        identity = '            repository_commit="6" * 40, task_hash="5" * 64, timeout_seconds=1200.0,\n'
        if identity in block:
            return text
        values_start = block.index("        values = dict(\n")
        values_end = block.index("        )\n        provisional = RunResult(**values)", values_start)
        block = block[:values_end] + identity + block[values_end:]
        return text[:start] + block + text[end:]

    if label in {"request identity", "cert request"}:
        old_expr = 'request_id_hash=("e" if arm == "base" else "f") * 64'
        new_expr = 'request_id_hash=(f"{repetition:064x}" if arm == "base" else f"{repetition + 1000:064x}")'
        if new_expr in text:
            return text
        if text.count(old_expr) != 1:
            raise RuntimeError(f"{label} request expression drift: {text.count(old_expr)}")
        return text.replace(old_expr, new_expr, 1)

    if label == "cert result identity":
        start = text.index("def fixture_result(")
        end = text.index("\n\ndef certify(", start)
        block = text[start:end]
        identity = '        repository_commit="6" * 40, task_hash="5" * 64, timeout_seconds=1200.0,\n'
        if identity in block:
            return text
        values_start = block.index("    values = dict(\n")
        values_end = block.index("    )\n    receipt = UsageReceipt.seal", values_start)
        block = block[:values_end] + identity + block[values_end:]
        return text[:start] + block + text[end:]

    if label == "cert task":
        lines = text.splitlines(keepends=True)
        matches = [index for index, line in enumerate(lines) if 'task = TaskSpec("certify-task"' in line]
        if len(matches) != 1:
            raise RuntimeError(f"cert task TaskSpec drift: {len(matches)}")
        task_index = matches[0]
        if "repository_commit=commit" not in lines[task_index]:
            close = lines[task_index].rfind(")")
            if close < 0:
                raise RuntimeError("cert task TaskSpec close missing")
            lines[task_index] = lines[task_index][:close] + ", repository_commit=commit" + lines[task_index][close:]
        commit_line = '        commit = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()\n'
        if commit_line not in lines:
            tree_matches = [
                index for index, line in enumerate(lines)
                if 'tree = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD^{tree}"]' in line
            ]
            if len(tree_matches) != 1:
                raise RuntimeError(f"cert task tree anchor drift: {len(tree_matches)}")
            lines.insert(tree_matches[0], commit_line)
        return "".join(lines)

    if label == "cert negative":
        marker = 'wrong_commit = TaskSpec(**{**asdict(task), "repository_commit": "0" * len(commit)})'
        if marker in text:
            return text
        lines = text.splitlines(keepends=True)
        matches = [
            index for index, line in enumerate(lines)
            if 'require("task:certify-task:repository-tree-mismatch"' in line
        ]
        if len(matches) != 1:
            raise RuntimeError(f"cert negative tree check drift: {len(matches)}")
        index = matches[0] + 1
        lines[index:index] = [
            '        wrong_commit = TaskSpec(**{**asdict(task), "repository_commit": "0" * len(commit)})\n',
            '        require("task:certify-task:repository-commit-mismatch" in SignalBenchRunner(root / "validation").validate_product([wrong_commit], arms)["reasons"], "commit mismatch did not fail closed")\n',
        ]
        return "".join(lines)

    if label == "cert contract keys":
        required = (
            "repository_commit_must_match",
            "git_workspace_preserved",
            "host_environment_isolated",
            "explicit_environment_inheritance_only",
        )
        if all(key in text for key in required):
            return text
        anchor = '        "repository_git_tree_must_match", "repository_worktree_must_be_clean", "exact_product_version_required",\n'
        if text.count(anchor) != 1:
            raise RuntimeError(f"cert contract key anchor drift: {text.count(anchor)}")
        replacement = (
            '        "repository_git_tree_must_match", "repository_commit_must_match", "repository_worktree_must_be_clean",\n'
            '        "git_workspace_preserved", "host_environment_isolated", "explicit_environment_inheritance_only", "exact_product_version_required",\n'
        )
        return text.replace(anchor, replacement, 1)

    if label == "cert output":
        if '"exact_repository_commit": True' in text:
            return text
        anchor = '        "frozen_repository_identity": True,\n'
        if text.count(anchor) != 1:
            raise RuntimeError(f"cert output anchor drift: {text.count(anchor)}")
        replacement = (
            anchor
            + '        "exact_repository_commit": True,\n'
            + '        "git_workspace_preserved": True,\n'
            + '        "host_environment_isolated": True,\n'
        )
        return text.replace(anchor, replacement, 1)

    raise RuntimeError(f"{label} drift: {old_count}")
'''

    if helper_marker not in source:
        location = source.index(patch_marker)
        source = source[:location] + helper + "\n\n" + source[location:]

    old_test_loop = '''    for old, new, label in pairs:
        count = text.count(old)
        if count == 1:
            text = text.replace(old, new, 1)
            continue
        if count == 0 and text.count(new) == 1:
            continue
        if label == "valid task fixture":
            method_start = text.index("    def test_exact_tree_and_clean_worktree_are_enforced(")
            method_end = text.index("\\n    @staticmethod\\n    def _result(", method_start)
            block = text[method_start:method_end]
            if "repo, commit, tree = self._repo(Path(temp))" not in block:
                if block.count("repo, tree = self._repo(Path(temp))") != 1:
                    raise RuntimeError("valid task repo unpack drift")
                block = block.replace("repo, tree = self._repo(Path(temp))", "repo, commit, tree = self._repo(Path(temp))", 1)
            if "repository_commit=commit" not in block:
                lines = block.splitlines(keepends=True)
                matches = [index for index, line in enumerate(lines) if "task = TaskSpec(" in line]
                if len(matches) != 1:
                    raise RuntimeError(f"valid task TaskSpec drift: {len(matches)}")
                index = matches[0]
                close = lines[index].rfind(")")
                if close < 0:
                    raise RuntimeError("valid task TaskSpec close missing")
                lines[index] = lines[index][:close] + ", repository_commit=commit" + lines[index][close:]
                block = "".join(lines)
            text = text[:method_start] + block + text[method_end:]
            continue
        raise RuntimeError(f"{label} drift: {count}")
'''
    new_test_loop = '''    for old, new, label in pairs:
        text = _apply_fixture_pair(text, old, new, label)
'''
    if source.count(old_test_loop) == 1:
        source = source.replace(old_test_loop, new_test_loop, 1)
    elif source.count(new_test_loop) != 1:
        raise RuntimeError("V14 test fixture loop source drift")

    old_cert_loop = '''    for old, new, label in cert_pairs:
        count = text.count(old)
        if count == 1:
            text = text.replace(old, new, 1)
            continue
        if count == 0 and text.count(new) == 1:
            continue
        raise RuntimeError(f"{label} drift: {count}")
'''
    new_cert_loop = '''    for old, new, label in cert_pairs:
        text = _apply_fixture_pair(text, old, new, label)
'''
    if source.count(old_cert_loop) == 1:
        source = source.replace(old_cert_loop, new_cert_loop, 1)
    elif source.count(new_cert_loop) != 1:
        raise RuntimeError("V14 certifier fixture loop source drift")

    compile(source, str(path), "exec")
    path.write_text(source, encoding="utf-8")


def main() -> None:
    previous = _load_previous()
    previous.main()
    _patch_all_fixture_drift()


if __name__ == "__main__":
    main()
