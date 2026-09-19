import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from bleak.backends.device import BLEDevice

from ac_infinity_ble import device
from ac_infinity_ble.const import CallbackType
from ac_infinity_ble.device import ACInfinityController
from ac_infinity_ble.models import DeviceInfo

from .frames import (
    INVALID_RESPONSES,
    MODEL_RESPONSE,
    SET_RESPONSE,
    TELEMETRY,
    response_frame,
)


def make_controller():
    controller = ACInfinityController(
        BLEDevice("00:11:22:33:44:55", "Test", {}),
        state=DeviceInfo(type=7, name="Test", version=3),
    )
    controller._client = Mock(write_gatt_char=AsyncMock())
    controller._read_char = Mock()
    controller._write_char = Mock()
    return controller


def assert_request_cleared(controller):
    assert controller._notify_future is None
    assert controller._pending_sequence is None
    assert controller._pending_command is None
    assert not controller._response_buffer


@pytest.mark.parametrize(
    "invalid",
    [frame for frame, _ in INVALID_RESPONSES],
    ids=[name for _, name in INVALID_RESPONSES],
)
def test_command_ignores_invalid_reply_until_matching_response(invalid):
    async def exercise():
        controller = make_controller()
        command = controller._protocol.set_level(7, 2, 5, 0, 0x7695)
        callback = Mock()
        controller.register_callback(callback)
        original_state = replace(controller.state)

        async def write(*args):
            future = controller._notify_future
            controller._notification_handler(0, bytearray(invalid))
            assert not future.done()
            assert controller.state == original_state
            callback.assert_not_called()
            controller._notification_handler(0, bytearray(SET_RESPONSE))
            assert future.result() == SET_RESPONSE
            # Duplicates arriving before the write returns cannot overwrite it.
            controller._notification_handler(0, bytearray(SET_RESPONSE))

        controller._client.write_gatt_char.side_effect = write
        assert await controller._execute_command_locked(command) == SET_RESPONSE
        controller._client.write_gatt_char.assert_awaited_once_with(
            controller._write_char, command, False
        )
        assert_request_cleared(controller)

    asyncio.run(exercise())


@pytest.mark.parametrize("timing", ["idle", "pending", "completed", "cancelled"])
def test_telemetry_updates_state_independently_of_pending_command(timing):
    async def exercise():
        controller = make_controller()
        callback = Mock()
        controller.register_callback(callback)
        future = None
        if timing != "idle":
            future = controller.loop.create_future()
            controller._notify_future = future
            controller._pending_sequence = 0x7695
            controller._pending_command = 3
            if timing == "completed":
                future.set_result(bytearray(SET_RESPONSE))
            elif timing == "cancelled":
                future.cancel()

        controller._notification_handler(0, bytearray(TELEMETRY))
        assert controller.temperature == 23.98
        assert controller.humidity == 67.33
        assert controller.vpd == 0.92
        assert controller.state.work_type == 2
        callback.assert_called_once_with(controller.state, CallbackType.NOTIFICATION)
        if timing == "pending":
            assert future is not None
            assert not future.done()
            controller._notification_handler(0, bytearray(SET_RESPONSE))
            assert future.result() == SET_RESPONSE
        elif timing == "completed":
            assert future is not None
            assert future.result() == SET_RESPONSE
        elif timing == "cancelled":
            assert future is not None
            assert future.cancelled()

    asyncio.run(exercise())


