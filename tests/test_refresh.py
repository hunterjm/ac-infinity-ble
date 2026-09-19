"""Telemetry-only polling must not write settings or leak connections."""

import asyncio
from dataclasses import FrozenInstanceError, asdict

import pytest

from ac_infinity_ble import DeviceInfo, PortState
from ac_infinity_ble import device as device_module

from .frames import TELEMETRY
from .test_connection import make_client, prepare_connection


@pytest.mark.parametrize("timing", ["subscribe", "later"])
def test_refresh_accepts_fresh_telemetry_without_command(monkeypatch, timing):
    async def exercise():
        client = make_client()
        controller, _ = prepare_connection(monkeypatch, client)

        async def subscribe(*args):
            if timing == "subscribe":
                controller._notification_handler(0, bytearray(TELEMETRY))
            else:
                controller.loop.call_soon(
                    controller._notification_handler, 0, bytearray(TELEMETRY)
                )

        client.start_notify.side_effect = subscribe
        await controller.refresh_telemetry()
        assert controller.temperature == 23.98
        client.write_gatt_char.assert_not_awaited()
        assert controller._disconnect_timer is not None
        await controller.stop()
        client.disconnect.assert_awaited_once()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [False, True])
def test_refresh_timeout_and_cancellation_release_session(monkeypatch, cancel):
    async def exercise():
        client = make_client()
        controller, _ = prepare_connection(monkeypatch, client)
        monkeypatch.setattr(device_module, "RESPONSE_TIMEOUT", 0.01)
        task = asyncio.create_task(controller.refresh_telemetry())
        if cancel:
            await asyncio.sleep(0)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
        assert controller._client is None
        client.disconnect.assert_awaited_once()
        assert not controller._operation_lock.locked()
        await controller.stop()

    asyncio.run(exercise())


def test_sensor_refresh_never_connects(monkeypatch):
    async def exercise():
        controller, establish = prepare_connection(monkeypatch, make_client())
        controller._state = DeviceInfo(14, "Test", 4)
        await controller.refresh_telemetry()
        establish.assert_not_awaited()

    asyncio.run(exercise())


def test_state_snapshot_owns_immutable_ports_and_supports_migration_serialization():
    ports = {1: PortState(1, kind="fan")}
    snapshot = DeviceInfo(11, "Test", 7, ports=ports)
    ports.clear()
    assert snapshot.ports[1].kind == "fan"
    with pytest.raises(FrozenInstanceError):
        snapshot.tmp = 1  # type: ignore[misc]
    with pytest.raises(TypeError):
        snapshot.ports[1] = PortState(1)  # type: ignore[index]
    assert asdict(snapshot)["type"] == 11
    assert asdict(snapshot)["ports"][1]["kind"] == "fan"
