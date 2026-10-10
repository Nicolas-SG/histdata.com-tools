"""Month arithmetic on ``YYYYMM`` strings.

HistData timestamps are EST without daylight saving, i.e. a fixed UTC-5.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

EST = timezone(timedelta(hours=-5), "EST")
_MONTH_RE = re.compile(r"^\d{4}(0[1-9]|1[0-2])$")


def check(month: str) -> str:
    """Return ``month`` if it is a valid ``YYYYMM`` string, else raise."""
    if not _MONTH_RE.match(month):
        raise ValueError(f"invalid month {month!r}, expected YYYYMM")
    return month


def current(now: datetime | None = None) -> str:
    """Return the current month on HistData's clock (UTC-5, no DST)."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(EST).strftime("%Y%m")


def shift(month: str, delta: int) -> str:
    """Return ``month`` moved by ``delta`` months."""
    index = int(month[:4]) * 12 + int(month[4:]) - 1 + delta
    return f"{index // 12:04d}{index % 12 + 1:02d}"


def between(start: str, end: str) -> list[str]:
    """Return every month from ``start`` to ``end`` inclusive."""
    months = []
    month = start
    while month <= end:
        months.append(month)
        month = shift(month, 1)
    return months
