# tasks.md — HistData mirror for `/media/storage/Vegas_Files/Data bases`

Analysis date: 2026-10-10

## Objective

Fork `dmidlo/histdata.com-tools` and use it to keep a local copy of HistData.com
ASCII tick archives under `/media/storage/Vegas_Files/Data bases/Hist Data`.

Target behaviour:

1. Choose one or more target pairs.
2. Scan the local files for holes (missing months).
3. Queue the missing (or updated) files.
4. Download **one file at a time** and show a tqdm-style progress bar over the queue.
5. Two ways to run it:
   - **backfill**: an on-demand request to fill the full history for the given pairs;
   - **maintenance**: a scheduled weekly run that fetches newly published or updated months.

---

## Observations

### 1. The repository

- `origin` = `git@github.com:dmidlo/histdata.com-tools.git` (upstream, not ours yet). 729 commits, `main` branch, clean tree.
- **This is not the small upstream scraper any more.** It has about 348 Python files and **362k lines**:
  Temporal workflow orchestration (it downloads the `temporal` binary on first run),
  reconstruction/Hawkes/TPP research engines, data-quality suites, InfluxDB import,
  a dataset catalog, broker capture, and more. Most of it has nothing to do with mirroring files.
- **Only ASCII tick data is supported** (`histdata.com_data_specs.md`). M1 and the MT4/NT/Excel formats were deliberately removed.
  This matches what is already stored locally (tick zips).
- **Temporal orchestration is the default execution engine** (`options.py: orchestration_start = True`).
  For a single-machine, one-file-at-a-time mirror, that is a lot of moving parts: a runtime process, workers, and job state.
- `agents.MD` sets upstream workflow rules (GitHub issue first, work on `dev`, Commitizen, TestPyPI).
  These belong to upstream; we decide whether our fork follows them.
- Dependencies: `requests`, `rich`, `polars`, `temporalio`, `Rx`, `pytz`, `PyYAML`, … `tqdm` is **not** a dependency.
  Progress bars use `rich.progress`.
- The test suite exists (161 files under `tests/`) but was not run as part of this analysis.

### 2. How a download actually works (the only part we really need)

The working core is small. It is spread across `src/histdatacom/activity_stages.py` and `config.py`:

| Step | Code | Detail |
|---|---|---|
| Build page URL | `_dataset_url()` | `http://www.histdata.com/download-free-forex-data/?/ascii/tick-data-quotes/<pair>/<YYYY>/<M>` (month without leading zero) |
| GET page | `fetch_histdata_page_data()` | Plain `requests.get`. **Raises if the `Content-Encoding` header is missing**, which is fragile. |
| Parse form | `_HistDataDownloadFormParser`, `parse_histdata_form_metadata()` | Reads `<form id="file_down">` hidden inputs: `tk, date, datemonth, platform, timeframe, fxpair`. No form or no `tk` means "no data for this month". |
| POST archive | `post_histdata_archive()` | `POST http://www.histdata.com/get.php` with those 6 fields and `Referer = page URL` (headers in `config.POST_HEADERS`) |
| Filename | `archive_filename_from_response()` | From `Content-Disposition`, e.g. `HISTDATA_COM_ASCII_EURUSD_T202609.zip` |
| Write | `atomic_write_zip_archive()` | Writes a temp file, validates it as a zip, then renames it into place. This is good; keep it. |

- **Tick data is one zip per month**, including past years (the planner always emits `YYYY/M` paths).
- **Verified live today (2026-10-10):** the page for EURUSD 2026/9 returns HTTP 200, the `file_down` form has all 6 fields,
  and the page shows **`Updated at: 2026-10-04 12:45:27`**. This per-month timestamp can drive "updated file" detection in maintenance runs.
  Inferred from that date: a month is published a few days after it closes.
- Availability ranges come from `data/.repo`, a JSON file of `{pair: {start, end}}` fetched from
  `raw.githubusercontent.com/dmidlo/histdata.com-tools/main/data/.repo`.
  - The committed copy is **stale**: `end = 202606`, while the site already has 202609.
  - `start` is per pair **across all timeframes**, so tick data may start later than that date.
    The first months of a range may legitimately have no tick file. We should record those as *confirmed absent* instead of re-requesting them forever.
  - It lists 66 instruments (FX pairs plus indices and metals such as `xauusd`, `spxusd`, `wtiusd`, …).

### 3. Behaviours / bugs that matter for our use

- **Skip-if-exists:** `existing_archive_artifact()` reuses any zip/csv/cache already on disk.
  A file that HistData later re-publishes (compare `Updated at`) would **never be refreshed**. The weekly job needs its own check.
- **Timezone bug:** `utils.get_current_datemonth_gmt_minus5()` uses `pytz.timezone("Etc/GMT-5")`.
  In POSIX `Etc/` naming that is **UTC+5**, not EST, so near a month boundary the "current month" can be wrong.
  The fix is `Etc/GMT+5`. Our planner should simply target "last fully published month" and ignore this function.
