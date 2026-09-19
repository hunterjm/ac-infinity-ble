"""Family dispatch boundaries and independent multiport transactions."""

import asyncio
from binascii import crc_hqx
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from ac_infinity_ble import DeviceInfo, PortState, resolve_profile
from ac_infinity_ble.protocol import Protocol
from ac_infinity_ble.routing import TelemetryLayout
from ac_infinity_ble.telemetry import parse_telemetry

from .frames import response_frame
from .test_device import make_controller


@pytest.mark.parametrize(
    "model,version,layout,width,addressed,root",
    [
        (1, 3, "legacy", 1, False, None),
        (11, 4, "legacy", 1, True, None),
        (11, 5, "v6", 1, True, None),
        (14, 4, "sensor", 1, False, None),
        (20, 19, "h", 1, True, None),
        (20, 20, "h4", 2, True, None),
        (51, 1, "h4", 2, True, None),
        (39, 6, "home", 1, True, "air_conditioner"),
        (49, 6, "home", 1, True, "fan"),
        (21, 19, "h", 1, True, "switch"),
    ],
)
def test_apk_routing_boundaries(model, version, layout, width, addressed, root):
    profile = resolve_profile(model, version)
    assert profile.telemetry == TelemetryLayout(layout)
    assert profile.parameter_length_size == width
    assert profile.addressed == addressed
    assert profile.root_kind == root


def test_high_command_byte_is_part_of_command_matching():
    frame = bytearray(response_frame())
    frame[8] = 1
    frame[-2:] = crc_hqx(frame[8:-2], 0xFFFF).to_bytes(2, "big")
    with pytest.raises(ValueError, match="command"):
        Protocol().parse_response(frame, expected_command=3)


def two_fan_telemetry():
    frame = bytearray(38)
    frame[:6] = bytes.fromhex("1eff02090020")
    frame[22:30] = bytes((0, 1, 12, 2, 0, 0, 0, 6))
    frame[30:38] = bytes((0, 1, 28, 2, 0, 0, 0, 7))
    return frame


def test_two_fans_have_independent_state_and_serialized_commands():
    async def exercise():
        controller = make_controller()
        controller._state = parse_telemetry(
            two_fan_telemetry(), DeviceInfo(11, "Test", 6)
        )
        assert [(p.id, p.level) for p in controller.state.outputs] == [(1, 3), (2, 7)]
        in_write = False
        ports_written = []

        async def write(characteristic, command, response):
            nonlocal in_write
            assert not in_write
            in_write = True
            port = command[-3]
            ports_written.append(port)
            await asyncio.sleep(0)
            # Independent telemetry must not complete either command.
            controller._notification_handler(0, two_fan_telemetry())
            assert not controller._notify_future.done()
            controller._notification_handler(
                0,
                bytearray(
                    response_frame(
                        bytes((16, 0, 18, 0, 255, port)),
                        sequence=int.from_bytes(command[4:6], "big"),
                    )
                ),
            )
            in_write = False

        controller._client.write_gatt_char = AsyncMock(side_effect=write)
        await asyncio.gather(
            controller.set_output(1, on=True, level=5),
            controller.set_output(2, on=True, level=8),
        )
        assert ports_written == [1, 2]
        assert controller.state.ports[1].level_on == 5
        assert controller.state.ports[2].level_on == 8
        assert controller.state.ports[2].level == 8
        # The intervening telemetry is authoritative for actual output level.
        assert controller.state.ports[1].level == 3
        await controller.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("model,version", [(11, 6), (20, 19), (20, 20), (51, 1)])
def test_other_ports_ack_cannot_publish_state(model, version):
    async def exercise():
        controller = make_controller()
        controller._state = DeviceInfo(
            model,
            "Test",
            version,
            ports={
                1: PortState(1, kind="fan", level=3),
                2: PortState(2, kind="fan", level=7),
            },
        )
        before = controller.state
        controller.update = AsyncMock()
        if controller.state.profile.packed_manual_level:
            controller._state = replace(
                controller.state,
                ports={
                    **controller.state.ports,
                    1: replace(controller.state.ports[1], raw_on_parameter=0x38),
                },
            )
            before = controller.state
        controller._send_command = AsyncMock(
            return_value=response_frame(b"\x10\x00\x12\x00\xff\x02")
        )
        with pytest.raises(ValueError, match="port"):
            await controller.set_output(1, on=True, level=5)
        assert controller.state == before

    asyncio.run(exercise())


