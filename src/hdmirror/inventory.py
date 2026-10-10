"""Scan the local archive tree ``<root>/<PAIR>/HISTDATA_COM_ASCII_<PAIR>_T<YYYYMM>.zip``."""

from __future__ import annotations

import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

ZIP_RE = re.compile(r"^HISTDATA_COM_ASCII_([A-Z0-9]+)_T(\d{6})\.zip$")


def zip_name(pair: str, month: str) -> str:
    return f"HISTDATA_COM_ASCII_{pair.upper()}_T{month}.zip"


def csv_name(pair: str, month: str) -> str:
    return f"DAT_ASCII_{pair.upper()}_T_{month}.csv"


def zip_path(root: Path, pair: str, month: str) -> Path:
    return root / pair.upper() / zip_name(pair, month)


def is_valid_zip(path: Path, pair: str, month: str) -> bool:
    """Cheap check: the archive opens and lists the expected CSV."""
    try:
        if path.stat().st_size == 0:
            return False
        with zipfile.ZipFile(path) as archive:
            return csv_name(pair, month) in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def scan(root: Path, pairs: list[str], quarantine_dir: Path | None = None) -> dict[str, dict[str, Path]]:
    """Return ``{pair: {month: path}}`` for valid archives of ``pairs``.

    Invalid archives are moved to ``quarantine_dir`` (when given) so they
    count as missing and get downloaded again.
    """
    found: dict[str, dict[str, Path]] = {}
    for pair in pairs:
        found[pair] = {}
        pair_dir = root / pair
        if not pair_dir.is_dir():
            continue
        for path in sorted(pair_dir.iterdir()):
            match = ZIP_RE.match(path.name)
            if not match or match.group(1) != pair:
                continue
            month = match.group(2)
            if is_valid_zip(path, pair, month):
                found[pair][month] = path
            elif quarantine_dir is not None:
                quarantine(path, quarantine_dir / pair)
    return found


def quarantine(path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    target = dest_dir / f"{path.name}.{stamp}"
    path.replace(target)
    log.warning("quarantined invalid archive %s -> %s", path, target)
    return target
