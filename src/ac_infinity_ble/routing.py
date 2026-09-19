"""Centralize APK model/version dispatch for commands, telemetry and clients."""

from dataclasses import dataclass
from enum import Enum

from .capabilities import (
    H_TYPES,
    HOME_TYPES,
    MODEL_NAMES,
    MODEL_NUMBERS,
    MULTIPORT_TYPES,
    SENSOR_TYPES,
    is_h4,
)


class TelemetryLayout(Enum):
    LEGACY = "legacy"
    V6 = "v6"
    SENSOR = "sensor"
    H = "h"
    H4 = "h4"
    HOME = "home"


@dataclass(frozen=True)
class ProtocolProfile:
    model_name: str
    telemetry: TelemetryLayout
    addressed: bool
    model_number: str | None = None
    parameter_length_size: int = 1
    writable: bool = True
    separate_power: bool = False
    root_kind: str | None = None
    aggregate_fan: bool = True
    packed_manual_level: bool = False

    @property
    def read_tags(self) -> tuple[int, ...]:
        return (16, 18) if self.separate_power else tuple(range(16, 24))

    def port_suffix(self, port: int) -> bytes:
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 255:
            raise ValueError("Invalid port")
        if port and not self.addressed:
            raise ValueError("Invalid port")
        return bytes((255, port)) if self.addressed else b""


def resolve_profile(device_type: int, version: int) -> ProtocolProfile:
    """Reject unverified models instead of inheriting a generic APK fallback."""
    if device_type not in MODEL_NAMES:
        raise ValueError("Unsupported device")
    sensor = device_type in SENSOR_TYPES
    home = device_type in HOME_TYPES
    extended = is_h4(device_type, version)
    if sensor:
        layout = TelemetryLayout.SENSOR
    elif home:
        layout = TelemetryLayout.HOME
    elif extended:
        layout = TelemetryLayout.H4
    elif device_type in H_TYPES:
        layout = TelemetryLayout.H
    else:
        layout = TelemetryLayout.V6 if version >= 5 else TelemetryLayout.LEGACY
    root_kind = None
    if home:
        root_kind = "air_conditioner" if device_type in (39, 40) else "fan"
    elif device_type in {2, 9, 12, 16, 17, 21, 22}:
        root_kind = "switch"
    return ProtocolProfile(
        model_name=MODEL_NAMES[device_type],
        model_number=MODEL_NUMBERS.get(device_type),
        telemetry=layout,
        addressed=device_type in MULTIPORT_TYPES,
        parameter_length_size=2 if extended else 1,
        writable=not sensor,
        separate_power=home,
        root_kind=root_kind,
        aggregate_fan=not sensor and root_kind is None,
        packed_manual_level=not home and not sensor and (extended or version > 7),
    )
