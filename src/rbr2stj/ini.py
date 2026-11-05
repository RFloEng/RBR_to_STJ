"""Byte-lossy, case-insensitive, first-match INI reader plus the C-locale number scans BTB needs.

Behaviour follows what the game's GetPrivateProfileStringA-style lookups see (reRBR notes/formats.md "Stage files",
notes/btb.md "Sources"):
- text is bytes decoded lossy; a BOM is dropped;
- `;` at line start is a comment; a `[Name]` line opens a section (text after `]` is ignored);
- headers count only at the start of a line, so `69[MAP1]` glued to the end of a data line is not a section;
- lines without `=` or with an empty key are skipped; values are trimmed (quotes kept);
- sections and keys are matched case-insensitively and the FIRST match wins (duplicate sections are kept in
  `sections` but lookups see the first).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Section:
    name: str
    entries: list[tuple[str, str]] = field(default_factory=list)

    def get(self, key: str) -> str | None:
        k = key.lower()
        for name, value in self.entries:
            if name.lower() == k:
                return value
        return None


def decode(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    return text[1:] if text.startswith("\ufeff") else text


def parse(data: bytes | str) -> list[Section]:
    text = decode(data) if isinstance(data, (bytes, bytearray)) else data
    sections: list[Section] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and "]" in line:
            sections.append(Section(line[1:line.index("]")].strip()))
            continue
        if "=" not in line or not sections:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            sections[-1].entries.append((key, value.strip()))
    return sections


def find_section(sections: list[Section], name: str) -> Section | None:
    n = name.lower()
    for s in sections:
        if s.name.lower() == n:
            return s
    return None


def get(sections: list[Section], section: str, key: str) -> str | None:
    s = find_section(sections, section)
    return None if s is None else s.get(key)


def unquote(v: str | None) -> str | None:
    return None if v is None else v.strip().strip('"').strip()


# ── numbers ──────────────────────────────────────────────────────────────────

def _float_prefix(s: str) -> int | None:
    """Length of the longest `[+-]digits[.digits][e[+-]digits]` prefix, or None (C `%f` scan)."""
    i = 0
    if s[:1] in ("+", "-"):
        i = 1
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    intd = j - i
    frac = 0
    if j < len(s) and s[j] == ".":
        k = j + 1
        while k < len(s) and s[k].isdigit():
            k += 1
        frac = k - j - 1
        if intd > 0 or frac > 0:
            j = k
    if intd + frac == 0:
        return None
    if j < len(s) and s[j] in "eE":
        k = j + 1
        if k < len(s) and s[k] in "+-":
            k += 1
        e0 = k
        while k < len(s) and s[k].isdigit():
            k += 1
        if k > e0:
            j = k
    return j


def scan_float(s: str) -> float | None:
    """`sscanf(s, "%f")` in the C locale: `2679,576` reads 2679 (the comma stops the scan)."""
    s = s.lstrip()
    n = _float_prefix(s)
    return None if n is None else float(s[:n])


def scan_col_box(s: str) -> tuple[float, float, float] | None:
    """`sscanf(s, "%f, %f, %f")`: `0,15, 1, 0,15` reads (0, 15, 1). None when fewer than three numbers read."""
    rest = s
    out: list[float] = []
    for i in range(3):
        if i > 0:
            if not rest.startswith(","):
                return None
            rest = rest[1:]
        rest = rest.lstrip()
        n = _float_prefix(rest)
        if n is None:
            return None
        out.append(float(rest[:n]))
        rest = rest[n:]
    return (out[0], out[1], out[2])


def decimal_floats(s: str) -> list[float]:
    """Numbers in a string where a comma between digits is a decimal comma (`0,4235294`), as in materials.ini
    colours. Separators are whitespace/semicolons. Used only for values the game does not scan in the C locale."""
    out: list[float] = []
    for tok in s.replace(";", " ").split():
        try:
            out.append(float(tok.replace(",", ".")))
        except ValueError:
            pass
    return out


def data_cells(value: str) -> list[int]:
    """One mat.ini `dataN` row: 16 whitespace-separated `atoi` tokens, first 16 kept, missing = 0, wrapped to u8."""
    cells = [0] * 16
    for i, tok in enumerate(value.split()[:16]):
        t = tok.lstrip("+-")
        digits = ""
        for ch in t:
            if not ch.isdigit():
                break
            digits += ch
        v = int(digits) if digits else 0
        if tok.startswith("-"):
            v = -v
        cells[i] = v & 0xFF
    return cells
