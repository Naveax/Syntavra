from __future__ import annotations

from typing import Callable

from ..base import EngineRequest, EngineResponse
from ...activation import AuthorizedSession
from ...errors import ActivationError


class LiveStudioAdapter:
    engine_id = "studio_bridge"
    engine_version = "1.0.0-live-contract"
    mode = "LIVE"

    def __init__(self, *, enabled: bool, session: AuthorizedSession | None, transport: Callable[[EngineRequest], EngineResponse] | None) -> None:
        self.enabled = enabled
        self.session = session
        self.transport = transport

    def execute(self, request: EngineRequest) -> EngineResponse:
        if not self.enabled or self.session is None or self.transport is None:
            raise ActivationError("live Studio adapter is disabled until signed activation and explicit configuration are present")
        if request.capability not in self.session.capabilities:
            raise ActivationError("live adapter capability is not authorized")
        return self.transport(request)
