#!/usr/bin/env python3
import os

os.environ["SYNTAVRA_PORTABLE_BOOTSTRAP"] = "1"

from syntavra_runtime.prerelease_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
