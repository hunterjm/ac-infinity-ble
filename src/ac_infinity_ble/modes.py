"""APK controller operating modes; home appliances use separate power flags."""

from enum import IntEnum


class ControllerMode(IntEnum):
    OFF = 1
    ON = 2
    AUTO = 3
    TIMER_ON = 4
    TIMER_OFF = 5
    CYCLE = 6
    SCHEDULE = 7
    VPD = 8
    TEMPERATURE_PARAM = 9
    HUMIDITY_PARAM = 10
    ADVANCE = 11
    AI = 12


class HomeMode(IntEnum):
    MANUAL = 1
