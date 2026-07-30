from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .release_identity import CHANNEL, VERSION
from .util import atomic_write_json

ENGINE_SELECTION_SCHEMA_VERSION = 1
ENGINE_CONTRACT_VERSION = 1
ENGINE_MODES = ("auto", "python", "rust")
DEFAULT_ENGINE = "python"
AUTO_ENGINE = "python"
RUST_BINARY_NAME = "syntavra-rs.exe" if os.name == "nt" else "syntavra-rs"
RUST_CAPABILITIES = (
    "config.explain",
    "config.resolve",
    "engine.capabilities",
    "engine.contract-hash",
    "pipeline.describe",
    "plugins.list",
    "receipt.inspect",
    "state.broker-live-snapshot",
    "state.broker-snapshot",
    "state.inspect",
    "state.layout",
    "status",
    "version",
)
RUST_CAPABILITY_ROWS = {
    name: ("preview", "read-only") for name in RUST_CAPABILITIES
}
ENGINE_CONTRACT_DESCRIPTOR = (
    "product=Syntavra\n"
    "product_version=0.0.1\n"
    "release_channel=pre-release\n"
    "contract_version=1\n"
    "engine=rust\n"
    "engine_stability=experimental\n"
    "capability=config.explain|preview|read-only\n"
    "capability=config.resolve|preview|read-only\n"
    "capability=engine.capabilities|preview|read-only\n"
    "capability=engine.contract-hash|preview|read-only\n"
    "capability=pipeline.describe|preview|read-only\n"
    "capability=plugins.list|preview|read-only\n"
    "capability=receipt.inspect|preview|read-only\n"
    "capability=state.broker-live-snapshot|preview|read-only\n"
    "capability=state.broker-snapshot|preview|read-only\n"
    "capability=state.inspect|preview|read-only\n"
    "capability=state.layout|preview|read-only\n"
    "capability=status|preview|read-only\n"
    "capability=version|preview|read-only\n"
)
ENGINE_CONTRACT_SHA256 = hashlib.sha256(ENGINE_CONTRACT_DESCRIPTOR.encode("utf-8")).hexdigest()

RustRunner = Callable[[Path, tuple[str, ...]], Mapping[str, Any]]


class EngineSelectionError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


@dataclass(frozen=True)
class EnginePreference:
    engine: str
    source: str
    scope: str
    path: str = ""


@dataclass(frozen=True)
class EngineSelection:
    requested: str
    resolved: str
    source: str
    scope: str
    source_path: str
    reason: str
    auto_policy: str = AUTO_ENGINE
    fallback_policy: str = "fail-closed"
    schema_version: int = ENGINE_SELECTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EngineVerification:
    engine: str
    available: bool
    compatible: bool
    stability: str
    executable: str = ""
    capabilities: tuple[str, ...] = ()
    checks: Mapping[str, bool] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    claim: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "capabilities": list(self.capabilities),
            "checks": dict(self.checks),
            "errors": list(self.errors),
        }


