from __future__ import annotations

from dataclasses import dataclass

from .errors import BudgetError


@dataclass(slots=True)
class BudgetLedger:
    token_limit: int
    request_limit: int
    transfer_limit: int
    gpu_limit_ms: int
    wall_time_limit_s: int
    tokens: int = 0
    requests: int = 0
    transfer_bytes: int = 0
    gpu_time_ms: int = 0
    wall_time_s: float = 0.0

    def reserve(self, *, tokens: int = 0, requests: int = 0, transfer_bytes: int = 0, gpu_time_ms: int = 0, wall_time_s: float = 0.0) -> None:
        proposed = (
            self.tokens + tokens, self.requests + requests, self.transfer_bytes + transfer_bytes,
            self.gpu_time_ms + gpu_time_ms, self.wall_time_s + wall_time_s,
        )
        limits = (self.token_limit, self.request_limit, self.transfer_limit, self.gpu_limit_ms, self.wall_time_limit_s)
        if any(value > limit for value, limit in zip(proposed, limits)):
            raise BudgetError("workflow budget exceeded")
        self.tokens, self.requests, self.transfer_bytes, self.gpu_time_ms, self.wall_time_s = proposed
