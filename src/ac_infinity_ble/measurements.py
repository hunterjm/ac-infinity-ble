"""Shared wire measurement primitives for advertisements and telemetry."""


def reading(data: bytes | bytearray, offset: int, scale: int = 100) -> float | None:
    value = int.from_bytes(data[offset : offset + 2], "big", signed=True)
    return None if value == -32768 else value / scale


def packed_sensor_values(data: bytes | bytearray) -> tuple[float | None, float | None]:
    """Cloudcom v4 uses two signed-magnitude 12-bit readings in three bytes."""
    raw = int.from_bytes(data, "big")

    def decode(value: int) -> float | None:
        if value == 0xFFF:
            return None
        return (value & 0x7FF) / (-10 if value & 0x800 else 10)

    return decode(raw >> 12), decode(raw & 0xFFF)
