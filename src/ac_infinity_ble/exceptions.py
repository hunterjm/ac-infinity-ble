"""Public errors; protocol validation remains compatible with ValueError handlers."""


class ACInfinityError(Exception):
    """Base library error."""


class CharacteristicMissingError(ACInfinityError):
    """Required GATT characteristics are unavailable."""


class ProtocolError(ACInfinityError, ValueError):
    """Malformed or rejected protocol data."""


class FrameValidationError(ProtocolError):
    """Frame header, length, CRC or request identity did not match."""


class ParameterValidationError(ProtocolError):
    """Response parameters or port are malformed."""


class CommandRejectedError(ProtocolError):
    """The controller returned a negative command acknowledgement."""
