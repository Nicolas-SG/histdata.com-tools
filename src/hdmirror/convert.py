"""Convert tick archives to Parquet, leaving the original ZIPs untouched.

Output: ``<parquet_root>/<PAIR>/<PAIR>_T_<YYYYMM>.parquet`` with columns
``timestamp`` (UTC, ms), ``bid``, ``ask``, ``volume``. Values are written as
HistData publishes them; only the timestamp is moved from EST (no DST) to UTC.
"""

from __future__ import annotations

import os
import zipfile
from datetime import datetime
from pathlib import Path

import polars as pl

from hdmirror.inventory import csv_name

_SCHEMA = {"raw_time": pl.String, "bid": pl.Float64, "ask": pl.Float64, "volume": pl.Int64}


def parquet_path(parquet_root: Path, pair: str, month: str) -> Path:
    return parquet_root / pair / f"{pair}_T_{month}.parquet"


def is_current(zip_file: Path, parquet_file: Path) -> bool:
    """True when the Parquet file exists and is not older than the ZIP."""
    return parquet_file.exists() and parquet_file.stat().st_mtime >= zip_file.stat().st_mtime


def read_ticks(zip_file: Path, pair: str, month: str) -> pl.DataFrame:
    with zipfile.ZipFile(zip_file) as archive:
        raw = archive.read(csv_name(pair, month))
    frame = pl.read_csv(raw, has_header=False, new_columns=list(_SCHEMA), schema=_SCHEMA)
    timestamp = (
        pl.col("raw_time").str.strptime(pl.Datetime("ms"), "%Y%m%d %H%M%S%3f")
        + pl.duration(hours=5)
    ).dt.replace_time_zone("UTC")
    return frame.select(timestamp.alias("timestamp"), "bid", "ask", "volume")


def convert(zip_file: Path, parquet_file: Path, pair: str, month: str) -> int:
    """Write ``parquet_file`` from ``zip_file`` atomically; return the row count."""
    frame = read_ticks(zip_file, pair, month)
    parquet_file.parent.mkdir(parents=True, exist_ok=True)
    temp = parquet_file.with_name(f".{parquet_file.name}.{os.getpid()}.tmp")
    try:
        frame.write_parquet(temp, compression="zstd", statistics=True)
        os.replace(temp, parquet_file)
    finally:
        temp.unlink(missing_ok=True)
    return frame.height


def last_tick(parquet_file: Path) -> datetime:
    """Return the newest timestamp in a Parquet file."""
    return pl.scan_parquet(parquet_file).select(pl.col("timestamp").max()).collect().item()
