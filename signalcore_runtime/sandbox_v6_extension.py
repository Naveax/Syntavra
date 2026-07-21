from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Iterable, Mapping


def install() -> None:
    from .sandbox import SandboxError, SandboxManager, SandboxPolicy

    if getattr(SandboxManager, "_signalcore_v6_hardening", False):
        return
    original_plan = SandboxManager.plan

    def plan(
        self: Any,
        argv: Iterable[str],
        *,
        policy: SandboxPolicy,
        cwd: str = ".",
        env: Mapping[str, str] | None = None,
    ) -> Any:
        overrides = dict(policy.env_overrides)
        configured_image = str(overrides.get("SIGNALCORE_SANDBOX_IMAGE") or "")
        selected, _ = self.select_backend(policy)
        if selected in {"docker", "podman"}:
            if not configured_image:
                raise SandboxError(
                    "container sandbox requires SIGNALCORE_SANDBOX_IMAGE pinned by digest"
                )
            if "@sha256:" not in configured_image or len(configured_image.rsplit("@sha256:", 1)[1]) != 64:
                raise SandboxError("container sandbox image must use an immutable sha256 digest")
            overrides["SIGNALCORE_SANDBOX_IMAGE"] = configured_image
            effective = replace(policy, env_overrides=overrides)
        else:
            effective = policy
        result = original_plan(self, argv, policy=effective, cwd=cwd, env=env)
        if result.backend not in {"docker", "podman"}:
            return result
        command = list(result.command)
        try:
            image_index = command.index(configured_image)
        except ValueError as exc:
            raise SandboxError("sandbox planner lost the configured immutable image") from exc
        hardening = [
            "--user", "65532:65532",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
        ]
        command[image_index:image_index] = hardening
        guarantees = dict(result.guarantees)
        guarantees.update({
            "non_root": True,
            "capabilities_dropped": True,
            "no_new_privileges": True,
            "image_digest_pinned": True,
        })
        return replace(
            result,
            command=tuple(command),
            guarantees=guarantees,
            policy=asdict(effective),
        )

    SandboxManager.plan = plan
    SandboxManager._signalcore_v6_hardening = True
