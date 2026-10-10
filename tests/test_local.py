import polars as pl

from conftest import write_zip
from hdmirror import convert, inventory
from hdmirror.state import OK, State


def test_scan_quarantines_invalid_archives(cfg):
    good = write_zip(cfg.root, "EURUSD", "202601")
    bad = cfg.root / "EURUSD" / "HISTDATA_COM_ASCII_EURUSD_T202602.zip"
    bad.write_bytes(b"")
    (cfg.root / "HISTDATA_COM_ASCII_EURUSD_M1202302.zip").write_bytes(b"ignored")
    found = inventory.scan(cfg.root, ["EURUSD"], cfg.quarantine_dir)
    assert found == {"EURUSD": {"202601": good}}
    assert not bad.exists()
    assert len(list((cfg.quarantine_dir / "EURUSD").iterdir())) == 1


def test_state_seed_and_round_trip(cfg):
    path = write_zip(cfg.root, "EURUSD", "202601")
    state = State(cfg.state_file)
    assert state.seed({"EURUSD": {"202601": path}}) == 1
    assert state.seed({"EURUSD": {"202601": path}}) == 0
    state.save()
    entry = State(cfg.state_file).get("EURUSD", "202601")
    assert entry["status"] == OK
    assert entry["size"] == path.stat().st_size
    assert len(entry["sha256"]) == 64


def test_convert_moves_timestamps_to_utc(cfg, tmp_path):
    path = write_zip(cfg.root, "EURUSD", "202601")
    out = convert.parquet_path(cfg.parquet_root, "EURUSD", "202601")
    assert convert.convert(path, out, "EURUSD", "202601") == 2
    frame = pl.read_parquet(out)
    assert frame.columns == ["timestamp", "bid", "ask", "volume"]
    assert str(frame["timestamp"][0]) == "2026-01-01 22:04:01.135000+00:00"
    assert frame["bid"][0] == 1.17387
    assert convert.is_current(path, out)
    assert path.exists()
