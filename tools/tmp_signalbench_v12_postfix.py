from __future__ import annotations

from pathlib import Path


def main() -> None:
    path = Path("tests/runtime/test_signalbench_python_product_v1.py")
    text = path.read_text(encoding="utf-8")
    old = '''        self.assertFalse(hasattr(args, "output_root"))
        self.assertFalse(hasattr(args, "seed"))
'''
    new = '''        self.assertEqual(args.output_root, "signalbench-results")
        self.assertEqual(args.seed, 1337)
'''
    if text.count(old) != 1:
        raise RuntimeError(f"SignalBench compare parser assertion anchor drift: {text.count(old)}")
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
