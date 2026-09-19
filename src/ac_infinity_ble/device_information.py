"""APK BleServer's optional GATT Device Information revision strings."""

from dataclasses import dataclass

REVISION_CHARACTERISTICS = {
    "software_revision": "00002a28-0000-1000-8000-00805f9b34fb",
    "hardware_revision": "00002a27-0000-1000-8000-00805f9b34fb",
    "firmware_revision": "00002a26-0000-1000-8000-00805f9b34fb",
}


@dataclass(frozen=True)
class DeviceInformation:
    firmware_revision: str | None = None
    hardware_revision: str | None = None
    software_revision: str | None = None


def decode_revision(data: bytes | bytearray) -> str | None:
    """Keep a real printable ASCII value; never substitute app default versions."""
    try:
        value = data.decode("ascii").rstrip("\x00").strip()
    except UnicodeDecodeError:
        return None
    return value if value and len(value) <= 64 and value.isprintable() else None
