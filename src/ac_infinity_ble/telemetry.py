"""Pure decoders for the APK's unsolicited 1e ff telemetry families."""

from dataclasses import replace

from .capabilities import load_kind, recognized_kind
from .measurements import packed_sensor_values, reading
from .models import DeviceInfo, PortState
from .routing import TelemetryLayout


def frame_length(data: bytes | bytearray, state: DeviceInfo) -> int | None:
    if state.profile.telemetry == TelemetryLayout.SENSOR:
        return 31
    width = state.profile.parameter_length_size
    if len(data) < 5 + width:
        return None
    return 5 + width + int.from_bytes(data[5 : 5 + width], "big")


def parse_telemetry(data: bytes | bytearray, state: DeviceInfo) -> DeviceInfo:
    if data[:4] != b"\x1e\xff\x02\x09" or len(data) != frame_length(data, state):
        raise ValueError("Invalid telemetry framing")
    return _DECODERS[state.profile.telemetry](data, state)


def _parse_sensor(data: bytes | bytearray, state: DeviceInfo) -> DeviceInfo:
    if len(data) != 31:
        raise ValueError("Invalid sensor telemetry length")
    tmp, hum = (
        packed_sensor_values(data[19:22])
        if state.version > 3
        else (reading(data, 19, 10), float(data[21]))
    )
    return replace(
        state,
        tmp=tmp,
        hum=hum,
        vpd=reading(data, 11) if state.type in (24, 25) else state.vpd,
    )


def _parse_home(data: bytes | bytearray, state: DeviceInfo) -> DeviceInfo:
    if len(data) < 28:
        raise ValueError("Truncated home appliance telemetry")
    kind = state.profile.root_kind
    port = PortState(
        0, kind=kind, level=data[7] >> 4, mode=data[23], fault=bool(data[7] & 2)
    )
    return replace(
        state,
        tmp=reading(data, 12),
        fan=port.level,
        work_type=port.mode,
        ports={0: port},
    )


def _parse_controller(data: bytes | bytearray, state: DeviceInfo) -> DeviceInfo:
    modern = state.profile.telemetry == TelemetryLayout.V6
    start, stride = (22, 8) if modern else (18, 4)
    if len(data) < start or (len(data) - start) % stride:
        raise ValueError("Invalid controller telemetry length")
    ports = {}
    for port_id, offset in enumerate(range(start, len(data), stride), 1):
        raw = int.from_bytes(data[offset : offset + 2], "big")
        ports[port_id] = PortState(
            port_id,
            connected=data[offset] != 255,
            raw_type=raw,
            kind=(load_kind(data[offset + 7]) if modern else None)
            or recognized_kind(raw),
            level=(data[offset + 2] >> 2) & 15 if modern else data[offset + 3] >> 4,
            mode=data[offset + 3] & 15,
            fault=bool(data[offset + 2] & 1) if modern else False,
        )
    return replace(
        state,
        is_degree=not bool(data[6] & 128),
        tmp_state=(data[6] >> 5) & 3,
        hum_state=(data[6] >> 3) & 3,
        vpd_state=(data[6] >> 1) & 3,
        choose_port=data[7] & 15,
        tmp=reading(data, 8),
        hum=reading(data, 10),
        vpd=reading(data, 12),
        fan_type=int.from_bytes(data[14:16], "big"),
        fan_state=data[16] >> 6,
        work_type=data[17] & 15,
        fan=(data[16] >> 2) & 15 if modern else state.fan,
        ports=ports,
    )


def _parse_h(data: bytes | bytearray, state: DeviceInfo) -> DeviceInfo:
    extended = state.profile.telemetry == TelemetryLayout.H4
    if len(data) < 22:
        raise ValueError("Truncated AI telemetry")
    if extended:
        sensor_count, port_count, selected = data[8] & 63, data[12], data[13]
        offset = 15 + (data[14] & 15) * 3
        stride, sensor_stride, sort_length = 11, 5, 11
    else:
        sensor_count = data[7] & 63
        # The APK accepts both original and expanded H headers.
        expanded = (
            len(data) >= 13
            and len(data)
            == 13 + (data[11] >> 4) * 10 + sensor_count * 4 + (data[12] & 15) * 3
        )
        base = 11 if expanded else 8
        port_count, selected = data[base] >> 4, data[base] & 15
        offset = base + 2 + (data[base + 1] & 15) * 3
        stride, sensor_stride, sort_length = 10, 4, 0
    if offset + port_count * stride + sort_length + sensor_count * sensor_stride != len(
        data
    ):
        raise ValueError("Invalid AI port/sensor counts")
    ports = {}
    for index in range(port_count):
        port_id = data[offset] if extended else index
        start = offset + int(extended)
        if port_id in ports:
            raise ValueError("Duplicate AI port")
        ports[port_id] = PortState(
            port_id,
            connected=data[start] != 255,
            raw_type=int.from_bytes(data[start : start + 2], "big"),
            kind=load_kind(data[start + 4])
            or recognized_kind(
                int.from_bytes(data[start : start + 2], "big"),
                int.from_bytes(data[start + 2 : start + 4], "big"),
            ),
            level=(data[start + 5] >> 2) & 15,
            mode=data[start + 6] & 31,
            fault=bool(data[start + 5] & 1),
        )
        offset += stride
    offset += sort_length
    readings = {}
    for _ in range(sensor_count):
        start = offset + int(extended)
        sensor_id, flags = data[start], data[start + 1]
        raw = int.from_bytes(data[start + 2 : start + 4], "big", signed=True)
        accuracy = (flags >> 5) & 3
        value = None if raw == -32768 else raw * (10, 1, 0.1, 0.01)[accuracy]
        # The primary climate probe uses IDs 0/1 (F/C), 2 (RH), 3 (VPD).
        if sensor_id == 0 and value is not None:
            value = (value - 32) / 1.8
        key = {0: "tmp", 1: "tmp", 2: "hum", 3: "vpd"}.get(sensor_id)
        if key is not None:
            readings[key] = value
        offset += sensor_stride
    main = ports.get(0)
    return replace(
        state,
        choose_port=selected,
        ports=ports,
        fan=main.level if main else state.fan,
        work_type=main.mode if main else state.work_type,
        tmp=readings.get("tmp", state.tmp),
        hum=readings.get("hum", state.hum),
        vpd=readings.get("vpd", state.vpd),
    )


_DECODERS = {
    TelemetryLayout.LEGACY: _parse_controller,
    TelemetryLayout.V6: _parse_controller,
    TelemetryLayout.SENSOR: _parse_sensor,
    TelemetryLayout.H: _parse_h,
    TelemetryLayout.H4: _parse_h,
    TelemetryLayout.HOME: _parse_home,
}
