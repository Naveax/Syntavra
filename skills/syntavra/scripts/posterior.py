#!/usr/bin/env python3
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BetaPosterior:
    alpha: float = 1.0
    beta: float = 1.0

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        total = self.alpha + self.beta
        return self.alpha * self.beta / (total * total * (total + 1.0))

    def lower(self, z: float = 1.6448536269514722) -> float:
        return max(0.0, self.mean - z * math.sqrt(max(0.0, self.variance)))

    def update(self, success: float, weight: float = 1.0) -> "BetaPosterior":
        bounded = max(0.0, min(1.0, success))
        return BetaPosterior(self.alpha + bounded * weight, self.beta + (1.0 - bounded) * weight)


@dataclass(frozen=True)
class NormalStats:
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0

    def update(self, value: float) -> "NormalStats":
        count = self.count + 1
        delta = value - self.mean
        mean = self.mean + delta / count
        m2 = self.m2 + delta * (value - mean)
        return NormalStats(count, mean, m2)

    @property
    def variance(self) -> float:
        return self.m2 / max(1, self.count - 1)

    def upper(self, z: float = 1.6448536269514722) -> float:
        if self.count <= 1:
            return self.mean
        return self.mean + z * math.sqrt(self.variance / self.count)

    def lower(self, z: float = 1.6448536269514722) -> float:
        if self.count <= 1:
            return self.mean
        return self.mean - z * math.sqrt(self.variance / self.count)
