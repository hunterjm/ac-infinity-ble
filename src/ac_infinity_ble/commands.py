"""Pure family-aware command payloads, separate from framing and transport."""

from dataclasses import dataclass, replace

from .exceptions import CommandRejectedError, ParameterValidationError
from .models import PortState
from .modes import ControllerMode
from .routing import ProtocolProfile


@dataclass(frozen=True)
class ParameterCodec:
    profile: ProtocolProfile

    def encode_get(self, port: int, tags: list[int]) -> bytes:
        return bytes(tags) + self.profile.port_suffix(port)

    def encode_set(self, port: int, values: dict[int, bytes]) -> bytes:
        if not self.profile.writable:
            raise ValueError("Device does not support output controls")
        suffix = self.profile.port_suffix(port)
        payload = bytearray()
        for tag, value in values.items():
            payload.append(tag)
            payload.extend(
                len(value).to_bytes(self.profile.parameter_length_size, "big")
            )
            payload.extend(value)
        return bytes(payload) + suffix

    def _strip_port(self, payload: bytes, port: int) -> bytes:
        suffix = self.profile.port_suffix(port)
        if suffix:
            if not payload.endswith(suffix):
                raise ParameterValidationError("Unexpected response port")
            return payload[: -len(suffix)]
        return payload

    def decode_get(self, payload: bytes, port: int) -> dict[int, bytes]:
        payload = self._strip_port(payload, port)
        width = self.profile.parameter_length_size
        values: dict[int, bytes] = {}
        offset = 0
        while offset < len(payload):
            if offset + width + 1 > len(payload):
                raise ParameterValidationError("Truncated parameter header")
            tag = payload[offset]
            length = int.from_bytes(payload[offset + 1 : offset + width + 1], "big")
            offset += width + 1
            if offset + length > len(payload) or tag in values:
                raise ParameterValidationError("Truncated or duplicate parameter")
            values[tag] = payload[offset : offset + length]
            offset += length
        return values

    def validate_ack(self, payload: bytes, port: int, tags: set[int]) -> None:
        payload = self._strip_port(payload, port)
        if len(payload) != len(tags) * 2:
            raise ParameterValidationError("Invalid acknowledgement length")
        results = dict(zip(payload[::2], payload[1::2], strict=True))
        if results.keys() != tags:
            raise ParameterValidationError("Missing command acknowledgement")
        if any(results.values()):
            raise CommandRejectedError("Controller rejected command")

    def decode_output(self, values: dict[int, bytes], old: PortState) -> PortState:
        required = {16, 18} if self.profile.separate_power else {16, 17, 18}
        if not required <= values.keys() or any(
            len(values[tag]) != 1 for tag in required
        ):
            raise ParameterValidationError("Missing or malformed mode parameters")
        mode, level_on = values[16][0], values[18][0] & 15
        if self.profile.packed_manual_level:
            level_on = values[18][0] >> 4
        if self.profile.separate_power:
            mode &= 15
        level_off = values[17][0] & 15 if 17 in required else 0
        if level_on > 10 or level_off > 10:
            raise ParameterValidationError("Invalid output level")
        return replace(
            old,
            mode=mode,
            level_on=level_on,
            level_off=level_off,
            raw_on_parameter=values[18][0],
            # GET returns configured levels, not actual output. Controller-wide
            # settings and output ramping can make telemetry differ from presets.
            level=old.level,
        )

    def output_values(
        self, on: bool, level: int | None, old: PortState | None = None
    ) -> dict[int, bytes]:
        if not self.profile.writable:
            raise ValueError("Device does not support output controls")
        if level is not None and (
            isinstance(level, bool)
            or not isinstance(level, int)
            or not 0 <= level <= 10
        ):
            raise ValueError("Level must be an integer between 0 and 10")
        # Home mode 1 is manual; 0x16 carries its independent power flag.
        values = (
            {22: bytes([int(on)])}
            if self.profile.separate_power
            else {16: bytes([ControllerMode.ON if on else ControllerMode.OFF])}
        )
        if on and level is not None:
            if self.profile.packed_manual_level:
                if old is None or old.raw_on_parameter is None:
                    raise ParameterValidationError(
                        "Read settings before changing the packed manual level"
                    )
                values[18] = bytes([(level << 4) | (old.raw_on_parameter & 15)])
            else:
                values[18] = bytes([level])
        return values

    def confirmed_output(
        self, old: PortState, on: bool, level: int | None
    ) -> PortState:
        """Apply only after ACK validation, preserving presets on mode-only writes."""
        return replace(
            old,
            mode=old.mode
            if self.profile.separate_power
            else ControllerMode.ON
            if on
            else ControllerMode.OFF,
            power=on if self.profile.separate_power else None,
            level=level
            if on and level is not None
            else old.level_on
            if on
            else old.level_off,
            level_on=level if on and level is not None else old.level_on,
            raw_on_parameter=(
                self.output_values(on, level, old)[18][0]
                if on and level is not None
                else old.raw_on_parameter
            ),
        )
