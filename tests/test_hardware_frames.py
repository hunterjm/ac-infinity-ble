"""Controller 69 Pro (software 3.2.56, revision 7) and Airtap T4 captures."""

import asyncio
from binascii import crc_hqx
from unittest.mock import AsyncMock

import pytest

from ac_infinity_ble.commands import ParameterCodec
from ac_infinity_ble.models import DeviceInfo, PortState
from ac_infinity_ble.modes import ControllerMode
from ac_infinity_ble.protocol import Protocol, parse_manufacturer_data
from ac_infinity_ble.telemetry import parse_telemetry

from .frames import response_frame
from .test_device import make_controller

MODEL_V7 = bytes.fromhex(
    "a5170033000273000001100102110100120106130a084c1820006400200000140400000000"
    "150400000000160800000000000000001704ffffffffff003603"
)
TELEMETRY_V7 = bytes.fromhex(
    "1eff0209073000400a1c109d00bf00001a020000000013ec1a0200000000"
    "ffff000100000000ffff000100000000ffff000100000000"
)
PORT_MODEL_V7 = bytes.fromhex(
    "a5170033000363210001100102110100120108130a00c25a20006400200000140400000000"
    "150400000000160800000000000000001704ffffffffff01f5b9"
)
SET_V7 = bytes.fromhex("a517000600043d93000310001200ff011629")


def test_captured_v7_model_and_port_trailer():
    protocol = Protocol()
    assert protocol.response_frame_length(MODEL_V7) == len(MODEL_V7)
    values = protocol.parse_parameters(MODEL_V7, DeviceInfo(11, "Test", 7), 0)
    assert values[16] == b"\x02" and values[18] == b"\x06"
    with pytest.raises(ValueError, match="port"):
        protocol.parse_parameters(MODEL_V7, DeviceInfo(11, "Test", 7), 1)


def test_captured_v7_ack_validates_written_parameters():
    Protocol().validate_ack(SET_V7, DeviceInfo(11, "Test", 7), 1, {16, 18})


def test_reading_saved_port_level_preserves_observed_output():
    async def exercise():
        controller = make_controller()
        controller._state = DeviceInfo(11, "Test", 7)
        controller._notification_handler(0, bytearray(TELEMETRY_V7))
        controller._send_command = AsyncMock(return_value=PORT_MODEL_V7)
        await controller.update(1)
        port = controller.state.ports[1]
        assert port.level_on == 8 and port.level == 6

    asyncio.run(exercise())


@pytest.mark.parametrize("header", range(32))
def test_apk_header_family_still_requires_crc_and_sequence(header):
    frame = bytearray(response_frame())
    frame[1] = header
    frame[6:8] = crc_hqx(frame[:6], 0xFFFF).to_bytes(2, "big")
    assert Protocol().parse_response(frame, 0x7695, 3) == b"\x10\x00"
    with pytest.raises(ValueError, match="sequence"):
        Protocol().parse_response(frame, 0x7696, 3)
    frame[6] ^= 1
    with pytest.raises(ValueError, match="CRC"):
        Protocol().parse_response(frame, 0x7695, 3)


@pytest.mark.parametrize("header", [32, 127, 255])
def test_headers_outside_apk_family_are_rejected(header):
    frame = bytearray(response_frame())
    frame[1] = header
    frame[6:8] = crc_hqx(frame[:6], 0xFFFF).to_bytes(2, "big")
    with pytest.raises(ValueError, match="header"):
        Protocol().parse_response(frame)


@pytest.mark.parametrize("split", range(2, len(MODEL_V7)))
def test_captured_v7_reply_fragments_interleaved_with_live_telemetry(split):
    async def exercise():
        controller = make_controller()
        controller._state = DeviceInfo(11, "Test", 7)
        controller._notify_future = controller.loop.create_future()
        controller._pending_sequence, controller._pending_command = 2, 1
        controller._notification_handler(0, bytearray(MODEL_V7[:split]))
        controller._notification_handler(0, bytearray(TELEMETRY_V7))
        assert not controller._notify_future.done()
        assert controller.state.ports[1].level == 6
        controller._notification_handler(0, bytearray(MODEL_V7[split:]))
        assert controller._notify_future.result() == MODEL_V7

    asyncio.run(exercise())


# Airtap T4 captures: device type 6, protocol revision 3. The advertisement's
# identity bytes are replaced with a placeholder address.
ADVERTISEMENT_T4 = bytes.fromhex(
    "a4c138000001343533534303060007360000010000000000000000"
)
MODEL_T4 = bytes.fromhex(
    "a513002a000337d5000110010211010512010113070c401242130000140400000000"
    "1504000000001608000000000000000017008439"
)
SET_T4 = bytes.fromhex("a51300040004daf50003100012009e74")


def test_captured_t4_advertisement():
    info = parse_manufacturer_data(ADVERTISEMENT_T4)
    assert (info.type, info.version, info.name) == (6, 3, "D-453SC")
    assert info.tmp == 18.46 and info.fan == 1


def test_captured_t4_model_and_ack():
    protocol = Protocol()
    state = DeviceInfo(6, "Test", 3)
    values = protocol.parse_parameters(MODEL_T4, state, 0)
    assert values[16] == b"\x02" and values[17] == b"\x05" and values[18] == b"\x01"
    output = ParameterCodec(state.profile).decode_output(values, PortState(0))
    assert (output.mode, output.level_on, output.level_off) == (2, 1, 5)
    protocol.validate_ack(SET_T4, state, 0, {16, 18})


@pytest.mark.parametrize(
    "frame,mode,level",
    [
        ("1eff0209030c000007460000000027100012", ControllerMode.ON, 1),
        ("1eff0209030c0000075b0000000027100022", ControllerMode.ON, 2),
        ("1eff0209030c000007670000000027100001", ControllerMode.OFF, 0),
    ],
)
def test_captured_t4_telemetry_reports_root_level(frame, mode, level):
    updated = parse_telemetry(bytes.fromhex(frame), DeviceInfo(6, "Test", 3, fan=9))
    assert (updated.work_type, updated.fan) == (mode, level)
    assert updated.ports == {}
    assert updated.tmp is not None and 18 < updated.tmp < 19


def test_unverified_legacy_models_keep_advertised_root_level():
    frame = bytes.fromhex("1eff0209030c000007460000000027100012")
    assert parse_telemetry(frame, DeviceInfo(1, "Test", 3, fan=9)).fan == 9
