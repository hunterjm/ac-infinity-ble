from dataclasses import replace

from .capabilities import (
    H_TYPES,
    HOME_TYPES,
    MODEL_NAMES,
    MULTIPORT_TYPES,
    SENSOR_TYPES,
    family,
)
from .commands import ParameterCodec
from .exceptions import FrameValidationError
from .measurements import packed_sensor_values, reading
from .models import DeviceInfo
from .modes import ControllerMode
from .util import crc16, get_bit, get_bits


def get_type(type: int) -> str:
    return family(type)


def get_mode(mode: int) -> str:
    try:
        return ControllerMode(mode).name.replace("_", " ")
    except ValueError:
        return ""


def parse_manufacturer_data(data: bytes) -> DeviceInfo:
    if len(data) not in (17, 27) or data[12] not in MODEL_NAMES:
        raise ValueError("Unsupported AC Infinity advertisement")
    sensor = data[12] in SENSOR_TYPES
    vpd_sensor = data[12] in (24, 25)
    try:
        name = data[:6].hex().upper() if vpd_sensor else data[6:11].decode("ascii")
    except UnicodeDecodeError as ex:
        raise ValueError("Invalid AC Infinity device name") from ex
    device = DeviceInfo(
        type=data[12],
        version=data[11],
        name=f"{get_type(data[12])}-{name}",
        is_degree=True ^ get_bit(data[13], 1),
        fan_state=get_bits(data[13], 2, 2),
        tmp_state=get_bits(data[13], 4, 2),
        hum_state=get_bits(data[13], 6, 2),
    )
    # Short advertisements supply identity, not the extended state fields.
    if len(data) == 17:
        return device
    offset = 15 if vpd_sensor else 14
    if sensor and device.version > 3:
        tmp, hum = packed_sensor_values(data[offset : offset + 3])
    elif vpd_sensor:
        tmp = reading(data, offset, 10)
        hum = float(data[offset + 2])
    else:
        tmp = reading(data, 14)
        hum = reading(data, 16)
    device = replace(device, tmp=tmp, hum=hum)
    if vpd_sensor:
        device = replace(
            device, vpd_state=get_bits(data[6], 2, 2), vpd=reading(data, 7)
        )
    if not sensor:
        device = replace(device, fan=data[18] if data[18] <= 10 else None)
    if device.version >= 3 and device.type in MULTIPORT_TYPES:
        device = replace(
            device,
            choose_port=data[19],
            vpd_state=get_bits(data[20], 0, 2),
            vpd=reading(data, 21),
        )
    return device


def is_command_header(data: bytes | bytearray) -> bool:
    """APK StateMachine accepts the A5 00 through A5 1F header family."""
    return len(data) >= 2 and data[0] == 0xA5 and data[1] <= 0x1F


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
        result[0 : len(self._head)] = self._head
        self._add_init(result, 2, len(data))
        self._add_init(result, 4, i)
        result[6:8] = crc16(result, 0, 6)
        result[8] = 0
        result[9] = b
        result[10 : 10 + len(data)] = data
        result[len(data) + 10 : len(data) + 12] = crc16(result, 8, len(data) + 2)
        return bytes(result)

    def parse_response(
        self,
        data: bytes | bytearray,
        expected_sequence: int | None = None,
        expected_command: int | None = None,
    ) -> bytes:
        """Return the payload of a valid, optionally request-matched response.

        Responses use the APK A5 00-A5 1F family and declare payload length in bytes
        2-3. The header and command/payload each have a separate CRC16.
        Telemetry (1E FF) uses a different format and is not a response.
        """
        if len(data) < 12 or not is_command_header(data):
            raise FrameValidationError("Invalid response header")
        payload_length = int.from_bytes(data[2:4], "big")
        if len(data) != payload_length + 12:
            raise FrameValidationError("Invalid response length")
        if data[6:8] != bytes(crc16(list(data[:6]))):
            raise FrameValidationError("Invalid response header CRC")
        if data[-2:] != bytes(crc16(list(data[8:-2]))):
            raise FrameValidationError("Invalid response body CRC")
        if (
            expected_sequence is not None
            and int.from_bytes(data[4:6], "big") != expected_sequence
        ):
            raise FrameValidationError("Unexpected response sequence")
        if (
            expected_command is not None
            and int.from_bytes(data[8:10], "big") != expected_command
        ):
            raise FrameValidationError("Unexpected response command")
        return bytes(data[10:-2])

    def response_frame_length(self, data: bytes | bytearray) -> int:
        """Read a frame length only after validating the complete header."""
        if len(data) < 8 or not is_command_header(data):
            raise FrameValidationError("Invalid response header")
        if data[6:8] != bytes(crc16(list(data[:6]))):
            raise FrameValidationError("Invalid response header CRC")
        return int.from_bytes(data[2:4], "big") + 12

    def get_model_data(self, type: int, b: int, sequence: int) -> bytes:
        return self.get_parameters(
            DeviceInfo(type, "", 0), b, list(range(16, 24)), sequence
        )

    def set_level(
        self, type: int, work_type: int, level: int, b: int, sequence: int
    ) -> bytes:
        """Legacy packet API; newer families require version-aware parameters."""
        if type not in MODEL_NAMES or type in H_TYPES | HOME_TYPES | SENSOR_TYPES:
            raise ValueError("Use version-aware set_parameters for this device")
        if work_type not in [1, 2]:
            raise ValueError("Work type must be 1 (off) or 2 (on)")
        if level not in range(0, 11):
            raise ValueError("Level must be between 0 and 10")

        command = [16, 1, work_type, work_type + 16, 1, level]
        if type in MULTIPORT_TYPES:
            command += [255, b]
        return self._add_head(command, 3, sequence)

    def get_parameters(
        self, device: DeviceInfo, port: int, tags: list[int], sequence: int
    ) -> bytes:
        payload = ParameterCodec(device.profile).encode_get(port, tags)
        return self._add_head(list(payload), 1, sequence)

    def set_parameters(
        self, device: DeviceInfo, port: int, values: dict[int, bytes], sequence: int
    ) -> bytes:
        payload = ParameterCodec(device.profile).encode_set(port, values)
        return self._add_head(list(payload), 3, sequence)

    def parse_parameters(
        self, data: bytes, device: DeviceInfo, port: int
    ) -> dict[int, bytes]:
        payload = self.parse_response(data, expected_command=1)
        return ParameterCodec(device.profile).decode_get(payload, port)

    def validate_ack(
        self, data: bytes, device: DeviceInfo, port: int, tags: set[int]
    ) -> None:
        payload = self.parse_response(data, expected_command=3)
        ParameterCodec(device.profile).validate_ack(payload, port, tags)
