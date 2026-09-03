"""profile.sgfi container + chunk-chain + integrity handling.

Format (reverse-engineered, verified end-to-end by the community project
wreckfest2-tuning-editor):

  * 20-byte header + raw LZ4 block(s).
  * The decompressed payload is a root node + a chain of chunks.
  * Each chunk is itself a nested container (header + LZ4) plus an optional trailer holding
    continuation LZ4 blocks for payloads larger than 64 KiB, plus a trailing chunk CRC.
  * Four integrity layers must be recomputed on write: container header CRCs, chunk CRCs,
    outer compressedLength, outer header CRC.
"""

from __future__ import annotations

import struct

from .lz4 import crc32c, lz4_decode, lz4_encode, read_u32, write_u32

BLOCK_SIZE = 64 * 1024
HEADER_SIZE = 20


class BbagContainer:
    """A 20-byte header + a raw LZ4 block (block 1 of possibly several)."""

    def __init__(self, root_value, tag, reserved, compressed, expected_crc):
        self.root_value = root_value
        self.tag = tag
        self.reserved = reserved
        self.compressed = bytes(compressed)
        self.expected_crc = expected_crc
        self._content = None
        self.is_modified = False

    @property
    def content(self):
        return self._content

    @content.setter
    def content(self, val):
        self._content = bytes(val)

    @classmethod
    def parse(cls, raw, off=0):
        root_value = read_u32(raw, off)
        tag = raw[off + 4:off + 8].decode("latin1")
        reserved = read_u32(raw, off + 8)
        compressed_length = read_u32(raw, off + 12)
        expected_crc = read_u32(raw, off + 16)
        compressed = bytes(raw[off + 20:off + 20 + compressed_length])
        c = cls(root_value, tag, reserved, compressed, expected_crc)
        c.content = lz4_decode(compressed)
        return c

    def serialize(self):
        if self.is_modified:
            payload = lz4_encode(self.content)
        else:
            payload = self.compressed
        buf = bytearray(HEADER_SIZE + len(payload))
        write_u32(buf, 0, self.root_value)
        buf[4:8] = self.tag.encode("latin1")
        write_u32(buf, 8, self.reserved)
        write_u32(buf, 12, len(payload))
        write_u32(buf, 16, crc32c(self.content))
        buf[20:] = payload
        return bytes(buf)


class SaveChunk:
    def __init__(self, container, trailer, stored_crc, stored_crc_valid):
        self.container = container
        self.trailer = bytes(trailer)
        self.stored_crc = stored_crc
        self.stored_crc_valid = stored_crc_valid

    @property
    def tag(self):
        return self.container.tag

    def continuation_blocks(self):
        """[(compressed, crc_of_decoded)] parsed from the trailer."""
        result = []
        pos = 0
        while pos + 8 <= len(self.trailer):
            length = read_u32(self.trailer, pos)
            crc = read_u32(self.trailer, pos + 4)
            if length <= 0 or pos + 8 + length > len(self.trailer):
                break
            result.append((bytes(self.trailer[pos + 8:pos + 8 + length]), crc))
            pos += 8 + length
        return result

    @property
    def decoded_payload(self):
        # Always return the COMPLETE logical payload: the container block plus any continuation
        # blocks in the trailer. (Must not short-circuit on is_modified — a chunk rewritten by
        # set_decoded_payload holds its first block in Content and the rest in the trailer, and
        # re-reading it must still yield the whole payload.)
        payload = self.container.content
        for compressed, stored_crc in self.continuation_blocks():
            try:
                decoded = lz4_decode(compressed, payload)
            except ValueError:
                break
            if crc32c(decoded) != stored_crc:
                break
            payload = payload + decoded
        return payload

    def set_decoded_payload(self, payload):
        if not payload:
            raise ValueError("cannot set empty chunk payload")
        cont_end = 0
        pos = 0
        while pos + 8 <= len(self.trailer):
            length = read_u32(self.trailer, pos)
            if length <= 0 or pos + 8 + length > len(self.trailer):
                break
            pos += 8 + length
        cont_end = pos
        residual = bytes(self.trailer[cont_end:])

        first = min(BLOCK_SIZE, len(payload))
        self.container.content = bytes(payload[:first])
        self.container.is_modified = True

        rebuilt = bytearray()
        off = first
        while off < len(payload):
            length = min(BLOCK_SIZE, len(payload) - off)
            slice_ = bytes(payload[off:off + length])
            comp = lz4_encode(slice_)
            rebuilt += struct.pack("<II", len(comp), crc32c(slice_))
            rebuilt += comp
            off += length
        rebuilt += residual
        self.trailer = bytes(rebuilt)

    def serialize(self):
        return self.container.serialize() + self.trailer