class EngineSelector:
    """Resolve and verify the R4 Python/Rust engine preference.

    R4 is selector-only. ``auto`` resolves to Python, and selecting Rust never
    causes a hidden Python fallback. General command routing to Rust is gated
    until later parity phases.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        state_root: Path | None = None,
        env: Mapping[str, str] | None = None,
        project_config: Path | None = None,
        user_config: Path | None = None,
        rust_binary: Path | None = None,
        runner: RustRunner | None = None,
    ):
        self.project_root = Path(project_root).resolve(strict=False)
        self.state_root = (
            Path(state_root).resolve(strict=False)
            if state_root is not None
            else self.project_root / ".syntavra" / "pre-release"
        )
        self.env = dict(os.environ if env is None else env)
        self.project_config = project_config or self.project_root / ".syntavra" / "engine.json"
        self.user_config = user_config or self._default_user_config()
        self._explicit_rust_binary = Path(rust_binary).resolve(strict=False) if rust_binary else None
        self._runner = runner or self._run_rust_json
        self.package_root = Path(__file__).resolve().parents[1]

    def _default_user_config(self) -> Path:
        override = self.env.get("SYNTAVRA_CONFIG_HOME", "").strip()
        if override:
            return Path(override).expanduser().resolve(strict=False) / "engine.json"
        if os.name == "nt":
            appdata = self.env.get("APPDATA", "").strip()
            if appdata:
                return Path(appdata).expanduser().resolve(strict=False) / "Syntavra" / "engine.json"
        xdg = self.env.get("XDG_CONFIG_HOME", "").strip()
        if xdg:
            return Path(xdg).expanduser().resolve(strict=False) / "syntavra" / "engine.json"
        home = self.env.get("HOME", "").strip()
        root = Path(home).expanduser() if home else Path.home()
        return root.resolve(strict=False) / ".config" / "syntavra" / "engine.json"

    @staticmethod
    def _validate_mode(value: object, *, source: str) -> str:
        mode = str(value).strip().casefold()
        if mode not in ENGINE_MODES:
            raise EngineSelectionError(
                "ENGINE_SELECTION_INVALID",
                f"invalid engine selection from {source}: {value!r}",
                source=source,
                allowed=list(ENGINE_MODES),
                actual=value,
            )
        return mode

    def _read_preference(self, path: Path, *, scope: str) -> EnginePreference | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EngineSelectionError(
                "ENGINE_CONFIG_INVALID",
                f"cannot read {scope} engine config",
                scope=scope,
                path=str(path),
                exception=type(exc).__name__,
            ) from exc
        if not isinstance(value, dict):
            raise EngineSelectionError(
                "ENGINE_CONFIG_INVALID",
                f"{scope} engine config must be a JSON object",
                scope=scope,
                path=str(path),
            )
        unknown = sorted(set(value) - {"schema_version", "engine"})
        if unknown:
            raise EngineSelectionError(
                "ENGINE_CONFIG_UNKNOWN_FIELDS",
                f"{scope} engine config contains unknown fields",
                scope=scope,
                path=str(path),
                fields=unknown,
            )
        if value.get("schema_version") != ENGINE_SELECTION_SCHEMA_VERSION:
            raise EngineSelectionError(
                "ENGINE_CONFIG_SCHEMA_UNSUPPORTED",
                f"unsupported {scope} engine config schema",
                scope=scope,
                path=str(path),
                expected=ENGINE_SELECTION_SCHEMA_VERSION,
                actual=value.get("schema_version"),
            )
        mode = self._validate_mode(value.get("engine"), source=str(path))
        return EnginePreference(mode, str(path), scope, str(path))

    def resolve(self, *, cli_override: str | None = None) -> EngineSelection:
        preference: EnginePreference | None = None
        if cli_override is not None:
            preference = EnginePreference(
                self._validate_mode(cli_override, source="--engine"),
                "--engine",
                "command",
            )
        if preference is None:
            environment = self.env.get("SYNTAVRA_ENGINE", "").strip()
            if environment:
                preference = EnginePreference(
                    self._validate_mode(environment, source="SYNTAVRA_ENGINE"),
                    "SYNTAVRA_ENGINE",
                    "environment",
                )
        if preference is None:
            preference = self._read_preference(self.project_config, scope="project")
        if preference is None:
            preference = self._read_preference(self.user_config, scope="user")
        if preference is None:
            preference = EnginePreference(DEFAULT_ENGINE, "builtin", "default")

        if preference.engine == "auto":
            resolved = AUTO_ENGINE
            reason = "AUTO_POLICY_PYTHON_R4"
        else:
            resolved = preference.engine
            reason = "EXPLICIT_SELECTION" if preference.scope != "default" else "BUILTIN_PYTHON_DEFAULT"
        return EngineSelection(
            requested=preference.engine,
            resolved=resolved,
            source=preference.source,
            scope=preference.scope,
            source_path=preference.path,
            reason=reason,
        )

    def _configured_rust_candidate(self) -> Path | None:
        if self._explicit_rust_binary is not None:
            return self._explicit_rust_binary
        override = self.env.get("SYNTAVRA_RUST_BIN", "").strip()
        if override:
            return Path(override).expanduser().resolve(strict=False)
        return None

    def discover_rust_binary(self) -> Path | None:
        configured = self._configured_rust_candidate()
        if configured is not None:
            return configured if configured.is_file() else None
        located = shutil.which("syntavra-rs")
        if located:
            return Path(located).resolve(strict=False)
        candidates: list[Path] = []
        for root in (self.project_root, self.package_root):
            candidates.extend(
                (
                    root / "target" / "release" / RUST_BINARY_NAME,
                    root / "target" / "debug" / RUST_BINARY_NAME,
                )
            )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve(strict=False)
        return None

    def _run_rust_json(self, binary: Path, arguments: tuple[str, ...]) -> Mapping[str, Any]:
        completed = subprocess.run(
            [str(binary), *arguments],
            cwd=self.project_root if self.project_root.is_dir() else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Rust engine command failed ({completed.returncode}): {completed.stderr.strip()}"
            )
        if len(completed.stdout.encode("utf-8")) > 1024 * 1024:
            raise RuntimeError("Rust engine response exceeded 1 MiB")
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise RuntimeError("Rust engine response must be a JSON object")
        return value

    def verify_python(self) -> EngineVerification:
        checks = {
            "product": True,
            "version": VERSION == "0.0.1",
            "channel": CHANNEL == "pre-release",
            "reference_engine": True,
        }
        return EngineVerification(
            engine="python",
            available=True,
            compatible=all(checks.values()),
            stability="reference",
            executable="python",
            capabilities=("all-current-python-surfaces",),
            checks=checks,
            claim="PYTHON_REFERENCE_ENGINE",
        )

    def verify_rust(self) -> EngineVerification:
        binary = self.discover_rust_binary()
        configured = self._configured_rust_candidate()
        if binary is None:
            candidate = str(configured) if configured is not None else ""
            return EngineVerification(
                engine="rust",
                available=False,
                compatible=False,
                stability="experimental",
                executable=candidate,
                checks={"binary_available": False},
                errors=("RUST_ENGINE_BINARY_NOT_FOUND",),
                claim="RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT",
            )

        errors: list[str] = []
        version: Mapping[str, Any] = {}
        capabilities: Mapping[str, Any] = {}
        contract_hash: Mapping[str, Any] = {}
        for arguments, target in (
            (("version",), "version"),
            (("engine", "capabilities"), "capabilities"),
            (("engine", "contract-hash"), "contract_hash"),
        ):
            try:
                value = self._runner(binary, arguments)
            except Exception as exc:  # subprocess/JSON boundaries are reported, not hidden
                errors.append(f"{target}:{type(exc).__name__}:{exc}")
                value = {}
            if target == "version":
                version = value
            elif target == "capabilities":
                capabilities = value
            else:
                contract_hash = value

        rows = capabilities.get("capabilities", [])
        capability_names: list[str] = []
        capability_rows_valid = isinstance(rows, list)
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    capability_rows_valid = False
                    continue
                name = str(row.get("name", ""))
                capability_names.append(name)
                expected = RUST_CAPABILITY_ROWS.get(name)
                if expected is None or (row.get("maturity"), row.get("mutation")) != expected:
                    capability_rows_valid = False

        checks = {
            "binary_available": True,
            "product": version.get("product") == "Syntavra",
            "version": version.get("product_version") == VERSION,
            "channel": version.get("release_channel") == CHANNEL,
            "engine": version.get("engine") == "rust",
            "stability": version.get("engine_stability") == "experimental",
            "contract_version": version.get("contract_version") == ENGINE_CONTRACT_VERSION,
            "capability_contract_version": capabilities.get("contract_version") == ENGINE_CONTRACT_VERSION,
            "capability_surface": tuple(capability_names) == RUST_CAPABILITIES,
            "capability_rows": capability_rows_valid,
            "hash_algorithm": contract_hash.get("algorithm") == "sha256",
            "descriptor_hash": contract_hash.get("contract_hash") == ENGINE_CONTRACT_SHA256,
        }
        return EngineVerification(
            engine="rust",
            available=True,
            compatible=not errors and all(checks.values()),
            stability="experimental",
            executable=str(binary),
            capabilities=tuple(capability_names),
            checks=checks,
            errors=tuple(errors),
            claim="RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT",
        )

    def list_engines(self, *, cli_override: str | None = None) -> dict[str, Any]:
        selection = self.resolve(cli_override=cli_override)
        return {
            "ok": True,
            "phase": "R4",
            "selection": selection.to_dict(),
            "engines": [self.verify_python().to_dict(), self.verify_rust().to_dict()],
            "policy": {
                "auto": AUTO_ENGINE,
                "python": "reference",
                "rust": "experimental-selector-only",
                "fallback": "fail-closed",
            },
        }

    def status(self, *, cli_override: str | None = None) -> dict[str, Any]:
        selection = self.resolve(cli_override=cli_override)
        selected = self.verify_python() if selection.resolved == "python" else self.verify_rust()
        return {
            "ok": selected.compatible,
            "phase": "R4",
            "selection": selection.to_dict(),
            "selected_engine": selected.to_dict(),
            "configs": {
                "project": {"path": str(self.project_config), "exists": self.project_config.is_file()},
                "user": {"path": str(self.user_config), "exists": self.user_config.is_file()},
                "environment": {"name": "SYNTAVRA_ENGINE", "set": bool(self.env.get("SYNTAVRA_ENGINE", "").strip())},
                "rust_binary": {"name": "SYNTAVRA_RUST_BIN", "set": bool(self.env.get("SYNTAVRA_RUST_BIN", "").strip())},
            },
            "routing": {
                "auto": "python",
                "python": "enabled",
                "rust": "general-command-routing-blocked-until-R5+",
                "fallback": "fail-closed",
            },
        }

    def verify(self, *, cli_override: str | None = None, all_engines: bool = False) -> dict[str, Any]:
        selection = self.resolve(cli_override=cli_override)
        python = self.verify_python()
        rust = self.verify_rust()
        selected = python if selection.resolved == "python" else rust
        ok = python.compatible and rust.compatible if all_engines else selected.compatible
        return {
            "ok": ok,
            "phase": "R4",
            "selection": selection.to_dict(),
            "selected_engine": selected.to_dict(),
            "engines": [python.to_dict(), rust.to_dict()] if all_engines else [selected.to_dict()],
            "all_engines_required": all_engines,
            "claim": "RUST_ENGINE_EXPERIMENTAL_NOT_DEFAULT",
        }

    def use(self, engine: str, *, scope: str = "project") -> dict[str, Any]:
        mode = self._validate_mode(engine, source="engine use")
        if scope not in {"project", "user"}:
            raise EngineSelectionError(
                "ENGINE_SCOPE_INVALID",
                f"invalid engine config scope: {scope!r}",
                allowed=["project", "user"],
            )
        rust_verification: EngineVerification | None = None
        if mode == "rust":
            rust_verification = self.verify_rust()
            if not rust_verification.compatible:
                raise EngineSelectionError(
                    "RUST_ENGINE_NOT_VERIFIED",
                    "Rust cannot be selected until its binary and R4 contract verify",
                    verification=rust_verification.to_dict(),
                )
        path = self.project_config if scope == "project" else self.user_config
        atomic_write_json(
            path,
            {"schema_version": ENGINE_SELECTION_SCHEMA_VERSION, "engine": mode},
            mode=0o600,
        )
        effective = self.resolve()
        warnings: list[str] = []
        if effective.source in {"SYNTAVRA_ENGINE", "--engine"}:
            warnings.append("higher-precedence-override-remains-active")
        return {
            "ok": True,
            "phase": "R4",
            "persisted": {"engine": mode, "scope": scope, "path": str(path)},
            "effective": effective.to_dict(),
            "rust_verification": rust_verification.to_dict() if rust_verification else None,
            "warnings": warnings,
        }

    def gate_general_command(self, command: str, *, cli_override: str | None = None) -> dict[str, Any]:
        selection = self.resolve(cli_override=cli_override)
        if selection.resolved == "python":
            return {"ok": True, "selection": selection.to_dict()}
        rust = self.verify_rust()
        if not rust.compatible:
            raise EngineSelectionError(
                "RUST_ENGINE_UNAVAILABLE",
                "Rust is selected but its binary or contract verification failed",
                command=command,
                selection=selection.to_dict(),
                verification=rust.to_dict(),
            )
        raise EngineSelectionError(
            "RUST_COMMAND_ROUTING_NOT_AVAILABLE_R4",
            "R4 exposes selection and verification only; general Rust command routing is still blocked",
            command=command,
            selection=selection.to_dict(),
            allowed=[
                "syntavra engine list",
                "syntavra engine status",
                "syntavra engine verify",
                "syntavra engine use python|rust|auto",
            ],
        )
