"""Command line: ``hdmirror status|backfill|maintain|convert``."""

from __future__ import annotations

import argparse
import fcntl
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from tqdm import tqdm

from hdmirror import __version__, config, convert, inventory, months
from hdmirror.histdata import HistDataClient
from hdmirror.notify import notify
from hdmirror.state import ABSENT, FAILED, State
from hdmirror.sync import Runner, Summary, convert_pending, first_month, plan

log = logging.getLogger("hdmirror")

EXIT_LOCKED = 75


class _TqdmHandler(logging.StreamHandler):
    """Print log lines without breaking progress bars."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record), file=sys.stderr)
        except Exception:
            self.handleError(record)


def _setup_logging(cfg: config.Config, verbose: bool) -> None:
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(cfg.log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console = _TqdmHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    # On a terminal the progress bars tell the story; under systemd log every file.
    console.setLevel(logging.INFO if verbose or not sys.stderr.isatty() else logging.WARNING)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"another hdmirror run holds {path}", file=sys.stderr)
            raise SystemExit(EXIT_LOCKED) from None
        yield


def _pairs(args: argparse.Namespace, cfg: config.Config) -> list[str]:
    pairs = [pair.upper() for pair in args.pairs] if args.pairs else cfg.pairs
    return list(dict.fromkeys(pairs))  # keep order, drop repeats


def _scan(cfg: config.Config, pairs: list[str]) -> tuple[State, dict]:
    found = inventory.scan(cfg.root, pairs, cfg.quarantine_dir)
    state = State(cfg.state_file)
    if added := state.seed(found):
        log.info("recorded %d existing archives in the state file", added)
        state.save()
    return state, found


def cmd_status(args: argparse.Namespace, cfg: config.Config) -> int:
    pairs = _pairs(args, cfg)
    state, found = _scan(cfg, pairs)
    start = args.start or cfg.start
    end = months.current()
    print(f"root: {cfg.root}\nrange: {start}..{end} (HistData clock, UTC-5)\n")
    header = f"{'pair':8} {'local range':15} {'files':>5} {'parquet':>7} {'holes':>5} {'absent':>6} {'failed':>6}"
    print(header)
    print("-" * len(header))
    total_holes = 0
    for pair in pairs:
        have = found[pair]
        wanted = months.between(first_month(pair, start), end)
        entries = {m: state.get(pair, m).get("status") for m in wanted}
        holes = [m for m in wanted if m not in have and entries[m] != ABSENT]
        total_holes += len(holes)
        parquet = sum(
            convert.is_current(path, convert.parquet_path(cfg.parquet_root, pair, m))
            for m, path in have.items()
        )
        span = f"{min(have)}..{max(have)}" if have else "-"
        print(
            f"{pair:8} {span:15} {len(have):5} {parquet:7} {len(holes):5} "
            f"{sum(s == ABSENT for s in entries.values()):6} {sum(s == FAILED for s in entries.values()):6}"
        )
        if args.verbose and holes:
            print("         holes: " + " ".join(holes))
    print(f"\n{total_holes} months to fetch")
    return 0


def _sync(args: argparse.Namespace, cfg: config.Config, *, recheck: bool, title: str) -> int:
    pairs = _pairs(args, cfg)
    with _lock(cfg.lock_file):
        state, found = _scan(cfg, pairs)
        start = getattr(args, "start", None) or cfg.start
        end = getattr(args, "end", None) or months.current()
        jobs = plan(cfg, state, found, pairs, start=months.check(start), end=months.check(end), recheck=recheck)
        log.info("%s: %d jobs for %s (%s..%s)", title, len(jobs), " ".join(pairs), start, end)
        if args.dry_run:
            for job in jobs:
                print(f"{job.pair} {job.month} {job.reason}")
            print(f"{len(jobs)} jobs")
            return 0
        summary = Runner(cfg, state, HistDataClient(timeout=cfg.timeout)).run(jobs)
        if not summary.interrupted:
            convert_pending(cfg, inventory.scan(cfg.root, pairs), summary)
    _report(cfg, title, summary)
    return 0 if summary.ok else (130 if summary.interrupted else 1)


def _report(cfg: config.Config, title: str, summary: Summary) -> None:
    for line in summary.lines():
        log.info("%s: %s", title, line)
    if cfg.notify:
        status = "finished" if summary.ok else ("interrupted" if summary.interrupted else "finished with errors")
        notify(
            f"hdmirror {title} {status}",
            "\n".join(summary.lines()),
            urgency="normal" if summary.ok else "critical",
        )


def cmd_backfill(args: argparse.Namespace, cfg: config.Config) -> int:
    return _sync(args, cfg, recheck=False, title="backfill")


def cmd_maintain(args: argparse.Namespace, cfg: config.Config) -> int:
    return _sync(args, cfg, recheck=True, title="maintain")


def cmd_convert(args: argparse.Namespace, cfg: config.Config) -> int:
    pairs = _pairs(args, cfg)
    summary = Summary()
    convert_pending(cfg, inventory.scan(cfg.root, pairs), summary, force=args.force)
    _report(cfg, "convert", summary)
    return 0 if summary.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hdmirror", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, help=f"TOML config (default {config.DEFAULT_CONFIG_PATH})")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every file to the console")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(func=func)
        sub.add_argument("-p", "--pairs", nargs="+", metavar="PAIR", help="pairs in processing order (default: config)")
        return sub

    status = add("status", cmd_status, "Show local coverage and holes; downloads nothing.")
    status.add_argument("--from", dest="start", metavar="YYYYMM")

    for name, func, help_text in (
        ("backfill", cmd_backfill, "Download every missing month for the pairs."),
        ("maintain", cmd_maintain, "Weekly run: fetch new months, refresh recently updated ones, retry failures."),
    ):
        sub = add(name, func, help_text)
        sub.add_argument("--dry-run", action="store_true", help="print the queue and exit")
        if name == "backfill":
            sub.add_argument("--from", dest="start", metavar="YYYYMM")
            sub.add_argument("--to", dest="end", metavar="YYYYMM")

    conv = add("convert", cmd_convert, "Write Parquet copies for archives that lack one.")
    conv.add_argument("--force", action="store_true", help="rewrite every Parquet file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config.load(args.config)
    if not cfg.root.is_dir():
        print(f"archive root {cfg.root} does not exist", file=sys.stderr)
        return 2
    _setup_logging(cfg, args.verbose)
    return args.func(args, cfg)
