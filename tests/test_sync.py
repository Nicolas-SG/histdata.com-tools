from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import write_zip
from hdmirror import convert, inventory
from hdmirror.histdata import DownloadError, MonthPage, NotAvailable
from hdmirror.state import ABSENT, FAILED, OK, State
from hdmirror.sync import MISSING, RECHECK, Job, Runner, plan

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
UPDATED = datetime(2026, 10, 4, 17, 45, tzinfo=timezone.utc)


def test_plan_orders_pairs_and_skips_known_absent(cfg):
    write_zip(cfg.root, "EURUSD", "202608")
    state = State(cfg.state_file)
    state.update("EURUSD", "202606", status=ABSENT, checked_at=(NOW - timedelta(days=1)).isoformat())
    state.update("EURUSD", "202610", status=ABSENT, checked_at=(NOW - timedelta(days=1)).isoformat())
    found = inventory.scan(cfg.root, ["GBPUSD", "EURUSD"])
    jobs = plan(cfg, state, found, ["GBPUSD", "EURUSD"], start="202606", end="202610", recheck=False, now=NOW)
    assert [(j.pair, j.month) for j in jobs] == [
        ("GBPUSD", "202606"), ("GBPUSD", "202607"), ("GBPUSD", "202608"),
        ("GBPUSD", "202609"), ("GBPUSD", "202610"),
        # 202606 is a recently confirmed absence; 202610 is too recent to trust.
        ("EURUSD", "202607"), ("EURUSD", "202609"), ("EURUSD", "202610"),
    ]


def test_plan_rechecks_recent_months_and_respects_first_month(cfg):
    write_zip(cfg.root, "XAUUSD", "202609")
    found = inventory.scan(cfg.root, ["XAUUSD"])
    jobs = plan(cfg, State(cfg.state_file), found, ["XAUUSD"], start="200801", end="202610", recheck=True, now=NOW)
    assert jobs[0] == Job("XAUUSD", "200903", MISSING)
    assert Job("XAUUSD", "202609", RECHECK) in jobs
    assert Job("XAUUSD", "202610", MISSING) in jobs


class FakeClient:
    def __init__(self, cfg, outcomes):
        self.cfg = cfg
        self.outcomes = outcomes
        self.downloads = []

    def page(self, pair, month):
        outcome = self.outcomes[(pair, month)]
        if isinstance(outcome, Exception):
            raise outcome
        return MonthPage(url="u", form={"tk": "x"}, updated_at=outcome)

    def download(self, pair, month, page, dest_dir, progress=None):
        self.downloads.append((pair, month))
        return write_zip(self.cfg.root, pair, month)


def test_runner_downloads_converts_and_records(cfg):
    state = State(cfg.state_file)
    client = FakeClient(cfg, {
        ("EURUSD", "202608"): UPDATED,
        ("EURUSD", "202609"): NotAvailable("none"),
        ("EURUSD", "202610"): DownloadError("timeout"),
    })
    jobs = [Job("EURUSD", m, MISSING) for m in ("202608", "202609", "202610")]
    summary = Runner(cfg, state, client, sleep=lambda s: None).run(jobs)
    assert summary.downloaded == ["EURUSD:202608"]
    assert summary.absent == ["EURUSD:202609"]
    assert summary.failed == ["EURUSD:202610"]
    assert summary.converted == 1
    saved = State(cfg.state_file)
    assert saved.get("EURUSD", "202608")["status"] == OK
    assert saved.get("EURUSD", "202608")["remote_updated_at"] == UPDATED.isoformat()
    assert saved.get("EURUSD", "202609")["status"] == ABSENT
    assert saved.get("EURUSD", "202610")["status"] == FAILED
    assert convert.parquet_path(cfg.parquet_root, "EURUSD", "202608").exists()


def test_recheck_refreshes_only_when_histdata_is_newer(cfg):
    path = write_zip(cfg.root, "EURUSD", "202609")
    state = State(cfg.state_file)
    state.record_file("EURUSD", "202609", path, remote_updated_at=UPDATED.isoformat())
    client = FakeClient(cfg, {("EURUSD", "202609"): UPDATED})
    runner = Runner(cfg, state, client, sleep=lambda s: None)
    assert runner.run([Job("EURUSD", "202609", RECHECK)]).unchanged == 1
    assert client.downloads == []

    client.outcomes[("EURUSD", "202609")] = UPDATED + timedelta(days=7)
    summary = Runner(cfg, state, client, sleep=lambda s: None).run([Job("EURUSD", "202609", RECHECK)])
    assert summary.refreshed == ["EURUSD:202609"]
    assert client.downloads == [("EURUSD", "202609")]


def test_summary_reports_refreshes_and_quiet_runs_plainly(cfg):
    path = write_zip(cfg.root, "EURUSD", "202609")
    state = State(cfg.state_file)
    state.record_file("EURUSD", "202609", path, remote_updated_at=UPDATED.isoformat())
    client = FakeClient(cfg, {("EURUSD", "202609"): UPDATED})
    quiet = Runner(cfg, state, client, sleep=lambda s: None).run([Job("EURUSD", "202609", RECHECK)])
    assert quiet.lines() == [
        "No new data: every checked month matches HistData",
        "HistData last published: 2026-10-04 12:45",
    ]

    client.outcomes[("EURUSD", "202609")] = UPDATED + timedelta(days=7)
    busy = Runner(cfg, state, client, sleep=lambda s: None).run([Job("EURUSD", "202609", RECHECK)])
    assert busy.lines()[0] == "1 files fetched: 0 new months, 1 updated"
    assert "Newest tick fetched: 2026-01-01 22:04 UTC" in busy.lines()
