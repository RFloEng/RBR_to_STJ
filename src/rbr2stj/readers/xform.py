"""BTB `.xform` placement files: an array of 4x4 row-major f32 matrices, Direct3D order (translation = elements
12..14). Format: reRBR notes/btb.md "Meshes". A length that is not a multiple of 64 bytes is an error."""

from __future__ import annotations

import struct

from ..errors import FormatError


def parse(data: bytes, label: str = ".xform") -> list[tuple[float, ...]]:
    if len(data) % 64:
        raise FormatError(f"{label}: length {len(data)} is not a multiple of one 4x4 matrix (64 bytes)")
    return [struct.unpack_from("<16f", data, o) for o in range(0, len(data), 64)]


def pack(matrices) -> bytes:
    """Inverse of parse, for the test fixtures."""
    return b"".join(struct.pack("<16f", *m) for m in matrices)
