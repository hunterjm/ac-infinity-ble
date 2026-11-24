"""Cloudcom 6 (type 64) captures: software 1.0.49, protocol revision 20.

Captured from a CLOUDLINE T8 fan on port 1, with and without an external sensor
probe. Address and advertised name bytes are replaced; everything else is as sent.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from bleak.backends.scanner import AdvertisementData

from ac_infinity_ble.const import MANUFACTURER_ID
from ac_infinity_ble.exceptions import FrameValidationError
from ac_infinity_ble.models import DeviceInfo
from ac_infinity_ble.modes import ControllerMode
from ac_infinity_ble.protocol import Protocol, parse_manufacturer_data
from ac_infinity_ble.routing import TelemetryLayout
from ac_infinity_ble.telemetry import parse_telemetry

from .test_device import make_controller

# Bytes 0-5 (address) and 6-10 (name) replaced; 11 = revision 20, 12 = type 64.
ADVERTISEMENT = bytes(6) + b"ABCDE" + bytes.fromhex("14408000000000000000000006000000")
# The same payload through an ESPHome Bluetooth proxy (Home Assistant): 24 bytes.
ADVERTISEMENT_PROXY = ADVERTISEMENT[:24]
# Port 1 ON at speed 2: built-in sensor 71.30 °F, 67.00 %, 0.86 kPa.
TELEMETRY_SPEED_2 = bytes.fromhex(
    "1eff020914002f1080010002010100000000ffff00000001f000000113d9ffff000a0002f0"
    "000000000300601bda02601a2c03600056"
)
# Port 1 OFF from the controller's screen: 72.80 °F, 64.50 %, 0.98 kPa.
TELEMETRY_OFF = bytes.fromhex(
    "1eff020914002f1080010002010100000000ffff00000001f000000113d9ffff00000001f0"
    "000000000300601c700260193203600062"
)
# Port 1 ON at speed 10.
TELEMETRY_SPEED_10 = bytes.fromhex(
    "1eff020914002f1080010002010100000000ffff00000001f000000113d9ffff002a0002f0"
    "000000000300601c7a0260193203600062"
)
# Probe plugged in: a second sensor group (06 01 03) reads 77.90 °F, 50.30 %.
TELEMETRY_PROBE = bytes.fromhex(
    "1eff020914003e1080010002020100000000ffff00000001f000000113d9ffff000e8002f0"
    "000000000300601c65026018a60360006506010300601e6e026013a6036000a2"
)
# GET replies carry 0x14 (the revision) in byte 8, ahead of command 01.
GET_PORT_0 = bytes.fromhex(
    "a51f003100012f2e140110000101110001001200011a1300001400040000000015000400"
    "000000160008000000000000000017000400000000ff0089ce"
)
GET_PORT_1_SPEED_3 = bytes.fromhex(
    "a51f003100012f2e14011000010211000100120001331300001400040000000015000400"
    "000000160008000000000000000017000400000000ff0180bc"
)
# The library-built request that moved port 1 from speed 3 to 2, and its ACK.
SET_PORT_1_SPEED_2 = bytes.fromhex("a500000a00024bba00031000010212000123ff01649d")
ACK_PORT_1 = bytes.fromhex("a51f000600025f78140310001200ff0124f0")

STATE = DeviceInfo(64, "Test", 20)


def fahrenheit(value):
    return pytest.approx((value - 32) / 1.8)


def test_profile_and_identity_only_advertisement():
    profile = STATE.profile
    assert profile.telemetry == TelemetryLayout.CLOUDCOM6
    assert profile.addressed and profile.packed_manual_level
    assert profile.parameter_length_size == 2
    assert profile.reply_command_prefix == 20
    assert not profile.advertised_climate
    state = parse_manufacturer_data(ADVERTISEMENT)
    assert (state.type, state.version) == (64, 20)
    # Same form as "Model Code" on the controller's info screen.
    assert state.name == "K-ABCDE"
    # The captured payload has zeros where other models put climate readings.
    assert state.tmp is None and state.hum is None and state.fan is None
    assert parse_manufacturer_data(ADVERTISEMENT_PROXY) == state


def test_24_byte_advertisements_are_only_for_identity_only_models():
    other = bytearray(ADVERTISEMENT_PROXY)
    other[11:13] = bytes((7, 11))  # Controller 69 Pro identity, proxy length
    with pytest.raises(ValueError):
        parse_manufacturer_data(bytes(other))


@pytest.mark.parametrize(
    ("frame", "level", "mode", "temperature", "humidity", "vpd"),
    [
        (TELEMETRY_SPEED_2, 2, ControllerMode.ON, 71.30, 67.00, 0.86),
        (TELEMETRY_OFF, 0, ControllerMode.OFF, 72.80, 64.50, 0.98),
        (TELEMETRY_SPEED_10, 10, ControllerMode.ON, 72.90, 64.50, 0.98),
        (TELEMETRY_PROBE, 3, ControllerMode.ON, 72.69, 63.10, 1.01),
    ],
)
def test_captured_telemetry(frame, level, mode, temperature, humidity, vpd):
    state = parse_telemetry(frame, STATE)
    assert set(state.ports) == {0, 1}
    fan = state.ports[1]
    assert fan.connected and fan.kind == "fan" and fan.raw_type == 0x13D9
    assert (fan.level, fan.mode) == (level, mode)
    # Port 0 is the controller-wide channel, which stays OFF in these captures.
    assert (state.fan, state.work_type) == (0, ControllerMode.OFF)
    assert state.choose_port == 1
    assert state.tmp == fahrenheit(temperature)
    assert state.hum == pytest.approx(humidity)
    assert state.vpd == pytest.approx(vpd)
    assert [port.id for port in state.outputs] == [1]


def test_probe_group_does_not_replace_the_controller_sensor():
    state = parse_telemetry(TELEMETRY_PROBE, STATE)
    assert state.tmp == fahrenheit(72.69)
    assert state.tmp != fahrenheit(77.90)


@pytest.mark.parametrize(
    ("offset", "value"),
    [(11, 3), (12, 1), (12, 3), (41, 2), (56, 5)],
    ids=[
        "extra-port",
        "missing-group",
        "extra-group",
        "short-sensor-group",
        "long-probe-group",
    ],
)
def test_miscounted_telemetry_is_rejected(offset, value):
    frame = bytearray(TELEMETRY_PROBE)
    frame[offset] = value
    with pytest.raises(ValueError):
        parse_telemetry(frame, STATE)


def test_captured_get_replies_decode_packed_levels():
    protocol = Protocol()
    root = protocol.parse_parameters(GET_PORT_0, STATE, 0)
    assert root[16] == b"\x01"
    values = protocol.parse_parameters(GET_PORT_1_SPEED_3, STATE, 1)
    assert values[16] == b"\x02" and values[18] == b"\x33"
    with pytest.raises(ValueError, match="port"):
        protocol.parse_parameters(GET_PORT_1_SPEED_3, STATE, 0)


def test_reply_revision_byte_is_required_and_scoped():
    protocol = Protocol()
    assert protocol.parse_response(GET_PORT_0, 1, 1, 20)
    with pytest.raises(FrameValidationError, match="command"):
        protocol.parse_response(GET_PORT_0, 1, 1)
    with pytest.raises(FrameValidationError, match="command"):
        protocol.parse_response(GET_PORT_0, 1, 1, 7)


def test_set_request_matches_capture_and_ack_validates():
    protocol = Protocol()
    request = protocol.set_parameters(STATE, 1, {16: b"\x02", 18: b"\x23"}, 2)
    assert request == SET_PORT_1_SPEED_2
    protocol.validate_ack(ACK_PORT_1, STATE, 1, {16, 18})


def test_set_speed_preserves_the_packed_maximum():
    async def exercise():
        controller = make_controller()
        controller._state = STATE
        controller._notification_handler(0, bytearray(TELEMETRY_SPEED_2))
        controller._send_command = AsyncMock(
            side_effect=[GET_PORT_1_SPEED_3, ACK_PORT_1]
        )
        await controller.set_output(1, on=True, level=2)
        sent = controller._send_command.await_args_list[1].args[0]
        assert sent[9] == 3 and sent[10:-2] == SET_PORT_1_SPEED_2[10:-2]
        port = controller.state.ports[1]
        assert port.level_on == 2 and port.raw_on_parameter == 0x23

    asyncio.run(exercise())


def test_advertisements_do_not_clear_telemetry_climate():
    async def exercise():
        controller = make_controller()
        controller._state = STATE
        controller._notification_handler(0, bytearray(TELEMETRY_SPEED_2))
        adv = AdvertisementData(
            None, {MANUFACTURER_ID: ADVERTISEMENT}, {}, [], None, -73, ()
        )
        controller.set_ble_device_and_advertisement_data(controller._ble_device, adv)
        assert controller.temperature == fahrenheit(71.30)
        assert controller.humidity == pytest.approx(67.00)
        assert controller.state.ports[1].level == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("split", range(2, len(GET_PORT_1_SPEED_3)))
def test_reply_fragments_interleaved_with_live_telemetry(split):
    async def exercise():
        controller = make_controller()
        controller._state = STATE
        controller._notify_future = controller.loop.create_future()
        controller._pending_sequence, controller._pending_command = 1, 1
        controller._notification_handler(0, bytearray(GET_PORT_1_SPEED_3[:split]))
        controller._notification_handler(0, bytearray(TELEMETRY_SPEED_10))
        assert not controller._notify_future.done()
        assert controller.state.ports[1].level == 10
        controller._notification_handler(0, bytearray(GET_PORT_1_SPEED_3[split:]))
        assert controller._notify_future.result() == GET_PORT_1_SPEED_3

    asyncio.run(exercise())
