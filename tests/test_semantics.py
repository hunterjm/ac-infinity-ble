"""APK unit preferences, modes and packed setting boundaries."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from ac_infinity_ble import ControllerMode, DeviceInfo, PortState
from ac_infinity_ble.commands import ParameterCodec
from ac_infinity_ble.exceptions import (
    CommandRejectedError,
    FrameValidationError,
    ParameterValidationError,
)
from ac_infinity_ble.protocol import Protocol
from ac_infinity_ble.telemetry import parse_telemetry

from .frames import TELEMETRY, response_frame
from .test_device import make_controller


@pytest.mark.parametrize(
    "model,version,packed",
    [
        (11, 7, False),
        (11, 8, True),
        (20, 19, True),
        (20, 20, True),
        (51, 1, True),
        (39, 8, False),
    ],
)
def test_manual_level_preserves_saved_maximum(model, version, packed):
    codec = ParameterCodec(DeviceInfo(model, "Test", version).profile)
    values = {16: b"\x02", 17: b"\x03", 18: b"\x68" if packed else b"\x06"}
    state = codec.decode_output(values, PortState(1, level=4))
    assert state.level_on == 6
    assert state.level == 4
    encoded = codec.output_values(True, 5, state)
    assert encoded[18] == (b"\x58" if packed else b"\x05")
    assert 17 not in encoded
    assert codec.confirmed_output(state, True, 5).raw_on_parameter == encoded[18][0]
    assert 18 not in codec.output_values(False, None, state)


def test_packed_speed_read_modify_write_and_bad_get_prevent_write():
    async def exercise():
        c = make_controller()
        c._state = DeviceInfo(
            11, "Test", 8, ports={1: PortState(1, kind="fan", level=4)}
        )
        c._send_command = AsyncMock(
            side_effect=[
                response_frame(
                    b"\x10\x01\x02\x11\x01\x00\x12\x01\x68\xff\x01", command=1
                ),
                response_frame(b"\x10\x00\x12\x00\xff\x01"),
            ]
        )
        await c.set_output(1, on=True, level=5)
        command = c._send_command.await_args_list[1].args[0]
        assert command[10:-2] == b"\x10\x01\x02\x12\x01\x58\xff\x01"
        c._send_command = AsyncMock(return_value=response_frame(b"\x10", command=1))
        with pytest.raises(ParameterValidationError):
            await c.set_output(1, on=True, level=3)
        assert c._send_command.await_count == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("fahrenheit", [False, True])
def test_unit_preference_and_nonzero_trend_flags_do_not_change_readings(fahrenheit):
    frame = bytearray(TELEMETRY)
    frame[6] = (128 if fahrenheit else 0) | 0x4C
    state = parse_telemetry(frame, DeviceInfo(11, "Test", 3))
    assert state.is_degree is not fahrenheit
    assert (state.tmp, state.hum, state.vpd) == (23.98, 67.33, 0.92)
    frame[8:10] = b"\x80\x00"
    assert parse_telemetry(frame, state).tmp is None


@pytest.mark.parametrize(
    "mode,on",
    [
        (ControllerMode.OFF, False),
        (ControllerMode.ON, True),
        (ControllerMode.AUTO, True),
        (ControllerMode.VPD, True),
    ],
)
def test_operating_mode_is_distinct_from_actual_speed(mode, on):
    async def exercise():
        c = make_controller()
        for speed in (0, 3):
            c._state = replace(c.state, work_type=mode, fan=speed, level_off=3)
            assert c.is_on is on
            assert c.speed == speed
        codec = ParameterCodec(c.state.profile)
        assert codec.output_values(False, None) == {16: b"\x01"}

    asyncio.run(exercise())


def test_protocol_errors_are_specific_and_value_error_compatible():
    with pytest.raises(FrameValidationError) as exc:
        Protocol().parse_response(b"bad")
    assert isinstance(exc.value, ValueError)
    with pytest.raises(CommandRejectedError):
        ParameterCodec(DeviceInfo(11, "Test", 7).profile).validate_ack(
            b"\x10\x01\xff\x00", 0, {16}
        )
