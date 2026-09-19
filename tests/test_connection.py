import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from bleak.exc import BleakError

from ac_infinity_ble import device
from ac_infinity_ble.const import (
    POSSIBLE_READ_CHARACTERISTIC_UUIDS,
    POSSIBLE_WRITE_CHARACTERISTIC_UUIDS,
)
from ac_infinity_ble.exceptions import CharacteristicMissingError

from .frames import SET_RESPONSE
from .test_device import assert_request_cleared, make_controller


def make_client():
    read_char, write_char = Mock(), Mock()
    characteristics = {
        POSSIBLE_READ_CHARACTERISTIC_UUIDS[0]: read_char,
        POSSIBLE_WRITE_CHARACTERISTIC_UUIDS[0]: write_char,
    }
    services = Mock(get_characteristic=Mock(side_effect=characteristics.get))
    client = Mock(
        is_connected=True,
        services=services,
        start_notify=AsyncMock(),
        stop_notify=AsyncMock(),
        get_services=AsyncMock(return_value=services),
        write_gatt_char=AsyncMock(),
        clear_cache=AsyncMock(return_value=False),
    )

    async def disconnect():
        client.is_connected = False

    client.disconnect = AsyncMock(side_effect=disconnect)
    return client


def test_characteristics_must_be_a_matching_uuid_pair():
    async def exercise():
        controller = make_controller()
        chars = {
            POSSIBLE_READ_CHARACTERISTIC_UUIDS[0]: Mock(),
            POSSIBLE_WRITE_CHARACTERISTIC_UUIDS[1]: Mock(),
        }
        services = Mock(get_characteristic=Mock(side_effect=chars.get))
        assert not controller._resolve_characteristics(services)
        chars[POSSIBLE_READ_CHARACTERISTIC_UUIDS[1]] = Mock()
        assert controller._resolve_characteristics(services)
        assert controller._read_char is chars[POSSIBLE_READ_CHARACTERISTIC_UUIDS[1]]
        assert controller._write_char is chars[POSSIBLE_WRITE_CHARACTERISTIC_UUIDS[1]]

    asyncio.run(exercise())


def test_provider_refreshes_adapter_routing_on_each_connection(monkeypatch):
    async def exercise():
        first, second = make_client(), make_client()
        controller, establish = prepare_connection(monkeypatch, first, second)
        first_route, second_route = Mock(), Mock()
        controller._ble_device_provider = Mock(side_effect=[first_route, second_route])
        await controller._ensure_connected()
        await controller._execute_disconnect()
        await controller._ensure_connected()
        assert establish.await_args_list[0].args[1] is first_route
        assert establish.await_args_list[1].args[1] is second_route
        assert "ble_device_callback" not in establish.await_args_list[1].kwargs
        await controller.stop()

    asyncio.run(exercise())


def test_idle_timer_releases_connection_without_another_command(monkeypatch):
    async def exercise():
        client = make_client()
        controller, _ = prepare_connection(monkeypatch, client)
        controller._disconnect_delay = 0.01
        await controller._ensure_connected()
        await asyncio.sleep(0.03)
        assert not client.is_connected
        client.disconnect.assert_awaited_once()
        await controller.stop()
        assert controller._disconnect_task is None

    asyncio.run(exercise())


def prepare_connection(monkeypatch, *clients):
    controller = make_controller()
    controller._client = None
    establish = AsyncMock(side_effect=clients)
    monkeypatch.setattr(device, "establish_connection", establish)
    return controller, establish


def reply_on_write(controller, client):
    async def write(*args):
        controller._notification_handler(0, bytearray(SET_RESPONSE))

    client.write_gatt_char.side_effect = write


@pytest.mark.parametrize("failure", [BleakError("failed"), EOFError(), TimeoutError()])
def test_retry_reconnects_and_subscribes_before_writing(monkeypatch, failure):
    async def exercise():
        first, second = make_client(), make_client()
        controller, establish = prepare_connection(monkeypatch, first, second)
        first.write_gatt_char.side_effect = failure
        reply_on_write(controller, second)
        command = controller._protocol.set_level(7, 2, 5, 0, 0x7695)
        assert await controller._send_command(command) == SET_RESPONSE
        assert establish.await_count == 2
        first.stop_notify.assert_awaited_once()
        first.disconnect.assert_awaited_once()
        second.start_notify.assert_awaited_once()
        second.disconnect.assert_not_awaited()
        assert_request_cleared(controller)
        await controller.stop()

    asyncio.run(exercise())


