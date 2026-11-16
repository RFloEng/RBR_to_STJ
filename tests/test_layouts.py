"""A reverse stage folder found beside its forward stage becomes a layout."""
from types import SimpleNamespace

from rbr2stj.convert import layouts
from rbr2stj.ir import Line, Timing
from rbr2stj.writers.stj_doc import layout_entry


def test_reverse_name_detection_and_pairing(tmp_path):
    for n in ("352-Passo Valle", "353-Passo Valle Reverse", "418-Pian_del_Colle", "419-Pian_del_Colle_Reversed",
              "410-Vargassence", "415-Vargassence Mirror", "500-Reverend Hill"):
        (tmp_path / n).mkdir()
    assert layouts.find_reverse_folders(tmp_path / "352-Passo Valle") == [tmp_path / "353-Passo Valle Reverse"]
    assert layouts.find_reverse_folders(tmp_path / "418-Pian_del_Colle") == [tmp_path / "419-Pian_del_Colle_Reversed"]
    assert layouts.find_reverse_folders(tmp_path / "410-Vargassence") == []      # a mirror is another map
    assert layouts.find_reverse_folders(tmp_path / "500-Reverend Hill") == []
    assert layouts.find_forward_folder(tmp_path / "353-Passo Valle Reverse") == tmp_path / "352-Passo Valle"


def test_layout_entry_carries_notes_and_direction():
    other = SimpleNamespace(line=None, timing=Timing(start=10.0, finish=90.0), notes=[{"s": 5.0}], spawn=None, edges=None)
    lay = layout_entry("reverse", "X Reverse", other, True, {"source": "x"})
    assert lay["direction"] == "reverse" and "reference" not in lay
    assert lay["race"]["pacenotes"]["notes"] == [{"s": 5.0}]
    assert lay["timing"]["start"] == {"s": 10.0} and lay["x_rbr"] == {"source": "x"}
