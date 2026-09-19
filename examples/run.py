"""Read one controller without changing settings: python examples/run.py --help."""

import argparse
import asyncio
import logging

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ac_infinity_ble import ACInfinityController, CallbackType, DeviceInfo
from ac_infinity_ble.const import MANUFACTURER_ID
from ac_infinity_ble.protocol import parse_manufacturer_data

_LOGGER = logging.getLogger(__name__)


async def run(address: str | None = None, timeout: float = 30) -> None:
    """Bound discovery and release the scanner and controller on every exit."""
    future: asyncio.Future[tuple[BLEDevice, AdvertisementData]] = (
        asyncio.get_running_loop().create_future()
    )

    def on_detected(device: BLEDevice, adv: AdvertisementData) -> None:
        if future.done() or (address and device.address.lower() != address.lower()):
            return
        try:
            parse_manufacturer_data(adv.manufacturer_data[MANUFACTURER_ID])
        except (KeyError, ValueError):
            return
        future.set_result((device, adv))

    async with BleakScanner(detection_callback=on_detected):
        async with asyncio.timeout(timeout):
            device, adv = await future

    controller = ACInfinityController(device, advertisement_data=adv)

    def on_state_changed(state: DeviceInfo, kind: CallbackType) -> None:
        _LOGGER.info("%s: %s", kind, state)

    remove_callback = controller.register_callback(on_state_changed)
    try:
        _LOGGER.info("Discovered: %s", controller.state)
        await controller.update()
        if controller.state.profile.writable:
            await controller.refresh_telemetry()
    finally:
        remove_callback()
        await controller.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="Bluetooth address (or macOS UUID)")
    parser.add_argument(
        "--timeout", type=float, default=30, help="Scan timeout in seconds"
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args.address, args.timeout))


if __name__ == "__main__":
    main()