def test_endpoint_policy_does_not_invent_ports_or_sensor_controls():
    state = DeviceInfo(
        11,
        "Test",
        6,
        ports={
            0: PortState(0, kind="fan"),
            1: PortState(1, kind="fan"),
            2: PortState(2, kind="light", connected=False),
        },
    )
    assert [p.id for p in state.outputs] == [1]
    assert replace(state, type=14).outputs == ()
    assert replace(state, type=1).outputs == ()
    assert (
        replace(state, type=39, ports={0: PortState(0)}).outputs[0].kind
        == "air_conditioner"
    )


def test_h4_keeps_sparse_port_ids_for_multiple_fans():
    frame = bytearray(48)
    frame[:7] = bytes.fromhex("1eff0209000029")
    frame[12] = 2
    frame[15:26] = bytes((2, 0, 1, 4, 0, 6, 12, 2, 0, 0, 0))
    frame[26:37] = bytes((7, 0, 1, 4, 0, 7, 28, 2, 0, 0, 0))
    state = parse_telemetry(frame, DeviceInfo(51, "Test", 1))
    assert [(port.id, port.level) for port in state.outputs] == [(2, 3), (7, 7)]
    command = Protocol().set_parameters(state, 7, {18: b"\x05"}, 1)
    assert command[10:-2] == b"\x12\x00\x01\x05\xff\x07"


def test_telemetry_does_not_transfer_presets_between_replaced_loads():
    previous = PortState(1, kind="fan", level_on=8, level_off=2)
    assert PortState(1, kind="fan").with_saved_levels(previous).level_on == 8
    assert PortState(1, kind="light").with_saved_levels(previous).level_on is None
    assert (
        PortState(1, kind="fan", connected=False).with_saved_levels(previous).level_on
        is None
    )


@pytest.mark.parametrize("raw_type", [1, 2])
def test_telemetry_retains_settings_only_for_unchanged_load_identity(raw_type):
    async def exercise():
        controller = make_controller()
        controller._state = parse_telemetry(
            two_fan_telemetry(), DeviceInfo(11, "Test", 8)
        )
        ports = dict(controller.state.ports)
        ports[1] = replace(ports[1], level_on=8, level_off=2, raw_on_parameter=0x89)
        controller._state = replace(controller.state, ports=ports)
        frame = two_fan_telemetry()
        frame[22:24] = raw_type.to_bytes(2, "big")
        frame[24] = 4 << 2

        controller._notification_handler(0, frame)

        current = controller.state.ports[1]
        assert current.kind == "fan"
        assert current.raw_type == raw_type
        assert current.level == 4
        assert (current.level_on, current.level_off, current.raw_on_parameter) == (
            (8, 2, 0x89) if raw_type == 1 else (None, None, None)
        )
        assert controller.state.ports[2] == ports[2]

    asyncio.run(exercise())


@pytest.mark.parametrize("version", [6, 8])
@pytest.mark.parametrize(
    "replacement",
    [
        None,
        PortState(1, connected=False, kind="fan"),
        PortState(1, kind="light"),
        PortState(1, kind="fan", raw_type=2, level=2),
    ],
)
def test_command_ack_cannot_restore_a_removed_or_changed_load(replacement, version):
    async def exercise():
        controller = make_controller()
        controller._state = DeviceInfo(
            11,
            "Test",
            version,
            ports={1: PortState(1, kind="fan", raw_type=1, level=3)},
        )

        async def send(command):
            if command[8:10] == b"\x00\x01":
                return response_frame(
                    b"\x10\x01\x02\x11\x01\x02\x12\x01\x89\xff\x01", command=1
                )
            controller._state = replace(
                controller._state, ports={} if replacement is None else {1: replacement}
            )
            return response_frame(b"\x10\x00\x12\x00\xff\x01")

        controller._send_command = AsyncMock(side_effect=send)
        with pytest.raises(ValueError, match="Port changed"):
            await controller.set_output(1, on=True, level=5)
        assert controller.state.ports == (
            {} if replacement is None else {1: replacement}
        )

    asyncio.run(exercise())
