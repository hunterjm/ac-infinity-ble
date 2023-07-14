from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeviceInfo:
    type: int
    name: str
    version: int
    is_degree: bool | None = None
    tmp_state: int | None = None
    hum_state: int | None = None
    vpd_state: int | None = None
    choose_port: int | None = None
    tmp: float | None = None
    hum: float | None = None
    vpd: float | None = None
    fan_type: int | None = None
    fan_state: int | None = None
    fan: int | None = None
    work_type: int | None = None
    level_on: int | None = None
    level_off: int | None = None
