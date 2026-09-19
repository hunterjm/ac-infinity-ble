"""Captured frames and independently checksummed response fixtures."""

from binascii import crc_hqx

# Captures from Viss/ha-ac-infinity-ble, tests/test_component.py at
# 362c30a40eab2dd29e73aed1aac5094b7d71b8eb.
MODEL_RESPONSE = bytes.fromhex(
    "a51300301a6663cc0001100101110105120105130700c25a200064001404"
    "0000000015040000000016080000000000000000170400000000ff00c513"
)
SET_RESPONSE = bytes.fromhex("a5130006769585f2000310001200ff011629")
TELEMETRY = bytes.fromhex(
    "1eff0209031c0000095e1a4d005c0000001213910012ffff0001ffff0001ffff0001"
)


def response_frame(payload=b"\x10\x00", sequence=0x7695, command=3):
    """Build frames without using the implementation's CRC or packet builder."""
    header = b"\xa5\x13" + len(payload).to_bytes(2, "big")
    header += sequence.to_bytes(2, "big")
    body = bytes((0, command)) + payload
    return (
        header
        + crc_hqx(header, 0xFFFF).to_bytes(2, "big")
        + body
        + crc_hqx(body, 0xFFFF).to_bytes(2, "big")
    )


def corrupt(data, offset):
    result = bytearray(data)
    result[offset] ^= 1
    return bytes(result)


INVALID_RESPONSES = [
    (b"", "empty"),
    (b"\xa5", "one-byte"),
    (SET_RESPONSE[:11], "short-header"),
    (corrupt(SET_RESPONSE, 0), "wrong-magic"),
    (corrupt(SET_RESPONSE, 1), "wrong-frame-type"),
    (corrupt(SET_RESPONSE, 2), "wrong-length-high-byte"),
    (corrupt(SET_RESPONSE, 3), "wrong-length-low-byte"),
    (SET_RESPONSE[:-1], "truncated"),
    (SET_RESPONSE + b"\x00", "trailing-byte"),
    (corrupt(SET_RESPONSE, 6), "header-crc-high-byte"),
    (corrupt(SET_RESPONSE, 7), "header-crc-low-byte"),
    (corrupt(SET_RESPONSE, 8), "body-prefix-corruption"),
    (corrupt(SET_RESPONSE, 10), "payload-corruption"),
    (corrupt(SET_RESPONSE, -2), "body-crc-high-byte"),
    (corrupt(SET_RESPONSE, -1), "body-crc-low-byte"),
    (response_frame(sequence=0x7595), "wrong-sequence-high-byte"),
    (response_frame(sequence=0x7694), "stale-sequence"),
    (response_frame(command=1), "wrong-command"),
    (b"\x1e\xff", "short-telemetry"),
    (TELEMETRY[:17], "truncated-telemetry"),
]
