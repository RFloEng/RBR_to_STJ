"""Protection checks: rbr2stj reads only unprotected files and never decrypts anything.

RallySimFans encrypts some stage files. A file that starts `01 a0 00 00` (`.ini`: `01 a1 00 00`) is such a
file; `21 19 19 19` is an obscured `.lbs` (RSF stage 500). Format: reRBR notes/formats.md "RSF cars" and
`stage_files::encrypted`. We refuse with a clear message and do not try to decode them.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ProtectedFileError

ENCRYPTED = bytes([0x01, 0xA0, 0x00, 0x00])
ENCRYPTED_INI = bytes([0x01, 0xA1, 0x00, 0x00])
OBSCURED_LBS = bytes([0x21, 0x19, 0x19, 0x19])


def is_protected(head: bytes) -> bool:
    return head.startswith((ENCRYPTED, ENCRYPTED_INI, OBSCURED_LBS))


def refusal(name: str) -> str:
    return (f"{name}: this file is encrypted by RallySimFans. rbr2stj reads only files you may use and never "
            "decrypts or decodes protected content, so this stage is not supported.")


def check_bytes(data: bytes, name: str) -> bytes:
    """Return `data` unchanged, or raise ProtectedFileError if it starts like a protected file."""
    if is_protected(data[:4]):
        raise ProtectedFileError(refusal(name))
    return data


def read_file(path: str | Path) -> bytes:
    """Read a game file after the protection check (reads the whole file; stage files are small)."""
    p = Path(path)
    data = p.read_bytes()
    return check_bytes(data, p.name)
