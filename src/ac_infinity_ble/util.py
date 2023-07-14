import ctypes


def get_short(b: bytes, i: int) -> int:
    return ctypes.c_int16((b[i + 1] & 255) | ((b[i] << 8) & 65280)).value


def get_bits(b: int, i: int, i2: int) -> int:
    return (b >> ((8 - i) - i2)) & (255 >> (8 - i2))


def get_bit(b: int, i: int) -> bool:
    return (b >> (7 - i)) & 1 == 0


def crc16(data: list[int], i: int | None = None, i2: int | None = None) -> list[int]:
    if i is None or i2 is None:
        i = 0
        i2 = len(data)

    b = 65535
    for i3 in range(i, i + i2):
        b2 = (((b << 8) | (b >> 8)) & 65535) ^ (data[i3] & 255)
        b3 = b2 ^ ((b2 & 255) >> 4)
        b4 = b3 ^ ((b3 << 12) & 65535)
        b = b4 ^ (((b4 & 255) << 5) & 65535)

    b5 = b & 65535
    return [((b5 >> 8) & 255), (b5 & 255)]
