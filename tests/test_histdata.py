from datetime import datetime, timezone

import pytest
import requests

from conftest import zip_bytes
from hdmirror.histdata import DownloadError, HistDataClient, NotAvailable, page_url, parse_page

FORM = """
<form id="file_down" name="file_down" method="POST" action="/get.php">
<input type="hidden" name="tk" id="tk" value="abc123" />
<input type="hidden" name="date" id="date" value="2026" />
<input type="hidden" name="datemonth" id="datemonth" value="202609" />
<input type="hidden" name="platform" id="platform" value="ASCII" />
<input type="hidden" name="timeframe" id="timeframe" value="T" />
<input type="hidden" name="fxpair" id="fxpair" value="EURUSD" />
</form>"""
PAGE = f"<html>{FORM}<p>Updated at: <b>2026-10-04 12:45:27</b></p>{FORM.replace('abc123', 'other')}</html>"


def test_page_url():
    assert page_url("EURUSD", "202609") == (
        "http://www.histdata.com/download-free-forex-data/?/ascii/tick-data-quotes/eurusd/2026/9"
    )


def test_parse_page_reads_first_form_and_updated_at():
    page = parse_page(PAGE, "u")
    assert page.form == {
        "tk": "abc123", "date": "2026", "datemonth": "202609",
        "platform": "ASCII", "timeframe": "T", "fxpair": "EURUSD",
    }
    assert page.updated_at == datetime(2026, 10, 4, 17, 45, 27, tzinfo=timezone.utc)


def test_parse_page_without_form_is_not_available():
    with pytest.raises(NotAvailable):
        parse_page("<html>nothing here</html>")


class FakeResponse:
    def __init__(self, body: bytes, filename: str, status: int = 200):
        self.body = body
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict({
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(body)),
        })
        self.text = PAGE

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, body: bytes, filename: str):
        self.headers = {}
        self.response = FakeResponse(body, filename)
        self.posts = []

    def get(self, url, timeout):
        return self.response

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.response


def test_download_writes_validated_archive(tmp_path):
    session = FakeSession(zip_bytes("EURUSD", "202609"), "HISTDATA_COM_ASCII_EURUSD_T202609.zip")
    client = HistDataClient(session=session)
    page = client.page("EURUSD", "202609")
    seen = []
    path = client.download("EURUSD", "202609", page, tmp_path, lambda done, total: seen.append((done, total)))
    assert path == tmp_path / "HISTDATA_COM_ASCII_EURUSD_T202609.zip"
    assert seen[-1][0] == seen[-1][1] == path.stat().st_size
    url, kwargs = session.posts[0]
    assert url == "http://www.histdata.com/get.php"
    assert kwargs["data"]["tk"] == "abc123"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    ("body", "filename"),
    [
        (b"<html>token expired</html>", "HISTDATA_COM_ASCII_EURUSD_T202609.zip"),
        (zip_bytes("EURUSD", "202609"), "HISTDATA_COM_ASCII_EURUSD_T202608.zip"),
    ],
)
def test_download_rejects_bad_payloads_and_leaves_no_files(tmp_path, body, filename):
    client = HistDataClient(session=FakeSession(body, filename))
    with pytest.raises(DownloadError):
        client.download("EURUSD", "202609", client.page("EURUSD", "202609"), tmp_path)
    assert list(tmp_path.iterdir()) == []
