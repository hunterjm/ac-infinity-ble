import asyncio
import logging

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ac_infinity_ble import ACInfinityController, CallbackType, DeviceInfo
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
            future.set_result((device, adv))

    scanner.register_detection_callback(on_detected)
    await scanner.start()

    def on_state_changed(state: DeviceInfo, type: CallbackType) -> None:
        _LOGGER.info("State changed: %s", state)

    device, adv = await future
    controller = ACInfinityController(device, advertisement_data=adv)
    cancel_callback = controller.register_callback(on_state_changed)
    await controller.update()
    await asyncio.sleep(3)
    _LOGGER.info("Setting speed to 7")
    await controller.set_speed(7)
    _LOGGER.info("Turn off")
    await controller.turn_off()
    _LOGGER.info("Turn on")
    await controller.turn_on()
    _LOGGER.info("Setting speed to 6")
    await controller.set_speed(6)
    await asyncio.sleep(3)
    await controller.update()
    await asyncio.sleep(3)
    cancel_callback()
    await scanner.stop()


logging.basicConfig(level=logging.INFO)
logging.getLogger("ac_infinity_ble").setLevel(logging.DEBUG)
asyncio.run(run())
