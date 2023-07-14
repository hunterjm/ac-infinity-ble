from ac_infinity_ble.protocol import Protocol


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
