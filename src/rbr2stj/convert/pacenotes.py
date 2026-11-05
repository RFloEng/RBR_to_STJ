"""RBR pace-note keys -> STJ `race.pacenotes` notes.

Tag meanings: reRBR crates/rbr-render/src/hud/pace.rs (`symbol`, `label`), crates/rbr-gameplay/src/markers.rs
(`slot_word_id`, `flag_word_id`) and notes/formats.md "Markers"; the plugin's extended ids: pacenote/notes.rs.

Tags (the low 16 bits of a key's tag; the high bits of a plugin note carry a take/value):
  0-5 left corners  hairpin, 90, K, medium, fast, easy        6-11 right corners easy, fast, medium, K, 90, hairpin
  12 twisty  13 distance call  14 narrows  15 wide out  16 over crest  17 ford  18 caution  19 bump  20 jump
  21-24 start/finish/split/end (timing, not notes)  25 flat right  26 flat left  27 bridge  28 go straight
  29 keep right  30 keep left  31 keep middle  32 caution water
Flag bits (word 1): 0x1 narrows 0x2 wide out 0x4 tightens 0x10 (unnamed) 0x20 don't cut 0x40 cut 0x80 tightens bad
  0x400 long 0x800 plus 0x1000 minus 0x2000 maybe; plugin only: 0x8 take in bits 16-18, 0x100 sticky/no link,
  0x4000 extended id in bits 19-30. Bits we do not map stay in `x_rbr.flags`.

STJ severity: 1 = slowest short of a hairpin ... 6 = fastest still called, so K = 2, medium = 3, fast = 4, easy = 5,
flat = 6 with modifier `flat`. The tag order 0-5 is hairpin..easy so left tag t gives severity t (t >= 2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TAG_DISTANCE = 13
TIMING_TAGS = {21: "start", 22: "finish", 23: "split", 24: "end"}
EXTENDED = 0x4000

# flag bit -> STJ modifier (schema enum) or an x_ string
FLAG_MODIFIERS = [(0x1, "narrows"), (0x2, "widens"), (0x4, "tightens"), (0x10, "x_flag_0x10"), (0x20, "dont_cut"),
                  (0x40, "cut"), (0x80, "tightens_bad"), (0x400, "long"), (0x800, "x_plus"), (0x1000, "x_minus"),
                  (0x2000, "maybe")]
FLAG_WORDS = {0x1: "narrows", 0x2: "wide out", 0x4: "tightens", 0x10: "", 0x20: "don't cut", 0x40: "cut",
              0x80: "tightens bad", 0x400: "long", 0x800: "plus", 0x1000: "minus", 0x2000: "maybe"}
MAPPED_FLAGS = sum(b for b, _ in FLAG_MODIFIERS)

CORNER_LABELS = {0: "hairpin", 1: "90", 2: "K", 3: "medium", 4: "fast", 5: "easy"}


@dataclass
class RawNote:
    tag: int
    flags: int
    s: float          # already shifted to STJ s


def note_id(tag: int, flags: int) -> tuple[int, int | None]:
    """(call id used for mapping, extended id or None). A plugin note with 0x4000 holds the call id in flag bits
    19-30 and its 1.02 stand-in in the tag's low 16 bits; we map the stand-in so the note still reads on a 1.02 table."""
    standin = tag & 0xFFFF
    ext = (flags >> 19) & 0xFFF if flags & EXTENDED else None
    return standin, ext


def _modifiers(flags: int) -> list[str]:
    return [m for bit, m in FLAG_MODIFIERS if flags & bit]


def _suffix(flags: int) -> str:
    words = [FLAG_WORDS[bit] for bit, _ in FLAG_MODIFIERS if flags & bit and FLAG_WORDS[bit]]
    return (" " + " ".join(words)) if words else ""