def test_stopped_controller_cannot_reopen_connection(monkeypatch):
    async def exercise():
        client = make_client()
        controller, establish = prepare_connection(monkeypatch, client)
        await controller.stop()
        command = controller._protocol.set_level(9, 2, 5, 0, 0x7695)
        with pytest.raises(BleakError, match="stopped"):
            await controller._send_command(command)
        establish.assert_not_awaited()

    asyncio.run(exercise())


def test_unexpected_disconnect_wakes_command_and_reconnects(monkeypatch):
    async def exercise():
        first, second = make_client(), make_client()
        controller, establish = prepare_connection(monkeypatch, first, second)

        async def disconnect_during_write(*args):
            first.is_connected = False
            controller._disconnected(first)

        first.write_gatt_char.side_effect = disconnect_during_write
        reply_on_write(controller, second)
        command = controller._protocol.set_level(9, 2, 5, 0, 0x7695)
        assert await controller._send_command(command) == SET_RESPONSE
        assert establish.await_count == 2
        assert_request_cleared(controller)
        await controller.stop()

    asyncio.run(exercise())


def test_connect_timeout_is_bounded(monkeypatch):
    async def exercise():
        controller = make_controller()
        controller._client = None
        cancelled = asyncio.Event()

        async def connect(*args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        monkeypatch.setattr(device, "establish_connection", connect)
        monkeypatch.setattr(device, "CONNECT_TIMEOUT", 0.01)
        with pytest.raises(asyncio.TimeoutError):
            await controller._ensure_connected()
        assert cancelled.is_set()
        assert not controller._connect_lock.locked()
        assert controller._client is None

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["raise", "hang"])
def test_unsubscribe_failure_still_disconnects_and_cancels_timer(monkeypatch, failure):
    async def exercise():
        client = make_client()
        controller = make_controller()
        controller._client = client
        controller._reset_disconnect_timer()
        timer = controller._disconnect_timer
        if failure == "raise":
            client.stop_notify.side_effect = BleakError("unsubscribe failed")
        else:

            async def hang(*args):
                await asyncio.Future()

            client.stop_notify.side_effect = hang
            monkeypatch.setattr(device, "DISCONNECT_TIMEOUT", 0.01)
        await controller.stop()
        client.disconnect.assert_awaited_once()
        assert controller._client is None
        assert controller._disconnect_timer is None
        assert timer.cancelled()
        await controller.stop()
        client.disconnect.assert_awaited_once()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["missing-characteristics", "subscribe", "hang"])
def test_partial_connection_is_released(monkeypatch, failure):
    async def exercise():
        client = make_client()
        controller, _ = prepare_connection(monkeypatch, client)
        error: type[Exception] = BleakError
        if failure == "missing-characteristics":
            client.services.get_characteristic.side_effect = lambda _: None
            error = CharacteristicMissingError
        elif failure == "subscribe":
            client.start_notify.side_effect = BleakError("subscribe failed")
        else:

            async def hang(*args):
                await asyncio.Future()

            client.start_notify.side_effect = hang
            monkeypatch.setattr(device, "GATT_TIMEOUT", 0.01)
            error = asyncio.TimeoutError
        with pytest.raises(error):
            await controller._ensure_connected()
        client.disconnect.assert_awaited_once()
        assert controller._client is None
        assert controller._read_char is None
        assert controller._write_char is None

    asyncio.run(exercise())


def test_connection_reuse_and_stale_callbacks(monkeypatch):
    async def exercise():
        first, second = make_client(), make_client()
        controller, establish = prepare_connection(monkeypatch, first, second)
        reply_on_write(controller, first)
        command = controller._protocol.set_level(7, 2, 5, 0, 0x7695)
        await controller._send_command(command)
        await controller._send_command(command)
        establish.assert_awaited_once()
        first.start_notify.assert_awaited_once()
        first.disconnect.assert_not_awaited()
        old_notification = first.start_notify.call_args.args[1]
        await controller._execute_disconnect()
        await controller._ensure_connected()
        controller._notify_future = controller.loop.create_future()
        controller._pending_sequence = 0x7695
        controller._pending_command = 3
        old_notification(0, bytearray(SET_RESPONSE))
        controller._disconnected(first)
        assert controller._client is second
        assert not controller._notify_future.done()
        second.start_notify.call_args.args[1](0, bytearray(SET_RESPONSE))
        assert controller._notify_future.result() == SET_RESPONSE
        await controller.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["write-timeout", "reply-timeout", "cancel"])
