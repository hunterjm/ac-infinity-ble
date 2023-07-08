from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ACInfinityControllerState:
    model: int = 0
