import pytest

from ac_infinity_ble.protocol import Protocol

from .frames import INVALID_RESPONSES, MODEL_RESPONSE, SET_RESPONSE, response_frame


def test_add_init():
    bp = Protocol()
    bytes = [0] * 3
    bp._add_init(bytes, 1, 342)
    assert bytes[1] == 1
    assert bytes[2] == 86


def test_add_head():
    bp = Protocol()
    result = bp._add_head([32, 33, 34, 36, 17, 18, 2], 1, 0)
    assert result == bytes(
        [
            165,
            0,
            0,
            7,
            0,
            0,
            41,
            169,
            0,
            1,
            32,
            33,
            34,
            36,
            17,
            18,
            2,
            193,
            119,
        ]
    )


@pytest.mark.parametrize("container", [bytes, bytearray])
@pytest.mark.parametrize(
    "frame,sequence,command",
    [(MODEL_RESPONSE, 0x1A66, 1), (SET_RESPONSE, 0x7695, 3)],
    ids=["model", "set"],
)
def test_parse_captured_response(frame, sequence, command, container):
    payload = Protocol().parse_response(container(frame), sequence, command)
    assert payload == frame[10:-2]
    assert isinstance(payload, bytes)


@pytest.mark.parametrize("sequence", [0, 1, 0x8000, 0xFFFF])
@pytest.mark.parametrize(
    "payload",
    [b"", b"\x10\x00", bytes(range(256))],
    ids=["empty", "short", "256-bytes"],
)
def test_parse_response_lengths_and_unsigned_sequences(sequence, payload):
    frame = response_frame(payload, sequence)
    assert Protocol().parse_response(frame, sequence, 3) == payload


@pytest.mark.parametrize(
    "frame",
    [frame for frame, _ in INVALID_RESPONSES],
    ids=[name for _, name in INVALID_RESPONSES],
)
def test_parse_response_rejects_invalid_or_unrelated_frames(frame):
    with pytest.raises(ValueError):
        Protocol().parse_response(frame, 0x7695, 3)


def test_parse_response_optional_expectations():
    assert Protocol().parse_response(SET_RESPONSE) == SET_RESPONSE[10:-2]
    with pytest.raises(ValueError, match="sequence"):
        Protocol().parse_response(SET_RESPONSE, expected_sequence=0)
    with pytest.raises(ValueError, match="command"):
        Protocol().parse_response(SET_RESPONSE, expected_command=0)
