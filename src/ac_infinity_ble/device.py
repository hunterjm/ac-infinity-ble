from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict, replace

import async_timeout
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.service import BleakGATTCharacteristic, BleakGATTServiceCollection
from bleak.exc import BleakDBusError
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakError,
    BleakNotFoundError,
    establish_connection,
    retry_bluetooth_connection_error,
)

from .const import (
    MANUFACTURER_ID,
    POSSIBLE_READ_CHARACTERISTIC_UUIDS,
    POSSIBLE_WRITE_CHARACTERISTIC_UUIDS,
    CallbackType,
)
from .exceptions import CharacteristicMissingError
from .models import DeviceInfo
from .protocol import Protocol, parse_manufacturer_data
from .util import get_bit, get_bits, get_short

BLEAK_BACKOFF_TIME = 0.25
DISCONNECT_DELAY = 120
DEFAULT_ATTEMPTS = 3

_LOGGER = logging.getLogger(__name__)


class ACInfinityController:
    def __init__(
        self,
        ble_device: BLEDevice,
        state: DeviceInfo | None = None,
        advertisement_data: AdvertisementData | None = None,
    ) -> None:
        """Init the ACInfinityController."""
        if not state and not advertisement_data:
            raise ValueError("Must provide either state or advertisement_data")

        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        self._state = state or parse_manufacturer_data(
            advertisement_data.manufacturer_data[MANUFACTURER_ID]  # type: ignore
        )
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._read_char: BleakGATTCharacteristic | None = None
        self._write_char: BleakGATTCharacteristic | None = None
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._protocol: Protocol = Protocol()
        self._expected_disconnect = False
        self.loop = asyncio.get_running_loop()
        self._callbacks: list[Callable[[DeviceInfo, CallbackType], None]] = []
        self._notify_future: asyncio.Future[bytearray] | None = None
        self._sequence = 1

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Set the ble device."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        info = parse_manufacturer_data(
            advertisement_data.manufacturer_data[MANUFACTURER_ID]
        )
        self._state = replace(
            self._state, **{k: v for k, v in asdict(info).items() if v is not None}
        )
        if self._state.fan:
            if self._state.level_off or 0 > self._state.fan:
                self._state.level_off = self._state.fan
            if self._state.level_on or 10 < self._state.fan:
                self._state.level_on = self._state.fan
        self._fire_callbacks(CallbackType.ADVERTISEMENT)

    @property
    def address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Get the name of the device."""
        return self._state.name

    @property
    def is_on(self) -> bool:
        """Get whether the device is on."""
        return bool(self._state.work_type == 2 and self._state.fan)

    @property
    def speed(self) -> int:
        """Get the speed of the device."""
        return self._state.fan or 0

    @property
    def temperature(self) -> float:
        """Get the temperature of the device."""
        return self._state.tmp or 0

    @property
    def humidity(self) -> float:
        """Get the humidity of the device."""
        return self._state.hum or 0

    @property
    def vpd(self) -> float:
        """Get the vpd of the device."""
        return self._state.vpd or 0

    @property
    def rssi(self) -> int | None:
        """Get the rssi of the device."""
        if self._advertisement_data:
            return self._advertisement_data.rssi
        return None

    @property
    def state(self) -> DeviceInfo:
        """Return the state."""
        return self._state

    @property
    def sequence(self) -> int:
        """Increment and return the sequence number."""
        if self._sequence == 65535:
            self._sequence = 0
        self._sequence += 1
        return self._sequence

    async def update(self) -> None:
        """Update the controller."""
        await self._ensure_connected()
        _LOGGER.debug("%s: Updating", self.name)
        command = self._protocol.get_model_data(self._state.type, 0, self.sequence)
        if data := await self._send_command(command):
            self._state.work_type = data[12]
            self._state.level_off = data[15]
            self._state.level_on = data[18]
            if self._state.work_type == 1:
                self._state.fan = self._state.level_off
            if self._state.work_type == 2:
                self._state.fan = self._state.level_on
            self._fire_callbacks(CallbackType.UPDATE_RESPONSE)
        await self._execute_disconnect()

    async def turn_on(self, speed: int | None = None) -> None:
        """Turn on the controller."""
        await self._ensure_connected()
        _LOGGER.debug("%s: Turn on", self.name)
        self._state.work_type = 2
        if speed is not None:
            self._state.fan = speed
            self._state.level_on = speed
        else:
            self._state.fan = self._state.level_on or 10
            self._state.level_on = self._state.fan

        command = self._protocol.set_level(
            self._state.type, 2, self._state.level_on, 0, self.sequence
        )
        await self._send_command(command)
        await self._execute_disconnect()

    async def turn_off(self) -> None:
        """Turn off the controller."""
        await self._ensure_connected()
        _LOGGER.debug("%s: Turn off", self.name)
        self._state.work_type = 1
        self._state.fan = self._state.level_off or 0
        self._state.level_off = self._state.fan
        command = self._protocol.set_level(
            self._state.type, 1, self._state.level_off, 0, self.sequence
        )
        await self._send_command(command)
        await self._execute_disconnect()

    async def set_speed(self, speed: int) -> None:
        """Set the speed of the controller."""
        await self._ensure_connected()
        _LOGGER.debug("%s: Set speed to %s", self.name, speed)
        self._state.work_type = 2 if speed > 0 else 1
        self.state.fan = speed
        if self._state.work_type == 1:
            self._state.level_off = speed
        else:
            self._state.level_on = speed
        command = self._protocol.set_level(
            self._state.type, self._state.work_type, speed, 0, self.sequence
        )
        await self._send_command(command)
        await self._execute_disconnect()

    async def stop(self) -> None:
        """Stop the controller."""
        _LOGGER.debug("%s: Stop", self.name)
        await self._execute_disconnect()

    def _fire_callbacks(self, type: CallbackType) -> None:
        """Fire the callbacks."""
        for callback in self._callbacks:
            callback(self._state, type)

    def register_callback(
        self, callback: Callable[[DeviceInfo, CallbackType], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""

        def unregister_callback() -> None:
            self._callbacks.remove(callback)

        self._callbacks.append(callback)
        return unregister_callback

    async def _ensure_connected(self) -> None:
        """Ensure connection to device is established."""
        if self._connect_lock.locked():
            _LOGGER.debug(
                "%s: Connection already in progress, waiting; RSSI: %s",
                self.name,
                self.rssi,
            )
        if self._client and self._client.is_connected:
            self._reset_disconnect_timer()
            return
        async with self._connect_lock:
            # Check again while holding the lock
            if self._client and self._client.is_connected:
                self._reset_disconnect_timer()
                return
            _LOGGER.debug("%s: Connecting; RSSI: %s", self.name, self.rssi)
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.name,
                self._disconnected,
                use_services_cache=True,
                ble_device_callback=lambda: self._ble_device,
            )
            _LOGGER.debug("%s: Connected; RSSI: %s", self.name, self.rssi)
            resolved = self._resolve_characteristics(client.services)
            if not resolved:
                # Try to handle services failing to load
                resolved = self._resolve_characteristics(await client.get_services())

            self._client = client
            self._reset_disconnect_timer()

            _LOGGER.debug(
                "%s: Subscribe to notifications; RSSI: %s", self.name, self.rssi
            )
            await client.start_notify(self._read_char, self._notification_handler)

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle notification responses."""
        _LOGGER.debug("%s: Notification received: %s", self.name, data.hex())
        if self._notify_future and not self._notify_future.done():
            self._notify_future.set_result(data)
            return

        if data[0] == 0x1E and data[1] == 0xFF:
            self._state.is_degree = get_bit(data[6], 0)
            self._state.tmp_state = get_bits(data[6], 1, 2)
            self._state.hum_state = get_bits(data[6], 3, 2)
            self._state.vpd_state = get_bits(data[6], 5, 2)
            self._state.choose_port = get_bits(data[7], 4, 4)
            self._state.tmp = get_short(data, 8) / 100
            self._state.hum = get_short(data, 10) / 100
            self._state.vpd = get_short(data, 12) / 100
            self._state.fan_type = get_short(data, 14)
            self._state.fan_state = get_bits(data[16], 0, 2)
            # self._state.fan = get_bits(data[17], 0, 4) # Not accurate
            self._state.work_type = get_bits(data[17], 4, 4)
            self._fire_callbacks(CallbackType.NOTIFICATION)

    def _reset_disconnect_timer(self) -> None:
        """Reset disconnect timer."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
        self._expected_disconnect = False
        self._disconnect_timer = self.loop.call_later(
            DISCONNECT_DELAY, self._disconnect
        )

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Disconnected callback."""
        if self._expected_disconnect:
            _LOGGER.debug(
                "%s: Disconnected from device; RSSI: %s", self.name, self.rssi
            )
            return
        _LOGGER.warning(
            "%s: Device unexpectedly disconnected; RSSI: %s",
            self.name,
            self.rssi,
        )

    def _disconnect(self) -> None:
        """Disconnect from device."""
        self._disconnect_timer = None
        asyncio.create_task(self._execute_timed_disconnect())

    async def _execute_timed_disconnect(self) -> None:
        """Execute timed disconnection."""
        _LOGGER.debug(
            "%s: Disconnecting after timeout of %s",
            self.name,
            DISCONNECT_DELAY,
        )
        await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            read_char = self._read_char
            client = self._client
            self._expected_disconnect = True
            self._client = None
            self._read_char = None
            self._write_char = None
            if client and client.is_connected:
                await client.stop_notify(read_char)
                await client.disconnect()

    @retry_bluetooth_connection_error(DEFAULT_ATTEMPTS)
    async def _send_command_locked(self, command: bytes) -> bytes | None:
        """Send command to device and read response."""
        try:
            return await self._execute_command_locked(command)
        except BleakDBusError as ex:
            # Disconnect so we can reset state and try again
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug(
                "%s: RSSI: %s; Backing off %ss; Disconnecting due to error: %s",
                self.name,
                self.rssi,
                BLEAK_BACKOFF_TIME,
                ex,
            )
            await self._execute_disconnect()
            raise
        except BleakError as ex:
            # Disconnect so we can reset state and try again
            _LOGGER.debug(
                "%s: RSSI: %s; Disconnecting due to error: %s", self.name, self.rssi, ex
            )
            await self._execute_disconnect()
            raise

    async def _send_command(
        self, command: bytes, retry: int | None = None
    ) -> bytes | None:
        """Send command to device and read response."""
        await self._ensure_connected()
        return await self._send_command_while_connected(command, retry)

    async def _send_command_while_connected(
        self, command: bytes, retry: int | None = None
    ) -> bytes | None:
        """Send command to device and read response."""
        _LOGGER.debug(
            "%s: Sending command %s",
            self.name,
            command.hex(),
        )
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation already in progress, waiting; RSSI: %s",
                self.name,
                self.rssi,
            )
        async with self._operation_lock:
            try:
                return await self._send_command_locked(command)
            except BleakNotFoundError:
                _LOGGER.error(
                    "%s: device not found, no longer in range, or poor RSSI: %s",
                    self.name,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except CharacteristicMissingError as ex:
                _LOGGER.debug(
                    "%s: characteristic missing: %s; RSSI: %s",
                    self.name,
                    ex,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.debug("%s: communication failed", self.name, exc_info=True)
                raise

        raise RuntimeError("Unreachable")

    async def _execute_command_locked(self, command: bytes) -> bytes:
        """Execute command and read response."""
        assert self._client is not None  # nosec
        if not self._read_char:
            raise CharacteristicMissingError("Read characteristic missing")
        if not self._write_char:
            raise CharacteristicMissingError("Write characteristic missing")

        self._notify_future = asyncio.Future()
        await self._client.write_gatt_char(self._write_char, command, False)

        notify_msg = None
        async with async_timeout.timeout(5):
            notify_msg = await self._notify_future

        self._notify_future = None
        return notify_msg

    def _resolve_characteristics(self, services: BleakGATTServiceCollection) -> bool:
        """Resolve characteristics."""
        for characteristic in POSSIBLE_READ_CHARACTERISTIC_UUIDS:
            if char := services.get_characteristic(characteristic):
                self._read_char = char
                break
        for characteristic in POSSIBLE_WRITE_CHARACTERISTIC_UUIDS:
            if char := services.get_characteristic(characteristic):
                self._write_char = char
                break
        return bool(self._read_char and self._write_char)
