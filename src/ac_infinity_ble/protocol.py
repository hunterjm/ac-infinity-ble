from .models import DeviceInfo
from .util import crc16, get_bit, get_bits, get_short


def get_type(type: int) -> str:
    if type == 2:
        return "B"
    if type in [3, 4, 5, 14, 15]:
        return "C"
    if type == 6:
        return "D"
    if type in [7, 8]:
        return "E"
    if type in [9, 12]:
        return "F"
    if type == 11:
        return "G"
    return "A"


def get_mode(mode: int) -> str:
    if mode == 1:
        return "OFF"
    if mode == 2:
        return "ON"
    if mode == 3:
        return "AUTO"
    if mode == 4:
        return "TIMER ON"
    if mode == 5:
        return "TIMER OFF"
    if mode == 6:
        return "CYCLE"
    if mode == 7:
        return "SCHEDULE"
    if mode == 8:
        return "VPD"
    if mode == 9:
        return "TEMPERATURE PARAM"
    if mode == 10:
        return "HUMIDITY PARAM"
    if mode == 11:
        return "ADVANCE"
    if mode == 12:
        return "AI"
    return ""


def parse_manufacturer_data(data: bytes) -> DeviceInfo:
    device = DeviceInfo(
        type=data[12],
        version=data[11],
        name=f"{get_type(data[12])}-{data[6:11].decode('ascii')}",
        is_degree=True ^ get_bit(data[13], 1),
        fan_state=get_bits(data[13], 2, 2),
        tmp_state=get_bits(data[13], 4, 2),
        hum_state=get_bits(data[13], 6, 2),
        tmp=get_short(data, 14) / 100,
        hum=get_short(data, 16) / 100,
        fan=data[18],
    )
    if device.version >= 3 and device.type in [7, 9, 11, 12]:
        device.choose_port = data[19]
        device.vpd_state = get_bits(data[20], 0, 2)
        device.vpd = get_short(data, 21) / 100
    return device


class Protocol:
    """Protocol for AC Infinity Controllers."""

    def __init__(self) -> None:
        self._head = [165, 0]
        self._scan_record_length = 27

    def _add_init(self, bytes: list[int], i: int, i2: int) -> None:
        bytes[i] = (i2 >> 8) & 255
        bytes[i + 1] = i2 & 255

    def _add_head(self, data: list[int], b: int, i: int) -> bytes:
        result = [0] * (len(data) + 12)
        result[0 : len(self._head)] = self._head  # noqa: E203
        self._add_init(result, 2, len(data))
        self._add_init(result, 4, i)
        result[6:8] = crc16(result, 0, 6)
        result[8] = 0
        result[9] = b
        result[10 : 10 + len(data)] = data  # noqa: E203
        result[len(data) + 10 : len(data) + 12] = crc16(  # noqa: E203
            result, 8, len(data) + 2
        )
        return bytes(result)

    def get_model_data(self, type: int, b: int, sequence: int) -> bytes:
        command = [16, 17, 18, 19, 20, 21, 22, 23]
        if type in [7, 9, 11, 12]:
            command += [255, b]
        return self._add_head(command, 1, sequence)

    def set_level(
        self, type: int, work_type: int, level: int, b: int, sequence: int
    ) -> bytes:
        if work_type not in [1, 2]:
            raise ValueError("Work type must be 1 (off) or 2 (on)")
        if level not in range(0, 11):
            raise ValueError("Level must be between 0 and 10")

        command = [16, 1, work_type, work_type + 16, 1, level]
        if type in [7, 9, 11, 12]:
            command += [255, b]
        return self._add_head(command, 3, sequence)
