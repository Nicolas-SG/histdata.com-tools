"""Plan which months to fetch and download them one file at a time."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tqdm import tqdm

from hdmirror import convert, months
from hdmirror.availability import FIRST_MONTH
from hdmirror.config import Config
from hdmirror.histdata import DownloadError, HistDataClient, MonthPage, NotAvailable
from hdmirror.inventory import zip_path
from hdmirror.state import ABSENT, FAILED, State, now_iso

log = logging.getLogger(__name__)

MISSING = "missing"
RECHECK = "recheck"


@dataclass(frozen=True)
class Job:
    pair: str
    month: str
    reason: str  # MISSING: no local file; RECHECK: compare with HistData's "Updated at"


@dataclass
class Summary:
    downloaded: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    unchanged: int = 0
    absent: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    converted: int = 0
    convert_failed: list[str] = field(default_factory=list)
    interrupted: bool = False

    @property
    def ok(self) -> bool:
        return not (self.failed or self.convert_failed or self.interrupted)

    def lines(self) -> list[str]:
        lines = [
            f"downloaded {len(self.downloaded)}, refreshed {len(self.refreshed)}, "
            f"unchanged {self.unchanged}, absent {len(self.absent)}, failed {len(self.failed)}",
            f"parquet: converted {self.converted}, failed {len(self.convert_failed)}",
        ]
        if self.failed:
            lines.append("failed: " + " ".join(self.failed[:20]))
        if self.convert_failed:
            lines.append("parquet failed: " + " ".join(self.convert_failed[:20]))
        if self.interrupted:
            lines.append("interrupted before the queue finished")
        return lines


def first_month(pair: str, start: str) -> str:
    """The earliest month worth asking HistData for."""
    return max(start, FIRST_MONTH.get(pair, start))


def plan(
    cfg: Config,
    state: State,
    inventory: dict[str, dict[str, Path]],
    pairs: list[str],
    *,
    start: str,
    end: str,
    recheck: bool,
    now: datetime | None = None,
) -> list[Job]:
    """Build the queue, pair by pair in the given order, oldest month first.

    Months HistData had no file for are skipped until ``absent_recheck_days``
    pass, except the most recent ``recheck_months`` which may simply not be
    published yet.
    """
    now = now or datetime.now(timezone.utc)
    recent_from = months.shift(months.current(now), -(cfg.recheck_months - 1))
    absent_cutoff = now - timedelta(days=cfg.absent_recheck_days)
    jobs = []
    for pair in pairs:
        have = inventory.get(pair, {})
        for month in months.between(first_month(pair, start), end):
            if month in have:
                if recheck and month >= recent_from:
                    jobs.append(Job(pair, month, RECHECK))
                continue
            entry = state.get(pair, month)
            if (
                entry.get("status") == ABSENT
                and month < recent_from
                and datetime.fromisoformat(entry["checked_at"]) > absent_cutoff
            ):
                continue
            jobs.append(Job(pair, month, MISSING))
    return jobs


def is_newer(page: MonthPage, entry: dict, path: Path) -> bool:
    """Whether HistData updated the month after our copy was taken."""
    if page.updated_at is None:
        return False
    if entry.get("remote_updated_at"):
        return page.updated_at > datetime.fromisoformat(entry["remote_updated_at"])
    return page.updated_at > datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


class Runner:
    """Work through a queue with one HTTP session, saving state after each file."""

    def __init__(
        self,
        cfg: Config,
        state: State,
        client: HistDataClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.client = client
        self.sleep = sleep
        self.summary = Summary()

    def run(self, jobs: list[Job]) -> Summary:
        bar = tqdm(total=len(jobs), unit="file", desc="histdata", dynamic_ncols=True, disable=None)
        try:
            for index, job in enumerate(jobs):
                bar.set_postfix_str(f"{job.pair} {job.month} {job.reason}")
                self._run_job(job)
                self.state.save()
                bar.update()
                if index + 1 < len(jobs):
                    self.sleep(self.cfg.delay_seconds)
        except KeyboardInterrupt:
            self.summary.interrupted = True
            log.warning("interrupted; state saved")
        finally:
            self.state.save()
            bar.close()
        return self.summary

    def _run_job(self, job: Job) -> None:
        label = f"{job.pair}:{job.month}"
        target = zip_path(self.cfg.root, job.pair, job.month)
        entry = self.state.get(job.pair, job.month)
        for attempt in range(1, self.cfg.retries + 1):
            try:
                page = self.client.page(job.pair, job.month)
                if job.reason == RECHECK and not is_newer(page, entry, target):
                    self.state.update(
                        job.pair, job.month,
                        remote_updated_at=_iso(page.updated_at), checked_at=now_iso(),
                    )
                    self.summary.unchanged += 1
                    log.info("%s unchanged on HistData", label)
                    return
                self.sleep(1.0)  # be gentle between the page GET and the archive POST
                saved = self._download(job, page, target.parent)
                self.state.record_file(
                    job.pair, job.month, saved,
                    remote_updated_at=_iso(page.updated_at),
                    downloaded_at=now_iso(), checked_at=now_iso(), attempts=0,
                )
                (self.summary.refreshed if job.reason == RECHECK else self.summary.downloaded).append(label)
                log.info("%s %s (%d bytes)", label, "refreshed" if job.reason == RECHECK else "downloaded", saved.stat().st_size)
                self._convert(job, saved)
                return
            except NotAvailable:
                if job.reason == RECHECK:
                    log.warning("%s: page has no download form any more; keeping local copy", label)
                    self.summary.unchanged += 1
                else:
                    self.state.update(job.pair, job.month, status=ABSENT, checked_at=now_iso())
                    self.summary.absent.append(label)
                    log.info("%s not available on HistData", label)
                return
            except DownloadError as err:
                if err.retryable and attempt < self.cfg.retries:
                    wait = self.cfg.delay_seconds * 2**attempt
                    log.warning("%s attempt %d failed (%s); retrying in %.0fs", label, attempt, err, wait)
                    self.sleep(wait)
                    continue
                fields = {"error": str(err), "checked_at": now_iso(), "attempts": entry.get("attempts", 0) + 1}
                if job.reason == MISSING:
                    fields["status"] = FAILED
                self.state.update(job.pair, job.month, **fields)
                self.summary.failed.append(label)
                log.error("%s failed: %s", label, err)
                return

    def _download(self, job: Job, page: MonthPage, dest_dir: Path) -> Path:
        with tqdm(unit="B", unit_scale=True, leave=False, dynamic_ncols=True, disable=None,
                  desc=f"{job.pair} {job.month}") as bytes_bar:
            def progress(done: int, total: int | None) -> None:
                if total and bytes_bar.total != total:
                    bytes_bar.total = total
                bytes_bar.update(done - bytes_bar.n)

            return self.client.download(job.pair, job.month, page, dest_dir, progress)

    def _convert(self, job: Job, zip_file: Path) -> None:
        try:
            convert.convert(zip_file, convert.parquet_path(self.cfg.parquet_root, job.pair, job.month), job.pair, job.month)
            self.summary.converted += 1
        except Exception as err:  # a bad conversion must not stop the download queue
            self.summary.convert_failed.append(f"{job.pair}:{job.month}")
            log.error("%s:%s parquet conversion failed: %s", job.pair, job.month, err)


def convert_pending(
    cfg: Config,
    inventory: dict[str, dict[str, Path]],
    summary: Summary,
    *,
    force: bool = False,
) -> None:
    """Write Parquet for every archive that has none (or an older one)."""
    pending = [
        (pair, month, path)
        for pair, found in inventory.items()
        for month, path in sorted(found.items())
        if force or not convert.is_current(path, convert.parquet_path(cfg.parquet_root, pair, month))
    ]
    for pair, month, path in tqdm(pending, unit="file", desc="parquet", dynamic_ncols=True, disable=None):
        try:
            rows = convert.convert(path, convert.parquet_path(cfg.parquet_root, pair, month), pair, month)
            summary.converted += 1
            log.info("%s:%s parquet written (%d rows)", pair, month, rows)
        except Exception as err:
            summary.convert_failed.append(f"{pair}:{month}")
            log.error("%s:%s parquet conversion failed: %s", pair, month, err)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
