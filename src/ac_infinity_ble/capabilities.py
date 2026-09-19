"""Device families identified in the AC Infinity 2.0.8 Android app.

Controller IDs and connected load IDs are separate namespaces. Never choose a
wire format from a marketing name or from the connected load's type.
"""

SENSOR_TYPES = frozenset({3, 4, 5, 14, 15, 24, 25, 34, 35})
H_TYPES = frozenset({19, 20, 21, 22, 26, 27, 51})
HOME_TYPES = frozenset({33, 39, 40, 48, 49, 50})
MULTIPORT_TYPES = frozenset({7, 8, 9, 11, 12, 16, 17, 18, 23} | H_TYPES | HOME_TYPES)
MODEL_NAMES = {
    1: "Controller 67",
    2: "Controller 76",
    3: "Cloudcom",
    4: "Cloudcom B2",
    5: "Cloudcom B1",
    6: "Airtap",
    7: "Controller 69",
    8: "Controller 69 Wi-Fi",
    9: "Wall-hang Controller",
    11: "Controller 69 Pro",
    12: "Desktop Controller",
    14: "Cloudcom A2",
    15: "Cloudcom A1",
    16: "Wall-hang Controller Pro",
    17: "Desktop Controller Pro",
    18: "Controller 69 Pro+",
    19: "Controller AI",
    20: "Controller AI+",
    21: "Outlet AI",
    22: "Outlet AI+",
    23: "Airtitan",
    24: "VPD Cloudcom B2",
    25: "VPD Cloudcom B1",
    26: "Controller H (26)",
    27: "Controller H (27)",
    33: "Through-wall Fan",
    34: "Cloudcom (34)",
    35: "Cloudcom (35)",
    39: "Smart AC 8K",
    40: "Smart AC 12K",
    48: "Airtap AI",
    49: "Desktop Fan",
    50: "Pedestal Fan",
    51: "Controller AI 2",
}

# Concrete retail model numbers in APK DeviceConfig.json. Category labels and
# "All Models" aliases are deliberately omitted.
MODEL_NUMBERS = {
    1: "CTR67A",
    2: "CTR76A",
    4: "AC-CCB2",
    5: "AC-CCB1",
    7: "CTR69A",
    8: "CTR69X",
    9: "CTR79A",
    11: "CTR69P",
    12: "CTR75A",
    14: "AC-CCA2",
    15: "AC-CCA1",
    16: "CTR79P",
    17: "CTR75P",
    18: "CTR69Q",
    20: "CTR89Q",
    21: "AC-ADA4",
    22: "AC-ADA8",
    24: "AC-CCB2",
    25: "AC-CCB1",
    39: "AC-TFH8",
    40: "AC-12K",
}


def is_h4(device_type: int, version: int) -> bool:
    return device_type == 51 or device_type in H_TYPES and version >= 20


def family(device_type: int) -> str:
    if device_type in SENSOR_TYPES:
        return "C"
    if device_type in H_TYPES:
        return "K" if device_type == 51 else "H"
    if device_type in HOME_TYPES:
        return "I"
    return {
        2: "B",
        6: "D",
        7: "E",
        8: "E",
        9: "F",
        12: "F",
        16: "F",
        17: "F",
        11: "G",
        18: "G",
        23: "G",
    }.get(device_type, "A")


def load_kind(wire_type: int | None) -> str | None:
    """Map the APK's port type byte, including its high-bit variants."""
    if wire_type is None:
        return None
    return {
        0x80: "switch",
        1: "light",
        2: "humidifier",
        3: "dehumidifier",
        4: "heater",
        5: "air_conditioner",
        6: "fan",
        7: "fan",
    }.get(
        wire_type,
        {
            1: "light",
            2: "humidifier",
            3: "dehumidifier",
            4: "heater",
            5: "air_conditioner",
            6: "fan",
            7: "fan",
        }.get(wire_type & 0x7F),
    )


def recognized_kind(resistance: int, load_id: int = 0) -> str | None:
    """Identify older UIS loads from resistance, or newer loads from their ID."""
    ranges: tuple[tuple[int, int, str], ...]
    if 0 < load_id < 65535:
        ranges = (
            (512, 1023, "light"),
            (1024, 2047, "fan"),
            (2048, 2303, "humidifier"),
            (2304, 2559, "dehumidifier"),
            (2560, 2815, "switch"),
            (2816, 3071, "heater"),
            (3072, 3327, "air_conditioner"),
            (33024, 65534, "switch"),
        )
        value = load_id
    else:
        ranges = (
            (380, 420, "light"),
            (3135, 3465, "light"),
            (4845, 10500, "fan"),
            (10501, 12600, "humidifier"),
            (12601, 14385, "dehumidifier"),
            (14386, 16590, "switch"),
            (16591, 18900, "heater"),
            (18901, 21525, "air_conditioner"),
            (35100, 42900, "dehumidifier"),
        )
        value = resistance
    return next((kind for low, high, kind in ranges if low <= value <= high), None)
