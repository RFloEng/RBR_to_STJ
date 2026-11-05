"""Little-endian reader with bounds checks and clear errors.

Every read that would pass the end of the buffer raises FormatError with the file offset, so a truncated
or corrupt file never produces a bare IndexError or struct.error.
"""

from __future__ import annotations

import struct

from .errors import FormatError


class Reader:
    """A cursor over bytes. `label` names the file or chunk in error messages."""

    def __init__(self, data: bytes | memoryview, pos: int = 0, end: int | None = None, label: str = "data"):
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end
        self.label = label
        if not 0 <= pos <= self.end <= len(data):
            raise FormatError(f"{label}: offset {pos} is outside the {len(data)}-byte buffer")

    def remaining(self) -> int:
        return self.end - self.pos

    def eof(self) -> bool:
        return self.pos >= self.end

    def need(self, n: int) -> None:
        if n < 0 or self.pos + n > self.end:
            raise FormatError(f"{self.label}: truncated at offset {self.pos}: need {n} bytes, {max(self.end - self.pos, 0)} left")

    def take(self, n: int) -> bytes:
        self.need(n)
        out = bytes(self.data[self.pos:self.pos + n])
        self.pos += n
        return out

    def skip(self, n: int) -> None:
        self.need(n)
        self.pos += n

    def _unpack(self, fmt: str, size: int):
        self.need(size)
        vals = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return vals

    def u8(self) -> int:
        return self._unpack("<B", 1)[0]

    def u16(self) -> int:
        return self._unpack("<H", 2)[0]

    def u32(self) -> int:
        return self._unpack("<I", 4)[0]

    def i32(self) -> int:
        return self._unpack("<i", 4)[0]

    def f32(self) -> float:
        return self._unpack("<f", 4)[0]

    def f32s(self, n: int) -> tuple[float, ...]:
        return self._unpack(f"<{n}f", 4 * n)

    def u32s(self, n: int) -> tuple[int, ...]:
        return self._unpack(f"<{n}I", 4 * n)

    def vec3(self) -> tuple[float, float, float]:
        return self._unpack("<3f", 12)

    def cstr(self) -> bytes:
        """Bytes up to (not including) a NUL; the NUL is consumed."""
        i = bytes(self.data[self.pos:self.end]).find(b"\0")
        if i < 0:
            raise FormatError(f"{self.label}: missing NUL terminator for the string at offset {self.pos}")
        out = bytes(self.data[self.pos:self.pos + i])
        self.pos += i + 1
        return out


def u32_at(data: bytes, offset: int, label: str = "data") -> int:
    if offset < 0 or offset + 4 > len(data):
        raise FormatError(f"{label}: truncated: u32 at offset {offset} in {len(data)} bytes")
    return struct.unpack_from("<I", data, offset)[0]


def lossy(b: bytes) -> str:
    """Bytes as text, as the game reads names: not UTF-8 validated; undecodable bytes become U+FFFD."""
    return b.decode("utf-8", errors="replace")
