# hdmirror

Keeps a local mirror of [HistData.com](http://www.histdata.com) ASCII tick
archives, plus a Parquet copy of each month.

Forked from [dmidlo/histdata.com-tools](https://github.com/dmidlo/histdata.com-tools)
and cut down to just the mirroring. The download flow (read the month page's
`file_down` form, POST it to `get.php`) comes from that project. The upstream
history is kept on `main`; this package lives on the `mirror` branch.

*Not affiliated with HistData.com. If you use it heavily, consider supporting
them through their [FTP access](http://www.histdata.com/download-by-ftp/).*

## What it does

- Scans `<root>/<PAIR>/HISTDATA_COM_ASCII_<PAIR>_T<YYYYMM>.zip` and finds the
  months that are missing from 2008-01 (configurable) through the current month.
- Queues them pair by pair, oldest first, and downloads **one file at a time**
  with a pause between files. A tqdm bar shows progress over the queue and over
  the bytes of each file.
- Writes each archive atomically and checks the CRCs before it replaces anything.
  Corrupt local archives are moved to `<root>/_quarantine/`.
- Writes `<root>/_parquet/<PAIR>/<PAIR>_T_<YYYYMM>.parquet` with columns
  `timestamp` (UTC), `bid`, `ask` and `volume`. The original zips are kept.
- Records every month in `<root>/.mirror_state.json`: status, size, sha256,
  HistData's `Updated at` value and any error. Months HistData has no file for are
  marked `absent` and re-probed after 90 days.
- Sends a desktop notification (`notify-send`) when a run ends. Runs with
  failures are sent as critical.

## Install

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[test]'
mkdir -p ~/.config/hdmirror && cp mirror.example.toml ~/.config/hdmirror/mirror.toml
```

## Use

```sh
hdmirror status                      # coverage table, nothing downloaded
hdmirror status -v -p EURUSD         # also list the missing months
hdmirror backfill -p GBPUSD EURUSD   # fill every hole, in this pair order
hdmirror backfill --dry-run          # print the queue only
hdmirror maintain                    # weekly job (see below)
hdmirror convert [--force]           # write missing/outdated Parquet files
```

`maintain` does three things:

1. Downloads any month still missing, including newly published ones.
2. Re-checks the last `recheck_months` months that are present locally (the
   current month and the previous one). If HistData's `Updated at` is newer than
   the copy on disk, it downloads the month again and overwrites the zip and the
   Parquet file.
3. Retries months that failed before.

HistData publishes the in-progress month about once a week, between Sunday and
Monday (EST), so the file ends at the previous Friday's close. `maintain` can
only be as fresh as that publish. The last days of a month arrive with the first
publish of the next month, which is why the previous month is re-checked too.

Only one `backfill`/`maintain` can run at a time, enforced with a lock on
`<root>/.mirror.lock`. Exit codes: 0 means success, 1 means some files failed,
130 means the run was interrupted, 75 means another run holds the lock. Logs go to `~/.local/state/hdmirror/hdmirror.log`.

## Weekly schedule (systemd user timer)

```sh
mkdir -p ~/.config/systemd/user
cp systemd/hdmirror.service systemd/hdmirror.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hdmirror.timer
loginctl enable-linger "$USER"        # optional: run even when not logged in
systemctl --user list-timers hdmirror.timer
journalctl --user -u hdmirror.service
```

The timer runs on Tuesdays at 06:00, after HistData's weekly publish. `Persistent=true` makes it catch up after
the machine was off.

## Tests

```sh
.venv/bin/pytest
```
