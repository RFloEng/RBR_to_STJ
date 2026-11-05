"""Pace-note tag/flag mapping (reRBR hud/pace.rs, gameplay/markers.rs) and the geometry cross-check."""
import math

import pytest

from rbr2stj.convert.pacenotes import RawNote, convert_note, convert_notes, direction_check
from rbr2stj.convert.project import RefLine


def conv(tag, flags=0, s=10.0):
    note, why = convert_note(RawNote(tag, flags, s))
    return note, why


@pytest.mark.parametrize("tag,kind,direction,severity,call", [
    (0, "hairpin", "left", None, "hairpin left"), (11, "hairpin", "right", None, "hairpin right"),
    (1, "square", "left", None, "90 left"), (10, "square", "right", None, "90 right"),
    (2, "turn", "left", 2, "K left"), (9, "turn", "right", 2, "K right"),
    (3, "turn", "left", 3, "medium left"), (8, "turn", "right", 3, "medium right"),
    (4, "turn", "left", 4, "fast left"), (7, "turn", "right", 4, "fast right"),
    (5, "turn", "left", 5, "easy left"), (6, "turn", "right", 5, "easy right"),
])
def test_corner_tags(tag, kind, direction, severity, call):
    n, _ = conv(tag)
    assert (n["kind"], n["direction"], n.get("severity"), n["call"]) == (kind, direction, severity, call)
    assert n["x_rbr"] == {"tag": tag, "flags": 0}


def test_flat_corners_have_severity_6_and_modifier():
    right, _ = conv(25)
    left, _ = conv(26)
    assert (right["kind"], right["severity"], right["direction"], right["modifiers"]) == ("turn", 6, "right", ["flat"])
    assert left["direction"] == "left" and left["call"] == "flat left"


@pytest.mark.parametrize("tag,kind,call", [
    (12, "twisty", "twisty"), (14, "narrows", "narrows"), (15, "widens", "wide out"), (16, "crest", "over crest"),
    (17, "water", "ford"), (18, "caution", "caution"), (19, "bump", "bump"), (20, "jump", "jump"),
    (27, "bridge", "bridge"), (28, "straight", "go straight")])
def test_feature_tags(tag, kind, call):
    n, _ = conv(tag)
    assert n["kind"] == kind and n["call"] == call and "direction" not in n


def test_keep_calls_order_and_caution_water():
    """Open question 5: tags 29/30/31 are keep right / left / middle (reRBR markers.rs slot_word_id 0x34/0x35/0x36)."""
    assert [conv(t)[0]["call"] for t in (29, 30, 31)] == ["keep right", "keep left", "keep middle"]
    assert conv(29)[0]["direction"] == "right" and conv(30)[0]["direction"] == "left"
    assert "direction" not in conv(31)[0] and conv(31)[0]["kind"] == "other"
    cw, _ = conv(32)
    assert cw["kind"] == "water" and cw["modifiers"] == ["caution"] and cw["call"] == "caution water"


def test_distance_call_and_timing_tags_are_not_notes():
    assert conv(13)[0] is None and "derived" in conv(13)[1]
    for tag in (21, 22, 23, 24):
        assert conv(tag)[1].startswith("timing:")
    assert conv(0xFEA)[0] is None and "plugin" in conv(0xFEA)[1]


def test_flag_bits_become_modifiers_and_wording():
    n, _ = conv(8, 0x400)
    assert n["modifiers"] == ["long"] and n["call"] == "medium right long"
    n, _ = conv(3, 0x1 | 0x2 | 0x4 | 0x20 | 0x40 | 0x80 | 0x2000)
    assert n["modifiers"] == ["narrows", "widens", "tightens", "dont_cut", "cut", "tightens_bad", "maybe"]
    n, _ = conv(3, 0x800 | 0x1000 | 0x10)
    assert n["modifiers"] == ["x_flag_0x10", "x_plus", "x_minus"]


def test_unmapped_flag_bits_stay_in_x_rbr():
    n, _ = conv(3, 0x100 | 0x8 | (5 << 16))
    assert n["x_rbr"]["unmapped_flags"] == 0x100 | 0x8 | (5 << 16) and "modifiers" not in n


def test_extended_id_uses_the_standin():
    # plugin note: 0x4000 puts the call id in bits 19-30; the tag's low 16 bits are the 1.02 stand-in (here medium left)
    n, _ = conv(3 | (7 << 16), 0x4000 | (0x123 << 19))
    assert n["call"] == "medium left" and n["x_rbr"]["extended_id"] == 0x123


def test_notes_are_sorted_and_skips_reported():
    raw = [RawNote(3, 0, 50.0), RawNote(13, 0, 10.0), RawNote(8, 0, 20.0), RawNote(21, 0, 1.0)]
    notes, skipped = convert_notes(raw)
    assert [n["s"] for n in notes] == [20.0, 50.0]
    assert [x["tag"] for x in skipped] == [13]          # timing tags are not listed as skipped


def _bend(sign):
    """A quarter circle turning left (sign +1) or right (-1), radius 50, preceded by a short straight."""
    pts = [(-20.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
    for k in range(1, 7):
        th = math.radians(15 * k)
        pts.append((50 * math.sin(th), sign * 50 * (1 - math.cos(th)), 0.0))
    return RefLine(pts)


def test_direction_check_agrees_on_a_left_bend_and_flags_a_mirrored_stage():
    left_note = [{"s": 20.0, "kind": "turn", "direction": "left"}]
    assert direction_check(left_note, _bend(+1)) == {"agree": 1, "disagree": 0, "unclear": 0}
    assert direction_check(left_note, _bend(-1)) == {"agree": 0, "disagree": 1, "unclear": 0}   # mirrored geometry
    straight = RefLine([(0, 0, 0), (100, 0, 0), (200, 0, 0)])
    assert direction_check(left_note, straight)["unclear"] == 1


def test_refline_projection_and_left_positive_t():
    line = RefLine([(0, 0, 0), (100, 0, 0), (200, 50, 0)], tangents=[(100, 0, 0), (100, 0, 0), (100, 50, 0)])
    s, t = line.project(50.0, 4.0)
    assert t == pytest.approx(4.0, abs=0.05) and s == pytest.approx(50.0, abs=0.5)      # left of travel: t > 0
    s, t = line.project(50.0, -3.0)
    assert t == pytest.approx(-3.0, abs=0.05)
    x, y, z = line.point(50.0, 4.0)
    assert (x, y) == pytest.approx((50.0, 4.0), abs=0.05)
