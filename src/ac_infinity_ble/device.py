from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import fields, replace

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.service import BleakGATTCharacteristic, BleakGATTServiceCollection
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakError,
    BleakNotFoundError,
    establish_connection,
    retry_bluetooth_connection_error,
)

from .commands import ParameterCodec
from .const import (
    MANUFACTURER_ID,
    POSSIBLE_READ_CHARACTERISTIC_UUIDS,
    POSSIBLE_WRITE_CHARACTERISTIC_UUIDS,
    CallbackType,
)
from .device_information import REVISION_CHARACTERISTICS, decode_revision
from .exceptions import CharacteristicMissingError
from .models import DeviceInfo, PortState
from .modes import ControllerMode
from .protocol import Protocol, is_command_header, parse_manufacturer_data
from .telemetry import frame_length as telemetry_frame_length
from .telemetry import parse_telemetry

CONNECT_TIMEOUT = 30
GATT_TIMEOUT = 10
RESPONSE_TIMEOUT = 5
DISCONNECT_TIMEOUT = 5
WRITE_CHUNK_SIZE = 20
DISCONNECT_DELAY = 5
DEVICE_INFORMATION_TIMEOUT = 5
DEFAULT_ATTEMPTS = 3

_LOGGER = logging.getLogger(__name__)


