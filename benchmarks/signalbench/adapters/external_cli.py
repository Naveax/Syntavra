#!/usr/bin/env python3
from __future__ import annotations

import sys
from syntavra_runtime.signalbench_external_adapter import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SignalBench adapter failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
