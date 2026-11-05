"""`.mat` surface-material grids.

Format: reRBR notes/formats.md "Stage files" (`.mat`) and the layout comment of crates/rbr-formats/src/mat.rs:

  u32 entry count; per entry: cstr name ("arctic standard dry new"), u32 run count,
  runs of 264 bytes {u32 width (16), u32 height (16), u8 cells[256]} - cells are surface ids.
  The sampler reads row 0 only: cells[clamp(floor(16 * a))]. Bytes after the last entry (a community `CMAP`
  block) are kept raw and not read.

An entry is selected by the first one whose name contains both the wear word (new/normal/worn) and the
wetness word (dry/damp/wet).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..binio import Reader, lossy

WEARS = ("new", "normal", "worn")
WETNESS = ("dry", "damp", "wet")
RECORD_LEN = 264


@dataclass
class Run:
    width: int
    height: int
    cells: bytes  # 256 surface ids, row-major; only row 0 (the first 16) is sampled


@dataclass
class Entry:
    name: str
    runs: list[Run] = field(default_factory=list)


@dataclass
class MatFile:
    entries: list[Entry]
    tail: bytes = b""


def parse(data: bytes) -> MatFile:
    r = Reader(data, label=".mat")
    n = r.u32()
    entries: list[Entry] = []
    for _ in range(n):
        name = lossy(r.cstr())
        runs = []
        for _ in range(r.u32()):
            r.need(RECORD_LEN)
            w, h = r.u32(), r.u32()
            runs.append(Run(w, h, r.take(256)))
        entries.append(Entry(name, runs))
    return MatFile(entries, bytes(data[r.pos:]))


def select(entries: list[Entry], wear: str, wetness: str) -> int | None:
    """Index of the first entry whose name contains both words (`mat::select` in the original loader)."""
    for i, e in enumerate(entries):
        if wear in e.name and wetness in e.name:
            return i
    return None


def surface_at(entry: Entry, run_index: int, nibble: int) -> int:
    """Surface id for a triangle corner: `cells[clamp(floor(16 * a))]` with `a = nibble / 15` (row 0)."""
    if run_index >= len(entry.runs):
        return 0
    cell = min(int(16 * (nibble & 0xF) / 15.0), 15)
    return entry.runs[run_index].cells[cell]
