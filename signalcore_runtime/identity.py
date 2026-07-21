from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .util import canonical_json


class IdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str = "local"
    project_id: str = ""
    host: str = ""
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()

    def allows(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


@dataclass(frozen=True)
class TokenClaims:
    schema_version: int
    issuer: str
    subject: str
    tenant: str
    project_id: str
    host: str
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    issued_at: int
    expires_at: int
    token_id: str


class CapabilityTokenIssuer:
    """Short-lived HMAC capability tokens for local/remote control-plane calls.

    Tokens are deliberately compact, project-bound and scope-bound. They are not a
    replacement for enterprise OIDC; they provide a safe local default and an auth
    boundary that can be swapped for an external identity provider later.
    """

    def __init__(self, key: bytes, *, issuer: str = "signalcore-local", clock_skew_seconds: int = 30):
        if len(key) < 32:
            raise IdentityError("capability signing key must be at least 32 bytes")
        self._key = bytes(key)
        self.issuer = issuer
        self.clock_skew_seconds = max(0, int(clock_skew_seconds))
        self._revoked: set[str] = set()

    @classmethod
    def from_environment(cls, name: str = "SIGNALCORE_CONTROL_SIGNING_KEY_B64") -> "CapabilityTokenIssuer":
        value = os.environ.get(name, "")
        if not value:
            raise IdentityError(f"missing control-plane signing key: {name}")
        try:
            key = base64.b64decode(value, validate=True)
        except Exception as exc:
            raise IdentityError("invalid base64 control-plane signing key") from exc
        return cls(key)

    @staticmethod
    def generate_key() -> str:
        return base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    def issue(self, principal: Principal, *, ttl_seconds: int = 300, now: int | None = None) -> str:
        issued = int(time.time() if now is None else now)
        ttl = int(ttl_seconds)
        if ttl < 1 or ttl > 24 * 60 * 60:
            raise IdentityError("token ttl must be between 1 second and 24 hours")
        claims = TokenClaims(
            schema_version=1,
            issuer=self.issuer,
            subject=principal.subject,
            tenant=principal.tenant,
            project_id=principal.project_id,
            host=principal.host,
            roles=tuple(sorted(set(principal.roles))),
            scopes=tuple(sorted(set(principal.scopes))),
            issued_at=issued,
            expires_at=issued + ttl,
            token_id=secrets.token_hex(16),
        )
        payload = self._encode(canonical_json(asdict(claims)))
        signature = self._encode(hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest())
        return f"scv1.{payload}.{signature}"

    def verify(
        self,
        token: str,
        *,
        required_scopes: Iterable[str] = (),
        project_id: str = "",
        host: str = "",
        now: int | None = None,
    ) -> Principal:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "scv1":
            raise IdentityError("invalid capability token format")
        expected = self._encode(hmac.new(self._key, parts[1].encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(parts[2], expected):
            raise IdentityError("capability token signature is invalid")
        try:
            value = json.loads(self._decode(parts[1]))
        except Exception as exc:
            raise IdentityError("capability token payload is invalid") from exc
        claims = TokenClaims(
            schema_version=int(value["schema_version"]),
            issuer=str(value["issuer"]),
            subject=str(value["subject"]),
            tenant=str(value.get("tenant", "local")),
            project_id=str(value.get("project_id", "")),
            host=str(value.get("host", "")),
            roles=tuple(str(item) for item in value.get("roles", ())),
            scopes=tuple(str(item) for item in value.get("scopes", ())),
            issued_at=int(value["issued_at"]),
            expires_at=int(value["expires_at"]),
            token_id=str(value["token_id"]),
        )
        current = int(time.time() if now is None else now)
        if claims.schema_version != 1 or claims.issuer != self.issuer:
            raise IdentityError("unsupported capability token")
        if claims.token_id in self._revoked:
            raise IdentityError("capability token is revoked")
        if current + self.clock_skew_seconds < claims.issued_at:
            raise IdentityError("capability token is not active yet")
        if current - self.clock_skew_seconds >= claims.expires_at:
            raise IdentityError("capability token is expired")
        if project_id and claims.project_id not in {"", project_id}:
            raise IdentityError("capability token project scope mismatch")
        if host and claims.host not in {"", host}:
            raise IdentityError("capability token host scope mismatch")
        principal = Principal(
            subject=claims.subject,
            tenant=claims.tenant,
            project_id=claims.project_id,
            host=claims.host,
            roles=claims.roles,
            scopes=claims.scopes,
        )
        denied = [scope for scope in required_scopes if not principal.allows(scope)]
        if denied:
            raise IdentityError("missing capability scopes: " + ",".join(sorted(denied)))
        return principal

    def revoke(self, token: str) -> str:
        parts = token.split(".")
        if len(parts) != 3:
            raise IdentityError("invalid capability token")
        try:
            token_id = str(json.loads(self._decode(parts[1]))["token_id"])
        except Exception as exc:
            raise IdentityError("invalid capability token") from exc
        self._revoked.add(token_id)
        return token_id

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)


class Authorizer:
    def __init__(self, role_scopes: Mapping[str, Iterable[str]] | None = None):
        self.role_scopes = {
            str(role): frozenset(str(scope) for scope in scopes)
            for role, scopes in (role_scopes or {}).items()
        }

    def effective_scopes(self, principal: Principal) -> frozenset[str]:
        scopes = set(principal.scopes)
        for role in principal.roles:
            scopes.update(self.role_scopes.get(role, ()))
        return frozenset(scopes)

    def require(self, principal: Principal, *scopes: str) -> None:
        effective = self.effective_scopes(principal)
        missing = [scope for scope in scopes if "*" not in effective and scope not in effective]
        if missing:
            raise IdentityError("authorization denied: " + ",".join(sorted(missing)))
