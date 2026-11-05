"""Chunk framing shared by `.trk` and `.lbs`.

Format: reRBR notes/formats.md "Stage files": chunks `u32 header_len (8), u32 type, u32 subtype, u32 size, payload`
tiling the file. Unknown chunks are kept raw.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..binio import Reader
from ..errors import FormatError


@dataclass
class Chunk:
    header: bytes
    data: bytes

    @property
    def kind(self) -> tuple[int, int] | None:
        """(type, subtype) for the usual 8-byte header, else None."""
        if len(self.header) != 8:
            return None
        return (int.from_bytes(self.header[:4], "little"), int.from_bytes(self.header[4:], "little"))


def read_chunks(data: bytes, label: str = "file") -> list[Chunk]:
    r = Reader(data, label=label)
    out: list[Chunk] = []
    while not r.eof():
        hl = r.u32()
        header = r.take(hl)
        pl = r.u32()
        payload = r.take(pl)
        out.append(Chunk(header, payload))
    return out


def write_chunk(kind: tuple[int, int], payload: bytes) -> bytes:
    """Inverse of one chunk, for the test fixtures' writers."""
    import struct
    return struct.pack("<IIII", 8, kind[0], kind[1], len(payload)) + payload


__all__ = ["Chunk", "read_chunks", "write_chunk", "FormatError"]
