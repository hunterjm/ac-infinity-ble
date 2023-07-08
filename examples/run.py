import asyncio
import logging

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ac_infinity_ble import ACInfinityController, ACInfinityControllerState
from ac_infinity_ble.const import MANUFACTURER_ID

_LOGGER = logging.getLogger(__name__)


async def run() -> None:
    scanner = BleakScanner()
    future: asyncio.Future[BLEDevice] = asyncio.Future()

    def on_detected(device: BLEDevice, adv: AdvertisementData) -> None:
        if future.done():
            return
        _LOGGER.info("Detected: %s", device)
        if adv.manufacturer_data.get(MANUFACTURER_ID):
            _LOGGER.info("Found device: %s", device.address)
            future.set_result(device)

    scanner.register_detection_callback(on_detected)
    await scanner.start()

    def on_state_changed(state: ACInfinityControllerState) -> None:
        _LOGGER.info("State changed: %s", state)

    device = await future
    controller = ACInfinityController(device)  # noqa: F841
    await scanner.stop()


logging.basicConfig(level=logging.INFO)
logging.getLogger("ac_infinity_ble").setLevel(logging.DEBUG)
asyncio.run(run())
