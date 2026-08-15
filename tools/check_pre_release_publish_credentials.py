#!/usr/bin/env python3
"""Zero-write credential and OIDC binding preflight for Syntavra publication.

The checker validates authentication material without uploading or publishing any
artifact. Minted short-lived credentials are masked immediately and never written
to evidence JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PYPI_BASE = "https://pypi.org"
NPM_REGISTRY_BASE = "https://registry.npmjs.org"
NPM_SCOPE = "syntavra"
NPM_ORG_PUBLISH_ROLES = frozenset({"owner", "admin", "member", "developer"})
MARKETPLACE_BASE = "https://marketplace.visualstudio.com"
MARKETPLACE_AUDIENCE = "marketplace.visualstudio.com"
MARKETPLACE_PUBLISHER = "naveax"


class CredentialPreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class JsonResponse:
    status: int
    value: dict[str, Any]


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 10,
) -> JsonResponse:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="strict")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise CredentialPreflightError(f"{url} returned non-object JSON")
            return JsonResponse(status=response.status, value=payload)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"message": raw[:500]}
        message = payload.get("message") or payload.get("error_description") or payload.get("error") or raw[:500]
        raise CredentialPreflightError(f"{url} rejected credential exchange with HTTP {exc.code}: {message}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CredentialPreflightError(f"{url} credential exchange failed: {type(exc).__name__}: {exc}") from exc


def _github_oidc_token(audience: str) -> str:
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not request_url or not request_token:
        raise CredentialPreflightError(
            "GitHub Actions OIDC request context is unavailable; the job requires id-token: write"
        )

    parsed = urllib.parse.urlsplit(request_url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "audience"]
    query.append(("audience", audience))
    url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )
    response = _request_json(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {request_token}",
        },
    )
    token = response.value.get("value")
    if not isinstance(token, str) or not token:
        raise CredentialPreflightError("GitHub OIDC endpoint returned no identity token")
    return token


def verify_npm_scope_authorization(npm_identity: str) -> dict[str, Any]:
    identity = npm_identity.strip()
    if not identity:
        raise CredentialPreflightError("npm identity is required for scope authorization")

    if identity.casefold() == NPM_SCOPE.casefold():
        return {
            "verified": True,
            "scope": f"@{NPM_SCOPE}",
            "scope_kind": "user",
            "identity": identity,
            "organization_role": None,
            "verification_endpoint": None,
            "credential_persisted": False,
            "publication_performed": False,
        }

    token = os.environ.get("NODE_AUTH_TOKEN", "").strip()
    if not token:
        raise CredentialPreflightError("NODE_AUTH_TOKEN is unavailable for npm organization scope verification")

    endpoint = f"{NPM_REGISTRY_BASE}/-/org/{NPM_SCOPE}/user"
    response = _request_json(
        endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Syntavra credential preflight/0.0.1",
        },
    )
    members = {str(name).casefold(): role for name, role in response.value.items()}
    role = members.get(identity.casefold())
    if not isinstance(role, str) or role.casefold() not in NPM_ORG_PUBLISH_ROLES:
        raise CredentialPreflightError(
            f"npm identity {identity!r} is not a publish-capable member of @{NPM_SCOPE}"
        )

    return {
        "verified": True,
        "scope": f"@{NPM_SCOPE}",
        "scope_kind": "organization",
        "identity": identity,
        "organization_role": role.casefold(),
        "verification_endpoint": endpoint,
        "credential_persisted": False,
        "publication_performed": False,
    }


def verify_pypi_trusted_publisher() -> dict[str, Any]:
    audience_response = _request_json(f"{PYPI_BASE}/_/oidc/audience")
    audience = audience_response.value.get("audience")
    if not isinstance(audience, str) or not audience:
        raise CredentialPreflightError("PyPI OIDC audience response is missing audience")

    oidc_token = _github_oidc_token(audience)
    exchange = _request_json(
        f"{PYPI_BASE}/_/oidc/mint-token",
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        body={"token": oidc_token},
    )
    token = exchange.value.get("token")
    if not isinstance(token, str) or not token:
        raise CredentialPreflightError("PyPI OIDC exchange returned no short-lived credential")
    print(f"::add-mask::{token}")
    return {
        "verified": True,
        "audience": audience,
        "exchange_endpoint": f"{PYPI_BASE}/_/oidc/mint-token",
        "credential_persisted": False,
        "publication_performed": False,
    }


def verify_marketplace_trusted_publisher() -> dict[str, Any]:
    oidc_token = _github_oidc_token(MARKETPLACE_AUDIENCE)
    exchange = _request_json(
        f"{MARKETPLACE_BASE}/_apis/gallery/token",
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {oidc_token}",
            "Content-Type": "application/json",
            "User-Agent": "Syntavra credential preflight/0.0.1",
        },
        body={"publisherName": MARKETPLACE_PUBLISHER},
    )
    credential = exchange.value.get("credential")
    if not isinstance(credential, str) or not credential:
        raise CredentialPreflightError("Marketplace OIDC exchange returned no short-lived credential")
    print(f"::add-mask::{credential}")
    return {
        "verified": True,
        "audience": MARKETPLACE_AUDIENCE,
        "publisher": MARKETPLACE_PUBLISHER,
        "exchange_endpoint": f"{MARKETPLACE_BASE}/_apis/gallery/token",
        "credential_persisted": False,
        "publication_performed": False,
    }


def build_report(
    *,
    exact_head: str,
    npm_authenticated: bool,
    npm_identity: str,
    npm_scope: dict[str, Any],
    crates_token_present: bool,
    pypi: dict[str, Any],
    marketplace: dict[str, Any],
) -> dict[str, Any]:
    if len(exact_head) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in exact_head):
        raise ValueError("exact_head must be exactly 40 hexadecimal characters")
    npm_identity = npm_identity.strip()
    if npm_authenticated and not npm_identity:
        raise ValueError("npm identity is required when npm authentication succeeded")

    ready = all(
        (
            npm_authenticated,
            npm_scope.get("verified") is True,
            crates_token_present,
            pypi.get("verified") is True,
            marketplace.get("verified") is True,
        )
    )
    return {
        "schema_version": 1,
        "product": "Syntavra",
        "version": "0.0.1",
        "channel": "pre-release",
        "exact_head": exact_head.lower(),
        "mode": "zero-write-credential-preflight",
        "publication_performed": False,
        "credential_values_exposed": False,
        "pypi_trusted_publisher": pypi,
        "vscode_marketplace_trusted_publisher": marketplace,
        "npm": {
            "authenticated": npm_authenticated,
            "identity": npm_identity if npm_authenticated else None,
            "registry": NPM_REGISTRY_BASE,
            "scope": f"@{NPM_SCOPE}",
            "scope_publish_rights_verified": npm_scope.get("verified") is True,
            "scope_authorization": npm_scope,
            "note": "npm token authentication and @syntavra namespace authorization are verified with read-only registry operations before publication.",
        },
        "crates_io": {
            "token_present": crates_token_present,
            "remote_token_validation_performed": False,
            "note": "The initial publication uses an API token because the target crates do not yet exist; no documented scope-neutral authenticated read endpoint is used to over-claim remote publish authorization.",
        },
        "publish_auth_ready": ready,
        "claim": "ZERO_WRITE_PUBLISH_AUTH_READY" if ready else "ZERO_WRITE_PUBLISH_AUTH_INCOMPLETE",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exact-head", required=True)
    parser.add_argument("--npm-authenticated", choices=("true", "false"), required=True)
    parser.add_argument("--npm-identity", default="")
    parser.add_argument("--crates-token-present", choices=("true", "false"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-live-oidc", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    npm_scope = verify_npm_scope_authorization(args.npm_identity)
    if args.skip_live_oidc:
        pypi = {"verified": False, "skipped": True, "publication_performed": False}
        marketplace = {"verified": False, "skipped": True, "publication_performed": False}
    else:
        pypi = verify_pypi_trusted_publisher()
        marketplace = verify_marketplace_trusted_publisher()

    report = build_report(
        exact_head=args.exact_head,
        npm_authenticated=args.npm_authenticated == "true",
        npm_identity=args.npm_identity,
        npm_scope=npm_scope,
        crates_token_present=args.crates_token_present == "true",
        pypi=pypi,
        marketplace=marketplace,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["publish_auth_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
