import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.fixtures import build  # noqa: E402


@pytest.fixture(scope="session")
def classic_stage(tmp_path_factory):
    return build.build_classic_stage(tmp_path_factory.mktemp("maps") / "101-Test Stage", letters=("M", "N"))


@pytest.fixture(scope="session")
def btb_stage(tmp_path_factory):
    return build.build_btb_stage(tmp_path_factory.mktemp("rx") / "Synthetic BTB")
