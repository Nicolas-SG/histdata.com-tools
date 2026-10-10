"""Talk to HistData.com: read a month's download page and fetch its archive.

The flow mirrors the site's own form: GET the month page, read the hidden
inputs of ``<form id="file_down">`` (including the one-time token ``tk``),
then POST them to ``/get.php`` which answers with the ZIP archive.
"""

from __future__ import annotations

import os
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

from hdmirror import months
from hdmirror.inventory import csv_name, zip_name

BASE_URL = "http://www.histdata.com"
POST_URL = f"{BASE_URL}/get.php"
FORM_FIELDS = ("tk", "date", "datemonth", "platform", "timeframe", "fxpair")
USER_AGENT = "hdmirror/0.1 (personal archive mirror; one file at a time)"
_UPDATED_RE = re.compile(
    r"Updated at:\s*(?:<[^>]+>\s*)*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)
_CHUNK = 256 * 1024


class NotAvailable(Exception):
    """The month page has no download form: HistData has no file for it."""


class DownloadError(Exception):
    """A download failed. ``retryable`` tells whether trying again may help."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class MonthPage:
    """What the month page tells us."""

    url: str
    form: dict[str, str]
    updated_at: datetime | None


def page_url(pair: str, month: str) -> str:
    """Return the download page URL for one pair/month of tick data."""
    return (
        f"{BASE_URL}/download-free-forex-data/?/ascii/tick-data-quotes/"
        f"{pair.lower()}/{month[:4]}/{int(month[4:])}"
    )


class _FormParser(HTMLParser):
    """Collect hidden input values inside ``<form id="file_down">``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_form = False
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "form":
            self.in_form = attributes.get("id") == "file_down"
        elif tag == "input" and self.in_form and attributes.get("id"):
            # The page repeats the form; keep the first value of each field.
            self.values.setdefault(attributes["id"], attributes.get("value", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.in_form = False


def parse_page(html: str, url: str = "") -> MonthPage:
    """Parse a month page. Raise :class:`NotAvailable` if it has no form."""
    parser = _FormParser()
    parser.feed(html)
    parser.close()
    form = {key: parser.values.get(key, "") for key in FORM_FIELDS}
    if not form["tk"]:
        raise NotAvailable(url or "no download form on page")
    match = _UPDATED_RE.search(html)
    updated_at = None
    if match:
        # The site does not say which zone it uses; assume HistData's
        # EST clock so a refresh errs towards downloading again.
        updated_at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        updated_at = updated_at.replace(tzinfo=months.EST).astimezone(timezone.utc)
    return MonthPage(url=url, form=form, updated_at=updated_at)


def validate_zip(path: Path, pair: str, month: str, *, full: bool = False) -> None:
    """Check ``path`` is a readable archive holding the expected CSV.

    With ``full`` every member's CRC is verified as well.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            if csv_name(pair, month) not in archive.namelist():
                raise DownloadError(
                    f"{path.name} does not contain {csv_name(pair, month)}",
                    retryable=False,
                )
            if full and (bad := archive.testzip()) is not None:
                raise DownloadError(f"{path.name}: corrupt member {bad}")
    except zipfile.BadZipFile as err:
        raise DownloadError(f"{path.name}: {err}") from err


class HistDataClient:
    """Fetch pages and archives through one HTTP session."""

    def __init__(self, timeout: float = 60.0, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def page(self, pair: str, month: str) -> MonthPage:
        url = page_url(pair, month)
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as err:
            raise DownloadError(f"GET {url}: {err}") from err
        return parse_page(response.text, url)

    def download(
        self,
        pair: str,
        month: str,
        page: MonthPage,
        dest_dir: Path,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        """Download the archive for ``page`` into ``dest_dir`` atomically.

        ``progress(bytes_done, bytes_total)`` is called while streaming.
        An existing file of the same name is replaced only once the new one
        has been fully written and validated.
        """
        expected = zip_name(pair, month)
        target = dest_dir / expected
        temp = dest_dir / f".{expected}.part"
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self.session.post(
                POST_URL,
                data=page.form,
                headers={"Referer": page.url, "Origin": BASE_URL},
                timeout=self.timeout,
                stream=True,
            ) as response:
                response.raise_for_status()
                filename = _disposition_filename(response.headers)
                if filename != expected:
                    raise DownloadError(
                        f"server sent {filename or 'no file'}, expected {expected}"
                    )
                total = response.headers.get("Content-Length")
                total_bytes = int(total) if total and total.isdigit() else None
                done = 0
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(_CHUNK):
                        handle.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
            validate_zip(temp, pair, month, full=True)
            os.replace(temp, target)
            return target
        except requests.RequestException as err:
            raise DownloadError(f"POST for {expected}: {err}") from err
        finally:
            temp.unlink(missing_ok=True)


def _disposition_filename(headers: requests.structures.CaseInsensitiveDict) -> str:
    disposition = headers.get("Content-Disposition", "")
    match = re.search(r'filename="?([^";]+)"?', disposition)
    return Path(match.group(1)).name if match else ""
