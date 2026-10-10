import io
import zipfile
from pathlib import Path

import pytest

from hdmirror.config import Config
from hdmirror.inventory import csv_name, zip_name

ROWS = "20260101 170401135,1.173870,1.175320,0\n20260101 170437436,1.174140,1.175310,0\n"


def zip_bytes(pair: str, month: str, rows: str = ROWS) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(csv_name(pair, month), rows)
        archive.writestr(f"DAT_ASCII_{pair}_T_{month}.txt", "gap report")
    return buffer.getvalue()


def write_zip(root: Path, pair: str, month: str) -> Path:
    path = root / pair / zip_name(pair, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zip_bytes(pair, month))
    return path


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    root = tmp_path / "Hist Data"
    root.mkdir()
    return Config(root=root, pairs=["EURUSD"], delay_seconds=0, retries=2, log_file=tmp_path / "log")
