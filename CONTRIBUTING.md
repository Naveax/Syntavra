# Contributing to Syntavra

Syntavra is a **0.0.1 pre-release** project. The version must not change unless the repository owner explicitly authorizes it.

## Development workflow

1. Start from the current `main`.
2. Work on an `agent/<scope>` or similarly focused branch.
3. Keep changes reviewable and do not push implementation commits directly to `main`.
4. Run the relevant Python, TypeScript and installer checks.
5. Refresh `MANIFEST.sha256` in the same branch before opening or updating the pull request.
6. Open a pull request and merge only after the required checks pass.

## Required validation

```bash
python -m compileall -q syntavra_runtime skills/syntavra tools tests benchmarks
python -m unittest discover -s tests -q
python tools/check_repository_hygiene.py
python tools/refresh_manifest.py
python tools/refresh_manifest.py --check
python tools/validate.py
python tools/validate_runtime.py

npm ci
npm test

cd sdk/typescript
npm ci
npm run check
npm test
```

## Product and benchmark claims

Internal fixtures may validate code paths, but they must not be presented as public superiority, adoption, live-integration, SWE-bench, OOLONG or production-maturity evidence. Claim-bearing results require the repository's external receipt schemas, pinned harness metadata and human review.

## Security

Do not place credentials, tokens, private keys, private repository content or unredacted provider payloads in commits, issues, tests, benchmark artifacts or telemetry. Report vulnerabilities through a private GitHub security advisory.