class ACInfinityController:
    def __init__(
        self,
        ble_device: BLEDevice,
        state: DeviceInfo | None = None,
        advertisement_data: AdvertisementData | None = None,
        *,
        ble_device_provider: Callable[[], BLEDevice | None] | None = None,
        disconnect_delay: float = DISCONNECT_DELAY,
    ) -> None:
        """Init the ACInfinityController."""
        if not state and not advertisement_data:
            raise ValueError("Must provide either state or advertisement_data")

        self._ble_device = ble_device
        self._ble_device_provider = ble_device_provider
        self._disconnect_delay = disconnect_delay
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        self._state = state or parse_manufacturer_data(
            advertisement_data.manufacturer_data[MANUFACTURER_ID]  # type: ignore
        )
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._read_char: BleakGATTCharacteristic | None = None
        self._write_char: BleakGATTCharacteristic | None = None
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._disconnect_task: asyncio.Task[None] | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._protocol: Protocol = Protocol()
        self._expected_disconnect = False
        self.loop = asyncio.get_running_loop()
        self._callbacks: list[Callable[[DeviceInfo, CallbackType], None]] = []
        self._notify_future: asyncio.Future[bytearray] | None = None
        self._pending_sequence: int | None = None
        self._pending_command: int | None = None
        self._response_buffer = bytearray()
        self._telemetry_buffer = bytearray()
        self._sequence = 1
        self._stopped = False
        self._device_information_read = False
        self._telemetry_event = asyncio.Event()
        self._cache_recovery_attempted = False

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Set the ble device."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        info = parse_manufacturer_data(
            advertisement_data.manufacturer_data[MANUFACTURER_ID]
        )
        # Advertisements do not contain saved ON/OFF presets or port settings.
        updates = {
            field.name: getattr(info, field.name)
            for field in fields(info)
            if field.name
            not in {"ports", "level_on", "level_off", "device_information"}
            and getattr(info, field.name) is not None
        }
        if len(advertisement_data.manufacturer_data[MANUFACTURER_ID]) == 27:
            updates.update(tmp=info.tmp, hum=info.hum, vpd=info.vpd)
        self._state = replace(self._state, **updates)
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
        return (
            self._state.work_type is not None
            and self._state.work_type != ControllerMode.OFF
        )

    @property
    def speed(self) -> int:
        """Get the speed of the device."""
        return self._state.fan or 0

    @property
    def temperature(self) -> float | None:
        """Get the temperature of the device."""
        return self._state.tmp

    @property
    def humidity(self) -> float | None:
        """Get the humidity of the device."""
        return self._state.hum

    @property
    def vpd(self) -> float | None:
        """Get the vpd of the device."""
        return self._state.vpd

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

    async def update(self, port: int = 0) -> None:
        """Read named mode parameters; sensors are updated from advertisements."""
        codec = ParameterCodec(self._state.profile)
        if not codec.profile.writable:
            return
        before = self._state.ports.get(port)
        command = self._protocol.get_parameters(
            self._state, port, list(codec.profile.read_tags), self.sequence
        )
        data = await self._send_command(command)
        values = self._protocol.parse_parameters(data, self._state, port)
        if (
            port
            and before is not None
            and (
                (latest := self._state.ports.get(port)) is None
                or not latest.connected
                or (latest.kind, latest.raw_type) != (before.kind, before.raw_type)
            )
        ):
            raise ValueError("Port changed while the settings read was pending")
        old = self._state.ports.get(
            port, PortState(port, level=self._state.fan if port == 0 else None)
        )
        current = codec.decode_output(values, old)
        self._publish_port(current, CallbackType.UPDATE_RESPONSE)

    async def refresh_telemetry(self) -> None:
        """Subscribe briefly and wait for fresh telemetry without reading settings."""
        if not self._state.profile.writable:
            return
        async with self._operation_lock:
            self._telemetry_event.clear()
            try:
                await self._ensure_connected()
                async with asyncio.timeout(RESPONSE_TIMEOUT):
                    await self._telemetry_event.wait()
            except BaseException:
                await self._execute_disconnect()
                raise
            finally:
                if self._client and self._client.is_connected:
                    self._reset_disconnect_timer()

    def _publish_port(self, port: PortState, kind: CallbackType) -> None:
        ports = dict(self._state.ports)
        ports[port.id] = port
        self._state = replace(self._state, ports=ports)
        if port.id == 0:
            self._state = replace(
                self._state,
                work_type=port.mode,
                fan=port.level,
                level_on=port.level_on,
                level_off=port.level_off,
            )
        self._fire_callbacks(kind)

    async def set_output(
        self, port: int, *, on: bool, level: int | None = None
    ) -> None:
        """Control one output, publishing only after a successful matching ACK."""
        codec = ParameterCodec(self._state.profile)
        # Validate levels before any Bluetooth traffic, including read-modify-write.
        if level is not None and (
            isinstance(level, bool)
            or not isinstance(level, int)
            or not 0 <= level <= 10
        ):
            raise ValueError("Level must be an integer between 0 and 10")
        if port and (
            port not in self._state.ports or not self._state.ports[port].connected
        ):
            raise ValueError("Port is not connected")
        if codec.profile.packed_manual_level and on and level is not None:
            # APK tp1 (>7) / ml2: manual speed occupies the upper nibble;
            # the lower nibble is a separate saved maximum. Preserve it.
            await self.update(port)
        old = self._state.ports.get(
            port,
            PortState(
                port, level_on=self._state.level_on, level_off=self._state.level_off
            ),
        )
        command = self._protocol.set_parameters(
            self._state,
            port,
            (values := codec.output_values(on, level, old)),
            self.sequence,
        )
        data = await self._send_command(command)
        self._protocol.validate_ack(data, self._state, port, set(values))
        if port and (
            (latest := self._state.ports.get(port)) is None
            or not latest.connected
            or (latest.kind, latest.raw_type) != (old.kind, old.raw_type)
        ):
            raise ValueError("Port changed while the command was pending")
        old = self._state.ports.get(port, old)
        current = codec.confirmed_output(old, on, level)
        self._publish_port(current, CallbackType.UPDATE_RESPONSE)

    async def turn_on(self, speed: int | None = None) -> None:
        await self.set_output(0, on=True, level=speed)

    async def turn_off(self) -> None:
        await self.set_output(0, on=False)

    async def set_speed(self, speed: int) -> None:
        await self.set_output(0, on=speed > 0, level=speed)

    async def stop(self) -> None:
        """Stop the controller."""
        _LOGGER.debug("%s: Stop", self.name)
        self._stopped = True
        async with self._operation_lock:
            await self._execute_disconnect()
        if self._disconnect_task is not None:
            await self._disconnect_task
            self._disconnect_task = None

    def _fire_callbacks(self, type: CallbackType) -> None:
        """Fire the callbacks."""
        for callback in list(self._callbacks):
            try:
                callback(self.state, type)
            except Exception:
                _LOGGER.exception("%s: State callback failed", self.name)

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
        if self._stopped:
            raise BleakError("Controller has been stopped")
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
            if self._stopped:
                raise BleakError("Controller has been stopped")
            # Check again while holding the lock
            if self._client and self._client.is_connected:
                self._reset_disconnect_timer()
                return
            _LOGGER.debug("%s: Connecting; RSSI: %s", self.name, self.rssi)
            if self._ble_device_provider is not None:
                device = self._ble_device_provider()
                if device is None:
                    raise BleakError("No connectable Bluetooth device available")
                self._ble_device = device
            async with asyncio.timeout(CONNECT_TIMEOUT):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self.name,
                    self._disconnected,
                    use_services_cache=True,
                )
            _LOGGER.debug("%s: Connected; RSSI: %s", self.name, self.rssi)
            self._client = client
            try:
                if self._stopped:
                    raise BleakError("Controller stopped while connecting")
                resolved = self._resolve_characteristics(client.services)
                if not resolved or self._read_char is None:
                    if not self._cache_recovery_attempted:
                        self._cache_recovery_attempted = True
                        try:
                            async with asyncio.timeout(GATT_TIMEOUT):
                                cleared = await client.clear_cache()
                        except (BleakError, NotImplementedError):
                            cleared = False
                        if cleared:
                            # The transport retry reconnects after cleanup and
                            # obtains a fresh HA route and service collection.
                            raise BleakError(
                                "Service cache cleared; reconnect required"
                            )
                    raise CharacteristicMissingError(
                        "AC Infinity read/write characteristics missing"
                    )

                def notification_handler(
                    sender: BleakGATTCharacteristic, data: bytearray
                ) -> None:
                    # Late callbacks from an old connection cannot satisfy a retry.
                    if self._client is client:
                        self._notification_handler(sender, data)

                async with asyncio.timeout(GATT_TIMEOUT):
                    await client.start_notify(self._read_char, notification_handler)
                if self._client is not client or not client.is_connected:
                    raise BleakError("Disconnected while subscribing to notifications")
                await self._read_device_information(client)
                self._cache_recovery_attempted = False
            except BaseException:
                read_char = self._read_char
                self._client = None
                self._read_char = None
                self._write_char = None
                await self._disconnect_client(client, read_char)
                raise
            self._reset_disconnect_timer()

    async def _read_device_information(
        self, client: BleakClientWithServiceCache
    ) -> None:
        """Read optional revisions once per runtime, inside the connection lock.

        Failed reads may retry on a later connection. No extra connection is
        opened for metadata, and cancellation still triggers session cleanup.
        """
        if self._device_information_read:
            return
        complete = True
        try:
            async with asyncio.timeout(DEVICE_INFORMATION_TIMEOUT):
                for field, uuid in REVISION_CHARACTERISTICS.items():
                    characteristic = client.services.get_characteristic(uuid)
                    if characteristic is None:
                        continue
                    try:
                        value = decode_revision(
                            await client.read_gatt_char(characteristic)
                        )
                    except BleakError:
                        complete = False
                        if not client.is_connected:
                            raise
                        continue
                    if value is not None:
                        self._state = replace(
                            self._state,
                            device_information=replace(
                                self._state.device_information, **{field: value}
                            ),
                        )
        except TimeoutError:
            complete = False
        if not client.is_connected or self._client is not client:
            raise BleakError("Disconnected while reading device information")
        self._device_information_read = complete

    def _notification_handler(
        self, _sender: BleakGATTCharacteristic | int, data: bytearray
    ) -> None:
        """Handle notification responses."""
        _LOGGER.debug("%s: Notification received: %s", self.name, data.hex())
        if data[:2] == b"\x1e\xff" or self._telemetry_buffer:
            # Telemetry and command frames have independent assembly buffers.
            if data[:2] == b"\x1e\xff":
                self._telemetry_buffer.clear()
            if data[:2] == b"\x1e\xff" or (
                not is_command_header(data) and not self._response_buffer
            ):
                self._telemetry_buffer.extend(data)
                size = telemetry_frame_length(self._telemetry_buffer, self._state)
                if size is None or len(self._telemetry_buffer) < size:
                    return
                try:
                    updated = parse_telemetry(self._telemetry_buffer, self._state)
                    updated = replace(
                        updated,
                        ports={
                            port_id: port.with_saved_levels(
                                self._state.ports.get(port_id)
                            )
                            for port_id, port in updated.ports.items()
                        },
                    )
                    self._state = updated
                    self._telemetry_event.set()
                    self._fire_callbacks(CallbackType.NOTIFICATION)
                except ValueError as ex:
                    _LOGGER.debug("%s: Ignoring telemetry: %s", self.name, ex)
                finally:
                    self._telemetry_buffer.clear()
                return

        if self._notify_future and not self._notify_future.done():
            if data[:2] == b"\x1e\xff":
                return
            try:
                # Notifications may split a response across several ATT packets.
                # A fresh valid header also recovers from an abandoned fragment.
                if len(data) >= 8 and is_command_header(data):
                    self._protocol.response_frame_length(data)
                    self._response_buffer.clear()
                self._response_buffer.extend(data)
                packet = self._response_buffer
                if len(packet) < 8:
                    if packet[:2] not in (b"", b"\xa5") and not is_command_header(
                        packet
                    ):
                        raise ValueError("Invalid response prefix")
                    return
                frame_length = self._protocol.response_frame_length(packet)
                if len(packet) < frame_length:
                    return
                self._protocol.parse_response(
                    packet, self._pending_sequence, self._pending_command
                )
            except ValueError as ex:
                self._response_buffer.clear()
                _LOGGER.debug("%s: Ignoring notification: %s", self.name, ex)
                return
            self._notify_future.set_result(bytearray(packet))
            self._response_buffer.clear()

    def _reset_disconnect_timer(self) -> None:
        """Reset disconnect timer."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
        self._expected_disconnect = False
        self._disconnect_timer = self.loop.call_later(
            self._disconnect_delay, self._disconnect
        )

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Disconnected callback."""
        if self._client is not client:
            return
        self._client = None
        self._read_char = None
        self._write_char = None
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None
        if self._notify_future and not self._notify_future.done():
            self._notify_future.set_exception(BleakError("Disconnected during command"))
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
        if self._disconnect_task is None or self._disconnect_task.done():
            self._disconnect_task = asyncio.create_task(
                self._execute_timed_disconnect()
            )

    async def _execute_timed_disconnect(self) -> None:
        """Execute timed disconnection."""
        _LOGGER.debug(
            "%s: Disconnecting after timeout of %s",
            self.name,
            DISCONNECT_DELAY,
        )
        async with self._operation_lock:
            # A command may have refreshed the idle timer while this task waited.
            if self._disconnect_timer is None:
                await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            if self._disconnect_timer:
                self._disconnect_timer.cancel()
                self._disconnect_timer = None
            read_char = self._read_char
            client = self._client
            self._expected_disconnect = True
            self._client = None
            self._read_char = None
            self._write_char = None
            self._response_buffer.clear()
            self._telemetry_buffer.clear()
            if client:
                await self._disconnect_client(client, read_char)

    async def _disconnect_client(
        self,
        client: BleakClientWithServiceCache,
        read_char: BleakGATTCharacteristic | None,
    ) -> None:
        """Attempt disconnect even when unsubscribing fails or times out."""
        try:
            if read_char is not None and client.is_connected:
                try:
                    async with asyncio.timeout(DISCONNECT_TIMEOUT):
                        await client.stop_notify(read_char)
                except Exception:
                    _LOGGER.debug("%s: Unsubscribe failed", self.name, exc_info=True)
        finally:
            try:
                async with asyncio.timeout(DISCONNECT_TIMEOUT):
                    await client.disconnect()
            except Exception:
                _LOGGER.warning("%s: Disconnect failed", self.name, exc_info=True)

    @retry_bluetooth_connection_error(DEFAULT_ATTEMPTS)
    async def _send_command_locked(self, command: bytes) -> bytes:
        """Send command to device and read response."""
        try:
            # This runs inside the operation lock, including on every retry.
            await self._ensure_connected()
            return await self._execute_command_locked(command)
        except (TimeoutError, EOFError) as ex:
            await self._execute_disconnect()
            raise BleakError("BLE session timed out or closed") from ex
        except BaseException:
            await self._execute_disconnect()
            raise

    async def _send_command(self, command: bytes) -> bytes:
        """Send command to device and read response."""
        return await self._send_command_while_connected(command)

    async def _send_command_while_connected(self, command: bytes) -> bytes:
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
            if self._stopped:
                raise BleakError("Controller has been stopped")
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
            finally:
                if self._client and self._client.is_connected:
                    self._reset_disconnect_timer()

        raise RuntimeError("Unreachable")

    async def _execute_command_locked(self, command: bytes) -> bytes:
        """Execute command and read response."""
        assert self._client is not None  # nosec
        if not self._read_char:
            raise CharacteristicMissingError("Read characteristic missing")
        if not self._write_char:
            raise CharacteristicMissingError("Write characteristic missing")

        # Register the request before writing: a reply can arrive during the write.
        self._pending_sequence = int.from_bytes(command[4:6], "big")
        self._pending_command = command[9]
        self._response_buffer.clear()
        self._notify_future = self.loop.create_future()
        client = self._client
        write_char = self._write_char
        try:
            # The APK splits packets for the negotiated MTU. Twenty-byte chunks
            # also work on the minimum BLE MTU, without backend-specific setup.
            async with asyncio.timeout(GATT_TIMEOUT):
                for offset in range(0, len(command), WRITE_CHUNK_SIZE):
                    await client.write_gatt_char(
                        write_char,
                        command[offset : offset + WRITE_CHUNK_SIZE],
                        False,
                    )
            async with asyncio.timeout(RESPONSE_TIMEOUT):
                return bytes(await self._notify_future)
        finally:
            if not self._notify_future.done():
                self._notify_future.cancel()
            elif not self._notify_future.cancelled():
                # A disconnect can fail the future while the write itself raises.
                self._notify_future.exception()
            self._notify_future = None
            self._pending_sequence = None
            self._pending_command = None
            self._response_buffer.clear()

    def _resolve_characteristics(self, services: BleakGATTServiceCollection) -> bool:
        """Resolve characteristics."""
        self._read_char = None
        self._write_char = None
        for read_uuid, write_uuid in zip(
            POSSIBLE_READ_CHARACTERISTIC_UUIDS,
            POSSIBLE_WRITE_CHARACTERISTIC_UUIDS,
            strict=True,
        ):
            read_char = services.get_characteristic(read_uuid)
            write_char = services.get_characteristic(write_uuid)
            if read_char is not None and write_char is not None:
                self._read_char, self._write_char = read_char, write_char
                break
        return bool(self._read_char and self._write_char)
