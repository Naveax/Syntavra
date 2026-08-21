from __future__ import annotations

import importlib.util


def _load_v9():
    spec = importlib.util.spec_from_file_location("signalbench_v9", "/tmp/signalbench-v9-apply.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load SignalBench V9 module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_pair_anchor(script: str) -> str:
    start_marker = "old_pair = '''    @classmethod\n    def pair_identity"
    end_marker = "text = text.replace(old_pair, new_pair, 1)\n"
    start = script.find(start_marker)
    if start < 0:
        raise RuntimeError("pair patch source start missing")
    end_at = script.find(end_marker, start)
    if end_at < 0:
        raise RuntimeError("pair patch source end missing")
    end = end_at + len(end_marker)
    semantic = '''pair_start = text.index("    @classmethod\\n    def pair_identity(")
pair_end = text.index("\\n\\n\\nclass SignalBenchRunner:", pair_start)
new_pair = \'''    @classmethod
    def pair_identity(cls, task: TaskSpec, arm: ArmSpec, *, cache_mode: str, hardware_hash: str = "") -> dict[str, Any]:
        return {
            "task_hash": cls.task_hash(task),
            "repository_tree": task.repository_tree,
            "arm_version": arm.version,
            "model": arm.model,
            "reasoning": arm.reasoning,
            "context_window": arm.context_window,
            "hardware_hash": hardware_hash,
            "prompt_hash": sha256_bytes(task.prompt.encode()),
            "verifier_hash": cls.verifier_hash(task),
            "permissions_hash": cls.permissions_hash(task),
            "timeout_seconds": task.timeout_seconds,
            "cache_mode": cache_mode,
        }
\'''
text = text[:pair_start] + new_pair + text[pair_end:]
'''
    return script[:start] + semantic + script[end:]


def _extract(text: str) -> str:
    lines = text.splitlines()
    start_marker = "          python - <<'PY'"
    try:
        start = lines.index(start_marker) + 1
    except ValueError as exc:
        raise RuntimeError("outer helper apply block start marker missing") from exc
    try:
        tail = next(
            index for index in range(start, len(lines))
            if "registry_path.write_text(json.dumps(registry" in lines[index]
        )
    except StopIteration as exc:
        raise RuntimeError("outer helper apply tail marker missing") from exc
    try:
        end = next(index for index in range(tail + 1, len(lines)) if lines[index] == "          PY")
    except StopIteration as exc:
        raise RuntimeError("outer helper apply closing marker missing") from exc
    prefix = " " * 10
    script = "\n".join(
        line[len(prefix):] if line.startswith(prefix) else line
        for line in lines[start:end]
    ) + "\n"
    return _patch_pair_anchor(script)


def main() -> None:
    module = _load_v9()
    module.extract_outer_yaml_python_block = _extract
    module.main()


if __name__ == "__main__":
    main()
