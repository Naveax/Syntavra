# Syntavra repository instructions

Use `skills/syntavra/SKILL.md` for complex coding-agent work involving repository exploration, debugging, impact analysis, large outputs, long sessions, tool overload, or token/cost analysis.

- Preserve correctness, exact evidence, and a runnable verifier before optimizing token count.
- Keep the canonical skill host-independent; platform differences belong in `data/platforms.json` and `scripts/platforms.py`.
- Do not duplicate platform-specific copies inside this repository. Generate or install them through `tools/install.py`.
- Do not claim universal or Token Savior superiority without paired provider benchmarks.
- Run `python tools/validate.py` before publishing changes.
