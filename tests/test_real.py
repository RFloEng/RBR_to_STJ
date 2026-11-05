"""Real-data conversion. Skipped unless RBR_DATA (a retail install) or RBR_RSF (an RSF install) is set.

    RBR_DATA=<RBR install>  python -m pytest tests/test_real.py
    RBR_RSF=<RSF install>   python -m pytest tests/test_real.py

Every stage found is converted (retail `Maps/track-NN_<L>.trk`; RSF `Maps/<id>-<name>/`; BTB
`RX_CONTENT/Tracks/<folder>/`) into a temporary folder; the test fails listing every stage that could not be converted.
Stages whose files are RSF-encrypted are expected to be refused (ProtectedFileError) and are counted, not failed.
Nothing is ever written into the install.
"""
import os
from pathlib import Path

import pytest

from rbr2stj.convert import Options
from rbr2stj.errors import ProtectedFileError, Rbr2StjError
from rbr2stj.readers import btb, stage_files

DATA = os.environ.get("RBR_DATA")
RSF = os.environ.get("RBR_RSF")

pytestmark = pytest.mark.skipif(not (DATA or RSF), reason="set RBR_DATA and/or RBR_RSF to run the real-data tests")


def _classic_targets():
    roots = []
    if DATA:
        roots.append(Path(DATA) / "Maps")
    if RSF:
        maps = Path(RSF) / "Maps"
        roots += [d for d in maps.iterdir() if d.is_dir()] if maps.is_dir() else []
    for root in roots:
        if not root.is_dir():
            continue
        for stem, letters in stage_files.variants_in(root).items():
            yield root / f"{stem}_{letters[0]}.trk"


def _btb_targets():
    if RSF:
        tracks = Path(RSF) / "RX_CONTENT" / "Tracks"
        if tracks.is_dir():
            yield from (d for d in sorted(tracks.iterdir()) if btb.is_btb_folder(d))


def test_convert_every_stage(tmp_path):
    from rbr2stj.convert.btb import convert_btb
    from rbr2stj.convert.classic import convert_classic
    from rbr2stj.writers import package
    failures, protected, ok = [], 0, 0
    opt = Options(no_geometry=False)
    for n, trk in enumerate(_classic_targets()):
        try:
            st = convert_classic(trk, opt)
            package.write_package([st], tmp_path / f"c{n}", opt, validate=False)
            ok += 1
        except ProtectedFileError:
            protected += 1
        except Exception as e:  # noqa: BLE001 - collect everything, report at the end
            failures.append(f"{trk}: {type(e).__name__}: {e}")
    for n, folder in enumerate(_btb_targets()):
        try:
            st = convert_btb(folder, opt)
            package.write_package([st], tmp_path / f"b{n}", opt, validate=False)
            ok += 1
        except ProtectedFileError:
            protected += 1
        except Rbr2StjError as e:
            failures.append(f"{folder}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(f"{folder}: {type(e).__name__}: {e}")
    print(f"converted {ok}, refused (protected) {protected}, failed {len(failures)}")
    assert not failures, "\n".join(failures)
