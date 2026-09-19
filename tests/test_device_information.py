"""Optional APK GATT revision reads and transport lifecycle boundaries."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from bleak.exc import BleakError

from ac_infinity_ble import device
from ac_infinity_ble.device_information import (
    REVISION_CHARACTERISTICS,
    DeviceInformation,
    decode_revision,
)

from .test_connection import make_client, prepare_connection
from .test_families import advertisement


def revision_client():
    client = make_client()
    lookup = client.services.get_characteristic.side_effect
    client.services.get_characteristic.side_effect = lambda uuid: (
        uuid if uuid in REVISION_CHARACTERISTICS.values() else lookup(uuid)
    )
    client.read_gatt_char = AsyncMock(side_effect=[b"3.4.5", b"2.0\x00", b"1.2.3"])
    return client


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"1.2.3", "1.2.3"),
        (b"2.0\x00", "2.0"),
        (b"", None),
        (b"\xff", None),
        (b"1\x002", None),
        (b"a" * 65, None),
    ],
)
def test_revision_strings(raw, expected):
    assert decode_revision(raw) == expected


def test_revisions_read_once_and_survive_advertisements(monkeypatch):
    async def exercise():
        client, second = revision_client(), revision_client()
        controller, _ = prepare_connection(monkeypatch, client, second)
        await controller._ensure_connected()
        expected = DeviceInformation("1.2.3", "2.0", "3.4.5")
        assert controller.state.device_information == expected
        controller.set_ble_device_and_advertisement_data(
            controller._ble_device,
            Mock(manufacturer_data={2306: bytes(advertisement())}),
        )
        assert controller.state.device_information == expected
        await controller._execute_disconnect()
        await controller._ensure_connected()
        second.read_gatt_char.assert_not_awaited()
        await controller.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [BleakError("unsupported read"), TimeoutError()])
def test_optional_read_failure_does_not_break_commands_and_retries_later(
    monkeypatch, failure
):
    async def exercise():
        first, second = revision_client(), revision_client()
        first.read_gatt_char.side_effect = failure
        controller, _ = prepare_connection(monkeypatch, first, second)
        await controller._ensure_connected()
        assert controller.state.device_information == DeviceInformation()
        assert first.is_connected
        await controller._execute_disconnect()
        await controller._ensure_connected()
        assert controller.state.device_information.software_revision == "3.4.5"
        await controller.stop()

    asyncio.run(exercise())


def test_revision_timeout_and_cancellation_are_bounded(monkeypatch):
    async def exercise():
        client = revision_client()
        controller, _ = prepare_connection(monkeypatch, client)

        async def stalled(*args):
            await asyncio.Event().wait()

        client.read_gatt_char.side_effect = stalled
        monkeypatch.setattr(device, "DEVICE_INFORMATION_TIMEOUT", 0.01)
        await asyncio.wait_for(controller._ensure_connected(), 0.5)
        assert controller.state.device_information == DeviceInformation()
        await controller.stop()

        client = revision_client()
        client.read_gatt_char.side_effect = asyncio.CancelledError()
        controller, _ = prepare_connection(monkeypatch, client)
        with pytest.raises(asyncio.CancelledError):
            await controller._ensure_connected()
        client.disconnect.assert_awaited_once()
        assert controller._client is None

    asyncio.run(exercise())


def test_missing_revision_characteristics_do_not_invent_versions(monkeypatch):
    async def exercise():
        controller, _ = prepare_connection(monkeypatch, make_client())
        await controller._ensure_connected()
        assert controller.state.device_information == DeviceInformation()
        await controller.stop()

    asyncio.run(exercise())
