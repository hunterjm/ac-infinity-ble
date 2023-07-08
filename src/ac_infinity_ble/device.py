from __future__ import annotations

import asyncio

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .models import ACInfinityControllerState


class ACInfinityController:
    def __init__(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData | None = None
    ) -> None:
        """Init the ACInfinityController."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        self._state = ACInfinityControllerState()