class SaveFile:
    def __init__(self, root_value, tag, reserved, stored_crc, chunks, root_trailer,
                 original_tree, original_compressed):
        self.root_value = root_value
        self.tag = tag
        self.reserved = reserved
        self.stored_crc = stored_crc
        self.root_node_tag = "ubas"
        self.root_node_kind = 0
        self.chunks = chunks
        self.root_trailer = root_trailer
        self._original_tree = original_tree
        self._original_compressed = original_compressed

    def cars_chunk(self):
        for c in self.chunks:
            if c.tag == "srcc":
                return c
        return None

    @staticmethod
    def parse(data):
        if len(data) < HEADER_SIZE:
            raise ValueError("file too small")
        tag = data[4:8].decode("latin1")
        if tag != "ifgs":
            raise ValueError(f"bad tag at 0x04: expected 'ifgs', got {tag!r}")
        root_value = read_u32(data, 0)
        reserved = read_u32(data, 8)
        compressed_length = read_u32(data, 12)
        stored_crc = read_u32(data, 16)
        if compressed_length != len(data) - HEADER_SIZE:
            raise ValueError("length field mismatch")
        compressed = bytes(data[HEADER_SIZE:])
        tree = lz4_decode(compressed)
        if crc32c(tree) != stored_crc:
            raise ValueError("outer CRC mismatch")

        root_node_tag = tree[0:4].decode("latin1")
        root_node_kind = read_u32(tree, 4)
        chunk_count = read_u32(tree, 8)
        if chunk_count > 1024:
            raise ValueError(f"chunk count {chunk_count} is implausible")

        chunks = []
        pos = 12
        for _ in range(chunk_count):
            declared = read_u32(tree, pos)
            pos += 4
            chunk_length = declared - 4  # field counts chunk + its CRC
            chunk_bytes = bytes(tree[pos:pos + chunk_length])
            container = BbagContainer.parse(chunk_bytes, 0)
            trailer = chunk_bytes[HEADER_SIZE + len(container.compressed):]
            chunk_crc = read_u32(tree, pos + chunk_length)
            chunks.append(SaveChunk(container, trailer, chunk_crc,
                                    crc32c(chunk_bytes) == chunk_crc))
            pos += chunk_length + 4
        root_trailer = bytes(tree[pos:])
        return SaveFile(root_value, tag, reserved, stored_crc, chunks, root_trailer,
                        tree, compressed)

    def build_tree(self):
        bodies = [c.serialize() for c in self.chunks]
        total = 12 + len(self.root_trailer) + sum(4 + len(b) + 4 for b in bodies)
        tree = bytearray(total)
        tree[0:4] = b"ubas"
        write_u32(tree, 4, self.root_node_kind)
        write_u32(tree, 8, len(self.chunks))
        pos = 12
        for b in bodies:
            write_u32(tree, pos, len(b) + 4)
            pos += 4
            tree[pos:pos + len(b)] = b
            write_u32(tree, pos + len(b), crc32c(b))
            pos += len(b) + 4
        tree[pos:pos + len(self.root_trailer)] = self.root_trailer
        return bytes(tree)

    def serialize(self):
        tree = self.build_tree()
        payload = self._original_compressed if tree == self._original_tree else lz4_encode(tree)
        buf = bytearray(HEADER_SIZE + len(payload))
        write_u32(buf, 0, self.root_value)
        buf[4:8] = self.tag.encode("latin1")
        write_u32(buf, 8, self.reserved)
        write_u32(buf, 12, len(payload))
        write_u32(buf, 16, crc32c(tree))
        buf[HEADER_SIZE:] = payload
        return bytes(buf)