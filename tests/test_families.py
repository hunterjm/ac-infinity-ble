"""APK-derived layouts are synthetic fixtures, not hardware captures."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from bleak.backends.scanner import AdvertisementData

from ac_infinity_ble.capabilities import H_TYPES, HOME_TYPES, MODEL_NAMES, is_h4
from ac_infinity_ble.const import MANUFACTURER_ID
from ac_infinity_ble.models import DeviceInfo, PortState
from ac_infinity_ble.protocol import Protocol, parse_manufacturer_data
from ac_infinity_ble.telemetry import parse_telemetry

from .frames import MODEL_RESPONSE, TELEMETRY, response_frame
from .test_device import make_controller


def advertisement(device_type=11, version=3):
    data = bytearray(27)
    data[6:11] = b"ABCDE"
    data[11:13] = bytes((version, device_type))
    data[14:18] = bytes.fromhex("0929193c")
    return data


@pytest.mark.parametrize("model", MODEL_NAMES)
def test_known_model_identity(model):
    state = parse_manufacturer_data(advertisement(model))
    assert state.type == model
    assert state.version == 3


@pytest.mark.parametrize("length", [0, 1, 12, 13, 16, 18, 26, 28])
def test_advertisement_rejects_bad_length(length):
    with pytest.raises(ValueError):
        parse_manufacturer_data(bytes(length))


def test_unknown_or_invalid_name_rejected():
    with pytest.raises(ValueError):
        parse_manufacturer_data(advertisement(99))
    data = advertisement()
    data[6] = 255
    with pytest.raises(ValueError):
        parse_manufacturer_data(data)


def test_short_advertisement_does_not_invent_measurements():
    state = parse_manufacturer_data(advertisement()[:17])
    assert state.tmp is None and state.hum is None and state.fan is None


@pytest.mark.parametrize("model", [64, 99])
def test_unverified_models_reject_discovery_and_commands(model):
    with pytest.raises(ValueError):
        parse_manufacturer_data(advertisement(model, 6))
    state = DeviceInfo(model, "Test", 6)
    with pytest.raises(ValueError):
        Protocol().get_parameters(state, 0, [16, 17, 18], 1)
    with pytest.raises(ValueError):
        Protocol().set_parameters(state, 0, {16: b"\x02"}, 1)
    with pytest.raises(ValueError):
        Protocol().get_model_data(model, 0, 1)
    with pytest.raises(ValueError):
        Protocol().set_level(model, 2, 5, 0, 1)


def test_bad_subscriber_cannot_corrupt_state_or_stop_other_callbacks():
    async def exercise():
        controller = make_controller()

        def bad_callback(state, kind):
            state.tmp = 999
            raise RuntimeError("subscriber failed")

        good = Mock()
        controller.register_callback(bad_callback)
        controller.register_callback(good)
        controller._notification_handler(0, bytearray(TELEMETRY))
        assert controller.temperature == 23.98
        good.assert_called_once()
        assert good.call_args.args[0].tmp == 23.98

    asyncio.run(exercise())


@pytest.mark.parametrize("model", [4, 5, 14, 15, 24, 25])
def test_cloudcom_signed_magnitude_temperature_and_missing_reading(model):
    data = advertisement(model, 4)
    start = 15 if model in (24, 25) else 14
    data[start : start + 3] = bytes.fromhex("87bfff")
    state = parse_manufacturer_data(data)
    assert state.tmp == -12.3
    assert state.hum is None
    assert state.fan is None


@pytest.mark.parametrize(
    "device_type,version", [(7, 3), (11, 6), (20, 19), (20, 20), (51, 1)]
)
def test_parameter_lengths_and_port_for_each_controller_generation(
    device_type, version
):
    state = DeviceInfo(device_type, "Test", version)
    command = Protocol().set_parameters(state, 2, {16: b"\x02", 18: b"\x05"}, 0xFEDC)
    payload = bytes.fromhex(
        "1000010212000105ff02" if is_h4(device_type, version) else "100102120105ff02"
    )
    assert command[10:-2] == payload
    assert command[4:6] == b"\xfe\xdc"
    reply = response_frame(payload, sequence=0xFEDC, command=1)
    assert Protocol().parse_parameters(reply, state, 2) == {16: b"\x02", 18: b"\x05"}


@pytest.mark.parametrize(
    "payload", [b"", b"\x10", b"\x10\x02\x01", b"\x10\x01\x01\x10\x01\x02"]
)
def test_truncated_duplicate_or_empty_model_cannot_change_state(payload):
    async def exercise():
        controller = make_controller()
        before = controller.state
        controller._send_command = AsyncMock(
            return_value=response_frame(payload + b"\xff\x00", command=1)
        )
        with pytest.raises(ValueError):
            await controller.update()
        assert controller.state == before

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "payload",
    [b"\x10\x01\xff\x00", b"\x10\x00\xff\x01", b"\x12\x00\xff\x00", b"\xff\x00"],
)
def test_negative_wrong_port_and_incomplete_ack_do_not_publish(payload):
    async def exercise():
        controller = make_controller()
        before = controller.state
        callback = Mock()
        controller.register_callback(callback)
        controller._send_command = AsyncMock(
            return_value=response_frame(payload, command=3)
        )
        with pytest.raises(ValueError):
            await controller.turn_off()
        assert controller.state == before
        callback.assert_not_called()

    asyncio.run(exercise())


def test_off_preserves_presets_and_success_publishes_once():
    async def exercise():
        controller = make_controller()
        controller._state = replace(controller.state, level_on=8, level_off=3)
        callback = Mock()
        controller.register_callback(callback)
        controller._send_command = AsyncMock(
            return_value=response_frame(b"\x10\x00\xff\x00", command=3)
        )
        await controller.turn_off()
        assert (
            controller._send_command.call_args.args[0][10:-2] == b"\x10\x01\x01\xff\x00"
        )
        assert controller.state.level_on == 8 and controller.state.level_off == 3
        callback.assert_called_once()

    asyncio.run(exercise())


@pytest.mark.parametrize("level", [-1, 11, True, 2.5])
def test_invalid_output_never_connects(level):
    async def exercise():
        controller = make_controller()
        controller._send_command = AsyncMock()
        with pytest.raises(ValueError):
            await controller.set_output(0, on=True, level=level)
        controller._send_command.assert_not_awaited()

    asyncio.run(exercise())


def test_advertisement_zero_and_fault_clear_readings_but_preserve_presets():
    async def exercise():
        controller = make_controller()
        controller._state = replace(controller.state, level_on=8, level_off=0)
        data = advertisement(7)
        data[14:18] = b"\x80\x00\x00\x00"
        adv = AdvertisementData(
            None, {MANUFACTURER_ID: bytes(data)}, {}, [], None, -60, ()
        )
        controller.set_ble_device_and_advertisement_data(controller._ble_device, adv)
        assert controller.temperature is None
        assert controller.humidity == 0
        assert controller.state.level_on == 8 and controller.state.level_off == 0
        snapshot = controller.state
        with pytest.raises(TypeError):
            snapshot.ports[99] = PortState(99)
        assert 99 not in controller.state.ports

    asyncio.run(exercise())


def test_legacy_telemetry_recognizes_physical_fan_and_disconnected_ports():
    state = parse_telemetry(TELEMETRY, DeviceInfo(11, "Test", 3))
    assert state.ports[1].kind == "fan"
    assert state.ports[1].level == 1
    assert not state.ports[2].connected


@pytest.mark.parametrize(
    "kind,wire",
    [
        ("light", 1),
        ("humidifier", 2),
        ("dehumidifier", 3),
        ("heater", 4),
        ("air_conditioner", 5),
        ("fan", 7),
        ("switch", 128),
    ],
)
def test_v6_loads_and_level_bits(kind, wire):
    data = bytearray(30)
    data[:6] = b"\x1e\xff\x02\x09\x00\x18"
    data[22:30] = bytes((0, 1, 24, 2, 0, 0, 0, wire))
    state = parse_telemetry(data, DeviceInfo(11, "Test", 6))
    assert state.ports[1].kind == kind
    assert state.ports[1].level == 6


@pytest.mark.parametrize("model", H_TYPES)
@pytest.mark.parametrize("version", [19, 20])
def test_h_and_h4_port_record_offsets(model, version):
    extended = is_h4(model, version)
    if extended:
        data = bytearray(37)
        data[:7] = b"\x1e\xff\x02\x09\x00\x00\x1e"
        data[12] = 1
        data[15:26] = bytes((2, 0, 1, 8, 1, 2, 24, 2, 0, 0, 0))
        port_id = 2
    else:
        data = bytearray(24)
        data[:6] = b"\x1e\xff\x02\x09\x00\x12"
        data[7] = 1
        data[8] = 16
        data[10:20] = bytes((0, 1, 8, 1, 2, 24, 2, 0, 0, 0))
        port_id = 0
    state = parse_telemetry(data, DeviceInfo(model, "Test", version))
    assert state.ports[port_id].kind == "humidifier"
    assert state.ports[port_id].level == 6
    with pytest.raises(ValueError):
        parse_telemetry(data[:-1], DeviceInfo(model, "Test", version))


@pytest.mark.parametrize("model", HOME_TYPES)
def test_home_appliances_use_dedicated_power_command(model):
    async def exercise():
        controller = make_controller()
        controller._state = DeviceInfo(model, "Test", 6)
        controller._send_command = AsyncMock(
            return_value=response_frame(b"\x16\x00\xff\x00", command=3)
        )
        await controller.turn_off()
        assert (
            controller._send_command.call_args.args[0][10:-2] == b"\x16\x01\x00\xff\x00"
        )
        assert controller.state.ports[0].power is False

    asyncio.run(exercise())


@pytest.mark.parametrize("split", range(2, len(TELEMETRY)))
def test_fragmented_telemetry_does_not_complete_pending_command(split):
    async def exercise():
        controller = make_controller()
        controller._notify_future = controller.loop.create_future()
        controller._pending_sequence, controller._pending_command = 0x1A66, 1
        controller._notification_handler(0, bytearray(TELEMETRY[:split]))
        controller._notification_handler(0, bytearray(TELEMETRY[split:]))
        assert controller.temperature == 23.98
        assert not controller._notify_future.done()
        controller._notification_handler(0, bytearray(MODEL_RESPONSE))
        assert controller._notify_future.result() == MODEL_RESPONSE

    asyncio.run(exercise())