- **Directory layout mismatch:** the tool stores files as `<root>/ASCII/T/<pair>/<YYYY>/<M>/<zip>`, and by default
  **extracts the CSV and deletes the zip** (`zip_persist = False`).
  The existing archive uses a flat `<PAIR>/HISTDATA_COM_ASCII_<PAIR>_T<YYYYMM>.zip`.
  Running the stock CLI against our folder would not recognise the 1,323 files already there and would download everything again.
- The hardcoded `Content-Length: 101` in `POST_HEADERS` is harmless (requests overwrites it) but sloppy.
- The scraper uses `http://`, not `https://`. That is how the site works today, but there is no TLS.
- There is no throttling or delay between requests. Our tool must be polite: one file at a time with a pause between files.
  HistData asks heavy users to support them via FTP access.

### 4. Current state of the local archive

`/media/storage/Vegas_Files/Data bases/Hist Data` contains 1,323 zips, 6.4 GB. **All 1,323 zips open, and each one contains a CSV.**
Disk: `/media/storage` has 918 GB free (84% used).

| Pair | Local range | Files | HistData range (`.repo`) | Missing vs `.repo` |
|---|---|---|---|---|
| AUDUSD | 200801–201812 | 132 | 200006–2026xx | 181 |
| EURAUD | 200801–201812 | 132 | 200208–2026xx | 155 |
| EURNZD | 200804–201503 | 84 | 200803–2026xx | 136 |
| EURUSD | 200801–202601 | 217 | 200005–2026xx | 97 |
| GBPAUD | 200801–201503 | 87 | 200709–2026xx | 139 |
| GBPNZD | 200804–201503 | 84 | 200803–2026xx | 136 |
| GBPUSD | 200801–201812 | 132 | 200005–2026xx | 182 |
| NZDUSD | 200801–201503 | 87 | 200508–2026xx | 164 |
| USDCAD | 200801–201812 | 132 | 200006–2026xx | 181 |
| USDCHF | 200801–201503 | 87 | 200005–2026xx | 227 |
| XAGUSD | 200905–201505 | 73 | 200905–2026xx | 133 |
| XAUUSD | 200903–201505 | 75 | 200903–2026xx | 133 |

- **No interior holes.** Every pair is contiguous between its first and last local month. The gaps are only at the
  **head** (before 2008; some of that may not exist as tick data) and the **tail** (2015/2018/2026-02 → now).
  The "missing" column is measured against the stale `.repo` end of 202606. The real tail is up to 202609.
- There is a stray file at the root: `HISTDATA_COM_ASCII_EURUSD_M1202302.zip` (M1, not tick). Move it aside or ignore it.
- `EURUSD/..._T202511.zip` has an mtime of today (2026-10-10 09:22). Something touched it recently; worth knowing.
- Size estimate: about 5 MB per month on average (recent EURUSD months are 8–10 MB).
  Filling these 12 pairs back to their `.repo` start is roughly 1,900 files, about 10–15 GB. All 66 instruments would be on the order of 100 GB.

---

## Recommendation

**Do not drive the mirror through the stock `histdatacom` CLI.**
Its Temporal runtime, CSV-extraction default and nested directory layout all work against the goal.
Instead, add a **small, standalone sync module inside the fork** (`src/histdatacom/mirror/` or a top-level `hdmirror/`).
It should reuse or copy the ~150 lines that matter (form parser, GET, POST, atomic zip write).
Its only dependencies should be `requests` + `tqdm`.
Keep the existing flat layout so the 1,323 files already on disk count as present.

Keeping it in the fork, rather than as a separate script, makes it easy to pull upstream fixes to the scraping
logic (for example if HistData changes its form) while ignoring the rest.

---

## Tasks

Status as of 2026-10-10. The package is `hdmirror` on the `mirror` branch. Everything unrelated to mirroring was
removed from that branch; upstream history stays on `main`.

### A. Fork & environment
- [x] Fork on GitHub: https://github.com/Nicolas-SG/histdata.com-tools (created 2026-10-10 with `gh`).
  `origin` = fork, `upstream` = dmidlo. The `mirror` branch is pushed and tracks `origin/mirror`.
- [x] Working branch `mirror`. The upstream `agents.MD` rules are not followed in the fork (file removed).
- [x] venv: `uv venv --python 3.12 .venv` and `uv pip install -e '.[test]'`. The system Python has no pip/ensurepip, so `uv` is installed in `~/.local/bin`.
  The upstream test baseline was skipped because that code is gone. `hdmirror` has its own tests (`pytest`, 16 passing).

### B. Configuration
- [x] `~/.config/hdmirror/mirror.toml` (template: `mirror.example.toml`). It holds root, pairs (in processing order),
  start, delay, retries, timeout, recheck_months, absent_recheck_days, notify and log_file.
- [x] CLI flags: `--pairs`, `--from`, `--to`, `--dry-run`, `--config`, `-v`.

