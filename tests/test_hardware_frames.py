"""Controller 69 Pro captures: software 3.2.56, protocol revision 7."""

import asyncio
from binascii import crc_hqx
from unittest.mock import AsyncMock

import pytest

from ac_infinity_ble.models import DeviceInfo
from ac_infinity_ble.protocol import Protocol

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