def test_failed_operation_releases_client_and_pending_state(monkeypatch, failure):
    async def exercise():
        clients = [make_client() for _ in range(device.DEFAULT_ATTEMPTS)]
        controller, establish = prepare_connection(monkeypatch, *clients)
        started = asyncio.Event()

        async def hang(*args):
            started.set()
            await asyncio.Future()

        if failure != "reply-timeout":
            for client in clients:
                client.write_gatt_char.side_effect = hang
        monkeypatch.setattr(device, "GATT_TIMEOUT", 0.01)
        monkeypatch.setattr(device, "RESPONSE_TIMEOUT", 0.01)
        command = controller._protocol.set_level(7, 2, 5, 0, 0x7695)
        task = asyncio.create_task(controller._send_command(command))
        if failure == "cancel":
            await started.wait()
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if failure == "cancel" else BleakError
        ):
            await task
        expected_attempts = 1 if failure == "cancel" else device.DEFAULT_ATTEMPTS
        assert establish.await_count == expected_attempts
        for client in clients[:expected_attempts]:
            client.disconnect.assert_awaited_once()
        assert controller._client is None
        assert controller._disconnect_timer is None
        assert_request_cleared(controller)
        assert not controller._operation_lock.locked()

    asyncio.run(exercise())


def test_idle_disconnect_cannot_interrupt_active_command(monkeypatch):
    async def exercise():
        client = make_client()
        controller, _ = prepare_connection(monkeypatch, client)
        started = asyncio.Event()
        release = asyncio.Event()

        async def write(*args):
            started.set()
            await release.wait()
            controller._notification_handler(0, bytearray(SET_RESPONSE))

        client.write_gatt_char.side_effect = write
        command = controller._protocol.set_level(7, 2, 5, 0, 0x7695)
        command_task = asyncio.create_task(controller._send_command(command))
        await started.wait()
        controller._disconnect_timer.cancel()
        controller._disconnect_timer = None
        idle_task = asyncio.create_task(controller._execute_timed_disconnect())
        await asyncio.sleep(0)
        client.disconnect.assert_not_awaited()
        release.set()
        assert await command_task == SET_RESPONSE
        await idle_task
        client.disconnect.assert_not_awaited()
        controller._disconnect_timer.cancel()
        controller._disconnect_timer = None
        await controller._execute_timed_disconnect()
        client.disconnect.assert_awaited_once()

    asyncio.run(exercise())


def test_stale_service_cache_reconnects_once_and_resolves_fresh_services(monkeypatch):
    async def exercise():
        first, second = make_client(), make_client()
        first.services.get_characteristic.side_effect = lambda _: None
        first.clear_cache.return_value = True
        controller, establish = prepare_connection(monkeypatch, first, second)
        with pytest.raises(BleakError, match="cache cleared"):
            await controller._ensure_connected()
        first.disconnect.assert_awaited_once()
        await controller._ensure_connected()
        assert controller._client is second
        second.start_notify.assert_awaited_once()
        assert establish.await_count == 2
        first.clear_cache.assert_awaited_once()
        second.clear_cache.assert_not_awaited()
        await controller.stop()

    asyncio.run(exercise())


def test_cache_recovery_does_not_repeat_for_a_persistently_wrong_service(monkeypatch):
    async def exercise():
        first, second = make_client(), make_client()
        for client in (first, second):
            client.services.get_characteristic.side_effect = lambda _: None
            client.clear_cache.return_value = True
        controller, _ = prepare_connection(monkeypatch, first, second)
        with pytest.raises(BleakError):
            await controller._ensure_connected()
        with pytest.raises(CharacteristicMissingError):
            await controller._ensure_connected()
        first.clear_cache.assert_awaited_once()
        second.clear_cache.assert_not_awaited()
        second.disconnect.assert_awaited_once()

    asyncio.run(exercise())