### C. Local inventory / hole detection
- [x] Scan `<root>/<PAIR>/HISTDATA_COM_ASCII_<PAIR>_T<YYYYMM>.zip` (`inventory.scan`).
- [x] Validate each archive (it opens and lists `DAT_ASCII_<PAIR>_T_<YYYYMM>.csv`). Invalid ones go to `<root>/_quarantine/<PAIR>/`.
  New downloads are also CRC-checked.
- [x] Expected range = max(`start` = 200801, HistData's first month for the pair) through the current month.
  First months come from a snapshot of upstream `.repo` (`availability.py`).
- [x] Holes = expected − present − recently confirmed absent.
- [x] The stray `HISTDATA_COM_ASCII_EURUSD_M1202302.zip` is ignored (left in place).

### D. State / manifest
- [x] `<root>/.mirror_state.json` records status (ok/absent/failed), size, sha256, remote_updated_at, downloaded_at,
  checked_at, attempts and error for each month. It is saved after every file.
- [x] Seeded from the 1,322 existing tick archives.
- [x] No form/token means `absent`. Absent months are re-probed after 90 days, and always if within the last `recheck_months` months.

### E. Queue + downloader
- [x] Ordered queue: pair order as given, oldest month first.
- [x] GET page → parse form → POST `get.php` → check the `Content-Disposition` filename → stream to `.part` → CRC check
  → atomic rename → update state → convert to Parquet.
- [x] Store the page's `Updated at:` value.
- [x] tqdm bar over the queue, plus a per-file bytes bar.
- [x] One `requests.Session`, timeout, retries with exponential backoff, 4 s between files and 1 s between page and POST.
- [x] Ctrl-C deletes the `.part` file and saves state; a re-run resumes.
- [x] Summary at the end. Exit 0 = success, 1 = failures, 130 = interrupted, 75 = another run holds the lock.

### F. Modes
- [x] `hdmirror backfill [-p …] [--from] [--to] [--dry-run]`
- [x] `hdmirror maintain`: missing/new months, re-check of the last 2 months against `Updated at`, retry of failures.
  **A re-published month is downloaded again and overwrites the zip and its Parquet file** (decision 3).
- [x] `hdmirror status [-p …] [-v]`
- [x] `hdmirror convert [--force]`

### G. Scheduling (weekly)
- [x] systemd user timer `hdmirror.timer` (Tue 06:00, `Persistent=true`; moved from Sunday on 2026-11-01) → `hdmirror.service` (`maintain`).
  Installed and enabled. Linger is enabled.
- [ ] Mount guard: **intentionally not implemented**, because the drive mounts automatically (decision 2026-10-10).
  `hdmirror` already refuses to run if `root` does not exist, but it does not check which device holds it.
- [x] Lock: `flock` on `<root>/.mirror.lock`.
- [x] Log: `~/.local/state/hdmirror/hdmirror.log`. Under systemd, per-file lines also go to the journal.

### H. Extras (done before the first download)
- [x] Upstream bugs avoided by construction: the "current month" uses a fixed UTC-5, and no `Content-Encoding` header is required.
- [x] Parquet copies at `<root>/_parquet/<PAIR>/<PAIR>_T_<YYYYMM>.parquet`, written as zstd. Columns are
  `timestamp` (ms, UTC; source is EST with no DST, +5 h), `bid`, `ask` and `volume`. Values are kept raw:
  some 2008 rows have bid > ask in the source. The zips are kept.
- [x] Desktop notifications via `notify-send` at the end of every backfill/maintain/convert. Runs with failures are sent as critical.

---

## Decisions (2026-10-10)

1. Pairs: the 12 already mirrored. First run order: GBPUSD, EURUSD, USDCHF, USDCAD, XAUUSD, then AUDUSD, EURAUD,
   EURNZD, GBPAUD, GBPNZD, NZDUSD, XAGUSD.
2. History: 2008-01 onward only.
3. Re-published month: download it again and overwrite.
4. Keep the zips, and add Parquet copies alongside.

## First run log

- 2026-10-10: `status` found 1,356 months to fetch (2008+ through 202610). `convert` wrote Parquet for the
  1,322 existing archives in 2m13s with 0 failures. `backfill` for all 12 pairs started at about 13:05 local time,
  taking about 12 s per file.
- 2026-10-10 16:30: backfill finished with exit 0. 1,356 downloaded, 1,356 converted to Parquet, 0 failed, 0 absent.
  All 12 pairs are complete from 2008 (or each pair's first tick month) through 202610 (partial current month).
  Disk use: zips 24G, Parquet 17G.

## Weekly run review (2026-11-01)

Reported: weekly runs seemed to download nothing. The downloader was working; a fresh EURUSD 202610 was identical
to the local zip. Two real problems:

- Timing: HistData's stamps were Sun 10-18 22:40 and Mon 10-26 10:27, so the Sunday 06:00 timer ran before each publish
  and fetched data a week late. The Oct 25 run found nothing new. The timer now runs on Tuesday.
- Wording: notifications led with "downloaded 0" even when files were refreshed. They now report files fetched,
  newest tick and HistData's last publish date.
