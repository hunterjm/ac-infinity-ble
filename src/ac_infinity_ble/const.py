from enum import Enum

MANUFACTURER_ID = 2306

POSSIBLE_WRITE_CHARACTERISTIC_UUIDS = [
    "70D51001-2C7F-4E75-AE8A-D758951CE4E0",
    "0000ff01-0000-1000-8000-00805f9b34fb",
]
POSSIBLE_READ_CHARACTERISTIC_UUIDS = [
    "70D51002-2C7F-4E75-AE8A-D758951CE4E0",
    "0000ff02-0000-1000-8000-00805f9b34fb",
]


class CallbackType(Enum):
    """Callback type."""

    NOTIFICATION = 1
    UPDATE_RESPONSE = 2
