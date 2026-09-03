"""CRC-32C and raw-LZ4 primitives for Bugbear "bbag" files (.sgfi, .upgr, .ctms).

The containers store a bare LZ4 block (no frame, no stored decompressed size), so decoding
guesses an output capacity and grows it until the block fits. Compression is done through the
`lz4` package (the game accepts any valid block that decodes to the same bytes).
"""

from __future__ import annotations

import struct

CRC32C_POLY = 0x82F63B78

_crc32c_table = None


def _build_crc_table():
    global _crc32c_table
    if _crc32c_table is None:
        t = []
        for i in range(256):
            c = i
            for _ in range(8):
                c = (c >> 1) ^ (CRC32C_POLY if c & 1 else 0)
            t.append(c & 0xFFFFFFFF)
        _crc32c_table = t
    return _crc32c_table


def crc32c(data: bytes) -> int:
    """Castagnoli CRC-32C, poly 0x82F63B78, init/xorout 0xFFFFFFFF."""
    t = _build_crc_table()
    crc = 0xFFFFFFFF
    for b in data:
        crc = (crc >> 8) ^ t[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFFFFFF


def lz4_decode(block: bytes, dictionary: bytes = b"") -> bytes:
    """Decode a raw LZ4 block.

    If `dictionary` is given, match offsets may reach into it (LZ4 linked-block mode): the
    dictionary is laid immediately before the output, mirroring LZ4_decompress_safe_usingDict.
    """
    cap = max(64 * 1024, len(block) * 8)
    while cap <= 64 * 1024 * 1024:
        buf = bytearray(dictionary) + bytearray(cap)
        out_pos = len(dictionary)
        i = 0
        n = len(block)
        overflow = False
        try:
            while i < n:
                token = block[i]
                i += 1
                literals = token >> 4
                if literals == 15:
                    while True:
                        add = block[i]
                        i += 1
                        literals += add
                        if add != 255:
                            break
                if i + literals > n:
                    raise ValueError("truncated literals")
                if out_pos + literals > len(buf):
                    overflow = True
                    break
                buf[out_pos:out_pos + literals] = block[i:i + literals]
                i += literals
                out_pos += literals
                if i >= n:
                    break
                if i + 2 > n:
                    raise ValueError("truncated match offset")
                offset = block[i] | (block[i + 1] << 8)
                i += 2
                if offset == 0 or offset > out_pos:
                    raise ValueError("invalid match offset")
                match_len = token & 15
                if match_len == 15:
                    while True:
                        add = block[i]
                        i += 1
                        match_len += add
                        if add != 255:
                            break
                match_len += 4
                if out_pos + match_len > len(buf):
                    overflow = True
                    break
                frm = out_pos - offset
                for k in range(match_len):
                    buf[out_pos + k] = buf[frm + k]
                out_pos += match_len
        except IndexError:
            overflow = True
        if not overflow:
            return bytes(buf[len(dictionary):out_pos])
        cap *= 2
    raise ValueError("LZ4 decode exceeded size limit")


def lz4_encode(data: bytes) -> bytes:
    """Compress as a raw LZ4 block (no stored size), max compression."""
    if not data:
        raise ValueError("cannot compress empty payload")
    from lz4 import block as lz4block
    return lz4block.compress(bytes(data), mode="high_compression", compression=12, store_size=False)


def read_u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def write_u32(buf: bytearray, off: int, val: int) -> None:
    struct.pack_into("<I", buf, off, val)