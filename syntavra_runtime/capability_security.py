from .platform_common import *


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    category: str
    reason: str
    requirements: tuple[str, ...]
    arguments_hash: str
    resource: str


class CapabilitySecurity:
    """Argument/resource-level signed, expiring, single-use capabilities."""

    READ_PREFIXES = ("read", "search", "list", "find", "inspect", "grep")
    WRITE_PREFIXES = ("write", "edit", "patch", "delete", "move", "create", "update")
    EXEC_PREFIXES = ("run", "exec", "shell", "terminal", "bash", "powershell", "cmd")
    NETWORK_PREFIXES = ("web", "http", "fetch", "download", "upload", "request", "browser")

    def __init__(self, state_root: Path):
        self.state_root = state_root
        state_root.mkdir(parents=True, exist_ok=True)
        self.key_path = state_root / "capability.key"
        legacy_key = state_root / "capability-v2.key"
        if not self.key_path.exists() and legacy_key.exists():
            os.replace(legacy_key, self.key_path)
        if not self.key_path.exists():
            temporary = self.key_path.with_suffix(".tmp")
            temporary.write_bytes(secrets.token_bytes(32))
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.key_path)
        self.db_path = state_root / "capability.sqlite3"
        legacy_db = state_root / "capability-v2.sqlite3"
        if not self.db_path.exists() and legacy_db.exists():
            os.replace(legacy_db, self.db_path)
            for suffix in ("-wal", "-shm"):
                legacy_sidecar = Path(str(legacy_db) + suffix)
                if legacy_sidecar.exists():
                    os.replace(legacy_sidecar, Path(str(self.db_path) + suffix))
        with _connect(self.db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS consumed (nonce TEXT PRIMARY KEY, consumed_at TEXT NOT NULL)")

    @classmethod
    def category(cls, tool: str) -> str:
        leaf = tool.casefold().replace("-", ".").replace("_", ".").rsplit(".", 1)[-1]
        for prefix in cls.READ_PREFIXES:
            if leaf.startswith(prefix):
                return "read"
        for prefix in cls.WRITE_PREFIXES:
            if leaf.startswith(prefix):
                return "write"
        for prefix in cls.EXEC_PREFIXES:
            if leaf.startswith(prefix):
                return "execute"
        for prefix in cls.NETWORK_PREFIXES:
            if leaf.startswith(prefix):
                return "network"
        return "unknown"

    def decide(
        self,
        tool: str,
        arguments: Mapping[str, Any] | Sequence[Any] | str,
        *,
        resource: str = "workspace:/",
        sandboxed: bool = False,
        user_authorized: bool = False,
        network_allowlist: Sequence[str] = (),
    ) -> CapabilityDecision:
        category = self.category(tool)
        rendered = canonical_json(arguments).decode("utf-8") if not isinstance(arguments, str) else arguments
        if isinstance(arguments, Mapping) and isinstance(arguments.get("argv"), Sequence) and not isinstance(arguments.get("argv"), (str, bytes, bytearray)):
            policy_text = " ".join(str(value) for value in arguments.get("argv", ()))
        else:
            policy_text = rendered
        args_hash = sha256_bytes(rendered.encode("utf-8"))
        requirements = ["signed-capability", "exact-evidence"]
        allowed = True
        reason = "policy-allowed"
        if category == "unknown":
            allowed, reason = False, "unknown-tool-fail-closed"
        if category in {"write", "execute", "network"}:
            requirements.append("explicit-user-authorization")
            if not user_authorized:
                allowed, reason = False, "authorization-required"
        if category == "execute":
            requirements.append("sandbox")
            if not sandboxed:
                allowed, reason = False, "sandbox-required"
            elif _DESTRUCTIVE_RE.search(policy_text):
                allowed, reason = False, "destructive-command-denied"
        if not resource.startswith("workspace:") and category in {"write", "execute"}:
            allowed, reason = False, "resource-outside-workspace"
        if category == "network":
            host = str(arguments.get("host", "") if isinstance(arguments, Mapping) else "")
            if network_allowlist and host not in set(network_allowlist):
                allowed, reason = False, "network-host-not-allowlisted"
        return CapabilityDecision(allowed, category, reason, tuple(requirements), args_hash, resource)

    def issue(
        self,
        *,
        session_id: str,
        tool: str,
        arguments: Mapping[str, Any] | Sequence[Any] | str,
        resource: str,
        permissions: Sequence[str],
        ttl_seconds: int = 300,
        single_use: bool = True,
    ) -> str:
        now = int(time.time())
        body = {
            "version": VERSION,
            "channel": CHANNEL,
            "session_id": session_id,
            "tool": tool,
            "arguments_hash": sha256_bytes(canonical_json(arguments) if not isinstance(arguments, str) else arguments.encode("utf-8")),
            "resource": resource,
            "permissions": sorted(set(permissions)),
            "issued_at": now,
            "expires_at": now + max(1, ttl_seconds),
            "single_use": bool(single_use),
            "nonce": secrets.token_urlsafe(18),
        }
        payload = base64.urlsafe_b64encode(canonical_json(body)).rstrip(b"=")
        signature = hmac.new(self.key_path.read_bytes(), payload, hashlib.sha256).digest()
        return payload.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    def verify(
        self,
        token: str,
        *,
        tool: str,
        arguments: Mapping[str, Any] | Sequence[Any] | str,
        resource: str,
        consume: bool = True,
    ) -> dict[str, Any]:
        try:
            payload_text, signature_text = token.split(".", 1)
            payload = payload_text.encode("ascii")
            expected = hmac.new(self.key_path.read_bytes(), payload, hashlib.sha256).digest()
            supplied = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            if not hmac.compare_digest(expected, supplied):
                return {"ok": False, "reason": "invalid-signature"}
            body = json.loads(base64.urlsafe_b64decode(payload_text + "=" * (-len(payload_text) % 4)))
        except (ValueError, json.JSONDecodeError, UnicodeError):
            return {"ok": False, "reason": "malformed-token"}
        if int(body.get("expires_at", 0)) < int(time.time()):
            return {"ok": False, "reason": "expired", "capability": body}
        args_hash = sha256_bytes(canonical_json(arguments) if not isinstance(arguments, str) else arguments.encode("utf-8"))
        if body.get("tool") != tool or body.get("arguments_hash") != args_hash or body.get("resource") != resource:
            return {"ok": False, "reason": "binding-mismatch", "capability": body}
        with _connect(self.db_path) as db:
            used = db.execute("SELECT 1 FROM consumed WHERE nonce = ?", (body["nonce"],)).fetchone()
            if used:
                return {"ok": False, "reason": "already-consumed", "capability": body}
            if consume and body.get("single_use", True):
                db.execute("INSERT INTO consumed VALUES(?,?)", (body["nonce"], _now()))
        return {"ok": True, "reason": "verified", "capability": body}
