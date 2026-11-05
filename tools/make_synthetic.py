"""Write the synthetic test stages (a classic stage and a BTB folder) so the converter can be tried without a game
install:

    python tools/make_synthetic.py out/synthetic
    rbr2stj classic "out/synthetic/classic/101-Test Stage" -o out/test_stage --all-variants
    rbr2stj btb "out/synthetic/btb/Synthetic BTB" -o out/test_btb

The files are built by the test-only writers in tests/fixtures/build.py from the documented layouts: they are not
game data and may be shared freely.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.fixtures import build  # noqa: E402


def main(argv):
    out = Path(argv[1] if len(argv) > 1 else "out/synthetic")
    build.build_classic_stage(out / "classic" / "101-Test Stage", letters=("M", "N"))
    build.build_btb_stage(out / "btb" / "Synthetic BTB")
    print(f"wrote {out / 'classic' / '101-Test Stage'} and {out / 'btb' / 'Synthetic BTB'}")


if __name__ == "__main__":
    main(sys.argv)