def convert_note(n: RawNote):
    """(STJ note dict, None) or (None, reason) when the key is not a note (timing, distance call, plugin setting)."""
    tid, ext = note_id(n.tag, n.flags)
    x = {"tag": n.tag, "flags": n.flags}
    if ext is not None:
        x["extended_id"] = ext
    unmapped = n.flags & ~MAPPED_FLAGS
    if unmapped:
        x["unmapped_flags"] = unmapped
    note: dict = {"s": round(n.s, 3)}
    mods = _modifiers(n.flags)
    call = None
    if tid in TIMING_TAGS:
        return None, f"timing:{TIMING_TAGS[tid]}"
    if tid == TAG_DISTANCE:
        return None, "distance call (derived by the consumer)"
    if 0 <= tid <= 11:
        left = tid <= 5
        sev_tag = tid if left else 11 - tid               # 0 hairpin .. 5 easy
        direction = "left" if left else "right"
        label = CORNER_LABELS[sev_tag]
        note["direction"] = direction
        if sev_tag == 0:
            note["kind"] = "hairpin"
        elif sev_tag == 1:
            note["kind"] = "square"
        else:
            note["kind"] = "turn"
            note["severity"] = sev_tag
        call = f"{label} {direction}"
    elif tid in (25, 26):
        note.update(kind="turn", severity=6, direction="right" if tid == 25 else "left")
        mods.insert(0, "flat")
        call = "flat " + note["direction"]
    elif tid == 12:
        note["kind"], call = "twisty", "twisty"
    elif tid == 14:
        note["kind"], call = "narrows", "narrows"
    elif tid == 15:
        note["kind"], call = "widens", "wide out"
    elif tid == 16:
        note["kind"], call = "crest", "over crest"
    elif tid == 17:
        note["kind"], call = "water", "ford"
    elif tid == 18:
        note["kind"], call = "caution", "caution"
    elif tid == 19:
        note["kind"], call = "bump", "bump"
    elif tid == 20:
        note["kind"], call = "jump", "jump"
    elif tid == 27:
        note["kind"], call = "bridge", "bridge"
    elif tid == 28:
        note["kind"], call = "straight", "go straight"
    elif tid in (29, 30, 31):
        note["kind"] = "other"
        word = {29: "right", 30: "left", 31: "middle"}[tid]
        if tid != 31:
            note["direction"] = word
        call = f"keep {word}"
    elif tid == 32:
        note["kind"], call = "water", "caution water"
        mods.insert(0, "caution")
    else:
        return None, f"plugin or unknown call id {tid:#x}"
    if mods:
        note["modifiers"] = mods
    note["call"] = (call + _suffix(n.flags)).strip()
    note["x_rbr"] = x
    return note, None


def convert_notes(raw: list[RawNote]):
    """Sorted STJ notes plus what was skipped: [(tag, flags, s, reason)]."""
    notes, skipped = [], []
    for n in sorted(raw, key=lambda r: r.s):
        note, why = convert_note(n)
        if note is not None:
            notes.append(note)
        elif not why.startswith("timing:"):
            skipped.append({"tag": n.tag, "flags": n.flags, "s": round(n.s, 3), "reason": why})
    return notes, skipped


def direction_check(notes, line, window: float = 60.0, min_turn: float = math.radians(5)):
    """Compare each left/right note with the geometry that follows it: the heading change over `window` metres
    (positive = turning left). Returns {"agree": n, "disagree": n, "unclear": n}. A frame mistake (mirrored stage)
    would show up here as disagree >> agree."""
    out = {"agree": 0, "disagree": 0, "unclear": 0}
    for n in notes:
        d = n.get("direction")
        if d not in ("left", "right") or n["kind"] not in ("turn", "hairpin", "square"):
            continue
        s0 = n["s"]
        s1 = min(s0 + window, line.length)
        dh = (line.heading(s1) - line.heading(s0) + math.pi) % (2 * math.pi) - math.pi
        if abs(dh) < min_turn:
            out["unclear"] += 1
        elif (dh > 0) == (d == "left"):
            out["agree"] += 1
        else:
            out["disagree"] += 1
    return out
