"""Compatibility namespace for installations created before the Syntavra rename."""
from pathlib import Path as _Path
_CANONICAL = _Path(__file__).resolve().parent.parent / "syntavra_runtime"
__path__ = [str(_CANONICAL)]
from syntavra_runtime import *  # noqa: F401,F403
from syntavra_runtime import __all__, __release_channel__, __version__
del _Path, _CANONICAL