@pytest.mark.parametrize("timing", ["during-write", "after-write"])
def test_update_uses_model_reply_when_telemetry_arrives(timing):
    async def exercise():
        controller = make_controller()
        controller._sequence = 0x1A65
        controller._ensure_connected = AsyncMock()
        controller._execute_disconnect = AsyncMock()
        callbacks = []
        chunks = []
        controller.register_callback(lambda state, kind: callbacks.append(kind))

        def notify():
            controller._notification_handler(0, bytearray(TELEMETRY))
            assert not controller._notify_future.done()
            controller._notification_handler(0, bytearray(MODEL_RESPONSE))

        async def write(characteristic, chunk, response):
            chunks.append(chunk)
            if len(b"".join(chunks)) < 22:
                return
            if timing == "during-write":
                notify()
            else:
                controller.loop.call_soon(notify)

        controller._client.write_gatt_char.side_effect = write
        await controller.update()
        assert controller.state.work_type == 1
        assert controller.state.level_off == 5
        assert controller.state.level_on == 5
        assert controller.temperature == 23.98
        assert callbacks == [CallbackType.NOTIFICATION, CallbackType.UPDATE_RESPONSE]
        assert [len(chunk) for chunk in chunks] == [20, 2]
        assert b"".join(chunks) == controller._protocol.get_model_data(7, 0, 0x1A66)
        controller._execute_disconnect.assert_not_awaited()
        controller._disconnect_timer.cancel()
        assert_request_cleared(controller)

    asyncio.run(exercise())


@pytest.mark.parametrize("split", range(1, len(MODEL_RESPONSE)))
def test_fragmented_reply_is_validated_and_telemetry_stays_independent(split):
    async def exercise():
        controller = make_controller()
        controller._notify_future = controller.loop.create_future()
        controller._pending_sequence = 0x1A66
        controller._pending_command = 1
        controller._notification_handler(0, bytearray(MODEL_RESPONSE[:split]))
        assert not controller._notify_future.done()
        controller._notification_handler(0, bytearray(TELEMETRY))
        assert controller.temperature == 23.98
        assert not controller._notify_future.done()
        controller._notification_handler(0, bytearray(MODEL_RESPONSE[split:]))
        assert controller._notify_future.result() == MODEL_RESPONSE
        assert not controller._response_buffer

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["write", "timeout", "cancel"])
def test_failed_command_cleans_up_and_next_command_rejects_old_reply(
    failure, monkeypatch
):
    async def exercise():
        controller = make_controller()
        first_command = controller._protocol.set_level(7, 2, 5, 0, 0x7695)
        started = asyncio.Event()
        pending = []

        async def write(*args):
            pending.append(controller._notify_future)
            started.set()
            if failure == "write":
                raise RuntimeError("write failed")

        controller._client.write_gatt_char.side_effect = write
        if failure == "timeout":
            timeout = device.asyncio.timeout
            monkeypatch.setattr(device.asyncio, "timeout", lambda _: timeout(0))
        task = asyncio.create_task(controller._execute_command_locked(first_command))
        await started.wait()
        if failure == "cancel":
            task.cancel()
        error = {
            "write": RuntimeError,
            "timeout": asyncio.TimeoutError,
            "cancel": asyncio.CancelledError,
        }[failure]
        with pytest.raises(error):
            await task
        assert_request_cleared(controller)
        assert pending[0].cancelled()

        next_response = response_frame(sequence=0x7696)

        async def next_write(*args):
            controller._notification_handler(0, bytearray(SET_RESPONSE))
            assert not controller._notify_future.done()
            controller._notification_handler(0, bytearray(next_response))

        controller._client.write_gatt_char.side_effect = next_write
        next_command = controller._protocol.set_level(7, 2, 5, 0, 0x7696)
        assert await controller._execute_command_locked(next_command) == next_response
        assert_request_cleared(controller)

    asyncio.run(exercise())


def test_unsolicited_responses_and_short_notifications_are_ignored():
    async def exercise():
        controller = make_controller()
        callback = Mock()
        controller.register_callback(callback)
        original_state = replace(controller.state)
        for frame in (b"", b"\x1e", TELEMETRY[:17], SET_RESPONSE, MODEL_RESPONSE):
            controller._notification_handler(0, bytearray(frame))
        assert controller.state == original_state
        callback.assert_not_called()
        assert_request_cleared(controller)

    asyncio.run(exercise())
